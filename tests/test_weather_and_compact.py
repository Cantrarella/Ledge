# -*- coding: utf-8 -*-
"""收起态时间天气整体居中 + 展开态天气页 419×232 左上双列布局测试。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from wisland.ui.compact import CompactView
from wisland.ui.pages.info import WeatherPage
from wisland.ui.theme import island_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


class _WeatherSignals(QtCore.QObject):
    cities_updated = QtCore.Signal(object)
    city_added = QtCore.Signal(bool, str)


def _make_weather_page(qt_app) -> WeatherPage:
    sig = _WeatherSignals()
    ctx = SimpleNamespace(
        weather=SimpleNamespace(
            cities_updated=sig.cities_updated,
            city_added=sig.city_added,
            refresh=lambda: None,
        ),
        open_settings=lambda *a, **k: None,
    )
    pg = WeatherPage(ctx)
    pg.resize(419, 218)
    pg.show()
    qt_app.processEvents()
    return pg


def _sample_weather(
    name="洛阳",
    temp=18,
    lo=14,
    hi=20,
    desc="阴",
    icon="04d",
    feels_like=16,
) -> dict:
    d = {
        "name": name,
        "temp": temp,
        "temp_min": lo,
        "temp_max": hi,
        "desc": desc,
        "icon": icon,
        "humidity": 73,
        "wind": 4.05,
        "wind_deg": 45,
        "pressure": 1024.6,
        "visibility": 23000,
        "clouds": 100,
        "sunrise": 1700000000,
        "sunset": 1700038000,
        "tz_offset": 28800,
        "daily": [
            {"date": "2026-11-19", "icon": "10d", "min": 14, "max": 20},
            {"date": "2026-11-20", "icon": "04d", "min": 13, "max": 19},
            {"date": "2026-11-21", "icon": "04d", "min": 11, "max": 20},
            {"date": "2026-11-22", "icon": "04d", "min": 12, "max": 21},
            {"date": "2026-11-23", "icon": "04d", "min": 13, "max": 26},
        ],
        "hourly": [
            {"time": 1700000000 + i * 10800, "icon": "04d", "temp": 18 - i % 4}
            for i in range(8)
        ],
    }
    if feels_like is not None:
        d["feels_like"] = feels_like
    return d


def test_01_compact_clock_weather_centering_and_stability(qt_app):
    c = CompactView()
    widths = set()
    for clk, weather in (
        ("09:04", ("\u2601", "18°")),
        ("23:59", ("\u2601", "-8°")),
        ("00:00", ("\u2600", "100°")),
        ("12:30", ("?", "22°")),
    ):
        c._clock_override = clk
        c.weather = weather
        w, _ = c.size_hint()
        widths.add(w)
        r = QtCore.QRectF(0, 0, w, 40)
        m = c._idle_layout_metrics(r)
        assert m["has_weather"] is True
        assert 13.0 <= m["gap"] <= 15.0
        group_center = m["group_left"] + m["content_total"] / 2.0
        assert abs(group_center - r.center().x()) <= 2.0
        assert m["clock_rect"].right() <= m["icon_rect"].left()
        assert m["icon_rect"].right() <= m["temp_rect"].left()

    # 温度为负数或三位数时，岛体宽度不突然变化
    assert len(widths) == 1

    # 无天气数据时，只显示居中的时间，不保留空天气槽位
    c._clock_override = "09:04"
    c.weather = None
    w_no, _ = c.size_hint()
    r_no = QtCore.QRectF(0, 0, w_no, 40)
    m_no = c._idle_layout_metrics(r_no)
    assert m_no["has_weather"] is False
    assert abs((m_no["clock_rect"].center().x()) - r_no.center().x()) <= 2.0

    # 主题切换后居中关系稳定
    orig = island_theme.mode
    try:
        c.weather = ("\u2601", "18°")
        w, _ = c.size_hint()
        r = QtCore.QRectF(0, 0, w, 40)
        for mode in ("light", "dark", "classic"):
            island_theme.set_mode(mode)
            qt_app.processEvents()
            m = c._idle_layout_metrics(r)
            assert abs((m["group_left"] + m["content_total"] / 2.0) - r.center().x()) <= 2.0
    finally:
        island_theme.set_mode(orig)


def test_02_weather_page_layout_header_dedup_and_edge_cases(qt_app):
    pg = _make_weather_page(qt_app)
    try:
        assert pg.preferred_panel_width() == 419
        assert pg.preferred_panel_height() == 218

        # 1) 正常天气数据
        pg.set_cities([_sample_weather()])
        qt_app.processEvents()

        # 页头只保留城市名（不含温度与天气图标）
        assert pg.city_btn.text == "洛阳"
        assert "°" not in pg.city_btn.text
        assert pg.city_btn.glyph == ""
        assert abs(pg.header.center_lbl.geometry().center().x() - pg.header.width() / 2.0) <= 2.0

        # 左上单一 QGridLayout 检查
        assert isinstance(pg.left_wrap.layout(), QtWidgets.QGridLayout)
        grid = pg.left_wrap.layout()
        assert grid.columnMinimumWidth(0) == 52
        assert grid.horizontalSpacing() == 10

        left_r = pg.left_wrap.geometry()
        daily_r = pg.daily.geometry()
        assert left_r.width() == 154
        assert left_r.right() < daily_r.left()

        icon_r = pg.icon.geometry()
        temp_r = pg.temp.geometry()
        desc_r = pg.desc.geometry()
        range_r = pg.range_lbl.geometry()
        feels_r = pg.feels_lbl.geometry()

        assert 0 <= temp_r.top() - icon_r.bottom() <= 6
        assert max(icon_r.right(), temp_r.right()) <= desc_r.left() - 8
        assert desc_r.x() == range_r.x() == feels_r.x()
        assert desc_r.bottom() < range_r.top()
        assert range_r.bottom() < feels_r.top()
        assert max(desc_r.right(), range_r.right(), feels_r.right()) <= pg.left_wrap.width()
        assert pg.feels_lbl.isVisible()

        # 底部指标：固定 4 列（上下两行 x 坐标完全一致），且不重复“体感”
        fact_keys = [k for k, _ in pg.facts.items]
        assert "体感" not in fact_keys
        assert {"湿度", "能见度", "风", "气压", "云量", "日出", "日落"} <= set(fact_keys)
        for col in range(3):
            r0 = pg.facts._cell_rect(0, col)
            r1 = pg.facts._cell_rect(1, col)
            assert abs(r0.x() - r1.x()) < 0.1
            assert abs(r0.width() - r1.width()) < 0.1
        wind_val = dict(pg.facts.items)["风"]
        assert "m/s" in wind_val and "风" not in wind_val

        # 2) 无体感温度 -> 隐藏 feels_lbl，但已有控件不发生纵向跳动
        desc_y_before = pg.desc.geometry().y()
        range_y_before = pg.range_lbl.geometry().y()
        temp_y_before = pg.temp.geometry().y()
        pg.set_cities([_sample_weather(feels_like=None)])
        qt_app.processEvents()
        assert not pg.feels_lbl.isVisible()
        assert pg.desc.geometry().y() == desc_y_before
        assert pg.range_lbl.geometry().y() == range_y_before
        assert pg.temp.geometry().y() == temp_y_before

        # 3) 长天气描述 + 负温度 + 无城市 + 温度范围缺失 + 长风速气压 + 缺失日出日落
        edge = _sample_weather(
            name="",
            temp=-8,
            lo=None,
            hi=None,
            desc="中到大雪转局地暴雪伴有大风降温",
            feels_like=-15,
        )
        edge.update({"wind": 18.7, "wind_deg": 225, "pressure": 1034.8, "sunrise": None, "sunset": None})
        pg.set_cities([edge])
        qt_app.processEvents()
        assert pg.city_btn.text == "选择城市"
        assert pg.temp._text == "-8°"
        assert pg.range_lbl._text == "--°~--°"
        assert pg.feels_lbl._text == "体感 -15°"
        assert dict(pg.facts.items)["日出"] == "--"
        assert dict(pg.facts.items)["日落"] == "--"
        assert max(pg.icon.geometry().right(), pg.temp.geometry().right()) <= pg.desc.geometry().left() - 8
        assert pg.left_wrap.geometry().right() < pg.daily.geometry().left()

        # 4) 长城市名不挤压居中日期时间
        pg.set_cities([_sample_weather(name="呼和浩特新城区测试长城市名称")])
        qt_app.processEvents()
        assert abs(pg.header.center_lbl.geometry().center().x() - pg.header.width() / 2.0) <= 2.0
    finally:
        pg.close()


def test_03_weather_themes_compact_and_dpi_screenshots(qt_app, tmp_path):
    shot_dir = Path(os.environ.get("WISLAND_WEATHER_SHOT_DIR") or (tmp_path / "weather_shots"))
    shot_dir.mkdir(parents=True, exist_ok=True)
    orig_mode = island_theme.mode
    pg = _make_weather_page(qt_app)
    try:
        pg.set_cities([_sample_weather()])
        qt_app.processEvents()
        for mode, bg in (
            ("light", "#eef0f5"),
            ("dark", "#14151a"),
            ("sky", "#69b7f5"),
            ("rouge", "#14151a"),
            ("classic", "#000000"),
        ):
            island_theme.set_mode(mode)
            qt_app.processEvents()
            pix = QtGui.QPixmap(419, 218)
            pix.fill(QtGui.QColor(bg))
            pg.render(pix, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
            assert pix.save(str(shot_dir / f"weather-{mode}.png"))

        # 125% & 200% DPI 截图
        island_theme.set_mode("classic")
        qt_app.processEvents()
        for dpr, name in ((1.25, "weather-classic-125.png"), (2.0, "weather-classic-2x.png")):
            pix_hi = QtGui.QPixmap(round(419 * dpr), round(218 * dpr))
            pix_hi.setDevicePixelRatio(dpr)
            pix_hi.fill(QtGui.QColor("#000000"))
            pg.render(pix_hi, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
            assert pix_hi.save(str(shot_dir / name))

        # 收起态时间+天气截图（light & classic）
        c = CompactView()
        c._clock_override = "09:04"
        c.weather = ("\u2601", "18°")
        cw, ch = c.size_hint()
        for mode, bg, cap in (
            ("light", (238, 240, 245, 225), "weather-compact-light.png"),
            ("classic", (0, 0, 0, 255), "weather-compact-classic.png"),
        ):
            island_theme.set_mode(mode)
            qt_app.processEvents()
            pix_c = QtGui.QPixmap(round(cw) + 20, round(ch) + 20)
            pix_c.fill(QtGui.QColor("#3b5f4f"))
            p = QtGui.QPainter(pix_c)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            r = QtCore.QRectF(10, 10, cw, ch)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(*bg))
            p.drawRoundedRect(r, ch / 2, ch / 2)
            c._paint_idle(p, r, 1.0)
            p.end()
            assert pix_c.save(str(shot_dir / cap))
    finally:
        island_theme.set_mode(orig_mode)
        pg.close()
