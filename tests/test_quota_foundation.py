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


def test_deepseek_timeout_and_network_errors():
    # 超时与断网
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


def test_workbuddy_adapter_parse_errors_refresh_and_local_connect(tmp_path):
    from wisland.services.quota.adapters.workbuddy import (
        connect_workbuddy_interactive,
        fetch_workbuddy_quota,
        parse_workbuddy_summary,
        workbuddy_slot,
    )

    # 1) 正常解析：保留最多两位小数（禁止 239.5900415），支持资源包分类与套餐名
    ok_payload = {
        "code": 0,
        "data": {
            "SubscriptionPackageName": "Pro",
            "Packages": [{"PackageCode": "pkg_1", "CycleRemainCapacity": "239.5900415"}],
            "CreditGroups": [
                {"Kind": "plan_base", "CycleRemainCapacity": 100.0},
                {"Kind": "benefit", "CycleRemainCapacity": "139.5900415"},
                {"Kind": "addon", "CycleRemainCapacity": 0},
            ],
        },
    }
    snap_ok = parse_workbuddy_summary(ok_payload, account_id="wb1")
    assert snap_ok.status == AccountStatus.OK.value
    assert snap_ok.credits == 239.59
    assert snap_ok.plan_name == "Pro"
    assert [(w.label, w.amount) for w in snap_ok.windows] == [("套餐", 100.0), ("赠送", 139.59)]

    # 2) 接口缺少资源包时不得返回 0 积分
    bad_snap = parse_workbuddy_summary({"code": 0, "data": {"Packages": []}}, account_id="wb1")
    assert bad_snap.status == AccountStatus.PARSE_ERROR.value
    assert bad_snap.credits is None

    # 3) 401 -> 触发 refreshToken 续期并更新会话；403 不误报 AUTH_ERROR；429 -> RATE_LIMITED
    state = {"step": 0}

    def _opener_refresh(req, timeout=10):
        if "/v2/plugin/auth/token/refresh" in req.full_url:
            return _FakeResp(200, json.dumps({"code": 0, "data": {"accessToken": "tok-new", "refreshToken": "ref-new"}}).encode("utf-8"))
        state["step"] += 1
        if state["step"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        return _FakeResp(200, json.dumps(ok_payload).encode("utf-8"))

    sess = {"origin": "https://www.codebuddy.cn", "auth": {"accessToken": "tok-old", "refreshToken": "ref-old", "uid": "u1"}}
    snap_ref, updated = fetch_workbuddy_quota("wb1", json.dumps(sess), http_opener=_opener_refresh)
    assert snap_ref.status == AccountStatus.OK.value
    assert updated is not None and updated["auth"]["accessToken"] == "tok-new"

    def _opener_403(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    snap_403, _ = fetch_workbuddy_quota("wb1", json.dumps(sess), http_opener=_opener_403)
    assert snap_403.status == AccountStatus.NETWORK_ERROR.value

    def _opener_429(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    snap_429, _ = fetch_workbuddy_quota("wb1", json.dumps(sess), http_opener=_opener_429)
    assert snap_429.status == AccountStatus.RATE_LIMITED.value


def test_workbuddy_local_desktop_connect(tmp_path):
    from wisland.services.quota.adapters.workbuddy import (
        connect_workbuddy_interactive,
        workbuddy_slot,
    )

    ok_payload = {
        "code": 0,
        "data": {
            "SubscriptionPackageName": "Pro",
            "Packages": [{"PackageCode": "pkg_1", "CycleRemainCapacity": "239.5900415"}],
        },
    }
    # 本地桌面登录文件只读读取（正常个人账号）
    info_file = tmp_path / "workbuddy-desktop-ai.info"
    info_file.write_text(json.dumps({
        "account": {"uid": "personal-uid", "isCurrentOneIdEnterprise": False},
        "auth": {"accessToken": "local-tok", "domain": "codebuddy.cn"},
    }), encoding="utf-8")
    ok_conn, err_conn, slot_id, _sess_conn, snap_conn = connect_workbuddy_interactive(
        account_id="wb1",
        local_info_path=info_file,
        http_opener=lambda req, timeout=10: _FakeResp(200, json.dumps(ok_payload).encode("utf-8")),
    )
    assert ok_conn and err_conn == ""
    assert slot_id == workbuddy_slot("personal-uid")
    assert snap_conn is not None and snap_conn.credits == 239.59


def test_codex_adapter_parse_extra_buckets_errors_and_local_auth(tmp_path):
    from wisland.services.quota.adapters.codex import (
        fetch_codex_usage,
        parse_codex_usage,
        read_codex_auth,
    )

    # 1) 正常解析 5 小时 (18000s)、本周 (604800s) 与 additional_rate_limits 额外额度
    payload = {
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {"used_percent": 23, "limit_window_seconds": 18000, "reset_at": 1900000000},
            "secondary_window": {"used_percent": 97, "limit_window_seconds": 604800, "reset_at": 1900500000},
        },
        "additional_rate_limits": [
            {
                "limit_name": "reserve",
                "rate_limit": {"primary_window": {"used_percent": 80, "limit_window_seconds": 604800}},
            }
        ],
    }
    snap = parse_codex_usage(payload, account_id="cx1")
    assert snap.status == AccountStatus.OK.value
    assert snap.plan_name == "plus"
    main_w = [w for w in snap.windows if w.currency != "EXTRA"]
    extra_w = [w for w in snap.windows if w.currency == "EXTRA"]
    assert [(w.label, w.remaining_percent) for w in main_w] == [("5 小时", 77.0), ("本周", 3.0)]
    assert [(w.label, w.remaining_percent) for w in extra_w] == [("reserve · 本周", 20.0)]

    # 2) 缺少周期时不得绘制假进度
    bad = parse_codex_usage({"rate_limit": {}}, account_id="cx1")
    assert bad.status == AccountStatus.PARSE_ERROR.value
    assert not bad.windows

    bad2 = parse_codex_usage({"rate_limit": {"primary_window": {"reset_at": 1900000000}}}, account_id="cx1")
    assert bad2.status == AccountStatus.PARSE_ERROR.value

    # 3) 只读读取 auth.json 并携带 Authorization + ChatGPT-Account-Id
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "tok-cx", "account_id": "acc-123"}}), encoding="utf-8")
    ok_r, _, auth_obj, _ = read_codex_auth(auth_file)
    assert ok_r and auth_obj["access_token"] == "tok-cx"

    seen_hdrs = {}

    def _opener_ok(req, timeout=10):
        seen_hdrs.update({k.lower(): v for k, v in req.header_items()})
        return _FakeResp(200, json.dumps(payload).encode("utf-8"))

    snap_ok, _ = fetch_codex_usage("cx1", secret_json=json.dumps(auth_obj), http_opener=_opener_ok)
    assert snap_ok.status == AccountStatus.OK.value
    assert seen_hdrs.get("authorization") == "Bearer tok-cx"
    assert seen_hdrs.get("chatgpt-account-id") == "acc-123"

    # 4) 401 -> AUTH_ERROR; 429 -> RATE_LIMITED
    snap_401, _ = fetch_codex_usage(
        "cx1",
        secret_json=json.dumps(auth_obj),
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)),
    )
    assert snap_401.status == AccountStatus.AUTH_ERROR.value

    snap_429, _ = fetch_codex_usage(
        "cx1",
        secret_json=json.dumps(auth_obj),
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)),
    )
    assert snap_429.status == AccountStatus.RATE_LIMITED.value


