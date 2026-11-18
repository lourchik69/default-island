"""System input helpers for Default Island.

Small, polling-friendly readers for things the island shows next to the clock:
the keyboard layout of the foreground window, Caps Lock, the microphone mute
switch, which apps are capturing from the microphone / camera, and the audio
output devices.

Everything here is safe to call from the GUI thread: no call raises, every
failure returns the documented "unknown" value and is logged once, and the
frequently polled calls cost microseconds (see the numbers next to each one,
measured on the development machine with ``timeit``).

No Qt and no subprocesses: only the standard library, ctypes, comtypes and the
Core Audio interface definitions that ship with pycaw.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "keyboard_layout",
    "caps_lock_on",
    "Microphone",
    "PrivacyUsage",
    "privacy_usage",
    "OutputDevice",
    "list_outputs",
    "set_default_output",
]

_log = logging.getLogger(__name__)
_logged: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    """Log a problem the first time it is seen; later repeats stay silent."""
    if key in _logged:
        return
    _logged.add(key)
    _log.warning(message, *args, exc_info=sys.exc_info()[0] is not None)


# --------------------------------------------------------------------------
# Win32 bindings. Private WinDLL instances, so the argtypes set here can never
# collide with what other modules set on the shared ``ctypes.windll`` objects.
# --------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_imm32 = ctypes.WinDLL("imm32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_GetForegroundWindow = _user32.GetForegroundWindow
_GetForegroundWindow.argtypes = []
_GetForegroundWindow.restype = wintypes.HWND

_GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
_GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_GetWindowThreadProcessId.restype = wintypes.DWORD

_GetKeyboardLayout = _user32.GetKeyboardLayout
_GetKeyboardLayout.argtypes = [wintypes.DWORD]
_GetKeyboardLayout.restype = ctypes.c_void_p  # HKL: pointer sized, None when 0

_GetGUIThreadInfo = _user32.GetGUIThreadInfo
_GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
_GetGUIThreadInfo.restype = wintypes.BOOL

_GetKeyState = _user32.GetKeyState
_GetKeyState.argtypes = [ctypes.c_int]
_GetKeyState.restype = ctypes.c_short

_ImmGetDefaultIMEWnd = _imm32.ImmGetDefaultIMEWnd
_ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
_ImmGetDefaultIMEWnd.restype = wintypes.HWND

_GetLocaleInfoW = _kernel32.GetLocaleInfoW
_GetLocaleInfoW.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int]
_GetLocaleInfoW.restype = ctypes.c_int

_CreateEventW = _kernel32.CreateEventW
_CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_CreateEventW.restype = wintypes.HANDLE

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_WaitForSingleObject.restype = wintypes.DWORD

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL

_CreateToolhelp32Snapshot = _kernel32.CreateToolhelp32Snapshot
_CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_CreateToolhelp32Snapshot.restype = wintypes.HANDLE

_Process32FirstW = _kernel32.Process32FirstW
_Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_Process32FirstW.restype = wintypes.BOOL

_Process32NextW = _kernel32.Process32NextW
_Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_Process32NextW.restype = wintypes.BOOL

_RegNotifyChangeKeyValue = _advapi32.RegNotifyChangeKeyValue
_RegNotifyChangeKeyValue.argtypes = [
    wintypes.HKEY, wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE, wintypes.BOOL,
]
_RegNotifyChangeKeyValue.restype = wintypes.LONG

_CoInitializeEx = _ole32.CoInitializeEx
_CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_CoInitializeEx.restype = ctypes.c_long  # HRESULT, read by hand: must not raise

_CoTaskMemFree = _ole32.CoTaskMemFree
_CoTaskMemFree.argtypes = [ctypes.c_void_p]
_CoTaskMemFree.restype = None

_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_WAIT_OBJECT_0 = 0


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------

_VK_CAPITAL = 0x14
_LOCALE_SISO639LANGNAME = 0x59
_language_codes: dict[int, str] = {}


def _language_code(hkl: int) -> str:
    """Language code ("EN", "RU", ...) of an HKL; its low word is the language id."""
    lang_id = hkl & 0xFFFF
    code = _language_codes.get(lang_id)
    if code is None:
        buffer = ctypes.create_unicode_buffer(16)
        if _GetLocaleInfoW(lang_id, _LOCALE_SISO639LANGNAME, buffer, len(buffer)):
            code = buffer.value[:2].upper()
        else:
            code = ""
        _language_codes[lang_id] = code
    return code


def _window_layout(hwnd: int) -> int:
    """The HKL used by the thread that receives ``hwnd``'s keyboard input, or 0."""
    thread_id = _GetWindowThreadProcessId(hwnd, None)
    if not thread_id:
        return 0
    # The focused control can live on another thread (UWP apps: the frame
    # belongs to ApplicationFrameHost, the content to the app itself), and the
    # layout is a per-thread setting, so follow the focus.
    info = _GUITHREADINFO()
    info.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if _GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        focus = info.hwndFocus
        if focus and focus != hwnd:
            thread_id = _GetWindowThreadProcessId(focus, None) or thread_id
    hkl = _GetKeyboardLayout(thread_id)
    if not hkl:
        # Classic console windows: Windows reports the console *client*
        # (cmd.exe, ...) as the owner, which has no input queue and therefore
        # no layout. The window really belongs to a conhost.exe thread, and
        # that thread's default IME window gives it away.
        ime_window = _ImmGetDefaultIMEWnd(hwnd)
        if ime_window:
            thread_id = _GetWindowThreadProcessId(ime_window, None)
            if thread_id:
                hkl = _GetKeyboardLayout(thread_id)
    return hkl or 0


