# -*- coding: utf-8 -*-
"""系统服务：窗口查找与激活、全屏检测。"""
from __future__ import annotations

import ctypes
import os
import traceback
from ctypes import wintypes as W

from PySide6 import QtCore

from wisland.core.bg import launch

# ======================================================================
# 窗口
# ======================================================================
_u32 = ctypes.WinDLL("user32")
_k32 = ctypes.windll.kernel32
_dwm = ctypes.windll.dwmapi

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_MAXIMIZE, WS_CAPTION = 0x01000000, 0x00C00000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
GW_OWNER = 4
DWMWA_CLOAKED = 14
SW_RESTORE, SW_SHOW = 9, 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
KEYEVENTF_KEYUP = 0x0002
_VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}


def send_hotkey(spec: str) -> bool:
    """模拟按下组合键，如 "Ctrl+Alt+W"。"""
    keys = []
    for part in (spec or "").split("+"):
        k = part.strip().lower()
        if k in _VK:
            keys.append(_VK[k])
        elif len(k) == 1 and k.isalnum():
            keys.append(ord(k.upper()))
        elif k.startswith("f") and k[1:].isdigit():
            keys.append(0x70 + int(k[1:]) - 1)
    if not keys:
        return False
    try:
        for vk in keys:
            _u32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(keys):
            _u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


MONITOR_DEFAULTTONEAREST = 2

_WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
_u32.EnumWindows.argtypes = [_WNDENUMPROC, W.LPARAM]
_u32.EnumChildWindows.argtypes = [W.HWND, _WNDENUMPROC, W.LPARAM]
_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.GetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int]
_u32.GetWindow.restype = W.HWND
_u32.GetWindow.argtypes = [W.HWND, W.UINT]
_u32.GetWindowTextLengthW.argtypes = [W.HWND]
_u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
_u32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
_u32.GetWindowThreadProcessId.restype = W.DWORD
_u32.IsWindowVisible.argtypes = [W.HWND]
_u32.IsWindow.argtypes = [W.HWND]
_u32.IsIconic.argtypes = [W.HWND]
_u32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
_u32.SetForegroundWindow.argtypes = [W.HWND]
_u32.BringWindowToTop.argtypes = [W.HWND]
_u32.GetForegroundWindow.restype = W.HWND
_u32.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
_u32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
_u32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
_u32.MonitorFromWindow.restype = W.HMONITOR
_u32.GetShellWindow.restype = W.HWND
_u32.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.c_size_t]
_dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
_k32.OpenProcess.restype = W.HANDLE
_k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
_k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
_k32.CloseHandle.argtypes = [W.HANDLE]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]


_u32.GetMonitorInfoW.argtypes = [W.HMONITOR, ctypes.POINTER(_MONITORINFO)]

_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _title(hwnd) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = W.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_path(pid: int) -> str:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = W.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(h)


def _cloaked(hwnd) -> bool:
    v = W.DWORD()
    if _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) == 0:
        return v.value != 0
    return False


