# -*- coding: utf-8 -*-
"""页面与导航设置页：对注册表中真实存在的页面（主页、邮箱、天气、实时字幕、工具、生活）进行拖拽/按键自由排序与显示开关控制。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import ConfigDraft
from ..theme import G, W, icon_font, theme, ui_font
from ..widgets import Card, IconButton, Switch, hbox, painter, vbox
from .base import SettingsPage

Qt = QtCore.Qt

# 真实已注册页面的默认元数据（当独立测试无 ctx.registry 时使用）
_FALLBACK_META = [
    ("home", G.HOME, "主页"),
    ("mail", G.MAIL, "邮箱"),
    ("quota", G.CPU, "AI 额度"),
    ("weather", G.SUN, "天气"),
    ("trans", G.LIVE, "实时字幕"),
    ("tools", G.CLIP, "工具"),
    ("life", G.CHECK, "生活"),
]


class _DragHandle(QtWidgets.QWidget):
    """左侧拖拽手柄图标（六点网格 ⋮⋮）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 32)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住上下拖拽调整顺序")

    def paintEvent(self, e):
        p = painter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.text3(0.85))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        for dx in (-3.0, 3.0):
            for dy in (-6.0, 0.0, 6.0):
                p.drawEllipse(QtCore.QPointF(cx + dx, cy + dy), 1.5, 1.5)


