# -*- coding: utf-8 -*-
"""WorkBuddy 额度适配器：本机客户端只读检测、官网浏览器授权轮询、Token 刷新与积分资源包解析。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from ....core.http import open_no_redirect
from ..models import AccountStatus, QuotaSnapshot, QuotaWindow

WORKBUDDY_ORIGIN = "https://www.codebuddy.cn"
WORKBUDDY_VALID_ORIGINS = frozenset({"https://www.codebuddy.cn", "https://www.workbuddy.cn"})
WORKBUDDY_SUMMARY_PATH = "/billing/meter/get-user-resource-summary"
WORKBUDDY_DEFAULT_TIMEOUT_S = 12.0
WORKBUDDY_MAX_BYTES = 2 * 1024 * 1024
WORKBUDDY_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CREDIT_GROUP_LABELS = {
    "plan_base": "套餐",
    "plan_bonus": "加赠",
    "addon": "加量包",
    "benefit": "赠送",
}


def workbuddy_slot(uid: str) -> str:
    return hashlib.sha256(str(uid or "").encode("utf-8")).hexdigest()


def _finite_nonneg(val) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0.0:
        return None
    return f


def parse_workbuddy_summary(payload: dict, account_id: str, fetched_at: float | None = None) -> QuotaSnapshot:
    """解析 WorkBuddy /billing/meter/get-user-resource-summary 响应。"""
    ts = fetched_at if fetched_at is not None else time.time()
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="WorkBuddy 积分接口暂未返回有效结果，请稍后重试",
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="WorkBuddy 积分响应缺少 data 字段",
        )

    rows = data.get("Packages")
    if not isinstance(rows, list) or not rows:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="WorkBuddy 积分接口缺少资源包数据",
        )

    remaining = 0.0
    for row in rows:
        if not isinstance(row, dict):
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=ts,
                status=AccountStatus.PARSE_ERROR.value,
                last_error="WorkBuddy 资源包格式异常",
            )
        amt = _finite_nonneg(row.get("CycleRemainCapacity"))
        if amt is None:
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=ts,
                status=AccountStatus.PARSE_ERROR.value,
                last_error="WorkBuddy 积分余额字段无效",
            )
        remaining += amt

    if not math.isfinite(remaining):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="WorkBuddy 积分余额超出范围",
        )

    windows: list[QuotaWindow] = []
    groups = data.get("CreditGroups")
    if isinstance(groups, list):
        for grp in groups:
            if not isinstance(grp, dict):
                continue
            label = _CREDIT_GROUP_LABELS.get(str(grp.get("Kind") or ""))
            if not label:
                continue
            amt = _finite_nonneg(grp.get("CycleRemainCapacity"))
            if amt is not None and amt > 0.0:
                windows.append(QuotaWindow(label=label, amount=round(amt, 2), currency="CREDITS"))

    plan_name = str(data.get("SubscriptionPackageName") or "").strip()
    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=ts,
        status=AccountStatus.OK.value,
        windows=windows,
        credits=round(remaining, 2),
        plan_name=plan_name,
        last_error="",
        raw_stale=False,
    )


def _open_request(req: urllib.request.Request, timeout_s: float, http_opener=None):
    return open_no_redirect(req, timeout_s, http_opener=http_opener)


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout_s: float = WORKBUDDY_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> tuple[int, dict | None, str]:
    """执行单次 JSON 请求，返回 (status_code_or_0, json_dict, err_kind)。
    err_kind: "" | "auth" | "forbidden" | "rate_limit" | "network" | "parse"
    """
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", WORKBUDDY_DEFAULT_UA)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        if v:
            req.add_header(k, v)
    try:
        with _open_request(req, timeout_s=timeout_s, http_opener=http_opener) as resp:
            status = getattr(resp, "status", 200) or 200
            raw = resp.read(WORKBUDDY_MAX_BYTES)
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        if code == 401:
            return code, None, "auth"
        if code == 403:
            return code, None, "forbidden"
        if code == 429:
            return code, None, "rate_limit"
        if 300 <= code < 400:
            return code, None, "network"
        if code == 400:
            try:
                raw = e.read(WORKBUDDY_MAX_BYTES)
                return code, json.loads(raw.decode("utf-8")), ""
            except Exception:
                return code, None, "network"
        return code, None, "network"
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, "network"
    except Exception:
        return 0, None, "network"

    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return status, None, "parse"
        return status, data, ""
    except Exception:
        return status, None, "parse"


def _build_session_headers(session: dict, include_platform: bool = True) -> dict[str, str]:
    origin = str(session.get("origin") or WORKBUDDY_ORIGIN).rstrip("/")
    hdrs: dict[str, str] = {
        "User-Agent": str(session.get("ua") or "").strip() or WORKBUDDY_DEFAULT_UA,
        "Origin": origin,
        "Referer": f"{origin}/",
    }
    if include_platform:
        hdrs["X-Client-Platform"] = "web"
    if session.get("cookie"):
        hdrs["Cookie"] = str(session["cookie"])
    auth = session.get("auth")
    if isinstance(auth, dict) and auth.get("accessToken"):
        hdrs["Authorization"] = f"Bearer {auth['accessToken']}"
        if auth.get("uid"):
            hdrs["X-User-Id"] = str(auth["uid"])
        if auth.get("domain"):
            hdrs["X-Domain"] = str(auth["domain"])
    return hdrs


def _local_workbuddy_info_path() -> Path | None:
    local_app = os.environ.get("LOCALAPPDATA")
    if not local_app:
        return None
    return Path(local_app) / "CodeBuddyExtension" / "Data" / "Public" / "auth" / "workbuddy-desktop-ai.info"


def try_local_workbuddy_snapshot(
    account_id: str,
    expected_slot: str | None = None,
    info_path: Path | None = None,
    http_opener=None,
    timeout_s: float = WORKBUDDY_DEFAULT_TIMEOUT_S,
) -> tuple[bool, str, dict | None, QuotaSnapshot | None]:
    """只读尝试使用本机 WorkBuddy 客户端当前登录查询积分。
    返回 (ok, err_msg, session_dict_or_none, snapshot_or_none)。
    """
    p = info_path or _local_workbuddy_info_path()
    if p is None or not p.is_file():
        return False, "未找到 WorkBuddy 客户端登录", None, None
    logged_out = p.with_suffix(".info.logged-out")
    if logged_out.exists():
        return False, "WorkBuddy 客户端已退出登录", None, None
    try:
        root = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False, "无法读取 WorkBuddy 客户端登录", None, None
    if not isinstance(root, dict):
        return False, "WorkBuddy 客户端登录格式无效", None, None

    acc_obj = root.get("account") or {}
    auth_obj = root.get("auth") or {}
    uid = str(acc_obj.get("uid") or "").strip()
    if not uid:
        return False, "WorkBuddy 客户端尚未登录", None, None
    slot = workbuddy_slot(uid)
    if expected_slot and slot != expected_slot:
        return False, "客户端当前账号与已绑定账号不同", None, None
    if acc_obj.get("isCurrentOneIdEnterprise") is True:
        return False, "请使用 WorkBuddy 个人账号", None, None

    token = str(auth_obj.get("accessToken") or "").strip()
    if not token:
        return False, "客户端登录已加密或缺少访问令牌，请通过官网登录", None, None
    domain = str(auth_obj.get("domain") or "").strip()
    refresh_token = str(auth_obj.get("refreshToken") or "").strip()

    session = {
        "origin": WORKBUDDY_ORIGIN,
        "ua": "",
        "cookie": "",
        "auth": {
            "accessToken": token,
            "refreshToken": refresh_token,
            "domain": domain,
            "uid": uid,
        },
        "slot": slot,
    }
    code, payload, err_kind = _http_json(
        "POST",
        f"{WORKBUDDY_ORIGIN}{WORKBUDDY_SUMMARY_PATH}",
        headers=_build_session_headers(session),
        body=b"{}",
        timeout_s=timeout_s,
        http_opener=http_opener,
    )
    if err_kind or payload is None:
        return False, f"客户端登录查询积分失败 ({code or err_kind})", None, None
    snap = parse_workbuddy_summary(payload, account_id=account_id)
    if snap.status != AccountStatus.OK.value:
        return False, snap.last_error, None, snap
    return True, "", session, snap


def _refresh_workbuddy_auth(
    session: dict,
    timeout_s: float = WORKBUDDY_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> tuple[bool, dict | None, str]:
    """使用 refreshToken 续期 WorkBuddy 会话。返回 (ok, new_auth_or_none, err_kind)。"""
    auth = session.get("auth")
    if not isinstance(auth, dict) or not auth.get("refreshToken"):
        return False, None, "auth"
    origin = str(session.get("origin") or WORKBUDDY_ORIGIN).rstrip("/")
    if origin not in WORKBUDDY_VALID_ORIGINS:
        return False, None, "parse"
    code, data, err_kind = _http_json(
        "POST",
        f"{origin}/v2/plugin/auth/token/refresh",
        headers={
            "X-Refresh-Token": str(auth["refreshToken"]),
            "X-Auth-Refresh-Source": "plugin",
            "X-Domain": str(auth.get("domain") or ""),
        },
        body=b"{}",
        timeout_s=timeout_s,
        http_opener=http_opener,
    )
    if err_kind in ("auth", "forbidden"):
        return False, None, "auth"
    if err_kind or not isinstance(data, dict):
        return False, None, err_kind or "network"
    inner = data.get("data")
    if not isinstance(inner, dict) or not inner.get("accessToken"):
        return False, None, "auth"
    next_auth = {
        "accessToken": str(inner["accessToken"]),
        "refreshToken": str(inner.get("refreshToken") or auth.get("refreshToken") or ""),
        "domain": str(inner.get("domain") or auth.get("domain") or ""),
        "uid": str(auth.get("uid") or ""),
    }
    return True, next_auth, ""


def _map_http_error_snapshot(account_id: str, code: int, err_kind: str) -> QuotaSnapshot:
    now_ts = time.time()
    if err_kind == "auth":
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now_ts,
            status=AccountStatus.AUTH_ERROR.value,
            last_error="WorkBuddy 登录已过期，请重新连接 (401)",
        )
    if err_kind == "rate_limit":
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now_ts,
            status=AccountStatus.RATE_LIMITED.value,
            last_error="WorkBuddy 请求过于频繁 (429)",
        )
    if err_kind == "forbidden":
        # 403 不得误报成 401 登录过期
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now_ts,
            status=AccountStatus.NETWORK_ERROR.value,
            last_error="WorkBuddy 官网拒绝访问 (403)，请检查网络或代理设置",
        )
    if err_kind == "parse":
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now_ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="WorkBuddy 官网返回了无效数据",
        )
    msg = f"WorkBuddy 官网返回 HTTP {code}" if code else "暂时无法连接 WorkBuddy 官网"
    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=now_ts,
        status=AccountStatus.NETWORK_ERROR.value,
        last_error=msg,
    )


def fetch_workbuddy_quota(
    account_id: str,
    secret_json: str,
    expected_slot: str = "",
    timeout_s: float = WORKBUDDY_DEFAULT_TIMEOUT_S,
    http_opener=None,
    local_info_path: Path | None = None,
) -> tuple[QuotaSnapshot, dict | None]:
    """查询 WorkBuddy 积分。返回 (QuotaSnapshot, updated_session_dict_if_refreshed)。"""
    now_ts = time.time()
    session: dict | None = None
    if secret_json.strip():
        try:
            parsed = json.loads(secret_json)
            if isinstance(parsed, dict):
                session = parsed
        except Exception:
            session = None

    slot_id = expected_slot or (str(session.get("slot") or "") if isinstance(session, dict) else "")

    # 1) 优先尝试本机 WorkBuddy 客户端登录（若存在且匹配已绑定账号）
    if slot_id and http_opener is None:
        ok_loc, _, loc_sess, loc_snap = try_local_workbuddy_snapshot(
            account_id=account_id,
            expected_slot=slot_id,
            info_path=local_info_path,
            timeout_s=timeout_s,
        )
        if ok_loc and loc_snap is not None:
            return loc_snap, loc_sess

    if not isinstance(session, dict):
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.AUTH_ERROR.value,
                last_error="WorkBuddy 会话无效或缺失，请重新连接",
            ),
            None,
        )

    origin = str(session.get("origin") or WORKBUDDY_ORIGIN).rstrip("/")
    if origin not in WORKBUDDY_VALID_ORIGINS:
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.AUTH_ERROR.value,
                last_error="WorkBuddy 登录来源无效，请重新连接",
            ),
            None,
        )

    url = f"{origin}{WORKBUDDY_SUMMARY_PATH}"
    code, payload, err_kind = _http_json(
        "POST",
        url,
        headers=_build_session_headers(session, include_platform=True),
        body=b"{}",
        timeout_s=timeout_s,
        http_opener=http_opener,
    )
    if err_kind == "forbidden":
        code_np, payload_np, err_kind_np = _http_json(
            "POST",
            url,
            headers=_build_session_headers(session, include_platform=False),
            body=b"{}",
            timeout_s=timeout_s,
            http_opener=http_opener,
        )
        if not err_kind_np and payload_np is not None:
            return parse_workbuddy_summary(payload_np, account_id=account_id), None
        if err_kind_np != "forbidden":
            code, payload, err_kind = code_np, payload_np, err_kind_np

    if err_kind == "auth" and isinstance(session.get("auth"), dict) and session["auth"].get("refreshToken"):
        ok_ref, next_auth, ref_err = _refresh_workbuddy_auth(session, timeout_s=timeout_s, http_opener=http_opener)
        if ok_ref and next_auth is not None:
            updated_session = dict(session)
            updated_session["auth"] = next_auth
            code2, payload2, err_kind2 = _http_json(
                "POST",
                url,
                headers=_build_session_headers(updated_session),
                body=b"{}",
                timeout_s=timeout_s,
                http_opener=http_opener,
            )
            if not err_kind2 and payload2 is not None:
                return parse_workbuddy_summary(payload2, account_id=account_id), updated_session
            return _map_http_error_snapshot(account_id, code2, err_kind2), updated_session
        if ref_err != "auth":
            return _map_http_error_snapshot(account_id, 0, ref_err), None

    if err_kind or payload is None:
        return _map_http_error_snapshot(account_id, code, err_kind or "network"), None

    return parse_workbuddy_summary(payload, account_id=account_id), None


def validate_workbuddy_auth_url(auth_url: str, state: str) -> bool:
    """严格校验官网返回的登录 URL，防止跳转到非官方域名。"""
    try:
        u = urllib.parse.urlparse(auth_url)
    except Exception:
        return False
    origin = f"{u.scheme}://{u.netloc}"
    if origin != WORKBUDDY_ORIGIN or u.path != "/login":
        return False
    qs = urllib.parse.parse_qs(u.query)
    return state in qs.get("state", [])


def connect_workbuddy_interactive(
    account_id: str,
    expected_slot: str = "",
    cancel_event: threading.Event | None = None,
    http_opener=None,
    browser_opener: Callable[[str], bool] | None = None,
    local_info_path: Path | None = None,
    poll_timeout_s: float = 600.0,
    poll_interval_s: float = 2.0,
) -> tuple[bool, str, str, dict | None, QuotaSnapshot | None]:
    """WorkBuddy 完整连接流程：
    1) 若本机客户端存在有效个人登录，直接读取并验证积分；
    2) 否则创建官网一次性登录请求，拉起默认浏览器并轮询授权，支持 cancel_event 取消。
    返回 (ok, err_msg, slot_id, session_dict, snapshot)。
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, "已取消 WorkBuddy 连接", "", None, None

    # 1) 尝试本机客户端登录
    ok_loc, _loc_err, loc_sess, loc_snap = try_local_workbuddy_snapshot(
        account_id=account_id,
        expected_slot=expected_slot or None,
        info_path=local_info_path,
        http_opener=http_opener,
    )
    if ok_loc and loc_sess is not None and loc_snap is not None:
        return True, "", str(loc_sess.get("slot") or ""), loc_sess, loc_snap

    # 2) 独立官网浏览器登录
    code, state_resp, err_kind = _http_json(
        "POST",
        f"{WORKBUDDY_ORIGIN}/v2/plugin/auth/state?platform=daozai",
        headers={"X-No-Authorization": "true", "X-No-User-Id": "true"},
        body=b"{}",
        http_opener=http_opener,
    )
    if err_kind or not isinstance(state_resp, dict) or state_resp.get("code") != 0:
        return False, f"无法创建 WorkBuddy 登录请求 ({code or err_kind or '错误'})", "", None, None

    data = state_resp.get("data") or {}
    state = str(data.get("state") or "").strip()
    auth_url = str(data.get("authUrl") or "").strip()
    if not state or not validate_workbuddy_auth_url(auth_url, state):
        return False, "WorkBuddy 官网登录地址无效", "", None, None

    opener_fn = browser_opener or webbrowser.open
    try:
        opened = opener_fn(auth_url)
        if opened is False:
            return False, "无法打开默认浏览器，请检查 Windows 默认应用设置", "", None, None
    except Exception:
        return False, "无法打开默认浏览器", "", None, None

    poll_url = f"{WORKBUDDY_ORIGIN}/v2/plugin/auth/token?state={urllib.parse.quote(state)}"
    deadline = time.monotonic() + poll_timeout_s
    while time.monotonic() < deadline:
        # 分片等待以快速响应取消
        steps = max(1, int(round(poll_interval_s / 0.1)))
        for _ in range(steps):
            if cancel_event is not None and cancel_event.is_set():
                return False, "已取消 WorkBuddy 登录", "", None, None
            time.sleep(0.1)

        p_code, p_data, p_err = _http_json(
            "GET",
            poll_url,
            headers={"X-No-Authorization": "true"},
            http_opener=http_opener,
        )
        if p_err == "network":
            continue
        if p_err or not isinstance(p_data, dict):
            return False, f"WorkBuddy 登录请求已失效 ({p_code or p_err})", "", None, None
        if p_data.get("code") == 11217:
            continue

        auth_info = p_data.get("data")
        if not isinstance(auth_info, dict) or not auth_info.get("accessToken"):
            return False, "WorkBuddy 官网未返回有效授权，请重新连接", "", None, None

        session: dict = {
            "origin": WORKBUDDY_ORIGIN,
            "ua": "",
            "cookie": "",
            "auth": {
                "accessToken": str(auth_info["accessToken"]),
                "refreshToken": str(auth_info.get("refreshToken") or ""),
                "domain": str(auth_info.get("domain") or ""),
                "uid": "",
            },
        }
        ac_code, ac_data, ac_err = _http_json(
            "GET",
            f"{WORKBUDDY_ORIGIN}/v2/plugin/accounts",
            headers=_build_session_headers(session),
            http_opener=http_opener,
        )
        if ac_err or not isinstance(ac_data, dict):
            return False, f"无法获取 WorkBuddy 账号列表 ({ac_code or ac_err})", "", None, None

        acc_list = (ac_data.get("data") or {}).get("accounts")
        if not isinstance(acc_list, list):
            return False, "WorkBuddy 官网未返回账号列表", "", None, None

        chosen_uid = ""
        for item in acc_list:
            if not isinstance(item, dict):
                continue
            if item.get("enterpriseId") or item.get("isCurrentOneIdEnterprise") is True:
                continue
            uid_cand = str(item.get("uid") or "").strip()
            if not uid_cand:
                continue
            if expected_slot and workbuddy_slot(uid_cand) != expected_slot:
                continue
            chosen_uid = uid_cand
            break

        if not chosen_uid:
            return False, "请在官网登录个人账号（若为重连请使用原绑定账号）后重试", "", None, None

        slot_id = workbuddy_slot(chosen_uid)
        session["auth"]["uid"] = chosen_uid
        session["slot"] = slot_id

        snap, _ = fetch_workbuddy_quota(
            account_id=account_id,
            secret_json=json.dumps(session),
            expected_slot=slot_id,
            http_opener=http_opener,
        )
        if snap.status != AccountStatus.OK.value:
            return False, snap.last_error or "WorkBuddy 积分查询失败", "", None, None
        if cancel_event is not None and cancel_event.is_set():
            return False, "已取消 WorkBuddy 登录", "", None, None
        return True, "", slot_id, session, snap

    return False, "WorkBuddy 官网登录等待超时，请重新连接", "", None, None
