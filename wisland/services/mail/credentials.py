# -*- coding: utf-8 -*-
"""Windows 凭据管理器封装（复用 core.credentials.Win32CredentialBase）。
Target 仅使用账号 ID 派生标识，绝不把明文密码或授权码写入配置文件或日志。
"""
from __future__ import annotations

from ...core.credentials import InMemoryCredentialStore, Win32CredentialBase


def make_credential_key(account_id: str) -> str:
    """生成不可反推密码的稳定 Credential Manager target 标识。"""
    clean = "".join(c for c in str(account_id or "") if c.isalnum() or c in ("-", "_"))
    return f"Wisland/mail/{clean}"


class WindowsCredentialStore(Win32CredentialBase):
    """Windows Credential Manager 邮箱凭据存储实现。"""

    _comment = "Wisland Mail Credential"

    def save_secret(self, target: str, username: str, secret: str) -> bool:
        self._clear_error()
        if not target or not secret:
            return False
        return self._write_single(target, username, secret)

    def read_secret(self, target: str) -> str | None:
        self._clear_error()
        return self._read_single(target)

    def delete_secret(self, target: str) -> bool:
        self._clear_error()
        return self._delete_single(target)


__all__ = ["InMemoryCredentialStore", "WindowsCredentialStore", "make_credential_key"]
