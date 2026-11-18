# -*- coding: utf-8 -*-
"""IMAP 连接测试与最小元数据读取（仅只读 EXAMINE + BODY.PEEK 头字段，不下载正文与附件）。"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
import socket
import ssl
import threading
import time

from .models import MailAccount, MailHeader

DEFAULT_TIMEOUT_S = 10.0
_UID_RE = re.compile(rb"UID\s+(\d+)", re.I)
_SEEN_RE = re.compile(rb"\\Seen\b", re.I)


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
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            return False, [], "无法打开只读收件箱 INBOX"
        _check_cancel(cancel_event)

        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return True, [], ""
        uids = [u for u in data[0].split() if u]
        if not uids:
            return True, [], ""
        target_uids = uids[-max(1, int(limit)):]
        uid_set = b",".join(target_uids).decode("ascii", errors="ignore")
        _check_cancel(cancel_event)

        typ, fetched = client.uid("FETCH", uid_set, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK" or not fetched:
            return True, [], ""

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
            ))
        headers.sort(key=lambda h: h.received_at, reverse=True)
        return True, headers, ""
    except Exception as exc:  # noqa: BLE001
        return False, [], _friendly_error(exc)
    finally:
        _safe_close(client)
