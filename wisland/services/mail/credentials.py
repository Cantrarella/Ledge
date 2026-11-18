# -*- coding: utf-8 -*-
"""Windows 凭据管理器封装（使用原生 advapi32 CredWriteW/CredReadW/CredDeleteW）。
Target 仅使用账号 ID 派生标识，绝不把明文密码或授权码写入配置文件或日志。
"""
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
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


def make_credential_key(account_id: str) -> str:
    """生成不可反推密码的稳定 Credential Manager target 标识。"""
    clean = "".join(c for c in str(account_id or "") if c.isalnum() or c in ("-", "_"))
    return f"Wisland/mail/{clean}"


class WindowsCredentialStore:
    """Windows Credential Manager 存储实现。"""

    def save_secret(self, target: str, username: str, secret: str) -> bool:
        if not target or not secret:
            return False
        blob = secret.encode("utf-16-le")
        arr = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = "Wisland Mail Credential"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = username or target
        return bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))

    def read_secret(self, target: str) -> str | None:
        if not target:
            return None
        p_cred = ctypes.POINTER(_CREDENTIALW)()
        if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p_cred)):
            return None
        try:
            cred = p_cred.contents
            if not cred.CredentialBlob or cred.CredentialBlobSize <= 0:
                return ""
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le", errors="replace")
        finally:
            _advapi32.CredFree(p_cred)

    def delete_secret(self, target: str) -> bool:
        if not target:
            return False
        ok = bool(_advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0))
        if not ok and ctypes.get_last_error() == ERROR_NOT_FOUND:
            return True
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
