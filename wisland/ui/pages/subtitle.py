# -*- coding: utf-8 -*-
"""实时字幕页（419×185 紧凑布局，中日双语纯展示模式，key 保持为 "trans"）。"""
from __future__ import annotations

from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets

from ..anim import clamp
from ..header import PageHeader
from ..theme import G, W, theme, ui_font
from ..widgets import Card, IconButton, Label, PillButton, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt


class LiveView(QtWidgets.QWidget):
    """实时字幕：日语小字 + 中文大字双行纯展示，最新的在最下面；正在说的一句（草稿）实时更新。"""
    MAX = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []          # {sid, jp, zh, final}
        self.partial = ""
        self.partial_zh = ""
        self.partial_sid = ""
        self._latest_live_seq = 0
        self._closed_live_sids = deque(maxlen=16)
        self.hint = ""
        self.scroll = 0.0                   # 距离底部（最新一行）的滚动距离，0 = 跟随最新
        self._content_h = 0.0
        self._latest_btn: QtCore.QRectF | None = None
        self.setMouseTracking(True)
        theme.changed.connect(self.update)

    def _find(self, sid):
        for r in reversed(self.rows):
            if r["sid"] == sid:
                return r
        return None

    def _row(self, sid):
        r = self._find(sid)
        if r is not None:
            return r
        r = {"sid": sid, "jp": "", "zh": "", "final": False}
        self.rows.append(r)
        del self.rows[:-self.MAX]
        return r

    def on_segment(self, sid, jp):
        before = self._content_h
        r = self._row(sid)
        r["jp"] = jp
        if not r["zh"] and self.partial_zh:
            r["zh"] = self.partial_zh
        if self.partial_sid:
            self._closed_live_sids.append(self.partial_sid)
        self.partial, self.partial_zh, self.partial_sid = "", "", ""
        self._grow(before)
        self.update()

    def on_live(self, sid, zh):
        if sid in self._closed_live_sids:
            return
        seq = 0
        if isinstance(sid, str) and sid.startswith("L") and sid[1:].isdigit():
            seq = int(sid[1:])
            if seq < self._latest_live_seq:
                return
        r = self._find(sid)
        if r is None:
            if self.partial_sid and self.partial_sid != sid:
                self._closed_live_sids.append(self.partial_sid)
            self.partial_sid = sid
            self._latest_live_seq = max(self._latest_live_seq, seq)
            self.partial_zh = zh
        elif not r["final"]:
            r["zh"] = zh
        self.update()

    def on_final(self, sid, zh):
        before = self._content_h
        r = self._row(sid)
        r["zh"], r["final"] = zh, True
        self._grow(before)
        self.update()

    def on_partial(self, jp):
        self.partial = jp
        self.update()

    def clear(self):
        self.rows, self.partial, self.partial_zh, self.partial_sid = [], "", "", ""
        self.scroll = 0.0
        self.update()

    def _max_scroll(self) -> float:
        return max(0.0, self._content_h - self.height() + 6)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.pixelDelta().y()
        mx = self._max_scroll()
        if not d or mx <= 0:
            e.ignore()
            return
        self.scroll = clamp(self.scroll + d / 120.0 * 44.0, 0.0, mx)
        self.update()
        e.accept()

    def _grow(self, before: float):
        if self.scroll > 0:
            self._measure()
            self.scroll = clamp(self.scroll + (self._content_h - before), 0.0, self._max_scroll())

    def paintEvent(self, e):
        p = painter(self)
        w = max(10, self.width() - 6)
        self._measure()
        self.scroll = clamp(self.scroll, 0.0, self._max_scroll())
        y = self.height() - 4 + self.scroll
        fj, fz = ui_font(12), ui_font(14.5, W.Medium)
        fmj, fmz = QtGui.QFontMetrics(fj), QtGui.QFontMetrics(fz)
        flags = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        items = list(self.rows)
        if self.partial or self.partial_zh:
            items.append({"jp": self.partial, "zh": self.partial_zh, "final": False, "draft": True})
        if not items:
            p.setFont(ui_font(12))
            p.setPen(theme.text3())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.hint)
            return
        for it in reversed(items):
            jp = it.get("jp", "")
            zh = it.get("zh", "")
            draft = bool(it.get("draft"))
            hz = fmz.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, zh).height() if zh else 0
            hj = fmj.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, jp).height() if jp else 0
            if y - hz - hj - 2 > self.height():
                y -= hz + hj + 2 + 8
                continue
            y -= hz
            if zh:
                p.setFont(fz)
                p.setPen(theme.text(1.0 if it.get("final") else 0.8))
                p.drawText(QtCore.QRect(3, int(y), w, hz), flags, zh)
            y -= hj + 2
            if jp:
                p.setFont(fj)
                p.setPen(theme.text3() if draft else theme.text2())
                p.drawText(QtCore.QRect(3, int(y), w, hj), flags, jp)
            y -= 8
            if y < 0:
                break
        self._latest_btn = None
        if self.scroll > 1:
            f = ui_font(11, W.Medium)
            txt = "↓ 最新"
            bw = QtGui.QFontMetrics(f).horizontalAdvance(txt) + 18
            br = QtCore.QRectF(self.width() - bw - 4, self.height() - 26, bw, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.card(1.8))
            p.drawRoundedRect(br, 11, 11)
            p.setFont(f)
            p.setPen(theme.text())
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
            self._latest_btn = br

    def _measure(self):
        w = max(10, self.width() - 6)
        fj, fz = ui_font(12), ui_font(14.5, W.Medium)
        fmj, fmz = QtGui.QFontMetrics(fj), QtGui.QFontMetrics(fz)
        flags = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        items = list(self.rows)
        if self.partial or self.partial_zh:
            items.append({"jp": self.partial, "zh": self.partial_zh, "draft": True})
        total = 4.0
        for it in items:
            jp = it.get("jp", "")
            zh = it.get("zh", "")
            hz = fmz.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, zh).height() if zh else 0
            hj = fmj.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, jp).height() if jp else 0
            total += hz + hj + 2 + 8
        self._content_h = total

    def mouseMoveEvent(self, e):
        on_btn = self._latest_btn is not None and self._latest_btn.contains(e.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if on_btn else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e):
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._latest_btn is not None and self._latest_btn.contains(e.position()):
            self.scroll = 0.0
            self.update()