def keyboard_layout() -> str:
    """Two-letter upper-case language of the foreground window's keyboard layout.

    "EN", "RU", "UK", "DE", ...; "" when unknown (no foreground window, e.g.
    while the secure desktop is up). About 2 microseconds per call.
    """
    try:
        hwnd = _GetForegroundWindow()
        if not hwnd:
            return ""
        hkl = _window_layout(hwnd)
        return _language_code(hkl) if hkl else ""
    except Exception:
        _log_once("keyboard_layout", "Could not read the keyboard layout")
        return ""


def caps_lock_on() -> bool:
    """True while Caps Lock is toggled on. Well under a microsecond per call.

    Reads the calling thread's key state, which Windows keeps up to date for
    threads that pump messages - call it from the GUI thread.
    """
    try:
        return bool(_GetKeyState(_VK_CAPITAL) & 1)
    except Exception:
        _log_once("caps_lock_on", "Could not read the Caps Lock state")
        return False


# --------------------------------------------------------------------------
# Core Audio plumbing (comtypes + the interface definitions pycaw ships)
# --------------------------------------------------------------------------

try:
    import comtypes
    from pycaw.api.endpointvolume import IAudioEndpointVolume
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY
    from pycaw.constants import CLSID_MMDeviceEnumerator
except Exception:  # pragma: no cover - a broken install must not stop the island
    comtypes = None
    _log_once("audio-import", "Core Audio bindings are unavailable; audio helpers are disabled")

if comtypes is not None:
    try:
        from pycaw.api.policyconfig import IPolicyConfig
        from pycaw.constants import CLSID_CPolicyConfigClient
    except Exception:  # pragma: no cover - pycaw releases older than 2024 lack it

        class IPolicyConfig(comtypes.IUnknown):  # type: ignore[no-redef]
            """Undocumented Windows 7+ interface; only SetDefaultEndpoint is used."""

            _iid_ = comtypes.GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
            _methods_ = tuple(
                comtypes.COMMETHOD([], ctypes.HRESULT, "Unused%d" % index)
                for index in range(1, 11)
            ) + (
                comtypes.COMMETHOD(
                    [], ctypes.HRESULT, "SetDefaultEndpoint",
                    (["in"], wintypes.LPCWSTR, "pwstrDeviceId"),
                    (["in"], wintypes.DWORD, "role"),
                ),
            )

        CLSID_CPolicyConfigClient = comtypes.GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")

    _PKEY_Device_FriendlyName = PROPERTYKEY(
        comtypes.GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14
    )

_E_RENDER, _E_CAPTURE = 0, 1                                # EDataFlow
_ROLE_CONSOLE, _ROLE_MULTIMEDIA, _ROLE_COMMUNICATIONS = 0, 1, 2  # ERole
_DEVICE_STATE_ACTIVE = 0x1
_STGM_READ = 0x0
_CLSCTX_ALL = 0x17
_E_NOTFOUND = -2147023728          # 0x80070490: no endpoint for that flow/role
_COINIT_MULTITHREADED, _COINIT_APARTMENTTHREADED = 0x0, 0x2

# IMMDevice::GetId through the raw vtable slot. The declared pycaw method hands
# back a Python str and leaks the CoTaskMem string behind it, which adds up in a
# widget that polls for days; this way the string can be freed.
_IMMDevice_GetId = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(5, "GetId")

