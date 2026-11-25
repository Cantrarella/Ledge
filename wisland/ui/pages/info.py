# -*- coding: utf-8 -*-
"""天气页（419×232 紧凑布局，保留完整气象数据与多城市管理）。"""
from __future__ import annotations

import datetime as dt

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg
from ..anim import clamp
from ..header import PageHeader
from ..theme import G, W, theme, ui_font
from ..widgets import Hoverable, IconButton, Label, LineEdit, PillButton, Themed, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt


class GlyphLabel(QtWidgets.QWidget, Themed):
    def __init__(self, px: int, parent=None):
        super().__init__(parent)
        self.px = px
        self.glyph = ("", "Segoe Fluent Icons")
        self.setFixedSize(px + 6, px + 6)
        self._hook_theme()

    def set_glyph(self, g):
        self.glyph = g
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        f = QtGui.QFont()
        f.setFamilies([self.glyph[1], "Segoe Fluent Icons", "Segoe UI Symbol"])
        f.setPixelSize(self.px)
        p.setFont(f)
        p.setPen(theme.text(0.92))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph[0])


class Hourly(QtWidgets.QWidget, Themed):
    """分时天气单行横向展示（支持 8 个时段，宽度不足时可横向滚动）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.tz = None
        self.setFixedHeight(46)
        self._hook_theme()

    def set_items(self, items):
        self.items = items[:8]
        self.setMinimumWidth(max(360, len(self.items) * 48))
        self.setToolTip("")
        self.update()

    def paintEvent(self, e):
        from ...services.weather import weather_glyph
        if not self.items:
            return
        p = painter(self)
        n = max(1, len(self.items))
        cw = self.width() / n
        for i, it in enumerate(self.items):
            x = i * cw
            p.setFont(ui_font(10, num=True))
            p.setPen(theme.text3())
            t_str = (
                dt.datetime.fromtimestamp(it.get("time", 0) + self.tz, dt.timezone.utc)
                if self.tz is not None
                else dt.datetime.fromtimestamp(it.get("time", 0))
            ).strftime("%H时")
            p.drawText(QtCore.QRectF(x, 0, cw, 12), Qt.AlignmentFlag.AlignCenter, t_str)

            g, fam = weather_glyph(it.get("icon"))
            f = QtGui.QFont()
            f.setFamilies([fam, "Segoe Fluent Icons", "Segoe UI Symbol"])
            f.setPixelSize(13)
            p.setFont(f)
            p.setPen(theme.text(0.9))
            p.drawText(QtCore.QRectF(x, 12, cw, 13), Qt.AlignmentFlag.AlignCenter, g)

            p.setFont(ui_font(10.5, W.DemiBold, num=True))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(x, 24, cw, 12), Qt.AlignmentFlag.AlignCenter, f"{round(it.get('temp', 0))}°")

            pop = it.get("pop") or 0
            if pop >= 0.1:
                p.setFont(ui_font(8.5, num=True))
                p.setPen(QtGui.QColor("#4f9cff"))
                p.drawText(QtCore.QRectF(x, 35, cw, 11), Qt.AlignmentFlag.AlignCenter, f"{round(pop * 100)}%")


class Daily(QtWidgets.QWidget, Themed):
    """五日预报紧凑列表（固定 5 行）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.setFixedHeight(68)
        self._hook_theme()

    def set_items(self, items):
        self.items = items[:5]
        self.setToolTip("")
        self.update()

    def paintEvent(self, e):
        from ...services.weather import weather_glyph
        if not self.items:
            return
        p = painter(self)
        lo = min(i["min"] for i in self.items)
        hi = max(i["max"] for i in self.items)
        rh = self.height() / len(self.items)
        wk = "一二三四五六日"
        for k, it in enumerate(self.items):
            y = k * rh
            d = it.get("date")
            try:
                day = dt.date.fromisoformat(str(d))
                name = "今天" if day == dt.date.today() else "周" + wk[day.weekday()]
            except Exception:  # noqa: BLE001
                name = str(d)
            p.setFont(ui_font(11.5, W.Medium))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(0, y, 32, rh), Qt.AlignmentFlag.AlignVCenter, name)

            g, fam = weather_glyph(it.get("icon"))
            f = QtGui.QFont()
            f.setFamilies([fam, "Segoe Fluent Icons", "Segoe UI Symbol"])
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(QtCore.QRectF(32, y, 20, rh), Qt.AlignmentFlag.AlignCenter, g)

            p.setFont(ui_font(11, num=True))
            p.setPen(theme.text2())
            p.drawText(QtCore.QRectF(52, y, 26, rh), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{round(it['min'])}°")
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(self.width() - 28, y, 28, rh), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{round(it['max'])}°")

            bx = 84
            bw = max(24.0, self.width() - bx - 32)
            span = max(1e-6, hi - lo)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.track())
            p.drawRoundedRect(QtCore.QRectF(bx, y + rh / 2 - 2.0, bw, 4), 2.0, 2.0)
            x0 = bx + bw * (it["min"] - lo) / span
            x1 = bx + bw * (it["max"] - lo) / span
            g2 = QtGui.QLinearGradient(bx, 0, bx + bw, 0)
            g2.setColorAt(0, QtGui.QColor("#5ac8fa"))
            g2.setColorAt(1, QtGui.QColor("#ff9f0a"))
            p.setBrush(g2)
            p.drawRoundedRect(QtCore.QRectF(x0, y + rh / 2 - 2.0, max(4.0, x1 - x0), 4), 2.0, 2.0)


