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
from .hid_native import enumerate_hid


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._devices: list[BatteryDevice] = []
        self._last_readings: dict[str, BatteryReading] = {}
        self._low_alerted: set[str] = set()
        self._seen_charging: set[str] = set()
        self._full_alerted: set[str] = set()
        self._apple_adv: tuple[float, dict] | None = None
        self._watcher = None
        self._busy = False
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
            self._stop_apple_watcher()
            with self._lock:
                self._devices = []
            self.changed.emit()
            return
        if not self._started:
            return
        self._timer.start(sec * 1000)
        self._start_apple_watcher()
        self.refresh()

    def stop(self):
        self._started = False
        self._timer.stop()
        self._stop_apple_watcher()

    @QtCore.Slot()
    @QtCore.Slot(bool)
    def refresh(self, force_discover: bool = False):
        if not cfg.get("battery.enabled", True):
            return
        if self._started:
            self._start_apple_watcher()
        if self._busy:
            return
        self._set_busy(True)
        run_bg(
            lambda fd=bool(force_discover): self._poll_sync(force_discover=fd),
            on_done=self._on_poll_done,
            on_error=lambda _e: self._set_busy(False),
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
        self._start_apple_watcher()
        self.refresh(force_discover=True)

    def _set_busy(self, on: bool):
        if self._busy != on:
            self._busy = on
            self.busy_changed.emit(on)

    # ---------------- Apple BLE 被动监听（可选 WinRT 增强） ----------------
    def _start_apple_watcher(self):
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
            w.add_received(self._on_ble_adv)
            try:
                w.add_stopped(self._on_ble_stopped)
            except Exception:  # noqa: BLE001
                pass
            w.start()
            self._watcher = w
        except Exception:  # noqa: BLE001
            self._watcher = None

    def _on_ble_stopped(self, sender, _args):
        """蓝牙适配器复位或系统休眠导致监听意外中止时，回到主线程自动重建。"""
        if not self._started or sender is not self._watcher:
            return
        self._watcher = None
        try:
            QtCore.QMetaObject.invokeMethod(
                self, "_schedule_ble_restart", QtCore.Qt.ConnectionType.QueuedConnection
            )
        except Exception:  # noqa: BLE001
            pass

    @QtCore.Slot()
    def _schedule_ble_restart(self):
        if self._started and cfg.get("battery.enabled", True):
            QtCore.QTimer.singleShot(2000, self._start_apple_watcher)

    def _stop_apple_watcher(self):
        w = self._watcher
        self._watcher = None
        if w is not None:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass

    def _on_ble_adv(self, _sender, args):
        try:
            if int(args.raw_signal_strength_in_dbm) < -82:
                return
            for mdata in args.advertisement.manufacturer_data:
                if int(mdata.company_id) != AppleAdvertParser.COMPANY_ID:
                    continue
                raw = bytes(mdata.data)
                parsed = AppleAdvertParser.parse(raw)
                if parsed:
                    with self._lock:
                        prev = self._apple_adv[1] if self._apple_adv else None
                        self._apple_adv = (time.time(), parsed)
                    if prev != parsed:
                        QtCore.QMetaObject.invokeMethod(
                            self, "refresh", QtCore.Qt.ConnectionType.QueuedConnection
                        )
        except Exception:  # noqa: BLE001
            pass

    def _fresh_apple_adv(self) -> dict | None:
        with self._lock:
            snap = self._apple_adv
        if not snap:
            return None
        ts, data = snap
        if time.time() - ts > 600.0:
            return None
        return data

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
            results.extend(self._bt_provider.discover_and_read(apple_snap))
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

    def _on_poll_done(self, devs: list[BatteryDevice]):
        self._set_busy(False)
        with self._lock:
            self._devices = devs
        self._check_low_battery(devs)
        self.changed.emit()

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
