# -*- coding: utf-8 -*-
"""专用矢量图标绘制：基于 QPainterPath，统一线宽 1.5–2 px，跟随主题配色。"""
from __future__ import annotations

import math
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
    p.setClipRect(rect)
    p.setOpacity(max(0.0, min(1.0, opacity)))
    ren.render(p, rect)
    p.restore()
    return True


_LIFE_SVG_FILES = {
    ("water", True): "water-cup-dark.svg",
    ("water", False): "water-cup-light.svg",
    ("sit", True): "chair-dark.svg",
    ("sit", False): "chair-light.svg",
}


def _life_icons_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    p1 = base / "wisland" / "assets" / "life-icons"
    if p1.is_dir():
        return p1
    return Path(__file__).resolve().parents[1] / "assets" / "life-icons"


def get_life_svg_renderer(kind: str, dark: bool) -> QtSvg.QSvgRenderer | None:
    """加载并缓存生活提醒 SVG 渲染器（water/sit），保留 SVG 原始颜色。"""
    k = str(kind or "").strip().lower()
    fname = _LIFE_SVG_FILES.get((k, bool(dark)))
    if not fname:
        return None
    cache_key = (f"life:{k}", bool(dark))
    if cache_key in _SVG_CACHE:
        return _SVG_CACHE[cache_key]
    path = _life_icons_dir() / fname
    if not path.is_file():
        _SVG_CACHE[cache_key] = None
        return None
    ren = QtSvg.QSvgRenderer(str(path))
    _SVG_CACHE[cache_key] = ren if ren.isValid() else None
    return _SVG_CACHE[cache_key]


def draw_life_svg_icon(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    kind: str,
    dark: bool,
    opacity: float = 1.0,
) -> bool:
    """保持宽高比在 rect 内居中渲染生活提醒 SVG 图标。"""
    ren = get_life_svg_renderer(kind, dark)
    if ren is None:
        return False
    vb = ren.viewBoxF()
    sw = vb.width() if vb.width() > 0 else float(ren.defaultSize().width() or 1)
    sh = vb.height() if vb.height() > 0 else float(ren.defaultSize().height() or 1)
    scale = min(rect.width() / sw, rect.height() / sh)
    tw = sw * scale
    th = sh * scale
    target = QtCore.QRectF(
        rect.center().x() - tw / 2.0,
        rect.center().y() - th / 2.0,
        tw,
        th,
    )
    p.save()
    p.setClipRect(rect)
    p.setOpacity(max(0.0, min(1.0, opacity)))
    ren.render(p, target)
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
    name = str(provider_key or "").strip().lower()
    p.save()
    p.setClipRect(rect)
    if name == "workbuddy":
        circle_clip = QtGui.QPainterPath()
        circle_clip.addEllipse(rect)
        p.setClipPath(circle_clip, Qt.ClipOperation.IntersectClip)
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


