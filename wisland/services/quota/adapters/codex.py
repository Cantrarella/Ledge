# -*- coding: utf-8 -*-
"""Codex 额度适配器：只读读取本机 auth.json、请求 chatgpt.com/backend-api/wham/usage 并解析主/额外周期窗口。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..models import AccountStatus, QuotaSnapshot, QuotaWindow

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_DEFAULT_TIMEOUT_S = 12.0
CODEX_MAX_BYTES = 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _finite_number(val) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d.timestamp()
        except ValueError:
            return None
    return None


def _cadence_label(seconds: int | None) -> str:
    if seconds == 18000:
        return "5 小时"
    if seconds == 604800:
        return "本周"
    if seconds == 86400:
        return "今日"
    if seconds is not None and seconds > 0:
        if seconds % 86400 == 0:
            return f"{seconds // 86400} 天"
        if seconds % 3600 == 0:
            return f"{seconds // 3600} 小时"
        return f"{max(1, seconds // 60)} 分钟"
    return "额度周期"


def _parse_codex_window(win_obj: dict, currency: str = "") -> QuotaWindow | None:
    if not isinstance(win_obj, dict):
        return None
    used = _finite_number(win_obj.get("used_percent"))
    if used is None:
        return None
    raw_sec = win_obj.get("limit_window_seconds")
    sec: int | None = None
    if isinstance(raw_sec, (int, float)) and not isinstance(raw_sec, bool) and int(raw_sec) > 0:
        sec = int(raw_sec)
    rem = max(0.0, min(100.0, 100.0 - used))
    rst = _parse_reset_ts(win_obj.get("reset_at"))
    return QuotaWindow(
        label=_cadence_label(sec),
        remaining_percent=round(rem, 1),
        reset_at=rst,
        amount=float(sec) if sec is not None else None,
        currency=currency,
    )


def parse_codex_usage(payload: dict, account_id: str, fetched_at: float | None = None) -> QuotaSnapshot:
    """解析 Codex /backend-api/wham/usage 响应。"""
    ts = fetched_at if fetched_at is not None else time.time()
    if not isinstance(payload, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Codex 服务未返回有效数据",
        )

    rate = payload.get("rate_limit")
    if not isinstance(rate, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Codex 服务未返回额度信息",
        )

    windows: list[QuotaWindow] = []
    for key in ("primary_window", "secondary_window"):
        raw_w = rate.get(key)
        if raw_w is None:
            continue
        parsed_w = _parse_codex_window(raw_w)
        if parsed_w is None:
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=ts,
                status=AccountStatus.PARSE_ERROR.value,
                last_error="Codex 额度字段不完整，暂时无法判断周期",
            )
        windows.append(parsed_w)

    if not windows:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Codex 服务未返回任何有效周期额度",
        )

    # 额外额度池：保留在 windows 末尾并标记 currency="EXTRA"，不替换主窗口
    extras = payload.get("additional_rate_limits")
    if isinstance(extras, list):
        for item in extras:
            if not isinstance(item, dict):
                continue
            lim_name = str(item.get("limit_name") or item.get("limit_id") or "额外额度").strip()
            sub_rate = item.get("rate_limit")
            if not isinstance(sub_rate, dict):
                continue
            for key in ("primary_window", "secondary_window"):
                raw_ew = sub_rate.get(key)
                if raw_ew is None:
                    continue
                ew = _parse_codex_window(raw_ew, currency="EXTRA")
                if ew is not None:
                    ew.label = f"{lim_name} · {ew.label}"
                    windows.append(ew)

    plan_name = str(payload.get("plan_type") or "").strip()
    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=ts,
        status=AccountStatus.OK.value,
        windows=windows,
        plan_name=plan_name,
        last_error="",
        raw_stale=False,
    )


def find_codex_auth_candidates() -> list[Path]:
    """返回本机 Codex auth.json 候选路径（$CODEX_HOME/auth.json 优先，其次 ~/.codex/auth.json）。"""
    out: list[Path] = []
    codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if codex_home:
        out.append(Path(codex_home) / "auth.json")
    out.append(Path.home() / ".codex" / "auth.json")
    return out


def read_codex_auth(auth_path: Path | None = None) -> tuple[bool, str, dict | None, Path | None]:
    """只读读取 Codex auth.json，返回 (ok, err_msg, auth_dict, resolved_path)。"""
    candidates = [auth_path] if auth_path is not None else find_codex_auth_candidates()
    target: Path | None = None
    for cand in candidates:
        if cand is not None and cand.is_file():
            target = cand
            break
    if target is None:
        return False, "未找到本机 Codex 登录文件（请先在 Codex CLI 完成登录）", None, None

    try:
        root = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return False, "无法读取 Codex 登录文件", None, target
    if not isinstance(root, dict):
        return False, "Codex 登录文件格式无效", None, target

    tokens = root.get("tokens") if isinstance(root.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or root.get("access_token") or "").strip()
    account_id = str(tokens.get("account_id") or root.get("account_id") or "").strip()
    if not access_token:
        return False, "Codex 登录文件中缺少有效的 access_token", None, target

    return True, "", {"access_token": access_token, "account_id": account_id}, target


def codex_identity_for_path(auth_path: Path, account_id: str = "") -> str:
    if account_id:
        return f"codex:{account_id}"
    h = hashlib.sha256(str(auth_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"codex:{h}"


def fetch_codex_usage(
    account_id: str,
    secret_json: str = "",
    source_path: str = "",
    timeout_s: float = CODEX_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> tuple[QuotaSnapshot, dict | None]:
    """查询 Codex 周期额度。返回 (QuotaSnapshot, updated_auth_dict_if_changed)。"""
    now_ts = time.time()
    auth_dict: dict | None = None
    updated_auth: dict | None = None

    # 若未注入自定义 http_opener，优先尝试从本地 auth_path 只读读取最新令牌
    if source_path and http_opener is None:
        p = Path(source_path)
        if p.is_file():
            ok_loc, _err_loc, loc_auth, _ = read_codex_auth(p)
            if ok_loc and loc_auth is not None:
                auth_dict = loc_auth
                updated_auth = loc_auth

    if auth_dict is None and secret_json.strip():
        try:
            parsed = json.loads(secret_json)
            if isinstance(parsed, dict) and parsed.get("access_token"):
                auth_dict = parsed
        except Exception:
            auth_dict = None

    if auth_dict is None and source_path:
        ok_loc, _err_loc, loc_auth, _ = read_codex_auth(Path(source_path))
        if ok_loc and loc_auth is not None:
            auth_dict = loc_auth

    if auth_dict is None or not str(auth_dict.get("access_token") or "").strip():
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.AUTH_ERROR.value,
                last_error="未找到有效的 Codex 登录凭据，请重新连接",
            ),
            None,
        )

    req = urllib.request.Request(CODEX_USAGE_URL, method="GET")
    req.add_header("Authorization", f"Bearer {auth_dict['access_token']}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "codex-cli/Wisland")
    if auth_dict.get("account_id"):
        req.add_header("ChatGPT-Account-Id", str(auth_dict["account_id"]))

    try:
        if http_opener is not None:
            cm = http_opener(req, timeout=timeout_s)
        else:
            cm = _NO_REDIRECT_OPENER.open(req, timeout=timeout_s)
        with cm as resp:
            raw = resp.read(CODEX_MAX_BYTES)
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        if code in (401, 403):
            return (
                QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error=f"Codex 登录已失效或权限不足 ({code})，请在 Codex CLI 重新登录",
                ),
                None,
            )
        if code == 429:
            return (
                QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.RATE_LIMITED.value,
                    last_error="Codex 请求过于频繁 (429)，请稍后重试",
                ),
                None,
            )
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error=f"Codex 额度服务暂不可用 (HTTP {code})",
            ),
            None,
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="无法连接 Codex 额度服务，请检查网络或代理",
            ),
            None,
        )
    except Exception:
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="Codex 额度查询异常",
            ),
            None,
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return (
            QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.PARSE_ERROR.value,
                last_error="Codex 返回了无效的 JSON 数据",
            ),
            None,
        )

    return parse_codex_usage(data, account_id=account_id), updated_auth
