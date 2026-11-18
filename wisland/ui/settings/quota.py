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
from ..icons import draw_ai_svg_icon
from ..pages.system import styled_menu
from ..theme import G, W, theme, ui_font
from ..widgets import (
    Card,
    ComboBox,
    ConfirmDialog,
    Hoverable,
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


class _ProviderSvgBadge(QtWidgets.QWidget):
    """使用品牌 SVG 绘制的服务商圆底图标（28×28）。"""

    def __init__(self, provider: str, badge: str, color_key: str, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.badge = badge
        self.color_key = color_key
        self.setFixedSize(28, 28)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        if self.provider != Provider.WORKBUDDY.value:
            _label, hex_col = ACCENT_COLORS.get(self.color_key, ACCENT_COLORS["blue"])
            col = QtGui.QColor(hex_col)
            bg = QtGui.QColor(col)
            bg.setAlphaF(0.16 if theme.dark else 0.12)
            p.setPen(QtGui.QPen(theme.text(0.10), 1.0))
            p.setBrush(bg)
            p.drawEllipse(r.adjusted(1.0, 1.0, -1.0, -1.0))
        icon_r = QtCore.QRectF(r.center().x() - 10.0, r.center().y() - 10.0, 20.0, 20.0)
        if not draw_ai_svg_icon(p, icon_r, self.provider, dark=theme.dark):
            _label, hex_col = ACCENT_COLORS.get(self.color_key, ACCENT_COLORS["blue"])
            col = QtGui.QColor(hex_col)
            p.setFont(ui_font(10.0, W.Bold))
            p.setPen(col if not theme.dark else QtGui.QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.badge)


class _ColorDotButton(Hoverable):
    """紧凑强调色圆点按钮。"""

    def __init__(self, color_key: str, name: str, selected: bool, parent=None):
        super().__init__(parent)
        self.color_key = color_key
        self.selected = selected
        _, hex_col = ACCENT_COLORS.get(color_key, ACCENT_COLORS["blue"])
        self._col = QtGui.QColor(hex_col)
        self.setFixedSize(18, 18)
        self.setToolTip(f"强调色：{name}")

    def paintEvent(self, e):
        p = painter(self)
        c = QtCore.QRectF(self.rect()).center()
        if self.selected:
            p.setPen(QtGui.QPen(theme.text(0.75), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 7.5, 7.5)
        elif self._hover.x > 0.05:
            p.setPen(QtGui.QPen(theme.text(0.35), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 7.5, 7.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._col)
        p.drawEllipse(c, 5.0, 5.0)


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


def _summary_text_for_card(acc: QuotaAccount, snap: QuotaSnapshot | None, eff_status: str) -> str:
    """生成折叠态右侧的简短余额/积分/周期摘要；异常或未就绪时返回空串改由状态胶囊承接。"""
    if snap is None or not acc.enabled:
        return ""
    if eff_status in (
        AccountStatus.AUTH_ERROR.value,
        AccountStatus.NETWORK_ERROR.value,
        AccountStatus.PARSE_ERROR.value,
        AccountStatus.RATE_LIMITED.value,
        AccountStatus.UNCONFIGURED.value,
        AccountStatus.UNSUPPORTED.value,
    ):
        return ""
    bal = snap.primary_balance()
    if bal is not None:
        return f"余额 {_currency_symbol(bal.currency)}{bal.total_balance:.2f}"
    if snap.credits is not None:
        return f"积分 {float(snap.credits):.2f}"
    if snap.windows:
        first_w = next((w for w in snap.windows if w.currency != "EXTRA"), snap.windows[0])
        if first_w.remaining_percent is not None:
            return f"{first_w.label} {first_w.remaining_percent:.0f}%"
    return ""


class _QuotaAccountCard(Card):
    """单账号设置卡片（对齐 Daozai：默认折叠摘要 + 详情/收起展开 + 直接移除/行内改名/常驻岛上/重新连接）。"""

    def __init__(
        self,
        acc: QuotaAccount,
        snap: QuotaSnapshot | None,
        is_refreshing: bool,
        narrow_mode: bool,
        expanded: bool,
        page: QuotaSettingsPage,
        parent=None,
    ):
        super().__init__(parent, radius=14, alpha=1.0)
        self.acc = acc
        self.snap = snap
        self.expanded = bool(expanded)
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

        # ---- 第一层（折叠/展开共用标题行）：品牌 SVG 图标 + 别名 + 服务商名称 + 右侧摘要/状态胶囊 + 详情/收起 ----
        self.badge_icon = _ProviderSvgBadge(acc.provider, spec.badge, acc.color, self)
        alias_txt = acc.alias.strip() or spec.name
        if acc.pinned:
            alias_txt = f"★ {alias_txt}"
        self.lbl_alias = Label(alias_txt, 14.0, W.DemiBold, 1, parent=self)
        self.lbl_provider = Label(spec.name, 11.5, W.Medium, 2, parent=self)
        self.status_pill = _StatusPill(eff_status, extra_tip=err_txt, parent=self)

        summary_txt = _summary_text_for_card(acc, snap, eff_status)
        self.lbl_summary = Label(summary_txt, 12.0, W.DemiBold, 1, num=True, parent=self)
        if summary_txt and not err_txt and not is_refreshing:
            self.status_pill.hide()
            self.lbl_summary.show()
        else:
            self.lbl_summary.hide()
            self.status_pill.show()

        self.btn_toggle_detail = PillButton("收起" if self.expanded else "详情", height=26, parent=self)
        self.btn_toggle_detail.setToolTip("收起账号详情" if self.expanded else "展开查看额度明细与操作")
        self.btn_toggle_detail.clicked.connect(lambda: self.page._toggle_card_expanded(self.acc.id))

        row1 = hbox(
            self.badge_icon,
            self.lbl_alias,
            self.lbl_provider,
            None,
            self.lbl_summary,
            self.status_pill,
            self.btn_toggle_detail,
            spacing=8,
        )

        # ---- 展开详情区 ----
        self.detail_wrap = QtWidgets.QWidget(self)
        self.detail_wrap.setVisible(self.expanded)

        # 1) 名称行：名称输入框（行内直接编辑） + 直接可见的“移除”按钮
        lbl_name_k = Label("名称", 11.5, W.Normal, 2, parent=self.detail_wrap)
        self.ed_alias_inline = LineEdit("账号名称", parent=self.detail_wrap, width=220)
        self.ed_alias_inline.setText(acc.alias or spec.name)
        self.ed_alias_inline.setToolTip("直接修改账号显示名称")
        self.ed_alias_inline.editingFinished.connect(self._on_inline_alias_edited)

        self.btn_remove = PillButton("移除", height=26, parent=self.detail_wrap)
        self.btn_remove.setToolTip("从列表中移除该账号（保存设置后同步清理系统凭据）")
        self.btn_remove.clicked.connect(lambda: self.page._on_delete_account(self.acc))

        row_name = hbox(lbl_name_k, (self.ed_alias_inline, 1), self.btn_remove, spacing=8)

        # 2) 额度明细展示区
        self.body_wrap = QtWidgets.QWidget(self.detail_wrap)
        if snap is not None and snap.primary_balance() is not None:
            bal = snap.primary_balance()
            assert bal is not None
            sym = _currency_symbol(bal.currency)
            self.lbl_main_bal = Label(
                f"{sym} {bal.total_balance:.2f}",
                20.0,
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
        elif snap is not None and snap.credits is not None:
            credits_val = float(snap.credits)
            self.lbl_main_bal = Label(
                f"积分 {credits_val:.2f}",
                20.0,
                W.Bold,
                1,
                num=True,
                parent=self.body_wrap,
            )
            pkg_parts = [
                f"{w.label} {float(w.amount):.2f}"
                for w in snap.windows
                if w.amount is not None
            ]
            sub_txt = "   ·   ".join(pkg_parts[:3]) if pkg_parts else (snap.plan_name or "可用积分")
            self.lbl_sub_bal = Label(sub_txt, 11.5, W.Normal, 2, num=True, parent=self.body_wrap)
            if narrow_mode:
                self.body_wrap.setLayout(vbox(self.lbl_main_bal, self.lbl_sub_bal, spacing=2, margins=(0, 2, 0, 2)))
            else:
                self.body_wrap.setLayout(
                    hbox(self.lbl_main_bal, self.lbl_sub_bal, None, spacing=12, margins=(0, 2, 0, 2))
                )
        elif snap is not None and snap.windows:
            main_wins = [w for w in snap.windows if w.currency != "EXTRA"]
            extra_wins = [w for w in snap.windows if w.currency == "EXTRA"]
            if not main_wins:
                main_wins = list(snap.windows)
            plan_txt = f"{spec.name} · {snap.plan_name}" if snap.plan_name else spec.name
            first_w = main_wins[0]
            pct_str = f"{first_w.remaining_percent:.0f}%" if first_w.remaining_percent is not None else "--%"
            self.lbl_main_bal = Label(
                f"{first_w.label} 剩余 {pct_str}",
                16.5,
                W.Bold,
                1,
                num=True,
                parent=self.body_wrap,
            )
            parts = [plan_txt]
            for w in main_wins[1:3]:
                p_s = f"{w.remaining_percent:.0f}%" if w.remaining_percent is not None else "--%"
                parts.append(f"{w.label} {p_s}")
            if extra_wins:
                parts.append(f"额外额度 {len(extra_wins)} 项")
            sub_txt = "   ·   ".join(parts)
            self.lbl_sub_bal = Label(sub_txt, 11.5, W.Normal, 2, num=True, parent=self.body_wrap)
            if narrow_mode:
                self.body_wrap.setLayout(vbox(self.lbl_main_bal, self.lbl_sub_bal, spacing=2, margins=(0, 2, 0, 2)))
            else:
                self.body_wrap.setLayout(
                    hbox(self.lbl_main_bal, self.lbl_sub_bal, None, spacing=12, margins=(0, 2, 0, 2))
                )
        else:
            lbl_wait = Label("等待首次刷新", 13.0, W.Medium, 3, parent=self.body_wrap)
            self.body_wrap.setLayout(hbox(lbl_wait, None, spacing=8, margins=(0, 2, 0, 2)))

        # 3) 错误信息或更新时间
        self.lbl_meta = QtWidgets.QLabel(self.detail_wrap)
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

        # 4) 操作行：监控 Switch + 常驻岛上（置顶） + 刷新/重试 + 重新连接（始终可用）
        self.sw_monitor = Switch(checked=acc.enabled, parent=self.detail_wrap)
        self.sw_monitor.setToolTip("开启或关闭该账号的额度监控")
        self.sw_monitor.toggled.connect(lambda on: self.page._on_toggle_account_enabled(self.acc, on))
        lbl_mon = Label("监控", 11.5, W.Normal, 2, parent=self.detail_wrap)

        self.btn_pin = PillButton("已常驻岛上" if acc.pinned else "常驻岛上", height=26, parent=self.detail_wrap)
        self.btn_pin.setToolTip("在岛内页面优先显示该账号")
        self.btn_pin.clicked.connect(lambda: self.page._on_toggle_account_pinned(self.acc))

        refresh_label = "重试" if is_error_state else "刷新"
        self.btn_refresh = PillButton(refresh_label, height=26, parent=self.detail_wrap)
        self.btn_refresh.setEnabled(spec.implemented and not is_refreshing)
        self.btn_refresh.clicked.connect(lambda: self.page._on_refresh_single(self.acc))

        self.btn_reconnect = PillButton("重新连接", height=26, parent=self.detail_wrap)
        self.btn_reconnect.setToolTip("重新授权或更换当前账号凭据")
        self.btn_reconnect.clicked.connect(lambda: self.page._on_reconnect_account(self.acc))
        self.btn_reconnect.setVisible(bool(spec.implemented))

        if narrow_mode:
            row4 = vbox(
                hbox(lbl_mon, self.sw_monitor, self.btn_pin, None, spacing=8),
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
                spacing=8,
            )

        # 5) 紧凑强调色圆点行
        lbl_col = Label("颜色", 11.0, W.Normal, 3, parent=self.detail_wrap)
        color_dots = []
        for c_key, (c_name, _hex) in ACCENT_COLORS.items():
            dot = _ColorDotButton(c_key, c_name, selected=(acc.color == c_key), parent=self.detail_wrap)
            dot.clicked.connect(lambda _=False, ck=c_key: self.page._on_set_color(self.acc, ck))
            color_dots.append(dot)
        row_colors = hbox(lbl_col, *color_dots, None, spacing=6)

        self.detail_wrap.setLayout(
            vbox(
                row_name,
                self.body_wrap,
                self.lbl_meta,
                row4,
                row_colors,
                spacing=8,
                margins=(0, 6, 0, 0),
            )
        )

        self.setLayout(
            vbox(
                row1,
                self.detail_wrap,
                spacing=4,
                margins=(16, 12, 16, 12),
            )
        )

    def _on_inline_alias_edited(self):
        spec = PROVIDERS.get(self.acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
        new_alias = self.ed_alias_inline.text().strip() or spec.name
        if new_alias != self.acc.alias:
            self.acc.alias = new_alias
            prefix = "★ " if self.acc.pinned else ""
            self.lbl_alias.setText(f"{prefix}{new_alias}")
            self.page.changed.emit()


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
        self._preserved_unimplemented_accounts: list[QuotaAccount] = []
        self._draft_snapshots: dict[str, QuotaSnapshot] = {}
        self._pending_secrets: dict[str, str] = {}
        self._deleted_accounts: list[QuotaAccount] = []
        self._expanded_ids: set[str] = set()
        self._gemini_manual_cookie: bool = False
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
            [(k, PROVIDERS[k].name) for k in PROVIDER_ORDER if is_provider_implemented(k)],
            parent=self.add_card,
            width=196,
        )
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_selected)

        self.ed_alias = LineEdit("例如：主账号（可选）", parent=self.add_card, width=196)
        self.ed_email = LineEdit("例如：user@gmail.com", parent=self.add_card, width=196)
        self.ed_api_key = PasswordEdit("输入 DeepSeek API Key", parent=self.add_card, width=196)

        self.row_form_provider = SettingRow("服务商", "选择要接入的 AI 服务商", self.cmb_provider)
        self.row_form_alias = SettingRow("账号别名", "用于区分多个账号的自定义名称", self.ed_alias)
        self.row_form_email = SettingRow("Google 邮箱", "用于识别并绑定对应 Google 账号", self.ed_email)
        self.row_form_secret = SettingRow("API Key", "仅保存在 Windows 凭据管理器中，绝不写入配置文件", self.ed_api_key)
        self.row_form_email.hide()

        self.lbl_form_status = Label("", 11.5, W.Medium, 2, parent=self.add_card, wrap=True)
        self.lbl_form_status.hide()

        self.btn_gemini_mode = PillButton("改用手动粘贴 Cookie", height=26, primary=False, parent=self.add_card)
        self.btn_gemini_mode.clicked.connect(self._toggle_gemini_manual_mode)
        self.btn_gemini_mode.hide()

        self.btn_test_save = PillButton("测试并保存", primary=True, parent=self.add_card)
        self.btn_test_save.clicked.connect(self._on_submit_add_form)
        self.btn_cancel_add = PillButton("取消", primary=False, parent=self.add_card)
        self.btn_cancel_add.clicked.connect(self.close_add_form)

        self.add_card.setLayout(
            vbox(
                self.lbl_form_title,
                self.row_form_provider,
                self.row_form_alias,
                self.row_form_email,
                self.row_form_secret,
                self.lbl_form_status,
                hbox(self.btn_gemini_mode, None, self.btn_cancel_add, self.btn_test_save, spacing=10),
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

    def focus_account(self, account_id: str):
        """从岛内“重新连接”跳转过来时定位到对应账号、展开详情并打开重连表单。"""
        target = next((a for a in self._draft_accounts if a.id == account_id), None)
        if target is not None:
            self._expanded_ids.add(target.id)
            self._rebuild_account_cards()
            self.open_add_form(provider=target.provider, reconnect_acc=target)

    def _toggle_card_expanded(self, account_id: str):
        if account_id in self._expanded_ids:
            self._expanded_ids.discard(account_id)
        else:
            self._expanded_ids.add(account_id)
        self._rebuild_account_cards()

    def _toggle_gemini_manual_mode(self):
        self._gemini_manual_cookie = not self._gemini_manual_cookie
        self.ed_api_key.setText("")
        self._sync_provider_form_fields()

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
            impl_provs = [k for k in PROVIDER_ORDER if is_provider_implemented(k)]
            default_prov = provider if (provider in impl_provs) else (impl_provs[0] if impl_provs else Provider.DEEPSEEK.value)
            self.cmb_provider.set_value(default_prov)
            self.ed_alias.setText("")
        self._sync_provider_form_fields()
        self.add_card.show()

    def close_add_form(self):
        """关闭或取消添加流程时立即中止在途验证并清空输入框中的密钥内存。"""
        if self._active_req_id:
            self._mgr.cancel_test(self._active_req_id)
            self._active_req_id = ""
        self._reconnect_target_acc = None
        self.ed_api_key.setText("")
        self.ed_email.setText("")
        self.ed_alias.setText("")
        self.lbl_form_status.hide()
        self.lbl_form_status.setText("")
        self.add_card.hide()

    def _on_provider_selected(self, *_):
        self.ed_api_key.setText("")
        self.ed_email.setText("")
        self._sync_provider_form_fields()

    def _sync_provider_form_fields(self):
        prov = str(self.cmb_provider.current_value() or Provider.DEEPSEEK.value)
        spec = PROVIDERS.get(prov, PROVIDERS[Provider.DEEPSEEK.value])
        self.row_form_alias.show()
        is_deepseek = prov == Provider.DEEPSEEK.value
        is_gemini = prov == Provider.GEMINI.value
        show_gemini_manual = is_gemini and self._gemini_manual_cookie
        self.row_form_email.setVisible(is_gemini)
        self.row_form_secret.setVisible(is_deepseek or show_gemini_manual)
        self.btn_gemini_mode.setVisible(is_gemini)
        self.btn_gemini_mode.setMaximumWidth(200)
        self.btn_gemini_mode.set_text("改用浏览器自动登录" if self._gemini_manual_cookie else "改用手动粘贴 Cookie")
        if is_gemini:
            self.ed_email.setPlaceholderText("可选，留空自动识别登录邮箱" if not self._gemini_manual_cookie else "例如：user@gmail.com")
            self.row_form_secret.title_lbl.setText("Cookie 会话")
            self.ed_api_key.edit.setPlaceholderText("粘贴含 SID 的 Cookie 请求头")
        elif is_deepseek:
            self.row_form_secret.title_lbl.setText("API Key")
            self.ed_api_key.edit.setPlaceholderText("输入 DeepSeek API Key")
        else:
            self.ed_api_key.setText("")

        if not (is_deepseek or is_gemini):
            self.lbl_form_status.set_color(theme.text3())
            self.lbl_form_status.setText(spec.auth_hint)
            self.lbl_form_status.show()
        elif is_gemini and not self._active_req_id:
            self.lbl_form_status.set_color(theme.text3())
            hint = (
                spec.auth_hint
                if self._gemini_manual_cookie
                else "点击右侧按钮将打开独立临时浏览器窗口，登录后自动提取会话并立即关闭。"
            )
            self.lbl_form_status.setText(hint)
            self.lbl_form_status.show()
        elif not self._active_req_id:
            self.lbl_form_status.hide()
        self.btn_test_save.setMaximumWidth(260)
        self.btn_test_save.setEnabled(spec.implemented and not bool(self._active_req_id))
        if is_deepseek or show_gemini_manual:
            self.btn_test_save.set_text("测试并保存")
        elif is_gemini:
            self.btn_test_save.set_text("浏览器登录 Gemini")
        else:
            self.btn_test_save.set_text(f"连接 {spec.name}")

    def _show_form_status(self, text: str, is_err: bool = False):
        self.lbl_form_status.set_color(theme.up() if is_err else theme.down())
        self.lbl_form_status.setText(text)
        self.lbl_form_status.show()

    def _on_submit_add_form(self):
        if self._active_req_id:
            return
        prov = str(self.cmb_provider.current_value() or Provider.DEEPSEEK.value)
        if not is_provider_implemented(prov):
            self._show_form_status("该服务商尚未接入", is_err=True)
            return
        secret = self.ed_api_key.text().strip()
        source = ""
        if prov == Provider.DEEPSEEK.value and not secret:
            self._show_form_status("请输入有效的 API Key（不能为空）", is_err=True)
            return
        if prov == Provider.GEMINI.value:
            source = self.ed_email.text().strip()
            if self._gemini_manual_cookie or secret:
                if not source:
                    self._show_form_status("请填写 Google 账号邮箱", is_err=True)
                    return
                if not secret:
                    self._show_form_status("请粘贴含 Google 登录会话的 Cookie", is_err=True)
                    return
        alias = self.ed_alias.text().strip()
        target_id = self._reconnect_target_acc.id if self._reconnect_target_acc is not None else ""

        started, err, req_id = self._mgr.test_account_async(
            provider=prov,
            secret=secret,
            alias=alias,
            source=source,
            account_id=target_id,
            check_duplicate_in=self._draft_accounts,
        )
        if not started:
            self._show_form_status(err or "无法发起连接测试", is_err=True)
            return

        self._active_req_id = req_id
        self.btn_test_save.setEnabled(False)
        self.btn_test_save.set_text("正在连接…" if prov != Provider.DEEPSEEK.value else "正在测试…")
        status_hint = (
            "正在验证 API Key 并查询余额…"
            if prov == Provider.DEEPSEEK.value
            else f"正在连接 {PROVIDERS[prov].name}，请在浏览器完成授权（可随时点击取消）…"
        )
        self._show_form_status(status_hint, is_err=False)

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
        self._expanded_ids.add(new_acc.id)

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
            if not self._mgr._creds.save_secret(acc.credential_key, acc.identity or acc.id, self._pending_secrets[acc.id]):
                self.lbl_toolbar_status.set_color(theme.up())
                self.lbl_toolbar_status.setText("会话凭据保存失败，未写入系统凭据管理器")
                return
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
                expanded=(acc.id in self._expanded_ids),
                page=self,
                parent=self.accounts_wrap,
            )
            self._cards.append(card)
            self.accounts_lay.addWidget(card)
            card.show()

    # ---------------- 与 SettingsWindow 保存/取消事务集成 ----------------
    def has_pending_changes(self) -> bool:
        return bool(self._pending_secrets) or bool(self._deleted_accounts)

    def commit_credentials(self) -> tuple[bool, str]:
        """校验暂存凭据是否完整（实际凭据写入与删除在配置原子保存成功后执行）。"""
        for acc_id, sec in self._pending_secrets.items():
            if not str(sec or "").strip():
                return False, f"账号 {acc_id} 的 API Key 为空"
        return True, ""

    def finalize_commit(self) -> tuple[bool, str]:
        """在 config.json 原子保存成功后，正式写入新增/重连凭据并清理已删除账号的凭据与缓存。"""
        saved_ids: list[str] = []
        for acc_id, sec in list(self._pending_secrets.items()):
            acc = next((a for a in self._draft_accounts if a.id == acc_id), None)
            if acc is None or not sec:
                saved_ids.append(acc_id)
                continue
            ok = bool(self._mgr._creds.save_secret(acc.credential_key, acc.identity or acc.id, sec))
            if not ok:
                # 保留未保存成功的暂存凭据，不把对应快照标记为已保存，也不触发后续自动刷新
                for sid in saved_ids:
                    self._pending_secrets.pop(sid, None)
                win_err = getattr(self._mgr._creds, "last_error", 0)
                detail = f" (WinError {win_err})" if win_err else ""
                return False, f"会话凭据保存失败，未写入系统凭据管理器{detail}"
            saved_ids.append(acc_id)
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
        return True, ""

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
            preserved: list[QuotaAccount] = []
            if isinstance(raw_accs, list):
                for item in raw_accs:
                    acc = QuotaAccount.from_config_dict(item)
                    if acc is not None:
                        if is_provider_implemented(acc.provider):
                            loaded.append(acc)
                        else:
                            preserved.append(acc)
            self._draft_accounts = loaded
            self._preserved_unimplemented_accounts = preserved
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
        all_accs = list(self._draft_accounts) + list(self._preserved_unimplemented_accounts)
        draft.set("quota.accounts", [a.to_config_dict() for a in all_accs])
