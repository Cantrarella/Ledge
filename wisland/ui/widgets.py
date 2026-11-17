# -*- coding: utf-8 -*-
"""通用控件：全部自绘、跟随主题；背景透明，直接落在毛玻璃上。"""
from __future__ import annotations

import math
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .anim import Spring
from .theme import W, icon_font, theme, ui_font

Qt = QtCore.Qt
AA = QtGui.QPainter.RenderHint.Antialiasing


def painter(w: QtWidgets.QWidget) -> QtGui.QPainter:
    p = QtGui.QPainter(w)
    p.setRenderHint(AA)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    return p


class Themed:
    """混入：主题切换时自动重绘。"""
    def _hook_theme(self):
        theme.changed.connect(self._on_theme)

    def _on_theme(self):
        try:
            self.update()
        except RuntimeError:
            pass


class Hoverable(QtWidgets.QWidget, Themed):
    clicked = QtCore.Signal()
    right_clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = Spring(0, 420, 1.0, 0.01)
        self._press = Spring(0, 600, 0.7, 0.01)
        self._timer = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        self._hook_theme()

    def _kick(self):
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._hover.step(dt)
        self._press.step(dt)
        self.update()
        if self._hover.rest and self._press.rest:
            self._timer.stop()

    def enterEvent(self, e):
        self._hover.to(1)
        self._kick()

    def leaveEvent(self, e):
        self._hover.to(0)
        self._press.to(0)
        self._kick()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press.to(1)
            self._kick()
        e.accept()

    def mouseReleaseEvent(self, e):
        self._press.to(0)
        self._kick()
        if self.rect().contains(e.position().toPoint()):
            if e.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()
            elif e.button() == Qt.MouseButton.RightButton:
                self.right_clicked.emit()
        e.accept()

    def press_scale(self) -> float:
        return 1.0 - 0.06 * self._press.x


class IconButton(Hoverable):
    """圆形图标按钮：悬停时出现半透明白底。"""
    def __init__(self, glyph: str, size: int = 32, icon_px: int = 15, tip: str = "", parent=None,
                 filled: bool = False):
        super().__init__(parent)
        self.glyph = glyph
        self.icon_px = icon_px
        self.filled = filled
        self.active = False
        self.color: QtGui.QColor | None = None
        self.setFixedSize(size, size)
        if tip:
            self.setToolTip(tip)

    def set_glyph(self, g: str):
        if g != self.glyph:
            self.glyph = g
            self.update()

    def set_active(self, on: bool):
        if on != self.active:
            self.active = on
            self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = self.press_scale()
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        if self.active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color or theme.fill())
            p.drawEllipse(r)
        elif self.filled or self._hover.x > 0.01:
            bg = QtGui.QColor(theme.card() if self.filled else theme.hover())
            if not self.filled:
                bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawEllipse(r)
        p.setFont(icon_font(self.icon_px))
        fg = QtGui.QColor(20, 20, 24) if self.active else (self.color or theme.text(0.9))
        if self.active and self.color is not None:
            fg = QtGui.QColor("white")
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.glyph)


class PillButton(Hoverable):
    """胶囊按钮：图标 + 文字。"""
    def __init__(self, text: str, glyph: str = "", parent=None, height: int = 30, primary: bool = False):
        super().__init__(parent)
        self.text = text
        self.glyph = glyph
        self.primary = primary
        self.setFixedHeight(height)
        self._fit()

    def _fit(self):
        fm = QtGui.QFontMetrics(ui_font(12.5, W.Medium))
        w = fm.horizontalAdvance(self.text) + 26 + (20 if self.glyph else 0)
        self.setMinimumWidth(min(w, self.maximumWidth()))

    def set_text(self, t: str):
        self.text = t
        self._fit()
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = self.press_scale()
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        bg = theme.fill() if self.primary else theme.card(1.0 + 0.6 * self._hover.x)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        fg = QtGui.QColor(20, 20, 24) if self.primary else theme.text(0.92)
        p.setPen(fg)
        x = r.x() + 13
        if self.glyph:
            p.setFont(icon_font(13))
            p.drawText(QtCore.QRectF(x, r.y(), 16, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph)
            x += 20
        p.setFont(ui_font(12.5, W.Medium))
        tw = r.right() - x - 11
        p.drawText(QtCore.QRectF(x, r.y(), tw, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QtGui.QFontMetrics(p.font()).elidedText(self.text, Qt.TextElideMode.ElideRight, int(tw)))


class Label(QtWidgets.QWidget, Themed):
    """主题文字：level 1/2/3 = 主/次/弱。"""
    clicked = QtCore.Signal()

    def __init__(self, text: str = "", px: float = 13, weight=W.Normal, level: int = 1, parent=None,
                 align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, num: bool = False,
                 color: QtGui.QColor | None = None, wrap: bool = False):
        super().__init__(parent)
        self._text = text
        self.font_ = ui_font(px, weight, num)
        self.level = level
        self.align = align
        self.color = color
        self.wrap = wrap
        fm = QtGui.QFontMetrics(self.font_)
        if not wrap:
            self.setFixedHeight(fm.height() + 2)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred if not wrap else QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed if not wrap else QtWidgets.QSizePolicy.Policy.Expanding)
        self._hook_theme()

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self.font_)
        return QtCore.QSize(fm.horizontalAdvance(self._text) + 4, fm.height() + 2)

    def minimumSizeHint(self):
        return QtCore.QSize(0, QtGui.QFontMetrics(self.font_).height() + 2)

    def setText(self, t: str):
        t = t or ""
        if t != self._text:
            self._text = t
            self.updateGeometry()
            self.update()

    def text(self) -> str:
        return self._text

    def set_color(self, c: QtGui.QColor | None):
        self.color = c
        self.update()

    def mouseReleaseEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = painter(self)
        p.setFont(self.font_)
        c = self.color or (theme.text() if self.level == 1 else theme.text2() if self.level == 2 else theme.text3())
        p.setPen(c)
        r = QtCore.QRectF(self.rect())
        if self.wrap:
            p.drawText(r, int(self.align) | int(Qt.TextFlag.TextWordWrap), self._text)
        else:
            fm = QtGui.QFontMetrics(self.font_)
            p.drawText(r, self.align, fm.elidedText(self._text, Qt.TextElideMode.ElideRight, int(r.width())))


