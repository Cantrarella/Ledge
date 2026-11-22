# -*- coding: utf-8 -*-
"""收起状态的内容（直接画在灵动岛上，不用子控件）。

三种内容，优先级：临时活动 > 正在播放 > 常态（时间 + 天气 + 未读小圆点）。
"""
from __future__ import annotations

import math
import time

from PySide6 import QtCore, QtGui

from .anim import clamp, ease_out, smooth
from .clock_face import (
    extract_cover_color,
    infer_weather_kind,
    measure_clock,
    measure_temperature,
    measure_vertical_clock_pair,
    measure_vertical_temperature,
    paint_clock,
    paint_temperature,
    paint_vertical_clock_pair,
    paint_vertical_temperature,
    paint_weather_icon,
    resolve_clock_palette,
    resolve_weather_colors,
)
from .icons import draw_life_svg_icon, draw_svg_icon
from .theme import KIND_COLORS, G, W, icon_font, theme, ui_font
from .widgets import rounded_pixmap

Qt = QtCore.Qt
H_IDLE = 34
H_MEDIA = 36
H_NOTIFY = 36
H_ACT = 40
H_SUB = 60          # 实时字幕：上日语、下中文两行
W_VERT_IDLE = 36
H_VERT_IDLE = 92
H_VERT_WEATHER = 136
H_VERT_BADGE_EXTRA = 12
_MIN_DISMISS_BANNER_W = 68.0
SUB_HOLD_S = 7.0    # 最后一句字幕之后保留多久再回到原来的视图
W_TEXT = 640        # 显示歌词 / 实时字幕时的胶囊宽度（约为普通收起态的 3 倍）
_IDLE_WEATHER_GAP = 14.0
_NOTIFY_LEFT_INSET = 18.0


class PetFace:
    """原创小形象（极简「发光双眼」风格）。

    常态：默认（东张西望）· 好奇（看向哪边那只眼变高）· 困倦（一分钟没人理）· 眨眼 · 惊讶（新消息）。
    鼠标靠近时随机换一种表情（不与上次重复）：
      开心 · 眨眼 · 爱心 · 星星眼 · 害羞 · 嘟嘴 · 眯眼
    s 为缩放（灵动岛上 1.0，主页上更大）。"""

    MOODS = ["happy", "wink", "love", "star", "shy", "pout", "squint"]

    def __init__(self):
        from .anim import Spring
        self.ex = Spring(0, 140, 0.8, 0.01)        # 视线
        self.ey = Spring(0, 140, 0.8, 0.01)
        self.lh = Spring(0, 160, 0.75, 0.003)      # 左眼下眼睑（开心月牙）
        self.rh = Spring(0, 160, 0.75, 0.003)      # 右眼下眼睑
        self.tired = Spring(0, 60, 1.0, 0.003)     # 上眼睑（困倦）
        self.wide = Spring(0, 220, 0.45, 0.003)    # 放大（惊讶 / 星星眼）
        self.morph = Spring(0, 200, 0.7, 0.003)    # 特殊形状（爱心 / 眯眼 / 嘟嘴）出现的程度
        self.blush = Spring(0, 120, 0.9, 0.003)
        self.mood = "default"
        self._last_mood = ""
        self._near = False
        self.blink_at = time.monotonic() + 2.5
        self.last_attention = time.monotonic()
        self.surprise_until = 0.0
        self._t = time.monotonic()
        self.spread = 0.0          # 被按压时两眼额外分开的距离（s=1 时的像素）
        self.tilt = 0.0            # 被按压时每只眼向外倾斜的角度

    # ---------- 事件 ----------
    def react(self, kind: str = "surprise"):
        self.last_attention = time.monotonic()
        if kind == "surprise":
            self.surprise_until = time.monotonic() + 1.4
            self.wide.v += 9.0

    def _pick(self):
        import random
        choices = [m for m in self.MOODS if m != self._last_mood]
        self.mood = random.choice(choices)
        self._last_mood = self.mood

    # ---------- 状态推进 ----------
    def _step(self, look: QtCore.QPointF, near: bool, s: float):
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._t))
        self._t = now
        if near and not self._near:
            self._pick()                       # 鼠标刚靠近：随机换表情
        elif not near and self._near:
            self.mood = "default"
        self._near = near
        if near:
            self.last_attention = now
        m = self.mood if near else "default"
        if now < self.surprise_until:
            m = "surprise"
        self._m = m
        # 视线
        if not near and abs(look.x()) > 420 * s:
            tx = math.sin(now * 0.55) * 0.8 + math.sin(now * 1.7) * 0.15
            ty = math.sin(now * 0.37) * 0.35
        else:
            tx = clamp(look.x() / (90.0 * s), -1, 1)
            ty = clamp(look.y() / (60.0 * s), -1, 1)
        if m == "shy":
            tx, ty = tx * 0.3 + 0.5, 0.75          # 害羞：看向斜下方
        self.ex.to(tx)
        self.ey.to(ty)
        self.lh.to({"happy": 1, "laugh": 1, "shy": 0.55, "wink": 1}.get(m, 0) if m != "wink" else 0)
        self.rh.to({"happy": 1, "laugh": 1, "shy": 0.55, "wink": 1}.get(m, 0))
        self.tired.to(0.55 if (now - self.last_attention > 60 and m == "default") else 0.0)
        self.wide.to({"star": 0.3, "surprise": 0.35}.get(m, 0.0))
        self.morph.to(1.0 if m in ("love", "squint", "pout") else 0.0)
        self.blush.to({"shy": 1.0, "love": 0.8, "happy": 0.5, "laugh": 0.6, "wink": 0.5}.get(m, 0.0))
        for sp in (self.ex, self.ey, self.lh, self.rh, self.tired, self.wide, self.morph, self.blush):
            sp.step(dt)
        # 眨眼（特殊表情时不眨）
        dur = 0.16 + 0.18 * self.tired.x
        if now >= self.blink_at + dur:
            gap = 2.4 + (math.sin(now * 12.9898) * 0.5 + 0.5) * 3.2
            self.blink_at = now + gap * (1.6 if self.tired.x > 0.3 else 1.0)
        b = 0.0
        if self.blink_at <= now < self.blink_at + dur and m in ("default", "pout", "shy", "star"):
            b = math.sin((now - self.blink_at) / dur * math.pi)
        return b

    # ---------- 绘制 ----------
    @staticmethod
    def _heart(cx, cy, size) -> QtGui.QPainterPath:
        p = QtGui.QPainterPath()
        w = size
        p.moveTo(cx, cy + w * 0.42)
        p.cubicTo(cx - w * 0.9, cy - w * 0.05, cx - w * 0.45, cy - w * 0.75, cx, cy - w * 0.28)
        p.cubicTo(cx + w * 0.45, cy - w * 0.75, cx + w * 0.9, cy - w * 0.05, cx, cy + w * 0.42)
        p.closeSubpath()
        return p

    @staticmethod
    def _sparkle(cx, cy, r) -> QtGui.QPainterPath:
        p = QtGui.QPainterPath()
        p.moveTo(cx, cy - r)
        p.quadTo(cx, cy, cx + r, cy)
        p.quadTo(cx, cy, cx, cy + r)
        p.quadTo(cx, cy, cx - r, cy)
        p.quadTo(cx, cy, cx, cy - r)
        return p

    def paint(self, p: QtGui.QPainter, cx: float, cy: float, s: float, a: float,
              look: QtCore.QPointF, near: bool):
        b = self._step(look, near, s)
        m = getattr(self, "_m", "default")
        now = time.monotonic()
        tired = clamp(self.tired.x, 0, 1)
        wide = clamp(self.wide.x, -0.3, 1.0)
        morph = clamp(self.morph.x, 0, 1)
        lx, ly = self.ex.x, self.ey.x
        bounce = math.sin(now * 16) * 1.6 * s if m == "laugh" else 0.0
        base_w, base_h, gap = 11.0 * s, 15.0 * s, 15.0 * s
        eye_col = theme.text(0.95 * a)
        glow_col = QtGui.QColor(theme.on_color()) if theme.dark else QtGui.QColor(0, 0, 0, 0)
        pink = QtGui.QColor(255, 92, 138)
        centers = []
        for side in (-1, 1):
            p.save()
            try:
                # 被按压时两只眼睛各自向外倾斜（按在中间时最明显）
                _ex0 = cx + side * (gap / 2 + base_w / 2 + self.spread * s / 2) + self.ex.x * 5.0 * s
                _ey0 = cy + self.ey.x * 3.0 * s
                if abs(self.tilt) > 0.05:
                    p.translate(_ex0, _ey0)
                    p.rotate(side * self.tilt)
                    p.translate(-_ex0, -_ey0)
                hap = clamp(self.lh.x if side < 0 else self.rh.x, 0, 1)
                curious = 1.0 + 0.22 * max(0.0, lx * side)
                w = base_w * (1 + 0.12 * wide) * (1 + 0.25 * b)
                h = base_h * curious * (1 + 0.18 * wide) * (1 - 0.88 * b)
                ex = cx + side * (gap / 2 + base_w / 2 + self.spread * s / 2) + lx * 5.0 * s
                ey = cy + ly * 3.0 * s + bounce
                centers.append((ex, ey))
                r = QtCore.QRectF(ex - w / 2, ey - h / 2, w, max(1.6 * s, h))
                # 光晕
                if glow_col.alpha() and a > 0.05:
                    g = QtGui.QRadialGradient(r.center(), max(r.width(), r.height()) * 0.85)
                    c1 = QtGui.QColor(pink if m == "love" else glow_col)
                    c1.setAlpha(round(85 * a))
                    c2 = QtGui.QColor(c1)
                    c2.setAlpha(0)
                    g.setColorAt(0.0, c1)
                    g.setColorAt(1.0, c2)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(g)
                    p.drawEllipse(r.center(), r.width() * 0.95, r.height() * 0.75 + 3 * s)
                # 特殊形状：爱心 / 眯眼 ><
                if m == "love" and morph > 0.05:
                    pulse = 1.0 + 0.08 * math.sin(now * 7)
                    p.setPen(Qt.PenStyle.NoPen)
                    hc = QtGui.QColor(pink)
                    hc.setAlphaF(a * morph)
                    p.setBrush(hc)
                    p.drawPath(self._heart(ex, ey, 15 * s * pulse * (0.6 + 0.4 * morph)))
                    continue
                if m == "squint" and morph > 0.05:
                    pen = QtGui.QPen(eye_col, 2.6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    d = -side                               # 左眼 ">"、右眼 "<"
                    k = 5.5 * s * morph
                    path = QtGui.QPainterPath()
                    path.moveTo(ex - d * k, ey - k)
                    path.lineTo(ex + d * k, ey)
                    path.lineTo(ex - d * k, ey + k)
                    p.drawPath(path)
                    continue
                rad = min(w, r.height()) * 0.42
                eye = QtGui.QPainterPath()
                eye.addRoundedRect(r, rad, rad)
                if tired > 0.01:
                    lid = QtGui.QPainterPath()
                    lid.addRect(QtCore.QRectF(r.left() - 2, r.top() - 2, r.width() + 4, 2 + r.height() * 0.5 * tired))
                    eye = eye.subtracted(lid)
                if m == "pout" and morph > 0.05:
                    # 嘟嘴：内侧上眼角斜切出"小眉头"
                    lid = QtGui.QPainterPath()
                    lid.addRect(QtCore.QRectF(-r.width(), -r.height(), r.width() * 2.2, r.height() * 1.0))
                    tr = QtGui.QTransform()
                    tr.translate(r.center().x() - side * r.width() * 0.1, r.top() + r.height() * 0.32 * morph)
                    tr.rotate(side * -24 * morph)
                    eye = eye.subtracted(tr.map(lid))
                if hap > 0.01:
                    cut_r = r.width() * 0.9
                    cc = QtCore.QPointF(r.center().x(), r.bottom() + cut_r - r.height() * 0.62 * hap)
                    lid = QtGui.QPainterPath()
                    lid.addEllipse(cc, cut_r, cut_r)
                    eye = eye.subtracted(lid)
                if not eye.isEmpty():
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(eye_col)
                    p.drawPath(eye)
                if m == "star" and r.height() > 6 * s:
                    sc = QtGui.QColor(20, 20, 26, round(220 * a)) if not theme.dark else QtGui.QColor(30, 30, 40, round(200 * a))
                    p.setBrush(sc)
                    p.drawPath(self._sparkle(ex + 1.4 * s, ey - 2.2 * s, 3.4 * s))
                    p.drawPath(self._sparkle(ex - 2.2 * s, ey + 3.0 * s, 1.6 * s))

            finally:
                p.restore()
        # 腮红
        bl = clamp(self.blush.x, 0, 1)
        if bl > 0.02:
            for (ex, ey), side in zip(centers, (-1, 1)):
                c = QtGui.QColor(255, 124, 162, round(150 * bl * a))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawEllipse(QtCore.QPointF(ex + side * 7.5 * s, ey + base_h * 0.55), 4.4 * s, 2.4 * s)
        # 嘴
        mx = cx + lx * 5.0 * s
        my = cy + base_h * 0.66 + ly * 3.0 * s + bounce
        mc = theme.text(0.9 * a)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QtGui.QPen(mc, 1.8 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        if m in ("happy", "wink", "love", "shy"):
            path = QtGui.QPainterPath()
            mw = 7.0 * s
            path.moveTo(mx - mw / 2, my)
            path.quadTo(mx, my + 4.2 * s, mx + mw / 2, my)
            p.setPen(pen)
            p.drawPath(path)
        elif m == "laugh":
            path = QtGui.QPainterPath()
            mw = 9.0 * s
            path.moveTo(mx - mw / 2, my - 0.5 * s)
            path.quadTo(mx, my + 8.5 * s, mx + mw / 2, my - 0.5 * s)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc)
            p.drawPath(path)
        elif m in ("star", "surprise"):
            p.setPen(pen)
            p.drawEllipse(QtCore.QPointF(mx, my + 1.5 * s), 2.4 * s, 2.8 * s)
        elif m == "pout":
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(mx - 3 * s, my + 1.5 * s), QtCore.QPointF(mx + 3 * s, my + 1.5 * s))
        elif m == "squint":
            path = QtGui.QPainterPath()
            mw = 6.0 * s
            path.moveTo(mx - mw / 2, my)
            path.quadTo(mx, my + 3.5 * s, mx + mw / 2, my)
            p.setPen(pen)
            p.drawPath(path)