class WindowService(QtCore.QObject):
    notice = QtCore.Signal(str)
    """窗口查找、激活与全屏检测。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._own_pid = os.getpid()
        self._exe_cache: dict[int, str] = {}

    def start(self):
        pass

    def stop(self):
        pass

    def _is_alt_tab(self, hwnd) -> bool:
        if not _u32.IsWindowVisible(hwnd):
            return False
        ex = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
            return False
        if ex & WS_EX_NOACTIVATE and not ex & WS_EX_APPWINDOW:
            return False
        if _u32.GetWindow(hwnd, GW_OWNER) and not ex & WS_EX_APPWINDOW:
            return False
        if _cloaked(hwnd):
            return False
        return True

    def _real_pid(self, hwnd, pid: int, exe: str) -> int:
        """UWP 窗口属于 ApplicationFrameHost.exe，找其子 CoreWindow 的真实进程。"""
        if exe.lower() != "applicationframehost.exe":
            return pid
        found = []

        def cb(h, lp):
            p = _pid(h)
            if p != pid:
                found.append(p)
                return False
            return True
        _u32.EnumChildWindows(hwnd, _WNDENUMPROC(cb), 0)
        return found[0] if found else pid

    def _exe_for(self, pid: int) -> str:
        p = self._exe_cache.get(pid)
        if p is None:
            p = _exe_path(pid)
            if len(self._exe_cache) > 512:
                self._exe_cache.clear()
            self._exe_cache[pid] = p
        return p

    # ---------------- 操作 ----------------
    def activate(self, hwnd: int) -> bool:
        try:
            hwnd = int(hwnd)
            if not _u32.IsWindow(hwnd):
                return False
            if not _u32.IsWindowVisible(hwnd):
                return self._restore_hidden_main_window(hwnd)
            if _u32.IsIconic(hwnd):
                _u32.ShowWindow(hwnd, SW_RESTORE)
            ok = bool(_u32.SetForegroundWindow(hwnd))
            if not ok or _u32.GetForegroundWindow() != hwnd:
                fg = _u32.GetForegroundWindow()
                cur_tid = _k32.GetCurrentThreadId()
                fg_tid = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
                tgt_tid = _u32.GetWindowThreadProcessId(hwnd, None)
                attached = []
                for t in {fg_tid, tgt_tid}:
                    if t and t != cur_tid and _u32.AttachThreadInput(cur_tid, t, True):
                        attached.append(t)
                try:
                    _u32.BringWindowToTop(hwnd)
                    ok = bool(_u32.SetForegroundWindow(hwnd))
                finally:
                    for t in attached:
                        _u32.AttachThreadInput(cur_tid, t, False)
            _u32.BringWindowToTop(hwnd)
            return ok or _u32.GetForegroundWindow() == hwnd
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return False

    def _restore_hidden_main_window(self, hwnd: int) -> bool:
        """通过客户端“显示窗口”热键恢复托盘隐藏窗口，禁止直接 SW_SHOW。"""
        try:
            exe = os.path.basename(self._exe_for(_pid(hwnd))).lower()
            if not exe:
                return False
            from wisland.core.config import cfg
            hk = str((cfg.get("apps.restore_hotkey") or {}).get(exe, "") or "").strip()
            if not hk or not self._process_running(exe):
                return False
            if not send_hotkey(hk):
                return False
            QtCore.QTimer.singleShot(600, lambda e=exe, k=hk: self._focus_later(e, k))
            return True
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return False

    def find_app_window(self, exe: str) -> int | None:
        """找到某个进程（按 exe 文件名）的顶层窗口，按 Z 序返回最上面的那个。"""
        exe = os.path.basename(exe or "").lower()
        if not exe:
            return None
        hwnds: list[int] = []

        def cb(h, lp):
            hwnds.append(h)
            return True
        try:
            _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        except Exception:  # noqa: BLE001
            return None
        for h in hwnds:
            try:
                if not self._is_alt_tab(h) or not _title(h).strip():
                    continue
                pid = _pid(h)
                path = self._exe_for(pid)
                name = os.path.basename(path)
                rpid = self._real_pid(h, pid, name)
                if rpid != pid:
                    name = os.path.basename(self._exe_for(rpid) or path)
                if name.lower() == exe:
                    return int(h)
            except Exception:  # noqa: BLE001
                continue
        return None

    def find_hidden_main_window(self, exe: str, titles: list[str]) -> int | None:
        """托盘里的应用：主窗口只是被隐藏了。按标题在该进程的顶层窗口里找（包括不可见的）。"""
        exe = os.path.basename(exe or "").lower()
        want = [t.lower() for t in titles or []]
        if not exe or not want:
            return None
        hwnds: list[int] = []

        def cb(h, lp):
            hwnds.append(h)
            return True
        try:
            _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        except Exception:  # noqa: BLE001
            return None
        best = None
        for h in hwnds:
            try:
                if _u32.GetWindow(h, 4):              # 有 owner 的不是主窗口
                    continue
                title = _title(h).strip().lower()
                if title not in want:
                    continue
                name = os.path.basename(self._exe_for(_pid(h))).lower()
                if name != exe:
                    continue
                r = W.RECT()
                _u32.GetWindowRect(h, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
                if area < 200 * 200:
                    continue
                if best is None or area > best[1]:
                    best = (int(h), area)
            except Exception:  # noqa: BLE001
                continue
        return best[0] if best else None

    def _process_running(self, exe: str) -> bool:
        import psutil
        exe = exe.lower()
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() == exe:
                return True
        return False

    def _focus_later(self, exe: str, hk: str = ""):
        h = self.find_app_window(exe)
        if h:
            self.activate(h)
        else:
            self.notice.emit(f"没能唤出窗口：请检查该应用的「显示窗口」快捷键是否为 {hk}")

    def show_hidden(self, hwnd: int) -> bool:
        try:
            _u32.ShowWindow(hwnd, SW_SHOW)
            if _u32.IsIconic(hwnd):
                _u32.ShowWindow(hwnd, SW_RESTORE)
            return self.activate(hwnd)
        except Exception:  # noqa: BLE001
            return False

    def open_or_focus(self, path: str, exe: str | None = None, args=None) -> str:
        """应用已打开 -> 切到它的窗口；否则启动。返回 "focused" / "launched" / "failed"。"""
        exe = exe or (os.path.basename(path) if str(path).lower().endswith(".exe") else "")
        if exe:
            h = self.find_app_window(exe)
            if h and self.activate(h):
                return "focused"
            from wisland.core.config import cfg
            hk = (cfg.get("apps.restore_hotkey") or {}).get(exe.lower())
            if hk and self._process_running(exe):
                if send_hotkey(hk):
                    QtCore.QTimer.singleShot(600, lambda e=exe, k=hk: self._focus_later(e, k))
                    return "focused"
        if args and isinstance(args, str):
            args = args.split()
        return "launched" if launch(path, list(args or [])) else "failed"

    def foreground_is_fullscreen(self, own_hwnd=None) -> bool:
        try:
            fg = _u32.GetForegroundWindow()
            if not fg or fg == _u32.GetShellWindow():
                return False
            if own_hwnd and int(fg) == int(own_hwnd):
                return False
            if _pid(fg) == self._own_pid:
                return False
            if _class(fg) in _SHELL_CLASSES:
                return False
            style = _u32.GetWindowLongPtrW(fg, GWL_STYLE)
            if style & WS_MAXIMIZE and (style & WS_CAPTION) == WS_CAPTION:
                return False
            r = W.RECT()
            if not _u32.GetWindowRect(fg, ctypes.byref(r)):
                return False
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            hm = _u32.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST)
            if not hm or not _u32.GetMonitorInfoW(hm, ctypes.byref(mi)):
                return False
            m = mi.rcMonitor
            return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
        except Exception:  # noqa: BLE001
            return False