def test_gemini_adapter_two_pools_missing_pool_redirect_and_errors():
    from wisland.services.quota.adapters.gemini import (
        GEMINI_ENDPOINT,
        decode_gemini_response,
        fetch_gemini_usage,
        is_safe_gemini_redirect,
        validate_gemini_cookie,
        validate_gemini_email,
    )

    ok_e, norm_e = validate_gemini_email(" User@Example.COM ")
    assert ok_e and norm_e == "user@example.com"
    assert not validate_gemini_cookie("foo=bar")[0]
    assert validate_gemini_cookie("Cookie: SID=abc; SAPISID=xyz")[0]

    # 同站重定向允许，跨域重定向绝对禁止
    cur = f"{GEMINI_ENDPOINT}?authuser=user@example.com"
    assert is_safe_gemini_redirect(cur, "?authuser=0") is not None
    assert is_safe_gemini_redirect(cur, "https://accounts.google.com/signin") is None
    assert is_safe_gemini_redirect(cur, "https://gemini.google.com.evil.com/") is None

    # 1) 双池解析 + XSSI 前缀
    body_2 = ")]}'\n" + json.dumps([
        {
            "data": {
                "viewer": {
                    "rpcs": {
                        "getUsageInfo": {
                            "subscriptionTier": "TIER1",
                            "pools": [
                                {"poolType": "GXU_FIVE_HOURLY", "percentageUsed": 0.24, "gxu": {"windowRefreshesAt": "2030-01-01T08:00:00Z"}},
                                {"poolType": "GXU_WEEKLY", "percentageUsed": 0.10, "gxu": {"windowRefreshesAt": "2030-01-04T03:00:00Z"}},
                            ],
                        }
                    }
                }
            }
        }
    ])
    snap2 = decode_gemini_response(body_2, account_id="gm1")
    assert snap2.status == AccountStatus.OK.value
    assert snap2.plan_name == "AI Pro"
    assert [(w.label, w.remaining_percent) for w in snap2.windows] == [("5 小时", 76.0), ("本周", 90.0)]

    # 2) 缺一个池只显示存在的另一个池；两池都缺则 PARSE_ERROR
    body_1 = json.dumps([{"data": {"viewer": {"rpcs": {"getUsageInfo": {"pools": [{"poolType": "GXU_WEEKLY", "percentageUsed": 0.5}]}}}}}])
    snap1 = decode_gemini_response(body_1, account_id="gm1")
    assert snap1.status == AccountStatus.OK.value
    assert [(w.label, w.remaining_percent) for w in snap1.windows] == [("本周", 50.0)]

    body_0 = json.dumps([{"data": {"viewer": {"rpcs": {"getUsageInfo": {"pools": []}}}}}])
    assert decode_gemini_response(body_0, account_id="gm1").status == AccountStatus.PARSE_ERROR.value
    assert decode_gemini_response("<html>login</html>", account_id="gm1").status == AccountStatus.AUTH_ERROR.value

    # 3) 401 -> AUTH_ERROR; 429 -> RATE_LIMITED
    sess_json = json.dumps({"email": "user@example.com", "cookie": "SID=synthetic"})
    assert fetch_gemini_usage(
        "gm1", sess_json,
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)),
    ).status == AccountStatus.AUTH_ERROR.value
    assert fetch_gemini_usage(
        "gm1", sess_json,
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)),
    ).status == AccountStatus.RATE_LIMITED.value


