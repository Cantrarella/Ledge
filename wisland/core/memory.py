# -*- coding: utf-8 -*-
"""进程内存辅助工具：空闲工作集整理。"""
from __future__ import annotations

import ctypes
import gc
import sys


def trim_working_set(collect_gc: bool = False) -> None:
    """回收 Python 循环引用并将长期闲置冷页交还 Windows 系统工作集。"""
    if collect_gc:
        gc.collect()
    if sys.platform != "win32":
        return
    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.EmptyWorkingSet(handle)
    except Exception:
        pass
