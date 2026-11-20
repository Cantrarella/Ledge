# -*- coding: utf-8 -*-
"""灵动岛收起态时间字体与配色渲染引擎（移植自 BrimDeck，适配 Ledge 主题）。"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Sequence

from PySide6 import QtCore, QtGui

from .theme import ui_font

Qt = QtCore.Qt
W = QtGui.QFont.Weight

# 7 种固定时间字体风格（按 BrimDeck 预设，不开放任意系统字体）
CLOCK_STYLES: tuple[dict, ...] = (
    {"key": "minimal", "label": "简约", "font_file": None, "px": 15.0, "weight": W.DemiBold, "italic": False, "glow": False},
    {"key": "segment", "label": "数码管", "font_file": None, "px": 14.5, "weight": W.DemiBold, "italic": False, "glow": True},
    {"key": "neon", "label": "霓虹", "font_file": "Orbitron-SemiBold.ttf", "px": 14.5, "weight": W.DemiBold, "italic": False, "glow": True},
    {"key": "serif", "label": "衬线", "font_file": "InstrumentSerif-Italic.ttf", "px": 18.0, "weight": W.Normal, "italic": True, "glow": False},
    {"key": "wide", "label": "宽体", "font_file": "Unbounded-SemiBold.ttf", "px": 13.5, "weight": W.DemiBold, "italic": False, "glow": False},
    {"key": "condensed", "label": "窄体", "font_file": "BebasNeue-Regular.ttf", "px": 18.0, "weight": W.Normal, "italic": False, "glow": False},
    {"key": "rounded", "label": "圆润", "font_file": "Fredoka-SemiBold.ttf", "px": 15.5, "weight": W.DemiBold, "italic": False, "glow": False},
)

CLOCK_STYLE_MAP: dict[str, dict] = {s["key"]: s for s in CLOCK_STYLES}
VALID_CLOCK_STYLES: tuple[str, ...] = tuple(s["key"] for s in CLOCK_STYLES)

# 8 种固定颜色模式
CLOCK_COLORS: tuple[dict, ...] = (
    {"key": "white", "label": "主题默认色", "hex": None},
    {"key": "amber", "label": "琥珀", "hex": "#FFBF5E"},
    {"key": "cyan", "label": "青色", "hex": "#5AE0FF"},
    {"key": "pink", "label": "粉色", "hex": "#FF6BA8"},
    {"key": "lime", "label": "柠檬绿", "hex": "#A8F558"},
    {"key": "cover", "label": "跟随封面", "hex": None},
    {"key": "custom", "label": "自定义颜色", "hex": None},
    {"key": "gradient", "label": "渐变", "hex": None},
)

VALID_CLOCK_COLORS: tuple[str, ...] = tuple(c["key"] for c in CLOCK_COLORS)

# 精选渐变预设（用于设置页快速切换）
CLOCK_GRADIENT_PRESETS: tuple[tuple[str, list[str]], ...] = (
    ("霓虹青粉", ["#5AE0FF", "#FF6BA8"]),
    ("日落琥珀", ["#FFD06E", "#FF6B7B"]),
    ("极光青绿", ["#56F0A8", "#5AE0FF"]),
    ("紫罗兰", ["#A78BFA", "#F472B6"]),
)

_LOADED_FAMILIES: dict[str, str] = {}
_FONT_LOAD_ATTEMPTED: set[str] = set()


def _fonts_dir() -> Path:
    """定位打包或源码环境下的字体资源目录。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    p1 = base / "wisland" / "assets" / "fonts"
    if p1.is_dir():
        return p1
    return Path(__file__).resolve().parents[1] / "assets" / "fonts"


