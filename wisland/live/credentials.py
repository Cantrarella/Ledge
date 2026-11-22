# -*- coding: utf-8 -*-
"""实时字幕 API Key 的 Windows 凭据存储与旧配置迁移。"""
from __future__ import annotations

from ..core.config import cfg
from ..services.quota.credentials import WindowsQuotaCredentialStore


TRANSLATE_CREDENTIAL_KEY = "Wisland/translate/deepseek"


def _store() -> WindowsQuotaCredentialStore:
    return WindowsQuotaCredentialStore()


def get_translate_api_key(config=cfg, store=None) -> str:
    """优先读取凭据管理器；旧版 config.json 明文 Key 首次读取时自动迁移。"""
    store = store or _store()
    key = store.read_secret(TRANSLATE_CREDENTIAL_KEY) or ""
    legacy = str(config.get("translate.api_key") or "").strip()
    if key:
        if legacy:
            config.set("translate.api_key", "", save=True)
        return key
    if legacy and store.save_secret(TRANSLATE_CREDENTIAL_KEY, "Ledge", legacy):
        config.set("translate.api_key", "", save=True)
    return legacy


def save_translate_api_key(value: str, config=cfg, store=None) -> bool:
    """空值表示删除；成功后同时清除旧的 config.json 明文字段。"""
    key = str(value or "").strip()
    store = store or _store()
    ok = store.delete_secret(TRANSLATE_CREDENTIAL_KEY) if not key else store.save_secret(
        TRANSLATE_CREDENTIAL_KEY,
        "Ledge",
        key,
    )
    if ok and config.get("translate.api_key"):
        config.set("translate.api_key", "", save=True)
    return ok
