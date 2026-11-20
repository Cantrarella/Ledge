# -*- coding: utf-8 -*-
"""收起态时间字体、配色方案、对比度适配及设置页集成测试。"""
from __future__ import annotations

import copy
import os
import sys

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wisland.core.config import DEFAULTS, ConfigDraft, validate_config
from wisland.ui.clock_face import (
    CLOCK_COLORS,
    CLOCK_STYLES,
    ensure_all_fonts_loaded,
    extract_cover_color,
    measure_clock,
    paint_clock,
    resolve_clock_palette,
)
from wisland.ui.compact import CompactView
from wisland.ui.settings.island_pet import IslandPetSettingsPage
from wisland.ui.theme import theme


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_all_bundled_fonts_loaded_and_non_empty_render(qapp):
    loaded = ensure_all_fonts_loaded()
    assert set(loaded.keys()) == {
        "Orbitron-SemiBold.ttf",
        "InstrumentSerif-Italic.ttf",
        "Unbounded-SemiBold.ttf",
        "BebasNeue-Regular.ttf",
        "Fredoka-SemiBold.ttf",
    }

    for spec in CLOCK_STYLES:
        style = spec["key"]
        img = QtGui.QImage(180, 40, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QtGui.QColor(0, 0, 0, 255))
        p = QtGui.QPainter(img)
        paint_clock(p, QtCore.QRectF(10, 3, 160, 34), "15:25", style, [QtGui.QColor("#FFBF5E")], dark=True, alpha=1.0)
        p.end()

        # 确认确实绘制了非黑色像素
        non_black = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.red() > 30 or c.green() > 30 or c.blue() > 30:
                    non_black += 1
        assert non_black > 40, f"style={style} rendered empty"


def test_fixed_digit_slot_width_prevents_jitter(qapp):
    times = ("00:00", "11:11", "09:04", "23:59")
    for spec in CLOCK_STYLES:
        style = spec["key"]
        widths = {measure_clock(style, t)["total_w"] for t in times}
        assert len(widths) == 1, f"style={style} jittered across digits: {widths}"

    cv = CompactView()
    cv.weather = ("☀", "22°", None)
    for spec in CLOCK_STYLES:
        cv.set_clock_appearance(style=spec["key"])
        w_hints = set()
        for t in times:
            cv._clock_override = t
            w_hints.add(cv.size_hint()[0])
        assert len(w_hints) == 1


def test_all_color_modes_and_light_dark_contrast(qapp):
    pix = QtGui.QPixmap(24, 24)
    pix.fill(QtGui.QColor("#FF4081"))
    cover_col = extract_cover_color(pix)
    assert cover_col is not None and cover_col.isValid()

    for dark_mode in ("dark", "light"):
        theme.set_mode(dark_mode)
        for cspec in CLOCK_COLORS:
            ckey = cspec["key"]
            pal = resolve_clock_palette(
                ckey,
                theme,
                custom_hex="#FFFFFF" if not theme.dark else "#101010",
                gradient_stops=["#5AE0FF", "#FF6BA8"],
                cover_color=cover_col,
            )
            assert pal, f"empty palette for {ckey}"
            for col in pal:
                lum = 0.2126 * col.redF() + 0.7152 * col.greenF() + 0.0722 * col.blueF()
                if theme.dark:
                    assert lum >= 0.14, f"{ckey} too dark on dark theme: {col.name()} (lum={lum:.3f})"
                else:
                    assert lum <= 0.62, f"{ckey} too bright on light theme: {col.name()} (lum={lum:.3f})"

    # cover 无封面时自动回落主题默认色
    theme.set_mode("dark")
    fallback_pal = resolve_clock_palette("cover", theme, cover_color=None)
    assert fallback_pal[0].rgba() == theme.text(1.0).rgba()
    theme.set_mode("auto")


