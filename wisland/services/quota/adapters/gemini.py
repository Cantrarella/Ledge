# -*- coding: utf-8 -*-
"""Gemini 网页会员额度适配器：仅请求 gemini.google.com，校验同站重定向与 Google 登录会话。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ....core.http import open_no_redirect
from ..models import AccountStatus, QuotaSnapshot, QuotaWindow

GEMINI_ENDPOINT = "https://gemini.google.com/_/BardChatUi/graphql/schemas/GEMINI_WEB_GRAPHQL/executeQuery"
GEMINI_SIGNATURE = "2/dWbCvkVB0X16r9NPnxifeOAAqdCqMf0xeWvau9yPy7g="
GEMINI_DEFAULT_TIMEOUT_S = 12.0
GEMINI_MAX_BYTES = 1024 * 1024
_GEMINI_SESSION_COOKIES = frozenset({"SID", "__Secure-1PSID", "__Secure-3PSID"})


def validate_gemini_email(raw: str) -> tuple[bool, str]:
    val = str(raw or "").strip().lower()
    if (
        not val
        or len(val) > 254
        or not val.isascii()
        or any(ch.isspace() for ch in val)
        or val.count("@") != 1
        or any(not part for part in val.split("@"))
    ):
        return False, "请填写这个 Google 账号的完整邮箱"
    return True, val


def validate_gemini_cookie(raw: str) -> tuple[bool, str]:
    s = str(raw or "").strip()
    if s.lower().startswith("cookie:"):
        s = s[7:].strip()
    if not s or len(s) > 32768 or not s.isascii():
        return False, "请粘贴有效的 Cookie 请求头"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in s):
        return False, "Cookie 包含非法控制字符"
    has_session = False
    for part in s.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k.strip() in _GEMINI_SESSION_COOKIES and v.strip():
            has_session = True
            break
    if not has_session:
        return False, "Cookie 中没有 Google 登录会话（缺少 SID/__Secure-1PSID/__Secure-3PSID）"
    return True, s


def gemini_identity(email: str) -> str:
    h = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"gemini:{h}"


def build_gemini_request_url(email: str) -> str:
    variables = json.dumps(
        {
            "g1Request": {"entrypoints": ["GEMINI_USAGE_SETTINGS_UPGRADE_BANNER"]},
            "workspaceRequest": {"recommendationsForSurfaceRequests": []},
        },
        separators=(",", ":"),
    )
    qs = urllib.parse.urlencode(
        {
            "query_signature": GEMINI_SIGNATURE,
            "variables": variables,
            "authuser": email,
        }
    )
    return f"{GEMINI_ENDPOINT}?{qs}"


def is_safe_gemini_redirect(current_url: str, location: str) -> str | None:
    """只允许跟随 https://gemini.google.com 的同站 executeQuery 重定向，禁止跨域转发 Cookie。"""
    try:
        target = urllib.parse.urljoin(current_url, location)
        u = urllib.parse.urlparse(target)
    except Exception:
        return None
    if u.scheme != "https" or u.hostname != "gemini.google.com":
        return None
    if u.port not in (None, 443):
        return None
    if u.username or u.password:
        return None
    if not u.path.endswith("/_/BardChatUi/graphql/schemas/GEMINI_WEB_GRAPHQL/executeQuery"):
        return None
    return target


