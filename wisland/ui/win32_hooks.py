# -*- coding: utf-8 -*-
"""Win32 热键解析与穿透模式低级鼠标钩子（由 island.py 拆出，保持原有逻辑与坐标换算）。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui

MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000


def parse_hotkey(s: str) -> tuple[int, int] | None:
    """将类似 'Ctrl+Alt+I' 的热键配置解析为 RegisterHotKey 所需的 (modifiers, vk)。"""
    parts = [x.strip().lower() for x in (s or "").split("+") if x.strip()]
    if not parts:
        return None
    mods = 0
    vk = 0
    for x in parts:
        if x in MOD:
            mods |= MOD[x]
        elif len(x) == 1 and x.isalnum():
            vk = ord(x.upper())
        elif x.startswith("f") and x[1:].isdigit():
            vk = 0x70 + int(x[1:]) - 1
        elif x == "space":
            vk = 0x20
    return (mods | MOD_NOREPEAT, vk) if vk else None


class RightClickHook:
    """穿透模式下窗口对鼠标完全透明；用低级鼠标钩子只截获落在灵动岛上的右键。
    只在穿透模式开启时安装，回调对非右键消息立即放行。"""

    WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205

    def __init__(self, island):
        self.island = island
        self.handle = None
        self._proc = None

    def install(self):
        if self.handle:
            return
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        self._u = u
        isl = self.island

        def proc(code, wparam, lparam):
            try:
                if code == 0 and wparam == 0x020A:        # WM_MOUSEWHEEL
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    pos = QtGui.QCursor.pos()
                    if isl.isVisible() and not isl.expanded and isl._inside(pos):
                        delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                        QtCore.QTimer.singleShot(0, lambda d=delta: isl.wheel_compact.emit(1 if d > 0 else -1))
                        return 1
                if code == 0 and wparam in (self.WM_RBUTTONDOWN, self.WM_RBUTTONUP):
                    pos = QtGui.QCursor.pos()
                    if isl.isVisible() and isl._inside(pos):
                        if wparam == self.WM_RBUTTONUP:
                            p_copy = QtCore.QPoint(pos)
                            QtCore.QTimer.singleShot(0, lambda p=p_copy: isl.menu_requested.emit(p))
                        return 1          # 吞掉右键，不传给下面的窗口
            except Exception:  # noqa: BLE001
                pass
            return u.CallNextHookEx(self.handle, code, wparam, lparam)

        self._proc = HOOKPROC(proc)
        self.handle = u.SetWindowsHookExW(14, self._proc, k.GetModuleHandleW(None), 0)

    def remove(self):
        if self.handle:
            self._u.UnhookWindowsHookEx(self.handle)
        self.handle = None
        self._proc = None


_RightClickHook = RightClickHook

__all__ = ["MOD", "MOD_NOREPEAT", "RightClickHook", "_RightClickHook", "parse_hotkey"]
