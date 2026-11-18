# -*- coding: utf-8 -*-
"""通知服务兼容入口：内部委托给统一的 NotificationController（Windows 官方通知 + 任务栏兼容）。"""
from __future__ import annotations

import configparser
import os
import re
import time
from pathlib import Path

from PySide6 import QtCore

from .notifications.controller import NotificationController
from .notifications.identity import exe_of_hwnd
from .notifications.models import NotificationEvent, compute_content_hash

_FILETIME_EPOCH_DIFF = 116444736000000000  # 1601 -> 1970，单位 100ns
_TEXT_RE = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S | re.I)


def filetime_to_epoch(ft) -> float:
    try:
        ft = int(ft)
    except (TypeError, ValueError):
        return time.time()
    if ft <= 0:
        return time.time()
    return (ft - _FILETIME_EPOCH_DIFF) / 1e7


def toast_first_text(payload) -> str:
    """toast XML 的第一个 <text>（通常是发送者 / 群名）。解析失败返回空串。"""
    try:
        s = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload or "")
        m = _TEXT_RE.search(s)
        if not m:
            return ""
        t = re.sub(r"<[^>]+>", "", m.group(1))
        t = (t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&")).strip()
        return t[:40]
    except Exception:  # noqa: BLE001
        return ""


def thunderbird_profile() -> Path | None:
    """兼容入口：查找 Thunderbird 默认配置目录（供 mailcheck 等模块使用）。"""
    root = Path(os.path.expandvars(r"%APPDATA%\Thunderbird"))
    ini = root / "profiles.ini"
    if not ini.exists():
        return None
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(ini, encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    cand: list[tuple[str, bool]] = []
    for sec in cp.sections():
        if sec.lower().startswith("install") and cp[sec].get("default"):
            cand.append((cp[sec]["default"], True))
    for sec in cp.sections():
        s = cp[sec]
        if sec.lower().startswith("profile") and s.get("path") and s.get("default") == "1":
            cand.append((s["path"], s.get("isrelative", "1") == "1"))
    for sec in cp.sections():
        s = cp[sec]
        if sec.lower().startswith("profile") and s.get("path"):
            cand.append((s["path"], s.get("isrelative", "1") == "1"))
    for path, rel in cand:
        p = (root / path) if rel else Path(path)
        if (p / "ImapMail").is_dir() or (p / "Mail").is_dir():
            return p
    return None


def inbox_files(profile: Path) -> list[Path]:
    """兼容入口：列举 Thunderbird 下的 INBOX 文件。"""
    out = []
    for sub in ("ImapMail", "Mail"):
        base = profile / sub
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if f in ("INBOX", "Inbox"):
                    out.append(Path(dirpath) / f)
    return out


class NotifyService(QtCore.QObject):
    """兼容旧接口的通知服务包装器，内部使用 NotificationController。"""

    notified = QtCore.Signal(dict)
    banner_changed = QtCore.Signal(object)
    counts_changed = QtCore.Signal(dict)
    status_changed = QtCore.Signal(dict)
    notice = QtCore.Signal(str, bool)

    def __init__(self, controller: NotificationController | None = None, parent=None):
        super().__init__(parent)
        self.controller = controller or NotificationController(parent=self)
        self.controller.notified.connect(self.notified)
        self.controller.banner_changed.connect(self.banner_changed)
        self.controller.counts_changed.connect(self.counts_changed)
        self.controller.status_changed.connect(self.status_changed)
        self.controller.notice.connect(self.notice)

    @property
    def counts(self) -> dict[str, int]:
        return self.controller.counts

    @property
    def history(self) -> list[dict]:
        return [NotificationController.to_legacy_dict(ev) for ev in self.controller.history]

    @property
    def current_banner(self) -> NotificationEvent | None:
        return self.controller.current_banner

    def start(self):
        self.controller.start()

    def stop(self):
        self.controller.stop()

    def apply_config(self):
        self.controller.apply_config()

    def request_permission(self):
        self.controller.request_permission()

    def open_system_settings(self) -> bool:
        return self.controller.open_system_settings()

    def status_snapshot(self) -> dict:
        return self.controller.status_snapshot()

    def clear(self, kind: str):
        self.controller.clear(kind)

    def open(self, kind: str) -> bool:
        return self.controller.open(kind)

    def open_current_banner(self) -> bool:
        return self.controller.open_current_banner()

    def dismiss_current_banner(self):
        self.controller.dismiss_current_banner()

    def on_flash(self, hwnd: int):
        self.controller.on_flash(hwnd)

    def _emit(self, d: dict):
        """兼容外部（如邮件检查等）按旧字典格式投递事件。"""
        kind = str(d.get("kind") or "other")
        name = str(d.get("name") or kind)
        sender = str(d.get("sender") or "")
        now = float(d.get("time") or time.time())
        ev = NotificationEvent(
            event_id=f"legacy:{kind}:{int(now * 1000)}",
            source_key=kind,
            app_name=name,
            app_identity=kind,
            title=name,
            sender=sender,
            body="",
            created_at=now,
            updated_at=now,
            content_hash=compute_content_hash(name, sender, str(int(now * 1000))),
            can_open=(kind in ("wechat", "qq")),
            raw_kind=str(d.get("source") or "legacy"),
            notification_id=f"legacy:{int(now * 1000)}",
            creation_time=int(now * 1e7),
        )
        self.controller.ingest_event(ev)


__all__ = [
    "NotifyService",
    "NotificationController",
    "exe_of_hwnd",
    "toast_first_text",
    "filetime_to_epoch",
    "thunderbird_profile",
    "inbox_files",
]