def test_grok_bot_adapter_parse_errors_and_token_renew():
    import base64
    from wisland.services.quota.adapters.grok_bot import (
        fetch_grok_usage,
        grok_checksum,
        grok_pkce_challenge,
        grok_slot_of_token,
        parse_grok_usage,
    )

    assert grok_pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert grok_checksum("synthetic-machine", 1_790_000_000_000) == "9cbI1oq_synthetic-machine"

    # 1) 正常解析本周/共享周额度
    snap = parse_grok_usage(
        {
            "usagePercent": 19.15,
            "hasNonZeroIncludedLimit": True,
            "currentPeriodStart": "2030-01-01T10:00:00Z",
            "nextResetTimestampUtc": "2030-01-08T10:00:00Z",
            "grokPlanLabel": "Super",
        },
        account_id="gk1",
    )
    assert snap.status == AccountStatus.OK.value
    assert snap.plan_name == "Super"
    assert snap.windows[0].label == "本周"
    assert abs(snap.windows[0].remaining_percent - 80.8) <= 0.1

    snap_uni = parse_grok_usage({"usagePercent": 0, "isUnified": True}, account_id="gk1")
    assert snap_uni.windows[0].label == "共享周额度" and snap_uni.windows[0].remaining_percent == 100.0

    # 企业共享额度或无套餐额度不得解析为可用
    assert parse_grok_usage({"usagePercent": 0, "usesPooledEnterpriseAllowance": True}, account_id="gk1").status == AccountStatus.PARSE_ERROR.value
    assert parse_grok_usage({"usagePercent": 0, "hasNonZeroIncludedLimit": False}, account_id="gk1").status == AccountStatus.PARSE_ERROR.value

    # 2) 401 -> AUTH_ERROR; 403 -> NETWORK_ERROR (不误报 401); 429 -> RATE_LIMITED
    def _make_jwt(sub: str, exp: int) -> str:
        pl = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": exp}).encode()).rstrip(b"=").decode()
        return f"e30.{pl}.sig"

    tok_old = _make_jwt("user-sub", int(time.time()) + 3600)
    tok_new = _make_jwt("user-sub", int(time.time()) + 7200)
    assert grok_slot_of_token(tok_old) == grok_slot_of_token(tok_new)

    sess_no_ref = json.dumps({"access_token": tok_old})
    s401, _ = fetch_grok_usage(
        "gk1", sess_no_ref,
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)),
    )
    assert s401.status == AccountStatus.AUTH_ERROR.value

    s403, _ = fetch_grok_usage(
        "gk1", sess_no_ref,
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)),
    )
    assert s403.status == AccountStatus.NETWORK_ERROR.value
    assert "403" in s403.last_error

    s429, _ = fetch_grok_usage(
        "gk1", sess_no_ref,
        http_opener=lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)),
    )
    assert s429.status == AccountStatus.RATE_LIMITED.value

    # 3) 401 时使用 refresh_token 自动续期并返回新会话
    calls = {"n": 0}

    def _opener_renew(req, timeout=10):
        if "/oauth/token" in req.full_url:
            return _FakeResp(200, json.dumps({"access_token": tok_new, "refresh_token": "ref-2"}).encode("utf-8"))
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        return _FakeResp(200, json.dumps({"usagePercent": 25, "grokPlanLabel": "Super"}).encode("utf-8"))

    sess_with_ref = json.dumps({"access_token": tok_old, "refresh_token": "ref-1"})
    s_ok, updated = fetch_grok_usage("gk1", sess_with_ref, http_opener=_opener_renew)
    assert s_ok.status == AccountStatus.OK.value
    assert s_ok.windows[0].remaining_percent == 75.0
    assert updated is not None and updated["access_token"] == tok_new