def ensure_font_loaded(font_file: str | None) -> str | None:
    """按需注册指定 TTF 字体文件并返回其族名；失败返回 None。"""
    if not font_file:
        return None
    if font_file in _LOADED_FAMILIES:
        return _LOADED_FAMILIES[font_file]
    if font_file in _FONT_LOAD_ATTEMPTED:
        return None
    if QtGui.QGuiApplication.instance() is None:
        return None

    _FONT_LOAD_ATTEMPTED.add(font_file)
    path = _fonts_dir() / font_file
    if not path.is_file():
        return None
    font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
    if font_id < 0:
        return None
    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        return None
    _LOADED_FAMILIES[font_file] = families[0]
    return families[0]


def ensure_all_fonts_loaded() -> dict[str, str]:
    """加载所有内置时间字体并返回 `{文件名: 字体族名}` 映射。"""
    for spec in CLOCK_STYLES:
        ff = spec.get("font_file")
        if ff:
            ensure_font_loaded(ff)
    return dict(_LOADED_FAMILIES)


def normalize_hex_color(value: object, fallback: str = "#FFBF5E") -> str:
    """将颜色值规范化为 `#RRGGBB`；非法输入返回 fallback。"""
    s = str(value or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        c = QtGui.QColor(s)
        if c.isValid():
            return c.name(QtGui.QColor.NameFormat.HexRgb).upper()
    return fallback


def is_valid_hex_color(value: object) -> bool:
    """判断字符串是否为合法 `#RGB` 或 `#RRGGBB` 十六进制颜色。"""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s.startswith("#") or len(s) not in (4, 7):
        return False
    return QtGui.QColor(s).isValid()


def _relative_luminance(c: QtGui.QColor) -> float:
    """按 WCAG 2.1 计算 sRGB 相对亮度 [0, 1]。"""
    def _lin(ch: float) -> float:
        return ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(c.redF()) + 0.7152 * _lin(c.greenF()) + 0.0722 * _lin(c.blueF())


def _contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def adapt_color_for_theme(color: QtGui.QColor, dark: bool, min_contrast: float = 3.4) -> QtGui.QColor:
    """按灵动岛当前深浅主题自适应调整颜色亮度，保证玻璃背景上的可读性。"""
    c = QtGui.QColor(color)
    if not c.isValid():
        return QtGui.QColor(255, 255, 255) if dark else QtGui.QColor(20, 22, 28)
    bg_lum = 0.035 if dark else 0.84
    if _contrast_ratio(_relative_luminance(c), bg_lum) >= min_contrast:
        return c

    h, s, l, a = c.getHslF()
    if h < 0:
        h = 0.0
    step = 0.04 if dark else -0.04
    for _ in range(25):
        l = max(0.06, min(0.94, l + step))
        cand = QtGui.QColor.fromHslF(h, min(1.0, max(s, 0.25 if s > 0.05 else 0.0)), l, a)
        if _contrast_ratio(_relative_luminance(cand), bg_lum) >= min_contrast:
            return cand
    return QtGui.QColor(245, 247, 252) if dark else QtGui.QColor(28, 32, 42)


def extract_cover_color(pixmap: QtGui.QPixmap | None) -> QtGui.QColor | None:
    """从专辑封面提取主色调（优先高饱和中等亮度像素），无效封面返回 None。"""
    if pixmap is None or pixmap.isNull():
        return None
    img = pixmap.toImage().scaled(
        16, 16, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    ).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None

    r_sum = g_sum = b_sum = weight_sum = 0.0
    r_all = g_all = b_all = n_all = 0.0
    for y in range(h):
        for x in range(w):
            px = img.pixelColor(x, y)
            if px.alpha() < 32:
                continue
            r, g, b = px.redF(), px.greenF(), px.blueF()
            r_all += r
            g_all += g
            b_all += b
            n_all += 1.0
            mx, mn = max(r, g, b), min(r, g, b)
            sat = (mx - mn) / mx if mx > 1e-4 else 0.0
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if 0.08 <= lum <= 0.92 and sat >= 0.18:
                wt = sat * (1.0 - abs(lum - 0.55))
                r_sum += r * wt
                g_sum += g * wt
                b_sum += b * wt
                weight_sum += wt

    if weight_sum > 0.05:
        out = QtGui.QColor.fromRgbF(r_sum / weight_sum, g_sum / weight_sum, b_sum / weight_sum)
        # 适度提饱和度让灵动岛数字更鲜明
        ch, cs, cl, _ = out.getHslF()
        if ch >= 0:
            out = QtGui.QColor.fromHslF(ch, min(0.92, max(cs, 0.52)), min(0.74, max(cl, 0.45)), 1.0)
        return out
    if n_all > 0:
        return QtGui.QColor.fromRgbF(r_all / n_all, g_all / n_all, b_all / n_all)
    return None


def resolve_clock_palette(
    color_key: str,
    theme_obj,
    custom_hex: str = "#FFBF5E",
    gradient_stops: Sequence[str] | None = None,
    cover_color: QtGui.QColor | None = None,
) -> list[QtGui.QColor]:
    """根据颜色模式、当前主题、封面颜色返回适应对比度后的 QColor 列表。"""
    dark = bool(getattr(theme_obj, "dark", True))
    key = color_key if color_key in VALID_CLOCK_COLORS else "white"

    if key == "white":
        return [theme_obj.text(1.0)]

    if key == "cover":
        if cover_color is not None and cover_color.isValid():
            return [adapt_color_for_theme(cover_color, dark=dark)]
        return [theme_obj.text(1.0)]

    if key == "custom":
        c = QtGui.QColor(normalize_hex_color(custom_hex, "#FFBF5E"))
        return [adapt_color_for_theme(c, dark=dark)]

    if key == "gradient":
        stops = list(gradient_stops or [])
        valid = [QtGui.QColor(normalize_hex_color(s, "")) for s in stops if is_valid_hex_color(s)]
        if len(valid) < 2:
            valid = [QtGui.QColor("#5AE0FF"), QtGui.QColor("#FF6BA8")]
        return [adapt_color_for_theme(c, dark=dark) for c in valid[:3]]

    preset = next((item for item in CLOCK_COLORS if item["key"] == key), None)
    hex_val = preset["hex"] if preset and preset["hex"] else "#FFBF5E"
    return [adapt_color_for_theme(QtGui.QColor(hex_val), dark=dark)]


def build_clock_font(style_key: str) -> QtGui.QFont:
    """构造指定风格的 QFont（对于 minimal 使用系统等宽数字字体）。"""
    spec = CLOCK_STYLE_MAP.get(style_key, CLOCK_STYLE_MAP["minimal"])
    if spec["key"] == "minimal":
        f = ui_font(spec["px"], spec["weight"], num=True)
        try:
            f.setFeature(QtGui.QFont.Tag.fromString("tnum"), 1)
        except Exception:
            pass
        return f

    fam = ensure_font_loaded(spec.get("font_file"))
    if fam:
        f = QtGui.QFont(fam)
        f.setPixelSize(round(spec["px"]))
        f.setWeight(spec["weight"])
        f.setItalic(bool(spec["italic"]))
        return f

    # 回落到系统等宽数字字体
    f = ui_font(spec["px"], spec["weight"], num=True)
    f.setItalic(bool(spec["italic"]))
    return f


def measure_clock(style_key: str, text: str = "12:34") -> dict:
    """按固定数字槽宽测量时钟尺寸，确保任意时刻（00:00~23:59）宽度零抖动。"""
    key = style_key if style_key in CLOCK_STYLE_MAP else "minimal"
    if key == "segment":
        digit_w = 8.4
        colon_w = 3.8
        gap = 2.2
        slot_widths = [colon_w if ch == ":" else digit_w for ch in text]
        total_w = sum(slot_widths) + gap * max(0, len(slot_widths) - 1)
        return {
            "style": key,
            "font": None,
            "digit_w": digit_w,
            "colon_w": colon_w,
            "gap": gap,
            "slot_widths": slot_widths,
            "total_w": float(math.ceil(total_w)),
            "height": 13.5,
        }

    f = build_clock_font(key)
    fm = QtGui.QFontMetricsF(f)
    digit_w = max(fm.horizontalAdvance(d) for d in "0123456789")
    colon_w = max(3.0, fm.horizontalAdvance(":"))
    # 某些字体（如 Instrument Serif 斜体 / Unbounded）字符偏紧凑，保留轻微外框余量防止右缘裁切
    gap = 0.8 if key in ("wide", "serif", "condensed") else 0.0
    slot_widths = [colon_w if ch == ":" else digit_w for ch in text]
    extra_tail = 1.6 if key == "serif" else 0.0
    total_w = sum(slot_widths) + gap * max(0, len(slot_widths) - 1) + extra_tail
    return {
        "style": key,
        "font": f,
        "digit_w": float(digit_w),
        "colon_w": float(colon_w),
        "gap": gap,
        "slot_widths": slot_widths,
        "total_w": float(math.ceil(total_w)),
        "height": float(fm.height()),
    }


# 7 段数码管映射：a(顶), b(右上), c(右下), d(底), e(左下), f(左上), g(中)
_SEG_MAP: dict[str, tuple[int, ...]] = {
    "0": (1, 1, 1, 1, 1, 1, 0),
    "1": (0, 1, 1, 0, 0, 0, 0),
    "2": (1, 1, 0, 1, 1, 0, 1),
    "3": (1, 1, 1, 1, 0, 0, 1),
    "4": (0, 1, 1, 0, 0, 1, 1),
    "5": (1, 0, 1, 1, 0, 1, 1),
    "6": (1, 0, 1, 1, 1, 1, 1),
    "7": (1, 1, 1, 0, 0, 0, 0),
    "8": (1, 1, 1, 1, 1, 1, 1),
    "9": (1, 1, 1, 1, 0, 1, 1),
    "-": (0, 0, 0, 0, 0, 0, 1),
}


def _add_segment_digit(path: QtGui.QPainterPath, ch: str, x: float, y: float, w: float, h: float):
    """向 QPainterPath 添加一个 7 段数码管数字（含轻微右倾）。"""
    segs = _SEG_MAP.get(ch)
    if not segs:
        return
    t = max(1.4, min(w, h) * 0.165)  # 笔画粗细
    pad = t * 0.25
    skew = -0.09  # 轻微电子表右倾

    def _pt(px: float, py: float) -> QtCore.QPointF:
        return QtCore.QPointF(x + px + (h - py) * (-skew), y + py)

    def _hseg(sx: float, ex: float, cy: float):
        ht = t * 0.5
        poly = QtGui.QPolygonF([
            _pt(sx + ht, cy - ht),
            _pt(ex - ht, cy - ht),
            _pt(ex, cy),
            _pt(ex - ht, cy + ht),
            _pt(sx + ht, cy + ht),
            _pt(sx, cy),
        ])
        path.addPolygon(poly)

    def _vseg(cx: float, sy: float, ey: float):
        ht = t * 0.5
        poly = QtGui.QPolygonF([
            _pt(cx, sy),
            _pt(cx + ht, sy + ht),
            _pt(cx + ht, ey - ht),
            _pt(cx, ey),
            _pt(cx - ht, ey - ht),
            _pt(cx - ht, sy + ht),
        ])
        path.addPolygon(poly)

    mid_y = h * 0.5
    left_x = t * 0.5
    right_x = w - t * 0.5

    if segs[0]:  # a 顶
        _hseg(left_x + pad, right_x - pad, t * 0.5)
    if segs[1]:  # b 右上
        _vseg(right_x, t * 0.5 + pad, mid_y - pad)
    if segs[2]:  # c 右下
        _vseg(right_x, mid_y + pad, h - t * 0.5 - pad)
    if segs[3]:  # d 底
        _hseg(left_x + pad, right_x - pad, h - t * 0.5)
    if segs[4]:  # e 左下
        _vseg(left_x, mid_y + pad, h - t * 0.5 - pad)
    if segs[5]:  # f 左上
        _vseg(left_x, t * 0.5 + pad, mid_y - pad)
    if segs[6]:  # g 中
        _hseg(left_x + pad, right_x - pad, mid_y)


def _make_brush(rect: QtCore.QRectF, palette: Sequence[QtGui.QColor], alpha: float) -> QtGui.QBrush:
    """根据调色板生成纯色或水平线性渐变画刷。"""
    cols = []
    for c in palette:
        cc = QtGui.QColor(c)
        cc.setAlphaF(max(0.0, min(1.0, cc.alphaF() * alpha)))
        cols.append(cc)
    if not cols:
        return QtGui.QBrush(QtGui.QColor(255, 255, 255, round(255 * alpha)))
    if len(cols) == 1:
        return QtGui.QBrush(cols[0])
    grad = QtGui.QLinearGradient(rect.left(), rect.center().y(), rect.right(), rect.center().y())
    n = len(cols)
    for idx, col in enumerate(cols):
        grad.setColorAt(idx / max(1, n - 1), col)
    return QtGui.QBrush(grad)


def paint_clock(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    text: str,
    style_key: str,
    palette: Sequence[QtGui.QColor],
    dark: bool = True,
    alpha: float = 1.0,
):
    """在 rect 内绘制指定风格和配色的灵动岛时间。"""
    if alpha <= 0.01 or not text:
        return
    m = measure_clock(style_key, text)
    key = m["style"]
    spec = CLOCK_STYLE_MAP.get(key, CLOCK_STYLE_MAP["minimal"])
    glow = bool(spec.get("glow"))

    p.save()
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

    if key == "segment":
        seg_h = m["height"]
        top_y = rect.center().y() - seg_h / 2.0
        # 暗底槽（88:88 轮廓），增强真实数码管质感
        dim_path = QtGui.QPainterPath()
        lit_path = QtGui.QPainterPath()
        cx = rect.x() + max(0.0, (rect.width() - m["total_w"]) * 0.5)
        for idx, ch in enumerate(text):
            sw = m["slot_widths"][idx]
            if ch == ":":
                dot_r = 1.05
                dot_x = cx + sw * 0.5
                lit_path.addEllipse(QtCore.QPointF(dot_x + seg_h * 0.03, top_y + seg_h * 0.30), dot_r, dot_r)
                lit_path.addEllipse(QtCore.QPointF(dot_x - seg_h * 0.02, top_y + seg_h * 0.70), dot_r, dot_r)
            else:
                _add_segment_digit(dim_path, "8", cx, top_y, sw, seg_h)
                _add_segment_digit(lit_path, ch, cx, top_y, sw, seg_h)
            cx += sw + m["gap"]

        dim_col = QtGui.QColor(255, 255, 255, round(18 * alpha)) if dark else QtGui.QColor(0, 0, 0, round(20 * alpha))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dim_col)
        p.drawPath(dim_path)

        if glow:
            glow_brush = _make_brush(rect, palette, alpha * (0.24 if dark else 0.16))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QtGui.QPen(glow_brush, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(lit_path)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_make_brush(rect, palette, alpha))
        p.drawPath(lit_path)
        p.restore()
        return

    # 字体风格：构造 QPainterPath 实现统一槽位 + 渐变填充 + 霓虹发光
    font: QtGui.QFont = m["font"]
    fm = QtGui.QFontMetricsF(font)
    baseline_y = rect.center().y() + (fm.ascent() - fm.descent()) * 0.5
    text_path = QtGui.QPainterPath()
    cx = rect.x() + max(0.0, (rect.width() - m["total_w"]) * 0.5)
    for idx, ch in enumerate(text):
        sw = m["slot_widths"][idx]
        ch_w = fm.horizontalAdvance(ch)
        draw_x = cx + (sw - ch_w) * 0.5
        text_path.addText(draw_x, baseline_y, font, ch)
        cx += sw + m["gap"]

    if glow:
        outer_pen = QtGui.QPen(
            _make_brush(rect, palette, alpha * (0.22 if dark else 0.14)),
            3.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        inner_pen = QtGui.QPen(
            _make_brush(rect, palette, alpha * (0.36 if dark else 0.22)),
            1.4,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(outer_pen)
        p.drawPath(text_path)
        p.setPen(inner_pen)
        p.drawPath(text_path)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_make_brush(rect, palette, alpha))
    p.drawPath(text_path)
    p.restore()


def _blend_color(c1: QtGui.QColor, c2: QtGui.QColor, t: float) -> QtGui.QColor:
    """线性混合两个 QColor。"""
    t = max(0.0, min(1.0, float(t)))
    return QtGui.QColor(
        round(c1.red() * (1.0 - t) + c2.red() * t),
        round(c1.green() * (1.0 - t) + c2.green() * t),
        round(c1.blue() * (1.0 - t) + c2.blue() * t),
        round(c1.alpha() * (1.0 - t) + c2.alpha() * t),
    )


def resolve_weather_colors(
    color_key: str,
    theme_obj,
    clock_palette: Sequence[QtGui.QColor],
    alpha: float = 1.0,
) -> tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
    """返回 (分隔点色, 矢量天气图标色, 温度数字色)，柔和呼应时间主色而不抢重心。"""
    dot_col = theme_obj.text(0.24 * alpha)
    if color_key == "white" or not clock_palette:
        icon_col = theme_obj.text(0.82 * alpha)
        temp_col = theme_obj.text(0.76 * alpha)
        return dot_col, icon_col, temp_col

    base = clock_palette[-1]
    icon_col = _blend_color(theme_obj.text(0.86), base, 0.65)
    icon_col.setAlphaF(0.90 * alpha)
    temp_col = _blend_color(theme_obj.text(0.84), base, 0.60)
    temp_col.setAlphaF(0.88 * alpha)
    return dot_col, icon_col, temp_col


def build_temperature_font(style_key: str) -> QtGui.QFont:
    """构造与时间同源且视觉高度协调的温度字体（minimal/segment 回落轻量等宽）。"""
    key = style_key if style_key in CLOCK_STYLE_MAP else "minimal"
    spec = CLOCK_STYLE_MAP[key]
    scale_px = {
        "minimal": 14.0,
        "segment": 13.5,
        "neon": 13.5,
        "serif": 16.5,
        "wide": 12.5,
        "condensed": 16.5,
        "rounded": 14.5,
    }.get(key, 14.0)

    if key in ("minimal", "segment"):
        f = ui_font(scale_px, W.Medium, num=True)
        try:
            f.setFeature(QtGui.QFont.Tag.fromString("tnum"), 1)
        except Exception:
            pass
        return f

    fam = ensure_font_loaded(spec.get("font_file"))
    if fam:
        f = QtGui.QFont(fam)
        f.setPixelSize(round(scale_px))
        f.setWeight(spec["weight"])
        f.setItalic(bool(spec["italic"]))
        return f

    f = ui_font(scale_px, W.Medium, num=True)
    f.setItalic(bool(spec["italic"]))
    return f


def measure_temperature(style_key: str, temp_text: str) -> dict:
    """测量同步字体下的温度宽度（独立计算数值部分 + 矢量度数圆环）。"""
    key = style_key if style_key in CLOCK_STYLE_MAP else "minimal"
    has_deg = "°" in temp_text
    num_text = temp_text.replace("°", "")
    deg_w = 5.2 if has_deg else 0.0

    if key == "segment":
        dw = 7.6
        gap = 1.9
        w = len(num_text) * dw + max(0, len(num_text) - 1) * gap + deg_w
        return {
            "style": key,
            "font": None,
            "num_text": num_text,
            "has_deg": has_deg,
            "digit_w": dw,
            "gap": gap,
            "deg_w": deg_w,
            "total_w": float(math.ceil(w + 1.0)),
            "height": 12.4,
        }

    f = build_temperature_font(key)
    fm = QtGui.QFontMetricsF(f)
    num_w = fm.horizontalAdvance(num_text) if num_text else 0.0
    extra = 1.2 if key == "serif" else 0.0
    return {
        "style": key,
        "font": f,
        "num_text": num_text,
        "has_deg": has_deg,
        "num_w": float(num_w),
        "deg_w": deg_w,
        "total_w": float(math.ceil(num_w + deg_w + extra + 1.0)),
        "height": float(fm.height()),
    }


def paint_temperature(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    temp_text: str,
    style_key: str,
    color: QtGui.QColor,
):
    """使用与时间同步的字体风格绘制温度数字，并以矢量小圆环绘制度数符号 `°`（避免缺字）。"""
    if not temp_text or color.alpha() <= 0:
        return
    m = measure_temperature(style_key, temp_text)
    key = m["style"]
    num_text = m["num_text"]

    p.save()
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

    if key == "segment":
        seg_h = m["height"]
        top_y = rect.center().y() - seg_h * 0.5
        cx = rect.x()
        path = QtGui.QPainterPath()
        for ch in num_text:
            _add_segment_digit(path, ch, cx, top_y, m["digit_w"], seg_h)
            cx += m["digit_w"] + m["gap"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        if m["has_deg"]:
            deg_cx = cx + 1.4
            deg_cy = top_y + 2.0
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QtGui.QPen(color, 1.2))
            p.drawEllipse(QtCore.QPointF(deg_cx, deg_cy), 1.55, 1.55)
        p.restore()
        return

    font: QtGui.QFont = m["font"]
    fm = QtGui.QFontMetricsF(font)
    baseline_y = rect.center().y() + (fm.ascent() - fm.descent()) * 0.5
    if num_text:
        tpath = QtGui.QPainterPath()
        tpath.addText(rect.x(), baseline_y, font, num_text)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(tpath)
    if m["has_deg"]:
        deg_cx = rect.x() + m["num_w"] + (3.0 if key == "serif" else 2.4)
        deg_cy = baseline_y - fm.ascent() * 0.68
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(color, 1.3))
        p.drawEllipse(QtCore.QPointF(deg_cx, deg_cy), 1.65, 1.65)
    p.restore()


