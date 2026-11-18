# -*- coding: utf-8 -*-
"""Grok Bot 额度适配器：PKCE 浏览器授权轮询、Token 续期、DashboardService/GetSandUsageStatus 请求与解析。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from typing import Callable

from ..models import AccountStatus, QuotaSnapshot, QuotaWindow

GROK_BACKEND = "https://api2.cursor.sh"
GROK_CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
GROK_USAGE_URL = f"{GROK_BACKEND}/aiserver.v1.DashboardService/GetSandUsageStatus"
GROK_DEFAULT_TIMEOUT_S = 12.0
GROK_MAX_BYTES = 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _urlsafe_b64_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def grok_pkce_challenge(verifier: str) -> str:
    return _urlsafe_b64_nopad(hashlib.sha256(verifier.encode("ascii")).digest())


def grok_checksum(machine: str, millis: int) -> str:
    stamp = (int(millis) // 1_000_000) & 0xFFFFFFFF
    raw = bytearray([
        (stamp >> 8) & 0xFF,
        stamp & 0xFF,
        (stamp >> 24) & 0xFF,
        (stamp >> 16) & 0xFF,
        (stamp >> 8) & 0xFF,
        stamp & 0xFF,
    ])
    prev = 165
    for i in range(len(raw)):
        raw[i] = ((raw[i] ^ prev) + i) & 0xFF
        prev = raw[i]
    return f"{_urlsafe_b64_nopad(bytes(raw))}{machine}"


def _jwt_claims(token: str) -> dict | None:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return None
    seg = parts[1]
    pad = "=" * ((4 - len(seg) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(seg + pad)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def grok_slot_of_token(token: str) -> str | None:
    claims = _jwt_claims(token)
    if not claims:
        return None
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        return None
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()


def _parse_reset_ts(val) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if (math.isfinite(f) and f > 0) else None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            f = float(s)
            return f if (math.isfinite(f) and f > 0) else None
        except ValueError:
            pass
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def parse_grok_usage(payload: dict, account_id: str, fetched_at: float | None = None) -> QuotaSnapshot:
    """解析 Grok Bot GetSandUsageStatus 响应。"""
    ts = fetched_at if fetched_at is not None else time.time()
    if not isinstance(payload, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Grok Bot 未返回有效数据",
        )
    if payload.get("usesPooledEnterpriseAllowance") is True:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="企业共享额度未提供个人百分比",
        )
    if payload.get("hasNonZeroIncludedLimit") is False:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="此账号没有可显示的套餐额度",
        )

    raw_used = payload.get("usagePercent")
    if isinstance(raw_used, bool) or raw_used is None:
        used = None
    else:
        try:
            used = float(raw_used)
        except (TypeError, ValueError):
            used = None
    if used is None or not math.isfinite(used) or not (0.0 <= used <= 100.0):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Grok Bot 服务未返回有效的额度百分比",
        )

    remaining = max(0.0, min(100.0, 100.0 - used))
    reset_at = _parse_reset_ts(payload.get("nextResetTimestampUtc"))
    start_at = _parse_reset_ts(payload.get("currentPeriodStart"))
    sec = (reset_at - start_at) if (start_at and reset_at and reset_at > start_at) else None
    label = "共享周额度" if payload.get("isUnified") is True else "本周"
    plan_name = str(payload.get("grokPlanLabel") or payload.get("cursorPlanName") or "").strip()

    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=ts,
        status=AccountStatus.OK.value,
        windows=[
            QuotaWindow(
                label=label,
                remaining_percent=round(remaining, 1),
                reset_at=reset_at,
                amount=float(sec) if sec else None,
            )
        ],
        plan_name=plan_name,
        last_error="",
        raw_stale=False,
    )


def _open_req(req: urllib.request.Request, timeout_s: float, http_opener=None):
    if http_opener is not None:
        return http_opener(req, timeout=timeout_s)
    return _NO_REDIRECT_OPENER.open(req, timeout=timeout_s)


def _renew_grok_session(
    old_sess: dict,
    timeout_s: float = GROK_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> tuple[bool, dict | None]:
    ref_tok = str(old_sess.get("refresh_token") or "").strip()
    if not ref_tok:
        return False, None
    body = json.dumps(
        {
            "client_id": GROK_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": ref_tok,
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{GROK_BACKEND}/oauth/token", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with _open_req(req, timeout_s=timeout_s, http_opener=http_opener) as resp:
            data = json.loads(resp.read(GROK_MAX_BYTES).decode("utf-8"))
    except Exception:
        return False, None
    if not isinstance(data, dict):
        return False, None
    new_tok = str(data.get("accessToken") or data.get("access_token") or "").strip()
    new_ref = str(data.get("refreshToken") or data.get("refresh_token") or ref_tok).strip()
    if not new_tok:
        return False, None
    old_slot = grok_slot_of_token(str(old_sess.get("access_token") or ""))
    new_slot = grok_slot_of_token(new_tok)
    if not new_slot or (old_slot and new_slot != old_slot):
        return False, None
    return True, {"access_token": new_tok, "refresh_token": new_ref, "slot": new_slot}


def fetch_grok_usage(
    account_id: str,
    secret_json: str,
    timeout_s: float = GROK_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> tuple[QuotaSnapshot, dict | None]:
    """查询 Grok Bot 周额度。返回 (QuotaSnapshot, updated_session_dict_if_renewed)。"""
    now_ts = time.time()
    try:
        sess = json.loads(secret_json or "{}")
    except Exception:
        sess = {}
    if not isinstance(sess, dict) or not str(sess.get("access_token") or "").strip():
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.AUTH_ERROR.value,
                last_error="Grok Bot 登录不可用，请重新连接",
            ),
            None,
        )

    updated_sess: dict | None = None
    claims = _jwt_claims(str(sess["access_token"]))
    if claims and isinstance(claims.get("exp"), (int, float)) and float(claims["exp"]) <= now_ts + 120 and sess.get("refresh_token"):
        ok_r, new_s = _renew_grok_session(sess, timeout_s=timeout_s, http_opener=http_opener)
        if ok_r and new_s is not None:
            sess = new_s
            updated_sess = new_s

    def _call_usage(tok: str):
        rid = str(uuid.uuid4())
        req = urllib.request.Request(GROK_USAGE_URL, data=b"{}", method="POST")
        req.add_header("Authorization", f"Bearer {tok}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("Connect-Protocol-Version", "1")
        req.add_header("x-cursor-client-type", "sand")
        req.add_header("x-cursor-client-version", "0.1.0")
        req.add_header("x-cursor-checksum", grok_checksum(rid, int(time.time() * 1000)))
        req.add_header("x-request-id", rid)
        req.add_header("x-sand-box-namespace", "prod")
        req.add_header("x-ghost-mode", "true")
        with _open_req(req, timeout_s=timeout_s, http_opener=http_opener) as resp:
            return json.loads(resp.read(GROK_MAX_BYTES).decode("utf-8"))

    try:
        payload = _call_usage(str(sess["access_token"]))
        return parse_grok_usage(payload, account_id=account_id), updated_sess
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        if code == 401 and sess.get("refresh_token"):
            ok_r, new_s = _renew_grok_session(sess, timeout_s=timeout_s, http_opener=http_opener)
            if ok_r and new_s is not None:
                updated_sess = new_s
                try:
                    payload2 = _call_usage(str(new_s["access_token"]))
                    return parse_grok_usage(payload2, account_id=account_id), updated_sess
                except urllib.error.HTTPError as e2:
                    code = int(getattr(e2, "code", 0) or 0)
                except Exception:
                    return (
                        QuotaSnapshot(
                            account_id=account_id,
                            fetched_at=now_ts,
                            status=AccountStatus.NETWORK_ERROR.value,
                            last_error="无法连接 Grok Bot，请检查网络或代理",
                        ),
                        updated_sess,
                    )
        if code == 401:
            return (
                QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error="Grok Bot 登录已失效 (401)，请重新连接",
                ),
                updated_sess,
            )
        if code == 403:
            # 403 表示权限或代理问题，绝不误报为 401
            return (
                QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.NETWORK_ERROR.value,
                    last_error="Grok Bot 额度请求被拒绝 (403)，请检查账号权限或网络代理",
                ),
                updated_sess,
            )
        if code == 429:
            return (
                QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.RATE_LIMITED.value,
                    last_error="Grok Bot 请求过于频繁 (429)，请稍后重试",
                ),
                updated_sess,
            )
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error=f"Grok Bot 额度服务暂不可用 (HTTP {code})",
            ),
            updated_sess,
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="无法连接 Grok Bot，请检查网络或代理",
            ),
            updated_sess,
        )
    except json.JSONDecodeError:
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.PARSE_ERROR.value,
                last_error="Grok Bot 未返回有效的 JSON 数据",
            ),
            updated_sess,
        )
    except Exception:
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="Grok Bot 额度查询异常",
            ),
            updated_sess,
        )


def connect_grok_browser(
    account_id: str,
    cancel_event: threading.Event | None = None,
    http_opener=None,
    browser_opener: Callable[[str], bool] | None = None,
    poll_timeout_s: float = 300.0,
) -> tuple[bool, str, str, dict | None, QuotaSnapshot | None, str]:
    """发起 Grok Bot PKCE 浏览器授权并轮询结果。
    返回 (ok, err_msg, slot_id, session_dict, snapshot, email_hint)。
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, "已取消 Grok Bot 登录", "", None, None, ""

    verifier = _urlsafe_b64_nopad(secrets.token_bytes(32))
    uid = str(uuid.uuid4())
    login_url = (
        f"https://cursor.com/loginDeepControl?challenge={grok_pkce_challenge(verifier)}"
        f"&uuid={uid}&mode=login&redirectTarget=sand&supportsSelectedTeamLogin=true"
    )
    opener_fn = browser_opener or webbrowser.open
    try:
        if opener_fn(login_url) is False:
            return False, "无法打开默认浏览器", "", None, None, ""
    except Exception:
        return False, "无法打开默认浏览器", "", None, None, ""

    poll_url = f"{GROK_BACKEND}/auth/poll?uuid={uid}&verifier={verifier}"
    deadline = time.monotonic() + poll_timeout_s
    delay_s = 1.0
    while time.monotonic() < deadline:
        steps = max(1, int(round(delay_s / 0.1)))
        for _ in range(steps):
            if cancel_event is not None and cancel_event.is_set():
                return False, "已取消 Grok Bot 登录", "", None, None, ""
            time.sleep(0.1)

        req = urllib.request.Request(poll_url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        try:
            with _open_req(req, timeout_s=GROK_DEFAULT_TIMEOUT_S, http_opener=http_opener) as resp:
                data = json.loads(resp.read(GROK_MAX_BYTES).decode("utf-8"))
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            if code in (401, 403):
                return False, "Grok Bot 登录未获授权，请重试", "", None, None, ""
            delay_s = min(5.0, delay_s + 0.5)
            continue
        except Exception:
            delay_s = min(5.0, delay_s + 0.5)
            continue

        if not isinstance(data, dict):
            continue
        tok = str(data.get("accessToken") or data.get("access_token") or "").strip()
        ref = str(data.get("refreshToken") or data.get("refresh_token") or "").strip()
        if not tok:
            continue
        slot_id = grok_slot_of_token(tok)
        if not slot_id:
            return False, "Grok Bot 返回的令牌格式无效", "", None, None, ""
        claims = _jwt_claims(tok) or {}
        email_hint = str(claims.get("email") or "").strip()
        sess = {"access_token": tok, "refresh_token": ref, "slot": slot_id}
        snap, _ = fetch_grok_usage(account_id=account_id, secret_json=json.dumps(sess), http_opener=http_opener)
        if snap.status != AccountStatus.OK.value:
            return False, snap.last_error or "Grok Bot 额度查询失败", "", None, None, ""
        if cancel_event is not None and cancel_event.is_set():
            return False, "已取消 Grok Bot 登录", "", None, None, ""
        return True, "", slot_id, sess, snap, email_hint

    return False, "Grok Bot 登录等待超时，请重试", "", None, None, ""
