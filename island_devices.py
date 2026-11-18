"""Hardware telemetry for Default Island: laptop battery, Bluetooth devices, GPU.

Pure ctypes on top of kernel32 / cfgmgr32 / BluetoothApis / pdh / dxgi / nvml.
No Qt, no subprocesses, no extra packages.

Threading model
    * ``battery_status()`` is a single cheap syscall, call it from anywhere.
    * ``DeviceMonitor`` and ``GpuMonitor`` are daemon threads.  Create them,
      call ``.start()``, then read snapshots with ``devices()`` /
      ``poll_events()`` / ``status()`` from the GUI thread.  Those accessors
      only copy a value under a lock and never raise.
    * ``stop()`` only signals the thread; it releases its handles and exits by
      itself within a moment.  A stopped monitor cannot be restarted, create a
      new one.

Battery semantics of ``WirelessDevice.percent``
    Only connected devices carry a percent.  A "connected" event always has
    ``percent=None``; the first reading after a connect arrives as a separate
    "battery" event (immediately for a fresh value, otherwise within a few
    seconds), further "battery" events follow whenever the value changes.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import re
import struct
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

__all__ = [
    "BatteryStatus", "battery_status",
    "WirelessDevice", "DeviceEvent", "DeviceMonitor",
    "GpuStatus", "GpuMonitor",
]

_log = logging.getLogger(__name__)
_logged: set[str] = set()


def _log_once(key: str, msg: str, *args, exc_info: bool = False) -> None:
    """Warn once per key, so a failure repeated every second cannot flood."""
    if key in _logged or len(_logged) >= 200:
        return
    _logged.add(key)
    _log.warning(msg, *args, exc_info=exc_info)


# ───────────────────────────── ctypes plumbing ──────────────────────────────

_U8 = ctypes.c_ubyte
_U16 = ctypes.c_uint16
_U32 = ctypes.c_uint32
_PTR = ctypes.c_void_p
_WSTR = ctypes.c_wchar_p
_P = ctypes.POINTER
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x0800


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", _U32), ("Data2", _U16), ("Data3", _U16),
                ("Data4", _U8 * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", _U32)]


def _pkey(fmtid: str, pid: int) -> _DEVPROPKEY:
    return _DEVPROPKEY(_GUID.parse(fmtid), pid)


_DEVICE_FMTID = "{a45c254e-df1c-4efd-8020-67d146a850e0}"
_PK_DEVICE_DESC = _pkey(_DEVICE_FMTID, 2)
_PK_HARDWARE_IDS = _pkey(_DEVICE_FMTID, 3)
_PK_COMPATIBLE_IDS = _pkey(_DEVICE_FMTID, 4)
_PK_FRIENDLY_NAME = _pkey(_DEVICE_FMTID, 14)
_PK_NAME = _pkey("{b725f130-47ef-101a-a5f1-02608c9eebac}", 10)
_PK_IS_CONNECTED = _pkey("{83da6326-97a6-4088-9453-a1923f573b29}", 15)
_PK_BT_BATTERY = _pkey("{104ea319-6ee2-4701-bd47-8ddbf425bbe5}", 2)
_PK_BT_CLASS_OF_DEVICE = _pkey("{2bd67d8b-8beb-48d5-87e0-6cda3428040a}", 10)

_GUID_GATT_SERVICE_INTERFACE = _GUID.parse(
    "{6e3bb679-4372-40c8-9eaa-4509df260cd8}")
_IID_IDXGIFactory1 = _GUID.parse("{770aae78-f26f-4dba-a829-253c83d1b387}")

_DEVPROP_TYPE_BOOLEAN = 0x11
_DEVPROP_TYPE_STRING = 0x12
_DEVPROP_TYPE_STRING_LIST = 0x2012

_CR_SUCCESS = 0x00
_CR_NO_SUCH_DEVNODE = 0x0D
_CR_BUFFER_SMALL = 0x1A
_CR_NO_SUCH_VALUE = 0x25
_CM_GETIDLIST_FILTER_ENUMERATOR = 0x001
_CM_GETIDLIST_FILTER_PRESENT = 0x100
_CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0

_libs: dict[str, object] = {}
_libs_lock = threading.Lock()


def _lib(name: str, setup):
    """Load a System32 DLL once and declare its prototypes; None if missing."""
    with _libs_lock:
        if name in _libs:
            return _libs[name]
        lib = None
        try:
            lib = ctypes.WinDLL(name, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
            setup(lib)
        except Exception as exc:    # not Windows, DLL or export missing
            lib = None
            _log_once("dll:" + name, "%s is unavailable: %r", name, exc)
        _libs[name] = lib
        return lib


def _setup_kernel32(lib) -> None:
    lib.GetSystemPowerStatus.argtypes = [_P(_SYSTEM_POWER_STATUS)]
    lib.GetSystemPowerStatus.restype = ctypes.c_int
    lib.CreateFileW.argtypes = [_WSTR, _U32, _U32, _PTR, _U32, _U32, _PTR]
    lib.CreateFileW.restype = _PTR
    lib.CloseHandle.argtypes = [_PTR]
    lib.CloseHandle.restype = ctypes.c_int


def _setup_cfgmgr(lib) -> None:
    protos = {
        "CM_Get_Device_ID_List_SizeW": [_P(_U32), _WSTR, _U32],
        "CM_Get_Device_ID_ListW": [_WSTR, _PTR, _U32, _U32],
        "CM_Locate_DevNodeW": [_P(_U32), _WSTR, _U32],
        "CM_Get_DevNode_PropertyW": [_U32, _P(_DEVPROPKEY), _P(_U32), _PTR,
                                     _P(_U32), _U32],
        "CM_Get_Child": [_P(_U32), _U32, _U32],
        "CM_Get_Sibling": [_P(_U32), _U32, _U32],
        "CM_Get_Device_IDW": [_U32, _PTR, _U32, _U32],
        "CM_Get_Device_Interface_List_SizeW": [_P(_U32), _P(_GUID), _WSTR,
                                               _U32],
        "CM_Get_Device_Interface_ListW": [_P(_GUID), _WSTR, _PTR, _U32, _U32],
    }
    for name, argtypes in protos.items():
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype = _U32


def _setup_bthapis(lib) -> None:
    lib.BluetoothGATTGetCharacteristics.argtypes = [
        _PTR, _PTR, _U16, _PTR, _P(_U16), _U32]
    lib.BluetoothGATTGetCharacteristics.restype = ctypes.c_long
    lib.BluetoothGATTGetCharacteristicValue.argtypes = [
        _PTR, _P(_BTH_LE_GATT_CHARACTERISTIC), _U32, _PTR, _P(_U16), _U32]
    lib.BluetoothGATTGetCharacteristicValue.restype = ctypes.c_long
    lib.BluetoothFindFirstDevice.argtypes = [
        _P(_BLUETOOTH_DEVICE_SEARCH_PARAMS), _P(_BLUETOOTH_DEVICE_INFO)]
    lib.BluetoothFindFirstDevice.restype = _PTR
    lib.BluetoothFindNextDevice.argtypes = [_PTR, _P(_BLUETOOTH_DEVICE_INFO)]
    lib.BluetoothFindNextDevice.restype = ctypes.c_int
    lib.BluetoothFindDeviceClose.argtypes = [_PTR]
    lib.BluetoothFindDeviceClose.restype = ctypes.c_int


def _setup_pdh(lib) -> None:
    lib.PdhOpenQueryW.argtypes = [_WSTR, _PTR, _P(_PTR)]
    lib.PdhAddEnglishCounterW.argtypes = [_PTR, _WSTR, _PTR, _P(_PTR)]
    lib.PdhCollectQueryData.argtypes = [_PTR]
    lib.PdhGetFormattedCounterArrayW.argtypes = [
        _PTR, _U32, _P(_U32), _P(_U32), _PTR]
    lib.PdhCloseQuery.argtypes = [_PTR]
    for name in ("PdhOpenQueryW", "PdhAddEnglishCounterW",
                 "PdhCollectQueryData", "PdhGetFormattedCounterArrayW",
                 "PdhCloseQuery"):
        getattr(lib, name).restype = _U32


def _setup_dxgi(lib) -> None:
    lib.CreateDXGIFactory1.argtypes = [_P(_GUID), _P(_PTR)]
    lib.CreateDXGIFactory1.restype = ctypes.c_long


def _kernel32():
    return _lib("kernel32.dll", _setup_kernel32)


def _cfgmgr():
    return _lib("cfgmgr32.dll", _setup_cfgmgr)


def _bthapis():
    return _lib("BluetoothApis.dll", _setup_bthapis)


# ─────────────────────────────── laptop battery ─────────────────────────────

@dataclass(frozen=True)
class BatteryStatus:
    percent: int          # 0..100
    charging: bool        # on AC power


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", _U8), ("BatteryFlag", _U8),
                ("BatteryLifePercent", _U8), ("SystemStatusFlag", _U8),
                ("BatteryLifeTime", _U32), ("BatteryFullLifeTime", _U32)]


def _decode_power_status(ac_line: int, flag: int,
                         percent: int) -> BatteryStatus | None:
    # BatteryFlag: 128 = no system battery, 255 = unknown.
    # BatteryLifePercent: 255 = unknown.
    if flag == 255 or flag & 128 or percent > 100:
        return None
    return BatteryStatus(percent=int(percent), charging=ac_line == 1)


def battery_status() -> BatteryStatus | None:
    """Battery level and AC state; None on a PC without a battery."""
    try:
        k32 = _kernel32()
        if k32 is None:
            return None
        sps = _SYSTEM_POWER_STATUS()
        if not k32.GetSystemPowerStatus(ctypes.byref(sps)):
            return None
        return _decode_power_status(sps.ACLineStatus, sps.BatteryFlag,
                                    sps.BatteryLifePercent)
    except Exception:
        _log_once("battery", "battery_status failed", exc_info=True)
        return None


# ───────────────────────────── cfgmgr32 helpers ─────────────────────────────
# Config Manager device instances (DEVINST) are plain numbers, not handles, so
# nothing here needs to be closed.

def _cm_id_list(enumerator: str) -> list[str] | None:
    """Instance ids of the present devices of one enumerator.

    Returns [] when the enumerator has no devices and None on a real failure,
    so a transient error is never mistaken for "everything disconnected".
    """
    cfg = _cfgmgr()
    if cfg is None:
        return []
    flags = _CM_GETIDLIST_FILTER_ENUMERATOR | _CM_GETIDLIST_FILTER_PRESENT
    for _ in range(4):
        size = _U32(0)
        cr = cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), enumerator,
                                             flags)
        if cr in (_CR_NO_SUCH_VALUE, _CR_NO_SUCH_DEVNODE):
            return []
        if cr != _CR_SUCCESS:
            return None
        if size.value <= 1:
            return []
        length = size.value + 256           # slack: the list may grow
        buf = ctypes.create_unicode_buffer(length)
        cr = cfg.CM_Get_Device_ID_ListW(enumerator, buf, length, flags)
        if cr == _CR_BUFFER_SMALL:
            continue
        if cr in (_CR_NO_SUCH_VALUE, _CR_NO_SUCH_DEVNODE):
            return []
        if cr != _CR_SUCCESS:
            return None
        return [s for s in ctypes.wstring_at(buf, length).split("\0") if s]
    return None


def _cm_locate(instance_id: str) -> int | None:
    """DEVINST of a present device, None if it is gone."""
    cfg = _cfgmgr()
    if cfg is None:
        return None
    devinst = _U32(0)
    if cfg.CM_Locate_DevNodeW(ctypes.byref(devinst), instance_id, 0):
        return None
    return devinst.value


def _cm_prop(devinst: int, key: _DEVPROPKEY) -> tuple[int, bytes] | None:
    cfg = _cfgmgr()
    if cfg is None:
        return None
    size = _U32(512)
    buf = ctypes.create_string_buffer(size.value)
    kind = _U32(0)
    cr = cfg.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(key),
                                      ctypes.byref(kind), buf,
                                      ctypes.byref(size), 0)
    if cr == _CR_BUFFER_SMALL and size.value:
        buf = ctypes.create_string_buffer(size.value)
        cr = cfg.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(key),
                                          ctypes.byref(kind), buf,
                                          ctypes.byref(size), 0)
    if cr != _CR_SUCCESS:
        return None
    return kind.value, buf.raw[:size.value]


def _cm_prop_str(devinst: int, key: _DEVPROPKEY) -> str:
    prop = _cm_prop(devinst, key)
    if not prop or prop[0] != _DEVPROP_TYPE_STRING:
        return ""
    return prop[1].decode("utf-16-le", "replace").split("\0", 1)[0].strip()


def _cm_prop_list(devinst: int, key: _DEVPROPKEY) -> list[str]:
    prop = _cm_prop(devinst, key)
    if not prop or prop[0] != _DEVPROP_TYPE_STRING_LIST:
        return []
    return [s for s in prop[1].decode("utf-16-le", "replace").split("\0") if s]


def _cm_prop_int(devinst: int, key: _DEVPROPKEY) -> int | None:
    prop = _cm_prop(devinst, key)
    # 0x02..0x09 are the signed/unsigned 8..64 bit integer types.
    if not prop or not prop[1] or not (
            0x02 <= prop[0] <= 0x09 or prop[0] == _DEVPROP_TYPE_BOOLEAN):
        return None
    return int.from_bytes(prop[1][:8], "little")


def _cm_prop_bool(devinst: int, key: _DEVPROPKEY) -> bool | None:
    value = _cm_prop_int(devinst, key)
    return None if value is None else value != 0


def _cm_device_id(devinst: int) -> str:
    cfg = _cfgmgr()
    if cfg is None:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    if cfg.CM_Get_Device_IDW(devinst, buf, 512, 0) != _CR_SUCCESS:
        return ""
    return buf.value


def _cm_descendants(devinst: int, limit: int = 48) -> list[int]:
    """Present child device nodes, breadth first, bounded."""
    cfg = _cfgmgr()
    if cfg is None:
        return []
    found: list[int] = []
    pending = [devinst]
    while pending and len(found) < limit:
        parent = pending.pop(0)
        node = _U32(0)
        cr = cfg.CM_Get_Child(ctypes.byref(node), parent, 0)
        while cr == _CR_SUCCESS and len(found) < limit:
            found.append(node.value)
            pending.append(node.value)
            current = node.value
            node = _U32(0)
            cr = cfg.CM_Get_Sibling(ctypes.byref(node), current, 0)
    return found


def _cm_interface_paths(guid: _GUID, instance_id: str) -> list[str]:
    cfg = _cfgmgr()
    if cfg is None:
        return []
    size = _U32(0)
    if cfg.CM_Get_Device_Interface_List_SizeW(
            ctypes.byref(size), ctypes.byref(guid), instance_id,
            _CM_GET_DEVICE_INTERFACE_LIST_PRESENT) or size.value <= 1:
        return []
    length = size.value + 256
    buf = ctypes.create_unicode_buffer(length)
    if cfg.CM_Get_Device_Interface_ListW(
            ctypes.byref(guid), instance_id, buf, length,
            _CM_GET_DEVICE_INTERFACE_LIST_PRESENT):
        return []
    return [s for s in ctypes.wstring_at(buf, length).split("\0") if s]


# ───────────────────────────── Bluetooth devices ────────────────────────────

@dataclass(frozen=True)
class WirelessDevice:
    device_id: str        # stable id ("bt:" + address, e.g. "bt:AC80C1D2E3F4")
    name: str             # friendly name as Windows shows it
    kind: str             # "headphones" | "mouse" | "keyboard" | "gamepad" | "other"
    connected: bool
    percent: int | None   # battery, None if the device does not report one


@dataclass(frozen=True)
class DeviceEvent:
    kind: str             # "connected" | "disconnected" | "battery"
    device: WirelessDevice


class _BTH_LE_UUID_VALUE(ctypes.Union):
    _fields_ = [("ShortUuid", _U16), ("LongUuid", _GUID)]


class _BTH_LE_UUID(ctypes.Structure):
    _fields_ = [("IsShortUuid", _U8), ("Value", _BTH_LE_UUID_VALUE)]


class _BTH_LE_GATT_CHARACTERISTIC(ctypes.Structure):
    _fields_ = [("ServiceHandle", _U16),
                ("CharacteristicUuid", _BTH_LE_UUID),
                ("AttributeHandle", _U16),
                ("CharacteristicValueHandle", _U16),
                ("IsBroadcastable", _U8), ("IsReadable", _U8),
                ("IsWritable", _U8), ("IsWritableWithoutResponse", _U8),
                ("IsSignedWritable", _U8), ("IsNotifiable", _U8),
                ("IsIndicatable", _U8), ("HasExtendedProperties", _U8)]


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [(name, _U16) for name in (
        "wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
        "wSecond", "wMilliseconds")]


class _BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", _U32), ("Address", ctypes.c_uint64),
                ("ulClassofDevice", _U32), ("fConnected", ctypes.c_int),
                ("fRemembered", ctypes.c_int),
                ("fAuthenticated", ctypes.c_int),
                ("stLastSeen", _SYSTEMTIME), ("stLastUsed", _SYSTEMTIME),
                ("szName", ctypes.c_wchar * 248)]


class _BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", _U32), ("fReturnAuthenticated", ctypes.c_int),
                ("fReturnRemembered", ctypes.c_int),
                ("fReturnUnknown", ctypes.c_int),
                ("fReturnConnected", ctypes.c_int),
                ("fIssueInquiry", ctypes.c_int),
                ("cTimeoutMultiplier", _U8), ("hRadio", _PTR)]


_GENERIC_READ = 0x80000000
_FILE_SHARE_READ_WRITE = 0x3
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_BLUETOOTH_GATT_FLAG_FORCE_READ_FROM_DEVICE = 0x4
_HRESULT_MORE_DATA = 0x800700EA
_BATTERY_LEVEL_CHARACTERISTIC = 0x2A19
_BLUETOOTH_BASE_UUID_TAIL = "-0000-1000-8000-00805F9B34FB"

_RE_BT_DEVICE = re.compile(r"^(BTHENUM|BTHLE)\\DEV_([0-9A-F]{12})(?:\\|$)")
_RE_BT_SERVICE = re.compile(
    r"^(?:BTHENUM|BTHLEDEVICE)\\\{0000([0-9A-F]{4})-0000-1000-8000-"
    r"00805F9B34FB\}")
_GATT_BATTERY_SERVICE_PREFIX = (
    "BTHLEDEVICE\\{0000180F" + _BLUETOOTH_BASE_UUID_TAIL + "}")

_NAME_HINTS = (
    ("headphones", ("headphone", "headset", "earbud", "earphone", "buds",
                    "airpods", "pods", "wh-", "wf-", "tws", "наушник",
                    "гарнитур")),
    ("mouse", ("mouse", "mice", "trackball", "trackpad", "mx master",
               "mx anywhere", "mx vertical", "mx ergo", "мыш")),
    ("keyboard", ("keyboard", "keys", "keychron", "kbd", "клавиатур")),
    ("gamepad", ("gamepad", "controller", "xbox", "dualsense", "dualshock",
                 "joy-con", "joystick", "8bitdo", "геймпад", "джойстик")),
)


def _kind_from_name(name: str) -> str | None:
    lowered = name.lower()
    for kind, hints in _NAME_HINTS:
        if any(hint in lowered for hint in hints):
            return kind
    return None


def _classify(name: str, cod: int | None, appearance: int | None,
              usages: frozenset[str] | set[str],
              services: frozenset[str] | set[str]) -> str:
    """Device kind from, in order of trust: Bluetooth Class of Device, HID
    gamepad usage, LE appearance, the name, HID usages, service UUIDs."""
    hinted = _kind_from_name(name)
    if cod:
        major = (cod >> 8) & 0x1F
        if major == 0x05:                           # peripheral
            if (cod >> 2) & 0x0F in (1, 2):         # joystick, gamepad
                return "gamepad"
            pointing_keyboard = (cod >> 6) & 0x03
            if pointing_keyboard == 1:
                return "keyboard"
            if pointing_keyboard == 2:
                return "mouse"
            if pointing_keyboard == 3:              # combo device
                return hinted if hinted in ("mouse", "keyboard") else "keyboard"
        elif major == 0x04:                         # audio / video
            # headset, hands-free, headphones; speakers and the rest are not
            if (cod >> 2) & 0x3F in (1, 2, 6):
                return "headphones"
            return hinted or "other"
    if "gamepad" in usages:
        return "gamepad"
    if appearance:
        category, sub = appearance >> 6, appearance & 0x3F
        if category == 0x0F:                        # HID
            by_sub = {1: "keyboard", 2: "mouse", 3: "gamepad", 4: "gamepad"}
            if sub in by_sub:
                return by_sub[sub]
        elif category == 0x25 and sub in (1, 2, 3, 5):   # wearable audio
            return "headphones"
    if hinted:
        return hinted
    if "keyboard" in usages:        # keyboards often add a pointer collection
        return "keyboard"
    if "mouse" in usages:
        return "mouse"
    if not cod and services & {"110B", "111E", "1108"}:  # A2DP sink, HFP, HSP
        return "headphones"
    return "other"


def _hid_usages(devinst: int) -> set[str]:
    """What a HID child node is, from the ids the HID class driver assigns."""
    device_id = _cm_device_id(devinst).upper()
    if not device_id.startswith("HID\\"):
        return set()
    ids = " ".join(_cm_prop_list(devinst, _PK_HARDWARE_IDS)
                   + _cm_prop_list(devinst, _PK_COMPATIBLE_IDS)).upper()
    usages: set[str] = set()
    if ("HID_DEVICE_SYSTEM_GAME" in ids or "UP:0001_U:0004" in ids
            or "UP:0001_U:0005" in ids or "&IG_" in device_id):
        usages.add("gamepad")
    if "HID_DEVICE_SYSTEM_MOUSE" in ids or "UP:0001_U:0002" in ids:
        usages.add("mouse")
    if "HID_DEVICE_SYSTEM_KEYBOARD" in ids or "UP:0001_U:0006" in ids:
        usages.add("keyboard")
    return usages


def _registry_bt_hints(mac: str) -> tuple[int | None, int | None]:
    """(class of device, LE appearance) the Bluetooth stack stored at pairing."""
    try:
        import winreg
        path = ("SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters"
                "\\Devices\\" + mac.lower())
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            values: list[int | None] = []
            for value_name in ("COD", "LEAppearance"):
                try:
                    value, _kind = winreg.QueryValueEx(key, value_name)
                    values.append(value if isinstance(value, int) else None)
                except OSError:
                    values.append(None)
            return values[0], values[1]
    except Exception:
        return None, None


def _classic_device_table() -> dict[str, tuple[bool, int]]:
    """address -> (connected, class of device) for paired classic devices.

    Second opinion from the Win32 Bluetooth API, used only when the device
    node properties are missing.  No inquiry is issued, so it returns at once.
    """
    table: dict[str, tuple[bool, int]] = {}
    api = _bthapis()
    if api is None:
        return table
    params = _BLUETOOTH_DEVICE_SEARCH_PARAMS()
    params.dwSize = ctypes.sizeof(params)
    params.fReturnAuthenticated = 1
    params.fReturnRemembered = 1
    params.fReturnConnected = 1
    info = _BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    find = api.BluetoothFindFirstDevice(ctypes.byref(params),
                                        ctypes.byref(info))
    if not find:
        return table
    try:
        for _ in range(256):
            mac = "%012X" % (info.Address & 0xFFFFFFFFFFFF)
            table[mac] = (bool(info.fConnected), int(info.ulClassofDevice))
            info = _BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(info)
            if not api.BluetoothFindNextDevice(find, ctypes.byref(info)):
                break
    finally:
        api.BluetoothFindDeviceClose(find)
    return table


def _gatt_read_battery(service_instance_id: str) -> int | None:
    """Battery Level (0x2A19) of a connected LE device, read from the device.

    Opens the Battery Service (0x180F) device interface that Windows creates
    for a paired LE device.  Blocking, so it only ever runs on the GATT worker.
    """
    api, k32 = _bthapis(), _kernel32()
    if api is None or k32 is None:
        return None
    for path in _cm_interface_paths(_GUID_GATT_SERVICE_INTERFACE,
                                    service_instance_id):
        handle = k32.CreateFileW(path, _GENERIC_READ, _FILE_SHARE_READ_WRITE,
                                 None, _OPEN_EXISTING, 0, None)
        if not handle or handle == _INVALID_HANDLE:
            continue
        try:
            count = _U16(0)
            hr = api.BluetoothGATTGetCharacteristics(
                handle, None, 0, None, ctypes.byref(count), 0)
            if hr & 0xFFFFFFFF != _HRESULT_MORE_DATA or not count.value:
                continue
            chars = (_BTH_LE_GATT_CHARACTERISTIC * count.value)()
            if api.BluetoothGATTGetCharacteristics(
                    handle, None, count.value, chars, ctypes.byref(count),
                    0) != 0:
                continue
            for index in range(min(count.value, len(chars))):
                char = chars[index]
                uuid_ = char.CharacteristicUuid
                if uuid_.IsShortUuid:
                    short = uuid_.Value.ShortUuid
                else:   # 0000xxxx-0000-1000-8000-00805F9B34FB
                    short = uuid_.Value.LongUuid.Data1 & 0xFFFF
                if short != _BATTERY_LEVEL_CHARACTERISTIC:
                    continue
                value = ctypes.create_string_buffer(64)   # ULONG size + data
                if api.BluetoothGATTGetCharacteristicValue(
                        handle, ctypes.byref(char), len(value), value, None,
                        _BLUETOOTH_GATT_FLAG_FORCE_READ_FROM_DEVICE) != 0:
                    continue
                size = struct.unpack_from("<I", value.raw, 0)[0]
                level = value.raw[4]
                if size >= 1 and level <= 100:
                    return level
        finally:
            k32.CloseHandle(handle)
    return None


class _GattBatteryReader:
    """Runs blocking GATT battery reads off the monitor thread.

    A sleeping LE device can keep a read waiting for many seconds; connection
    polling must not wait for that, so reads go through this one worker.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._results: dict[str, int] = {}
        self._thread: threading.Thread | None = None

    def request(self, mac: str, service_instance_id: str) -> None:
        with self._lock:
            if mac in self._pending:
                return
            self._pending.add(mac)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._work, name="island-devices-gatt", daemon=True)
                self._thread.start()
        self._jobs.put((mac, service_instance_id))

    def take(self, mac: str) -> int | None:
        with self._lock:
            return self._results.pop(mac, None)

    def busy(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def stop(self) -> None:
        with self._lock:
            running = self._thread is not None
        if running:
            self._jobs.put(None)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            mac, service_instance_id = job
            level = None
            try:
                level = _gatt_read_battery(service_instance_id)
            except Exception:
                _log_once("gatt:" + mac, "GATT battery read failed for %s",
                          mac, exc_info=True)
            with self._lock:
                self._pending.discard(mac)
                if level is not None:
                    self._results[mac] = level


class _BtNode:
    """One paired Bluetooth device as seen by a connection scan."""
    __slots__ = ("mac", "name", "connected", "classic", "le", "ids")

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.name = ""
        self.connected = False
        self.classic = False
        self.le = False
        self.ids: list[str] = []    # device node + its service nodes


class _BtDetails:
    __slots__ = ("kind", "percent", "gatt_service_id")

    def __init__(self, kind: str, percent: int | None,
                 gatt_service_id: str | None) -> None:
        self.kind = kind
        self.percent = percent              # DEVPKEY_Bluetooth_Battery
        self.gatt_service_id = gatt_service_id


class _BluetoothScanner:
    """Reads paired Bluetooth devices from the PnP device tree."""

    def scan(self) -> dict[str, _BtNode] | None:
        """Cheap pass: who is paired and who is connected.  None on failure."""
        nodes: dict[str, _BtNode] = {}
        service_ids: list[str] = []
        unknown: list[_BtNode] = []
        for enumerator in ("BTHENUM", "BTHLE"):
            ids = _cm_id_list(enumerator)
            if ids is None:
                return None
            for instance_id in ids:
                match = _RE_BT_DEVICE.match(instance_id.upper())
                if not match:
                    service_ids.append(instance_id)
                    continue
                devinst = _cm_locate(instance_id)
                if devinst is None:
                    continue
                node = nodes.get(match.group(2))
                if node is None:
                    node = nodes[match.group(2)] = _BtNode(match.group(2))
                is_classic = match.group(1) == "BTHENUM"
                node.classic |= is_classic
                node.le |= not is_classic
                node.ids.append(instance_id)
                name = (_cm_prop_str(devinst, _PK_FRIENDLY_NAME)
                        or _cm_prop_str(devinst, _PK_NAME)
                        or _cm_prop_str(devinst, _PK_DEVICE_DESC))
                if name and (is_classic or not node.name):
                    node.name = name
                connected = _cm_prop_bool(devinst, _PK_IS_CONNECTED)
                if connected is None:
                    if is_classic and node not in unknown:
                        unknown.append(node)
                elif connected:
                    node.connected = True
        if unknown:
            table = _classic_device_table()
            for node in unknown:
                if table.get(node.mac, (False, 0))[0]:
                    node.connected = True
        for instance_id in service_ids:
            upper = instance_id.upper()
            for mac, node in nodes.items():
                if mac in upper:
                    node.ids.append(instance_id)
        return nodes

    def details(self, node: _BtNode) -> _BtDetails:
        """Slower pass for one device: battery property and what it is."""
        ids = list(node.ids)
        if node.le:
            ids += [i for i in (_cm_id_list("BTHLEDEVICE") or [])
                    if node.mac in i.upper()]
        percent: int | None = None
        cod: int | None = None
        gatt_service_id: str | None = None
        usages: set[str] = set()
        services: set[str] = set()
        seen: set[int] = set()
        for instance_id in ids:
            upper = instance_id.upper()
            service = _RE_BT_SERVICE.match(upper)
            if service:
                services.add(service.group(1))
            if upper.startswith(_GATT_BATTERY_SERVICE_PREFIX):
                gatt_service_id = instance_id
            devinst = _cm_locate(instance_id)
            if devinst is None:
                continue
            if cod is None and upper.startswith("BTHENUM\\DEV_"):
                cod = _cm_prop_int(devinst, _PK_BT_CLASS_OF_DEVICE)
            for inst in [devinst] + _cm_descendants(devinst):
                if inst in seen:
                    continue
                seen.add(inst)
                if percent is None:
                    level = _cm_prop_int(inst, _PK_BT_BATTERY)
                    if level is not None and 0 <= level <= 100:
                        percent = level
                if inst != devinst:
                    usages |= _hid_usages(inst)
        reg_cod, appearance = _registry_bt_hints(node.mac)
        if not cod:
            cod = reg_cod
        if not cod and node.classic:
            cod = _classic_device_table().get(node.mac, (False, 0))[1] or None
        kind = _classify(node.name, cod, appearance, usages, services)
        return _BtDetails(kind, percent, gatt_service_id)


class _Tracked:
    __slots__ = ("mac", "name", "kind", "connected", "percent", "stale",
                 "grace_until", "next_detail")

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.name = ""
        self.kind = "other"
        self.connected = False
        self.percent: int | None = None
        self.stale: int | None = None   # value left over from the last session
        self.grace_until = 0.0
        self.next_detail = 0.0

    def snapshot(self) -> WirelessDevice:
        return WirelessDevice(device_id="bt:" + self.mac,
                              name=self.name or "Bluetooth device",
                              kind=self.kind, connected=self.connected,
                              percent=self.percent)


class DeviceMonitor(threading.Thread):
    """Watches paired Bluetooth devices: connection state and battery.

    Connection state is polled every 2 s, battery about once a minute (and
    right after a device connects).  Call ``start()`` once.
    """

    _POLL_S = 2.0               # connection poll
    _BATTERY_S = 60.0           # battery / kind refresh
    _GRACE_S = 8.0              # how long a pre-connect value is distrusted
    _BASELINE_GATT_WAIT_S = 5.0

    def __init__(self) -> None:
        super().__init__(name="island-devices", daemon=True)
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._devices: tuple[WirelessDevice, ...] = ()
        self._events: deque[DeviceEvent] = deque(maxlen=256)
        self._scanner = _BluetoothScanner()
        self._gatt = _GattBatteryReader()
        self._tracked: dict[str, _Tracked] = {}
        self._baseline_done = False

    # ── public, GUI thread ──

    def devices(self) -> tuple[WirelessDevice, ...]:
        """Last known state of the paired Bluetooth devices."""
        with self._lock:
            return self._devices

    def poll_events(self) -> list[DeviceEvent]:
        """Drain the events queued since the previous call."""
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def stop(self) -> None:
        self._stop_evt.set()

    # ── worker ──

    def run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                try:
                    nodes = self._scanner.scan()
                    if nodes is not None:
                        self._step(nodes, time.monotonic())
                except Exception:
                    _log_once("bt-scan", "Bluetooth scan failed",
                              exc_info=True)
                self._stop_evt.wait(self._POLL_S)
        finally:
            self._gatt.stop()

    def _step(self, nodes: dict[str, _BtNode], now: float) -> None:
        baseline = not self._baseline_done
        events: list[DeviceEvent] = []
        for mac, node in nodes.items():
            tracked = self._tracked.get(mac)
            is_new = tracked is None
            if tracked is None:
                tracked = self._tracked[mac] = _Tracked(mac)
            was_connected = tracked.connected
            just_connected = node.connected and not was_connected
            if node.name:
                tracked.name = node.name
            tracked.connected = node.connected
            if not node.connected:
                tracked.percent = None
                tracked.grace_until = 0.0
                if was_connected and not baseline:
                    events.append(DeviceEvent("disconnected",
                                              tracked.snapshot()))
            if is_new or just_connected:
                tracked.next_detail = 0.0
            details = None
            if now >= tracked.next_detail:
                try:
                    details = self._scanner.details(node)
                except Exception:
                    _log_once("bt-details", "Bluetooth detail scan failed",
                              exc_info=True)
                tracked.next_detail = now + self._BATTERY_S
            if details is not None:
                tracked.kind = details.kind
            if just_connected and not baseline:
                # The battery property still holds what the device said in
                # its previous session; do not trust it until it changes or
                # the device had time to report.
                tracked.stale = details.percent if details else None
                tracked.grace_until = (
                    now + self._GRACE_S if tracked.stale is not None else 0.0)
                events.append(DeviceEvent("connected", tracked.snapshot()))
            if details is not None and node.connected:
                fresh = details.percent
                if fresh is None:
                    tracked.grace_until = 0.0
                    if details.gatt_service_id:
                        self._gatt.request(mac, details.gatt_service_id)
                elif now < tracked.grace_until:
                    if fresh == tracked.stale:
                        fresh = None
                        tracked.next_detail = now       # look again next poll
                    else:
                        tracked.grace_until = 0.0
                if fresh is not None and fresh != tracked.percent:
                    tracked.percent = fresh
                    if not baseline:
                        events.append(DeviceEvent("battery",
                                                  tracked.snapshot()))
        for mac in [m for m in self._tracked if m not in nodes]:
            tracked = self._tracked.pop(mac)    # unpaired or Bluetooth is off
            if tracked.connected and not baseline:
                tracked.connected = False
                tracked.percent = None
                events.append(DeviceEvent("disconnected", tracked.snapshot()))
        if baseline:
            # Let the first GATT reads finish so the baseline already has them.
            deadline = time.monotonic() + self._BASELINE_GATT_WAIT_S
            while (self._gatt.busy() and time.monotonic() < deadline
                   and not self._stop_evt.wait(0.1)):
                pass
        for mac, tracked in self._tracked.items():
            level = self._gatt.take(mac)
            if (level is not None and tracked.connected
                    and level != tracked.percent):
                tracked.percent = level
                if not baseline:
                    events.append(DeviceEvent("battery", tracked.snapshot()))
        snapshot = tuple(sorted(
            (t.snapshot() for t in self._tracked.values()),
            key=lambda d: (not d.connected, d.name.lower(), d.device_id)))
        with self._lock:
            self._devices = snapshot
            self._events.extend(events)
        self._baseline_done = True


# ──────────────────────────────────── GPU ───────────────────────────────────

@dataclass(frozen=True)
class GpuStatus:
    name: str             # e.g. "NVIDIA GeForce RTX 4070"; "" if unknown
    load: int | None      # percent 0..100
    temperature: int | None   # °C


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", _U32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128),
                ("VendorId", _U32), ("DeviceId", _U32), ("SubSysId", _U32),
                ("Revision", _U32),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", _U32)]


class _Adapter:
    __slots__ = ("name", "vendor", "device", "memory", "luid")

    def __init__(self, name: str, vendor: int, device: int, memory: int,
                 luid: tuple[int, int]) -> None:
        self.name = name
        self.vendor = vendor
        self.device = device
        self.memory = memory
        self.luid = luid                    # (high, low)


_VENDOR_NVIDIA = 0x10DE
_VENDOR_MICROSOFT = 0x1414
_DXGI_ADAPTER_FLAG_SOFTWARE = 2


def _com_method(obj: int, index: int, restype, *argtypes):
    vtable = ctypes.cast(obj, _P(_P(_PTR)))[0]
    return ctypes.WINFUNCTYPE(restype, _PTR, *argtypes)(vtable[index])


def _com_release(obj) -> None:
    if obj:
        _com_method(obj, 2, ctypes.c_ulong)(obj)


def _dxgi_adapters() -> list[_Adapter]:
    """Hardware display adapters with their LUIDs (DXGI needs no COM init)."""
    dxgi = _lib("dxgi.dll", _setup_dxgi)
    if dxgi is None:
        return []
    adapters: list[_Adapter] = []
    factory = _PTR()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1),
                               ctypes.byref(factory)) < 0 or not factory:
        return adapters
    try:
        # IDXGIFactory1::EnumAdapters1 is vtable slot 12
        enum_adapters = _com_method(factory.value, 12, ctypes.c_long, _U32,
                                    _P(_PTR))
        for index in range(16):
            adapter = _PTR()
            if enum_adapters(factory, index,
                             ctypes.byref(adapter)) < 0 or not adapter:
                break
            try:
                # IDXGIAdapter1::GetDesc1 is vtable slot 10
                desc = _DXGI_ADAPTER_DESC1()
                if _com_method(adapter.value, 10, ctypes.c_long,
                               _P(_DXGI_ADAPTER_DESC1))(
                                   adapter, ctypes.byref(desc)) < 0:
                    continue
                if (desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE
                        or desc.VendorId == _VENDOR_MICROSOFT):
                    continue
                adapters.append(_Adapter(
                    desc.Description.strip(), desc.VendorId, desc.DeviceId,
                    int(desc.DedicatedVideoMemory),
                    (desc.AdapterLuid.HighPart & 0xFFFFFFFF,
                     desc.AdapterLuid.LowPart)))
            finally:
                _com_release(adapter.value)
    finally:
        _com_release(factory.value)
    return adapters


