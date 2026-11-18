# -*- coding: utf-8 -*-
"""QQ / 微信前台窗口监听：用户直接打开或切换至客户端主窗口时自动清除对应来源未读。"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from pathlib import Path

from PySide6 import QtCore

from ...core.config import cfg
from .identity import enumerate_client_windows, get_process_info

log = logging.getLogger("wisland.notify.foreground")

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

_EXE_TO_SOURCE = {
    "weixin.exe": "wechat",
    "wechat.exe": "wechat",
    "qq.exe": "qq",
    "qqnt.exe": "qq",
}


def _build_exe_map() -> dict[str, str]:
    mapping = dict(_EXE_TO_SOURCE)
    for src in ("qq", "wechat"):
        custom = str(cfg.get(f"notify.open.{src}", "") or "").strip()
        if custom:
            exe_name = Path(custom).name.lower()
            if exe_name.endswith(".exe"):
                mapping[exe_name] = src
    return mapping

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _WIN_EVENT_PROC = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _user32.SetWinEventHook.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HMODULE,
        _WIN_EVENT_PROC,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _user32.SetWinEventHook.restype = wintypes.HANDLE
    _user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    _user32.UnhookWinEvent.restype = wintypes.BOOL
else:
    _user32 = None
    _WIN_EVENT_PROC = None


def _default_foreground_hwnd() -> int:
    if _user32 is None:
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def _default_is_active_window(hwnd: int) -> bool:
    if _user32 is None or not hwnd:
        return False
    hw = wintypes.HWND(int(hwnd))
    if not _user32.IsWindow(hw):
        return False
    if not _user32.IsWindowVisible(hw):
        return False
    if _user32.IsIconic(hw):
        return False
    # 过滤工具窗口（WS_EX_TOOLWINDOW）
    if int(_user32.GetWindowLongW(hw, -20) or 0) & 0x00000080:
        return False
    return True


def _default_pid_of_hwnd(hwnd: int) -> int:
    if _user32 is None or not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value or 0)


class ForegroundClientWatcher(QtCore.QObject):
    """按需监听 QQ/微信前台窗口：仅在存在未读时启用，未读清零后立即停止。"""

    foreground_matched = QtCore.Signal(str)  # source_key

    def __init__(
        self,
        parent=None,
        poll_interval_ms: int = 500,
        foreground_hwnd_fn=None,
        is_active_fn=None,
        pid_of_hwnd_fn=None,
        process_info_fn=None,
        enumerator=None,
        ambiguous_fn=None,
        use_win_hook: bool = True,
    ):
        super().__init__(parent)
        self._foreground_hwnd_fn = foreground_hwnd_fn or _default_foreground_hwnd
        self._is_active_fn = is_active_fn or _default_is_active_window
        self._pid_of_hwnd_fn = pid_of_hwnd_fn or _default_pid_of_hwnd
        self._process_info_fn = process_info_fn or get_process_info
        self._enumerator = enumerator or enumerate_client_windows
        self._ambiguous_fn = ambiguous_fn

        self._active_unread: dict[str, int] = {}
        self._running: bool = False
        self._hook_handle = None
        self._hook_cb = None
        self._use_win_hook = bool(use_win_hook and sys.platform == "win32" and foreground_hwnd_fn is None)

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(max(200, int(poll_interval_ms or 500)))
        self._poll_timer.timeout.connect(self.check_foreground_once)

    @property
    def is_watching(self) -> bool:
        return self._running

    def update_unread_counts(self, counts: dict[str, int] | None):
        """根据当前 QQ/微信未读数按需启停监听。"""
        c = counts or {}
        tracked = {k: int(c.get(k, 0) or 0) for k in ("qq", "wechat") if int(c.get(k, 0) or 0) > 0}
        self._active_unread = tracked
        if tracked:
            self._start_watching()
            self.check_foreground_once()
        else:
            self.stop()

    def _start_watching(self):
        if self._running:
            return
        self._running = True
        hook_ok = False
        if self._use_win_hook and _user32 is not None and _WIN_EVENT_PROC is not None:
            try:
                def _on_win_event(_hook, _event, hwnd, _id_obj, _id_child, _thread, _time):
                    if self._running and hwnd:
                        QtCore.QMetaObject.invokeMethod(
                            self,
                            "_on_foreground_hwnd",
                            QtCore.Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(int, int(hwnd)),
                        )

                self._hook_cb = _WIN_EVENT_PROC(_on_win_event)
                self._hook_handle = _user32.SetWinEventHook(
                    EVENT_SYSTEM_FOREGROUND,
                    EVENT_SYSTEM_FOREGROUND,
                    0,
                    self._hook_cb,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
                hook_ok = bool(self._hook_handle)
            except Exception as exc:
                log.debug("install SetWinEventHook failed: %s", exc)
                self._hook_handle = None
                self._hook_cb = None
        # 若未启用 WinEventHook 或安装失败，使用低频 500ms 兼容轮询；仅在有未读时运行
        if not hook_ok and not self._poll_timer.isActive():
            self._poll_timer.start()

    def stop(self):
        """停止定时器并释放 WinEventHook 句柄。"""
        self._running = False
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._hook_handle and _user32 is not None:
            try:
                _user32.UnhookWinEvent(self._hook_handle)
            except Exception:
                pass
        self._hook_handle = None
        self._hook_cb = None

    @QtCore.Slot(int)
    def _on_foreground_hwnd(self, hwnd: int):
        if self._running:
            self._evaluate_hwnd(hwnd)

    @QtCore.Slot()
    def check_foreground_once(self):
        """主动检测一次当前前台窗口（也可用于测试）。"""
        if not self._active_unread:
            return
        hwnd = int(self._foreground_hwnd_fn() or 0)
        if hwnd:
            self._evaluate_hwnd(hwnd)

    def _evaluate_hwnd(self, hwnd: int):
        if not self._active_unread:
            return
        if not self._is_active_fn(hwnd):
            return
        pid = int(self._pid_of_hwnd_fn(hwnd) or 0)
        if pid <= 0:
            return
        exe, _started = self._process_info_fn(pid)
        source_key = _build_exe_map().get(str(exe or "").lower(), "")
        if not source_key or self._active_unread.get(source_key, 0) <= 0:
            return

        # 实时检查当前主窗口候选：多实例时绝不猜测清除（不复用旧歧义状态）
        candidates = self._enumerator(source_key) or []
        distinct_pids = {c.pid for c in candidates if getattr(c, "pid", 0)}
        if len(distinct_pids) > 1:
            log.info("skip auto-clear for %s: multiple instances (%d)", source_key, len(distinct_pids))
            return

        self.foreground_matched.emit(source_key)