def infer_weather_kind(icon_code: str = "", glyph: str = "") -> str:
    """由 OWM 天气代码或字符回落推断矢量天气类型。"""
    code = str(icon_code or "").lower()
    if code.startswith("01"):
        return "moon" if code.endswith("n") else "sun"
    if code.startswith("02"):
        return "partly_night" if code.endswith("n") else "partly"
    if code.startswith(("03", "04")):
        return "cloud"
    if code.startswith(("09", "10")):
        return "rain"
    if code.startswith("11"):
        return "thunder"
    if code.startswith("13"):
        return "snow"
    if code.startswith("50"):
        return "fog"

    g = str(glyph or "")
    if g in ("\uE706", "☀", "☼"):
        return "sun"
    if g in ("\uE708", "🌙"):
        return "moon"
    if "⛈" in g or "🌩" in g:
        return "thunder"
    if "🌧" in g or "🌦" in g or "☔" in g:
        return "rain"
    if "❄" in g or "🌨" in g:
        return "snow"
    if "🌫" in g:
        return "fog"
    return "cloud"


def _cloud_path(cx: float, cy: float, scale: float = 1.0) -> QtGui.QPainterPath:
    """构造简洁圆润的矢量云朵轮廓路径。"""
    path = QtGui.QPainterPath()
    s = scale
    path.moveTo(cx - 4.6 * s, cy + 3.0 * s)
    path.lineTo(cx + 4.2 * s, cy + 3.0 * s)
    path.arcTo(QtCore.QRectF(cx + 1.5 * s, cy - 1.6 * s, 4.6 * s, 4.6 * s), -90, 180)
    path.arcTo(QtCore.QRectF(cx - 2.5 * s, cy - 4.0 * s, 6.0 * s, 6.0 * s), 15, 165)
    path.arcTo(QtCore.QRectF(cx - 6.4 * s, cy - 1.2 * s, 4.2 * s, 4.2 * s), 90, 180)
    path.closeSubpath()
    return path