_audio_lock = threading.RLock()
_com_threads = threading.local()
_enumerator_cache = None


def _com_ready() -> bool:
    """Make sure COM is usable on the calling thread (once per thread)."""
    if comtypes is None:
        return False
    if not getattr(_com_threads, "ready", False):
        # The GUI thread is already an STA (comtypes initialised it on import)
        # and this is then a no-op. Worker threads join the MTA: they have no
        # message loop, and the Core Audio objects are free-threaded anyway.
        main = threading.current_thread() is threading.main_thread()
        _CoInitializeEx(None, _COINIT_APARTMENTTHREADED if main else _COINIT_MULTITHREADED)
        # S_OK, S_FALSE and RPC_E_CHANGED_MODE all mean "COM is initialised".
        _com_threads.ready = True
    return True


def _enumerator():
    global _enumerator_cache
    if _enumerator_cache is None:
        _enumerator_cache = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
    return _enumerator_cache


def _drop_enumerator() -> None:
    global _enumerator_cache
    _enumerator_cache = None


def _device_id(device) -> str:
    raw = ctypes.c_void_p()
    _IMMDevice_GetId(device, ctypes.byref(raw))
    try:
        return ctypes.wstring_at(raw.value) if raw.value else ""
    finally:
        _CoTaskMemFree(raw)


def _default_endpoint(flow: int, roles: tuple[int, ...]):
    """The default IMMDevice for the first role that has one, or None."""
    enumerator = _enumerator()
    for role in roles:
        try:
            return enumerator.GetDefaultAudioEndpoint(flow, role)
        except comtypes.COMError as error:
            if error.hresult != _E_NOTFOUND:
                raise
    return None


# --------------------------------------------------------------------------
# Microphone
# --------------------------------------------------------------------------


class Microphone:
    """The default capture device (communications role first, then console)."""

    _RESOLVE_INTERVAL = 2.0  # seconds between checks for a new default device
    _FLOW = _E_CAPTURE
    _ROLES = (_ROLE_COMMUNICATIONS, _ROLE_CONSOLE)

    def __init__(self) -> None:
        self._volume = None          # IAudioEndpointVolume of the cached endpoint
        self._device_id = ""
        self._resolved_at = float("-inf")

    def _forget(self) -> None:
        self._volume = None
        self._device_id = ""
        self._resolved_at = float("-inf")

    def _endpoint(self, fresh: bool = False):
        """The mute/volume interface of the current default microphone, or None."""
        now = time.monotonic()
        if not fresh and now - self._resolved_at < self._RESOLVE_INTERVAL:
            return self._volume
        device = _default_endpoint(self._FLOW, self._ROLES)
        if device is None:
            self._volume = None
            self._device_id = ""
        else:
            device_id = _device_id(device)
            if self._volume is None or device_id != self._device_id:
                interface = device.Activate(IAudioEndpointVolume._iid_, _CLSCTX_ALL, None)
                self._volume = interface.QueryInterface(IAudioEndpointVolume)
                self._device_id = device_id
        self._resolved_at = now
        return self._volume

    def _run(self, action, fresh: bool):
        """Run ``action(volume)``; one retry with a re-resolved endpoint on error."""
        with _audio_lock:
            if not _com_ready():
                return None
            for attempt in (0, 1):
                try:
                    volume = self._endpoint(fresh or attempt == 1)
                    if volume is None:
                        return None
                    return action(volume)
                except Exception:
                    # Unplugged device, restarted audio service, ...: start over.
                    self._forget()
                    _drop_enumerator()
                    if attempt == 1:
                        _log_once("microphone", "Could not reach the default microphone")
            return None

    def state(self) -> bool | None:
        """True = muted, False = live, None = no microphone.

        About 1 microsecond per call; every two seconds one call also checks
        whether the default microphone changed (about 60 microseconds).
        """
        return self._run(lambda volume: bool(volume.GetMute()), fresh=False)

    def set_muted(self, muted: bool) -> bool:
        """Mute or unmute the default microphone. True on success."""
        def apply(volume) -> bool:
            volume.SetMute(bool(muted), None)
            return True

        return bool(self._run(apply, fresh=True))

    def toggle(self) -> bool | None:
        """Flip the mute switch. Returns the new state, None if no microphone."""
        def flip(volume) -> bool:
            muted = not volume.GetMute()
            volume.SetMute(muted, None)
            return muted

        return self._run(flip, fresh=True)


