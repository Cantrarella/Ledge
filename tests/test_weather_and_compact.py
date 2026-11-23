# -*- coding: utf-8 -*-
"""收起态时间天气整体居中 + 展开态天气页 419×232 左上双列布局测试。"""
from __future__ import annotations

import math
import os
import time
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

    # 温度为负数或三位数时，岛体宽度不突然变化（含宽体等超过 146px 的字体风格）
    assert len(widths) == 1
    for style in ("wide", "neon"):
        c.clock_style = style
        style_widths = set()
        for temp_txt in ("18°", "-8°", "100°", "22°"):
            c.weather = ("\u2601", temp_txt)
            w_s, _ = c.size_hint()
            style_widths.add(w_s)
        assert len(style_widths) == 1, f"{style} width jitter: {style_widths}"
    c.clock_style = "minimal"

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


def _render_vertical_compact_image(
    c: CompactView,
    w: float,
    h: float,
    dpr: float = 1.0,
    bg_rgba: tuple[int, int, int, int] = (14, 16, 22, 240),
) -> QtGui.QImage:
    pw = max(1, round(w * dpr))
    ph = max(1, round(h * dpr))
    img = QtGui.QImage(pw, ph, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    r = QtCore.QRectF(0.0, 0.0, float(w), float(h))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(*bg_rgba))
    rad = min(w * 0.5, h * 0.5)
    p.drawRoundedRect(r, rad, rad)
    c.paint(p, r, 1.0, 0.0)
    p.end()
    return img


def test_04_left_right_36px_vertical_clock_readability_and_dpi_screenshots(qt_app, tmp_path):
    from wisland.ui.clock_face import (
        VALID_CLOCK_STYLES,
        measure_vertical_clock_pair,
        measure_vertical_temperature,
        paint_vertical_clock_pair,
        resolve_clock_palette,
    )

    shot_dir = Path(
        os.environ.get("WISLAND_VERT_SHOT_DIR")
        or (tmp_path / "vertical_36px_shots")
    )
    shot_dir.mkdir(parents=True, exist_ok=True)
    orig_mode = island_theme.mode

    try:
        c = CompactView()
        c.set_edge("left")
        c._mode_t = 0.0

        # 1) 四种基础尺寸校验：36×92、36×104、36×136、36×148
        c.weather = None
        c.badges = {}
        assert c.size_hint() == (36.0, 92.0)
        c.badges = {"qq": 1}
        assert c.size_hint() == (36.0, 104.0)
        c.weather = ("\u2600", "18°", None, "01d")
        c.badges = {}
        assert c.size_hint() == (36.0, 136.0)
        c.badges = {"qq": 1, "wechat": 1, "mail": 1}
        assert c.size_hint() == (36.0, 148.0)

        # 2) 全部 7 种字体（重点 wide 与 neon/segment）固定双数字槽位、26px 核心区与 30px 裁剪区校验
        row_rect = QtCore.QRectF(0.0, 16.0, 36.0, 20.0)
        for style in VALID_CLOCK_STYLES:
            m88 = measure_vertical_clock_pair(style, "88", max_core_w=26.0, max_clip_w=30.0, rect=row_rect)
            # 联合字墨包围盒中心严格居中于 18.0，总宽 <= 26.0px（落在 [5.0, 31.0] 核心区内）
            u_ink = m88["union_ink_rect"]
            assert abs(u_ink.center().x() - 18.0) <= 0.15, f"{style} union center: {u_ink}"
            assert u_ink.width() <= 26.0 + 1e-4, f"{style} union width > 26: {u_ink.width()}"
            assert u_ink.left() >= 5.0 - 0.15 and u_ink.right() <= 31.0 + 0.15, f"{style} ink out of 26px core: {u_ink}"

            # 含外发光描边与数码管斜切外伸的实际绘制包围盒严格落在 30px 裁剪区 [3.0, 33.0] 内且留有余量
            u_glow = m88["union_glow_rect"]
            assert u_glow.width() <= 28.0 + 1e-3, f"{style} glow width: {u_glow.width()}"
            assert u_glow.left() >= 3.0 + 0.8 and u_glow.right() <= 33.0 - 0.8, f"{style} glow too close to clip: {u_glow}"

            # 08:08 -> 08:09 -> 11:11 -> 23:59 切换时：缩放比例恒定、左右槽位中心零抖动
            scales = set()
            slot0_xs = set()
            slot1_xs = set()
            m_08 = None
            m_09 = None
            for pair_txt in ("08", "09", "11", "23", "59", "88"):
                mp = measure_vertical_clock_pair(style, pair_txt, max_core_w=26.0, max_clip_w=30.0, rect=row_rect)
                scales.add(round(mp["scale"], 6))
                slot0_xs.add(round(mp["slot0_cx"], 4))
                slot1_xs.add(round(mp["slot1_cx"], 4))
                assert mp["text_ink_rect"].left() >= 5.0 - 0.15
                assert mp["text_ink_rect"].right() <= 31.0 + 0.15
                assert mp["text_glow_rect"].left() >= 3.0 + 0.8
                assert mp["text_glow_rect"].right() <= 33.0 - 0.8
                if pair_txt == "08":
                    m_08 = mp
                elif pair_txt == "09":
                    m_09 = mp
            assert len(scales) == 1, f"{style} scale changed across digits: {scales}"
            assert len(slot0_xs) == 1 and len(slot1_xs) == 1, f"{style} slot jitter: {slot0_xs}, {slot1_xs}"
            # 08 与 09 中左槽 '0' 的左边界完全不变
            assert abs(m_08["text_ink_rect"].left() - m_09["text_ink_rect"].left()) <= 1e-4

        # 3) 负温度 (-18°, -88°, 100°) + 三颗未读点 (36×148) 整体居中、<=26px 及垂直间距 >= 4px 校验
        r_148 = QtCore.QRectF(0.0, 0.0, 36.0, 148.0)
        c.badges = {"qq": 1, "wechat": 1, "mail": 1}
        for style in VALID_CLOCK_STYLES:
            c.clock_style = style
            for t_str in ("-18°", "-88°", "100°"):
                c.weather = ("\u2601", t_str, None, "03d")
                vm = c._vertical_idle_layout_metrics(r_148)
                tm = vm["temp_metrics"]
                assert tm is not None
                t_ink = tm["ink_rect"]
                # 负号、数字、度数圆环及描边作为整体居中于 x=18.0，总宽 <= 26.0px
                assert abs(t_ink.center().x() - 18.0) <= 0.2, f"{style} {t_str} not centered: {t_ink}"
                assert t_ink.width() <= 26.0 + 1e-3, f"{style} {t_str} width > 26: {t_ink.width()}"
                assert t_ink.left() >= 5.0 - 0.2 and t_ink.right() <= 31.0 + 0.2, f"{style} {t_str} out of core: {t_ink}"
                # 负号/数字与度数圆环均非空且完整可见
                assert not tm["num_ink_rect"].isEmpty() and not tm["deg_ink_rect"].isEmpty()
                assert tm["num_ink_rect"].left() < tm["deg_ink_rect"].left()
                # 从温度可见笔画底缘到三颗未读点顶缘至少留 4px
                badges_r = vm["badges_rect"]
                assert not badges_r.isEmpty()
                assert abs(badges_r.center().x() - 18.0) <= 0.15
                assert badges_r.width() <= 26.0
                v_gap = badges_r.top() - t_ink.bottom()
                assert v_gap >= 4.0, f"{style} {t_str} temp-to-badge gap {v_gap} < 4px"

        # 4) 左右两侧 × 深浅主题 × 100% / 125% / 150% / 200% 四档缩放离屏渲染边界与截图保存
        for edge in ("left", "right"):
            c.set_edge(edge)
            c._mode_t = 0.0
            for theme_mode, bg_rgba in (
                ("dark", (14, 16, 22, 242)),
                ("light", (242, 244, 249, 242)),
            ):
                island_theme.set_mode(theme_mode)
                qt_app.processEvents()
                for dpr, dpr_tag in ((1.0, "100"), (1.25, "125"), (1.5, "150"), (2.0, "200")):
                    for style, clk_txt, temp_txt in (
                        ("wide", "08:08", "-18°"),
                        ("neon", "08:08", "-88°"),
                        ("segment", "23:59", "-88°"),
                    ):
                        c.clock_style = style
                        c._clock_override = clk_txt
                        c.weather = ("\u2600", temp_txt, None, "01d")
                        c.badges = {"qq": 1, "wechat": 1, "mail": 1}

                        # 单独在透明画布上渲染时钟+光晕，直接检验 30px 裁剪区边界像素无硬切
                        pw, ph = round(36 * dpr), round(148 * dpr)
                        raw_img = QtGui.QImage(pw, ph, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                        raw_img.setDevicePixelRatio(dpr)
                        raw_img.fill(QtCore.Qt.GlobalColor.transparent)
                        rp = QtGui.QPainter(raw_img)
                        r_log = QtCore.QRectF(0.0, 0.0, 36.0, 148.0)
                        c.paint(rp, r_log, 1.0, 0.0)
                        rp.end()

                        # 裁剪线位于逻辑 x=3.0 与 x=33.0；检查裁剪线内侧最外列像素 alpha 接近 0（无硬切平边）
                        left_clip_px = max(0, round(3.0 * dpr))
                        right_clip_px = min(pw - 1, round(33.0 * dpr) - 1)
                        max_edge_alpha = 0
                        center_ink_alpha = 0
                        for py in range(round(12 * dpr), round(142 * dpr)):
                            a_l = QtGui.qAlpha(raw_img.pixel(left_clip_px, py))
                            a_r = QtGui.qAlpha(raw_img.pixel(right_clip_px, py))
                            max_edge_alpha = max(max_edge_alpha, a_l, a_r)
                            center_ink_alpha = max(center_ink_alpha, QtGui.qAlpha(raw_img.pixel(pw // 2, py)))
                        assert center_ink_alpha > 120, f"blank render for {style} {theme_mode} {dpr_tag}"
                        assert max_edge_alpha == 0, (
                            f"hard clip detected at 30px clip boundary for {style} {theme_mode} {dpr_tag}: alpha={max_edge_alpha}"
                        )

                        # 保存带胶囊底色的稳定态验收截图
                        full_img = _render_vertical_compact_image(c, 36.0, 148.0, dpr=dpr, bg_rgba=bg_rgba)
                        fname = f"vert36-{edge}-{theme_mode}-{style}-{dpr_tag}pct.png"
                        assert full_img.save(str(shot_dir / fname))
        assert len(list(shot_dir.glob("vert36-*.png"))) == 48
    finally:
        island_theme.set_mode(orig_mode)


def test_05_squeeze_transition_drag_snap_anchored_and_corner_competition(qt_app):
    from wisland.core.config import cfg
    from wisland.ui.island import EDGE_MARGIN, FILLET, Island

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    orig_scr = cfg.get("island.screen", 0)

    # 1) 碰撞挤压过渡帧（~22.3px，裁剪后 ~16.3px）按实际宽度等比缩放，优先保留时钟且不消失、不硬切
    c = CompactView()
    c.set_edge("left")
    c._mode_t = 0.0
    c.clock_style = "wide"
    c._clock_override = "08:08"
    c.weather = ("\u2600", "-18°", None, "01d")
    c.badges = {"qq": 1, "wechat": 1, "mail": 1}

    r_squeezed = QtCore.QRectF(0.0, 0.0, 22.32, 160.0)
    vm_sq = c._vertical_idle_layout_metrics(r_squeezed)
    assert abs(vm_sq["actual_clip_w"] - 16.32) <= 0.05
    assert vm_sq["weather_alpha"] == 0.0
    assert vm_sq["badge_alpha"] == 0.0
    # 挤压态下时钟宽度按实际裁剪空间等比缩放到 <= 14.15px (< 16.32px 裁剪宽)
    assert vm_sq["hh_metrics"]["union_ink_rect"].width() <= vm_sq["eff_core_w"] + 1e-3
    assert vm_sq["hh_metrics"]["union_glow_rect"].width() < vm_sq["actual_clip_w"]

    sq_img = QtGui.QImage(23, 160, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    sq_img.fill(QtCore.Qt.GlobalColor.transparent)
    sp = QtGui.QPainter(sq_img)
    c.paint(sp, r_squeezed, 1.0, 0.0)
    sp.end()
    # 确认挤压帧时钟未整帧消失，且底部未读区不绘制
    clock_band_alpha = max(
        QtGui.qAlpha(sq_img.pixel(x, y))
        for x in range(4, 19)
        for y in range(50, 110)
    )
    badge_band_alpha = max(
        QtGui.qAlpha(sq_img.pixel(x, y))
        for x in range(4, 19)
        for y in range(142, 156)
    )
    assert clock_band_alpha > 100
    assert badge_band_alpha == 0

    # 宽度恢复到 36px 后，天气和未读点完整恢复显示
    vm_rec = c._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 36.0, 148.0))
    assert vm_rec["weather_alpha"] == 1.0
    assert vm_rec["badge_alpha"] == 1.0

    # 1b) 定点回归 29.544 与 29.546 两帧（未读区透明度跨阈值处）以及左右两侧完整挤压→回弹逐帧连续性
    for edge in ("left", "right"):
        c.set_edge(edge)
        c._mode_t = 0.0
        for style in ("wide", "neon"):
            c.clock_style = style
            vm_a = c._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 29.544, 148.0))
            vm_b = c._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 29.546, 148.0))
            assert abs(vm_b["hh_rect"].center().y() - vm_a["hh_rect"].center().y()) < 0.02, (
                f"{edge} {style} jump at 29.544->29.546: {vm_a['hh_rect'].center().y()} -> {vm_b['hh_rect'].center().y()}"
            )
            assert abs(vm_b["mm_rect"].center().y() - vm_a["mm_rect"].center().y()) < 0.02
            assert abs(vm_b["hh_metrics"]["scale"] - vm_a["hh_metrics"]["scale"]) < 0.005

            # 完整挤压 (36.0 -> 22.32) 与回弹 (22.32 -> 36.0) 逐帧扫描
            widths_seq = [36.0 - (36.0 - 22.32) * (i / 20.0) for i in range(21)] + [
                22.32 + (36.0 - 22.32) * (i / 20.0) for i in range(1, 21)
            ]
            prev_vm = None
            for w_cur in widths_seq:
                r_cur = QtCore.QRectF(0.0, 0.0, w_cur, 148.0)
                vm_cur = c._vertical_idle_layout_metrics(r_cur)
                if prev_vm is not None:
                    assert abs(vm_cur["hh_rect"].center().y() - prev_vm["hh_rect"].center().y()) < 2.6
                    assert abs(vm_cur["mm_rect"].center().y() - prev_vm["mm_rect"].center().y()) < 2.6
                    assert abs(vm_cur["hh_metrics"]["scale"] - prev_vm["hh_metrics"]["scale"]) < 0.06
                prev_vm = vm_cur
                # 最窄帧与关键帧检查渲染非空且裁剪线无硬切
                if abs(w_cur - 22.32) < 1e-3 or abs(w_cur - 29.545) < 0.4:
                    pw_c = max(24, int(w_cur) + 4)
                    f_img = QtGui.QImage(pw_c, 148, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    f_img.fill(QtCore.Qt.GlobalColor.transparent)
                    fp = QtGui.QPainter(f_img)
                    c.paint(fp, r_cur, 1.0, 0.0)
                    fp.end()
                    assert max(
                        QtGui.qAlpha(f_img.pixel(x, y))
                        for x in range(4, max(5, int(w_cur) - 4))
                        for y in range(20, 120)
                    ) > 80
                    l_clip = 3
                    r_clip = min(pw_c - 1, int(math.floor(w_cur - 3.0)) - 1)
                    # 允许不同 Windows 版本系统字体在 0.5px 边缘产生极淡抗锯齿（alpha<=24），同时严格拦截硬切字形
                    assert max(QtGui.qAlpha(f_img.pixel(l_clip, y)) for y in range(12, 136)) <= 24
                    assert max(QtGui.qAlpha(f_img.pixel(r_clip, y)) for y in range(12, 136)) <= 24

            # 恢复到 36×148 后，验证 -18°、-88°、100° 与三颗未读点完整可见且间距 >= 4px
            for t_str in ("-18°", "-88°", "100°"):
                c.weather = ("\u2600", t_str, None, "01d")
                vm_36 = c._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 36.0, 148.0))
                assert vm_36["weather_alpha"] == 1.0 and vm_36["badge_alpha"] == 1.0
                t_ink = vm_36["temp_metrics"]["ink_rect"]
                b_rect = vm_36["badges_rect"]
                assert t_ink.width() <= 26.0 + 1e-3
                assert b_rect.top() - t_ink.bottom() >= 4.0

    # 2) Island 拖拽定边、正负距离正角竞争、36×148 夹紧、落定零跳位与无 36↔40 突跳
    class _StubPage(QtWidgets.QWidget):
        key = "home"
        title = "首页"
        glyph = "H"
        island = None

        def preferred_panel_width(self):
            return 419

        def preferred_panel_height(self):
            return 140

        def preferred_height(self):
            return 100

        def on_show(self):
            pass

        def on_hide(self):
            pass

    isl = Island([_StubPage()])
    try:
        isl.compact._clock_override = "08:08"
        isl.compact.weather = ("\u2600", "-18°", None, "01d")
        isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
        R = isl._avail()

        # (a) 正角边缘竞争：屏幕内正距离 (+4, +4) -> top 胜出；越界负距离 (-4, -4) -> left 胜出；加权平局 (13.5, 10.0) -> top 胜出
        cfg.set("island.edge", "top", save=False)
        isl.apply_config()
        w_d, h_d = isl.target_size()

        # 屏幕内正距离等距：gap_top = +4, gap_left = +4
        isl.begin_drag(QtCore.QPoint(round(R.left() + 100), round(R.top() + 50)))
        isl.fx.snap(R.left() + 4.0 + w_d / 2.0)
        isl.fy.snap(R.top() + 4.0 + h_d / 2.0)
        isl._end_drag(QtCore.QPoint(round(isl.fx.x), round(isl.fy.x)))
        assert isl.edge == "top"

        # 越界负距离等距：gap_top = -4, gap_left = -4 -> -4*1.35 = -5.4 < -4*1.0 -> left 胜出
        isl.mode = "anchored"
        isl.begin_drag(QtCore.QPoint(round(R.left() + 100), round(R.top() + 50)))
        isl.fx.snap(R.left() - 4.0 + w_d / 2.0)
        isl.fy.snap(R.top() - 4.0 + h_d / 2.0)
        isl._end_drag(QtCore.QPoint(round(isl.fx.x), round(isl.fy.x)))
        assert isl.edge == "left"
        assert isl.target_size() == (36.0, 148.0)

        # 加权平局：gap_top = 13.5, gap_left = 10.0 (13.5*1.0 == 10.0*1.35 == 13.5) -> 按字典键序 top 胜出
        cfg.set("island.edge", "top", save=False)
        isl.apply_config()
        w_d, h_d = isl.target_size()
        isl.begin_drag(QtCore.QPoint(round(R.left() + 100), round(R.top() + 50)))
        isl.fx.snap(R.left() + 10.0 + w_d / 2.0)
        isl.fy.snap(R.top() + 13.5 + h_d / 2.0)
        isl._end_drag(QtCore.QPoint(round(isl.fx.x), round(isl.fy.x)))
        assert isl.edge == "top"

        # (b) 偏向四角松手（左上偏左、左下偏左、右上偏右、右下偏右）+ 36×148 沿边夹紧 + snap->anchored 零二次跳位 + 无 36↔40 突跳
        half_along = 148.0 / 2.0 + FILLET + EDGE_MARGIN  # 74 + 13 + 6 = 93.0
        for want_edge, rx, ry in (
            ("left", R.left() + 12.0, R.top() + 5.0),
            ("left", R.left() + 12.0, R.bottom() - 5.0),
            ("right", R.right() - 12.0, R.top() + 5.0),
            ("right", R.right() - 12.0, R.bottom() - 5.0),
        ):
            cfg.set("island.edge", "top", save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            gstart = QtCore.QPoint(round(R.center().x()), round(R.top() + 20))
            isl.begin_drag(gstart)
            orig_off = QtCore.QPointF(isl.drag_off)
            # 拖动中保持顶边横条布局且抓手偏移恒定
            assert isl.edge == "top"
            assert isl.compact.layout_state() == ("horizontal", "idle")
            assert isl.drag_off == orig_off

            isl.fx.snap(rx)
            isl.fy.snap(ry)
            isl.fx.v = -220.0 if want_edge == "left" else 220.0
            isl._end_drag(QtCore.QPoint(round(rx), round(ry)))
            assert isl.edge == want_edge
            assert isl.compact.layout_state() == ("vertical", "idle")
            assert isl.target_size() == (36.0, 148.0)

            widths_trace = []
            last_snap_global_body = None
            first_anchored_global_body = None
            for _ in range(240):
                prev_mode = isl.mode
                if prev_mode == "snap":
                    last_snap_global_body = isl.body.translated(QtCore.QPointF(isl._win_origin))
                isl._tick()
                widths_trace.append(round(isl.body.width(), 2))
                if prev_mode == "snap" and isl.mode == "anchored" and first_anchored_global_body is None:
                    first_anchored_global_body = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    break

            assert isl.mode == "anchored"
            assert last_snap_global_body is not None and first_anchored_global_body is not None
            # snap 最后一帧与 anchored 第一帧的全局坐标差 < 0.5px，零二次跳位
            assert abs(last_snap_global_body.center().x() - first_anchored_global_body.center().x()) < 0.5
            assert abs(last_snap_global_body.center().y() - first_anchored_global_body.center().y()) < 0.5
            # 稳定停靠宽度为 36.0px，中心距停靠线 18.0px
            assert abs(isl.body.width() - 36.0) < 0.05
            assert abs(isl.body.height() - 148.0) < 0.05
            expected_cx = (R.left() + 18.0) if want_edge == "left" else (R.right() - 18.0)
            assert abs(first_anchored_global_body.center().x() - expected_cx) < 0.5
            # 36×148 沿边夹紧严格落在 [R.top() + 93, R.bottom() - 93] 内
            assert (R.top() + half_along - 0.5) <= first_anchored_global_body.center().y() <= (R.bottom() - half_along + 0.5)

        # (c) 窗口原点变更时，局部点击热区与 _press 在同一帧完成换算
        isl._win_origin = QtCore.QPoint(isl._win_origin.x() - 35, isl._win_origin.y() - 20)
        isl.compact.play_rect = QtCore.QRectF(100.0, 50.0, 24.0, 24.0)
        isl.compact._notify_dismiss_rect = QtCore.QRectF(120.0, 50.0, 24.0, 24.0)
        isl._press = QtCore.QPointF(110.0, 60.0)
        old_global_play = isl.compact.play_rect.translated(QtCore.QPointF(isl._win_origin))
        old_global_dismiss = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))
        old_global_press = isl._press + QtCore.QPointF(isl._win_origin)
        # 触发 _update_geometry(force=True) 自动将 _win_origin 修正回目标原点并同帧换算局部热区
        isl._update_geometry(force=True)
        new_global_play = isl.compact.play_rect.translated(QtCore.QPointF(isl._win_origin))
        new_global_dismiss = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))
        new_global_press = isl._press + QtCore.QPointF(isl._win_origin)
        assert abs(old_global_play.x() - new_global_play.x()) < 1e-4
        assert abs(old_global_dismiss.x() - new_global_dismiss.x()) < 1e-4
        assert abs(old_global_press.x() - new_global_press.x()) < 1e-4
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.set("island.screen", orig_scr, save=False)
        cfg.save()
        isl.close()