def test_credentials_chunking_manifest_atomicity_and_winerror():
    from wisland.services.quota.credentials import WindowsQuotaCredentialStore

    class _MockWinCredStore(WindowsQuotaCredentialStore):
        def __init__(self):
            super().__init__()
            self.blobs: dict[str, str] = {}
            self.fail_on_call: int | None = None
            self.write_calls = 0

        def _write_single(self, target: str, username: str, secret: str) -> bool:
            self.write_calls += 1
            if self.fail_on_call is not None and self.write_calls == self.fail_on_call:
                self.last_error = 87
                self.last_error_message = "WinError 87: 参数错误。"
                return False
            # 强制检查单条写入必须小于 Windows 2560 字节上限
            assert len(secret.encode("utf-16-le")) <= 2560
            self.blobs[target] = secret
            return True

        def _read_single(self, target: str) -> str | None:
            return self.blobs.get(target)

        def _delete_single(self, target: str) -> bool:
            self.blobs.pop(target, None)
            return True

    store = _MockWinCredStore()
    target = "Wisland/quota/test_chunk_acc"

    # 1) 小内容走单条凭据
    assert store.save_secret(target, "user", "sk-small-123") is True
    assert store.read_secret(target) == "sk-small-123"
    assert len(store.blobs) == 1

    # 2) 超过 6KB 的大内容自动分块保存并完整还原
    big_secret = json.dumps({"email": "u@example.com", "cookie": "SID=" + ("x" * 7000)})
    assert len(big_secret.encode("utf-8")) > 6000
    assert store.save_secret(target, "user", big_secret) is True
    assert store.read_secret(target) == big_secret
    manifest_raw = store.blobs[target]
    assert "x" * 100 not in manifest_raw  # manifest 不包含原 secret
    assert f"{target}/chunk/0" in store.blobs

    # 3) 分块中途失败时：清理本次新分块、记录 WinError、绝不覆盖旧有效凭据
    store.write_calls = 0
    store.fail_on_call = 2
    newer_big = json.dumps({"email": "u@example.com", "cookie": "SID=" + ("y" * 7000)})
    assert store.save_secret(target, "user", newer_big) is False
    assert store.last_error == 87
    assert store.read_secret(target) == big_secret  # 旧有效凭据完好无损

    # 4) 删除同时清理 manifest 和全部分块
    store.fail_on_call = None
    assert store.delete_secret(target) is True
    assert store.read_secret(target) is None
    assert store.blobs == {}


