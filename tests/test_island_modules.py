# -*- coding: utf-8 -*-
"""灵动岛拆出子模块（win32_hooks / island_overlays）的单元测试与向后兼容校验。"""
from __future__ import annotations

from wisland.ui import island as island_mod
from wisland.ui.island_overlays import BlankDoubleClick, LifeReminderBanner, Veil
from wisland.ui.win32_hooks import MOD, MOD_NOREPEAT, RightClickHook, parse_hotkey


def test_parse_hotkey_variants_and_reexport():
    assert parse_hotkey("Ctrl+Alt+I") == (MOD["ctrl"] | MOD["alt"] | MOD_NOREPEAT, ord("I"))
    assert parse_hotkey("Shift+F10") == (MOD["shift"] | MOD_NOREPEAT, 0x70 + 9)
    assert parse_hotkey("Win+Space") == (MOD["win"] | MOD_NOREPEAT, 0x20)
    assert parse_hotkey("") is None
    assert parse_hotkey("Ctrl+Alt") is None

    assert island_mod.parse_hotkey is parse_hotkey
    assert island_mod._RightClickHook is RightClickHook
    assert island_mod._Veil is Veil
    assert island_mod._BlankDoubleClick is BlankDoubleClick
    assert island_mod._LifeReminderBanner is LifeReminderBanner


def test_life_reminder_banner_signals_and_close_hit():
    from PySide6 import QtCore, QtGui, QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    banner = LifeReminderBanner()
    banner.resize(395, 40)
    banner.set_reminder({"kind": "water", "title": "该喝水了", "sub": "今日 500/2000 ml"})

    clicked: list[str] = []
    dismissed: list[bool] = []
    banner.clicked_kind.connect(clicked.append)
    banner.dismissed.connect(lambda: dismissed.append(True))

    # 点击主体触发 clicked_kind
    ev_body = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QPointF(80.0, 20.0),
        QtCore.QPointF(80.0, 20.0),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    banner.mouseReleaseEvent(ev_body)
    assert clicked == ["water"]
    assert dismissed == []

    # 点击关闭按钮区域触发 dismissed
    cr = banner._close_rect().center()
    ev_close = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        cr,
        cr,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    banner.mouseReleaseEvent(ev_close)
    assert dismissed == [True]


def test_backdrop_thread_blocks_when_want_is_none(monkeypatch):
    from PySide6 import QtWidgets
    from wisland.ui.glass import Backdrop

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    bd = Backdrop()
    wait_timeouts = []

    def fake_wait(timeout=None):
        wait_timeouts.append(timeout)
        bd._stop = True
        return True

    monkeypatch.setattr(bd._wake, "wait", fake_wait)
    bd._run()
    assert wait_timeouts == [None]


