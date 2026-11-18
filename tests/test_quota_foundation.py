# -*- coding: utf-8 -*-
"""AI 额度第一阶段基础服务测试（模型、配置、凭据、缓存、DeepSeek 适配器与 QuotaManager）。"""
from __future__ import annotations

import io
import json
import socket
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest
from PySide6 import QtWidgets

from wisland.core.config import Config, validate_config
from wisland.services.quota import (
    AccountStatus,
    InMemoryQuotaCredentialStore,
    LocalQuotaCache,
    PROVIDER_ORDER,
    Provider,
    QuotaAccount,
    QuotaManager,
    QuotaSnapshot,
    VALID_PROVIDERS,
    fetch_deepseek_balance,
    make_quota_credential_key,
)


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _wait_until(qt_app, pred, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    qt_app.processEvents()
    return bool(pred())


def test_01_providers_and_config_validation(tmp_path):
    assert set(VALID_PROVIDERS) == {"deepseek", "workbuddy", "gemini", "grok_bot", "codex"}
    assert list(PROVIDER_ORDER) == ["deepseek", "workbuddy", "gemini", "grok_bot", "codex"]

    with pytest.raises(ValueError):
        QuotaAccount(provider="dsh")
    with pytest.raises(ValueError):
        QuotaAccount(provider="openai")

    # 非法 refresh_minutes 在严格模式下被拒绝，非严格模式校正为 3
    ok_strict, errs_strict, _ = validate_config({"quota": {"enabled": True, "refresh_minutes": 0}}, strict=True)
    assert not ok_strict and errs_strict
    ok_strict2, _, _ = validate_config({"quota": {"enabled": True, "refresh_minutes": 1441}}, strict=True)
    assert not ok_strict2

    _, _, norm = validate_config(
        {
            "quota": {
                "enabled": True,
                "refresh_minutes": 9999,
                "compact_account_id": "old_daozai",
                "custom_future_field": "keep_me",
                "accounts": [
                    {"id": "a1", "provider": "deepseek", "api_key": "sk-must-strip", "identity": "k1"},
                    {"id": "bad", "provider": "invalid_prov", "identity": "x"},
                ],
            }
        },
        strict=False,
    )
    assert norm["quota"]["refresh_minutes"] == 3
    assert "compact_account_id" not in norm["quota"]
    assert norm["quota"]["custom_future_field"] == "keep_me"
    assert len(norm["quota"]["accounts"]) == 1
    assert "api_key" not in norm["quota"]["accounts"][0]

    # 损坏配置不覆盖原文件
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{corrupt_json", encoding="utf-8")
    c = Config(path=cfg_path)
    assert c.get("quota.refresh_minutes") == 3
    assert cfg_path.read_text(encoding="utf-8") == "{corrupt_json"


def test_02_deepseek_adapter_parsing_and_errors():
    # 1) 正常余额：优先 CNY，Decimal 解析
    payload_ok = {
        "is_available": True,
        "balance_infos": [
            {"currency": "USD", "total_balance": "12.50", "granted_balance": "2.50", "topped_up_balance": "10.00"},
            {"currency": "CNY", "total_balance": "88.88", "granted_balance": "8.88", "topped_up_balance": "80.00"},
        ],
    }
    snap = fetch_deepseek_balance(
        "sk-test-secret",
        account_id="acc1",
        http_opener=lambda req, timeout=10: _FakeResp(200, json.dumps(payload_ok).encode("utf-8")),
    )
    assert snap.status == AccountStatus.OK.value
    assert snap.primary_balance().currency == "CNY"
    assert snap.primary_balance().total_balance == pytest.approx(88.88)
    assert snap.primary_balance().granted_balance == pytest.approx(8.88)
    assert snap.primary_balance().topped_up_balance == pytest.approx(80.00)

    # 2) 空余额列表
    snap_empty = fetch_deepseek_balance(
        "sk-test-secret",
        account_id="acc1",
        http_opener=lambda req, timeout=10: _FakeResp(200, json.dumps({"is_available": False, "balance_infos": []}).encode("utf-8")),
    )
    assert snap_empty.status == AccountStatus.PARSE_ERROR.value

    # 3) 非 JSON
    snap_nonjson = fetch_deepseek_balance(
        "sk-test-secret",
        account_id="acc1",
        http_opener=lambda req, timeout=10: _FakeResp(200, b"<html>bad gateway</html>"),
    )
    assert snap_nonjson.status == AccountStatus.PARSE_ERROR.value

    # 4) 401 密钥失效（错误文本绝不泄露密钥）
    def _raise_401(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    snap_401 = fetch_deepseek_balance("sk-secret-xyz", account_id="acc1", http_opener=_raise_401)
    assert snap_401.status == AccountStatus.AUTH_ERROR.value
    assert "sk-secret-xyz" not in snap_401.last_error

    # 5) 429 频率限制
    def _raise_429(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b""))

    snap_429 = fetch_deepseek_balance("sk-secret-xyz", account_id="acc1", http_opener=_raise_429)
    assert snap_429.status == AccountStatus.RATE_LIMITED.value

    # 6) 超时与断网
    def _raise_timeout(req, timeout=10):
        raise socket.timeout("timed out")

    snap_to = fetch_deepseek_balance("sk-secret-xyz", account_id="acc1", http_opener=_raise_timeout)
    assert snap_to.status == AccountStatus.NETWORK_ERROR.value
    assert "超时" in snap_to.last_error

    def _raise_net(req, timeout=10):
        raise urllib.error.URLError("NameResolutionError")

    snap_net = fetch_deepseek_balance("sk-secret-xyz", account_id="acc1", http_opener=_raise_net)
    assert snap_net.status == AccountStatus.NETWORK_ERROR.value


def test_03_manager_creds_dedupe_stale_concurrency_and_lifecycle(qt_app, tmp_path):
    cfg_path = tmp_path / "config.json"
    cm = Config(path=cfg_path)
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")

    mode = {"net_fail": False, "slow": False}
    call_count = {"n": 0}
    gate = threading.Event()

    def _opener(req, timeout=10):
        call_count["n"] += 1
        if mode["slow"]:
            gate.wait(timeout=1.5)
        if mode["net_fail"]:
            raise urllib.error.URLError("offline")
        body = {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "42.00", "granted_balance": "2.00", "topped_up_balance": "40.00"}],
        }
        return _FakeResp(200, json.dumps(body).encode("utf-8"))

    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=_opener)
    results: list[tuple[str, bool, str]] = []
    mgr.test_finished.connect(lambda rid, ok, err, _p: results.append((rid, ok, err)))

    # 1) 异步测试并提交（使用内存凭据替身，凭据键格式 Wisland/quota/<id>）
    ok_start, err_start, rid = mgr.add_account_async("deepseek", "sk-real-key-123", alias="DS主号", commit_immediately=True)
    assert ok_start and not err_start
    assert _wait_until(qt_app, lambda: len(results) == 1)
    assert results[0][1] is True
    accs = mgr.list_accounts()
    assert len(accs) == 1
    acc = accs[0]
    assert acc.credential_key == make_quota_credential_key(acc.id)
    assert creds.read_secret(acc.credential_key) == "sk-real-key-123"

    # config.json 与 cache 中绝不出现 API Key
    cm.save()
    assert "sk-real-key-123" not in cfg_path.read_text(encoding="utf-8")
    assert "sk-real-key-123" not in (tmp_path / "quota_cache.json").read_text(encoding="utf-8")

    # 2) 重复账号被阻止
    dup_ok, dup_err, _ = mgr.test_account_async("deepseek", "sk-real-key-123", alias="重复")
    assert dup_ok is False and "已存在" in dup_err

    # 3) 断网失败保留上一份有效快照并标记 stale
    mode["net_fail"] = True
    assert mgr.refresh_account(acc.id) is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing(acc.id))
    stale_snap = mgr.get_snapshot(acc.id)
    assert stale_snap is not None
    assert stale_snap.raw_stale is True
    assert stale_snap.status == AccountStatus.STALE.value
    assert stale_snap.primary_balance().total_balance == pytest.approx(42.0)

    # 4) 同账号并发重复刷新被阻止
    mode["net_fail"] = False
    mode["slow"] = True
    gate.clear()
    before_calls = call_count["n"]
    assert mgr.refresh_account(acc.id) is True
    assert mgr.refresh_account(acc.id) is False
    gate.set()
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing(acc.id))
    assert call_count["n"] == before_calls + 1

    # 5) quota.enabled = False 时不启动自动刷新定时器
    cm.set("quota.enabled", False)
    mgr.start()
    assert not mgr._auto_timer.isActive()

    # 6) stop() 正确停止定时器与任务，删除后清理凭据和缓存
    mgr.remove_account_after_commit(acc.id, acc.credential_key)
    assert creds.read_secret(acc.credential_key) is None
    assert cache.get(acc.id) is None
    mgr.stop()
    assert not mgr._auto_timer.isActive()
