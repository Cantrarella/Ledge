# -*- coding: utf-8 -*-
"""工具页（key="tools"）：包含「文件置物架」与「文字剪贴板」两个内部标签。
- 文件置物架：只保存本地文件绝对路径，支持多选添加、打开、定位、移除、清空与拖入/拖出（CopyAction）。
- 文字剪贴板：只保存纯文字（最近 30 条未固定 + 最多 20 条固定），支持点击复制、固定/取消固定、删除与清空。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ...services.clipboard_history import preview_text
from ..anim import Spring
from ..theme import G, W, icon_font, theme, ui_font
from ..widgets import Card, ConfirmDialog, Hoverable, IconButton, Label, PillButton, ScrollArea, hbox, painter, vbox
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


class _SubTabBar(QtWidgets.QWidget):
    """工具页顶部双标签切换器（文件 / 剪贴板），使用弹簧滑块高亮。"""
    changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = [("shelf", "文件"), ("clipboard", "剪贴板")]
        self.index = 0
        self._pos = Spring(0.0, 420, 0.82, 0.005)
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        self.setFixedSize(148, 30)
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
            p.setFont(ui_font(12.5, W.DemiBold if sel else W.Medium))
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


class _ShelfFileRow(Hoverable):
    """文件置物架单行：点击打开原文件，按住拖出触发 CopyAction，右侧提供定位与移除按钮。"""
    open_requested = QtCore.Signal(str)
    reveal_requested = QtCore.Signal(str)
    remove_requested = QtCore.Signal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.path: str = item["path"]
        self.name: str = item["name"]
        self.dir_str: str = item["dir"]
        self.exists: bool = bool(item["exists"])
        self._drag_start: QtCore.QPoint | None = None
        self.setFixedHeight(42)
        self.setToolTip(self.path if self.exists else f"{self.path}\n（文件已移动或删除）")

        self.btn_reveal = IconButton(G.OPEN, size=28, icon_px=13, tip="在资源管理器中定位", parent=self)
        self.btn_reveal.setEnabled(self.exists)
        self.btn_reveal.clicked.connect(lambda: self.reveal_requested.emit(self.path))

        self.btn_remove = _DangerIconButton(G.CLOSE, size=28, icon_px=12, tip="移除记录（不删除原文件）", parent=self)
        self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self.path))

        self.clicked.connect(lambda: self.open_requested.emit(self.path))
        self.setLayout(hbox(None, self.btn_reveal, self.btn_remove, spacing=4, margins=(10, 6, 8, 6)))

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
            # 严格只允许 CopyAction，绝不移动或删除原文件
            drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = theme.card(1.0 + 0.55 * self._hover.x)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 11, 11)

        # 左侧文件图标
        ic_rect = QtCore.QRectF(r.left() + 10, r.top() + 7, 26, 26)
        p.setBrush(theme.on_color() if self.exists else theme.up())
        p.setOpacity(0.16)
        p.drawRoundedRect(ic_rect, 7, 7)
        p.setOpacity(1.0)
        p.setFont(icon_font(13))
        p.setPen(theme.on_color() if self.exists else theme.up())
        p.drawText(ic_rect, Qt.AlignmentFlag.AlignCenter, G.CLIP)

        text_x = ic_rect.right() + 10
        text_w = max(40.0, r.right() - text_x - 74)

        f_name = ui_font(12.5, W.DemiBold)
        p.setFont(f_name)
        p.setPen(theme.text() if self.exists else theme.text2())
        fm_n = QtGui.QFontMetrics(f_name)
        p.drawText(
            QtCore.QRectF(text_x, r.top() + 4, text_w, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm_n.elidedText(self.name, Qt.TextElideMode.ElideMiddle, int(text_w)),
        )

        f_sub = ui_font(11)
        p.setFont(f_sub)
        fm_s = QtGui.QFontMetrics(f_sub)
        if self.exists:
            p.setPen(theme.text3())
            sub_txt = fm_s.elidedText(self.dir_str, Qt.TextElideMode.ElideMiddle, int(text_w))
        else:
            p.setPen(theme.up())
            sub_txt = "文件已移动或删除"
        p.drawText(
            QtCore.QRectF(text_x, r.top() + 21, text_w, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            sub_txt,
        )


class _ClipItemRow(Hoverable):
    """文字剪贴板单行：点击重新复制，右侧提供固定/取消固定与删除按钮。"""
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
        self.setFixedHeight(38)
        tip_preview = self.full_text[:400] + ("…" if len(self.full_text) > 400 else "")
        self.setToolTip(f"点击复制（共 {len(self.full_text)} 字符）：\n{tip_preview}")

        self.btn_pin = IconButton(G.PIN, size=28, icon_px=12, tip="取消固定" if self.pinned else "固定此条", parent=self)
        self.btn_pin.set_active(self.pinned)
        if self.pinned:
            self.btn_pin.color = theme.on_color()
        self.btn_pin.clicked.connect(lambda: self.pin_requested.emit(self.index))

        self.btn_del = _DangerIconButton(G.TRASH, size=28, icon_px=12, tip="删除此条记录", parent=self)
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self.index))

        self.clicked.connect(lambda: self.copy_requested.emit(self.full_text))
        self.setLayout(hbox(None, self.btn_pin, self.btn_del, spacing=4, margins=(10, 4, 8, 4)))

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = theme.card(1.0 + 0.55 * self._hover.x)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

        if self.pinned:
            bar = QtCore.QRectF(r.left() + 4, r.center().y() - 7, 3.0, 14)
            p.setBrush(theme.on_color())
            p.drawRoundedRect(bar, 1.5, 1.5)

        text_x = r.left() + (13 if self.pinned else 11)
        time_w = 38.0 if self.at else 0.0
        text_w = max(40.0, r.right() - text_x - 74 - time_w)

        f = ui_font(12.5, W.Medium if self.pinned else W.Normal)
        p.setFont(f)
        p.setPen(theme.text())
        fm = QtGui.QFontMetrics(f)
        p.drawText(
            QtCore.QRectF(text_x, r.top(), text_w, r.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(self.preview, Qt.TextElideMode.ElideRight, int(text_w)),
        )

        if self.at:
            p.setFont(ui_font(11, num=True))
            p.setPen(theme.text3())
            p.drawText(
                QtCore.QRectF(r.right() - 72 - time_w, r.top(), time_w, r.height()),
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
        self.setStyleSheet(f"QDialog{{background:{bg};color:{fg};}} Q Label,QCheckBox{{color:{fg};background:transparent;}}")

    def _on_confirm(self):
        self.include_pinned = self.chk_pinned.isChecked()
        self.accept()


class _DropContainerCard(Card):
    """支持本地文件拖放高亮的列表外框卡片。"""
    files_dropped = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent, radius=16, alpha=1.0)
        self.drop_active = False
        self.setAcceptDrops(True)

    def set_drop_active(self, on: bool):
        if on != self.drop_active:
            self.drop_active = on
            self.update()

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

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_active:
            p = painter(self)
            r = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            c = QtGui.QColor(theme.on_color())
            fill = QtGui.QColor(c)
            fill.setAlphaF(0.12)
            p.setPen(QtGui.QPen(c, 2.0, Qt.PenStyle.DashLine))
            p.setBrush(fill)
            p.drawRoundedRect(r, self.radius, self.radius)


class ToolsPage(Page):
    key, glyph, title, height = "tools", G.CLIP, "工具", 290

    def __init__(self, ctx):
        super().__init__(ctx)
        self.setAcceptDrops(True)
        self._clip_dirty = True
        self._shelf_dirty = True

        self.tab_bar = _SubTabBar(self)
        self.tab_bar.changed.connect(self._on_tab_changed)

        self.status_lbl = Label("", 11.5, W.Medium, 2)
        self._status_timer = QtCore.QTimer(self, singleShot=True, interval=3200, timeout=lambda: self.status_lbl.setText(""))

        self.btn_add_file = PillButton("添加文件", G.ADD, height=28, primary=True)
        self.btn_add_file.clicked.connect(self._on_add_files_dialog)

        self.btn_clear = _DangerIconButton(G.TRASH, size=28, icon_px=13, tip="清空当前列表", parent=self)
        self.btn_clear.clicked.connect(self._on_clear_clicked)

        # ---- 内部堆栈：0=文件置物架，1=文字剪贴板 ----
        self.body_card = _DropContainerCard(self)
        self.body_card.files_dropped.connect(self._on_files_dropped)

        self.sub_stack = QtWidgets.QStackedWidget(self.body_card)

        # 1) 文件置物架页（共享同一个 QVBoxLayout，每个文件一个普通列，不做瀑布流）
        self.shelf_scroll = ScrollArea(self.sub_stack)
        self.shelf_lay = vbox(spacing=6, margins=(6, 6, 6, 6))
        self.shelf_scroll.inner.setLayout(self.shelf_lay)
        self.sub_stack.addWidget(self.shelf_scroll)

        # 2) 文字剪贴板页（共享同一个 QVBoxLayout）
        self.clip_scroll = ScrollArea(self.sub_stack)
        self.clip_lay = vbox(spacing=6, margins=(6, 6, 6, 6))
        self.clip_scroll.inner.setLayout(self.clip_lay)
        self.sub_stack.addWidget(self.clip_scroll)

        self.body_card.setLayout(vbox((self.sub_stack, 1), margins=(4, 4, 4, 4)))

        top_bar = hbox(self.tab_bar, (self.status_lbl, 1), self.btn_add_file, self.btn_clear, spacing=8)
        self.setLayout(vbox(top_bar, (self.body_card, 1), spacing=8))

        # 绑定服务信号
        if hasattr(ctx, "shelf") and ctx.shelf is not None:
            ctx.shelf.changed.connect(self._on_shelf_changed)
            ctx.shelf.notice.connect(self.show_notice)
        if hasattr(ctx, "clipboard") and ctx.clipboard is not None:
            ctx.clipboard.changed.connect(self._on_clipboard_changed)
            ctx.clipboard.notice.connect(self.show_notice)

        self.refresh_shelf()
        self.refresh_clipboard()

    def show_notice(self, msg: str, is_error: bool = False):
        self.status_lbl.set_color(theme.up() if is_error else theme.text2())
        self.status_lbl.setText(msg)
        if msg:
            self._status_timer.start()

    def switch_sub_tab(self, tab_key: str):
        self.tab_bar.set_tab(tab_key, emit=True)

    def set_drop_hover(self, on: bool):
        if on and self.tab_bar.current_key() != "shelf":
            self.switch_sub_tab("shelf")
        self.body_card.set_drop_active(on)
        if on:
            self.show_notice("松手将文件加入置物架", False)

    def _on_tab_changed(self, key: str):
        is_shelf = (key == "shelf")
        self.sub_stack.setCurrentIndex(0 if is_shelf else 1)
        self.btn_add_file.setVisible(is_shelf)
        self.btn_clear.setToolTip("清空文件置物架" if is_shelf else "清空剪贴板历史")
        if is_shelf and self._shelf_dirty:
            self.refresh_shelf()
        elif not is_shelf and self._clip_dirty:
            self.refresh_clipboard()

    def on_show(self):
        super().on_show()
        # 每次展示时刷新文件存在状态与待更新的剪贴板记录
        self.refresh_shelf()
        if self._clip_dirty:
            self.refresh_clipboard()

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
            lbl = Label("文件置物架已关闭（可在设置「工具与生活」中开启）", 12.5, W.Normal, 3,
                        align=Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(180)
            self.shelf_lay.addWidget(lbl)
            self.btn_add_file.setEnabled(False)
            self.btn_clear.setEnabled(False)
            return

        self.btn_add_file.setEnabled(True)
        items = shelf.list_items()
        if not items:
            lbl = Label("把文件拖到这里，或点击添加", 13, W.Normal, 3, align=Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(180)
            self.shelf_lay.addWidget(lbl)
            if self.tab_bar.current_key() == "shelf":
                self.btn_clear.setEnabled(False)
            return

        if self.tab_bar.current_key() == "shelf":
            self.btn_clear.setEnabled(True)
        for it in items:
            row = _ShelfFileRow(it, self.shelf_scroll.inner)
            row.open_requested.connect(shelf.open_file)
            row.reveal_requested.connect(shelf.reveal_in_explorer)
            row.remove_requested.connect(shelf.remove_file)
            self.shelf_lay.addWidget(row)
        self.shelf_lay.addStretch(1)

    def refresh_clipboard(self):
        self._clip_dirty = False
        self._clear_layout(self.clip_lay)
        clip = getattr(self.ctx, "clipboard", None)
        if clip is None:
            return
        items = clip.list_items()
        if not items:
            hint = "复制文字后会出现在这里" if clip.is_enabled() else "剪贴板记录已暂停（可在设置「工具与生活」中开启）"
            lbl = Label(hint, 13, W.Normal, 3, align=Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(180)
            self.clip_lay.addWidget(lbl)
            if self.tab_bar.current_key() == "clipboard":
                self.btn_clear.setEnabled(False)
            return

        if self.tab_bar.current_key() == "clipboard":
            self.btn_clear.setEnabled(True)
        for idx, it in enumerate(items):
            row = _ClipItemRow(idx, it, self.clip_scroll.inner)
            row.copy_requested.connect(clip.copy_from_history)
            row.pin_requested.connect(clip.toggle_pin)
            row.delete_requested.connect(clip.remove_at)
            self.clip_lay.addWidget(row)
        self.clip_lay.addStretch(1)

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

    def _on_clear_clicked(self):
        if self.island:
            self.island.locks.add("dialog")
        try:
            if self.tab_bar.current_key() == "shelf":
                shelf = getattr(self.ctx, "shelf", None)
                if shelf is None or not shelf.paths():
                    return
                if ConfirmDialog.ask(
                    self.window(),
                    "确认清空文件置物架？",
                    "将移除置物架中的所有文件记录，不会删除原文件。",
                    confirm_text="清空置物架",
                ):
                    shelf.clear()
            else:
                clip = getattr(self.ctx, "clipboard", None)
                if clip is None or not clip.list_items():
                    return
                _, pinned_cnt = clip.counts()
                dlg = ClearClipboardDialog(pinned_count=pinned_cnt, parent=self.window())
                if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                    clip.clear(include_pinned=dlg.include_pinned)
        finally:
            if self.island:
                self.island.locks.discard("dialog")
