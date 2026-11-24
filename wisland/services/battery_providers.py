# -*- coding: utf-8 -*-
"""无线外设电量适配器：
- ROG Delta II 凌镜 2 (AsusHeadsetProvider: 0x0B05, CC 12 07 / CC 12 08)
- 迈从 MCHOSE K99 V2 (MchoseProvider: 0x3554, 0x13 0x4A)
- 罗技 Logitech GPW3 等 (LogitechProvider: 0x046D, HID++ 2.0 0x1000/0x1004)
- 通用 HID 耳机 (HeadsetProvider: 标准 HID 电源页 & Razer BlackShark 音频协议)
- XInput 手柄 (XInputProvider)
- PlayStation 手柄 (PlayStationProvider: DualSense / DualSense Edge / DualShock 4)
- Windows 系统蓝牙 (DEVPKEY_Device_BatteryPlus) + Apple AirPods 邻近广播解析
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import re
import time

from .hid_native import (
    HidDeviceInfo,
    HidSession,
    get_connected_apple_bluetooth_devices,
    has_connected_apple_bluetooth,
    read_bluetooth_batteries,
)


@dataclass
class DeviceCandidate:
    id: str
    name: str
    brand: str
    model: str
    kind: str                  # "mouse" | "keyboard" | "headset" | "earbuds" | "gamepad" | "bluetooth"
    provider: str
    vendor_id: int = 0
    product_id: int = 0
    hid_path: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class BatteryReading:
    percent: int | None = None
    charging: bool | None = None
    online: bool = False
    sleeping: bool = False
    detail: str = ""
    parts: list[tuple[str, int, bool]] = field(default_factory=list)   # e.g. [("左", 80, False), ("右", 75, False), ("盒", 60, True)]
    updated_at: float = field(default_factory=time.time)
    error: str = ""


def infer_device_kind(name: str, fallback: str = "bluetooth") -> str:
    s = (name or "").lower()
    if any(k in s for k in ("airpods", "buds", "earbud", "beats", "tws", "pods")):
        return "earbuds"
    if any(k in s for k in ("headset", "headphone", "delta", "pelta", "blackshark", "barracuda", "arctis", "cloud", "耳机", "凌镜")):
        return "headset"
    if any(k in s for k in ("mouse", "gpw", "superlight", "g pro", "g304", "viper", "deathadder", "鼠标")):
        return "mouse"
    if any(k in s for k in ("keyboard", "k99", "k87", "azoth", "g75", "键盘")):
        return "keyboard"
    if any(k in s for k in ("xbox", "dualsense", "dualshock", "controller", "gamepad", "8bitdo", "手柄")):
        return "gamepad"
    return fallback


# ==============================================================================
# 1. ASUS ROG Delta II 凌镜 2 (AsusHeadsetProvider)
# ==============================================================================
class AsusHeadsetProvider:
    name = "ROG Audio"
    _VID = 0x0B05
    _PAGE = 0xFF00
    _KNOWN = {
        0x1AFA: "ROG Delta II",
        0x1D41: "ROG Delta II KJP",
        0x1B82: "ROG Pelta",
        0x1B84: "ROG Pelta",
    }
    _RE = re.compile(r"DELTA|PELTA|CETRA|CLAVIS|STRIX\s*GO|HEADSET|HATSUNE", re.I)

    def discover(self, catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        best: dict[int, HidDeviceInfo] = {}
        for h in catalog:
            if h.vendor_id != self._VID or h.usage_page != self._PAGE:
                continue
            if h.input_len < 64 or h.output_len < 64:
                continue
            if h.product_id not in self._KNOWN and not self._RE.search(h.product_name or ""):
                continue
            best.setdefault(h.product_id, h)
        out: list[DeviceCandidate] = []
        for pid, h in best.items():
            model = self._KNOWN.get(pid) or (h.product_name or f"ROG {pid:04X}").strip()
            out.append(
                DeviceCandidate(
                    id=f"rog-hs-{pid:04X}",
                    name=model,
                    brand="ASUS ROG",
                    model=model,
                    kind="headset",
                    provider=self.name,
                    vendor_id=h.vendor_id,
                    product_id=pid,
                    hid_path=h.path,
                    extra={"input_len": h.input_len, "output_len": h.output_len},
                )
            )
        return out

    @staticmethod
    def parse_battery(frame: bytes) -> int | None:
        if len(frame) < 7 or frame[0] != 0xCC or frame[1] != 0x12 or frame[2] != 0x07:
            return None
        if frame[1:3] == b"\xff\xaa" or frame[5:7] == b"\xff\xaa":
            return None
        pct = int(frame[6])
        return pct if 0 <= pct <= 100 else None

    @staticmethod
    def parse_charging(frame: bytes) -> bool | None:
        if len(frame) < 6 or frame[0] != 0xCC or frame[1] != 0x12 or frame[2] != 0x08:
            return None
        if frame[1:3] == b"\xff\xaa" or frame[5:7] == b"\xff\xaa":
            return None
        return frame[5] == 1

    def read(self, d: DeviceCandidate) -> BatteryReading:
        r = BatteryReading(detail="2.4G")
        ilen = int(d.extra.get("input_len", 64))
        olen = int(d.extra.get("output_len", 64))
        try:
            with HidSession(d.hid_path, ilen, olen) as s:
                s.flush(40)
                pct = self._query_cmd(s, olen, 0x07, self.parse_battery)
                if pct is None:
                    r.sleeping = True
                    r.error = "耳机未应答（可能已关机）"
                    return r
                chg = self._query_cmd(s, olen, 0x08, self.parse_charging)
                r.percent = pct
                r.charging = chg
                r.online = True
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        return r

    @staticmethod
    def _query_cmd(s: HidSession, olen: int, cmd_id: int, parser):
        req = bytearray(olen)
        req[0] = 0xCC
        req[1] = 0x12
        req[2] = cmd_id
        for attempt in range(2):
            if attempt > 0:
                time.sleep(0.08)
            if not s.write(bytes(req)):
                continue
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                resp = s.read(250)
                if not resp:
                    break
                val = parser(resp)
                if val is not None:
                    return val
        return None


# ==============================================================================
# 2. 迈从 MCHOSE K99 V2 (MchoseProvider)
# ==============================================================================
class MchoseProvider:
    name = "MCHOSE HID"
    _VID = 0x3554
    _KNOWN = {
        0xFA09: "K99 V2",
        0xF5D5: "G75 Pro",
    }

    def discover(self, catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        best: dict[tuple[int, int], HidDeviceInfo] = {}
        for h in catalog:
            if h.vendor_id != self._VID or h.usage_page != 0xFF02:
                continue
            if h.input_len != 20 or h.output_len != 20:
                continue
            best.setdefault((h.vendor_id, h.product_id), h)
        out: list[DeviceCandidate] = []
        for (vid, pid), h in best.items():
            model = self._KNOWN.get(pid) or (h.product_name or f"MCHOSE {pid:04X}").strip()
            out.append(
                DeviceCandidate(
                    id=f"mchose-{vid:04X}:{pid:04X}",
                    name=f"MCHOSE {model}" if not model.upper().startswith("MCHOSE") else model,
                    brand="MCHOSE",
                    model=model,
                    kind=infer_device_kind(model, "keyboard"),
                    provider=self.name,
                    vendor_id=vid,
                    product_id=pid,
                    hid_path=h.path,
                )
            )
        return out

    @staticmethod
    def checksum(buf: bytes | bytearray) -> int:
        return sum(buf[:-1]) & 0xFF

    @classmethod
    def parse_battery(cls, frame: bytes) -> tuple[int, bool] | None:
        if len(frame) != 20:
            return None
        if frame[0] != 0x13 or frame[1] != 0x4A or frame[2] != 0x01 or frame[3] != 0x00 or frame[4] != 0x02:
            return None
        if frame[19] != cls.checksum(frame):
            return None
        pct = int(frame[5])
        if pct > 100:
            return None
        charging = (frame[6] >> 4) == 1
        return pct, charging

    def read(self, d: DeviceCandidate) -> BatteryReading:
        r = BatteryReading(detail="2.4G")
        cmd = bytearray(20)
        cmd[0] = 0x13
        cmd[1] = 0x4A
        cmd[2] = 0x01
        cmd[3] = 0x00
        cmd[19] = self.checksum(cmd)
        try:
            with HidSession(d.hid_path, 20, 20) as s:
                s.flush(30)
                parsed = None
                for attempt in range(3):
                    if attempt > 0:
                        time.sleep(0.12)
                    if not s.set_output_report(bytes(cmd)):
                        r.error = "发送失败（设备被占用？）"
                        return r
                    deadline = time.monotonic() + 0.45
                    while time.monotonic() < deadline:
                        resp = s.read(150)
                        if not resp:
                            break
                        parsed = self.parse_battery(resp)
                        if parsed is not None:
                            break
                    if parsed is not None:
                        break
                if parsed is None:
                    r.sleeping = True
                    r.error = "设备休眠"
                    return r
                r.percent, r.charging = parsed
                r.online = True
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        return r


# ==============================================================================
# 3. 罗技 Logitech GPW3 等 (LogitechProvider HID++ 2.0)
# ==============================================================================
class LogitechProvider:
    name = "Logitech HID++"
    _VID = 0x046D
    _SW_ID = 0x0E
    _KNOWN = {
        0xC54D: "GPW3 / Superlight 2",
        0xC539: "LIGHTSPEED",
        0xC547: "LIGHTSPEED",
        0xC548: "Logi Bolt",
        0xC52B: "Unifying",
    }
    def __init__(self):
        self._idx_cache: dict[str, int] = {}

    def discover(self, catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        best: dict[tuple[int, int], HidDeviceInfo] = {}
        for h in catalog:
            if h.vendor_id != self._VID or h.usage_page != 0xFF00 or h.usage != 0x0002 or h.output_len != 20:
                continue
            best.setdefault((h.vendor_id, h.product_id), h)
        out: list[DeviceCandidate] = []
        for (vid, pid), h in best.items():
            model = self._KNOWN.get(pid) or (h.product_name or f"Logitech {pid:04X}").strip()
            out.append(
                DeviceCandidate(
                    id=f"logi-{vid:04X}:{pid:04X}",
                    name=f"Logitech {model}" if not model.lower().startswith("logi") else model,
                    brand="Logitech",
                    model=model,
                    kind=infer_device_kind(model, "mouse"),
                    provider=self.name,
                    vendor_id=vid,
                    product_id=pid,
                    hid_path=h.path,
                )
            )
        return out

    def read(self, d: DeviceCandidate) -> BatteryReading:
        r = BatteryReading(detail="HID++ 2.0")
        try:
            with HidSession(d.hid_path, 20, 20) as s:
                s.flush(20)
                dev = self._resolve_index(s, d.hid_path)
                if dev == 0:
                    r.sleeping = True
                    r.error = "设备休眠"
                    return r

                feat, responded = self._get_feature(s, dev, 0x1000)
                if feat == 0 and not responded:
                    self._idx_cache.pop(d.hid_path, None)
                    r.sleeping = True
                    r.error = "设备休眠"
                    return r
                bat_func = 0x00
                if feat == 0:
                    feat, responded = self._get_feature(s, dev, 0x1004)
                    bat_func = 0x01
                    if feat == 0 and not responded:
                        self._idx_cache.pop(d.hid_path, None)
                        r.sleeping = True
                        r.error = "设备休眠"
                        return r
                    if feat == 0:
                        r.online = True
                        r.error = "该设备不上报电量"
                        return r

                bat = self._call(s, dev, feat, bat_func)
                if bat is None:
                    self._idx_cache.pop(d.hid_path, None)
                    r.sleeping = True
                    r.error = "设备休眠"
                    return r
                if bat[2] == 0xFF:
                    r.error = f"HID++ 错误 0x{bat[5]:02X}"
                    return r
                lvl = int(bat[4])
                if lvl > 100:
                    r.online = True
                    r.error = "读数异常"
                    return r
                r.percent = lvl
                r.charging = (bat[5] & 0x02) != 0
                r.online = True
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        return r

    def _resolve_index(self, s: HidSession, path: str) -> int:
        cached = self._idx_cache.get(path)
        if cached:
            return cached
        deadline = time.monotonic() + 0.9
        for i in range(1, 7):
            if time.monotonic() >= deadline:
                break
            if self._ping(s, i):
                self._idx_cache[path] = i
                return i
        return 0

    def _ping(self, s: HidSession, dev: int) -> bool:
        pkt = bytearray(20)
        pkt[0] = 0x11
        pkt[1] = dev
        pkt[2] = 0x00
        pkt[3] = (0x01 << 4) | self._SW_ID
        pkt[6] = 0xAA
        if not s.write(bytes(pkt)):
            return False
        end = time.monotonic() + 0.16
        while time.monotonic() < end:
            resp = s.read(80)
            if resp and len(resp) >= 7 and resp[1] == dev:
                if resp[2] == 0xFF:
                    return False
                if (resp[3] & 0x0F) == self._SW_ID:
                    return True
        return False

    def _get_feature(self, s: HidSession, dev: int, feat_id: int) -> tuple[int, bool]:
        resp = self._call(s, dev, 0x00, 0x00, bytes([(feat_id >> 8) & 0xFF, feat_id & 0xFF]))
        if resp is None:
            return 0, False
        return (0, True) if resp[2] == 0xFF else (int(resp[4]), True)

    def _call(self, s: HidSession, dev: int, feat_idx: int, func_id: int, args: bytes = b"") -> bytes | None:
        pkt = bytearray(20)
        pkt[0] = 0x11
        pkt[1] = dev
        pkt[2] = feat_idx & 0xFF
        pkt[3] = ((func_id & 0x0F) << 4) | self._SW_ID
        for i, b in enumerate(args[:16]):
            pkt[4 + i] = b
        for attempt in range(2):
            if attempt > 0:
                time.sleep(0.05)
            if not s.write(bytes(pkt)):
                continue
            end = time.monotonic() + 0.25
            while time.monotonic() < end:
                resp = s.read(100)
                if resp and len(resp) >= 7 and resp[1] == dev:
                    if resp[2] == 0xFF or ((resp[3] & 0x0F) == self._SW_ID and resp[2] == (feat_idx & 0xFF)):
                        return resp
        return None


# ==============================================================================
# 4. 通用 HID 耳机 (Razer Audio 64B + 标准 HID Battery/Telephony Feature)
# ==============================================================================
class GenericHidHeadsetProvider:
    name = "HID Headset"
    _RAZER_VID = 0x1532

    def discover(self, catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        out: list[DeviceCandidate] = []
        seen: set[tuple[int, int]] = set()
        for h in catalog:
            key = (h.vendor_id, h.product_id)
            if key in seen:
                continue
            # Razer BlackShark V2 Pro 等 64 字节音频接收器
            if h.vendor_id == self._RAZER_VID and h.usage_page == 0xFF00 and h.input_len == 64 and h.output_len == 64 and h.product_id != 0x007E:
                seen.add(key)
                name = (h.product_name or "Razer 无线耳机").strip()
                out.append(
                    DeviceCandidate(
                        id=f"hid-hs-{h.vendor_id:04X}:{h.product_id:04X}",
                        name=name,
                        brand="Razer",
                        model=name,
                        kind="headset",
                        provider=self.name,
                        vendor_id=h.vendor_id,
                        product_id=h.product_id,
                        hid_path=h.path,
                        extra={"proto": "razer_audio"},
                    )
                )
        return out

    def read(self, d: DeviceCandidate) -> BatteryReading:
        r = BatteryReading(detail="2.4G HID")
        if d.extra.get("proto") == "razer_audio":
            try:
                with HidSession(d.hid_path, 64, 64) as s:
                    s.flush(40)
                    pct = self._query_razer_audio(s, 0x21)
                    if pct is None:
                        r.sleeping = True
                        r.error = "耳机未应答"
                        return r
                    chg = self._query_razer_audio(s, 0x2A)
                    r.percent = max(0, min(100, pct))
                    r.charging = (chg == 1) if chg is not None else None
                    r.online = True
            except Exception as e:  # noqa: BLE001
                r.error = str(e)
        return r

    @staticmethod
    def _query_razer_audio(s: HidSession, field_id: int) -> int | None:
        req = bytearray(64)
        req[:14] = bytes([0x02, 0x80, 0x09, 0x00, 0x00, 0x50, 0x41, 0x08, 0x00, 0x04, field_id, 0x00, 0x01, 0x01])
        if not s.write(bytes(req)):
            return None
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            resp = s.read(150)
            if resp and len(resp) >= 15 and resp[0] == 0x02 and resp[2:4] == b"\x50\x49" and resp[11] == field_id:
                return int(resp[14])
        return None


# ==============================================================================
# 5. XInput 手柄 (Xbox / 8BitDo 等)
# ==============================================================================
class _XINPUT_BATTERY_INFO(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XINPUT_GAMEPAD)]


class XInputProvider:
    name = "XInput"
    _LEVEL_PCT = {0: 5, 1: 20, 2: 50, 3: 100}

    def __init__(self):
        self._dll = None
        for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self._dll = ctypes.WinDLL(dll_name)
                break
            except OSError:
                continue

    def _connected(self, slot: int) -> bool:
        if self._dll is None:
            return False
        st = _XINPUT_STATE()
        return self._dll.XInputGetState(slot, ctypes.byref(st)) == 0

    def discover(self, _catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        out: list[DeviceCandidate] = []
        if self._dll is None:
            return out
        for slot in range(4):
            if not self._connected(slot):
                continue
            out.append(
                DeviceCandidate(
                    id=f"xinput-{slot}",
                    name=f"Xbox 手柄 {slot + 1}" if slot > 0 else "Xbox 手柄",
                    brand="Xbox",
                    model=f"XInput {slot + 1}",
                    kind="gamepad",
                    provider=self.name,
                    extra={"slot": slot},
                )
            )
        return out

    def read(self, d: DeviceCandidate) -> BatteryReading:
        slot = int(d.extra.get("slot", 0))
        r = BatteryReading(detail="XInput")
        if self._dll is None or not hasattr(self._dll, "XInputGetBatteryInformation"):
            r.error = "XInput 不可用"
            return r
        if not self._connected(slot):
            r.sleeping = True
            r.error = "手柄休眠"
            return r
        info = _XINPUT_BATTERY_INFO()
        if self._dll.XInputGetBatteryInformation(slot, 0, ctypes.byref(info)) != 0:
            r.sleeping = True
            return r
        btype, blevel = int(info.BatteryType), int(info.BatteryLevel)
        if btype == 0:
            r.sleeping = True
            return r
        if btype == 1:
            r.percent = 100
            r.charging = False
            r.detail = "有线连接"
            r.online = True
            return r
        r.percent = self._LEVEL_PCT.get(blevel, 50)
        r.charging = False
        r.detail = "四档电量"
        r.online = True
        return r


# ==============================================================================
# 6. PlayStation 手柄 (DualSense / DualSense Edge / DualShock 4)
# ==============================================================================
class PlayStationProvider:
    name = "PlayStation HID"
    _SONY_VID = 0x054C
    _DUALSENSE_PIDS = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}
    _DUALSHOCK_PIDS = {0x05C4: "DualShock 4", 0x09CC: "DualShock 4 v2", 0x0BA0: "DualShock 4 Adapter"}

    def discover(self, catalog: list[HidDeviceInfo]) -> list[DeviceCandidate]:
        out: list[DeviceCandidate] = []
        seen: set[int] = set()
        for h in catalog:
            if h.vendor_id != self._SONY_VID or h.usage_page != 0x0001 or h.usage not in (0x0004, 0x0005):
                continue
            model = self._DUALSENSE_PIDS.get(h.product_id) or self._DUALSHOCK_PIDS.get(h.product_id)
            if not model or h.product_id in seen:
                continue
            seen.add(h.product_id)
            is_bt = ("bth" in h.path.lower()) or (h.input_len >= 78)
            out.append(
                DeviceCandidate(
                    id=f"ps-{h.product_id:04X}",
                    name=model,
                    brand="PlayStation",
                    model=model,
                    kind="gamepad",
                    provider=self.name,
                    vendor_id=h.vendor_id,
                    product_id=h.product_id,
                    hid_path=h.path,
                    extra={"input_len": h.input_len, "bluetooth": is_bt},
                )
            )
        return out

    @classmethod
    def parse_report(cls, pid: int, data: bytes, bluetooth: bool) -> tuple[int, bool] | None:
        if pid in cls._DUALSENSE_PIDS:
            at = 54 if (bluetooth and len(data) >= 78 and data[0] == 0x31) else (53 if (not bluetooth and len(data) >= 64 and data[0] == 0x01) else -1)
            if at < 0:
                return None
            raw = data[at]
            lvl, state = raw & 0x0F, raw >> 4
            if state == 0x00:
                return min(lvl * 10 + 5, 100), False
            if state == 0x01:
                return min(lvl * 10 + 5, 100), True
            if state == 0x02:
                return 100, False
            return None
        if pid in cls._DUALSHOCK_PIDS:
            at = 32 if (bluetooth and len(data) >= 78 and data[0] == 0x11) else (30 if (not bluetooth and len(data) >= 64 and data[0] == 0x01) else -1)
            if at < 0:
                return None
            raw = data[at]
            lvl = raw & 0x0F
            cable = (raw & 0x10) != 0
            if cable:
                return (100, False) if lvl >= 11 else (min(lvl * 10 + 5, 100), True)
            return min(lvl * 10 + 5, 100), False
        return None

    def read(self, d: DeviceCandidate) -> BatteryReading:
        bt = bool(d.extra.get("bluetooth", False))
        ilen = int(d.extra.get("input_len", 78 if bt else 64))
        r = BatteryReading(detail="蓝牙" if bt else "USB")
        try:
            with HidSession(d.hid_path, ilen, ilen, read_only=True) as s:
                end = time.monotonic() + 0.45
                while time.monotonic() < end:
                    pkt = s.read(160)
                    if not pkt:
                        break
                    parsed = self.parse_report(d.product_id, pkt, bt)
                    if parsed is not None:
                        r.percent, r.charging = parsed
                        r.online = True
                        return r
                r.sleeping = True
                r.error = "手柄未上报"
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        return r


# ==============================================================================
# 7. Apple AirPods 邻近广播解析 + Windows 原生蓝牙电量
# ==============================================================================
class AppleAdvertParser:
    COMPANY_ID = 0x004C
    _MODELS = {
        0x0220: "AirPods",
        0x0F20: "AirPods",
        0x1320: "AirPods",
        0x1920: "AirPods 4",
        0x1B20: "AirPods 4",
        0x0E20: "AirPods Pro",
        0x1420: "AirPods Pro",
        0x2420: "AirPods Pro",
        0x2720: "AirPods Pro",
        0x0A20: "AirPods Max",
        0x1F20: "AirPods Max",
        0x0B20: "Powerbeats Pro",
    }
    _SINGLES = {0x0A20, 0x1F20}

    @classmethod
    def model_for_pid(cls, pid: int) -> str | None:
        """将 Windows 蓝牙 PnP ProductId（如 0x2024）映射为型号名（兼容大/小端字节序）。"""
        if not pid:
            return None
        swapped = ((pid & 0xFF) << 8) | ((pid >> 8) & 0xFF)
        return cls._MODELS.get(swapped) or cls._MODELS.get(pid)

    @classmethod
    def parse(cls, data: bytes) -> dict | None:
        if len(data) < 8 or data[0] != 0x07 or data[1] != 0x19:
            return None
        model_id = (data[3] << 8) | data[4]
        model = cls._MODELS.get(model_id)
        if not model:
            return None
        if model_id in cls._SINGLES:
            nibble = data[6] & 0x0F
            if nibble > 10:
                return None
            return {
                "model": model,
                "model_id": model_id,
                "side": "left",
                "percent": nibble * 10,
                "charging": (data[7] & 0x10) != 0,
                "parts": [],
            }

        flipped = (data[5] & 0x20) == 0
        side = "left" if flipped else "right"
        high, low = data[6] >> 4, data[6] & 0x0F
        case_nibble, chg = data[7] & 0x0F, data[7] >> 4
        left_n = high if flipped else low
        right_n = low if flipped else high
        left_chg = (chg & (0x02 if flipped else 0x01)) != 0
        right_chg = (chg & (0x01 if flipped else 0x02)) != 0
        case_chg = (chg & 0x04) != 0

        parts: list[tuple[str, int, bool]] = []
        bud_pcts: list[int] = []
        if left_n <= 10:
            parts.append(("左", left_n * 10, left_chg))
            bud_pcts.append(left_n * 10)
        if right_n <= 10:
            parts.append(("右", right_n * 10, right_chg))
            bud_pcts.append(right_n * 10)
        if case_nibble <= 10:
            parts.append(("盒", case_nibble * 10, case_chg))
        if not parts:
            return None
        overall = min(bud_pcts) if bud_pcts else parts[0][1]
        return {
            "model": model,
            "model_id": model_id,
            "side": side,
            "percent": overall,
            "charging": bool(bud_pcts) and all(p[2] for p in parts if p[0] in ("左", "右")),
            "parts": parts,
        }


class BluetoothBatteryProvider:
    name = "Windows Bluetooth"

    def discover_and_read(
        self,
        apple_snapshot: dict | None = None,
        connected_apple: list[dict] | None = None,
    ) -> list[tuple[DeviceCandidate, BatteryReading]]:
        rows = read_bluetooth_batteries()
        out: list[tuple[DeviceCandidate, BatteryReading]] = []
        conn_list: list[dict] = (
            list(connected_apple)
            if connected_apple is not None
            else (get_connected_apple_bluetooth_devices() if apple_snapshot else [])
        )
        apple_macs = {str(d.get("mac") or "").upper() for d in conn_list if d.get("mac")}
        apple_names = {
            str(d.get("name") or "").strip().lower()
            for d in conn_list
            if str(d.get("name") or "").strip()
        }
        merged_apple = False
        for item in rows:
            name = item["name"]
            pct = int(item["percent"])
            kind = infer_device_kind(name, "bluetooth")
            item_id_up = str(item.get("id") or "").upper()
            name_low = name.strip().lower()
            is_apple_row = (
                any(mac in item_id_up for mac in apple_macs)
                or (name_low in apple_names)
                or ("airpods" in name_low)
                or ("beats" in name_low)
            )
            parts: list[tuple[str, int, bool]] = []
            charging: bool | None = None
            detail = "蓝牙"
            if is_apple_row and apple_snapshot and apple_snapshot.get("parts"):
                merged_apple = True
                parts = list(apple_snapshot["parts"])
                pct = int(apple_snapshot["percent"])
                charging = bool(apple_snapshot.get("charging", False))
                detail = " ".join(f"{k}{v}%" for k, v, _ in parts)
            cand = DeviceCandidate(
                id=item["id"],
                name=name,
                brand="Apple" if is_apple_row else "Bluetooth",
                model=name,
                kind=kind,
                provider=self.name,
            )
            rd = BatteryReading(percent=pct, charging=charging, online=True, detail=detail, parts=parts)
            out.append((cand, rd))
        if apple_snapshot and not merged_apple:
            if not conn_list and not has_connected_apple_bluetooth():
                return out
            parts = list(apple_snapshot.get("parts") or [])
            pct = int(apple_snapshot["percent"])
            charging = bool(apple_snapshot.get("charging", False))
            model = str(apple_snapshot.get("model") or "AirPods")
            display_name = model
            for dev in conn_list:
                dev_model = AppleAdvertParser.model_for_pid(int(dev.get("pid") or 0))
                if (dev_model is None or dev_model == model) and str(dev.get("name") or "").strip():
                    display_name = str(dev["name"]).strip()
                    break
            detail = " ".join(f"{k}{v}%" for k, v, _ in parts) if parts else "蓝牙广播"
            out.append(
                (
                    DeviceCandidate(
                        id=f"apple:{model.lower().replace(' ', '-')}",
                        name=display_name,
                        brand="Apple",
                        model=model,
                        kind="earbuds",
                        provider="Apple BLE",
                    ),
                    BatteryReading(percent=pct, charging=charging, online=True, detail=detail, parts=parts),
                )
            )
        return out
