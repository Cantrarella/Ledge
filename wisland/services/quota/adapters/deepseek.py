# -*- coding: utf-8 -*-
"""DeepSeek 真实余额查询适配器。
接口：GET https://api.deepseek.com/user/balance
绝不将 API Key 或完整响应原文写入日志或错误文本。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from ..models import AccountStatus, QuotaBalance, QuotaSnapshot

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
DEFAULT_TIMEOUT_S = 13.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB


def _parse_finite_decimal(raw_val) -> float:
    if raw_val is None or isinstance(raw_val, bool):
        raise ValueError("金额字段缺失或类型非法")
    txt = str(raw_val).strip()
    if not txt:
        raise ValueError("金额字段为空")
    try:
        dec = Decimal(txt)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额格式非法") from exc
    if not dec.is_finite():
        raise ValueError("金额不是有限数值")
    val = float(dec)
    if val != val or val in (float("inf"), float("-inf")):
        raise ValueError("金额转换溢出")
    return val


def fetch_deepseek_balance(
    api_key: str,
    account_id: str = "",
    url: str = DEEPSEEK_BALANCE_URL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> QuotaSnapshot:
    """请求并解析 DeepSeek 账户余额，测试连接与正式刷新共用同一套逻辑。"""
    now = time.time()
    clean_key = str(api_key or "").strip()
    if not clean_key:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.AUTH_ERROR.value,
            last_error="API Key 不能为空",
        )

    req_url = str(url or DEEPSEEK_BALANCE_URL).strip() or DEEPSEEK_BALANCE_URL
    req = urllib.request.Request(
        req_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {clean_key}",
            "Accept": "application/json",
            "User-Agent": "Wisland/1.0",
        },
    )

    open_fn = http_opener or urllib.request.urlopen
    try:
        resp_ctx = open_fn(req, timeout=max(1.0, min(float(timeout_s or DEFAULT_TIMEOUT_S), 15.0)))
        with resp_ctx as resp:
            status_code = int(getattr(resp, "status", 200) or 200)
            if status_code in (401, 403):
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now,
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error="API Key 无效或已失效 (401/403)",
                )
            if status_code == 429:
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now,
                    status=AccountStatus.RATE_LIMITED.value,
                    last_error="请求过于频繁，请稍后重试 (429)",
                )
            if status_code >= 400:
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now,
                    status=AccountStatus.NETWORK_ERROR.value,
                    last_error=f"服务端返回异常状态码 ({status_code})",
                )
            raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code in (401, 403):
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now,
                status=AccountStatus.AUTH_ERROR.value,
                last_error="API Key 无效或已失效 (401/403)",
            )
        if code == 429:
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now,
                status=AccountStatus.RATE_LIMITED.value,
                last_error="请求过于频繁，请稍后重试 (429)",
            )
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.NETWORK_ERROR.value,
            last_error=f"HTTP 请求失败 ({code})",
        )
    except (TimeoutError, socket.timeout):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.NETWORK_ERROR.value,
            last_error="请求 DeepSeek 接口超时，请检查网络连接",
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="请求 DeepSeek 接口超时，请检查网络连接",
            )
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.NETWORK_ERROR.value,
            last_error="网络连接断开或无法访问 DeepSeek 服务器",
        )
    except OSError:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.NETWORK_ERROR.value,
            last_error="网络连接断开或无法访问 DeepSeek 服务器",
        )

    if len(raw_bytes) > MAX_RESPONSE_BYTES:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="响应体积超过 2MB 安全上限",
        )

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="响应不是合法的 JSON 格式",
        )

    if not isinstance(payload, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="响应 JSON 顶层结构不是对象",
        )

    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or len(infos) == 0:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="DeepSeek 返回的余额列表为空",
        )

    parsed_balances: list[QuotaBalance] = []
    try:
        for item in infos:
            if not isinstance(item, dict):
                raise ValueError("余额项不是对象")
            currency = str(item.get("currency") or "").strip().upper()
            if not currency:
                raise ValueError("缺少币种字段 currency")
            total_b = _parse_finite_decimal(item.get("total_balance"))
            granted_b = _parse_finite_decimal(item.get("granted_balance", "0"))
            topped_b = _parse_finite_decimal(item.get("topped_up_balance", "0"))
            parsed_balances.append(
                QuotaBalance(
                    currency=currency,
                    total_balance=total_b,
                    granted_balance=granted_b,
                    topped_up_balance=topped_b,
                )
            )
    except ValueError as exc:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now,
            status=AccountStatus.PARSE_ERROR.value,
            last_error=f"余额字段解析失败：{exc}",
        )

    # 优先将 CNY 排在首位；若无 CNY 则保持接口返回顺序
    cny_first = [b for b in parsed_balances if b.currency == "CNY"] + [
        b for b in parsed_balances if b.currency != "CNY"
    ]
    is_avail = bool(payload.get("is_available", True))
    plan_txt = "可用" if is_avail else "余额不足"

    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=now,
        status=AccountStatus.OK.value,
        balances=cny_first,
        plan_name=plan_txt,
        last_error="",
        raw_stale=False,
    )