_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
_PDH_ITEM = struct.Struct("<QI4xd")     # PDH_FMT_COUNTERVALUE_ITEM_W, x64
_RE_GPU_ENGINE = re.compile(
    r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)_phys_(\d+)_eng_(\d+)")


class _PdhGpuLoad:
    """GPU utilisation the way Task Manager computes it.

    ``\\GPU Engine(*)\\Utilization Percentage`` has one instance per process
    per engine; the load of an adapter is the busiest engine, where an engine
    is the sum over all processes.  PDH re-expands the wildcard on every
    collection, so processes coming and going need no handling here.
    """

    _COUNTER = "\\GPU Engine(*)\\Utilization Percentage"

    def __init__(self) -> None:
        self._query: int | None = None
        self._counter: int | None = None
        self._buf = None
        self._buf_size = 0
        self._failures = 0
        self._retry_at = 0.0
        self._keys: dict[str, tuple[int, int, int, int] | None] = {}

    @property
    def is_open(self) -> bool:
        return self._query is not None

    def ensure_open(self, now: float) -> bool:
        if self._query is not None:
            return True
        if now < self._retry_at:
            return False
        self._retry_at = now + 60.0
        pdh = _lib("pdh.dll", _setup_pdh)
        if pdh is None:
            return False
        query = _PTR()
        status = pdh.PdhOpenQueryW(None, None, ctypes.byref(query))
        if status or not query:
            _log_once("pdh-open", "PdhOpenQuery failed: 0x%08X", status)
            return False
        counter = _PTR()
        status = pdh.PdhAddEnglishCounterW(query, self._COUNTER, None,
                                           ctypes.byref(counter))
        if status or not counter:
            pdh.PdhCloseQuery(query)
            _log_once("pdh-add", "GPU Engine counter unavailable: 0x%08X",
                      status)
            return False
        self._query, self._counter = query.value, counter.value
        self._failures = 0
        return True

    def collect(self) -> bool:
        pdh = _lib("pdh.dll", _setup_pdh)
        if pdh is None or self._query is None:
            return False
        if pdh.PdhCollectQueryData(self._query):
            self._failed()
            return False
        return True

    def read(self, luid: tuple[int, int] | None,
             check_presence: bool) -> tuple[int | None, bool | None]:
        """(load percent, adapter has instances) from the last two collects.

        ``presence`` is only computed when asked for (it has to look at every
        instance name) and is None otherwise.
        """
        pdh = _lib("pdh.dll", _setup_pdh)
        if pdh is None or self._counter is None:
            return None, None
        count = _U32(0)
        status = 0
        for _ in range(3):
            size = _U32(self._buf_size)
            count = _U32(0)
            status = pdh.PdhGetFormattedCounterArrayW(
                self._counter, _PDH_FMT_DOUBLE, ctypes.byref(size),
                ctypes.byref(count), self._buf)
            if status != _PDH_MORE_DATA:
                break
            self._buf_size = size.value + 8192
            self._buf = ctypes.create_string_buffer(self._buf_size)
        if status:
            self._failed()
            return None, None
        self._failures = 0
        engines: dict[tuple[int, int, int, int], float] = {}
        present = False if check_presence else None
        if len(self._keys) > 20000:
            self._keys.clear()
        items = memoryview(self._buf)[:count.value * _PDH_ITEM.size]
        for name_ptr, cstatus, value in _PDH_ITEM.iter_unpack(items):
            # CStatus 0 / 1 = valid / new data
            if cstatus > 1 or not name_ptr:
                continue
            if value <= 0.0 and not check_presence:
                continue
            name = ctypes.wstring_at(name_ptr)
            key = self._keys.get(name, 0)
            if key == 0:
                match = _RE_GPU_ENGINE.search(name)
                key = self._keys[name] = match and (
                    int(match.group(1), 16), int(match.group(2), 16),
                    int(match.group(3)), int(match.group(4)))
            if not key or (luid is not None and key[:2] != luid):
                continue
            present = True if check_presence else None
            if value > 0.0:
                engines[key] = engines.get(key, 0.0) + value
        load = max(engines.values(), default=0.0)
        return max(0, min(100, int(round(load)))), present

    def _failed(self) -> None:
        self._failures += 1
        if self._failures >= 5:             # rebuild the query from scratch
            self.close()
            self._retry_at = time.monotonic() + 30.0

    def close(self) -> None:
        query, self._query, self._counter = self._query, None, None
        self._buf, self._buf_size = None, 0
        self._keys.clear()
        if query is not None:
            pdh = _lib("pdh.dll", _setup_pdh)
            if pdh is not None:
                pdh.PdhCloseQuery(query)


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlPciInfo(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16),
                ("domain", ctypes.c_uint), ("bus", ctypes.c_uint),
                ("device", ctypes.c_uint), ("pciDeviceId", ctypes.c_uint),
                ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


