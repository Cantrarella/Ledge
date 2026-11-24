# -*- coding: utf-8 -*-
"""无线设备电量监控服务：统一调度 HID / XInput / PlayStation / Windows 蓝牙及 AirPods 采集，支持休眠保留与低电防抖提醒。"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

from PySide6 import QtCore

from ..core.bg import run_bg
from ..core.config import cfg
from .battery_providers import (
    AppleAdvertParser,
    AsusHeadsetProvider,
    BatteryReading,
    BluetoothBatteryProvider,
    DeviceCandidate,
    GenericHidHeadsetProvider,
    LogitechProvider,
    MchoseProvider,
    PlayStationProvider,
    XInputProvider,
)
from .hid_native import (
    enumerate_hid,
    get_connected_apple_bluetooth_devices,
    query_connected_apple_bluetooth_devices,
)
_ORIG_GET_CONNECTED_APPLE = get_connected_apple_bluetooth_devices


@dataclass
class BatteryDevice:
    id: str
    name: str
    alias: str
    brand: str
    kind: str
    provider: str
    percent: int | None = None
    charging: bool | None = None
    online: bool = False
    sleeping: bool = False
    detail: str = ""
    parts: list[tuple[str, int, bool]] = field(default_factory=list)
    updated_at: float = 0.0
    hidden: bool = False

    @property
    def display_name(self) -> str:
        return self.alias or self.name


class BatteryService(QtCore.QObject):
    changed = QtCore.Signal()
    busy_changed = QtCore.Signal(bool)
    low_battery = QtCore.Signal(str, str, int, str)    # (dev_id, display_name, percent, kind)
    full_battery = QtCore.Signal(str, str, str)        # (dev_id, display_name, kind)
    _watcher_want_changed = QtCore.Signal(bool, bool)  # (want, force_restart)
    _ble_watcher_stopped = QtCore.Signal(object)

    _APPLE_SIDE_STALE_S = 180.0
    _APPLE_MAX_KNOWN_ADDRS = 6
    _BT_WATCH_INTERVAL_MS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._devices: list[BatteryDevice] = []
        self._last_readings: dict[str, BatteryReading] = {}
        self._low_alerted: set[str] = set()
        self._seen_charging: set[str] = set()
        self._full_alerted: set[str] = set()
        self._apple_adv: tuple[float, dict] | None = None
        self._apple_side_adv: dict[str, tuple[float, dict]] = {}
        self._apple_known_addrs: dict[int, float] = {}
        self._connected_apple: list[dict] = []
        self._bound_apple_models: set[str] = set()
        self._watcher = None
        self._watcher_gen = 0
        self._busy = False
        self._bt_probe_busy = False
        self._refresh_pending = False
        self._refresh_pending_force = False
        self._cached_hid: list[tuple[object, DeviceCandidate]] | None = None
        self._last_discover_ts: float = 0.0

        self._hid_providers = (
            AsusHeadsetProvider(),
            MchoseProvider(),
            LogitechProvider(),
            GenericHidHeadsetProvider(),
            XInputProvider(),
            PlayStationProvider(),
        )
        self._bt_provider = BluetoothBatteryProvider()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._bt_watch_timer = QtCore.QTimer(self)
        self._bt_watch_timer.setInterval(self._BT_WATCH_INTERVAL_MS)
        self._bt_watch_timer.timeout.connect(self._probe_apple_connection)
        self._dev_change_debounce = QtCore.QTimer(self)
        self._dev_change_debounce.setSingleShot(True)
        self._dev_change_debounce.setInterval(800)
        self._dev_change_debounce.timeout.connect(self._probe_apple_connection)
        self._ble_restart_timer = QtCore.QTimer(self)
        self._ble_restart_timer.setSingleShot(True)
        self._ble_restart_timer.setInterval(2000)
        self._ble_restart_timer.timeout.connect(self._try_restart_apple_watcher)
        self._watcher_want_changed.connect(self._sync_apple_watcher, QtCore.Qt.ConnectionType.QueuedConnection)
        self._ble_watcher_stopped.connect(self._on_ble_stopped_main, QtCore.Qt.ConnectionType.QueuedConnection)
        self._started = False

    def start(self):
        self._started = True
        self.apply_config()

    @property
    def busy(self) -> bool:
        return self._busy

    def devices(self, include_hidden: bool = False) -> list[BatteryDevice]:
        with self._lock:
            items = list(self._devices)
        if include_hidden:
            return items
        return [d for d in items if not d.hidden]

    def apply_config(self):
        enabled = bool(cfg.get("battery.enabled", True))
        sec = max(15, min(300, int(cfg.get("battery.poll_interval_s", 60) or 60)))
        if not enabled:
            self._timer.stop()
            self._bt_watch_timer.stop()
            self._dev_change_debounce.stop()
            self._refresh_pending = False
            self._refresh_pending_force = False
            self._stop_apple_watcher()
            with self._lock:
                self._devices = []
                self._connected_apple = []
                self._bound_apple_models.clear()
                self._reset_apple_state_locked()
            self.changed.emit()
            return
        if not self._started:
            return
        self._timer.start(sec * 1000)
        self._bt_watch_timer.start()
        # 参考 AirPodsDesktop：由当前经典蓝牙连接状态门控 BLE 扫描，不再无条件常驻空扫
        self.refresh()

    def stop(self):
        self._started = False
        self._refresh_pending = False
        self._refresh_pending_force = False
        self._timer.stop()
        self._bt_watch_timer.stop()
        self._dev_change_debounce.stop()
        self._stop_apple_watcher()

    @QtCore.Slot()
    def on_device_changed(self):
        """Windows 收到 WM_DEVICECHANGE（蓝牙/USB 拔插或连接）时，防抖后快速探测 Apple 蓝牙连接变化。"""
        if not self._started or not cfg.get("battery.enabled", True):
            return
        self._dev_change_debounce.start()

    def _query_connected_apple_safe(self) -> tuple[bool, list[dict]]:
        """区分“枚举失败”与“成功枚举到 0 台设备”，同时兼容单测对 get_connected_apple_bluetooth_devices 的替换。"""
        try:
            if get_connected_apple_bluetooth_devices is not _ORIG_GET_CONNECTED_APPLE:
                return True, list(get_connected_apple_bluetooth_devices() or [])
            ok, devs = query_connected_apple_bluetooth_devices()
            return bool(ok), list(devs or [])
        except Exception:  # noqa: BLE001
            return False, []

    def _update_connected_apple_locked(self, connected_apple: list[dict]) -> tuple[bool, bool]:
        """更新已连接 Apple 列表与绑定型号，返回 (changed, switched)。
        断开或直接切换到完全不同的一副耳机时清空上一组广播缓存并递增 _watcher_gen 使旧回调立即失效。
        """
        prev_macs = {str(d.get("mac") or "").upper() for d in self._connected_apple if d.get("mac")}
        new_macs = {str(d.get("mac") or "").upper() for d in connected_apple if d.get("mac")}
        changed = (new_macs != prev_macs) or (bool(connected_apple) != bool(self._connected_apple))
        switched = bool(prev_macs and new_macs and prev_macs.isdisjoint(new_macs))
        self._connected_apple = list(connected_apple)
        if connected_apple:
            if switched:
                self._reset_apple_state_locked()
                self._watcher_gen += 1
            self._bound_apple_models = {
                m for d in connected_apple
                if (m := AppleAdvertParser.model_for_pid(int(d.get("pid") or 0)))
            }
        else:
            self._bound_apple_models.clear()
            self._reset_apple_state_locked()
        return changed, switched

    @QtCore.Slot()
    def _probe_apple_connection(self):
        """轻量级后台探测（仅查询 BTHENUM，约 2~3ms，不碰 HID），实现连上/断开 1~5 秒内响应。"""
        if not self._started or not cfg.get("battery.enabled", True) or self._bt_probe_busy:
            return
        self._bt_probe_busy = True
        run_bg(
            self._query_connected_apple_safe,
            on_done=self._on_apple_probe_done,
            on_error=lambda _e: setattr(self, "_bt_probe_busy", False),
        )

    def _on_apple_probe_done(self, result):
        self._bt_probe_busy = False
        if not self._started or not cfg.get("battery.enabled", True):
            return
        # 兼容直接传入 list（单测）或 (ok, list)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            ok, connected_apple = result
            if not ok:
                # 单次底层枚举失败，保留上一次状态避免假断连
                return
        else:
            connected_apple = list(result or [])
        with self._lock:
            changed, switched = self._update_connected_apple_locked(connected_apple)
        if changed:
            self._sync_apple_watcher(bool(connected_apple), force_restart=switched)
            self.refresh(force_discover=False)
        elif connected_apple and self._watcher is None and not self._ble_restart_timer.isActive():
            # 蓝牙适配器重插/唤醒后若此前启动 BLE 监听失败（如短暂“设备未就绪”），心跳周期自动自愈拉起
            self._start_apple_watcher()

    @QtCore.Slot()
    @QtCore.Slot(bool)
    def refresh(self, force_discover: bool = False):
        if not cfg.get("battery.enabled", True):
            return
        if self._busy:
            # 轮询进行中若有新触发（例如 BLE 刚收到第一帧广播），记为待补刷而非静默丢弃
            self._refresh_pending = True
            if force_discover:
                self._refresh_pending_force = True
            return
        self._refresh_pending = False
        self._refresh_pending_force = False
        self._set_busy(True)
        run_bg(
            lambda fd=bool(force_discover): self._poll_sync(force_discover=fd),
            on_done=self._on_poll_done,
            on_error=self._on_poll_error,
        )

    def on_system_resume(self):
        """系统睡眠/休眠唤醒后重建可能被 Windows 蓝牙栈中止的 BLE 监听器并刷新设备。"""
        if not self._started or not cfg.get("battery.enabled", True):
            return
        self._stop_apple_watcher()
        QtCore.QTimer.singleShot(1500, self._resume_restart_ble)

    @QtCore.Slot()
    def _resume_restart_ble(self):
        if not self._started or not cfg.get("battery.enabled", True):
            return
        self.refresh(force_discover=True)

    @QtCore.Slot(bool, bool)
    def _sync_apple_watcher(self, want: bool, force_restart: bool = False):
        if want and self._started and cfg.get("battery.enabled", True):
            if force_restart:
                self._stop_apple_watcher()
            self._start_apple_watcher()
        else:
            self._stop_apple_watcher()

    def _reset_apple_state_locked(self):
        self._apple_adv = None
        self._apple_side_adv.clear()
        self._apple_known_addrs.clear()

    def _set_busy(self, on: bool):
        if self._busy != on:
            self._busy = on
            self.busy_changed.emit(on)

    # ---------------- Apple BLE 被动监听（可选 WinRT 增强） ----------------
    def _start_apple_watcher(self):
        self._ble_restart_timer.stop()
        if self._watcher is not None:
            try:
                # BluetoothLEAdvertisementWatcherStatus: 0=CREATED, 1=STARTED, 2=STOPPING, 3=STOPPED, 4=ABORTED
                if int(self._watcher.status) in (0, 1):
                    return
            except Exception:  # noqa: BLE001
                pass
            self._stop_apple_watcher()
        try:
            from winrt.windows.devices.bluetooth.advertisement import (
                BluetoothLEAdvertisementBytePattern,
                BluetoothLEAdvertisementWatcher,
                BluetoothLEScanningMode,
            )
            from winrt.windows.storage.streams import DataWriter

            dw = DataWriter()
            dw.write_bytes(bytes([0x4C, 0x00]))
            pat = BluetoothLEAdvertisementBytePattern(0xFF, 0, dw.detach_buffer())
            w = BluetoothLEAdvertisementWatcher()
            w.scanning_mode = BluetoothLEScanningMode.PASSIVE
            w.advertisement_filter.byte_patterns.append(pat)
            with self._lock:
                self._watcher_gen += 1
                gen = self._watcher_gen
            w.add_received(lambda s, a, g=gen: self._on_ble_adv(s, a, g))
            try:
                w.add_stopped(lambda s, a, g=gen: self._on_ble_stopped(s, a, g))
            except Exception:  # noqa: BLE001
                pass
            w.start()
            self._watcher = w
        except Exception:  # noqa: BLE001
            self._watcher = None
            with self._lock:
                has_apple = bool(self._connected_apple)
            if self._started and has_apple and cfg.get("battery.enabled", True):
                self._ble_restart_timer.start()

    def _on_ble_stopped(self, sender, _args, watcher_gen: int | None = None):
        """蓝牙适配器复位或系统休眠导致监听意外中止时，投递回主线程统一清理与重建。"""
        try:
            self._ble_watcher_stopped.emit((sender, watcher_gen))
        except Exception:  # noqa: BLE001
            pass

    @QtCore.Slot(object)
    def _on_ble_stopped_main(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            sender, watcher_gen = payload
        else:
            sender, watcher_gen = payload, None
        if not self._started or self._watcher is None:
            return
        with self._lock:
            if watcher_gen is not None and watcher_gen != self._watcher_gen:
                return
            if watcher_gen is None and sender is not self._watcher:
                return
            self._watcher_gen += 1
            has_apple = bool(self._connected_apple)
        self._watcher = None
        if has_apple and cfg.get("battery.enabled", True):
            self._ble_restart_timer.start()

    @QtCore.Slot()
    def _try_restart_apple_watcher(self):
        """延迟重启回调到期时再次确认服务状态与连接状态，防止断开或禁用后误重启空扫。"""
        with self._lock:
            has_apple = bool(self._connected_apple)
        if self._started and has_apple and cfg.get("battery.enabled", True):
            self._start_apple_watcher()

    def _stop_apple_watcher(self):
        self._ble_restart_timer.stop()
        with self._lock:
            self._watcher_gen += 1
        w = self._watcher
        self._watcher = None
        if w is not None:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass

    def _merge_apple_sides_locked(self) -> dict | None:
        """参考 AirPodsStateManager：合并左/右耳分侧广播，保留每个部位最新有效电量。"""
        if not self._apple_side_adv:
            return None
        entries = list(self._apple_side_adv.values())
        entries.sort(key=lambda item: item[0])
        latest_ts, latest = entries[-1]
        # 丢弃比最新帧过旧的另一侧缓存（避免充电盒合盖或单耳放回盒后旧数值永久残留）
        entries = [e for e in entries if (latest_ts - e[0]) <= self._APPLE_SIDE_STALE_S]
        model = str(latest.get("model") or "AirPods")
        # 单电池设备（如 AirPods Max）直接使用最新帧
        if not any(e[1].get("parts") for e in entries):
            return {
                "model": model,
                "percent": int(latest["percent"]),
                "charging": bool(latest.get("charging", False)),
                "parts": [],
            }
        # 对“左/右/盒”分别保留时间戳最新的有效读数
        part_map: dict[str, tuple[float, int, bool]] = {}
        for ts, d in entries:
            for k, val, chg in d.get("parts") or ():
                prev = part_map.get(k)
                if prev is None or ts >= prev[0]:
                    part_map[k] = (ts, int(val), bool(chg))
        parts: list[tuple[str, int, bool]] = []
        bud_pcts: list[int] = []
        for k in ("左", "右", "盒"):
            if k in part_map:
                _, val, chg = part_map[k]
                parts.append((k, val, chg))
                if k in ("左", "右"):
                    bud_pcts.append(val)
        if not parts:
            return None
        overall = min(bud_pcts) if bud_pcts else parts[0][1]
        charging = bool(bud_pcts) and all(p[2] for p in parts if p[0] in ("左", "右"))
        return {"model": model, "percent": overall, "charging": charging, "parts": parts}

    def _on_ble_adv(self, sender, args, watcher_gen: int | None = None):
        try:
            rssi = int(args.raw_signal_strength_in_dbm)
            if rssi <= -127:
                return
            addr = int(getattr(args, "bluetooth_address", 0) or 0)
            with self._lock:
                if watcher_gen is not None and watcher_gen != self._watcher_gen:
                    return
                if not self._connected_apple:
                    return
                known = bool(addr and addr in self._apple_known_addrs)
                bound_models = set(self._bound_apple_models)
            if not known and rssi < -82:
                return
            for mdata in args.advertisement.manufacturer_data:
                if int(mdata.company_id) != AppleAdvertParser.COMPANY_ID:
                    continue
                raw = bytes(mdata.data)
                parsed = AppleAdvertParser.parse(raw)
                if not parsed:
                    continue
                if bound_models and parsed.get("model") not in bound_models:
                    continue
                now_ts = time.time()
                side = str(parsed.get("side") or "left")
                with self._lock:
                    if watcher_gen is not None and watcher_gen != self._watcher_gen:
                        return
                    if not self._connected_apple:
                        return
                    prev = self._apple_adv[1] if self._apple_adv else None
                    if addr:
                        self._apple_known_addrs[addr] = now_ts
                        if len(self._apple_known_addrs) > self._APPLE_MAX_KNOWN_ADDRS:
                            oldest_addr = min(self._apple_known_addrs, key=self._apple_known_addrs.get)
                            self._apple_known_addrs.pop(oldest_addr, None)
                    self._apple_side_adv[side] = (now_ts, parsed)
                    merged = self._merge_apple_sides_locked()
                    if merged is not None:
                        self._apple_adv = (now_ts, merged)
                if merged is not None and prev != merged:
                    QtCore.QMetaObject.invokeMethod(
                        self, "refresh", QtCore.Qt.ConnectionType.QueuedConnection
                    )
        except Exception:  # noqa: BLE001
            pass

    def _fresh_apple_adv(self) -> dict | None:
        with self._lock:
            if not self._connected_apple:
                return None
            snap = self._apple_adv
        if not snap:
            return None
        return snap[1]

    # ---------------- 后台扫描与合并 ----------------
    def _poll_sync(self, force_discover: bool = False) -> list[BatteryDevice]:
        aliases = cfg.get("battery.aliases", {}) or {}
        hidden_set = set(str(x) for x in (cfg.get("battery.hidden", []) or []))
        now = time.monotonic()
        need_discover = force_discover or self._cached_hid is None or (now - self._last_discover_ts >= 300.0)
        if need_discover:
            catalog = enumerate_hid()
            pairs: list[tuple[object, DeviceCandidate]] = []
            for prov in self._hid_providers:
                try:
                    cands = prov.discover(catalog)
                except Exception:  # noqa: BLE001
                    cands = []
                for cand in cands:
                    pairs.append((prov, cand))
            self._cached_hid = pairs
            self._last_discover_ts = now
        else:
            pairs = list(self._cached_hid or [])

        # 参考 AirPodsDesktop：以系统蓝牙连接状态门控 BLE 扫描与缓存生命周期（枚举失败时保留上次状态避免假断连）
        ok_apple, queried_apple = self._query_connected_apple_safe()
        switched = False
        with self._lock:
            if ok_apple:
                _, switched = self._update_connected_apple_locked(queried_apple)
            connected_apple = list(self._connected_apple)
        if self._started:
            self._watcher_want_changed.emit(bool(connected_apple), switched)

        apple_snap = self._fresh_apple_adv()

        results: list[tuple[DeviceCandidate, BatteryReading]] = []
        had_io_error = False
        for prov, cand in pairs:
            try:
                rd = prov.read(cand)
                if rd.error and not rd.sleeping and not rd.online:
                    had_io_error = True
            except Exception as e:  # noqa: BLE001
                rd = BatteryReading(error=str(e))
                had_io_error = True
            results.append((cand, rd))
        if had_io_error:
            # 接收器可能被拔插过，下次轮询强制重新枚举 HID 目录
            self._cached_hid = None

        try:
            results.extend(self._bt_provider.discover_and_read(apple_snap, connected_apple=connected_apple))
        except Exception:  # noqa: BLE001
            pass

        out: list[BatteryDevice] = []
        with self._lock:
            for cand, rd in results:
                prev = self._last_readings.get(cand.id)
                pct = rd.percent
                chg = rd.charging
                detail = rd.detail
                parts = rd.parts
                sleeping = rd.sleeping
                online = rd.online
                updated_at = rd.updated_at

                if rd.online and rd.percent is not None:
                    self._last_readings[cand.id] = rd
                elif rd.sleeping and prev is not None and prev.percent is not None:
                    # 休眠时保留上一次有效电量，避免闪现空值或 0%
                    pct = prev.percent
                    chg = False
                    parts = prev.parts
                    detail = "休眠"
                    updated_at = prev.updated_at
                elif not rd.online and prev is None and rd.percent is None:
                    # 接收器插在电脑上但设备从未上线过时也保留占位（或显示休眠）
                    detail = rd.error or "未响应"

                alias = str(aliases.get(cand.id, "")).strip()
                out.append(
                    BatteryDevice(
                        id=cand.id,
                        name=cand.name,
                        alias=alias,
                        brand=cand.brand,
                        kind=cand.kind,
                        provider=cand.provider,
                        percent=pct,
                        charging=chg,
                        online=online,
                        sleeping=sleeping,
                        detail=detail,
                        parts=list(parts),
                        updated_at=updated_at,
                        hidden=(cand.id in hidden_set),
                    )
                )
        return out

    def _drain_pending_refresh(self):
        if self._refresh_pending and self._started and cfg.get("battery.enabled", True):
            force = self._refresh_pending_force
            self._refresh_pending = False
            self._refresh_pending_force = False
            self.refresh(force_discover=force)

    def _on_poll_error(self, _err):
        self._set_busy(False)
        self._drain_pending_refresh()

    def _on_poll_done(self, devs: list[BatteryDevice]):
        self._set_busy(False)
        with self._lock:
            self._devices = devs
        self._check_low_battery(devs)
        self.changed.emit()
        self._drain_pending_refresh()

    def _check_low_battery(self, devs: list[BatteryDevice]):
        low_on = bool(cfg.get("battery.low_notify", True))
        full_on = bool(cfg.get("battery.full_notify", True))
        thresh = max(5, min(50, int(cfg.get("battery.low_threshold", 20) or 20)))
        seen_chg = getattr(self, "_seen_charging", None)
        if seen_chg is None:
            seen_chg = self._seen_charging = set()
        full_alerted = getattr(self, "_full_alerted", None)
        if full_alerted is None:
            full_alerted = self._full_alerted = set()

        for d in devs:
            if d.hidden or d.percent is None:
                continue
            if d.charging:
                self._low_alerted.discard(d.id)
                seen_chg.add(d.id)
                if full_on and d.online and not d.sleeping and d.percent >= 100:
                    if d.id not in full_alerted:
                        full_alerted.add(d.id)
                        self.full_battery.emit(d.id, d.display_name, d.kind)
                continue

            if d.id in seen_chg and d.percent >= 99 and d.online and not d.sleeping:
                if full_on and d.id not in full_alerted:
                    full_alerted.add(d.id)
                    self.full_battery.emit(d.id, d.display_name, d.kind)
            elif d.percent <= 94:
                seen_chg.discard(d.id)
                full_alerted.discard(d.id)

            if d.percent >= thresh + 5:
                self._low_alerted.discard(d.id)
                continue
            if low_on and d.online and not d.sleeping and d.percent <= thresh:
                if d.id not in self._low_alerted:
                    self._low_alerted.add(d.id)
                    self.low_battery.emit(d.id, d.display_name, d.percent, d.kind)