def test_manager_missing_vs_corrupt_vs_auth_error_and_refresh_save_fail(qt_app, tmp_path):
    cm = Config(path=tmp_path / "config.json")
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")

    acc_gm = QuotaAccount(
        id="gm_miss",
        provider=Provider.GEMINI.value,
        alias="Gemini",
        identity="gemini:1",
        credential_key=make_quota_credential_key("gm_miss"),
    )
    acc_wb = QuotaAccount(
        id="wb_ok",
        provider=Provider.WORKBUDDY.value,
        alias="WorkBuddy",
        identity="wb:1",
        credential_key=make_quota_credential_key("wb_ok"),
    )
    cm.set("quota.accounts", [acc_gm.to_config_dict(), acc_wb.to_config_dict()])

    mode = {"wb_401": False}

    def _opener(req, timeout=10):
        if "gemini.google.com" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        if "/v2/plugin/auth/token/refresh" in req.full_url:
            return _FakeResp(
                200,
                json.dumps({"code": 0, "data": {"accessToken": "tok-new", "refreshToken": "ref-new"}}).encode("utf-8"),
            )
        if mode["wb_401"]:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        return _FakeResp(
            200,
            json.dumps({
                "code": 0,
                "data": {"Packages": [{"PackageCode": "p1", "CycleRemainCapacity": 100.0}]},
            }).encode("utf-8"),
        )

    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=_opener)

    # 1) 凭据缺失 (read_secret -> None)
    assert mgr.refresh_account("gm_miss") is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("gm_miss"))
    s_miss = mgr.get_snapshot("gm_miss")
    assert s_miss.status == AccountStatus.AUTH_ERROR.value
    assert "未找到已保存的会话凭据，请重新保存" in s_miss.last_error

    # 2) 凭据损坏 (JSON 损坏)
    creds.save_secret(acc_gm.credential_key, "gm_miss", "{not-json")
    assert mgr.refresh_account("gm_miss") is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("gm_miss"))
    s_bad = mgr.get_snapshot("gm_miss")
    assert s_bad.status == AccountStatus.AUTH_ERROR.value
    assert "保存的会话凭据损坏，请重新连接" in s_bad.last_error

    # 3) 凭据存在且服务端 403 -> Gemini 登录已失效
    creds.save_secret(acc_gm.credential_key, "gm_miss", json.dumps({"email": "u@example.com", "cookie": "SID=abc"}))
    assert mgr.refresh_account("gm_miss") is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("gm_miss"))
    s_403 = mgr.get_snapshot("gm_miss")
    assert s_403.status == AccountStatus.AUTH_ERROR.value
    assert "Gemini 登录已失效" in s_403.last_error

    # 4) WorkBuddy 正常刷新后，若 token 续期保存失败：保留旧缓存数据且不误报 AUTH_ERROR
    wb_sess = {"origin": "https://www.codebuddy.cn", "auth": {"accessToken": "tok-old", "refreshToken": "ref-old", "uid": "u1"}}
    creds.save_secret(acc_wb.credential_key, "wb:1", json.dumps(wb_sess))
    assert mgr.refresh_account("wb_ok") is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("wb_ok"))
    assert mgr.get_snapshot("wb_ok").credits == 100.0

    # 模拟续期后写入凭据失败
    mode["wb_401"] = True
    orig_save = creds.save_secret

    def _fail_save(target, username, secret):
        creds.last_error = 5
        return False

    creds.save_secret = _fail_save  # type: ignore[method-assign]
    assert mgr.refresh_account("wb_ok") is True
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("wb_ok"))
    creds.save_secret = orig_save  # type: ignore[method-assign]

    s_keep = mgr.get_snapshot("wb_ok")
    assert s_keep.status == AccountStatus.STALE.value  # 绝不误报 AUTH_ERROR
    assert s_keep.credits == 100.0
    assert "保存失败" in s_keep.last_error
    mgr.stop()


