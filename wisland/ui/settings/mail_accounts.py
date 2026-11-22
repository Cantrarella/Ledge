# -*- coding: utf-8 -*-
"""邮件账号设置页：统一表单卡片、响应式布局、密码显隐、草稿语义与独立账号状态。"""
from __future__ import annotations

import webbrowser

from PySide6 import QtCore, QtWidgets

from ...core.config import ConfigDraft
from ...services.mail.credentials import make_credential_key
from ...services.mail.manager import MailAccountManager
from ...services.mail.models import MailAccount
from ...services.mail.providers import PROVIDER_ORDER, get_provider, validate_account_params
from ..theme import G, W, bind_theme_context, settings_theme
from ..widgets import (
    Card,
    ComboBox,
    ConfirmDialog,
    IconButton,
    Label,
    LineEdit,
    PillButton,
    SpinBox,
    Switch,
    hbox,
    vbox,
)
from .base import SettingsPage

Qt = QtCore.Qt


def _classify_account_status(acc: MailAccount, is_pending: bool, is_connecting: bool) -> tuple[str, str]:
    """返回账号状态的 (状态标签, 颜色类型 'accent'|'ok'|'err'|'muted'|'sub')。"""
    if is_connecting:
        return "正在连接", "accent"
    if is_pending:
        return "待保存", "accent"
    if not acc.enabled:
        return "已停用", "muted"
    err = (acc.last_error or "").strip()
    if err:
        if any(k in err for k in ("授权码", "密码", "验证失败", "凭据")):
            return "登录失效", "err"
        return "网络错误", "err"
    if acc.last_test_at:
        return "连接正常", "ok"
    return "未测试", "sub"


