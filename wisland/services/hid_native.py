# -*- coding: utf-8 -*-
"""Windows 原生 HID 枚举、Overlapped 读写及蓝牙 PnP 电量属性读取（仅标准库 ctypes，零第三方驱动依赖）。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import time

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_hid = ctypes.WinDLL("hid", use_last_error=True)

_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_RW = 0x00000003
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_DIGCF_PRESENT = 0x02
_DIGCF_ALLCLASSES = 0x04
_DIGCF_DEVICEINTERFACE = 0x10
_HIDP_STATUS_SUCCESS = 0x00110000

_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [
        ("fmtid", _GUID),
        ("pid", wintypes.ULONG),
    ]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", _GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", _ULONG_PTR),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", _ULONG_PTR),
    ]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", _ULONG_PTR),
        ("InternalHigh", _ULONG_PTR),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


# 常用 Windows GUID / DEVPROPKEY
_HID_GUID = _GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))
# {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, pid=2 -> 蓝牙/PnP 电池百分比
_DEVPKEY_BATTERY = _DEVPROPKEY(
    _GUID(0x104EA319, 0x6EE2, 0x4701, (ctypes.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5)),
    2,
)
# {B725F130-47EF-101A-A5F1-02608C9EEBAC}, pid=10 -> DEVPKEY_NAME
_DEVPKEY_NAME = _DEVPROPKEY(
    _GUID(0xB725F130, 0x47EF, 0x101A, (ctypes.c_ubyte * 8)(0xA5, 0xF1, 0x02, 0x60, 0x8C, 0x9E, 0xEB, 0xAC)),
    10,
)
# {83DA6326-97A6-4088-9453-A1923F573B29}, pid=15 -> DEVPKEY_Device_IsConnected
_DEVPKEY_CONNECTED = _DEVPROPKEY(
    _GUID(0x83DA6326, 0x97A6, 0x4088, (ctypes.c_ubyte * 8)(0x94, 0x53, 0xA1, 0x92, 0x3F, 0x57, 0x3B, 0x29)),
    15,
)

# ctypes 函数签名声明（确保 64 位指针安全）
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
_kernel32.ResetEvent.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
_kernel32.GetOverlappedResult.restype = wintypes.BOOL
_kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
_kernel32.CancelIoEx.restype = wintypes.BOOL

_setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
_setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
_setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
_setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
_setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
_setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA)]
_setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
_setupapi.SetupDiGetDevicePropertyW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), ctypes.POINTER(_DEVPROPKEY), ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
_setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
_setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL

_hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
_hid.HidD_GetAttributes.restype = wintypes.BOOL
_hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
_hid.HidD_GetPreparsedData.restype = wintypes.BOOL
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidD_FreePreparsedData.restype = wintypes.BOOL
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
_hid.HidP_GetCaps.restype = wintypes.LONG
_hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_GetProductString.restype = wintypes.BOOL
_hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_GetManufacturerString.restype = wintypes.BOOL
_hid.HidD_SetOutputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_SetOutputReport.restype = wintypes.BOOL
_hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_SetFeature.restype = wintypes.BOOL
_hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_GetFeature.restype = wintypes.BOOL


@dataclass
class HidDeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product_name: str = ""
    manufacturer: str = ""


def enumerate_hid() -> list[HidDeviceInfo]:
    """枚举当前系统存在的所有 HID 接口集合。"""
    out: list[HidDeviceInfo] = []
    h_dev = _setupapi.SetupDiGetClassDevsW(ctypes.byref(_HID_GUID), None, None, _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if not h_dev or h_dev == _INVALID_HANDLE_VALUE:
        return out
    try:
        did = _SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
        idx = 0
        detail_cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        while _setupapi.SetupDiEnumDeviceInterfaces(h_dev, None, ctypes.byref(_HID_GUID), idx, ctypes.byref(did)):
            idx += 1
            req = wintypes.DWORD(0)
            _setupapi.SetupDiGetDeviceInterfaceDetailW(h_dev, ctypes.byref(did), None, 0, ctypes.byref(req), None)
            if req.value <= 4:
                continue
            buf = ctypes.create_string_buffer(req.value)
            ctypes.memmove(buf, ctypes.byref(ctypes.c_uint32(detail_cb_size)), 4)
            req2 = wintypes.DWORD(0)
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(h_dev, ctypes.byref(did), buf, req.value, ctypes.byref(req2), None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if not path:
                continue
            h = _kernel32.CreateFileW(path, 0, _FILE_SHARE_RW, None, _OPEN_EXISTING, 0, None)
            if not h or h == _INVALID_HANDLE_VALUE:
                continue
            try:
                attrs = _HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
                if not _hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    continue
                pp = ctypes.c_void_p(0)
                caps = _HIDP_CAPS()
                ok = bool(_hid.HidD_GetPreparsedData(h, ctypes.byref(pp)))
                if ok:
                    ok = _hid.HidP_GetCaps(pp, ctypes.byref(caps)) == _HIDP_STATUS_SUCCESS
                if pp.value:
                    _hid.HidD_FreePreparsedData(pp)
                if not ok:
                    continue
                pbuf = ctypes.create_unicode_buffer(128)
                mbuf = ctypes.create_unicode_buffer(128)
                pname = pbuf.value.strip() if _hid.HidD_GetProductString(h, pbuf, ctypes.sizeof(pbuf)) else ""
                mname = mbuf.value.strip() if _hid.HidD_GetManufacturerString(h, mbuf, ctypes.sizeof(mbuf)) else ""
                out.append(
                    HidDeviceInfo(
                        path=path,
                        vendor_id=int(attrs.VendorID),
                        product_id=int(attrs.ProductID),
                        usage_page=int(caps.UsagePage),
                        usage=int(caps.Usage),
                        input_len=int(caps.InputReportByteLength),
                        output_len=int(caps.OutputReportByteLength),
                        feature_len=int(caps.FeatureReportByteLength),
                        product_name=pname,
                        manufacturer=mname,
                    )
                )
            finally:
                _kernel32.CloseHandle(h)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(h_dev)
    return out


class HidSession:
    """Overlapped I/O HID 会话（避免阻塞读写互锁或无应答时挂死）。"""

    def __init__(self, path: str, input_len: int, output_len: int, read_only: bool = False):
        self.input_len = max(1, int(input_len))
        self.output_len = max(1, int(output_len))
        access = _GENERIC_READ if read_only else (_GENERIC_READ | _GENERIC_WRITE)
        self._handle = _kernel32.CreateFileW(
            path, access, _FILE_SHARE_RW, None, _OPEN_EXISTING, _FILE_FLAG_OVERLAPPED, None
        )
        if not self._handle or self._handle == _INVALID_HANDLE_VALUE:
            raise OSError(f"CreateFileW failed: {ctypes.get_last_error()}")
        self._evt_r = _kernel32.CreateEventW(None, True, False, None)
        self._evt_w = _kernel32.CreateEventW(None, True, False, None)

    def __enter__(self) -> HidSession:
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if getattr(self, "_handle", None) and self._handle != _INVALID_HANDLE_VALUE:
            _kernel32.CloseHandle(self._handle)
            self._handle = _INVALID_HANDLE_VALUE
        for attr in ("_evt_r", "_evt_w"):
            ev = getattr(self, attr, None)
            if ev:
                _kernel32.CloseHandle(ev)
                setattr(self, attr, None)

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        _kernel32.ResetEvent(self._evt_w)
        ov = _OVERLAPPED()
        ov.hEvent = self._evt_w
        written = wintypes.DWORD(0)
        ok = _kernel32.WriteFile(self._handle, buf, len(data), ctypes.byref(written), ctypes.byref(ov))
        if ok:
            return True
        if ctypes.get_last_error() != _ERROR_IO_PENDING:
            return False
        if _kernel32.WaitForSingleObject(self._evt_w, timeout_ms) == _WAIT_OBJECT_0:
            return bool(_kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(written), False))
        _kernel32.CancelIoEx(self._handle, ctypes.byref(ov))
        return False

    def read(self, timeout_ms: int = 300) -> bytes | None:
        buf = (ctypes.c_ubyte * self.input_len)()
        _kernel32.ResetEvent(self._evt_r)
        ov = _OVERLAPPED()
        ov.hEvent = self._evt_r
        nread = wintypes.DWORD(0)
        ok = _kernel32.ReadFile(self._handle, buf, self.input_len, ctypes.byref(nread), ctypes.byref(ov))
        if not ok:
            if ctypes.get_last_error() != _ERROR_IO_PENDING:
                return None
            if _kernel32.WaitForSingleObject(self._evt_r, timeout_ms) != _WAIT_OBJECT_0:
                _kernel32.CancelIoEx(self._handle, ctypes.byref(ov))
                return None
            if not _kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(nread), False):
                return None
        return bytes(buf[: nread.value])

    def flush(self, drain_ms: int = 40):
        deadline = time.monotonic() + drain_ms / 1000.0
        while time.monotonic() < deadline:
            if self.read(15) is None:
                break

    def set_output_report(self, data: bytes) -> bool:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return bool(_hid.HidD_SetOutputReport(self._handle, buf, len(data)))

    def set_feature(self, data: bytes) -> bool:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return bool(_hid.HidD_SetFeature(self._handle, buf, len(data)))

    def get_feature(self, report_id: int, length: int) -> bytes | None:
        buf = (ctypes.c_ubyte * length)()
        buf[0] = report_id & 0xFF
        if not _hid.HidD_GetFeature(self._handle, buf, length):
            return None
        return bytes(buf)


def read_bluetooth_batteries() -> list[dict]:
    """读取 Windows 蓝牙/PnP 设备电池百分比（DEVPKEY_Device_BatteryPlus，只读系统已连接设备）。"""
    out: list[dict] = []
    seen: set[str] = set()
    h_dev = _setupapi.SetupDiGetClassDevsW(None, None, None, _DIGCF_PRESENT | _DIGCF_ALLCLASSES)
    if not h_dev or h_dev == _INVALID_HANDLE_VALUE:
        return out
    try:
        dev_data = _SP_DEVINFO_DATA()
        dev_data.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
        idx = 0
        id_buf = ctypes.create_unicode_buffer(512)
        name_buf = ctypes.create_unicode_buffer(256)
        while _setupapi.SetupDiEnumDeviceInfo(h_dev, idx, ctypes.byref(dev_data)):
            idx += 1
            req = wintypes.DWORD(0)
            if not _setupapi.SetupDiGetDeviceInstanceIdW(h_dev, ctypes.byref(dev_data), id_buf, len(id_buf), ctypes.byref(req)):
                continue
            inst_id = id_buf.value
            up_id = inst_id.upper()
            if not (up_id.startswith("BTHENUM\\") or up_id.startswith("BTHLE\\") or up_id.startswith("BTHLEDEVICE\\")):
                continue

            # 读取电池百分比（BYTE）
            ptype = wintypes.ULONG(0)
            bat = ctypes.c_ubyte(0)
            rsize = wintypes.DWORD(0)
            if not _setupapi.SetupDiGetDevicePropertyW(
                h_dev, ctypes.byref(dev_data), ctypes.byref(_DEVPKEY_BATTERY),
                ctypes.byref(ptype), ctypes.byref(bat), ctypes.sizeof(bat), ctypes.byref(rsize), 0
            ):
                continue
            pct = int(bat.value)
            if not (0 <= pct <= 100):
                continue

            # 若系统报告了连接状态且明确未连接，则跳过
            conn = ctypes.c_ubyte(1)
            if _setupapi.SetupDiGetDevicePropertyW(
                h_dev, ctypes.byref(dev_data), ctypes.byref(_DEVPKEY_CONNECTED),
                ctypes.byref(ptype), ctypes.byref(conn), ctypes.sizeof(conn), ctypes.byref(rsize), 0
            ):
                if conn.value == 0:
                    continue

            name = ""
            if _setupapi.SetupDiGetDevicePropertyW(
                h_dev, ctypes.byref(dev_data), ctypes.byref(_DEVPKEY_NAME),
                ctypes.byref(ptype), name_buf, ctypes.sizeof(name_buf), ctypes.byref(rsize), 0
            ):
                name = name_buf.value.strip()
            if not name:
                name = inst_id.split("\\")[-1]
            # 去掉同一物理设备多个服务节点（如 HFP/AVRCP）的重复条目，按名称归一
            norm = name.lower()
            if norm in seen:
                continue
            seen.add(norm)
            out.append({"id": f"bt:{inst_id}", "name": name, "percent": pct})
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(h_dev)
    return out
