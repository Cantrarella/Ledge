# -*- coding: utf-8 -*-
"""主页（紧凑双栏：左侧媒体控制 + 右侧小形象，尺寸 419×140）。"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from ..anim import Spring, clamp
from ..header import PageHeader
from ..theme import G, W, icon_font, theme, ui_font
from ..widgets import (Card, IconButton, Themed, fmt_time, hbox,
                       painter, rounded_pixmap, vbox)
from .base import Page

Qt = QtCore.Qt


# ======================================================================== 播放卡片
class Progress(QtWidgets.QWidget, Themed):
    """单行横向进度条：左当前时间 + 中 2.5px 圆角进度条 + 右总时长。"""
    seek = QtCore.Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.pos_ = 0.0
        self.dur = 0.0
        self.stamp = 0.0
        self.playing = False
        self.hover = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hook_theme()

    def set_state(self, m: dict):
        self.pos_ = float(m.get("position") or 0)
        self.dur = float(m.get("duration") or 0)
        self.stamp = float(m.get("stamp") or time.monotonic())
        self.playing = bool(m.get("playing"))
        self.update()

    def current(self) -> float:
        p = self.pos_ + ((time.monotonic() - self.stamp) if self.playing else 0)
        return clamp(p, 0, self.dur) if self.dur else p

    def _layout_metrics(self) -> tuple[str, str, QtCore.QRectF, QtCore.QRectF, QtCore.QRectF]:
        cur = self.current()
        cur_txt = fmt_time(cur)
        dur_txt = fmt_time(self.dur) if self.dur > 0 else ""
        fm = QtGui.QFontMetrics(ui_font(9.5, num=True))
        lw = float(max(24, fm.horizontalAdvance(cur_txt) + 2))
        rw = float(max(24, fm.horizontalAdvance(dur_txt) + 2)) if dur_txt else 0.0
        w = float(max(1, self.width()))
        h = float(self.height())
        gap = 5.0
        bar_l = lw + gap
        bar_r = w - (rw + gap if rw > 0 else 0.0)
        bar_w = max(12.0, bar_r - bar_l)
        return (
            cur_txt,
            dur_txt,
            QtCore.QRectF(0, 0, lw, h),
            QtCore.QRectF(bar_l, 0, bar_w, h),
            QtCore.QRectF(w - rw, 0, rw, h),
        )

    def mouseReleaseEvent(self, e):
        if self.dur > 0:
            _, _, _, bar_r, _ = self._layout_metrics()
            ratio = clamp((e.position().x() - bar_r.left()) / max(1.0, bar_r.width()), 0.0, 1.0)
            self.seek.emit(ratio * self.dur)

    def paintEvent(self, e):
        p = painter(self)
        cur_txt, dur_txt, l_rect, bar_rect, r_rect = self._layout_metrics()
        cy = bar_rect.center().y()
        th = 2.5 + 1.0 * self.hover
        tr = QtCore.QRectF(bar_rect.left(), cy - th / 2.0, bar_rect.width(), th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.track())
        p.drawRoundedRect(tr, th / 2.0, th / 2.0)

        cur = self.current()
        if self.dur > 0:
            ratio = clamp(cur / self.dur, 0.0, 1.0)
            fill_w = max(th, bar_rect.width() * ratio) if ratio > 0 else 0.0
            if fill_w > 0:
                p.setBrush(theme.text(0.85 + 0.15 * self.hover))
                p.drawRoundedRect(QtCore.QRectF(bar_rect.left(), cy - th / 2.0, fill_w, th), th / 2.0, th / 2.0)

        p.setFont(ui_font(9.5, num=True))
        p.setPen(theme.text3())
        p.drawText(l_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, cur_txt)
        if dur_txt:
            p.drawText(r_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur_txt)


class CoverView(QtWidgets.QWidget):
    """封面：48×48 固定尺寸垂直居中绘制，不响应鼠标悬停缩放或发光。"""
    def __init__(self, size: int = 48, parent=None):
        super().__init__(parent)
        self.size_ = size
        self.pm: QtGui.QPixmap | None = None
        self.setFixedSize(size, size)

    def set_pixmap(self, pm, _glow: QtGui.QColor | None = None):
        self.pm = pm
        self.update()

    def paintEvent(self, e):
        if self.pm is None:
            return
        p = painter(self)
        target = QtCore.QRectF(0, 0, float(self.size_), float(self.size_))
        p.drawPixmap(target.toRect(), self.pm)


def _now_playing_text(title: str, artist: str) -> str:
    """规范化单行正在播放文本：无标题时回退'未在播放'，有歌手时以' · '连接。"""
    t = (title or "").strip() or "未在播放"
    a = (artist or "").strip()
    return f"{t} · {a}" if a else t


class NowPlayingLine(QtWidgets.QWidget, Themed):
    """双色单行正在播放信息：左对齐、同一基线、放不下时优先省略歌手。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_text = "未在播放"
        self.artist_text = ""
        self._text = "未在播放"
        self.setFixedHeight(17)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self._hook_theme()

    def set_now_playing(self, title: str, artist: str = ""):
        self.title_text = (title or "").strip() or "未在播放"
        self.artist_text = (artist or "").strip()
        self._text = _now_playing_text(self.title_text, self.artist_text)
        self.update()

    def _compute_segments(self, max_w: float) -> tuple[str, str, float, float]:
        f_title = ui_font(13.0, W.DemiBold)
        f_sub = ui_font(10.5, W.Normal)
        fm_t = QtGui.QFontMetrics(f_title)
        fm_s = QtGui.QFontMetrics(f_sub)
        max_w = max(8.0, float(max_w))

        if not self.artist_text:
            t_drawn = fm_t.elidedText(self.title_text, Qt.TextElideMode.ElideRight, int(max_w))
            return t_drawn, "", float(fm_t.horizontalAdvance(t_drawn)), 0.0

        sep_artist = f" · {self.artist_text}"
        tw_full = float(fm_t.horizontalAdvance(self.title_text))
        sw_full = float(fm_s.horizontalAdvance(sep_artist))
        if tw_full + sw_full <= max_w:
            return self.title_text, sep_artist, tw_full, sw_full

        # 放不下时：标题至少保留 60% 可用宽度；剩余宽度 < 28px 时直接隐藏歌手，标题使用全宽省略
        if tw_full <= max_w * 0.60:
            t_drawn = self.title_text
            tw_drawn = tw_full
        else:
            # 优先尽量完整保留标题，若留给歌手不足 28px，则回退到标题占 60%~max_w-28px
            rem_full = max_w - tw_full
            if rem_full >= 28.0:
                t_drawn = self.title_text
                tw_drawn = tw_full
            else:
                title_budget = max(max_w * 0.60, max_w - 28.0)
                t_drawn = fm_t.elidedText(self.title_text, Qt.TextElideMode.ElideRight, int(title_budget))
                tw_drawn = float(fm_t.horizontalAdvance(t_drawn))

        rem = max_w - tw_drawn
        if rem < 28.0:
            t_full_elide = fm_t.elidedText(self.title_text, Qt.TextElideMode.ElideRight, int(max_w))
            return t_full_elide, "", float(fm_t.horizontalAdvance(t_full_elide)), 0.0
        s_drawn = fm_s.elidedText(sep_artist, Qt.TextElideMode.ElideRight, int(rem))
        sw_drawn = float(fm_s.horizontalAdvance(s_drawn))
        return t_drawn, s_drawn, tw_drawn, sw_drawn

    def paintEvent(self, e):
        p = painter(self)
        p.setClipRect(self.rect())
        f_title = ui_font(13.0, W.DemiBold)
        f_sub = ui_font(10.5, W.Normal)
        fm_t = QtGui.QFontMetrics(f_title)
        t_drawn, s_drawn, tw_drawn, _ = self._compute_segments(self.width())
        baseline_y = (self.height() + fm_t.ascent() - fm_t.descent()) / 2.0

        p.setFont(f_title)
        p.setPen(theme.text())
        p.drawText(QtCore.QPointF(0.0, baseline_y), t_drawn)

        if s_drawn:
            p.setFont(f_sub)
            p.setPen(theme.text2())
            p.drawText(QtCore.QPointF(tw_drawn, baseline_y), s_drawn)


