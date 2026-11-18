# -*- coding: utf-8 -*-
"""字幕设置页：实时字幕音频源、设备名、双语翻译开关、DeepSeek/OpenAI 兼容接口配置与 VAD 参数。"""
from __future__ import annotations

from ...core.config import ConfigDraft
from ..theme import G
from ..widgets import ComboBox, DoubleSpinBox, LineEdit, PasswordEdit, SettingRow, SpinBox, Switch
from .base import SettingsPage


class SubtitlesSettingsPage(SettingsPage):
    key = "subtitles"
    title = "字幕"
    glyph = G.LIVE

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.cb_source = ComboBox([
            ("loopback", "系统声音内录 (Loopback)"),
            ("mic", "麦克风输入 (Mic)"),
        ], width=196)
        self.ed_device = LineEdit("留空使用系统默认设备", width=196)

        self.sw_tr_enabled = Switch()
        self.sw_tr_live = Switch()

        self.ed_base_url = LineEdit("https://api.deepseek.com", width=196)
        self.ed_model = LineEdit("deepseek-chat / deepseek-flash", width=196)
        self.ed_api_key = PasswordEdit("粘贴 API Key（可选）", width=196)

        self.sp_vad_th = DoubleSpinBox(0.05, 0.95, 0.05, decimals=2, width=196)
        self.sp_max_seg = SpinBox(2, 60, 1, suffix=" 秒", width=196)

        self.finish_layout(
            self.section("音频采集与语音检测", "配置实时字幕监听的声音来源与断句灵敏度（重启字幕后生效）"),
            SettingRow("声音采集来源", "选择监听电脑正在播放的系统音频（内录）或麦克风输入", self.cb_source),
            SettingRow("指定音频设备名", "可填写声卡或耳机关键词筛选设备；留空则自动使用默认输出/输入设备", self.ed_device),
            SettingRow("VAD 语音检测阈值", "范围 0.05~0.95，数值越小越容易触发语音识别，默认 0.40", self.sp_vad_th),
            SettingRow("最长单句切分时长", "连续说话不间断时强制切分句子的最大秒数", self.sp_max_seg),
            self.section("双语字幕与大模型翻译", "开启后将识别到的日文原文字幕实时翻译为中文并双语对照显示"),
            SettingRow("显示中文译文", "在实时字幕中同时显示中文译文", self.sw_tr_enabled),
            SettingRow("边说边译（流式草稿）", "在说话未结束前提前预测并刷新中文草稿译文", self.sw_tr_live),
            SettingRow("接口地址 (Base URL)", "兼容 OpenAI / DeepSeek 格式的 API 根地址", self.ed_base_url),
            SettingRow("模型名称 (Model)", "用于实时字幕翻译的模型名称（如 deepseek-chat 或 deepseek-flash）", self.ed_model),
            SettingRow("接口密钥 (API Key)", "用于调用翻译接口的密钥（保存在本地 config.json）", self.ed_api_key),
        )

    def load_from_draft(self, draft: ConfigDraft):
        self.clear_errors()
        self.cb_source.set_value(draft.get("live.audio.source", "loopback"))
        self.ed_device.setText(str(draft.get("live.audio.device", "") or ""))
        self.sp_vad_th.setValue(float(draft.get("live.vad.threshold", 0.4) or 0.4))
        self.sp_max_seg.setValue(int(draft.get("live.vad.max_segment_s", 10) or 10))
        self.sw_tr_enabled.setChecked(bool(draft.get("live.translate.enabled", True)))
        self.sw_tr_live.setChecked(bool(draft.get("live.translate.live", True)))
        self.ed_base_url.setText(str(draft.get("translate.base_url", "https://api.deepseek.com") or ""))
        self.ed_model.setText(str(draft.get("translate.model", "deepseek-flash") or ""))
        self.ed_api_key.setText(str(draft.get("translate.api_key", "") or ""))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("live.audio.source", self.cb_source.current_value() or "loopback")
        draft.set("live.audio.device", self.ed_device.text().strip())
        draft.set("live.vad.threshold", round(float(self.sp_vad_th.value()), 2))
        max_s = int(self.sp_max_seg.value())
        draft.set("live.vad.max_segment_s", max_s)
        draft.set("live.asr.max_segment_s", max_s)
        draft.set("live.translate.enabled", self.sw_tr_enabled.isChecked())
        draft.set("live.translate.live", self.sw_tr_live.isChecked())
        draft.set("translate.base_url", self.ed_base_url.text().strip() or "https://api.deepseek.com")
        draft.set("translate.model", self.ed_model.text().strip() or "deepseek-flash")
        draft.set("translate.api_key", self.ed_api_key.text().strip())
