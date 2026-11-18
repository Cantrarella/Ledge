# -*- coding: utf-8 -*-
"""通知设置页：Windows 通知权限状态、来源开关、隐私摘要、任务栏兼容与免打扰时段。"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ...core.config import ConfigDraft
from ...services.notifications.sources.windows import open_windows_notification_settings
from ..theme import G, W
from ..widgets import Label, LineEdit, PillButton, SettingRow, SpinBox, Switch, hbox
from .base import SettingsPage


class NotifySettingsPage(SettingsPage):
    key = "notify"
    title = "通知"
    glyph = G.BELL

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.sw_enabled = Switch()
        self.sw_wechat = Switch()
        self.sw_qq = Switch()
        self.sw_flash = Switch()
        self.sw_preview = Switch()
        self.sp_display_s = SpinBox(1, 30, 1, suffix=" 秒")

        self.sw_dnd = Switch()
        self.ed_dnd_start = LineEdit("23:00", width=72)
        self.ed_dnd_end = LineEdit("07:00", width=72)
        dnd_time_w = QtWidgets.QWidget()
        dnd_time_w.setLayout(hbox(self.ed_dnd_start, Label("至", 12, W.Normal, 2), self.ed_dnd_end, spacing=6))

        self.lbl_perm = Label("正在检测权限状态…", 12, W.Medium, 2)
        self.lbl_listen = Label("未启动", 12, W.Normal, 3)

        self.btn_req_perm = PillButton("重新请求权限", height=28)
        self.btn_req_perm.clicked.connect(self._on_request_perm)
        self.btn_sys_settings = PillButton("打开 Windows 通知设置", height=28)
        self.btn_sys_settings.clicked.connect(self._on_open_sys_settings)
        perm_actions = QtWidgets.QWidget()
        perm_actions.setLayout(hbox(self.btn_req_perm, self.btn_sys_settings, None, spacing=8))

        self.ed_qq, w_qq = self._path_picker("例如 C:/Program Files/Tencent/QQNT/QQ.exe")
        self.ed_wechat, w_wechat = self._path_picker("例如 C:/Program Files/Tencent/Weixin/Weixin.exe")
        self.ed_mail, w_mail = self._path_picker("例如 C:/Program Files/Mozilla Thunderbird/thunderbird.exe")

        self.ed_wx_hotkey = LineEdit("例如 Ctrl+Alt+W")
        self.ed_wx_hotkey.setFixedWidth(160)
        self.sp_mail_interval = SpinBox(15, 86400, 15, suffix=" 秒")

        self.finish_layout(
            self.section("Windows 通知监听", "优先使用 Windows 官方 UserNotificationListener，权限受限时自动保留任务栏闪烁兼容"),
            SettingRow("开启 Windows 通知读取", "关闭后立即停止后台通知监听", self.sw_enabled),
            SettingRow("当前权限状态", "Windows 10 / 11 通知访问授权状态", self.lbl_perm),
            SettingRow("监听状态与最近错误", "后台采集线程实时状态", self.lbl_listen),
            SettingRow("权限恢复操作", "若被系统拒绝，可重新请求或在系统设置中开启通知访问", perm_actions),

            self.section("来源与隐私展示", "默认隐藏通知正文以保护隐私，点击横幅仅聚焦对应客户端窗口"),
            SettingRow("微信通知开关", "接收微信桌面端通知与闪烁提醒", self.sw_wechat),
            SettingRow("QQ 通知开关", "接收 QQ 桌面端通知与闪烁提醒", self.sw_qq),
            SettingRow("任务栏闪烁兼容模式", "官方通知权限不可用或客户端仅闪灯时兜底提示「有新消息」", self.sw_flash),
            SettingRow("显示通知摘要", "开启后才在横幅展示脱敏后的消息摘要（默认关闭）", self.sw_preview),
            SettingRow("通知显示时长", "收起态紧凑横幅单条停留时间", self.sp_display_s),
            SettingRow("免打扰时段", "免打扰期间仅累计未读色点，不弹出横幅", self.sw_dnd),
            SettingRow("免打扰时间范围", "24 小时制 HH:MM，支持跨零点（如 23:00 至 07:00）", dnd_time_w),

            self.section("消息点击打开路径", "当客户端未运行或需指定路径时使用的可执行文件（留空则优先聚焦已运行窗口）"),
            SettingRow("QQ 客户端路径", "指定 QQ.exe 完整路径", w_qq),
            SettingRow("微信客户端路径", "指定 Weixin.exe / WeChat.exe 完整路径", w_wechat),
            SettingRow("邮件客户端路径", "指定 Thunderbird 或其它邮件客户端路径", w_mail),
            self.section("托盘唤醒与邮件检查", "配置后台最小化到托盘的应用唤醒快捷键与 IMAP 轮询周期"),
            SettingRow("微信窗口唤醒热键", "当微信隐藏在系统托盘时发送该快捷键唤出（默认 Ctrl+Alt+W）", self.ed_wx_hotkey),
            SettingRow("IMAP 邮件检查间隔", "后台定时检查新邮件的轮询秒数（最少 15 秒）", self.sp_mail_interval),
        )

        self._notify_svc = getattr(ctx, "notify", None) if ctx is not None else None
        if self._notify_svc is not None and hasattr(self._notify_svc, "status_changed"):
            self._notify_svc.status_changed.connect(self._update_status_ui, QtCore.Qt.ConnectionType.UniqueConnection)
            self.destroyed.connect(self._disconnect_status)
            if hasattr(self._notify_svc, "status_snapshot"):
                self._update_status_ui(self._notify_svc.status_snapshot())

    def _disconnect_status(self):
        svc = getattr(self, "_notify_svc", None)
        if svc is not None and hasattr(svc, "status_changed"):
            try:
                svc.status_changed.disconnect(self._update_status_ui)
            except Exception:  # noqa: BLE001
                pass

    def _update_status_ui(self, snap: dict):
        if not isinstance(snap, dict):
            return
        self.lbl_perm.setText(str(snap.get("permission_label") or "未知"))
        listening = bool(snap.get("listening", False))
        err = str(snap.get("last_error") or "").strip()
        if err:
            self.lbl_listen.setText(f"{'监听中' if listening else '已停止'} · {err}")
        else:
            self.lbl_listen.setText("监听中（后台线程运行，不阻塞 UI）" if listening else "已停止或等待授权")

    def _on_request_perm(self):
        if self._notify_svc is not None and hasattr(self._notify_svc, "request_permission"):
            self._notify_svc.request_permission()

    def _on_open_sys_settings(self):
        if self._notify_svc is not None and hasattr(self._notify_svc, "open_system_settings"):
            self._notify_svc.open_system_settings()
        else:
            open_windows_notification_settings()

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
        self.sw_enabled.setChecked(bool(draft.get("notify.enabled", True)))
        self.sw_wechat.setChecked(bool(draft.get("notify.wechat_enabled", True)))
        self.sw_qq.setChecked(bool(draft.get("notify.qq_enabled", True)))
        self.sw_flash.setChecked(bool(draft.get("notify.flash_compat", True)))
        self.sw_preview.setChecked(bool(draft.get("notify.preview_enabled", False)))
        self.sp_display_s.setValue(int(draft.get("notify.display_seconds", 4) or 4))
        self.sw_dnd.setChecked(bool(draft.get("notify.dnd_enabled", False)))
        self.ed_dnd_start.setText(str(draft.get("notify.dnd_start", "23:00") or "23:00"))
        self.ed_dnd_end.setText(str(draft.get("notify.dnd_end", "07:00") or "07:00"))

        self.ed_qq.setText(str(draft.get("notify.open.qq", "") or ""))
        self.ed_wechat.setText(str(draft.get("notify.open.wechat", "") or ""))
        self.ed_mail.setText(str(draft.get("notify.open.mail", "") or ""))
        hk = draft.get("apps.restore_hotkey.weixin.exe") or draft.get("apps.restore_hotkey.wechat.exe") or "Ctrl+Alt+W"
        self.ed_wx_hotkey.setText(str(hk))
        self.sp_mail_interval.setValue(int(draft.get("mail.interval_s", 120) or 120))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("notify.enabled", bool(self.sw_enabled.isChecked()))
        draft.set("notify.wechat_enabled", bool(self.sw_wechat.isChecked()))
        draft.set("notify.qq_enabled", bool(self.sw_qq.isChecked()))
        draft.set("notify.flash_compat", bool(self.sw_flash.isChecked()))
        draft.set("notify.preview_enabled", bool(self.sw_preview.isChecked()))
        draft.set("notify.display_seconds", int(self.sp_display_s.value()))
        draft.set("notify.dnd_enabled", bool(self.sw_dnd.isChecked()))
        draft.set("notify.dnd_start", self.ed_dnd_start.text().strip() or "23:00")
        draft.set("notify.dnd_end", self.ed_dnd_end.text().strip() or "07:00")

        draft.set("notify.open.qq", self.ed_qq.text().strip())
        draft.set("notify.open.wechat", self.ed_wechat.text().strip())
        draft.set("notify.open.mail", self.ed_mail.text().strip())
        wx_hk = self.ed_wx_hotkey.text().strip() or "Ctrl+Alt+W"
        draft.set("apps.restore_hotkey.weixin.exe", wx_hk)
        draft.set("apps.restore_hotkey.wechat.exe", wx_hk)
        draft.set("mail.interval_s", int(self.sp_mail_interval.value()))
