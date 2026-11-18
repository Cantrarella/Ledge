# -*- coding: utf-8 -*-
"""多账号邮件基础设施数据模型（仅包含元数据与最小邮件头，绝不存储密码/正文/附件）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

_FORBIDDEN_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "auth_code", "secret", "cookie", "cookies", "token", "oauth_token",
})


@dataclass
class MailAccount:
    """邮箱账号配置与运行状态元数据（凭据仅通过 credential_key 引用）。"""
    id: str
    provider: str
    email: str
    display_name: str = ""
    username: str = ""
    host: str = ""
    port: int = 993
    security: str = "ssl"               # "ssl" | "starttls" | "none"
    enabled: bool = True
    credential_key: str = ""
    last_test_at: float | None = None
    last_sync_at: float | None = None
    last_error: str = ""

    def to_config_dict(self) -> dict:
        """导出可写入 config.json 的安全字典（绝不包含明文密码/授权码/Cookie/Token）。"""
        return {
            "id": str(self.id),
            "provider": str(self.provider),
            "email": str(self.email),
            "display_name": str(self.display_name or ""),
            "username": str(self.username or self.email),
            "host": str(self.host),
            "port": int(self.port or 993),
            "security": str(self.security or "ssl"),
            "enabled": bool(self.enabled),
            "credential_key": str(self.credential_key),
            "last_test_at": self.last_test_at,
            "last_sync_at": self.last_sync_at,
            "last_error": str(self.last_error or ""),
        }

    @classmethod
    def from_config_dict(cls, raw: dict) -> MailAccount | None:
        """从配置字典安全恢复 MailAccount，损坏或缺关键字段时返回 None。"""
        if not isinstance(raw, dict):
            return None
        email = str(raw.get("email") or "").strip()
        host = str(raw.get("host") or "").strip()
        if not email or not host:
            return None
        acc_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
        provider = str(raw.get("provider") or "custom").strip().lower()
        try:
            port = int(raw.get("port") or 993)
        except (TypeError, ValueError):
            port = 993
        if port < 1 or port > 65535:
            port = 993
        sec = str(raw.get("security") or "ssl").strip().lower()
        if sec not in ("ssl", "starttls", "none"):
            sec = "ssl"
        cred_key = str(raw.get("credential_key") or f"Wisland/mail/{acc_id}")
        return cls(
            id=acc_id,
            provider=provider,
            email=email,
            display_name=str(raw.get("display_name") or "").strip(),
            username=str(raw.get("username") or email).strip(),
            host=host,
            port=port,
            security=sec,
            enabled=bool(raw.get("enabled", True)),
            credential_key=cred_key,
            last_test_at=raw.get("last_test_at"),
            last_sync_at=raw.get("last_sync_at"),
            last_error=str(raw.get("last_error") or ""),
        )


def strip_forbidden_secret_keys(d: dict) -> dict:
    """清理字典中任何禁止持久化的敏感凭据键。"""
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if str(k).lower() not in _FORBIDDEN_SECRET_KEYS}


@dataclass
class MailHeader:
    """最小邮件头元数据（不下载正文与附件）。"""
    uid: str
    account_id: str
    sender: str
    subject: str
    received_at: float
    is_read: bool
