# -*- coding: utf-8 -*-
"""工具页（key="tools"）：包含「置物架」与「剪贴板」两个内部标签。
- 置物架：只保存本地文件绝对路径，支持多选添加、打开、定位、移除、两步清空与拖入/拖出（CopyAction）。
- 剪贴板：只保存纯文字（最近 30 条未固定 + 最多 20 条固定），统一内嵌列表容器，支持点击整行复制、固定、删除与清空。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ...services.clipboard_history import preview_text
from ..anim import Spring
from ..header import PageHeader
from ..icons import draw_document, draw_tray
from ..theme import G, W, theme, ui_font
from ..widgets import Card, Hoverable, IconButton, Label, PillButton, WheelScrollArea, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt


def extract_local_files(mime: QtCore.QMimeData | None) -> list[str]:
    """从拖拽 QMimeData 中提取真实存在的本地文件绝对路径；纯文本或文件夹一律忽略。"""
    if mime is None or not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        local_p = url.toLocalFile()
        if not local_p:
            continue
        try:
            p = Path(local_p)
            if p.is_file():
                out.append(str(p.resolve()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _format_file_meta(path_str: str, exists: bool) -> str:
    """格式化文件扩展名与体积，例如 'LOG · 2KB'。"""
    if not exists:
        return "文件丢失"
    ext = Path(path_str).suffix.lstrip(".").upper() or "FILE"
    if len(ext) > 5:
        ext = ext[:5]
    try:
        sz = os.path.getsize(path_str)
    except OSError:
        return ext
    if sz < 1024:
        sz_s = f"{sz}B"
    elif sz < 1024 * 1024:
        sz_s = f"{max(1, round(sz / 1024))}KB"
    elif sz < 1024 * 1024 * 1024:
        sz_s = f"{sz / (1024 * 1024):.1f}M"
    else:
        sz_s = f"{sz / (1024 * 1024 * 1024):.1f}G"
    return f"{ext} · {sz_s}"


class _SubTabBar(QtWidgets.QWidget):
    """工具页顶部双标签切换器（置物架 / 剪贴板），使用弹簧滑块高亮。"""
    changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = [("shelf", "置物架"), ("clipboard", "剪贴板")]
        self.index = 0
        self._pos = Spring(0.0, 420, 0.82, 0.005)
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        self.setFixedSize(116, 21)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.changed.connect(self.update)

    def current_key(self) -> str:
        return self.tabs[self.index][0]

    def set_tab(self, key: str, emit: bool = False):
        keys = [k for k, _ in self.tabs]
        if key not in keys:
            return
        i = keys.index(key)
        if i != self.index:
            self.index = i
            self._pos.to(float(i))
            if not self._t.isActive():
                self._last = time.perf_counter()
                self._t.start()
            self.update()
            if emit:
                self.changed.emit(key)

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._pos.step(dt)
        self.update()
        if self._pos.rest:
            self._t.stop()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            i = 0 if e.position().x() < self.width() / 2.0 else 1
            self.set_tab(self.tabs[i][0], emit=True)
            e.accept()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.card(1.1))
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)

        half_w = (r.width() - 4.0) / 2.0
        px = r.left() + 2.0 + half_w * self._pos.x
        pill_r = QtCore.QRectF(px, r.top() + 2.0, half_w, r.height() - 4.0)
        p.setBrush(theme.active_pill())
        p.drawRoundedRect(pill_r, pill_r.height() / 2.0, pill_r.height() / 2.0)

        for i, (_, label) in enumerate(self.tabs):
            tr = QtCore.QRectF(r.left() + 2.0 + i * half_w, r.top(), half_w, r.height())
            sel = (i == self.index)
            p.setFont(ui_font(12, W.DemiBold if sel else W.Medium))
            p.setPen(theme.pill_text(0.96) if sel else theme.text2())
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, label)


class _DangerIconButton(IconButton):
    """悬停时呈现危险红色的图标按钮（用于删除/移除操作）。"""
    def paintEvent(self, e):
        if self._hover.x > 0.05 and self.isEnabled():
            self.color = theme.up()
        else:
            self.color = None
        super().paintEvent(e)


class _TwoStepClearButton(Hoverable):
    """内容区右下角两步确认「清空」按钮：首次点击变为「确认」，再次点击执行，超时或外部点击复位。"""
    confirmed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.confirming = False
        self._filter_installed = False
        self.setFixedSize(36, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("清空当前列表")
        self._reset_timer = QtCore.QTimer(self, singleShot=True, interval=3000, timeout=self.reset)
        self.clicked.connect(self._on_click)

    def _set_app_filter(self, on: bool):
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        if on and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True
        elif not on and self._filter_installed:
            app.removeEventFilter(self)
            self._filter_installed = False

    def reset(self):
        self._set_app_filter(False)
        if getattr(self, "confirming", False):
            self.confirming = False
            self._reset_timer.stop()
            self.setFixedSize(36, 18)
            self.setToolTip("清空当前列表")
            self.update()

    def _on_click(self):
        if not self.confirming:
            self.confirming = True
            self.setFixedSize(38, 18)
            self.setToolTip("再次点击确认清空")
            self._set_app_filter(True)
            self._reset_timer.start()
            self.update()
        else:
            self.reset()
            self.confirmed.emit()

    def eventFilter(self, obj, event):
        if getattr(self, "confirming", False) and event.type() == QtCore.QEvent.Type.MouseButtonPress:
            gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
            if gpos is not None:
                local = self.mapFromGlobal(gpos)
                if not self.rect().contains(local):
                    self.reset()
        return super().eventFilter(obj, event)

    def hideEvent(self, e):
        self.reset()
        super().hideEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        s = self.press_scale()
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        if self.confirming:
            bg = QtGui.QColor(theme.up())
            bg.setAlphaF(0.24 + 0.12 * self._hover.x)
            fg = theme.up()
            txt = "确认"
        else:
            bg = theme.card(1.1 + 0.65 * self._hover.x)
            fg = theme.up() if self._hover.x > 0.1 else theme.text3()
            txt = "清空"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        p.setFont(ui_font(10, W.DemiBold if self.confirming else W.Medium))
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)


class _ShelfFileRow(Hoverable):
    """文件置物架卡片项（66×68px，最多横向可见 5 个，支持滚轮横向滚动、点击打开、拖出 CopyAction）。"""
    open_requested = QtCore.Signal(str)
    reveal_requested = QtCore.Signal(str)
    remove_requested = QtCore.Signal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.path: str = item["path"]
        self.name: str = item["name"]
        self.dir_str: str = item["dir"]
        self.exists: bool = bool(item["exists"])
        self.meta_text: str = _format_file_meta(self.path, self.exists)
        self._drag_start: QtCore.QPoint | None = None
        self.setFixedSize(66, 68)
        self.setToolTip(f"{self.name}\n{self.path}" if self.exists else f"{self.name}\n{self.path}\n（文件已移动或删除）")

        self.btn_reveal = IconButton(G.OPEN, size=15, icon_px=8, tip="在资源管理器中定位", parent=self)
        self.btn_reveal.setEnabled(self.exists)
        self.btn_reveal.clicked.connect(lambda: self.reveal_requested.emit(self.path))

        self.btn_remove = _DangerIconButton(G.CLOSE, size=15, icon_px=8, tip="移除记录（不删除原文件）", parent=self)
        self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self.path))

        self._eff_reveal = QtWidgets.QGraphicsOpacityEffect(self.btn_reveal)
        self._eff_reveal.setOpacity(0.0)
        self.btn_reveal.setGraphicsEffect(self._eff_reveal)

        self._eff_remove = QtWidgets.QGraphicsOpacityEffect(self.btn_remove)
        self._eff_remove.setOpacity(0.32)
        self.btn_remove.setGraphicsEffect(self._eff_remove)

        self.clicked.connect(lambda: self.open_requested.emit(self.path))
        self.setLayout(vbox(hbox(self.btn_reveal, None, self.btn_remove, spacing=0, margins=(2, 1, 2, 0)), None, spacing=0, margins=(0, 0, 0, 0)))

    def _sync_hover_btns(self):
        hov = self._hover.x > 0.08 or self.underMouse()
        self._eff_reveal.setOpacity(0.95 if (hov and self.exists) else 0.0)
        self._eff_remove.setOpacity(1.0 if hov else 0.32)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._sync_hover_btns()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._sync_hover_btns()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (
            self._drag_start is not None
            and (e.buttons() & Qt.MouseButton.LeftButton)
            and self.exists
            and (e.position().toPoint() - self._drag_start).manhattanLength() > 6
        ):
            self._drag_start = None
            self._press.to(0)
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setUrls([QtCore.QUrl.fromLocalFile(self.path)])
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(e)

    def paintEvent(self, e):
        self._sync_hover_btns()
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = theme.card(1.0 + 0.55 * self._hover.x)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

        icon_col = theme.on_color() if self.exists else theme.up()
        draw_document(p, r.center().x(), r.top() + 22.0, 28.0, icon_col)

        f_name = ui_font(9.5, W.Medium)
        p.setFont(f_name)
        p.setPen(theme.text() if self.exists else theme.up())
        fm_n = QtGui.QFontMetrics(f_name)
        label_txt = self.name if self.exists else f"丢失:{self.name}"
        p.drawText(
            QtCore.QRectF(r.left() + 3, r.bottom() - 25, r.width() - 6, 13),
            Qt.AlignmentFlag.AlignCenter,
            fm_n.elidedText(label_txt, Qt.TextElideMode.ElideMiddle, int(r.width() - 6)),
        )

        f_meta = ui_font(8.5, W.Normal, num=True)
        p.setFont(f_meta)
        p.setPen(theme.text3() if self.exists else theme.up())
        fm_m = QtGui.QFontMetrics(f_meta)
        p.drawText(
            QtCore.QRectF(r.left() + 3, r.bottom() - 13, r.width() - 6, 11),
            Qt.AlignmentFlag.AlignCenter,
            fm_m.elidedText(self.meta_text, Qt.TextElideMode.ElideRight, int(r.width() - 6)),
        )


class _HorizontalShelfScroll(WheelScrollArea):
    """复用统一滚轮规则的横向置物架滚动区：滚轮上下直接映射到横向滚动。"""
    def __init__(self, parent=None):
        super().__init__(parent, orientation="horizontal")


class _ClipItemRow(Hoverable):
    """剪贴板统一内嵌列表行（17px）：圆点 + 单行文字 + 时间 + 固定 + 删除，点击整行复制。"""
    copy_requested = QtCore.Signal(str)
    pin_requested = QtCore.Signal(int)
    delete_requested = QtCore.Signal(int)

    def __init__(self, index: int, item: dict, parent=None):
        super().__init__(parent)
        self.index = index
        self.full_text: str = item["text"]
        self.preview: str = preview_text(self.full_text, 140)
        self.at: str = item.get("at") or ""
        self.pinned: bool = bool(item.get("pinned", False))
        self.setFixedHeight(17)
        tip_preview = self.full_text[:400] + ("…" if len(self.full_text) > 400 else "")
        self.setToolTip(f"点击复制（共 {len(self.full_text)} 字符）：\n{tip_preview}")

        self.btn_pin = IconButton(G.PIN, size=15, icon_px=8, tip="取消固定" if self.pinned else "固定此条", parent=self)
        self.btn_pin.set_active(self.pinned)
        if self.pinned:
            self.btn_pin.color = theme.on_color()
        self.btn_pin.clicked.connect(lambda: self.pin_requested.emit(self.index))

        self.btn_del = _DangerIconButton(G.TRASH, size=15, icon_px=8, tip="删除此条记录", parent=self)
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self.index))

        self.clicked.connect(lambda: self.copy_requested.emit(self.full_text))
        self.setLayout(hbox(None, self.btn_pin, self.btn_del, spacing=2, margins=(6, 0, 4, 0)))

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        if self._hover.x > 0.02:
            hov_col = QtGui.QColor(255, 255, 255) if theme.dark else QtGui.QColor(0, 0, 0)
            hov_col.setAlphaF(0.08 * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hov_col)
            p.drawRoundedRect(r.adjusted(1, 0, -1, 0), 5, 5)

        if self.pinned:
            bar = QtCore.QRectF(r.left() + 2.0, r.center().y() - 5.5, 3.0, 11.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.on_color())
            p.drawRoundedRect(bar, 1.5, 1.5)

        dot_x = r.left() + (9.5 if self.pinned else 7.5)
        dot_col = theme.on_color() if self.pinned else theme.text3()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_col)
        p.drawEllipse(QtCore.QPointF(dot_x, r.center().y()), 1.8, 1.8)

        text_x = dot_x + 6.0
        time_w = 34.0 if self.at else 0.0
        text_w = max(40.0, r.right() - text_x - 42.0 - time_w)

        f = ui_font(11, W.Medium if self.pinned else W.Normal)
        p.setFont(f)
        p.setPen(theme.text())
        fm = QtGui.QFontMetrics(f)
        p.drawText(
            QtCore.QRectF(text_x, r.top(), text_w, r.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(self.preview, Qt.TextElideMode.ElideRight, int(text_w)),
        )

        if self.at:
            p.setFont(ui_font(9.5, num=True))
            p.setPen(theme.text3())
            p.drawText(
                QtCore.QRectF(r.right() - 40.0 - time_w, r.top(), time_w, r.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self.at,
            )


class ClearClipboardDialog(QtWidgets.QDialog):
    """清空剪贴板历史的二次确认弹窗：明确说明固定项是否清除。"""
    def __init__(self, pinned_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认清空剪贴板历史？")
        self.setModal(True)
        self.setFixedWidth(400)
        self.include_pinned = False

        t_lbl = Label("确认清空剪贴板历史？", 15, W.DemiBold, 1)
        desc = (
            f"当前共有 {pinned_count} 条已固定记录。\n默认仅清空未固定的最近记录，保留已固定条目。"
            if pinned_count > 0
            else "此操作将清空所有最近剪贴板文字记录，且不可撤销。"
        )
        m_lbl = QtWidgets.QLabel(desc, self)
        m_lbl.setFont(ui_font(12.5))
        m_lbl.setWordWrap(True)

        self.chk_pinned = QtWidgets.QCheckBox("同时清除已固定的条目", self)
        self.chk_pinned.setFont(ui_font(12.5))
        self.chk_pinned.setVisible(pinned_count > 0)

        btn_cancel = PillButton("取消", height=32)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = PillButton("确认清空", G.TRASH, height=32, danger=True)
        btn_ok.clicked.connect(self._on_confirm)

        self.setLayout(vbox(
            t_lbl,
            m_lbl,
            self.chk_pinned,
            8,
            hbox(None, btn_cancel, btn_ok, spacing=10),
            spacing=10,
            margins=(20, 18, 20, 18),
        ))
        bg = "#1e2028" if theme.dark else "#f5f6fa"
        fg = "#ffffff" if theme.dark else "#14161c"
        self.setStyleSheet(f"QDialog{{background:{bg};color:{fg};}} QLabel,QCheckBox{{color:{fg};background:transparent;}}")

    def _on_confirm(self):
        self.include_pinned = self.chk_pinned.isChecked()
        self.accept()


class _DropOverlayWidget(QtWidgets.QWidget):
    """唯一拖放提示层：虚线外框 + 托盘图标 + 「松手放进置物架」。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        c = QtGui.QColor(theme.on_color())
        fill = QtGui.QColor(c)
        fill.setAlphaF(0.11)
        p.setPen(QtGui.QPen(c, 1.5, Qt.PenStyle.DashLine))
        p.setBrush(fill)
        p.drawRoundedRect(r, 11.0, 11.0)

        cx = r.center().x()
        cy = r.center().y()
        draw_tray(p, cx, cy - 10.0, 18.0, c)

        p.setFont(ui_font(11.5, W.DemiBold))
        p.setPen(theme.text())
        p.drawText(
            QtCore.QRectF(r.left() + 8.0, cy + 4.0, r.width() - 16.0, 18.0),
            Qt.AlignmentFlag.AlignCenter,
            "松手放进置物架",
        )