def draw_bolt(p: QtGui.QPainter, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制充电闪电符号（实心）。"""
    p.save()
    s = size / 14.0
    p.translate(cx, cy)
    p.scale(s, s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    bolt = QtGui.QPainterPath()
    bolt.moveTo(1.2, -5.8)
    bolt.lineTo(-3.4, 0.4)
    bolt.lineTo(0.2, 0.4)
    bolt.lineTo(-1.2, 5.8)
    bolt.lineTo(3.6, -0.4)
    bolt.lineTo(0.0, -0.4)
    bolt.closeSubpath()
    p.drawPath(bolt)
    p.restore()


def draw_device_icon(p: QtGui.QPainter, kind: str, cx: float, cy: float, size: float, color: QtGui.QColor):
    """绘制无线外设矢量图标（mouse/keyboard/headset/earbuds/gamepad/bluetooth）。"""
    p.save()
    s = size / 18.0
    p.translate(cx, cy)
    p.scale(s, s)
    fill = QtGui.QColor(color)
    fill.setAlphaF(min(1.0, color.alphaF() * 0.22))

    if kind == "mouse":
        p.setPen(_pen(color, 1.5))
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(-4.2, -6.5, 8.4, 13.0), 4.2, 4.2)
        p.drawLine(QtCore.QPointF(0.0, -4.0), QtCore.QPointF(0.0, -1.6))
    elif kind == "keyboard":
        p.setPen(_pen(color, 1.5))
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(-7.2, -4.4, 14.4, 8.8), 2.0, 2.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for ky in (-1.9, 0.2):
            for kx in (-4.8, -2.4, 0.0, 2.4, 4.8):
                p.drawEllipse(QtCore.QPointF(kx, ky), 0.75, 0.75)
        p.drawRoundedRect(QtCore.QRectF(-3.6, 2.0, 7.2, 1.2), 0.6, 0.6)
    elif kind == "headset":
        p.setPen(_pen(color, 1.55))
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QtGui.QPainterPath()
        arc.arcMoveTo(-5.8, -5.8, 11.6, 11.6, 0)
        arc.arcTo(-5.8, -5.8, 11.6, 11.6, 0, 180)
        p.drawPath(arc)
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(-7.0, -0.2, 2.8, 5.6), 1.3, 1.3)
        p.drawRoundedRect(QtCore.QRectF(4.2, -0.2, 2.8, 5.6), 1.3, 1.3)
    elif kind == "earbuds":
        p.setPen(_pen(color, 1.4))
        p.setBrush(fill)
        for sx in (-1.0, 1.0):
            p.drawEllipse(QtCore.QPointF(sx * 3.4, -3.2), 2.3, 2.3)
            p.drawRoundedRect(QtCore.QRectF(sx * 3.4 - 0.85, -1.2, 1.7, 6.2), 0.85, 0.85)
    elif kind == "gamepad":
        p.setPen(_pen(color, 1.5))
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(-7.2, -4.2, 14.4, 8.4), 3.6, 3.6)
        p.setPen(_pen(color, 1.25))
        p.drawLine(QtCore.QPointF(-4.8, 0.0), QtCore.QPointF(-2.2, 0.0))
        p.drawLine(QtCore.QPointF(-3.5, -1.3), QtCore.QPointF(-3.5, 1.3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QtCore.QPointF(3.8, -1.1), 0.85, 0.85)
        p.drawEllipse(QtCore.QPointF(2.5, 0.4), 0.85, 0.85)
    else:
        p.setPen(_pen(color, 1.45))
        p.setBrush(Qt.BrushStyle.NoBrush)
        bt = QtGui.QPainterPath()
        bt.moveTo(-2.5, -3.2)
        bt.lineTo(2.8, 2.2)
        bt.lineTo(0.0, 5.8)
        bt.lineTo(0.0, -5.8)
        bt.lineTo(2.8, -2.2)
        bt.lineTo(-2.5, 3.2)
        p.drawPath(bt)
    p.restore()


def draw_ui_glyph(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    glyph: str,
    px: float,
    color: QtGui.QColor,
) -> bool:
    """对页头高频小尺寸按钮图标（设置齿轮、垃圾桶、加号、刷新）采用像素对齐的原生矢量绘制，
    彻底消除小字号字体图标在透明玻璃上的模糊与 ClearType 彩边。未命中返回 False 由字体兜底。"""
    if glyph not in ("\uE713", "\uE74D", "\uE710", "+", "\uE72C"):
        return False

    cx = rect.center().x()
    cy = rect.center().y()
    # 小字号槽位（10~11px）在矢量模式下适度放宽至 ~13px 视觉尺寸，同时不超过容器边界
    max_fit = max(8.0, min(rect.width(), rect.height()) - 5.0)
    s = min(max_fit, max(12.5, float(px) + 2.0))

    p.save()
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if glyph in ("\uE710", "+"):
        # 加号：半像素对齐，100% DPI 下横竖线零虚边
        ax = round(cx) + 0.5
        ay = round(cy) + 0.5
        r = max(3.5, round(s * 0.35))
        p.setPen(_pen(color, 1.35))
        p.drawLine(QtCore.QPointF(ax - r, ay), QtCore.QPointF(ax + r, ay))
        p.drawLine(QtCore.QPointF(ax, ay - r), QtCore.QPointF(ax, ay + r))
    elif glyph == "\uE74D":
        # 垃圾桶：清晰顶盖 + 提手 + 桶身 + 双竖纹
        u = s / 14.0
        x0 = cx - 7.0 * u
        y0 = cy - 7.0 * u
        p.setPen(_pen(color, max(1.15, 1.2 * (s / 13.5))))
        lid_y = round(y0 + 3.6 * u) + 0.5
        p.drawLine(QtCore.QPointF(x0 + 2.2 * u, lid_y), QtCore.QPointF(x0 + 11.8 * u, lid_y))
        p.drawRoundedRect(QtCore.QRectF(x0 + 5.0 * u, lid_y - 2.0 * u, 4.0 * u, 2.0 * u), 0.8 * u, 0.8 * u)
        body = QtGui.QPainterPath()
        body.moveTo(x0 + 3.4 * u, lid_y + 0.3 * u)
        body.lineTo(x0 + 4.1 * u, y0 + 11.5 * u)
        body.quadTo(x0 + 4.2 * u, y0 + 12.3 * u, x0 + 5.1 * u, y0 + 12.3 * u)
        body.lineTo(x0 + 8.9 * u, y0 + 12.3 * u)
        body.quadTo(x0 + 9.8 * u, y0 + 12.3 * u, x0 + 9.9 * u, y0 + 11.5 * u)
        body.lineTo(x0 + 10.6 * u, lid_y + 0.3 * u)
        p.drawPath(body)
    elif glyph == "\uE713":
        # 设置齿轮：6 齿几何外轮廓 + 清晰中心环（12~14px 下齿形分明不挤糊）
        p.setPen(_pen(color, max(1.15, 1.2 * (s / 13.5))))
        r_inner = s * 0.17
        r_ring = s * 0.34
        r_tip = s * 0.45
        p.drawEllipse(QtCore.QPointF(cx, cy), r_inner, r_inner)
        teeth = 6
        steps = teeth * 4
        path = QtGui.QPainterPath()
        for i in range(steps):
            # 偏移 -0.5 步，使顶部与底部齿的中线严格垂直于 -90° / +90°，左右完全对称
            ang = -math.pi / 2.0 + (i - 0.5) * (2.0 * math.pi / steps)
            rr = r_tip if (i % 4) in (0, 1) else r_ring
            pt = QtCore.QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif glyph == "\uE72C":
        # 刷新：290° 圆弧 + 右上箭头
        p.setPen(_pen(color, max(1.2, 1.25 * (s / 13.5))))
        r = s * 0.36
        arc_rect = QtCore.QRectF(cx - r, cy - r, r * 2.0, r * 2.0)
        p.drawArc(arc_rect, 35 * 16, 290 * 16)
        ax = cx + r * math.cos(math.radians(-35.0))
        ay = cy + r * math.sin(math.radians(-35.0))
        p.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(ax - s * 0.19, ay - s * 0.02))
        p.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(ax - s * 0.01, ay - s * 0.19))

    p.restore()
    return True
