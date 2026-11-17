# -*- coding: utf-8 -*-
"""通知设置页：QQ / 微信 / 邮件打开路径、微信唤醒热键与邮件 IMAP 检查间隔。"""
from __future__ import annotations

from PySide6 import QtWidgets

from ...core.config import ConfigDraft
from ..theme import G
from ..widgets import LineEdit, PillButton, SettingRow, SpinBox, hbox
from .base import SettingsPage


class NotifySettingsPage(SettingsPage):
    key = "notify"
    title = "通知"
    glyph = G.BELL

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.ed_qq, w_qq = self._path_picker("例如 C:/Program Files/Tencent/QQNT/QQ.exe")
        self.ed_wechat, w_wechat = self._path_picker("例如 C:/Program Files/Tencent/Weixin/Weixin.exe")
        self.ed_mail, w_mail = self._path_picker("例如 C:/Program Files/Mozilla Thunderbird/thunderbird.exe")

        self.ed_wx_hotkey = LineEdit("例如 Ctrl+Alt+W")
        self.ed_wx_hotkey.setFixedWidth(160)

        self.sp_mail_interval = SpinBox(15, 86400, 15, suffix=" 秒")

        self.finish_layout(
            self.section("消息点击打开路径", "在展开态「消息」页点击对应通知或图标时启动的客户端路径（留空则自动查找已运行窗口）"),
            SettingRow("QQ 客户端路径", "指定 QQ.exe 完整路径", w_qq),
            SettingRow("微信客户端路径", "指定 Weixin.exe / WeChat.exe 完整路径", w_wechat),
            SettingRow("邮件客户端路径", "指定 Thunderbird 或其它邮件客户端可执行文件路径", w_mail),
            self.section("托盘唤醒与邮件检查", "配置后台最小化到托盘的应用唤醒快捷键与 IMAP 轮询周期"),
            SettingRow("微信窗口唤醒热键", "当微信隐藏在系统托盘时，发送该快捷键将其主窗口唤出（默认 Ctrl+Alt+W）", self.ed_wx_hotkey),
            SettingRow("IMAP 邮件检查间隔", "后台定时检查新邮件的轮询秒数（最少 15 秒）", self.sp_mail_interval),
        )

    def _path_picker(self, placeholder: str) -> tuple[LineEdit, QtWidgets.QWidget]:
        ed = LineEdit(placeholder, width=164)
        ed.setFixedWidth(164)
        btn = PillButton("浏览...", height=30)
        btn.clicked.connect(lambda: self._browse_exe(ed))
        wrap = QtWidgets.QWidget()
        wrap.setLayout(hbox(ed, btn, spacing=6))
        return ed, wrap

    def _browse_exe(self, target_edit: LineEdit):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择客户端程序", "", "可执行文件 (*.exe);;所有文件 (*.*)"
        )
        if path:
            target_edit.setText(path)

    def load_from_draft(self, draft: ConfigDraft):
        self.ed_qq.setText(str(draft.get("notify.open.qq", "") or ""))
        self.ed_wechat.setText(str(draft.get("notify.open.wechat", "") or ""))
        self.ed_mail.setText(str(draft.get("notify.open.mail", "") or ""))
        hk = draft.get("apps.restore_hotkey.weixin.exe") or draft.get("apps.restore_hotkey.wechat.exe") or "Ctrl+Alt+W"
        self.ed_wx_hotkey.setText(str(hk))
        self.sp_mail_interval.setValue(int(draft.get("mail.interval_s", 120) or 120))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("notify.open.qq", self.ed_qq.text().strip())
        draft.set("notify.open.wechat", self.ed_wechat.text().strip())
        draft.set("notify.open.mail", self.ed_mail.text().strip())
        wx_hk = self.ed_wx_hotkey.text().strip() or "Ctrl+Alt+W"
        draft.set("apps.restore_hotkey.weixin.exe", wx_hk)
        draft.set("apps.restore_hotkey.wechat.exe", wx_hk)
        draft.set("mail.interval_s", int(self.sp_mail_interval.value()))