class _DropContainerCard(Card):
    """支持本地文件拖放的容器卡片，负责通知拖放状态变化与位置同步。"""
    files_dropped = QtCore.Signal(list)
    drop_state_changed = QtCore.Signal(bool)
    resized = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent, radius=14, alpha=1.0)
        self.drop_active = False
        self.setAcceptDrops(True)

    def set_drop_active(self, on: bool):
        if on != self.drop_active:
            self.drop_active = on
            self.drop_state_changed.emit(on)
            self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def dragEnterEvent(self, e):
        files = extract_local_files(e.mimeData())
        if files:
            self.set_drop_active(True)
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        files = extract_local_files(e.mimeData())
        if files:
            self.set_drop_active(True)
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.set_drop_active(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self.set_drop_active(False)
        files = extract_local_files(e.mimeData())
        if files:
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
            self.files_dropped.emit(files)
        else:
            e.ignore()


class ToolsPage(Page):
    key, glyph, title, height, panel_width, panel_height = "tools", G.CLIP, "工具", 98, 419, 140

    def __init__(self, ctx):
        super().__init__(ctx)
        self.setAcceptDrops(True)
        self._clip_dirty = True
        self._shelf_dirty = True
        self._shelf_has_items = False
        self._clip_has_items = False

        # ---- 统一 Header：左(置物架/剪贴板标签 或 2.5s 状态提示) | 中(日期时间) | 右(添加/齿轮) ----
        self.header = PageHeader(ctx, self, height=24)
        self.left_stack = QtWidgets.QStackedWidget(self)
        self.left_stack.setFixedHeight(21)

        self.tab_bar = _SubTabBar(self.left_stack)
        self.tab_bar.changed.connect(self._on_tab_changed)

        self.header_notice_lbl = Label("", 11.5, W.Medium, 2, parent=self.left_stack)
        self.header_notice_lbl.setFixedHeight(21)

        self.left_stack.addWidget(self.tab_bar)
        self.left_stack.addWidget(self.header_notice_lbl)
        self.left_stack.setCurrentIndex(0)

        self._notice_timer = QtCore.QTimer(
            self, singleShot=True, interval=2500,
            timeout=self._restore_header_tabs,
        )

        self.btn_add_file = IconButton(G.ADD, 22, 10, "添加文件到置物架", parent=self)
        self.btn_add_file.clicked.connect(self._on_add_files_dialog)

        self.btn_clear_clip = _TwoStepClearButton(self)
        self.btn_clear_clip.confirmed.connect(self._on_clear_confirmed)
        self.btn_clear_clip.hide()

        self.header.add_left(self.left_stack)
        self.header.add_right(self.btn_add_file)
        self.header.add_right(self.btn_clear_clip)

        # ---- 内容区：0=置物架（横向最多5个可滚动），1=剪贴板（统一内嵌列表） ----
        self.body_card = _DropContainerCard(self)
        self.body_card.files_dropped.connect(self._on_files_dropped)
        self.body_card.drop_state_changed.connect(self._on_drop_state_changed)
        self.body_card.resized.connect(self._sync_overlays)

        self.sub_stack = QtWidgets.QStackedWidget(self.body_card)

        self.shelf_scroll = _HorizontalShelfScroll(self.sub_stack)
        self.shelf_inner = self.shelf_scroll.inner
        self.shelf_lay = hbox(spacing=5, margins=(2, 0, 2, 0))
        self.shelf_inner.setLayout(self.shelf_lay)
        self.sub_stack.addWidget(self.shelf_scroll)

        self.clip_card = Card(self.sub_stack, radius=10, alpha=0.55)
        self.clip_scroll = WheelScrollArea(self.clip_card, orientation="vertical")
        self.clip_lay = vbox(spacing=0, margins=(2, 1, 2, 1))
        self.clip_scroll.inner.setLayout(self.clip_lay)
        self.clip_card.setLayout(vbox(self.clip_scroll, spacing=0, margins=(0, 0, 0, 0)))
        self.sub_stack.addWidget(self.clip_card)

        self.drop_overlay = _DropOverlayWidget(self.body_card)
        self.drop_overlay.hide()

        self.btn_clear_bottom = _TwoStepClearButton(self.body_card)
        self.btn_clear_bottom.confirmed.connect(self._on_clear_confirmed)
        self.btn_clear_bottom.hide()

        self.body_card.setLayout(vbox((self.sub_stack, 1), spacing=0, margins=(2, 1, 2, 1)))
        self.setLayout(vbox(self.header, (self.body_card, 1), spacing=2))

        if hasattr(ctx, "shelf") and ctx.shelf is not None:
            ctx.shelf.changed.connect(self._on_shelf_changed)
            ctx.shelf.notice.connect(self.show_notice)
        if hasattr(ctx, "clipboard") and ctx.clipboard is not None:
            ctx.clipboard.changed.connect(self._on_clipboard_changed)
            ctx.clipboard.notice.connect(self.show_notice)

        self.refresh_shelf()
        self.refresh_clipboard()
        self._sync_clear_btn()

    def _restore_header_tabs(self):
        self.header_notice_lbl.setText("")
        self.left_stack.setCurrentIndex(0)

    def show_notice(self, msg: str, is_error: bool = False):
        if not msg:
            self._restore_header_tabs()
            return
        self.header_notice_lbl.set_color(theme.up() if is_error else theme.text2())
        self.header_notice_lbl.setText(msg)
        self.left_stack.setCurrentIndex(1)
        self._notice_timer.start()

    def switch_sub_tab(self, tab_key: str):
        self.tab_bar.set_tab(tab_key, emit=True)

    def _sync_overlays(self):
        r = self.body_card.rect().adjusted(2, 1, -2, -1)
        self.drop_overlay.setGeometry(r)
        bw, bh = self.btn_clear_bottom.width(), self.btn_clear_bottom.height()
        self.btn_clear_bottom.move(
            max(0, self.body_card.width() - bw - 5),
            max(0, self.body_card.height() - bh - 4),
        )
        self.btn_clear_bottom.raise_()

    def _sync_clear_btn(self):
        if self.body_card.drop_active:
            self.btn_clear_bottom.hide()
            self.btn_clear_clip.hide()
            self.shelf_scroll.setViewportMargins(0, 0, 0, 0)
            return
        is_shelf = (self.tab_bar.current_key() == "shelf")
        if is_shelf:
            self.btn_clear_clip.hide()
            self.btn_clear_clip.reset()
            has_data = self._shelf_has_items
            self.btn_clear_bottom.setVisible(has_data)
            self.shelf_scroll.setViewportMargins(0, 0, 42 if has_data else 0, 0)
            if not has_data:
                self.btn_clear_bottom.reset()
        else:
            self.btn_clear_bottom.hide()
            self.btn_clear_bottom.reset()
            self.shelf_scroll.setViewportMargins(0, 0, 0, 0)
            has_data = self._clip_has_items
            self.btn_clear_clip.setVisible(has_data)
            if not has_data:
                self.btn_clear_clip.reset()
        self._sync_overlays()

    def _on_drop_state_changed(self, active: bool):
        if active:
            self.sub_stack.hide()
            self.btn_clear_bottom.hide()
            self._sync_overlays()
            self.drop_overlay.show()
            self.drop_overlay.raise_()
        else:
            self.drop_overlay.hide()
            self.sub_stack.show()
            self._sync_clear_btn()

    def set_drop_hover(self, on: bool):
        if on and self.tab_bar.current_key() != "shelf":
            self.switch_sub_tab("shelf")
        self.body_card.set_drop_active(on)

    def _on_tab_changed(self, key: str):
        is_shelf = (key == "shelf")
        self.sub_stack.setCurrentIndex(0 if is_shelf else 1)
        self.btn_add_file.setVisible(is_shelf)
        self.btn_clear_bottom.reset()
        if is_shelf and self._shelf_dirty:
            self.refresh_shelf()
        elif not is_shelf and self._clip_dirty:
            self.refresh_clipboard()
        self.shelf_scroll.clamp_scroll()
        self.clip_scroll.clamp_scroll()
        self._sync_clear_btn()

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        self.refresh_shelf()
        if self._clip_dirty:
            self.refresh_clipboard()
        self.shelf_scroll.reset_scroll()
        self.clip_scroll.reset_scroll()
        self._sync_overlays()

    def _on_shelf_changed(self):
        self._shelf_dirty = True
        if self.active or self.isVisible():
            self.refresh_shelf()

    def _on_clipboard_changed(self):
        self._clip_dirty = True
        # 工具页隐藏时不触发 UI 重建和重绘
        if self.active and self.tab_bar.current_key() == "clipboard":
            self.refresh_clipboard()

    @staticmethod
    def _clear_layout(lay: QtWidgets.QLayout):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def refresh_shelf(self):
        self._shelf_dirty = False
        self._clear_layout(self.shelf_lay)
        shelf = getattr(self.ctx, "shelf", None)
        if shelf is None or not shelf.is_enabled():
            self._shelf_has_items = False
            lbl = Label("文件置物架已关闭（可在设置「工具与生活」中开启）", 11.5, W.Normal, 3,
                        align=Qt.AlignmentFlag.AlignCenter)
            self.shelf_lay.addWidget(lbl, 1)
            self.btn_add_file.setEnabled(False)
            self._sync_clear_btn()
            return

        self.btn_add_file.setEnabled(True)
        items = shelf.list_items()
        if not items:
            self._shelf_has_items = False
            empty_box = QtWidgets.QFrame(self.shelf_inner)
            empty_box.setMinimumSize(360, 56)
            empty_box.setMaximumHeight(64)
            empty_box.setStyleSheet(
                f"QFrame{{border:1.5px dashed {theme.qss_color(theme.text3(0.42))};border-radius:10px;background:transparent;}}"
            )
            lbl = Label("把文件拖到这里，或点击右上角 + 添加", 11, W.Normal, 3, align=Qt.AlignmentFlag.AlignCenter)
            empty_box.setLayout(vbox(lbl, margins=(6, 4, 6, 4)))
            self.shelf_lay.addWidget(empty_box, 1)
            self._sync_clear_btn()
            return

        self._shelf_has_items = True
        for it in items:
            row = _ShelfFileRow(it, self.shelf_inner)
            row.open_requested.connect(shelf.open_file)
            row.reveal_requested.connect(shelf.reveal_in_explorer)
            row.remove_requested.connect(shelf.remove_file)
            self.shelf_lay.addWidget(row)
        self.shelf_lay.addStretch(1)
        self.shelf_scroll.clamp_scroll()
        self._sync_clear_btn()

    def refresh_clipboard(self):
        self._clip_dirty = False
        self._clear_layout(self.clip_lay)
        clip = getattr(self.ctx, "clipboard", None)
        if clip is None:
            self._clip_has_items = False
            self._sync_clear_btn()
            return
        items = clip.list_items()
        if not items:
            self._clip_has_items = False
            self.clip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            hint = "复制文字后会出现在这里" if clip.is_enabled() else "剪贴板记录已暂停（可在设置「工具与生活」中开启）"
            lbl = Label(hint, 11.5, W.Normal, 3, align=Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(58)
            self.clip_lay.addWidget(lbl)
            self._sync_clear_btn()
            return

        self._clip_has_items = True
        policy = Qt.ScrollBarPolicy.ScrollBarAsNeeded if len(items) > 4 else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        self.clip_scroll.setVerticalScrollBarPolicy(policy)
        for idx, it in enumerate(items):
            row = _ClipItemRow(idx, it, self.clip_scroll.inner)
            row.copy_requested.connect(clip.copy_from_history)
            row.pin_requested.connect(clip.toggle_pin)
            row.delete_requested.connect(clip.remove_at)
            self.clip_lay.addWidget(row)
        self.clip_lay.addStretch(1)
        self.clip_scroll.clamp_scroll()
        self._sync_clear_btn()

    def _on_add_files_dialog(self):
        shelf = getattr(self.ctx, "shelf", None)
        if shelf is None:
            return
        if self.island:
            self.island.locks.add("dialog")
        try:
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self.window(), "添加文件到置物架", "", "所有文件 (*.*)"
            )
        finally:
            if self.island:
                self.island.locks.discard("dialog")
        if paths:
            shelf.add_files(paths)

    def _on_files_dropped(self, files: list[str]):
        shelf = getattr(self.ctx, "shelf", None)
        if shelf is not None:
            self.switch_sub_tab("shelf")
            shelf.add_files(files)

    def _on_clear_confirmed(self):
        if self.tab_bar.current_key() == "shelf":
            shelf = getattr(self.ctx, "shelf", None)
            if shelf is not None and shelf.paths():
                shelf.clear()
        else:
            clip = getattr(self.ctx, "clipboard", None)
            if clip is not None and clip.list_items():
                recent_cnt, _ = clip.counts()
                clip.clear(include_pinned=(recent_cnt == 0))
