# -*- coding: utf-8 -*-
"""实时字幕页（中日双语纯展示模式）。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..anim import clamp
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
            r["zh"] = self.partial_zh          # 草稿译文先挂到这一句，等最终译文覆盖
        self.partial, self.partial_zh = "", ""
        self._grow(before)
        self.update()

    def on_live(self, sid, zh):
        r = self._find(sid)
        if r is None:
            self.partial_zh = zh               # 句子还没定稿：译文跟着草稿行显示，不单独成行
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
        self.rows, self.partial, self.partial_zh = [], "", ""
        self.scroll = 0.0
        self.update()

    # ---------- 滚动：滚轮上翻看历史；有新内容时停在原处，点「回到最新」或滚到底恢复跟随 ----------
    def _max_scroll(self) -> float:
        return max(0.0, self._content_h - self.height() + 8)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.pixelDelta().y()
        mx = self._max_scroll()
        if not d or mx <= 0:
            e.ignore()
            return
        self.scroll = clamp(self.scroll + d / 120.0 * 52.0, 0.0, mx)
        self.update()
        e.accept()

    def _grow(self, before: float):
        """内容变高时：如果正在看历史，就把滚动量加上增量，保持画面不动。"""
        if self.scroll > 0:
            self._measure()
            self.scroll = clamp(self.scroll + (self._content_h - before), 0.0, self._max_scroll())

    def paintEvent(self, e):
        p = painter(self)
        w = max(10, self.width() - 8)
        self._measure()
        self.scroll = clamp(self.scroll, 0.0, self._max_scroll())
        y = self.height() - 4 + self.scroll
        fj, fz = ui_font(12.5), ui_font(16, W.Medium)
        fmj, fmz = QtGui.QFontMetrics(fj), QtGui.QFontMetrics(fz)
        flags = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        items = list(self.rows)
        if self.partial or self.partial_zh:
            items.append({"jp": self.partial, "zh": self.partial_zh, "final": False, "draft": True})
        if not items:
            p.setFont(ui_font(13))
            p.setPen(theme.text3())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.hint)
            return
        for it in reversed(items):
            jp = it.get("jp", "")
            zh = it.get("zh", "")
            draft = bool(it.get("draft"))
            hz = fmz.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, zh).height() if zh else 0
            hj = fmj.boundingRect(QtCore.QRect(0, 0, w, 10000), flags, jp).height() if jp else 0
            if y - hz - hj - 2 > self.height():          # 整条都在可见区下方（正在看历史）：跳过
                y -= hz + hj + 2 + 12
                continue
            y -= hz
            if zh:
                p.setFont(fz)
                p.setPen(theme.text(1.0 if it.get("final") else 0.8))
                p.drawText(QtCore.QRect(4, int(y), w, hz), flags, zh)
            y -= hj + 2
            if jp:
                p.setFont(fj)
                p.setPen(theme.text3() if draft else theme.text2())
                p.drawText(QtCore.QRect(4, int(y), w, hj), flags, jp)
            y -= 12
            if y < 0:
                break
        # 正在看历史：右下角「回到最新」
        self._latest_btn = None
        if self.scroll > 1:
            f = ui_font(11.5, W.Medium)
            txt = "↓ 回到最新"
            bw = QtGui.QFontMetrics(f).horizontalAdvance(txt) + 22
            br = QtCore.QRectF(self.width() - bw - 6, self.height() - 30, bw, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.card(1.8))
            p.drawRoundedRect(br, 12, 12)
            p.setFont(f)
            p.setPen(theme.text())
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
            self._latest_btn = br

    def _measure(self):
        """全部字幕的总高度（用于滚动范围）。"""
        w = max(10, self.width() - 8)
        fj, fz = ui_font(12.5), ui_font(16, W.Medium)
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
            total += hz + hj + 2 + 12
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
        self.setFixedSize(46, 8)

    def set_level(self, v):
        self.v = self.v * 0.6 + float(v) * 0.4
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.track())
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(theme.on_color())
        p.drawRoundedRect(QtCore.QRectF(0, 0, max(8, r.width() * min(1.0, self.v * 1.6)), r.height()), 4, 4)


class TranslatePage(Page):
    key, glyph, title, height = "trans", G.LIVE, "实时字幕", 290

    def __init__(self, ctx):
        super().__init__(ctx)
        # 实时字幕（系统声音 → 日语识别 → 翻译，中日双语纯展示）
        self.live_btn = PillButton("开始聆听", G.LIVE, height=30, primary=True)
        self.live_btn.clicked.connect(lambda: ctx.live.toggle())
        self.live_status = Label("", 11.5, level=3)
        self.level = LevelBar()
        clear = IconButton(G.TRASH, 30, 12, "清空")
        self.view = LiveView()
        self.view.hint = "播放日语视频后点「开始聆听」" + (
            "" if ctx.live.available()[0] else "\n（第一次使用会先自动下载约 630MB 的识别模型）")
        clear.clicked.connect(self.view.clear)
        live_card = Card(radius=16)
        live_card.setLayout(vbox((self.view, 1), margins=(10, 8, 10, 8)))
        self.setLayout(vbox(hbox(self.live_btn, self.level, self.live_status, None, clear, spacing=8), (live_card, 1), spacing=8))

        lv = ctx.live
        lv.segment.connect(self.view.on_segment)
        lv.live_zh.connect(self.view.on_live)
        lv.final_zh.connect(self.view.on_final)
        lv.partial.connect(self.view.on_partial)
        lv.level.connect(self.level.set_level)
        lv.status.connect(self.live_status.setText)
        lv.error.connect(lambda m: self.live_status.setText(m))
        lv.running_changed.connect(self._live_state)
        lv.downloading_changed.connect(self._dl_state)

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
            self.live_status.setText("正在聆听系统声音…")
        else:
            self.level.set_level(0)
            self.live_status.setText("")
