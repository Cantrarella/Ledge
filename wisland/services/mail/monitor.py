# -*- coding: utf-8 -*-
"""多账号邮件后台监听服务：IMAP IDLE 为主（不支持时轮询降级），建立 UID baseline 后推送新邮件通知。"""
from __future__ import annotations

import email
import select
import threading
import time

from PySide6 import QtCore

from ...core.config import cfg
from ..notifications.models import NotificationEvent, compute_content_hash
from .imap import (
    DEFAULT_TIMEOUT_S,
    _UID_RE,
    _SEEN_RE,
    _decode_mime_header,
    _default_client_factory,
    _parse_date_epoch,
    _safe_close,
    extract_uidvalidity,
)
from .manager import MailAccountManager
from .models import MailAccount, MailHeader

IDLE_RENEW_S = 240.0


def _supports_idle(client) -> bool:
    caps = getattr(client, "capabilities", ()) or ()
    for c in caps:
        s = c.decode("ascii", errors="ignore") if isinstance(c, (bytes, bytearray)) else str(c)
        if s.upper() == "IDLE":
            return True
    return False


def _uid_int(u: str | bytes) -> int:
    s = u.decode("ascii", errors="ignore") if isinstance(u, (bytes, bytearray)) else str(u)
    try:
        return int(s.strip())
    except ValueError:
        return -1


