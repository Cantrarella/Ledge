# -*- coding: utf-8 -*-
"""系统服务：窗口列表与激活、全屏检测。"""
from __future__ import annotations

import ctypes
import os
import traceback
from ctypes import wintypes as W

from PySide6 import QtCore, QtGui

from wisland.core.bg import launch

# ======================================================================
# 窗口
# ======================================================================
_u32 = ctypes.WinDLL("user32")
_k32 = ctypes.windll.kernel32
_dwm = ctypes.windll.dwmapi
_shell32 = ctypes.windll.shell32

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_VISIBLE = 0x10000000
WS_MAXIMIZE, WS_CAPTION = 0x01000000, 0x00C00000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
GW_OWNER = 4
DWMWA_CLOAKED = 14
WM_GETICON, WM_CLOSE = 0x007F, 0x0010
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCLP_HICON, GCLP_HICONSM = -14, -34
SMTO_ABORTIFHUNG = 0x0002
SW_RESTORE, SW_SHOW = 9, 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
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
_u32.GetClassLongPtrW.restype = ctypes.c_size_t
_u32.GetClassLongPtrW.argtypes = [W.HWND, ctypes.c_int]
_u32.GetWindow.restype = W.HWND
_u32.GetWindow.argtypes = [W.HWND, W.UINT]
_u32.GetWindowTextLengthW.argtypes = [W.HWND]
_u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
_u32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
_u32.GetWindowThreadProcessId.restype = W.DWORD
_u32.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                     ctypes.POINTER(ctypes.c_size_t)]
_u32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
_u32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
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
_u32.GetAncestor.argtypes = [W.HWND, W.UINT]
_u32.GetAncestor.restype = W.HWND
_u32.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.c_size_t]
_u32.DestroyIcon.argtypes = [W.HICON]
_dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
_k32.OpenProcess.restype = W.HANDLE
_k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
_k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
_k32.CloseHandle.argtypes = [W.HANDLE]
_shell32.ExtractIconExW.argtypes = [W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.HICON),
                                    ctypes.POINTER(W.HICON), W.UINT]


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


def _hicon_to_image(hicon) -> QtGui.QImage | None:
    if not hicon:
        return None
    try:
        img = QtGui.QImage.fromHICON(int(hicon))
        return None if img.isNull() else img
    except Exception:  # noqa: BLE001
        return None


class WindowService(QtCore.QObject):
    notice = QtCore.Signal(str)
    """Alt-Tab 风格窗口列表、激活、关闭、全屏检测。全部是同步的快速 Win32 调用（主线程可用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._own_pid = os.getpid()
        self._icons: dict[int, QtGui.QImage | None] = {}
        self._exe_icons: dict[str, QtGui.QImage | None] = {}
        self._exe_cache: dict[int, str] = {}

    def start(self):
        pass

    def stop(self):
        pass

    # ---------------- 列表 ----------------
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

    def list_windows(self) -> list[dict]:
        hwnds: list[int] = []

        def cb(h, lp):
            hwnds.append(h)
            return True
        try:
            _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return []
        fg = _u32.GetForegroundWindow() or 0
        fg_root = _u32.GetAncestor(fg, 3) if fg else 0   # GA_ROOTOWNER
        out = []
        alive = set()
        for h in hwnds:
            try:
                if not self._is_alt_tab(h):
                    continue
                title = _title(h)
                if not title.strip():
                    continue
                pid = _pid(h)
                if pid == self._own_pid:
                    continue
                path = self._exe_for(pid)
                exe = os.path.basename(path)
                rpid = self._real_pid(h, pid, exe)
                if rpid != pid:
                    pid = rpid
                    path = self._exe_for(pid) or path
                    exe = os.path.basename(path)
                alive.add(h)
                out.append({"hwnd": int(h), "title": title, "exe": exe, "pid": pid,
                            "icon": self._icon(h, path),
                            "active": h == fg or h == fg_root})
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        for h in list(self._icons):
            if h not in alive:
                del self._icons[h]
        return out

    def _icon(self, hwnd, path: str):
        if hwnd in self._icons and self._icons[hwnd] is not None:
            return self._icons[hwnd]
        img = None
        for wp in (ICON_BIG, ICON_SMALL2, ICON_SMALL):
            res = ctypes.c_size_t(0)
            if _u32.SendMessageTimeoutW(hwnd, WM_GETICON, wp, 0, SMTO_ABORTIFHUNG, 50,
                                        ctypes.byref(res)) and res.value:
                img = _hicon_to_image(res.value)
                if img is not None:
                    break
        if img is None:
            for idx in (GCLP_HICON, GCLP_HICONSM):
                hi = _u32.GetClassLongPtrW(hwnd, idx)
                if hi:
                    img = _hicon_to_image(hi)
                    if img is not None:
                        break
        if img is None and path:
            img = self._exe_icon(path)
        self._icons[hwnd] = img
        return img

    def _exe_icon(self, path: str):
        key = path.lower()
        if key in self._exe_icons:
            return self._exe_icons[key]
        img = None
        large, small = W.HICON(), W.HICON()
        try:
            if _shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1) > 0:
                img = _hicon_to_image(large.value) or _hicon_to_image(small.value)
        except Exception:  # noqa: BLE001
            pass
        finally:
            for h in (large, small):
                if h.value:
                    _u32.DestroyIcon(h)
        self._exe_icons[key] = img
        return img

    # ---------------- 操作 ----------------
    def activate(self, hwnd: int) -> bool:
        try:
            hwnd = int(hwnd)
            if not _u32.IsWindow(hwnd):
                return False
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

    def close(self, hwnd: int) -> bool:
        try:
            return bool(_u32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0))
        except Exception:  # noqa: BLE001
            return False

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
