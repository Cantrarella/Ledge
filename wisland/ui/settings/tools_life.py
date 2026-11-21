# -*- coding: utf-8 -*-
"""工具与生活设置页：展示文件置物架、文字剪贴板历史以及生活（待办、喝水、久坐）设置项。"""
from __future__ import annotations

from PySide6 import QtWidgets

from ...core.config import ConfigDraft
from ..pages.tools import ClearClipboardDialog
from ..theme import G, W
from ..widgets import ConfirmDialog, Label, LineEdit, PillButton, SettingRow, SpinBox, Switch, hbox
from .base import SettingsPage


class ToolsLifeSettingsPage(SettingsPage):
    key = "tools_life"
    title = "工具与生活"
    glyph = G.CLIP

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent, lazy=True)

    def _build_ui(self):
        # ---- 工具：文件置物架与剪贴板 ----
        self.sw_shelf = Switch()
        self.btn_clear_shelf = PillButton("清空置物架", G.TRASH, height=30, danger=True)
        self.btn_clear_shelf.clicked.connect(self._on_clear_shelf)

        self.sw_clip = Switch()
        self.btn_clear_clip = PillButton("清空剪贴板历史", G.TRASH, height=30, danger=True)
        self.btn_clear_clip.clicked.connect(self._on_clear_clipboard)

        # ---- 生活：待办 ----
        self.btn_clear_todo_done = PillButton("清空已完成待办", G.CHECK, height=30)
        self.btn_clear_todo_done.clicked.connect(self._on_clear_todo_done)
        self.btn_clear_todo_all = PillButton("清空全部待办", G.TRASH, height=30, danger=True)
        self.btn_clear_todo_all.clicked.connect(self._on_clear_todo_all)
        todo_btns = QtWidgets.QWidget()
        todo_btns.setLayout(hbox(self.btn_clear_todo_done, self.btn_clear_todo_all, spacing=8))

        # ---- 生活：喝水提醒 ----
        self.sw_water_remind = Switch()
        self.sp_water_goal = SpinBox(500, 6000, 100, suffix=" ml")
        self.sp_water_serving = SpinBox(50, 1000, 50, suffix=" ml")
        self.sp_water_interval = SpinBox(5, 180, 5, suffix=" 分钟")

        self.sp_win_start = SpinBox(0, 23, 1, suffix=" 点", width=70)
        self.sp_win_end = SpinBox(1, 24, 1, suffix=" 点", width=70)
        win_wrap = QtWidgets.QWidget()
        win_wrap.setLayout(hbox(self.sp_win_start, Label("点", 12.5, W.Medium, 2), Label("–", 12.5, W.Normal, 3),
                                self.sp_win_end, Label("点", 12.5, W.Medium, 2), spacing=6, margins=(0, 0, 0, 0)))

        self.sw_lunch = Switch()
        self.ed_lunch_start = LineEdit("12:00", width=96)
        self.ed_lunch_end = LineEdit("13:30", width=96)
        lunch_wrap = QtWidgets.QWidget()
        lunch_wrap.setLayout(hbox(self.ed_lunch_start, self.ed_lunch_end, spacing=8))

        self.row_window = self.register_row(
            "life.remind_window",
            SettingRow("提醒时段（小时）", "仅在此时间段内发出喝水与久坐提醒（默认 09:00–22:00）", win_wrap),
        )
        self.row_lunch_time = self.register_row(
            "life.lunch_time",
            SettingRow("午休免打扰时段", "HH:MM 起止时间（默认 12:00–13:30），该时段内暂停喝水与久坐提醒", lunch_wrap),
        )

        # ---- 生活：久坐提醒 ----
        self.sw_sit_remind = Switch()
        self.sp_sit_interval = SpinBox(15, 180, 5, suffix=" 分钟")

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

            self.section("生活 · 待办", "所有待办事项仅保存在本机 data/todo.json，不上传云端"),
            SettingRow("本地数据管理", "可清理已完成待办，或清空全部待办事项（需二次确认）", todo_btns),

            self.section("生活 · 喝水与提醒时段", "喝水数据保存在本机 data/water.json，每日零点自动重置今日进度"),
            SettingRow("开启喝水提醒", "在提醒时段内按设定间隔提醒饮水；达到每日目标后自动停止当天提醒", self.sw_water_remind),
            SettingRow("每日饮水目标", "范围 500–6000 ml，默认 2000 ml", self.sp_water_goal),
            SettingRow("每次记录容量", "每次点击「喝一口」增加的毫升数（50–1000 ml，默认 250 ml）", self.sp_water_serving),
            SettingRow("喝水提醒间隔", "范围 5–180 分钟，默认 45 分钟", self.sp_water_interval),
            self.row_window,
            SettingRow("开启午休免打扰", "在午休时段内暂停喝水与久坐提醒", self.sw_lunch),
            self.row_lunch_time,

            self.section("生活 · 久坐提醒", "久坐计时在系统锁屏或点击「起身了」后自动重置"),
            SettingRow("开启久坐提醒", "连续久坐达到设定时长后发出起身活动提醒", self.sw_sit_remind),
            SettingRow("久坐提醒间隔", "范围 15–180 分钟，默认 60 分钟", self.sp_sit_interval),
            SettingRow("提醒时段与免打扰说明", "久坐提醒自动沿用上方的提醒时段与午休免打扰设置"),
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

    def _on_clear_todo_done(self):
        todo = getattr(self.ctx, "todo", None) if self.ctx else None
        if todo is None:
            from ...services.todo import TodoService
            todo = TodoService()
        cnt = todo.clear_completed()
        ConfirmDialog.info(self.window(), "清理完成", f"已清理 {cnt} 条已完成待办。" if cnt else "当前没有已完成待办。")

    def _on_clear_todo_all(self):
        if not ConfirmDialog.ask(
            self.window(),
            "确认清空全部待办？",
            "此操作将删除本地保存的所有待办事项（含重复待办），且不可恢复。",
            confirm_text="全部清空",
        ):
            return
        todo = getattr(self.ctx, "todo", None) if self.ctx else None
        if todo is None:
            from ...services.todo import TodoService
            todo = TodoService()
        todo.clear_all()

    def load_from_draft(self, draft: ConfigDraft):
        self.clear_errors()
        self.sw_shelf.setChecked(bool(draft.get("tools.shelf_enabled", True)))
        self.sw_clip.setChecked(bool(draft.get("tools.clipboard_enabled", True)))

        self.sw_water_remind.setChecked(bool(draft.get("life.water_remind", True)))
        self.sp_water_goal.setValue(int(draft.get("life.water_goal_ml", 2000) or 2000))
        self.sp_water_serving.setValue(int(draft.get("life.water_serving_ml", 250) or 250))
        self.sp_water_interval.setValue(int(draft.get("life.water_interval_min", 45) or 45))

        self.sp_win_start.setValue(int(draft.get("life.remind_start_hour", 9) or 9))
        self.sp_win_end.setValue(int(draft.get("life.remind_end_hour", 22) or 22))
        self.sw_lunch.setChecked(bool(draft.get("life.lunch_rest", True)))
        self.ed_lunch_start.setText(str(draft.get("life.lunch_start", "12:00") or "12:00"))
        self.ed_lunch_end.setText(str(draft.get("life.lunch_end", "13:30") or "13:30"))

        self.sw_sit_remind.setChecked(bool(draft.get("life.sit_remind", True)))
        self.sp_sit_interval.setValue(int(draft.get("life.sit_interval_min", 60) or 60))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("tools.shelf_enabled", self.sw_shelf.isChecked())
        draft.set("tools.clipboard_enabled", self.sw_clip.isChecked())

        draft.set("life.water_remind", self.sw_water_remind.isChecked())
        draft.set("life.water_goal_ml", int(self.sp_water_goal.value()))
        draft.set("life.water_serving_ml", int(self.sp_water_serving.value()))
        draft.set("life.water_interval_min", int(self.sp_water_interval.value()))

        draft.set("life.remind_start_hour", int(self.sp_win_start.value()))
        draft.set("life.remind_end_hour", int(self.sp_win_end.value()))
        draft.set("life.lunch_rest", self.sw_lunch.isChecked())
        draft.set("life.lunch_start", self.ed_lunch_start.text().strip() or "12:00")
        draft.set("life.lunch_end", self.ed_lunch_end.text().strip() or "13:30")

        draft.set("life.sit_remind", self.sw_sit_remind.isChecked())
        draft.set("life.sit_interval_min", int(self.sp_sit_interval.value()))