def test_expanded_spotlight_and_constellation_all_skins():
    from PySide6 import QtCore, QtGui, QtWidgets
    from wisland.ui.island import Island
    from wisland.ui.pages.base import Page
    from wisland.ui.theme import PALETTES, theme
    from wisland.ui.widgets import Card

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    required_keys = (
        "spot_tech",
        "spot_pulse",
        "spot_alpha",
        "spot_card_supp",
        "spot_surface",
        "spot_card_fill",
        "spot_card_border",
        "spot_rim",
    )
    for skin_name, pal in PALETTES.items():
        for k in required_keys:
            assert k in pal, f"{skin_name} missing {k}"

    pg = Page()
    pg.key, pg.title, pg.panel_width, pg.panel_height = "home", "主页", 419, 140
    card = Card(pg, radius=14, alpha=1.0)
    card.setGeometry(12, 36, 395, 76)

    isl = Island([pg])
    try:
        isl.expanded = True
        w, h = isl.target_size()
        isl.sw.snap(w)
        isl.sh.snap(h)
        isl._update_geometry(force=True)
        isl._spot_pos = isl.body.center()
        isl._spot_target = isl.body.center()
        isl._spot_a.snap(1.0)

        old_mode = theme.mode
        try:
            for skin_name in PALETTES:
                theme.set_mode(skin_name)
                canvas = QtGui.QImage(isl.width(), isl.height(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                canvas.fill(QtCore.Qt.GlobalColor.transparent)
                isl.render(canvas)

                card_img = QtGui.QImage(card.width(), card.height(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                card_img.fill(QtCore.Qt.GlobalColor.transparent)
                p = QtGui.QPainter(card_img)
                isl.paint_card_spotlight(p, card, QtCore.QRectF(card.rect()), card.radius, theme)
                p.end()
        finally:
            theme.set_mode(old_mode)

        isl.collapse(force=True)
        assert isl._spot_a.x == 0.0
    finally:
        isl.close()


def test_spotlight_nested_card_skip_and_transition_sync():
    from PySide6 import QtCore, QtGui, QtWidgets
    from wisland.ui.island import Island
    from wisland.ui.pages.base import Page
    from wisland.ui.theme import theme
    from wisland.ui.widgets import Card

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    pg = Page()
    pg.key, pg.title, pg.panel_width, pg.panel_height = "tools", "工具", 419, 140
    outer_card = Card(pg, radius=14, alpha=1.0)
    outer_card.setGeometry(12, 36, 395, 76)
    inner_card = Card(outer_card, radius=10, alpha=0.55)
    inner_card.setGeometry(2, 1, 391, 74)

    isl = Island([pg])
    try:
        isl.expanded = True
        w, h = isl.target_size()
        isl.sw.snap(w)
        isl.sh.snap(h)
        isl._update_geometry(force=True)
        isl._spot_pos = isl.body.center()
        isl._spot_target = isl.body.center()
        isl._spot_a.snap(1.0)

        # 内容已显现但 content_a 为 0 时，有效聚光透明度应同步为 0（防展开初段闪烁）
        isl.content.show()
        isl.content_a.snap(0.0)
        assert isl._effective_spot_alpha() == 0.0
        isl.content_a.snap(1.0)
        assert isl._effective_spot_alpha() == 1.0

        # 嵌套在 outer_card 内的 inner_card 应跳过重复聚光描边
        inner_img = QtGui.QImage(inner_card.width(), inner_card.height(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        inner_img.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(inner_img)
        isl.paint_card_spotlight(p, inner_card, QtCore.QRectF(inner_card.rect()), inner_card.radius, theme)
        p.end()
        assert inner_img.pixelColor(0, 0).alpha() == 0
    finally:
        isl.close()


def test_spotlight_settings_config_and_general_page():
    from PySide6 import QtWidgets
    from wisland.core.config import ConfigDraft, cfg, validate_config
    from wisland.ui.settings.general import GeneralSettingsPage

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    ok, errs, merged = validate_config({
        "island": {
            "spotlight_enabled": False,
            "spotlight_style": "soft",
            "spotlight_radius": 180,
        }
    }, strict=True)
    assert ok, errs
    assert merged["island"]["spotlight_enabled"] is False
    assert merged["island"]["spotlight_style"] == "soft"
    assert merged["island"]["spotlight_radius"] == 180

    bad_ok, bad_errs, _ = validate_config({
        "island": {
            "spotlight_style": "invalid_style",
            "spotlight_radius": 500,
        }
    }, strict=True)
    assert not bad_ok
    assert len(bad_errs) >= 2

    draft = cfg.create_draft()
    pg = GeneralSettingsPage()
    pg.ensure_built()
    pg.load_from_draft(draft)
    assert pg.sw_spotlight.isChecked() is True
    assert pg.cb_spotlight_style.current_value() == "constellation"
    assert pg.sp_spotlight_radius.value() == 155

    pg.sw_spotlight.setChecked(False)
    pg.cb_spotlight_style.set_value("soft")
    pg.sp_spotlight_radius.setValue(140)
    pg.save_to_draft(draft)
    assert draft.get("island.spotlight_enabled") is False
    assert draft.get("island.spotlight_style") == "soft"
    assert draft.get("island.spotlight_radius") == 140
