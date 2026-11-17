# -*- coding: utf-8 -*-
"""喝水记录服务：记录今日饮水量、目标百分比、24 小时统计与最近一次撤销，跨天自动清零。
数据文件保存在 data/water.json，原子写入，损坏先备份再从安全默认值恢复。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
from pathlib import Path

from PySide6 import QtCore

from ..core.config import DATA_DIR, cfg


def _today() -> str:
    return dt.date.today().isoformat()


class WaterService(QtCore.QObject):
    changed = QtCore.Signal()
    notice = QtCore.Signal(str, bool)

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._lock = threading.RLock()
        self.path: Path = Path(path) if path is not None else (DATA_DIR / "water.json")
        self.date: str = _today()
        self.drunk_ml: int = 0
        self.hours: list[int] = [0] * 24
        self._undo_stack: list[tuple[int, int]] = []
        self.load()

    def goal_ml(self) -> int:
        return max(500, min(6000, int(cfg.get("life.water_goal_ml", 2000) or 2000)))

    def serving_ml(self) -> int:
        return max(50, min(1000, int(cfg.get("life.water_serving_ml", 250) or 250)))

    def remind_enabled(self) -> bool:
        return bool(cfg.get("life.water_remind", True))

    def set_remind_enabled(self, on: bool):
        on = bool(on)
        cfg.set("life.water_remind", on)
        self.changed.emit()
        self.notice.emit("喝水提醒已打开" if on else "喝水提醒已关闭", False)

    def load(self):
        today = _today()
        with self._lock:
            self.date = today
            self.drunk_ml = 0
            self.hours = [0] * 24
            if not self.path.exists():
                self.save()
                return
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("water.json 格式无效")
                saved_date = str(data.get("date") or "")
                if saved_date == today:
                    self.drunk_ml = max(0, int(data.get("drunk_ml", 0) or 0))
                    hrs = data.get("hours")
                    if isinstance(hrs, list):
                        self.hours = [max(0, int(hrs[i] or 0)) if i < len(hrs) else 0 for i in range(24)]
                else:
                    # 跨天自动清零今日饮水记录，保留目标和设置
                    self.date = today
                    self.drunk_ml = 0
                    self.hours = [0] * 24
                    self._undo_stack.clear()
                    self.save()
            except Exception:  # noqa: BLE001
                self._backup_corrupted()
                self.date = today
                self.drunk_ml = 0
                self.hours = [0] * 24
                self.save()

    def _backup_corrupted(self):
        try:
            bak = self.path.with_suffix(".corrupt.bak")
            shutil.copy2(self.path, bak)
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "date": self.date,
                "drunk_ml": self.drunk_ml,
                "hours": self.hours,
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def ensure_today(self, today_override: str | None = None) -> bool:
        """若本地日期发生变化，自动清零今日饮水记录并返回 True。"""
        today = today_override or _today()
        with self._lock:
            if self.date != today:
                self.date = today
                self.drunk_ml = 0
                self.hours = [0] * 24
                self._undo_stack.clear()
                self.save()
                reset = True
            else:
                reset = False
        if reset:
            self.changed.emit()
        return reset

    def drink(self, ml: int | None = None, hour: int | None = None) -> int:
        self.ensure_today()
        amount = self.serving_ml() if ml is None else max(1, int(ml))
        hr = dt.datetime.now().hour if hour is None else max(0, min(23, int(hour)))
        cap = max(self.goal_ml(), amount) * 4
        with self._lock:
            new_total = min(cap, self.drunk_ml + amount)
            added = new_total - self.drunk_ml
            if added <= 0:
                return 0
            self.drunk_ml = new_total
            self.hours[hr] += added
            self._undo_stack.append((added, hr))
            if len(self._undo_stack) > 10:
                self._undo_stack.pop(0)
            self.save()
        self.changed.emit()
        return added

    def can_undo(self) -> bool:
        with self._lock:
            return len(self._undo_stack) > 0

    def undo_last(self) -> bool:
        with self._lock:
            if not self._undo_stack:
                return False
            ml, hr = self._undo_stack.pop()
            self.drunk_ml = max(0, self.drunk_ml - ml)
            self.hours[hr] = max(0, self.hours[hr] - ml)
            self.save()
        self.changed.emit()
        self.notice.emit("已撤销最近一次饮水记录", False)
        return True

    def percent(self) -> float:
        g = self.goal_ml()
        if g <= 0:
            return 0.0
        return min(100.0, max(0.0, self.drunk_ml / float(g) * 100.0))

    def reached_goal(self) -> bool:
        return self.drunk_ml >= self.goal_ml()
