# -*- coding: utf-8 -*-
"""AI 额度五服务商定义与连接器状态表（DeepSeek、WorkBuddy、Gemini、Grok Bot、Codex 均已接入）。"""
from __future__ import annotations

from dataclasses import dataclass

from .models import AccentColor, Provider, VALID_PROVIDERS


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    name: str
    badge: str
    implemented: bool
    default_source: str
    default_color: str
    auth_hint: str


PROVIDER_ORDER: tuple[str, ...] = (
    Provider.DEEPSEEK.value,
    Provider.WORKBUDDY.value,
    Provider.GEMINI.value,
    Provider.GROK_BOT.value,
    Provider.CODEX.value,
)

PROVIDERS: dict[str, ProviderSpec] = {
    Provider.DEEPSEEK.value: ProviderSpec(
        key=Provider.DEEPSEEK.value,
        name="DeepSeek",
        badge="DS",
        implemented=True,
        default_source="https://api.deepseek.com/user/balance",
        default_color=AccentColor.BLUE.value,
        auth_hint="使用 DeepSeek 开放平台 API Key 查询账户可用余额、赠送余额与充值余额。",
    ),
    Provider.WORKBUDDY.value: ProviderSpec(
        key=Provider.WORKBUDDY.value,
        name="WorkBuddy",
        badge="WB",
        implemented=True,
        default_source="https://www.codebuddy.cn",
        default_color=AccentColor.EMERALD.value,
        auth_hint="优先读取本机 WorkBuddy 登录；不可用时通过官网个人账号授权连接。",
    ),
    Provider.GEMINI.value: ProviderSpec(
        key=Provider.GEMINI.value,
        name="Gemini",
        badge="GM",
        implemented=True,
        default_source="https://gemini.google.com/usage",
        default_color=AccentColor.VIOLET.value,
        auth_hint="使用 Google 邮箱与 gemini.google.com 登录 Cookie 查询网页会员额度（绝不跨域转发）。",
    ),
    Provider.GROK_BOT.value: ProviderSpec(
        key=Provider.GROK_BOT.value,
        name="Grok Bot",
        badge="GK",
        implemented=True,
        default_source="https://api2.cursor.sh",
        default_color=AccentColor.AMBER.value,
        auth_hint="通过默认浏览器完成 Grok Bot (Cursor) 授权登录，令牌仅存于系统凭据管理器。",
    ),
    Provider.CODEX.value: ProviderSpec(
        key=Provider.CODEX.value,
        name="Codex",
        badge="CX",
        implemented=True,
        default_source="codex://local",
        default_color=AccentColor.CYAN.value,
        auth_hint="自动只读读取本机 $CODEX_HOME/auth.json 或 ~/.codex/auth.json 登录状态。",
    ),
}


def is_valid_provider(provider: str) -> bool:
    return str(provider or "").strip().lower() in VALID_PROVIDERS


def get_provider(provider: str) -> ProviderSpec:
    key = str(provider or "").strip().lower()
    if key not in PROVIDERS:
        raise ValueError(f"未知的 AI 额度服务商: {provider!r}")
    return PROVIDERS[key]


def is_provider_implemented(provider: str) -> bool:
    key = str(provider or "").strip().lower()
    spec = PROVIDERS.get(key)
    return bool(spec and spec.implemented)
