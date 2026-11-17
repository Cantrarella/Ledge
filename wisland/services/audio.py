# -*- coding: utf-8 -*-
"""音量（pycaw，主线程 COM）。"""
from __future__ import annotations

import time
import traceback

from PySide6 import QtCore


# ======================================================================
# 音量
# ======================================================================
class AudioService(QtCore.QObject):
    """默认播放设备的主音量。所有 COM 调用只在主线程。"""

    volume_changed = QtCore.Signal(float, bool)   # (0..1, muted)

    POLL_MS = 250
    DEVICE_CHECK_S = 3.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enum = None
        self._dev_id = None
        self._vol = None      # IAudioEndpointVolume
        self._meter = None    # IAudioMeterInformation
        self._last = None     # (volume, muted)
        self._last_dev_check = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    # ---------------- 设备 ----------------
    def _acquire(self) -> bool:
        try:
            import comtypes
            from pycaw.pycaw import (IAudioEndpointVolume, IAudioMeterInformation,
                                     IMMDeviceEnumerator)
            try:
                from pycaw.constants import CLSID_MMDeviceEnumerator
            except Exception:  # noqa: BLE001
                from pycaw.pycaw import CLSID_MMDeviceEnumerator  # type: ignore
            if self._enum is None:
                self._enum = comtypes.CoCreateInstance(
                    CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
            dev = self._enum.GetDefaultAudioEndpoint(0, 1)   # eRender, eMultimedia
            self._dev_id = dev.GetId()
            self._vol = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None) \
                .QueryInterface(IAudioEndpointVolume)
            try:
                self._meter = dev.Activate(IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None) \
                    .QueryInterface(IAudioMeterInformation)
            except Exception:  # noqa: BLE001
                self._meter = None
            self._last_dev_check = time.monotonic()
            return True
        except Exception:  # noqa: BLE001
            # 没有播放设备时会走到这里
            self._vol = self._meter = None
            self._dev_id = None
            self._last_dev_check = time.monotonic()
            return False

    def _check_device(self):
        """便宜地检查默认设备是否变了（只取 ID 字符串）。"""
        now = time.monotonic()
        if now - self._last_dev_check < self.DEVICE_CHECK_S and self._vol is not None:
            return
        self._last_dev_check = now
        try:
            if self._enum is None:
                self._acquire()
                return
            dev_id = self._enum.GetDefaultAudioEndpoint(0, 1).GetId()
            if dev_id != self._dev_id or self._vol is None:
                self._acquire()
        except Exception:  # noqa: BLE001
            self._vol = self._meter = None
            self._dev_id = None

    def _ev(self):
        if self._vol is None:
            self._acquire()
        return self._vol

    def _call(self, fn, default=None):
        """调用 COM，出错则重新获取设备再试一次。"""
        for attempt in range(2):
            ev = self._ev()
            if ev is None:
                return default
            try:
                return fn(ev)
            except Exception:  # noqa: BLE001
                self._vol = self._meter = None
                if attempt == 1:
                    return default
        return default

    # ---------------- 生命周期 ----------------
    def start(self):
        self._acquire()
        self._poll()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        try:
            self._check_device()
            cur = (round(self.volume(), 4), self.muted())
            if cur != self._last:
                self._last = cur
                self.volume_changed.emit(cur[0], cur[1])
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _notify_soon(self):
        QtCore.QTimer.singleShot(0, self._poll)

    # ---------------- API ----------------
    def available(self) -> bool:
        return self._ev() is not None

    def volume(self) -> float:
        return float(self._call(lambda ev: ev.GetMasterVolumeLevelScalar(), 0.0) or 0.0)

    def set_volume(self, v: float):
        v = max(0.0, min(1.0, float(v)))
        self._call(lambda ev: ev.SetMasterVolumeLevelScalar(v, None))
        self._notify_soon()

    def muted(self) -> bool:
        return bool(self._call(lambda ev: ev.GetMute(), False))

    def set_muted(self, b: bool):
        self._call(lambda ev: ev.SetMute(1 if b else 0, None))
        self._notify_soon()

    def toggle_mute(self):
        self.set_muted(not self.muted())

    def step(self, delta: float):
        """delta 为 0..1 刻度，例如 +0.02。调大音量时自动取消静音。"""
        self.set_volume(self.volume() + delta)
        if delta > 0 and self.muted():
            self.set_muted(False)

    def peak(self) -> float:
        m = self._meter
        if m is None:
            return 0.0
        try:
            return max(0.0, min(1.0, float(m.GetPeakValue())))
        except Exception:  # noqa: BLE001
            self._meter = None
            self._vol = None
            return 0.0
