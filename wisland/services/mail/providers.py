# -*- coding: utf-8 -*-
"""邮箱服务商预设配置、域名不支持校验（Microsoft OAuth）与自定义 IMAP 参数校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MailProviderSpec:
    key: str
    name: str
    host: str
    port: int
    security: str           # "ssl" | "starttls" | "none"
    auth_url: str
    guide: str


PROVIDERS: dict[str, MailProviderSpec] = {
    "qq": MailProviderSpec(
        key="qq",
        name="QQ 邮箱",
        host="imap.qq.com",
        port=993,
        security="ssl",
        auth_url="https://mail.qq.com",
        guide="请在 QQ 邮箱「设置 → 账号 → POP3/IMAP/SMTP」开启 IMAP 服务，并生成 16 位授权码填入下方。",
    ),
    "163": MailProviderSpec(
        key="163",
        name="163 邮箱",
        host="imap.163.com",
        port=993,
        security="ssl",
        auth_url="https://mail.163.com",
        guide="请在网易 163 邮箱「设置 → POP3/SMTP/IMAP」开启 IMAP 服务，并使用客户端授权密码登录。",
    ),
    "126": MailProviderSpec(
        key="126",
        name="126 邮箱",
        host="imap.126.com",
        port=993,
        security="ssl",
        auth_url="https://mail.126.com",
        guide="请在网易 126 邮箱「设置 → POP3/SMTP/IMAP」开启 IMAP 服务，并使用客户端授权密码登录。",
    ),
    "exmail": MailProviderSpec(
        key="exmail",
        name="腾讯企业邮",
        host="imap.exmail.qq.com",
        port=993,
        security="ssl",
        auth_url="https://exmail.qq.com",
        guide="请在腾讯企业邮「设置 → 邮箱绑定/安全设置」开启 IMAP 并生成客户端专用密码。",
    ),
    "icloud": MailProviderSpec(
        key="icloud",
        name="iCloud",
        host="imap.mail.me.com",
        port=993,
        security="ssl",
        auth_url="https://account.apple.com",
        guide="需先在 Apple 账户开启双重认证，再生成「App 专用密码」填入（请勿填 Apple ID 登录密码）。",
    ),
    "gmail": MailProviderSpec(
        key="gmail",
        name="Gmail",
        host="imap.gmail.com",
        port=993,
        security="ssl",
        auth_url="https://myaccount.google.com/apppasswords",
        guide="需先在 Gmail 设置开启 IMAP 并为 Google 账户生成 16 位「应用专用密码」（需可访问 Google 网络）。",
    ),
    "custom": MailProviderSpec(
        key="custom",
        name="自定义 IMAP",
        host="",
        port=993,
        security="ssl",
        auth_url="",
        guide="请填写服务商提供的 IMAP 服务器地址、端口（默认 SSL 993）与加密方式。",
    ),
}

PROVIDER_ORDER: tuple[str, ...] = ("qq", "163", "126", "exmail", "icloud", "gmail", "custom")

_UNSUPPORTED_MS_DOMAINS = (
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "outlook.cn",
    "office365.com",
)
_UNSUPPORTED_MS_HOSTS = (
    "outlook.office365.com",
    "imap-mail.outlook.com",
    "imap.outlook.com",
)
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_provider(key: str) -> MailProviderSpec:
    return PROVIDERS.get((key or "").strip().lower(), PROVIDERS["custom"])


def is_unsupported_microsoft_email(email: str, host: str = "") -> str:
    """检测是否为需要 OAuth 的微软邮箱，若不支持则返回明确中文说明，否则返回空串。"""
    em = (email or "").strip().lower()
    domain = em.rsplit("@", 1)[1] if "@" in em else ""
    h = (host or "").strip().lower()
    if any(domain == d or domain.endswith("." + d) for d in _UNSUPPORTED_MS_DOMAINS) or h in _UNSUPPORTED_MS_HOSTS:
        return "Outlook / Hotmail / Live / Microsoft 365 需要 OAuth2 登录，当前版本暂不支持。"
    return ""


def validate_account_params(
    provider: str,
    email: str,
    secret: str,
    host: str = "",
    port: int = 993,
    security: str = "ssl",
    require_secret: bool = True,
) -> tuple[bool, str, dict]:
    """校验添加/重连邮箱参数并补全服务商默认值。"""
    em = (email or "").strip()
    if not _EMAIL_RE.match(em):
        return False, "请输入有效的邮箱地址", {}

    spec = get_provider(provider)
    ms_err = is_unsupported_microsoft_email(em, host if spec.key == "custom" else "")
    if ms_err:
        return False, ms_err, {}

    if require_secret and not (secret or "").strip():
        return False, "请填写授权码或 App 专用密码", {}

    if spec.key != "custom":
        final_host = spec.host
        final_port = spec.port
        final_sec = spec.security
    else:
        final_host = (host or "").strip()
        if not final_host or not _HOST_RE.match(final_host):
            return False, "请输入有效的 IMAP 服务器地址", {}
        try:
            final_port = int(port)
        except (TypeError, ValueError):
            return False, "端口号必须是 1~65535 的整数", {}
        if final_port < 1 or final_port > 65535:
            return False, "端口号必须在 1~65535 之间", {}
        final_sec = (security or "ssl").strip().lower()
        if final_sec not in ("ssl", "starttls", "none"):
            return False, "安全方式仅支持 SSL/TLS、STARTTLS 或无加密", {}

    return True, "", {
        "provider": spec.key,
        "email": em,
        "host": final_host,
        "port": final_port,
        "security": final_sec,
    }