def _parse_iso_ts(val) -> float | None:
    if not isinstance(val, str) or not val.strip():
        return None
    try:
        return dt.datetime.fromisoformat(val.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_gemini_usage(payload, account_id: str, fetched_at: float | None = None) -> QuotaSnapshot:
    """解析 Gemini executeQuery JSON 响应。"""
    ts = fetched_at if fetched_at is not None else time.time()
    if not isinstance(payload, list):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Gemini 额度接口已变化，暂时无法读取",
        )

    data_obj = None
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("data"), dict) and "viewer" in item["data"]:
            data_obj = item["data"]
            break
    if data_obj is None:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Gemini 额度接口已变化，暂时无法读取",
        )

    viewer = data_obj.get("viewer")
    if viewer is None:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.AUTH_ERROR.value,
            last_error="Gemini 登录已失效，请重新连接",
        )
    if not isinstance(viewer, dict):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Gemini 额度接口已变化，暂时无法读取",
        )

    usage = (viewer.get("rpcs") or {}).get("getUsageInfo")
    if not isinstance(usage, dict) or not isinstance(usage.get("pools"), list):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Gemini 额度接口已变化，暂时无法读取",
        )

    pools = usage["pools"]
    windows: list[QuotaWindow] = []
    for kind, sec, label in (("GXU_FIVE_HOURLY", 18000, "5 小时"), ("GXU_WEEKLY", 604800, "本周")):
        pool = next((p for p in pools if isinstance(p, dict) and p.get("poolType") == kind), None)
        if pool is None:
            continue
        used = pool.get("percentageUsed")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(float(used)) or float(used) < 0.0:
            continue
        rem = max(0.0, min(100.0, 100.0 - float(used) * 100.0))
        rst = _parse_iso_ts(((pool.get("gxu") or {}).get("windowRefreshesAt")))
        windows.append(
            QuotaWindow(
                label=label,
                remaining_percent=round(rem, 1),
                reset_at=rst,
                amount=float(sec),
            )
        )

    if not windows:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="此 Google 账号暂未提供 Gemini 订阅额度",
        )

    plan_name = "AI Pro" if usage.get("subscriptionTier") == "TIER1" else ""
    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=ts,
        status=AccountStatus.OK.value,
        windows=windows,
        plan_name=plan_name,
        last_error="",
        raw_stale=False,
    )


def decode_gemini_response(raw_text: str, account_id: str, fetched_at: float | None = None) -> QuotaSnapshot:
    ts = fetched_at if fetched_at is not None else time.time()
    s = str(raw_text or "").lstrip()
    if s.startswith(")]}'"):
        s = s[4:].lstrip()
    if s.startswith("<"):
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.AUTH_ERROR.value,
            last_error="Gemini 登录已失效，请重新连接",
        )
    try:
        payload = json.loads(s)
    except Exception:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=ts,
            status=AccountStatus.PARSE_ERROR.value,
            last_error="Gemini 返回了无效的 JSON 数据",
        )
    return parse_gemini_usage(payload, account_id=account_id, fetched_at=ts)


def fetch_gemini_usage(
    account_id: str,
    secret_json: str,
    timeout_s: float = GEMINI_DEFAULT_TIMEOUT_S,
    http_opener=None,
) -> QuotaSnapshot:
    """使用保存的 Google 会话请求 Gemini 用量接口。"""
    now_ts = time.time()
    try:
        sess = json.loads(secret_json or "{}")
    except Exception:
        sess = {}
    if not isinstance(sess, dict):
        sess = {}

    ok_e, email_or_err = validate_gemini_email(sess.get("email", ""))
    ok_c, cookie_or_err = validate_gemini_cookie(sess.get("cookie", ""))
    if not ok_e or not ok_c:
        return QuotaSnapshot(
            account_id=account_id,
            fetched_at=now_ts,
            status=AccountStatus.AUTH_ERROR.value,
            last_error="Gemini 会话无效或已过期，请重新连接",
        )

    current_url = build_gemini_request_url(email_or_err)
    for _ in range(3):
        req = urllib.request.Request(current_url, method="GET")
        req.add_header("Cookie", cookie_or_err)
        req.add_header("Accept", "application/json")
        req.add_header("Referer", "https://gemini.google.com/usage")
        req.add_header("User-Agent", "Mozilla/5.0 Wisland")

        try:
            cm = open_no_redirect(req, timeout_s, http_opener=http_opener)
            with cm as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if 300 <= status < 400:
                    loc = resp.headers.get("Location", "") if getattr(resp, "headers", None) else ""
                    nxt = is_safe_gemini_redirect(current_url, loc)
                    if not nxt:
                        return QuotaSnapshot(
                            account_id=account_id,
                            fetched_at=now_ts,
                            status=AccountStatus.AUTH_ERROR.value,
                            last_error="Gemini 登录已失效，请重新连接",
                        )
                    current_url = nxt
                    continue
                raw = resp.read(GEMINI_MAX_BYTES)
                return decode_gemini_response(raw.decode("utf-8", errors="replace"), account_id=account_id)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            if 300 <= code < 400:
                loc = e.headers.get("Location", "") if getattr(e, "headers", None) else ""
                nxt = is_safe_gemini_redirect(current_url, loc)
                if nxt:
                    current_url = nxt
                    continue
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error="Gemini 登录已失效，请重新连接",
                )
            if code in (401, 403):
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.AUTH_ERROR.value,
                    last_error="Gemini 登录已失效，请重新连接",
                )
            if code == 429:
                return QuotaSnapshot(
                    account_id=account_id,
                    fetched_at=now_ts,
                    status=AccountStatus.RATE_LIMITED.value,
                    last_error="Gemini 请求过于频繁 (429)，请稍后重试",
                )
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error=f"Gemini 额度服务暂不可用 (HTTP {code})",
            )
        except (urllib.error.URLError, TimeoutError, OSError):
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="无法连接 Gemini，请检查网络或代理",
            )
        except Exception:
            return QuotaSnapshot(
                account_id=account_id,
                fetched_at=now_ts,
                status=AccountStatus.NETWORK_ERROR.value,
                last_error="Gemini 额度查询异常",
            )

    return QuotaSnapshot(
        account_id=account_id,
        fetched_at=now_ts,
        status=AccountStatus.AUTH_ERROR.value,
        last_error="Gemini 登录已失效，请重新连接",
    )


