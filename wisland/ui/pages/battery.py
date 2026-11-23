# -*- coding: utf-8 -*-
"""岛内无线设备电量页（key="battery"，固定 419×176）：
- 顶部 Header：标题、严格居中日期时间、刷新按钮（忙碌旋转）、跳转设置 battery 分类
- 内容区（参考 BrimDeck 极简列式布局，透底无灰框）：圆环电量弧 + 中央矢量图标 + 大号百分比 + 设备名与状态副文案
- 最多同屏舒适展示 4 台设备，超出支持横向滚轮浏览（有余量时消费滚轮，不误切页）
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg
from ...services.battery import BatteryDevice
from ..header import PageHeader
from ..icons import draw_bolt, draw_device_icon
from ..theme import G, W, theme, ui_font
from ..widgets import Hoverable, IconButton, Label, PillButton, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt


class _SpinRefreshButton(IconButton):
    def __init__(self, glyph: str, size: int = 20, icon_px: int = 10, tip: str = "", parent=None):
        super().__init__(glyph, size=size, icon_px=icon_px, tip=tip, parent=parent)
        self._spinning = False
        self._angle = 0.0
        self._spin_timer = QtCore.QTimer(self, interval=30, timeout=self._tick)

    def set_spinning(self, on: bool):
        on = bool(on)
        if self._spinning == on:
            return
        self._spinning = on
        if on:
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self._angle = 0.0
        self.update()

    def _tick(self):
        self._angle = (self._angle + 15.0) % 360.0
        self.update()

    def paintEvent(self, e):
        if not self._spinning:
            return super().paintEvent(e)
        with painter(self) as p:
            hv = self._hover.x
            if hv > 0.01:
                p.setPen(Qt.PenStyle.NoPen)
                bg = theme.hover() if hasattr(theme, "hover") else theme.card()
                p.setBrush(bg)
                p.setOpacity(hv)
                p.drawRoundedRect(self.rect(), 6, 6)
                p.setOpacity(1.0)
            c = self.rect().center()
            p.translate(c.x(), c.y())
            p.rotate(self._angle)
            p.translate(-c.x(), -c.y())
            from ..icons import draw_ui_glyph
            from ..theme import icon_font
            fg = theme.text(0.85)
            if not draw_ui_glyph(p, QtCore.QRectF(self.rect()), self.glyph, self.icon_px, fg):
                p.setFont(icon_font(self.icon_px))
                p.setPen(fg)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph)


def _ring_color(dev: BatteryDevice, low_thresh: int) -> QtGui.QColor:
    if dev.charging:
        return QtGui.QColor("#34d399")
    pct = dev.percent
    if pct is not None:
        if pct <= max(10, low_thresh - 5):
            return QtGui.QColor("#f87171")
        if pct <= max(30, low_thresh + 10):
            return QtGui.QColor("#fbbf24")
    return QtGui.QColor(theme.text(0.95))


def _status_text(dev: BatteryDevice, low_thresh: int) -> tuple[str, QtGui.QColor]:
    if dev.sleeping:
        return "休眠", theme.text3(0.85)
    if dev.charging:
        return "充电中", QtGui.QColor("#34d399")
    if dev.percent is not None:
        if dev.percent <= max(10, low_thresh - 5):
            return "电量低", QtGui.QColor("#f87171")
        if dev.percent <= max(30, low_thresh + 10):
            return "电量偏低", QtGui.QColor("#fbbf24")
    return dev.detail or "已连接", theme.text3(0.95)


class _DeviceCard(QtWidgets.QWidget):
    """单台设备的极简垂直列（圆环 + 图标 + 大百分比 + 名称 + 状态），高度精确适配 419×176。"""

    def __init__(self, dev: BatteryDevice, low_thresh: int, width: int = 92, parent=None):
        super().__init__(parent)
        self.dev = dev
        self.low_thresh = low_thresh
        self.setFixedSize(width, 110)

    def paintEvent(self, e):
        d = self.dev
        alpha = 0.55 if d.sleeping else 1.0
        ring_col = _ring_color(d, self.low_thresh)
        with painter(self) as p:

            w = float(self.width())
            cx = w * 0.5
            cy = 27.0
            radius = 21.0
            arc_rect = QtCore.QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)

            # 1) 圆环轨道 + 电量弧
            track_col = QtGui.QColor(theme.track())
            track_col.setAlphaF(track_col.alphaF() * alpha)
            p.setPen(QtGui.QPen(track_col, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(arc_rect)

            if d.percent is not None and d.percent > 0:
                fg = QtGui.QColor(ring_col)
                fg.setAlphaF(fg.alphaF() * alpha)
                p.setPen(QtGui.QPen(fg, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                span_angle = -int(round(360.0 * 16.0 * max(0, min(100, d.percent)) / 100.0))
                p.drawArc(arc_rect, 90 * 16, span_angle)

            # 2) 中央设备图标
            icon_col = QtGui.QColor(theme.text(0.92 * alpha))
            draw_device_icon(p, d.kind, cx, cy, 18.0, icon_col)

            # 3) 充电中右下角绿底闪电小徽章
            if d.charging:
                bx, by = cx + radius * 0.72, cy + radius * 0.72
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QtGui.QColor("#34d399"))
                p.drawEllipse(QtCore.QPointF(bx, by), 6.0, 6.0)
                draw_bolt(p, bx, by, 7.5, QtGui.QColor("#0f172a"))

            # 4) 百分比数字（轻盈字重 + 居中对齐，小 % 贴右下基线）
            pct_str = str(d.percent) if d.percent is not None else "--"
            num_col = QtGui.QColor(ring_col if (d.charging or (d.percent is not None and d.percent <= max(30, self.low_thresh + 10))) else theme.text(0.95))
            num_col.setAlphaF(num_col.alphaF() * alpha)
            f_num = ui_font(15.5, W.Medium, num=True)
            f_sym = ui_font(9.0, W.Normal)
            fm_n = QtGui.QFontMetrics(f_num)
            fm_s = QtGui.QFontMetrics(f_sym)
            nw = fm_n.horizontalAdvance(pct_str)
            sw = fm_s.horizontalAdvance("%") if d.percent is not None else 0
            nx = cx - nw * 0.5
            p.setFont(f_num)
            p.setPen(num_col)
            p.drawText(QtCore.QRectF(nx, 54.0, nw + 2, 19.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, pct_str)
            if sw:
                p.setFont(f_sym)
                p.setPen(theme.text2(0.75 * alpha))
                p.drawText(QtCore.QRectF(nx + nw + 1, 57.0, sw + 2, 15.0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "%")

            # 5) 设备名 + 缩小版状态副文案（参考 BrimDeck 层次感）
            f_name = ui_font(10.5, W.Normal)
            fm_name = QtGui.QFontMetrics(f_name)
            p.setFont(f_name)
            p.setPen(theme.text(0.88 * alpha))
            elided = fm_name.elidedText(d.display_name, Qt.TextElideMode.ElideRight, int(w - 8))
            p.drawText(QtCore.QRectF(4.0, 74.5, w - 8.0, 15.0), Qt.AlignmentFlag.AlignCenter, elided)

            st_txt, st_col = _status_text(d, self.low_thresh)
            st_col = QtGui.QColor(st_col)
            st_col.setAlphaF(st_col.alphaF() * alpha)
            f_sub = ui_font(8.8, W.Normal)
            fm_sub = QtGui.QFontMetrics(f_sub)
            p.setFont(f_sub)
            p.setPen(st_col)
            elided_st = fm_sub.elidedText(st_txt, Qt.TextElideMode.ElideRight, int(w - 6))
            p.drawText(QtCore.QRectF(3.0, 90.0, w - 6.0, 14.0), Qt.AlignmentFlag.AlignCenter, elided_st)


class _HorizontalDeviceStrip(QtWidgets.QScrollArea):
    """横向设备列表容器：最多同屏 4 列，超过时消费滚轮横向滚动，不误触岛体翻页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent;")
        self.viewport().setStyleSheet("background: transparent;")
        self._inner = QtWidgets.QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._lay = QtWidgets.QHBoxLayout(self._inner)
        self._lay.setContentsMargins(6, 0, 6, 0)
        self._lay.setSpacing(6)
        self.setWidget(self._inner)

    def wheelEvent(self, e: QtGui.QWheelEvent):
        sb = self.horizontalScrollBar()
        if sb.maximum() > 0:
            dy = e.angleDelta().y() or e.angleDelta().x()
            if dy:
                sb.setValue(sb.value() - int(dy / 2))
                e.accept()
                return
        e.ignore()


