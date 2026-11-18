# -*- coding: utf-8 -*-
"""AI 额度设置页（第一阶段）：功能开关、内联添加账号、五服务商状态、DeepSeek 余额卡片与凭据事务管理。"""
from __future__ import annotations

import copy
import datetime as dt

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import ConfigDraft, cfg
from ...services.quota import (
    ACCENT_COLORS,
    AccountStatus,
    PROVIDER_ORDER,
    PROVIDERS,
    Provider,
    QuotaAccount,
    QuotaManager,
    QuotaSnapshot,
    STATUS_META,
    is_provider_implemented,
)
from ..pages.system import styled_menu
from ..theme import G, W, theme, ui_font
from ..widgets import (
    Card,
    ComboBox,
    ConfirmDialog,
    IconButton,
    Label,
    LineEdit,
    PasswordEdit,
    PillButton,
    SettingRow,
    SpinBox,
    Switch,
    hbox,
    painter,
    vbox,
)
from .base import SettingsPage

Qt = QtCore.Qt


def _format_ts(ts: float) -> str:
    if not ts or ts <= 0:
        return "尚未刷新"
    try:
        d = dt.datetime.fromtimestamp(float(ts))
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "尚未刷新"


def _currency_symbol(cur: str) -> str:
    c = str(cur or "").upper()
    if c == "CNY":
        return "¥"
    if c == "USD":
        return "$"
    return f"{c} "


