# -*- coding: utf-8 -*-
"""首页（HomePage）419×140 紧凑双栏布局、媒体区与小形象单元测试。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from wisland.ui.pages import home as home_mod
from wisland.ui.pages.home import HomePage
from wisland.ui.theme import G, island_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


class _MockSignals(QtCore.QObject):
    changed = QtCore.Signal(object)


def _make_home(qt_app) -> tuple[HomePage, dict]:
    sig = _MockSignals()
    calls = {"prev": 0, "next": 0, "play_pause": 0, "seek": []}
    media_svc = SimpleNamespace(
        changed=sig.changed,
        prev=lambda: calls.__setitem__("prev", calls["prev"] + 1),
        next=lambda: calls.__setitem__("next", calls["next"] + 1),
        play_pause=lambda: calls.__setitem__("play_pause", calls["play_pause"] + 1),
        seek=lambda s: calls["seek"].append(s),
    )
    ctx = SimpleNamespace(
        media=media_svc,
        open_settings=lambda *a, **k: None,
    )
    pg = HomePage(ctx)
    pg.resize(419, 140)
    pg.show()
    qt_app.processEvents()
    return pg, calls


def test_01_dimensions_and_safe_bounds(qt_app):
    pg, _ = _make_home(qt_app)
    try:
        assert pg.preferred_panel_width() == 419
        assert pg.preferred_panel_height() == 140
        assert pg.preferred_height() == 98
        assert pg.width() == 419
        assert pg.size().height() == 140
        assert pg.findChildren(QtWidgets.QScrollArea) == []

        page_rect = pg.rect()
        for child in pg.findChildren(QtWidgets.QWidget):
            if child.isVisible():
                top_left = child.mapTo(pg, QtCore.QPoint(0, 0))
                crect = QtCore.QRect(top_left, child.size())
                assert page_rect.contains(crect), f"控件越界: {child} {crect}"
    finally:
        pg.close()


def test_02_header_centered_clock_no_left_title_and_gear(qt_app):
    pg, _ = _make_home(qt_app)
    try:
        # 页头左侧无“主页”装饰性标签
        left_labels = [
            lbl.text()
            for lbl in pg.header.findChildren(QtWidgets.QLabel)
            if lbl is not pg.header.center_lbl
        ]
        assert "主页" not in left_labels
        assert pg.header.left_lay.count() == 0

        # 日期时间严格水平居中，齿轮位于右侧
        pg.resize(419, 140)
        qt_app.processEvents()
        clk_center = pg.header.center_lbl.geometry().center().x()
        assert abs(clk_center - pg.header.width() / 2.0) <= 2.0
        assert pg.header.gear_btn is not None
        assert pg.header.gear_btn.isVisible()
        assert pg.header.gear_btn.mapTo(pg.header, QtCore.QPoint(0, 0)).x() > clk_center

        # 调整尺寸后时钟仍保持居中
        pg.resize(500, 140)
        qt_app.processEvents()
        assert abs(pg.header.center_lbl.geometry().center().x() - pg.header.width() / 2.0) <= 2.0
    finally:
        pg.close()


def test_03_quick_actions_removed_and_set_muted_kept(qt_app):
    pg, _ = _make_home(qt_app)
    try:
        assert not hasattr(home_mod, "Action")
        for attr in ("a_pin", "a_pt", "a_mute", "a_live", "_toggle_live"):
            assert not hasattr(pg, attr)
        assert pg.findChildren(QtWidgets.QGridLayout) == []

        # set_muted() 保留以兼容 app.py 音量同步
        pg.set_muted(True)
        pg.set_muted(False)
    finally:
        pg.close()


def test_04_media_card_states_controls_and_bounds(qt_app):
    pg, calls = _make_home(qt_app)
    try:
        m = pg.media
        assert m.height() <= 70
        assert m.width() >= 210

        assert not hasattr(m, "artist")

        # 1) 未播放状态（占位音符封面也使用 48×48）
        m.set_media({"has_session": False, "title": "", "artist": "", "playing": False, "position": 0, "duration": 0})
        qt_app.processEvents()
        assert m.cover.geometry().size() == QtCore.QSize(48, 48)
        assert m.cover.pm is not None
        assert m.cover.pm.deviceIndependentSize().toSize() == QtCore.QSize(48, 48)
        assert m.cover.cursor().shape() != QtCore.Qt.CursorShape.PointingHandCursor
        assert not m.cover.toolTip()
        assert m.title._text == "未在播放"
        assert m.play.glyph == G.PLAY
        assert not m.prog.isEnabled()

        # 2) 单行连续双色文本各类组合验收（含超长/空歌手/空标题/中英文混合）
        cover_img = QtGui.QImage(96, 96, QtGui.QImage.Format.Format_ARGB32)
        cover_img.fill(QtGui.QColor("#3b82f6"))
        m.set_cover(cover_img)
        samples = [
            ("心愿吟", "鸣潮先约电台", True),
            ("晴天", "周杰伦", True),
            ("A", "很长的歌手名称测试非常长超宽歌手", True),
            ("这是一个非常长的歌曲标题用来测试标题省略与歌手协同展示", "歌手", True),
            ("这是一个非常长的歌曲标题用来测试极限宽度下标题至少占六成", "同样非常长的歌手名称用来测试截断与隐藏阈值", True),
            ("短标题", "", False),
            ("", "周杰伦", True),
            ("Blinding Lights", "The Weeknd", True),
            ("夜に駆ける", "YOASOBI feat. 某中文混合", True),
        ]
        for s_title, s_artist, expect_sep in samples:
            m.set_media({
                "has_session": True,
                "title": s_title,
                "artist": s_artist,
                "playing": True,
                "position": 2500.0,
                "duration": 5000.0,
            })
            qt_app.processEvents()
            t_drawn, s_drawn, tw, sw = m.title._compute_segments(m.title.width())
            assert tw + sw <= m.title.width() + 0.5
            if not s_artist:
                assert s_drawn == ""
                assert " · " not in m.title._text
            elif expect_sep:
                assert " · " in m.title._text
            if not s_title:
                assert m.title.title_text == "未在播放"

        # 检查封面 48×48、单行 NowPlayingLine 与进度条约束
        cov_r = m.cover.geometry()
        now_r = m.title.geometry()
        prog_r = m.prog.geometry()
        assert cov_r.size() == QtCore.QSize(48, 48)
        assert now_r.left() >= cov_r.right() + 8
        assert now_r.right() <= m.rect().right()
        assert prog_r.left() >= cov_r.right() + 8
        assert not cov_r.intersects(now_r)
        assert not cov_r.intersects(prog_r)

        # 悬停进入/离开：封面尺寸与像素保持完全不变，同时进度条 hover 仍响应
        before_cov = m.cover.grab().toImage()
        m.enterEvent(QtGui.QEnterEvent(QtCore.QPointF(10, 10), QtCore.QPointF(10, 10), QtCore.QPointF(10, 10)))
        for _ in range(10):
            m._hover_tick()
        qt_app.processEvents()
        assert m.cover.geometry() == cov_r
        assert m.cover.grab().toImage() == before_cov
        assert m.prog.hover > 0.0

        m.leaveEvent(QtCore.QEvent(QtCore.QEvent.Type.Leave))
        for _ in range(30):
            m._hover_tick()
        qt_app.processEvents()
        assert m.cover.geometry() == cov_r
        assert m.cover.grab().toImage() == before_cov

        # 3) 空歌手无尾部圆点 + 0% / 100% 进度 + 暂停状态
        for pos in (0.0, 5000.0):
            m.set_media({
                "has_session": True,
                "title": "无歌手测试曲目",
                "artist": "",
                "playing": False,
                "position": pos,
                "duration": 5000.0,
            })
            qt_app.processEvents()
            assert m.title._text == "无歌手测试曲目"
            assert m.play.glyph == G.PLAY

        # 检查子控件互不重叠且都在 MediaCard 内
        mrect = m.rect()
        title_r = m.title.geometry()
        prog_r = m.prog.geometry()
        play_r = m.play.geometry()
        assert not title_r.intersects(prog_r)
        assert not prog_r.intersects(play_r)
        for btn in (m.prev, m.play, m.next):
            assert mrect.contains(btn.geometry())

        # 点击上一首、播放、下一首与进度条跳转
        m.prev.clicked.emit()
        m.play.clicked.emit()
        m.next.clicked.emit()
        assert calls["prev"] == 1
        assert calls["play_pause"] == 1
        assert calls["next"] == 1

        QtTest.QTest.mouseClick(
            m.prog,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.QPoint(int(m.prog.width() * 0.5), int(m.prog.height() * 0.5)),
        )
        assert len(calls["seek"]) == 1
        assert 1000.0 <= calls["seek"][0] <= 4000.0
    finally:
        pg.close()


def test_05_pet_widget_size_centering_and_eye_follow(qt_app):
    pg, _ = _make_home(qt_app)
    try:
        pet = pg.pet
        assert pet.width() == 126
        assert 1.05 <= pet.scale <= 1.2
        assert pg.media.geometry().right() < pet.geometry().left()

        # 模拟视角偏移，眼睛弹簧目标偏移应随视线更新
        pet.face._step(QtCore.QPointF(60.0, -20.0), False, pet.scale)
        assert pet.face.ex.target > 0
        assert pet.face.ey.target < 0
        pet.face._step(QtCore.QPointF(-60.0, 20.0), False, pet.scale)
        assert pet.face.ex.target < 0
        assert pet.face.ey.target > 0
    finally:
        pg.close()


def test_06_themes_and_2x_dpi_screenshots(qt_app, tmp_path):
    pg, _ = _make_home(qt_app)
    orig_mode = island_theme.mode
    shot_dir = Path(os.environ.get("WISLAND_HOME_SHOT_DIR") or (tmp_path / "home_shots"))
    shot_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 构造一张合成彩色封面图
        cover_img = QtGui.QImage(84, 84, QtGui.QImage.Format.Format_ARGB32)
        cover_img.fill(QtGui.QColor("#3b82f6"))

        for state_name, media_payload, with_cover in (
            ("idle", {"has_session": False, "title": "", "artist": "", "playing": False, "position": 0, "duration": 0}, False),
            ("playing", {"has_session": True, "title": "心愿吟", "artist": "鸣潮先约电台频道", "playing": True, "position": 85.0, "duration": 258.0}, True),
        ):
            pg.media.set_media(media_payload)
            pg.media.set_cover(cover_img if with_cover else None)
            for mode in ("light", "dark", "sky", "rouge", "classic"):
                island_theme.set_mode(mode)
                qt_app.processEvents()
                pix = QtGui.QPixmap(419, 140)
                if mode == "classic":
                    pix.fill(QtGui.QColor("#000000"))
                elif mode in ("dark", "rouge"):
                    pix.fill(QtGui.QColor("#14151a"))
                elif mode == "sky":
                    pix.fill(QtGui.QColor("#69b7f5"))
                else:
                    pix.fill(QtGui.QColor("#eef0f5"))
                pg.render(
                    pix,
                    QtCore.QPoint(),
                    QtGui.QRegion(),
                    QtWidgets.QWidget.RenderFlag.DrawChildren,
                )
                out = shot_dir / f"home-{state_name}-{mode}.png"
                assert pix.save(str(out))

        # 125% 与 200% DPI 渲染验证
        island_theme.set_mode("classic")
        qt_app.processEvents()
        for dpr, tag in ((1.25, "125"), (2.0, "2x")):
            pix_hi = QtGui.QPixmap(round(419 * dpr), round(140 * dpr))
            pix_hi.setDevicePixelRatio(dpr)
            pix_hi.fill(QtGui.QColor("#000000"))
            pg.render(
                pix_hi,
                QtCore.QPoint(),
                QtGui.QRegion(),
                QtWidgets.QWidget.RenderFlag.DrawChildren,
            )
            assert pix_hi.save(str(shot_dir / f"home-playing-classic-{tag}.png"))
    finally:
        island_theme.set_mode(orig_mode)
        pg.close()