_GEMINI_EMAIL_JS = r"""(() => {
  if (location.origin !== 'https://gemini.google.com') return '';
  const selectors = [
    'a[href^="https://accounts.google.com/SignOutOptions"][aria-label]',
    'a[aria-label*="@"]',
    'button[aria-label*="@"]',
    '[data-email*="@"]',
    'img[alt*="@"]',
  ];
  for (const sel of selectors) {
    const nodes = document.querySelectorAll(sel);
    for (const el of nodes) {
      const raw = el.getAttribute('aria-label') || el.getAttribute('data-email') || el.getAttribute('alt') || '';
      const m = raw.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
      if (m) return m[0];
    }
  }
  return '';
})()"""

_GEMINI_ALLOWED_COOKIE_DOMAINS = frozenset({
    ".google.com",
    "google.com",
    ".gemini.google.com",
    "gemini.google.com",
})


def is_gemini_usage_page(raw_url: str) -> bool:
    """校验是否为 https://gemini.google.com/usage 或 /u/<n>/usage 页面。"""
    try:
        u = urllib.parse.urlparse(str(raw_url or ""))
    except Exception:
        return False
    if u.scheme != "https" or u.hostname != "gemini.google.com":
        return False
    if u.port not in (None, 443) or u.username or u.password:
        return False
    path = u.path.rstrip("/")
    return path == "/usage" or bool(re.fullmatch(r"/u/\d+/usage", path))


def is_gemini_logged_in_page(raw_url: str) -> bool:
    """校验是否为 https://gemini.google.com 的已跳转工作页面（含 /usage、/app 或根路径）。"""
    if is_gemini_usage_page(raw_url):
        return True
    try:
        u = urllib.parse.urlparse(str(raw_url or ""))
    except Exception:
        return False
    if u.scheme != "https" or u.hostname != "gemini.google.com":
        return False
    if u.port not in (None, 443) or u.username or u.password:
        return False
    return True


def build_gemini_cookie_header(cookies: list[dict]) -> tuple[bool, str]:
    """从 CDP Network.getCookies 结果中过滤 Google/Gemini 域名 Cookie 并校验会话完整性。"""
    pairs: list[str] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        dom = str(c.get("domain") or "").lower()
        if dom not in _GEMINI_ALLOWED_COOKIE_DOMAINS:
            continue
        name = str(c.get("name") or "").strip()
        val = str(c.get("value") or "")
        if name:
            pairs.append(f"{name}={val}")
    return validate_gemini_cookie("; ".join(pairs))