def test_gemini_browser_launcher_exit_and_app_url_fallback(monkeypatch):
    """验证 Gemini 浏览器启动器进程提前退出但 CDP 端口存活、且登录后停留在 /app 时仍能正确提取并验证额度。"""
    import wisland.services.quota.adapters.gemini as gm

    class _FakeProc:
        def poll(self):
            return 0  # 模拟 Windows Edge/Chrome 启动器进程已转交窗口并退出

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class _FakeCdp:
        def __init__(self, ws_url, timeout_s=3.0):
            self.ws_url = ws_url

        def call(self, method, params=None, cancel_event=None, timeout_s=15.0):
            if method == "Runtime.evaluate":
                return {"result": {"value": "gemini.user@gmail.com"}}
            if method == "Network.getCookies":
                return {
                    "cookies": [
                        {"domain": ".google.com", "name": "__Secure-1PSID", "value": "sid-ok"},
                        {"domain": ".google.com", "name": "SID", "value": "sid-1"},
                    ]
                }
            return {}

        def close(self):
            pass

    monkeypatch.setattr(gm, "find_chromium_browser", lambda: Path("C:/Fake/msedge.exe"))
    monkeypatch.setattr(gm.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(gm, "_LocalCdpSocket", _FakeCdp)
    monkeypatch.setattr(
        gm.urllib.request,
        "urlopen",
        lambda url, timeout=1.5: _FakeResp(
            200,
            json.dumps([
                {
                    "type": "page",
                    "url": "https://gemini.google.com/app",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9999/devtools/page/1",
                }
            ]).encode("utf-8"),
        ),
    )

    valid_payload = [
        {
            "data": {
                "viewer": {
                    "rpcs": {
                        "getUsageInfo": {
                            "pools": [
                                {"poolType": "GXU_FIVE_HOURLY", "percentageUsed": 0.2, "gxu": {"windowRefreshesAt": "2026-11-20T12:00:00Z"}}
                            ]
                        }
                    }
                }
            }
        }
    ]
    ok, err, sess, snap = gm.connect_gemini_browser(
        account_id="gm_test",
        http_opener=lambda req, timeout=12.0: _FakeResp(200, json.dumps(valid_payload).encode("utf-8")),
        timeout_s=5.0,
    )
    assert ok is True
    assert err == ""
    assert sess is not None and sess["email"] == "gemini.user@gmail.com"
    assert snap is not None and snap.status == AccountStatus.OK.value