class _AccountMonitorWorker:
    """单个账号的独立监听线程（IDLE / 轮询）。"""

    def __init__(
        self,
        account: MailAccount,
        secret: str,
        poll_interval_fn,
        on_new_mail_fn,
        client_factory=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        idle_renew_s: float = IDLE_RENEW_S,
        initial_baseline_uid: int | None = None,
        initial_uid_validity: str = "",
    ):
        self.account = account
        self.secret = secret
        self._poll_interval_fn = poll_interval_fn
        self._on_new_mail = on_new_mail_fn
        self._factory = client_factory or _default_client_factory
        self._timeout_s = float(timeout_s)
        self._idle_renew_s = float(idle_renew_s)

        self.stop_ev = threading.Event()
        self._client = None
        self._in_idle = False
        self._idle_lock = threading.Lock()
        self._baseline_uid: int | None = initial_baseline_uid
        self._uid_validity: str = str(initial_uid_validity or "")
        # 仅在首次建基线或 UIDVALIDITY 变更时写入，不随未确认的新邮件批次提前推进
        self._init_cursor: tuple[int, str] | None = (
            (initial_baseline_uid, self._uid_validity)
            if initial_baseline_uid is not None
            else None
        )
        self.thread = threading.Thread(
            target=self._run,
            name=f"wisland-mail-mon-{account.id[:8]}",
            daemon=True,
        )

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        with self._idle_lock:
            cli = self._client
            in_idle = self._in_idle
        if cli is not None and in_idle:
            self._send_idle_done(cli)
        if self.thread.is_alive():
            self.thread.join(timeout=1.5)
        if self.thread.is_alive() and cli is not None:
            _safe_close(cli)

    def _send_idle_done(self, client):
        try:
            send_fn = getattr(client, "send", None)
            if callable(send_fn):
                send_fn(b"DONE\r\n")
            done_fn = getattr(client, "idle_done", None)
            if callable(done_fn):
                done_fn()
        except Exception:  # noqa: BLE001
            pass

    def _run(self):
        backoff = 3.0
        while not self.stop_ev.is_set():
            client = None
            try:
                client = self._factory(
                    self.account.host,
                    self.account.port,
                    self.account.security,
                    self._timeout_s,
                )
                with self._idle_lock:
                    self._client = client
                if self.stop_ev.is_set():
                    break

                client.login(self.account.username or self.account.email, self.secret)
                if self.account.provider in ("163", "126"):
                    try:
                        client.xatom("ID", '("name" "Wisland" "version" "1.0")')
                    except Exception:  # noqa: BLE001
                        pass
                typ, sel_data = client.select("INBOX", readonly=True)
                if typ != "OK":
                    raise RuntimeError("select INBOX failed")

                uv = extract_uidvalidity(client, sel_data)
                uids = self._search_all_uids(client)
                max_uid = max((_uid_int(u) for u in uids), default=0)

                # 首次连接或 UIDVALIDITY 变更：建立 baseline，不通知旧邮件
                if self._baseline_uid is None or (uv and self._uid_validity and uv != self._uid_validity):
                    self._uid_validity = uv
                    self._baseline_uid = max_uid
                    self._init_cursor = (max_uid, uv)
                else:
                    if uv:
                        self._uid_validity = uv
                        if self._init_cursor is not None and not self._init_cursor[1]:
                            self._init_cursor = (self._init_cursor[0], uv)
                    self._check_and_emit_new(client, uids)

                backoff = 3.0
                use_idle = _supports_idle(client)

                while not self.stop_ev.is_set():
                    if use_idle:
                        ok_idle = self._wait_idle_cycle(client)
                        if not ok_idle:
                            use_idle = False
                    else:
                        interval = max(15.0, float(self._poll_interval_fn()))
                        if self.stop_ev.wait(interval):
                            break
                    if self.stop_ev.is_set():
                        break
                    cur_uids = self._search_all_uids(client)
                    self._check_and_emit_new(client, cur_uids)
            except Exception:  # noqa: BLE001
                if self.stop_ev.is_set():
                    break
            finally:
                with self._idle_lock:
                    if self._in_idle and client is not None:
                        self._send_idle_done(client)
                    self._in_idle = False
                    self._client = None
                _safe_close(client)

            if not self.stop_ev.is_set():
                self.stop_ev.wait(backoff)
                backoff = min(60.0, backoff * 2.0)

    def _search_all_uids(self, client) -> list[str]:
        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        return [
            (u.decode("ascii", errors="ignore") if isinstance(u, (bytes, bytearray)) else str(u))
            for u in data[0].split() if u
        ]

    def _wait_idle_cycle(self, client) -> bool:
        """执行一次 IMAP IDLE 等待，收到 EXISTS 或超时或停止时发送 DONE 退出 IDLE。"""
        # 优先支持测试/自定义 client 的 idle_wait(timeout, stop_ev)
        idle_wait_fn = getattr(client, "idle_wait", None)
        if callable(idle_wait_fn):
            with self._idle_lock:
                self._in_idle = True
            try:
                idle_wait_fn(self._idle_renew_s, self.stop_ev)
            finally:
                with self._idle_lock:
                    self._in_idle = False
            return True

        sock = getattr(client, "sock", None)
        if sock is None:
            return False

        try:
            tag = client._new_tag() if hasattr(client, "_new_tag") else b"IDL1"
            if isinstance(tag, str):
                tag = tag.encode("ascii")
            client.send(tag + b" IDLE\r\n")
            line = client.readline()
            if not line or not line.startswith(b"+"):
                return False

            with self._idle_lock:
                self._in_idle = True
            deadline = time.monotonic() + self._idle_renew_s
            while not self.stop_ev.is_set():
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                rlist, _, _ = select.select([sock], [], [], min(1.0, remain))
                if rlist:
                    resp = client.readline()
                    if not resp or b"EXISTS" in resp.upper() or b"RECENT" in resp.upper():
                        break
            self._send_idle_done(client)
            # 读掉 DONE 结束响应行（最多读几行避免阻塞）
            for _ in range(4):
                rlist, _, _ = select.select([sock], [], [], 0.25)
                if not rlist:
                    break
                end_line = client.readline()
                if not end_line or tag in end_line:
                    break
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            with self._idle_lock:
                self._in_idle = False

    def _check_and_emit_new(self, client, uids: list[str]):
        base = self._baseline_uid if self._baseline_uid is not None else 0
        new_uids = [u for u in uids if _uid_int(u) > base]
        if not new_uids:
            return
        uid_set = ",".join(new_uids)
        typ, fetched = client.uid("FETCH", uid_set, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK" or not fetched:
            return

        new_hdrs: list[MailHeader] = []
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
            sender = _decode_mime_header(msg.get("From")) or "未知发件人"
            subject = _decode_mime_header(msg.get("Subject")) or "（无主题）"
            received_at = _parse_date_epoch(msg.get("Date"))
            new_hdrs.append(MailHeader(
                uid=uid_s,
                account_id=self.account.id,
                sender=sender,
                subject=subject,
                received_at=received_at,
                is_read=is_read,
                uid_validity=self._uid_validity,
            ))

        if new_hdrs:
            self._baseline_uid = max([base] + [_uid_int(h.uid) for h in new_hdrs])
            self._on_new_mail(self.account.id, self._uid_validity, new_hdrs)


class MailMonitorService(QtCore.QObject):
    """后台监听所有已启用邮箱账号的新邮件，并接入 NotificationController。"""

    _internal_new_mail = QtCore.Signal(str, str, list, object)  # account_id, uid_validity, list[MailHeader], src_worker

    def __init__(
        self,
        manager: MailAccountManager,
        controller=None,
        config_obj=None,
        client_factory=None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        idle_renew_s: float = IDLE_RENEW_S,
        parent=None,
    ):
        super().__init__(parent)
        self._mgr = manager
        self._ctrl = controller
        self._cfg = config_obj or cfg
        self._client_factory = client_factory
        self._timeout_s = float(timeout_s)
        self._idle_renew_s = float(idle_renew_s)

        self._running = False
        self._workers: dict[str, _AccountMonitorWorker] = {}
        self._last_cursor: dict[str, tuple[int | None, str]] = {}
        self._unread_by_account: dict[str, set[tuple[str, str]]] = {}
        self._internal_new_mail.connect(self._handle_new_mail_on_main)
        self._mgr.accounts_changed.connect(self.sync_accounts)

    def start(self):
        self._running = True
        self.sync_accounts()

    def _snapshot_worker_cursors(self):
        """仅同步首次建基线或 UIDVALIDITY 变更后的初始游标；已收到新邮件的推进以主线程确认处理为准。"""
        for aid, w in self._workers.items():
            init_cur = getattr(w, "_init_cursor", None)
            if init_cur is None:
                continue
            init_uid, init_uv = init_cur
            prev = self._last_cursor.get(aid)
            if prev is None:
                self._last_cursor[aid] = (init_uid, init_uv)
            else:
                prev_uid, prev_uv = prev
                if init_uv and prev_uv and init_uv != prev_uv:
                    self._last_cursor[aid] = (init_uid, init_uv)
                elif prev_uid is None or init_uid > prev_uid:
                    self._last_cursor[aid] = (init_uid, init_uv or prev_uv)
                elif init_uv and not prev_uv:
                    self._last_cursor[aid] = (prev_uid, init_uv)

    def stop(self):
        self._running = False
        self._snapshot_worker_cursors()
        workers = list(self._workers.values())
        self._workers.clear()
        for w in workers:
            w.stop()

    def apply_config(self):
        """休眠唤醒或配置变化时，保留各账号主线程已确认的 UID 游标并重建监听连接，确保不漏掉未处理的邮件。"""
        if not self._running:
            return
        self._snapshot_worker_cursors()
        workers = list(self._workers.values())
        self._workers.clear()
        for w in workers:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass
        self.sync_accounts()

    @QtCore.Slot()
    def sync_accounts(self):
        if not self._running:
            return
        enabled_map = {a.id: a for a in self._mgr.list_accounts() if a.enabled}

        # 停止已删除或已停用的账号监听，并清理其未读集合与游标
        for aid in list(self._workers.keys()):
            if aid not in enabled_map:
                w = self._workers.pop(aid)
                w.stop()
                self._unread_by_account.pop(aid, None)
                self._last_cursor.pop(aid, None)
        for aid in list(self._unread_by_account.keys()):
            if aid not in enabled_map:
                self._unread_by_account.pop(aid, None)
        for aid in list(self._last_cursor.keys()):
            if aid not in enabled_map:
                self._last_cursor.pop(aid, None)
        self._sync_controller_mail_count()

        # 为新启用的账号启动独立监听线程
        for aid, acc in enabled_map.items():
            if aid in self._workers:
                continue
            secret = self._mgr._creds.read_secret(acc.credential_key)
            if not secret:
                continue
            init_uid, init_uv = self._last_cursor.get(aid, (None, ""))
            worker_box: list[_AccountMonitorWorker] = []
            w = _AccountMonitorWorker(
                acc,
                secret,
                poll_interval_fn=lambda: int(self._cfg.get("mail.interval_s", 120) or 120),
                on_new_mail_fn=lambda a_id, uv, hdrs, wb=worker_box: self._internal_new_mail.emit(
                    a_id, uv, hdrs, wb[0] if wb else None
                ),
                client_factory=self._client_factory,
                timeout_s=self._timeout_s,
                idle_renew_s=self._idle_renew_s,
                initial_baseline_uid=init_uid,
                initial_uid_validity=init_uv,
            )
            worker_box.append(w)
            self._workers[aid] = w
            w.start()

    @QtCore.Slot(str, str, list, object)
    def _handle_new_mail_on_main(self, account_id: str, uid_validity: str, hdrs: list, src_worker=None):
        if not self._running or not hdrs:
            return
        # 丢弃已停用/删除账号或已销毁旧监听线程排队的过期批次
        cur_w = self._workers.get(account_id)
        if cur_w is None:
            return
        if src_worker is not None and cur_w is not src_worker:
            return
        self._mgr.merge_incoming_headers(account_id, hdrs)

        # 仅在主线程成功处理当前活跃批次后，再推进恢复游标
        batch_max_uid = max((_uid_int(h.uid) for h in hdrs), default=0)
        batch_uv = str(uid_validity or getattr(cur_w, "_uid_validity", "") or "")
        prev = self._last_cursor.get(account_id)
        if prev is None or (batch_uv and prev[1] and batch_uv != prev[1]):
            self._last_cursor[account_id] = (batch_max_uid, batch_uv)
        else:
            prev_uid = prev[0] if prev[0] is not None else 0
            self._last_cursor[account_id] = (max(prev_uid, batch_max_uid), batch_uv or prev[1])

        unread_set = self._unread_by_account.setdefault(account_id, set())
        for h in hdrs:
            if not h.is_read:
                unread_set.add((str(uid_validity or h.uid_validity or ""), str(h.uid)))

            if self._ctrl is None:
                continue
            uv = str(uid_validity or h.uid_validity or "0")
            ev = NotificationEvent(
                event_id=f"mail:{account_id}:{uv}:{h.uid}",
                source_key="mail",
                app_name="邮件",
                app_identity=f"mail:{account_id}",
                account_key=account_id,
                title=h.subject,
                sender=h.sender,
                body="",  # 默认横幅只显示发件人/标题，绝不显示正文
                created_at=float(h.received_at or time.time()),
                updated_at=float(h.received_at or time.time()),
                content_hash=compute_content_hash(h.subject, h.sender, f"{uv}:{h.uid}"),
                can_open=True,
                raw_kind="mail_imap",
                notification_id=f"{uv}:{h.uid}",
                creation_time=int(h.received_at or 0),
                meta={
                    "account_id": account_id,
                    "uid_validity": uv,
                    "uid": str(h.uid),
                    "sender": h.sender,
                    "subject": h.subject,
                    "received_at": float(h.received_at or 0.0),
                },
            )
            self._ctrl.ingest_event(ev)

        self._sync_controller_mail_count()

    def unread_count_for_account(self, account_id: str) -> int:
        return len(self._unread_by_account.get(account_id, set()))

    def mark_account_seen(self, account_id: str, uid: str | None = None, uid_validity: str = ""):
        """查看对应邮件或该账号后，只清除该账号的未读，不影响其他账号。"""
        unread_set = self._unread_by_account.get(account_id)
        if not unread_set:
            return
        if uid is None:
            unread_set.clear()
        else:
            u = str(uid)
            to_del = {k for k in unread_set if k[1] == u and (not uid_validity or not k[0] or k[0] == uid_validity)}
            unread_set.difference_update(to_del)
        if not unread_set:
            self._unread_by_account.pop(account_id, None)
        self._sync_controller_mail_count()

    def _sync_controller_mail_count(self):
        if self._ctrl is None:
            return
        total = sum(len(s) for s in self._unread_by_account.values())
        cur = int(self._ctrl.counts.get("mail", 0) or 0)
        if total == 0:
            if cur > 0:
                self._ctrl.clear("mail")
        elif total != cur:
            self._ctrl.counts["mail"] = total
            self._ctrl.counts_changed.emit(dict(self._ctrl.counts))