class _Divider(QtWidgets.QFrame):
    """1px 细水平分隔线。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        settings_theme.changed.connect(self._apply)
        self._apply()

    @QtCore.Slot()
    def _apply(self):
        col = "rgba(255,255,255,18)" if settings_theme.dark else "rgba(0,0,0,15)"
        self.setStyleSheet(f"background:{col};border:none;")


class _SecretInput(QtWidgets.QWidget):
    """带显示/隐藏眼睛按钮的授权码输入框：切换明文不改变总宽度，不写入日志或 tooltip。"""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.edit = LineEdit(placeholder, width=0)
        self.edit.setMinimumWidth(0)
        self.edit.setMaximumWidth(16777215)
        self.edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.btn_eye = IconButton(G.EYE, size=28, icon_px=13, tip="显示授权码", parent=self)
        self.btn_eye.clicked.connect(self.toggle_visible)
        self._visible = False
        self.setLayout(hbox((self.edit, 1), self.btn_eye, spacing=6, margins=(0, 0, 0, 0)))

    def toggle_visible(self):
        self._visible = not self._visible
        self.edit.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Normal if self._visible else QtWidgets.QLineEdit.EchoMode.Password
        )
        self.btn_eye.glyph = G.EYE_OFF if self._visible else G.EYE
        self.btn_eye.setToolTip("隐藏授权码" if self._visible else "显示授权码")
        self.btn_eye.update()

    def text(self) -> str:
        return self.edit.text()

    def clear(self):
        self.edit.clear()
        if self._visible:
            self.toggle_visible()


class _ResponsiveRow(QtWidgets.QWidget):
    """响应式表单行：宽屏左右排列，窄屏自动切为标签在上、控件占满下方的上下排列。"""

    def __init__(self, title: str, sub: str, control: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.control = control
        self.lbl_title = Label(title, 13, W.Medium, 1)
        self.lbl_sub = QtWidgets.QLabel(sub, self)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setVisible(bool(sub))
        self.lbl_sub.setStyleSheet("background:transparent;border:none;font-size:11px;color:rgba(150,155,168,210);")

        self.label_box = QtWidgets.QWidget(self)
        self.label_box.setLayout(vbox(self.lbl_title, self.lbl_sub, spacing=2, margins=(0, 0, 0, 0)))

        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 8, 0, 8)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(6)
        self._compact: bool | None = None
        self.set_compact(False)

    def set_compact(self, compact: bool):
        if self._compact == compact:
            return
        self._compact = compact
        self._grid.removeWidget(self.label_box)
        self._grid.removeWidget(self.control)
        if compact:
            self.label_box.setMinimumWidth(0)
            self.label_box.setMaximumWidth(16777215)
            self._grid.setColumnMinimumWidth(0, 0)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 0)
            self._grid.addWidget(self.label_box, 0, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(self.control, 1, 0, Qt.AlignmentFlag.AlignTop)
        else:
            self.label_box.setFixedWidth(176)
            self._grid.setColumnMinimumWidth(0, 176)
            self._grid.setColumnStretch(0, 0)
            self._grid.setColumnStretch(1, 1)
            self._grid.addWidget(self.label_box, 0, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(self.control, 0, 1, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.updateGeometry()


class MailAccountsSettingsPage(SettingsPage):
    key = "mail_accounts"
    title = "邮件"
    glyph = G.MAIL

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)
        self._mgr: MailAccountManager = (
            getattr(ctx, "mail", None) if ctx is not None else None
        ) or MailAccountManager(parent=self)

        # 草稿工作副本：仅在底部点击「保存设置」时写入 config.json 与 Windows 凭据管理器
        self._draft_accounts: list[MailAccount] = []
        self._pending_secrets: dict[str, str] = {}   # account_id -> secret（待保存或重连新凭据）
        self._pending_ids: set[str] = set()          # 新加入待保存的账号 ID
        self._deleted_accounts: list[MailAccount] = []
        self._connecting_ids: set[str] = set()
        self._active_req_id: str = ""
        self._active_mode: str = ""                  # "test" | "stage_add" | "test_existing" | "reconnect"
        self._staged_candidate: tuple[MailAccount, str] | None = None
        self._pending_test_acc: MailAccount | None = None
        self._pending_reconn: tuple[MailAccount, str] | None = None
        self._narrow_mode: bool = False

        # ---- 1. 已添加账号列表 ----
        self.list_wrap = QtWidgets.QWidget()
        self.list_lay = vbox(spacing=8, margins=(0, 0, 0, 0))
        self.list_wrap.setLayout(self.list_lay)

        # ---- 2. 统一「添加邮箱账号」表单卡片（圆角 14px，内边距 18px，1px 分隔线） ----
        self.form_card = Card(self, radius=14, alpha=1.0)
        self.form_card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)

        self.cmb_provider = ComboBox(width=330)
        for pk in PROVIDER_ORDER:
            self.cmb_provider.addItem(get_provider(pk).name, pk)
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_changed)

        # 配置说明独占整行，自动换行，绝不竖排或截断
        self.guide_box = QtWidgets.QWidget(self.form_card)
        self.lbl_guide_title = Label("配置说明", 12.5, W.DemiBold, 1)
        self.lbl_guide_body = QtWidgets.QLabel("", self.guide_box)
        self.lbl_guide_body.setWordWrap(True)
        self.lbl_guide_body.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        self.btn_auth_page = PillButton("打开服务商授权页", height=30)
        self.btn_auth_page.clicked.connect(self._on_open_auth_page)
        self.guide_lay = QtWidgets.QVBoxLayout(self.guide_box)
        self.guide_lay.setContentsMargins(0, 8, 0, 8)
        self.guide_lay.setSpacing(6)
        self.guide_lay.addWidget(self.lbl_guide_title)
        self.guide_lay.addWidget(self.lbl_guide_body)
        self.guide_lay.addLayout(hbox(self.btn_auth_page, None, spacing=8))

        self.ed_email = LineEdit("例如 name@qq.com", width=330)
        self.ed_alias = LineEdit("可选显示别名", width=330)
        self.ed_secret = _SecretInput("授权码 / App 专用密码")

        self.ed_host = LineEdit("例如 imap.example.com", width=330)
        self.sp_port = SpinBox(1, 65535, 1)
        self.sp_port.setValue(993)
        self.cmb_security = ComboBox(width=330)
        self.cmb_security.addItem("SSL / TLS", "ssl")
        self.cmb_security.addItem("STARTTLS", "starttls")
        self.cmb_security.addItem("无加密", "none")

        self.row_provider = _ResponsiveRow("邮箱服务商", "支持主流国内/海外邮箱及自定义 IMAP", self.cmb_provider, self.form_card)
        self.row_email = _ResponsiveRow("邮箱地址", "Outlook / Hotmail / Live 暂不支持", self.ed_email, self.form_card)
        self.row_alias = _ResponsiveRow("显示别名", "可选，用于区分多个同服务商账号", self.ed_alias, self.form_card)
        self.row_secret = _ResponsiveRow("授权码 / 专用密码", "仅在点击底部「保存设置」后写入 Windows 凭据管理器", self.ed_secret, self.form_card)
        self.row_host = _ResponsiveRow("IMAP 服务器", "仅自定义 IMAP 需填写", self.ed_host, self.form_card)
        self.row_port = _ResponsiveRow("端口", "SSL 默认 993，STARTTLS 默认 143", self.sp_port, self.form_card)
        self.row_sec = _ResponsiveRow("加密方式", "默认推荐 SSL / TLS", self.cmb_security, self.form_card)

        self.div_host = _Divider(self.form_card)
        self.div_port = _Divider(self.form_card)
        self.div_sec = _Divider(self.form_card)

        self.btn_test = PillButton("测试连接", height=30)
        self.btn_test.clicked.connect(self._on_test_clicked)
        self.btn_stage_add = PillButton("验证并加入设置", height=30, primary=True)
        self.btn_stage_add.clicked.connect(self._on_stage_add_clicked)
        self.btn_cancel_task = PillButton("取消", height=30)
        self.btn_cancel_task.clicked.connect(self.cancel_pending_connection)
        self.btn_cancel_task.hide()

        self.lbl_status = QtWidgets.QLabel("", self.form_card)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.hide()

        self.action_bar = QtWidgets.QWidget(self.form_card)
        self.action_bar.setLayout(hbox(self.btn_test, self.btn_stage_add, self.btn_cancel_task, None, spacing=8))
        self.row_verify = _ResponsiveRow("连接验证", "后台执行，不阻塞界面；需点击底部「保存设置」才正式落盘", self.action_bar, self.form_card)

        self.form_card.setLayout(vbox(
            self.row_provider,
            _Divider(self.form_card),
            self.guide_box,
            _Divider(self.form_card),
            self.row_email,
            _Divider(self.form_card),
            self.row_alias,
            _Divider(self.form_card),
            self.row_secret,
            self.div_host,
            self.row_host,
            self.div_port,
            self.row_port,
            self.div_sec,
            self.row_sec,
            _Divider(self.form_card),
            self.row_verify,
            self.lbl_status,
            spacing=0,
            margins=(18, 12, 18, 14),
        ))

        # ---- 3. 阅读与验证码偏好卡片 ----
        self.prefs_card = Card(self, radius=14, alpha=1.0)
        self.prefs_card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)

        self.sw_otp_enabled = Switch()
        self.sw_otp_enabled.toggled.connect(lambda *_: self.changed.emit())
        self.cmb_otp_hide = ComboBox(width=330)
        self.cmb_otp_hide.addItem("60 秒", 60)
        self.cmb_otp_hide.addItem("180 秒（默认）", 180)
        self.cmb_otp_hide.addItem("300 秒（5 分钟）", 300)
        self.cmb_otp_hide.currentIndexChanged.connect(lambda *_: self.changed.emit())

        self.sw_sync_seen = Switch()
        self.sw_sync_seen.toggled.connect(lambda *_: self.changed.emit())

        self.row_otp_enabled = _ResponsiveRow("开启验证码识别", "从邮件标题、纯文本和 HTML 文本中自动识别验证码", self.sw_otp_enabled, self.prefs_card)
        self.row_otp_hide = _ResponsiveRow("验证码停留时长", "左侧验证码预览区从展示起倒计时关闭的时间（一律直接显示）", self.cmb_otp_hide, self.prefs_card)
        self.row_sync_seen = _ResponsiveRow("同步已读状态到服务器", "默认关闭，仅在本机标记已读；开启后打开正文会回写服务器", self.sw_sync_seen, self.prefs_card)

        self.privacy_box = QtWidgets.QWidget(self.prefs_card)
        self.lbl_privacy_title = Label("隐私说明", 12.5, W.DemiBold, 1)
        self.lbl_privacy_body = QtWidgets.QLabel(
            "邮件正文仅在本机处理，不执行脚本、不自动加载远程图片、不下载附件，验证码也不会写入任何日志。",
            self.privacy_box,
        )
        self.lbl_privacy_body.setWordWrap(True)
        self.lbl_privacy_body.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        priv_lay = QtWidgets.QVBoxLayout(self.privacy_box)
        priv_lay.setContentsMargins(0, 8, 0, 4)
        priv_lay.setSpacing(4)
        priv_lay.addWidget(self.lbl_privacy_title)
        priv_lay.addWidget(self.lbl_privacy_body)

        self.prefs_card.setLayout(vbox(
            self.row_otp_enabled,
            _Divider(self.prefs_card),
            self.row_otp_hide,
            _Divider(self.prefs_card),
            self.row_sync_seen,
            _Divider(self.prefs_card),
            self.privacy_box,
            spacing=0,
            margins=(18, 12, 18, 14),
        ))

        self.finish_layout(
            self.section("已添加账号", "各账号独立状态与凭据隔离；修改或新加账号需点击底部「保存设置」后生效"),
            self.list_wrap,
            self.section("添加邮箱账号", "填写后先验证连接，再加入设置草稿"),
            self.form_card,
            self.section("阅读与验证码", "控制正文已读同步与验证码识别行为"),
            self.prefs_card,
        )

        self._mgr.task_finished.connect(self._on_task_finished, Qt.ConnectionType.UniqueConnection)
        settings_theme.changed.connect(self._apply_guide_style)

        self._apply_guide_style()
        self._on_provider_changed()
        self._apply_responsive_layout()

    @QtCore.Slot()
    def _apply_guide_style(self):
        fg = "#c9ceda" if settings_theme.dark else "#3b4252"
        self.lbl_guide_body.setStyleSheet(f"background:transparent;border:none;font-size:12px;color:{fg};line-height:1.4;")
        self.lbl_privacy_body.setStyleSheet(f"background:transparent;border:none;font-size:12px;color:{fg};line-height:1.4;")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self):
        cw = max(320, self.width() or 580)
        narrow = cw < 560
        was_narrow = self._narrow_mode
        self._narrow_mode = narrow
        for row in (self.row_provider, self.row_email, self.row_alias, self.row_secret,
                    self.row_host, self.row_port, self.row_sec, self.row_verify,
                    self.row_otp_enabled, self.row_otp_hide, self.row_sync_seen):
            row.set_compact(narrow)

        ctrl_widgets = (
            self.cmb_provider, self.ed_email, self.ed_alias, self.ed_secret,
            self.ed_host, self.sp_port, self.cmb_security, self.action_bar,
            self.cmb_otp_hide,
        )
        if narrow:
            for w in ctrl_widgets:
                w.setMinimumWidth(0)
                w.setMaximumWidth(16777215)
        else:
            for w in ctrl_widgets:
                w.setFixedWidth(330)
        if was_narrow != narrow and self._draft_accounts:
            self._rebuild_account_list()
        if was_narrow != narrow:
            self.form_card.layout().invalidate()
            self.form_card.layout().activate()
            self.form_card.setMinimumHeight(self.form_card.sizeHint().height())
            self.form_card.updateGeometry()
            if self.layout() is not None:
                self.layout().invalidate()
                self.layout().activate()
            self.adjustSize()
            self.updateGeometry()

    def _current_provider_key(self) -> str:
        return str(self.cmb_provider.currentData() or "qq")

    def _on_provider_changed(self):
        spec = get_provider(self._current_provider_key())
        self.lbl_guide_body.setText(spec.guide)
        self.btn_auth_page.setVisible(bool(spec.auth_url))
        is_custom = (spec.key == "custom")
        for w in (self.div_host, self.row_host, self.div_port, self.row_port, self.div_sec, self.row_sec):
            w.setVisible(is_custom)

    def _on_open_auth_page(self):
        spec = get_provider(self._current_provider_key())
        if spec.auth_url:
            webbrowser.open(spec.auth_url)

    def _show_status(self, msg: str, kind: str = "info"):
        if not msg:
            self.lbl_status.hide()
            return
        if kind == "err":
            col = settings_theme.qss_color(settings_theme.up())
        elif kind == "ok":
            col = settings_theme.qss_color(settings_theme.down())
        else:
            col = settings_theme.qss_color(settings_theme.on_color())
        self.lbl_status.setStyleSheet(f"background:transparent;border:none;font-size:12px;font-weight:600;color:{col};padding-top:6px;")
        self.lbl_status.setText(msg)
        self.lbl_status.show()

    def _set_busy(self, req_id: str, mode: str, text: str):
        self._active_req_id = req_id
        self._active_mode = mode
        busy = bool(req_id)
        self.btn_test.setEnabled(not busy)
        self.btn_stage_add.setEnabled(not busy)
        self.btn_test.set_text("正在连接…" if (busy and mode == "test") else "测试连接")
        self.btn_stage_add.set_text("正在连接…" if (busy and mode == "stage_add") else "验证并加入设置")
        self.btn_cancel_task.setVisible(busy)
        self._show_status(text, kind="info")

    def cancel_pending_connection(self):
        """取消任何进行中的连接任务，确保关闭窗口后不会发生延迟写入。"""
        if self._active_req_id:
            self._mgr.cancel_task(self._active_req_id)
            self._active_req_id = ""
            self._active_mode = ""
            self._staged_candidate = None
            self._connecting_ids.clear()
            self.btn_test.setEnabled(True)
            self.btn_stage_add.setEnabled(True)
            self.btn_test.set_text("测试连接")
            self.btn_stage_add.set_text("验证并加入设置")
            self.btn_cancel_task.hide()
            self._show_status("已取消连接", kind="info")
            self._rebuild_account_list()

    def _build_candidate_from_form(self) -> tuple[bool, str, MailAccount | None, str]:
        prov = self._current_provider_key()
        email = self.ed_email.text().strip()
        secret = self.ed_secret.text()
        host = self.ed_host.text().strip()
        port = int(self.sp_port.value())
        sec = str(self.cmb_security.currentData() or "ssl")
        valid, err, clean = validate_account_params(prov, email, secret, host, port, sec, require_secret=True)
        if not valid:
            return False, err, None, ""
        for existing in self._draft_accounts:
            if existing.email.lower() == clean["email"].lower() and existing.host.lower() == clean["host"].lower():
                return False, "该邮箱账号已在列表中", None, ""
        import uuid
        acc_id = uuid.uuid4().hex[:12]
        acc = MailAccount(
            id=acc_id,
            provider=clean["provider"],
            email=clean["email"],
            display_name=self.ed_alias.text().strip(),
            username=clean["email"],
            host=clean["host"],
            port=clean["port"],
            security=clean["security"],
            enabled=True,
            credential_key=make_credential_key(acc_id),
        )
        return True, "", acc, secret

    def _on_test_clicked(self):
        if self._active_req_id:
            return
        ok, err, req_id = self._mgr.start_test_connection(
            provider=self._current_provider_key(),
            email=self.ed_email.text(),
            secret=self.ed_secret.text(),
            host=self.ed_host.text(),
            port=self.sp_port.value(),
            security=str(self.cmb_security.currentData() or "ssl"),
        )
        if not ok:
            self._show_status(err, kind="err")
            return
        self._set_busy(req_id, "test", "正在连接 IMAP 服务器进行测试…")

    def _on_stage_add_clicked(self):
        if self._active_req_id:
            return
        valid, err, acc, secret = self._build_candidate_from_form()
        if not valid or acc is None:
            self._show_status(err, kind="err")
            return
        ok, err2, req_id = self._mgr.start_test_connection(
            provider=acc.provider,
            email=acc.email,
            secret=secret,
            host=acc.host,
            port=acc.port,
            security=acc.security,
        )
        if not ok:
            self._show_status(err2, kind="err")
            return
        self._staged_candidate = (acc, secret)
        self._set_busy(req_id, "stage_add", "正在验证连接，通过后加入设置草稿…")

    @QtCore.Slot(str, bool, str)
    def _on_task_finished(self, req_id: str, ok: bool, err: str):
        if req_id != self._active_req_id:
            return
        mode = self._active_mode
        cand = self._staged_candidate
        test_acc = self._pending_test_acc
        reconn = self._pending_reconn
        self._active_req_id = ""
        self._active_mode = ""
        self._staged_candidate = None
        self._pending_test_acc = None
        self._pending_reconn = None
        self._connecting_ids.clear()
        self.btn_test.setEnabled(True)
        self.btn_stage_add.setEnabled(True)
        self.btn_test.set_text("测试连接")
        self.btn_stage_add.set_text("验证并加入设置")
        self.btn_cancel_task.hide()

        if mode == "test":
            if ok:
                self._show_status("连接成功，可加入设置", kind="ok")
            else:
                self._show_status(err or "连接失败，请检查配置或授权码后重试", kind="err")
        elif mode == "stage_add":
            if ok and cand is not None:
                acc, secret = cand
                acc.last_test_at = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000.0
                acc.last_error = ""
                self._draft_accounts.append(acc)
                self._pending_ids.add(acc.id)
                self._pending_secrets[acc.id] = secret
                self.ed_email.clear()
                self.ed_alias.clear()
                self.ed_secret.clear()
                self._show_status("已验证并加入列表（请点击底部「保存设置」提交）", kind="ok")
                self._rebuild_account_list()
                self.changed.emit()
            else:
                self._show_status(err or "验证失败，未加入列表", kind="err")
        elif mode == "test_existing":
            if test_acc is not None:
                if ok:
                    test_acc.last_test_at = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000.0
                    test_acc.last_error = ""
                else:
                    test_acc.last_error = err or "连接失败"
            self._rebuild_account_list()
            if ok:
                self._show_status("已有账号连接测试通过", kind="ok")
            else:
                self._show_status(err or "连接失败", kind="err")
        elif mode == "reconnect":
            if reconn is not None:
                r_acc, new_sec = reconn
                if ok:
                    r_acc.last_test_at = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000.0
                    r_acc.last_error = ""
                    self._pending_secrets[r_acc.id] = new_sec
                    self.changed.emit()
                else:
                    r_acc.last_error = err or "重连验证失败"
            self._rebuild_account_list()
            if ok:
                self._show_status("重连验证通过（请点击底部「保存设置」写入新凭据）", kind="ok")
            else:
                self._show_status(err or "重连验证失败，原凭据保持不变", kind="err")

    def _rebuild_account_list(self):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        if not self._draft_accounts:
            empty = Card(self.list_wrap, radius=14, alpha=0.85)
            btn_first = PillButton("添加第一个邮箱", height=30, primary=True)
            btn_first.clicked.connect(self._focus_add_form)
            empty.setLayout(
                hbox(
                    vbox(Label("暂未添加邮箱账号", 13.5, W.DemiBold, 1),
                         Label("支持 QQ、163、126、腾讯企业邮、iCloud、Gmail 与自定义 IMAP", 12, W.Normal, 3), spacing=2),
                    None,
                    btn_first,
                    spacing=12,
                    margins=(16, 14, 16, 14),
                )
            )
            self.list_lay.addWidget(empty)
            bind_theme_context(empty, settings_theme)
            return

        for acc in self._draft_accounts:
            self.list_lay.addWidget(self._build_account_card(acc))
        bind_theme_context(self.list_wrap, settings_theme)

    def _focus_add_form(self):
        self.ed_email.setFocus()
        parent_sa = self.parentWidget()
        while parent_sa is not None:
            if isinstance(parent_sa, QtWidgets.QScrollArea):
                parent_sa.ensureWidgetVisible(self.form_card)
                break
            parent_sa = parent_sa.parentWidget()

    def _build_account_card(self, acc: MailAccount) -> Card:
        card = Card(self.list_wrap, radius=14, alpha=1.0)
        spec = get_provider(acc.provider)
        title_txt = f"{acc.display_name}（{acc.email}）" if acc.display_name else acc.email
        st_label, st_kind = _classify_account_status(
            acc,
            is_pending=(acc.id in self._pending_ids or acc.id in self._pending_secrets),
            is_connecting=(acc.id in self._connecting_ids),
        )
        badge = Label(st_label, 11.5, W.DemiBold, 1)
        if st_kind == "ok":
            badge.set_color(settings_theme.down())
        elif st_kind == "err":
            badge.set_color(settings_theme.up())
        elif st_kind == "accent":
            badge.set_color(settings_theme.on_color())
        elif st_kind == "muted":
            badge.set_color(settings_theme.text3())
        else:
            badge.set_color(settings_theme.text2())

        detail = f"{spec.name} · {acc.host}:{acc.port}"
        if acc.last_error:
            detail = f"{spec.name} · {acc.last_error}"
        lbl_t = Label(title_txt, 13.5, W.DemiBold, 1)
        lbl_s = QtWidgets.QLabel(detail, card)
        lbl_s.setWordWrap(True)
        lbl_s.setStyleSheet(
            f"background:transparent;border:none;font-size:11.5px;color:{settings_theme.qss_color(settings_theme.up() if acc.last_error else settings_theme.text3())};"
        )

        sw = Switch(checked=acc.enabled)
        sw.setToolTip("启用或停用该邮箱账号")
        sw.toggled.connect(lambda on, a=acc: self._on_toggle_enabled(a, on))

        btn_test = PillButton("重试" if acc.last_error else "测试", height=28)
        btn_test.clicked.connect(lambda _=False, a=acc: self._on_test_draft_account(a))

        btn_reconn = PillButton("重连", height=28)
        btn_reconn.clicked.connect(lambda _=False, a=acc: self._on_reconnect(a))

        btn_alias = IconButton(G.SLIDERS, size=28, icon_px=12, tip="编辑显示别名")
        btn_alias.clicked.connect(lambda _=False, a=acc: self._on_edit_alias(a))

        btn_del = IconButton(G.TRASH, size=28, icon_px=12, tip="删除账号及对应凭据")
        btn_del.clicked.connect(lambda _=False, a=acc: self._on_delete_account(a))

        info_col = vbox(hbox(lbl_t, badge, None, spacing=8), lbl_s, spacing=3)
        if self._narrow_mode:
            card.setLayout(
                vbox(
                    info_col,
                    hbox(sw, btn_test, btn_reconn, btn_alias, btn_del, None, spacing=8),
                    spacing=8,
                    margins=(16, 12, 14, 12),
                )
            )
        else:
            card.setLayout(
                hbox(
                    (info_col, 1),
                    sw,
                    btn_test,
                    btn_reconn,
                    btn_alias,
                    btn_del,
                    spacing=8,
                    margins=(16, 12, 14, 12),
                )
            )
        return card

    def _on_toggle_enabled(self, acc: MailAccount, on: bool):
        acc.enabled = bool(on)
        self._rebuild_account_list()
        self.changed.emit()

    def _on_test_draft_account(self, acc: MailAccount):
        if self._active_req_id:
            return
        secret = self._pending_secrets.get(acc.id) or self._mgr._creds.read_secret(acc.credential_key) or ""
        if not secret:
            acc.last_error = "未找到授权码，请点击「重连」重新输入"
            self._rebuild_account_list()
            return
        ok, err, req_id = self._mgr.start_test_connection(
            provider=acc.provider, email=acc.email, secret=secret, host=acc.host, port=acc.port, security=acc.security
        )
        if not ok:
            self._show_status(err, kind="err")
            return
        self._connecting_ids.add(acc.id)
        self._pending_test_acc = acc
        self._rebuild_account_list()
        self._set_busy(req_id, "test_existing", f"正在测试 {acc.email} 连接…")

    def _on_reconnect(self, acc: MailAccount):
        if self._active_req_id:
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "重新连接账号",
            f"请输入 {acc.email} 的新授权码 / App 专用密码\n（验证通过后才会暂存并在点击保存时替换原凭据）：",
            QtWidgets.QLineEdit.EchoMode.Password,
        )
        if not ok or not text.strip():
            return
        new_sec = text.strip()
        started, err, req_id = self._mgr.start_test_connection(
            provider=acc.provider, email=acc.email, secret=new_sec, host=acc.host, port=acc.port, security=acc.security
        )
        if not started:
            self._show_status(err, kind="err")
            return
        self._connecting_ids.add(acc.id)
        # 仅在验证成功后通过回调写入 _pending_secrets
        self._pending_reconn = (acc, new_sec)
        self._rebuild_account_list()
        self._set_busy(req_id, "reconnect", f"正在验证 {acc.email} 新凭据…")

    def _on_edit_alias(self, acc: MailAccount):
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "编辑账号别名",
            f"为 {acc.email} 设置显示别名：",
            QtWidgets.QLineEdit.EchoMode.Normal,
            acc.display_name,
        )
        if ok:
            acc.display_name = text.strip()
            self._rebuild_account_list()
            self.changed.emit()

    def _on_delete_account(self, acc: MailAccount):
        if ConfirmDialog.ask(
            self,
            "确认删除邮箱账号？",
            f"将从列表中移除 {acc.email}，并在点击「保存设置」后清理 Windows 凭据管理器中的对应授权码，不会删除本地无关数据。",
            confirm_text="移除账号",
        ):
            self._draft_accounts = [a for a in self._draft_accounts if a.id != acc.id]
            self._pending_secrets.pop(acc.id, None)
            self._pending_ids.discard(acc.id)
            if acc.credential_key:
                self._deleted_accounts.append(acc)
            self._rebuild_account_list()
            self.changed.emit()

    # ---------------- 统一草稿提交与取消语义 ----------------
    def commit_credentials(self) -> tuple[bool, str]:
        """在保存 config.json 前提交暂存的新增/重连凭据；任一步失败立即返回错误且不提交配置。"""
        written_rollback: list[tuple[str, str, str | None]] = []
        for acc_id, sec in list(self._pending_secrets.items()):
            acc = next((a for a in self._draft_accounts if a.id == acc_id), None)
            if acc is None:
                continue
            prev = self._mgr._creds.read_secret(acc.credential_key)
            if not prev and (getattr(self._mgr._creds, "last_error", 0) or getattr(self._mgr._creds, "last_error_message", "")):
                for tgt, usr, old_val in reversed(written_rollback):
                    if old_val is None:
                        self._mgr._creds.delete_secret(tgt)
                    else:
                        self._mgr._creds.save_secret(tgt, usr, old_val)
                return False, f"读取 {acc.email} 授权码备份失败"
            if not self._mgr._creds.save_secret(acc.credential_key, acc.username or acc.email, sec):
                # 回滚本次已写入的凭据
                for tgt, usr, old_val in written_rollback:
                    if old_val is None:
                        self._mgr._creds.delete_secret(tgt)
                    else:
                        self._mgr._creds.save_secret(tgt, usr, old_val)
                return False, f"保存 {acc.email} 授权码至 Windows 凭据管理器失败"
            written_rollback.append((acc.credential_key, acc.username or acc.email, prev))
        self._last_commit_rollback = written_rollback
        return True, ""

    def rollback_credentials(self) -> bool:
        """在后续提交步骤失败时回滚本次已删除或已写入的邮箱凭据。"""
        all_ok = True
        del_rb = list(getattr(self, "_last_delete_rollback", []) or [])
        for tgt, usr, old_val in reversed(del_rb):
            if old_val is not None and not self._mgr._creds.save_secret(tgt, usr, old_val):
                all_ok = False
        self._last_delete_rollback = []

        rb = list(getattr(self, "_last_commit_rollback", []) or [])
        for tgt, usr, old_val in reversed(rb):
            if old_val is None:
                if not self._mgr._creds.delete_secret(tgt):
                    all_ok = False
            elif not self._mgr._creds.save_secret(tgt, usr, old_val):
                all_ok = False
        self._last_commit_rollback = []
        return all_ok

    def commit_credential_deletes(self) -> tuple[bool, str]:
        """删除待移除邮箱账号的凭据（带逆序回滚），不修改管理器运行时状态。"""
        del_rollback: list[tuple[str, str, str | None]] = []
        for acc in list(self._deleted_accounts):
            if not acc.credential_key:
                continue
            prev = self._mgr._creds.read_secret(acc.credential_key)
            if not prev and (getattr(self._mgr._creds, "last_error", 0) or getattr(self._mgr._creds, "last_error_message", "")):
                rb_ok = True
                for tgt, usr, old_val in reversed(del_rollback):
                    if old_val is not None and not self._mgr._creds.save_secret(tgt, usr, old_val):
                        rb_ok = False
                rb_note = "（且部分凭据回滚失败，请检查系统凭据管理器）" if not rb_ok else ""
                return False, f"读取 {acc.email} 授权码备份失败{rb_note}"
            if not self._mgr._creds.delete_secret(acc.credential_key):
                rb_ok = True
                for tgt, usr, old_val in reversed(del_rollback):
                    if old_val is not None and not self._mgr._creds.save_secret(tgt, usr, old_val):
                        rb_ok = False
                rb_note = "（且部分凭据回滚失败，请检查系统凭据管理器）" if not rb_ok else ""
                return False, f"清理 {acc.email} 授权码失败{rb_note}"
            del_rollback.append((acc.credential_key, acc.username or acc.email, prev))
        self._last_delete_rollback = del_rollback
        return True, ""

    def apply_runtime_commit(self, emit_signal: bool = True) -> None:
        """全部配置与凭据提交成功后，清理草稿状态并刷新管理器运行时与通知。"""
        self._last_commit_rollback = []
        self._last_delete_rollback = []
        self._pending_secrets.clear()
        self._pending_ids.clear()
        self._deleted_accounts.clear()
        self._mgr.load_from_config()
        if emit_signal:
            self._mgr.accounts_changed.emit()
        self._rebuild_account_list()

    def finalize_commit(self) -> tuple[bool, str]:
        """兼容入口：删除待清理凭据后刷新管理器内存状态。"""
        ok, err = self.commit_credential_deletes()
        if not ok:
            return False, err
        self.apply_runtime_commit()
        return True, ""

    def discard_pending(self):
        """取消或关闭时丢弃暂存账号、清空内存授权码并取消进行中的连接。"""
        self.cancel_pending_connection()
        self._pending_secrets.clear()
        self._pending_ids.clear()
        self._deleted_accounts.clear()
        self._staged_candidate = None

    def load_from_draft(self, draft: ConfigDraft):
        self.discard_pending()
        raw = draft.get("mail.accounts", [])
        loaded: list[MailAccount] = []
        if isinstance(raw, list):
            for item in raw:
                acc = MailAccount.from_config_dict(item)
                if acc is not None:
                    loaded.append(acc)
        self._draft_accounts = loaded
        self.sw_otp_enabled.setChecked(bool(draft.get("mail.otp_enabled", True)))
        hide_s = int(draft.get("mail.otp_hide_seconds", 180) or 180)
        idx = self.cmb_otp_hide.findData(hide_s)
        self.cmb_otp_hide.setCurrentIndex(idx if idx >= 0 else 1)
        self.sw_sync_seen.setChecked(bool(draft.get("mail.sync_read_to_server", False)))
        self._rebuild_account_list()

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("mail.accounts", [a.to_config_dict() for a in self._draft_accounts])
        draft.set("mail.otp_enabled", bool(self.sw_otp_enabled.isChecked()))
        draft.set("mail.otp_hide_seconds", int(self.cmb_otp_hide.currentData() or 180))
        draft.set("mail.sync_read_to_server", bool(self.sw_sync_seen.isChecked()))
