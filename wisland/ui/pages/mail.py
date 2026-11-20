# -*- coding: utf-8 -*-
"""Daozai 风格邮箱页（key="mail"）：
- 列表态（419×140）：多账号筛选、全部/未读过滤、仅本机已读、空状态、内部滚动
- 详情态（419×236）：安全只读正文、HTML 安全纯文本提取、验证码识别/显隐/复制/定时隐藏
"""
from __future__ import annotations

import datetime as dt
import math
import time
from email.utils import parseaddr

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg
from ...services.mail.models import MailAccount, MailBody, MailHeader, VerificationCodeInfo
from ..header import PageHeader
from ..theme import G, W, theme, ui_font
from ..widgets import Card, Hoverable, IconButton, Label, PillButton, WheelScrollArea, hbox, painter, vbox
from .base import Page
from .system import styled_menu

Qt = QtCore.Qt

_ACCOUNT_BADGE_COLORS = (
    "#3b82f6",
    "#10b981",
    "#f59e0b",
    "#8b5cf6",
    "#ec4899",
    "#06b6d4",
    "#ef4444",
)

_PROVIDER_BADGES = {
    "qq": "Q",
    "gmail": "G",
    "163": "163",
    "126": "126",
    "exmail": "企",
    "icloud": "i",
    "custom": "@",
}


def _account_color(account_id: str, index: int = 0) -> QtGui.QColor:
    """为每个账号生成稳定的柔和徽章色。"""
    if index >= 0:
        return QtGui.QColor(_ACCOUNT_BADGE_COLORS[index % len(_ACCOUNT_BADGE_COLORS)])
    h = sum(ord(ch) for ch in (account_id or ""))
    return QtGui.QColor(_ACCOUNT_BADGE_COLORS[h % len(_ACCOUNT_BADGE_COLORS)])


def _provider_badge_text(acc: MailAccount | None) -> str:
    """返回服务商标识字符（不使用账号别名首字符）。"""
    if acc is None:
        return "@"
    key = str(getattr(acc, "provider", "") or "").strip().lower()
    return _PROVIDER_BADGES.get(key, "@")


def _account_label(acc: MailAccount) -> str:
    return (acc.display_name or "").strip() or acc.email


def _sender_display_name(sender: str) -> str:
    """预览卡只显示发件人名称；有名称时不重复展示完整邮箱地址。"""
    raw = (sender or "").strip()
    if not raw:
        return "未知发件人"
    name, address = parseaddr(raw)
    return name.strip().strip('"') or address.strip() or raw


def _format_mail_time(ts: float, now: dt.datetime | None = None) -> str:
    """今天显示 HH:MM，非今天显示 MM/DD。"""
    if not ts or ts <= 0:
        return "--:--"
    try:
        local_dt = dt.datetime.fromtimestamp(float(ts))
    except (OSError, OverflowError, ValueError):
        return "--:--"
    cur = now or dt.datetime.now()
    if local_dt.date() == cur.date():
        return local_dt.strftime("%H:%M")
    return local_dt.strftime("%m/%d")


def _format_full_time(ts: float) -> str:
    if not ts or ts <= 0:
        return "未知时间"
    try:
        return dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "未知时间"


def _format_mail_tooltip(hdr: MailHeader, acc: MailAccount | None) -> str:
    acc_name = _account_label(acc) if acc is not None else hdr.account_id
    sender = hdr.sender or "未知发件人"
    subject = hdr.subject or "(无主题)"
    t_str = _format_full_time(hdr.received_at)
    state = "已读" if hdr.is_read else "未读"
    return f"邮箱：{acc_name}\n发件人：{sender}\n主题：{subject}\n时间：{t_str}（{state}）"


def _elide_two_lines(text: str, font: QtGui.QFont, max_width: int) -> str:
    """将标题裁剪为最多两行，超出部分末尾省略。"""
    s = " ".join((text or "(无主题)").split())
    if max_width <= 20:
        return s
    fm = QtGui.QFontMetrics(font)
    if fm.horizontalAdvance(s) <= max_width:
        return s

    # 按字符折断第一行
    line1_end = 0
    for i in range(1, len(s) + 1):
        if fm.horizontalAdvance(s[:i]) > max_width:
            break
        line1_end = i
    line1_end = max(1, line1_end)
    line1 = s[:line1_end]
    rest = s[line1_end:].lstrip()
    if not rest:
        return line1
    line2 = fm.elidedText(rest, Qt.TextElideMode.ElideRight, max_width)
    return f"{line1}\n{line2}"


class _CompactChip(Hoverable):
    """顶部/工具行紧凑胶囊按钮。"""

    def __init__(self, text: str, parent=None, active: bool = False, max_w: int | None = None):
        super().__init__(parent)
        self._text = text
        self._active = active
        self._max_w = max_w
        self.setFixedHeight(18)
        self._sync_width()
        theme.changed.connect(self.update)

    def set_text(self, text: str):
        if text != self._text:
            self._text = text
            self._sync_width()
            self.update()

    def set_active(self, active: bool):
        if active != self._active:
            self._active = active
            self.update()

    def _sync_width(self):
        fm = QtGui.QFontMetrics(ui_font(10.5, W.DemiBold))
        w = fm.horizontalAdvance(self._text) + 18
        if self._max_w is not None:
            w = min(w, self._max_w)
        self.setFixedWidth(max(36, w))

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.isEnabled():
            p.setOpacity(0.42)
        if self._active:
            bg = QtGui.QColor(theme.on_color())
            bg.setAlphaF(0.22 if theme.dark else 0.16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 9, 9)
            fg = theme.on_color() if not theme.dark else theme.text()
        else:
            if self._hover.x > 0.02:
                bg = QtGui.QColor(255, 255, 255) if theme.dark else QtGui.QColor(0, 0, 0)
                bg.setAlphaF(0.10 * self._hover.x)
            else:
                bg = theme.card(0.65)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 9, 9)
            fg = theme.text2()
        f = ui_font(10.5, W.DemiBold if self._active else W.Medium)
        p.setFont(f)
        p.setPen(fg)
        fm = QtGui.QFontMetrics(f)
        txt_r = r.adjusted(6, 0, -6, 0)
        p.drawText(txt_r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(self._text, Qt.TextElideMode.ElideRight, int(txt_r.width())))