class Speakers(Microphone):
    """The default output device, read for the volume display."""

    _FLOW = _E_RENDER
    _ROLES = (_ROLE_CONSOLE, _ROLE_MULTIMEDIA)

    def level(self) -> tuple[int, bool] | None:
        """(volume 0-100, muted), or None without an output device. About a
        microsecond per call."""
        return self._run(
            lambda volume: (round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())),
            fresh=False,
        )


class BrightnessWatch:
    """Brightness of a built-in screen (laptops), read through WMI off the
    GUI thread. External monitors do not report it; then level() stays None
    and the thread ends."""

    def __init__(self) -> None:
        self._level = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="brightness").start()

    def level(self) -> int | None:
        return self._level

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            return
        pythoncom.CoInitialize()
        try:
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            while not self._stop.is_set():
                items = list(wmi.ExecQuery("SELECT CurrentBrightness FROM WmiMonitorBrightness"))
                if not items:
                    return  # no built-in screen: nothing to watch
                self._level = int(items[0].CurrentBrightness)
                self._stop.wait(0.6)
        except Exception as error:
            # The usual case on a desktop: the monitor has no brightness control.
            _log.info("Screen brightness is not available (%s)", type(error).__name__)
        finally:
            self._level = None
            pythoncom.CoUninitialize()


# --------------------------------------------------------------------------
# Screen capture (recording, screen sharing)
# --------------------------------------------------------------------------

_VIDEO_EXTENSIONS = (".mkv", ".mp4", ".mov", ".flv", ".ts", ".m4v", ".webm", ".avi")
_RECORDERS = ("obs64.exe", "obs32.exe", "obs.exe")


def _game_bar_folder() -> str:
    """Where Xbox Game Bar saves its clips."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\GameDVR") as key:
            folder = winreg.QueryValueEx(key, "AppCaptureFolder")[0]
            if folder:
                return os.path.expandvars(folder)
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "Videos", "Captures")


class RecordingWatch:
    """Is the screen being recorded? Checked off the GUI thread, every two
    seconds: an app capturing the screen through Windows (Snipping Tool video,
    screen sharing in Discord or a browser), OBS writing a video file, or Game
    Bar's newest clip still growing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._who = ""
        self._since = None
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._sizes: dict[str, int] = {}
        self._capture = [_ConsentWatch("graphicsCaptureProgrammatic"),
                         _ConsentWatch("graphicsCaptureWithoutBorder")]
        threading.Thread(target=self._run, daemon=True, name="recording").start()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()
            with self._lock:
                self._who, self._since = "", None

    def state(self) -> tuple[str, float] | None:
        """(who records, time.monotonic() it began) or None."""
        with self._lock:
            return (self._who, self._since) if self._who else None

    def stop(self) -> None:
        self._stop.set()
        self._enabled.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._enabled.wait()
            if self._stop.is_set():
                break
            try:
                who = self._look()
            except Exception:
                _log_once("recording", "Could not check for screen recording")
                who = ""
            with self._lock:
                if who and not self._who:
                    self._since = time.monotonic()
                self._who = who if self._enabled.is_set() else ""
                if not self._who:
                    self._since = None
            self._stop.wait(2.0)

    def _look(self) -> str:
        for watch in self._capture:
            with _privacy_lock:
                names = watch.names()
            if names:
                return names[0]
        try:
            import psutil
        except ImportError:
            psutil = None
        if psutil is not None:
            for process in psutil.process_iter(["name"]):
                if (process.info.get("name") or "").casefold() not in _RECORDERS:
                    continue
                try:
                    if any(item.path.casefold().endswith(_VIDEO_EXTENSIONS) for item in process.open_files()):
                        return "OBS"
                except (psutil.Error, OSError):
                    pass
        # Game Bar: the newest clip is still being written.
        folder = _game_bar_folder()
        newest = None
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.casefold().endswith(".mp4") and entry.is_file():
                        stat = entry.stat()
                        if newest is None or stat.st_mtime > newest[1]:
                            newest = (entry.path, stat.st_mtime, stat.st_size)
        except OSError:
            newest = None
        if newest is not None and time.time() - newest[1] < 5.0:
            # Grown since the last look; a clip that was just saved has not.
            before = self._sizes.get(newest[0])
            self._sizes = {newest[0]: newest[2]}
            if before is not None and newest[2] != before:
                return "Game Bar"
        return ""


