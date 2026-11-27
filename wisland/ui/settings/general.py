# -*- coding: utf-8 -*-
"""常规设置页：主题、开机启动、展开方式、可穿透、在截图中显示、全屏隐藏、热键。"""
from __future__ import annotations

from ...core.branding import DISPLAY_NAME
from ...core.config import ConfigDraft
from ..theme import G, ISLAND_THEME_NAMES, SETTINGS_THEME_NAMES, SPOTLIGHT_STYLE_NAMES
from ..widgets import ComboBox, LineEdit, SettingRow, SpinBox, Switch
from .base import SettingsPage


class GeneralSettingsPage(SettingsPage):
    key = "general"
    title = "常规"
    glyph = G.GEAR

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent, lazy=True)
        self._autostart_init = False

    def _build_ui(self):
        self.cb_theme = ComboBox(ISLAND_THEME_NAMES, width=196)
        self.cb_settings_theme = ComboBox(SETTINGS_THEME_NAMES, width=196)
        self.sw_spotlight = Switch()
        self.cb_spotlight_style = ComboBox(SPOTLIGHT_STYLE_NAMES, width=196)
        self.sp_spotlight_radius = SpinBox(80, 260, 5, suffix=" px", width=196)
        self.sw_autostart = Switch()
        self.cb_expand = ComboBox([
            ("hover", "悬停自动展开"),
            ("always", "常驻保持展开"),
            ("never", "长关（双击切换）"),
        ], width=196)
        self.sw_passthrough = Switch()
        self.sw_capture = Switch()
        self.sw_fullscreen = Switch()
        self.ed_hotkey = LineEdit("例如 Ctrl+Alt+I（留空禁用）", width=196)

        self.row_hotkey = self.register_row(
            "hotkeys.toggle",
            SettingRow("展开 / 收起热键", "支持 Ctrl / Alt / Shift / Win 加字母、数字或 F1~F12（如 Ctrl+Alt+I）", self.ed_hotkey),
        )

        self.finish_layout(
            self.section("外观与启动", "分别设置灵动岛皮肤、展开态聚光灯与设置窗口主题，以及开机自动运行行为"),
            self.register_row(
                "island.theme",
                SettingRow("岛体皮肤", "选择灵动岛本身的外观皮肤（含跟随背景自动与经典纯黑）", self.cb_theme),
            ),
            self.register_row(
                "island.spotlight_enabled",
                SettingRow("展开态聚光灯", "开启后在灵动岛展开态下跟随鼠标照亮面板与卡片边框", self.sw_spotlight),
            ),
            self.register_row(
                "island.spotlight_style",
                SettingRow("聚光效果类型", "选择展开态聚光灯的光效纹理样式（后续可扩展更多特效）", self.cb_spotlight_style),
            ),
            self.register_row(
                "island.spotlight_radius",
                SettingRow("聚光半径大小", "控制展开态聚光灯与科技纹理的照亮半径范围（80~260 px，默认 155 px）", self.sp_spotlight_radius),
            ),
            self.register_row(
                "settings_window.theme",
                SettingRow("设置窗口主题", "独立控制设置中心窗口本身的浅色或深色外观", self.cb_settings_theme),
            ),
            SettingRow("开机启动", f"登录 Windows 时自动在后台启动 {DISPLAY_NAME}", self.sw_autostart),
            self.section("交互与可见性", "控制灵动岛的展开触发、鼠标穿透、截图显示与全屏避让"),
            SettingRow("展开方式", "控制鼠标移入灵动岛时的展开策略（也可在灵动岛上双击快速切换长关）", self.cb_expand),
            SettingRow("可穿透", "开启后鼠标点击直接穿透给下方窗口，灵动岛仅响应右键菜单与收起态滚轮", self.sw_passthrough),
            SettingRow("在截图中显示", "开启后截图和录屏可拍到灵动岛；关闭则对截图工具完全隐身", self.sw_capture),
            SettingRow("全屏应用时自动隐藏", "当前台运行全屏游戏或全屏视频时自动隐藏灵动岛", self.sw_fullscreen),
            self.section("全局快捷键", "使用键盘组合键在任意窗口下快速展开或收起灵动岛"),
            self.row_hotkey,
        )

    def load_from_draft(self, draft: ConfigDraft):
        from ...app import autostart_enabled
        self.clear_errors()
        self.cb_theme.set_value(draft.get("island.theme", "auto"))
        self.sw_spotlight.setChecked(bool(draft.get("island.spotlight_enabled", True)))
        self.cb_spotlight_style.set_value(draft.get("island.spotlight_style", "constellation"))
        self.sp_spotlight_radius.setValue(int(draft.get("island.spotlight_radius", 155) or 155))
        self.cb_settings_theme.set_value(draft.get("settings_window.theme", "light"))
        self._autostart_init = autostart_enabled()
        self.sw_autostart.setChecked(self._autostart_init)
        self.cb_expand.set_value(draft.get("island.expand_mode", "hover"))
        self.sw_passthrough.setChecked(bool(draft.get("island.passthrough", False)))
        self.sw_capture.setChecked(not bool(draft.get("island.exclude_from_capture", False)))
        self.sw_fullscreen.setChecked(bool(draft.get("island.hide_fullscreen", False)))
        self.ed_hotkey.setText(str(draft.get("hotkeys.toggle", "Ctrl+Alt+I") or ""))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("island.theme", self.cb_theme.current_value() or "auto")
        draft.set("island.spotlight_enabled", self.sw_spotlight.isChecked())
        draft.set("island.spotlight_style", self.cb_spotlight_style.current_value() or "constellation")
        draft.set("island.spotlight_radius", int(self.sp_spotlight_radius.value()))
        draft.set("settings_window.theme", self.cb_settings_theme.current_value() or "light")
        emode = self.cb_expand.current_value() or "hover"
        draft.set("island.expand_mode", emode)
        draft.set("island.pinned", emode == "always")
        draft.set("island.passthrough", self.sw_passthrough.isChecked())
        draft.set("island.exclude_from_capture", not self.sw_capture.isChecked())
        draft.set("island.hide_fullscreen", self.sw_fullscreen.isChecked())
        draft.set("hotkeys.toggle", self.ed_hotkey.text().strip())

    def autostart_dirty(self) -> bool:
        return self.sw_autostart.isChecked() != self._autostart_init

    def apply_autostart_if_changed(self):
        from ...app import set_autostart
        want = self.sw_autostart.isChecked()
        if want != self._autostart_init:
            try:
                set_autostart(want)
                self._autostart_init = want
            except OSError:
                pass
