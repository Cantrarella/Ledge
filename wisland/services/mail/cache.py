# -*- coding: utf-8 -*-
"""本机邮件正文与验证码缓存：按 (account_id, uid_validity, uid) 隔离存储并限制每账号缓存上限。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from ...core.config import DATA_DIR
from .models import MailBody, VerificationCodeInfo

MAX_BODIES_PER_ACCOUNT = 60


def compute_body_hash(text: str) -> str:
    """计算正文纯文本哈希，用于判断内容是否变化。"""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


class LocalMailBodyCache:
    """按账号落盘保存最近阅读/预取的纯文本正文与验证码（绝不保存凭据）。"""

    def __init__(self, cache_dir: Path | str | None = None, max_per_account: int = MAX_BODIES_PER_ACCOUNT):
        self._dir = Path(cache_dir) if cache_dir is not None else (DATA_DIR / "mail_cache")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max = max(5, int(max_per_account))
        self._lock = threading.Lock()

    def _file_for(self, account_id: str) -> Path:
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (account_id or "default"))
        return self._dir / f"{safe_id}.json"

    def _read_account_raw(self, account_id: str) -> dict:
        p = self._file_for(account_id)
        if not p.exists():
            return {"uid_validity": "", "entries": {}}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                return raw
        except Exception:  # noqa: BLE001
            pass
        return {"uid_validity": "", "entries": {}}

    def _write_account_raw(self, account_id: str, data: dict):
        p = self._file_for(account_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    @staticmethod
    def _entry_key(uid_validity: str, uid: str) -> str:
        return f"{uid_validity or ''}::{uid}"

    def get(
        self,
        account_id: str,
        uid: str,
        uid_validity: str = "",
    ) -> tuple[MailBody | None, VerificationCodeInfo | None]:
        """从本机缓存读取正文及对应验证码；若 UIDVALIDITY 变化则不命中。"""
        with self._lock:
            raw = self._read_account_raw(account_id)
            entries: dict = raw.get("entries", {})
            k = self._entry_key(uid_validity, str(uid))
            item = entries.get(k)
            if not isinstance(item, dict) and not uid_validity:
                # 若调用未指定 uid_validity，尝试匹配当前账号最新 uid_validity
                cur_uv = str(raw.get("uid_validity") or "")
                item = entries.get(self._entry_key(cur_uv, str(uid)))
            if not isinstance(item, dict):
                return None, None
            body = MailBody(
                account_id=account_id,
                uid=str(uid),
                text=str(item.get("text") or ""),
                source=str(item.get("source") or "text/plain"),
                truncated=bool(item.get("truncated", False)),
                fetched_at=float(item.get("fetched_at") or 0.0),
                uid_validity=str(item.get("uid_validity") or uid_validity or ""),
                content_hash=str(item.get("content_hash") or ""),
            )
            otp_code = str(item.get("otp_code") or "")
            otp: VerificationCodeInfo | None = None
            if otp_code:
                ttl_raw = item.get("otp_ttl")
                otp = VerificationCodeInfo(
                    code=otp_code,
                    ttl_seconds=int(ttl_raw) if isinstance(ttl_raw, (int, float)) else None,
                    source_hint=str(item.get("otp_hint") or ""),
                )
            return body, otp

    def has(self, account_id: str, uid: str, uid_validity: str = "") -> bool:
        body, _ = self.get(account_id, uid, uid_validity=uid_validity)
        return body is not None

    def put(
        self,
        body: MailBody,
        otp: VerificationCodeInfo | None = None,
    ):
        """写入或更新本机缓存（内容 Hash 未变且已存在时避免无意义重写），超出上限按时间淘汰。"""
        with self._lock:
            raw = self._read_account_raw(body.account_id)
            uv = str(body.uid_validity or "")
            if uv and raw.get("uid_validity") and raw.get("uid_validity") != uv:
                # UIDVALIDITY 变更：淘汰旧 UIDVALIDITY 条目
                raw["entries"] = {
                    ek: ev for ek, ev in raw.get("entries", {}).items()
                    if isinstance(ev, dict) and str(ev.get("uid_validity") or "") == uv
                }
            if uv:
                raw["uid_validity"] = uv
            entries: dict = raw.setdefault("entries", {})
            c_hash = body.content_hash or compute_body_hash(body.text)
            body.content_hash = c_hash
            k = self._entry_key(uv, str(body.uid))

            old = entries.get(k)
            if isinstance(old, dict) and old.get("content_hash") == c_hash:
                old_code = str(old.get("otp_code") or "")
                new_code = otp.code if otp is not None else ""
                if old_code == new_code:
                    return

            entries[k] = {
                "uid": str(body.uid),
                "uid_validity": uv,
                "text": body.text,
                "source": body.source,
                "truncated": bool(body.truncated),
                "fetched_at": float(body.fetched_at or time.time()),
                "content_hash": c_hash,
                "otp_code": otp.code if otp is not None else "",
                "otp_ttl": otp.ttl_seconds if otp is not None else None,
                "otp_hint": otp.source_hint if otp is not None else "",
            }

            if len(entries) > self._max:
                sorted_items = sorted(
                    entries.items(),
                    key=lambda kv: float((kv[1] or {}).get("fetched_at") or 0.0),
                    reverse=True,
                )
                raw["entries"] = dict(sorted_items[: self._max])

            self._write_account_raw(body.account_id, raw)

    def remove_account(self, account_id: str):
        with self._lock:
            p = self._file_for(account_id)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
