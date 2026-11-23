# -*- coding: utf-8 -*-
"""生活页（419×140 紧凑布局）：含「待办」「喝水」「久坐」三个内部视图。"""
from __future__ import annotations

import datetime as dt

from PySide6 import QtCore, QtGui, QtWidgets

from ...services.todo import (
    REPEAT_ALL,
    REPEAT_WEEKEND,
    REPEAT_WORKDAYS,
    WEEKDAY_NAMES,
    done_for,
    reminder_text,
    repeat_tag,
    scheduled_on,
    today_str,
)
from ..anim import Spring, clamp
from ..header import PageHeader
from ..icons import draw_chair, draw_checklist, draw_droplet, draw_pencil
from ..theme import G, W, theme, ui_font
from ..widgets import Card, Hoverable, IconButton, Label, PillButton, ScrollArea, Switch, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt

COLOR_WATER = QtGui.QColor("#3b82f6")
COLOR_SIT = QtGui.QColor("#22c55e")
COLOR_WARN = QtGui.QColor("#f59e0b")
COLOR_MUTED_GRAY = QtGui.QColor("#8e8e93")


class _DynamicTitle(QtWidgets.QWidget):
    """动态页面标题：待办清单 / 喝水提醒 / 久坐提醒，后两字随分页着强调色。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._head = "待办"
        self._tail = "清单"
        self._accent = COLOR_SIT
        self.setFixedHeight(22)
        self._fit()
        theme.changed.connect(self.update)

    def _fit(self):
        fm = QtGui.QFontMetrics(ui_font(12, W.DemiBold))
        w = fm.horizontalAdvance(self._head + self._tail) + 6
        self.setFixedWidth(w)

    def set_title(self, head: str, tail: str, accent: QtGui.QColor):
        self._head = head
        self._tail = tail
        self._accent = accent
        self._fit()
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        f = ui_font(12, W.DemiBold)
        p.setFont(f)
        fm = QtGui.QFontMetrics(f)
        w1 = fm.horizontalAdvance(self._head)
        p.setPen(theme.text())
        p.drawText(QtCore.QRectF(0, 0, w1 + 2, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._head)
        p.setPen(self._accent)
        p.drawText(QtCore.QRectF(w1, 0, self.width() - w1, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._tail)


class _LifeTabs(QtWidgets.QWidget):
    """顶部左侧三图标分段切换：待办(清单) / 喝水(水滴) / 久坐(椅子)，带明确悬停提示。"""
    changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = [
            ("todo", "待办"),
            ("water", "喝水提醒"),
            ("sit", "久坐提醒"),
        ]
        self.index = 0
        self._pos = Spring(0.0, 420, 0.82, 0.005)
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = QtCore.QElapsedTimer()
        self._last.start()
        self.setFixedSize(72, 22)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_i = -1
        self.setToolTip("待办 / 喝水提醒 / 久坐提醒")
        theme.changed.connect(self.update)

    def current_key(self) -> str:
        return self.items[self.index][0]

    def set_tab(self, key: str, emit: bool = False):
        keys = [k for k, _ in self.items]
        if key not in keys:
            return
        i = keys.index(key)
        if i != self.index:
            self.index = i
            self._pos.to(float(i))
            if not self._t.isActive():
                self._last.restart()
                self._t.start()
            self.update()
            if emit:
                self.changed.emit(key)

    def _tick(self):
        dt_s = min(0.05, max(0.001, self._last.restart() / 1000.0))
        self._pos.step(dt_s)
        self.update()
        if self._pos.rest:
            self._t.stop()

    def _accent_for(self, idx: int) -> QtGui.QColor:
        if idx == 1:
            return COLOR_WATER
        return COLOR_SIT

    def mouseMoveEvent(self, e):
        w = self.width() / 3.0
        i = max(0, min(2, int(e.position().x() // w)))
        if i != self._hover_i:
            self._hover_i = i
            self.setToolTip(self.items[i][1])
            self.update()

    def leaveEvent(self, e):
        self._hover_i = -1
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            w = self.width() / 3.0
            i = max(0, min(2, int(e.position().x() // w)))
            self.set_tab(self.items[i][0], emit=True)
            e.accept()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.card(1.1))
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)

        cell_w = (r.width() - 4.0) / 3.0
        px = r.left() + 2.0 + cell_w * self._pos.x
        pill = QtCore.QRectF(px, r.top() + 2.0, cell_w, r.height() - 4.0)
        ac = self._accent_for(self.index)
        pill_bg = QtGui.QColor(ac)
        pill_bg.setAlphaF(0.24 if theme.dark else 0.18)
        p.setBrush(pill_bg)
        p.drawRoundedRect(pill, pill.height() / 2.0, pill.height() / 2.0)

        for i in range(3):
            cx = r.left() + 2.0 + (i + 0.5) * cell_w
            cy = r.center().y()
            sel = (i == self.index)
            col = self._accent_for(i) if sel else (theme.text2() if i == self._hover_i else theme.text3())
            if i == 0:
                draw_checklist(p, cx, cy, 11.0, col, theme.text3())
            elif i == 1:
                draw_droplet(p, cx, cy, 11.0, col)
            else:
                draw_chair(p, cx, cy, 11.0, col)


class _GlyphPill(Hoverable):
    """紧凑矢量/图标胶囊按钮：完整展示文字与图标，绝不被省略号截断。"""
    def __init__(self, text: str, draw_fn=None, glyph: str = "", accent: QtGui.QColor | None = None,
                 height: int = 22, min_width: int = 0, parent=None):
        super().__init__(parent)
        self.text = text
        self.draw_fn = draw_fn
        self.glyph = glyph
        self.accent = accent
        self.setFixedHeight(height)
        fm = QtGui.QFontMetrics(ui_font(11, W.DemiBold))
        has_icon = (draw_fn is not None) or bool(glyph)
        w = max(min_width, fm.horizontalAdvance(text) + (30 if has_icon else 20))
        if min_width > 0:
            self.setMinimumWidth(min_width)
        self.setFixedWidth(w)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        s = self.press_scale()
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        if self.accent is not None:
            bg = QtGui.QColor(self.accent)
            bg.setAlphaF(0.22 + 0.12 * self._hover.x)
            fg = self.accent.lighter(135) if theme.dark else self.accent.darker(115)
        else:
            bg = theme.card(1.1 + 0.6 * self._hover.x)
            fg = theme.text(0.92)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        has_icon = (self.draw_fn is not None) or bool(self.glyph)
        if self.draw_fn is not None:
            self.draw_fn(p, r.left() + 12.0, r.center().y(), fg)
        elif self.glyph:
            from ..theme import icon_font
            p.setFont(icon_font(11))
            p.setPen(fg)
            p.drawText(QtCore.QRectF(r.left() + 6.0, r.top(), 14.0, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(ui_font(11, W.DemiBold))
        p.setPen(fg)
        tx = r.left() + (22.0 if has_icon else 10.0)
        p.drawText(QtCore.QRectF(tx, r.top(), r.right() - tx - 6.0, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)


class _ProgressRing(QtWidgets.QWidget):
    """底部留缺口的 276° 弧形进度环（外径约 60px，高对比度轨道）。"""
    def __init__(self, mode: str = "water", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.frac = 0.0
        self.pct_text = "0"
        self.sub_text = "%"
        self.active = True
        self.overdue = False
        self.setFixedSize(66, 66)
        theme.changed.connect(self.update)

    def set_state(self, frac: float, pct_text: str, sub_text: str, active: bool = True, overdue: bool = False):
        self.frac = clamp(frac, 0.0, 1.0)
        self.pct_text = pct_text
        self.sub_text = sub_text
        self.active = active
        self.overdue = overdue
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        cx, cy = self.width() / 2.0, self.height() / 2.0 + 1.0
        radius = 27.0   # 外径约 60px (27*2 + 5)
        thick = 5.0
        start_deg = 228.0
        span_deg = -276.0

        track_col = QtGui.QColor(255, 255, 255, 52) if theme.dark else QtGui.QColor(0, 0, 0, 44)
        if not self.active:
            fill_col = COLOR_MUTED_GRAY
        elif self.overdue:
            fill_col = COLOR_WARN
        elif self.mode == "water":
            fill_col = COLOR_WATER
        else:
            fill_col = COLOR_SIT

        rect = QtCore.QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        pen_t = QtGui.QPen(track_col, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_t)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, round(start_deg * 16), round(span_deg * 16))

        if self.frac > 0.005:
            pen_f = QtGui.QPen(fill_col, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_f)
            p.drawArc(rect, round(start_deg * 16), round(span_deg * self.frac * 16))

        if self.mode == "water":
            p.setFont(ui_font(16, W.Bold, num=True))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(cx - 22, cy - 13, 44, 20), Qt.AlignmentFlag.AlignCenter, self.pct_text)
            draw_droplet(p, cx - 6.5, cy + 12, 8.5, fill_col)
            p.setFont(ui_font(9.5, W.DemiBold, num=True))
            p.setPen(theme.text2())
            p.drawText(QtCore.QRectF(cx - 0.5, cy + 6, 18, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "%")
        else:
            draw_chair(p, cx, cy - 4, 15.0, fill_col if (not self.active or self.overdue) else theme.text())
            p.setFont(ui_font(9.5, W.DemiBold))
            p.setPen(fill_col if (not self.active or self.overdue) else theme.text2())
            p.drawText(QtCore.QRectF(cx - 26, cy + 12, 52, 14), Qt.AlignmentFlag.AlignCenter, self.sub_text)


class _ProgressBarWidget(QtWidgets.QWidget):
    def __init__(self, color: QtGui.QColor, parent=None):
        super().__init__(parent)
        self.frac = 0.0
        self.color = color
        self.setFixedHeight(5)

    def set_value(self, frac: float, color: QtGui.QColor):
        self.frac = clamp(frac, 0.0, 1.0)
        self.color = color
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(255, 255, 255, 42) if theme.dark else QtGui.QColor(0, 0, 0, 34))
        p.drawRoundedRect(r, 2.5, 2.5)
        if self.frac > 0.005:
            fw = max(5.0, r.width() * self.frac)
            p.setBrush(self.color)
            p.drawRoundedRect(QtCore.QRectF(r.left(), r.top(), fw, r.height()), 2.5, 2.5)


class _HealthView(QtWidgets.QWidget):
    """喝水 / 久坐共用统一坐标版式：
    左侧(212px)：大数字 + 单位、状态行、进度条、主按钮 + 设置按钮(+ 撤销文字)
    中间：1px 细分割线
    右侧：圆环(66x66, 外径约60px) + 右侧对齐的「提醒」文字与开关
    """
    open_settings = QtCore.Signal()

    def __init__(self, ctx, mode: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.mode = mode  # "water" | "sit"

        self.lbl_hero = Label("0.0", 19, W.Bold, 1, num=True)
        self.lbl_hero.setFixedHeight(20)
        self.lbl_unit = Label("/ 2.0 L", 10.5, W.Medium, 3, num=True)
        self.lbl_unit.setFixedHeight(20)
        self.lbl_sub = Label("", 10.5, W.Normal, 2)
        self.lbl_sub.setFixedHeight(14)
        self.prog_bar = _ProgressBarWidget(COLOR_WATER if mode == "water" else COLOR_SIT, self)

        if mode == "water":
            self.btn_main = _GlyphPill(
                "喝一口",
                draw_fn=lambda p, cx, cy, col: draw_droplet(p, cx, cy, 10.5, col),
                accent=COLOR_WATER,
                height=22,
                min_width=66,
                parent=self,
            )
            self.btn_main.clicked.connect(self._on_drink)
            self.btn_undo = Label("撤销", 10.5, W.Medium, 3, parent=self)
            self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_undo.setToolTip("撤销最近一次饮水记录")
            self.btn_undo.clicked.connect(self._on_undo)
        else:
            self.btn_main = _GlyphPill(
                "起身了",
                draw_fn=lambda p, cx, cy, col: draw_chair(p, cx, cy, 10.5, col),
                accent=COLOR_SIT,
                height=22,
                min_width=66,
                parent=self,
            )
            self.btn_main.setToolTip("久坐计时归零；锁屏（Win + L）也会自动归零")
            self.btn_main.clicked.connect(self._on_stand)
            self.btn_undo = None

        self.btn_cfg = _GlyphPill("设置", glyph=G.SLIDERS, height=22, min_width=60, parent=self)
        self.btn_cfg.clicked.connect(self.open_settings.emit)

        left_btns = [self.btn_main, self.btn_cfg, None]

        self.btn_row = QtWidgets.QWidget(self)
        self.btn_row.setFixedHeight(22)
        self.btn_row.setLayout(hbox(*left_btns, spacing=7, margins=(0, 0, 0, 0)))

        sub_items = [self.lbl_sub, None]
        if self.btn_undo is not None:
            sub_items.append(self.btn_undo)
        self.sub_row = QtWidgets.QWidget(self)
        self.sub_row.setFixedHeight(14)
        self.sub_row.setLayout(hbox(*sub_items, spacing=4, margins=(0, 0, 2, 0)))

        left_box = vbox(
            hbox(self.lbl_hero, self.lbl_unit, None, spacing=4),
            self.sub_row,
            self.prog_bar,
            3,
            self.btn_row,
            spacing=1,
            margins=(10, 0, 4, 0),
        )
        self.left_w = QtWidgets.QWidget(self)
        self.left_w.setFixedWidth(216)
        self.left_w.setLayout(left_box)

        self.sep = QtWidgets.QFrame(self)
        self.sep.setFixedWidth(1)
        self.sep.setFixedHeight(60)
        self.sep.setStyleSheet("background:rgba(128,128,128,48);")

        self.ring = _ProgressRing(mode=mode, parent=self)

        self.lbl_rem_title = Label("提醒", 11, W.Medium, 2, parent=self)
        self.sw_remind = Switch(checked=True, parent=self)
        self.sw_remind.setFixedSize(36, 20)
        self.sw_remind.setToolTip("打开 / 关闭提醒")
        self.sw_remind.toggled.connect(self._on_toggle_remind)

        rem_col = vbox(
            self.lbl_rem_title,
            self.sw_remind,
            None,
            spacing=4,
            margins=(0, 4, 2, 0),
        )
        right_w = QtWidgets.QWidget(self)
        right_w.setLayout(hbox(self.ring, (rem_col, 1), spacing=10, margins=(6, 0, 4, 0)))

        self.setLayout(hbox(self.left_w, self.sep, (right_w, 1), spacing=8, margins=(0, 0, 0, 0)))

    def _on_drink(self):
        if self.ctx and getattr(self.ctx, "water", None):
            self.ctx.water.drink()
            if getattr(self.ctx, "health", None):
                self.ctx.health.reset_water_timer()

    def _on_undo(self):
        if self.ctx and getattr(self.ctx, "water", None):
            self.ctx.water.undo_last()

    def _on_stand(self):
        if self.ctx and getattr(self.ctx, "health", None):
            self.ctx.health.stood_up()

    def _on_toggle_remind(self, on: bool):
        if self.mode == "water":
            if self.ctx and getattr(self.ctx, "water", None):
                self.ctx.water.set_remind_enabled(on)
        else:
            if self.ctx and getattr(self.ctx, "health", None):
                self.ctx.health.set_sit_remind_enabled(on)
        self.refresh()

    def refresh(self):
        health = getattr(self.ctx, "health", None) if self.ctx else None
        if self.mode == "water":
            water = getattr(self.ctx, "water", None) if self.ctx else None
            if water is None:
                return
            liters = water.drunk_ml / 1000.0
            goal_l = water.goal_ml() / 1000.0
            pct = water.percent()
            on = water.remind_enabled()
            self.sw_remind.setChecked(on, emit=False)
            self.lbl_hero.setText(f"{liters:.1f}")
            self.lbl_unit.setText(f"/ {goal_l:.1f} L")
            if water.reached_goal():
                tail = "已达标"
            elif not on:
                tail = "提醒已关"
            elif health and health.is_lunch_rest():
                tail = "午休中"
            elif health and not health.in_remind_window():
                sh, eh = health.remind_window_hours()
                tail = f"{sh:02d}:00–{eh:02d}:00 外"
            elif health:
                tail = f"下次 {health.next_water_clock()}"
            else:
                tail = ""
            sub_txt = f"今天 · {pct:.0f}%" + (f" · {tail}" if tail else "")
            self.lbl_sub.setText(sub_txt)
            self.lbl_sub.setToolTip(sub_txt)
            self.prog_bar.set_value(pct / 100.0, COLOR_WATER)
            self.ring.set_state(pct / 100.0, f"{pct:.0f}", "%", active=True, overdue=False)
            if self.btn_undo is not None:
                self.btn_undo.setVisible(water.can_undo())
        else:
            if health is None:
                return
            on = health.sit_remind_enabled()
            self.sw_remind.setChecked(on, emit=False)
            sat = health.sat_minutes()
            left = health.sit_remaining_minutes()
            in_win = health.in_remind_window()
            lunch = health.is_lunch_rest()
            sh, eh = health.remind_window_hours()
            overdue = on and in_win and not lunch and (health.sit_overdue or left == 0)

            if not on:
                self.lbl_hero.setText(f"{sat}")
                self.lbl_hero.set_color(None)
                self.lbl_unit.setText("分钟")
                sub_txt = "久坐提醒已关闭"
                bar_col = COLOR_MUTED_GRAY
                active_visual = False
            elif lunch:
                self.lbl_hero.setText(f"{sat}")
                self.lbl_hero.set_color(None)
                self.lbl_unit.setText("分钟")
                sub_txt = "午休中不提醒"
                bar_col = COLOR_MUTED_GRAY
                active_visual = False
            elif not in_win:
                self.lbl_hero.setText(f"{sat}")
                self.lbl_hero.set_color(None)
                self.lbl_unit.setText("分钟")
                sub_txt = f"当前不提醒 · {sh:02d}:00–{eh:02d}:00"
                bar_col = COLOR_MUTED_GRAY
                active_visual = False
            elif overdue:
                self.lbl_hero.setText("该起身了")
                self.lbl_hero.set_color(COLOR_WARN)
                self.lbl_unit.setText("")
                sub_txt = f"已坐 {sat} 分钟 · 起来走走再回来"
                bar_col = COLOR_WARN
                active_visual = True
            else:
                self.lbl_hero.setText(f"{left}")
                self.lbl_hero.set_color(None)
                self.lbl_unit.setText("分钟后提醒")
                sub_txt = f"已坐 {sat} 分钟 · {health.next_sit_clock()} 提醒"
                bar_col = COLOR_SIT
                active_visual = True

            self.lbl_sub.setText(sub_txt)
            self.lbl_sub.setToolTip(sub_txt)
            frac = health.sit_progress() if on else min(1.0, sat / max(1, health.sit_interval_min()))
            self.prog_bar.set_value(frac, bar_col)
            ring_lbl = "已关闭" if not on else ("时段外" if not active_visual else ("动一动" if overdue else "坐着"))
            self.ring.set_state(frac, "", ring_lbl, active=active_visual, overdue=overdue)


class _TodoInput(QtWidgets.QLineEdit):
    """支持中文输入法组合态保护（IME composing 时 Enter 不误提交）、Esc 取消、失焦提交。"""
    commit_text = QtCore.Signal(str)
    cancel_edit = QtCore.Signal()

    def __init__(self, placeholder: str = "输入待办内容，回车保存", parent=None):
        super().__init__(parent)
        self._composing = False
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(20)
        self.setFont(ui_font(11.5))
        theme.changed.connect(self._style)
        self._style()

    def _style(self):
        bg = theme.qss_color(theme.card(1.4))
        fg = theme.qss_color(theme.text())
        fc = theme.qss_color(theme.on_color())
        self.setStyleSheet(
            f"QLineEdit{{background:{bg};border:1px solid {fc};border-radius:6px;padding:0 6px;color:{fg};}}"
        )

    def inputMethodEvent(self, e):
        self._composing = bool(e.preeditString())
        super().inputMethodEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancel_edit.emit()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._composing:
                super().keyPressEvent(e)
                return
            self.commit_text.emit(self.text())
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if not self._composing:
            self.commit_text.emit(self.text())


class _TodoReminderDialog(QtWidgets.QDialog):
    """设置待办提醒时间与每周重复规则弹窗。"""
    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置提醒与重复")
        self.setModal(True)
        self.setFixedWidth(350)
        self.result_due: str | None = item.get("due")
        self.result_repeat: int = int(item.get("repeat", 0) or 0) & REPEAT_ALL

        t_lbl = Label("提醒与每周重复", 14, W.DemiBold, 1)
        self.chk_due = QtWidgets.QCheckBox("开启到点提醒", self)
        self.chk_due.setChecked(bool(self.result_due))

        now = dt.datetime.now()
        init_dt = now.strftime("%Y-%m-%d %H:%M")
        self.ed_due = QtWidgets.QLineEdit(self.result_due or init_dt, self)
        self.ed_due.setPlaceholderText("YYYY-MM-DD HH:MM")
        self.ed_due.setEnabled(self.chk_due.isChecked())
        self.chk_due.toggled.connect(self.ed_due.setEnabled)

        rep_lbl = Label("重复频率", 12, W.Medium, 2)
        self.day_btns: list[QtWidgets.QCheckBox] = []
        day_row = hbox(spacing=4)
        for i, name in enumerate(WEEKDAY_NAMES):
            cb = QtWidgets.QCheckBox(name, self)
            cb.setChecked(bool(self.result_repeat & (1 << i)))
            self.day_btns.append(cb)
            day_row.addWidget(cb)

        def _set_mask(m: int):
            for i, cb in enumerate(self.day_btns):
                cb.setChecked(bool(m & (1 << i)))

        btn_once = PillButton("一次性", height=24)
        btn_once.clicked.connect(lambda: _set_mask(0))
        btn_work = PillButton("工作日", height=24)
        btn_work.clicked.connect(lambda: _set_mask(REPEAT_WORKDAYS))
        btn_wknd = PillButton("周末", height=24)
        btn_wknd.clicked.connect(lambda: _set_mask(REPEAT_WEEKEND))
        btn_all = PillButton("每天", height=24)
        btn_all.clicked.connect(lambda: _set_mask(REPEAT_ALL))

        btn_cancel = PillButton("取消", height=28)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = PillButton("确定", G.CHECK, height=28, primary=True)
        btn_ok.clicked.connect(self._on_ok)

        self.setLayout(vbox(
            t_lbl,
            self.chk_due,
            self.ed_due,
            rep_lbl,
            day_row,
            hbox(btn_once, btn_work, btn_wknd, btn_all, None, spacing=4),
            6,
            hbox(None, btn_cancel, btn_ok, spacing=8),
            spacing=8,
            margins=(16, 14, 16, 14),
        ))
        bg = "#1e2028" if theme.dark else "#f5f6fa"
        fg = "#ffffff" if theme.dark else "#14161c"
        self.setStyleSheet(f"QDialog{{background:{bg};color:{fg};}} QCheckBox{{color:{fg};}}")

    def _on_ok(self):
        self.result_repeat = 0
        for i, cb in enumerate(self.day_btns):
            if cb.isChecked():
                self.result_repeat |= (1 << i)
        if self.chk_due.isChecked():
            txt = self.ed_due.text().strip()
            self.result_due = txt if txt else dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        else:
            self.result_due = None
        self.accept()


class _TodoRow(Hoverable):
    """单行待办：勾选圈 + 正文(重复标签) + 提醒按钮 + 编辑/删除。"""
    toggled = QtCore.Signal(str)
    edit_requested = QtCore.Signal(str)
    remind_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)

    def __init__(self, item: dict, today: str, choose_edit: bool = False, parent=None):
        super().__init__(parent)
        self.item = item
        self.item_id: str = item["id"]
        self.today = today
        self.choose_edit = choose_edit
        self.scheduled = scheduled_on(item, today)
        self.checked = done_for(item, today)
        self.setFixedHeight(20)

        rep_s = repeat_tag(int(item.get("repeat", 0) or 0))
        rem_s = reminder_text(item)
        tip = item["text"]
        if rep_s:
            tip += f"\n重复：{rep_s}" + ("" if self.scheduled else "（今天不用做）")
        if rem_s:
            tip += f"\n提醒：{rem_s}"
        self.setToolTip(tip)

        self.btn_rem = IconButton(G.BELL, size=18, icon_px=9, tip=f"提醒：{rem_s}" if rem_s else "设置提醒与重复", parent=self)
        if item.get("due"):
            self.btn_rem.color = COLOR_WATER
        self.btn_rem.clicked.connect(lambda: self.remind_requested.emit(self.item_id))

        self.btn_del = IconButton(G.CLOSE, size=18, icon_px=8, tip="删除待办", parent=self)
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self.item_id))

        self.btn_rem.setVisible(not choose_edit)
        self.btn_del.setVisible(not choose_edit)

        self.clicked.connect(self._on_row_click)
        self.setLayout(hbox(None, self.btn_rem, self.btn_del, spacing=1, margins=(4, 1, 2, 1)))

    def _on_row_click(self):
        if self.choose_edit:
            self.edit_requested.emit(self.item_id)
        elif self.scheduled:
            self.toggled.emit(self.item_id)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover.x > 0.01:
            bg = QtGui.QColor(theme.hover())
            bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 6, 6)

        if self.choose_edit:
            draw_pencil(p, r.left() + 10.0, r.center().y(), 10.0, COLOR_WATER)
        else:
            cx, cy = r.left() + 10.0, r.center().y()
            if self.checked:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(COLOR_SIT)
                p.drawEllipse(QtCore.QPointF(cx, cy), 5.2, 5.2)
                p.setPen(QtGui.QPen(QtGui.QColor("white"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QtCore.QPointF(cx - 2.3, cy), QtCore.QPointF(cx - 0.5, cy + 2.0))
                p.drawLine(QtCore.QPointF(cx - 0.5, cy + 2.0), QtCore.QPointF(cx + 2.6, cy - 1.8))
            else:
                p.setPen(QtGui.QPen(theme.text3(0.8 if self.scheduled else 0.4), 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QtCore.QPointF(cx, cy), 4.8, 4.8)

        tag = repeat_tag(int(self.item.get("repeat", 0) or 0))
        right_pad = 38 if not self.choose_edit else 34
        tag_w = (QtGui.QFontMetrics(ui_font(9.5)).horizontalAdvance(tag) + 6) if tag else 0
        txt_x = r.left() + 20.0
        max_tw = max(30.0, r.right() - txt_x - right_pad - tag_w)

        f = ui_font(11.5)
        p.setFont(f)
        if self.checked:
            p.setPen(theme.text3())
        elif not self.scheduled:
            p.setPen(theme.text3(0.65))
        else:
            p.setPen(theme.text())
        fm = QtGui.QFontMetrics(f)
        elided = fm.elidedText(self.item["text"], Qt.TextElideMode.ElideRight, int(max_tw))
        p.drawText(QtCore.QRectF(txt_x, r.top(), max_tw, r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        if self.checked:
            tw_actual = min(max_tw, fm.horizontalAdvance(elided))
            p.setPen(QtGui.QPen(theme.text3(), 1.0))
            p.drawLine(QtCore.QPointF(txt_x, r.center().y()), QtCore.QPointF(txt_x + tw_actual, r.center().y()))

        if tag:
            p.setFont(ui_font(9.5))
            p.setPen(COLOR_WATER if self.scheduled else theme.text3())
            p.drawText(QtCore.QRectF(txt_x + fm.horizontalAdvance(elided) + 4, r.top(), tag_w, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)

        if self.choose_edit:
            p.setFont(ui_font(10))
            p.setPen(COLOR_WATER)
            p.drawText(QtCore.QRectF(r.right() - 32, r.top(), 28, r.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "修改")


class _EmptyChecklistIcon(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 22)

    def paintEvent(self, e):
        p = painter(self)
        draw_checklist(p, self.width() / 2.0, self.height() / 2.0, 18.0, theme.text2(), theme.text3(0.5))


class _TodoScrollArea(ScrollArea):
    """待办专用滚动区：<=3 条时完全隐藏滚动条；>3 条时显示 2px 极细滚动条，且尽量不挤压右侧内容。"""
    def _style_bar(self):
        c = theme.qss_color(theme.text(0.28))
        self.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical{background:transparent;width:2px;margin:2px 0 2px 0}"
            f"QScrollBar::handle:vertical{{background:{c};border-radius:1px;min-height:16px}}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0}"
            "QScrollBar::add-page,QScrollBar::sub-page{background:transparent}")


class _TodoView(QtWidgets.QWidget):
    """待办视图：左 158px（今日完成数 + 加一个 + 改一下）+ 右侧内嵌圆角列表卡。"""
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._adding = False
        self._editing_id: str | None = None
        self._choosing_edit = False
        self._input_box: _TodoInput | None = None

        self.lbl_done = Label("0", 20, W.Bold, 1, num=True)
        self.lbl_total = Label("/ 0", 13, W.Bold, 2, num=True)
        self.lbl_hint = Label("已完成", 10.5, W.Normal, 3)

        self.btn_add = _GlyphPill(
            "加一个",
            draw_fn=lambda p, cx, cy, col: self._draw_plus_circle(p, cx, cy, col),
            height=22,
            parent=self,
        )
        self.btn_add.clicked.connect(self.start_add)

        self.btn_edit = _GlyphPill(
            "改一下",
            draw_fn=lambda p, cx, cy, col: draw_pencil(p, cx, cy, 10.0, col),
            height=22,
            parent=self,
        )
        self.btn_edit.clicked.connect(self._toggle_choose_edit)

        left_w = QtWidgets.QWidget(self)
        left_w.setFixedWidth(158)
        left_w.setLayout(vbox(
            hbox(self.lbl_done, self.lbl_total, None, spacing=2),
            self.lbl_hint,
            None,
            hbox(self.btn_add, self.btn_edit, None, spacing=6),
            spacing=2,
            margins=(4, 0, 4, 0),
        ))

        self.list_card = Card(radius=12, alpha=1.0)
        self.scroll = _TodoScrollArea(self.list_card)
        self.list_lay = vbox(spacing=2, margins=(4, 3, 2, 3))
        self.scroll.inner.setLayout(self.list_lay)
        self.list_card.setLayout(vbox((self.scroll, 1), margins=(0, 0, 0, 0)))

        self.setLayout(hbox(left_w, (self.list_card, 1), spacing=8, margins=(0, 0, 0, 0)))

    @staticmethod
    def _draw_plus_circle(p: QtGui.QPainter, cx: float, cy: float, col: QtGui.QColor):
        p.save()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QtCore.QPointF(cx, cy), 5.5, 5.5)
        p.setPen(QtGui.QPen(theme.pill_text() if theme.dark else QtGui.QColor("white"), 1.3,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QtCore.QPointF(cx - 2.6, cy), QtCore.QPointF(cx + 2.6, cy))
        p.drawLine(QtCore.QPointF(cx, cy - 2.6), QtCore.QPointF(cx, cy + 2.6))
        p.restore()

    def _want_focus(self):
        p = self.parentWidget()
        while p is not None:
            if hasattr(p, "want_focus"):
                p.want_focus()
                break
            p = p.parentWidget()

    def _release_focus(self):
        p = self.parentWidget()
        while p is not None:
            if hasattr(p, "island") and p.island is not None:
                p.island.release_focus()
                break
            p = p.parentWidget()

    def start_add(self):
        self._choosing_edit = False
        self._editing_id = None
        self._adding = True
        self.refresh()
        if self._input_box is not None:
            self._want_focus()
            self._input_box.setFocus()

    def start_edit(self, item_id: str):
        self._choosing_edit = False
        self._adding = False
        self._editing_id = item_id
        self.refresh()
        if self._input_box is not None:
            self._want_focus()
            self._input_box.setFocus()
            self._input_box.selectAll()

    def _toggle_choose_edit(self):
        todo = getattr(self.ctx, "todo", None)
        if not todo or not todo.items:
            return
        self._choosing_edit = not self._choosing_edit
        self._adding = False
        self._editing_id = None
        self.refresh()

    def _commit_input(self, text: str):
        if not self._adding and self._editing_id is None:
            return
        todo = getattr(self.ctx, "todo", None)
        was_add = self._adding
        edit_id = self._editing_id
        self._adding = False
        self._editing_id = None
        self._input_box = None
        self._release_focus()

        clean = (text or "").strip()
        if todo is not None and clean:
            if was_add:
                todo.add(clean)
            elif edit_id:
                ok, err = todo.update_text(edit_id, clean)
                if not ok and err:
                    todo.notice.emit(err, True)
        self.refresh()

    def _cancel_input(self):
        self._adding = False
        self._editing_id = None
        self._input_box = None
        self._release_focus()
        self.refresh()

    def _on_remind(self, item_id: str):
        todo = getattr(self.ctx, "todo", None)
        if not todo:
            return
        res = todo.get_by_id(item_id)
        if res is None:
            todo.notice.emit("该待办已不存在或已被删除", True)
            return
        _, item = res
        p = self.window()
        isl = None
        cur = self.parentWidget()
        while cur is not None:
            cand = getattr(cur, "island", None)
            if cand is not None:
                isl = cand
                break
            cur = cur.parentWidget()
        if isl:
            isl.locks.add("dialog")
        try:
            dlg = _TodoReminderDialog(item, parent=p)
            if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                todo.set_schedule(item_id, dlg.result_due, dlg.result_repeat)
        finally:
            if isl:
                isl.locks.discard("dialog")

    def refresh(self):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._input_box = None

        todo = getattr(self.ctx, "todo", None) if self.ctx else None
        if todo is None:
            return
        day = today_str()
        done, total = todo.progress(day)
        self.lbl_done.setText(str(done))
        self.lbl_done.set_color(COLOR_SIT if (total > 0 and done == total) else None)
        self.lbl_total.setText(f"/ {total}")
        self.btn_edit.setEnabled(bool(todo.items) and not self._adding and self._editing_id is None)

        ordered = todo.list_ordered(day)
        editing_any = self._adding or (self._editing_id is not None)

        # 空状态与 1-3 条待办完全隐藏滚动条；超过 3 条显示 2px 细滚动条
        visible_rows = len(ordered) + (1 if editing_any else 0)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if visible_rows > 3 else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        if editing_any:
            init_val = ""
            if self._editing_id:
                found = todo.get_by_id(self._editing_id)
                if found:
                    init_val = found[1]["text"]
            inp = _TodoInput(parent=self.scroll.inner)
            inp.setText(init_val)
            inp.commit_text.connect(self._commit_input)
            inp.cancel_edit.connect(self._cancel_input)
            self._input_box = inp
            self.list_lay.addWidget(inp)

        if not ordered and not editing_any:
            empty_icon = _EmptyChecklistIcon(self.scroll.inner)
            empty_lbl = Label("今天还没有安排", 11.5, W.Normal, 3, align=Qt.AlignmentFlag.AlignCenter)
            self.list_lay.addStretch(1)
            self.list_lay.addWidget(hbox_wrap(empty_icon, self.scroll.inner))
            self.list_lay.addWidget(empty_lbl)
            self.list_lay.addStretch(1)
            return

        for _, item in ordered:
            if self._editing_id == item["id"]:
                continue
            row = _TodoRow(item, day, choose_edit=self._choosing_edit, parent=self.scroll.inner)
            row.toggled.connect(lambda iid: todo.toggle(iid, day))
            row.edit_requested.connect(self.start_edit)
            row.remind_requested.connect(self._on_remind)
            row.delete_requested.connect(todo.remove)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)


def hbox_wrap(w: QtWidgets.QWidget, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
    wrap = QtWidgets.QWidget(parent)
    wrap.setLayout(hbox(None, w, None, spacing=0, margins=(0, 0, 0, 0)))
    return wrap


class LifePage(Page):
    key, glyph, title, height, panel_width, panel_height = "life", G.CHECK, "生活", 98, 419, 140

    def __init__(self, ctx):
        super().__init__(ctx)
        # ---- 统一 Header：左(动态标题 + 三图标切换) | 中(日期时间) | 右(设置齿轮 -> 工具与生活) ----
        self.header = PageHeader(ctx, self, show_gear=False, height=26)
        self.title_w = _DynamicTitle(self)
        self.tabs = _LifeTabs(self)
        self.tabs.changed.connect(self.switch_view)

        self.gear_btn = IconButton(G.GEAR, 22, 11, "打开工具与生活设置", parent=self)
        self.gear_btn.clicked.connect(self._open_life_settings)

        self.header.add_left(self.title_w)
        self.header.add_left(self.tabs)
        self.header.add_right(self.gear_btn)

        # ---- 三个内部视图（交叉淡化切换，外框尺寸不变） ----
        self.stack = QtWidgets.QStackedWidget(self)
        self.todo_view = _TodoView(ctx, self.stack)
        self.water_view = _HealthView(ctx, "water", self.stack)
        self.sit_view = _HealthView(ctx, "sit", self.stack)
        self.water_view.open_settings.connect(self._open_life_settings)
        self.sit_view.open_settings.connect(self._open_life_settings)

        self.stack.addWidget(self.todo_view)
        self.stack.addWidget(self.water_view)
        self.stack.addWidget(self.sit_view)

        self._fade_eff = QtWidgets.QGraphicsOpacityEffect(self.stack)
        self._fade_eff.setOpacity(1.0)
        self.stack.setGraphicsEffect(self._fade_eff)
        self._fade_anim = QtCore.QPropertyAnimation(self._fade_eff, b"opacity", self)
        self._fade_anim.setDuration(150)

        self.setLayout(vbox(self.header, (self.stack, 1), spacing=4))

        if hasattr(ctx, "todo") and ctx.todo is not None:
            ctx.todo.changed.connect(self.todo_view.refresh)
        if hasattr(ctx, "water") and ctx.water is not None:
            ctx.water.changed.connect(self.water_view.refresh)
        if hasattr(ctx, "health") and ctx.health is not None:
            ctx.health.state_changed.connect(self._refresh_health)

        self.switch_view("todo", animate=False)
        self.refresh_all()

    def _open_life_settings(self):
        if self.ctx and hasattr(self.ctx, "open_settings"):
            self.ctx.open_settings("tools_life")

    def switch_view(self, key: str, animate: bool = True):
        order = ("todo", "water", "sit")
        if key not in order:
            return
        idx = order.index(key)
        self.tabs.set_tab(key, emit=False)
        if key == "todo":
            self.title_w.set_title("待办", "清单", COLOR_SIT)
        elif key == "water":
            self.title_w.set_title("喝水", "提醒", COLOR_WATER)
        else:
            self.title_w.set_title("久坐", "提醒", COLOR_SIT)

        if self.stack.currentIndex() != idx:
            self.stack.setCurrentIndex(idx)
            if animate and self.isVisible():
                self._fade_anim.stop()
                self._fade_eff.setOpacity(0.15)
                self._fade_anim.setStartValue(0.15)
                self._fade_anim.setEndValue(1.0)
                self._fade_anim.start()
        self.refresh_all()

    def _refresh_health(self):
        if self.active or self.isVisible():
            self.water_view.refresh()
            self.sit_view.refresh()

    def refresh_all(self):
        self.todo_view.refresh()
        self.water_view.refresh()
        self.sit_view.refresh()

    def on_show(self):
        super().on_show()
        self.header.refresh_clock()
        self.refresh_all()
