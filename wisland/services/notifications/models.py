# -*- coding: utf-8 -*-
"""标准化通知事件模型与隐私脱敏工具（不依赖任何 WinRT/COM 对象）。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum


class PermissionStatus(str, Enum):
    """Windows 通知访问权限状态。"""
    ALLOWED = "allowed"
    DENIED = "denied"
    NOT_DETERMINED = "not_determined"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


def permission_status_label(status: PermissionStatus | str) -> str:
    """将权限状态转换为中文可读说明。"""
    val = status.value if isinstance(status, PermissionStatus) else str(status or "")
    return {
        "allowed": "已授权（Windows 通知监听中）",
        "denied": "已被系统拒绝（请在 Windows 设置中开启通知访问权限，当前使用任务栏兼容）",
        "not_determined": "尚未授权（点击「重新请求权限」或在 Windows 设置中开启）",
        "unsupported": "当前系统不支持 UserNotificationListener（已自动降级为任务栏兼容）",
        "unknown": "正在检测权限状态",
    }.get(val, "状态未知")


_CODE_RE = re.compile(
    r"(?:(?:验证码|校验码|动态码|确认码|code|otp|pin|passcode)\s*[:：是为]?\s*)([A-Za-z0-9]{4,8})",
    re.I,
)
_DIGIT_TOKEN_RE = re.compile(r"\b\d{4,8}\b")


def mask_sensitive_text(text: str, max_len: int = 48) -> str:
    """对通知摘要做隐私脱敏（遮盖验证码/数字口令并截断），绝不原样暴露敏感验证码。"""
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    if _CODE_RE.search(s):
        s = _CODE_RE.sub(lambda m: m.group(0).replace(m.group(1), "****"), s)
    elif any(k in s.lower() for k in ("密码", "授权码", "口令", "password", "token", "secret", "验证码")):
        s = _DIGIT_TOKEN_RE.sub("****", s)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def compute_content_hash(title: str = "", sender: str = "", body: str = "") -> str:
    """计算通知内容摘要哈希，用于去重与原地内容更新检测。"""
    h = hashlib.sha256()
    for part in (title or "", sender or "", body or ""):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


@dataclass
class NotificationEvent:
    """标准化通知事件（页面、横幅与控制层统一使用，不携带 WinRT 对象）。"""
    event_id: str
    source_key: str                      # "wechat" | "qq" | "mail" | "other"
    app_name: str
    app_identity: str
    account_key: str = ""
    pid: int | None = None
    window_handle: int | None = None
    process_started_at: float | None = None
    title: str = ""
    sender: str = ""
    body: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    content_hash: str = ""
    can_open: bool = False
    raw_kind: str = "winrt_toast"        # "winrt_toast" | "wpn_db" | "taskbar_flash" | "mail_imap"
    notification_id: str = ""
    creation_time: int = 0
    account_ambiguous: bool = False
    meta: dict = field(default_factory=dict)

    def dedupe_key(self) -> tuple[str, str, int]:
        """去重键：(app_identity, notification_id, creation_time)。"""
        return (self.app_identity or self.source_key, str(self.notification_id or self.event_id), int(self.creation_time or 0))