def test_compact_view_clock_weather_centering_with_styled_clock(qapp):
    cv = CompactView()
    cv.weather = ("☀", "19°", None)
    cv._clock_override = "15:25"
    for style in ("minimal", "segment", "neon", "serif", "wide", "condensed", "rounded"):
        cv.set_clock_appearance(style=style, color="gradient")
        w, h = cv.size_hint()
        r = QtCore.QRectF(0, 0, w, h)
        m = cv._idle_layout_metrics(r)
        group_center = (m["clock_rect"].left() + m["temp_rect"].right()) / 2.0
        assert abs(group_center - r.center().x()) <= 2.5, f"style={style} off-center: {group_center} vs {r.center().x()}"


def test_clock_config_validation():
    cfg = copy.deepcopy(DEFAULTS)
    ok, errs, _fixed = validate_config(cfg, strict=True)
    assert ok is True and errs == []

    bad = copy.deepcopy(DEFAULTS)
    bad["island"]["clock_style"] = "comic_sans"
    bad["island"]["clock_color"] = "rainbow"
    bad["island"]["clock_custom_color"] = "not-a-color"
    bad["island"]["clock_gradient"] = ["#123456"]
    ok2, errs2, fixed = validate_config(bad, strict=False)
    assert ok2 is False
    assert len(errs2) == 4
    assert fixed["island"]["clock_style"] == "minimal"
    assert fixed["island"]["clock_color"] == "white"
    assert fixed["island"]["clock_custom_color"] == "#FFBF5E"
    assert fixed["island"]["clock_gradient"] == ["#5AE0FF", "#FF6BA8"]


def test_settings_page_clock_roundtrip(qapp):
    page = IslandPetSettingsPage()
    draft = ConfigDraft(copy.deepcopy(DEFAULTS))
    draft.set("island.clock_style", "neon")
    draft.set("island.clock_color", "gradient")
    draft.set("island.clock_custom_color", "#12AB34")
    draft.set("island.clock_gradient", ["#FFD06E", "#FF6B7B"])

    page.load_from_draft(draft)
    assert page.cb_clock_style.current_value() == "neon"
    assert page.cb_clock_color.current_value() == "gradient"
    assert not page.row_custom_color.isHidden() is False  # hidden
    assert not page.row_gradient_color.isHidden()

    page.cb_clock_style.set_value("serif")
    page.cb_clock_color.set_value("custom")
    page.btn_custom_color.set_hex_color("#3399ff")
    page._refresh_clock_controls()
    assert not page.row_custom_color.isHidden()
    assert page.row_gradient_color.isHidden()

    out = ConfigDraft(copy.deepcopy(DEFAULTS))
    page.save_to_draft(out)
    assert out.get("island.clock_style") == "serif"
    assert out.get("island.clock_color") == "custom"
    assert out.get("island.clock_custom_color") == "#3399FF"


def test_weather_vector_icon_synced_temperature_and_toggle(qapp):
    from wisland.ui.clock_face import infer_weather_kind, paint_temperature, paint_weather_icon

    assert infer_weather_kind("01d", "") == "sun"
    assert infer_weather_kind("01n", "") == "moon"
    assert infer_weather_kind("10d", "") == "rain"
    assert infer_weather_kind("11d", "") == "thunder"
    assert infer_weather_kind("13n", "") == "snow"
    assert infer_weather_kind("50d", "") == "fog"
    assert infer_weather_kind("", "☀") == "sun"

    # 每种风格下都能正常绘制温度（包括负温）和矢量天气图标
    for spec in CLOCK_STYLES:
        img = QtGui.QImage(120, 36, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QtGui.QColor(0, 0, 0, 255))
        p = QtGui.QPainter(img)
        paint_weather_icon(p, QtCore.QRectF(8, 2, 14, 32), "rain", QtGui.QColor("#5AE0FF"))
        paint_temperature(p, QtCore.QRectF(26, 2, 60, 32), "-3°", spec["key"], QtGui.QColor("#5AE0FF"))
        p.end()

    # compact_weather=False 时只居中显示时间
    cv = CompactView()
    cv.weather = ("☀", "21°", None, "01d")
    cv._clock_override = "15:25"
    cv.set_clock_appearance(style="neon", color="cyan", show_weather=False)
    w, h = cv.size_hint()
    m = cv._idle_layout_metrics(QtCore.QRectF(0, 0, w, h))
    assert m["has_weather"] is False
    assert abs(m["clock_rect"].center().x() - w / 2.0) <= 1.5