class _ProviderBadge(QtWidgets.QWidget):
    """使用账号强调色绘制的服务商圆角徽标（28×28）。"""

    def __init__(self, text: str, color_key: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.color_key = color_key
        self.setFixedSize(28, 28)

    def paintEvent(self, e):
        p = painter(self)
        _label, hex_col = ACCENT_COLORS.get(self.color_key, ACCENT_COLORS["blue"])
        col = QtGui.QColor(hex_col)
        bg = QtGui.QColor(col)
        bg.setAlphaF(0.20 if theme.dark else 0.15)
        p.setPen(QtGui.QPen(col, 1.2))
        p.setBrush(bg)
        r = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        p.drawRoundedRect(r, 8.0, 8.0)
        p.setFont(ui_font(10.5, W.Bold))
        p.setPen(col if not theme.dark else QtGui.QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)


class _StatusPill(QtWidgets.QWidget):
    """右侧状态胶囊：带明确语义颜色与 Tooltip。"""

    def __init__(self, status: str, extra_tip: str = "", parent=None):
        super().__init__(parent)
        self.status = status if status in STATUS_META else AccountStatus.UNCONFIGURED.value
        label, kind, base_tip = STATUS_META[self.status]
        self.label_text = label
        self.kind = kind
        tip = f"{label}：{base_tip}"
        if extra_tip:
            tip = f"{tip}\n{extra_tip}"
        self.setToolTip(tip)
        fm = QtGui.QFontMetrics(ui_font(11.0, W.DemiBold))
        self.setFixedSize(max(64, fm.horizontalAdvance(self.label_text) + 20), 22)

    def _resolve_color(self) -> QtGui.QColor:
        if self.kind == "ok":
            return QtGui.QColor(theme.down())
        if self.kind == "err":
            return QtGui.QColor(theme.up())
        if self.kind == "warn":
            return QtGui.QColor("#f59e0b")
        if self.kind == "accent":
            return QtGui.QColor(theme.on_color())
        return QtGui.QColor(theme.text3())

    def paintEvent(self, e):
        p = painter(self)
        col = self._resolve_color()
        bg = QtGui.QColor(col)
        bg.setAlphaF(0.18 if theme.dark else 0.14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        r = QtCore.QRectF(self.rect())
        p.drawRoundedRect(r, 11.0, 11.0)
        p.setFont(ui_font(11.0, W.DemiBold))
        p.setPen(col)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.label_text)


class _QuotaAccountCard(Card):
    """单账号固定四层结构卡片（圆角 14px）。"""

    def __init__(
        self,
        acc: QuotaAccount,
        snap: QuotaSnapshot | None,
        is_refreshing: bool,
        narrow_mode: bool,
        page: QuotaSettingsPage,
        parent=None,
    ):
        super().__init__(parent, radius=14, alpha=1.0)
        self.acc = acc
        self.snap = snap
        self.page = page
        spec = PROVIDERS.get(acc.provider, PROVIDERS[Provider.DEEPSEEK.value])

        # 计算有效状态
        if not is_provider_implemented(acc.provider):
            eff_status = AccountStatus.UNSUPPORTED.value
        elif is_refreshing:
            eff_status = AccountStatus.REFRESHING.value
        elif not acc.enabled:
            eff_status = AccountStatus.DISABLED.value
        elif snap is not None:
            eff_status = snap.status
        elif acc.last_error:
            eff_status = AccountStatus.NETWORK_ERROR.value
        else:
            eff_status = AccountStatus.UNCONFIGURED.value

        self.eff_status = eff_status
        err_txt = (snap.last_error if snap and snap.last_error else acc.last_error) or ""

        # ---- 第一层：服务商图标 + 账号别名 + 服务商名称 + 来源/标识 + 右侧状态胶囊 ----
        self.badge_icon = _ProviderBadge(spec.badge, acc.color, self)
        alias_txt = acc.alias.strip() or spec.name
        if acc.pinned:
            alias_txt = f"★ {alias_txt}"
        self.lbl_alias = Label(alias_txt, 14.0, W.DemiBold, 1, parent=self)
        self.lbl_provider = Label(spec.name, 11.5, W.Medium, 2, parent=self)
        ident_part = acc.identity or acc.source or spec.default_source
        self.lbl_source = Label(f"· {ident_part}", 11.5, W.Normal, 3, parent=self)
        self.status_pill = _StatusPill(eff_status, extra_tip=err_txt, parent=self)

        if narrow_mode:
            row1 = vbox(
                hbox(self.badge_icon, self.lbl_alias, self.lbl_provider, None, self.status_pill, spacing=8),
                self.lbl_source,
                spacing=3,
            )
        else:
            row1 = hbox(
                self.badge_icon,
                self.lbl_alias,
                self.lbl_provider,
                self.lbl_source,
                None,
                self.status_pill,
                spacing=8,
            )

        # ---- 第二层：额度展示区 ----
        self.body_wrap = QtWidgets.QWidget(self)
        if not spec.implemented:
            lbl_unimp = Label("连接器待接入", 13.0, W.Medium, 3, parent=self.body_wrap)
            self.body_wrap.setLayout(hbox(lbl_unimp, None, spacing=8, margins=(0, 2, 0, 2)))
        elif snap is not None and snap.primary_balance() is not None:
            bal = snap.primary_balance()
            assert bal is not None
            sym = _currency_symbol(bal.currency)
            self.lbl_main_bal = Label(
                f"{sym} {bal.total_balance:.2f}",
                21.0,
                W.Bold,
                1,
                num=True,
                parent=self.body_wrap,
            )
            sub_bal_txt = (
                f"可用余额 ({bal.currency})   ·   "
                f"赠送 {sym}{bal.granted_balance:.2f}   ·   "
                f"充值 {sym}{bal.topped_up_balance:.2f}"
            )
            self.lbl_sub_bal = Label(sub_bal_txt, 11.5, W.Normal, 2, num=True, parent=self.body_wrap)
            if narrow_mode:
                self.body_wrap.setLayout(vbox(self.lbl_main_bal, self.lbl_sub_bal, spacing=2, margins=(0, 2, 0, 2)))
            else:
                self.body_wrap.setLayout(
                    hbox(self.lbl_main_bal, self.lbl_sub_bal, None, spacing=12, margins=(0, 2, 0, 2))
                )
        else:
            lbl_wait = Label("等待首次刷新", 13.0, W.Medium, 3, parent=self.body_wrap)
            self.body_wrap.setLayout(hbox(lbl_wait, None, spacing=8, margins=(0, 2, 0, 2)))

        # ---- 第三层：错误信息（最多两行）或最后更新时间 ----
        self.lbl_meta = QtWidgets.QLabel(self)
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setMaximumHeight(36)
        self.lbl_meta.setFont(ui_font(11.5, W.Normal, num=True))
        is_error_state = eff_status in (
            AccountStatus.AUTH_ERROR.value,
            AccountStatus.NETWORK_ERROR.value,
            AccountStatus.PARSE_ERROR.value,
            AccountStatus.RATE_LIMITED.value,
        ) or bool(err_txt)

        if err_txt:
            lines = [ln.strip() for ln in err_txt.splitlines() if ln.strip()]
            short_err = "\n".join(lines[:2])
            if snap is not None and snap.raw_stale and snap.fetched_at > 0:
                short_err = f"{short_err}（当前展示 {_format_ts(snap.fetched_at)} 的缓存数据）"
            self.lbl_meta.setText(short_err)
            col_meta = theme.up() if eff_status != AccountStatus.RATE_LIMITED.value else QtGui.QColor("#f59e0b")
        else:
            ts_val = snap.fetched_at if (snap is not None and snap.fetched_at > 0) else acc.last_refresh_at
            meta_txt = f"最后更新：{_format_ts(ts_val)}"
            if snap is not None and snap.raw_stale:
                meta_txt = f"{meta_txt} · 数据已过期"
            self.lbl_meta.setText(meta_txt)
            col_meta = theme.text3()
        self.lbl_meta.setStyleSheet(f"background:transparent;border:none;color:{theme.qss_color(col_meta)};")

        # ---- 第四层：固定操作区（监控 Switch + 置顶按钮 + 刷新/重试 + 异常时重新连接 + ⋯ 菜单） ----
        self.sw_monitor = Switch(checked=acc.enabled, parent=self)
        self.sw_monitor.setToolTip("开启或关闭该账号的额度监控")
        self.sw_monitor.toggled.connect(lambda on: self.page._on_toggle_account_enabled(self.acc, on))
        lbl_mon = Label("监控", 11.5, W.Normal, 2, parent=self)

        self.btn_pin = PillButton("已置顶" if acc.pinned else "置顶", height=26, parent=self)
        self.btn_pin.setToolTip("在岛内页面优先显示该账号")
        self.btn_pin.clicked.connect(lambda: self.page._on_toggle_account_pinned(self.acc))

        refresh_label = "重试" if is_error_state else "刷新"
        self.btn_refresh = PillButton(refresh_label, height=26, parent=self)
        self.btn_refresh.setEnabled(spec.implemented and not is_refreshing)
        self.btn_refresh.clicked.connect(lambda: self.page._on_refresh_single(self.acc))

        # 仅在异常状态时显示“重新连接”，正常状态隐藏
        self.btn_reconnect = PillButton("重新连接", height=26, parent=self)
        self.btn_reconnect.clicked.connect(lambda: self.page._on_reconnect_account(self.acc))
        self.btn_reconnect.setVisible(bool(spec.implemented and is_error_state))

        self.btn_more = PillButton("⋯", height=26, parent=self)
        self.btn_more.setToolTip("更多操作（修改别名、强调色、置顶、删除）")
        self.btn_more.clicked.connect(self._open_more_menu)

        if narrow_mode:
            row4 = vbox(
                hbox(lbl_mon, self.sw_monitor, self.btn_pin, None, self.btn_more, spacing=8),
                hbox(self.btn_refresh, self.btn_reconnect, None, spacing=8),
                spacing=6,
            )
        else:
            row4 = hbox(
                lbl_mon,
                self.sw_monitor,
                self.btn_pin,
                self.btn_refresh,
                self.btn_reconnect,
                None,
                self.btn_more,
                spacing=8,
            )

        self.setLayout(
            vbox(
                row1,
                self.body_wrap,
                self.lbl_meta,
                row4,
                spacing=8,
                margins=(16, 12, 16, 12),
            )
        )

    def _open_more_menu(self):
        menu = styled_menu(self)
        act_alias = menu.addAction("修改别名")
        act_alias.triggered.connect(lambda: self.page._on_edit_alias(self.acc))

        color_menu = menu.addMenu("强调色")
        color_menu.setStyleSheet(menu.styleSheet())
        for c_key, (c_name, _hex) in ACCENT_COLORS.items():
            prefix = "✓ " if self.acc.color == c_key else "    "
            act_c = color_menu.addAction(f"{prefix}{c_name}")
            act_c.triggered.connect(lambda _=False, ck=c_key: self.page._on_set_color(self.acc, ck))

        pin_label = "取消置顶" if self.acc.pinned else "置顶"
        act_pin = menu.addAction(pin_label)
        act_pin.triggered.connect(lambda: self.page._on_toggle_account_pinned(self.acc))

        menu.addSeparator()
        act_del = menu.addAction("删除账号")
        act_del.triggered.connect(lambda: self.page._on_delete_account(self.acc))

        pos = self.btn_more.mapToGlobal(QtCore.QPoint(0, self.btn_more.height() + 4))
        menu.exec(pos)


class QuotaSettingsPage(SettingsPage):
    """AI 额度设置分类页（key="quota"）。"""

    key = "quota"
    title = "AI 额度"
    glyph = G.PULSE

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)
        self._mgr: QuotaManager = (
            getattr(ctx, "quota", None)
            if (ctx is not None and getattr(ctx, "quota", None) is not None)
            else QuotaManager()
        )
        self._loading = False
        self._narrow_mode = False
        self._active_req_id = ""
        self._reconnect_target_acc: QuotaAccount | None = None

        self._draft_accounts: list[QuotaAccount] = []
        self._draft_snapshots: dict[str, QuotaSnapshot] = {}
        self._pending_secrets: dict[str, str] = {}
        self._deleted_accounts: list[QuotaAccount] = []
        self._cards: list[_QuotaAccountCard] = []

        # ---- 1. 功能设置卡 ----
        self.sw_enabled = Switch()
        self.sw_enabled.toggled.connect(self._on_pref_changed)
        self.sp_refresh_minutes = SpinBox(1, 1440, 1, suffix=" 分钟", width=196)
        self.sp_refresh_minutes.valueChanged.connect(self._on_pref_changed)

        self.row_enabled = SettingRow(
            "开启 AI 额度",
            "启用后按设定间隔自动刷新各账号余额与配额",
            self.sw_enabled,
        )
        self.row_refresh = SettingRow(
            "自动刷新间隔",
            "后台定时查询间隔（1–1440 分钟，默认 3 分钟）",
            self.sp_refresh_minutes,
        )

        # ---- 2. 工具栏（唯一主按钮“＋ 添加账号” + 次操作“全部刷新”） ----
        self.toolbar_wrap = QtWidgets.QWidget(self)
        self.btn_add = PillButton("＋ 添加账号", primary=True, parent=self.toolbar_wrap)
        self.btn_add.clicked.connect(lambda: self.open_add_form(Provider.DEEPSEEK.value))

        self.btn_refresh_all = PillButton("全部刷新", primary=False, parent=self.toolbar_wrap)
        self.btn_refresh_all.clicked.connect(self._on_refresh_all_clicked)

        self.lbl_toolbar_status = Label("", 11.5, W.Normal, 2, parent=self.toolbar_wrap)
        self.toolbar_wrap.setLayout(
            hbox(
                self.btn_add,
                self.btn_refresh_all,
                self.lbl_toolbar_status,
                None,
                spacing=10,
                margins=(2, 4, 2, 2),
            )
        )

        # ---- 3. 内联添加/重连账号表单（默认折叠隐藏） ----
        self.add_card = Card(self, radius=14, alpha=1.0)
        self.lbl_form_title = Label("添加 AI 额度账号", 13.5, W.DemiBold, 1, parent=self.add_card)

        self.cmb_provider = ComboBox(
            [(k, PROVIDERS[k].name) for k in PROVIDER_ORDER],
            parent=self.add_card,
            width=196,
        )
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_selected)

        self.ed_alias = LineEdit("例如：DeepSeek 主账号（可选）", parent=self.add_card, width=196)
        self.ed_api_key = PasswordEdit("输入 DeepSeek API Key", parent=self.add_card, width=196)

        self.row_form_provider = SettingRow("服务商", "选择要接入的 AI 服务商", self.cmb_provider)
        self.row_form_alias = SettingRow("账号别名", "用于区分多个账号的自定义名称", self.ed_alias)
        self.row_form_secret = SettingRow("API Key", "仅保存在 Windows 凭据管理器中，绝不写入配置文件", self.ed_api_key)

        self.lbl_unsupported_notice = Label(
            "该服务商连接器将在下一阶段接入，当前暂不可添加。",
            12.0,
            W.Medium,
            3,
            parent=self.add_card,
            wrap=True,
        )
        self.lbl_unsupported_notice.hide()

        self.lbl_form_status = Label("", 11.5, W.Medium, 2, parent=self.add_card, wrap=True)
        self.lbl_form_status.hide()

        self.btn_test_save = PillButton("测试并保存", primary=True, parent=self.add_card)
        self.btn_test_save.clicked.connect(self._on_submit_add_form)
        self.btn_cancel_add = PillButton("取消", primary=False, parent=self.add_card)
        self.btn_cancel_add.clicked.connect(self.close_add_form)

        self.add_card.setLayout(
            vbox(
                self.lbl_form_title,
                self.row_form_provider,
                self.row_form_alias,
                self.row_form_secret,
                self.lbl_unsupported_notice,
                self.lbl_form_status,
                hbox(None, self.btn_cancel_add, self.btn_test_save, spacing=10),
                spacing=8,
                margins=(16, 14, 16, 14),
            )
        )
        self.add_card.hide()

        # ---- 4. 账号列表容器与空状态 ----
        self.empty_card = Card(self, radius=14, alpha=1.0)
        self.lbl_empty_title = Label("还没有 AI 额度账号", 14.0, W.DemiBold, 1, parent=self.empty_card)
        self.lbl_empty_desc = Label(
            "添加 DeepSeek API Key 后即可在本地安全查询可用余额，密钥仅存于系统凭据管理器。",
            11.5,
            W.Normal,
            3,
            parent=self.empty_card,
            wrap=True,
        )
        self.btn_empty_add = PillButton("添加 DeepSeek 账号", primary=True, parent=self.empty_card)
        self.btn_empty_add.clicked.connect(lambda: self.open_add_form(Provider.DEEPSEEK.value))
        self.empty_card.setLayout(
            vbox(
                self.lbl_empty_title,
                self.lbl_empty_desc,
                hbox(self.btn_empty_add, None, spacing=0),
                spacing=8,
                margins=(18, 16, 18, 16),
            )
        )

        self.accounts_wrap = QtWidgets.QWidget(self)
        self.accounts_lay = vbox(spacing=10, margins=(0, 0, 0, 0))
        self.accounts_wrap.setLayout(self.accounts_lay)

        self.finish_layout(
            self.section("AI 额度监控", "管理 AI 服务商账号与自动刷新策略"),
            self.row_enabled,
            self.row_refresh,
            self.section("账号列表", "API Key 仅保存在 Windows 凭据管理器中"),
            self.toolbar_wrap,
            self.add_card,
            self.empty_card,
            self.accounts_wrap,
        )

        self._mgr.test_finished.connect(self._on_mgr_test_finished)
        self._mgr.snapshot_updated.connect(self._on_mgr_snapshot_updated)
        self._mgr.refreshing_changed.connect(self._on_mgr_refreshing_changed)
        self._mgr.all_refreshing_changed.connect(self._on_mgr_all_refreshing_changed)
        theme.changed.connect(self._rebuild_account_cards)

    # ---------------- 响应式宽度与表单交互 ----------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 520
        if narrow != self._narrow_mode:
            self._narrow_mode = narrow
            self._rebuild_account_cards()

    def _on_pref_changed(self, *_):
        if not self._loading:
            self.changed.emit()

    def open_add_form(self, provider: str = Provider.DEEPSEEK.value, reconnect_acc: QuotaAccount | None = None):
        self._reconnect_target_acc = reconnect_acc
        self.ed_api_key.setText("")
        self.lbl_form_status.hide()
        self.lbl_form_status.setText("")
        if reconnect_acc is not None:
            self.lbl_form_title.setText(f"重新连接：{reconnect_acc.alias or PROVIDERS[reconnect_acc.provider].name}")
            self.cmb_provider.set_value(reconnect_acc.provider)
            self.cmb_provider.setEnabled(False)
            self.ed_alias.setText(reconnect_acc.alias)
        else:
            self.lbl_form_title.setText("添加 AI 额度账号")
            self.cmb_provider.setEnabled(True)
            self.cmb_provider.set_value(provider if provider in PROVIDERS else Provider.DEEPSEEK.value)
            self.ed_alias.setText("")
        self._sync_provider_form_fields()
        self.add_card.show()

    def close_add_form(self):
        """关闭或取消添加流程时立即清空输入框中的密钥内存。"""
        self._reconnect_target_acc = None
        self.ed_api_key.setText("")
        self.ed_alias.setText("")
        self.lbl_form_status.hide()
        self.lbl_form_status.setText("")
        self.add_card.hide()

    def _on_provider_selected(self, *_):
        self._sync_provider_form_fields()

    def _sync_provider_form_fields(self):
        prov = str(self.cmb_provider.current_value() or Provider.DEEPSEEK.value)
        spec = PROVIDERS.get(prov, PROVIDERS[Provider.DEEPSEEK.value])
        if spec.implemented:
            self.row_form_alias.show()
            self.row_form_secret.show()
            self.lbl_unsupported_notice.hide()
            self.btn_test_save.setEnabled(not bool(self._active_req_id))
            self.btn_test_save.set_text("测试并保存")
        else:
            self.row_form_alias.hide()
            self.row_form_secret.hide()
            self.ed_api_key.setText("")
            self.lbl_unsupported_notice.setText(f"{spec.name} 连接器将在下一阶段接入，当前暂不可添加。")
            self.lbl_unsupported_notice.show()
            self.btn_test_save.setEnabled(False)
            self.btn_test_save.set_text("下一阶段接入")

    def _show_form_status(self, text: str, is_err: bool = False):
        self.lbl_form_status.set_color(theme.up() if is_err else theme.down())
        self.lbl_form_status.setText(text)
        self.lbl_form_status.show()

    def _on_submit_add_form(self):
        if self._active_req_id:
            return
        prov = str(self.cmb_provider.current_value() or Provider.DEEPSEEK.value)
        if not is_provider_implemented(prov):
            self._show_form_status("连接器将在下一阶段接入", is_err=True)
            return
        secret = self.ed_api_key.text().strip()
        if not secret:
            self._show_form_status("请输入有效的 API Key（不能为空）", is_err=True)
            return
        alias = self.ed_alias.text().strip()
        target_id = self._reconnect_target_acc.id if self._reconnect_target_acc is not None else ""

        started, err, req_id = self._mgr.test_account_async(
            provider=prov,
            secret=secret,
            alias=alias,
            account_id=target_id,
            check_duplicate_in=self._draft_accounts,
        )
        if not started:
            self._show_form_status(err or "无法发起连接测试", is_err=True)
            return

        self._active_req_id = req_id
        self.btn_test_save.setEnabled(False)
        self.btn_test_save.set_text("正在测试…")
        self._show_form_status("正在验证 API Key 并查询余额…", is_err=False)

    @QtCore.Slot(str, bool, str, object)
    def _on_mgr_test_finished(self, req_id: str, ok: bool, err: str, payload: object):
        if req_id != self._active_req_id:
            return
        self._active_req_id = ""
        self._sync_provider_form_fields()

        if not ok or not isinstance(payload, dict):
            self._show_form_status(err or "连接测试未通过，请检查 API Key", is_err=True)
            return

        new_acc: QuotaAccount = payload["account"]
        snap: QuotaSnapshot = payload["snapshot"]
        sec: str = payload.get("secret", "")

        if self._reconnect_target_acc is not None:
            for idx, existing in enumerate(self._draft_accounts):
                if existing.id == self._reconnect_target_acc.id:
                    new_acc.pinned = existing.pinned
                    new_acc.color = existing.color
                    new_acc.enabled = existing.enabled
                    self._draft_accounts[idx] = new_acc
                    break
        else:
            self._draft_accounts.append(new_acc)

        if sec:
            self._pending_secrets[new_acc.id] = sec
        self._draft_snapshots[new_acc.id] = snap

        self.close_add_form()
        self._rebuild_account_cards()
        self.changed.emit()

    # ---------------- 账号卡片操作 ----------------
    def _on_toggle_account_enabled(self, acc: QuotaAccount, enabled: bool):
        acc.enabled = bool(enabled)
        self._rebuild_account_cards()
        self.changed.emit()

    def _on_toggle_account_pinned(self, acc: QuotaAccount):
        acc.pinned = not acc.pinned
        self._rebuild_account_cards()
        self.changed.emit()

    def _on_edit_alias(self, acc: QuotaAccount):
        spec = PROVIDERS.get(acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "修改账号别名",
            f"请输入 {spec.name} 账号的显示别名：",
            QtWidgets.QLineEdit.EchoMode.Normal,
            acc.alias or spec.name,
        )
        if ok:
            acc.alias = text.strip() or spec.name
            self._rebuild_account_cards()
            self.changed.emit()

    def _on_set_color(self, acc: QuotaAccount, color_key: str):
        if color_key in ACCENT_COLORS and acc.color != color_key:
            acc.color = color_key
            self._rebuild_account_cards()
            self.changed.emit()

    def _on_reconnect_account(self, acc: QuotaAccount):
        self.open_add_form(provider=acc.provider, reconnect_acc=acc)

    def _on_delete_account(self, acc: QuotaAccount):
        spec = PROVIDERS.get(acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
        name = acc.alias or spec.name
        if ConfirmDialog.ask(
            self,
            "确认删除 AI 额度账号？",
            f"将从列表中移除「{name}」，并在点击「保存设置」后同步删除 Windows 凭据管理器中的密钥与本地额度缓存。",
            confirm_text="删除账号",
        ):
            self._draft_accounts = [a for a in self._draft_accounts if a.id != acc.id]
            self._pending_secrets.pop(acc.id, None)
            self._draft_snapshots.pop(acc.id, None)
            self._deleted_accounts.append(acc)
            self._rebuild_account_cards()
            self.changed.emit()

    def _on_refresh_single(self, acc: QuotaAccount):
        # 若该账号是尚未保存进 manager 的草稿账号且有暂存密钥，先临时同步进 manager 内存供刷新
        if self._mgr.get_account(acc.id) is None and acc.id in self._pending_secrets:
            self._mgr._creds.save_secret(acc.credential_key, acc.identity or acc.id, self._pending_secrets[acc.id])
            self._mgr._accounts.append(copy.deepcopy(acc))
        self._mgr.refresh_account(acc.id)

    def _on_refresh_all_clicked(self):
        self._mgr.refresh_all()

    @QtCore.Slot(str, object)
    def _on_mgr_snapshot_updated(self, account_id: str, snap: object):
        if isinstance(snap, QuotaSnapshot):
            self._draft_snapshots[account_id] = copy.deepcopy(snap)
            for acc in self._draft_accounts:
                if acc.id == account_id:
                    acc.last_error = snap.last_error
                    if snap.fetched_at > 0:
                        acc.last_refresh_at = snap.fetched_at
                    break
            self._rebuild_account_cards()

    @QtCore.Slot(str, bool)
    def _on_mgr_refreshing_changed(self, _account_id: str, _is_refreshing: bool):
        self._rebuild_account_cards()

    @QtCore.Slot(bool)
    def _on_mgr_all_refreshing_changed(self, any_refreshing: bool):
        self.btn_refresh_all.setEnabled(not any_refreshing)
        self.btn_refresh_all.set_text("正在刷新…" if any_refreshing else "全部刷新")

    def _rebuild_account_cards(self):
        while self.accounts_lay.count():
            item = self.accounts_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()

        if not self._draft_accounts:
            self.empty_card.show()
            self.accounts_wrap.hide()
            self.btn_refresh_all.setEnabled(False)
            return

        self.empty_card.hide()
        self.accounts_wrap.show()
        self.btn_refresh_all.setEnabled(not self._mgr.is_refreshing())

        # 置顶账号优先展示，保持原有相对顺序
        ordered = sorted(self._draft_accounts, key=lambda a: (0 if a.pinned else 1))
        for acc in ordered:
            snap = self._draft_snapshots.get(acc.id) or self._mgr.get_snapshot(acc.id)
            card = _QuotaAccountCard(
                acc=acc,
                snap=snap,
                is_refreshing=self._mgr.is_refreshing(acc.id),
                narrow_mode=self._narrow_mode,
                page=self,
                parent=self.accounts_wrap,
            )
            self._cards.append(card)
            self.accounts_lay.addWidget(card)
            card.show()

    # ---------------- 与 SettingsWindow 保存/取消事务集成 ----------------
    def commit_credentials(self) -> tuple[bool, str]:
        """校验暂存凭据是否完整（实际凭据写入与删除在配置原子保存成功后执行）。"""
        for acc_id, sec in self._pending_secrets.items():
            if not str(sec or "").strip():
                return False, f"账号 {acc_id} 的 API Key 为空"
        return True, ""

    def finalize_commit(self):
        """在 config.json 原子保存成功后，正式写入新增/重连凭据并清理已删除账号的凭据与缓存。"""
        for acc_id, sec in list(self._pending_secrets.items()):
            acc = next((a for a in self._draft_accounts if a.id == acc_id), None)
            if acc is not None and sec:
                self._mgr._creds.save_secret(acc.credential_key, acc.identity or acc.id, sec)
                snap = self._draft_snapshots.get(acc.id)
                if snap is not None:
                    self._mgr._cache.put(snap, flush=True)

        remaining_ids = {a.id for a in self._draft_accounts}
        for del_acc in list(self._deleted_accounts):
            if del_acc.id not in remaining_ids:
                self._mgr.remove_account_after_commit(del_acc.id, del_acc.credential_key)

        self._pending_secrets.clear()
        self._deleted_accounts.clear()
        self._mgr.load_from_config()
        for acc_id, snap in self._draft_snapshots.items():
            if acc_id in remaining_ids:
                self._mgr._snapshots[acc_id] = copy.deepcopy(snap)
        self._mgr._sync_auto_timer()
        self._mgr.accounts_changed.emit()
        self._rebuild_account_cards()

    def discard_pending(self):
        """取消或关闭设置窗口时清空暂存凭据与输入框内存，不修改系统凭据。"""
        self._active_req_id = ""
        self._pending_secrets.clear()
        self._deleted_accounts.clear()
        self.close_add_form()

    def load_from_draft(self, draft: ConfigDraft):
        self._loading = True
        try:
            self.discard_pending()
            self.sw_enabled.setChecked(bool(draft.get("quota.enabled", True)))
            r_min = int(draft.get("quota.refresh_minutes", 3) or 3)
            self.sp_refresh_minutes.setValue(max(1, min(1440, r_min)))

            raw_accs = draft.get("quota.accounts", [])
            loaded: list[QuotaAccount] = []
            if isinstance(raw_accs, list):
                for item in raw_accs:
                    acc = QuotaAccount.from_config_dict(item)
                    if acc is not None:
                        loaded.append(acc)
            self._draft_accounts = loaded
            self._draft_snapshots.clear()
            for acc in self._draft_accounts:
                snap = self._mgr.get_snapshot(acc.id)
                if snap is not None:
                    self._draft_snapshots[acc.id] = snap
            self._rebuild_account_cards()
        finally:
            self._loading = False

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("quota.enabled", bool(self.sw_enabled.isChecked()))
        draft.set("quota.refresh_minutes", int(self.sp_refresh_minutes.value()))
        draft.set("quota.accounts", [a.to_config_dict() for a in self._draft_accounts])
