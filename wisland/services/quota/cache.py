# -*- coding: utf-8 -*-
"""AI 额度本地缓存：存储在 data/quota_cache.json，以 account_id 为键，绝不写入敏感凭据或响应原文。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ...core.config import DATA_DIR
from .models import AccountStatus, QuotaSnapshot, _FORBIDDEN_SECRET_KEYS


def _strip_sensitive(obj):
    if isinstance(obj, dict):
        return {
            k: _strip_sensitive(v)
            for k, v in obj.items()
            if str(k).lower() not in _FORBIDDEN_SECRET_KEYS and str(k).lower() not in ("raw_body", "raw_response")
        }
    if isinstance(obj, list):
        return [_strip_sensitive(x) for x in obj]
    return obj


class LocalQuotaCache:
    """额度快照本地持久化缓存。"""

    def __init__(self, cache_path: Path | None = None):
        self.path: Path = Path(cache_path) if cache_path is not None else (DATA_DIR / "quota_cache.json")
        self._snapshots: dict[str, QuotaSnapshot] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        items = raw.get("snapshots", {}) if isinstance(raw, dict) else {}
        if not isinstance(items, dict):
            return
        for acc_id, data in items.items():
            if not isinstance(data, dict):
                continue
            clean_data = _strip_sensitive(data)
            clean_data["account_id"] = str(acc_id)
            snap = QuotaSnapshot.from_dict(clean_data)
            if snap is not None:
                self._snapshots[str(acc_id)] = snap

    def load_startup_snapshots(self) -> dict[str, QuotaSnapshot]:
        """启动时加载已有缓存并统一标记为“数据已过期 (stale)”，等待后台刷新。"""
        self._ensure_loaded()
        out: dict[str, QuotaSnapshot] = {}
        for acc_id, snap in self._snapshots.items():
            if snap.has_valid_data():
                snap.raw_stale = True
                snap.status = AccountStatus.STALE.value
            out[acc_id] = snap
        return out

    def get(self, account_id: str) -> QuotaSnapshot | None:
        self._ensure_loaded()
        return self._snapshots.get(str(account_id or ""))

    def put(self, snapshot: QuotaSnapshot, flush: bool = True):
        self._ensure_loaded()
        if not snapshot or not snapshot.account_id:
            return
        # 仅缓存非敏感快照结构
        clean_dict = _strip_sensitive(snapshot.to_dict())
        clean_snap = QuotaSnapshot.from_dict(clean_dict)
        if clean_snap is not None:
            self._snapshots[clean_snap.account_id] = clean_snap
            if flush:
                self.flush()

    def remove(self, account_id: str, flush: bool = True):
        self._ensure_loaded()
        key = str(account_id or "")
        if key in self._snapshots:
            self._snapshots.pop(key, None)
            if flush:
                self.flush()

    def flush(self):
        self._ensure_loaded()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "snapshots": {
                    acc_id: _strip_sensitive(snap.to_dict())
                    for acc_id, snap in self._snapshots.items()
                },
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