# --------------------------------------------------------------------------
# Microphone / camera usage (the data behind the Windows tray indicator)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PrivacyUsage:
    microphone: tuple[str, ...]   # friendly names of apps using the microphone right now
    camera: tuple[str, ...]


_CONSENT_STORE = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
)
_REG_NOTIFY_CHANGE_NAME = 0x00000001
_REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_REG_NOTIFY_THREAD_AGNOSTIC = 0x10000000
_TH32CS_SNAPPROCESS = 0x00000002

_OWN_EXE = os.path.splitext(os.path.basename(sys.executable or ""))[0].casefold()
_PACKAGE_NAMES = {"immersivecontrolpanel": "Settings", "WindowsCamera": "Camera"}


def _running_exe_names() -> frozenset[str] | None:
    """Case-folded image names of all running processes (None if unreadable)."""
    snapshot = _CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        names = set()
        more = _Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names.add(entry.szExeFile.casefold())
            more = _Process32NextW(snapshot, ctypes.byref(entry))
        return frozenset(names)
    finally:
        _CloseHandle(snapshot)


def _subkey_names(key) -> list[str]:
    names = []
    try:
        index = 0
        while True:
            names.append(winreg.EnumKey(key, index))
            index += 1
    except OSError:
        pass
    return names


def _usage_start(parent, name: str) -> int:
    """LastUsedTimeStart of an entry that is in use right now, else 0."""
    try:
        with winreg.OpenKey(parent, name) as key:
            if winreg.QueryValueEx(key, "LastUsedTimeStop")[0] != 0:
                return 0
            return int(winreg.QueryValueEx(key, "LastUsedTimeStart")[0] or 0)
    except (OSError, TypeError, ValueError):
        return 0  # no timestamps: the app was granted access but never used it


class _ConsentWatch:
    """In-use apps of one ConsentStore capability, rescanned only on change.

    A registry change notification on the capability key says when something
    was written below it, so the usual call is a single WaitForSingleObject.
    """

    _SAFETY_RESCAN = 10.0  # seconds; also how often a missing key is looked for again

    def __init__(self, capability: str) -> None:
        self._path = _CONSENT_STORE + "\\" + capability
        self._key = None
        self._event = None
        self._armed = False
        self._names: tuple[str, ...] = ()
        self._scanned_at = float("-inf")
        self._alive: dict[str, tuple[int, bool]] = {}

    def _arm(self) -> None:
        if self._event is None:
            self._event = _CreateEventW(None, False, False, None)  # auto-reset
        self._armed = bool(self._event) and _RegNotifyChangeKeyValue(
            self._key.handle,
            True,
            _REG_NOTIFY_CHANGE_NAME | _REG_NOTIFY_CHANGE_LAST_SET | _REG_NOTIFY_THREAD_AGNOSTIC,
            self._event,
            True,
        ) == 0

    def _close(self) -> None:
        if self._key is not None:
            self._key.Close()
        self._key = None
        self._armed = False

    def names(self) -> tuple[str, ...]:
        now = time.monotonic()
        stale = now - self._scanned_at >= self._SAFETY_RESCAN
        if self._key is None:
            if not stale:
                return self._names
            try:
                self._key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._path)
            except OSError:
                # The capability was never used on this account: nothing to show.
                self._names, self._scanned_at = (), now
                return self._names
            self._arm()
            changed = True
        elif self._armed:
            changed = _WaitForSingleObject(self._event, 0) == _WAIT_OBJECT_0
            if changed:
                self._arm()  # one-shot: re-arm before reading so nothing is missed
        else:
            self._arm()     # notifications unavailable: keep trying, and
            changed = True  # read the registry on every call meanwhile
        if changed or stale:
            try:
                self._names = self._scan()
            except OSError:
                self._close()  # key deleted under us; reopen on a later call
                self._names = ()
            self._scanned_at = now
        return self._names

    def _scan(self) -> tuple[str, ...]:
        found: dict[str, None] = {}
        alive: dict[str, tuple[int, bool]] = {}
        running: list[frozenset[str] | None] = []

        def is_running(exe: str) -> bool:
            if not running:
                running.append(_running_exe_names())
            return running[0] is None or exe.casefold() in running[0]

        for name in _subkey_names(self._key):
            if name != "NonPackaged":
                if _usage_start(self._key, name):
                    short = name.split("_", 1)[0].rsplit(".", 1)[-1]
                    found[_PACKAGE_NAMES.get(short, short)] = None
                continue
            with winreg.OpenKey(self._key, name) as desktop:
                for entry in _subkey_names(desktop):
                    start = _usage_start(desktop, entry)
                    if not start:
                        continue
                    exe = entry.rsplit("#", 1)[-1]
                    # An entry left "in use" by a crash or power cut would stay
                    # that way for good, so each new usage session is checked
                    # once against the running processes.
                    known = self._alive.get(entry)
                    live = known[1] if known and known[0] == start else is_running(exe)
                    alive[entry] = (start, live)
                    if live:
                        found[os.path.splitext(exe)[0]] = None
        self._alive = alive
        return tuple(name for name in found if name.casefold() != _OWN_EXE)