class CompactView:
    def __init__(self):
        self.media: dict = {}
        self.cover: QtGui.QPixmap | None = None
        self.activity: dict | None = None
        self._activity_seq: int = 0
        self._notify_dismiss_rect: QtCore.QRectF | None = None
        self._dismiss_rect_seq: int = -1
        self.notify_dismiss_hover: float = 0.0
        self.badges: dict[str, int] = {}
        self.weather: tuple | None = None   # (glyph, "18°", font_family)
        self.edge: str = "top"
        self._mode = "idle"
        self._mode_t = -10.0
        self._full_state: tuple = ("horizontal", "idle", "", 0)
        self._last_horiz_snap: dict | None = None
        self._outgoing_horiz_snap: dict | None = None
        self._outgoing_t0: float = -100.0
        self._last_vert_vol_snap: dict | None = None
        self._outgoing_vert_vol_snap: dict | None = None
        self._outgoing_vert_vol_t0: float = -100.0
        self._banner_pinned_rest_h: float | None = None
        self._wea_h0: float = 36.0
        self.on_state_changed = None
        # 收起态视图（滚轮切换）：clock = 时间天气，pet = 可爱形象，music = 音乐
        self.views = ["clock", "pet", "music"]
        self.view = "clock"
        self._view_t = -10.0
        self.look = QtCore.QPointF(0, 0)      # 光标相对胶囊中心的位置（形象的眼睛会看过去）
        self.near = False                      # 光标在附近（形象开心）
        self.pet = PetFace()
        self._bars = [0.0] * 4
        self._peak = 0.0
        # 歌词（由 app 设置：services.lyrics.Lyrics 或 None）
        self.lyrics = None
        self._lyr_w = 0.0
        self._lyr_idx = -2
        self._lyr_prev = ""
        self._lyr_t = 0.0
        self._lyr_cur = ""
        self._lyr_font = None
        self._lyr_fm = None
        self._lyr_zoom_font = None
        self._lyr_full_cache: tuple[str, int] = ("", 0)
        self._lyr_word_cache: tuple[str, str, float, float, float] = ("", "", 0.0, 0.0, 0.0)
        # 实时字幕（收起态两行：日语 / 中文）
        self.sub_on = False
        self.sub_jp = ""
        self.sub_zh = ""
        self.sub_t = -100.0
        # 收起态音乐视图右侧的播放 / 暂停键（灵动岛坐标，绘制时更新）
        self.play_rect: QtCore.QRectF | None = None
        self.play_hover = 0.0
        # 收起态时间样式与配色
        self.clock_style: str = "minimal"
        self.clock_color: str = "white"
        self.clock_custom_color: str = "#FFBF5E"
        self.clock_gradient: list[str] = ["#5AE0FF", "#FF6BA8"]
        self.show_weather: bool = True
        self.cover_color: QtGui.QColor | None = None

    def set_clock_appearance(
        self,
        style: str = "minimal",
        color: str = "white",
        custom_color: str = "#FFBF5E",
        gradient: list[str] | None = None,
        show_weather: bool = True,
    ):
        """同步收起态时间字体风格与配色配置。"""
        self.clock_style = str(style or "minimal")
        self.clock_color = str(color or "white")
        self.clock_custom_color = str(custom_color or "#FFBF5E")
        self.show_weather = bool(show_weather)
        if isinstance(gradient, (list, tuple)) and len(gradient) >= 2:
            self.clock_gradient = [str(x) for x in gradient[:3]]
        else:
            self.clock_gradient = ["#5AE0FF", "#FF6BA8"]

    # --- 数据 ---
    def set_media(self, m: dict):
        self.media = m or {}

    def set_lyrics(self, lyr):
        """设置当前歌曲的歌词；保持与歌名一致的紧凑胶囊宽度，长句通过横向滚动展示。"""
        self.lyrics = lyr if lyr else None
        self._lyr_idx, self._lyr_prev = -2, ""
        self._lyr_w = 0.0

    def media_pos(self) -> float:
        m = self.media
        pos = float(m.get("position") or 0)
        if m.get("playing"):
            pos += time.monotonic() - float(m.get("stamp") or time.monotonic())
        dur = float(m.get("duration") or 0)
        return min(pos, dur) if dur > 0 else pos

    def lyric_now(self) -> tuple[int, str, str]:
        """-> (行号, 歌词, 翻译)；没有歌词 / 没在播放时行号为 -1。"""
        if not self.lyrics or not self.media.get("title"):
            return -1, "", ""
        return self.lyrics.at(self.media_pos())

    # --- 实时字幕 ---
    def set_sub_running(self, on: bool):
        self.sub_on = bool(on)
        if not on:
            self.sub_jp = self.sub_zh = ""
            self.sub_t = -100.0

    def set_sub(self, jp: str | None = None, zh: str | None = None):
        if jp is not None:
            self.sub_jp = jp
        if zh is not None:
            self.sub_zh = zh
        self.sub_t = time.monotonic()

    def sub_active(self) -> bool:
        return self.sub_on and bool(self.sub_jp or self.sub_zh) and time.monotonic() - self.sub_t < SUB_HOLD_S

    def set_cover(self, img):
        self.cover = rounded_pixmap(img, 26, 7) if img is not None else None
        self.cover_color = extract_cover_color(img) if img is not None else None

    def _clear_interactive_rects(self):
        self.play_rect = None
        self._notify_dismiss_rect = None
        self._dismiss_rect_seq = -1
        self.play_hover = 0.0
        self.notify_dismiss_hover = 0.0

    def set_edge(self, edge: str):
        e = str(edge or "top")
        if e not in ("top", "bottom", "left", "right"):
            e = "top"
        if e != self.edge:
            self.edge = e
            self.layout_state()
            if self._outgoing_horiz_snap is None:
                self._mode_t = -10.0

    def has_outgoing_horizontal(self, cur_w: float | None = None) -> bool:
        """当前是否仍处于横向横幅向竖时钟回收的过渡期。"""
        if self._outgoing_horiz_snap is None:
            return False
        if (time.monotonic() - self._outgoing_t0) >= 0.45:
            self._outgoing_horiz_snap = None
            return False
        if cur_w is not None and float(cur_w) <= 36.2:
            self._outgoing_horiz_snap = None
            return False
        return True

    def current_activity_seq(self) -> int:
        if self.activity is None:
            return 0
        return int(self.activity.get("seq") or 0)

    def show_activity(self, act: dict, duration: float = 2.4):
        now = time.monotonic()
        self._activity_seq += 1
        a = dict(act)
        a["seq"] = self._activity_seq
        a["until"] = now + duration
        a["start"] = now
        self.activity = a
        self._clear_interactive_rects()
        self.layout_state()

    def update_activity(self, **kw):
        """原地更新（例如连续调音量时不重新入场）。"""
        if self.activity:
            self.activity.update(kw)

    def clear_activity(self, act_type: str | None = None):
        if self.activity is None:
            return
        if act_type is None or self.activity.get("type") == act_type:
            self.activity = None
            self._clear_interactive_rects()
            self.layout_state()

    def set_view(self, v: str):
        if v in self.views:
            self.view = v

    def cycle_view(self, d: int):
        i = self.views.index(self.view) if self.view in self.views else 0
        self.view = self.views[(i + (1 if d < 0 else -1)) % len(self.views)]
        self._view_t = time.monotonic()
        return self.view

    def playing(self) -> bool:
        return bool(self.media.get("has_session") and self.media.get("playing") and self.media.get("title"))

    def layout_state(self) -> tuple[str, str]:
        """返回由停靠边和内容模式共同决定的显式布局状态 (orientation, mode)。"""
        now = time.monotonic()
        if self.activity and now < self.activity["until"]:
            m = "activity"
        elif self.sub_active():
            m = "subtitle"            # 有人在说话时字幕优先；安静几秒后回到原视图（音乐 / 时间 / 小形象）
            self.activity = None
        else:
            self.activity = None
            if self.edge in ("left", "right"):
                # 首版侧边支持竖时钟与竖版音乐视图，不显示小表情（保留 self.view 偏好待拖回顶/底边后恢复）
                m = "media" if self.view == "music" else "idle"
            else:
                m = {"music": "media", "pet": "pet"}.get(self.view, "idle")

        act_type = str((self.activity or {}).get("type") or "") if m == "activity" else ""
        act_seq = int((self.activity or {}).get("seq") or 0) if m == "activity" else 0
        is_side_vert = (self.edge in ("left", "right")) and (
            m in ("idle", "media") or (m == "activity" and act_type == "volume")
        )
        orientation = "vertical" if is_side_vert else "horizontal"
        full_state = (orientation, m, act_type, act_seq)

        prev_orient, prev_m, _prev_t, _prev_s = self._full_state
        if full_state != self._full_state:
            if prev_orient == "horizontal" and prev_m in ("activity", "subtitle") and orientation == "vertical":
                self._wea_h0 = 36.0
                self._outgoing_vert_vol_snap = None
                if self._last_horiz_snap is not None:
                    self._outgoing_horiz_snap = dict(self._last_horiz_snap)
                    self._outgoing_t0 = now
            elif prev_orient == "vertical" and prev_m == "activity" and _prev_t == "volume" and orientation == "vertical" and m in ("idle", "media"):
                self._outgoing_horiz_snap = None
                self._wea_h0 = float(H_VERT_IDLE)
                if self._last_vert_vol_snap is not None:
                    self._outgoing_vert_vol_snap = dict(self._last_vert_vol_snap)
                    self._outgoing_vert_vol_t0 = now
            elif orientation == "horizontal":
                self._outgoing_horiz_snap = None
                self._outgoing_vert_vol_snap = None
                if m in ("activity", "subtitle") and prev_orient == "vertical":
                    self._banner_pinned_rest_h = self.vertical_rest_height()
            elif orientation == "vertical":
                self._outgoing_horiz_snap = None
                self._outgoing_vert_vol_snap = None
                self._wea_h0 = float(H_VERT_IDLE)
            self._full_state = full_state
            self._mode = m
            self._mode_t = now
            self._clear_interactive_rects()
            if callable(self.on_state_changed):
                self.on_state_changed(full_state)
        elif m != self._mode:
            self._mode = m
            self._mode_t = now

        if orientation == "horizontal":
            if m == "activity" and self.activity is not None:
                self._last_horiz_snap = {"mode": "activity", "activity": dict(self.activity)}
            elif m == "subtitle":
                self._last_horiz_snap = {"mode": "subtitle", "sub_jp": self.sub_jp, "sub_zh": self.sub_zh}
        elif orientation == "vertical" and m == "activity" and act_type == "volume" and self.activity is not None:
            self._last_vert_vol_snap = dict(self.activity)
        return orientation, m

    def mode(self) -> str:
        return self.layout_state()[1]

    def busy(self) -> bool:
        """是否需要持续 60FPS 动画帧。"""
        orientation, m = self.layout_state()
        if orientation == "vertical":
            if m == "media":
                return (
                    bool(self.media.get("playing"))
                    or max(self._bars) > 0.20
                    or (time.monotonic() - self._mode_t) < 0.4
                )
            return m == "activity" or (time.monotonic() - self._mode_t) < 0.4
        if m == "media":
            return (
                bool(self.media.get("playing"))
                or max(self._bars) > 0.20
                or (time.monotonic() - self._lyr_t < 0.38)
                or (time.monotonic() - self._view_t < 1.4)
            )
        return m in ("activity", "pet", "subtitle") or time.monotonic() - self._view_t < 1.4

    # --- 尺寸 ---
    def vertical_rest_height(self) -> float:
        """返回左右侧边稳定收起态竖条的目标高度（音乐态与时间+天气竖条统一为 136px，含未读点 +12px）。"""
        nb = sum(1 for v in self.badges.values() if v)
        extra_h = H_VERT_BADGE_EXTRA if nb > 0 else 0
        if self.edge in ("left", "right") and self.view == "music":
            return float(H_VERT_WEATHER + extra_h)
        base_h = H_VERT_WEATHER if (self.weather and self.show_weather) else H_VERT_IDLE
        return float(base_h + extra_h)

    def pin_banner_anchor_height(self, h: float):
        """在拖拽横幅松手时更新横幅锁定的竖条沿边锚定高度。"""
        self._banner_pinned_rest_h = float(h)

    def banner_anchor_height(self) -> float:
        """返回横幅显示期间锁定的竖条沿边锚定高度（防止通知显示中首次收到天气导致横幅跳位）。"""
        if self._banner_pinned_rest_h is not None:
            return float(self._banner_pinned_rest_h)
        return self.vertical_rest_height()

    def size_hint(self, avail_max_w: float | None = None) -> tuple[float, float]:
        orientation, m = self.layout_state()
        nb = sum(1 for v in self.badges.values() if v)
        if orientation == "vertical":
            return float(W_VERT_IDLE), self.vertical_rest_height()

        def _cap_w(raw_w: float) -> float:
            if avail_max_w is None:
                return float(raw_w)
            return float(clamp(float(raw_w), float(W_VERT_IDLE), max(float(W_VERT_IDLE), float(avail_max_w))))

        if m == "activity":
            a = self.activity
            t = a.get("type")
            if t == "volume":
                return _cap_w(280.0), float(H_ACT)
            if t == "life-reminder":
                fm_t = QtGui.QFontMetrics(ui_font(12.5, W.DemiBold))
                fm_s = QtGui.QFontMetrics(ui_font(12))
                tw = float(math.ceil(fm_t.horizontalAdvance(a.get("title", ""))) + 2)
                sub = a.get("sub", "")
                sw = (8.0 + float(math.ceil(fm_s.horizontalAdvance(sub)) + 2)) if sub else 0.0
                # 左边距9 + 图标24 + 间距9 + 标题 + 副标题 + 间距8 + 关闭按钮24 + 右边距6
                w = 9 + 24 + 9 + tw + sw + 8 + 24 + 6
                return _cap_w(clamp(w, 220.0, 440.0)), float(H_NOTIFY)
            if t == "notify":
                fm_app = QtGui.QFontMetrics(ui_font(12.5, W.DemiBold))
                fm_sub = QtGui.QFontMetrics(ui_font(12))
                app_w = min(108.0, float(math.ceil(fm_app.horizontalAdvance(a.get("title", ""))) + 2))
                sub = a.get("sub", "") or "有新消息"
                sep_w = float(fm_sub.horizontalAdvance(" · "))
                sub_w = float(fm_sub.horizontalAdvance(sub))
                # 左边距 + 图标24 + 间距9 + 应用名 + 分隔 + 摘要 + 间距8 + 关闭按钮24 + 右边距6
                w = _NOTIFY_LEFT_INSET + 24 + 9 + app_w + sep_w + sub_w + 8 + 24 + 6
                return _cap_w(clamp(w, 190.0, 360.0)), float(H_NOTIFY)
            fm = QtGui.QFontMetrics(ui_font(13, W.DemiBold))
            tw = float(math.ceil(fm.horizontalAdvance(a.get("title", ""))) + 2)
            sub_txt = a.get("sub", "")
            sw = (8.0 + float(math.ceil(QtGui.QFontMetrics(ui_font(12)).horizontalAdvance(sub_txt)) + 2)) if sub_txt else 0.0
            content_w = 22.0 + 6.0 + tw + sw
            return _cap_w(clamp(content_w + 36.0, 132.0, 460.0)), float(H_ACT)
        if m == "subtitle":
            return _cap_w(W_TEXT + nb * 12), float(H_SUB)           # 固定宽度：说话时不会一直伸缩
        if m == "media":
            if not self.playing() and not self.media.get("title"):
                return _cap_w(190 + nb * 12), float(H_MEDIA)
            if self.lyrics and self.lyrics.lines:
                return _cap_w(334 + nb * 12), float(H_MEDIA)
            title = self.media.get("title", "")
            artist = self.media.get("artist", "")
            text = f"{title}  ·  {artist}" if artist else title
            tw = QtGui.QFontMetrics(ui_font(13, W.Medium)).horizontalAdvance(text) if text else 0
            w = clamp(math.ceil(tw) + 128, 190, 334)
            return _cap_w(w + nb * 12), float(H_MEDIA)
        if m == "pet":
            return _cap_w(134 + nb * 13), float(H_IDLE)
        clock_w = measure_clock(self.clock_style, "88:88")["total_w"]
        if self.weather and self.show_weather:
            temp_w = max(
                measure_temperature(self.clock_style, "-88°")["total_w"],
                measure_temperature(self.clock_style, "100°")["total_w"],
                measure_temperature(self.clock_style, self.weather[1])["total_w"],
            )
            weather_w = 15.0 + 4.5 + temp_w
            base_w = max(146, int(math.ceil(clock_w + _IDLE_WEATHER_GAP + weather_w + 36)))
        else:
            base_w = max(96, int(math.ceil(clock_w + 36)))
        # 对称扩展胶囊宽度：时间天气始终以整个胶囊中心居中，同时右侧预留色点空间
        extra_w = 2 * (8 + nb * 4 + max(0, nb - 1) * 5.5) if nb else 0
        return _cap_w(base_w + extra_w), float(H_IDLE)

    def _karaoke_glow_strength(self, idx: int, pos_now: float, cur_frac: float) -> float:
        """计算当前歌词交界微光强度；暂停态下返回 0，避免亮边定格在原地。"""
        if not self.playing() or self.lyrics is None:
            return 0.0

        def _ease(v: float) -> float:
            v = clamp(v, 0.0, 1.0)
            return v * v * (3.0 - 2.0 * v)

        words_line = self.lyrics.words[idx] if idx < len(self.lyrics.words) else None
        if words_line:
            for wi, (ws, we, _tok) in enumerate(words_line):
                prev_we = words_line[wi - 1][1] if wi > 0 else None
                next_ws = words_line[wi + 1][0] if wi + 1 < len(words_line) else None
                if ws <= pos_now < we:
                    prog = (pos_now - ws) / max(0.02, we - ws)
                    fade_in = 1.0 if (prev_we is not None and ws - prev_we <= 0.14) else _ease(prog / 0.08)
                    fade_out = 1.0 if (next_ws is not None and next_ws - we <= 0.14) else _ease((1.0 - prog) / 0.12)
                    return 0.72 * fade_in * fade_out
                if pos_now < ws:
                    if prev_we is not None and (ws - prev_we) <= 0.14:
                        # 短间隔内微光直接顺滑衔接滑向下一词
                        return 0.72
                    return 0.0
        elif 0.0 < cur_frac < 1.0:
            return 0.60 * _ease(cur_frac / 0.08) * _ease((1.0 - cur_frac) / 0.12)
        return 0.0

    # --- 绘制 ---
    def _paint_outgoing_horizontal(self, p: QtGui.QPainter, r: QtCore.QRectF, out_a: float, snap: dict):
        """在横幅向竖时钟回收的过渡初期绘制旧横幅残影淡出，不生成任何点击热区。"""
        if out_a <= 0.015 or r.width() < 44.0:
            return
        p.save()
        p.setClipRect(r.adjusted(6.0, 0.0, -6.0, 0.0), Qt.ClipOperation.IntersectClip)
        sm = snap.get("mode")
        if sm == "activity" and snap.get("activity"):
            saved_act = self.activity
            saved_dr = self._notify_dismiss_rect
            saved_seq = self._dismiss_rect_seq
            try:
                self.activity = snap["activity"]
                self._paint_activity(p, r, out_a)
            finally:
                self.activity = saved_act
                self._notify_dismiss_rect = saved_dr
                self._dismiss_rect_seq = saved_seq
        elif sm == "subtitle":
            saved_jp, saved_zh = self.sub_jp, self.sub_zh
            try:
                self.sub_jp = str(snap.get("sub_jp") or "")
                self.sub_zh = str(snap.get("sub_zh") or "")
                self._paint_subtitle(p, r, out_a)
            finally:
                self.sub_jp, self.sub_zh = saved_jp, saved_zh
        p.restore()

    def paint(self, p: QtGui.QPainter, r: QtCore.QRectF, alpha: float, peak: float):
        if alpha <= 0.01 or r.width() < 18.0 or r.height() < 18.0:
            return
        orientation, m = self.layout_state()
        now = time.monotonic()
        # 切换首帧保留 0.28 的可见底值，杜绝状态切换当帧(dt=0)出现内容全透明的空白帧
        fade = 0.28 + 0.72 * ease_out((now - self._mode_t) / 0.26)
        a = alpha * fade
        self._peak = self._peak * 0.6 + peak * 0.4
        p.save()
        if orientation == "vertical":
            if m != "media":
                self.play_rect = None
            self._notify_dismiss_rect = None
            self._dismiss_rect_seq = -1
            if m == "activity" and (self.activity or {}).get("type") == "volume":
                p.setClipRect(r.adjusted(3.0, 6.0, -3.0, -6.0), Qt.ClipOperation.IntersectClip)
                dt_in = now - self._mode_t
                if dt_in < 0.16:
                    prev_out_a = alpha * (1.0 - ease_out(dt_in / 0.16))
                    if prev_out_a > 0.015:
                        if self.view == "music":
                            self._paint_media_vertical(p, r, prev_out_a, update_hit=False)
                        else:
                            self._paint_idle_vertical(p, r, prev_out_a)
                self._paint_volume_vertical(p, r, a, self.activity)
                p.restore()
                return
            if self._outgoing_horiz_snap is not None:
                dt_out = now - self._outgoing_t0
                w_out = clamp((float(r.width()) - 46.0) / 72.0, 0.0, 1.0)
                t_out = 1.0 - ease_out(dt_out / 0.16)
                out_a = alpha * t_out * w_out
                if out_a > 0.015 and dt_out < 0.16:
                    self._paint_outgoing_horizontal(p, r, out_a, self._outgoing_horiz_snap)
                    self._notify_dismiss_rect = None
                    self._dismiss_rect_seq = -1
                elif float(r.width()) <= 36.2 or dt_out >= 0.45:
                    self._outgoing_horiz_snap = None
            p.setClipRect(r.adjusted(3.0, 6.0, -3.0, -6.0), Qt.ClipOperation.IntersectClip)
            if self._outgoing_vert_vol_snap is not None:
                dt_v = now - self._outgoing_vert_vol_t0
                if dt_v < 0.18:
                    v_out_a = alpha * (1.0 - ease_out(dt_v / 0.18))
                    if v_out_a > 0.015:
                        self._paint_volume_vertical(p, r, v_out_a, self._outgoing_vert_vol_snap)
                else:
                    self._outgoing_vert_vol_snap = None
            if m == "media":
                self._paint_media_vertical(p, r, a, update_hit=True)
            else:
                self._paint_idle_vertical(p, r, a)
            p.restore()
            return

        p.setClipRect(r.adjusted(6, 0, -6, 0), Qt.ClipOperation.IntersectClip)
        if m != "media":
            self.play_rect = None
        if m != "activity":
            self._notify_dismiss_rect = None
            self._dismiss_rect_seq = -1
        if m == "activity":
            self._paint_activity(p, r, a)
        elif m == "subtitle":
            self._paint_subtitle(p, r, a)
        elif m == "media":
            if not self.media.get("title"):
                self._paint_no_media(p, r, a)
            else:
                self._paint_media(p, r, a)
        elif m == "pet":
            self._paint_pet(p, r, a)
        else:
            self._paint_idle(p, r, a)
        p.restore()

    def _paint_subtitle(self, p, r, a):
        """两行字幕：上面日语（小字，较淡），下面中文（大字）；太长时保留结尾（最新的话）。"""
        x = r.x() + 18
        w = r.width() - 36 - (self._badges_w() if any(self.badges.values()) else 0)
        if any(self.badges.values()):
            self._badges(p, r.right() - 14, r.y() + 16, a)
        cy = r.center().y()
        fj, fz = ui_font(12), ui_font(15, W.DemiBold)
        fmj, fmz = QtGui.QFontMetrics(fj), QtGui.QFontMetrics(fz)

        def tail(fm, text):
            text = " ".join((text or "").split())
            if fm.horizontalAdvance(text) <= w:
                return text
            return fm.elidedText(text, Qt.TextElideMode.ElideLeft, int(w))
        jp = tail(fmj, self.sub_jp)
        zh = tail(fmz, self.sub_zh) or "…"
        top = QtCore.QRectF(x, cy - 22, w, 18)
        bot = QtCore.QRectF(x, cy - 3, w, 24)
        p.setFont(fj)
        p.setPen(theme.text(0.62 * a))
        p.drawText(top, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, jp)
        p.setFont(fz)
        p.setPen(theme.text(a))
        p.drawText(bot, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, zh)

    def _badges_w(self) -> float:
        return 12 + 12 * sum(1 for v in self.badges.values() if v)

    def _paint_no_media(self, p, r, a):
        txt = "未在播放"
        f_txt = ui_font(13, W.Medium)
        txt_w = float(QtGui.QFontMetrics(f_txt).horizontalAdvance(txt))
        icon_w = 18.0
        gap = 7.0
        x0 = r.center().x() - (icon_w + gap + txt_w) / 2.0
        p.setFont(icon_font(14))
        p.setPen(theme.text(0.75 * a))
        p.drawText(QtCore.QRectF(x0, r.y(), icon_w, r.height()), Qt.AlignmentFlag.AlignCenter, G.MUSIC)
        p.setFont(f_txt)
        p.setPen(theme.text2(a))
        p.drawText(QtCore.QRectF(x0 + icon_w + gap, r.y(), txt_w + 2.0, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)

    def _paint_pet(self, p, r, a):
        cx, cy = r.center().x(), r.center().y()
        sx, sy = getattr(self, "squash", (1.0, 1.0))
        amount, rel = getattr(self, "press", (0.0, 0.0))
        amount = clamp(amount, 0.0, 1.2)
        side_w = abs(rel)                     # 0 = 按在中间，1 = 按在一端
        lean = -rel * min(1.0, amount * 1.6)   # 向没被按的一侧倾斜
        self.pet.spread = amount * (1 - side_w) * 9.0
        self.pet.tilt = amount * (1 - side_w) * 14.0
        p.save()
        p.translate(cx, cy)
        p.translate(lean * 24.0, 0)            # 明显地偏向没被按的一侧
        p.rotate(lean * 18.0)
        p.scale(sx, sy)                       # 灵动岛被按压时表情一起被压扁
        p.translate(-cx, -cy)
        self.pet.paint(p, cx, cy - 1, 1.0, a, self.look, self.near)
        p.restore()
        x = r.right() - 14
        self._badges(p, x, cy, a)

    def _badges(self, p, right: float, cy: float, a: float) -> float:
        """在岛体最右侧按固定顺序（1. QQ  2. 微信  3. 邮件）绘制未读色点（直径约 3.8px，间距 5.5px）。"""
        active = [k for k in ("qq", "wechat", "mail") if self.badges.get(k)]
        if not active:
            return right
        dot_d = 3.8
        gap = 5.5
        total_w = len(active) * dot_d + max(0, len(active) - 1) * gap
        x = right - total_w
        p.setPen(Qt.PenStyle.NoPen)
        for kind in active:
            c = QtGui.QColor(KIND_COLORS.get(kind, KIND_COLORS["other"]))
            c.setAlphaF(a)
            p.setBrush(c)
            p.drawEllipse(QtCore.QPointF(x + dot_d / 2.0, cy), dot_d / 2.0, dot_d / 2.0)
            x += dot_d + gap
        return right - total_w

    @staticmethod
    def _tabular_font(px: float, weight: QtGui.QFont.Weight) -> QtGui.QFont:
        f = ui_font(px, weight, num=True)
        try:
            f.setFeature(QtGui.QFont.Tag.fromString("tnum"), 1)
        except Exception:
            pass
        return f

    def _idle_layout_metrics(self, r: QtCore.QRectF) -> dict:
        """基于固定数字槽宽计算收起态时间与天气整体居中的几何参数。"""
        txt = getattr(self, "_clock_override", None) or time.strftime("%H:%M", time.localtime())
        cm = measure_clock(self.clock_style, txt)
        ft = cm["font"] or self._tabular_font(15, W.DemiBold)
        clock_w = float(cm["total_w"])
        if not self.weather or not self.show_weather:
            sx = r.center().x() - clock_w / 2.0
            return {
                "txt": txt,
                "ft": ft,
                "clock_w": clock_w,
                "clock_rect": QtCore.QRectF(sx, r.y(), clock_w + 1.0, r.height()),
                "has_weather": False,
                "group_left": sx,
                "content_total": clock_w,
                "gap": 0.0,
            }
        glyph, temp = self.weather[0], self.weather[1]
        fam = self.weather[2] if len(self.weather) > 2 else None
        icon_code = self.weather[3] if len(self.weather) > 3 else ""
        tm = measure_temperature(self.clock_style, temp)
        temp_w = float(tm["total_w"])
        icon_w = 15.0
        icon_temp_gap = 4.5
        weather_w = icon_w + icon_temp_gap + temp_w
        gap = _IDLE_WEATHER_GAP
        content_total = clock_w + gap + weather_w
        group_left = r.center().x() - content_total / 2.0
        clock_rect = QtCore.QRectF(group_left, r.y(), clock_w + 1.0, r.height())
        wx = group_left + clock_w + gap
        icon_rect = QtCore.QRectF(wx, r.y(), icon_w, r.height())
        temp_rect = QtCore.QRectF(wx + icon_w + icon_temp_gap, r.y(), temp_w + 1.0, r.height())
        return {
            "txt": txt,
            "ft": ft,
            "fw": tm["font"] or self._tabular_font(13.0, W.Medium),
            "fam": fam,
            "glyph": glyph,
            "icon_code": icon_code,
            "temp": temp,
            "clock_w": clock_w,
            "weather_w": weather_w,
            "gap": gap,
            "dot_pos": QtCore.QPointF(group_left + clock_w + gap * 0.44, r.center().y()),
            "content_total": content_total,
            "group_left": group_left,
            "clock_rect": clock_rect,
            "icon_rect": icon_rect,
            "temp_rect": temp_rect,
            "has_weather": True,
        }

    def _paint_idle(self, p, r, a):
        cy = r.center().y()
        m = self._idle_layout_metrics(r)

        # 时间 + 天气组始终以整个胶囊中心 r.center().x() 严格居中，绝不计入未读色点宽度
        palette = resolve_clock_palette(
            self.clock_color,
            theme,
            custom_hex=self.clock_custom_color,
            gradient_stops=self.clock_gradient,
            cover_color=self.cover_color,
        )
        paint_clock(p, m["clock_rect"], m["txt"], self.clock_style, palette, dark=theme.dark, alpha=a)

        if m["has_weather"]:
            dot_col, icon_col, temp_col = resolve_weather_colors(self.clock_color, theme, palette, alpha=a)
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot_col)
            p.drawEllipse(m["dot_pos"], 1.1, 1.1)
            p.restore()

            kind = infer_weather_kind(m.get("icon_code", ""), m.get("glyph", ""))
            paint_weather_icon(p, m["icon_rect"], kind, icon_col)
            paint_temperature(p, m["temp_rect"], m["temp"], self.clock_style, temp_col)

        # 未读色点固定在胶囊最右侧（约 12px 右边距）
        self._badges(p, r.right() - 12.0, cy, a)

    def _vertical_idle_layout_metrics(self, r: QtCore.QRectF) -> dict:
        """计算 36px 侧边竖时钟（含碰撞挤压等比缩放、连续空间分配、天气、负温度与底部独立未读区）的几何参数。"""
        txt = getattr(self, "_clock_override", None) or time.strftime("%H:%M", time.localtime())
        parts = str(txt).split(":")
        hh = (parts[0] if parts else "00").strip().zfill(2)[:2]
        mm = (parts[1] if len(parts) > 1 else "00").strip().zfill(2)[:2]

        clip_rect = r.adjusted(3.0, 6.0, -3.0, -6.0)
        actual_clip_w = max(6.0, float(clip_rect.width()))
        actual_clip_h = max(12.0, float(clip_rect.height()))
        # 稳定态 36px 对应 30px 裁剪区与 26px 核心区；挤压至 <36px 或横幅矮高度回收初期按实际可用裁剪宽高平滑等比缩放
        w_squeeze_ratio = clamp(actual_clip_w / 30.0, 0.25, 1.0)
        h_squeeze_ratio = clamp(actual_clip_h / 44.0, 0.45, 1.0)
        squeeze_ratio = min(w_squeeze_ratio, h_squeeze_ratio)
        eff_core_w = min(26.0 * squeeze_ratio, max(6.0, actual_clip_w - 4.0))
        eff_clip_w = min(30.0 * squeeze_ratio, actual_clip_w)

        has_weather = bool(self.weather and self.show_weather)
        active_badges = [k for k in ("qq", "wechat", "mail") if self.badges.get(k)]
        nb = len(active_badges)

        # 1) 视觉显示透明度（空间不足以清晰容纳天气和未读点时，优先保留时钟，宽度与高度恢复后平滑显现）
        w_extra_vis = smooth((float(r.width()) - 29.5) / 4.5)
        h_weather_vis = smooth((float(r.height()) - 108.0) / 18.0) if has_weather else 0.0
        min_badge_h = 130.0 if has_weather else 86.0
        h_badge_vis = smooth((float(r.height()) - min_badge_h) / 12.0) if nb > 0 else 0.0

        weather_alpha = float(w_extra_vis * h_weather_vis) if has_weather else 0.0
        badge_alpha = float(w_extra_vis * h_badge_vis) if nb > 0 else 0.0

        # 2) 连续布局空间让位（随可用宽高线性连续变化，消除中段斜率放大与阈值阶跃）
        w_space_k = clamp((float(r.width()) - 22.32) / (36.0 - 22.32), 0.0, 1.0)
        h_badge_space_k = clamp((float(r.height()) - (40.0 if has_weather else 36.0)) / (108.0 if has_weather else 68.0), 0.0, 1.0) if nb > 0 else 0.0
        badge_space_k = float(w_space_k * h_badge_space_k) if nb > 0 else 0.0
        badge_band_h = float(H_VERT_BADGE_EXTRA) * badge_space_k
        content_r = QtCore.QRectF(r.x(), r.y(), r.width(), max(20.0, float(r.height()) - badge_band_h))

        # 当处于横幅回收期时 _wea_h0 = 36px，当纯竖条首次收到天气时 _wea_h0 = 92px，确保插值全程单调且无过期切换跳变
        if not has_weather:
            self._wea_h0 = float(H_VERT_IDLE)
        wea_h0 = 36.0 if float(r.height()) < 90.0 else float(self._wea_h0)
        h_weather_space_k = clamp((float(content_r.height()) - wea_h0) / max(1.0, float(H_VERT_WEATHER) - wea_h0), 0.0, 1.0) if has_weather else 0.0
        weather_space_k = float(w_space_k * h_weather_space_k) if has_weather else 0.0

        row_h = 20.0 * squeeze_ratio
        row_gap = 3.0 * squeeze_ratio
        cy_centered = content_r.center().y()
        hh_cy_centered = cy_centered - (row_h + row_gap) * 0.5
        mm_cy_centered = cy_centered + (row_h + row_gap) * 0.5

        y0 = content_r.top()
        h_scale = content_r.height() / 136.0
        hh_cy_wea = y0 + 26.0 * h_scale
        mm_cy_wea = y0 + 49.0 * h_scale

        hh_cy = hh_cy_centered + (hh_cy_wea - hh_cy_centered) * weather_space_k
        mm_cy = mm_cy_centered + (mm_cy_wea - mm_cy_centered) * weather_space_k
        hh_rect = QtCore.QRectF(r.x(), hh_cy - row_h * 0.5, r.width(), row_h)
        mm_rect = QtCore.QRectF(r.x(), mm_cy - row_h * 0.5, r.width(), row_h)

        if has_weather:
            div_y = y0 + 68.5 * h_scale
            div_w = min(eff_core_w, 14.0 * squeeze_ratio)
            div_rect = QtCore.QRectF(r.center().x() - div_w * 0.5, div_y - 0.5, div_w, 1.0)
            icon_s = 15.0 * squeeze_ratio
            icon_cy = y0 + 84.0 * h_scale
            icon_rect = QtCore.QRectF(r.center().x() - icon_s * 0.5, icon_cy - icon_s * 0.5, icon_s, icon_s)
            temp_h = 18.0 * squeeze_ratio
            temp_cy = y0 + 106.0 * h_scale
            temp_rect = QtCore.QRectF(r.x(), temp_cy - temp_h * 0.5, r.width(), temp_h)
            glyph = self.weather[0]
            temp = self.weather[1]
            icon_code = self.weather[3] if len(self.weather) > 3 else ""
            temp_metrics = measure_vertical_temperature(
                self.clock_style,
                temp,
                max_core_w=eff_core_w,
                rect=temp_rect,
            )
        else:
            div_rect = QtCore.QRectF()
            icon_rect = QtCore.QRectF()
            temp_rect = QtCore.QRectF()
            glyph = ""
            temp = ""
            icon_code = ""
            temp_metrics = None

        hh_metrics = measure_vertical_clock_pair(
            self.clock_style,
            text=hh,
            max_core_w=eff_core_w,
            max_clip_w=eff_clip_w,
            rect=hh_rect,
        )
        mm_metrics = measure_vertical_clock_pair(
            self.clock_style,
            text=mm,
            max_core_w=eff_core_w,
            max_clip_w=eff_clip_w,
            rect=mm_rect,
        )

        dot_d = 3.8 * squeeze_ratio
        dot_gap = 5.5 * squeeze_ratio
        if nb > 0:
            total_bw = nb * dot_d + max(0, nb - 1) * dot_gap
            cy_badges = r.bottom() - 10.0
            bx0 = r.center().x() - total_bw * 0.5
            badges_rect = QtCore.QRectF(bx0, cy_badges - dot_d * 0.5, total_bw, dot_d)
        else:
            cy_badges = r.bottom() - 10.0
            badges_rect = QtCore.QRectF()

        return {
            "txt": txt,
            "hh": hh,
            "mm": mm,
            "clip_rect": clip_rect,
            "actual_clip_w": actual_clip_w,
            "actual_clip_h": actual_clip_h,
            "squeeze_ratio": float(squeeze_ratio),
            "eff_core_w": float(eff_core_w),
            "eff_clip_w": float(eff_clip_w),
            "badge_space_k": float(badge_space_k),
            "weather_space_k": float(weather_space_k),
            "content_rect": content_r,
            "hh_rect": hh_rect,
            "mm_rect": mm_rect,
            "hh_metrics": hh_metrics,
            "mm_metrics": mm_metrics,
            "has_weather": has_weather,
            "weather_alpha": weather_alpha,
            "div_rect": div_rect,
            "icon_rect": icon_rect,
            "temp_rect": temp_rect,
            "temp_metrics": temp_metrics,
            "glyph": glyph,
            "icon_code": icon_code,
            "temp": temp,
            "active_badges": active_badges,
            "badge_alpha": badge_alpha,
            "badges_rect": badges_rect,
            "dot_d": float(dot_d),
            "dot_gap": float(dot_gap),
            "cy_badges": float(cy_badges),
        }

    def _badges_vertical(self, p: QtGui.QPainter, m: dict, a: float):
        """在竖条底部独立空间水平居中绘制未读色点（1. QQ  2. 微信  3. 邮件）。"""
        active = m["active_badges"]
        ba = a * m["badge_alpha"]
        if not active or ba <= 0.01:
            return
        br: QtCore.QRectF = m["badges_rect"]
        dot_d = m["dot_d"]
        gap = m["dot_gap"]
        cy = m["cy_badges"]
        x = br.left()
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for kind in active:
            c = QtGui.QColor(KIND_COLORS.get(kind, KIND_COLORS["other"]))
            c.setAlphaF(ba)
            p.setBrush(c)
            p.drawEllipse(QtCore.QPointF(x + dot_d * 0.5, cy), dot_d * 0.5, dot_d * 0.5)
            x += dot_d + gap
        p.restore()

    def _paint_idle_vertical(self, p: QtGui.QPainter, r: QtCore.QRectF, a: float):
        m = self._vertical_idle_layout_metrics(r)
        palette = resolve_clock_palette(
            self.clock_color,
            theme,
            custom_hex=self.clock_custom_color,
            gradient_stops=self.clock_gradient,
            cover_color=self.cover_color,
        )
        paint_vertical_clock_pair(
            p,
            m["hh_rect"],
            m["hh"],
            self.clock_style,
            palette,
            dark=theme.dark,
            alpha=a,
            max_core_w=m["eff_core_w"],
            max_clip_w=m["eff_clip_w"],
        )
        paint_vertical_clock_pair(
            p,
            m["mm_rect"],
            m["mm"],
            self.clock_style,
            palette,
            dark=theme.dark,
            alpha=a,
            max_core_w=m["eff_core_w"],
            max_clip_w=m["eff_clip_w"],
        )

        wa = a * m["weather_alpha"]
        if m["has_weather"] and wa > 0.01:
            dot_col, icon_col, temp_col = resolve_weather_colors(self.clock_color, theme, palette, alpha=wa)
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot_col)
            p.drawRoundedRect(m["div_rect"], 0.5, 0.5)
            p.restore()

            kind = infer_weather_kind(m.get("icon_code", ""), m.get("glyph", ""))
            paint_weather_icon(p, m["icon_rect"], kind, icon_col)
            paint_vertical_temperature(
                p,
                m["temp_rect"],
                m["temp"],
                self.clock_style,
                temp_col,
                max_core_w=m["eff_core_w"],
            )

        self._badges_vertical(p, m, a)

    def _paint_volume_vertical(self, p: QtGui.QPainter, r: QtCore.QRectF, a: float, act: dict | None = None):
        """左右侧边纵向音量条：沿用侧边竖条宽度与当时高度，顶部显示扬声器/静音图标，中间音量从下往上填充。"""
        act = act or self.activity or {}
        muted = bool(act.get("muted"))
        val = 0.0 if muted else clamp(float(act.get("value", 0.0) or 0.0), 0.0, 1.0)
        enter = ease_out((time.monotonic() - float(act.get("start", 0.0) or 0.0)) / 0.25)

        clip_w = max(6.0, float(r.width()) - 6.0)
        squeeze_ratio = clamp(clip_w / 30.0, 0.4, 1.0)
        cx = r.center().x()

        icon_s = 18.0 * squeeze_ratio
        icon_top = r.top() + 12.0
        icon_rect = QtCore.QRectF(cx - icon_s * 0.5, icon_top, icon_s, icon_s)
        glyph = act.get("glyph") or (G.MUTE if muted else G.VOLUME)
        p.save()
        p.setFont(icon_font(max(9, round(14.5 * squeeze_ratio))))
        p.setPen(theme.text(a))
        p.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, glyph)

        track_w = 6.0 * squeeze_ratio
        track_top = icon_rect.bottom() + 8.0
        track_bot = max(track_top + 16.0, r.bottom() - 14.0)
        track_h = track_bot - track_top
        track_rect = QtCore.QRectF(cx - track_w * 0.5, track_top, track_w, track_h)
        rad = track_w * 0.5

        p.setPen(Qt.PenStyle.NoPen)
        tr_col = QtGui.QColor(theme.track())
        tr_col.setAlphaF(tr_col.alphaF() * a)
        p.setBrush(tr_col)
        p.drawRoundedRect(track_rect, rad, rad)

        if val > 0.005:
            fill_h = clamp(track_h * val * enter, track_w, track_h)
            fill_rect = QtCore.QRectF(track_rect.left(), track_bot - fill_h, track_w, fill_h)
            p.setBrush(theme.text(a))
            p.drawRoundedRect(fill_rect, rad, rad)
        p.restore()

    def _paint_vertical_play_pause_icon(
        self, p: QtGui.QPainter, pr: QtCore.QRectF, s: float, is_playing: bool, a: float
    ):
        """绘制左右侧竖版音乐的播放/暂停矢量图标：略微加粗笔画并平衡两种状态的视觉重量，严格居中于按钮热区。"""
        cx = pr.center().x()
        cy = pr.center().y()
        fg = theme.text(a)
        p.save()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if is_playing:
            # 暂停双竖线：1.9px 宽、11.0px 高，中间 4.0px 清晰净空隙，严格镜像对称于 cx
            bw = 1.9 * s
            bh = 11.0 * s
            gap = 4.0 * s
            r_cap = bw * 0.45
            y0 = cy - bh * 0.5
            x_l = cx - gap * 0.5 - bw
            x_r = cx + gap * 0.5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fg)
            p.drawRoundedRect(QtCore.QRectF(x_l, y0, bw, bh), r_cap, r_cap)
            p.drawRoundedRect(QtCore.QRectF(x_r, y0, bw, bh), r_cap, r_cap)
        else:
            # 播放空心三角：1.5px 轮廓描边 + 圆角转折，略向右补偿重心以与按钮视觉居中，与暂停双线视觉重量均衡
            sw = 1.50 * s
            half_h = 4.9 * s
            x_left = cx - 3.4 * s
            x_right = cx + 4.7 * s
            path = QtGui.QPainterPath()
            path.moveTo(x_left, cy - half_h)
            path.lineTo(x_right, cy)
            path.lineTo(x_left, cy + half_h)
            path.closeSubpath()
            pen = QtGui.QPen(fg, sw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.restore()

    def _paint_media_vertical(self, p: QtGui.QPainter, r: QtCore.QRectF, a: float, update_hit: bool = True):
        """左右侧边 36×136 竖版音乐视图：自上而下舒展排列 26px 圆角封面、四条律动波形、24px 播放/暂停键（常态透明无灰底，悬停浮现圆形反馈）。"""
        active_badges = [k for k in ("qq", "wechat", "mail") if self.badges.get(k)]
        nb = len(active_badges)
        badge_band = float(H_VERT_BADGE_EXTRA) if nb > 0 else 0.0
        content_r = QtCore.QRectF(r.x(), r.y(), r.width(), max(44.0, float(r.height()) - badge_band))

        clip_w = max(6.0, float(r.width()) - 6.0)
        w_ratio = clamp(clip_w / 30.0, 0.35, 1.0)
        h_ratio = clamp(float(content_r.height()) / float(H_VERT_WEATHER), 0.55, 1.0)
        s = min(w_ratio, h_ratio)
        cx = content_r.center().x()

        top_pad = 15.0 * s
        bot_pad = 15.0 * s
        cover_s = 26.0 * s
        bars_h = 16.0 * s
        btn_d = 24.0 * s

        # 1) 顶部 26px 圆角封面
        cr = QtCore.QRectF(cx - cover_s * 0.5, content_r.top() + top_pad, cover_s, cover_s)
        p.save()
        if self.cover is not None:
            p.setOpacity(a)
            p.drawPixmap(cr.toRect(), self.cover)
            p.setOpacity(1.0)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.card(1.4))
            p.drawRoundedRect(cr, 7.5 * s, 7.5 * s)
            p.setFont(icon_font(max(8, round(12.5 * s))))
            p.setPen(theme.text(0.85 * a))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, G.MUSIC)

        # 3) 底部 24px 播放/暂停键（先定位底部，再把四条波形严格居中在封面与按钮之间）
        pr = QtCore.QRectF(cx - btn_d * 0.5, content_r.bottom() - bot_pad - btn_d, btn_d, btn_d)
        if update_hit:
            self.play_rect = pr.intersected(r) if r.width() >= 24.0 else None

        # 2) 中间四条律动波形
        mid_top = cr.bottom()
        mid_bot = pr.top()
        bars_cy = (mid_top + mid_bot) * 0.5
        bars_bot = bars_cy + bars_h * 0.5
        bar_w = 2.5 * s
        bar_gap = 2.5 * s
        total_bw = 4.0 * bar_w + 3.0 * bar_gap
        bx0 = cx - total_bw * 0.5
        t_now = time.monotonic()
        is_playing = bool(self.media.get("playing"))
        pk = clamp(self._peak * 1.6, 0.0, 1.0) if is_playing else 0.0
        for i in range(4):
            if is_playing:
                target = 0.28 + max(pk, 0.35) * (0.45 + 0.55 * math.sin(t_now * (7.3 + i * 2.1) + i * 1.7) ** 2)
            else:
                target = 0.22
            self._bars[i] += (target - self._bars[i]) * 0.35
            bh = clamp(bars_h * self._bars[i], 3.0 * s, bars_h)
            br = QtCore.QRectF(bx0 + i * (bar_w + bar_gap), bars_bot - bh, bar_w, bh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.text(0.85 * a))
            p.drawRoundedRect(br, bar_w * 0.5, bar_w * 0.5)

        # 绘制播放/暂停按钮：常态透明无灰底，鼠标悬停时显示圆形反馈
        hov = self.play_hover
        if hov > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.hover() if hasattr(theme, "hover") else theme.card(1.2))
            p.setOpacity(a * hov)
            p.drawEllipse(pr.adjusted(1, 1, -1, -1))
            p.setOpacity(1.0)
        self._paint_vertical_play_pause_icon(p, pr, s, is_playing, a)
        p.restore()

        # 底部独立未读色点（若有）
        if nb > 0:
            dot_d = 3.8 * s
            dot_gap = 5.5 * s
            total_w = nb * dot_d + max(0, nb - 1) * dot_gap
            cy_b = r.bottom() - 10.0
            x = r.center().x() - total_w * 0.5
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            for kind in active_badges:
                c = QtGui.QColor(KIND_COLORS.get(kind, KIND_COLORS["other"]))
                c.setAlphaF(a)
                p.setBrush(c)
                p.drawEllipse(QtCore.QPointF(x + dot_d * 0.5, cy_b), dot_d * 0.5, dot_d * 0.5)
                x += dot_d + dot_gap
            p.restore()

    def _paint_media(self, p, r, a):
        cy = r.center().y()
        x = r.x() + 6
        cr = QtCore.QRectF(x + 2, cy - 13, 26, 26)
        p.setOpacity(a)
        if self.cover is not None:
            p.drawPixmap(cr.toRect(), self.cover)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.card(1.4))
            p.drawRoundedRect(cr, 7, 7)
            p.setFont(icon_font(13))
            p.setPen(theme.text(0.8))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, G.MUSIC)
        p.setOpacity(1.0)
        # 最右侧：播放 / 暂停键（收起时也能直接点）；左边是音频律动条（跟随系统输出峰值）
        right = self._badges(p, r.right() - 14, cy, a) - 4 if any(self.badges.values()) else r.right() - 8
        pr = QtCore.QRectF(right - 28, cy - 14, 28, 28)
        self.play_rect = pr
        hov = self.play_hover
        if hov > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.hover() if hasattr(theme, "hover") else theme.card(1.2))
            p.setOpacity(a * hov)
            p.drawEllipse(pr.adjusted(1, 1, -1, -1))
            p.setOpacity(1.0)
        p.setFont(icon_font(14))
        p.setPen(theme.text(a))
        p.drawText(pr, Qt.AlignmentFlag.AlignCenter, G.PAUSE if self.media.get("playing") else G.PLAY)
        bars_w = 26
        bx = pr.left() - 6 - bars_w
        t = time.monotonic()
        pk = clamp(self._peak * 1.6, 0, 1)
        for i in range(4):
            target = 0.18 + pk * (0.55 + 0.45 * math.sin(t * (7.3 + i * 2.1) + i * 1.7) ** 2)
            self._bars[i] += (target - self._bars[i]) * 0.35
            h = 4 + 14 * self._bars[i]
            br = QtCore.QRectF(bx + i * 7, cy - h / 2, 3.6, h)
            c = theme.text(0.85 * a)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(br, 1.8, 1.8)
        # 中间：当前歌词（有歌词时）或 标题 · 歌手，过长时滚动
        tx = cr.right() + 10
        tw = bx - 10 - tx
        title = self.media.get("title", "")
        artist = self.media.get("artist", "")
        text = f"{title}  ·  {artist}" if artist else title
        idx, line, _tr = self.lyric_now()
        if line:
            text = line
        if idx != self._lyr_idx:                      # 换行：旧行上移淡出，新行从下方淡入
            self._lyr_prev = self._lyr_cur if self._lyr_idx != -2 else ""
            self._lyr_idx, self._lyr_t = idx, t
        self._lyr_cur = text
        k = ease_out((t - self._lyr_t) / 0.32)
        if self._lyr_font is None:
            self._lyr_font = ui_font(13, W.Medium)
            self._lyr_fm = QtGui.QFontMetrics(self._lyr_font)
            self._lyr_zoom_font = QtGui.QFont(self._lyr_font)
        p.setFont(self._lyr_font)
        fm = self._lyr_fm
        if self._lyr_full_cache[0] != text:
            self._lyr_full_cache = (text, fm.horizontalAdvance(text))
        full = self._lyr_full_cache[1]
        p.save()
        p.setClipRect(QtCore.QRectF(tx, r.y(), tw, r.height()))
        if k < 1 and self._lyr_prev:
            p.setPen(theme.text(a * (1 - k)))
            p.drawText(QtCore.QRectF(tx, r.y() - 9 * k, tw, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignHCenter if line else Qt.AlignmentFlag.AlignLeft),
                       fm.elidedText(self._lyr_prev, Qt.TextElideMode.ElideRight, int(tw)))
        p.translate(0, 9 * (1 - k) if self._lyr_prev else 0)
        p.setPen(theme.text(a * (k if self._lyr_prev else 1.0)))
        t = t - self._lyr_t                           # 滚动从这一行出现时开始计时
        line_alpha = a * (k if self._lyr_prev else 1.0)
        fill_w = -1.0
        glow_w = -1.0
        glow_strength = 0.0
        if line and self.lyrics is not None:
            pos_now = self.media_pos()
            done_s, cur_s, cur_frac = self.lyrics.sweep_at(idx, pos_now)
            if self._lyr_word_cache[0] == done_s and self._lyr_word_cache[1] == cur_s:
                _, _, w_done, w_cur, _w_after = self._lyr_word_cache
            else:
                w_done = float(fm.horizontalAdvance(done_s)) if done_s else 0.0
                w_cur = float(fm.horizontalAdvance(cur_s)) if cur_s else 0.0
                w_after = float(fm.horizontalAdvance(done_s + cur_s)) if (done_s or cur_s) else 0.0
                self._lyr_word_cache = (done_s, cur_s, w_done, w_cur, w_after)
            fill_w = clamp(w_done + w_cur * cur_frac, 0.0, float(full))
            glow_w = fill_w
            glow_strength = self._karaoke_glow_strength(idx, pos_now, cur_frac)

        def _draw_karaoke(rect_line: QtCore.QRectF, align_flag):
            if fill_w < 0.0:
                p.setPen(theme.text(line_alpha))
                p.drawText(rect_line, Qt.AlignmentFlag.AlignVCenter | align_flag, text)
                return
            unsung_c = theme.text(0.40 * line_alpha)
            sung_c = theme.text(line_alpha)
            if fill_w <= 0.5 or full <= 1.0:
                p.setPen(unsung_c)
                p.drawText(rect_line, Qt.AlignmentFlag.AlignVCenter | align_flag, text)
                return
            if fill_w >= full - 0.5:
                p.setPen(sung_c)
                p.drawText(rect_line, Qt.AlignmentFlag.AlignVCenter | align_flag, text)
                return
            text_x = rect_line.x()
            if align_flag == Qt.AlignmentFlag.AlignHCenter and full < rect_line.width():
                text_x = rect_line.x() + (rect_line.width() - full) / 2.0
            # 24px 宽羽化水流渐变扫光（整句原生统一字距）
            feather = 24.0
            x0 = text_x + max(0.0, fill_w - feather * 0.72)
            x1 = text_x + min(float(full), fill_w + feather * 0.48)
            if x1 <= x0 + 0.5:
                x1 = x0 + 1.0
            grad = QtGui.QLinearGradient(x0, 0.0, x1, 0.0)
            grad.setColorAt(0.0, sung_c)
            grad.setColorAt(0.55, theme.text(0.76 * line_alpha))
            grad.setColorAt(1.0, unsung_c)
            pen = QtGui.QPen(QtGui.QBrush(grad), 0)
            p.setPen(pen)
            p.drawText(rect_line, Qt.AlignmentFlag.AlignVCenter | align_flag, text)

            # 贴着亮暗交界的连续微光（短间隔衔接、长停顿与句尾淡出，无任何矩形底块）
            if glow_strength > 0.02 and glow_w >= 0.0:
                gx = text_x + glow_w
                half_span = 9.0
                if gx + half_span > tx and gx - half_span < tx + tw:
                    if theme.dark:
                        core_c = QtGui.QColor(244, 255, 249, round(255 * glow_strength * line_alpha))
                        halo_c = QtGui.QColor(165, 235, 198, round(95 * glow_strength * line_alpha))
                    else:
                        accent = QtGui.QColor(theme.on_color())
                        core_c = QtGui.QColor(accent)
                        core_c.setAlphaF(clamp(0.85 * glow_strength * line_alpha, 0.0, 1.0))
                        halo_c = QtGui.QColor(accent)
                        halo_c.setAlphaF(clamp(0.28 * glow_strength * line_alpha, 0.0, 1.0))

                    def _make_glow_pen(col: QtGui.QColor) -> QtGui.QPen:
                        c0 = QtGui.QColor(col)
                        c0.setAlpha(0)
                        g = QtGui.QLinearGradient(gx - half_span, 0.0, gx + half_span, 0.0)
                        g.setColorAt(0.0, c0)
                        g.setColorAt(0.5, col)
                        g.setColorAt(1.0, c0)
                        return QtGui.QPen(QtGui.QBrush(g), 0)

                    p.save()
                    p.setClipRect(
                        QtCore.QRectF(gx - half_span, rect_line.y() - 3.0, half_span * 2.0, rect_line.height() + 6.0),
                        Qt.ClipOperation.IntersectClip,
                    )
                    p.setPen(_make_glow_pen(halo_c))
                    for dx, dy in ((-0.7, 0.0), (0.7, 0.0), (0.0, -0.7), (0.0, 0.7)):
                        p.drawText(rect_line.translated(dx, dy), Qt.AlignmentFlag.AlignVCenter | align_flag, text)
                    p.setPen(_make_glow_pen(core_c))
                    p.drawText(rect_line, Qt.AlignmentFlag.AlignVCenter | align_flag, text)
                    p.restore()

        if full <= tw:
            # 歌词居中显示；歌名 · 歌手靠左
            h_al = Qt.AlignmentFlag.AlignHCenter if line else Qt.AlignmentFlag.AlignLeft
            _draw_karaoke(QtCore.QRectF(tx, r.y(), tw, r.height()), h_al)
        elif line and fill_w >= 0.0:
            # 长歌词单向跟随演唱进度滚到末尾停住，不再从右侧循环折返
            off = clamp(fill_w - tw * 0.65, 0.0, float(full - tw))
            _draw_karaoke(
                QtCore.QRectF(tx - off, r.y(), full + 4, r.height()),
                Qt.AlignmentFlag.AlignLeft,
            )
        else:
            gap = 36
            speed = 38.0
            pause = 1.2
            period = (full + gap) / speed
            ph = (t % (period + pause))
            off = 0 if ph < pause else (ph - pause) * speed
            for k_copy in (0, 1):
                _draw_karaoke(
                    QtCore.QRectF(tx - off + k_copy * (full + gap), r.y(), full + 4, r.height()),
                    Qt.AlignmentFlag.AlignLeft,
                )
            # 两端渐隐
            for left in (True, False):
                g = QtGui.QLinearGradient(tx if left else tx + tw - 14, 0, tx + 14 if left else tx + tw, 0)
                g.setColorAt(0 if left else 1, QtGui.QColor(0, 0, 0, 255))
                g.setColorAt(1 if left else 0, QtGui.QColor(0, 0, 0, 0))
            # （渐隐需要独立图层，代价较高，这里省略，靠裁剪即可）
        p.restore()

    def _paint_activity(self, p, r, a):
        act = self.activity or {}
        cy = r.center().y()
        t = act.get("type")
        is_notify = (t == "notify")
        is_life = (t == "life-reminder")
        enter = ease_out((time.monotonic() - act.get("start", 0)) / 0.35)
        color = act.get("color")
        title = act.get("title", "")
        sub = act.get("sub", "")
        is_generic = not (is_notify or is_life or t == "volume")
        # 左侧通知图标槽位 24×24px；通知整体右侧留出呼吸空间，通用轻提示按内容总宽居中
        d = 24 if (is_notify or is_life) else (22 if is_generic else 26)
        gap = 9 if (is_notify or is_life) else (6 if is_generic else 10)
        if is_generic:
            f_title = ui_font(13, W.DemiBold)
            fm_t = QtGui.QFontMetrics(f_title)
            f_sub = ui_font(12)
            fm_s = QtGui.QFontMetrics(f_sub)
            tw_raw = float(math.ceil(fm_t.horizontalAdvance(title)) + 2)
            sw_raw = float(math.ceil(fm_s.horizontalAdvance(sub)) + 2) if sub else 0.0
            sub_gap = 8.0 if sub else 0.0
            max_content = max(24.0, r.width() - 28.0)
            content_w = min(max_content, d + gap + tw_raw + sub_gap + sw_raw)
            start_x = r.center().x() - content_w / 2.0
            cr = QtCore.QRectF(start_x, cy - d / 2, d, d)
        elif r.width() < _MIN_DISMISS_BANNER_W:
            # 超窄宽度（< 68px，如刚从 36px 竖条向外展开的前几帧或超窄屏）：不画关闭键，仅居中显示图标
            cr = QtCore.QRectF(r.center().x() - d / 2, cy - d / 2, d, d)
        else:
            left_inset = 9.0 if (not is_notify or r.width() < 110.0) else _NOTIFY_LEFT_INSET
            cr = QtCore.QRectF(r.x() + left_inset, cy - d / 2, d, d)
        icon_rect = cr.adjusted(1.0, 1.0, -1.0, -1.0)
        if is_life:
            drew_svg = draw_life_svg_icon(p, icon_rect, str(act.get("kind") or ""), theme.dark, a)
        else:
            svg_key = act.get("svg_key") if is_notify else ""
            drew_svg = bool(svg_key) and draw_svg_icon(p, icon_rect, svg_key, theme.dark, a)
        if not drew_svg and not is_life:
            if color is not None:
                c = QtGui.QColor(color)
                c.setAlphaF(a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawEllipse(cr)
                p.setPen(QtGui.QColor(255, 255, 255, round(255 * a)))
            else:
                p.setPen(theme.text(a))
            img = act.get("icon")
            if isinstance(img, QtGui.QPixmap):
                p.setOpacity(a)
                p.drawPixmap(cr.toRect(), img)
                p.setOpacity(1)
            else:
                p.setFont(icon_font((12 if is_notify else 13) if color is not None else (14 if is_generic else 16)))
                p.drawText(cr, Qt.AlignmentFlag.AlignCenter, act.get("glyph", G.BELL))
        x = cr.right() + gap
        if t == "volume":
            self._notify_dismiss_rect = None
            self._dismiss_rect_seq = -1
            if r.width() < 100.0:
                return
            v = clamp(act.get("value", 0.0), 0, 1)
            bw = max(12.0, r.right() - 52 - x)
            track = QtCore.QRectF(x, cy - 3, bw, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.track())
            p.drawRoundedRect(track, 3, 3)
            p.setBrush(theme.text(a))
            p.drawRoundedRect(QtCore.QRectF(x, cy - 3, max(6, bw * v * enter), 6), 3, 3)
            p.setFont(ui_font(12.5, W.DemiBold, num=True))
            p.setPen(theme.text(a))
            p.drawText(QtCore.QRectF(r.right() - 48, r.y(), 34, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       "静音" if act.get("muted") else f"{round(v * 100)}")
            return
        title = act.get("title", "")
        sub = act.get("sub", "")
        act_seq = int(act.get("seq") or 0)
        if is_life or is_notify:
            # 关闭键绘制与命中热区严格使用同一门槛 _MIN_DISMISS_BANNER_W = 68.0
            if r.width() < _MIN_DISMISS_BANNER_W:
                self._notify_dismiss_rect = None
                self._dismiss_rect_seq = -1
                return
            dr = QtCore.QRectF(r.right() - 30, cy - 12, 24, 24)
            self._notify_dismiss_rect = dr.intersected(r)
            self._dismiss_rect_seq = act_seq
            if self.notify_dismiss_hover > 0.01:
                hbg = QtGui.QColor(255, 255, 255) if theme.dark else QtGui.QColor(0, 0, 0)
                hbg.setAlphaF(0.14 * self.notify_dismiss_hover * a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(hbg)
                p.drawEllipse(dr.adjusted(2, 2, -2, -2))
            p.setFont(icon_font(8.5))
            close_alpha = (0.65 + 0.32 * self.notify_dismiss_hover) * a
            p.setPen(theme.text(close_alpha))
            p.drawText(dr, Qt.AlignmentFlag.AlignCenter, G.CLOSE)

            avail = dr.left() - 6 - x
            if avail < 14.0 or r.width() < 110.0:
                if avail >= 14.0 and title:
                    f_t = ui_font(12.5, W.DemiBold)
                    fm_t = QtGui.QFontMetrics(f_t)
                    p.setFont(f_t)
                    p.setPen(theme.text(a))
                    p.drawText(
                        QtCore.QRectF(x, r.y(), avail, r.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        fm_t.elidedText(title, Qt.TextElideMode.ElideRight, int(avail)),
                    )
                return

            avail = dr.left() - 8 - x
            if is_life:
                f_title = ui_font(12.5, W.DemiBold)
                fm_t = QtGui.QFontMetrics(f_title)
                tw = min(float(math.ceil(fm_t.horizontalAdvance(title)) + 2), avail)
                p.setFont(f_title)
                p.setPen(theme.text(a))
                p.drawText(
                    QtCore.QRectF(x, r.y(), tw + 1, r.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    fm_t.elidedText(title, Qt.TextElideMode.ElideRight, int(tw)),
                )
                if r.width() >= 190.0 and sub and tw + 8 < avail:
                    sub_x = x + tw + 8
                    sub_w = max(10.0, avail - tw - 8)
                    f_sub = ui_font(12)
                    fm_s = QtGui.QFontMetrics(f_sub)
                    p.setFont(f_sub)
                    p.setPen(theme.text2(a))
                    p.drawText(
                        QtCore.QRectF(sub_x, r.y(), sub_w, r.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        fm_s.elidedText(sub, Qt.TextElideMode.ElideRight, int(sub_w)),
                    )
                return

            # is_notify (r.width() >= 110.0)
            f_app = ui_font(12.5, W.DemiBold)
            fm_app = QtGui.QFontMetrics(f_app)
            max_app = 108.0 if r.width() >= 190.0 else avail
            app_w = min(max_app, float(math.ceil(fm_app.horizontalAdvance(title)) + 2), max(14.0, avail))
            p.setFont(f_app)
            p.setPen(theme.text(a))
            p.drawText(
                QtCore.QRectF(x, r.y(), app_w + 1, r.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                fm_app.elidedText(title, Qt.TextElideMode.ElideRight, int(app_w)),
            )

            if r.width() >= 190.0:
                sub_txt = sub or "有新消息"
                f_sub = ui_font(12)
                fm_sep = QtGui.QFontMetrics(f_sub)
                sep_str = " · "
                sep_w = float(fm_sep.horizontalAdvance(sep_str))
                sx = x + app_w
                if sx + sep_w + 12 <= dr.left() - 8:
                    p.setFont(f_sub)
                    p.setPen(theme.text3(0.75 * a))
                    p.drawText(QtCore.QRectF(sx, r.y(), sep_w + 1, r.height()),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sep_str)
                    sub_x = sx + sep_w
                    sub_w = max(10.0, dr.left() - 8 - sub_x)
                    p.setPen(theme.text2(a))
                    p.drawText(
                        QtCore.QRectF(sub_x, r.y(), sub_w, r.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        fm_sep.elidedText(sub_txt, Qt.TextElideMode.ElideRight, int(sub_w)),
                    )
            return

        self._notify_dismiss_rect = None
        self._dismiss_rect_seq = -1
        avail = max(20.0, (cr.left() + max_content if is_generic else r.right() - 14) - x)
        p.setFont(ui_font(13, W.DemiBold))
        fm = QtGui.QFontMetrics(p.font())
        tw = float(math.ceil(fm.horizontalAdvance(title)) + 2)
        tc = act.get("title_color")
        p.setPen(QtGui.QColor(tc) if tc else theme.text(a))
        p.drawText(QtCore.QRectF(x, r.y(), min(tw, avail), r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, int(avail)))
        if sub and tw + 8 < avail:
            p.setFont(ui_font(12))
            fm2 = QtGui.QFontMetrics(p.font())
            sc = act.get("sub_color")
            p.setPen(QtGui.QColor(sc) if sc else theme.text2(a))
            sw = avail - tw - 8
            p.drawText(QtCore.QRectF(x + tw + 8, r.y(), sw, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm2.elidedText(sub, Qt.TextElideMode.ElideRight, int(sw)))
