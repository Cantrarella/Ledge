# -*- coding: utf-8 -*-
"""Ledge 品牌更名与内部 wisland 技术兼容回归测试。"""
from __future__ import annotations

import uuid
import winreg
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6 import QtWidgets

from wisland.app import (
    AUTOSTART_NAME,
    LEGACY_AUTOSTART_NAME,
    _autostart_cmd,
    autostart_enabled,
    fix_autostart,
    populate_tray_menu,
    set_autostart,
)
from wisland.core.branding import (
    ABOUT_TITLE,
    APP_ID,
    DISPLAY_NAME,
    LEGACY_DISPLAY_NAME,
    OPEN_SETTINGS_TOOLTIP,
    SETTINGS_TITLE,
    STARTUP_ERROR_TITLE,
    TRAY_COLLAPSE,
    TRAY_EXPAND,
    TRAY_OPEN_SETTINGS,
    TRAY_QUIT,
    TRAY_TOOLTIP,
)
from wisland.core.config import Config
from wisland.services.clipboard_history import HISTORY_COPY_FORMAT
from wisland.services.mail.credentials import make_credential_key
from wisland.services.quota.credentials import make_quota_credential_key
from wisland.ui.header import PageHeader
from wisland.ui.island import Island
from wisland.ui.pages.base import Page
from wisland.ui.settings.about import AboutSettingsPage
from wisland.ui.settings.data import DataSettingsPage
from wisland.ui.settings.general import GeneralSettingsPage
from wisland.ui.settings_window import SettingsWindow
from wisland.ui.theme import G
from wisland.ui.widgets import Label, SettingRow


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


class _DummyHomePage(Page):
    key, glyph, title, height, panel_width, panel_height = "home", G.HOME, "主页", 98, 419, 140


def test_01_branding_constants_and_ui_surfaces(qt_app, tmp_path):
    assert DISPLAY_NAME == "Ledge"
    assert LEGACY_DISPLAY_NAME == "Wisland"
    assert APP_ID == "wisland"
    assert SETTINGS_TITLE == "Ledge 设置"
    assert ABOUT_TITLE == "Ledge 灵动岛"
    assert TRAY_TOOLTIP == "Ledge"
    assert STARTUP_ERROR_TITLE == "Ledge 启动失败"

    # 主窗口标题
    ctx = SimpleNamespace(open_settings=lambda *a, **k: None)
    isl = Island([_DummyHomePage(ctx)])
    try:
        assert isl.windowTitle() == "Ledge"
    finally:
        isl.close()

    # 岛内页面页头齿轮 Tooltip
    hdr = PageHeader(ctx, show_gear=True)
    try:
        assert hdr.gear_btn is not None
        assert hdr.gear_btn.toolTip() == OPEN_SETTINGS_TOOLTIP == "打开 Ledge 设置"
    finally:
        hdr.close()

    # 设置窗口标题、左下角品牌、关于页、常规开机启动说明、数据重载说明
    cm = Config(path=tmp_path / "config.json")
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        assert win.windowTitle() == "Ledge 设置"
        labels_txt = [lbl._text for lbl in win.findChildren(Label)]
        assert "Ledge" in labels_txt
        assert "Wisland" not in labels_txt

        about_pg = next(pg for pg in win.leaf_pages if isinstance(pg, AboutSettingsPage))
        about_labels = [lbl._text for lbl in about_pg.findChildren(Label)]
        assert "Ledge 灵动岛" in about_labels
        assert all("Wisland" not in t for t in about_labels)

        gen_pg = next(pg for pg in win.leaf_pages if isinstance(pg, GeneralSettingsPage))
        gen_rows = [r for r in gen_pg.findChildren(SettingRow)]
        autostart_desc = next(r for r in gen_rows if r.title_lbl.text() == "开机启动").sub_lbl.text()
        assert "启动 Ledge" in autostart_desc
        assert "Wisland" not in autostart_desc

        data_pg = next(pg for pg in win.leaf_pages if isinstance(pg, DataSettingsPage))
        data_rows = [r for r in data_pg.findChildren(SettingRow)]
        reload_desc = next(r for r in data_rows if r.title_lbl.text() == "重新加载配置").sub_lbl.text()
        assert "正在运行的 Ledge" in reload_desc
        assert "Wisland" not in reload_desc
    finally:
        win.close()


