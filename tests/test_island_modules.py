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