_privacy_lock = threading.Lock()
_microphone_watch = _ConsentWatch("microphone")
_camera_watch = _ConsentWatch("webcam")
_no_usage = PrivacyUsage((), ())


def privacy_usage() -> PrivacyUsage:
    """Apps capturing from the microphone / camera right now.

    About 2 microseconds per call while nothing changes; a rescan after a
    change (or every ten seconds, as a safety net) costs well under a
    millisecond for a few dozen registered apps.
    """
    try:
        with _privacy_lock:
            return PrivacyUsage(_microphone_watch.names(), _camera_watch.names())
    except Exception:
        _log_once("privacy_usage", "Could not read microphone / camera usage")
        return _no_usage


# --------------------------------------------------------------------------
# Output devices
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputDevice:
    device_id: str
    name: str            # friendly name, e.g. "Наушники (WH-1000XM4)"
    is_default: bool


def _friendly_name(device) -> str:
    store = device.OpenPropertyStore(_STGM_READ)
    value = store.GetValue(_PKEY_Device_FriendlyName)
    try:
        name = value.GetValue()
        if not isinstance(name, str):
            return ""
        # Drivers often pad names: "Динамики (2- USB  AUDIO  )".
        name = " ".join(name.split())
        return name.replace("( ", "(").replace(" )", ")")
    finally:
        value.clear()


def _active_outputs() -> tuple[OutputDevice, ...]:
    default = _default_endpoint(_E_RENDER, (_ROLE_MULTIMEDIA, _ROLE_CONSOLE))
    default_id = _device_id(default) if default is not None else ""
    collection = _enumerator().EnumAudioEndpoints(_E_RENDER, _DEVICE_STATE_ACTIVE)
    devices = []
    for index in range(collection.GetCount()):
        device = collection.Item(index)
        device_id = _device_id(device)
        try:
            name = _friendly_name(device)
        except Exception:
            name = ""
        devices.append(OutputDevice(device_id, name or device_id, device_id == default_id))
    devices.sort(key=lambda d: (not d.is_default, d.name.casefold(), d.device_id))
    return tuple(devices)


def list_outputs() -> tuple[OutputDevice, ...]:
    """Active playback devices: the default one first, the rest by name.

    Empty when there is no output device or it could not be read. A few
    hundred microseconds per call - meant for opening a menu, not for a timer.
    """
    with _audio_lock:
        try:
            if not _com_ready():
                return ()
            return _active_outputs()
        except Exception:
            _drop_enumerator()
            _log_once("list_outputs", "Could not list the audio output devices")
            return ()


def set_default_output(device_id: str) -> bool:
    """Make ``device_id`` the default output for console, multimedia and
    communications. True only when all three roles were switched."""
    with _audio_lock:
        try:
            if not device_id or not isinstance(device_id, str) or not _com_ready():
                return False
            if not any(device.device_id == device_id for device in _active_outputs()):
                _log_once("output-unknown", "Not an active output device: %r", device_id)
                return False
            policy = comtypes.CoCreateInstance(
                CLSID_CPolicyConfigClient, IPolicyConfig, _CLSCTX_ALL
            )
            for role in (_ROLE_CONSOLE, _ROLE_MULTIMEDIA, _ROLE_COMMUNICATIONS):
                policy.SetDefaultEndpoint(device_id, role)
            return True
        except Exception:
            _drop_enumerator()
            _log_once("set_default_output", "Could not change the default output device")
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.stdout is not None:
        sys.stdout.reconfigure(errors="replace")
    demo_microphone = Microphone()
    for _ in range(3):
        print("layout          :", repr(keyboard_layout()))
        print("caps lock       :", caps_lock_on())
        print("microphone muted:", demo_microphone.state())
        print("privacy         :", privacy_usage())
        for output in list_outputs():
            print("output          :", "*" if output.is_default else " ", output.name, output.device_id)
        print()
        time.sleep(1.0)