class _NvmlTemperature(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("sensorType", ctypes.c_int),
                ("temperature", ctypes.c_int)]


def _nvml_path() -> str | None:
    root = os.environ.get("SystemRoot") or "C:\\Windows"
    candidates = [os.path.join(root, "System32", "nvml.dll")]
    program_files = os.environ.get("ProgramW6432") or os.environ.get(
        "ProgramFiles")
    if program_files:
        candidates.append(os.path.join(
            program_files, "NVIDIA Corporation", "NVSMI", "nvml.dll"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class _Nvml:
    """NVIDIA Management Library: temperature (and load as a fallback)."""

    def __init__(self) -> None:
        self._lib = None
        self._handle: int | None = None
        self._ready = False
        self.name = ""

    def open(self, pci_device: int | None) -> bool:
        path = _nvml_path()
        if path is None:
            return False
        try:
            lib = ctypes.CDLL(path)
            lib.nvmlInit_v2.restype = ctypes.c_int
            lib.nvmlShutdown.restype = ctypes.c_int
            lib.nvmlDeviceGetCount_v2.argtypes = [_P(ctypes.c_uint)]
            lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,
                                                          _P(_PTR)]
            lib.nvmlDeviceGetName.argtypes = [_PTR, ctypes.c_char_p,
                                              ctypes.c_uint]
            lib.nvmlDeviceGetUtilizationRates.argtypes = [
                _PTR, _P(_NvmlUtilization)]
            for name in ("nvmlDeviceGetCount_v2",
                         "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetName",
                         "nvmlDeviceGetUtilizationRates"):
                getattr(lib, name).restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            _log_once("nvml-load", "nvml.dll is unusable: %r", exc)
            return False
        if lib.nvmlInit_v2() != 0:
            return False
        self._lib, self._ready = lib, True
        try:
            count = ctypes.c_uint(0)
            if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
                count.value = 0
            chosen = None
            for index in range(count.value):
                handle = _PTR()
                if lib.nvmlDeviceGetHandleByIndex_v2(
                        index, ctypes.byref(handle)) != 0 or not handle:
                    continue
                if chosen is None:
                    chosen = handle.value
                if pci_device is not None and self._pci_device(
                        handle.value) == pci_device:
                    chosen = handle.value
                    break
            if chosen is None:
                self.close()
                return False
            self._handle = chosen
            buf = ctypes.create_string_buffer(96)
            if lib.nvmlDeviceGetName(chosen, buf, len(buf)) == 0:
                self.name = buf.value.decode("utf-8", "replace").strip()
            return True
        except Exception:
            _log_once("nvml-open", "NVML setup failed", exc_info=True)
            self.close()
            return False

    def _pci_device(self, handle: int) -> int | None:
        try:
            fn = self._lib.nvmlDeviceGetPciInfo_v3
            fn.argtypes = [_PTR, _P(_NvmlPciInfo)]
            fn.restype = ctypes.c_int
            info = _NvmlPciInfo()
            if fn(handle, ctypes.byref(info)) != 0:
                return None
            return info.pciDeviceId >> 16       # low word is the vendor id
        except AttributeError:
            return None

    def temperature(self) -> int | None:
        """GPU core temperature in °C; None on error."""
        lib, handle = self._lib, self._handle
        if lib is None or handle is None:
            return None
        value = None
        try:
            fn = lib.nvmlDeviceGetTemperature
            fn.argtypes = [_PTR, ctypes.c_int, _P(ctypes.c_uint)]
            fn.restype = ctypes.c_int
            raw = ctypes.c_uint(0)
            if fn(handle, 0, ctypes.byref(raw)) == 0:   # NVML_TEMPERATURE_GPU
                value = raw.value
        except AttributeError:
            pass
        if value is None:
            try:                # the replacement API of newer drivers
                fn = lib.nvmlDeviceGetTemperatureV
                fn.argtypes = [_PTR, _P(_NvmlTemperature)]
                fn.restype = ctypes.c_int
                info = _NvmlTemperature()
                info.version = ctypes.sizeof(info) | (1 << 24)
                info.sensorType = 0
                if fn(handle, ctypes.byref(info)) == 0:
                    value = info.temperature
            except AttributeError:
                pass
        if value is None or not 0 < value < 150:
            return None
        return int(value)

    def utilization(self) -> int | None:
        lib, handle = self._lib, self._handle
        if lib is None or handle is None:
            return None
        rates = _NvmlUtilization()
        if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(rates)):
            return None
        return max(0, min(100, int(rates.gpu)))

    def close(self) -> None:
        lib, ready = self._lib, self._ready
        self._lib, self._handle, self._ready = None, None, False
        if lib is not None and ready:
            try:
                lib.nvmlShutdown()
            except Exception:
                pass


