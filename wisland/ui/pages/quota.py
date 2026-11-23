# -*- coding: utf-8 -*-
"""岛内 AI 额度展示页（key="quota"，固定 419×176）：
- 顶部 Header（22px）：页面标题、严格居中日期时间、单账号刷新（带忙碌旋转）、跳转设置 quota 分类
- 账号切换行（22px）：置顶星标优先、强调色淡底高亮、横向滚动（有余量时消费滚轮，不误切页）、键盘 Tab/Enter 切换
- 透明内容区（无灰色大卡片、无多余纵向滚动条）：直接在岛体表面呈现图标、可用余额、充值/赠送、其他币种与更新时间
"""
from __future__ import annotations

import datetime as dt
import math
import time

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg
from ...services.quota import (
    ACCENT_COLORS,
    AccountStatus,
    PROVIDERS,
    Provider,
    QuotaAccount,
    QuotaBalance,
    QuotaSnapshot,
    QuotaWindow,
    is_provider_implemented,
)
from ..header import PageHeader
from ..icons import draw_ai_svg_icon
from ..theme import G, W, theme, ui_font
from ..widgets import Hoverable, IconButton, Label, PillButton, WheelScrollArea, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt


def _accent_qcolor(color_key: str) -> QtGui.QColor:
    _, hex_col = ACCENT_COLORS.get(color_key, ACCENT_COLORS["blue"])
    return QtGui.QColor(hex_col)


def _finite_nonneg(val: float | None) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return max(0.0, f)


def _format_currency(currency: str, amount: float | None) -> str:
    cur = str(currency or "").strip().upper()
    v = _finite_nonneg(amount)
    num_str = f"{v:.2f}" if v is not None else "--"
    if cur == "CNY":
        return f"¥{num_str}"
    if cur == "USD":
        return f"${num_str}"
    if cur == "EUR":
        return f"€{num_str}"
    return f"{cur} {num_str}".strip()


def _format_hhmm(ts: float) -> str:
    if not ts or ts <= 0:
        return "--:--"
    try:
        return dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except Exception:
        return "--:--"


def _big_amount_font_px(text: str) -> float:
    """根据金额文本长度自适应主数字字号，避免大额数字挤压右侧充值/赠送明细。"""
    n = len(text or "")
    if n <= 7:
        return 23.0
    if n <= 10:
        return 19.0
    return 16.0


class _SpinRefreshButton(IconButton):
    """支持刷新忙碌旋转反馈的紧凑图标按钮。"""

    def __init__(self, glyph: str, size: int = 20, icon_px: int = 10, tip: str = "", parent=None):
        super().__init__(glyph, size=size, icon_px=icon_px, tip=tip, parent=parent)
        self._spinning = False
        self._angle = 0.0
        self._spin_timer = QtCore.QTimer(self, interval=32, timeout=self._on_spin)

    def set_spinning(self, on: bool):
        on = bool(on)
        if on == self._spinning:
            return
        self._spinning = on
        self.setEnabled(not on)
        if on:
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self._angle = 0.0
        self.update()

    @QtCore.Slot()
    def _on_spin(self):
        self._angle = (self._angle + 18.0) % 360.0
        self.update()

    def paintEvent(self, e):
        if not self._spinning:
            super().paintEvent(e)
            return
        p = painter(self)
        c = QtCore.QRectF(self.rect()).center()
        p.translate(c)
        p.rotate(self._angle)
        p.translate(-c)
        from ..theme import icon_font
        p.setFont(icon_font(self.icon_px))
        p.setPen(theme.on_color())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph)


