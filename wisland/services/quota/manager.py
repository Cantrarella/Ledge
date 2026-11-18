# -*- coding: utf-8 -*-
"""AI 额度后台管理服务（QuotaManager）：异步连接测试、并发防重刷新、速率退避、旧快照保留与生命周期管理。"""
from __future__ import annotations

import concurrent.futures
import copy
import time
import uuid
from pathlib import Path

from PySide6 import QtCore

from ...core.config import cfg as global_cfg
from .adapters.deepseek import DEEPSEEK_BALANCE_URL, DEFAULT_TIMEOUT_S, fetch_deepseek_balance
from .cache import LocalQuotaCache
from .credentials import (
    WindowsQuotaCredentialStore,
    derive_secret_identity,
    make_quota_credential_key,
)
from .models import (
    AccountStatus,
    Provider,
    QuotaAccount,
    QuotaSnapshot,
    VALID_PROVIDERS,
)
from .providers import get_provider, is_provider_implemented, is_valid_provider

RATE_LIMIT_BACKOFF_S = 60.0


class QuotaManager(QtCore.QObject):
    """AI 额度核心后台管理器：所有网络请求均在后台线程池执行，绝不阻塞 UI 线程。"""

    accounts_changed = QtCore.Signal()
    snapshot_updated = QtCore.Signal(str, object)       # account_id, QuotaSnapshot
    account_state_changed = QtCore.Signal(str)          # account_id
    refreshing_changed = QtCore.Signal(str, bool)       # account_id, is_refreshing
    all_refreshing_changed = QtCore.Signal(bool)        # any_refreshing
    notice = QtCore.Signal(str, bool)                   # message, is_error
    test_finished = QtCore.Signal(str, bool, str, object)  # req_id, ok, err_msg, payload

    _internal_refresh_done = QtCore.Signal(str, object)
    _internal_test_done = QtCore.Signal(str, bool, str, object)

    def __init__(
        self,
        config_obj=None,
        cred_store=None,
        cache: LocalQuotaCache | None = None,
        http_opener=None,
        parent=None,
    ):
        super().__init__(parent)
        self._cfg = config_obj or global_cfg
        self._creds = cred_store or WindowsQuotaCredentialStore()
        self._cache = cache or LocalQuotaCache()
        self._http_opener = http_opener

        self._accounts: list[QuotaAccount] = []
        self._snapshots: dict[str, QuotaSnapshot] = {}
        self._refreshing_ids: set[str] = set()
        self._rate_limit_until: dict[str, float] = {}
        self._active_futures: dict[str, concurrent.futures.Future] = {}
        self._stopping = False
        self._started = False

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="wisland-quota"
        )

        self._auto_timer = QtCore.QTimer(self)
        self._auto_timer.timeout.connect(self._on_auto_timer)

        self._internal_refresh_done.connect(self._on_refresh_done_main)
        self._internal_test_done.connect(self._on_test_done_main)

        self.load_from_config()
        # 启动时加载本地旧缓存并标记为 stale ("数据已过期")
        cached = self._cache.load_startup_snapshots()
        for acc in self._accounts:
            if acc.id in cached:
                self._snapshots[acc.id] = cached[acc.id]
            else:
                self._snapshots[acc.id] = self._initial_snapshot_for(acc)

        if hasattr(self._cfg, "changed"):
            try:
                self._cfg.changed.connect(self._on_config_changed)
            except Exception:
                pass

    def _initial_snapshot_for(self, acc: QuotaAccount) -> QuotaSnapshot:
        if not is_provider_implemented(acc.provider):
            st = AccountStatus.UNSUPPORTED.value
        elif not acc.enabled:
            st = AccountStatus.DISABLED.value
        elif acc.last_error:
            st = AccountStatus.NETWORK_ERROR.value
        elif acc.last_refresh_at > 0:
            st = AccountStatus.STALE.value
        else:
            st = AccountStatus.UNCONFIGURED.value
        return QuotaSnapshot(
            account_id=acc.id,
            fetched_at=acc.last_refresh_at,
            status=st,
            last_error=acc.last_error,
            raw_stale=(st == AccountStatus.STALE.value),
        )

    # ---------------- 配置加载与同步 ----------------
    def load_from_config(self):
        raw_list = self._cfg.get("quota.accounts", [])
        loaded: list[QuotaAccount] = []
        seen_ids: set[str] = set()
        seen_dedupe: set[tuple[str, str, str]] = set()
        if isinstance(raw_list, list):
            for item in raw_list:
                acc = QuotaAccount.from_config_dict(item)
                if acc is None:
                    continue
                if acc.id in seen_ids:
                    continue
                dkey = acc.dedupe_key()
                if dkey[1] and dkey in seen_dedupe:
                    continue
                seen_ids.add(acc.id)
                if dkey[1]:
                    seen_dedupe.add(dkey)
                loaded.append(acc)
        self._accounts = loaded
        for acc in self._accounts:
            if acc.id not in self._snapshots:
                cached = self._cache.get(acc.id)
                if cached is not None:
                    self._snapshots[acc.id] = cached
                else:
                    self._snapshots[acc.id] = self._initial_snapshot_for(acc)
            else:
                snap = self._snapshots[acc.id]
                if not is_provider_implemented(acc.provider):
                    snap.status = AccountStatus.UNSUPPORTED.value
                elif not acc.enabled and snap.status not in (
                    AccountStatus.AUTH_ERROR.value,
                    AccountStatus.NETWORK_ERROR.value,
                    AccountStatus.PARSE_ERROR.value,
                    AccountStatus.RATE_LIMITED.value,
                ):
                    snap.status = AccountStatus.DISABLED.value

    def _save_accounts_to_config(self):
        self._cfg.set("quota.accounts", [a.to_config_dict() for a in self._accounts])

    @QtCore.Slot()
    def _on_config_changed(self):
        self.load_from_config()
        self._sync_auto_timer()
        self.accounts_changed.emit()

    # ---------------- 账号查询与管理 ----------------
    def list_accounts(self) -> list[QuotaAccount]:
        return [copy.deepcopy(a) for a in self._accounts]

    def get_account(self, account_id: str) -> QuotaAccount | None:
        for a in self._accounts:
            if a.id == account_id:
                return copy.deepcopy(a)
        return None

    def get_snapshot(self, account_id: str) -> QuotaSnapshot | None:
        snap = self._snapshots.get(str(account_id or ""))
        return copy.deepcopy(snap) if snap is not None else None

    def is_refreshing(self, account_id: str | None = None) -> bool:
        if account_id is None:
            return bool(self._refreshing_ids)
        return str(account_id) in self._refreshing_ids

    def has_duplicate(
        self,
        provider: str,
        identity: str,
        source: str,
        exclude_id: str = "",
        extra_accounts: list[QuotaAccount] | None = None,
    ) -> bool:
        target = (
            str(provider or "").strip().lower(),
            str(identity or "").strip().lower(),
            str(source or "").strip().lower(),
        )
        if not target[1]:
            return False
        pool = list(self._accounts) + list(extra_accounts or [])
        for acc in pool:
            if exclude_id and acc.id == exclude_id:
                continue
            if acc.dedupe_key() == target:
                return True
        return False

    def update_account(self, account: QuotaAccount, persist: bool = True) -> bool:
        if not isinstance(account, QuotaAccount) or account.provider not in VALID_PROVIDERS:
            return False
        for idx, acc in enumerate(self._accounts):
            if acc.id == account.id:
                self._accounts[idx] = copy.deepcopy(account)
                if not account.enabled and account.id in self._snapshots:
                    self._snapshots[account.id].status = AccountStatus.DISABLED.value
                if persist:
                    self._save_accounts_to_config()
                self.account_state_changed.emit(account.id)
                self.accounts_changed.emit()
                return True
        return False

    def remove_account_after_commit(self, account_id: str, credential_key: str = ""):
        """在配置原子写入成功后删除对应凭据与本地额度缓存。"""
        acc_id = str(account_id or "").strip()
        if not acc_id:
            return
        cred_key = str(credential_key or "").strip() or make_quota_credential_key(acc_id)
        self._creds.delete_secret(cred_key)
        self._cache.remove(acc_id, flush=True)
        self._snapshots.pop(acc_id, None)
        self._rate_limit_until.pop(acc_id, None)
        self._accounts = [a for a in self._accounts if a.id != acc_id]
        self.accounts_changed.emit()

    # ---------------- 异步测试与添加 ----------------
    def test_account_async(
        self,
        provider: str,
        secret: str,
        alias: str = "",
        source: str = "",
        account_id: str = "",
        check_duplicate_in: list[QuotaAccount] | None = None,
    ) -> tuple[bool, str, str]:
        """后台测试账号连接；成功前绝不写入凭据。返回 (是否启动, 错误信息, req_id)。"""
        if self._stopping:
            return False, "服务正在停止", ""
        prov = str(provider or "").strip().lower()
        if not is_valid_provider(prov):
            return False, f"不支持的服务商：{provider}", ""
        if not is_provider_implemented(prov):
            return False, "连接器将在下一阶段接入", ""
        clean_secret = str(secret or "").strip()
        if not clean_secret:
            return False, "API Key 不能为空", ""

        spec = get_provider(prov)
        src = str(source or spec.default_source).strip() or spec.default_source
        ident = derive_secret_identity(prov, clean_secret)

        if self.has_duplicate(prov, ident, src, exclude_id=account_id, extra_accounts=check_duplicate_in):
            return False, f"该 {spec.name} 账号已存在，请勿重复添加", ""

        req_id = f"qtest-{uuid.uuid4().hex[:10]}"
        acc_id = str(account_id or uuid.uuid4().hex).strip()
        url = src if src.startswith("http") else DEEPSEEK_BALANCE_URL

        def _worker():
            snap = fetch_deepseek_balance(
                api_key=clean_secret,
                account_id=acc_id,
                url=url,
                timeout_s=DEFAULT_TIMEOUT_S,
                http_opener=self._http_opener,
            )
            ok = snap.status == AccountStatus.OK.value
            err = "" if ok else (snap.last_error or "连接测试失败")
            now_ts = time.time()
            cand_acc = QuotaAccount(
                id=acc_id,
                provider=prov,
                alias=str(alias or "").strip() or spec.name,
                identity=ident,
                source=src,
                credential_key=make_quota_credential_key(acc_id),
                enabled=True,
                pinned=False,
                color=spec.default_color,
                created_at=now_ts,
                last_test_at=now_ts,
                last_refresh_at=now_ts if ok else 0.0,
                last_error=err,
            )
            payload = {"account": cand_acc, "snapshot": snap, "secret": clean_secret}
            if not self._stopping:
                self._internal_test_done.emit(req_id, ok, err, payload)

        fut = self._executor.submit(_worker)
        self._active_futures[req_id] = fut
        return True, "", req_id

    def add_account_async(
        self,
        provider: str,
        secret: str,
        alias: str = "",
        source: str = "",
        commit_immediately: bool = False,
    ) -> tuple[bool, str, str]:
        """异步测试并准备添加账号；当 commit_immediately=True 时测试成功后写入凭据与配置。"""
        ok, err, req_id = self.test_account_async(provider=provider, secret=secret, alias=alias, source=source)
        if ok and commit_immediately:
            self._active_futures[f"commit:{req_id}"] = self._active_futures[req_id]
        return ok, err, req_id

    @QtCore.Slot(str, bool, str, object)
    def _on_test_done_main(self, req_id: str, ok: bool, err: str, payload: object):
        self._active_futures.pop(req_id, None)
        commit_now = self._active_futures.pop(f"commit:{req_id}", None) is not None
        if ok and isinstance(payload, dict):
            acc: QuotaAccount = payload["account"]
            snap: QuotaSnapshot = payload["snapshot"]
            sec: str = payload.get("secret", "")
            self._snapshots[acc.id] = snap
            if commit_now and sec:
                if self._creds.save_secret(acc.credential_key, acc.identity or acc.id, sec):
                    self._accounts.append(acc)
                    self._save_accounts_to_config()
                    self._cache.put(snap, flush=True)
                    self.accounts_changed.emit()
                    self.snapshot_updated.emit(acc.id, copy.deepcopy(snap))
                else:
                    ok = False
                    err = "写入 Windows 凭据管理器失败"
        self.test_finished.emit(req_id, ok, err, payload)

    # ---------------- 刷新逻辑（单账号 & 全部） ----------------
    def refresh_account(self, account_id: str) -> bool:
        """后台刷新指定账号：同一账号不并发发起重复刷新。"""
        if self._stopping:
            return False
        acc_id = str(account_id or "").strip()
        acc = next((a for a in self._accounts if a.id == acc_id), None)
        if acc is None:
            return False
        if not is_provider_implemented(acc.provider):
            snap = QuotaSnapshot(
                account_id=acc.id,
                fetched_at=time.time(),
                status=AccountStatus.UNSUPPORTED.value,
                last_error="连接器将在下一阶段接入",
            )
            self._snapshots[acc.id] = snap
            self.snapshot_updated.emit(acc.id, copy.deepcopy(snap))
            self.account_state_changed.emit(acc.id)
            return False

        if acc_id in self._refreshing_ids:
            return False

        # 检查 429 速率限制退避窗口（至少 60 秒）
        now_m = time.monotonic()
        until_m = self._rate_limit_until.get(acc_id, 0.0)
        if until_m > now_m:
            rem_s = int( until_m - now_m ) + 1
            msg = f"请求过于频繁，请在 {rem_s} 秒后重试 (429)"
            acc.last_error = msg
            prev = self._snapshots.get(acc_id)
            if prev is not None and prev.has_valid_data():
                prev.raw_stale = True
                prev.status = AccountStatus.RATE_LIMITED.value
                prev.last_error = msg
            else:
                self._snapshots[acc_id] = QuotaSnapshot(
                    account_id=acc_id,
                    fetched_at=time.time(),
                    status=AccountStatus.RATE_LIMITED.value,
                    last_error=msg,
                )
            self.snapshot_updated.emit(acc_id, copy.deepcopy(self._snapshots[acc_id]))
            self.account_state_changed.emit(acc_id)
            return False

        was_empty = len(self._refreshing_ids) == 0
        self._refreshing_ids.add(acc_id)
        self.refreshing_changed.emit(acc_id, True)
        if was_empty:
            self.all_refreshing_changed.emit(True)
        self.account_state_changed.emit(acc_id)

        cred_key = acc.credential_key or make_quota_credential_key(acc.id)
        src_url = acc.source if acc.source.startswith("http") else DEEPSEEK_BALANCE_URL

        def _worker():
            secret = self._creds.read_secret(cred_key) or ""
            if not secret.strip():
                snap = QuotaSnapshot(
                    account_id=acc_id,
                    fetched_at=time.time(),
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error="未找到 API Key 或凭据已失效，请重新连接",
                )
            else:
                snap = fetch_deepseek_balance(
                    api_key=secret,
                    account_id=acc_id,
                    url=src_url,
                    timeout_s=DEFAULT_TIMEOUT_S,
                    http_opener=self._http_opener,
                )
            if not self._stopping:
                self._internal_refresh_done.emit(acc_id, snap)

        fut = self._executor.submit(_worker)
        self._active_futures[f"refresh:{acc_id}"] = fut
        return True

    def refresh_all(self) -> int:
        """后台并发刷新所有已启用且已实现连接器的账号，绝不阻塞 UI。"""
        if self._stopping:
            return 0
        started = 0
        for acc in list(self._accounts):
            if acc.enabled and is_provider_implemented(acc.provider):
                if self.refresh_account(acc.id):
                    started += 1
        return started

    @QtCore.Slot(str, object)
    def _on_refresh_done_main(self, account_id: str, new_snap: QuotaSnapshot):
        self._active_futures.pop(f"refresh:{account_id}", None)
        self._refreshing_ids.discard(account_id)
        self.refreshing_changed.emit(account_id, False)
        if not self._refreshing_ids:
            self.all_refreshing_changed.emit(False)

        acc = next((a for a in self._accounts if a.id == account_id), None)
        prev_snap = self._snapshots.get(account_id)
        if prev_snap is None:
            prev_snap = self._cache.get(account_id)

        if new_snap.status == AccountStatus.RATE_LIMITED.value:
            self._rate_limit_until[account_id] = time.monotonic() + RATE_LIMIT_BACKOFF_S

        if new_snap.status == AccountStatus.OK.value:
            self._rate_limit_until.pop(account_id, None)
            final_snap = new_snap
            self._cache.put(final_snap, flush=True)
            if acc is not None:
                acc.last_refresh_at = final_snap.fetched_at
                acc.last_error = ""
                self._save_accounts_to_config()
        else:
            # 失败时：若存在上一份有效快照则保留其额度数据并标记 raw_stale=True
            if prev_snap is not None and prev_snap.has_valid_data():
                kept = copy.deepcopy(prev_snap)
                kept.raw_stale = True
                kept.last_error = new_snap.last_error
                # 网络断开保留旧快照并标记为 stale；401/403 密钥失效保持 auth_error 以明确区分
                if new_snap.status == AccountStatus.NETWORK_ERROR.value:
                    kept.status = AccountStatus.STALE.value
                else:
                    kept.status = new_snap.status
                final_snap = kept
                self._cache.put(final_snap, flush=True)
            else:
                final_snap = new_snap
            if acc is not None:
                acc.last_error = new_snap.last_error
                self._save_accounts_to_config()

        self._snapshots[account_id] = final_snap
        self.snapshot_updated.emit(account_id, copy.deepcopy(final_snap))
        self.account_state_changed.emit(account_id)

    # ---------------- 生命周期与自动刷新 ----------------
    def _sync_auto_timer(self):
        enabled = bool(self._cfg.get("quota.enabled", True))
        if not self._started or self._stopping or not enabled:
            if self._auto_timer.isActive():
                self._auto_timer.stop()
            return
        mins = int(self._cfg.get("quota.refresh_minutes", 3) or 3)
        mins = max(1, min(1440, mins))
        interval_ms = mins * 60 * 1000
        if self._auto_timer.interval() != interval_ms or not self._auto_timer.isActive():
            self._auto_timer.start(interval_ms)

    @QtCore.Slot()
    def _on_auto_timer(self):
        if bool(self._cfg.get("quota.enabled", True)) and not self._stopping:
            self.refresh_all()

    def start(self):
        """启动后台自动刷新（仅在 quota.enabled 为 True 时运行）。"""
        self._stopping = False
        self._started = True
        self._sync_auto_timer()
        if bool(self._cfg.get("quota.enabled", True)):
            self.refresh_all()

    def stop(self):
        """停止定时器、取消未完成请求、关闭线程池并持久化有效缓存。"""
        self._stopping = True
        self._started = False
        if self._auto_timer.isActive():
            self._auto_timer.stop()
        for fut in list(self._active_futures.values()):
            try:
                fut.cancel()
            except Exception:
                pass
        self._active_futures.clear()
        self._refreshing_ids.clear()
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        self._cache.flush()
