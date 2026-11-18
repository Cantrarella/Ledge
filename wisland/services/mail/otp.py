# -*- coding: utf-8 -*-
"""邮件验证码识别与有效期解析（纯本地文本分析，绝不写入日志）。"""
from __future__ import annotations

import re

from .models import VerificationCodeInfo

# 上下文触发词（必须命中其一才允许提取候选码）
_CTX_RE = re.compile(
    r"(?:"
    r"验证码|校验码|动态码|安全码|登录码|确认码|取件码|一次性密码|动态口令"
    r"|verification\s*code|security\s*code|confirm(?:ation)?\s*code|login\s*code"
    r"|auth(?:entication)?\s*code|passcode|one[- ]time(?:\s+(?:password|passcode|code))?|otp|2fa"
    r")",
    re.I,
)

# 紧邻强排除上下文（订单号、金额、套餐、运单等）
_NEG_CTX_RE = re.compile(
    r"(?:订单号?|单号|运单号?|流水号?|发票号?|账单号?|交易号?|会员号?|客服电话|热线|套餐|order(?:\s*(?:id|no|number|#))?|invoice|tracking)",
    re.I,
)

# 紧跟候选码的排除后缀（金额/日期/时长/流量单位）
_BAD_SUFFIX_RE = re.compile(
    r"^\s*(?:元|块|角|分|天|日|月|年|小时|时|分钟|秒钟?|次|件|折|%|gb|mb|kb|tb|g|m|usd|cny|rmb)\b",
    re.I,
)

# 候选码：4~8 位数字或字母数字组合（不跨连字符/小数点）
_CAND_RE = re.compile(r"(?<![A-Za-z0-9.\-/:￥¥$€£])([A-Za-z0-9]{4,8})(?![A-Za-z0-9.\-/:%])")

# 有效期解析（必须与 valid / expire / 有效 / 过期 / 内 同句或相邻）
_TTL_MIN_RE = re.compile(r"(?:有效[期为]?|valid(?:\s+for)?|expire[sd]?\s+in|within|在)\D{0,10}?(\d{1,3})\s*(?:分钟|分|min(?:ute)?s?)", re.I)
_TTL_SEC_RE = re.compile(r"(?:有效[期为]?|valid(?:\s+for)?|expire[sd]?\s+in|within|在)\D{0,10}?(\d{1,4})\s*(?:秒钟?|sec(?:ond)?s?)", re.I)
_TTL_MIN_TAIL_RE = re.compile(r"(\d{1,3})\s*(?:分钟|分|min(?:ute)?s?)\s*(?:内有效|后失效|有效|内使用|内输入)", re.I)
_TTL_SEC_TAIL_RE = re.compile(r"(\d{1,4})\s*(?:秒钟?|sec(?:ond)?s?)\s*(?:内有效|后失效|有效|内使用|内输入)", re.I)


def _extract_ttl_seconds(text: str) -> int | None:
    if not text:
        return None
    for pat, mult in (
        (_TTL_MIN_RE, 60),
        (_TTL_MIN_TAIL_RE, 60),
        (_TTL_SEC_RE, 1),
        (_TTL_SEC_TAIL_RE, 1),
    ):
        m = pat.search(text)
        if m:
            try:
                val = int(m.group(1)) * mult
                if 10 <= val <= 3600:
                    return val
            except (TypeError, ValueError):
                continue
    return None


def _is_valid_candidate(tok: str, text: str, start: int, end: int) -> bool:
    if not (4 <= len(tok) <= 8):
        return False
    # 字母数字组合必须至少包含 1 个数字，排除普通英文单词（如 code / login / valid）
    if not any(ch.isdigit() for ch in tok):
        return False

    # 紧邻前缀不能是货币符号或小数点
    prefix_near = text[max(0, start - 6):start]
    if re.search(r"(?:[￥¥$€£]|RMB|USD|CNY)\s*$", prefix_near, re.I):
        return False

    # 紧邻后缀不能是金额/时间/流量单位
    suffix_near = text[end:min(len(text), end + 12)]
    if _BAD_SUFFIX_RE.match(suffix_near):
        return False

    # 排除常见年份（2020~2035）在日期语境中误命中
    if len(tok) == 4 and tok.isdigit() and 2020 <= int(tok) <= 2035:
        if re.match(r"^\s*(?:年|-|/)", suffix_near):
            return False

    return True


def _find_in_text(text: str) -> str:
    if not text:
        return ""
    for ctx_m in _CTX_RE.finditer(text):
        # 优先看关键词后方 42 字符，其次看关键词前方 24 字符
        after_start = ctx_m.end()
        after_end = min(len(text), after_start + 42)
        after_seg = text[after_start:after_end]

        for m in _CAND_RE.finditer(after_seg):
            abs_s = after_start + m.start(1)
            abs_e = after_start + m.end(1)
            tok = m.group(1)
            near_before = text[max(ctx_m.end(), abs_s - 18):abs_s]
            if _NEG_CTX_RE.search(near_before):
                continue
            if _is_valid_candidate(tok, text, abs_s, abs_e):
                return tok

        before_start = max(0, ctx_m.start() - 24)
        before_end = ctx_m.start()
        before_seg = text[before_start:before_end]
        matches = list(_CAND_RE.finditer(before_seg))
        for m in reversed(matches):
            abs_s = before_start + m.start(1)
            abs_e = before_start + m.end(1)
            tok = m.group(1)
            near_before = text[max(0, abs_s - 14):abs_s]
            if _NEG_CTX_RE.search(near_before):
                continue
            if _is_valid_candidate(tok, text, abs_s, abs_e):
                return tok
    return ""


def extract_verification_code(subject: str = "", body_text: str = "") -> VerificationCodeInfo | None:
    """从邮件标题与纯文本正文中识别验证码及有效期；未命中返回 None。"""
    subj = (subject or "").strip()
    body = (body_text or "").strip()

    code = _find_in_text(subj)
    hint = "subject" if code else ""
    if not code:
        code = _find_in_text(body)
        if code:
            hint = "body"
    if not code:
        return None

    ttl = _extract_ttl_seconds(body) or _extract_ttl_seconds(subj)
    return VerificationCodeInfo(code=code, ttl_seconds=ttl, source_hint=hint)
