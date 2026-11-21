# -*- coding: utf-8 -*-
"""灵动岛独立叠加层与事件过滤器（由 island.py 拆出）：玻璃面纱、空白双击过滤、生活提醒浮层。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .icons import draw_life_svg_icon
from .theme import G, W, icon_font, theme, ui_font

Qt = QtCore.Qt


class Veil(QtWidgets.QWidget):
    """内容渐显用的"玻璃面纱"：盖在内容上，画和灵动岛完全相同的玻璃，不透明度从 1 降到 0。
    普通子控件、与灵动岛同一次绘制，不会像 QGraphicsOpacityEffect 那样出现没有玻璃底的帧。"""

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.alpha = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, e):
        if self.alpha <= 0.002:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self.alpha)
        p.translate(-self.x(), -self.y())
        self.island._paint_glass(p, self.island.path, crossfade=False)
        p.end()


class BlankDoubleClick(QtCore.QObject):
    """灵动岛展开后，双击空白区域切换 悬停展开 ↔ 长关；滚轮在内部滚动区外才切换页面。"""

    def __init__(self, island):
        super().__init__(island)
        self.island = island

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.Wheel and isinstance(obj, QtWidgets.QWidget):
            isl = self.island
            if isl.expanded and not isl.locks and obj.window() is isl:
                if self._in_internal_scroll_or_input(obj):
                    return False
                dy = ev.angleDelta().y() or ev.angleDelta().x() or ev.pixelDelta().y() or ev.pixelDelta().x()
                if dy:
                    isl.nav.step_wheel(dy)
                    ev.accept()
                    return True
            return False
        if ev.type() != QtCore.QEvent.Type.MouseButtonDblClick or not isinstance(obj, QtWidgets.QWidget):
            return False
        isl = self.island
        if obj is isl or obj.window() is not isl or ev.button() != Qt.MouseButton.LeftButton:
            return False
        if self._blank(obj, ev):
            isl.toggle_long_off()
            return True
        return False

    def _in_internal_scroll_or_input(self, w: QtWidgets.QWidget) -> bool:
        """判断指针是否位于内部滚动区（含 viewport 及子控件）或输入/下拉等自带滚轮控件内。"""
        cur = w
        while cur is not None and cur is not self.island:
            if isinstance(
                cur,
                (
                    QtWidgets.QAbstractScrollArea,
                    QtWidgets.QLineEdit,
                    QtWidgets.QTextEdit,
                    QtWidgets.QPlainTextEdit,
                    QtWidgets.QAbstractSpinBox,
                    QtWidgets.QComboBox,
                    QtWidgets.QSlider,
                ),
            ):
                return True
            if hasattr(cur, "_max_scroll") and cur._max_scroll() > 0:
                return True
            cur = cur.parentWidget()
        return False

    @staticmethod
    def _blank(w, ev) -> bool:
        from .nav import NavBar
        from .pages.base import Page
        from .widgets import Card
        if isinstance(w, NavBar):
            return w._hit(ev.position()) < 0
        if isinstance(w, (Page, Card, QtWidgets.QStackedWidget)):
            return True
        if type(w) is QtWidgets.QWidget:                 # 普通容器（内容区、滚动区内层等）
            return True
        p = w.parentWidget()
        if isinstance(p, QtWidgets.QAbstractScrollArea) and w is p.viewport():
            return True
        return False


class LifeReminderBanner(QtWidgets.QWidget):
    """展开态岛内轻量生活提醒浮层：不改变 419px 面板尺寸、不遮挡顶部齿轮/刷新按钮与底部圆点。"""

    clicked_kind = QtCore.Signal(str)
    dismissed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kind = "water"
        self._title = ""
        self._sub = ""
        self._hover_close = False
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def set_reminder(self, item: dict):
        self._kind = str(item.get("kind") or "water")
        self._title = str(item.get("title") or "")
        self._sub = str(item.get("sub") or "")
        self.update()

    def _close_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self.width() - 30.0, (self.height() - 22.0) / 2.0, 22.0, 22.0)

    def mouseMoveEvent(self, e):
        hv = self._close_rect().adjusted(-3, -3, 3, 3).contains(e.position())
        if hv != self._hover_close:
            self._hover_close = hv
            self.update()

    def leaveEvent(self, e):
        if self._hover_close:
            self._hover_close = False
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._close_rect().adjusted(-3, -3, 3, 3).contains(e.position()):
                self.dismissed.emit()
            else:
                self.clicked_kind.emit(self._kind)
            e.accept()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = QtGui.QColor(34, 36, 44, 242) if theme.dark else QtGui.QColor(250, 251, 255, 244)
        bd = QtGui.QColor(255, 255, 255, 38) if theme.dark else QtGui.QColor(16, 18, 24, 28)
        p.setPen(QtGui.QPen(bd, 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(r, 12.0, 12.0)

        # 左侧 24×24 槽位居中绘制 22×22 SVG（无彩色圆底）
        slot_r = QtCore.QRectF(r.left() + 9.0, r.center().y() - 12.0, 24.0, 24.0)
        icon_r = slot_r.adjusted(1.0, 1.0, -1.0, -1.0)
        draw_life_svg_icon(p, icon_r, self._kind, theme.dark, 1.0)

        cr = self._close_rect()
        if self._hover_close:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.hover())
            p.drawEllipse(cr)
        p.setFont(icon_font(9.0))
        p.setPen(theme.text(0.72 if not self._hover_close else 0.95))
        p.drawText(cr, Qt.AlignmentFlag.AlignCenter, G.CLOSE)

        tx = slot_r.right() + 9.0
        tw = max(40.0, cr.left() - 6.0 - tx)
        f_t = ui_font(12.0, W.DemiBold)
        fm_t = QtGui.QFontMetrics(f_t)
        p.setFont(f_t)
        p.setPen(theme.text())
        if self._sub:
            p.drawText(
                QtCore.QRectF(tx, r.top() + 4.0, tw, 16.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_t.elidedText(self._title, Qt.TextElideMode.ElideRight, int(tw)),
            )
            f_s = ui_font(10.5, W.Normal)
            fm_s = QtGui.QFontMetrics(f_s)
            p.setFont(f_s)
            p.setPen(theme.text2())
            p.drawText(
                QtCore.QRectF(tx, r.top() + 20.0, tw, 15.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_s.elidedText(self._sub, Qt.TextElideMode.ElideRight, int(tw)),
            )
        else:
            p.drawText(
                QtCore.QRectF(tx, r.top(), tw, r.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_t.elidedText(self._title, Qt.TextElideMode.ElideRight, int(tw)),
            )
        p.end()


_Veil = Veil
_BlankDoubleClick = BlankDoubleClick
_LifeReminderBanner = LifeReminderBanner

__all__ = [
    "BlankDoubleClick",
    "LifeReminderBanner",
    "Veil",
    "_BlankDoubleClick",
    "_LifeReminderBanner",
    "_Veil",
]