class _PageItemRow(Card):
    """单行页面排序卡片：拖拽手柄 + 图标 + 名称 + 上移 + 下移 + 显示开关。支持鼠标拖拽与键盘上下键移动。"""
    move_requested = QtCore.Signal(str, int)          # (key, delta: -1 or +1)
    drag_moved = QtCore.Signal(str, QtCore.QPoint)    # (key, global_pos)
    drag_finished = QtCore.Signal()
    visibility_toggled = QtCore.Signal(str, bool)

    def __init__(self, key: str, glyph: str, title: str, visible: bool = True, parent=None):
        super().__init__(parent, radius=14, alpha=1.0)
        self.key = key
        self.glyph = glyph
        self.title = title
        self._dragging = False
        self._press_gpos: QtCore.QPoint | None = None
        self.setMinimumHeight(60)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        self.handle = _DragHandle(self)

        self.icon_lbl = QtWidgets.QLabel(glyph, self)
        self.icon_lbl.setFont(icon_font(16))
        self.icon_lbl.setFixedSize(28, 28)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QtWidgets.QLabel(title, self)
        self.title_lbl.setFont(ui_font(13.5, W.DemiBold))

        self.btn_up = IconButton(G.UP, size=30, icon_px=13, tip="上移（快捷键：↑）", parent=self)
        self.btn_down = IconButton(G.DOWN, size=30, icon_px=13, tip="下移（快捷键：↓）", parent=self)
        self.sw_vis = Switch(checked=visible, parent=self)
        self.sw_vis.setToolTip("显示 / 隐藏该页面")

        self.btn_up.clicked.connect(lambda: self.move_requested.emit(self.key, -1))
        self.btn_down.clicked.connect(lambda: self.move_requested.emit(self.key, 1))
        self.sw_vis.toggled.connect(lambda on: self.visibility_toggled.emit(self.key, on))

        self.setLayout(hbox(
            self.handle,
            self.icon_lbl,
            (self.title_lbl, 1),
            self.btn_up,
            self.btn_down,
            8,
            self.sw_vis,
            spacing=8,
            margins=(14, 12, 16, 12),
        ))
        theme.changed.connect(self._style_labels)
        self._style_labels()

    def _style_labels(self):
        tc = theme.qss_color(theme.text())
        ic = theme.qss_color(theme.on_color())
        self.title_lbl.setStyleSheet(f"QLabel{{color:{tc};background:transparent;}}")
        self.icon_lbl.setStyleSheet(f"QLabel{{color:{ic};background:transparent;}}")

    def set_nav_bounds(self, is_first: bool, is_last: bool):
        self.btn_up.setEnabled(not is_first)
        self.btn_down.setEnabled(not is_last)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Up:
            self.move_requested.emit(self.key, -1)
            e.accept()
            return
        if e.key() == Qt.Key.Key_Down:
            self.move_requested.emit(self.key, 1)
            e.accept()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_gpos = e.globalPosition().toPoint()
            self._dragging = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_gpos is not None:
            gpos = e.globalPosition().toPoint()
            if not self._dragging and (gpos - self._press_gpos).manhattanLength() > 5:
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                self.drag_moved.emit(self.key, gpos)
                e.accept()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.drag_finished.emit()
        self._press_gpos = None
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.hasFocus() or self._dragging:
            p = painter(self)
            r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
            c = QtGui.QColor(theme.on_color())
            c.setAlphaF(0.85 if self._dragging else 0.55)
            p.setPen(QtGui.QPen(c, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, self.radius, self.radius)


class PagesNavSettingsPage(SettingsPage):
    key = "pages_nav"
    title = "页面与导航"
    glyph = G.APPS

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)
        self._items_state: list[dict] = []   # [{"key": str, "glyph": str, "title": str, "visible": bool}]
        self._row_widgets: dict[str, _PageItemRow] = {}

        self.list_container = QtWidgets.QWidget(self.inner)
        self.list_lay = vbox(spacing=8, margins=(0, 0, 0, 0))
        self.list_container.setLayout(self.list_lay)

        self.tip_lbl = QtWidgets.QLabel("", self.inner)
        self.tip_lbl.setFont(ui_font(12, W.Medium))
        self.tip_lbl.setWordWrap(True)
        self.tip_lbl.hide()

        self.finish_layout(
            self.section(
                "展开态页面顺序与显示",
                "拖拽行或使用上下按钮调整灵动岛底部圆点导航顺序；关闭开关可隐藏页面（至少保留一个可见页面，首页隐藏时展开默认进入第一个可见页面）",
            ),
            self.list_container,
            self.tip_lbl,
        )
        theme.changed.connect(self._style_tip)
        self._style_tip()

    def _style_tip(self):
        self.tip_lbl.setStyleSheet(f"QLabel{{color:{theme.qss_color(theme.up())};background:transparent;padding-left:4px;}}")

    def _show_tip(self, msg: str):
        if msg:
            self.tip_lbl.setText(msg)
            self.tip_lbl.show()
        else:
            self.tip_lbl.clear()
            self.tip_lbl.hide()

    def _registry(self):
        return getattr(self.ctx, "registry", None) if self.ctx else None

    def _registered_meta(self) -> list[tuple[str, str, str]]:
        reg = self._registry()
        if reg is not None:
            return [(e.key, e.glyph, e.title) for e in reg.all_entries()]
        return list(_FALLBACK_META)

    def _rebuild_rows_ui(self, focus_key: str | None = None):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._row_widgets.clear()

        n = len(self._items_state)
        for idx, item in enumerate(self._items_state):
            row = _PageItemRow(
                key=item["key"],
                glyph=item["glyph"],
                title=item["title"],
                visible=item["visible"],
                parent=self.list_container,
            )
            row.set_nav_bounds(is_first=(idx == 0), is_last=(idx == n - 1))
            row.move_requested.connect(self.move_page)
            row.drag_moved.connect(self._on_row_drag_moved)
            row.visibility_toggled.connect(self.set_page_visible)
            self.list_lay.addWidget(row)
            self._row_widgets[item["key"]] = row
            if focus_key and item["key"] == focus_key:
                row.setFocus(Qt.FocusReason.OtherFocusReason)

    def move_page(self, key: str, delta: int):
        """将指定 key 的页面上移（delta=-1）或下移（delta=+1）。"""
        keys = [x["key"] for x in self._items_state]
        if key not in keys:
            return
        idx = keys.index(key)
        new_idx = idx + delta
        if not (0 <= new_idx < len(self._items_state)):
            return
        self._items_state[idx], self._items_state[new_idx] = self._items_state[new_idx], self._items_state[idx]
        self._show_tip("")
        self._rebuild_rows_ui(focus_key=key)
        self.changed.emit()

    def _on_row_drag_moved(self, key: str, gpos: QtCore.QPoint):
        keys = [x["key"] for x in self._items_state]
        if key not in keys:
            return
        cur_idx = keys.index(key)
        local_y = self.list_container.mapFromGlobal(gpos).y()
        for target_idx, item in enumerate(self._items_state):
            if target_idx == cur_idx:
                continue
            w = self._row_widgets.get(item["key"])
            if w is not None:
                cy = w.geometry().center().y()
                if (target_idx < cur_idx and local_y < cy) or (target_idx > cur_idx and local_y > cy):
                    self._items_state[cur_idx], self._items_state[target_idx] = (
                        self._items_state[target_idx],
                        self._items_state[cur_idx],
                    )
                    # 移动布局中的控件顺序而不销毁正在拖拽的控件
                    self.list_lay.removeWidget(self._row_widgets[key])
                    self.list_lay.insertWidget(target_idx, self._row_widgets[key])
                    n = len(self._items_state)
                    for i, st in enumerate(self._items_state):
                        rw = self._row_widgets.get(st["key"])
                        if rw:
                            rw.set_nav_bounds(is_first=(i == 0), is_last=(i == n - 1))
                    self._show_tip("")
                    self.changed.emit()
                    break

    def set_page_visible(self, key: str, on: bool):
        """切换页面可见性：强制至少保留一个可见页面。"""
        for item in self._items_state:
            if item["key"] == key:
                if not on:
                    other_vis = sum(1 for x in self._items_state if x["key"] != key and x["visible"])
                    if other_vis == 0:
                        item["visible"] = True
                        rw = self._row_widgets.get(key)
                        if rw is not None:
                            rw.sw_vis.setChecked(True, emit=False)
                        self._show_tip("至少需要保留一个可见页面")
                        return
                item["visible"] = bool(on)
                break
        self._show_tip("")
        self.changed.emit()

    def ordered_keys(self) -> list[str]:
        return [x["key"] for x in self._items_state]

    def visible_map(self) -> dict[str, bool]:
        return {x["key"]: bool(x["visible"]) for x in self._items_state}

    def load_from_draft(self, draft: ConfigDraft):
        self._show_tip("")
        reg = self._registry()
        if reg is not None:
            entries = reg.entries_for_draft(draft)
            self._items_state = [
                {"key": e.key, "glyph": e.glyph, "title": e.title, "visible": e.visible}
                for e in entries
            ]
        else:
            meta_map = {k: (g, t) for k, g, t in _FALLBACK_META}
            raw_order = draft.get("pages.order")
            raw_vis = draft.get("pages.visible")
            order_list = raw_order if isinstance(raw_order, list) else []
            vis_map = raw_vis if isinstance(raw_vis, dict) else {}

            ordered_keys: list[str] = []
            for k in order_list:
                if isinstance(k, str) and k in meta_map and k not in ordered_keys:
                    ordered_keys.append(k)
            for k, _, _ in _FALLBACK_META:
                if k not in ordered_keys:
                    ordered_keys.append(k)

            self._items_state = [
                {
                    "key": k,
                    "glyph": meta_map[k][0],
                    "title": meta_map[k][1],
                    "visible": bool(vis_map.get(k, True)),
                }
                for k in ordered_keys
            ]
            if self._items_state and not any(x["visible"] for x in self._items_state):
                self._items_state[0]["visible"] = True

        self._rebuild_rows_ui()

    def save_to_draft(self, draft: ConfigDraft):
        reg = self._registry()
        ordered_reg = self.ordered_keys()
        vis_reg = self.visible_map()
        if reg is not None:
            reg.save_to_draft(draft, ordered_reg, vis_reg)
            return

        # 无 registry 实例时的等效保留未知字段逻辑
        reg_set = {k for k, _, _ in _FALLBACK_META}
        old_order = draft.get("pages.order")
        old_order_list = old_order if isinstance(old_order, list) else []
        new_order: list[str] = []
        reg_iter = iter([k for k in ordered_reg if k in reg_set])
        for k in old_order_list:
            if isinstance(k, str) and k in reg_set:
                nxt = next(reg_iter, None)
                if nxt is not None and nxt not in new_order:
                    new_order.append(nxt)
            elif isinstance(k, str) and k not in new_order:
                new_order.append(k)
        for nxt in reg_iter:
            if nxt not in new_order:
                new_order.append(nxt)
        for rk, _, _ in _FALLBACK_META:
            if rk not in new_order:
                new_order.append(rk)

        old_vis = draft.get("pages.visible")
        new_vis = dict(old_vis) if isinstance(old_vis, dict) else {}
        for rk, val in vis_reg.items():
            new_vis[rk] = bool(val)

        draft.set("pages.order", new_order)
        draft.set("pages.visible", new_vis)
