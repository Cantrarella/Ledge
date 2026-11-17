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

    def enterEvent(self, e):
        if not self.isEnabled():
            return
        super().enterEvent(e)

    def mousePressEvent(self, e):
        if not self.isEnabled():
            e.ignore()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if not self.isEnabled():
            e.ignore()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        enabled = self.isEnabled()
        s = self.press_scale() if enabled else 1.0
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        if self.active and enabled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color or theme.fill())
            p.drawEllipse(r)
        elif (self.filled or self._hover.x > 0.01) and enabled:
            bg = QtGui.QColor(theme.card() if self.filled else theme.hover())
            if not self.filled:
                bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawEllipse(r)
        p.setFont(icon_font(self.icon_px))
        if not enabled:
            fg = theme.text3(0.35)
        else:
            fg = QtGui.QColor(20, 20, 24) if self.active else (self.color or theme.text(0.9))
            if self.active and self.color is not None:
                fg = QtGui.QColor("white")
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.glyph)


class PillButton(Hoverable):
    """胶囊按钮：图标 + 文字，支持主色（primary）、危险操作描边（danger）与禁用弱化态。"""
    def __init__(self, text: str, glyph: str = "", parent=None, height: int = 30, primary: bool = False,
                 danger: bool = False):
        super().__init__(parent)
        self.text = text
        self.glyph = glyph
        self.primary = primary
        self.danger = danger
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(height)
        self._fit()

    def _fit(self):
        fm = QtGui.QFontMetrics(ui_font(12.5, W.Medium))
        w = fm.horizontalAdvance(self.text) + 34 + (22 if self.glyph else 0)
        self.setMinimumWidth(min(max(w, 84), self.maximumWidth()))

    def set_text(self, t: str):
        self.text = t
        self._fit()
        self.update()

    def mousePressEvent(self, e):
        if not self.isEnabled():
            e.ignore()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if not self.isEnabled():
            e.ignore()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if self.isEnabled() and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        enabled = self.isEnabled()
        s = self.press_scale() if enabled else 1.0
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())

        if not enabled:
            bg = theme.card(0.55)
            fg = theme.text3(0.6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        elif self.danger:
            dc = QtGui.QColor(theme.up())
            bg = QtGui.QColor(dc)
            bg.setAlphaF(0.16 * self._hover.x)
            pen_c = QtGui.QColor(dc)
            pen_c.setAlphaF(0.78 + 0.22 * self._hover.x)
            p.setPen(QtGui.QPen(pen_c, 1.3))
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = dc
        elif self.primary:
            bg = QtGui.QColor(theme.on_color())
            if self._hover.x > 0.01:
                bg = bg.lighter(100 + round(10 * self._hover.x))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = theme.on_fg(0.98)
        else:
            bg = theme.card(1.0 + 0.6 * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = theme.text(0.92)

        if self.hasFocus() and enabled:
            fc = QtGui.QColor(theme.up() if self.danger else theme.on_color())
            p.setPen(QtGui.QPen(fc, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

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
    """需要键盘焦点的输入框：点击时通知灵动岛临时接受焦点，自带清晰键盘焦点描边。"""
    focus_wanted = QtCore.Signal()

    def __init__(self, placeholder: str = "", parent=None, width: int = 200):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setProperty("wants_focus", True)
        self.setFixedHeight(32)
        if width:
            self.setMinimumWidth(min(160, width))
            self.setMaximumWidth(max(240, width))
        self.setFont(ui_font(13))
        theme.changed.connect(self._style)
        self._style()

    def _style(self):
        bg = theme.qss_color(theme.card(1.2))
        fg = theme.qss_color(theme.text())
        bd = theme.qss_color(theme.text(0.14))
        fc = theme.qss_color(theme.on_color())
        self.setStyleSheet(
            f"QLineEdit{{background:{bg};border:1px solid {bd};border-radius:10px;"
            f"padding:0 12px;color:{fg};"
            f"selection-background-color:rgba(79,140,255,120)}}"
            f"QLineEdit:focus{{border:1.5px solid {fc};}}")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.ColorRole.PlaceholderText, theme.text3())
        self.setPalette(pal)

    def mousePressEvent(self, e):
        self.focus_wanted.emit()
        super().mousePressEvent(e)


class PasswordEdit(QtWidgets.QWidget):
    """带显示/隐藏明文切换按钮的密钥输入框（160–240px）。"""
    textChanged = QtCore.Signal(str)

    def __init__(self, placeholder: str = "粘贴 API Key（可选）", parent=None, width: int = 220):
        super().__init__(parent)
        from .theme import G
        self._G = G
        self.setFixedHeight(32)
        self.setMinimumWidth(max(160, min(width, 240)))
        self.setMaximumWidth(max(240, width))
        self.edit = LineEdit(placeholder, self, width=0)
        self.edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.edit.setMinimumWidth(120)
        self.edit.setMaximumWidth(16777215)
        self.btn_eye = IconButton(G.EYE, size=28, icon_px=13, tip="显示 / 隐藏密钥", parent=self)
        self.btn_eye.clicked.connect(self._toggle_echo)
        self.edit.textChanged.connect(self.textChanged.emit)
        self.setLayout(hbox((self.edit, 1), self.btn_eye, spacing=4, margins=(0, 0, 0, 0)))

    def _toggle_echo(self):
        is_pwd = self.edit.echoMode() == QtWidgets.QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal if is_pwd else QtWidgets.QLineEdit.EchoMode.Password)
        self.btn_eye.set_glyph(self._G.EYE_OFF if is_pwd else self._G.EYE)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, t: str):
        self.edit.setText(t)


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


class Switch(Hoverable):
    """自绘平滑弹簧开关控件（支持 Tab 焦点与空格/回车切换）。"""
    toggled = QtCore.Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._knob = Spring(1.0 if self._checked else 0.0, 480, 0.82, 0.008)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(44, 24)
        self.clicked.connect(self.toggle)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
            e.accept()
            return
        super().keyPressEvent(e)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool, emit: bool = False):
        on = bool(on)
        if on != self._checked:
            self._checked = on
            self._knob.to(1.0 if on else 0.0)
            self._kick()
            if emit:
                self.toggled.emit(on)
        else:
            self._knob.snap(1.0 if on else 0.0)
            self.update()

    def toggle(self):
        self.setChecked(not self._checked, emit=True)

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._hover.step(dt)
        self._press.step(dt)
        self._knob.step(dt)
        self.update()
        if self._hover.rest and self._press.rest and self._knob.rest:
            self._timer.stop()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        t = max(0.0, min(1.0, self._knob.x))
        off_c = theme.track()
        on_c = theme.on_color()
        bg = QtGui.QColor(
            round(off_c.red() + (on_c.red() - off_c.red()) * t),
            round(off_c.green() + (on_c.green() - off_c.green()) * t),
            round(off_c.blue() + (on_c.blue() - off_c.blue()) * t),
            round(off_c.alpha() + (on_c.alpha() - off_c.alpha()) * t),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        pad = 3.0
        d = r.height() - pad * 2
        w_stretch = d + 3.0 * self._press.x
        x0 = r.left() + pad
        x1 = r.right() - pad - w_stretch
        kx = x0 + (x1 - x0) * t
        knob_r = QtCore.QRectF(kx, r.top() + pad, w_stretch, d)
        p.setBrush(QtGui.QColor(255, 255, 255, 248))
        p.drawRoundedRect(knob_r, d / 2, d / 2)
        if self.hasFocus():
            p.setPen(QtGui.QPen(theme.on_color(), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


class ComboBox(QtWidgets.QComboBox):
    """主题化下拉框：支持 (value, label) 键值对绑定（建议宽度 160–240px）。"""
    value_changed = QtCore.Signal(object)

    def __init__(self, items: list[tuple[object, str]] | None = None, parent=None, width: int = 180):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setMinimumWidth(max(160, min(width, 240)))
        self.setMaximumWidth(max(240, width))
        self.setFont(ui_font(13))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if items:
            self.set_items(items)
        self.currentIndexChanged.connect(self._on_idx)
        theme.changed.connect(self._style)
        self._style()

    def set_items(self, items: list[tuple[object, str]]):
        self.blockSignals(True)
        self.clear()
        for val, label in items:
            self.addItem(label, val)
        self.blockSignals(False)

    def current_value(self):
        return self.currentData()

    def set_value(self, val):
        for i in range(self.count()):
            if self.itemData(i) == val:
                if self.currentIndex() != i:
                    self.setCurrentIndex(i)
                break

    def _on_idx(self, _):
        self.value_changed.emit(self.currentData())

    def _style(self):
        bg = theme.qss_color(theme.card(1.2))
        fg = theme.qss_color(theme.text())
        bd = theme.qss_color(theme.text(0.14))
        fc = theme.qss_color(theme.on_color())
        m_bg, m_fg, m_sel = theme.menu_qss()
        self.setStyleSheet(
            f"QComboBox{{background:{bg};border:1px solid {bd};border-radius:10px;"
            f"padding:0 12px;color:{fg};}}"
            f"QComboBox:hover,QComboBox:focus{{border:1.5px solid {fc};}}"
            "QComboBox::drop-down{border:none;width:24px;}"
            f"QComboBox QAbstractItemView{{background:{m_bg};color:{m_fg};"
            f"selection-background-color:{m_sel};border:1px solid {bd};border-radius:8px;padding:4px;outline:none;}}"
        )


class SpinBox(QtWidgets.QSpinBox):
    """主题化整数微调框（建议宽度 160–240px）。"""
    def __init__(self, min_v: int = 0, max_v: int = 9999, step: int = 1, suffix: str = "", parent=None, width: int = 168):
        super().__init__(parent)
        self.setRange(min_v, max_v)
        self.setSingleStep(step)
        if suffix:
            self.setSuffix(suffix)
        self.setFixedHeight(32)
        self.setMinimumWidth(max(160, min(width, 240)))
        self.setMaximumWidth(max(240, width))
        self.setFont(ui_font(13, num=True))
        theme.changed.connect(self._style)
        self._style()

    def _style(self):
        bg = theme.qss_color(theme.card(1.2))
        fg = theme.qss_color(theme.text())
        bd = theme.qss_color(theme.text(0.14))
        fc = theme.qss_color(theme.on_color())
        self.setStyleSheet(
            f"QSpinBox{{background:{bg};border:1px solid {bd};border-radius:10px;"
            f"padding:0 10px;color:{fg};}}"
            f"QSpinBox:hover,QSpinBox:focus{{border:1.5px solid {fc};}}"
            "QSpinBox::up-button,QSpinBox::down-button{width:18px;border:none;background:transparent;}"
        )


class DoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """主题化浮点数微调框（建议宽度 160–240px）。"""
    def __init__(self, min_v: float = 0.0, max_v: float = 100.0, step: float = 0.05, decimals: int = 2,
                 suffix: str = "", parent=None, width: int = 168):
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setRange(min_v, max_v)
        self.setSingleStep(step)
        if suffix:
            self.setSuffix(suffix)
        self.setFixedHeight(32)
        self.setMinimumWidth(max(160, min(width, 240)))
        self.setMaximumWidth(max(240, width))
        self.setFont(ui_font(13, num=True))
        theme.changed.connect(self._style)
        self._style()

    def _style(self):
        bg = theme.qss_color(theme.card(1.2))
        fg = theme.qss_color(theme.text())
        bd = theme.qss_color(theme.text(0.14))
        fc = theme.qss_color(theme.on_color())
        self.setStyleSheet(
            f"QDoubleSpinBox{{background:{bg};border:1px solid {bd};border-radius:10px;"
            f"padding:0 10px;color:{fg};}}"
            f"QDoubleSpinBox:hover,QDoubleSpinBox:focus{{border:1.5px solid {fc};}}"
            "QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:18px;border:none;background:transparent;}"
        )


class SettingRow(Card):
    """标准设置项卡片行：
    - 最小高度 60px，说明文字使用原生 QLabel 自动折行，绝不用省略号截断；
    - 支持就地展示校验错误信息（set_error）；
    - 响应式布局：当内容区宽度小于 640px（卡片宽 < 580px）时，右侧控件自动移到说明文字下方。
    """
    def __init__(self, title: str, subtitle: str = "", control: QtWidgets.QWidget | None = None,
                 badge: str = "", parent=None):
        super().__init__(parent, radius=14, alpha=1.0)
        self.setMinimumHeight(60)
        self.control = control
        self._stacked_mode = False

        self.title_lbl = QtWidgets.QLabel(title, self)
        self.title_lbl.setFont(ui_font(13.5, W.DemiBold))
        self.title_lbl.setWordWrap(True)

        top_items: list = [self.title_lbl]
        if badge:
            self.badge_lbl = Label(badge, 11, W.Medium, 2, color=theme.on_color())
            top_items.append(self.badge_lbl)
        top_items.append(None)
        left_items: list = [hbox(*top_items, spacing=8)]

        self.sub_lbl: QtWidgets.QLabel | None = None
        if subtitle:
            self.sub_lbl = QtWidgets.QLabel(subtitle, self)
            self.sub_lbl.setFont(ui_font(12, W.Normal))
            self.sub_lbl.setWordWrap(True)
            self.sub_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
            left_items.append(self.sub_lbl)

        self.err_lbl = QtWidgets.QLabel("", self)
        self.err_lbl.setFont(ui_font(11.5, W.Medium))
        self.err_lbl.setWordWrap(True)
        self.err_lbl.hide()
        left_items.append(self.err_lbl)

        self._left_lay = vbox(*left_items, spacing=4)
        self._ctrl_wrap = QtWidgets.QWidget(self)
        self._ctrl_lay = hbox(control, spacing=0, margins=(0, 0, 0, 0)) if control is not None else hbox()
        self._ctrl_wrap.setLayout(self._ctrl_lay)

        self._main_lay = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.LeftToRight, self)
        self._main_lay.setContentsMargins(16, 12, 16, 12)
        self._main_lay.setSpacing(14)
        self._main_lay.addLayout(self._left_lay, 1)
        if control is not None:
            self._main_lay.addWidget(self._ctrl_wrap, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        theme.changed.connect(self._style_labels)
        self._style_labels()

    def _style_labels(self):
        self.title_lbl.setStyleSheet(f"QLabel{{color:{theme.qss_color(theme.text())};background:transparent;}}")
        if self.sub_lbl is not None:
            self.sub_lbl.setStyleSheet(f"QLabel{{color:{theme.qss_color(theme.text3())};background:transparent;}}")
        self.err_lbl.setStyleSheet(f"QLabel{{color:{theme.qss_color(theme.up())};background:transparent;}}")

    def set_error(self, msg: str):
        msg = (msg or "").strip()
        if msg:
            self.err_lbl.setText(msg)
            self.err_lbl.show()
        else:
            self.err_lbl.clear()
            self.err_lbl.hide()
        self.updateGeometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.control is None:
            return
        # 内容区宽度 < 640px（扣除左右边距后卡片宽 < 580px）时，将控件移至说明文字下方
        want_stack = self.width() < 580
        if want_stack != self._stacked_mode:
            self._stacked_mode = want_stack
            if want_stack:
                self._main_lay.setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom)
                self._main_lay.setSpacing(8)
                self._main_lay.setAlignment(self._ctrl_wrap, Qt.AlignmentFlag.AlignLeft)
            else:
                self._main_lay.setDirection(QtWidgets.QBoxLayout.Direction.LeftToRight)
                self._main_lay.setSpacing(14)
                self._main_lay.setAlignment(self._ctrl_wrap, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)


class ConfirmDialog(QtWidgets.QDialog):
    """主题化轻量确认/提示对话框。"""
    def __init__(self, title: str, message: str, confirm_text: str = "确定", cancel_text: str | None = "取消", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(380)
        t_lbl = Label(title, 15, W.DemiBold, 1)
        m_lbl = Label(message, 13, W.Normal, 2, wrap=True)
        m_lbl.setMinimumHeight(44)
        btns = [None]
        if cancel_text:
            btn_cancel = PillButton(cancel_text, height=32)
            btn_cancel.clicked.connect(self.reject)
            btns.append(btn_cancel)
        btn_ok = PillButton(confirm_text, height=32, primary=True)
        btn_ok.clicked.connect(self.accept)
        btns.append(btn_ok)
        self.setLayout(vbox(t_lbl, m_lbl, 8, hbox(*btns, spacing=10), spacing=10, margins=(20, 18, 20, 18)))
        self._style()

    def _style(self):
        bg = "#1e2028" if theme.dark else "#f5f6fa"
        fg = "#ffffff" if theme.dark else "#14161c"
        self.setStyleSheet(f"QDialog{{background:{bg};color:{fg};}}")

    @classmethod
    def ask(cls, parent, title: str, message: str, confirm_text: str = "确定") -> bool:
        dlg = cls(title, message, confirm_text=confirm_text, cancel_text="取消", parent=parent)
        return dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted

    @classmethod
    def info(cls, parent, title: str, message: str):
        dlg = cls(title, message, confirm_text="知道了", cancel_text=None, parent=parent)
        dlg.exec()


_ = math
