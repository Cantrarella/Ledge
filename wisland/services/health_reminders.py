# -*- coding: utf-8 -*-
"""健康与待办统一提醒调度服务：
负责喝水提醒、久坐提醒（含锁屏自动重置）与待办到点提醒，严格遵守提醒时段与午休免打扰。
"""
from __future__ import annotations

import ctypes
import datetime as dt
import time

from PySide6 import QtCore

from ..core.config import cfg
from .todo import TodoService
from .water import WaterService

user32 = ctypes.WinDLL("user32", use_last_error=True)


def _is_workstation_locked() -> bool:
    """轻量检测 Windows 当前是否处于锁屏状态（OpenInputDesktop 失败通常表示锁屏/UAC桌面）。"""
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x0001)
        if not hdesk:
            return True
        user32.CloseDesktop(hdesk)
        return False
    except Exception:  # noqa: BLE001
        return False


def _parse_hhmm(s: str, fallback_min: int) -> int:
    try:
        parts = str(s or "").strip().split(":")
        if len(parts) == 2:
            return max(0, min(24 * 60, int(parts[0]) * 60 + int(parts[1])))
    except Exception:  # noqa: BLE001
        pass
    return fallback_min


class HealthReminderService(QtCore.QObject):
    state_changed = QtCore.Signal()
    # 统一提醒事件：(kind: "water"|"sit"|"todo", title: str, sub: str)
    reminder_triggered = QtCore.Signal(str, str, str)

    def __init__(self, water: WaterService, todo: TodoService):
        super().__init__()
        self.water = water
        self.todo = todo
        now_m = time.monotonic()
        self.water_last_ts: float = now_m
        self.sit_start_ts: float = now_m
        self.sit_last_alert_ts: float | None = None
        self.sit_overdue: bool = False
        self._was_locked: bool = False
        self._last_tick_mono: float = now_m
        self._timer = QtCore.QTimer(self, interval=15000, timeout=self.tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()

    # ---------------- 配置读取 ----------------
    def sit_remind_enabled(self) -> bool:
        return bool(cfg.get("life.sit_remind", True))

    def set_sit_remind_enabled(self, on: bool):
        on = bool(on)
        cfg.set("life.sit_remind", on)
        if on:
            self.stood_up(silent=True)
        else:
            self.sit_overdue = False
        self.state_changed.emit()

    def water_interval_min(self) -> int:
        return max(5, min(180, int(cfg.get("life.water_interval_min", 45) or 45)))

    def sit_interval_min(self) -> int:
        return max(15, min(180, int(cfg.get("life.sit_interval_min", 60) or 60)))

    def remind_window_hours(self) -> tuple[int, int]:
        sh = max(0, min(23, int(cfg.get("life.remind_start_hour", 9) or 9)))
        eh = max(1, min(24, int(cfg.get("life.remind_end_hour", 22) or 22)))
        return sh, eh

    def in_remind_window(self, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now()
        sh, eh = self.remind_window_hours()
        return sh <= now.hour < eh

    def is_lunch_rest(self, now: dt.datetime | None = None) -> bool:
        if not bool(cfg.get("life.lunch_rest", True)):
            return False
        now = now or dt.datetime.now()
        cur_m = now.hour * 60 + now.minute
        ls = _parse_hhmm(cfg.get("life.lunch_start", "12:00"), 12 * 60)
        le = _parse_hhmm(cfg.get("life.lunch_end", "13:30"), 13 * 60 + 30)
        if ls <= le:
            return ls <= cur_m < le
        return cur_m >= ls or cur_m < le

    # ---------------- 业务状态与交互 ----------------
    def reset_water_timer(self):
        self.water_last_ts = time.monotonic()
        self.state_changed.emit()

    def stood_up(self, silent: bool = False):
        """点击“起身了”或锁屏后重置久坐计时。"""
        self.sit_start_ts = time.monotonic()
        self.sit_last_alert_ts = None
        self.sit_overdue = False
        if not silent:
            self.state_changed.emit()

    def sat_minutes(self, now_mono: float | None = None) -> int:
        cur = time.monotonic() if now_mono is None else now_mono
        return max(0, int((cur - self.sit_start_ts) // 60))

    def sit_remaining_minutes(self, now_mono: float | None = None) -> int:
        cur = time.monotonic() if now_mono is None else now_mono
        due = self.sit_start_ts + self.sit_interval_min() * 60.0
        rem = max(0.0, due - cur)
        return int((rem + 59.999) // 60) if rem > 0 else 0

    def sit_progress(self, now_mono: float | None = None) -> float:
        cur = time.monotonic() if now_mono is None else now_mono
        goal_s = max(60.0, self.sit_interval_min() * 60.0)
        return min(1.0, max(0.0, (cur - self.sit_start_ts) / goal_s))

    def next_water_clock(self, now_dt: dt.datetime | None = None, now_mono: float | None = None) -> str:
        now_dt = now_dt or dt.datetime.now()
        cur = time.monotonic() if now_mono is None else now_mono
        rem_s = max(0.0, self.water_last_ts + self.water_interval_min() * 60.0 - cur)
        target = now_dt + dt.timedelta(seconds=rem_s)
        return target.strftime("%H:%M")

    def next_sit_clock(self, now_dt: dt.datetime | None = None, now_mono: float | None = None) -> str:
        now_dt = now_dt or dt.datetime.now()
        cur = time.monotonic() if now_mono is None else now_mono
        rem_s = max(0.0, self.sit_start_ts + self.sit_interval_min() * 60.0 - cur)
        target = now_dt + dt.timedelta(seconds=rem_s)
        return target.strftime("%H:%M")

    def tick(self, now_dt: dt.datetime | None = None, now_mono: float | None = None, check_lock: bool = True):
        now_dt = now_dt or dt.datetime.now()
        cur = time.monotonic() if now_mono is None else now_mono
        if now_mono is None:
            if cur - self._last_tick_mono > 300.0:
                # 系统休眠/挂起恢复：重置久坐与喝水计时，避免一次性补发过期提醒
                self.water_last_ts = cur
                self.sit_start_ts = cur
                self.sit_last_alert_ts = None
                self.sit_overdue = False
            self._last_tick_mono = cur

        # 1) 跨天自动清零今日饮水
        self.water.ensure_today(now_dt.strftime("%Y-%m-%d"))

        # 2) 锁屏检测：锁屏后自动重置久坐计时
        if check_lock:
            locked = _is_workstation_locked()
            if locked and not self._was_locked:
                self._was_locked = True
                self.stood_up(silent=True)
            elif not locked and self._was_locked:
                self._was_locked = False
                self.stood_up(silent=True)

        in_win = self.in_remind_window(now_dt)
        lunch = self.is_lunch_rest(now_dt)

        # 3) 喝水定时提醒（未达目标、已开启、在时段内、非午休）
        if self.water.remind_enabled() and not self.water.reached_goal() and in_win and not lunch:
            if cur - self.water_last_ts >= self.water_interval_min() * 60.0:
                self.water_last_ts = cur
                self.reminder_triggered.emit("water", "该喝水啦", f"每次 {self.water.serving_ml()} ml · 今日已喝 {self.water.drunk_ml} ml")

        # 4) 久坐定时提醒（已开启、在时段内、非午休）
        if self.sit_remind_enabled() and in_win and not lunch:
            interval_s = self.sit_interval_min() * 60.0
            if cur - self.sit_start_ts >= interval_s:
                self.sit_overdue = True
                if self.sit_last_alert_ts is None or (cur - self.sit_last_alert_ts) >= interval_s:
                    self.sit_last_alert_ts = cur
                    self.reminder_triggered.emit("sit", "该起身活动一下了", f"已连续久坐 {self.sat_minutes(cur)} 分钟")
        else:
            self.sit_overdue = False

        # 5) 待办到点提醒（不受午休限制，按用户设定的时间触发）
        for item in self.todo.pop_due_reminders(now_dt):
            self.reminder_triggered.emit("todo", "待办提醒", item.get("text", ""))

        self.state_changed.emit()
