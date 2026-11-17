# -*- coding: utf-8 -*-
"""专用矢量图标绘制：基于 QPainterPath，统一线宽 1.5–2 px，跟随主题配色。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

Qt = QtCore.Qt


def _pen(color: QtGui.QColor, width: float = 1.6) -> QtGui.QPen:
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_checklist(p: QtGui.QPainter, cx: float, cy: float, size: float,
                   color: QtGui.QColor, muted: QtGui.QColor | None = None):
    """绘制清单图标（左勾线 + 右横线）。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    # 第一行对勾 + 横线
    p.setPen(_pen(color, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    ck = QtGui.QPainterPath()
    ck.moveTo(-6.0, -3.2)
    ck.lineTo(-4.3, -1.5)
    ck.lineTo(-1.5, -4.5)
    p.drawPath(ck)
    p.drawLine(QtCore.QPointF(1.0, -3.0), QtCore.QPointF(6.5, -3.0))

    # 第二行圆圈 + 横线
    sec = muted or color
    p.setPen(_pen(sec, 1.5))
    p.drawEllipse(QtCore.QPointF(-3.8, 3.0), 1.9, 1.9)
    p.drawLine(QtCore.QPointF(1.0, 3.0), QtCore.QPointF(6.5, 3.0))
    p.restore()


def draw_droplet(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制水滴图标。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    path = QtGui.QPainterPath()
    path.moveTo(0.0, -6.5)
    path.cubicTo(3.2, -2.5, 5.2, 0.2, 5.2, 2.5)
    path.cubicTo(5.2, 5.4, 2.9, 7.0, 0.0, 7.0)
    path.cubicTo(-2.9, 7.0, -5.2, 5.4, -5.2, 2.5)
    path.cubicTo(-5.2, 0.2, -3.2, -2.5, 0.0, -6.5)
    path.closeSubpath()
    p.setPen(_pen(color, 1.6))
    fill = QtGui.QColor(color)
    fill.setAlphaF(min(1.0, color.alphaF() * 0.22))
    p.setBrush(fill)
    p.drawPath(path)
    p.restore()


def draw_chair(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制椅子/久坐图标。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    p.setPen(_pen(color, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QtGui.QPainterPath()
    # 椅背 + 座面 + 前腿
    path.moveTo(-4.0, -6.0)
    path.lineTo(-4.0, 1.5)
    path.lineTo(4.2, 1.5)
    path.lineTo(4.2, 6.2)
    p.drawPath(path)
    # 后腿
    p.drawLine(QtCore.QPointF(-4.0, 1.5), QtCore.QPointF(-4.0, 6.2))
    # 底座横撑
    p.drawLine(QtCore.QPointF(-4.0, 3.8), QtCore.QPointF(4.2, 3.8))
    p.restore()


def draw_pencil(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制铅笔编辑图标。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    p.setPen(_pen(color, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QtGui.QPainterPath()
    path.moveTo(-5.5, 5.5)
    path.lineTo(-4.8, 2.2)
    path.lineTo(2.8, -5.4)
    path.lineTo(5.4, -2.8)
    path.lineTo(-2.2, 4.8)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QtCore.QPointF(1.2, -3.8), QtCore.QPointF(3.8, -1.2))
    p.restore()


def draw_tray(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制托盘图标。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    p.setPen(_pen(color, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QtGui.QPainterPath()
    path.moveTo(-6.2, 1.0)
    path.lineTo(-6.2, 4.5)
    path.lineTo(6.2, 4.5)
    path.lineTo(6.2, 1.0)
    p.drawPath(path)
    p.drawLine(QtCore.QPointF(0.0, -5.5), QtCore.QPointF(0.0, 1.5))
    p.drawLine(QtCore.QPointF(-2.8, -1.2), QtCore.QPointF(0.0, 1.6))
    p.drawLine(QtCore.QPointF(2.8, -1.2), QtCore.QPointF(0.0, 1.6))
    p.restore()


def draw_document(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制折角文件图标（QPainterPath，不依赖系统字体字形）。"""
    p.save()
    s = size / 16.0
    p.translate(cx, cy)
    p.scale(s, s)
    p.setPen(_pen(color, 1.45))
    fill = QtGui.QColor(color)
    fill.setAlphaF(0.14)
    p.setBrush(fill)

    body = QtGui.QPainterPath()
    body.moveTo(-4.8, -6.5)
    body.lineTo(1.8, -6.5)
    body.lineTo(4.8, -3.5)
    body.lineTo(4.8, 6.5)
    body.lineTo(-4.8, 6.5)
    body.closeSubpath()
    p.drawPath(body)

    p.setBrush(Qt.BrushStyle.NoBrush)
    fold = QtGui.QPainterPath()
    fold.moveTo(1.8, -6.5)
    fold.lineTo(1.8, -3.5)
    fold.lineTo(4.8, -3.5)
    p.drawPath(fold)

    p.drawLine(QtCore.QPointF(-2.4, -0.4), QtCore.QPointF(2.4, -0.4))
    p.drawLine(QtCore.QPointF(-2.4, 2.5), QtCore.QPointF(1.6, 2.5))
    p.restore()