class MediaCard(Card):
    """紧凑横向媒体区（总高固定 68px，嵌入岛体玻璃表面，不绘制独立背景卡）。"""
    COVER_SIZE = 48

    def __init__(self, ctx, parent=None):
        super().__init__(parent, 16)
        self.ctx = ctx
        self.setFixedHeight(68)
        self.setMinimumWidth(210)
        self.cover = CoverView(self.COVER_SIZE, self)
        self._hov = Spring(0, 260, 0.6, 0.004)
        self._ht = QtCore.QTimer(self, interval=16, timeout=self._hover_tick)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        self.title = NowPlayingLine(self)

        self.prog = Progress()
        self.prev = IconButton(G.PREV, 24, 12, "上一首")
        self.play = IconButton(G.PLAY, 30, 14, "播放 / 暂停", filled=True)
        self.next = IconButton(G.NEXT, 24, 12, "下一首")

        self.prev.clicked.connect(lambda: ctx.media.prev())
        self.next.clicked.connect(lambda: ctx.media.next())
        self.play.clicked.connect(lambda: ctx.media.play_pause())
        self.prog.seek.connect(lambda s: ctx.media.seek(s))

        ctrls = hbox(None, self.prev, self.play, self.next, None, spacing=6, margins=(0, 0, 0, 0))
        right_col = vbox(self.title, self.prog, ctrls, spacing=2, margins=(0, 0, 0, 0))
        self.setLayout(hbox(self.cover, (right_col, 1), spacing=8, margins=(4, 2, 4, 2)))

        self._tick = QtCore.QTimer(self, interval=500, timeout=self.prog.update)
        self._set_cover(None)
        theme.changed.connect(self._on_theme_refresh)

    def _on_theme_refresh(self):
        if not self._has_real_cover():
            self._set_cover(None)

    def _has_real_cover(self) -> bool:
        return bool(getattr(self, "_raw_cover_img", None) is not None)

    # --- 悬停动画（仅用于进度条高亮，封面不缩放/不发光） ---
    def enterEvent(self, e):
        self._hov.to(1.0, 260, 0.6)
        self._ht.start()

    def leaveEvent(self, e):
        self._hov.to(0.0, 220, 0.8)
        self._ht.start()

    def _hover_tick(self):
        self._hov.step(0.016)
        h = max(0.0, self._hov.x)
        self.prog.hover = h
        self.prog.update()
        if self._hov.rest:
            self._ht.stop()

    def paintEvent(self, e):
        """不画独立背景，直接嵌在灵动岛玻璃上。"""
        return

    def _set_cover(self, img):
        px = rounded_pixmap(img, self.COVER_SIZE, 12) if img is not None else None
        self._raw_cover_img = img if px is not None else None
        if px is None:
            pm = QtGui.QPixmap(self.COVER_SIZE * 2, self.COVER_SIZE * 2)
            pm.fill(Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.card(1.6))
            p.drawRoundedRect(QtCore.QRectF(0, 0, self.COVER_SIZE * 2, self.COVER_SIZE * 2), 24, 24)
            p.setFont(icon_font(38))
            p.setPen(theme.text(0.65))
            p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, G.MUSIC)
            p.end()
            pm.setDevicePixelRatio(2)
            px = pm
        self.cover.set_pixmap(px)
        self.cover.update()

    def set_cover(self, img):
        self._set_cover(img)

    def set_media(self, m: dict):
        has = bool(m.get("has_session") and m.get("title"))
        self.title.set_now_playing(str(m.get("title") or ""), str(m.get("artist") or ""))
        self.play.set_glyph(G.PAUSE if m.get("playing") else G.PLAY)
        self.prog.set_state(m)
        self.prog.setVisible(True)
        self.prog.setEnabled(has and (m.get("duration") or 0) > 0)
        if m.get("playing"):
            self._tick.start()
        else:
            self._tick.stop()


