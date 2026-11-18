# -*- coding: utf-8 -*-
"""多账号邮件管理器：账号配置持久化（无明文凭据）、Windows 凭据管理、非阻塞 IMAP 测试与只读同步。"""
from __future__ import annotations

import threading
import time
import uuid

from PySide6 import QtCore

from ...core.config import cfg
from .cache import LocalMailBodyCache, MAX_BODIES_PER_ACCOUNT
from .credentials import WindowsCredentialStore, make_credential_key
from .imap import (
    DEFAULT_TIMEOUT_S,
    fetch_headers_and_prefetch_bodies,
    fetch_message_body,
    mark_message_seen_remote,
    prefetch_bodies_in_session,
    test_imap_connection,
)
from .models import MailAccount, MailBody, MailHeader, VerificationCodeInfo
from .otp import extract_verification_code
from .providers import validate_account_params

MAX_RUNTIME_HEADERS = 60
DEFAULT_FETCH_LIMIT = 20
IMMEDIATE_PREFETCH_COUNT = 5


class MailAccountManager(QtCore.QObject):
    """管理多个邮箱账号及其独立连接状态、凭据引用与最近邮件元数据。"""

    accounts_changed = QtCore.Signal()
    account_state_changed = QtCore.Signal(str)          # account_id
    headers_updated = QtCore.Signal(str, list)          # account_id, list[MailHeader]
    body_loaded = QtCore.Signal(str, str, bool, object, str)     # account_id, uid, ok, MailBody|None, error_msg
    body_prefetched = QtCore.Signal(str, str)           # account_id, uid
    remote_seen_finished = QtCore.Signal(str, str, bool, str)    # account_id, uid, ok, error_msg
    task_finished = QtCore.Signal(str, bool, str)       # req_id, ok, error_msg

    # 内部跨线程投递信号（确保所有状态修改与对外信号在主线程执行）
    _internal_done = QtCore.Signal(str, str, bool, str, object)
    _internal_prefetch_item = QtCore.Signal(str, object)

    def __init__(
        self,
        config_obj=None,
        cred_store=None,
        client_factory=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        body_cache: LocalMailBodyCache | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._cfg = config_obj or cfg
        self._creds = cred_store or WindowsCredentialStore()
        self._client_factory = client_factory
        self._timeout_s = float(timeout_s)
        self._body_cache = body_cache or LocalMailBodyCache()

        self._accounts: list[MailAccount] = []
        self._headers: dict[str, list[MailHeader]] = {}
        self._uid_validities: dict[str, str] = {}
        self._bodies: dict[tuple[str, str, str], MailBody] = {}
        self._otp_cache: dict[tuple[str, str, str], VerificationCodeInfo] = {}
        self._local_read_uids: dict[str, set[str]] = {}
        self._syncing_accounts: set[str] = set()
        self._prefetch_req_by_acc: dict[str, str] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

        self._internal_done.connect(self._on_task_done)
        self._internal_prefetch_item.connect(self._on_prefetch_item)
        self.load_from_config()

    # ---------------- 加载与保存（绝不保存凭据明文） ----------------
    def load_from_config(self):
        raw = self._cfg.get("mail.accounts", [])
        loaded: list[MailAccount] = []
        seen_ids: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                acc = MailAccount.from_config_dict(item)
                if acc is None or acc.id in seen_ids:
                    continue
                seen_ids.add(acc.id)
                loaded.append(acc)
        self._accounts = loaded

    def _save_to_config(self):
        self._cfg.set("mail.accounts", [a.to_config_dict() for a in self._accounts])

    def list_accounts(self) -> list[MailAccount]:
        return list(self._accounts)

    def get_account(self, account_id: str) -> MailAccount | None:
        for a in self._accounts:
            if a.id == account_id:
                return a
        return None

    def headers_for(self, account_id: str) -> list[MailHeader]:
        return list(self._headers.get(account_id, []))

    def all_headers(self, account_id: str | None = None) -> list[MailHeader]:
        """按收到时间倒序返回全部或指定账号的邮件头（不同账号同 UID 绝不合并）。"""
        if account_id:
            return sorted(self._headers.get(account_id, []), key=lambda x: x.received_at, reverse=True)
        enabled_ids = {a.id for a in self._accounts if a.enabled}
        merged: list[MailHeader] = []
        for aid, hdrs in self._headers.items():
            if aid in enabled_ids:
                merged.extend(hdrs)
        merged.sort(key=lambda x: x.received_at, reverse=True)
        return merged

    def is_syncing(self, account_id: str | None = None) -> bool:
        with self._lock:
            if account_id:
                return account_id in self._syncing_accounts
            return bool(self._syncing_accounts)

    def mark_all_read_local(self, account_id: str | None = None) -> int:
        """仅在本机将当前列表中的未读邮件标记为已读（不连网回写服务器）。"""
        target_ids = [account_id] if account_id else [a.id for a in self._accounts if a.enabled]
        total_changed = 0
        for aid in target_ids:
            hdrs = self._headers.get(aid, [])
            read_set = self._local_read_uids.setdefault(aid, set())
            acc_changed = 0
            for h in hdrs:
                if not h.is_read:
                    h.is_read = True
                    acc_changed += 1
                read_set.add(str(h.uid))
            if acc_changed:
                total_changed += acc_changed
                self.headers_updated.emit(aid, list(hdrs))
        return total_changed

    # ---------------- 账号编辑与删除 ----------------
    def set_enabled(self, account_id: str, enabled: bool) -> bool:
        acc = self.get_account(account_id)
        if acc is None:
            return False
        acc.enabled = bool(enabled)
        self._save_to_config()
        self.account_state_changed.emit(account_id)
        self.accounts_changed.emit()
        return True

    def set_display_name(self, account_id: str, display_name: str) -> bool:
        acc = self.get_account(account_id)
        if acc is None:
            return False
        acc.display_name = str(display_name or "").strip()
        self._save_to_config()
        self.account_state_changed.emit(account_id)
        self.accounts_changed.emit()
        return True

    def remove_account(self, account_id: str) -> bool:
        acc = self.get_account(account_id)
        if acc is None:
            return False
        self._cancel_account_prefetch(account_id)
        self._accounts = [a for a in self._accounts if a.id != account_id]
        self._headers.pop(account_id, None)
        self._uid_validities.pop(account_id, None)
        self._local_read_uids.pop(account_id, None)
        for key in [k for k in self._bodies if k[0] == account_id]:
            self._bodies.pop(key, None)
        for key in [k for k in self._otp_cache if k[0] == account_id]:
            self._otp_cache.pop(key, None)
        self._body_cache.remove_account(account_id)
        if acc.credential_key:
            self._creds.delete_secret(acc.credential_key)
        self._save_to_config()
        self.accounts_changed.emit()
        return True

    def _cache_key(self, account_id: str, uid: str, uid_validity: str = "") -> tuple[str, str, str]:
        uv = str(uid_validity or self._uid_validities.get(account_id, "") or "")
        return (account_id, uv, str(uid))

    def _store_body_and_otp(self, body: MailBody, persist: bool = True) -> VerificationCodeInfo | None:
        """将正文及提取出的验证码一起存入内存缓存与本机磁盘缓存，并执行每账号上限淘汰。"""
        uv = str(body.uid_validity or self._uid_validities.get(body.account_id, "") or "")
        if uv and not body.uid_validity:
            body.uid_validity = uv
        key = (body.account_id, uv, str(body.uid))
        self._bodies[key] = body

        otp_info: VerificationCodeInfo | None = None
        if bool(self._cfg.get("mail.otp_enabled", True)):
            subj = ""
            for h in self._headers.get(body.account_id, []):
                if str(h.uid) == str(body.uid):
                    subj = h.subject
                    break
            otp_info = extract_verification_code(subject=subj, body_text=body.text)
            if otp_info is not None:
                self._otp_cache[key] = otp_info

        # 控制内存缓存每账号上限
        acc_keys = [k for k in self._bodies if k[0] == body.account_id]
        if len(acc_keys) > MAX_BODIES_PER_ACCOUNT:
            acc_keys.sort(key=lambda k: self._bodies[k].fetched_at, reverse=True)
            for stale_k in acc_keys[MAX_BODIES_PER_ACCOUNT:]:
                self._bodies.pop(stale_k, None)
                self._otp_cache.pop(stale_k, None)

        if persist:
            self._body_cache.put(body, otp_info)
        return otp_info

    def get_cached_body(self, account_id: str, uid: str, uid_validity: str = "") -> MailBody | None:
        """按顺序检查内存缓存 -> 本机正文缓存；命中则同步载入内存与验证码。"""
        u = str(uid)
        key = self._cache_key(account_id, u, uid_validity)
        body = self._bodies.get(key)
        if body is not None:
            return body
        if not uid_validity:
            for k, b in self._bodies.items():
                if k[0] == account_id and k[2] == u:
                    return b
        # 本机磁盘缓存
        disk_body, disk_otp = self._body_cache.get(account_id, u, uid_validity=key[1])
        if disk_body is not None:
            dk = (account_id, str(disk_body.uid_validity or key[1]), u)
            self._bodies[dk] = disk_body
            if disk_otp is not None:
                self._otp_cache[dk] = disk_otp
            elif bool(self._cfg.get("mail.otp_enabled", True)):
                subj = ""
                for h in self._headers.get(account_id, []):
                    if str(h.uid) == u:
                        subj = h.subject
                        break
                info = extract_verification_code(subject=subj, body_text=disk_body.text)
                if info is not None:
                    self._otp_cache[dk] = info
            return disk_body
        return None

    def get_verification_code(
        self,
        account_id: str,
        uid: str,
        subject: str = "",
        uid_validity: str = "",
    ) -> VerificationCodeInfo | None:
        """获取邮件识别出的验证码（受 mail.otp_enabled 控制；不写入日志）。"""
        if not bool(self._cfg.get("mail.otp_enabled", True)):
            return None
        u = str(uid)
        key = self._cache_key(account_id, u, uid_validity)
        if key in self._otp_cache:
            return self._otp_cache[key]
        if not uid_validity:
            for k, v in self._otp_cache.items():
                if k[0] == account_id and k[2] == u:
                    return v
        body = self.get_cached_body(account_id, u, uid_validity=key[1])
        if key in self._otp_cache:
            return self._otp_cache[key]
        body_text = body.text if body is not None else ""
        info = extract_verification_code(subject=subject, body_text=body_text)
        if info is not None:
            self._otp_cache[key] = info
        return info

    def merge_incoming_headers(self, account_id: str, new_hdrs: list[MailHeader]):
        """供后台监听合入新邮件头，并按需在后台预取其正文。"""
        if not new_hdrs:
            return
        read_set = self._local_read_uids.get(account_id, set())
        existing = {h.uid: h for h in self._headers.get(account_id, [])}
        uv = ""
        for h in new_hdrs:
            if h.uid_validity:
                uv = h.uid_validity
            if str(h.uid) in read_set:
                h.is_read = True
            existing[h.uid] = h
        if uv:
            self._uid_validities[account_id] = uv
        merged = sorted(existing.values(), key=lambda x: x.received_at, reverse=True)[:MAX_RUNTIME_HEADERS]
        self._headers[account_id] = merged
        self.headers_updated.emit(account_id, list(merged))

        # 若当前无并发同步/预取，后台低优先级预取新邮件正文
        new_uids = [str(h.uid) for h in new_hdrs if self.get_cached_body(account_id, str(h.uid), h.uid_validity) is None]
        if new_uids:
            self._start_background_prefetch(account_id, new_uids, uv)

    def mark_message_read_local(self, account_id: str, uid: str) -> bool:
        """仅在本机将单封邮件标记为已读，不修改 IMAP 服务器。"""
        u = str(uid)
        read_set = self._local_read_uids.setdefault(account_id, set())
        read_set.add(u)
        hdrs = self._headers.get(account_id, [])
        changed = False
        for h in hdrs:
            if str(h.uid) == u and not h.is_read:
                h.is_read = True
                changed = True
                break
        if changed:
            self.headers_updated.emit(account_id, list(hdrs))
        return changed

    # ---------------- 异步任务调度与取消 ----------------
    def cancel_task(self, req_id: str):
        with self._lock:
            ev = self._cancel_events.get(req_id)
        if ev is not None:
            ev.set()

    def shutdown(self):
        with self._lock:
            events = list(self._cancel_events.values())
            threads = list(self._threads.values())
        for ev in events:
            ev.set()
        for th in threads:
            if th.is_alive():
                th.join(timeout=1.2)

    def _spawn(self, kind: str, fn, payload: dict) -> str:
        req_id = uuid.uuid4().hex[:10]
        cancel_ev = threading.Event()
        with self._lock:
            self._cancel_events[req_id] = cancel_ev

        def _worker():
            ok = False
            err = ""
            res = dict(payload)
            try:
                ok, err, extra = fn(cancel_ev)
                if isinstance(extra, dict):
                    res.update(extra)
            except Exception as exc:  # noqa: BLE001
                ok = False
                err = f"执行异常（{type(exc).__name__}）"
            self._internal_done.emit(req_id, kind, ok, err, res)

        th = threading.Thread(target=_worker, name=f"wisland-mail-{kind}", daemon=True)
        with self._lock:
            self._threads[req_id] = th
        th.start()
        return req_id

    def _on_task_done(self, req_id: str, kind: str, ok: bool, err: str, res: dict):
        with self._lock:
            self._cancel_events.pop(req_id, None)
            self._threads.pop(req_id, None)

        now = time.time()
        if kind == "add":
            if ok:
                acc: MailAccount = res["account"]
                secret: str = res["secret"]
                if not self._creds.save_secret(acc.credential_key, acc.username or acc.email, secret):
                    self.task_finished.emit(req_id, False, "写入 Windows 凭据管理器失败")
                    return
                acc.last_test_at = now
                acc.last_error = ""
                self._accounts.append(acc)
                self._save_to_config()
                self.accounts_changed.emit()
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "reconnect":
            acc_id = res.get("account_id", "")
            acc = self.get_account(acc_id)
            if acc is not None:
                if ok:
                    secret = res["secret"]
                    if not self._creds.save_secret(acc.credential_key, acc.username or acc.email, secret):
                        acc.last_error = "更新 Windows 凭据失败"
                        self._save_to_config()
                        self.account_state_changed.emit(acc_id)
                        self.task_finished.emit(req_id, False, acc.last_error)
                        return
                    acc.last_test_at = now
                    acc.last_error = ""
                else:
                    # 验证失败不覆盖旧凭据，仅记录错误
                    acc.last_error = err
                self._save_to_config()
                self.account_state_changed.emit(acc_id)
                self.accounts_changed.emit()
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "test_existing":
            acc_id = res.get("account_id", "")
            acc = self.get_account(acc_id)
            if acc is not None:
                acc.last_test_at = now if ok else acc.last_test_at
                acc.last_error = "" if ok else err
                self._save_to_config()
                self.account_state_changed.emit(acc_id)
                self.accounts_changed.emit()
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "sync":
            acc_id = res.get("account_id", "")
            with self._lock:
                self._syncing_accounts.discard(acc_id)
            acc = self.get_account(acc_id)
            if acc is not None:
                if ok:
                    uv = str(res.get("uid_validity") or "")
                    if uv and self._uid_validities.get(acc_id) and self._uid_validities[acc_id] != uv:
                        # UIDVALIDITY 变更：淘汰旧内存缓存
                        for k in [k for k in self._bodies if k[0] == acc_id and k[1] != uv]:
                            self._bodies.pop(k, None)
                            self._otp_cache.pop(k, None)
                    if uv:
                        self._uid_validities[acc_id] = uv

                    new_hdrs: list[MailHeader] = res.get("headers") or []
                    read_set = self._local_read_uids.get(acc_id, set())
                    existing = {h.uid: h for h in self._headers.get(acc_id, [])}
                    for h in new_hdrs:
                        if str(h.uid) in read_set:
                            h.is_read = True
                        existing[h.uid] = h
                    merged = sorted(existing.values(), key=lambda x: x.received_at, reverse=True)[:MAX_RUNTIME_HEADERS]
                    self._headers[acc_id] = merged

                    # 保存同会话预取的最近 5 封正文并立即提取验证码
                    prefetched: list[MailBody] = res.get("bodies") or []
                    for b in prefetched:
                        self._store_body_and_otp(b, persist=True)

                    acc.last_sync_at = now
                    acc.last_error = ""
                    self.headers_updated.emit(acc_id, list(merged))

                    # 其余未缓存的邮件（最多 15 封）在后台低优先级单会话预取
                    remain_uids = [
                        str(h.uid) for h in merged[IMMEDIATE_PREFETCH_COUNT:DEFAULT_FETCH_LIMIT]
                        if self.get_cached_body(acc_id, str(h.uid), h.uid_validity or uv) is None
                    ]
                    if remain_uids:
                        self._start_background_prefetch(acc_id, remain_uids, uv)
                else:
                    acc.last_error = err
                self._save_to_config()
                self.account_state_changed.emit(acc_id)
                self.accounts_changed.emit()
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "prefetch_bg":
            acc_id = res.get("account_id", "")
            with self._lock:
                if self._prefetch_req_by_acc.get(acc_id) == req_id:
                    self._prefetch_req_by_acc.pop(acc_id, None)
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "fetch_body":
            acc_id = res.get("account_id", "")
            uid = str(res.get("uid") or "")
            body: MailBody | None = res.get("body")
            if ok and body is not None:
                self._store_body_and_otp(body, persist=True)
            self.body_loaded.emit(acc_id, uid, ok, body if ok else None, err)
            self.task_finished.emit(req_id, ok, err)
            return

        if kind == "mark_seen_remote":
            acc_id = res.get("account_id", "")
            uid = str(res.get("uid") or "")
            self.remote_seen_finished.emit(acc_id, uid, ok, err)
            self.task_finished.emit(req_id, ok, err)
            return

        self.task_finished.emit(req_id, ok, err)

    # ---------------- 对外非阻塞操作 ----------------
    def start_test_connection(
        self,
        provider: str,
        email: str,
        secret: str,
        host: str = "",
        port: int = 993,
        security: str = "ssl",
    ) -> tuple[bool, str, str]:
        """仅测试连接（不保存配置与凭据）。返回 (是否发起, 错误提示, req_id)。"""
        valid, err, clean = validate_account_params(provider, email, secret, host, port, security, require_secret=True)
        if not valid:
            return False, err, ""
        temp_acc = MailAccount(
            id="temp",
            provider=clean["provider"],
            email=clean["email"],
            username=clean["email"],
            host=clean["host"],
            port=clean["port"],
            security=clean["security"],
        )

        def _work(cancel_ev):
            ok, msg = test_imap_connection(
                temp_acc, secret, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {}

        return True, "", self._spawn("test_only", _work, {})

    def start_add_account(
        self,
        provider: str,
        email: str,
        secret: str,
        display_name: str = "",
        host: str = "",
        port: int = 993,
        security: str = "ssl",
    ) -> tuple[bool, str, str]:
        """先测试连接，成功后才保存账号与凭据。"""
        valid, err, clean = validate_account_params(provider, email, secret, host, port, security, require_secret=True)
        if not valid:
            return False, err, ""
        for existing in self._accounts:
            if existing.email.lower() == clean["email"].lower() and existing.host.lower() == clean["host"].lower():
                return False, "该邮箱账号已添加", ""

        acc_id = uuid.uuid4().hex[:12]
        acc = MailAccount(
            id=acc_id,
            provider=clean["provider"],
            email=clean["email"],
            display_name=str(display_name or "").strip(),
            username=clean["email"],
            host=clean["host"],
            port=clean["port"],
            security=clean["security"],
            enabled=True,
            credential_key=make_credential_key(acc_id),
        )

        def _work(cancel_ev):
            ok, msg = test_imap_connection(
                acc, secret, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {}

        return True, "", self._spawn("add", _work, {"account": acc, "secret": secret})

    def start_reconnect_account(self, account_id: str, new_secret: str) -> tuple[bool, str, str]:
        """重连：先验证新凭据，成功后再替换 Windows 凭据管理器中的旧凭据。"""
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        sec = (new_secret or "").strip()
        if not sec:
            return False, "请填写新的授权码或 App 专用密码", ""

        def _work(cancel_ev):
            ok, msg = test_imap_connection(
                acc, sec, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {}

        return True, "", self._spawn("reconnect", _work, {"account_id": account_id, "secret": sec})

    def start_test_existing(self, account_id: str) -> tuple[bool, str, str]:
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            acc.last_error = "未在 Windows 凭据管理器中找到授权码，请重新连接"
            self._save_to_config()
            self.account_state_changed.emit(account_id)
            return False, acc.last_error, ""

        def _work(cancel_ev):
            ok, msg = test_imap_connection(
                acc, secret, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {}

        return True, "", self._spawn("test_existing", _work, {"account_id": account_id})

    @QtCore.Slot(str, object)
    def _on_prefetch_item(self, acc_id: str, body_obj: object):
        if isinstance(body_obj, MailBody):
            self._store_body_and_otp(body_obj, persist=True)
            self.body_prefetched.emit(acc_id, str(body_obj.uid))

    def _cancel_account_prefetch(self, account_id: str):
        with self._lock:
            old_req = self._prefetch_req_by_acc.pop(account_id, "")
        if old_req:
            self.cancel_task(old_req)

    def _start_background_prefetch(self, account_id: str, uids: list[str], uid_validity: str):
        if not uids:
            return
        acc = self.get_account(account_id)
        if acc is None or not acc.enabled:
            return
        with self._lock:
            if account_id in self._syncing_accounts or account_id in self._prefetch_req_by_acc:
                return
        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            return

        def _has_cached(uv: str, uid: str) -> bool:
            return self.get_cached_body(account_id, uid, uv) is not None

        def _on_body(b: MailBody):
            self._internal_prefetch_item.emit(account_id, b)

        def _work(cancel_ev):
            ok, _bodies, msg = prefetch_bodies_in_session(
                acc,
                secret,
                uids,
                expected_uid_validity=uid_validity,
                has_cached_fn=_has_cached,
                on_body_fn=_on_body,
                timeout=self._timeout_s,
                cancel_event=cancel_ev,
                client_factory=self._client_factory,
            )
            return ok, msg, {}

        req_id = self._spawn("prefetch_bg", _work, {"account_id": account_id})
        with self._lock:
            self._prefetch_req_by_acc[account_id] = req_id

    def start_sync_headers(self, account_id: str, limit: int = DEFAULT_FETCH_LIMIT) -> tuple[bool, str, str]:
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        if not acc.enabled:
            return False, "该账号已停用", ""
        with self._lock:
            if account_id in self._syncing_accounts:
                return False, "该账号正在同步中", ""
        # 取消同账号低优先级后台预取，保证不并发打开重复会话
        self._cancel_account_prefetch(account_id)

        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            acc.last_error = "缺少授权码，请重新连接"
            self._save_to_config()
            self.account_state_changed.emit(account_id)
            return False, acc.last_error, ""

        with self._lock:
            if account_id in self._syncing_accounts:
                return False, "该账号正在同步中", ""
            self._syncing_accounts.add(account_id)

        def _has_cached(uv: str, uid: str) -> bool:
            return self.get_cached_body(account_id, uid, uv) is not None

        def _work(cancel_ev):
            ok, hdrs, bodies, uv, msg = fetch_headers_and_prefetch_bodies(
                acc,
                secret,
                limit=limit,
                immediate_count=IMMEDIATE_PREFETCH_COUNT,
                has_cached_fn=_has_cached,
                timeout=self._timeout_s,
                cancel_event=cancel_ev,
                client_factory=self._client_factory,
            )
            return ok, msg, {"headers": hdrs, "bodies": bodies, "uid_validity": uv}

        return True, "", self._spawn("sync", _work, {"account_id": account_id})

    def start_fetch_body(self, account_id: str, uid: str) -> tuple[bool, str, str]:
        """后台线程按账号和 UID 获取正文（不同账号各自连接，支持取消与超时）。"""
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            return False, "缺少授权码，请重新连接", ""

        u = str(uid)

        def _work(cancel_ev):
            ok, body, msg = fetch_message_body(
                acc, secret, u, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {"body": body}

        return True, "", self._spawn("fetch_body", _work, {"account_id": account_id, "uid": u})

    def start_mark_seen_remote(self, account_id: str, uid: str) -> tuple[bool, str, str]:
        """仅当用户明确开启服务器同步时，在后台向 IMAP 服务器回写 \\Seen 已读标记。"""
        if not bool(self._cfg.get("mail.sync_read_to_server", False)):
            return False, "未开启服务器已读同步", ""
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            return False, "缺少授权码，无法同步已读状态", ""

        u = str(uid)

        def _work(cancel_ev):
            ok, msg = mark_message_seen_remote(
                acc, secret, u, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {}

        return True, "", self._spawn("mark_seen_remote", _work, {"account_id": account_id, "uid": u})
