# -*- coding: utf-8 -*-
"""岛体与表情设置页：停靠边、沿边位置、屏幕选择、收起态视图、轮换间隔、延迟、毛玻璃帧率与媒体联动。"""
from __future__ import annotations

from PySide6 import QtGui

from ...core.config import ConfigDraft
from ..theme import G
from ..widgets import ComboBox, SpinBox, SettingRow, Switch
from .base import SettingsPage


class IslandPetSettingsPage(SettingsPage):
    key = "island_pet"
    title = "岛体与表情"
    glyph = G.HOME

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.cb_edge = ComboBox([
            ("top", "顶部边缘"),
            ("bottom", "底部边缘"),
            ("left", "左侧边缘"),
            ("right", "右侧边缘"),
        ], width=196)
        self.sp_pos = SpinBox(0, 100, 5, suffix=" %", width=196)
        self.cb_screen = ComboBox(width=196)
        self._populate_screens()

        self.cb_compact = ComboBox([
            ("clock", "时间与天气"),
            ("pet", "灵动小表情"),
            ("music", "音乐播放"),
        ], width=196)
        self.sp_cycle = SpinBox(0, 3600, 5, suffix=" 秒", width=196)
        self.sp_hover_ms = SpinBox(0, 5000, 20, suffix=" ms", width=196)
        self.sp_collapse_ms = SpinBox(0, 10000, 50, suffix=" ms", width=196)
        self.sp_blur_fps = SpinBox(1, 60, 1, suffix=" FPS", width=196)

        self.sw_media_auto = Switch()
        self.sw_media_lyrics = Switch()
        self.sw_netease_log = Switch()

        self.finish_layout(
            self.section("停靠与位置", "调整灵动岛吸附的显示器、屏幕边缘及沿边偏移（也可直接按住灵动岛拖拽）"),
            SettingRow("停靠屏幕边缘", "灵动岛默认贴附的屏幕边缘方向", self.cb_edge),
            SettingRow("沿边位置比例", "50% 为正中，0% 靠近左/上端，100% 靠近右/下端", self.sp_pos),
            SettingRow("目标显示器", "选择灵动岛停靠所在的显示器屏幕", self.cb_screen),
            self.section("收起态与小表情", "设置收起胶囊显示的常驻视图与自动轮换节奏（收起态滚轮也可随时切换）"),
            SettingRow("收起态当前视图", "灵动岛收起时默认展示的内容样式", self.cb_compact),
            SettingRow("时间天气 / 小表情轮换间隔", "每隔指定秒数在时间天气与小表情间自动切换（设为 0 秒则不自动轮换）", self.sp_cycle),
            self.section("动效响应与毛玻璃", "岛体动效保持动态 60 FPS、空闲 30 FPS；此处可微调悬停延迟与背景模糊采样率"),
            SettingRow("悬停展开延迟", "鼠标移入收起态灵动岛后触发自动展开的等待毫秒数", self.sp_hover_ms),
            SettingRow("移出收起延迟", "鼠标离开展开态灵动岛后自动收起的缓冲毫秒数", self.sp_collapse_ms),
            SettingRow("毛玻璃背景采样率", "展开态身后画面模糊纹理的每秒更新次数（建议 6~15 FPS）", self.sp_blur_fps),
            self.section("音乐与歌词联动", "控制系统媒体播放及网易云音乐与收起态胶囊的联动行为"),
            SettingRow("播放时自动切到音乐视图", "检测到音乐播放时收起态自动显示封面、律动波形与歌词，暂停 8 秒后切回", self.sw_media_auto),
            SettingRow("收起态显示实时歌词", "音乐播放时在收起态胶囊右侧滚动显示当前同步歌词", self.sw_media_lyrics),
            SettingRow("读取网易云本地日志", "从网易云客户端本地日志获取精准播放状态与进度", self.sw_netease_log),
        )

    def _populate_screens(self):
        screens = QtGui.QGuiApplication.screens()
        items = []
        for idx, scr in enumerate(screens):
            g = scr.geometry()
            name = scr.name() or f"显示器 {idx + 1}"
            items.append((idx, f"{idx}: {name} ({g.width()}×{g.height()})"))
        if not items:
            items = [(0, "0: 主显示器")]
        self.cb_screen.set_items(items)

    def load_from_draft(self, draft: ConfigDraft):
        self._populate_screens()
        self.cb_edge.set_value(draft.get("island.edge", "top"))
        pos_frac = float(draft.get("island.pos", 0.5) or 0.5)
        self.sp_pos.setValue(round(pos_frac * 100))
        self.cb_screen.set_value(int(draft.get("island.screen", 0) or 0))
        self.cb_compact.set_value(draft.get("island.compact_view", "clock"))
        self.sp_cycle.setValue(int(draft.get("island.auto_cycle_s", 30) or 0))
        self.sp_hover_ms.setValue(int(draft.get("island.hover_expand_ms", 120) or 120))
        self.sp_collapse_ms.setValue(int(draft.get("island.collapse_delay_ms", 320) or 320))
        self.sp_blur_fps.setValue(int(float(draft.get("island.blur_fps", 8) or 8)))
        self.sw_media_auto.setChecked(bool(draft.get("media.auto_show", True)))
        self.sw_media_lyrics.setChecked(bool(draft.get("media.lyrics", True)))
        self.sw_netease_log.setChecked(bool(draft.get("media.netease_log", True)))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("island.edge", self.cb_edge.current_value() or "top")
        draft.set("island.pos", round(self.sp_pos.value() / 100.0, 4))
        scr_val = self.cb_screen.current_value()
        draft.set("island.screen", int(scr_val) if scr_val is not None else 0)
        draft.set("island.compact_view", self.cb_compact.current_value() or "clock")
        draft.set("island.auto_cycle_s", int(self.sp_cycle.value()))
        draft.set("island.hover_expand_ms", int(self.sp_hover_ms.value()))
        draft.set("island.collapse_delay_ms", int(self.sp_collapse_ms.value()))
        draft.set("island.blur_fps", int(self.sp_blur_fps.value()))
        draft.set("media.auto_show", self.sw_media_auto.isChecked())
        draft.set("media.lyrics", self.sw_media_lyrics.isChecked())
        draft.set("media.netease_log", self.sw_netease_log.isChecked())
