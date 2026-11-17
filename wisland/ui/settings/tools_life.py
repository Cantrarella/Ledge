# -*- coding: utf-8 -*-
"""工具与生活设置页：仅展示当前已实现的真实功能（文件置物架与文字剪贴板历史）。"""
from __future__ import annotations

from PySide6 import QtWidgets

from ...core.config import ConfigDraft
from ..pages.tools import ClearClipboardDialog
from ..theme import G
from ..widgets import ConfirmDialog, PillButton, SettingRow, Switch
from .base import SettingsPage


class ToolsLifeSettingsPage(SettingsPage):
    key = "tools_life"
    title = "工具与生活"
    glyph = G.CLIP

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.sw_shelf = Switch()
        self.btn_clear_shelf = PillButton("清空置物架", G.TRASH, height=30, danger=True)
        self.btn_clear_shelf.clicked.connect(self._on_clear_shelf)

        self.sw_clip = Switch()
        self.btn_clear_clip = PillButton("清空剪贴板历史", G.TRASH, height=30, danger=True)
        self.btn_clear_clip.clicked.connect(self._on_clear_clipboard)

        self.finish_layout(
            self.section("文件置物架", "在展开态「工具 -> 文件」中暂存常用文件快捷入口，仅保存绝对路径，不复制文件内容"),
            SettingRow("开启文件置物架", "允许将本地文件拖入灵动岛或点击添加至置物架，支持点击打开、定位与拖出复制", self.sw_shelf),
            SettingRow("清空置物架", "移除置物架中的所有文件记录（不会删除磁盘上的原文件）", self.btn_clear_shelf),
            self.section("文字剪贴板历史", "基于系统剪贴板事件通知自动记录复制的纯文字，保存在本机 data/clipboard.json"),
            SettingRow("记录剪贴板历史", "开启后在后台监听系统复制的纯文字；关闭后立即停止剪贴板监听", self.sw_clip),
            SettingRow(
                "固定条目数与容量说明",
                "最近未固定文本最多保留 30 条；固定文本最多 20 条（固定条目不占最近 30 条名额），单条文本上限 20,000 字符",
            ),
            SettingRow(
                "敏感内容不记录说明",
                "自动识别 Windows 剪贴板隐私标记（ExcludeClipboardContentFromMonitorProcessing / Clipboard Viewer Ignore / CanIncludeInClipboardHistory=0），密码管理器等标记的敏感内容绝不记录",
            ),
            SettingRow("清空剪贴板历史", "清空本地保存的剪贴板历史记录（弹窗中可选择是否保留已固定条目）", self.btn_clear_clip),
        )

    def _on_clear_shelf(self):
        if not ConfirmDialog.ask(
            self.window(),
            "确认清空文件置物架？",
            "将清空置物架中的所有文件记录，不会删除原文件。",
            confirm_text="清空置物架",
        ):
            return
        if self.ctx and getattr(self.ctx, "shelf", None) is not None:
            self.ctx.shelf.clear()
        else:
            from ...services.shelf import ShelfService
            ShelfService().clear()

    def _on_clear_clipboard(self):
        clip = getattr(self.ctx, "clipboard", None) if self.ctx else None
        if clip is None:
            from ...services.clipboard_history import ClipboardHistoryService
            clip = ClipboardHistoryService()
        _, pinned_cnt = clip.counts()
        dlg = ClearClipboardDialog(pinned_count=pinned_cnt, parent=self.window())
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            clip.clear(include_pinned=dlg.include_pinned)

    def load_from_draft(self, draft: ConfigDraft):
        self.clear_errors()
        self.sw_shelf.setChecked(bool(draft.get("tools.shelf_enabled", True)))
        self.sw_clip.setChecked(bool(draft.get("tools.clipboard_enabled", True)))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("tools.shelf_enabled", self.sw_shelf.isChecked())
        draft.set("tools.clipboard_enabled", self.sw_clip.isChecked())