# ======================================================================== 小形象
class PetWidget(QtWidgets.QWidget):
    """首页右侧小形象（区域 126px 宽，实际表情约 54–60px，居中绘制）：眼睛跟随光标，靠近时开心。"""
    def __init__(self, parent=None, scale: float = 1.15):
        super().__init__(parent)
        from ..compact import PetFace
        self.face = PetFace()
        self.scale = scale
        self.setFixedWidth(126)
        self.setMinimumHeight(68)
        self._t = QtCore.QTimer(self, interval=16, timeout=self.update)
        self._t.setTimerType(Qt.TimerType.PreciseTimer)
        theme.changed.connect(self.update)

    def showEvent(self, e):
        self._t.start()

    def hideEvent(self, e):
        self._t.stop()

    def paintEvent(self, e):
        p = painter(self)
        c = QtCore.QRectF(self.rect()).center()
        gc = QtCore.QPointF(self.mapToGlobal(c.toPoint()))
        look = QtCore.QPointF(QtGui.QCursor.pos()) - gc
        near = self.rect().adjusted(-18, -18, 18, 18).contains(self.mapFromGlobal(QtGui.QCursor.pos()))
        self.face.paint(p, c.x(), c.y(), self.scale, 1.0, look, near)


class HomePage(Page):
    key, glyph, title = "home", G.HOME, "主页"
    height, panel_width, panel_height = 98, 419, 140

    def __init__(self, ctx):
        super().__init__(ctx)
        self.header = PageHeader(ctx, self, height=26)

        self.media = MediaCard(ctx)
        self.pet = PetWidget()

        main = hbox(
            (self.media, 1),
            self.pet,
            spacing=10,
            margins=(8, 0, 8, 0),
        )
        lay = vbox(
            self.header,
            (main, 1),
            spacing=4,
            margins=(0, 0, 0, 0),
        )
        self.setLayout(lay)

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        # 展开时强制同步最新封面，避免隐藏期间错过或未重绘封面
        media_svc = getattr(self.ctx, "media", None)
        if media_svc is not None and hasattr(media_svc, "cover_image"):
            cur_img = media_svc.cover_image
            if cur_img is not getattr(self.media, "_raw_cover_img", None):
                self.media.set_cover(cur_img)

    def set_muted(self, muted: bool):
        """保留接口供外部（如 app.py 音量同步）调用，首页不再展示静音快捷按钮。"""
        _ = muted


def cfg_path():
    from ...core.config import CONFIG_PATH
    return CONFIG_PATH