class Facts(QtWidgets.QWidget, Themed):
    """两行紧凑详情网格：湿度、能见度、风、气压、云量、日出、日落（不再重复体感温度）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, str]] = []
        self.setFixedHeight(28)
        self._hook_theme()

    def set_items(self, items):
        self.items = list(items)
        self.setToolTip("")
        self.update()

    def _cell_rect(self, row_idx: int, col_idx: int) -> QtCore.QRectF:
        cw = self.width() / 4.0
        rh = self.height() / 2.0
        return QtCore.QRectF(col_idx * cw, row_idx * rh, cw, rh)

    def paintEvent(self, e):
        if not self.items:
            return
        p = painter(self)
        cols = 4
        rows = (self.items[:cols], self.items[cols:cols * 2])
        for row_idx, row_items in enumerate(rows):
            if not row_items:
                continue
            for col_idx, (k, v) in enumerate(row_items):
                cell = self._cell_rect(row_idx, col_idx)
                x, y, cw, rh = cell.x(), cell.y(), cell.width(), cell.height()
                p.setFont(ui_font(10.5))
                p.setPen(theme.text3())
                kw = 36 if len(k) >= 3 else (16 if len(k) == 1 else 24)
                p.drawText(QtCore.QRectF(x, y, kw, rh), Qt.AlignmentFlag.AlignVCenter, k)
                p.setFont(ui_font(10.5, W.Medium, num=True))
                p.setPen(theme.text())
                fm = QtGui.QFontMetrics(p.font())
                vw = max(10, int(cw - kw - 4))
                p.drawText(
                    QtCore.QRectF(x + kw, y, vw, rh),
                    Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(v, Qt.TextElideMode.ElideRight, vw),
                )


def _fmt_clock(ts, tz_off):
    if not ts:
        return "--"
    return dt.datetime.fromtimestamp(int(ts) + int(tz_off or 0), dt.timezone.utc).strftime("%H:%M")


def _wind_dir(deg):
    if deg is None:
        return ""
    return ["北", "东北", "东", "东南", "南", "西南", "西", "西北"][int(((deg + 22.5) % 360) // 45)] + " "


class WeatherPage(Page):
    key, glyph, title, height, panel_width, panel_height = "weather", G.CLOUD, "天气", 190, 419, 218

    def __init__(self, ctx):
        super().__init__(ctx)
        self.sel = 0
        self.data: list[dict] = []

        # ---- 统一 Header：左(城市选择+添加) | 中(日期时间) | 右(刷新+齿轮) ----
        self.header = PageHeader(ctx, self, height=26)
        self.city_btn = PillButton("选择城市", height=22)
        self.city_btn.setMaximumWidth(116)
        self.city_btn.clicked.connect(self._open_city_menu)

        self.add_btn = IconButton(G.ADD, 22, 10, "添加城市")
        self.add_btn.clicked.connect(self._start_add)

        self.refresh_btn = IconButton(G.REFRESH, 22, 10, "刷新天气")
        self.refresh_btn.clicked.connect(lambda: ctx.weather.refresh())

        self.header.add_left(self.city_btn)
        self.header.add_left(self.add_btn)
        self.header.add_right(self.refresh_btn)

        # ---- 添加城市浮出输入条（不挤占 Header 中间时钟） ----
        self.add_edit = LineEdit("输入城市名并回车添加（Esc 取消）", width=260)
        self.add_edit.setMaximumWidth(395)
        self.add_edit.setFixedHeight(24)
        self.add_edit.hide()
        self.add_edit.focus_wanted.connect(self.want_focus)
        self.add_edit.returnPressed.connect(self._do_add)
        self.add_edit.installEventFilter(self)

        self.msg = Label("", 10.5, level=3)
        self.msg.hide()

        # ---- 左栏：当前天气（单一 QGridLayout：左列 52px 图标+温度 | 右列描述/温差/体感） ----
        self.icon = GlyphLabel(30)
        self.temp = Label("--°", 28, W.Light, num=True, align=Qt.AlignmentFlag.AlignCenter)
        self.temp.setFixedHeight(34)

        self.desc = Label("", 12.0, W.Medium, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.desc.setFixedHeight(18)
        self.range_lbl = Label("", 11.0, level=2, num=True, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.range_lbl.setFixedHeight(16)
        self.feels_lbl = Label("", 11.0, level=2, num=True, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.feels_lbl.setFixedHeight(16)
        feels_sp = self.feels_lbl.sizePolicy()
        feels_sp.setRetainSizeWhenHidden(True)
        self.feels_lbl.setSizePolicy(feels_sp)

        self.left_wrap = QtWidgets.QWidget()
        self.left_wrap.setFixedWidth(154)
        self.left_wrap.setMinimumHeight(68)
        self.left_grid = QtWidgets.QGridLayout(self.left_wrap)
        self.left_grid.setContentsMargins(0, 2, 0, 0)
        self.left_grid.setHorizontalSpacing(10)
        self.left_grid.setVerticalSpacing(2)
        self.left_grid.setColumnMinimumWidth(0, 52)
        self.left_grid.setColumnStretch(1, 1)
        self.left_grid.setRowMinimumHeight(2, 16)
        self.left_grid.setRowStretch(3, 1)
        self.left_grid.addWidget(self.icon, 0, 0, Qt.AlignmentFlag.AlignCenter)
        self.left_grid.addWidget(self.desc, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.left_grid.addWidget(self.temp, 1, 0, Qt.AlignmentFlag.AlignCenter)
        self.left_grid.addWidget(self.range_lbl, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.left_grid.addWidget(self.feels_lbl, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # ---- 右栏：五日预报 ----
        self.daily = Daily()

        # ---- 中部两行详情网格 + 下部单行分时滚动区 ----
        self.facts = Facts()
        self.hourly = Hourly()

        class _HorizontalWheelScrollArea(QtWidgets.QScrollArea):
            def wheelEvent(self, e):
                bar = self.horizontalScrollBar()
                delta = e.angleDelta().x() or e.angleDelta().y()
                if bar is None or bar.maximum() <= 0 or not delta:
                    e.ignore()
                    return
                step = -delta // 2
                cur = bar.value()
                if (step < 0 and cur <= bar.minimum()) or (step > 0 and cur >= bar.maximum()):
                    e.ignore()
                    return
                bar.setValue(max(bar.minimum(), min(bar.maximum(), cur + step)))
                e.accept()

        self.hourly_scroll = _HorizontalWheelScrollArea()
        self.hourly_scroll.setWidgetResizable(True)
        self.hourly_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.hourly_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hourly_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.hourly_scroll.setFixedHeight(46)
        self.hourly_scroll.setStyleSheet("QScrollArea,QWidget{background:transparent;border:none;}")
        self.hourly_scroll.setWidget(self.hourly)

        self.body = QtWidgets.QWidget()
        self.body.setLayout(vbox(
            hbox(self.left_wrap, (self.daily, 1), spacing=8),
            self.facts,
            self.hourly_scroll,
            spacing=2,
            margins=(0, 0, 0, 0),
        ))

        self.err = Label("", 12, level=2, align=Qt.AlignmentFlag.AlignCenter, wrap=True)
        self.err.hide()

        self.setLayout(vbox(self.header, self.add_edit, self.msg, (self.body, 1), self.err, spacing=2))

        ctx.weather.cities_updated.connect(self.set_cities)
        ctx.weather.city_added.connect(self._added)

    def _open_city_menu(self):
        from .system import styled_menu
        m = styled_menu(self)
        for idx, d in enumerate(self.data):
            name = d.get("name", "?")
            t_txt = f"{round(d['temp'])}°" if d.get("temp") is not None else "--°"
            prefix = "✓  " if idx == self.sel else "    "
            act = m.addAction(f"{prefix}{name}  ({t_txt})")
            act.triggered.connect(lambda _=False, i=idx: self.select(i))
        if self.data:
            m.addSeparator()
            if self.sel > 0:
                m.addAction("设当前城市为默认").triggered.connect(lambda: self._default(self.sel))
            rm = m.addAction("删除当前城市")
            rm.setEnabled(len(self.data) > 1)
            rm.triggered.connect(lambda: self._remove(self.sel))
        m.addAction("添加新城市…").triggered.connect(self._start_add)

        if self.island:
            self.island.locks.add("menu")
            m.aboutToHide.connect(lambda: self.island.locks.discard("menu"))
        m.popup(self.city_btn.mapToGlobal(QtCore.QPoint(0, self.city_btn.height() + 4)))

    # ---------- 城市数据 ----------
    def set_data(self, d: dict):
        if not d.get("ok") and not self.data:
            self.err.setText(d.get("error") or "暂无天气数据")
            self.err.show()
            self.body.hide()

    def set_cities(self, data: list):
        self.data = data
        if getattr(self, "_pending_select_new", False) and data:
            want_name = str(getattr(self, "_pending_add_city", "") or "").strip().lower()
            if want_name:
                matched_idx = None
                for i in range(len(data) - 1, -1, -1):
                    if str(data[i].get("name") or "").strip().lower() == want_name:
                        matched_idx = i
                        break
                if matched_idx is not None:
                    self.sel = matched_idx
                    self._pending_select_new = False
                    self._pending_add_city = ""
                else:
                    # 旧刷新结果尚未包含新城市：保留待选状态与当前选中项，不误跳到旧列表末尾
                    self.sel = min(self.sel, max(0, len(data) - 1))
            else:
                self.sel = len(data) - 1
                self._pending_select_new = False
        else:
            self.sel = min(self.sel, max(0, len(data) - 1))
        self.select(self.sel)

    def select(self, i: int):
        if not self.data:
            self.sel = 0
            return
        self.sel = max(0, min(int(i), len(self.data) - 1))
        self._show(self.data[self.sel])

    def _show(self, d: dict):
        from ...services.weather import weather_glyph
        name = str(d.get("name") or "").strip() or "选择城市"
        if d.get("temp") is None:
            self.city_btn.set_text(name)
            self.err.setText(d.get("error") or "暂无数据")
            self.err.show()
            self.body.hide()
            return
        self.err.hide()
        self.body.show()
        t_round = round(d.get("temp", 0))
        self.city_btn.set_text(name)
        self.city_btn.setToolTip("")

        err_txt = d.get("error") or ""
        self.msg.setText(err_txt)
        self.msg.setVisible(bool(err_txt))

        self.icon.set_glyph(weather_glyph(d.get("icon")))
        self.temp.setText(f"{t_round}°")
        desc = str(d.get("desc") or "")
        self.desc.setText(desc)
        self.desc.setToolTip("")

        lo, hi = d.get("temp_min"), d.get("temp_max")
        rng = f"{round(lo)}°~{round(hi)}°" if lo is not None and hi is not None else "--°~--°"
        self.range_lbl.setText(rng)

        fl = d.get("feels_like")
        if fl is not None:
            self.feels_lbl.setText(f"体感 {round(fl)}°")
            self.feels_lbl.show()
        else:
            self.feels_lbl.setText("")
            self.feels_lbl.hide()

        tz = d.get("tz_offset", 0)
        vis = d.get("visibility")
        self.facts.set_items([
            ("湿度", f"{d.get('humidity', '--')}%"),
            ("能见度", f"{vis / 1000:.0f}km" if vis else "--"),
            ("风", f"{_wind_dir(d.get('wind_deg'))}{d.get('wind', '--')}m/s"),
            ("气压", f"{d.get('pressure', '--')}hPa"),
            ("云量", f"{d.get('clouds', '--')}%"),
            ("日出", _fmt_clock(d.get("sunrise"), tz)),
            ("日落", _fmt_clock(d.get("sunset"), tz)),
        ])
        self.hourly.tz = tz
        self.hourly.set_items(d.get("hourly") or [])
        self.daily.set_items(d.get("daily") or [])

    def _default(self, i):
        self.sel = 0
        self.ctx.weather.make_default(i)

    def _remove(self, i):
        if self.sel >= i and self.sel > 0:
            self.sel -= 1
        self.ctx.weather.remove_city(i)

    def _start_add(self):
        self.add_edit.clear()
        self.add_edit.show()
        self.want_focus()
        self.add_edit.setFocus()

    def _end_add(self):
        self.add_edit.hide()
        if self.island is not None:
            self.island.release_focus()

    def _do_add(self):
        q = self.add_edit.text().strip()
        if q:
            self._pending_add_city = q
            self._pending_select_new = False
            self.msg.setText(f"正在查找 {q}…")
            self.msg.show()
            self.ctx.weather.add_city(q)
        self._end_add()

    def _added(self, ok: bool, text: str):
        self._msg_seq = getattr(self, "_msg_seq", 0) + 1
        seq = self._msg_seq
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        if ok:
            want_name = str(getattr(self, "_pending_add_city", "") or "").strip().lower()
            matched_idx = None
            if want_name and self.data:
                for i in range(len(self.data) - 1, -1, -1):
                    if str(self.data[i].get("name") or "").strip().lower() == want_name:
                        matched_idx = i
                        break
            if matched_idx is not None:
                self.select(matched_idx)
                self._pending_select_new = False
                self._pending_add_city = ""
            else:
                self._pending_select_new = True
        else:
            self._pending_select_new = False
            self._pending_add_city = ""
        QtCore.QTimer.singleShot(
            3500,
            lambda s=seq: (self.msg.setText(""), self.msg.hide()) if getattr(self, "_msg_seq", 0) == s else None,
        )

    def eventFilter(self, obj, ev):
        add_edit = getattr(self, "add_edit", None)
        if add_edit is not None and obj is add_edit:
            if ev.type() == QtCore.QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                self._end_add()
                return True
            if ev.type() == QtCore.QEvent.Type.FocusOut and not add_edit.text().strip():
                self._end_add()
        return super().eventFilter(obj, ev)

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        if self.data:
            self.select(self.sel)


_ = (cfg, clamp, Hoverable)