def find_chromium_browser() -> Path | None:
    """查找本机 Edge 或 Chrome 可执行文件（仅用于拉起独立临时登录窗口）。"""
    env_pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    env_pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    env_local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(env_pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(env_pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(env_local) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(env_pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(env_pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(env_local) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for cmd in ("msedge", "chrome"):
        found = shutil.which(cmd)
        if found:
            return Path(found)
    return None


class _LocalCdpSocket:
    """极简本地 RFC 6455 WebSocket 客户端，仅连接 127.0.0.1 用于临时浏览器的 CDP 通信。"""

    def __init__(self, ws_url: str, timeout_s: float = 5.0):
        u = urllib.parse.urlparse(ws_url)
        if u.scheme != "ws" or u.hostname not in ("127.0.0.1", "localhost") or not u.port:
            raise ValueError("仅允许连接本机 CDP 调试地址")
        self._sock = socket.create_connection((u.hostname, int(u.port)), timeout=timeout_s)
        self._sock.settimeout(0.5)
        self._buf = bytearray()
        self._next_id = 0

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = u.path + (f"?{u.query}" if u.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._sock.sendall(req)

        hdr = b""
        while b"\r\n\r\n" not in hdr:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP 握手失败")
            hdr += chunk
        head_end = hdr.index(b"\r\n\r\n") + 4
        if b"101" not in hdr[:head_end].split(b"\r\n", 1)[0]:
            raise ConnectionError("CDP 握手未返回 101")
        self._buf.extend(hdr[head_end:])

    def _recv_exact(self, n: int, deadline: float, cancel_event: threading.Event | None) -> bytes:
        while len(self._buf) < n:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("cdp timeout")
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("cdp closed")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _send_text(self, text: str):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        ln = len(payload)
        if ln < 126:
            header.append(0x80 | ln)
        elif ln < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", ln))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", ln))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_frame(self, deadline: float, cancel_event: threading.Event | None) -> tuple[int, bytes]:
        b1, b2 = self._recv_exact(2, deadline, cancel_event)
        opcode = b1 & 0x0F
        has_mask = bool(b2 & 0x80)
        ln = b2 & 0x7F
        if ln == 126:
            (ln,) = struct.unpack("!H", self._recv_exact(2, deadline, cancel_event))
        elif ln == 127:
            (ln,) = struct.unpack("!Q", self._recv_exact(8, deadline, cancel_event))
        mask_key = self._recv_exact(4, deadline, cancel_event) if has_mask else b""
        data = self._recv_exact(ln, deadline, cancel_event) if ln else b""
        if has_mask:
            data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        return opcode, data

    def call(
        self,
        method: str,
        params: dict | None = None,
        cancel_event: threading.Event | None = None,
        timeout_s: float = 15.0,
    ) -> dict:
        self._next_id += 1
        req_id = self._next_id
        self._send_text(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout_s
        while True:
            opcode, data = self._recv_frame(deadline, cancel_event)
            if opcode == 0x8:
                raise ConnectionError("cdp closed")
            if opcode == 0x9:
                # Ping -> Pong
                continue
            if opcode != 0x1:
                continue
            msg = json.loads(data.decode("utf-8", errors="replace"))
            if msg.get("id") != req_id:
                continue
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result") or {}

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


def connect_gemini_browser(
    account_id: str,
    expected_email: str = "",
    cancel_event: threading.Event | None = None,
    http_opener=None,
    timeout_s: float = 300.0,
) -> tuple[bool, str, dict | None, QuotaSnapshot | None]:
    """拉起独立临时 Edge/Chrome 窗口打开 gemini.google.com/usage，自动提取邮箱与会话 Cookie 并立即清理临时目录。"""
    if cancel_event is not None and cancel_event.is_set():
        return False, "已取消 Gemini 连接", None, None

    wanted_email = ""
    if str(expected_email or "").strip():
        ok_e, norm_or_err = validate_gemini_email(expected_email)
        if not ok_e:
            return False, norm_or_err, None, None
        wanted_email = norm_or_err

    browser_exe = find_chromium_browser()
    if browser_exe is None:
        return False, "未找到 Edge 或 Chrome 浏览器，请改用手动粘贴 Cookie", None, None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    temp_dir = tempfile.mkdtemp(prefix="wisland-gemini-")
    proc: subprocess.Popen | None = None
    cdp: _LocalCdpSocket | None = None

    try:
        args = [
            str(browser_exe),
            f"--user-data-dir={temp_dir}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=msEdgeStartupBoost",
            "--new-window",
            "https://gemini.google.com/usage",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        start_ts = time.monotonic()
        deadline = start_ts + timeout_s
        list_url = f"http://127.0.0.1:{port}/json/list"
        cdp_seen = False
        missed_cdp = 0

        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return False, "已取消 Gemini 连接", None, None

            targets = None
            try:
                with urllib.request.urlopen(list_url, timeout=1.5) as resp:
                    targets = json.loads(resp.read(1024 * 1024).decode("utf-8", errors="replace"))
            except Exception:
                targets = None

            if isinstance(targets, list):
                page_targets = [
                    t for t in targets
                    if isinstance(t, dict)
                    and t.get("type") == "page"
                    and t.get("webSocketDebuggerUrl")
                ]
                if page_targets:
                    cdp_seen = True
                    missed_cdp = 0
                elif cdp_seen:
                    missed_cdp += 1
                    if missed_cdp >= 3:
                        return False, "浏览器窗口已关闭，未完成 Gemini 登录", None, None

                # 优先匹配 /usage 页面，其次匹配登录后留在 gemini.google.com 的其他页面
                page_target = next(
                    (t for t in page_targets if is_gemini_usage_page(t.get("url", ""))),
                    None,
                ) or next(
                    (t for t in page_targets if is_gemini_logged_in_page(t.get("url", ""))),
                    None,
                )
                if page_target is not None:
                    ws_url = str(page_target["webSocketDebuggerUrl"])
                    try:
                        cdp = _LocalCdpSocket(ws_url, timeout_s=3.0)
                        ev_res = cdp.call(
                            "Runtime.evaluate",
                            {"expression": _GEMINI_EMAIL_JS, "returnByValue": True},
                            cancel_event=cancel_event,
                        )
                        raw_email = str((ev_res.get("result") or {}).get("value") or "").strip()
                        ok_e, cur_email = validate_gemini_email(raw_email)
                        if not ok_e and wanted_email:
                            ok_e, cur_email = True, wanted_email
                        if ok_e and (not wanted_email or cur_email == wanted_email):
                            ck_res = cdp.call(
                                "Network.getCookies",
                                {"urls": ["https://gemini.google.com/", "https://accounts.google.com/"]},
                                cancel_event=cancel_event,
                            )
                            ok_c, cookie_hdr = build_gemini_cookie_header(ck_res.get("cookies") or [])
                            if ok_c:
                                sess = {"email": cur_email, "cookie": cookie_hdr}
                                snap = fetch_gemini_usage(
                                    account_id=account_id,
                                    secret_json=json.dumps(sess, ensure_ascii=False),
                                    http_opener=http_opener,
                                )
                                if snap.status == AccountStatus.OK.value:
                                    return True, "", sess, snap
                                if snap.status != AccountStatus.AUTH_ERROR.value:
                                    return False, snap.last_error or "Gemini 额度查询失败", None, None
                    except InterruptedError:
                        return False, "已取消 Gemini 连接", None, None
                    except Exception:
                        pass
                    finally:
                        if cdp is not None:
                            cdp.close()
                            cdp = None
            else:
                if proc.poll() is not None:
                    if cdp_seen:
                        missed_cdp += 1
                        if missed_cdp >= 2:
                            return False, "浏览器窗口已关闭，未完成 Gemini 登录", None, None
                    elif (time.monotonic() - start_ts) >= 4.0:
                        return False, "独立登录窗口未能连接调试端口，请关闭已有浏览器后重试或改用手动粘贴 Cookie", None, None

            for _ in range(10):
                if cancel_event is not None and cancel_event.is_set():
                    return False, "已取消 Gemini 连接", None, None
                time.sleep(0.1)

        return False, "等待 Gemini 浏览器登录超时，请重新连接", None, None
    finally:
        if cdp is not None:
            cdp.close()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        shutil.rmtree(temp_dir, ignore_errors=True)