class BatteryPage(Page):
    key, glyph, title, height, panel_width, panel_height = "battery", G.BOLT, "设备电量", 134, 419, 176
    SIZE = (419, 176)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.svc = getattr(ctx, "battery", None)

        self.header = PageHeader(self.ctx, self, show_gear=False, height=22)
        self.lbl_title = Label("设备电量", 11.5, W.DemiBold, 1, parent=self)
        self.btn_refresh = _SpinRefreshButton(G.REFRESH, size=20, icon_px=10, tip="立即扫描设备电量", parent=self)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_settings = IconButton(G.GEAR, size=20, icon_px=10, tip="打开设备电量设置", parent=self)
        self.btn_settings.clicked.connect(self._open_battery_settings)
        self.header.add_left(self.lbl_title)
        self.header.add_right(self.btn_refresh)
        self.header.add_right(self.btn_settings)

        self.strip = _HorizontalDeviceStrip(self)
        self.empty_box = QtWidgets.QWidget(self)
        self.empty_lbl = Label("暂未检测到已连接的无线设备电量", 12, W.Normal, 2, align=Qt.AlignmentFlag.AlignCenter)
        self.btn_scan = PillButton("立即扫描", G.REFRESH, height=26)
        self.btn_scan.clicked.connect(self._on_refresh)
        el = vbox(self.empty_lbl, hbox(None, self.btn_scan, None), spacing=8, margins=(0, 8, 0, 0))
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_box.setLayout(el)

        self.stack = QtWidgets.QStackedLayout()
        self.stack.addWidget(self.strip)
        self.stack.addWidget(self.empty_box)

        main_lay = vbox(self.header, spacing=2, margins=(12, 4, 12, 2))
        main_lay.addLayout(self.stack, 1)
        self.setLayout(main_lay)

        self._dirty = False
        if self.svc is not None:
            self.svc.changed.connect(self._on_svc_changed)
            self.svc.busy_changed.connect(self.btn_refresh.set_spinning)
        self._rebuild()

    def _on_svc_changed(self):
        if self.isVisible():
            self._dirty = False
            self._rebuild()
        else:
            self._dirty = True

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        if self._dirty:
            self._dirty = False
            self._rebuild()
        if self.svc is not None:
            try:
                self.svc.refresh(force_discover=True)
            except TypeError:
                self.svc.refresh()

    def _open_battery_settings(self):
        if self.ctx is not None and hasattr(self.ctx, "open_settings"):
            self.ctx.open_settings("battery")

    def _on_refresh(self):
        if self.svc is not None:
            try:
                self.svc.refresh(force_discover=True)
            except TypeError:
                self.svc.refresh()

    def _rebuild(self):
        devs = self.svc.devices(include_hidden=False) if self.svc is not None else []
        lay = self.strip._lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not devs:
            self.stack.setCurrentWidget(self.empty_box)
            return

        self.stack.setCurrentWidget(self.strip)
        low_thresh = int(cfg.get("battery.low_threshold", 20) or 20)
        n = len(devs)
        card_w = 108 if n <= 3 else 92
        if n <= 4:
            lay.addStretch(1)
        for d in devs:
            lay.addWidget(_DeviceCard(d, low_thresh, width=card_w))
        if n <= 4:
            lay.addStretch(1)