def paint_weather_icon(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    kind: str,
    color: QtGui.QColor,
    stroke: float = 1.55,
):
    """在 rect 内绘制 1.55px 精致线条矢量天气图标（内部带淡填充，尺寸与时间高度协调）。"""
    if color.alpha() <= 0:
        return
    p.save()
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    cx, cy = rect.center().x(), rect.center().y()
    pen = QtGui.QPen(color, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    fill = QtGui.QColor(color)
    fill.setAlphaF(color.alphaF() * 0.18)
    p.setPen(pen)

    if kind == "sun":
        p.setBrush(fill)
        p.drawEllipse(QtCore.QPointF(cx, cy), 3.6, 3.6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            p.drawLine(
                QtCore.QPointF(cx + math.cos(rad) * 5.3, cy + math.sin(rad) * 5.3),
                QtCore.QPointF(cx + math.cos(rad) * 7.0, cy + math.sin(rad) * 7.0),
            )
    elif kind == "moon":
        p.setBrush(fill)
        outer = QtGui.QPainterPath()
        outer.addEllipse(QtCore.QPointF(cx - 0.5, cy), 5.4, 5.4)
        cut = QtGui.QPainterPath()
        cut.addEllipse(QtCore.QPointF(cx + 2.5, cy - 1.8), 4.5, 4.5)
        p.drawPath(outer.subtracted(cut))
    elif kind in ("partly", "partly_night"):
        p.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "partly":
            p.drawEllipse(QtCore.QPointF(cx + 3.2, cy - 2.8), 2.4, 2.4)
        else:
            p.drawArc(QtCore.QRectF(cx + 1.2, cy - 5.0, 4.2, 4.2), 30 * 16, 200 * 16)
        p.setBrush(fill)
        p.drawPath(_cloud_path(cx - 0.9, cy + 1.4, 1.02))
    elif kind == "rain":
        p.setBrush(fill)
        p.drawPath(_cloud_path(cx, cy - 1.6, 1.04))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for dx in (-2.8, 0.2, 3.2):
            p.drawLine(QtCore.QPointF(cx + dx, cy + 3.2), QtCore.QPointF(cx + dx - 1.0, cy + 5.8))
    elif kind == "thunder":
        p.setBrush(fill)
        p.drawPath(_cloud_path(cx, cy - 1.8, 1.04))
        p.setBrush(Qt.BrushStyle.NoBrush)
        bolt = QtGui.QPainterPath()
        bolt.moveTo(cx + 0.7, cy + 1.8)
        bolt.lineTo(cx - 1.3, cy + 4.2)
        bolt.lineTo(cx + 0.6, cy + 4.2)
        bolt.lineTo(cx - 0.9, cy + 6.6)
        p.drawPath(bolt)
    elif kind == "snow":
        p.setBrush(fill)
        p.drawPath(_cloud_path(cx, cy - 1.6, 1.04))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for dx, dy in ((-2.8, 4.0), (0.2, 5.5), (3.0, 4.0)):
            p.drawEllipse(QtCore.QPointF(cx + dx, cy + dy), 1.05, 1.05)
    elif kind == "fog":
        p.setBrush(Qt.BrushStyle.NoBrush)
        for dy, w_half in ((-3.4, 5.0), (0.0, 6.2), (3.4, 4.4)):
            p.drawLine(QtCore.QPointF(cx - w_half, cy + dy), QtCore.QPointF(cx + w_half, cy + dy))
    else:  # cloud
        p.setBrush(fill)
        p.drawPath(_cloud_path(cx, cy + 0.3, 1.15))

    p.restore()
