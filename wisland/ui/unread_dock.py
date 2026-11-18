# -*- coding: utf-8 -*-
"""岛旁未读快捷胶囊：仅展示 QQ / 微信未读，支持裸图标与共享玻璃胶囊平滑过渡、Spring 动画与内部滚动。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui, QtWidgets

from .anim import Spring, clamp
from .icons import draw_svg_icon
from .theme import theme
from .widgets import painter

Qt = QtCore.Qt

DOCK_WIDTH = 40
ITEM_HEIGHT = 36
ICON_SIZE = 22
PAD_Y = 2
MAX_VISIBLE_ITEMS = 3
MAX_DOCK_HEIGHT = PAD_Y * 2 + ITEM_HEIGHT * MAX_VISIBLE_ITEMS  # 112px
SINGLE_DOCK_HEIGHT = 40
SLIDE_DIST = 5.0

_TRACKED_ORDER = ("qq", "wechat")
_SOURCE_LABELS = {
    "qq": "QQ",
    "wechat": "微信",
}


def target_dock_height(active_count: int) -> int:
    """1 项 40px；2 项 76px；3 项及以上固定最大 112px。"""
    if active_count <= 0:
        return 0
    if active_count == 1:
        return SINGLE_DOCK_HEIGHT
    return min(MAX_DOCK_HEIGHT, PAD_Y * 2 + ITEM_HEIGHT * active_count)


@dataclass
class _DockItem:
    source_key: str
    count: int
    y_pos: Spring
    row_h: Spring = field(default_factory=lambda: Spring(float(ITEM_HEIGHT), 420.0, 0.85, 0.08))
    scale: Spring = field(default_factory=lambda: Spring(1.0, 480.0, 0.86, 0.004))
    offset_y: Spring = field(default_factory=lambda: Spring(0.0, 480.0, 0.86, 0.02))
    opacity: Spring = field(default_factory=lambda: Spring(1.0, 620.0, 1.0, 0.008))
    removing: bool = False
    shake_until: float = 0.0


class UnreadDock(QtWidgets.QWidget):
    """展开主岛旁边的独立未读快捷区（仅 QQ / 微信）。"""

    item_clicked = QtCore.Signal(str)  # source_key
    layout_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedWidth(DOCK_WIDTH)
        self.hide()

        self._items: list[_DockItem] = []
        self._counts: dict[str, int] = {}
        self._island_expanded: bool = False
        self._island_progress: float = 0.0
        self._side: str = "right"  # "right" | "left"
        self._hovered_key: str | None = None
        self._pressed_key: str | None = None
        self._scroll_y: float = 0.0
        self._last_tooltip_key: str | None = None

        self._dock_alpha = Spring(0.0, 380.0, 0.92, 0.008)
        self._slide = Spring(0.0, 380.0, 0.88, 0.04)
        self._capsule_h = Spring(0.0, 420.0, 0.84, 0.08)
        self._capsule_bg_alpha = Spring(0.0, 360.0, 0.92, 0.008)

        self._last_tick = time.perf_counter()
        self._timer = QtCore.QTimer(self, interval=16, timeout=self._on_tick)
        theme.changed.connect(self.update)

    # ---------------- 外部状态同步 ----------------
    def active_keys(self) -> list[str]:
        return [it.source_key for it in self._items if not it.removing]

    def item_count(self) -> int:
        return len(self._items)

    def current_height(self) -> int:
        return max(SINGLE_DOCK_HEIGHT, int(round(self._capsule_h.x)) if self._capsule_h.x > 1 else SINGLE_DOCK_HEIGHT)

    def side(self) -> str:
        return self._side

    def set_side(self, side: str):
        s = "left" if side == "left" else "right"
        if s != self._side:
            self._side = s
            self.update()

    def sync_counts(self, counts: dict[str, int] | None):
        """仅接收 QQ / 微信未读（其他系统通知仅走横幅）；确认清零时才触发清除动画。"""
        c = counts or {}
        cleaned: dict[str, int] = {}
        for k in _TRACKED_ORDER:
            v = int(c.get(k, 0) or 0)
            if v > 0:
                cleaned[k] = v
        self._apply_cleaned_counts(cleaned)

    def _apply_cleaned_counts(self, cleaned: dict[str, int]):
        self._counts = cleaned

        # 1) 标记已清零的条目进入清除动画（先缩至 0.92 淡出，约 150ms）
        for it in self._items:
            if it.source_key not in cleaned:
                if not it.removing:
                    it.removing = True
                    it.scale.to(0.92, 680.0, 1.0)
                    it.opacity.to(0.0, 680.0, 1.0)
                    it.row_h.to(0.0, 460.0, 0.86)
                    if self._hovered_key == it.source_key:
                        self._hovered_key = None
                    if self._pressed_key == it.source_key:
                        self._pressed_key = None
            else:
                it.count = cleaned[it.source_key]
                if it.removing:
                    it.removing = False
                    it.scale.to(1.0, 480.0, 0.86)
                    it.opacity.to(1.0, 480.0, 0.92)
                    it.row_h.to(float(ITEM_HEIGHT), 420.0, 0.85)

        # 2) 新增未读条目
        existing_keys = {it.source_key for it in self._items}
        for k, v in cleaned.items():
            if k not in existing_keys:
                idx = len([x for x in self._items if not x.removing])
                init_y = float(PAD_Y + idx * ITEM_HEIGHT)
                item = _DockItem(
                    source_key=k,
                    count=v,
                    y_pos=Spring(init_y, 400.0, 0.84, 0.05),
                )
                item.opacity.snap(0.0)
                item.opacity.to(1.0, 480.0, 0.95)
                item.scale.snap(0.92)
                item.scale.to(1.0, 460.0, 0.85)
                self._items.append(item)

        self._reflow_targets()
        self._sync_visibility_targets()
        self._kick()

    def set_island_state(self, expanded: bool, expand_progress: float = 1.0):
        """与主岛展开/收起状态协调：不能抢先出现在主岛之前。"""
        self._island_expanded = bool(expanded)
        self._island_progress = clamp(float(expand_progress), 0.0, 1.0)
        self._sync_visibility_targets()
        self._kick()

    def trigger_fail_shake(self, source_key: str):
        """打开失败时播放轻微晃动，绝不清除未读。"""
        for it in self._items:
            if it.source_key == source_key and not it.removing:
                it.shake_until = time.monotonic() + 0.28
                self._kick()
                break

    def hit_test_global(self, gp: QtCore.QPoint) -> bool:
        if not self.isVisible():
            return False
        return self.rect().contains(self.mapFromGlobal(gp))

    # ---------------- 内部布局与动画 ----------------
    def _active_items(self) -> list[_DockItem]:
        return [it for it in self._items if not it.removing]

    def _reflow_targets(self):
        active = self._active_items()
        n = len(active)
        if n == 1:
            # 单个裸图标在 40×40 内居中（36×36 区域）
            active[0].y_pos.to(2.0, 400.0, 0.84)
        else:
            for idx, it in enumerate(active):
                it.y_pos.to(float(PAD_Y + idx * ITEM_HEIGHT), 400.0, 0.84)

        tgt_h = target_dock_height(n)
        if tgt_h > 0:
            if self._capsule_h.x <= 1.0:
                self._capsule_h.snap(float(tgt_h))
            else:
                self._capsule_h.to(float(tgt_h), 420.0, 0.84)
            self._capsule_bg_alpha.to(1.0, 360.0, 0.92)
        else:
            self._capsule_bg_alpha.to(0.0, 360.0, 0.92)

        max_scroll = max(0.0, float(PAD_Y * 2 + n * ITEM_HEIGHT - MAX_DOCK_HEIGHT))
        self._scroll_y = clamp(self._scroll_y, 0.0, max_scroll)
        self._update_item_visual_targets()

    def _update_item_visual_targets(self):
        active = self._active_items()
        multi = len(active) >= 2
        for it in active:
            is_hov = (self._hovered_key == it.source_key)
            is_prs = (self._pressed_key == it.source_key)
            if is_prs:
                it.scale.to(0.94, 580.0, 0.88)
                it.offset_y.to(0.0, 480.0, 0.86)
                it.opacity.to(1.0, 480.0, 0.92)
            elif is_hov:
                it.scale.to(1.05, 480.0, 0.84)
                it.offset_y.to(-1.0, 480.0, 0.84)
                it.opacity.to(1.0, 480.0, 0.92)
            else:
                it.scale.to(1.0, 480.0, 0.86)
                it.offset_y.to(0.0, 480.0, 0.86)
                # 多项共处胶囊时，其他未悬停图标透明度降至约 0.65
                target_op = 0.65 if (multi and self._hovered_key is not None) else 1.0
                it.opacity.to(target_op, 420.0, 0.92)

    def _sync_visibility_targets(self):
        active = self._active_items()
        want_visible = self._island_expanded and self._island_progress >= 0.30 and len(active) > 0
        if want_visible:
            if not self.isVisible():
                self._dock_alpha.snap(0.0)
                self._slide.snap(0.0)
                self.show()
            self._dock_alpha.to(1.0, 380.0, 0.92)
            self._slide.to(SLIDE_DIST, 380.0, 0.86)
        else:
            if self.isVisible():
                self._dock_alpha.to(0.0, 420.0, 0.95)
                self._slide.to(0.0, 420.0, 0.92)

    def _needs_frames(self) -> bool:
        now = time.monotonic()
        if not self._dock_alpha.rest or not self._slide.rest or not self._capsule_h.rest or not self._capsule_bg_alpha.rest:
            return True
        for it in self._items:
            if it.shake_until > now:
                return True
            if not (it.y_pos.rest and it.row_h.rest and it.scale.rest and it.offset_y.rest and it.opacity.rest):
                return True
        return False

    def _kick(self):
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()
            self._timer.start()

    @QtCore.Slot()
    def _on_tick(self):
        now = time.perf_counter()
        dt = min(0.05, max(0.001, now - self._last_tick))
        self._last_tick = now

        self._dock_alpha.step(dt)
        self._slide.step(dt)
        self._capsule_h.step(dt)
        self._capsule_bg_alpha.step(dt)

        alive: list[_DockItem] = []
        for it in self._items:
            it.y_pos.step(dt)
            it.row_h.step(dt)
            it.scale.step(dt)
            it.offset_y.step(dt)
            it.opacity.step(dt)
            if it.removing and it.opacity.rest and it.opacity.x <= 0.02 and it.row_h.rest and it.row_h.x <= 0.5:
                continue
            alive.append(it)
        self._items = alive

        new_h = max(SINGLE_DOCK_HEIGHT, int(round(self._capsule_h.x))) if self._items else SINGLE_DOCK_HEIGHT
        if new_h != self.height():
            self.setFixedHeight(new_h)
            self.layout_changed.emit()

        # 全部淡出完成后真正隐藏
        if (not self._active_items() or not self._island_expanded) and self._dock_alpha.rest and self._dock_alpha.x <= 0.02 and not self._items:
            self.hide()
            self._capsule_h.snap(0.0)
            self._timer.stop()
            return
        if not self._island_expanded and self._dock_alpha.rest and self._dock_alpha.x <= 0.02:
            self.hide()
            self._timer.stop()
            return

        self.update()
        if not self._needs_frames():
            self._timer.stop()

    # ---------------- 鼠标与滚轮交互 ----------------
    def _item_at(self, pos: QtCore.QPointF) -> _DockItem | None:
        if pos.x() < 0 or pos.x() > DOCK_WIDTH:
            return None
        for it in self._active_items():
            top = it.y_pos.x - self._scroll_y
            rect = QtCore.QRectF(0.0, top, float(DOCK_WIDTH), float(ITEM_HEIGHT))
            if rect.contains(pos):
                return it
        return None

    def _show_item_tooltip(self, it: _DockItem):
        name = _SOURCE_LABELS.get(it.source_key, it.source_key)
        tip = f"{name} · {it.count} 条未读 · 点击打开"
        if self._last_tooltip_key != f"{it.source_key}:{it.count}":
            self._last_tooltip_key = f"{it.source_key}:{it.count}"
        # 将 Tooltip 放在未读区外侧，绝不覆盖主岛内容
        local_y = it.y_pos.x - self._scroll_y + ITEM_HEIGHT * 0.5 - 14.0
        if self._side == "right":
            gpos = self.mapToGlobal(QtCore.QPoint(DOCK_WIDTH + 6, int(local_y)))
        else:
            gpos = self.mapToGlobal(QtCore.QPoint(-145, int(local_y)))
        QtWidgets.QToolTip.showText(gpos, tip, self)

    def mouseMoveEvent(self, e):
        it = self._item_at(e.position())
        new_key = it.source_key if it is not None else None
        if new_key != self._hovered_key:
            self._hovered_key = new_key
            self._update_item_visual_targets()
            self._kick()
        if it is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._show_item_tooltip(it)
        else:
            self.unsetCursor()
            self._last_tooltip_key = None
            QtWidgets.QToolTip.hideText()
        e.accept()

    def leaveEvent(self, e):
        if self._hovered_key is not None or self._pressed_key is not None:
            self._hovered_key = None
            self._pressed_key = None
            self._update_item_visual_targets()
            self._kick()
        self._last_tooltip_key = None
        QtWidgets.QToolTip.hideText()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            it = self._item_at(e.position())
            if it is not None:
                self._pressed_key = it.source_key
                self._update_item_visual_targets()
                self._kick()
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            prs = self._pressed_key
            self._pressed_key = None
            it = self._item_at(e.position())
            self._update_item_visual_targets()
            self._kick()
            if it is not None and prs == it.source_key:
                # 仅触发打开意图；绝不在点击瞬间提前清除未读
                self.item_clicked.emit(it.source_key)
        e.accept()

    def wheelEvent(self, e):
        active = self._active_items()
        if len(active) > MAX_VISIBLE_ITEMS:
            delta = e.pixelDelta().y()
            if delta == 0 and e.angleDelta().y():
                delta = int(round(e.angleDelta().y() * 0.35))
            if delta:
                max_scroll = max(0.0, float(PAD_Y * 2 + len(active) * ITEM_HEIGHT - MAX_DOCK_HEIGHT))
                self._scroll_y = clamp(self._scroll_y - delta, 0.0, max_scroll)
                self.update()
        # 无论是否达到滚动边界，滚轮由胶囊内部消费，绝不触发主岛翻页
        e.accept()

    # ---------------- 绘制 ----------------
    def paintEvent(self, e):
        alpha = clamp(self._dock_alpha.x, 0.0, 1.0)
        if alpha <= 0.01 or not self._items:
            return
        p = painter(self)
        p.setOpacity(alpha)

        # 与主岛展开协调的横向滑出/收拢偏移（右侧向右移出，左侧向左移出）
        shift = (SLIDE_DIST - self._slide.x) * (-1.0 if self._side == "right" else 1.0)
        p.translate(shift, 0.0)

        h_now = max(float(SINGLE_DOCK_HEIGHT), self._capsule_h.x) if self._items else float(SINGLE_DOCK_HEIGHT)
        bg_alpha = clamp(self._capsule_bg_alpha.x, 0.0, 1.0)

        # 1 项及以上均绘制胶囊，完整复用主岛当前主题玻璃、色调渐变与边缘高光
        if bg_alpha > 0.02:
            cap_r = QtCore.QRectF(1.0, 1.0, DOCK_WIDTH - 2.0, max(float(SINGLE_DOCK_HEIGHT) - 2.0, h_now - 2.0))
            cap_path = QtGui.QPainterPath()
            cap_path.addRoundedRect(cap_r, 18.0, 18.0)

            p.save()
            p.setOpacity(alpha * bg_alpha)
            # 1) 柔和阴影
            sp = cap_path.translated(0, 1.0)
            for wdt, sa in ((8, 5), (4, 9)):
                p.strokePath(sp, QtGui.QPen(QtGui.QColor(0, 0, 0, sa), wdt))

            # 2) 毛玻璃底 + 色调 + 光泽（有主岛父控件时直接复用主岛 _paint_glass，无则主题玻璃渐变兜底）
            parent_island = self.parent()
            if parent_island is not None and hasattr(parent_island, "_paint_glass") and hasattr(parent_island, "backdrop"):
                win_pos = QtCore.QPointF(self.pos()) + QtCore.QPointF(shift, 0.0)
                p.save()
                p.translate(-win_pos)
                parent_island._paint_glass(p, cap_path.translated(win_pos), crossfade=True)
                p.restore()
            else:
                p.fillPath(cap_path, theme.tint_brush(cap_r))

            # 3) 外侧轮廓 + 顶部/底部边缘高光
            p.strokePath(cap_path, QtGui.QPen(theme.outline(), 1.4))
            rg = QtGui.QLinearGradient(0, cap_r.top(), 0, cap_r.bottom())
            rg.setColorAt(0, theme.rim_top())
            rg.setColorAt(1, theme.rim_bottom())
            p.strokePath(cap_path, QtGui.QPen(QtGui.QBrush(rg), 1.0))
            p.restore()
            p.setClipRect(cap_r.adjusted(0.0, 1.0, 0.0, -1.0))

        now_m = time.monotonic()
        active_multi = len(self._active_items()) >= 2

        for it in self._items:
            it_op = clamp(it.opacity.x, 0.0, 1.0)
            if it_op <= 0.01:
                continue
            top = it.y_pos.x - self._scroll_y
            row_rect = QtCore.QRectF(0.0, top, float(DOCK_WIDTH), float(ITEM_HEIGHT))

            # 多项共处胶囊时，悬停行内绘制 4px 内缩中性圆角高亮（theme.hover）
            if active_multi and not it.removing and self._hovered_key == it.source_key:
                hov_r = row_rect.adjusted(4.0, 2.0, -4.0, -2.0)
                p.save()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.hover())
                p.drawRoundedRect(hov_r, 12.0, 12.0)
                p.restore()

            shake_dx = 0.0
            if it.shake_until > now_m:
                rem_t = it.shake_until - now_m
                shake_dx = math.sin((0.28 - rem_t) * 52.0) * 3.0 * (rem_t / 0.28)

            cx = row_rect.center().x() + shake_dx
            cy = row_rect.center().y() + it.offset_y.x
            sc = max(0.5, it.scale.x)
            icon_half = (ICON_SIZE * sc) / 2.0
            icon_rect = QtCore.QRectF(cx - icon_half, cy - icon_half, ICON_SIZE * sc, ICON_SIZE * sc)

            if not draw_svg_icon(p, icon_rect, it.source_key, theme.dark, opacity=alpha * it_op):
                # SVG 缺失时的兜底绘制
                p.save()
                p.setOpacity(alpha * it_op)
                p.setPen(theme.text())
                p.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, _SOURCE_LABELS.get(it.source_key, it.source_key)[:1])
                p.restore()
