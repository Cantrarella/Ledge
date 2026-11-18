# -*- coding: utf-8 -*-
"""IMAP 连接测试与最小元数据读取（仅只读 EXAMINE + BODY.PEEK 头字段，不下载正文与附件）。"""
from __future__ import annotations

import email
import email.header
import email.utils
import html
import html.parser
import imaplib
import re
import socket
import ssl
import threading
import time

from .cache import compute_body_hash
from .models import MailAccount, MailBody, MailHeader

DEFAULT_TIMEOUT_S = 10.0
MAX_BODY_BYTES = 2 * 1024 * 1024
_UID_RE = re.compile(rb"UID\s+(\d+)", re.I)
_SEEN_RE = re.compile(rb"\\Seen\b", re.I)
_UIDVAL_RE = re.compile(r"UIDVALIDITY\s+(\d+)", re.I)
_BLOCK_TAGS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section", "article"})
_IGNORE_TAGS = frozenset({"script", "style", "noscript", "iframe", "svg", "object", "embed", "head"})


class MailCancelledError(Exception):
    """用户取消了连接测试或同步。"""


def _check_cancel(cancel_event: threading.Event | None):
    if cancel_event is not None and cancel_event.is_set():
        raise MailCancelledError("已取消操作")


def _decode_mime_header(val: str | None) -> str:
    if not val:
        return ""
    parts: list[str] = []
    for raw, enc in email.header.decode_header(val):
        if isinstance(raw, bytes):
            try:
                parts.append(raw.decode(enc or "utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                parts.append(raw.decode("utf-8", errors="replace"))
        else:
            parts.append(str(raw))
    return "".join(parts).strip()


def _parse_date_epoch(val: str | None) -> float:
    if not val:
        return time.time()
    try:
        dt = email.utils.parsedate_to_datetime(val)
        if dt is not None:
            return dt.timestamp()
    except Exception:  # noqa: BLE001
        pass
    return time.time()


def _default_client_factory(host: str, port: int, security: str, timeout: float):
    sec = (security or "ssl").lower()
    if sec == "ssl":
        ctx = ssl.create_default_context()
        return imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)
    client = imaplib.IMAP4(host, port, timeout=timeout)
    if sec == "starttls":
        ctx = ssl.create_default_context()
        client.starttls(ssl_context=ctx)
    return client


def _safe_close(client):
    if client is None:
        return
    try:
        client.logout()
    except Exception:  # noqa: BLE001
        try:
            sock = getattr(client, "sock", None)
            if sock is not None:
                sock.close()
        except Exception:  # noqa: BLE001
            pass


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, MailCancelledError):
        return "已取消连接"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "连接超时，请检查服务器或网络状态"
    if isinstance(exc, imaplib.IMAP4.error):
        msg = str(exc).lower()
        if any(k in msg for k in ("auth", "login", "credentials", "password", "invalid", "fail", "no")):
            return "授权码或账号验证失败，请确认已开启 IMAP 并使用正确的授权码"
        return "IMAP 服务拒绝连接或命令失败"
    if isinstance(exc, ssl.SSLError):
        return "TLS/SSL 握手失败，请核对加密方式与端口"
    if isinstance(exc, (socket.gaierror, OSError)):
        return "无法连接 IMAP 服务器，请检查主机名与网络连接"
    return f"连接失败（{type(exc).__name__}）"


def test_imap_connection(
    account: MailAccount,
    secret: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
) -> tuple[bool, str]:
    """测试 IMAP 连接与只读 INBOX 登录；绝不记录或返回凭据明文。"""
    factory = client_factory or _default_client_factory
    client = None
    try:
        _check_cancel(cancel_event)
        client = factory(account.host, account.port, account.security, timeout)
        _check_cancel(cancel_event)
        client.login(account.username or account.email, secret)
        _check_cancel(cancel_event)
        # 163/126 等邮箱有时需要简单 ID 声明（失败忽略）
        if account.provider in ("163", "126"):
            try:
                client.xatom("ID", '("name" "Wisland" "version" "1.0")')
            except Exception:  # noqa: BLE001
                pass
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            return False, "无法打开只读收件箱 INBOX"
        _check_cancel(cancel_event)
        client.noop()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, _friendly_error(exc)
    finally:
        _safe_close(client)