class _GpuSampler:
    """Everything the GPU thread owns: adapter choice, PDH query, NVML."""

    def __init__(self) -> None:
        self.name = ""
        self._adapter: _Adapter | None = None
        self._has_nvidia = False
        self._pdh = _PdhGpuLoad()
        self._nvml: _Nvml | None = None
        self._nvml_retry_at = 0.0
        self._nvml_errors = 0
        self._primed = False
        self._idle_ticks = 0
        self._absent = 0

    def identify(self) -> str:
        """Pick the adapter to report: the one with the most dedicated VRAM
        (the discrete GPU on a hybrid laptop)."""
        try:
            adapters = _dxgi_adapters()
        except Exception:
            _log_once("dxgi", "DXGI adapter enumeration failed",
                      exc_info=True)
            adapters = []
        self._has_nvidia = any(a.vendor == _VENDOR_NVIDIA for a in adapters)
        self._adapter = max(adapters, key=lambda a: a.memory, default=None)
        if self._adapter is not None:
            self.name = self._adapter.name
        return self.name

    def pause(self) -> None:
        self._primed = False    # the next sample must not span the pause

    def sample(self) -> tuple[int | None, int | None]:
        now = time.monotonic()
        adapter = self._adapter
        load: int | None = None
        if self._pdh.ensure_open(now):
            if not self._pdh.collect():
                self._primed = False
            elif not self._primed:
                self._primed = True     # a rate needs two collections
            else:
                check = adapter is not None and self._idle_ticks >= 10
                load, present = self._pdh.read(
                    adapter.luid if adapter else None, check)
                if check:
                    self._idle_ticks = 0
                    # The adapter LUID changes when its driver restarts.
                    self._absent = 0 if present else self._absent + 1
                    if self._absent >= 2:
                        self._absent = 0
                        self.identify()
                elif load:
                    self._idle_ticks = 0
                else:
                    self._idle_ticks += 1
        nvml = self._nvml_for(adapter, now)
        temperature = None
        if nvml is not None:
            temperature = nvml.temperature()
            if temperature is None:
                self._nvml_errors += 1
                if self._nvml_errors >= 5:      # driver restarted or GPU lost
                    self._drop_nvml(now)
                    nvml = None
            else:
                self._nvml_errors = 0
            if load is None and nvml is not None and not self._pdh.is_open:
                load = nvml.utilization()
        return load, temperature

    def _nvml_for(self, adapter: _Adapter | None, now: float) -> _Nvml | None:
        wanted = (adapter.vendor == _VENDOR_NVIDIA if adapter is not None
                  else True)
        if not wanted:
            return None
        if self._nvml is None and now >= self._nvml_retry_at:
            self._nvml_retry_at = now + 30.0
            nvml = _Nvml()
            if nvml.open(adapter.device if adapter else None):
                self._nvml = nvml
                self._nvml_errors = 0
                if not self.name:
                    self.name = nvml.name
        return self._nvml

    def _drop_nvml(self, now: float) -> None:
        if self._nvml is not None:
            self._nvml.close()
            self._nvml = None
        self._nvml_errors = 0
        self._nvml_retry_at = now + 30.0

    def close(self) -> None:
        self._pdh.close()
        if self._nvml is not None:
            self._nvml.close()
            self._nvml = None


