# -*- coding: utf-8 -*-
"""专用矢量图标绘制：基于 QPainterPath，统一线宽 1.5–2 px，跟随主题配色。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg

Qt = QtCore.Qt

_SVG_CACHE: dict[tuple[str, bool | None], QtSvg.QSvgRenderer | None] = {}


def _icons_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    p1 = base / "wisland" / "assets" / "icons"
    if p1.is_dir():
        return p1
    return Path(__file__).resolve().parents[1] / "assets" / "icons"


def get_notify_svg_renderer(svg_key: str, dark: bool) -> QtSvg.QSvgRenderer | None:
    """加载并缓存通知 SVG 矢量渲染器：保留 SVG 原始颜色，绝不替换 fill；单版本跨主题复用缓存。"""
    name = str(svg_key or "").lower()
    if not name:
        return None

    themed_key = (name, bool(dark))
    if themed_key in _SVG_CACHE:
        return _SVG_CACHE[themed_key]
    shared_key = (name, None)
    if shared_key in _SVG_CACHE:
        return _SVG_CACHE[shared_key]

    d = _icons_dir()
    themed_path = d / f"{name}_{'light' if dark else 'dark'}.svg"
    if themed_path.is_file():
        ren = QtSvg.QSvgRenderer(str(themed_path))
        _SVG_CACHE[themed_key] = ren if ren.isValid() else None
        return _SVG_CACHE[themed_key]

    base_path = d / f"{name}.svg"
    if not base_path.is_file():
        _SVG_CACHE[shared_key] = None
        return None
    ren = QtSvg.QSvgRenderer(str(base_path))
    _SVG_CACHE[shared_key] = ren if ren.isValid() else None
    return _SVG_CACHE[shared_key]


def draw_svg_icon(p: QtGui.QPainter, rect: QtCore.QRectF, svg_key: str, dark: bool, opacity: float = 1.0) -> bool:
    """在指定矩形内高 DPI 矢量渲染缓存的 SVG 图标（保持原始颜色）。"""
    ren = get_notify_svg_renderer(svg_key, dark)
    if ren is None:
        return False
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    ren.render(p, rect)
    p.restore()
    return True


_AI_SVG_FILES = {
    "deepseek": "deepseek.svg",
    "workbuddy": "workbuddy.svg",
    "gemini": "gemini.svg",
    "grok_bot": "grokbot.svg",
    "grokbot": "grokbot.svg",
    "codex": "codex.svg",
}

_AI_MONOCHROME_PROVIDERS = frozenset({"deepseek", "gemini", "grok_bot", "grokbot", "codex"})


def _ai_icons_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    p1 = base / "wisland" / "assets" / "ai-icons"
    if p1.is_dir():
        return p1
    return Path(__file__).resolve().parents[1] / "assets" / "ai-icons"


def get_ai_svg_renderer(provider_key: str, dark: bool = False) -> QtSvg.QSvgRenderer | None:
    """加载 AI 服务商 SVG；仅对单色图标做主题前景色适配，彩色品牌图标保持原样。"""
    name = str(provider_key or "").strip().lower()
    fname = _AI_SVG_FILES.get(name)
    if not fname:
        return None
    cache_key = (f"ai:{name}:{'dark' if dark else 'light'}", None)
    if cache_key in _SVG_CACHE:
        return _SVG_CACHE[cache_key]
    path = _ai_icons_dir() / fname
    if not path.is_file():
        _SVG_CACHE[cache_key] = None
        return None
    if name in _AI_MONOCHROME_PROVIDERS:
        raw = path.read_bytes()
        target = b"#f5f7fb" if dark else b"#111318"
        for token in (b'fill="currentColor"', b'fill="#ffffff"', b'fill="#FFFFFF"', b'fill="#fff"', b'fill="#FFF"'):
            raw = raw.replace(token, b'fill="' + target + b'"')
        ren = QtSvg.QSvgRenderer(QtCore.QByteArray(raw))
    else:
        ren = QtSvg.QSvgRenderer(str(path))
    _SVG_CACHE[cache_key] = ren if ren.isValid() else None
    return _SVG_CACHE[cache_key]


def draw_ai_svg_icon(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    provider_key: str,
    opacity: float = 1.0,
    dark: bool = False,
) -> bool:
    """高 DPI 渲染 AI 图标；单色图标适配明暗主题，彩色图标保留品牌色。"""
    ren = get_ai_svg_renderer(provider_key, dark=dark)
    if ren is None:
        return False
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    ren.render(p, rect)
    p.restore()
    return True


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
