# -*- coding: utf-8 -*-
"""待办服务：支持一次性待办、每周重复（每天/工作日/周末/自定义星期）、指定提醒时间、排序与进度统计。
数据文件保存在 data/todo.json，原子写入，损坏时先备份 .corrupt.bak 再恢复默认空列表；不在日志输出待办正文。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from PySide6 import QtCore

from ..core.config import DATA_DIR

WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")
REPEAT_ALL = 0b111_1111
REPEAT_WORKDAYS = 0b001_1111
REPEAT_WEEKEND = 0b110_0000


def today_str(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%d")


def now_hm_str(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")


def weekday_bit(date_str: str) -> int | None:
    try:
        day = dt.date.fromisoformat(str(date_str)[:10])
        return 1 << day.weekday()
    except Exception:  # noqa: BLE001
        return None


def repeat_label(mask: int) -> str:
    m = int(mask or 0) & REPEAT_ALL
    if m == 0:
        return "不重复"
    if m == REPEAT_ALL:
        return "每天"
    if m == REPEAT_WORKDAYS:
        return "工作日"
    if m == REPEAT_WEEKEND:
        return "周末"
    days = [WEEKDAY_NAMES[i] for i in range(7) if m & (1 << i)]
    return "每周" + "、".join(days)


def repeat_tag(mask: int) -> str:
    m = int(mask or 0) & REPEAT_ALL
    if m == 0:
        return ""
    if m == REPEAT_ALL:
        return "每天"
    if m == REPEAT_WORKDAYS:
        return "工作日"
    if m == REPEAT_WEEKEND:
        return "周末"
    return "周" + "".join(WEEKDAY_NAMES[i] for i in range(7) if m & (1 << i))


def is_repeating(item: dict) -> bool:
    return (int(item.get("repeat", 0) or 0) & REPEAT_ALL) != 0


def scheduled_on(item: dict, day: str) -> bool:
    """一次性待办每天都显示；重复待办只在选中的星期几显示。"""
    rep = int(item.get("repeat", 0) or 0) & REPEAT_ALL
    if rep == 0:
        return True
    wb = weekday_bit(day)
    return wb is not None and (rep & wb) != 0


def done_for(item: dict, day: str) -> bool:
    """重复待办第二天自动变回未完成。"""
    done_on = item.get("done_on")
    if is_repeating(item):
        return done_on == day
    return bool(done_on)


def reminder_text(item: dict) -> str | None:
    due = item.get("due")
    if not due:
        return None
    if is_repeating(item):
        hm = due[11:16] if len(due) >= 16 else due
        return f"{repeat_label(int(item.get('repeat', 0)))} {hm}"
    return str(due)


def reminder_due(item: dict, now_str: str) -> bool:
    """now_str 格式为 YYYY-MM-DD HH:MM。已完成不再提醒，到点只触发一次。"""
    due = item.get("due")
    if not due or not isinstance(due, str):
        return False
    today = now_str[:10]
    if done_for(item, today):
        return False
    if not is_repeating(item):
        return (not bool(item.get("reminded", False))) and (due <= now_str)
    at = due[11:16] if len(due) >= 16 else (due if len(due) == 5 and ":" in due else "")
    clock = now_str[11:16]
    if not at or len(clock) != 5:
        return False
    return scheduled_on(item, today) and item.get("reminded_on") != today and at <= clock


class TodoService(QtCore.QObject):
    changed = QtCore.Signal()
    notice = QtCore.Signal(str, bool)

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._lock = threading.RLock()
        self.path: Path = Path(path) if path is not None else (DATA_DIR / "todo.json")
        self.items: list[dict] = []
        self.load()

    def load(self):
        with self._lock:
            self.items = []
            if not self.path.exists():
                return
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                rows = data.get("items") if isinstance(data, dict) else (data if isinstance(data, list) else None)
                if not isinstance(rows, list):
                    raise ValueError("todo.json 格式无效")
                loaded: list[dict] = []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    txt = str(r.get("text") or "").strip()
                    if not txt:
                        continue
                    loaded.append({
                        "id": str(r.get("id") or uuid.uuid4().hex[:12]),
                        "text": txt,
                        "done_on": r.get("done_on") if isinstance(r.get("done_on"), str) and r.get("done_on") else None,
                        "due": r.get("due") if isinstance(r.get("due"), str) and r.get("due") else None,
                        "repeat": int(r.get("repeat", 0) or 0) & REPEAT_ALL,
                        "reminded": bool(r.get("reminded", False)),
                        "reminded_on": r.get("reminded_on") if isinstance(r.get("reminded_on"), str) and r.get("reminded_on") else None,
                    })
                self.items = loaded
            except Exception:  # noqa: BLE001
                self._backup_corrupted()
                self.items = []

    def _backup_corrupted(self):
        try:
            bak = self.path.with_suffix(".corrupt.bak")
            shutil.copy2(self.path, bak)
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "items": self.items}
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def progress(self, day: str | None = None) -> tuple[int, int]:
        """今天要做的待办中 (已完成, 总数)。"""
        d = day or today_str()
        with self._lock:
            done = 0
            total = 0
            for it in self.items:
                if scheduled_on(it, d):
                    total += 1
                    if done_for(it, d):
                        done += 1
            return done, total

    def display_order(self, day: str | None = None) -> list[int]:
        """排序规则：1. 今天未完成；2. 其他日期的未完成；3. 已完成沉底；同组保持原始顺序。"""
        d = day or today_str()
        with self._lock:
            indices = list(range(len(self.items)))
            indices.sort(key=lambda i: (1 if done_for(self.items[i], d) else 0,
                                        0 if scheduled_on(self.items[i], d) else 1, i))
            return indices

    def list_ordered(self, day: str | None = None) -> list[tuple[int, dict]]:
        d = day or today_str()
        with self._lock:
            order = self.display_order(d)
            return [(i, dict(self.items[i])) for i in order]

    def get_by_id(self, item_id: str) -> tuple[int, dict] | None:
        with self._lock:
            for i, it in enumerate(self.items):
                if it["id"] == item_id:
                    return i, dict(it)
            return None

    def add(self, text: str, due: str | None = None, repeat: int = 0) -> dict | None:
        clean = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
        if not clean:
            return None
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": clean,
            "done_on": None,
            "due": due.strip() if isinstance(due, str) and due.strip() else None,
            "repeat": int(repeat or 0) & REPEAT_ALL,
            "reminded": False,
            "reminded_on": None,
        }
        with self._lock:
            self.items.append(item)
            self.save()
        self.changed.emit()
        return dict(item)

    def update_text(self, item_id: str, new_text: str) -> tuple[bool, str]:
        clean = " ".join((new_text or "").replace("\r", " ").replace("\n", " ").split())
        if not clean:
            return False, "待办内容不能为空"
        with self._lock:
            for it in self.items:
                if it["id"] == item_id:
                    it["text"] = clean
                    self.save()
                    self.changed.emit()
                    return True, ""
        return False, "该待办已不存在或已被删除"

    def set_schedule(self, item_id: str, due: str | None, repeat: int) -> bool:
        with self._lock:
            for it in self.items:
                if it["id"] == item_id:
                    it["due"] = due.strip() if isinstance(due, str) and due.strip() else None
                    it["repeat"] = int(repeat or 0) & REPEAT_ALL
                    it["reminded"] = False
                    it["reminded_on"] = None
                    self.save()
                    self.changed.emit()
                    return True
        return False

    def toggle(self, item_id: str, day: str | None = None) -> bool:
        d = day or today_str()
        with self._lock:
            for it in self.items:
                if it["id"] == item_id:
                    it["done_on"] = None if done_for(it, d) else d
                    self.save()
                    self.changed.emit()
                    return True
        return False

    def remove(self, item_id: str) -> bool:
        with self._lock:
            before = len(self.items)
            self.items = [it for it in self.items if it["id"] != item_id]
            if len(self.items) != before:
                self.save()
                self.changed.emit()
                return True
        return False

    def clear_completed(self, day: str | None = None) -> int:
        d = day or today_str()
        with self._lock:
            before = len(self.items)
            self.items = [it for it in self.items if not done_for(it, d)]
            removed = before - len(self.items)
            if removed > 0:
                self.save()
                self.changed.emit()
            return removed

    def clear_all(self) -> int:
        with self._lock:
            removed = len(self.items)
            if removed > 0:
                self.items = []
                self.save()
                self.changed.emit()
            return removed

    def pop_due_reminders(self, now_dt: dt.datetime | None = None) -> list[dict]:
        """检查当前到点的待办提醒并原子标记已提醒，返回本次触发的条目列表。"""
        now_s = now_hm_str(now_dt)
        today = now_s[:10]
        due_list: list[dict] = []
        with self._lock:
            dirty = False
            for it in self.items:
                if reminder_due(it, now_s):
                    if is_repeating(it):
                        it["reminded_on"] = today
                    else:
                        it["reminded"] = True
                    due_list.append(dict(it))
                    dirty = True
            if dirty:
                self.save()
        if due_list:
            self.changed.emit()
        return due_list