class LevelBar(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.v = 0.0
        self.setFixedSize(42, 6)

    def set_level(self, v):
        self.v = self.v * 0.6 + float(v) * 0.4
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.track())
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(theme.on_color())
        p.drawRoundedRect(QtCore.QRectF(0, 0, max(6, r.width() * min(1.0, self.v * 1.6)), r.height()), 3, 3)


class SubtitlePage(Page):
    key, glyph, title, height, panel_width, panel_height = "trans", G.LIVE, "实时字幕", 143, 419, 185

    def __init__(self, ctx):
        super().__init__(ctx)
        # ---- 统一 Header：左(开始/停止聆听) | 中(日期时间) | 右(清空+齿轮) ----
        self.header = PageHeader(ctx, self, height=26)
        self.live_btn = PillButton("开始聆听", G.LIVE, height=22, primary=True)
        self.live_btn.setMaximumWidth(114)
        self.live_btn.clicked.connect(lambda: ctx.live.toggle())

        self.clear_btn = IconButton(G.TRASH, 22, 10, "清空字幕")
        self.header.add_left(self.live_btn)
        self.header.add_right(self.clear_btn)

        # ---- 紧凑状态条（“模型未下载”仅在此处保留一次，下载大小放悬停提示） ----
        self.level = LevelBar()
        avail_ok = ctx.live.available()[0]
        init_msg = "就绪" if avail_ok else "模型未下载"
        init_tip = "就绪（点击「开始聆听」）" if avail_ok else "模型未下载（首次使用自动下载约 630MB 识别模型）"
        self.live_status = Label(init_msg, 11, level=3)
        self.live_status.setToolTip(init_tip)
        status_bar = hbox(self.level, (self.live_status, 1), spacing=8, margins=(4, 0, 4, 0))

        # ---- 紧凑中日双行字幕卡片（stretch=1 填满剩余空间，底部保留 6px 间距） ----
        self.view = LiveView()
        self.view.hint = "播放日语视频后点「开始聆听」"
        self.clear_btn.clicked.connect(self.view.clear)
        live_card = Card(radius=14)
        live_card.setLayout(vbox((self.view, 1), margins=(8, 6, 8, 6)))

        self.setLayout(vbox(self.header, status_bar, (live_card, 1), spacing=4, margins=(0, 0, 0, 2)))

        lv = ctx.live
        lv.segment.connect(self.view.on_segment)
        lv.live_zh.connect(self.view.on_live)
        lv.final_zh.connect(self.view.on_final)
        lv.partial.connect(self.view.on_partial)
        lv.level.connect(self.level.set_level)
        lv.status.connect(self._set_status)
        lv.error.connect(self._set_status)
        lv.running_changed.connect(self._live_state)
        lv.downloading_changed.connect(self._dl_state)

    def _set_status(self, m: str):
        self.live_status.setText(m)
        self.live_status.setToolTip(m)

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        self.live.preload()

    def show_live(self):
        pass

    def _dl_state(self, on: bool):
        self.live_btn.set_text("取消下载" if on else "开始聆听")
        self.live_btn.primary = not on
        self.live_btn.update()

    def _live_state(self, on: bool):
        self.live_btn.set_text("停止聆听" if on else "开始聆听")
        self.live_btn.primary = not on
        self.live_btn.update()
        if on:
            self._set_status("正在聆听系统声音…")
        else:
            self.level.set_level(0)
            avail_ok = self.ctx.live.available()[0]
            self.live_status.setText("已停止聆听" if avail_ok else "模型未下载")
            self.live_status.setToolTip("已停止聆听" if avail_ok else "模型未下载（首次使用自动下载约 630MB 识别模型）")


TranslatePage = SubtitlePage
