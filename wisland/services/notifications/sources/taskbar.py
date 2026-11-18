# -*- coding: utf-8 -*-
"""任务栏闪烁兼容采集源（HSHELL_FLASH）：仅产生“已知客户端有活动”事件，不伪装读取正文。"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from ....core.config import cfg
from ..identity import classify_source, get_process_info
from ..models import NotificationEvent, compute_content_hash

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD


def _default_hwnd_proc_info(hwnd: int) -> tuple[int | None, str, float | None]:
    if not hwnd:
        return None, "", None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    p = int(pid.value)
    if p <= 0:
        return None, "", None
    exe, started = get_process_info(p)
    return p, exe, started


class TaskbarFlashSource:
    """将 Windows 任务栏闪烁事件转换为标准化 NotificationEvent。"""

    def __init__(self, proc_resolver=None):
        self._proc_resolver = proc_resolver or _default_hwnd_proc_info

    def build_event(self, hwnd: int) -> NotificationEvent | None:
        """根据闪烁窗口句柄构建事件；未知进程直接忽略。"""
        try:
            hwnd_i = int(hwnd)
        except (TypeError, ValueError):
            return None
        if hwnd_i <= 0:
            return None

        pid, exe, started_at = self._proc_resolver(hwnd_i)
        if not exe:
            return None

        flash_map = cfg.get("notify.flash_processes") or {}
        meta = flash_map.get(exe)
        if not meta:
            return None

        source_key, app_name = classify_source(exe_name=exe)
        if source_key == "other":
            source_key = str(meta.get("kind") or "other")
            app_name = str(meta.get("name") or app_name)

        now = time.time()
        bucket = int(now // 2)  # 2 秒时间桶，配合控制层闪烁抑制窗
        app_id = f"taskbar:{exe}"
        return NotificationEvent(
            event_id=f"flash:{source_key}:{pid or 0}:{bucket}",
            source_key=source_key,
            app_name=app_name,
            app_identity=app_id,
            account_key=f"{source_key}:{pid}" if pid else "",
            pid=pid,
            window_handle=hwnd_i,
            process_started_at=started_at,
            title=app_name,
            sender="",
            body="",  # 任务栏闪烁无法读取正文，严格留空
            created_at=now,
            updated_at=now,
            content_hash=compute_content_hash(app_name, "", f"flash:{pid}"),
            can_open=bool(pid),
            raw_kind="taskbar_flash",
            notification_id=f"flash:{pid or hwnd_i}",
            creation_time=bucket,
        )
