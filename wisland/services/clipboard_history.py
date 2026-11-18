# -*- coding: utf-8 -*-
"""文字剪贴板历史服务：参考 Daozai src/clipboard.rs 实现。
- 仅记录纯文字（不记录图片和文件引用），保存在本机 data/clipboard.json。
- 最近未固定文本最多 30 条；固定文本最多 20 条（固定条目不占最近 30 条名额）；单条最长 20,000 字符。
- 识别 Windows 剪贴板敏感/隐私格式：
  ExcludeClipboardContentFromMonitorProcessing / Clipboard Viewer Ignore / CanIncludeInClipboardHistory=0
- 使用 Windows AddClipboardFormatListener 系统事件通知（零轮询），剪贴板被占用时在后台线程有限重试，不阻塞 GUI 线程。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore

from ..core.config import DATA_DIR, cfg

MAX_RECENT = 30
MAX_PINNED = 20
MAX_CHARS = 20_000

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
WM_CLIPBOARDUPDATE = 0x031D
WM_QUIT = 0x0012
HWND_MESSAGE = wintypes.HWND(-3)

HISTORY_COPY_FORMAT = "Wisland.ClipboardHistoryCopy"
DAOZAI_COPY_FORMAT = "Daozai.ClipboardHistoryCopy"
FMT_EXCLUDE_MONITOR = "ExcludeClipboardContentFromMonitorProcessing"
FMT_VIEWER_IGNORE = "Clipboard Viewer Ignore"
FMT_CAN_INCLUDE_HISTORY = "CanIncludeInClipboardHistory"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def preview_text(text: str, max_len: int = 120) -> str:
    """将多行与连续空白压成单行空格预览。"""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > max_len:
        return collapsed[:max_len] + "…"
    return collapsed


def check_sensitive_formats(available_formats: set[str], can_include_dword: int | None = None) -> bool:
    """纯逻辑判定函数（可供单元测试与 Win32 读取共同调用）：
    若包含密码管理器敏感标记或 CanIncludeInClipboardHistory == 0，则返回 True（跳过不记录）。
    """
    if FMT_EXCLUDE_MONITOR in available_formats or FMT_VIEWER_IGNORE in available_formats:
        return True
    if FMT_CAN_INCLUDE_HISTORY in available_formats:
        if can_include_dword is None or can_include_dword == 0:
            return True
    return False


def _win32_is_excluded() -> bool:
    """在已打开的剪贴板上下文中检查敏感/忽略格式。"""
    fmt_ex = user32.RegisterClipboardFormatW(FMT_EXCLUDE_MONITOR)
    fmt_ig = user32.RegisterClipboardFormatW(FMT_VIEWER_IGNORE)
    if (fmt_ex and user32.IsClipboardFormatAvailable(fmt_ex)) or (fmt_ig and user32.IsClipboardFormatAvailable(fmt_ig)):
        return True

    fmt_hist = user32.RegisterClipboardFormatW(FMT_CAN_INCLUDE_HISTORY)
    if not fmt_hist or not user32.IsClipboardFormatAvailable(fmt_hist):
        return False

    hmem = user32.GetClipboardData(fmt_hist)
    if not hmem:
        return True
    ptr = kernel32.GlobalLock(hmem)
    if not ptr:
        return True
    try:
        sz = kernel32.GlobalSize(hmem)
        if sz < 4:
            return True
        val = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32)).contents.value
        return val == 0
    finally:
        kernel32.GlobalUnlock(hmem)


def _win32_read_clipboard_text(hwnd: int = 0) -> tuple[str, bool] | None:
    """在后台线程中读取系统剪贴板纯文本。
    - 若剪贴板不包含 CF_UNICODETEXT 或包含文件引用 CF_HDROP，返回 None。
    - 若剪贴板被其他进程短暂占用，最多重试 5 次（每次间隔 20ms）。
    - 返回 (text, from_history) 或 None。
    """
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if user32.IsClipboardFormatAvailable(CF_HDROP):
        return None

    opened = False
    for _ in range(5):
        if user32.OpenClipboard(hwnd):
            opened = True
            break
        time.sleep(0.02)
    if not opened:
        return None

    try:
        if _win32_is_excluded():
            return None

        fmt_self = user32.RegisterClipboardFormatW(HISTORY_COPY_FORMAT)
        fmt_daozai = user32.RegisterClipboardFormatW(DAOZAI_COPY_FORMAT)
        from_history = bool(
            (fmt_self and user32.IsClipboardFormatAvailable(fmt_self))
            or (fmt_daozai and user32.IsClipboardFormatAvailable(fmt_daozai))
        )

        hmem = user32.GetClipboardData(CF_UNICODETEXT)
        if not hmem:
            return None
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            return None
        try:
            byte_size = kernel32.GlobalSize(hmem)
            max_u16 = min(byte_size // 2, MAX_CHARS + 2)
            if max_u16 <= 0:
                return None
            text = ctypes.wstring_at(ptr, max_u16)
            nul = text.find("\x00")
            if nul >= 0:
                text = text[:nul]
            if len(text) > MAX_CHARS:
                return None
            return text, from_history
        finally:
            kernel32.GlobalUnlock(hmem)
    finally:
        user32.CloseClipboard()


def _win32_write_history_text(text: str) -> bool:
    """将历史条目写回系统剪贴板，并打上 Wisland.ClipboardHistoryCopy 标记避免再次生成重复记录。"""
    fmt_self = user32.RegisterClipboardFormatW(HISTORY_COPY_FORMAT)
    opened = False
    for _ in range(8):
        if user32.OpenClipboard(0):
            opened = True
            break
        time.sleep(0.015)
    if not opened:
        return False

    try:
        user32.EmptyClipboard()
        raw_bytes = (text + "\x00").encode("utf-16-le")
        h_text = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw_bytes))
        if not h_text:
            return False
        p_text = kernel32.GlobalLock(h_text)
        if not p_text:
            return False
        ctypes.memmove(p_text, raw_bytes, len(raw_bytes))
        kernel32.GlobalUnlock(h_text)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_text):
            return False

        if fmt_self:
            h_flag = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            if h_flag:
                p_flag = kernel32.GlobalLock(h_flag)
                if p_flag:
                    ctypes.memset(p_flag, 1, 4)
                    kernel32.GlobalUnlock(h_flag)
                    user32.SetClipboardData(fmt_self, h_flag)
        return True
    finally:
        user32.CloseClipboard()


class ClipboardHistoryService(QtCore.QObject):
    changed = QtCore.Signal()
    notice = QtCore.Signal(str, bool)   # (message, is_error)

    def __init__(self, path: Path | None = None, start_watcher: bool = False):
        super().__init__()
        self._lock = threading.RLock()
        self.path: Path = Path(path) if path is not None else (DATA_DIR / "clipboard.json")
        self.items: list[dict] = []     # [{"text": str, "at": "HH:MM", "pinned": bool}]
        self.version: int = 0
        self._watcher_thread: threading.Thread | None = None
        self._watcher_tid: int = 0
        self._stop_flag = threading.Event()
        self.load()
        if start_watcher and self.is_enabled():
            self.start()

    def is_enabled(self) -> bool:
        return bool(cfg.get("tools.clipboard_enabled", True))

    def set_enabled(self, on: bool):
        on = bool(on)
        cfg.set("tools.clipboard_enabled", on)
        if on:
            self.start()
        else:
            self.stop()
        self.changed.emit()

    def load(self):
        """从 data/clipboard.json 加载；若文件损坏则先备份为 .corrupt.bak 再重置，绝不崩溃。"""
        with self._lock:
            self.items = []
            if not self.path.exists():
                return
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                rows = data.get("items") if isinstance(data, dict) else (data if isinstance(data, list) else None)
                if not isinstance(rows, list):
                    raise ValueError("clipboard.json 格式无效")
                loaded: list[dict] = []
                pinned_cnt = 0
                recent_cnt = 0
                seen: set[str] = set()
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    t = r.get("text")
                    if not isinstance(t, str) or not t.strip() or len(t) > MAX_CHARS or t in seen:
                        continue
                    pinned = bool(r.get("pinned", False))
                    if pinned:
                        if pinned_cnt >= MAX_PINNED:
                            continue
                        pinned_cnt += 1
                    else:
                        if recent_cnt >= MAX_RECENT:
                            continue
                        recent_cnt += 1
                    seen.add(t)
                    at = str(r.get("at") or "")
                    loaded.append({"text": t, "at": at, "pinned": pinned})
                self.items = loaded
            except Exception:  # noqa: BLE001
                self._backup_corrupted()
                self.items = []

    def _backup_corrupted(self):
        try:
            bak = self.path.with_suffix(".corrupt.bak")
            shutil.copy2(self.path, bak)
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        """原子写入 data/clipboard.json，不向控制台或日志输出任何剪贴板文本内容。"""
        with self._lock:
            self.version += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.items, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def list_items(self) -> list[dict]:
        with self._lock:
            return [dict(it) for it in self.items]

    def counts(self) -> tuple[int, int]:
        """返回 (未固定条数, 已固定条数)。"""
        with self._lock:
            pinned = sum(1 for it in self.items if it["pinned"])
            return len(self.items) - pinned, pinned

    def push(self, text: str, at: str | None = None, from_history: bool = False) -> bool:
        """记录一条新文本：
        - from_history=True 时直接跳过（自身从历史复制的内容不重复触发）。
        - 空文本或超过 20,000 字符不记录。
        - 重复文本移到最前并保留原固定状态。
        - 固定条目不占最近 30 条未固定名额。
        """
        if from_history:
            return False
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
            return False
        now_at = at or time.strftime("%H:%M", time.localtime())

        with self._lock:
            existing_idx = next((i for i, c in enumerate(self.items) if c["text"] == text), None)
            if existing_idx == 0:
                return False
            pinned = False
            if existing_idx is not None:
                pinned = bool(self.items.pop(existing_idx).get("pinned", False))
            self.items.insert(0, {"text": text, "at": now_at, "pinned": pinned})

            # 保留所有已固定条目 + 最多 MAX_RECENT (30) 条未固定条目
            recent_count = 0
            kept: list[dict] = []
            for c in self.items:
                if c["pinned"]:
                    kept.append(c)
                else:
                    recent_count += 1
                    if recent_count <= MAX_RECENT:
                        kept.append(c)
            self.items = kept
            self.save()

        self.changed.emit()
        return True

    def toggle_pin(self, index: int) -> tuple[bool, str]:
        """切换指定索引条目的固定状态（固定上限 MAX_PINNED = 20）。"""
        with self._lock:
            if not (0 <= index < len(self.items)):
                return False, "条目不存在"
            item = self.items[index]
            if not item["pinned"]:
                pinned_cnt = sum(1 for c in self.items if c["pinned"])
                if pinned_cnt >= MAX_PINNED:
                    msg = f"固定条目已达上限（最多 {MAX_PINNED} 条）"
                    self.notice.emit(msg, True)
                    return False, msg
                item["pinned"] = True
            else:
                item["pinned"] = False
                # 取消固定后若未固定总数超过 MAX_RECENT，裁剪最旧的未固定项
                recent_count = 0
                kept: list[dict] = []
                for c in self.items:
                    if c["pinned"]:
                        kept.append(c)
                    else:
                        recent_count += 1
                        if recent_count <= MAX_RECENT:
                            kept.append(c)
                self.items = kept
            self.save()
        self.changed.emit()
        return True, ""

    def remove_at(self, index: int) -> bool:
        with self._lock:
            if not (0 <= index < len(self.items)):
                return False
            self.items.pop(index)
            self.save()
        self.changed.emit()
        return True

    def clear(self, include_pinned: bool = False):
        """清空剪贴板历史：include_pinned=False 时仅清空未固定条目，保留已固定条目。"""
        with self._lock:
            if include_pinned:
                self.items = []
            else:
                self.items = [c for c in self.items if c["pinned"]]
            self.save()
        self.changed.emit()
        msg = "已清空全部剪贴板历史（含固定项）" if include_pinned else "已清空未固定的剪贴板历史（固定项已保留）"
        self.notice.emit(msg, False)

    def copy_from_history(self, text: str) -> bool:
        """从历史列表中点击复制：打上自身复制标记，不重新改变历史顺序或生成重复项。"""
        ok = _win32_write_history_text(text)
        if ok:
            self.notice.emit("已复制到剪贴板", False)
        else:
            self.notice.emit("复制失败：剪贴板正被其他程序占用", True)
        return ok

    # ---------------- Windows AddClipboardFormatListener 后台监听 ----------------
    def start(self):
        if not self.is_enabled():
            return
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return
        self._stop_flag.clear()
        self._watcher_thread = threading.Thread(target=self._watch_loop, name="wisland-clipboard", daemon=True)
        self._watcher_thread.start()

    def stop(self):
        self._stop_flag.set()
        tid = self._watcher_tid
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=0.5)
        self._watcher_thread = None
        self._watcher_tid = 0

    def _watch_loop(self):
        """后台消息循环：使用 HWND_MESSAGE + AddClipboardFormatListener 零轮询监听剪贴板变化。"""
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                if not self._stop_flag.is_set() and self.is_enabled():
                    res = _win32_read_clipboard_text(hwnd)
                    if res is not None:
                        text, from_hist = res
                        self.push(text, from_history=from_hist)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc_cb = WNDPROC(wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"WislandClipboardWatcher_{id(self)}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = proc_cb
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(
            0, cls_name, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst, None
        )
        if not hwnd:
            return

        self._watcher_tid = int(kernel32.GetCurrentThreadId())
        if self._stop_flag.is_set():
            user32.DestroyWindow(hwnd)
            return

        user32.AddClipboardFormatListener(hwnd)
        try:
            msg = wintypes.MSG()
            while not self._stop_flag.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.RemoveClipboardFormatListener(hwnd)
            user32.DestroyWindow(hwnd)
            self._watcher_tid = 0
