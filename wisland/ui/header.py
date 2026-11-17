# -*- coding: utf-8 -*-
"""统一展开页顶部栏：左侧页面操作、中间严格居中的本地日期时间、右侧页面操作与设置齿轮。"""
from __future__ import annotations

import datetime as dt

from PySide6 import QtCore, QtWidgets

from ..core.config import cfg
from .theme import G, W, theme, ui_font
from .widgets import IconButton, hbox

Qt = QtCore.Qt
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def format_header_datetime(now: dt.datetime | None = None, hour12: bool | None = None) -> str:
    """格式化顶部栏日期时间，如：'11月18日 周三  07:00'。"""
    now = now or dt.datetime.now()
    if hour12 is None:
        hour12 = bool(cfg.get("compact.hour12", cfg.get("time.hour12", False)))
    wd = _WEEKDAYS[now.weekday()]
    date_part = f"{now.month}月{now.day}日 {wd}"
    if hour12:
        ap = "上午" if now.hour < 12 else "下午"
        h12 = now.hour % 12 or 12
        time_part = f"{ap} {h12:02d}:{now.minute:02d}"
    else:
        time_part = f"{now.hour:02d}:{now.minute:02d}"
    return f"{date_part}  {time_part}"


class PageHeader(QtWidgets.QWidget):
    """统一紧凑顶部栏（默认高 28px）：
    中间日期时间基于整个 Header 宽度严格水平居中，不受左右两侧控件宽度影响。
    """
    HEADER_H = 28

    def __init__(self, ctx=None, parent=None, show_gear: bool = True, height: int = 28):
        super().__init__(parent)
        self.ctx = ctx
        self.setFixedHeight(height)

        self.left_wrap = QtWidgets.QWidget(self)
        self.left_lay = hbox(spacing=6, margins=(0, 0, 0, 0))
        self.left_wrap.setLayout(self.left_lay)

        self.right_wrap = QtWidgets.QWidget(self)
        self.right_lay = hbox(spacing=4, margins=(0, 0, 0, 0))
        self.right_wrap.setLayout(self.right_lay)

        self.center_lbl = QtWidgets.QLabel(self)
        self.center_lbl.setFont(ui_font(11.5, W.Medium, num=True))
        self.center_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.gear_btn: IconButton | None = None
        if show_gear:
            self.gear_btn = IconButton(G.GEAR, 26, 12, "打开 Wisland 设置", parent=self.right_wrap)
            self.gear_btn.clicked.connect(self._open_settings)
            self.right_lay.addWidget(self.gear_btn)

        self._last_text = ""
        self._timer = QtCore.QTimer(self, interval=30000, timeout=self.refresh_clock)
        self._timer.start()
        cfg.changed.connect(self.refresh_clock)
        theme.changed.connect(self._style_clock)
        self._style_clock()
        self.refresh_clock()

    def _open_settings(self):
        if self.ctx is not None and hasattr(self.ctx, "open_settings"):
            self.ctx.open_settings()

    def _style_clock(self):
        self.center_lbl.setStyleSheet(
            f"QLabel{{color:{theme.qss_color(theme.text2())};background:transparent;}}"
        )

    def refresh_clock(self):
        txt = format_header_datetime()
        if txt != self._last_text:
            self._last_text = txt
            self.center_lbl.setText(txt)
            self._layout_parts()

    def add_left(self, item):
        if isinstance(item, QtWidgets.QLayout):
            self.left_lay.addLayout(item)
        else:
            self.left_lay.addWidget(item)
        self._layout_parts()

    def add_right(self, item):
        # 始终保持设置齿轮在最右侧
        idx = self.right_lay.count()
        if self.gear_btn is not None and idx > 0:
            idx -= 1
        if isinstance(item, QtWidgets.QLayout):
            self.right_lay.insertLayout(idx, item)
        else:
            self.right_lay.insertWidget(idx, item)
        self._layout_parts()

    def _layout_parts(self):
        w = self.width()
        h = self.height()
        if w <= 0:
            return
        fm = self.center_lbl.fontMetrics()
        cw = min(max(110, fm.horizontalAdvance(self._last_text) + 12), max(110, w - 120))
        cx = round((w - cw) / 2.0)
        self.center_lbl.setGeometry(cx, 0, cw, h)

        left_w = max(0, cx - 6)
        self.left_wrap.setGeometry(0, 0, left_w, h)

        rx = cx + cw + 6
        right_w = max(0, w - rx)
        self.right_wrap.setGeometry(rx, 0, right_w, h)
        # 右侧控件靠右对齐：确保 right_lay 内无多余拉伸，如有需要通过 setAlignment
        self.right_lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.left_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.center_lbl.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_parts()
