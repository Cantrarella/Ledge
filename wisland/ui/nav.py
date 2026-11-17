# -*- coding: utf-8 -*-
"""底部页码圆点导航：整体水平居中，未选中为半透明小白点，选中平滑展开为高亮纯白短胶囊。"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from .anim import Spring, clamp, lerp
from .theme import theme
from .widgets import painter

Qt = QtCore.Qt

DOT_H = 4.5
DOT_W = 4.5
ACTIVE_W = 17.0
GAP = 5.5
LONG_PRESS_MS = 380


class NavBar(QtWidgets.QWidget):
    changed = QtCore.Signal(str)
    drag_requested = QtCore.Signal(QtCore.QPoint)

    def __init__(self, items: list[tuple[str, str, str]], parent=None, height: int = 18):
        super().__init__(parent)
        self.items = items                       # (key, glyph, label)
        self.index = 0
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.anchor_fn = None
        self.w = [Spring(ACTIVE_W if i == 0 else DOT_W, 380, 0.86, 0.02) for i in range(len(items))]
        self.hover = [Spring(0, 400, 1.0, 0.01) for _ in items]
        self.badges: dict[str, int] = {}
        self._hover_i = -1
        self._press_pos: QtCore.QPoint | None = None
        self._lp = QtCore.QTimer(self, singleShot=True, interval=LONG_PRESS_MS, timeout=self._long_press)
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        theme.changed.connect(self.update)

    # --- 状态 ---
    def keys(self) -> list[str]:
        return [k for k, _, _ in self.items]

    def set_items(self, items: list[tuple[str, str, str]], current_key: str | None = None):
        """动态更新可见导航项与圆点弹簧，不丢失平滑过渡。"""
        items = list(items)
        old_widths = {k: self.w[idx].x for idx, (k, _, _) in enumerate(self.items) if idx < len(self.w)}
        self.items = items
        keys = self.keys()
        if not keys:
            self.index = 0
            self.w = []
            self.hover = []
            self._hover_i = -1
            self.update()
            return
        target_key = current_key if current_key in keys else keys[min(self.index, len(keys) - 1)]
        self.index = keys.index(target_key)
        new_w: list[Spring] = []
        for i, (k, _, _) in enumerate(items):
            target_w = ACTIVE_W if i == self.index else DOT_W
            init_w = old_widths.get(k, target_w)
            sp = Spring(init_w, 380, 0.86, 0.02)
            sp.to(target_w)
            new_w.append(sp)
        self.w = new_w
        self.hover = [Spring(0, 400, 1.0, 0.01) for _ in items]
        self._hover_i = -1
        self._kick()
        self.update()

    def set_current(self, key: str, emit: bool = False):
        keys = self.keys()
        if key not in keys:
            return
        i = keys.index(key)
        if i == self.index:
            return
        self.index = i
        for j, s in enumerate(self.w):
            s.to(ACTIVE_W if j == i else DOT_W)
        self._kick()
        if emit:
            self.changed.emit(key)

    def current(self) -> str:
        return self.items[self.index][0]

    def set_badge(self, key: str, n: int):
        """保留接口兼容，但底部圆点导航不再绘制任何红点。"""
        return

    def _kick(self):
        if not self._t.isActive():
            self._last = time.perf_counter()
            self._t.start()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        rest = True
        for s in self.w + self.hover:
            s.step(dt)
            rest = rest and s.rest
        self.update()
        if rest:
            self._t.stop()

    def _dot_rects(self) -> list[QtCore.QRectF]:
        """返回每个圆点/胶囊的实际绘制矩形，整排在底部水平居中。"""
        widths = [max(3.5, s.x) for s in self.w]
        n = len(widths)
        if n == 0:
            return []
        total = sum(widths) + GAP * (n - 1)
        x = (self.width() - total) / 2.0
        cy = self.height() / 2.0 + 1.0
        out = []
        for w in widths:
            out.append(QtCore.QRectF(x, cy - DOT_H / 2.0, w, DOT_H))
            x += w + GAP
        return out

    def _rects(self) -> list[QtCore.QRectF]:
        """点击与悬停热区（比绘制圆点略大，便于点击）。"""
        return [r.adjusted(-2.5, -5.0, 2.5, 5.0) for r in self._dot_rects()]

    def _hit(self, pos: QtCore.QPointF) -> int:
        for i, r in enumerate(self._rects()):
            if r.contains(pos):
                return i
        return -1

    # --- 交互 ---
    def mouseMoveEvent(self, e):
        i = self._hit(e.position())
        if i != self._hover_i:
            if self._hover_i >= 0:
                self.hover[self._hover_i].to(0)
            if i >= 0:
                self.hover[i].to(1)
                self.setToolTip(self.items[i][2])
            else:
                self.setToolTip("")
            self._hover_i = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self._kick()
        if self._press_pos is not None and (e.position().toPoint() - self._press_pos).manhattanLength() > 4:
            # 在空白处按住并移动 -> 立即拖动灵动岛
            self._press_pos = None
            self.drag_requested.emit(QtGui.QCursor.pos())

    def leaveEvent(self, e):
        if self._hover_i >= 0:
            self.hover[self._hover_i].to(0)
            self._hover_i = -1
            self.setToolTip("")
            self._kick()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        i = self._hit(e.position())
        if i >= 0:
            self.set_current(self.items[i][0], emit=True)
            self._press_pos = None
        else:
            self._press_pos = e.position().toPoint()

    def mouseReleaseEvent(self, e):
        self._lp.stop()
        self._press_pos = None

    def _long_press(self):
        if self._press_pos is not None:
            self._press_pos = None
            self.drag_requested.emit(QtGui.QCursor.pos())

    def step_wheel(self, dy: int):
        if not dy or not self.items:
            return
        now = time.perf_counter()
        if now - getattr(self, "_wheel_t", 0.0) < 0.12:
            return
        self._wheel_t = now
        d = -1 if dy > 0 else 1
        nxt = int(clamp(self.index + d, 0, len(self.items) - 1))
        if nxt != self.index:
            self.set_current(self.items[nxt][0], emit=True)

    def wheelEvent(self, e):
        dy = e.angleDelta().y() or e.angleDelta().x()
        self.step_wheel(dy)
        e.accept()

    # --- 绘制 ---
    def paintEvent(self, e):
        p = painter(self)
        p.setPen(Qt.PenStyle.NoPen)
        dots = self._dot_rects()
        for i, (key, _glyph, _label) in enumerate(self.items):
            r = dots[i]
            on = clamp((r.width() - DOT_W) / max(1.0, ACTIVE_W - DOT_W), 0.0, 1.0)
            hov = clamp(self.hover[i].x, 0.0, 1.0)
            if hov > 0.01 and i != self.index:
                halo = QtGui.QColor(255, 255, 255, round(32 * hov)) if theme.dark else QtGui.QColor(0, 0, 0, round(24 * hov))
                p.setBrush(halo)
                hr = r.adjusted(-1.5, -1.5, 1.5, 1.5)
                p.drawRoundedRect(hr, hr.height() / 2.0, hr.height() / 2.0)

            if theme.dark:
                base_c = (105, 106, 112, 185)
                act_c = (255, 255, 255, 255)
            else:
                base_c = (60, 66, 82, 110)
                act_c = (24, 28, 38, 240)
            t = clamp(on + hov * 0.35 * (1.0 - on), 0.0, 1.0)
            col = QtGui.QColor(
                round(lerp(base_c[0], act_c[0], t)),
                round(lerp(base_c[1], act_c[1], t)),
                round(lerp(base_c[2], act_c[2], t)),
                round(lerp(base_c[3], act_c[3], t)),
            )
            p.setBrush(col)
            rad = r.height() / 2.0
            p.drawRoundedRect(r, rad, rad)