class _MailRow(Hoverable):
    """单行邮件项（17px）：未读圆点 + 可选服务商徽章 + 发件人 + 标题 + 可选验证码隐藏标 + 时间。"""
    row_clicked = QtCore.Signal(str, str)  # account_id, uid

    def __init__(
        self,
        hdr: MailHeader,
        acc: MailAccount | None,
        badge_color: QtGui.QColor,
        show_badge: bool = False,
        has_otp: bool = False,
        show_otp_badge: bool = True,
        selected: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.hdr = hdr
        self.acc = acc
        self.badge_color = badge_color
        self.show_badge = bool(show_badge)
        self.has_otp = bool(has_otp)
        self.show_otp_badge = bool(show_otp_badge)
        self.badge_text = _provider_badge_text(acc)
        self.selected = selected
        self.time_str = _format_mail_time(hdr.received_at)
        self.setFixedHeight(17)
        self.setToolTip(_format_mail_tooltip(hdr, acc))
        self.clicked.connect(lambda: self.row_clicked.emit(self.hdr.account_id, str(self.hdr.uid)))

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())

        # 选中或悬停背景
        if self.selected:
            sel_bg = QtGui.QColor(theme.on_color())
            sel_bg.setAlphaF(0.20 if theme.dark else 0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sel_bg)
            p.drawRoundedRect(r.adjusted(1, 0, -1, 0), 5, 5)
            bar = QtCore.QRectF(r.left() + 2.0, r.center().y() - 5.0, 2.5, 10.0)
            p.setBrush(theme.on_color())
            p.drawRoundedRect(bar, 1.2, 1.2)
        elif self._hover.x > 0.02:
            hov = QtGui.QColor(255, 255, 255) if theme.dark else QtGui.QColor(0, 0, 0)
            hov.setAlphaF(0.08 * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hov)
            p.drawRoundedRect(r.adjusted(1, 0, -1, 0), 5, 5)

        # 1. 未读圆点
        if not self.hdr.is_read:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.on_color())
            p.drawEllipse(QtCore.QPointF(r.left() + 7.5, r.center().y()), 2.2, 2.2)

        # 2. 所属服务商小徽章（仅全部账号且启用账号 > 1 时显示）
        if self.show_badge:
            badge_r = QtCore.QRectF(r.left() + 13.0, r.center().y() - 5.5, 16.0, 11.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.badge_color)
            p.drawRoundedRect(badge_r, 3.5, 3.5)
            p.setFont(ui_font(7.0 if len(self.badge_text) > 1 else 7.5, W.Bold))
            p.setPen(QtGui.QColor("#ffffff"))
            p.drawText(badge_r, Qt.AlignmentFlag.AlignCenter, self.badge_text)

        # 3. 时间（右对齐固定约 34px）
        time_w = 34.0
        time_r = QtCore.QRectF(r.right() - time_w - 5.0, r.top(), time_w, r.height())
        p.setFont(ui_font(9.5, num=True))
        p.setPen(theme.text3())
        p.drawText(time_r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.time_str)

        # 单行列表不再显示"已隐藏"小标签
        right_limit = time_r.left() - 4.0

        # 4. 发件人 & 5. 标题（窄列表按剩余宽度弹性分配，整宽保留 105/115px）
        unread = not self.hdr.is_read
        weight = W.DemiBold if unread else W.Normal
        sender_x = r.left() + (33.0 if self.show_badge else 14.0)
        base_sender_w = 105.0 if self.show_badge else 115.0
        avail_for_text = max(60.0, right_limit - sender_x - 6.0)
        sender_w = min(base_sender_w, max(46.0, avail_for_text * 0.38)) if r.width() < 280 else base_sender_w
        sender_r = QtCore.QRectF(sender_x, r.top(), sender_w, r.height())
        f_sender = ui_font(10.5, weight)
        p.setFont(f_sender)
        p.setPen(theme.text() if unread else theme.text2())
        fm_s = QtGui.QFontMetrics(f_sender)
        sender_txt = _sender_display_name(self.hdr.sender)
        p.drawText(sender_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm_s.elidedText(sender_txt, Qt.TextElideMode.ElideRight, int(sender_w)))

        subj_x = sender_x + sender_w + 6.0
        subj_w = max(36.0, right_limit - subj_x)
        subj_r = QtCore.QRectF(subj_x, r.top(), subj_w, r.height())
        f_subj = ui_font(10.5, weight)
        p.setFont(f_subj)
        p.setPen(theme.text() if unread else theme.text2())
        fm_t = QtGui.QFontMetrics(f_subj)
        subj_txt = self.hdr.subject or "(无主题)"
        p.drawText(subj_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm_t.elidedText(subj_txt, Qt.TextElideMode.ElideRight, int(subj_w)))


_OTP_PREVIEW_TTL_SECONDS = 180


