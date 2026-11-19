# -*- coding: utf-8 -*-
"""统一 Windows 通知控制层：基线去重、同源更新、FIFO 横幅队列、账号窗口聚焦与未读管理。"""
from __future__ import annotations

import datetime as dt
import time
from collections import OrderedDict, deque

from PySide6 import QtCore

from ...core.config import cfg
from .identity import focus_event_target
from .models import NotificationEvent, PermissionStatus, mask_sensitive_text, permission_status_label
from .sources.taskbar import TaskbarFlashSource
from .sources.windows import WindowsNotificationSource, open_windows_notification_settings

MAX_DEDUPE_CACHE = 1000
HISTORY_MAX = 50
FLASH_DEDUPE_S = 8.0


def _parse_hhmm(s: str, fallback: int) -> int:
    try:
        parts = str(s or "").strip().split(":")
        if len(parts) == 2:
            return max(0, min(24 * 60, int(parts[0]) * 60 + int(parts[1])))
    except Exception:  # noqa: BLE001
        pass
    return fallback


class NotificationController(QtCore.QObject):
    """通知控制层：汇总官方 Windows 通知与任务栏闪烁事件，驱动紧凑横幅与未读状态。"""

    notified = QtCore.Signal(dict)            # 兼容旧订阅：{kind, name, source, time, sender, event}
    banner_changed = QtCore.Signal(object)    # NotificationEvent | None
    counts_changed = QtCore.Signal(dict)      # {source_key: int}
    status_changed = QtCore.Signal(dict)      # {permission, permission_label, listening, last_error}
    notice = QtCore.Signal(str, bool)         # (message, is_error)

    def __init__(
        self,
        win_source: WindowsNotificationSource | None = None,
        flash_source: TaskbarFlashSource | None = None,
        max_dedupe: int = MAX_DEDUPE_CACHE,
        enumerator=None,
        focuser=None,
        parent=None,
    ):
        super().__init__(parent)
        self.win_source = win_source or WindowsNotificationSource(parent=self)
        self.flash_source = flash_source or TaskbarFlashSource()
        self._max_dedupe = max(10, int(max_dedupe))
        self._enumerator = enumerator
        self._focuser = focuser
        self.mail_opener = None

        self._dedupe: OrderedDict[tuple[str, str, int], str] = OrderedDict()
        self._queue: deque[NotificationEvent] = deque()
        self.current_banner: NotificationEvent | None = None
        self.history: list[NotificationEvent] = []
        self.counts: dict[str, int] = {}
        self._latest_by_source: dict[str, NotificationEvent] = {}
        self._last_toast_ts: dict[str, float] = {}
        self._last_flash_ts: dict[str, float] = {}
        self._running = False

        self._perm_status = PermissionStatus.UNKNOWN.value
        self._listening = False
        self._last_error = ""

        self._banner_timer = QtCore.QTimer(self, singleShot=True, timeout=self.dismiss_current_banner)

        self.win_source.baseline_captured.connect(self.seed_baseline)
        self.win_source.events_sampled.connect(self._on_win_events)
        self.win_source.status_updated.connect(self._on_source_status)

    def set_focuser(self, focuser):
        """替换客户端窗口聚焦实现；生产环境使用 WindowService 的托盘恢复逻辑。"""
        self._focuser = focuser

    # ---------------- 生命周期（幂等） ----------------
    def start(self):
        if self._running:
            self.apply_config()
            return
        self._running = True
        self.apply_config()

    def stop(self):
        self._running = False
        self._banner_timer.stop()
        self.win_source.stop()

    def apply_config(self):
        """根据配置即时启停 Windows 官方通知监听（幂等）。"""
        if not self._running:
            return
        if bool(cfg.get("notify.enabled", True)):
            self.win_source.start()
        else:
            self.win_source.stop()

    def request_permission(self):
        self.win_source.request_permission()

    def open_system_settings(self) -> bool:
        return open_windows_notification_settings()

    def status_snapshot(self) -> dict:
        return {
            "permission": self._perm_status,
            "permission_label": permission_status_label(self._perm_status),
            "listening": self._listening,
            "last_error": self._last_error,
        }

    def _on_source_status(self, perm: str, listening: bool, err: str):
        self._perm_status = perm
        self._listening = bool(listening)
        self._last_error = err or ""
        self.status_changed.emit(self.status_snapshot())

    # ---------------- 配置过滤 ----------------
    @staticmethod
    def is_source_enabled(source_key: str) -> bool:
        if source_key == "wechat":
            return bool(cfg.get("notify.wechat_enabled", True))
        if source_key == "qq":
            return bool(cfg.get("notify.qq_enabled", True))
        return bool(cfg.get("notify.other_enabled", True))

    @staticmethod
    def in_dnd(now_dt: dt.datetime | None = None) -> bool:
        if not bool(cfg.get("notify.dnd_enabled", False)):
            return False
        now_dt = now_dt or dt.datetime.now()
        cur_m = now_dt.hour * 60 + now_dt.minute
        s_m = _parse_hhmm(cfg.get("notify.dnd_start", "23:00"), 23 * 60)
        e_m = _parse_hhmm(cfg.get("notify.dnd_end", "07:00"), 7 * 60)
        if s_m <= e_m:
            return s_m <= cur_m < e_m
        return cur_m >= s_m or cur_m < e_m

    # ---------------- 基线与去重 ----------------
    def _remember_dedupe(self, key: tuple[str, str, int], content_hash: str):
        if key in self._dedupe:
            self._dedupe.move_to_end(key)
        self._dedupe[key] = content_hash
        while len(self._dedupe) > self._max_dedupe:
            self._dedupe.popitem(last=False)

    def seed_baseline(self, events: list[NotificationEvent]):
        """建立基线快照：只写入去重缓存，绝不回放历史通知或增加未读。"""
        for ev in events:
            self._remember_dedupe(ev.dedupe_key(), ev.content_hash)

    def _on_win_events(self, events: list[NotificationEvent]):
        for ev in events:
            self.ingest_event(ev)

    def ingest_event(self, ev: NotificationEvent, now_dt: dt.datetime | None = None) -> str:
        """处理标准化通知事件，返回 'new' | 'updated' | 'ignored'。"""
        if not self.is_source_enabled(ev.source_key):
            return "ignored"

        dkey = ev.dedupe_key()
        if dkey in self._dedupe:
            if self._dedupe[dkey] == ev.content_hash:
                return "ignored"
            # 同一通知 ID + 同一创建时间但内容更新 -> 原地更新，不新增未读
            self._remember_dedupe(dkey, ev.content_hash)
            self._update_existing(ev)
            return "updated"

        self._remember_dedupe(dkey, ev.content_hash)
        if ev.raw_kind == "winrt_toast":
            self._last_toast_ts[ev.source_key] = time.monotonic()

        self._latest_by_source[ev.source_key] = ev
        self.history.insert(0, ev)
        del self.history[HISTORY_MAX:]

        self.counts[ev.source_key] = self.counts.get(ev.source_key, 0) + 1
        self.counts_changed.emit(dict(self.counts))
        self.notified.emit(self.to_legacy_dict(ev))

        if not self.in_dnd(now_dt):
            self._enqueue_banner(ev)
        return "new"

    def _update_existing(self, ev: NotificationEvent):
        dkey = ev.dedupe_key()
        self._latest_by_source[ev.source_key] = ev
        for idx, old in enumerate(self.history):
            if old.dedupe_key() == dkey:
                self.history[idx] = ev
                break
        if self.current_banner is not None and self.current_banner.dedupe_key() == dkey:
            self.current_banner = ev
            self.banner_changed.emit(ev)
            return
        for idx, qev in enumerate(self._queue):
            if qev.dedupe_key() == dkey:
                self._queue[idx] = ev
                break

    # ---------------- 任务栏闪烁兼容 ----------------
    def on_flash(self, hwnd: int) -> str:
        """任务栏闪烁兼容入口：与官方 Toast 统一去重，不重复计数。"""
        if not bool(cfg.get("notify.flash_compat", True)):
            return "ignored"
        ev = self.flash_source.build_event(hwnd)
        if ev is None:
            return "ignored"
        if not self.is_source_enabled(ev.source_key):
            return "ignored"

        now_m = time.monotonic()
        # 同一时间段已经收到该来源的官方 Toast 时，任务栏闪烁不再重复计数
        if now_m - self._last_toast_ts.get(ev.source_key, 0.0) < FLASH_DEDUPE_S:
            return "ignored"
        if now_m - self._last_flash_ts.get(ev.source_key, 0.0) < FLASH_DEDUPE_S:
            return "ignored"
        self._last_flash_ts[ev.source_key] = now_m
        return self.ingest_event(ev)

    # ---------------- FIFO 显示队列 ----------------
    def _enqueue_banner(self, ev: NotificationEvent):
        if self.current_banner is None:
            self._show_banner(ev)
            return
        # 队列中若已有同一来源的任务栏闪烁事件，原地更新而非堆叠重复闪烁
        if ev.raw_kind == "taskbar_flash":
            for idx, qev in enumerate(self._queue):
                if qev.source_key == ev.source_key and qev.raw_kind == "taskbar_flash":
                    self._queue[idx] = ev
                    return
        self._queue.append(ev)

    def _show_banner(self, ev: NotificationEvent):
        self.current_banner = ev
        dur_s = max(1, min(30, int(cfg.get("notify.display_seconds", 4) or 4)))
        self._banner_timer.start(dur_s * 1000)
        self.banner_changed.emit(ev)

    def dismiss_current_banner(self):
        """关闭当前横幅（或显示超时），自动弹出队列下一条；绝不清除未读计数。"""
        self._banner_timer.stop()
        if self._queue:
            nxt = self._queue.popleft()
            self._show_banner(nxt)
        else:
            if self.current_banner is not None:
                self.current_banner = None
                self.banner_changed.emit(None)

    # ---------------- 未读清除与点击打开 ----------------
    def is_source_ambiguous(self, source_key: str) -> bool:
        ev = self._latest_by_source.get(source_key)
        return bool(ev is not None and ev.account_ambiguous)

    def clear(self, source_key: str):
        """只清除指定来源的未读计数，并同步关闭该来源仍在显示的通知横幅与队列。"""
        if self._queue:
            self._queue = deque(qev for qev in self._queue if qev.source_key != source_key)
        if self.current_banner is not None and self.current_banner.source_key == source_key:
            self.dismiss_current_banner()
        if self.counts.get(source_key):
            self.counts[source_key] = 0
            self.counts_changed.emit(dict(self.counts))

    def open_event(self, ev: NotificationEvent) -> bool:
        """点击通知打开对应客户端窗口或站内邮件页；成功才按来源/账号清未读，失败则明确提示。"""
        if ev.source_key == "mail" and callable(self.mail_opener):
            ok = bool(self.mail_opener(ev))
            if ok:
                if self.current_banner is not None and self.current_banner.event_id == ev.event_id:
                    self.dismiss_current_banner()
                return True
            return False

        ok, msg = focus_event_target(ev, enumerator=self._enumerator, focuser=self._focuser)
        if ok:
            self.clear(ev.source_key)
            if self.current_banner is not None and self.current_banner.event_id == ev.event_id:
                self.dismiss_current_banner()
            return True
        if msg:
            self.notice.emit(msg, True)
        return False

    def open_current_banner(self) -> bool:
        if self.current_banner is None:
            return False
        return self.open_event(self.current_banner)

    def open(self, source_key: str) -> bool:
        """按来源打开最近通知关联的客户端，并只清除该来源未读。"""
        ev = self._latest_by_source.get(source_key)
        if ev is not None:
            return self.open_event(ev)
        synthetic = NotificationEvent(
            event_id=f"open:{source_key}",
            source_key=source_key,
            app_name={"wechat": "微信", "qq": "QQ", "mail": "邮件"}.get(source_key, source_key),
            app_identity=source_key,
        )
        ok, msg = focus_event_target(synthetic, enumerator=self._enumerator, focuser=self._focuser)
        if ok:
            self.clear(source_key)
            return True
        if msg:
            self.notice.emit(msg, True)
        return False

    # ---------------- 隐私格式化与兼容输出 ----------------
    @staticmethod
    def format_banner_sub(ev: NotificationEvent) -> str:
        """默认隐藏正文；仅在开启摘要预览时展示脱敏摘要。邮件通知默认仅显示发件人/标题。"""
        if ev.source_key == "mail":
            sender = mask_sensitive_text(ev.sender, max_len=18) if ev.sender else ""
            subj = mask_sensitive_text(ev.title, max_len=28) if ev.title else ""
            if sender and subj:
                return f"{sender} · {subj}"
            return sender or subj or "有新邮件"
        if ev.raw_kind == "taskbar_flash":
            return "有新消息"
        preview_on = bool(cfg.get("notify.preview_enabled", False))
        if preview_on and ev.body:
            return mask_sensitive_text(ev.body)
        if ev.sender and ev.sender != ev.app_name:
            return mask_sensitive_text(ev.sender, max_len=28)
        if ev.title and ev.title != ev.app_name:
            return mask_sensitive_text(ev.title, max_len=28)
        return "有新消息"

    @classmethod
    def to_legacy_dict(cls, ev: NotificationEvent) -> dict:
        return {
            "kind": ev.source_key,
            "name": ev.app_name,
            "source": "flash" if ev.raw_kind == "taskbar_flash" else "toast",
            "time": ev.updated_at or ev.created_at or time.time(),
            "sender": cls.format_banner_sub(ev),
            "event": ev,
        }