def extract_uidvalidity(client, select_data=None) -> str:
    """从已打开的 IMAP INBOX 会话读取 UIDVALIDITY。"""
    try:
        resp_fn = getattr(client, "response", None)
        if callable(resp_fn):
            _typ, vals = resp_fn("UIDVALIDITY")
            if vals and vals[0]:
                v = vals[0].decode("ascii", errors="ignore") if isinstance(vals[0], (bytes, bytearray)) else str(vals[0])
                v = v.strip()
                if v.isdigit():
                    return v
    except Exception:  # noqa: BLE001
        pass
    try:
        untagged = getattr(client, "untagged_responses", None)
        if isinstance(untagged, dict):
            uv = untagged.get("UIDVALIDITY")
            if uv and uv[0]:
                v = uv[0].decode("ascii", errors="ignore") if isinstance(uv[0], (bytes, bytearray)) else str(uv[0])
                v = v.strip()
                if v.isdigit():
                    return v
    except Exception:  # noqa: BLE001
        pass
    if select_data:
        for item in select_data:
            s = item.decode("ascii", errors="ignore") if isinstance(item, (bytes, bytearray)) else str(item or "")
            m = _UIDVAL_RE.search(s)
            if m:
                return m.group(1)
    return "0"


def _fetch_body_on_open_client(
    client,
    account: MailAccount,
    uid: str,
    uid_validity: str,
    cancel_event: threading.Event | None = None,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[bool, MailBody | None, str]:
    """在已登录且已选中只读 INBOX 的 IMAP 会话上拉取单封邮件正文。"""
    _check_cancel(cancel_event)
    fetch_spec = f"(UID BODY.PEEK[]<0.{int(max_bytes) + 1}>)"
    typ, fetched = client.uid("FETCH", str(uid), fetch_spec)
    if typ != "OK" or not fetched:
        _check_cancel(cancel_event)
        typ, fetched = client.uid("FETCH", str(uid), "(UID BODY.PEEK[])")
    if typ != "OK" or not fetched:
        return False, None, "未找到该邮件或服务器返回为空"

    raw_bytes = b""
    for item in fetched:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            raw_bytes = bytes(item[1])
            break
    if not raw_bytes:
        return False, None, "邮件正文内容为空或无法读取"

    _check_cancel(cancel_event)
    try:
        text, src, truncated = extract_safe_body_from_bytes(raw_bytes, max_bytes=max_bytes)
    except Exception as exc:  # noqa: BLE001
        return False, None, f"正文解析失败（{type(exc).__name__}）"

    body = MailBody(
        account_id=account.id,
        uid=str(uid),
        text=text,
        source=src,
        truncated=truncated,
        fetched_at=time.time(),
        uid_validity=str(uid_validity or ""),
        content_hash=compute_body_hash(text),
    )
    return True, body, ""


def fetch_recent_headers(
    account: MailAccount,
    secret: str,
    limit: int = 20,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
) -> tuple[bool, list[MailHeader], str]:
    """只读方式拉取最近最多 limit 封邮件的最小元数据（UID、发件人、标题、时间、已读状态）。
    严格使用 readonly=True 与 BODY.PEEK，绝不下载正文/附件，绝不修改服务器状态。
    """
    ok, headers, _bodies, _uv, err = fetch_headers_and_prefetch_bodies(
        account,
        secret,
        limit=limit,
        immediate_count=0,
        timeout=timeout,
        cancel_event=cancel_event,
        client_factory=client_factory,
    )
    return ok, headers, err


def fetch_headers_and_prefetch_bodies(
    account: MailAccount,
    secret: str,
    limit: int = 20,
    immediate_count: int = 5,
    has_cached_fn=None,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[bool, list[MailHeader], list[MailBody], str, str]:
    """同一 IMAP 会话内读取最近 limit 封 Header，并立即预取最近 immediate_count 封未缓存正文。"""
    factory = client_factory or _default_client_factory
    client = None
    try:
        _check_cancel(cancel_event)
        client = factory(account.host, account.port, account.security, timeout)
        _check_cancel(cancel_event)
        client.login(account.username or account.email, secret)
        _check_cancel(cancel_event)
        if account.provider in ("163", "126"):
            try:
                client.xatom("ID", '("name" "Wisland" "version" "1.0")')
            except Exception:  # noqa: BLE001
                pass
        typ, sel_data = client.select("INBOX", readonly=True)
        if typ != "OK":
            return False, [], [], "", "无法打开只读收件箱 INBOX"
        uid_validity = extract_uidvalidity(client, sel_data)
        _check_cancel(cancel_event)

        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return True, [], [], uid_validity, ""
        uids = [u for u in data[0].split() if u]
        if not uids:
            return True, [], [], uid_validity, ""
        target_uids = uids[-max(1, int(limit)):]
        uid_set = b",".join(target_uids).decode("ascii", errors="ignore")
        _check_cancel(cancel_event)

        typ, fetched = client.uid("FETCH", uid_set, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK" or not fetched:
            return True, [], [], uid_validity, ""

        headers: list[MailHeader] = []
        for item in fetched:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta_bytes = item[0] if isinstance(item[0], (bytes, bytearray)) else str(item[0]).encode("utf-8", "replace")
            hdr_bytes = item[1] if isinstance(item[1], (bytes, bytearray)) else str(item[1]).encode("utf-8", "replace")
            m_uid = _UID_RE.search(meta_bytes)
            uid_s = m_uid.group(1).decode("ascii") if m_uid else ""
            if not uid_s:
                continue
            is_read = bool(_SEEN_RE.search(meta_bytes))
            msg = email.message_from_bytes(bytes(hdr_bytes))
            sender = _decode_mime_header(msg.get("From"))
            subject = _decode_mime_header(msg.get("Subject")) or "（无主题）"
            received_at = _parse_date_epoch(msg.get("Date"))
            headers.append(MailHeader(
                uid=uid_s,
                account_id=account.id,
                sender=sender or "未知发件人",
                subject=subject,
                received_at=received_at,
                is_read=is_read,
                uid_validity=uid_validity,
            ))
        headers.sort(key=lambda h: h.received_at, reverse=True)

        bodies: list[MailBody] = []
        for h in headers[: max(0, int(immediate_count))]:
            _check_cancel(cancel_event)
            if callable(has_cached_fn) and has_cached_fn(uid_validity, h.uid):
                continue
            b_ok, body, _ = _fetch_body_on_open_client(
                client, account, h.uid, uid_validity, cancel_event=cancel_event, max_bytes=max_bytes
            )
            if b_ok and body is not None:
                bodies.append(body)

        return True, headers, bodies, uid_validity, ""
    except Exception as exc:  # noqa: BLE001
        return False, [], [], "", _friendly_error(exc)
    finally:
        _safe_close(client)


def prefetch_bodies_in_session(
    account: MailAccount,
    secret: str,
    uids: list[str],
    expected_uid_validity: str = "",
    has_cached_fn=None,
    on_body_fn=None,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[bool, list[MailBody], str]:
    """在单个后台 IMAP 会话中低优先级预取一批未缓存邮件的正文。"""
    if not uids:
        return True, [], ""
    factory = client_factory or _default_client_factory
    client = None
    try:
        _check_cancel(cancel_event)
        client = factory(account.host, account.port, account.security, timeout)
        _check_cancel(cancel_event)
        client.login(account.username or account.email, secret)
        _check_cancel(cancel_event)
        if account.provider in ("163", "126"):
            try:
                client.xatom("ID", '("name" "Wisland" "version" "1.0")')
            except Exception:  # noqa: BLE001
                pass
        typ, sel_data = client.select("INBOX", readonly=True)
        if typ != "OK":
            return False, [], "无法打开只读收件箱 INBOX"
        uid_validity = extract_uidvalidity(client, sel_data)
        if expected_uid_validity and uid_validity and uid_validity != expected_uid_validity:
            return True, [], ""

        bodies: list[MailBody] = []
        for u in uids:
            _check_cancel(cancel_event)
            if callable(has_cached_fn) and has_cached_fn(uid_validity, str(u)):
                continue
            b_ok, body, _ = _fetch_body_on_open_client(
                client, account, str(u), uid_validity, cancel_event=cancel_event, max_bytes=max_bytes
            )
            if b_ok and body is not None:
                bodies.append(body)
                if callable(on_body_fn):
                    on_body_fn(body)
        return True, bodies, ""
    except Exception as exc:  # noqa: BLE001
        return False, [], _friendly_error(exc)
    finally:
        _safe_close(client)


class _SafeHTMLTextParser(html.parser.HTMLParser):
    """安全 HTML 纯文本提取器：丢弃脚本/样式/框架内容，绝不加载远程图片或执行脚本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = (tag or "").lower()
        if t in _IGNORE_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in ("br", "hr"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        t = (tag or "").lower()
        if t in _IGNORE_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = html.unescape("".join(self._parts))
        lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.splitlines()]
        cleaned: list[str] = []
        blank_run = 0
        for ln in lines:
            if not ln:
                blank_run += 1
                if blank_run <= 1 and cleaned:
                    cleaned.append("")
            else:
                blank_run = 0
                cleaned.append(ln)
        return "\n".join(cleaned).strip()


def html_to_safe_text(html_str: str) -> str:
    """将 HTML 字符串安全提取为纯文本，不执行脚本、不加载任何外部资源或图片。"""
    if not html_str:
        return ""
    parser = _SafeHTMLTextParser()
    try:
        parser.feed(html_str)
        parser.close()
    except Exception:  # noqa: BLE001
        return ""
    return parser.get_text()


def _decode_part_bytes(payload: bytes, charset: str | None) -> str:
    for enc in (charset, "utf-8", "gb18030", "latin-1"):
        if not enc:
            continue
        try:
            return payload.decode(enc, errors="replace")
        except LookupError:
            continue
    return payload.decode("utf-8", errors="replace")


def extract_safe_body_from_bytes(
    raw_bytes: bytes,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[str, str, bool]:
    """从 RFC822/MIME 原始字节安全提取正文（优先 text/plain，其次 text/html；跳过附件；限制大小）。"""
    truncated = len(raw_bytes) > max_bytes
    clipped = raw_bytes[:max_bytes] if truncated else raw_bytes
    msg = email.message_from_bytes(clipped)

    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = (part.get_content_type() or "").lower()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, (bytes, bytearray)):
                continue
            text = _decode_part_bytes(bytes(payload), part.get_content_charset())
            if ctype == "text/plain" and text.strip():
                plain_parts.append(text.strip())
            elif ctype == "text/html" and text.strip():
                html_parts.append(text)
    else:
        disp = str(msg.get("Content-Disposition") or "").lower()
        if "attachment" not in disp:
            ctype = (msg.get_content_type() or "text/plain").lower()
            payload = msg.get_payload(decode=True)
            if isinstance(payload, (bytes, bytearray)):
                text = _decode_part_bytes(bytes(payload), msg.get_content_charset())
                if ctype == "text/html":
                    html_parts.append(text)
                elif text.strip():
                    plain_parts.append(text.strip())

    if plain_parts:
        out = "\n\n".join(plain_parts).strip()
        src = "text/plain"
    elif html_parts:
        out = "\n\n".join(filter(None, (html_to_safe_text(h) for h in html_parts))).strip()
        src = "text/html"
    else:
        out = ""
        src = "empty"

    if truncated and out:
        out += "\n\n[正文已超过 2 MB 上限，已截断显示]"
    return out, src, truncated


def fetch_message_body(
    account: MailAccount,
    secret: str,
    uid: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[bool, MailBody | None, str]:
    """按账号与 UID 后台安全拉取单封邮件正文（只读打开，限制 2MB，不下载附件）。"""
    factory = client_factory or _default_client_factory
    client = None
    try:
        _check_cancel(cancel_event)
        client = factory(account.host, account.port, account.security, timeout)
        _check_cancel(cancel_event)
        client.login(account.username or account.email, secret)
        _check_cancel(cancel_event)
        if account.provider in ("163", "126"):
            try:
                client.xatom("ID", '("name" "Wisland" "version" "1.0")')
            except Exception:  # noqa: BLE001
                pass
        typ, sel_data = client.select("INBOX", readonly=True)
        if typ != "OK":
            return False, None, "无法打开只读收件箱 INBOX"
        uid_validity = extract_uidvalidity(client, sel_data)
        return _fetch_body_on_open_client(
            client, account, str(uid), uid_validity, cancel_event=cancel_event, max_bytes=max_bytes
        )
    except Exception as exc:  # noqa: BLE001
        return False, None, _friendly_error(exc)
    finally:
        _safe_close(client)


def mark_message_seen_remote(
    account: MailAccount,
    secret: str,
    uid: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
    client_factory=None,
) -> tuple[bool, str]:
    """向 IMAP 服务器回写 \\Seen 已读标记（仅用户显式开启服务器同步时调用）。"""
    factory = client_factory or _default_client_factory
    client = None
    try:
        _check_cancel(cancel_event)
        client = factory(account.host, account.port, account.security, timeout)
        _check_cancel(cancel_event)
        client.login(account.username or account.email, secret)
        _check_cancel(cancel_event)
        if account.provider in ("163", "126"):
            try:
                client.xatom("ID", '("name" "Wisland" "version" "1.0")')
            except Exception:  # noqa: BLE001
                pass
        typ, _ = client.select("INBOX", readonly=False)
        if typ != "OK":
            return False, "无法以可写模式打开 INBOX"
        _check_cancel(cancel_event)
        typ, _ = client.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Seen)")
        if typ != "OK":
            typ, _ = client.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
        if typ != "OK":
            return False, "服务器拒绝更新已读状态"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, _friendly_error(exc)
    finally:
        _safe_close(client)