class Card(QtWidgets.QFrame, Themed):
    """轻卡片：极淡的白色圆角底（无描边、无分割线）。"""
    def __init__(self, parent=None, radius: float = 18, alpha: float = 1.0):
        super().__init__(parent)
        self.radius = radius
        self.alpha = alpha
        self._hook_theme()

    def paintEvent(self, e):
        p = painter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.card(self.alpha))
        p.drawRoundedRect(QtCore.QRectF(self.rect()), self.radius, self.radius)


class ScrollArea(QtWidgets.QScrollArea):
    """透明滚动区，细滚动条。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("QScrollArea{background:transparent;border:none}")
        self.viewport().setStyleSheet("background:transparent")
        self.inner = QtWidgets.QWidget()
        self.inner.setAutoFillBackground(False)
        self.inner.setObjectName("scrollInner")
        self.inner.setStyleSheet("#scrollInner{background:transparent}")
        self.setWidget(self.inner)
        self.inner.setAutoFillBackground(False)   # setWidget 会强制打开
        theme.changed.connect(self._style_bar)
        self._style_bar()

    def _style_bar(self):
        c = theme.qss_color(theme.text(0.25))
        self.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical{background:transparent;width:6px;margin:4px 0 4px 0}"
            f"QScrollBar::handle:vertical{{background:{c};border-radius:3px;min-height:24px}}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0}"
            "QScrollBar::add-page,QScrollBar::sub-page{background:transparent}")


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QtWidgets.QHBoxLayout:
    lay = QtWidgets.QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QtWidgets.QVBoxLayout:
    lay = QtWidgets.QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it):
    if it is None:
        lay.addStretch(1)
    elif isinstance(it, int):
        lay.addSpacing(it)
    elif isinstance(it, QtWidgets.QLayout):
        lay.addLayout(it)
    elif isinstance(it, tuple):
        if isinstance(it[0], QtWidgets.QLayout):
            lay.addLayout(it[0], it[1])
        else:
            lay.addWidget(it[0], it[1])
    else:
        lay.addWidget(it)


def rounded_pixmap(img: QtGui.QImage | QtGui.QPixmap | None, size: int, radius: float,
                   dpr: float = 2.0) -> QtGui.QPixmap | None:
    if img is None:
        return None
    src = QtGui.QPixmap.fromImage(img) if isinstance(img, QtGui.QImage) else img
    if src.isNull():
        return None
    px = round(size * dpr)
    out = QtGui.QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QtGui.QPainter(out)
    p.setRenderHint(AA)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    p.setClipPath(path)
    scaled = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class LineEdit(QtWidgets.QLineEdit):
    """需要键盘焦点的输入框：点击时通知灵动岛临时接受焦点。"""
    focus_wanted = QtCore.Signal()

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setProperty("wants_focus", True)
        self.setFixedHeight(32)
        self.setFont(ui_font(13))
        theme.changed.connect(self._style)
        self._style()

    def _style(self):
        self.setStyleSheet(
            f"QLineEdit{{background:{theme.qss_color(theme.card())};border:none;border-radius:16px;"
            f"padding:0 14px;color:{theme.qss_color(theme.text())};"
            f"selection-background-color:rgba(79,140,255,120)}}")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.ColorRole.PlaceholderText, theme.text3())
        self.setPalette(pal)

    def mousePressEvent(self, e):
        self.focus_wanted.emit()
        super().mousePressEvent(e)


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def glyph_pixmap(glyph: str, px: int, color: QtGui.QColor) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(px * 2, px * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setFont(icon_font(px * 2 * 0.8))
    p.setPen(color)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


_ = math