def test_02_tray_menu_and_internal_protocol_compatibility():
    # 托盘文案全部为 Ledge
    isl = SimpleNamespace(
        expanded=False,
        toggle=lambda: None,
        passthrough=lambda: False,
        capture_visible=lambda: True,
        set_passthrough=lambda _on: None,
        set_capture_visible=lambda _on: None,
    )
    menu = QtWidgets.QMenu()
    populate_tray_menu(menu, isl, lambda: None, lambda: None)
    texts_collapsed = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert TRAY_EXPAND in texts_collapsed
    assert TRAY_OPEN_SETTINGS in texts_collapsed
    assert TRAY_QUIT in texts_collapsed
    assert all("Wisland" not in t for t in texts_collapsed)

    isl.expanded = True
    populate_tray_menu(menu, isl, lambda: None, lambda: None)
    assert menu.actions()[0].text() == TRAY_COLLAPSE == "收起 Ledge"

    # 内部协议与兼容标识不得改变
    assert make_credential_key("acc_1") == "Wisland/mail/acc_1"
    assert make_quota_credential_key("q_1") == "Wisland/quota/q_1"
    assert HISTORY_COPY_FORMAT == "Wisland.ClipboardHistoryCopy"

    # run.pyw / Ledge.vbs / Ledge.spec 存在性检查
    root = Path(__file__).resolve().parents[1]
    assert (root / "Ledge.vbs").is_file()
    assert (root / "wisland.vbs").is_file()
    assert (root / "Ledge.spec").is_file()
    assert (root / "build" / "version_info.txt").is_file()
    run_pyw = (root / "run.pyw").read_text(encoding="utf-8")
    assert "Ledge.exe" in run_pyw
    assert "Ledge 启动失败" in run_pyw
    assert "wisland.log" in run_pyw
    assert "WISLAND_CONSOLE" in run_pyw
    assert "WISLAND_RELAUNCHED" in run_pyw


def test_03_autostart_safe_migration_isolated_registry():
    """使用隔离测试注册表键验证 wisland -> Ledge 安全迁移、双项去重与关闭双清。"""
    subkey = rf"Software\WislandTestRun_{uuid.uuid4().hex[:10]}"
    try:
        # 1) 仅有旧 wisland 启动项 -> 迁移为 Ledge 并删除旧项
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            winreg.SetValueEx(k, LEGACY_AUTOSTART_NAME, 0, winreg.REG_SZ, '"C:\\old\\pythonw.exe" run.pyw')
        assert autostart_enabled(run_key=subkey) is True

        fix_autostart(run_key=subkey)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val_new, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            assert val_new == _autostart_cmd()
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(k, LEGACY_AUTOSTART_NAME)

        # 2) 新旧项同时存在 -> 保留 Ledge 并删除旧 wisland，防止开机启动两次
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, LEGACY_AUTOSTART_NAME, 0, winreg.REG_SZ, "legacy-dup")
        fix_autostart(run_key=subkey)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val_new, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            assert val_new == _autostart_cmd()
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(k, LEGACY_AUTOSTART_NAME)

        # 3) 关闭开机启动 -> 同时删除 Ledge 和 wisland
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, LEGACY_AUTOSTART_NAME, 0, winreg.REG_SZ, "legacy-rem")
        set_autostart(False, run_key=subkey)
        assert autostart_enabled(run_key=subkey) is False
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(k, AUTOSTART_NAME)
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(k, LEGACY_AUTOSTART_NAME)

        # 4) 开启开机启动 -> 只写 Ledge
        set_autostart(True, run_key=subkey)
        assert autostart_enabled(run_key=subkey) is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val_new, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            assert val_new == _autostart_cmd()
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(k, LEGACY_AUTOSTART_NAME)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except OSError:
            pass