def test_06_inward_expand_cross_page_vs_intra_page_and_banner_isolation(qt_app):
    from wisland.core.config import cfg
    from wisland.ui.island import Island

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)

    class _FlexPage(QtWidgets.QWidget):
        def __init__(self, key: str, title: str, pw: int, ph: int):
            super().__init__()
            self.key = key
            self.title = title
            self.glyph = key[:1].upper()
            self.island = None
            self._pw = pw
            self._ph = ph
            self.show_count = 0
            self.hide_count = 0

        def preferred_panel_width(self):
            return self._pw

        def preferred_panel_height(self):
            return self._ph

        def preferred_height(self):
            return self._ph - 36

        def on_show(self):
            self.show_count += 1

        def on_hide(self):
            self.hide_count += 1

    p_home = _FlexPage("home", "首页", 419, 140)
    p_mail = _FlexPage("mail", "邮箱", 419, 140)
    p_tall = _FlexPage("tall", "高页", 419, 236)
    isl = Island([p_home, p_mail, p_tall])
    try:
        isl.compact._clock_override = "08:08"
        isl.compact.weather = ("\u2600", "18°", None, "01d")
        isl.compact.badges = {"qq": 1}  # 竖条尺寸 36×148（比首页 140 高，测试变矮展开）
        isl.compact.set_view("pet")     # 用户原偏好为 pet（侧边不显示小表情，保留偏好待拖回顶/底边恢复）

        for edge in ("left", "right"):
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            assert isl.compact.view == "pet"  # 偏好保留
            assert isl.compact.layout_state() == ("vertical", "idle")
            assert isl.target_size() == (36.0, 148.0)
            assert abs(isl.body.width() - 36.0) < 0.1

            # 1) 从 36×148 竖条向内展开到 419×140 首页（变矮展开：必须在 sh.x <= 141.5 且 sw.x >= 418 时才显现）
            isl.expand("home")
            assert not isl._content_shown()
            for _ in range(180):
                isl._tick()
                if isl._content_shown():
                    assert isl.sw.x >= 418.0
                    assert isl.sh.x <= 141.5 and isl.sh.x >= 139.0
                    break
            assert isl._content_shown()
            cr = isl.content.geometry()
            lbody = isl.body
            if edge == "left":
                assert abs(cr.left() - round(lbody.left())) <= 1
            else:
                assert abs(cr.right() - round(lbody.right())) <= 1
            assert abs(cr.center().y() - round(lbody.center().y())) <= 1

            # 2) 真正的跨页切换 (home 419×140 -> tall 419×236)：expanded_size 立即读取待显现页 236，长高到位后才显示
            old_tall_shows = p_tall.show_count
            isl.show_page("tall")
            assert isl.expanded_size() == (419.0, 236.0)
            assert not isl._content_shown()
            for _ in range(180):
                isl._tick()
                if isl._content_shown():
                    assert isl.sh.x >= 235.0
                    break
            assert isl._content_shown()
            qt_app.processEvents()
            QtTest_wait = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(55, QtTest_wait.quit)
            QtTest_wait.exec()
            assert p_tall.show_count == old_tall_shows + 1

            # 3) 邮箱页内同页变高 (mail 列表 419×140 -> 详情 419×236)：只调整显现，不重复调用 on_show()
            isl.show_page("mail")
            for _ in range(180):
                isl._tick()
            QtCore.QTimer.singleShot(55, QtTest_wait.quit)
            QtTest_wait.exec()
            mail_shows_before = p_mail.show_count
            mail_hides_before = p_mail.hide_count
            p_mail._ph = 236
            isl.page_size_changed()
            assert isl.target_size() == (419.0, 236.0)
            assert not isl._content_shown()
            for _ in range(180):
                isl._tick()
                if isl._content_shown():
                    assert isl.sh.x >= 235.0
                    break
            assert isl._content_shown()
            QtCore.QTimer.singleShot(55, QtTest_wait.quit)
            QtTest_wait.exec()
            assert p_mail.show_count == mail_shows_before
            assert p_mail.hide_count == mail_hides_before
            p_mail._ph = 140

            # 4) 快速连续切页 (home -> tall -> home) 取消过时回调，tall 不闪现、不触发 on_show
            isl.show_page("home")
            for _ in range(180):
                isl._tick()
            tall_shows_0 = p_tall.show_count
            isl.show_page("tall")
            isl._tick()
            isl._tick()
            assert not isl._content_shown()
            isl.show_page("home")
            for _ in range(180):
                isl._tick()
            QtCore.QTimer.singleShot(55, QtTest_wait.quit)
            QtTest_wait.exec()
            assert isl.stack.currentIndex() == 0
            assert isl._content_shown()
            assert p_tall.show_count == tall_shows_0

            # 收起回到 36×148 竖条，无 36↔40 突跳
            isl.set_pinned(False)
            isl.collapse()
            for _ in range(180):
                isl._tick()
            assert abs(isl.body.width() - 36.0) < 0.05

        # 5) 68px 统一关闭键门槛（66px / 70px / 90px）与横幅 A 按下切 B 松手的手势隔离
        cfg.set("island.edge", "left", save=False)
        isl.apply_config()
        isl.activity({"type": "notify", "title": "微信", "sub": "测试消息"}, 5.0)
        for test_w, expect_dismiss in ((66.0, False), (70.0, True), (90.0, True)):
            img_b = QtGui.QImage(100, 40, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            bp = QtGui.QPainter(img_b)
            isl.compact.paint(bp, QtCore.QRectF(0.0, 0.0, test_w, 36.0), 1.0, 0.0)
            bp.end()
            if expect_dismiss:
                assert isl.compact._notify_dismiss_rect is not None
            else:
                assert isl.compact._notify_dismiss_rect is None

        # 展开到完整通知宽度后，按下通知 A 的关闭键，松手前切换到通知 B（以及切到生活提醒）
        for _ in range(160):
            isl._tick()
        img_full = QtGui.QImage(400, 60, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        fp = QtGui.QPainter(img_full)
        isl.compact.paint(fp, isl.body, 1.0, 0.0)
        fp.end()
        dr_a = isl.compact._notify_dismiss_rect
        assert dr_a is not None
        dismissed_count = {"n": 0}
        clicked_count = {"n": 0}
        isl.notify_banner_dismissed.connect(lambda: dismissed_count.__setitem__("n", dismissed_count["n"] + 1))
        isl.notify_banner_clicked.connect(lambda: clicked_count.__setitem__("n", clicked_count["n"] + 1))

        # 模拟在 A 的关闭键按下
        press_pt = dr_a.center()
        isl._press = press_pt
        isl._press_ctx = isl._classify_hit_ctx(press_pt)
        assert isl._press_ctx is not None and isl._press_ctx[3] == "dismiss"

        # 松手前被通知 B 替换 -> _press 与 _press_ctx 被立即清空，松手不会关闭 B
        isl.activity({"type": "notify", "title": "QQ", "sub": "第二条消息"}, 5.0)
        assert isl._press is None and isl._press_ctx is None
        rel_ev = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            press_pt,
            press_pt + QtCore.QPointF(isl._win_origin),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        isl.mouseReleaseEvent(rel_ev)
        assert dismissed_count["n"] == 0 and clicked_count["n"] == 0

        # 重新绘制 B 后点击 B 的关闭键，正常触发关闭
        fp2 = QtGui.QPainter(img_full)
        isl.compact.paint(fp2, isl.body, 1.0, 0.0)
        fp2.end()
        dr_b = isl.compact._notify_dismiss_rect
        assert dr_b is not None
        pt_b = dr_b.center()
        isl._press = pt_b
        isl._press_ctx = isl._classify_hit_ctx(pt_b)
        rel_ev_b = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            pt_b,
            pt_b + QtCore.QPointF(isl._win_origin),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        isl.mouseReleaseEvent(rel_ev_b)
        assert dismissed_count["n"] == 1

        # 清理横幅后回到侧边 36×148 竖时钟，且拖回 top 后自动恢复用户原来的 pet 视图
        isl.compact.clear_activity()
        assert isl.compact.layout_state() == ("vertical", "idle")
        assert isl.compact._notify_dismiss_rect is None
        for _ in range(180):
            isl._tick()
        assert abs(isl.body.width() - 36.0) < 0.05

        cfg.set("island.edge", "top", save=False)
        isl.apply_config()
        assert isl.compact.layout_state() == ("horizontal", "pet")

        # 5b) 左右两侧（中段 + 36×92拖到底角再加天气和三颗未读点变36×148）四类横幅自然超时 + 真实点击关闭按钮手动关闭 60Hz 逐帧回收验收：
        # 内容层零全透明帧、时钟两行全局 Y 坐标连续移动（相邻帧 < 8.0px）、旧热区立清、窗口原点不动、最终回到横幅前沿边位置与 36×148
        def _click_notify_dismiss_button(target_isl: Island):
            """在当前岛体坐标系渲染一帧以刷新 _notify_dismiss_rect，随后通过真实 mousePressEvent + mouseReleaseEvent 点击关闭按钮。"""
            bw_c = max(40, int(math.ceil(target_isl.body.right())) + 8)
            bh_c = max(40, int(math.ceil(target_isl.body.bottom())) + 8)
            c_img = QtGui.QImage(bw_c, bh_c, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            c_img.fill(QtCore.Qt.GlobalColor.transparent)
            cp = QtGui.QPainter(c_img)
            target_isl.compact.paint(cp, QtCore.QRectF(target_isl.body), 1.0, 0.0)
            cp.end()
            dr = target_isl.compact._notify_dismiss_rect
            assert dr is not None, "Expected _notify_dismiss_rect to be populated before manual click"
            pt_local = dr.center()
            pt_global = pt_local + QtCore.QPointF(target_isl._win_origin)
            ev_press = QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonPress,
                pt_local,
                pt_global,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            )
            ev_rel = QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonRelease,
                pt_local,
                pt_global,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            )
            hit_flag = {"dismissed": False}
            def _on_dismiss():
                hit_flag["dismissed"] = True
                target_isl.compact.clear_activity("notify")
            target_isl.notify_banner_dismissed.connect(_on_dismiss)
            try:
                target_isl.mousePressEvent(ev_press)
                assert target_isl._press_ctx is not None and target_isl._press_ctx[3] == "dismiss"
                target_isl.mouseReleaseEvent(ev_rel)
                assert hit_flag["dismissed"], "Actual mouse click on dismiss button did not emit notify_banner_dismissed"
            finally:
                target_isl.notify_banner_dismissed.disconnect(_on_dismiss)

        def _assert_five_banner_sequences_60hz(target_isl: Island, edge: str, tag: str):
            R_e = target_isl._avail()
            orig_along_y = target_isl._anchor_along(36.0, 148.0)
            expected_cx = (R_e.left() + 18.0) if edge == "left" else (R_e.right() - 18.0)
            pre_banner_gbody = target_isl.body.translated(QtCore.QPointF(target_isl._win_origin))
            assert abs(target_isl.body.width() - 36.0) < 0.05
            assert abs(target_isl.body.height() - 148.0) < 0.05
            assert abs(pre_banner_gbody.center().y() - orig_along_y) < 0.5

            banner_cases = [
                ("volume", lambda: target_isl.activity({"type": "volume", "value": 0.65, "muted": False}, 1.8), "timeout"),
                ("notify_timeout", lambda: target_isl.activity({"type": "notify", "title": "微信", "sub": "测试超时回收"}, 2.0), "timeout"),
                ("notify_manual_click", lambda: target_isl.activity({"type": "notify", "title": "微信", "sub": "测试手动点击关闭"}, 5.0), "manual_click"),
                ("life", lambda: target_isl.activity({"type": "life-reminder", "kind": "water", "title": "喝水提醒", "sub": "补充水分"}, 2.0), "timeout"),
                ("subtitle", lambda: (target_isl.compact.set_sub_running(True), target_isl.compact.set_sub("テスト字幕", "双语字幕超时回收测试")), "sub_timeout"),
            ]

            for case_name, trigger_fn, end_kind in banner_cases:
                trigger_fn()
                if case_name == "volume":
                    # 左右侧边纵向音量条：沿用侧边竖条宽度 36px 与当前高度 148px，支持连续调音量与静音图标切换，顶/底边保持横向 280×40
                    assert target_isl.compact.layout_state() == ("vertical", "activity")
                    assert target_isl.target_size() == (36.0, 148.0)
                    target_isl.compact.update_activity(value=0.90, muted=False, until=time.monotonic() + 1.8)
                    assert target_isl.target_size() == (36.0, 148.0)
                    target_isl.compact.update_activity(value=0.90, muted=True, until=time.monotonic() + 1.8)
                    assert target_isl.target_size() == (36.0, 148.0)
                for _ in range(120):
                    target_isl._last = time.perf_counter() - (1.0 / 60.0)
                    target_isl._tick()
                if case_name == "volume":
                    assert abs(target_isl.body.width() - 36.0) < 0.05
                    assert abs(target_isl.body.height() - 148.0) < 0.05
                # 结束前一帧记录基准
                pre_origin = QtCore.QPoint(target_isl._win_origin)
                vm_pre = target_isl.compact._vertical_idle_layout_metrics(target_isl.body)
                prev_hh_gy = vm_pre["hh_rect"].center().y() + target_isl._win_origin.y()
                prev_mm_gy = vm_pre["mm_rect"].center().y() + target_isl._win_origin.y()

                # 触发自然超时或通过真实鼠标点击关闭按钮手动关闭
                if end_kind == "timeout":
                    target_isl.compact.activity["until"] = time.monotonic() - 0.001
                elif end_kind == "manual_click":
                    _click_notify_dismiss_button(target_isl)
                elif end_kind == "sub_timeout":
                    target_isl.compact.sub_t = time.monotonic() - 10.0

                for frame_idx in range(120):
                    target_isl._last = time.perf_counter() - (1.0 / 60.0)
                    target_isl._tick()
                    # 窗口原点在 anchored 回收全程保持不动
                    assert target_isl._win_origin == pre_origin, f"{tag} {edge} {case_name} frame {frame_idx} origin moved"
                    # 进入回收首帧起旧热区立即失效
                    assert target_isl.compact._notify_dismiss_rect is None
                    assert target_isl.compact.play_rect is None

                    # 逐帧检查内容层可见像素（无全透明空白帧）
                    bw = max(20, round(target_isl.body.width()) + 2)
                    bh = max(20, round(target_isl.body.height()) + 2)
                    f_img = QtGui.QImage(bw, bh, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    f_img.fill(QtCore.Qt.GlobalColor.transparent)
                    fp = QtGui.QPainter(f_img)
                    r_local = QtCore.QRectF(0.0, 0.0, target_isl.body.width(), target_isl.body.height())
                    target_isl.compact.paint(fp, r_local, 1.0, 0.0)
                    fp.end()
                    max_a = 0
                    for py in range(0, bh, 2):
                        for px in range(0, bw, 2):
                            a_px = QtGui.qAlpha(f_img.pixel(px, py))
                            if a_px > max_a:
                                max_a = a_px
                    assert max_a > 55, f"{tag} {edge} {case_name} blank content frame at frame {frame_idx} (w={target_isl.body.width():.1f}, h={target_isl.body.height():.1f})"

                    # 逐帧检查小时行与分钟行全局 Y 坐标连续移动（相邻帧均 < 8.0 Qt 逻辑像素）
                    vm_f = target_isl.compact._vertical_idle_layout_metrics(target_isl.body)
                    hh_gy = vm_f["hh_rect"].center().y() + target_isl._win_origin.y()
                    mm_gy = vm_f["mm_rect"].center().y() + target_isl._win_origin.y()
                    d_hh = abs(hh_gy - prev_hh_gy)
                    d_mm = abs(mm_gy - prev_mm_gy)
                    assert d_hh < 8.0, f"{tag} {edge} {case_name} frame {frame_idx} hh_gy jumped by {d_hh:.2f}px"
                    assert d_mm < 8.0, f"{tag} {edge} {case_name} frame {frame_idx} mm_gy jumped by {d_mm:.2f}px"
                    prev_hh_gy = hh_gy
                    prev_mm_gy = mm_gy

                if end_kind == "sub_timeout":
                    target_isl.compact.set_sub_running(False)
                gbody = target_isl.body.translated(QtCore.QPointF(target_isl._win_origin))
                assert abs(target_isl.body.width() - 36.0) < 0.05
                assert abs(target_isl.body.height() - 148.0) < 0.05
                assert abs(gbody.center().x() - expected_cx) < 0.5
                assert abs(gbody.center().y() - orig_along_y) < 0.5

        for edge in ("left", "right"):
            # (i) 中段 pos=0.5 回归
            isl.compact.weather = ("\u2600", "-18°", None, "01d")
            isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            _assert_five_banner_sequences_60hz(isl, edge, "mid_edge")

            # (ii) 左下/右下底角复现场景：先将无天气、无未读点的 36×92 竖条拖到可停靠的最下方落定，再加天气和三颗未读点变 36×148
            isl.compact.weather = None
            isl.compact.badges = {}
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            assert isl.target_size() == (36.0, 92.0)

            R_cur = isl._avail()
            _, hi_92 = isl._along_range(edge, 36.0, 92.0)
            start_pt = isl.body.center().toPoint() + isl._win_origin
            isl.begin_drag(start_pt)
            isl.drag_off = QtCore.QPointF(0.0, 0.0)
            bottom_x = int(R_cur.left() + 8) if edge == "left" else int(R_cur.right() - 8)
            bottom_y = int(round(hi_92 + 10.0))
            isl.fx.snap(float(bottom_x))
            isl.fy.snap(float(bottom_y))
            isl._end_drag(QtCore.QPoint(bottom_x, bottom_y))
            for _ in range(180):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert isl.mode == "anchored" and isl.edge == edge
            assert abs(isl.body.height() - 92.0) < 0.05
            gbody_92 = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(gbody_92.center().y() - hi_92) < 0.5

            # 加入天气和 QQ、微信、邮件三颗未读点，使其变为 36×148 并收敛稳定
            isl.compact.weather = ("\u2600", "-18°", None, "01d")
            isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
            for _ in range(180):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert abs(isl.body.width() - 36.0) < 0.05
            assert abs(isl.body.height() - 148.0) < 0.05
            _, hi_148 = isl._along_range(edge, 36.0, 148.0)
            gbody_148 = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(gbody_148.center().y() - hi_148) < 0.5

            # 在左下/右下底角跑满 5 条序列（4 类自然超时 + 真实点击关闭按钮）
            _assert_five_banner_sequences_60hz(isl, edge, "bottom_corner")

            # (iii) 新增路径 1：通知仍在显示时（侧边竖条含天气与三颗未读点），把岛体拖到左下/右下底角松手
            isl.compact.weather = ("\u2600", "-18°", None, "01d")
            isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            isl.activity({"type": "notify", "title": "微信", "sub": "通知显示期拖到底角"}, 15.0)
            for _ in range(90):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert isl.compact.layout_state() == ("horizontal", "activity")
            R_p1 = isl._avail()
            _, hi_148_p1 = isl._along_range(edge, 36.0, 148.0)
            start_p1 = isl.body.center().toPoint() + isl._win_origin
            isl.begin_drag(start_p1)
            isl.drag_off = QtCore.QPointF(0.0, 0.0)
            w_drag_p1 = isl.sw.target
            bx_p1 = int(round(R_p1.left() + w_drag_p1 / 2.0 - 4.0)) if edge == "left" else int(round(R_p1.right() - w_drag_p1 / 2.0 + 4.0))
            by_p1 = int(round(R_p1.bottom() - 18.0 - 30.0))
            isl.fx.snap(float(bx_p1))
            isl.fy.snap(float(by_p1))
            isl._end_drag(QtCore.QPoint(bx_p1, by_p1))
            assert isl.edge == edge and isl.mode == "snap"

            last_snap_gb = None
            last_snap_gd = None
            first_anch_gb = None
            first_anch_gd = None
            for _ in range(240):
                prev_m1 = isl.mode
                if prev_m1 == "snap":
                    bw1 = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh1 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    img1 = QtGui.QImage(bw1, bh1, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    img1.fill(QtCore.Qt.GlobalColor.transparent)
                    p1 = QtGui.QPainter(img1)
                    isl.compact.paint(p1, QtCore.QRectF(isl.body), 1.0, 0.0)
                    p1.end()
                    last_snap_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    dr1 = isl.compact._notify_dismiss_rect
                    last_snap_gd = dr1.translated(QtCore.QPointF(isl._win_origin)) if dr1 is not None else None
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                # 逐帧检查无全透明空白帧
                bw_c1 = max(20, round(isl.body.width()) + 2)
                bh_c1 = max(20, round(isl.body.height()) + 2)
                f_img1 = QtGui.QImage(bw_c1, bh_c1, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                f_img1.fill(QtCore.Qt.GlobalColor.transparent)
                fp1 = QtGui.QPainter(f_img1)
                isl.compact.paint(fp1, QtCore.QRectF(0.0, 0.0, isl.body.width(), isl.body.height()), 1.0, 0.0)
                fp1.end()
                max_a1 = max(QtGui.qAlpha(f_img1.pixel(px, py)) for py in range(0, bh_c1, 2) for px in range(0, bw_c1, 2))
                assert max_a1 > 55, f"path1 {edge} blank frame during snap"

                if prev_m1 == "snap" and isl.mode == "anchored" and first_anch_gb is None:
                    bw1 = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh1 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    img1 = QtGui.QImage(bw1, bh1, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    img1.fill(QtCore.Qt.GlobalColor.transparent)
                    p1 = QtGui.QPainter(img1)
                    isl.compact.paint(p1, QtCore.QRectF(isl.body), 1.0, 0.0)
                    p1.end()
                    first_anch_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    dr1 = isl.compact._notify_dismiss_rect
                    first_anch_gd = dr1.translated(QtCore.QPointF(isl._win_origin)) if dr1 is not None else None
                    break

            assert isl.mode == "anchored"
            assert isl.compact.layout_state() == ("horizontal", "activity")
            assert last_snap_gb is not None and first_anch_gb is not None
            assert last_snap_gd is not None and first_anch_gd is not None
            assert abs(first_anch_gb.center().x() - last_snap_gb.center().x()) < 0.5
            assert abs(first_anch_gb.center().y() - last_snap_gb.center().y()) < 0.5
            assert abs(first_anch_gd.center().x() - last_snap_gd.center().x()) < 0.5
            assert abs(first_anch_gd.center().y() - last_snap_gd.center().y()) < 0.5

            # 随后等通知自然超时，确认正常回收到该落点的 36×148 竖条
            isl.compact.activity["until"] = time.monotonic() - 0.001
            vm_p1_0 = isl.compact._vertical_idle_layout_metrics(isl.body)
            prev_hh_p1 = vm_p1_0["hh_rect"].center().y() + isl._win_origin.y()
            prev_mm_p1 = vm_p1_0["mm_rect"].center().y() + isl._win_origin.y()
            for _ in range(120):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                vm_p1 = isl.compact._vertical_idle_layout_metrics(isl.body)
                cur_hh_p1 = vm_p1["hh_rect"].center().y() + isl._win_origin.y()
                cur_mm_p1 = vm_p1["mm_rect"].center().y() + isl._win_origin.y()
                assert abs(cur_hh_p1 - prev_hh_p1) < 8.0
                assert abs(cur_mm_p1 - prev_mm_p1) < 8.0
                prev_hh_p1, prev_mm_p1 = cur_hh_p1, cur_mm_p1
            gbody_p1_end = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(isl.body.width() - 36.0) < 0.05
            assert abs(isl.body.height() - 148.0) < 0.05
            assert abs(gbody_p1_end.center().y() - hi_148_p1) < 0.5

            # (iv) 新增路径 2：关闭未读点，无天气的 36×92 竖条停在左右底角后，首次收到天气数据变为 36×136
            isl.compact.weather = None
            isl.compact.badges = {}
            isl.compact.show_weather = True
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            R_p2 = isl._avail()
            _, hi_92_p2 = isl._along_range(edge, 36.0, 92.0)
            _, hi_136_p2 = isl._along_range(edge, 36.0, 136.0)
            isl.begin_drag(isl.body.center().toPoint() + isl._win_origin)
            isl.drag_off = QtCore.QPointF(0.0, 0.0)
            bx_p2 = int(R_p2.left() + 8) if edge == "left" else int(R_p2.right() - 8)
            by_p2 = int(round(hi_92_p2 + 10.0))
            isl.fx.snap(float(bx_p2))
            isl.fy.snap(float(by_p2))
            isl._end_drag(QtCore.QPoint(bx_p2, by_p2))
            for _ in range(180):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert isl.mode == "anchored" and isl.edge == edge
            assert abs(isl.body.width() - 36.0) < 0.05 and abs(isl.body.height() - 92.0) < 0.05

            g0_p2 = isl.body.translated(QtCore.QPointF(isl._win_origin))
            vm0_p2 = isl.compact._vertical_idle_layout_metrics(isl.body)
            prev_cy_p2 = g0_p2.center().y()
            prev_hh_p2 = vm0_p2["hh_rect"].center().y() + isl._win_origin.y()
            prev_mm_p2 = vm0_p2["mm_rect"].center().y() + isl._win_origin.y()

            # 送入首份有效天气数据并触发动画帧
            isl.compact.weather = ("\u2600", "18°", None, "01d")
            isl._kick()
            for f_p2 in range(120):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                gf_p2 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                # 屏幕边界约束：底边缘不越出 R.bottom() - (FILLET + EDGE_MARGIN)
                assert gf_p2.bottom() <= R_p2.bottom() - 19.0 + 0.5
                vm_p2 = isl.compact._vertical_idle_layout_metrics(isl.body)
                cy_p2 = gf_p2.center().y()
                hh_p2 = vm_p2["hh_rect"].center().y() + isl._win_origin.y()
                mm_p2 = vm_p2["mm_rect"].center().y() + isl._win_origin.y()
                assert abs(cy_p2 - prev_cy_p2) < 8.0, f"path2 {edge} frame {f_p2} cy jumped by {abs(cy_p2 - prev_cy_p2):.2f}"
                assert abs(hh_p2 - prev_hh_p2) < 8.0, f"path2 {edge} frame {f_p2} hh jumped by {abs(hh_p2 - prev_hh_p2):.2f}"
                assert abs(mm_p2 - prev_mm_p2) < 8.0, f"path2 {edge} frame {f_p2} mm jumped by {abs(mm_p2 - prev_mm_p2):.2f}"
                prev_cy_p2, prev_hh_p2, prev_mm_p2 = cy_p2, hh_p2, mm_p2

                bw_c2 = max(20, round(isl.body.width()) + 2)
                bh_c2 = max(20, round(isl.body.height()) + 2)
                f_img2 = QtGui.QImage(bw_c2, bh_c2, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                f_img2.fill(QtCore.Qt.GlobalColor.transparent)
                fp2 = QtGui.QPainter(f_img2)
                isl.compact.paint(fp2, QtCore.QRectF(0.0, 0.0, isl.body.width(), isl.body.height()), 1.0, 0.0)
                fp2.end()
                max_a2 = max(QtGui.qAlpha(f_img2.pixel(px, py)) for py in range(0, bh_c2, 2) for px in range(0, bw_c2, 2))
                assert max_a2 > 55, f"path2 {edge} blank frame at {f_p2}"

            g_p2_end = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(isl.body.width() - 36.0) < 0.05
            assert abs(isl.body.height() - 136.0) < 0.05
            assert abs(g_p2_end.center().y() - hi_136_p2) < 0.5

            # (v) 新增路径 3：无天气的 36×92 竖条停在左右底角后，弹出通知横幅，趁通知仍在显示时首次收到天气（分别测自然超时与真实点击关闭按钮）
            for end_kind_p3 in ("timeout", "manual_click"):
                isl.compact.weather = None
                isl.compact.badges = {}
                isl.compact.show_weather = True
                cfg.set("island.edge", edge, save=False)
                cfg.set("island.pos", 0.5, save=False)
                isl.apply_config()
                R_p3 = isl._avail()
                _, hi_92_p3 = isl._along_range(edge, 36.0, 92.0)
                _, hi_136_p3 = isl._along_range(edge, 36.0, 136.0)
                isl.begin_drag(isl.body.center().toPoint() + isl._win_origin)
                isl.drag_off = QtCore.QPointF(0.0, 0.0)
                bx_p3 = int(R_p3.left() + 8) if edge == "left" else int(R_p3.right() - 8)
                by_p3 = int(round(hi_92_p3 + 10.0))
                isl.fx.snap(float(bx_p3))
                isl.fy.snap(float(by_p3))
                isl._end_drag(QtCore.QPoint(bx_p3, by_p3))
                for _ in range(180):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                assert isl.mode == "anchored" and isl.edge == edge
                assert abs(isl.body.width() - 36.0) < 0.05 and abs(isl.body.height() - 92.0) < 0.05

                # 弹出通知并等待横幅尺寸与关闭按钮热区稳定
                isl.activity({"type": "notify", "title": "微信", "sub": "有新消息"}, 10.0)
                for _ in range(120):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                bw_p3 = max(40, int(math.ceil(isl.body.right())) + 8)
                bh_p3 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                im0_p3 = QtGui.QImage(bw_p3, bh_p3, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                im0_p3.fill(QtCore.Qt.GlobalColor.transparent)
                p0_p3 = QtGui.QPainter(im0_p3)
                isl.compact.paint(p0_p3, QtCore.QRectF(isl.body), 1.0, 0.0)
                p0_p3.end()
                pre_wea_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                assert isl.compact._notify_dismiss_rect is not None
                pre_wea_gd = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))

                # 趁同一条通知仍在显示，通过天气回调送入首份有效数据
                isl.compact.weather = ("\u2600", "18°", None, "01d")
                isl._kick()
                for _ in range(30):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    bw_p3 = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh_p3 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    im1_p3 = QtGui.QImage(bw_p3, bh_p3, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    im1_p3.fill(QtCore.Qt.GlobalColor.transparent)
                    p1_p3 = QtGui.QPainter(im1_p3)
                    isl.compact.paint(p1_p3, QtCore.QRectF(isl.body), 1.0, 0.0)
                    p1_p3.end()
                    cur_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    assert isl.compact._notify_dismiss_rect is not None
                    cur_gd = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))
                    # 天气更新帧及通知继续显示期间，横幅与关闭热区位置均保持稳定（无 22px 跳位）
                    assert abs(cur_gb.center().y() - pre_wea_gb.center().y()) < 0.05
                    assert abs(cur_gd.center().y() - pre_wea_gd.center().y()) < 0.05
                    is_nb, is_dis = isl._on_notify_banner(isl.compact._notify_dismiss_rect.center())
                    assert is_nb and is_dis

                vm0_p3 = isl.compact._vertical_idle_layout_metrics(isl.body)
                prev_hh_p3 = vm0_p3["hh_rect"].center().y() + isl._win_origin.y()
                prev_mm_p3 = vm0_p3["mm_rect"].center().y() + isl._win_origin.y()
                if end_kind_p3 == "timeout":
                    isl.compact.activity["until"] = time.monotonic() - 0.001
                else:
                    _click_notify_dismiss_button(isl)

                for f_p3 in range(120):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    assert isl.compact._notify_dismiss_rect is None
                    gf_p3 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    assert gf_p3.bottom() <= R_p3.bottom() - 19.0 + 0.5
                    vm_p3 = isl.compact._vertical_idle_layout_metrics(isl.body)
                    hh_p3 = vm_p3["hh_rect"].center().y() + isl._win_origin.y()
                    mm_p3 = vm_p3["mm_rect"].center().y() + isl._win_origin.y()
                    assert abs(hh_p3 - prev_hh_p3) < 8.0, f"path3 {edge} {end_kind_p3} f={f_p3} d_hh={abs(hh_p3 - prev_hh_p3):.2f}"
                    assert abs(mm_p3 - prev_mm_p3) < 8.0, f"path3 {edge} {end_kind_p3} f={f_p3} d_mm={abs(mm_p3 - prev_mm_p3):.2f}"
                    prev_hh_p3, prev_mm_p3 = hh_p3, mm_p3

                    bw_c3 = max(20, round(isl.body.width()) + 2)
                    bh_c3 = max(20, round(isl.body.height()) + 2)
                    f_img3 = QtGui.QImage(bw_c3, bh_c3, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    f_img3.fill(QtCore.Qt.GlobalColor.transparent)
                    fp3 = QtGui.QPainter(f_img3)
                    isl.compact.paint(fp3, QtCore.QRectF(0.0, 0.0, isl.body.width(), isl.body.height()), 1.0, 0.0)
                    fp3.end()
                    max_a3 = max(QtGui.qAlpha(f_img3.pixel(px, py)) for py in range(0, bh_c3, 2) for px in range(0, bw_c3, 2))
                    assert max_a3 > 55, f"path3 {edge} {end_kind_p3} blank frame at {f_p3}"

                g_p3_end = isl.body.translated(QtCore.QPointF(isl._win_origin))
                assert abs(isl.body.width() - 36.0) < 0.05
                assert abs(isl.body.height() - 136.0) < 0.05
                assert abs(g_p3_end.center().y() - hi_136_p3) < 0.5

        # 6) 窄屏限宽、200% 窄屏长字幕、面板放不下拒止展开、窗口 <900×680 自适应与跨屏边界松手归属
        isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
        cfg.set("island.edge", "left", save=False)
        isl.apply_config()
        orig_avail = isl._avail
        orig_sg = isl._screen_geom
        try:
            # 模拟 200% 缩放窄屏（逻辑尺寸 360×640 < 900×680，且放不下 419×140 展开面板）
            isl._avail = lambda: QtCore.QRectF(0.0, 0.0, 360.0, 640.0)
            isl._screen_geom = lambda: QtCore.QRect(0, 0, 360, 640)
            isl._update_geometry(force=True)
            assert isl.width() == 360 and isl.height() == 640
            assert not isl.can_fit_expanded(419, 140)
            isl.expand("home")
            assert not isl.expanded  # 面板放不下时拒止展开，维持 36px 竖条

            # 在 200% 窄屏弹出长双语字幕（原宽 640+12=652，被唯一限宽夹紧到 avail_max_w = 360 - 12 - 13 = 335px）
            isl.compact.set_sub_running(True)
            isl.compact.set_sub("長い日本語のリアルタイム字幕テスト文字列です", "这是一段超长的双语实时字幕窄屏限宽与省略测试文本")
            tw_sub, th_sub = isl.target_size()
            assert tw_sub == 335.0 and th_sub == 60.0
            for _ in range(180):
                isl._tick()
            assert abs(isl.body.width() - 335.0) < 0.5
            sub_img = _render_vertical_compact_image(isl.compact, tw_sub, th_sub, dpr=2.0)
            assert sub_img.width() == round(335.0 * 2.0)

            # 字幕超时结束后平滑回收到 36×148 竖时钟
            isl.compact.set_sub_running(False)
            assert isl.target_size() == (36.0, 148.0)
            for _ in range(180):
                isl._tick()
            assert abs(isl.body.width() - 36.0) < 0.1
        finally:
            isl._avail = orig_avail
            isl._screen_geom = orig_sg
            isl.resize(900, 680)
            isl._update_geometry(force=True)
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.save()
        isl.close()


def test_07_cross_screen_cursor_vs_center_release_and_post_land_squeeze_banner_recovery(qt_app, monkeypatch):
    """验证混合 DPI 跨屏拖拽（光标已入目标屏、岛体中心仍在原屏时松手）归属目标屏、落点零二次跳位、36px 稳定宽度，以及跨屏落定后的挤压回弹与四类横幅回收。"""
    from wisland.core.config import cfg
    from wisland.ui.island import Island

    class _MockScreen:
        def __init__(self, name: str, geom: QtCore.QRect, avail: QtCore.QRect, dpr: float):
            self._name = name
            self._geom = geom
            self._avail = avail
            self._dpr = dpr

        def name(self):
            return self._name

        def geometry(self):
            return QtCore.QRect(self._geom)

        def availableGeometry(self):
            return QtCore.QRect(self._avail)

        def devicePixelRatio(self):
            return self._dpr

    # 主屏 A (100% 缩放, 0..1920×1080) 与 副屏 B (150% 缩放, 位于左侧负坐标 -1280..0×720)
    scr_a = _MockScreen("Primary-100pct", QtCore.QRect(0, 0, 1920, 1080), QtCore.QRect(0, 0, 1920, 1040), 1.0)
    scr_b = _MockScreen("Secondary-150pct", QtCore.QRect(-1280, 0, 1280, 720), QtCore.QRect(-1280, 0, 1280, 680), 1.5)
    mock_screens = [scr_a, scr_b]

    def _mock_screen_at(pt: QtCore.QPoint):
        for s in mock_screens:
            if s.geometry().contains(pt):
                return s
        return None

    class _StubPage(QtWidgets.QWidget):
        key = "home"
        title = "首页"
        glyph = "H"
        island = None

        def preferred_panel_width(self):
            return 419

        def preferred_panel_height(self):
            return 140

        def preferred_height(self):
            return 100

        def on_show(self):
            pass

        def on_hide(self):
            pass

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    orig_scr = cfg.get("island.screen", 0)
    isl = Island([_StubPage()])
    try:
        monkeypatch.setattr(QtGui.QGuiApplication, "screens", staticmethod(lambda: list(mock_screens)))
        monkeypatch.setattr(QtGui.QGuiApplication, "primaryScreen", staticmethod(lambda: scr_a))
        monkeypatch.setattr(QtGui.QGuiApplication, "screenAt", staticmethod(_mock_screen_at))
        monkeypatch.setattr(isl.backdrop, "request", lambda *a, **k: None)

        isl.compact._clock_override = "08:08"
        isl.compact.weather = ("\u2600", "-18°", None, "01d")
        isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}

        # 1) A -> B 跨屏：在主屏 A 抓起胶囊右端（产生较大正抓手偏移），光标跨过 x=0 进入左副屏 B (-12, 340)，而岛体中心仍在主屏 A (+38, 340) 时松手
        isl.scr = scr_a
        isl.edge = "top"
        isl.compact.set_edge("top")
        isl.mode = "anchored"
        isl._update_geometry(force=True)

        isl.begin_drag(QtCore.QPoint(960, 20))
        isl.drag_off = QtCore.QPointF(50.0, 0.0)
        cursor_in_b = QtCore.QPoint(-12, 340)   # 光标已进入副屏 B 右边缘内侧
        center_in_a = QtCore.QPointF(38.0, 340.0)  # 岛体中心仍在主屏 A 内
        assert _mock_screen_at(cursor_in_b) is scr_b
        assert _mock_screen_at(center_in_a.toPoint()) is scr_a

        isl.fx.snap(center_in_a.x())
        isl.fy.snap(center_in_a.y())
        isl._end_drag(cursor_in_b)
        # 即使期间收到 WM_DPICHANGED (_on_screens_changed)，目标屏幕仍锁定为副屏 B
        isl._on_screens_changed()
        assert isl.scr is scr_b
        assert isl.edge == "right"
        assert isl.target_size() == (36.0, 148.0)

        last_snap_g = None
        first_anch_g = None
        for _ in range(240):
            prev_m = isl.mode
            if prev_m == "snap":
                last_snap_g = isl.body.translated(QtCore.QPointF(isl._win_origin))
            isl._last = time.perf_counter() - (1.0 / 60.0)
            isl._tick()
            if prev_m == "snap" and isl.mode == "anchored" and first_anch_g is None:
                first_anch_g = isl.body.translated(QtCore.QPointF(isl._win_origin))
                break

        assert isl.mode == "anchored"
        assert isl.scr is scr_b
        assert last_snap_g is not None and first_anch_g is not None
        assert abs(last_snap_g.center().x() - first_anch_g.center().x()) < 0.5
        assert abs(last_snap_g.center().y() - first_anch_g.center().y()) < 0.5
        assert abs(isl.body.width() - 36.0) < 0.05
        # 副屏 B 右边缘停靠线 isl._line("right")，中心距停靠线精确为 18.0 逻辑像素
        assert abs((isl._line("right") - first_anch_g.center().x()) - 18.0) < 0.5

        # 2) B -> A 反向跨屏：从副屏 B 拖回主屏 A 左边缘（光标已入主屏 A x=+10，岛体中心仍在副屏 B x=-30 时松手）
        isl.begin_drag(QtCore.QPoint(-18, 340))
        isl.drag_off = QtCore.QPointF(-40.0, 0.0)
        cursor_in_a = QtCore.QPoint(10, 500)
        center_in_b = QtCore.QPointF(-30.0, 500.0)
        assert _mock_screen_at(cursor_in_a) is scr_a
        assert _mock_screen_at(center_in_b.toPoint()) is scr_b
        isl.fx.snap(center_in_b.x())
        isl.fy.snap(center_in_b.y())
        isl._end_drag(cursor_in_a)
        isl._on_screens_changed()
        assert isl.scr is scr_a
        assert isl.edge == "left"

        for _ in range(240):
            isl._last = time.perf_counter() - (1.0 / 60.0)
            isl._tick()
        assert isl.mode == "anchored"
        assert isl.scr is scr_a
        gbody_a = isl.body.translated(QtCore.QPointF(isl._win_origin))
        assert abs(isl.body.width() - 36.0) < 0.05
        assert abs(gbody_a.center().x() - 18.0) < 0.5

        # 3) 跨屏落定后，在左右底角复测两条新增路径（Path 1 通知显示期拖到底角松手 & Path 2 36×92 底角首次收到天气变 36×136）、挤压回弹阈值连续性与 5 条横幅回收序列
        for edge in ("left", "right"):
            # --- 3a) Path 1：通知仍在显示时拖到左右底角松手 -> snap 切 anchored 零跳位 -> 超时回收至 36×148 ---
            isl.compact.weather = ("\u2600", "-18°", None, "01d")
            isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
            isl.edge = edge
            isl.compact.set_edge(edge)
            isl.pos_frac = 0.5
            isl.sw.snap(36.0)
            isl.sh.snap(148.0)
            isl._update_geometry(force=True)
            isl.activity({"type": "notify", "title": "微信", "sub": "跨屏后通知拖到底角"}, 15.0)
            for _ in range(90):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert isl.compact.layout_state() == ("horizontal", "activity")
            R_cur = isl._avail()
            _, hi_148_p1 = isl._along_range(edge, 36.0, 148.0)
            isl.begin_drag(isl.body.center().toPoint() + isl._win_origin)
            isl.drag_off = QtCore.QPointF(0.0, 0.0)
            w_d1 = isl.sw.target
            bx_d1 = int(round(R_cur.left() + w_d1 / 2.0 - 4.0)) if edge == "left" else int(round(R_cur.right() - w_d1 / 2.0 + 4.0))
            by_d1 = int(round(R_cur.bottom() - 18.0 - 30.0))
            isl.fx.snap(float(bx_d1))
            isl.fy.snap(float(by_d1))
            isl._end_drag(QtCore.QPoint(bx_d1, by_d1))
            assert isl.edge == edge and isl.mode == "snap"

            last_s_gb = None
            last_s_gd = None
            first_a_gb = None
            first_a_gd = None
            for _ in range(240):
                pm = isl.mode
                if pm == "snap":
                    bw_s = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh_s = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    im_s = QtGui.QImage(bw_s, bh_s, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    im_s.fill(QtCore.Qt.GlobalColor.transparent)
                    ps = QtGui.QPainter(im_s)
                    isl.compact.paint(ps, QtCore.QRectF(isl.body), 1.0, 0.0)
                    ps.end()
                    last_s_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    drs = isl.compact._notify_dismiss_rect
                    last_s_gd = drs.translated(QtCore.QPointF(isl._win_origin)) if drs is not None else None
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                if pm == "snap" and isl.mode == "anchored" and first_a_gb is None:
                    bw_s = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh_s = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    im_s = QtGui.QImage(bw_s, bh_s, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    im_s.fill(QtCore.Qt.GlobalColor.transparent)
                    ps = QtGui.QPainter(im_s)
                    isl.compact.paint(ps, QtCore.QRectF(isl.body), 1.0, 0.0)
                    ps.end()
                    first_a_gb = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    drs = isl.compact._notify_dismiss_rect
                    first_a_gd = drs.translated(QtCore.QPointF(isl._win_origin)) if drs is not None else None
                    break
            assert isl.mode == "anchored" and isl.compact.layout_state() == ("horizontal", "activity")
            assert last_s_gb is not None and first_a_gb is not None
            assert last_s_gd is not None and first_a_gd is not None
            assert abs(first_a_gb.center().x() - last_s_gb.center().x()) < 0.5
            assert abs(first_a_gb.center().y() - last_s_gb.center().y()) < 0.5
            assert abs(first_a_gd.center().x() - last_s_gd.center().x()) < 0.5
            assert abs(first_a_gd.center().y() - last_s_gd.center().y()) < 0.5

            isl.compact.activity["until"] = time.monotonic() - 0.001
            vm_p1_0 = isl.compact._vertical_idle_layout_metrics(isl.body)
            prev_hh1 = vm_p1_0["hh_rect"].center().y() + isl._win_origin.y()
            prev_mm1 = vm_p1_0["mm_rect"].center().y() + isl._win_origin.y()
            for _ in range(120):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                vm_p1 = isl.compact._vertical_idle_layout_metrics(isl.body)
                ch1 = vm_p1["hh_rect"].center().y() + isl._win_origin.y()
                cm1 = vm_p1["mm_rect"].center().y() + isl._win_origin.y()
                assert abs(ch1 - prev_hh1) < 8.0 and abs(cm1 - prev_mm1) < 8.0
                prev_hh1, prev_mm1 = ch1, cm1
            gb_p1_end = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(isl.body.width() - 36.0) < 0.05 and abs(isl.body.height() - 148.0) < 0.05
            assert abs(gb_p1_end.center().y() - hi_148_p1) < 0.5

            # --- 3b) Path 2：无天气無未读点的 36×92 竖条停在左右底角后，首次收到天气数据变为 36×136 ---
            isl.compact.weather = None
            isl.compact.badges = {}
            isl.compact.show_weather = True
            isl.edge = edge
            isl.compact.set_edge(edge)
            isl.pos_frac = 0.5
            isl.sw.snap(36.0)
            isl.sh.snap(92.0)
            isl._update_geometry(force=True)

            _, hi_92 = isl._along_range(edge, 36.0, 92.0)
            _, hi_136 = isl._along_range(edge, 36.0, 136.0)
            start_pt = isl.body.center().toPoint() + isl._win_origin
            isl.begin_drag(start_pt)
            isl.drag_off = QtCore.QPointF(0.0, 0.0)
            bottom_x = int(R_cur.left() + 8) if edge == "left" else int(R_cur.right() - 8)
            bottom_y = int(round(hi_92 + 10.0))
            isl.fx.snap(float(bottom_x))
            isl.fy.snap(float(bottom_y))
            isl._end_drag(QtCore.QPoint(bottom_x, bottom_y))
            for _ in range(180):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert isl.mode == "anchored" and isl.edge == edge
            assert abs(isl.body.height() - 92.0) < 0.05

            g0_p2 = isl.body.translated(QtCore.QPointF(isl._win_origin))
            vm0_p2 = isl.compact._vertical_idle_layout_metrics(isl.body)
            prev_cy2 = g0_p2.center().y()
            prev_hh2 = vm0_p2["hh_rect"].center().y() + isl._win_origin.y()
            prev_mm2 = vm0_p2["mm_rect"].center().y() + isl._win_origin.y()
            isl.compact.weather = ("\u2600", "-18°", None, "01d")
            isl._kick()
            for _ in range(120):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                gf2 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                assert gf2.bottom() <= R_cur.bottom() - 19.0 + 0.5
                vm2 = isl.compact._vertical_idle_layout_metrics(isl.body)
                cy2 = gf2.center().y()
                hh2 = vm2["hh_rect"].center().y() + isl._win_origin.y()
                mm2 = vm2["mm_rect"].center().y() + isl._win_origin.y()
                assert abs(cy2 - prev_cy2) < 8.0
                assert abs(hh2 - prev_hh2) < 8.0
                assert abs(mm2 - prev_mm2) < 8.0
                prev_cy2, prev_hh2, prev_mm2 = cy2, hh2, mm2
            assert abs(isl.body.width() - 36.0) < 0.05 and abs(isl.body.height() - 136.0) < 0.05
            assert abs(isl.body.translated(QtCore.QPointF(isl._win_origin)).center().y() - hi_136) < 0.5

            # --- 3b-2) Path 3：跨屏后在左右底角，无天气 36×92 弹出通知，通知显示期间首次收到天气，分别验证自然超时和手动点击关闭 ---
            for end_kind_p3 in ("timeout", "manual_click"):
                isl.compact.weather = None
                isl.compact.badges = {}
                isl.compact.show_weather = True
                isl.edge = edge
                isl.compact.set_edge(edge)
                isl.pos_frac = 0.5
                isl.sw.snap(36.0)
                isl.sh.snap(92.0)
                isl._update_geometry(force=True)

                start_pt3 = isl.body.center().toPoint() + isl._win_origin
                isl.begin_drag(start_pt3)
                isl.drag_off = QtCore.QPointF(0.0, 0.0)
                bx3 = int(R_cur.left() + 8) if edge == "left" else int(R_cur.right() - 8)
                by3 = int(round(hi_92 + 10.0))
                isl.fx.snap(float(bx3))
                isl.fy.snap(float(by3))
                isl._end_drag(QtCore.QPoint(bx3, by3))
                for _ in range(180):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                assert isl.mode == "anchored" and isl.edge == edge
                assert abs(isl.body.height() - 92.0) < 0.05

                isl.activity({"type": "notify", "title": "微信", "sub": "跨屏后通知期收天气"}, 10.0)
                for _ in range(120):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                bw3 = max(40, int(math.ceil(isl.body.right())) + 8)
                bh3 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                im0_3 = QtGui.QImage(bw3, bh3, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                im0_3.fill(QtCore.Qt.GlobalColor.transparent)
                p0_3 = QtGui.QPainter(im0_3)
                isl.compact.paint(p0_3, QtCore.QRectF(isl.body), 1.0, 0.0)
                p0_3.end()
                pre_gb3 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                assert isl.compact._notify_dismiss_rect is not None
                pre_gd3 = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))

                isl.compact.weather = ("\u2600", "18°", None, "01d")
                isl._kick()
                for _ in range(30):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    bw3 = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh3 = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    im1_3 = QtGui.QImage(bw3, bh3, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    im1_3.fill(QtCore.Qt.GlobalColor.transparent)
                    p1_3 = QtGui.QPainter(im1_3)
                    isl.compact.paint(p1_3, QtCore.QRectF(isl.body), 1.0, 0.0)
                    p1_3.end()
                    cur_gb3 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    assert isl.compact._notify_dismiss_rect is not None
                    cur_gd3 = isl.compact._notify_dismiss_rect.translated(QtCore.QPointF(isl._win_origin))
                    assert abs(cur_gb3.center().y() - pre_gb3.center().y()) < 0.05
                    assert abs(cur_gd3.center().y() - pre_gd3.center().y()) < 0.05

                vm0_3 = isl.compact._vertical_idle_layout_metrics(isl.body)
                prev_hh3 = vm0_3["hh_rect"].center().y() + isl._win_origin.y()
                prev_mm3 = vm0_3["mm_rect"].center().y() + isl._win_origin.y()
                if end_kind_p3 == "timeout":
                    isl.compact.activity["until"] = time.monotonic() - 0.001
                else:
                    dr3 = isl.compact._notify_dismiss_rect
                    assert dr3 is not None
                    pt_l3 = dr3.center()
                    pt_g3 = pt_l3 + QtCore.QPointF(isl._win_origin)
                    ev_p3 = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonPress, pt_l3, pt_g3,
                        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
                    )
                    ev_r3 = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonRelease, pt_l3, pt_g3,
                        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                    )
                    isl.notify_banner_dismissed.connect(lambda: isl.compact.clear_activity("notify"))
                    isl.mousePressEvent(ev_p3)
                    isl.mouseReleaseEvent(ev_r3)

                for _ in range(120):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    assert isl.compact._notify_dismiss_rect is None
                    gf3 = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    assert gf3.bottom() <= R_cur.bottom() - 19.0 + 0.5
                    vm3 = isl.compact._vertical_idle_layout_metrics(isl.body)
                    hh3 = vm3["hh_rect"].center().y() + isl._win_origin.y()
                    mm3 = vm3["mm_rect"].center().y() + isl._win_origin.y()
                    assert abs(hh3 - prev_hh3) < 8.0
                    assert abs(mm3 - prev_mm3) < 8.0
                    prev_hh3, prev_mm3 = hh3, mm3
                assert abs(isl.body.width() - 36.0) < 0.05 and abs(isl.body.height() - 136.0) < 0.05
                assert abs(isl.body.translated(QtCore.QPointF(isl._win_origin)).center().y() - hi_136) < 0.5

            # --- 3c) 再加入三颗未读点变 36×148，复测挤压回弹与 5 条横幅回收序列 ---
            isl.compact.badges = {"qq": 1, "wechat": 1, "mail": 1}
            for _ in range(180):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            assert abs(isl.body.width() - 36.0) < 0.05
            assert abs(isl.body.height() - 148.0) < 0.05
            _, hi_148 = isl._along_range(edge, 36.0, 148.0)
            orig_along_y = hi_148

            vm_1 = isl.compact._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 29.544, 148.0))
            vm_2 = isl.compact._vertical_idle_layout_metrics(QtCore.QRectF(0.0, 0.0, 29.546, 148.0))
            assert abs(vm_2["hh_rect"].center().y() - vm_1["hh_rect"].center().y()) < 0.02

            seq_cases = [
                ("volume", lambda: isl.activity({"type": "volume", "value": 0.5, "muted": False}, 1.8), "timeout"),
                ("notify_timeout", lambda: isl.activity({"type": "notify", "title": "微信", "sub": "跨屏后超时通知"}, 2.0), "timeout"),
                ("notify_manual_click", lambda: isl.activity({"type": "notify", "title": "微信", "sub": "跨屏后点击关闭"}, 5.0), "manual_click"),
                ("life", lambda: isl.activity({"type": "life-reminder", "kind": "sit", "title": "久坐提醒", "sub": "活动一下"}, 2.0), "timeout"),
                ("subtitle", lambda: (isl.compact.set_sub_running(True), isl.compact.set_sub("字幕テスト", "跨屏后底角双语字幕回收")), "sub_timeout"),
            ]
            for case_name, trigger_fn, end_kind in seq_cases:
                trigger_fn()
                for _ in range(90):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                pre_origin = QtCore.QPoint(isl._win_origin)
                vm_pre = isl.compact._vertical_idle_layout_metrics(isl.body)
                prev_hh = vm_pre["hh_rect"].center().y() + isl._win_origin.y()
                prev_mm = vm_pre["mm_rect"].center().y() + isl._win_origin.y()

                if end_kind == "timeout":
                    isl.compact.activity["until"] = time.monotonic() - 0.001
                elif end_kind == "manual_click":
                    bw_c = max(40, int(math.ceil(isl.body.right())) + 8)
                    bh_c = max(40, int(math.ceil(isl.body.bottom())) + 8)
                    c_img = QtGui.QImage(bw_c, bh_c, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                    c_img.fill(QtCore.Qt.GlobalColor.transparent)
                    cp = QtGui.QPainter(c_img)
                    isl.compact.paint(cp, QtCore.QRectF(isl.body), 1.0, 0.0)
                    cp.end()
                    dr = isl.compact._notify_dismiss_rect
                    assert dr is not None
                    pt_local = dr.center()
                    pt_global = pt_local + QtCore.QPointF(isl._win_origin)
                    ev_p = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonPress, pt_local, pt_global,
                        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
                    )
                    ev_r = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseButtonRelease, pt_local, pt_global,
                        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                    )
                    isl.notify_banner_dismissed.connect(lambda: isl.compact.clear_activity("notify"))
                    isl.mousePressEvent(ev_p)
                    isl.mouseReleaseEvent(ev_r)
                elif end_kind == "sub_timeout":
                    isl.compact.sub_t = time.monotonic() - 10.0

                for _ in range(110):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    assert isl._win_origin == pre_origin
                    assert isl.compact._notify_dismiss_rect is None
                    vm_c = isl.compact._vertical_idle_layout_metrics(isl.body)
                    cur_hh = vm_c["hh_rect"].center().y() + isl._win_origin.y()
                    cur_mm = vm_c["mm_rect"].center().y() + isl._win_origin.y()
                    assert abs(cur_hh - prev_hh) < 8.0, f"post-cross {edge} {case_name} d_hh={abs(cur_hh - prev_hh):.2f}"
                    assert abs(cur_mm - prev_mm) < 8.0, f"post-cross {edge} {case_name} d_mm={abs(cur_mm - prev_mm):.2f}"
                    prev_hh, prev_mm = cur_hh, cur_mm
                if end_kind == "sub_timeout":
                    isl.compact.set_sub_running(False)
                gbody_end = isl.body.translated(QtCore.QPointF(isl._win_origin))
                assert abs(isl.body.width() - 36.0) < 0.05
                assert abs(isl.body.height() - 148.0) < 0.05
                assert abs(gbody_end.center().y() - orig_along_y) < 0.5
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.set("island.screen", orig_scr, save=False)
        cfg.save()
        isl.close()


def test_08_settings_pos_preservation_zero_percent_side_music_and_panel_collapse_continuity(qt_app, tmp_path):
    """三项 P2 专项回归：
    1) 保存无关设置不覆盖四边非整数百分比位置与 0% 合法配置；主动修改停靠字段生效且重启保留；
    2) 左右侧 36px 竖版音乐视图（26px 封面 + 4 条波形 + 24px 圆形播放/暂停键）在 100%/150% 下居中无裁切、点击仅触发播放/暂停、横幅/音量临时覆盖后按当时状态恢复；
    3) 左右侧中段与上下两端展开首页/高邮件详情后收起到 36×92 / 36×136 / 36×148，60Hz 逐帧无 36.5 宽度尾段跳位、无空白帧、无越界。
    """
    from wisland.core.config import Config, cfg
    from wisland.ui.island import Island
    from wisland.ui.settings_window import SettingsWindow
    from wisland.ui.settings.island_pet import IslandPetSettingsPage

    class _FlexPage(QtWidgets.QWidget):
        def __init__(self, key: str, title: str, pw: int, ph: int):
            super().__init__()
            self.key = key
            self.title = title
            self.glyph = key[:1].upper()
            self.island = None
            self._pw = pw
            self._ph = ph

        def preferred_panel_width(self):
            return self._pw

        def preferred_panel_height(self):
            return self._ph

        def preferred_height(self):
            return self._ph - 36

        def on_show(self):
            pass

        def on_hide(self):
            pass

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    orig_scr = cfg.get("island.screen", 0)

    # ---------------- 1) 设置保存无关项不改变四边非整数百分比位置与 0% ----------------
    cm = Config(path=tmp_path / "p2_settings_pos.json")
    for edge in ("top", "bottom", "left", "right"):
        for raw_pos in (0.6234, 0.0, 0.9678):
            cm.set("island.edge", edge, save=False)
            cm.set("island.pos", raw_pos, save=False)
            cm.set("island.screen", 0, save=False)
            cm.save()

            win = SettingsWindow(ctx=None, config_obj=cm, initial_category="island_pet")
            try:
                # 切换到岛体页确保控件已构建，但只改一项无关设置（收起态显示天气）
                isl_pg = next(pg for pg in win.leaf_pages if isinstance(pg, IslandPetSettingsPage))
                isl_pg.ensure_built()
                assert isl_pg.sp_pos.value() == round(raw_pos * 100)
                cur_w = isl_pg.sw_compact_weather.isChecked()
                isl_pg.sw_compact_weather.setChecked(not cur_w)
                assert win.save_settings() is True
                # 保存后原值精确不变（0.6234 仍是 0.6234，0.0 仍是 0.0 不回退 0.5）
                assert cm.get("island.edge") == edge
                assert abs(float(cm.get("island.pos")) - raw_pos) < 1e-6
            finally:
                win.close()
                win.deleteLater()

    # 验证 0.0 读取在 Island 初始化和 apply_config 中不回退 0.5
    cfg.set("island.edge", "left", save=False)
    cfg.set("island.pos", 0.0, save=False)
    isl0 = Island([_FlexPage("home", "首页", 419, 140), _FlexPage("mail", "邮箱", 419, 236)])
    try:
        assert isl0.pos_frac == 0.0
        isl0.apply_config()
        assert isl0.pos_frac == 0.0

        # 验证主动修改位置与停靠边能生效且持久化
        cm2 = Config(path=tmp_path / "p2_settings_active.json")
        cm2.set("island.edge", "left", save=False)
        cm2.set("island.pos", 0.6234, save=False)
        cm2.save()
        win2 = SettingsWindow(ctx=None, config_obj=cm2, initial_category="island_pet")
        try:
            isl_pg2 = next(pg for pg in win2.leaf_pages if isinstance(pg, IslandPetSettingsPage))
            isl_pg2.ensure_built()
            isl_pg2.cb_edge.set_value("right")
            isl_pg2.sp_pos.setValue(0)
            assert win2.save_settings() is True
            cm2_reload = Config(path=tmp_path / "p2_settings_active.json")
            assert cm2_reload.get("island.edge") == "right"
            assert float(cm2_reload.get("island.pos")) == 0.0
        finally:
            win2.close()
            win2.deleteLater()

        # ---------------- 2) 左右侧 36×136 竖版音乐视图：与时间天气等长无跳位、无灰底/悬停反馈、100%/150% 渲染、点击与覆盖恢复 ----------------
        cover_img = QtGui.QImage(64, 64, QtGui.QImage.Format.Format_ARGB32)
        cover_img.fill(QtGui.QColor("#5ae0ff"))
        isl0.compact.set_cover(cover_img)
        isl0.compact.weather = ("\u2600", "18°", None, "01d")
        isl0.compact.show_weather = True
        isl0.compact.set_media({"has_session": True, "playing": True, "title": "夜に駆ける", "artist": "YOASOBI"})
        isl0.compact.badges = {}

        play_toggled = {"n": 0}
        isl0.play_pause_requested.connect(lambda: play_toggled.__setitem__("n", play_toggled["n"] + 1))

        for edge in ("left", "right"):
            # 音乐态与时间+天气态等长（36×136），中段与底角切换均不跳位
            for pos_f in (0.5, 0.98):
                cfg.set("island.edge", edge, save=False)
                cfg.set("island.pos", pos_f, save=False)
                isl0.compact.set_view("clock")
                isl0.apply_config()
                assert isl0.target_size() == (36.0, 136.0)
                clk_gy = isl0._win_origin.y() + isl0.body.y()
                clk_gh = isl0.body.height()

                isl0.compact.set_view("music")
                isl0.apply_config()
                assert isl0.compact.layout_state() == ("vertical", "media")
                assert isl0.target_size() == (36.0, 136.0)
                assert abs(isl0.body.width() - 36.0) < 0.05
                mus_gy = isl0._win_origin.y() + isl0.body.y()
                mus_gh = isl0.body.height()
                assert abs(mus_gy - clk_gy) < 0.05
                assert abs(mus_gh - clk_gh) < 0.05

            cfg.set("island.pos", 0.5, save=False)
            isl0.compact.set_view("music")
            isl0.apply_config()
            isl0.compact._mode_t = 0.0

            from wisland.ui.theme import theme as island_theme
            orig_theme_mode = island_theme.mode
            try:
                island_theme.set_mode("dark")
                for dpr in (1.0, 1.5):
                    for playing in (True, False):
                        isl0.compact.set_media({"has_session": True, "playing": playing, "title": "夜に駆ける", "artist": "YOASOBI"})
                        isl0.compact._mode_t = 0.0
                        # 常态：无灰色圆底，圆环边缘像素与纯岛体底色一致
                        isl0.compact.play_hover = 0.0
                        img_normal = _render_vertical_compact_image(isl0.compact, 36.0, 136.0, dpr=dpr)
                        assert img_normal.width() == round(36.0 * dpr)
                        assert img_normal.height() == round(136.0 * dpr)
                        pr_v = isl0.compact.play_rect
                        assert pr_v is not None
                        assert abs(pr_v.center().x() - 18.0) < 0.5
                        assert abs(pr_v.width() - 24.0) < 0.5
                        assert pr_v.top() >= 0.0 and pr_v.bottom() <= 136.0
                        rim_x = round((pr_v.left() + 3.0) * dpr)
                        rim_y = round(pr_v.center().y() * dpr)
                        top_x = round(pr_v.center().x() * dpr)
                        top_y = round((pr_v.top() + 3.0) * dpr)
                        # 取按钮热区外侧同高度的纯岛体底色作为基准
                        bg_x = round(2.5 * dpr)
                        bg_c = img_normal.pixelColor(bg_x, rim_y)
                        assert img_normal.pixelColor(rim_x, rim_y) == bg_c
                        assert img_normal.pixelColor(top_x, top_y) == bg_c

                        # 悬停态：显示圆形悬停反馈（颜色与纯底色明显区分）
                        isl0.compact.play_hover = 1.0
                        img_hover = _render_vertical_compact_image(isl0.compact, 36.0, 136.0, dpr=dpr)
                        assert img_hover.pixelColor(rim_x, rim_y) != bg_c
                        assert img_hover.pixelColor(top_x, top_y) != bg_c

                        # 移开鼠标恢复透明（与纯底色完全一致）
                        isl0.compact.play_hover = 0.0
                        img_restore = _render_vertical_compact_image(isl0.compact, 36.0, 136.0, dpr=dpr)
                        assert img_restore.pixelColor(rim_x, rim_y) == bg_c
                        assert img_restore.pixelColor(top_x, top_y) == bg_c

                        # 校验加粗后的播放/暂停矢量图标：清晰可辨、暂停双线间隙分明、播放三角中空轮廓清晰
                        ic_cx = round(pr_v.center().x() * dpr)
                        ic_cy = round(pr_v.center().y() * dpr)
                        if playing:
                            # 暂停双线正中间隙应为纯底色，左右两线中心应为高亮度前景色
                            assert img_normal.pixelColor(ic_cx, ic_cy) == bg_c
                            bar_l = img_normal.pixelColor(round((pr_v.center().x() - 3.0) * dpr), ic_cy)
                            bar_r = img_normal.pixelColor(round((pr_v.center().x() + 3.0) * dpr), ic_cy)
                            assert bar_l.red() > 200 and bar_r.red() > 200
                        else:
                            # 播放三角内部空心点为纯底色，左侧竖边为高亮度前景色
                            assert img_normal.pixelColor(ic_cx, ic_cy) == bg_c
                            lx_px = int(math.floor((pr_v.center().x() - 3.4) * dpr))
                            edge_l_max = max(img_normal.pixelColor(x_i, ic_cy).red() for x_i in (lx_px, lx_px + 1))
                            assert edge_l_max > 200

                    # 播放与暂停两种状态视觉墨量接近（比值在 0.85~1.15 之间）
                    weights = []
                    for pl_state in (False, True):
                        im_w = QtGui.QImage(round(36.0 * dpr), round(136.0 * dpr), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                        im_w.setDevicePixelRatio(dpr)
                        im_w.fill(QtCore.Qt.GlobalColor.transparent)
                        pw_p = QtGui.QPainter(im_w)
                        isl0.compact._paint_vertical_play_pause_icon(pw_p, pr_v, 1.0, pl_state, 1.0)
                        pw_p.end()
                        tot_a = 0
                        for py_i in range(round(pr_v.top() * dpr), round(pr_v.bottom() * dpr)):
                            for px_i in range(round(pr_v.left() * dpr), round(pr_v.right() * dpr)):
                                tot_a += QtGui.qAlpha(im_w.pixel(px_i, py_i))
                        weights.append(tot_a / (dpr * dpr * 255.0))
                    assert weights[0] >= 35.0 and weights[1] >= 35.0
                    assert 0.85 <= (weights[0] / weights[1]) <= 1.15
            finally:
                island_theme.set_mode(orig_theme_mode)

            # 恢复 playing=True，在岛体坐标系刷新 play_rect 并真实点击播放/暂停按钮 -> 仅触发 play_pause_requested，不展开岛体
            isl0.compact.set_media({"has_session": True, "playing": True, "title": "夜に駆ける", "artist": "YOASOBI"})
            bw_m = max(40, int(math.ceil(isl0.body.right())) + 8)
            bh_m = max(150, int(math.ceil(isl0.body.bottom())) + 8)
            im_m = QtGui.QImage(bw_m, bh_m, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            im_m.fill(QtCore.Qt.GlobalColor.transparent)
            pm = QtGui.QPainter(im_m)
            isl0.compact.paint(pm, QtCore.QRectF(isl0.body), 1.0, 0.0)
            pm.end()
            pr = isl0.compact.play_rect
            assert pr is not None
            before_n = play_toggled["n"]
            pt_l = pr.center()
            pt_g = pt_l + QtCore.QPointF(isl0._win_origin)
            isl0.mousePressEvent(QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonPress, pt_l, pt_g,
                QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
            ))
            isl0.mouseReleaseEvent(QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonRelease, pt_l, pt_g,
                QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
            ))
            assert play_toggled["n"] == before_n + 1
            assert not isl0.expanded

            # 纵向音量条临时覆盖音乐态 -> 超时后回到 vertical/media
            isl0.activity({"type": "volume", "value": 0.6, "muted": False}, 1.5)
            assert isl0.compact.layout_state() == ("vertical", "activity")
            assert isl0.target_size() == (36.0, 136.0)
            isl0.compact.activity["until"] = time.monotonic() - 0.001
            assert isl0.compact.layout_state() == ("vertical", "media")

            # 通知横幅临时覆盖音乐态 -> 超时后回到 vertical/media
            isl0.activity({"type": "notify", "title": "微信", "sub": "测试覆盖音乐"}, 1.5)
            assert isl0.compact.layout_state() == ("horizontal", "activity")
            isl0.compact.activity["until"] = time.monotonic() - 0.001
            assert isl0.compact.layout_state() == ("vertical", "media")

        # 顶边和底边横向音乐布局保持原样
        for h_edge in ("top", "bottom"):
            cfg.set("island.edge", h_edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl0.apply_config()
            assert isl0.compact.layout_state() == ("horizontal", "media")
            hw_m, hh_m = isl0.target_size()
            assert hw_m > 120.0 and hh_m == 36.0

        # ---------------- 3) 左右侧中段与上下两端展开首页/邮件详情后收起到 36×92 / 36×136 / 36×148 逐帧连续性 ----------------
        isl0.compact.set_view("clock")
        R_c = isl0._avail()
        for edge in ("left", "right"):
            for pos_f in (0.02, 0.5, 0.98):
                for rest_cfg, expect_h in (
                    ((None, {}), 92.0),
                    ((("\u2600", "18°", None, "01d"), {}), 136.0),
                    ((("\u2600", "18°", None, "01d"), {"qq": 1, "wechat": 1, "mail": 1}), 148.0),
                ):
                    isl0.compact.weather, isl0.compact.badges = rest_cfg
                    cfg.set("island.edge", edge, save=False)
                    cfg.set("island.pos", pos_f, save=False)
                    isl0.apply_config()
                    cw_r, ch_r = isl0.target_size()
                    isl0.sw.snap(cw_r)
                    isl0.sh.snap(ch_r)
                    isl0._update_geometry(force=True)
                    assert abs(isl0.body.height() - expect_h) < 0.05

                    for page_key in ("home", "mail"):
                        isl0.expand(page_key)
                        for _ in range(120):
                            isl0._last = time.perf_counter() - (1.0 / 60.0)
                            isl0._tick()
                        assert isl0.expanded

                        # 从收起前一帧录到稳定，检查 36.5 宽度阈值及落定全程无单帧补位、无越界、无空白帧
                        g_prev = isl0.body.translated(QtCore.QPointF(isl0._win_origin))
                        prev_cy = g_prev.center().y()
                        prev_h = g_prev.height()
                        isl0.collapse()
                        assert not isl0.content.isVisible()
                        assert isl0._collapse_snap is not None
                        t_col0 = isl0._collapse_snap_t0
                        for f_i in range(140):
                            isl0._last = time.perf_counter() - (1.0 / 60.0)
                            isl0._tick()
                            # 收起前 0.15s 内，面板快照淡出与竖条接续两层不可同时淡空
                            if f_i < 10:
                                sim_prog = min(1.0, (f_i / 60.0) / 0.13)
                                snap_f = 0.0 if sim_prog >= 1.0 else (1.0 - sim_prog) ** 2
                                comp_v = max(float(isl0.compact_a.x), min(1.0, max(0.0, (sim_prog - 0.18) / 0.52)))
                                assert max(snap_f, comp_v) >= 0.35
                            gf = isl0.body.translated(QtCore.QPointF(isl0._win_origin))
                            # 屏幕边界约束
                            assert gf.top() >= R_c.top() + 19.0 - 1.5
                            assert gf.bottom() <= R_c.bottom() - 19.0 + 1.5
                            cur_cy = gf.center().y()
                            cur_h = gf.height()
                            d_cy = abs(cur_cy - prev_cy)
                            d_h = abs(cur_h - prev_h)
                            # 沿边补偿严格等于当前帧高度变化的一半（无额外单帧跳位）；在 36.5 宽度尾段及落定段 d_cy 远小于 8px
                            assert d_cy <= d_h * 0.5 + 0.5, f"extra jump: edge={edge} pos={pos_f} page={page_key} h={expect_h} f={f_i} w={isl0.body.width():.2f} d_cy={d_cy:.2f} d_h={d_h:.2f}"
                            if isl0.body.width() <= 45.0:
                                assert d_cy < 8.0, f"tail jump near 36.5: edge={edge} pos={pos_f} page={page_key} h={expect_h} f={f_i} w={isl0.body.width():.2f} d_cy={d_cy:.2f}"
                            prev_cy = cur_cy
                            prev_h = cur_h

                        g_end = isl0.body.translated(QtCore.QPointF(isl0._win_origin))
                        assert abs(g_end.width() - 36.0) < 0.05
                        assert abs(g_end.height() - expect_h) < 0.05
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.set("island.screen", orig_scr, save=False)
        cfg.save()
        isl0.close()


def test_09_notify_sub_no_flicker_volume_easing_and_top_equal_width(qt_app):
    """v1.0.9 后三项专项回归：
    1) P2 顶部与左右侧短微信通知（目标宽 190px）60Hz 弹出/回弹全程摘要出现后不闪动，长摘要省略且关闭热区一致；
    2) P2 横/纵向音量条共用显示值缓动：连续升降、半途快速反向无回跳、静音/解除静音及超时平稳回原位；
    3) P3 顶部音量条与同配置时间天气条（有天气 / 无天气 / 带未读点）等长，0%/100%/静音完整可见无重叠，侧边保持纵向。
    """
    from wisland.core.config import cfg
    from wisland.ui.island import Island
    from wisland.ui.theme import theme as island_theme

    class _DummyPage(QtWidgets.QWidget):
        key = "home"
        title = "首页"
        glyph = "H"
        island = None

        def preferred_panel_width(self):
            return 419

        def preferred_panel_height(self):
            return 176

        def preferred_height(self):
            return 140

        def on_show(self):
            pass

        def on_hide(self):
            pass

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    orig_theme = island_theme.mode
    isl = Island(pages=[_DummyPage()], peak_fn=lambda: 0.0)
    try:
        island_theme.set_mode("dark")
        isl.compact.weather = ("\u2600", "18°", None, "01d")
        isl.compact.show_weather = True
        isl.compact.badges = {}

        # ---------------- 1) P2 短微信通知 60Hz 弹出与回弹全程摘要不闪动 ----------------
        dismiss_hits = {"n": 0}
        isl.notify_banner_dismissed.connect(lambda: dismiss_hits.__setitem__("n", dismiss_hits["n"] + 1))
        for edge in ("top", "left", "right"):
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            cw0, ch0 = isl.target_size()
            isl.sw.snap(cw0)
            isl.sh.snap(ch0)
            isl._update_geometry(force=True)

            isl.activity({"type": "notify", "title": "微信", "sub": "有新消息", "svg_key": "wechat"}, 6.0)
            tw_n, _ = isl.target_size()
            assert abs(tw_n - 190.0) < 0.5

            seen_sub = False
            saw_rebound_below_190 = False
            saw_above_190 = False
            for _ in range(90):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                bw = float(isl.body.width())
                bh = float(isl.body.height())
                if bw >= 190.0:
                    saw_above_190 = True
                elif saw_above_190 and bw < 190.0:
                    saw_rebound_below_190 = True

                # 离屏渲染当帧横幅并检查摘要区域像素可见性
                im = QtGui.QImage(max(40, int(math.ceil(bw)) + 4), max(40, int(math.ceil(bh)) + 4), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                im.fill(QtCore.Qt.GlobalColor.transparent)
                p = QtGui.QPainter(im)
                isl.compact.paint(p, QtCore.QRectF(0.0, 0.0, bw, bh), 1.0, 0.0)
                p.end()

                if bw >= 150.0:
                    # 摘要区位于 x=82..(bw-40)
                    x0, x1 = 82, max(84, int(bw) - 40)
                    sub_ink = 0
                    for yy in range(4, int(bh) - 4):
                        for xx in range(x0, x1):
                            if QtGui.qAlpha(im.pixel(xx, yy)) > 60:
                                sub_ink += 1
                    if sub_ink > 15:
                        seen_sub = True
                    elif seen_sub:
                        raise AssertionError(f"notify sub flickered off on {edge} at bw={bw:.2f}")

            assert seen_sub, f"notify sub never appeared on {edge}"
            # 验证长摘要省略与关闭热区共用同一矩形且点击可关闭
            isl.activity({"type": "notify", "title": "微信", "sub": "这是一条非常长的测试微信消息摘要用于验证省略号与关闭按钮不重叠", "svg_key": "wechat"}, 6.0)
            for _ in range(50):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            im_l = QtGui.QImage(max(40, int(math.ceil(isl.body.right())) + 8), max(40, int(math.ceil(isl.body.bottom())) + 8), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            im_l.fill(QtCore.Qt.GlobalColor.transparent)
            pl = QtGui.QPainter(im_l)
            isl.compact.paint(pl, QtCore.QRectF(isl.body), 1.0, 0.0)
            pl.end()
            dr = isl.compact._notify_dismiss_rect
            assert dr is not None and dr.width() >= 20.0
            before_d = dismiss_hits["n"]
            pt_l = dr.center()
            pt_g = pt_l + QtCore.QPointF(isl._win_origin)
            isl.mousePressEvent(QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonPress, pt_l, pt_g,
                QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
            ))
            isl.mouseReleaseEvent(QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonRelease, pt_l, pt_g,
                QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
            ))
            assert dismiss_hits["n"] == before_d + 1
            isl.compact.clear_activity("notify")
            assert isl.compact.activity is None

        # ---------------- 2) P2 横纵向音量填充缓动：连续升降、快速反向无回跳、静音/解除静音 ----------------
        for edge in ("top", "left", "right"):
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            cw0, ch0 = isl.target_size()
            isl.sw.snap(cw0)
            isl.sh.snap(ch0)
            isl._update_geometry(force=True)
            g_before = isl.body.translated(QtCore.QPointF(isl._win_origin))

            # 首次弹出 0.2 -> 缓动到位
            isl.activity({"type": "volume", "value": 0.2, "muted": False}, 2.0)
            t_start = isl.compact._vol_anim_t0
            vals_inc = [isl.compact._current_volume_display(t_start + k * 0.02) for k in range(8)]
            assert all(vals_inc[i] <= vals_inc[i + 1] + 1e-6 for i in range(len(vals_inc) - 1))
            assert abs(vals_inc[-1] - 0.2) < 1e-4

            # 连续升高到 0.9，仅过 50ms（未到位）立即快速反向到 0.3
            t1 = t_start + 0.16
            isl.compact._set_volume_target({"type": "volume", "value": 0.9, "muted": False}, continuing=True, now=t1)
            mid_v = isl.compact._current_volume_display(t1 + 0.05)
            assert 0.35 < mid_v < 0.85

            t2 = t1 + 0.05
            isl.compact._set_volume_target({"type": "volume", "value": 0.3, "muted": False}, continuing=True, now=t2)
            assert abs(isl.compact._vol_from - mid_v) < 1e-4
            rev_vals = [isl.compact._current_volume_display(t2 + k * 0.02) for k in range(8)]
            # 反向期间严格单调下降，无回跳到 0.9 或 0.2
            assert all(rev_vals[i] >= rev_vals[i + 1] - 1e-6 for i in range(len(rev_vals) - 1))
            assert rev_vals[0] <= mid_v + 1e-6
            assert abs(rev_vals[-1] - 0.3) < 1e-4

            # 静音 -> 平滑归零；解除静音 -> 平滑回到 0.65
            t3 = t2 + 0.20
            isl.compact._set_volume_target({"type": "volume", "value": 0.65, "muted": True}, continuing=True, now=t3)
            assert abs(isl.compact._current_volume_display(t3 + 0.16) - 0.0) < 1e-4
            t4 = t3 + 0.20
            isl.compact._set_volume_target({"type": "volume", "value": 0.65, "muted": False}, continuing=True, now=t4)
            assert abs(isl.compact._current_volume_display(t4 + 0.16) - 0.65) < 1e-4

            # 超时后平稳回到原位
            isl.compact.activity["until"] = time.monotonic() - 0.001
            for _ in range(60):
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
            g_after = isl.body.translated(QtCore.QPointF(isl._win_origin))
            assert abs(g_after.x() - g_before.x()) < 0.2
            assert abs(g_after.y() - g_before.y()) < 0.2
            assert abs(g_after.width() - g_before.width()) < 0.2

        # ---------------- 3) P3 顶部音量条与时间天气条等长（有天气 / 无天气 / 带未读点） ----------------
        cfg.set("island.edge", "top", save=False)
        cfg.set("island.pos", 0.5, save=False)
        for wea, badges in (
            (("\u2600", "18°", None, "01d"), {}),
            (None, {}),
            (("\u2600", "18°", None, "01d"), {"qq": 1, "wechat": 1}),
            (None, {"mail": 1}),
        ):
            isl.compact.clear_activity()
            isl.compact.weather = wea
            isl.compact.show_weather = True
            isl.compact.badges = badges
            isl.apply_config()
            idle_w, _ = isl.target_size()
            isl.sw.snap(idle_w)
            isl._update_geometry(force=True)
            idle_body_w = float(isl.body.width())

            for val, muted in ((0.0, False), (1.0, False), (0.5, True)):
                isl.activity({"type": "volume", "value": val, "muted": muted}, 2.0)
                vol_w, _ = isl.target_size()
                assert abs(vol_w - idle_w) < 0.05, f"top volume width {vol_w} != idle width {idle_w}"
                for _ in range(40):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                assert abs(float(isl.body.width()) - idle_body_w) < 0.2

                # 离屏渲染验证无天气/有天气下 0%、100%、静音均可完整绘制且轨道可见
                im_v = QtGui.QImage(int(math.ceil(vol_w)), 40, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                im_v.fill(QtCore.Qt.GlobalColor.transparent)
                pv = QtGui.QPainter(im_v)
                isl.compact.paint(pv, QtCore.QRectF(0.0, 0.0, vol_w, 40.0), 1.0, 0.0)
                pv.end()
                ink_left = sum(1 for y in range(6, 34) for x in range(4, 28) if QtGui.qAlpha(im_v.pixel(x, y)) > 40)
                ink_right = sum(1 for y in range(6, 34) for x in range(int(vol_w) - 38, int(vol_w) - 4) if QtGui.qAlpha(im_v.pixel(x, y)) > 40)
                assert ink_left > 10 and ink_right > 15

        # 侧边继续使用纵向音量条（宽 36）
        for s_edge in ("left", "right"):
            cfg.set("island.edge", s_edge, save=False)
            isl.apply_config()
            isl.activity({"type": "volume", "value": 0.5, "muted": False}, 2.0)
            assert isl.compact.layout_state() == ("vertical", "activity")
            assert isl.target_size()[0] == 36.0
    finally:
        island_theme.set_mode(orig_theme)
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.save()
        isl.close()


def test_10_first_rebound_corner_radius_and_neck_continuity(qt_app):
    """顶部、左侧、右侧各测极轻甩(5,100)、普通轻甩(8,200)、快甩(25,930)共 9 组，60fps 逐帧验收：
    - 首次碰撞挤压保留现有挤压形状；
    - 首次离边时靠边两角连续恢复，极轻甩（峰值约 0.68px）在峰值附近也恢复到 >= 80% 同帧另一侧圆角；
    - 液桥截面逐帧连续收窄，再次接触时逐帧连续放宽，相邻帧桥宽变化不超过满桥宽的 42%（无 214->54 / 158->42 骤缩）；
    - snap 最后一帧与 anchored 第一帧全局岛体矩形一致，无二次跳位。
    """
    from wisland.core.config import cfg
    from wisland.ui.island import Island

    class _DummyPage(QtWidgets.QWidget):
        key = "home"
        title = "首页"
        glyph = "H"
        island = None

        def preferred_panel_width(self):
            return 419

        def preferred_panel_height(self):
            return 176

        def preferred_height(self):
            return 140

        def on_show(self):
            pass

        def on_hide(self):
            pass

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    isl = Island(pages=[_DummyPage()], peak_fn=lambda: 0.0)
    try:
        isl.compact.weather = ("\u2600", "18°", None, "01d")
        isl.compact.show_weather = True
        isl.compact.badges = {}
        R = isl._avail()

        for edge in ("top", "left", "right"):
            for tag, start_gap, v_toward in (
                ("ultra_light", 5.0, 100.0),
                ("light", 8.0, 200.0),
                ("fast", 25.0, 930.0),
            ):
                cfg.set("island.edge", edge, save=False)
                cfg.set("island.pos", 0.5, save=False)
                isl.apply_config()
                cw, ch = isl.target_size()
                isl.sw.snap(cw)
                isl.sh.snap(ch)
                isl._update_geometry(force=True)
                full_len = cw if edge == "top" else ch

                if edge == "top":
                    cx, cy = R.center().x(), R.top() + start_gap + ch / 2.0
                    vx, vy = 0.0, -v_toward
                elif edge == "left":
                    cx, cy = R.left() + start_gap + cw / 2.0, R.center().y()
                    vx, vy = -v_toward, 0.0
                else:
                    cx, cy = R.right() - start_gap - cw / 2.0, R.center().y()
                    vx, vy = v_toward, 0.0

                isl.begin_drag(QtCore.QPoint(round(cx), round(cy)))
                isl.fx.snap(cx)
                isl.fy.snap(cy)
                isl.fx.v = vx
                isl.fy.v = vy
                isl._end_drag(QtCore.QPoint(round(cx), round(cy)))
                assert isl.mode == "snap" and isl.edge == edge

                def _path_w_at_012(ed: str) -> float:
                    pth = isl.path
                    br = pth.boundingRect()
                    ln = isl._line(ed)
                    wx0, wy0 = isl._win_origin.x(), isl._win_origin.y()
                    st = 0.5
                    if ed == "top":
                        yy = (ln - wy0) + 0.12
                        xs = []
                        xx = br.left() - 2.0
                        end_x = br.right() + 2.0
                        while xx <= end_x:
                            if pth.contains(QtCore.QPointF(xx, yy)):
                                xs.append(xx)
                            xx += st
                        return (xs[-1] - xs[0]) if len(xs) >= 2 else 0.0
                    xx = (ln - wx0) + (0.12 if ed == "left" else -0.12)
                    ys = []
                    yy = br.top() - 2.0
                    end_y = br.bottom() + 2.0
                    while yy <= end_y:
                        if pth.contains(QtCore.QPointF(xx, yy)):
                            ys.append(yy)
                        yy += st
                    return (ys[-1] - ys[0]) if len(ys) >= 2 else 0.0

                recs = []
                for _ in range(180):
                    isl._last = time.perf_counter() - (1.0 / 60.0)
                    isl._tick()
                    g_rect = isl.body.translated(QtCore.QPointF(isl._win_origin))
                    pw012 = _path_w_at_012(edge)
                    recs.append((
                        isl.mode,
                        float(isl.gap.x),
                        float(isl.gap.v),
                        float(isl._last_corner_r),
                        float(isl._last_base_r),
                        float(isl._last_neck_w),
                        g_rect,
                        pw012,
                    ))
                    if isl.mode == "anchored":
                        break

                assert recs[-1][0] == "anchored", f"{edge}/{tag} did not settle"
                assert isl.edge == edge

                # 1) 确认录到首次碰撞挤压与随后离边
                hit_idx = next((i for i, r in enumerate(recs) if r[1] < -0.5), None)
                assert hit_idx is not None, f"{edge}/{tag} no initial collision squash"
                min_gap = min(r[1] for r in recs)
                if tag == "fast":
                    assert min_gap < -3.0, f"{edge}/fast squash too weak: {min_gap}"

                reb_start = next((i for i in range(hit_idx + 1, len(recs)) if recs[i][1] > 0.05), None)
                assert reb_start is not None, f"{edge}/{tag} no rebound after squash"
                reb_end = next((i for i in range(reb_start + 1, len(recs)) if recs[i][1] <= 0.0), len(recs) - 1)
                reb_slice = recs[reb_start:reb_end]
                assert len(reb_slice) >= 3

                # 2) 圆角恢复：极轻甩峰值附近 >= 0.80 * base_r，普通轻甩与快甩 >= 0.90 * base_r
                peak_ratio = max(r[3] / max(1.0, r[4]) for r in reb_slice)
                min_want_ratio = 0.80 if tag == "ultra_light" else 0.90
                assert peak_ratio >= min_want_ratio, f"{edge}/{tag} corner_r peak_ratio={peak_ratio:.2f} < {min_want_ratio}"

                # 3) 实际 QPainterPath 在距屏边 0.12px 处截面宽度连续性：首次离边无骤缩、再次接触无宽差尖峰、无断桥重现
                dep_drop_pct = abs(recs[reb_start][7] - recs[reb_start - 1][7]) / max(1.0, recs[reb_start - 1][7])
                assert dep_drop_pct < 0.10, f"{edge}/{tag} 0.12px path drop too sharp on departure: {dep_drop_pct:.1%}"
                recontact_win = [recs[i][7] - recs[i - 1][7] for i in range(max(reb_start + 1, reb_end - 2), min(len(recs), reb_end + 4))]
                assert max(recontact_win) < 12.0, f"{edge}/{tag} 0.12px path spike on recontact: {recontact_win}"
                assert not any(recs[i][7] == 0.0 and recs[i + 1][7] > 0.0 for i in range(hit_idx, len(recs) - 1))

                # 辅助回归：_last_neck_w 相邻帧变化不超过满桥宽的 42%
                max_d_neck = 0.0
                for i in range(hit_idx + 1, len(recs)):
                    if recs[i][5] > 0.0 and recs[i - 1][5] > 0.0:
                        dn = abs(recs[i][5] - recs[i - 1][5])
                        if dn > max_d_neck:
                            max_d_neck = dn
                assert max_d_neck <= full_len * 0.42, (
                    f"{edge}/{tag} sudden neck width jump: max_d_neck={max_d_neck:.1f} > 42% of {full_len:.1f}"
                )

                # 4) 相邻两帧圆角无瞬跳
                for i in range(1, len(recs)):
                    dr = abs(recs[i][3] - recs[i - 1][3])
                    assert dr <= recs[i][4] * 0.55 + 0.5, f"{edge}/{tag} sudden corner jump at f={i}: dr={dr:.2f}"

                # 5) 最终落定：snap 最后一帧与 anchored 第一帧全局岛体矩形无二次跳位
                g_snap_last = recs[-2][6]
                g_anch_first = recs[-1][6]
                assert abs(g_snap_last.x() - g_anch_first.x()) < 0.6
                assert abs(g_snap_last.y() - g_anch_first.y()) < 0.6
                assert abs(g_snap_last.width() - g_anch_first.width()) < 0.6
                assert abs(g_snap_last.height() - g_anch_first.height()) < 0.6
                assert recs[-1][3] < 0.5
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.save()
        isl.close()


def test_11_snap_switches_idle_33ms_timer_to_16ms():
    """松手进入 snap 时，应将运行中的 33 ms 空闲定时器立即切到 16 ms 并重排；未运行时以 16 ms 启动；已在 16 ms 时保持原节奏；落定后恢复 33 ms。"""
    import time
    from PySide6 import QtCore, QtWidgets
    from wisland.core.config import cfg
    from wisland.ui.island import Island

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class _DummyPage(QtWidgets.QWidget):
        key = "home"
        title = "首页"
        glyph = "H"
        island = None

        def preferred_panel_width(self):
            return 419

        def preferred_panel_height(self):
            return 176

        def preferred_height(self):
            return 140

        def on_show(self):
            pass

        def on_hide(self):
            pass

    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    isl = Island(pages=[_DummyPage()], peak_fn=lambda: 0.0)
    try:
        cfg.set("island.edge", "top", save=False)
        cfg.set("island.pos", 0.5, save=False)
        isl.apply_config()
        cw, ch = isl.target_size()
        isl.sw.snap(cw)
        isl.sh.snap(ch)
        isl._update_geometry(force=True)

        # 1) 推进到收起空闲态：定时器运行且周期进入 33 ms
        isl._tick()
        assert isl.timer.isActive()
        assert isl.timer.interval() == 33

        # 模拟刚重排 33 ms 周期（remainingTime > 16），松手进入 snap 必须立刻切到 16 ms 并重排
        isl.timer.start(33)
        R = isl._avail()
        cx, cy = R.center().x(), R.top() + 25.0 + ch / 2.0
        isl.fx.snap(cx)
        isl.fy.snap(cy)
        isl.fx.v = 0.0
        isl.fy.v = -930.0
        isl.mode = "drag"
        isl._end_drag(QtCore.QPoint(round(cx), round(cy)))
        assert isl.mode == "snap"
        assert isl.timer.isActive()
        assert isl.timer.interval() == 16
        rem_after = isl.timer.remainingTime()
        assert 0 <= rem_after <= 16, f"expected remainingTime <= 16 after 33->16 switch, got {rem_after}"

        # 2) 已在 16 ms 运行时调用 _kick() 保持当前节奏（不重置剩余时间）
        time.sleep(0.006)
        rem_before_keep = isl.timer.remainingTime()
        isl._kick()
        rem_after_keep = isl.timer.remainingTime()
        assert isl.timer.interval() == 16
        assert rem_after_keep <= rem_before_keep + 1, (
            f"16 ms timer rhythm should not reset upward: before={rem_before_keep}, after={rem_after_keep}"
        )

        # 3) 定时器未运行时调用 _kick() 直接以 16 ms 启动
        isl.timer.stop()
        isl.timer.setInterval(33)
        isl._kick()
        assert isl.timer.isActive()
        assert isl.timer.interval() == 16
        assert 0 <= isl.timer.remainingTime() <= 16

        # 4) 回弹落定后继续按原有空闲规则回到 33 ms
        for _ in range(180):
            isl._last = time.perf_counter() - (1.0 / 60.0)
            isl._tick()
            if isl.mode == "anchored" and isl.sw.rest and isl.sh.rest:
                isl._last = time.perf_counter() - (1.0 / 60.0)
                isl._tick()
                break
        assert isl.mode == "anchored"
        assert isl.timer.isActive()
        assert isl.timer.interval() == 33
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.save()
        isl.close()