class _OtpPreviewCard(Hoverable):
    """左侧验证码预览区：无背景/无描边，动态等宽、动态纵向分区，直接明文显示并在 180s 后自动关闭。"""

    expired = QtCore.Signal(object)  # (account_id, uid, code)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMinimumWidth(0)
        self.setMinimumHeight(66)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self._hdr: MailHeader | None = None
        self._info: VerificationCodeInfo | None = None
        self._token: tuple[str, str, str] | None = None
        self._copied: bool = False
        self._remaining_s: int = _OTP_PREVIEW_TTL_SECONDS
        self._reveal_deadline: float = 0.0
        self._hide_timer = QtCore.QTimer(self, interval=500, timeout=self._on_tick)
        self._copy_timer = QtCore.QTimer(self, singleShot=True, interval=1500, timeout=self._on_copy_done)
        self.clicked.connect(self._on_clicked)
        theme.changed.connect(self.update)

    def current_key(self) -> tuple[str, str] | None:
        if self._hdr is None:
            return None
        return (self._hdr.account_id, str(self._hdr.uid))

    def set_target(self, hdr: MailHeader, info: VerificationCodeInfo, deadline: float | None = None):
        new_token = (hdr.account_id, str(hdr.uid), info.code)
        same_target = (self._token == new_token)
        self._hdr = hdr
        self._info = info
        self._token = new_token
        if deadline is not None:
            self._reveal_deadline = float(deadline)
        elif not same_target or self._reveal_deadline <= 0.0:
            self._reveal_deadline = time.monotonic() + _OTP_PREVIEW_TTL_SECONDS
        if not same_target:
            self._copy_timer.stop()
            self._copied = False
        rem = max(0, math.ceil(self._reveal_deadline - time.monotonic()))
        self._remaining_s = rem
        if rem > 0 and not self._hide_timer.isActive():
            self._hide_timer.start()
        self.setToolTip(f"发件人：{hdr.sender}\n主题：{hdr.subject}")
        self.update()

    def clear_and_stop(self):
        self._hide_timer.stop()
        self._copy_timer.stop()
        self._hdr = None
        self._info = None
        self._token = None
        self._copied = False
        self._remaining_s = 0
        self._reveal_deadline = 0.0
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if self._info is not None and self._reveal_deadline > 0:
            rem = math.ceil(self._reveal_deadline - time.monotonic())
            if rem <= 0:
                tok = self._token
                self._hide_timer.stop()
                self._remaining_s = 0
                if tok is not None:
                    self.expired.emit(tok)
            else:
                self._remaining_s = rem
                if not self._hide_timer.isActive():
                    self._hide_timer.start()
            self.update()

    def hideEvent(self, e):
        self._hide_timer.stop()
        self._copy_timer.stop()
        super().hideEvent(e)

    @QtCore.Slot()
    def _on_clicked(self):
        if self._info is None:
            return
        cb = QtWidgets.QApplication.clipboard()
        if cb is not None:
            cb.setText(self._info.code)
        self._copied = True
        self._copy_timer.start()
        self.update()

    @QtCore.Slot()
    def _on_tick(self):
        if self._info is None:
            self._hide_timer.stop()
            return
        rem = math.ceil(self._reveal_deadline - time.monotonic()) if self._reveal_deadline > 0 else (self._remaining_s - 1)
        if rem <= 0:
            tok = self._token
            self._hide_timer.stop()
            self._remaining_s = 0
            self.update()
            if tok is not None:
                self.expired.emit(tok)
        elif rem != self._remaining_s:
            self._remaining_s = rem
            self.update()

    @QtCore.Slot()
    def _on_copy_done(self):
        self._copied = False
        self.update()

    def _layout_rects(self) -> tuple[QtCore.QRectF, QtCore.QRectF, QtCore.QRectF, QtCore.QRectF]:
        """按可用高度动态分配四层矩形（顶部、验证码、标题、底部），保证互不重叠。"""
        r = QtCore.QRectF(self.rect())
        pad_x = 4.0
        pad_y = 2.0
        inner_w = max(20.0, r.width() - pad_x * 2)
        avail_h = max(48.0, r.height() - pad_y * 2)

        h_top = max(13.0, round(avail_h * 0.21))
        h_bot = max(12.0, round(avail_h * 0.18))
        h_subj = max(13.0, round(avail_h * 0.21))
        h_code = max(18.0, avail_h - h_top - h_subj - h_bot)

        y = r.top() + pad_y
        header = QtCore.QRectF(r.left() + pad_x, y, inner_w, h_top)
        y = header.bottom()
        code = QtCore.QRectF(r.left() + pad_x, y, inner_w, h_code)
        y = code.bottom()
        subject = QtCore.QRectF(r.left() + pad_x, y, inner_w, h_subj)
        y = subject.bottom()
        footer = QtCore.QRectF(r.left() + pad_x, y, inner_w, max(10.0, r.bottom() - pad_y - y))
        return header, code, subject, footer

    def paintEvent(self, e):
        # 取消所有背景、描边、外框和悬停填充，文字直接绘制在页面主题背景上
        if self._hdr is None or self._info is None:
            return
        p = painter(self)
        header_r, code_r, subject_r, footer_r = self._layout_rects()
        inner_w = header_r.width()

        # 1. 第一行：左侧 "验证码" + 右侧发件人名称（约 11px 半粗体）
        f_tag = ui_font(10.5, W.Normal)
        p.setFont(f_tag)
        p.setPen(theme.text3())
        tag_w = QtGui.QFontMetrics(f_tag).horizontalAdvance("验证码") + 2.0
        p.drawText(QtCore.QRectF(header_r.left(), header_r.top(), tag_w, header_r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "验证码")

        f_sender = ui_font(11.0, W.DemiBold)
        p.setFont(f_sender)
        p.setPen(theme.text())
        snd_x = header_r.left() + tag_w + 5.0
        snd_w = max(16.0, header_r.right() - snd_x)
        fm_snd = QtGui.QFontMetrics(f_sender)
        p.drawText(QtCore.QRectF(snd_x, header_r.top(), snd_w, header_r.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   fm_snd.elidedText(_sender_display_name(self._hdr.sender),
                                     Qt.TextElideMode.ElideRight, int(snd_w)))

        # 2. 第二层：绿色大号验证码（直接明文显示，从约 25px 开始按实际宽度自动缩小，绝不溢出）
        code_text = self._info.code
        px = 25.0
        f_code = ui_font(px, W.Bold, num=True)
        while px > 13.0:
            f_code = ui_font(px, W.Bold, num=True)
            if QtGui.QFontMetrics(f_code).horizontalAdvance(code_text) <= inner_w:
                break
            px -= 1.0
        p.setFont(f_code)
        p.setPen(theme.down())
        p.drawText(code_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, code_text)

        # 3. 第三层：邮件标题（约 11px，单行省略）
        f_subj = ui_font(11.0, W.Normal)
        p.setFont(f_subj)
        p.setPen(theme.text2())
        fm_subj = QtGui.QFontMetrics(f_subj)
        p.drawText(subject_r,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm_subj.elidedText(self._hdr.subject or "(无主题)", Qt.TextElideMode.ElideRight, int(inner_w)))

        # 4. 底部：左侧交互提示 + 右侧时间（约 10px）
        t_str = _format_mail_time(self._hdr.received_at)
        f_time = ui_font(10.0, W.Normal, num=True)
        fm_time = QtGui.QFontMetrics(f_time)
        t_w = fm_time.horizontalAdvance(t_str) + 2.0
        p.setFont(f_time)
        p.setPen(theme.text3())
        p.drawText(QtCore.QRectF(footer_r.right() - t_w, footer_r.top(), t_w, footer_r.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t_str)

        if self._copied:
            hint_txt = "已复制"
            hint_col = theme.down()
        else:
            hint_txt = f"点击复制 · {self._remaining_s}s"
            hint_col = theme.down()
        f_hint = ui_font(10.0, W.Normal, num=True)
        p.setFont(f_hint)
        p.setPen(hint_col)
        hint_w = max(20.0, inner_w - t_w - 4.0)
        fm_hint = QtGui.QFontMetrics(f_hint)
        p.drawText(QtCore.QRectF(footer_r.left(), footer_r.top(), hint_w, footer_r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm_hint.elidedText(hint_txt, Qt.TextElideMode.ElideRight, int(hint_w)))


class _EmptyStateView(QtWidgets.QWidget):
    """紧凑空状态视图：标题 + 说明 + 右侧操作按钮。"""
    action_clicked = QtCore.Signal()

    def __init__(self, title: str, desc: str, btn_text: str, parent=None):
        super().__init__(parent)
        self.lbl_title = Label(title, 12, W.DemiBold, 1, parent=self)
        self.lbl_desc = Label(desc, 10, W.Normal, 3, parent=self)
        self.btn_action = PillButton(btn_text, primary=True, parent=self)
        self.btn_action.setFixedHeight(22)
        self.btn_action.clicked.connect(self.action_clicked.emit)

        text_col = vbox(self.lbl_title, self.lbl_desc, spacing=1, margins=(0, 0, 0, 0))
        self.setLayout(hbox((text_col, 1), self.btn_action, spacing=8, margins=(10, 4, 10, 4)))


class _ReadOnlyBodyEdit(QtWidgets.QPlainTextEdit):
    """只读正文文本区：支持划选复制、内部滚动并完全消费滚轮，不触发岛体翻页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setFont(ui_font(11, W.Normal))
        self.viewport().setAutoFillBackground(False)
        theme.changed.connect(self._apply_style)
        self._apply_style()

    @QtCore.Slot()
    def _apply_style(self):
        fg = theme.qss_color(theme.text())
        sb_col = theme.qss_color(theme.text(0.28))
        self.setStyleSheet(
            f"QPlainTextEdit{{background:transparent;border:none;color:{fg};padding:2px 4px;}}"
            "QScrollBar:vertical{background:transparent;width:3px;margin:2px 0;}"
            f"QScrollBar::handle:vertical{{background:{sb_col};border-radius:1px;min-height:14px;}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;border:none;background:none;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
        )

    def wheelEvent(self, e):
        sb = self.verticalScrollBar()
        delta = e.pixelDelta().y()
        if delta == 0 and e.angleDelta().y():
            delta = int(round(e.angleDelta().y() * 0.45))
        if delta and sb.maximum() > sb.minimum():
            sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() - delta)))
        e.accept()


class MailPage(Page):
    """Daozai 风格邮箱页：支持列表（419×140）与详情正文/验证码（419×236）弹簧切换。"""

    key, glyph, title, height, panel_width, panel_height = "mail", G.MAIL, "邮箱", 98, 419, 140

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self._selected_account_id: str | None = None
        self._filter_mode: str = "all"  # "all" | "unread"
        self._selected_key: tuple[str, str] | None = None
        self._rows: list[_MailRow] = []
        self._last_rows_sig: tuple | None = None
        self._last_otp_enabled: bool = bool(cfg.get("mail.otp_enabled", True))
        self._otp_deadlines: dict[tuple[str, str, str], float] = {}
        self._expired_otp_tokens: set[tuple[str, str, str]] = set()

        # 详情态状态
        self._detail_hdr: MailHeader | None = None
        self._detail_req_id: str = ""
        self._detail_scroll_pos: dict[tuple[str, str], int] = {}
        self._otp_info: VerificationCodeInfo | None = None
        self._copy_tip_timer = QtCore.QTimer(self, singleShot=True, interval=1500, timeout=self._restore_otp_hint)

        self.mode_stack = QtWidgets.QStackedWidget(self)
        self.list_view = QtWidgets.QWidget(self.mode_stack)
        self.detail_view = QtWidgets.QWidget(self.mode_stack)
        self.mode_stack.addWidget(self.list_view)    # 0: 列表态 (419×140)
        self.mode_stack.addWidget(self.detail_view)  # 1: 详情态 (419×236)
        self.detail_view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.setLayout(vbox(self.mode_stack, spacing=0, margins=(0, 0, 0, 0)))

        self._build_list_view()
        self._build_detail_view()

        mgr = self._manager()
        if mgr is not None:
            mgr.accounts_changed.connect(self._on_accounts_changed)
            mgr.headers_updated.connect(self._on_headers_updated)
            mgr.body_loaded.connect(self._on_body_loaded)
            mgr.body_prefetched.connect(self._on_body_prefetched)
            mgr.remote_seen_finished.connect(self._on_remote_seen_finished)
        cfg.changed.connect(self._on_config_changed)

        self._sync_mode_label()
        self.refresh()

    # ================================================================ 列表视图
    def _build_list_view(self):
        self.header = PageHeader(self.ctx, self.list_view, height=22)
        self.btn_account_filter = _CompactChip("邮箱 ▾", parent=self.list_view, active=False, max_w=108)
        self.btn_account_filter.setToolTip("筛选邮箱账号")
        self.btn_account_filter.clicked.connect(self._open_account_menu)

        self.btn_refresh = IconButton(G.REFRESH, 20, 10, "刷新邮件", parent=self.list_view)
        self.btn_refresh.clicked.connect(self.sync_enabled_accounts)

        self.header.add_left(self.btn_account_filter)
        self.header.add_right(self.btn_refresh)

        self.btn_filter_all = _CompactChip("全部 0", parent=self.list_view, active=True)
        self.btn_filter_unread = _CompactChip("未读 0", parent=self.list_view, active=False)
        self.btn_filter_all.clicked.connect(lambda: self.set_filter_mode("all"))
        self.btn_filter_unread.clicked.connect(lambda: self.set_filter_mode("unread"))

        self.lbl_local_only = Label("仅本机", 10, W.Normal, 3, parent=self.list_view)
        self.lbl_local_only.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Preferred)
        self.lbl_local_only.setToolTip("默认只修改本机状态；开启服务器同步后才允许回写")
        self.btn_mark_read = _CompactChip("全部已读", parent=self.list_view, active=False)
        self.btn_mark_read.clicked.connect(self.mark_current_all_read)

        self.toolbar_wrap = QtWidgets.QWidget(self.list_view)
        self.toolbar_wrap.setMinimumWidth(0)
        toolbar = hbox(
            self.btn_filter_all,
            self.btn_filter_unread,
            None,
            self.lbl_local_only,
            self.btn_mark_read,
            spacing=3,
            margins=(0, 0, 0, 0),
        )
        self.toolbar_wrap.setLayout(toolbar)

        self.otp_preview_card = _OtpPreviewCard(self.list_view)
        self.otp_preview_card.expired.connect(self._on_otp_preview_expired)
        self.otp_preview_card.hide()

        self.right_pane = QtWidgets.QWidget(self.list_view)
        self.right_pane.setMinimumWidth(0)
        self.list_card = Card(self.right_pane, radius=10, alpha=0.55)
        self.stack = QtWidgets.QStackedWidget(self.list_card)

        self.empty_no_account = _EmptyStateView(
            "还没有邮箱",
            "在设置 → 邮箱账号里添加；授权码只存进系统凭据管理器",
            "去添加",
            parent=self.stack,
        )
        self.empty_no_account.action_clicked.connect(self._open_mail_settings)

        self.empty_no_mail = _EmptyStateView(
            "暂无邮件",
            "点击刷新重新同步",
            "刷新",
            parent=self.stack,
        )
        self.empty_no_mail.action_clicked.connect(self.sync_enabled_accounts)

        self.scroll = WheelScrollArea(self.stack, orientation="vertical")
        self.scroll_lay = vbox(spacing=0, margins=(2, 1, 2, 1))
        self.scroll.inner.setLayout(self.scroll_lay)

        self.stack.addWidget(self.empty_no_account)  # 0
        self.stack.addWidget(self.empty_no_mail)     # 1
        self.stack.addWidget(self.scroll)            # 2
        self.list_card.setLayout(vbox(self.stack, spacing=0, margins=(0, 0, 0, 0)))

        # 左右按 38:62 比例分配：左侧紧凑呈现验证码（约 148px），右侧约 241px 保证“仅本机”与邮件信息充裕
        self.right_pane.setLayout(vbox(self.toolbar_wrap, (self.list_card, 1), spacing=2, margins=(0, 0, 0, 0)))
        self.list_body_wrap = hbox(
            (self.otp_preview_card, 38),
            (self.right_pane, 62),
            spacing=6,
            margins=(0, 0, 0, 0),
        )
        self.list_view.setLayout(
            vbox(self.header, (self.list_body_wrap, 1), spacing=2, margins=(0, 0, 0, 0))
        )

    # ================================================================ 详情视图
    def _build_detail_view(self):
        self.detail_header = PageHeader(self.ctx, self.detail_view, height=22)
        self.btn_back = _CompactChip("‹ 返回", parent=self.detail_view, active=False)
        self.btn_back.setToolTip("返回邮件列表")
        self.btn_back.clicked.connect(self.close_detail)
        self.detail_header.add_left(self.btn_back)

        # 第一行：发件人 + 可选远程同步提示
        self.lbl_detail_sender = Label("", 11.5, W.DemiBold, 1, parent=self.detail_view)
        self.lbl_sync_warn = Label("", 9.5, W.Medium, 3, parent=self.detail_view)
        self.lbl_sync_warn.hide()
        row_sender = hbox((self.lbl_detail_sender, 1), self.lbl_sync_warn, spacing=6, margins=(2, 0, 2, 0))

        # 第二行：邮件标题（最多两行）
        self.lbl_detail_subject = QtWidgets.QLabel("", self.detail_view)
        self.lbl_detail_subject.setFont(ui_font(11, W.Medium))
        self.lbl_detail_subject.setWordWrap(True)
        self.lbl_detail_subject.setMaximumHeight(30)
        self.lbl_detail_subject.setContentsMargins(2, 0, 2, 0)

        # 第三行：时间、收件邮箱
        self.lbl_detail_meta = Label("", 9.5, W.Normal, 3, num=True, parent=self.detail_view)
        self.lbl_detail_meta.setContentsMargins(2, 0, 2, 0)

        # 验证码区域（仅识别到验证码时显示，一律直接明文显示）
        self.otp_card = Card(self.detail_view, radius=8, alpha=0.75)
        self.otp_card.setFixedHeight(24)
        self.lbl_otp_tag = Label("验证码", 10, W.DemiBold, 2, parent=self.otp_card)
        self.btn_otp_toggle = _CompactChip("", parent=self.otp_card, active=True, max_w=120)
        self.btn_otp_toggle.setToolTip("点击复制验证码")
        self.btn_otp_toggle.clicked.connect(self._on_otp_chip_clicked)

        self.btn_otp_copy = _CompactChip("复制", parent=self.otp_card, active=False)
        self.btn_otp_copy.setToolTip("直接一键复制验证码")
        self.btn_otp_copy.clicked.connect(self.copy_current_otp)

        self.lbl_otp_hint = Label("", 9.5, W.Normal, 3, num=True, parent=self.otp_card)
        self.otp_card.setLayout(
            hbox(
                self.lbl_otp_tag,
                self.btn_otp_toggle,
                (self.lbl_otp_hint, 1),
                self.btn_otp_copy,
                spacing=5,
                margins=(6, 0, 6, 0),
            )
        )
        self.otp_card.hide()

        # 下方正文滚动区
        self.body_card = Card(self.detail_view, radius=10, alpha=0.55)
        self.body_stack = QtWidgets.QStackedWidget(self.body_card)

        # 0: 加载中
        loading_w = QtWidgets.QWidget(self.body_stack)
        self.lbl_loading = Label("正在获取正文…", 11, W.Normal, 2, align=Qt.AlignmentFlag.AlignCenter, parent=loading_w)
        self.pb_loading = QtWidgets.QProgressBar(loading_w)
        self.pb_loading.setRange(0, 0)
        self.pb_loading.setTextVisible(False)
        self.pb_loading.setFixedSize(140, 3)
        loading_w.setLayout(vbox(None, self.lbl_loading, hbox(None, self.pb_loading, None), None, spacing=6, margins=(10, 6, 10, 6)))

        # 1: 加载失败
        err_w = QtWidgets.QWidget(self.body_stack)
        self.lbl_body_err = QtWidgets.QLabel("", err_w)
        self.lbl_body_err.setWordWrap(True)
        self.lbl_body_err.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_body_err.setFont(ui_font(10.5, W.Normal))
        self.btn_retry_body = PillButton("重试", primary=True, parent=err_w)
        self.btn_retry_body.setFixedHeight(22)
        self.btn_retry_body.clicked.connect(self._retry_fetch_body)
        err_w.setLayout(vbox(None, self.lbl_body_err, hbox(None, self.btn_retry_body, None), None, spacing=6, margins=(10, 6, 10, 6)))

        # 2: 只读正文
        self.body_edit = _ReadOnlyBodyEdit(self.body_stack)
        self.body_edit.verticalScrollBar().valueChanged.connect(self._on_body_scrolled)

        self.body_stack.addWidget(loading_w)      # 0
        self.body_stack.addWidget(err_w)          # 1
        self.body_stack.addWidget(self.body_edit) # 2
        self.body_card.setLayout(vbox(self.body_stack, spacing=0, margins=(2, 2, 2, 2)))

        self.detail_view.setLayout(
            vbox(
                self.detail_header,
                row_sender,
                self.lbl_detail_subject,
                self.lbl_detail_meta,
                self.otp_card,
                (self.body_card, 1),
                spacing=2,
                margins=(0, 0, 0, 0),
            )
        )
        theme.changed.connect(self._style_detail_labels)
        self._style_detail_labels()

    @QtCore.Slot()
    def _style_detail_labels(self):
        self.lbl_detail_subject.setStyleSheet(
            f"background:transparent;border:none;color:{theme.qss_color(theme.text())};"
        )
        self.lbl_body_err.setStyleSheet(
            f"background:transparent;border:none;color:{theme.qss_color(theme.up())};"
        )
        self.lbl_sync_warn.set_color(theme.up())
        chunk_col = theme.qss_color(theme.on_color())
        track_col = theme.qss_color(theme.card(0.8))
        self.pb_loading.setStyleSheet(
            f"QProgressBar{{background:{track_col};border:none;border-radius:1px;}}"
            f"QProgressBar::chunk{{background:{chunk_col};border-radius:1px;}}"
        )

    def _manager(self):
        return getattr(self.ctx, "mail", None) if self.ctx is not None else None

    @QtCore.Slot()
    def _on_config_changed(self):
        self._sync_mode_label()
        otp_on = bool(cfg.get("mail.otp_enabled", True))
        if otp_on != self._last_otp_enabled:
            self._last_otp_enabled = otp_on
            self.refresh()

    @QtCore.Slot()
    def _sync_mode_label(self):
        sync_srv = bool(cfg.get("mail.sync_read_to_server", False))
        self.lbl_local_only.setText("同步已读" if sync_srv else "仅本机")

    def _open_mail_settings(self):
        if self.ctx is not None and hasattr(self.ctx, "open_settings"):
            self.ctx.open_settings("mail_accounts")

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        self.detail_header.refresh_clock()
        self.refresh()
        if self.mode_stack.currentIndex() == 0:
            self.sync_enabled_accounts()

    def sync_enabled_accounts(self):
        """后台刷新所有已启用账号的最近邮件（同一账号不并发重复发起）。"""
        mgr = self._manager()
        if mgr is None:
            return
        for acc in mgr.list_accounts():
            if acc.enabled and not mgr.is_syncing(acc.id):
                mgr.start_sync_headers(acc.id)

    def set_account_filter(self, account_id: str | None):
        """切换账号筛选，不清除其他账号未读、不修改服务器状态。"""
        if account_id != self._selected_account_id:
            self._selected_account_id = account_id
            self.scroll.reset_scroll()
            self.refresh()

    def set_filter_mode(self, mode: str):
        """切换「全部 / 未读」过滤，仅过滤本地列表。"""
        if mode not in ("all", "unread"):
            return
        if mode != self._filter_mode:
            self._filter_mode = mode
            self.btn_filter_all.set_active(mode == "all")
            self.btn_filter_unread.set_active(mode == "unread")
            self.scroll.reset_scroll()
            self.refresh()

    def mark_current_all_read(self):
        """将当前筛选范围内的邮件标记为已读（默认仅本机），并清除对应账号通知未读。"""
        mgr = self._manager()
        if mgr is None:
            return
        mgr.mark_all_read_local(self._selected_account_id)
        mon = getattr(self.ctx, "mail_monitor", None) if self.ctx is not None else None
        if mon is not None:
            if self._selected_account_id:
                mon.mark_account_seen(self._selected_account_id)
            else:
                for acc in mgr.list_accounts():
                    if acc.enabled:
                        mon.mark_account_seen(acc.id)
        self.refresh()

    def _open_account_menu(self):
        mgr = self._manager()
        accounts = [a for a in (mgr.list_accounts() if mgr else []) if a.enabled]

        m = styled_menu(self)
        act_all = m.addAction(("✓ " if self._selected_account_id is None else "   ") + "全部账号")
        act_all.triggered.connect(lambda: self.set_account_filter(None))

        if accounts:
            m.addSeparator()
            for acc in accounts:
                prefix = "✓ " if self._selected_account_id == acc.id else "   "
                act = m.addAction(prefix + _account_label(acc))
                act.triggered.connect(lambda _=False, aid=acc.id: self.set_account_filter(aid))
        else:
            m.addSeparator()
            empty_act = m.addAction("   暂无已启用账号")
            empty_act.setEnabled(False)

        pos = self.btn_account_filter.mapToGlobal(QtCore.QPoint(0, self.btn_account_filter.height() + 3))
        m.popup(pos)

    @QtCore.Slot()
    def _on_accounts_changed(self):
        mgr = self._manager()
        if mgr is not None and self._selected_account_id is not None:
            valid_ids = {a.id for a in mgr.list_accounts() if a.enabled}
            if self._selected_account_id not in valid_ids:
                self._selected_account_id = None
        self.refresh()

    @QtCore.Slot(str, list)
    def _on_headers_updated(self, _account_id: str, _headers: list):
        self.refresh()

    @QtCore.Slot(str)
    def _on_account_state_changed(self, _account_id: str):
        self.refresh()

    @QtCore.Slot(str, str)
    def _on_row_clicked(self, account_id: str, uid: str):
        self._selected_key = (account_id, str(uid))
        for row in self._rows:
            row.set_selected((row.hdr.account_id, str(row.hdr.uid)) == self._selected_key)
            if (row.hdr.account_id, str(row.hdr.uid)) == self._selected_key:
                self.open_detail(row.hdr)

    # ================================================================ 详情与正文加载
    def _set_panel_size(self, width: int, height: int):
        changed = (self.panel_width != width or self.panel_height != height)
        self.panel_width = width
        self.panel_height = height
        if changed and self.island is not None:
            self.island.page_size_changed()

    def open_detail(self, hdr: MailHeader):
        """进入邮件详情（419×236），默认仅改本机已读状态并后台获取正文。"""
        mgr = self._manager()
        was_unread = not hdr.is_read
        self._detail_hdr = hdr
        self._selected_key = (hdr.account_id, str(hdr.uid))
        self.lbl_sync_warn.hide()
        self.lbl_sync_warn.setText("")

        # 返回列表后重新打开需回到顶部
        self._detail_scroll_pos[(hdr.account_id, str(hdr.uid))] = 0

        acc = mgr.get_account(hdr.account_id) if mgr is not None else None
        self.lbl_detail_sender.setText(hdr.sender or "未知发件人")
        subj_font = self.lbl_detail_subject.font()
        self.lbl_detail_subject.setText(_elide_two_lines(hdr.subject or "(无主题)", subj_font, 390))
        acc_addr = acc.email if acc is not None else hdr.account_id
        self.lbl_detail_meta.setText(f"{_format_full_time(hdr.received_at)}  ·  {acc_addr}")

        # 切换至详情尺寸（419×236 弹簧动画）；详情态停止并隐藏左侧大预览卡
        self.otp_preview_card.clear_and_stop()
        self.otp_preview_card.hide()
        self.detail_view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.list_view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.mode_stack.setCurrentIndex(1)
        self._set_panel_size(419, 236)

        # 已读处理：默认只改本机，仅开启服务器同步才远程回写；通知未读仅清除当前邮件所属账号
        if mgr is not None:
            mgr.mark_message_read_local(hdr.account_id, hdr.uid)
            if was_unread and bool(cfg.get("mail.sync_read_to_server", False)):
                ok_r, err_r, _ = mgr.start_mark_seen_remote(hdr.account_id, hdr.uid)
                if not ok_r and err_r:
                    self._show_remote_sync_warn(err_r)
        mon = getattr(self.ctx, "mail_monitor", None) if self.ctx is not None else None
        if mon is not None:
            mon.mark_account_seen(hdr.account_id, hdr.uid, hdr.uid_validity)

        # 正文缓存（内存 -> 本机正文缓存 -> 网络读取）：命中缓存则不显示加载条，正文与验证码同步出现
        cached = mgr.get_cached_body(hdr.account_id, hdr.uid, hdr.uid_validity) if mgr is not None else None
        if cached is not None:
            self._display_body(cached, reset_scroll=True)
            self._refresh_detail_otp(reset_hidden=True)
        else:
            self._refresh_detail_otp(reset_hidden=True)
            self._start_detail_body_fetch(hdr.account_id, hdr.uid)

    def open_notification_target(self, account_id: str, uid: str, uid_validity: str = "") -> bool:
        """从邮件通知点击进入：切换账号筛选、定位并打开对应邮件。"""
        mgr = self._manager()
        if mgr is None:
            return False
        self._selected_account_id = account_id
        self._selected_key = (account_id, str(uid))
        target_hdr: MailHeader | None = None
        for h in mgr.headers_for(account_id):
            if str(h.uid) == str(uid):
                target_hdr = h
                break
        if target_hdr is None:
            hdrs = mgr.headers_for(account_id)
            if hdrs:
                target_hdr = hdrs[0]
        if target_hdr is None:
            self.refresh()
            return False
        self.open_detail(target_hdr)
        return True

    @QtCore.Slot(str, str)
    def _on_body_prefetched(self, account_id: str, uid: str):
        if self.mode_stack.currentIndex() == 0:
            mgr = self._manager()
            if mgr is not None and bool(cfg.get("mail.otp_enabled", True)):
                info = mgr.get_verification_code(account_id, uid)
                if info is not None and self.otp_preview_card.current_key() != (account_id, str(uid)):
                    self.refresh()
        elif self._detail_hdr is not None and self._detail_hdr.account_id == account_id and str(self._detail_hdr.uid) == str(uid):
            mgr = self._manager()
            cached = mgr.get_cached_body(account_id, uid, self._detail_hdr.uid_validity) if mgr is not None else None
            if cached is not None and self.body_stack.currentIndex() == 0:
                self._display_body(cached, reset_scroll=True)
                self._refresh_detail_otp(reset_hidden=False)

    def close_detail(self):
        """返回列表态（419×140），保留列表选中状态且不清除未读来源点。"""
        mgr = self._manager()
        if mgr is not None and self._detail_req_id:
            mgr.cancel_task(self._detail_req_id)
        self._detail_req_id = ""
        self._copy_tip_timer.stop()
        if self._detail_hdr is not None:
            # 清除滚动位置记录，保证返回后重新打开回到顶部
            self._detail_scroll_pos.pop((self._detail_hdr.account_id, str(self._detail_hdr.uid)), None)
        self._detail_hdr = None
        self.list_view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.detail_view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.mode_stack.setCurrentIndex(0)
        self._set_panel_size(419, 140)
        self.refresh()

    def _start_detail_body_fetch(self, account_id: str, uid: str):
        mgr = self._manager()
        if mgr is None:
            self.lbl_body_err.setText("邮件服务未初始化")
            self.body_stack.setCurrentIndex(1)
            return
        if self._detail_req_id:
            mgr.cancel_task(self._detail_req_id)
        self.body_stack.setCurrentIndex(0)
        ok, err, req_id = mgr.start_fetch_body(account_id, uid)
        if not ok:
            self.lbl_body_err.setText(err or "无法获取正文")
            self.body_stack.setCurrentIndex(1)
            self._detail_req_id = ""
        else:
            self._detail_req_id = req_id

    def _retry_fetch_body(self):
        if self._detail_hdr is not None:
            self._start_detail_body_fetch(self._detail_hdr.account_id, self._detail_hdr.uid)

    @QtCore.Slot(str, str, bool, object, str)
    def _on_body_loaded(self, account_id: str, uid: str, ok: bool, body: MailBody | None, err: str):
        if self._detail_hdr is None:
            return
        if self._detail_hdr.account_id != account_id or str(self._detail_hdr.uid) != str(uid):
            return
        self._detail_req_id = ""
        if ok and body is not None:
            self._display_body(body, reset_scroll=True)
            self._refresh_detail_otp(reset_hidden=False)
        else:
            self.lbl_body_err.setText(err or "正文加载失败")
            self.body_stack.setCurrentIndex(1)

    @QtCore.Slot(str, str, bool, str)
    def _on_remote_seen_finished(self, account_id: str, uid: str, ok: bool, err: str):
        if not ok and self._detail_hdr is not None:
            if self._detail_hdr.account_id == account_id and str(self._detail_hdr.uid) == str(uid):
                self._show_remote_sync_warn(err or "远程同步失败")

    def _show_remote_sync_warn(self, reason: str):
        self.lbl_sync_warn.setText("远程已读同步失败")
        self.lbl_sync_warn.setToolTip(f"已保留本机已读状态：{reason}")
        self.lbl_sync_warn.show()

    def _display_body(self, body: MailBody, reset_scroll: bool = False):
        content = (body.text or "").strip() or "（该邮件没有可显示的纯文本内容）"
        key = (body.account_id, str(body.uid))
        saved_pos = 0 if reset_scroll else self._detail_scroll_pos.get(key, 0)
        self.body_edit.setPlainText(content)
        self.body_stack.setCurrentIndex(2)
        sb = self.body_edit.verticalScrollBar()
        sb.setValue(max(sb.minimum(), min(sb.maximum(), saved_pos)))

    @QtCore.Slot(int)
    def _on_body_scrolled(self, val: int):
        if self._detail_hdr is not None and self.body_stack.currentIndex() == 2:
            self._detail_scroll_pos[(self._detail_hdr.account_id, str(self._detail_hdr.uid))] = int(val)

    # ================================================================ 验证码区域
    def _refresh_detail_otp(self, reset_hidden: bool = True):
        mgr = self._manager()
        if self._detail_hdr is None or mgr is None or not bool(cfg.get("mail.otp_enabled", True)):
            self._otp_info = None
            self.otp_card.hide()
            return
        info = mgr.get_verification_code(
            self._detail_hdr.account_id,
            self._detail_hdr.uid,
            self._detail_hdr.subject,
            self._detail_hdr.uid_validity,
        )
        if info is None:
            self._otp_info = None
            self.otp_card.hide()
            return
        self._otp_info = info
        self._sync_otp_ui()
        self.otp_card.show()

    def _sync_otp_ui(self):
        if self._otp_info is None:
            return
        self.btn_otp_toggle.set_text(self._otp_info.code)
        self.btn_otp_toggle.set_active(True)
        self.lbl_otp_hint.setText("")

    def _on_otp_chip_clicked(self):
        if self._otp_info is None:
            return
        self.copy_current_otp()

    def copy_current_otp(self):
        if self._otp_info is None:
            return
        cb = QtWidgets.QApplication.clipboard()
        if cb is not None:
            cb.setText(self._otp_info.code)
        self.lbl_otp_hint.setText("已复制")
        self._copy_tip_timer.start()

    @QtCore.Slot()
    def _restore_otp_hint(self):
        if self._otp_info is not None:
            self._sync_otp_ui()

    @QtCore.Slot(object)
    def _on_otp_preview_expired(self, token):
        if token is not None:
            self._expired_otp_tokens.add(token)
            self._otp_deadlines.pop(token, None)
        self.refresh()

    # ================================================================ 列表刷新
    def refresh(self):
        mgr = self._manager()
        accounts = mgr.list_accounts() if mgr is not None else []
        acc_map = {a.id: a for a in accounts}
        acc_index = {a.id: idx for idx, a in enumerate(accounts)}

        # 更新顶部账号筛选按钮文案
        if self._selected_account_id and self._selected_account_id in acc_map:
            self.btn_account_filter.set_text(f"{_account_label(acc_map[self._selected_account_id])} ▾")
        else:
            self.btn_account_filter.set_text("邮箱 ▾")

        # 情况 1：没有邮箱账号
        if not accounts:
            self.btn_filter_all.set_text("全部 0")
            self.btn_filter_unread.set_text("未读 0")
            self.btn_mark_read.setEnabled(False)
            self.otp_preview_card.clear_and_stop()
            self.otp_preview_card.hide()
            self._clear_rows()
            self.stack.setCurrentIndex(0)
            return

        # 收集当前账号筛选范围下的所有邮件（按时间倒序，不同账号同 UID 不合并）
        all_hdrs = mgr.all_headers(self._selected_account_id) if mgr is not None else []
        unread_count = sum(1 for h in all_hdrs if not h.is_read)
        total_count = len(all_hdrs)

        self.btn_filter_all.set_text(f"全部 {total_count}")
        self.btn_filter_unread.set_text(f"未读 {unread_count}")
        self.btn_mark_read.setEnabled(unread_count > 0)

        shown_hdrs = [h for h in all_hdrs if not h.is_read] if self._filter_mode == "unread" else all_hdrs

        if not shown_hdrs:
            self.otp_preview_card.clear_and_stop()
            self.otp_preview_card.hide()
            self._clear_rows()
            if self._filter_mode == "unread" and total_count > 0:
                self.empty_no_mail.lbl_title.setText("暂无未读邮件")
                self.empty_no_mail.lbl_desc.setText("当前筛选范围内邮件已全部读完")
            else:
                self.empty_no_mail.lbl_title.setText("暂无邮件")
                self.empty_no_mail.lbl_desc.setText("点击刷新重新同步")
            self.stack.setCurrentIndex(1)
            return

        # 在当前筛选结果中查找最新一封有验证码的邮件（仅读取现有缓存与预取结果，绝不访问网络）
        otp_on = bool(cfg.get("mail.otp_enabled", True))
        latest_otp_hdr: MailHeader | None = None
        latest_otp_info: VerificationCodeInfo | None = None
        latest_otp_deadline: float | None = None
        if otp_on and mgr is not None:
            now_m = time.monotonic()
            for h in shown_hdrs:
                info = mgr.get_verification_code(h.account_id, h.uid, h.subject, h.uid_validity)
                if info is not None:
                    tok = (h.account_id, str(h.uid), info.code)
                    if tok in self._expired_otp_tokens:
                        break
                    dl = self._otp_deadlines.get(tok)
                    if dl is None:
                        ttl_s = max(30, int(cfg.get("mail.otp_hide_seconds", _OTP_PREVIEW_TTL_SECONDS) or _OTP_PREVIEW_TTL_SECONDS))
                        dl = now_m + ttl_s
                        self._otp_deadlines[tok] = dl
                    if dl <= now_m:
                        self._expired_otp_tokens.add(tok)
                        self._otp_deadlines.pop(tok, None)
                        break
                    latest_otp_hdr = h
                    latest_otp_info = info
                    latest_otp_deadline = dl
                    break

        has_preview_card = (latest_otp_hdr is not None and latest_otp_info is not None and latest_otp_deadline is not None)
        if has_preview_card:
            self.otp_preview_card.set_target(latest_otp_hdr, latest_otp_info, deadline=latest_otp_deadline)
            self.otp_preview_card.show()
        else:
            self.otp_preview_card.clear_and_stop()
            self.otp_preview_card.hide()

        self._rebuild_rows(shown_hdrs, acc_map, acc_index, show_otp_badge=False)
        self.stack.setCurrentIndex(2)

    def _clear_rows(self):
        self._rows.clear()
        self._last_rows_sig = None
        while self.scroll_lay.count():
            item = self.scroll_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _rebuild_rows(
        self,
        hdrs: list[MailHeader],
        acc_map: dict[str, MailAccount],
        acc_index: dict[str, int],
        show_otp_badge: bool = True,
    ):
        mgr = self._manager()
        otp_on = bool(cfg.get("mail.otp_enabled", True))
        enabled_count = sum(1 for a in acc_map.values() if a.enabled)
        show_badge = (self._selected_account_id is None) and (enabled_count > 1)

        sig_items = []
        for h in hdrs:
            has_otp = bool(otp_on and mgr is not None and mgr.get_verification_code(h.account_id, h.uid, h.subject, h.uid_validity) is not None)
            sig_items.append((
                h.account_id,
                str(h.uid),
                h.uid_validity,
                bool(h.is_read),
                h.sender,
                h.subject,
                round(float(h.received_at or 0.0)),
                has_otp,
            ))
        new_sig = (show_badge, bool(show_otp_badge), self._selected_key, tuple(sig_items))
        if new_sig == self._last_rows_sig:
            return

        self._clear_rows()
        self._last_rows_sig = new_sig
        for h, item_sig in zip(hdrs, sig_items):
            acc = acc_map.get(h.account_id)
            idx = acc_index.get(h.account_id, -1)
            col = _account_color(h.account_id, idx)
            selected = self._selected_key == (h.account_id, str(h.uid))
            has_otp = item_sig[7]
            row = _MailRow(
                h,
                acc,
                col,
                show_badge=show_badge,
                has_otp=has_otp,
                show_otp_badge=show_otp_badge,
                selected=selected,
                parent=self.scroll.inner,
            )
            row.row_clicked.connect(self._on_row_clicked)
            self._rows.append(row)
            self.scroll_lay.addWidget(row)
            row.show()
        self.scroll_lay.addStretch(1)
        self.scroll_lay.activate()
        self.scroll.inner.updateGeometry()
        self.scroll.clamp_scroll()
        self.scroll.viewport().update()

    def closeEvent(self, e):
        self.otp_preview_card.clear_and_stop()
        self._copy_tip_timer.stop()
        super().closeEvent(e)

    def on_hide(self):
        """岛体收起或切走页面时，若当前停留在邮件详情则自动返回邮件列表。"""
        if self.mode_stack.currentIndex() == 1:
            self.close_detail()
        super().on_hide()