class GpuMonitor(threading.Thread):
    """GPU name, load and temperature, sampled once a second while enabled.

    Call ``start()`` once.  While disabled the thread sleeps and ``status()``
    reports the name with ``load`` and ``temperature`` set to None.
    """

    _INTERVAL_S = 1.0

    def __init__(self) -> None:
        super().__init__(name="island-gpu", daemon=True)
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._wake = threading.Event()
        self._enabled = False
        self._status = GpuStatus(name="", load=None, temperature=None)

    # ── public, GUI thread ──

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
            if not enabled:
                self._status = GpuStatus(self._status.name, None, None)
        self._wake.set()

    def status(self) -> GpuStatus:
        with self._lock:
            return self._status

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    # ── worker ──

    def _publish(self, name: str, load: int | None,
                 temperature: int | None) -> None:
        with self._lock:
            if self._enabled:
                self._status = GpuStatus(name, load, temperature)
            else:
                self._status = GpuStatus(name, None, None)

    def run(self) -> None:
        sampler = _GpuSampler()
        try:
            try:
                self._publish(sampler.identify(), None, None)
            except Exception:
                _log_once("gpu-identify", "GPU identification failed",
                          exc_info=True)
            next_due = 0.0
            while not self._stop_evt.is_set():
                self._wake.clear()
                with self._lock:
                    enabled = self._enabled
                if not enabled:
                    sampler.pause()
                    next_due = 0.0
                    self._wake.wait()
                    continue
                remaining = next_due - time.monotonic()
                if remaining > 0:
                    self._wake.wait(remaining)
                    continue
                next_due = time.monotonic() + self._INTERVAL_S
                try:
                    load, temperature = sampler.sample()
                except Exception:
                    _log_once("gpu-sample", "GPU sampling failed",
                              exc_info=True)
                    load = temperature = None
                self._publish(sampler.name, load, temperature)
        finally:
            try:
                sampler.close()
            except Exception:
                _log_once("gpu-close", "GPU monitor cleanup failed",
                          exc_info=True)


# ─────────────────────────────────── demo ───────────────────────────────────

def _demo(seconds: float = 20.0) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    print("battery_status():", battery_status())
    devices = DeviceMonitor()
    gpu = GpuMonitor()
    devices.start()
    gpu.start()
    gpu.set_enabled(True)
    shown: tuple[WirelessDevice, ...] | None = None
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(1.0)
            current = devices.devices()
            if current != shown:
                shown = current
                print("wireless devices: %d" % len(current))
                for device in current:
                    print("   ", device)
            for event in devices.poll_events():
                print("event:", event.kind, event.device)
            print("gpu:", gpu.status(), "| battery:", battery_status())
    finally:
        devices.stop()
        gpu.stop()
        devices.join(5.0)
        gpu.join(5.0)


if __name__ == "__main__":
    _demo()
