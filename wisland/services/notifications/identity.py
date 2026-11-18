# -*- coding: utf-8 -*-
"""通知来源识别与账号/进程窗口精确绑定（支持 PID 复用检测与多实例防误触）。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

from .models import NotificationEvent

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
]
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
SW_SHOW = 5

# 可信 AppUserModelId 前缀（带边界校验，避免把无关扩展误判成微信或 QQ）
_TRUSTED_AUMID_RULES: list[tuple[str, str, str]] = [
    ("tencent.weixin", "wechat", "微信"),
    ("tencent.wechat", "wechat", "微信"),
    ("com.tencent.xinwechat", "wechat", "微信"),
    ("weixin", "wechat", "微信"),
    ("wechat", "wechat", "微信"),
    ("tencent.qqnt", "qq", "QQ"),
    ("tencent.qq", "qq", "QQ"),
    ("com.tencent.qq", "qq", "QQ"),
    ("qq", "qq", "QQ"),
]

_SOURCE_EXES: dict[str, tuple[str, ...]] = {
    "wechat": ("weixin.exe", "wechat.exe"),
    "qq": ("qq.exe",),
}

_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "wechat": "微信",
    "qq": "QQ",
    "mail": "邮件",
    "other": "系统通知",
}


def _prefix_match_boundary(value: str, prefix: str) -> bool:
    """前缀匹配且要求后续为空或非字母数字分隔符。"""
    if not value.startswith(prefix):
        return False
    rest = value[len(prefix):]
    return not rest or not rest[0].isalnum()


def classify_source(
    app_identity: str = "",
    app_name: str = "",
    exe_name: str = "",
) -> tuple[str, str]:
    """根据可信 AppUserModelId / 进程名 / 精确显示名识别通知来源。
    返回 (source_key, canonical_app_name)。绝不凭正文或标题里的模糊关键字误判。
    """
    aumid = (app_identity or "").strip().lower()
    if aumid:
        for prefix, src_key, cname in _TRUSTED_AUMID_RULES:
            if _prefix_match_boundary(aumid, prefix):
                return src_key, cname

    exe = os.path.basename((exe_name or "").strip()).lower()
    if exe:
        for src_key, exes in _SOURCE_EXES.items():
            if exe in exes:
                return src_key, _SOURCE_DISPLAY_NAMES[src_key]

    # 仅在缺少 AUMID 时才用精确应用显示名降级匹配
    if not aumid:
        disp = (app_name or "").strip().lower()
        if disp in ("微信", "wechat", "weixin"):
            return "wechat", "微信"
        if disp in ("qq", "腾讯qq"):
            return "qq", "QQ"

    clean_name = (app_name or "").strip() or (app_identity or "").strip() or "系统通知"
    return "other", clean_name


def _filetime_to_float(ft: wintypes.FILETIME) -> float:
    val = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return val / 1e7


def get_process_info(pid: int) -> tuple[str, float | None]:
    """查询指定 PID 的 (小写 exe 名, 进程启动时间戳)；失败返回 ('', None)。"""
    if not pid or pid <= 0:
        return "", None
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return "", None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        sz = wintypes.DWORD(1024)
        exe = ""
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
            exe = os.path.basename(buf.value).lower()
        c_t = wintypes.FILETIME()
        e_t = wintypes.FILETIME()
        k_t = wintypes.FILETIME()
        u_t = wintypes.FILETIME()
        started: float | None = None
        if _kernel32.GetProcessTimes(h, ctypes.byref(c_t), ctypes.byref(e_t), ctypes.byref(k_t), ctypes.byref(u_t)):
            started = _filetime_to_float(c_t)
        return exe, started
    finally:
        _kernel32.CloseHandle(h)


def exe_of_hwnd(hwnd: int) -> str:
    """窗口句柄 -> 小写 exe 文件名。"""
    if not hwnd:
        return ""
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    if not pid.value:
        return ""
    exe, _ = get_process_info(pid.value)
    return exe


@dataclass
class WindowCandidate:
    hwnd: int
    pid: int
    exe: str
    started_at: float | None
    visible: bool
    title: str = ""


def enumerate_client_windows(source_key: str) -> list[WindowCandidate]:
    """枚举指定来源（wechat/qq）当前运行中的主窗口候选列表。"""
    target_exes = _SOURCE_EXES.get(source_key)
    if not target_exes:
        return []

    found: list[WindowCandidate] = []
    proc_cache: dict[int, tuple[str, float | None]] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        p = int(pid.value)
        if p <= 0:
            return True
        if p not in proc_cache:
            proc_cache[p] = get_process_info(p)
        exe, started = proc_cache[p]
        if exe not in target_exes:
            return True
        vis = bool(_user32.IsWindowVisible(hwnd))
        n_len = _user32.GetWindowTextLengthW(hwnd)
        title = ""
        if n_len > 0:
            buf = ctypes.create_unicode_buffer(min(n_len + 1, 256))
            _user32.GetWindowTextW(hwnd, buf, len(buf))
            title = buf.value
        # 只收集可见主窗口或有标题的顶层窗口（兼容托盘隐藏窗）
        if vis or title:
            found.append(WindowCandidate(hwnd=int(hwnd), pid=p, exe=exe, started_at=started, visible=vis, title=title))
        return True

    _user32.EnumWindows(_cb, 0)
    return found


def default_focus_hwnd(hwnd: int) -> bool:
    """恢复并前置聚焦指定窗口句柄。"""
    if not hwnd or not _user32.IsWindow(wintypes.HWND(int(hwnd))):
        return False
    hw = wintypes.HWND(int(hwnd))
    if _user32.IsIconic(hw):
        _user32.ShowWindow(hw, SW_RESTORE)
    else:
        _user32.ShowWindow(hw, SW_SHOW)
    return bool(_user32.SetForegroundWindow(hw))


@dataclass
class ClientBinding:
    pid: int | None = None
    window_handle: int | None = None
    process_started_at: float | None = None
    account_key: str = ""
    can_open: bool = False
    account_ambiguous: bool = False


def _same_started_at(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) < 0.05


def bind_client_target(
    source_key: str,
    pid: int | None = None,
    window_handle: int | None = None,
    process_started_at: float | None = None,
    enumerator=None,
) -> ClientBinding:
    """为通知事件绑定目标客户端的 PID、窗口句柄与进程启动时间。
    多实例且无法唯一确认账号时，标记 account_ambiguous=True，绝不猜测绑定。
    """
    if source_key not in _SOURCE_EXES:
        return ClientBinding(can_open=False)

    enum_fn = enumerator or enumerate_client_windows
    candidates: list[WindowCandidate] = enum_fn(source_key)

    if pid is not None:
        matched = [c for c in candidates if c.pid == pid]
        if process_started_at is not None:
            matched = [c for c in matched if _same_started_at(c.started_at, process_started_at)]
        if not matched:
            return ClientBinding(pid=pid, window_handle=window_handle, process_started_at=process_started_at, can_open=False)
        best = next((c for c in matched if c.hwnd == window_handle), None) or next((c for c in matched if c.visible), matched[0])
        return ClientBinding(
            pid=best.pid,
            window_handle=best.hwnd,
            process_started_at=best.started_at,
            account_key=f"{source_key}:{best.pid}",
            can_open=True,
            account_ambiguous=False,
        )

    distinct_pids = sorted({c.pid for c in candidates})
    if not distinct_pids:
        return ClientBinding(can_open=False, account_ambiguous=False)
    if len(distinct_pids) > 1:
        # 多个客户端实例同时运行且事件未携带 PID：禁止猜测绑定到任意账号
        return ClientBinding(can_open=False, account_ambiguous=True)

    only_pid = distinct_pids[0]
    matched = [c for c in candidates if c.pid == only_pid]
    best = next((c for c in matched if c.visible), matched[0])
    return ClientBinding(
        pid=best.pid,
        window_handle=best.hwnd,
        process_started_at=best.started_at,
        account_key=f"{source_key}:{best.pid}",
        can_open=True,
        account_ambiguous=False,
    )


def focus_event_target(
    event: NotificationEvent,
    enumerator=None,
    focuser=None,
) -> tuple[bool, str]:
    """点击通知时安全聚焦目标窗口。
    返回 (成功聚焦, 提示信息)。无法确认账号或客户端已退出时绝不乱开无关窗口。
    """
    if event.account_ambiguous:
        return False, "客户端已打开但无法识别账号"

    if event.source_key not in _SOURCE_EXES:
        return False, f"暂不支持直接打开 {event.app_name}"

    enum_fn = enumerator or enumerate_client_windows
    focus_fn = focuser or default_focus_hwnd
    candidates: list[WindowCandidate] = enum_fn(event.source_key)

    if not candidates:
        return False, f"{event.app_name} 客户端已退出"

    # 1) 优先校验已绑定的窗口句柄 + PID + 启动时间（防止 PID 复用与句柄失效）
    if event.window_handle:
        for c in candidates:
            if c.hwnd == event.window_handle:
                if event.pid is not None and c.pid != event.pid:
                    continue
                if event.process_started_at is not None and not _same_started_at(c.started_at, event.process_started_at):
                    continue
                if focus_fn(c.hwnd):
                    return True, ""

    # 2) 窗口句柄失效时，仅在同一 PID + 同一启动时间内查找唯一可见窗口
    if event.pid is not None:
        same_proc = [
            c for c in candidates
            if c.pid == event.pid
            and (event.process_started_at is None or _same_started_at(c.started_at, event.process_started_at))
        ]
        if len(same_proc) == 1 and focus_fn(same_proc[0].hwnd):
            return True, ""
        vis_same = [c for c in same_proc if c.visible]
        if len(vis_same) == 1 and focus_fn(vis_same[0].hwnd):
            return True, ""
        # 原进程已结束或 PID 被复用，绝不切换到另一个账号的进程
        return False, "无法确认账号，原客户端窗口已失效"

    # 3) 未绑定 PID 时，仅在唯一客户端实例时允许聚焦
    distinct_pids = {c.pid for c in candidates}
    if len(distinct_pids) > 1:
        return False, "客户端已打开但无法识别账号"
    best = next((c for c in candidates if c.visible), candidates[0])
    if focus_fn(best.hwnd):
        return True, ""
    return False, f"无法恢复 {event.app_name} 窗口"
