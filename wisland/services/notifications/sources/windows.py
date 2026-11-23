# -*- coding: utf-8 -*-
"""Windows 官方通知采集源（UserNotificationListener + 只读 wpndatabase.db 兜底核对发送方）。
所有 WinRT / COM 调用均封装在后台线程，绝不向页面或控制层暴露 WinRT 对象。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
import time
from typing import Callable

from PySide6 import QtCore

from ..identity import bind_client_target, classify_source
from ..models import NotificationEvent, PermissionStatus, compute_content_hash

WPN_DB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
POLL_INTERVAL_S = 1.2
SLEEP_GAP_S = 120.0


def open_windows_notification_settings() -> bool:
    """打开 Windows 系统“通知和操作”设置页。"""
    try:
        os.startfile("ms-settings:notifications")
        return True
    except Exception:  # noqa: BLE001
        return False


def _lookup_wpndb_app_identity(notification_id: str) -> str:
    """仅在官方 API 缺少 AppUserModelId 时，只读查询 wpndatabase.db 核对发送方 PrimaryId。
    绝不修改数据库，绝不把数据库正文当作主数据源。
    """
    if not notification_id or not os.path.isfile(WPN_DB):
        return ""
    try:
        nid = int(notification_id)
    except (TypeError, ValueError):
        return ""
    conn = None
    try:
        uri = f"file:///{WPN_DB.replace(os.sep, '/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.4)
        cur = conn.execute(
            "SELECT h.PrimaryId FROM Notification n "
            "LEFT JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
            "WHERE n.Id = ? LIMIT 1",
            (nid,),
        )
        row = cur.fetchone()
        return str(row[0] or "") if row else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _datetime_to_ticks_and_epoch(dt_val) -> tuple[int, float]:
    """将 WinRT DateTime（universal_time: 1601 起的 100ns）或 datetime 转为 (ticks, epoch_s)。"""
    try:
        if hasattr(dt_val, "universal_time"):
            ticks = int(dt_val.universal_time)
            epoch = max(0.0, (ticks - 116444736000000000) / 1e7)
            return ticks, epoch
        if hasattr(dt_val, "timestamp"):
            ep = float(dt_val.timestamp())
            ticks = int(round(ep * 1e7)) + 116444736000000000
            return ticks, ep
    except Exception:  # noqa: BLE001
        pass
    now = time.time()
    return int(round(now * 1e7)), now


class _WinRTBackend:
    """封装 WinRT UserNotificationListener 调用，便于真实运行与测试替身替换。"""

    def __init__(self):
        self._listener = None

    def is_supported(self) -> bool:
        if sys.platform != "win32":
            return False
        ver = sys.getwindowsversion()
        # Windows 10 (10.0) 及以上才支持 UserNotificationListener
        return ver.major >= 10

    def _ensure_listener(self):
        if self._listener is None:
            from winrt.windows.ui.notifications.management import UserNotificationListener
            self._listener = UserNotificationListener.current
        return self._listener

    def get_access_status(self) -> PermissionStatus:
        if not self.is_supported():
            return PermissionStatus.UNSUPPORTED
        try:
            from winrt.windows.ui.notifications.management import UserNotificationListenerAccessStatus
            listener = self._ensure_listener()
            st = listener.get_access_status()
            if st == UserNotificationListenerAccessStatus.ALLOWED:
                return PermissionStatus.ALLOWED
            if st == UserNotificationListenerAccessStatus.DENIED:
                return PermissionStatus.DENIED
            return PermissionStatus.NOT_DETERMINED
        except Exception:  # noqa: BLE001
            return PermissionStatus.UNSUPPORTED

    async def request_access(self) -> PermissionStatus:
        if not self.is_supported():
            return PermissionStatus.UNSUPPORTED
        from winrt.windows.ui.notifications.management import UserNotificationListenerAccessStatus
        listener = self._ensure_listener()
        st = await listener.request_access_async()
        if st == UserNotificationListenerAccessStatus.ALLOWED:
            return PermissionStatus.ALLOWED
        if st == UserNotificationListenerAccessStatus.DENIED:
            return PermissionStatus.DENIED
        return PermissionStatus.NOT_DETERMINED

    async def fetch_toasts(self) -> list[dict]:
        """读取当前所有 Toast 通知并提取为纯字典（不泄漏 WinRT 对象）。"""
        from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
        listener = self._ensure_listener()
        raw_list = await listener.get_notifications_async(NotificationKinds.TOAST)
        out: list[dict] = []
        for n in raw_list:
            try:
                nid = str(getattr(n, "id", "") or "")
                ticks, epoch = _datetime_to_ticks_and_epoch(getattr(n, "creation_time", None))
                app_id = ""
                app_name = ""
                app_info = getattr(n, "app_info", None)
                if app_info is not None:
                    app_id = str(getattr(app_info, "app_user_model_id", "") or "")
                    disp = getattr(app_info, "display_info", None)
                    if disp is not None:
                        app_name = str(getattr(disp, "display_name", "") or "")
                texts: list[str] = []
                notif = getattr(n, "notification", None)
                vis = getattr(notif, "visual", None) if notif is not None else None
                if vis is not None:
                    binding = vis.get_binding(KnownNotificationBindings.toast_generic)
                    if binding is not None:
                        for el in binding.get_text_elements():
                            t = str(getattr(el, "text", "") or "").strip()
                            if t:
                                texts.append(t)
                title = texts[0] if texts else ""
                body = " ".join(texts[1:]).strip() if len(texts) > 1 else ""
                out.append({
                    "notification_id": nid,
                    "creation_time": ticks,
                    "created_at": epoch,
                    "app_identity": app_id,
                    "app_name": app_name,
                    "title": title,
                    "body": body,
                })
            except Exception:  # noqa: BLE001
                continue
        return out

    def release(self):
        self._listener = None


def raw_record_to_event(rec: dict, wpndb_lookup: Callable[[str], str] | None = None) -> NotificationEvent:
    """将纯字典快照转换为标准化 NotificationEvent 并绑定客户端进程/窗口。"""
    nid = str(rec.get("notification_id") or "")
    ticks = int(rec.get("creation_time") or 0)
    created_at = float(rec.get("created_at") or time.time())
    app_id = str(rec.get("app_identity") or "").strip()
    app_name = str(rec.get("app_name") or "").strip()
    title = str(rec.get("title") or "").strip()
    body = str(rec.get("body") or "").strip()

    if not app_id and nid and wpndb_lookup is not None:
        app_id = wpndb_lookup(nid)

    source_key, canonical_name = classify_source(app_identity=app_id, app_name=app_name)
    display_app = canonical_name if source_key in ("wechat", "qq") else (app_name or canonical_name)

    binding = bind_client_target(source_key) if source_key in ("wechat", "qq") else None
    sender = title if (title and title != display_app) else ""
    now = time.time()
    c_hash = compute_content_hash(title, sender, body)
    return NotificationEvent(
        event_id=f"win:{app_id or source_key}:{nid}:{ticks}",
        source_key=source_key,
        app_name=display_app,
        app_identity=app_id or source_key,
        account_key=binding.account_key if binding else "",
        pid=binding.pid if binding else None,
        window_handle=binding.window_handle if binding else None,
        process_started_at=binding.process_started_at if binding else None,
        title=title,
        sender=sender,
        body=body,
        created_at=created_at,
        updated_at=now,
        content_hash=c_hash,
        can_open=binding.can_open if binding else False,
        raw_kind="winrt_toast",
        notification_id=nid,
        creation_time=ticks,
        account_ambiguous=binding.account_ambiguous if binding else False,
    )


class WindowsNotificationSource(QtCore.QObject):
    """后台运行的 Windows 通知监听源（不阻塞 UI，退出/关闭时清理 WinRT 与线程）。"""

    baseline_captured = QtCore.Signal(list)     # list[NotificationEvent]
    events_sampled = QtCore.Signal(list)        # list[NotificationEvent]
    status_updated = QtCore.Signal(str, bool, str)  # (permission_status, listening, last_error)

    def __init__(self, backend=None, wpndb_lookup: Callable[[str], str] | None = None, parent=None):
        super().__init__(parent)
        self._backend = backend or _WinRTBackend()
        self._wpndb_lookup = wpndb_lookup if wpndb_lookup is not None else _lookup_wpndb_app_identity
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_ev = threading.Event()
        self._wake_ev = threading.Event()
        self._request_perm_flag = False
        self._baseline_done = False
        self._last_sample_mono = 0.0
        self.permission_status: PermissionStatus = PermissionStatus.UNKNOWN
        self.listening: bool = False
        self.last_error: str = ""

    def start(self):
        """幂等启动后台监听线程。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_ev.clear()
            self._wake_ev.clear()
            self._baseline_done = False
            self._last_sample_mono = time.monotonic()
            self._thread = threading.Thread(target=self._run_loop, name="wisland-winrt-notify", daemon=True)
            self._thread.start()

    def stop(self):
        """幂等停止后台监听线程并释放 WinRT 对象引用。"""
        with self._lock:
            th = self._thread
            self._stop_ev.set()
            self._wake_ev.set()
        if th is not None and th.is_alive():
            th.join(timeout=1.8)
        with self._lock:
            self._thread = None
            self._baseline_done = False
            self.listening = False
        self.status_updated.emit(self.permission_status.value, False, self.last_error)

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def request_permission(self):
        """请求重新向 Windows 申请通知访问权限。"""
        with self._lock:
            self._request_perm_flag = True
            self._wake_ev.set()
        if not self.is_running():
            self.start()

    def reset_baseline(self):
        """下次采样重新建立基线（例如休眠恢复或重连时）。"""
        with self._lock:
            self._baseline_done = False

    def _emit_status(self, perm: PermissionStatus, listening: bool, err: str = ""):
        self.permission_status = perm
        self.listening = listening
        self.last_error = err
        self.status_updated.emit(perm.value, listening, err)

    def _run_loop(self):
        import ctypes
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        com_hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            perm = self._backend.get_access_status()
            if perm == PermissionStatus.NOT_DETERMINED:
                try:
                    perm = loop.run_until_complete(self._backend.request_access())
                except Exception as exc:  # noqa: BLE001
                    self._emit_status(PermissionStatus.DENIED, False, f"请求通知权限失败: {type(exc).__name__}")
                    perm = PermissionStatus.DENIED

            if perm != PermissionStatus.ALLOWED:
                self._emit_status(perm, False, "")
            else:
                self._emit_status(perm, True, "")

            while not self._stop_ev.is_set():
                want_req = False
                with self._lock:
                    if self._request_perm_flag:
                        self._request_perm_flag = False
                        want_req = True

                if want_req:
                    try:
                        perm = loop.run_until_complete(self._backend.request_access())
                        self._baseline_done = False
                        self._emit_status(perm, perm == PermissionStatus.ALLOWED, "")
                    except Exception as exc:  # noqa: BLE001
                        self._emit_status(self.permission_status, False, f"权限请求异常: {type(exc).__name__}")

                if perm != PermissionStatus.ALLOWED:
                    # 权限未开启时低频复检状态，不阻塞 UI，也不高频轮询
                    self._wake_ev.wait(timeout=3.0)
                    self._wake_ev.clear()
                    if self._stop_ev.is_set():
                        break
                    new_perm = self._backend.get_access_status()
                    if new_perm != perm:
                        perm = new_perm
                        self._baseline_done = False
                        self._emit_status(perm, perm == PermissionStatus.ALLOWED, "")
                    continue

                now_m = time.monotonic()
                if self._last_sample_mono > 0 and (now_m - self._last_sample_mono) > SLEEP_GAP_S:
                    # 休眠恢复后重新建立 baseline，绝不回放大量旧通知
                    self._baseline_done = False
                self._last_sample_mono = now_m

                try:
                    records = loop.run_until_complete(self._backend.fetch_toasts())
                    if self._stop_ev.is_set():
                        break
                    prev_cache = getattr(self, "_toast_event_cache", {})
                    next_cache = {}
                    events = []
                    for r in records:
                        if isinstance(r, dict):
                            sig = (
                                r.get("notification_id"),
                                r.get("creation_time"),
                                r.get("app_identity"),
                                r.get("title"),
                                r.get("body"),
                            )
                        else:
                            sig = None
                        ev = prev_cache.get(sig) if sig is not None else None
                        if ev is None:
                            ev = raw_record_to_event(r, self._wpndb_lookup)
                        if sig is not None:
                            next_cache[sig] = ev
                        events.append(ev)
                    self._toast_event_cache = next_cache
                    if self._stop_ev.is_set():
                        break
                    if not self._baseline_done:
                        self._baseline_done = True
                        self.baseline_captured.emit(events)
                    else:
                        self.events_sampled.emit(events)
                    if not self.listening or self.last_error:
                        self._emit_status(PermissionStatus.ALLOWED, True, "")
                except Exception as exc:  # noqa: BLE001
                    if not self._stop_ev.is_set():
                        self._emit_status(self.permission_status, False, f"读取通知失败: {type(exc).__name__}")

                self._wake_ev.wait(timeout=POLL_INTERVAL_S)
                self._wake_ev.clear()
        finally:
            try:
                self._backend.release()
            except Exception:  # noqa: BLE001
                pass
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
            if com_hr in (0, 1):
                try:
                    ole32.CoUninitialize()
                except Exception:  # noqa: BLE001
                    pass
