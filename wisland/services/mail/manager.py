# -*- coding: utf-8 -*-
"""多账号邮件管理器：账号配置持久化（无明文凭据）、Windows 凭据管理、非阻塞 IMAP 测试与只读同步。"""
from __future__ import annotations

import threading
import time
import uuid

from PySide6 import QtCore

from ...core.config import cfg
from .credentials import WindowsCredentialStore, make_credential_key
from .imap import DEFAULT_TIMEOUT_S, fetch_recent_headers, test_imap_connection
from .models import MailAccount, MailHeader
from .providers import validate_account_params

MAX_RUNTIME_HEADERS = 60
DEFAULT_FETCH_LIMIT = 20


class MailAccountManager(QtCore.QObject):
    """管理多个邮箱账号及其独立连接状态、凭据引用与最近邮件元数据。"""

    accounts_changed = QtCore.Signal()
    account_state_changed = QtCore.Signal(str)          # account_id
    headers_updated = QtCore.Signal(str, list)          # account_id, list[MailHeader]
    task_finished = QtCore.Signal(str, bool, str)       # req_id, ok, error_msg

    # 内部跨线程投递信号（确保所有状态修改与对外信号在主线程执行）
    _internal_done = QtCore.Signal(str, str, bool, str, object)

    def __init__(
        self,
        config_obj=None,
        cred_store=None,
        client_factory=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        parent=None,
    ):
        super().__init__(parent)
        self._cfg = config_obj or cfg
        self._creds = cred_store or WindowsCredentialStore()
        self._client_factory = client_factory
        self._timeout_s = float(timeout_s)

        self._accounts: list[MailAccount] = []
        self._headers: dict[str, list[MailHeader]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

        self._internal_done.connect(self._on_task_done)
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
        self._accounts = [a for a in self._accounts if a.id != account_id]
        self._headers.pop(account_id, None)
        if acc.credential_key:
            self._creds.delete_secret(acc.credential_key)
        self._save_to_config()
        self.accounts_changed.emit()
        return True

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
            acc = self.get_account(acc_id)
            if acc is not None:
                if ok:
                    new_hdrs: list[MailHeader] = res.get("headers") or []
                    existing = {h.uid: h for h in self._headers.get(acc_id, [])}
                    for h in new_hdrs:
                        existing[h.uid] = h
                    merged = sorted(existing.values(), key=lambda x: x.received_at, reverse=True)[:MAX_RUNTIME_HEADERS]
                    self._headers[acc_id] = merged
                    acc.last_sync_at = now
                    acc.last_error = ""
                    self.headers_updated.emit(acc_id, list(merged))
                else:
                    acc.last_error = err
                self._save_to_config()
                self.account_state_changed.emit(acc_id)
                self.accounts_changed.emit()
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

    def start_sync_headers(self, account_id: str, limit: int = DEFAULT_FETCH_LIMIT) -> tuple[bool, str, str]:
        acc = self.get_account(account_id)
        if acc is None:
            return False, "账号不存在", ""
        if not acc.enabled:
            return False, "该账号已停用", ""
        secret = self._creds.read_secret(acc.credential_key)
        if not secret:
            acc.last_error = "缺少授权码，请重新连接"
            self._save_to_config()
            self.account_state_changed.emit(account_id)
            return False, acc.last_error, ""

        def _work(cancel_ev):
            ok, hdrs, msg = fetch_recent_headers(
                acc, secret, limit=limit, timeout=self._timeout_s, cancel_event=cancel_ev, client_factory=self._client_factory
            )
            return ok, msg, {"headers": hdrs}

        return True, "", self._spawn("sync", _work, {"account_id": account_id})
