# -*- coding: utf-8 -*-
"""AI 额度数据模型：服务商枚举、状态枚举、强调色、账号配置与额度快照。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Provider(str, Enum):
    """第一阶段仅允许的五个服务商枚举。"""
    DEEPSEEK = "deepseek"
    WORKBUDDY = "workbuddy"
    GEMINI = "gemini"
    GROK_BOT = "grok_bot"
    CODEX = "codex"


VALID_PROVIDERS: frozenset[str] = frozenset(p.value for p in Provider)


class AccountStatus(str, Enum):
    """统一账号状态枚举。"""
    UNCONFIGURED = "unconfigured"
    UNSUPPORTED = "unsupported"
    REFRESHING = "refreshing"
    OK = "ok"
    STALE = "stale"
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    DISABLED = "disabled"


STATUS_META: dict[str, tuple[str, str, str]] = {
    # status: (短标签, 颜色语义, Tooltip 说明)
    AccountStatus.UNCONFIGURED.value: ("尚未连接", "muted", "账号尚未完成连接测试或首次刷新"),
    AccountStatus.UNSUPPORTED.value: ("连接器待接入", "muted", "该服务商连接器将在下一阶段接入"),
    AccountStatus.REFRESHING.value: ("刷新中", "accent", "正在后台请求最新额度数据"),
    AccountStatus.OK.value: ("正常", "ok", "额度数据已同步，连接正常"),
    AccountStatus.STALE.value: ("数据已过期", "warn", "最近一次刷新失败或刚从缓存加载，当前显示上一份有效数据"),
    AccountStatus.AUTH_ERROR.value: ("密钥失效", "err", "API Key 无效、过期或权限不足 (401/403)，请点击重新连接"),
    AccountStatus.RATE_LIMITED.value: ("请求频繁", "warn", "触发服务端速率限制 (429)，已自动退避至少 60 秒"),
    AccountStatus.NETWORK_ERROR.value: ("网络异常", "err", "网络连接断开或请求超时，请检查网络设置"),
    AccountStatus.PARSE_ERROR.value: ("格式错误", "err", "服务端响应格式异常或余额数据为空"),
    AccountStatus.DISABLED.value: ("监控关闭", "muted", "已暂停该账号的自动与批量刷新"),
}


class AccentColor(str, Enum):
    """账号强调色枚举。"""
    BLUE = "blue"
    EMERALD = "emerald"
    AMBER = "amber"
    VIOLET = "violet"
    ROSE = "rose"
    CYAN = "cyan"


ACCENT_COLORS: dict[str, tuple[str, str]] = {
    AccentColor.BLUE.value: ("深海蓝", "#4f8cff"),
    AccentColor.EMERALD.value: ("翡翠绿", "#2ec27e"),
    AccentColor.AMBER.value: ("琥珀黄", "#f59e0b"),
    AccentColor.VIOLET.value: ("星云紫", "#8b5cf6"),
    AccentColor.ROSE.value: ("珊瑚红", "#f43f5e"),
    AccentColor.CYAN.value: ("晴空青", "#06b6d4"),
}

_FORBIDDEN_SECRET_KEYS = frozenset({
    "api_key",
    "apikey",
    "key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "oauth_token",
    "cookie",
    "cookies",
    "auth_code",
    "authorization",
})

_KNOWN_ACCOUNT_FIELDS = frozenset({
    "id",
    "provider",
    "alias",
    "identity",
    "source",
    "credential_key",
    "enabled",
    "pinned",
    "color",
    "created_at",
    "last_test_at",
    "last_refresh_at",
    "last_error",
})


@dataclass
class QuotaWindow:
    """周期配额窗口。"""
    label: str
    remaining_percent: float | None = None
    reset_at: float | None = None
    amount: float | None = None
    currency: str = ""

    def to_dict(self) -> dict:
        return {
            "label": str(self.label or ""),
            "remaining_percent": float(self.remaining_percent) if self.remaining_percent is not None else None,
            "reset_at": float(self.reset_at) if self.reset_at is not None else None,
            "amount": float(self.amount) if self.amount is not None else None,
            "currency": str(self.currency or ""),
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuotaWindow | None:
        if not isinstance(data, dict):
            return None
        try:
            rem = data.get("remaining_percent")
            rst = data.get("reset_at")
            amt = data.get("amount")
            return cls(
                label=str(data.get("label") or ""),
                remaining_percent=float(rem) if rem is not None else None,
                reset_at=float(rst) if rst is not None else None,
                amount=float(amt) if amt is not None else None,
                currency=str(data.get("currency") or ""),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class QuotaBalance:
    """货币余额明细（如 DeepSeek CNY/USD 可用、赠送、充值余额）。"""
    currency: str
    total_balance: float
    granted_balance: float = 0.0
    topped_up_balance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "currency": str(self.currency or "CNY"),
            "total_balance": float(self.total_balance),
            "granted_balance": float(self.granted_balance),
            "topped_up_balance": float(self.topped_up_balance),
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuotaBalance | None:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                currency=str(data.get("currency") or "CNY"),
                total_balance=float(data.get("total_balance", 0.0)),
                granted_balance=float(data.get("granted_balance", 0.0)),
                topped_up_balance=float(data.get("topped_up_balance", 0.0)),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class QuotaSnapshot:
    """单账号额度快照（仅包含非敏感展示数据，可安全落盘缓存）。"""
    account_id: str
    fetched_at: float = 0.0
    status: str = AccountStatus.UNCONFIGURED.value
    windows: list[QuotaWindow] = field(default_factory=list)
    balances: list[QuotaBalance] = field(default_factory=list)
    credits: float | None = None
    plan_name: str = ""
    last_error: str = ""
    raw_stale: bool = False

    def has_valid_data(self) -> bool:
        return bool(self.balances or self.windows or self.credits is not None)

    def primary_balance(self) -> QuotaBalance | None:
        if not self.balances:
            return None
        for b in self.balances:
            if b.currency.upper() == "CNY":
                return b
        return self.balances[0]

    def to_dict(self) -> dict:
        return {
            "account_id": str(self.account_id or ""),
            "fetched_at": float(self.fetched_at or 0.0),
            "status": str(self.status or AccountStatus.UNCONFIGURED.value),
            "windows": [w.to_dict() for w in self.windows],
            "balances": [b.to_dict() for b in self.balances],
            "credits": float(self.credits) if self.credits is not None else None,
            "plan_name": str(self.plan_name or ""),
            "last_error": str(self.last_error or ""),
            "raw_stale": bool(self.raw_stale),
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuotaSnapshot | None:
        if not isinstance(data, dict):
            return None
        acc_id = str(data.get("account_id") or "").strip()
        if not acc_id:
            return None
        raw_wins = data.get("windows", [])
        wins = [w for item in (raw_wins if isinstance(raw_wins, list) else []) if (w := QuotaWindow.from_dict(item)) is not None]
        raw_bals = data.get("balances", [])
        bals = [b for item in (raw_bals if isinstance(raw_bals, list) else []) if (b := QuotaBalance.from_dict(item)) is not None]
        st = str(data.get("status") or AccountStatus.UNCONFIGURED.value)
        if st not in STATUS_META:
            st = AccountStatus.UNCONFIGURED.value
        cred = data.get("credits")
        try:
            cred_val = float(cred) if cred is not None else None
        except (TypeError, ValueError):
            cred_val = None
        try:
            fetched = float(data.get("fetched_at") or 0.0)
        except (TypeError, ValueError):
            fetched = 0.0
        return cls(
            account_id=acc_id,
            fetched_at=fetched,
            status=st,
            windows=wins,
            balances=bals,
            credits=cred_val,
            plan_name=str(data.get("plan_name") or ""),
            last_error=str(data.get("last_error") or ""),
            raw_stale=bool(data.get("raw_stale", False)),
        )


@dataclass
class QuotaAccount:
    """AI 额度账号配置模型（绝不保存明文凭据）。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    provider: str = Provider.DEEPSEEK.value
    alias: str = ""
    identity: str = ""
    source: str = ""
    credential_key: str = ""
    enabled: bool = True
    pinned: bool = False
    color: str = AccentColor.BLUE.value
    created_at: float = field(default_factory=time.time)
    last_test_at: float = 0.0
    last_refresh_at: float = 0.0
    last_error: str = ""
    extra_fields: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        p = str(self.provider or "").strip().lower()
        if p not in VALID_PROVIDERS:
            raise ValueError(f"不支持的 AI 额度服务商: {self.provider!r}")
        self.provider = p
        if not self.id or not str(self.id).strip():
            self.id = uuid.uuid4().hex
        else:
            self.id = str(self.id).strip()
        if self.color not in ACCENT_COLORS:
            self.color = AccentColor.BLUE.value
        if not self.credential_key:
            from .credentials import make_quota_credential_key
            self.credential_key = make_quota_credential_key(self.id)

    def dedupe_key(self) -> tuple[str, str, str]:
        """使用 provider + identity + source 进行账号去重。"""
        return (
            self.provider.lower().strip(),
            self.identity.lower().strip(),
            self.source.lower().strip(),
        )

    def to_config_dict(self) -> dict:
        out = {
            k: v
            for k, v in (self.extra_fields or {}).items()
            if str(k).lower() not in _FORBIDDEN_SECRET_KEYS and k not in _KNOWN_ACCOUNT_FIELDS
        }
        out.update({
            "id": self.id,
            "provider": self.provider,
            "alias": str(self.alias or ""),
            "identity": str(self.identity or ""),
            "source": str(self.source or ""),
            "credential_key": str(self.credential_key or ""),
            "enabled": bool(self.enabled),
            "pinned": bool(self.pinned),
            "color": self.color if self.color in ACCENT_COLORS else AccentColor.BLUE.value,
            "created_at": float(self.created_at or 0.0),
            "last_test_at": float(self.last_test_at or 0.0),
            "last_refresh_at": float(self.last_refresh_at or 0.0),
            "last_error": str(self.last_error or ""),
        })
        return out

    @classmethod
    def from_config_dict(cls, data: dict) -> QuotaAccount | None:
        if not isinstance(data, dict):
            return None
        raw_provider = str(data.get("provider") or "").strip().lower()
        if raw_provider not in VALID_PROVIDERS:
            return None
        acc_id = str(data.get("id") or "").strip()
        # 拒绝使用纯数字数组下标作为 id（若缺失则生成稳定 UUID）
        if not acc_id:
            acc_id = uuid.uuid4().hex
        color = str(data.get("color") or AccentColor.BLUE.value).strip().lower()
        if color not in ACCENT_COLORS:
            color = AccentColor.BLUE.value
        extra = {
            k: v
            for k, v in data.items()
            if k not in _KNOWN_ACCOUNT_FIELDS and str(k).lower() not in _FORBIDDEN_SECRET_KEYS
        }
        try:
            created_at = float(data.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        try:
            last_test_at = float(data.get("last_test_at") or 0.0)
        except (TypeError, ValueError):
            last_test_at = 0.0
        try:
            last_refresh_at = float(data.get("last_refresh_at") or 0.0)
        except (TypeError, ValueError):
            last_refresh_at = 0.0
        return cls(
            id=acc_id,
            provider=raw_provider,
            alias=str(data.get("alias") or "").strip(),
            identity=str(data.get("identity") or "").strip(),
            source=str(data.get("source") or "").strip(),
            credential_key=str(data.get("credential_key") or "").strip(),
            enabled=bool(data.get("enabled", True)),
            pinned=bool(data.get("pinned", False)),
            color=color,
            created_at=created_at,
            last_test_at=last_test_at,
            last_refresh_at=last_refresh_at,
            last_error=str(data.get("last_error") or "").strip(),
            extra_fields=extra,
        )