class _AccountChip(Hoverable):
    """账号切换横向胶囊标签（完整显示短名称，过长别名内部省略并保留 Tooltip）。"""

    selected_clicked = QtCore.Signal(str)  # account_id

    def __init__(self, acc: QuotaAccount, active: bool = False, parent=None):
        super().__init__(parent)
        self.acc = acc
        self.active = bool(active)
        spec = PROVIDERS.get(acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
        raw_name = acc.alias.strip() or spec.name
        self.display_text = f"★ {raw_name}" if acc.pinned else raw_name
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(20)

        # 用最宽的 DemiBold 字重测算宽度，确保选中加粗后短名称（如“★ 主力”）不被省略。
        fm = QtGui.QFontMetrics(ui_font(10.5, W.DemiBold))
        text_w = fm.horizontalAdvance(self.display_text)
        # 7px 左内距 + 13px 图标 + 4px 间距 + text_w + 9px 右内距 + 2px 余量
        w = text_w + 35
        self.setFixedWidth(max(52, min(140, w)))
        self.setToolTip(f"{spec.name} · {raw_name}")
        self.clicked.connect(lambda: self.selected_clicked.emit(self.acc.id))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected_clicked.emit(self.acc.id)
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = _accent_qcolor(self.acc.color)

        if self.active:
            bg = QtGui.QColor(accent)
            bg.setAlphaF(0.22 if theme.dark else 0.16)
            bd = QtGui.QColor(accent)
            bd.setAlphaF(0.45 if theme.dark else 0.35)
            p.setPen(QtGui.QPen(bd, 0.9))
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        elif self._hover.x > 0.02:
            bg = QtGui.QColor(theme.hover())
            bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)

        if self.hasFocus():
            p.setPen(QtGui.QPen(accent, 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)

        # 左侧 13×13 品牌 SVG 图标
        icon_r = QtCore.QRectF(r.left() + 6.5, r.center().y() - 6.5, 13.0, 13.0)
        if not draw_ai_svg_icon(p, icon_r, self.acc.provider, dark=theme.dark):
            spec = PROVIDERS.get(self.acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
            p.setFont(ui_font(8.0, W.Bold))
            p.setPen(accent)
            p.drawText(icon_r, Qt.AlignmentFlag.AlignCenter, spec.badge)

        f = ui_font(10.5, W.DemiBold if self.active else W.Normal)
        p.setFont(f)
        if self.active:
            fg = accent.lighter(142) if theme.dark else accent.darker(125)
        else:
            fg = theme.text2()
        p.setPen(fg)
        txt_r = QtCore.QRectF(icon_r.right() + 4.0, r.top(), max(10.0, r.right() - icon_r.right() - 8.0), r.height())
        fm = QtGui.QFontMetrics(f)
        p.drawText(
            txt_r,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(self.display_text, Qt.TextElideMode.ElideRight, int(txt_r.width())),
        )


class _ProviderCircleIcon(QtWidgets.QWidget):
    """左侧 54×54 图标承载区：48px 主题玻璃圆底 + 26px 品牌 SVG 图标。"""

    def __init__(self, provider: str = "deepseek", parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setFixedSize(54, 54)

    def set_provider(self, provider: str):
        if provider != self.provider:
            self.provider = provider
            self.update()

    def paintEvent(self, e):
        p = painter(self)
        c = QtCore.QRectF(self.rect()).center()
        circle_r = QtCore.QRectF(c.x() - 24.0, c.y() - 24.0, 48.0, 48.0)
        p.setPen(QtGui.QPen(theme.text(0.10), 1.0))
        p.setBrush(theme.card(0.95 if theme.dark else 0.85))
        p.drawEllipse(circle_r)

        icon_r = QtCore.QRectF(c.x() - 13.0, c.y() - 13.0, 26.0, 26.0)
        if not draw_ai_svg_icon(p, icon_r, self.provider, dark=theme.dark):
            spec = PROVIDERS.get(self.provider, PROVIDERS[Provider.DEEPSEEK.value])
            p.setFont(ui_font(13.0, W.Bold))
            p.setPen(theme.text())
            p.drawText(icon_r, Qt.AlignmentFlag.AlignCenter, spec.badge)


class _SideSwitchButton(Hoverable):
    """主内容两侧的极简左右账号切换按钮（仅多账号时显示）。"""

    def __init__(self, direction: str = "prev", tip: str = "", parent=None):
        super().__init__(parent)
        self._dir = direction
        self.setFixedSize(16, 48)
        self.setToolTip(tip)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        if self._hover.x > 0.02:
            bg = QtGui.QColor(theme.hover())
            bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r.adjusted(1, 10, -1, -10), 6.0, 6.0)

        p.setFont(ui_font(12.0, W.DemiBold))
        p.setPen(theme.text() if self._hover.x > 0.3 else theme.text3())
        ch = "‹" if self._dir == "prev" else "›"
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, ch)


class _WindowProgressRow(QtWidgets.QWidget):
    """通用周期额度进度条行：左侧周期名 + 右侧剩余% + 下方 2.5px 强调色进度条。"""

    def __init__(self, win: QuotaWindow, color_key: str, parent=None):
        super().__init__(parent)
        self.win = win
        self.color_key = color_key
        self.setFixedHeight(20)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        pct = _finite_nonneg(self.win.remaining_percent)
        pct_clamped = max(0.0, min(100.0, pct)) if pct is not None else 0.0

        p.setFont(ui_font(10.0, W.Medium))
        p.setPen(theme.text())
        fm = QtGui.QFontMetrics(p.font())
        lbl_rect = QtCore.QRectF(r.left(), r.top(), r.width() * 0.56, 14.0)
        p.drawText(
            lbl_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(self.win.label or "周期额度", Qt.TextElideMode.ElideRight, int(lbl_rect.width())),
        )

        pct_txt = f"剩余 {pct_clamped:.0f}%" if pct is not None else "剩余 --%"
        p.setFont(ui_font(9.5, W.DemiBold, num=True))
        p.setPen(theme.text2())
        p.drawText(QtCore.QRectF(r.left() + r.width() * 0.50, r.top(), r.width() * 0.50, 14.0),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pct_txt)

        bar_r = QtCore.QRectF(r.left(), r.bottom() - 3.0, r.width(), 2.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.text(0.14))
        p.drawRoundedRect(bar_r, 1.25, 1.25)

        if pct_clamped > 0:
            fill_w = max(3.0, bar_r.width() * (pct_clamped / 100.0))
            fill_r = QtCore.QRectF(bar_r.left(), bar_r.top(), fill_w, bar_r.height())
            p.setBrush(_accent_qcolor(self.color_key))
            p.drawRoundedRect(fill_r, 1.25, 1.25)


class QuotaPage(Page):
    """灵动岛内 AI 额度展示页（419×176 固定尺寸，无灰色大卡片，直接落在岛体表面）。"""

    key, glyph, title, height, panel_width, panel_height = "quota", G.CPU, "AI 额度", 134, 419, 176

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self._selected_account_id: str | None = None
        self._chip_widgets: list[_AccountChip] = []
        self._ordered_ids: list[str] = []

        # 1. 顶部 Header（高 22px）
        self.header = PageHeader(self.ctx, self, show_gear=False, height=22)
        self.lbl_title = Label("AI 额度", 11.5, W.DemiBold, 1, parent=self)
        self.btn_refresh = _SpinRefreshButton(G.REFRESH, size=20, icon_px=10, tip="刷新当前账号", parent=self)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        self.btn_gear = IconButton(G.GEAR, size=20, icon_px=10, tip="打开 AI 额度设置", parent=self)
        self.btn_gear.clicked.connect(lambda: self._open_quota_settings())

        self.header.add_left(self.lbl_title)
        self.header.add_right(self.btn_refresh)
        self.header.add_right(self.btn_gear)

        # 2. 账号切换行（高 22px，横向滚动消费滚轮）
        self.chip_scroll = WheelScrollArea(self, orientation="horizontal")
        self.chip_scroll.setFixedHeight(22)
        self.chip_lay = hbox(spacing=6, margins=(0, 0, 0, 0))
        self.chip_scroll.inner.setLayout(self.chip_lay)
        self.chip_scroll.hide()

        # 3. 透明内容区（不使用 Card，不包裹多余垂直 QScrollArea）
        self.content_host = QtWidgets.QWidget(self)
        self.content_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.content_host.setStyleSheet("background:transparent;border:none;")
        self.content_lay = vbox(spacing=2, margins=(2, 2, 2, 0))
        self.content_host.setLayout(self.content_lay)

        self.setLayout(vbox(self.header, self.chip_scroll, (self.content_host, 1), spacing=4, margins=(0, 0, 0, 0)))

        mgr = self._manager()
        if mgr is not None:
            mgr.accounts_changed.connect(self._on_mgr_updated)
            mgr.snapshot_updated.connect(self._on_mgr_snapshot)
            mgr.account_state_changed.connect(self._on_mgr_account_state)
            mgr.refreshing_changed.connect(self._on_mgr_refreshing)
            mgr.all_refreshing_changed.connect(self._on_mgr_all_refreshing)
        self._theme_dirty = False
        cfg.changed.connect(self.refresh)
        theme.changed.connect(self._on_theme_changed)
        self.refresh()

    def _on_theme_changed(self):
        if self.isVisible():
            self._theme_dirty = False
            self.refresh()
        else:
            self._theme_dirty = True

    def _manager(self):
        return getattr(self.ctx, "quota", None) if self.ctx is not None else None

    def has_connected_accounts(self) -> bool:
        mgr = self._manager()
        if mgr is None:
            return False
        return bool(mgr.has_connected_accounts())

    def should_show(self) -> bool:
        return bool(cfg.get("quota.enabled", True)) and self.has_connected_accounts()

    def _open_quota_settings(self, account_id: str | None = None):
        if self.ctx is None or not hasattr(self.ctx, "open_settings"):
            return
        try:
            self.ctx.open_settings("quota", account_id=account_id)
        except TypeError:
            self.ctx.open_settings("quota")

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        self.refresh()
        self._maybe_refresh_on_show()

    def _maybe_refresh_on_show(self):
        """仅在快照为空或超过刷新间隔且未刷新时发起一次刷新，绝不每次切页无条件请求。"""
        if not bool(cfg.get("quota.enabled", True)):
            return
        mgr = self._manager()
        if mgr is None or not self._selected_account_id:
            return
        acc = mgr.get_account(self._selected_account_id)
        if acc is None or not acc.enabled or not is_provider_implemented(acc.provider):
            return
        if mgr.is_refreshing(acc.id):
            return
        snap = mgr.get_snapshot(acc.id)
        if snap is None or not snap.has_valid_data() or snap.status == AccountStatus.UNCONFIGURED.value:
            mgr.refresh_account(acc.id)
            return
        mins = max(1, min(1440, int(cfg.get("quota.refresh_minutes", 3) or 3)))
        if snap.fetched_at <= 0 or (time.time() - snap.fetched_at) >= mins * 60:
            mgr.refresh_account(acc.id)

    def _ordered_accounts(self) -> list[QuotaAccount]:
        mgr = self._manager()
        if mgr is None:
            return []
        return mgr.list_connected_accounts()

    def _resolve_selected_account(self, ordered: list[QuotaAccount]) -> QuotaAccount | None:
        if not ordered:
            self._selected_account_id = None
            return None
        if self._selected_account_id:
            for a in ordered:
                if a.id == self._selected_account_id:
                    return a
        self._selected_account_id = ordered[0].id
        return ordered[0]

    def select_account(self, account_id: str):
        """仅切换当前展示账号，绝不触发网络请求。"""
        if account_id != self._selected_account_id:
            self._selected_account_id = account_id
            self.refresh()

    def _step_account(self, delta: int):
        if len(self._ordered_ids) <= 1 or self._selected_account_id not in self._ordered_ids:
            return
        idx = self._ordered_ids.index(self._selected_account_id)
        nxt = (idx + delta) % len(self._ordered_ids)
        self.select_account(self._ordered_ids[nxt])

    def _on_refresh_clicked(self):
        mgr = self._manager()
        if mgr is not None and self._selected_account_id:
            mgr.refresh_account(self._selected_account_id)

    @QtCore.Slot()
    def _on_mgr_updated(self):
        self.refresh()

    @QtCore.Slot(str, object)
    def _on_mgr_snapshot(self, _account_id: str, _snap: object):
        self.refresh()

    @QtCore.Slot(str)
    def _on_mgr_account_state(self, _account_id: str):
        self.refresh()

    @QtCore.Slot(str, bool)
    def _on_mgr_refreshing(self, _account_id: str, _on: bool):
        self.refresh()

    @QtCore.Slot(bool)
    def _on_mgr_all_refreshing(self, _on: bool):
        self.refresh()

    def _clear_layout(self, lay: QtWidgets.QLayout):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                sub = item.layout()
                self._clear_layout(sub)
                sub.setParent(None)
                sub.deleteLater()

    def refresh(self):
        mgr = self._manager()
        ordered = self._ordered_accounts()
        self._ordered_ids = [a.id for a in ordered]
        selected = self._resolve_selected_account(ordered)

        # ---- 更新账号切换行 ----
        self._clear_layout(self.chip_lay)
        self._chip_widgets.clear()
        if not ordered:
            self.chip_scroll.hide()
        else:
            for acc in ordered:
                chip = _AccountChip(acc, active=(selected is not None and acc.id == selected.id), parent=self.chip_scroll.inner)
                chip.selected_clicked.connect(self.select_account)
                self._chip_widgets.append(chip)
                self.chip_lay.addWidget(chip)
            self.chip_lay.addStretch(1)
            self.chip_scroll.show()

        # ---- 更新内容区 & 刷新按钮 ----
        self._clear_layout(self.content_lay)
        quota_enabled = bool(cfg.get("quota.enabled", True))

        if selected is None:
            self.btn_refresh.hide()
            self._render_message_state(
                title="还没有 AI 额度账号",
                desc="在设置中添加 AI 服务商账号后即可查看额度",
                btn_text="去添加",
                on_click=lambda: self._open_quota_settings(),
                primary=True,
            )
            return

        if not quota_enabled:
            self.btn_refresh.hide()
            self._render_message_state(
                title="AI 额度监控已关闭",
                desc="可在设置 → AI 额度中重新开启监控",
                btn_text="打开设置",
                on_click=lambda: self._open_quota_settings(),
                primary=False,
            )
            return

        spec = PROVIDERS.get(selected.provider, PROVIDERS[Provider.DEEPSEEK.value])
        snap = mgr.get_snapshot(selected.id) if mgr is not None else None
        is_refreshing = bool(mgr is not None and mgr.is_refreshing(selected.id))

        self.btn_refresh.show()
        self.btn_refresh.set_spinning(is_refreshing)

        # 账号监控关闭
        if not selected.enabled:
            if snap is not None and snap.has_valid_data():
                self._render_snapshot(selected, snap, force_stale_note="已停用 · 数据已过期")
            else:
                self._render_message_state(
                    title="该账号监控已关闭",
                    desc="可在设置 → AI 额度中重新开启监控",
                    btn_text="打开设置",
                    on_click=lambda: self._open_quota_settings(),
                    primary=False,
                )
            return

        # 首次刷新中（无有效旧数据）
        if is_refreshing and (snap is None or not snap.has_valid_data()):
            self._render_loading_state(selected)
            return

        eff_status = snap.status if snap is not None else AccountStatus.UNCONFIGURED.value

        # 有有效数据（正常 / 数据已过期）
        if snap is not None and snap.has_valid_data():
            stale_note = "数据已过期" if (snap.raw_stale or eff_status != AccountStatus.OK.value) else ""
            self._render_snapshot(selected, snap, force_stale_note=stale_note)
            return

        # 无有效数据时的错误状态
        if eff_status == AccountStatus.AUTH_ERROR.value:
            auth_title = f"{spec.name} API Key 已失效" if selected.provider == Provider.DEEPSEEK.value else f"{spec.name} 登录凭据已失效"
            self._render_message_state(
                title=auth_title,
                desc=(snap.last_error if snap else "") or "请重新连接或更新凭据后重试",
                btn_text="重新连接",
                on_click=lambda aid=selected.id: self._open_quota_settings(account_id=aid),
                primary=True,
                is_error=True,
            )
        elif eff_status == AccountStatus.PARSE_ERROR.value:
            self._render_message_state(
                title="额度数据格式异常",
                desc="服务返回格式不可解析，请稍后重试",
                btn_text="重试",
                on_click=self._on_refresh_clicked,
                primary=False,
                is_error=True,
            )
        elif eff_status in (AccountStatus.NETWORK_ERROR.value, AccountStatus.RATE_LIMITED.value):
            self._render_message_state(
                title="网络连接失败" if eff_status == AccountStatus.NETWORK_ERROR.value else "请求过于频繁",
                desc=(snap.last_error if snap else "") or "无法连接服务器，请检查网络",
                btn_text="重试",
                on_click=self._on_refresh_clicked,
                primary=False,
                is_error=True,
            )
        else:
            self._render_message_state(
                title="等待首次刷新",
                desc="点击刷新即可获取最新额度数据",
                btn_text="立即刷新",
                on_click=self._on_refresh_clicked,
                primary=True,
            )

    def _wrap_with_side_arrows(self, inner_layout: QtWidgets.QHBoxLayout | QtWidgets.QLayout) -> QtWidgets.QHBoxLayout:
        """超过 3 个已连接账号时在主内容左右两侧放置紧凑切换按钮；3 个及以内隐藏不占位。"""
        if len(self._ordered_ids) <= 3:
            return inner_layout if isinstance(inner_layout, QtWidgets.QHBoxLayout) else hbox((inner_layout, 1), spacing=0)
        btn_prev = _SideSwitchButton("prev", "上一个账号", parent=self.content_host)
        btn_next = _SideSwitchButton("next", "下一个账号", parent=self.content_host)
        btn_prev.clicked.connect(lambda: self._step_account(-1))
        btn_next.clicked.connect(lambda: self._step_account(1))
        return hbox(btn_prev, (inner_layout, 1), btn_next, spacing=4)

    # ---------------- 各状态渲染器 ----------------
    def _render_message_state(
        self,
        title: str,
        desc: str,
        btn_text: str,
        on_click,
        primary: bool = False,
        is_error: bool = False,
    ):
        lbl_t = Label(title, 12.5, W.DemiBold, 1, align=Qt.AlignmentFlag.AlignCenter, parent=self.content_host)
        if is_error:
            lbl_t.set_color(theme.up())
        lbl_d = QtWidgets.QLabel(desc, self.content_host)
        lbl_d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_d.setWordWrap(True)
        lbl_d.setFont(ui_font(10.0, W.Normal))
        lbl_d.setStyleSheet(f"background:transparent;border:none;color:{theme.qss_color(theme.text3())};")

        btn = PillButton(btn_text, height=22, primary=primary, parent=self.content_host)
        btn.clicked.connect(on_click)

        self.content_lay.addStretch(1)
        self.content_lay.addWidget(lbl_t)
        self.content_lay.addWidget(lbl_d)
        self.content_lay.addSpacing(2)
        self.content_lay.addLayout(hbox(None, btn, None, spacing=0))
        self.content_lay.addStretch(1)

    def _render_loading_state(self, acc: QuotaAccount):
        icon_w = _ProviderCircleIcon(acc.provider, parent=self.content_host)
        lbl_t = Label("正在更新额度…", 12.0, W.Medium, 2, parent=self.content_host)
        pb = QtWidgets.QProgressBar(self.content_host)
        pb.setRange(0, 0)
        pb.setTextVisible(False)
        pb.setFixedSize(160, 3)
        chunk_col = theme.qss_color(_accent_qcolor(acc.color))
        track_col = theme.qss_color(theme.text(0.14))
        pb.setStyleSheet(
            f"QProgressBar{{background:{track_col};border:none;border-radius:1px;}}"
            f"QProgressBar::chunk{{background:{chunk_col};border-radius:1px;}}"
        )
        col = vbox(None, lbl_t, pb, None, spacing=6)
        row = self._wrap_with_side_arrows(hbox(icon_w, (col, 1), spacing=10))
        self.content_lay.addStretch(1)
        self.content_lay.addLayout(row)
        self.content_lay.addStretch(1)

    def _render_snapshot(self, acc: QuotaAccount, snap: QuotaSnapshot, force_stale_note: str = ""):
        spec = PROVIDERS.get(acc.provider, PROVIDERS[Provider.DEEPSEEK.value])
        icon_w = _ProviderCircleIcon(acc.provider, parent=self.content_host)
        primary_bal = snap.primary_balance()

        if primary_bal is not None:
            # 1) 货币余额主布局（DeepSeek 使用此类型）
            amount_txt = _format_currency(primary_bal.currency, primary_bal.total_balance)
            lbl_sub = Label("可用余额", 10.0, W.Normal, 2, parent=self.content_host)
            lbl_big = Label(
                amount_txt,
                _big_amount_font_px(amount_txt),
                W.Bold,
                1,
                num=True,
                parent=self.content_host,
            )
            center_col = vbox(None, lbl_sub, lbl_big, None, spacing=1)

            lbl_top_k = Label("充值", 10.0, W.Normal, 3, align=Qt.AlignmentFlag.AlignRight, parent=self.content_host)
            lbl_top_v = Label(
                _format_currency(primary_bal.currency, primary_bal.topped_up_balance),
                11.0,
                W.DemiBold,
                1,
                align=Qt.AlignmentFlag.AlignRight,
                num=True,
                parent=self.content_host,
            )
            lbl_gr_k = Label("赠送", 10.0, W.Normal, 3, align=Qt.AlignmentFlag.AlignRight, parent=self.content_host)
            lbl_gr_v = Label(
                _format_currency(primary_bal.currency, primary_bal.granted_balance),
                11.0,
                W.DemiBold,
                1,
                align=Qt.AlignmentFlag.AlignRight,
                num=True,
                parent=self.content_host,
            )
            right_col = vbox(
                None,
                hbox(lbl_top_k, lbl_top_v, spacing=8),
                hbox(lbl_gr_k, lbl_gr_v, spacing=8),
                None,
                spacing=4,
            )
            main_row = self._wrap_with_side_arrows(hbox(icon_w, (center_col, 1), right_col, spacing=8))
            self.content_lay.addStretch(1)
            self.content_lay.addLayout(main_row)
            self.content_lay.addStretch(1)

            # 其它币种提示（如 "另有 USD $0.00"）；无其他币种时显示服务商名称
            others = [b for b in snap.balances if b is not primary_bal]
            if others:
                other_txt = "  ·  ".join(
                    f"另有 {b.currency} {_format_currency(b.currency, b.total_balance)}" for b in others[:2]
                )
            else:
                other_txt = f"{spec.name} · 账户余额"

        elif snap.credits is not None:
            # 2) 积分主布局（WorkBuddy 资源包分类摘要 或 周期额度进度条）
            c_val = _finite_nonneg(snap.credits)
            c_txt = f"{c_val:.2f}" if c_val is not None else "--"
            lbl_sub = Label("可用积分", 10.0, W.Normal, 2, parent=self.content_host)
            lbl_big = Label(
                c_txt,
                _big_amount_font_px(c_txt),
                W.Bold,
                1,
                num=True,
                parent=self.content_host,
            )
            pkg_items = [w for w in snap.windows if w.amount is not None and w.remaining_percent is None]
            pct_items = [w for w in snap.windows if w.remaining_percent is not None]
            if pkg_items:
                pkg_str = "  ·  ".join(f"{w.label} {float(w.amount):.2f}" for w in pkg_items[:3])
                lbl_pkg = Label(pkg_str, 10.0, W.Normal, 2, num=True, parent=self.content_host)
                center_col = vbox(None, lbl_sub, lbl_big, lbl_pkg, None, spacing=1)
            else:
                center_col = vbox(None, lbl_sub, lbl_big, None, spacing=1)
            if pct_items:
                wins_col = vbox(spacing=4)
                wins_col.addStretch(1)
                for w in pct_items[:2]:
                    row_w = _WindowProgressRow(w, acc.color, parent=self.content_host)
                    row_w.setMinimumWidth(138)
                    wins_col.addWidget(row_w)
                wins_col.addStretch(1)
                inner_row = hbox(icon_w, (center_col, 1), (wins_col, 1), spacing=10)
            else:
                inner_row = hbox(icon_w, (center_col, 1), spacing=10)
            self.content_lay.addStretch(1)
            self.content_lay.addLayout(self._wrap_with_side_arrows(inner_row))
            self.content_lay.addStretch(1)

            if len(pct_items) > 2:
                other_txt = f"{spec.name} · 共 {len(pct_items)} 项周期额度"
            elif snap.plan_name:
                other_txt = f"{spec.name} · {snap.plan_name}"
            else:
                other_txt = f"{spec.name} · 积分余额"
        else:
            # 3) 纯周期额度窗口布局（Codex / Gemini / Grok Bot，参考图 2：套餐名 + 剩余 + 周期进度 + 重置时间）
            main_wins = [w for w in snap.windows if w.currency != "EXTRA"]
            extra_wins = [w for w in snap.windows if w.currency == "EXTRA"]
            if not main_wins:
                main_wins = list(snap.windows)

            plan_head = f"{spec.name} · {snap.plan_name.title()}" if snap.plan_name else spec.name
            lbl_plan = Label(plan_head, 10.5, W.DemiBold, 1, parent=self.content_host)
            lbl_rem_h = Label("剩余", 9.5, W.Normal, 3, align=Qt.AlignmentFlag.AlignRight, parent=self.content_host)
            wins_box = vbox(hbox((lbl_plan, 1), lbl_rem_h, spacing=6), spacing=2)
            for w in main_wins[:2]:
                wins_box.addWidget(_WindowProgressRow(w, acc.color, parent=self.content_host))
            self.content_lay.addStretch(1)
            self.content_lay.addLayout(self._wrap_with_side_arrows(hbox(icon_w, (wins_box, 1), spacing=10)))
            self.content_lay.addStretch(1)

            if extra_wins:
                ew = extra_wins[0]
                epct = f"{ew.remaining_percent:.0f}%" if ew.remaining_percent is not None else "--%"
                other_txt = f"{ew.label} {epct}" if len(extra_wins) == 1 else f"{ew.label} {epct} 等 {len(extra_wins)} 项"
            else:
                rst_w = next((w for w in main_wins if w.reset_at and w.reset_at > 0), None)
                if rst_w is not None and rst_w.reset_at:
                    try:
                        rst_str = dt.datetime.fromtimestamp(float(rst_w.reset_at)).strftime("%m月%d日 %H:%M")
                        other_txt = f"{rst_str} 重置"
                    except Exception:
                        other_txt = f"{spec.name} · 周期额度"
                elif len(main_wins) > 2:
                    other_txt = f"{spec.name} · 共 {len(main_wins)} 项周期额度"
                else:
                    other_txt = f"{spec.name} · 周期额度"

        # 底部状态与更新时间行（始终处于 82px 安全区域底部，不被 NavBar 遮挡）
        lbl_left = Label(other_txt, 10.0, W.Normal, 3, num=True, parent=self.content_host)
        time_str = _format_hhmm(snap.fetched_at or acc.last_refresh_at)
        footer_right_widgets: list = []
        if force_stale_note:
            lbl_stale = Label(force_stale_note, 10.0, W.DemiBold, 1, parent=self.content_host)
            lbl_stale.set_color(QtGui.QColor("#f59e0b"))
            footer_right_widgets.append(lbl_stale)
            btn_retry = IconButton(G.REFRESH, size=16, icon_px=9, tip="重试刷新", parent=self.content_host)
            btn_retry.clicked.connect(self._on_refresh_clicked)
            footer_right_widgets.append(btn_retry)
        lbl_time = Label(time_str, 10.0, W.Normal, 3, num=True, parent=self.content_host)
        footer_right_widgets.append(lbl_time)

        self.content_lay.addLayout(hbox((lbl_left, 1), *footer_right_widgets, spacing=6))
