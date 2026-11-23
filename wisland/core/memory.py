# -*- coding: utf-8 -*-
"""进程内存辅助工具：空闲工作集整理。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import sys

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetCurrentProcess.argtypes = []
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    _psapi.EmptyWorkingSet.restype = wintypes.BOOL
else:
    _k32 = None
    _psapi = None


def trim_working_set(collect_gc: bool = False) -> None:
    """回收 Python 循环引用并将长期闲置冷页交还 Windows 系统工作集。"""
    if collect_gc:
        gc.collect()
    if _k32 is None or _psapi is None:
        return
    try:
        handle = _k32.GetCurrentProcess()
        _psapi.EmptyWorkingSet(handle)
    except Exception:
        pass
