# -*- coding: utf-8 -*-
"""Windows Credential Manager 基础封装（基于原生 advapi32 CredWriteW/CredReadW/CredDeleteW）。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredReadW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


class Win32CredentialBase:
    """Windows Generic Credential 单条读写基类（带 WinError 记录）。"""

    _comment: str = "Wisland Credential"

    def __init__(self):
        self.last_error: int = 0
        self.last_error_message: str = ""

    def _clear_error(self):
        self.last_error = 0
        self.last_error_message = ""

    def _record_win_error(self, fallback: str):
        err_code = int(ctypes.get_last_error() or 0)
        self.last_error = err_code
        if err_code:
            try:
                detail = str(ctypes.FormatError(err_code) or "").strip()
            except Exception:
                detail = ""
            self.last_error_message = f"WinError {err_code}: {detail}" if detail else f"WinError {err_code}"
        else:
            self.last_error_message = fallback

    def _write_single(self, target: str, username: str, secret: str) -> bool:
        blob = secret.encode("utf-16-le")
        arr = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = self._comment
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = username or target
        ctypes.set_last_error(0)
        ok = bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))
        if not ok:
            self._record_win_error("CredWriteW 失败")
        return ok

    def _read_single(self, target: str) -> str | None:
        if not target:
            return None
        p_cred = ctypes.POINTER(_CREDENTIALW)()
        ctypes.set_last_error(0)
        if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p_cred)):
            err_code = int(ctypes.get_last_error() or 0)
            if err_code and err_code != ERROR_NOT_FOUND:
                self._record_win_error("CredReadW 失败")
            return None
        try:
            cred = p_cred.contents
            if not cred.CredentialBlob or cred.CredentialBlobSize <= 0:
                return ""
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le", errors="replace")
        finally:
            _advapi32.CredFree(p_cred)

    def _delete_single(self, target: str) -> bool:
        if not target:
            return False
        ctypes.set_last_error(0)
        ok = bool(_advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0))
        if not ok:
            err_code = int(ctypes.get_last_error() or 0)
            if err_code == ERROR_NOT_FOUND:
                return True
            self._record_win_error("CredDeleteW 失败")
        return ok


class InMemoryCredentialStore:
    """测试用内存凭据存储。"""

    def __init__(self):
        self._data: dict[str, tuple[str, str]] = {}

    def save_secret(self, target: str, username: str, secret: str) -> bool:
        if not target or not secret:
            return False
        self._data[target] = (username, secret)
        return True

    def read_secret(self, target: str) -> str | None:
        item = self._data.get(target)
        return item[1] if item is not None else None

    def delete_secret(self, target: str) -> bool:
        self._data.pop(target, None)
        return True
