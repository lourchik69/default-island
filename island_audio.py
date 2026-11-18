"""Real-time spectrum levels for the island's music visualizer.

``SpectrumAnalyzer`` is a daemon thread that captures whatever the PC is
playing (WASAPI loopback of the default render endpoint, shared mode, in the
endpoint's mix format) and reduces it to a handful of 0..1 bar heights.

Only the standard library, ``comtypes`` and the COM interface definitions that
ship with ``pycaw`` are used.  There is no Qt in here: the GUI thread just
calls ``set_enabled()`` and ``levels()``.

The DSP is a small constant-Q filter bank.  Every bar is fed by two windowed
complex kernels; a kernel is correlated with the newest audio through
``math.sumprod`` (a C-level dot product), so no Python code runs per sample.
Bass kernels work on a 4x decimated copy of the signal to keep them short.
"""

from __future__ import annotations

import ctypes
import logging
import math
import operator
import struct
import sys
import threading
import time
from array import array

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from pycaw.api.audioclient import IAudioClient
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    _IMPORT_ERROR = None
except Exception as _exc:  # not Windows, or comtypes/pycaw missing
    comtypes = None
    _IMPORT_ERROR = _exc

__all__ = ["SpectrumAnalyzer"]

log = logging.getLogger(__name__)

try:
    _sumprod = math.sumprod  # Python 3.12+
except AttributeError:  # pragma: no cover - older interpreters
    def _sumprod(p, q):
        return sum(map(operator.mul, p, q))


# ----------------------------------------------------------------- tuning --

_F_LOW = 60.0            # bottom edge of the first bar, Hz
_F_HIGH = 12000.0        # top edge of the last bar, Hz
_MIN_KERNELS = 12        # the range is covered by at least this many kernels
_MAX_KERNEL_SECONDS = 0.05  # bass kernels are shortened to stay this responsive
_SPAN = 0.025            # seconds of audio every analysis looks back over
_MAX_POSITIONS = 8       # time positions a short kernel is evaluated at
_WORK_RATE = 24000.0     # preferred rate of the decimated mono signal

_POLL = 0.022            # capture/analysis period -> about 40-45 updates/s
_SILENCE_AFTER = 0.06    # no audio for this long counts as silence
_RELEASE_DELAY = 0.4     # keep the audio client this long after a disable
_RETRY = 1.5             # delay between attempts to open the device
_RETRY_QUICK = 0.3       # first retry after a stream that had been healthy
_HEALTHY_AFTER = 3.0     # a stream this old clears the failure history
_FAILS_UNAVAILABLE = 4   # consecutive failures before available() is False
_DEVICE_CHECK = 2.0      # how often the default endpoint is re-checked
_BUFFER_HNS = 5_000_000  # 0.5 s capture buffer, in 100 ns units
_MAX_PACKETS = 128       # packets drained per poll, at most

_TILT_DB_PER_OCTAVE = 2.5  # treble lift that evens out typical music
_RANGE_DB = 24.0         # a bar spans this many dB below its reference
_SPREAD_DB = 8.0         # a bar's own reference may sit this far below the loudest
_GAIN_FLOOR_DB = -66.0   # the gain reference never drops below this (dBFS)
_AGC_ATTACK = 0.03       # s, how fast the references follow louder audio
_AGC_DECAY_DB = 5.0      # dB/s, how fast they creep back down
_GAMMA = 1.4             # expands the upper part of the range a little
_ATTACK = 0.015          # s, bar rise time constant
_DECAY = 0.13            # s, bar fall time constant ...
_DECAY_LINEAR = 0.5      # ... plus this much per second, so bars reach zero
_SNAP = 0.004            # falling below this lands on exactly 0.0

_E_RENDER = 0
_E_CONSOLE = 0
_SHAREMODE_SHARED = 0
_STREAMFLAGS_LOOPBACK = 0x00020000
_BUFFERFLAGS_SILENT = 0x2
_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_SUBTYPE_TAIL = bytes.fromhex("000010008000 00aa00389b71".replace(" ", ""))

_E_NOTFOUND = 0x80070490
_AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
_AUDCLNT_E_DEVICE_IN_USE = 0x8889000A
_AUDCLNT_E_UNSUPPORTED_FORMAT = 0x88890008
_AUDCLNT_E_SERVICE_NOT_RUNNING = 0x88890010
_QUIET_HRESULTS = frozenset((
    _E_NOTFOUND,
    _AUDCLNT_E_DEVICE_INVALIDATED,
    _AUDCLNT_E_DEVICE_IN_USE,
    _AUDCLNT_E_SERVICE_NOT_RUNNING,
))


# -------------------------------------------------------------------- COM --

if _IMPORT_ERROR is None:
    from ctypes import POINTER, c_void_p
    from ctypes.wintypes import DWORD

    _UINT32 = ctypes.c_uint32

    class _IAudioCaptureClient(IUnknown):
        """IAudioCaptureClient (pycaw does not define it)."""

        _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
        _methods_ = (
            COMMETHOD(
                [], HRESULT, "GetBuffer",
                (["out"], POINTER(c_void_p), "ppData"),
                (["out"], POINTER(_UINT32), "pNumFramesToRead"),
                (["out"], POINTER(DWORD), "pdwFlags"),
                (["in"], c_void_p, "pu64DevicePosition"),
                (["in"], c_void_p, "pu64QPCPosition"),
            ),
            COMMETHOD(
                [], HRESULT, "ReleaseBuffer",
                (["in"], _UINT32, "NumFramesRead"),
            ),
            COMMETHOD(
                [], HRESULT, "GetNextPacketSize",
                (["out"], POINTER(_UINT32), "pNumFramesInNextPacket"),
            ),
        )

    class _IMMDeviceId(IUnknown):
        """IMMDevice with GetId() returning the raw pointer.

        pycaw's definition converts the string and leaks the COM allocation,
        which adds up when the default endpoint is polled for days.
        """

        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = (
            COMMETHOD(
                [], HRESULT, "Activate",
                (["in"], POINTER(GUID), "iid"),
                (["in"], DWORD, "dwClsCtx"),
                (["in"], c_void_p, "pActivationParams"),
                (["out"], POINTER(POINTER(IUnknown)), "ppInterface"),
            ),
            COMMETHOD(
                [], HRESULT, "OpenPropertyStore",
                (["in"], DWORD, "stgmAccess"),
                (["out"], POINTER(POINTER(IUnknown)), "ppProperties"),
            ),
            COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p), "ppstrId")),
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdwState")),
        )

    _CoTaskMemFree = ctypes.windll.ole32.CoTaskMemFree
    _CoTaskMemFree.argtypes = (c_void_p,)
    _CoTaskMemFree.restype = None


def _device_id(device) -> str:
    """Endpoint id string of an IMMDevice, without leaking it."""
    address = device.QueryInterface(_IMMDeviceId).GetId()
    if not address:
        return ""
    try:
        return ctypes.wstring_at(address)
    finally:
        _CoTaskMemFree(address)


def _parse_format(address: int):
    """Read a WAVEFORMATEX(TENSIBLE) -> (rate, channels, block, typecode, fullscale, packed24)."""
    head = ctypes.string_at(address, 18)
    tag, channels, rate, _avg, block, bits, extra = struct.unpack("<HHIIHHH", head)
    if tag == _WAVE_FORMAT_EXTENSIBLE and extra >= 22:
        sub = ctypes.string_at(address + 24, 16)
        if sub[4:] != _SUBTYPE_TAIL:
            raise ValueError("unsupported mix sub-format %s" % sub.hex())
        tag = int.from_bytes(sub[:4], "little")
    if channels < 1 or rate < 4000 or block != channels * (bits // 8):
        raise ValueError(
            "unsupported mix format: %d ch, %d Hz, %d bit, block %d"
            % (channels, rate, bits, block)
        )
    if tag == _WAVE_FORMAT_IEEE_FLOAT and bits == 32:
        kind = ("f", 1.0, False)
    elif tag == _WAVE_FORMAT_IEEE_FLOAT and bits == 64:
        kind = ("d", 1.0, False)
    elif tag == _WAVE_FORMAT_PCM and bits == 16:
        kind = ("h", 32768.0, False)
    elif tag == _WAVE_FORMAT_PCM and bits == 24:
        kind = ("h", 32768.0, True)  # reduced to its top 16 bits
    elif tag == _WAVE_FORMAT_PCM and bits == 32:
        kind = ("i", 2147483648.0, False)  # also 24-in-32, left justified
    else:
        raise ValueError("unsupported mix format: tag %#x, %d bit" % (tag, bits))
    return (rate, channels, block) + kind


# -------------------------------------------------------------------- DSP --

class _FilterBank:
    """Turns raw interleaved audio into one power value per bar.

    ``feed()`` downmixes and decimates new audio into two history buffers (a
    ~24 kHz one and a 4x slower one); ``analyze()`` correlates the newest part
    of them with the precomputed kernels.  1.0 is the power of a full-scale
    sine at a kernel's centre frequency.
    """

    def __init__(self, bands, rate, channels, block, typecode, fullscale, packed24):
        self.bands = bands
        self._typecode = typecode
        self._packed24 = packed24
        if array(typecode).itemsize * (3 if packed24 else 2) // 2 != block // channels:
            raise ValueError("no array type matches %d-byte samples" % (block // channels))

        decim = max(1, int(round(rate / _WORK_RATE)))
        hi_rate = rate / decim
        lo_rate = hi_rate / 4.0
        self._group = block * decim            # bytes per decimated sample
        self._stride = channels * decim
        # One sample of the left channel plus one of the right channel half a
        # step later: downmix and a (modest) anti-alias average in one add.
        if channels >= 2:
            self._taps = (0, (decim // 2) * channels + 1)
        elif decim >= 2:
            self._taps = (0, decim // 2)
        else:
            self._taps = (0,)
        hi_gain = 1.0 / (len(self._taps) * fullscale)
        lo_gain = hi_gain / 8.0                # the 4x decimator's taps sum to 8

        f_high = min(_F_HIGH, 0.45 * hi_rate)
        f_low = min(_F_LOW, f_high / 8.0)
        per_bar = max(1, -(-_MIN_KERNELS // bands))
        subs = bands * per_bar
        ratio = (f_high / f_low) ** (1.0 / subs)
        # Kernel length in whole periods: neighbours cross about one DFT bin
        # away from their centres.  Whole periods also put a null of the Hann
        # window on DC, which keeps loud bass out of the treble kernels.
        cycles_wanted = min(12, max(3, int(round(1.25 / (math.sqrt(ratio) - 1.0)))))

        self._hi = hi = []
        self._lo = lo = []
        kernels = []
        need_hi = need_lo = 8
        for j in range(subs):
            freq = f_low * ratio ** (j + 0.5)
            use_lo = freq <= hi_rate / 32.0
            krate = lo_rate if use_lo else hi_rate
            cycles = max(3, min(cycles_wanted, int(_MAX_KERNEL_SECONDS * freq)))
            length = max(cycles + 3, int(round(cycles * krate / freq)))
            window = [0.5 - 0.5 * math.cos(2.0 * math.pi * (n + 0.5) / length)
                      for n in range(length)]
            norm = 2.0 * (lo_gain if use_lo else hi_gain) / sum(window)
            omega = 2.0 * math.pi * cycles / length
            mid = (length - 1) / 2.0
            kcos = [norm * w * math.cos(omega * (n - mid)) for n, w in enumerate(window)]
            ksin = [norm * w * math.sin(omega * (n - mid)) for n, w in enumerate(window)]
            span = max(length, int(_SPAN * krate))
            count = min(_MAX_POSITIONS, max(1, int(round(span / length))))
            step = (span - length) / (count - 1) if count > 1 else 0.0
            slices = []
            for i in range(count):
                off = int(round(i * step))
                slices.append(slice(-length - off, -off if off else None))
            if use_lo:
                need_lo = max(need_lo, span)
            else:
                need_hi = max(need_hi, span)
            kernels.append((j // per_bar, lo if use_lo else hi, kcos, ksin,
                            tuple(slices), 1.0 / count))
        self._kernels = tuple(kernels)
        self._keep_hi = need_hi + 8
        self._keep_lo = need_lo + 8

        centres = [f_low * ratio ** (per_bar * (k + 0.5)) for k in range(bands)]
        self.tilt = [_TILT_DB_PER_OCTAVE * math.log2(c / centres[-1]) for c in centres]
        self._carry = b""
        self._lo_next = 0
        self.reset()

    def reset(self) -> None:
        """Forget all buffered audio (used across gaps in the stream)."""
        self._hi[:] = [0.0] * self._keep_hi
        self._lo[:] = [0.0] * self._keep_lo
        self._lo_next = self._keep_hi
        self._carry = b""

    def feed(self, raw: bytes) -> None:
        if self._carry:
            raw = self._carry + raw
        group = self._group
        used = len(raw) - len(raw) % group
        self._carry = raw[used:] if used < len(raw) else b""
        if not used:
            return
        if self._packed24:
            packed = bytearray(used // 3 * 2)
            packed[0::2] = raw[1:used:3]
            packed[1::2] = raw[2:used:3]
            samples = array("h", bytes(packed))
        else:
            samples = array(self._typecode)
            samples.frombytes(memoryview(raw)[:used])
        stride = self._stride
        taps = self._taps
        hi = self._hi
        if len(taps) == 2:
            hi.extend(map(operator.add, samples[taps[0]::stride], samples[taps[1]::stride]))
        else:
            hi.extend(samples[taps[0]::stride])

        # 4x decimation for the bass kernels, taps [1 1 2 2 1 1]: zeros at the
        # frequencies that would otherwise fold onto the bass.
        base = self._lo_next
        count = (len(hi) - base) // 4
        if count > 0:
            end = base + 4 * count
            self._lo.extend([
                a + b + 2.0 * (c + d) + e + f
                for a, b, c, d, e, f in zip(
                    hi[base - 2:end - 2:4], hi[base - 1:end - 1:4],
                    hi[base:end:4], hi[base + 1:end + 1:4],
                    hi[base + 2:end + 2:4], hi[base + 3:end + 3:4],
                )
            ])
            self._lo_next = end

        extra = len(hi) - self._keep_hi
        if extra > 4096:
            del hi[:extra]
            self._lo_next -= extra
        extra = len(self._lo) - self._keep_lo
        if extra > 1024:
            del self._lo[:extra]

    def analyze(self) -> list:
        out = [0.0] * self.bands
        sumprod = _sumprod
        for bar, buf, kcos, ksin, slices, weight in self._kernels:
            acc = 0.0
            for part in slices:
                seg = buf[part]
                re = sumprod(kcos, seg)
                im = sumprod(ksin, seg)
                acc += re * re + im * im
            out[bar] += acc * weight
        return out


class _Dynamics:
    """Band powers -> bar heights: tilt, slow automatic gain, attack/decay."""

    def __init__(self, bands: int):
        self._zero = (0.0,) * bands
        self._tilt = [0.0] * bands
        self._gain = _GAIN_FLOOR_DB
        self._refs = [_GAIN_FLOOR_DB] * bands
        self._levels = [0.0] * bands
        self._flat = True

    def set_tilt(self, tilt) -> None:
        self._tilt = list(tilt)

    def clear(self) -> None:
        """Drop the bars to zero at once; the learned gain is kept."""
        self._levels = [0.0] * len(self._zero)
        self._flat = True

    def update(self, powers, dt: float) -> tuple:
        """Advance by ``dt`` seconds; ``powers`` is None for silence."""
        levels = self._levels
        targets = None
        if powers is not None:
            log10 = math.log10
            db = [10.0 * log10(p) + t if 1e-13 < p < 1e6 else -200.0
                  for p, t in zip(powers, self._tilt)]
            peak = max(db)
            if peak > -150.0:
                follow = 1.0 - math.exp(-dt / _AGC_ATTACK)
                creep = _AGC_DECAY_DB * dt
                gain = self._gain
                if peak > gain:
                    gain += (peak - gain) * follow
                else:
                    gain = max(gain - creep, peak, _GAIN_FLOOR_DB)
                self._gain = gain
                lowest = gain - _SPREAD_DB
                refs = self._refs
                targets = []
                for i, value in enumerate(db):
                    ref = refs[i]
                    if value > ref:
                        ref += (value - ref) * follow
                    else:
                        ref -= creep
                    ref = gain if ref > gain else lowest if ref < lowest else ref
                    refs[i] = ref
                    x = (value - ref) / _RANGE_DB + 1.0
                    targets.append(0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x ** _GAMMA)
        if targets is None:
            if self._flat:
                return self._zero
            targets = self._zero

        rise = 1.0 - math.exp(-dt / _ATTACK)
        fall = math.exp(-dt / _DECAY)
        drop = _DECAY_LINEAR * dt
        flat = True
        for i, target in enumerate(targets):
            level = levels[i]
            if target > level:
                level += (target - level) * rise
            else:
                level = max(target, level * fall - drop)
            if level < _SNAP:
                level = 0.0
            else:
                flat = False
            levels[i] = level
        self._flat = flat
        return self._zero if flat else tuple(levels)


# ----------------------------------------------------------------- thread --

class SpectrumAnalyzer(threading.Thread):
    """Daemon thread that turns the PC's audio output into visualizer bars.

    Usage::

        analyzer = SpectrumAnalyzer(bands=6)
        analyzer.start()                 # once
        analyzer.set_enabled(True)       # any time, from any thread
        heights = analyzer.levels()      # tuple of 6 floats, 0.0..1.0
        analyzer.stop()                  # on shutdown

    Capture only runs while enabled; the audio client is released about
    0.4 s after ``set_enabled(False)``.  All COM work happens on this thread.
    """

    def __init__(self, bands: int = 6):
        super().__init__(name="SpectrumAnalyzer", daemon=True)
        self._bands = max(1, int(bands))
        self._zero = (0.0,) * self._bands
        self._levels = self._zero
        self._on = False
        self._quit = False
        self._wake = threading.Event()
        self._ok = _IMPORT_ERROR is None and sys.platform == "win32"
        if not self._ok:
            log.warning("Audio spectrum capture is unavailable: %s",
                        _IMPORT_ERROR or "not running on Windows")

        # Everything below belongs to the worker thread.
        self._dynamics = _Dynamics(self._bands)
        self._enumerator = None
        self._client = None
        self._capture = None
        self._device_key = None
        self._frame_bytes = 0
        self._bank = None
        self._fail_key = None

    # ---- public API (any thread) ----

    def set_enabled(self, enabled: bool) -> None:
        """Start or stop capturing.  Cheap; fine to call every frame."""
        enabled = bool(enabled)
        if enabled != self._on:
            self._on = enabled
            self._wake.set()

    def levels(self) -> tuple[float, ...]:
        """Bar heights, bass first, each 0.0..1.0.  Never blocks."""
        return self._levels if self._on else self._zero

    def available(self) -> bool:
        """False when capture cannot work here.

        That is: the platform or the COM libraries are missing, or opening
        the loopback stream keeps failing (no output device, device held in
        exclusive mode, unsupported format...).  The thread keeps retrying in
        the latter case and this turns True again once capture works.
        """
        return self._ok

    def stop(self) -> None:
        """End the thread; returns once it has exited (waits 0.5 s at most)."""
        self._quit = True
        self._on = False
        self._wake.set()
        if self.ident is not None and self is not threading.current_thread():
            self.join(0.5)

    # ---- worker thread ----

    def run(self) -> None:
        if _IMPORT_ERROR is not None or sys.platform != "win32":
            return
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            log.warning("Audio spectrum: COM initialisation failed", exc_info=True)
            self._ok = False
            return
        try:
            self._loop()
        except Exception:
            log.warning("Audio spectrum thread stopped unexpectedly", exc_info=True)
            self._ok = False
        finally:
            self._levels = self._zero
            self._close()
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _loop(self) -> None:
        monotonic = time.monotonic
        dynamics = self._dynamics
        zero = self._zero
        fails = 0
        retry_at = 0.0
        off_since = None
        opened_at = last_audio = last_update = next_check = 0.0
        silent = True

        while not self._quit:
            self._wake.clear()
            now = monotonic()

            if not self._on:
                self._levels = zero
                dynamics.clear()
                if self._enumerator is None and self._client is None:
                    off_since = None
                    if not self._on and not self._quit:
                        self._wake.wait(5.0)
                    continue
                if off_since is None:
                    off_since = now
                remaining = _RELEASE_DELAY - (now - off_since)
                if remaining > 0.0:
                    self._wake.wait(remaining)
                else:
                    self._close()
                    log.debug("Audio spectrum: capture released (disabled)")
                continue

            resumed = off_since is not None
            off_since = None

            if self._capture is None:
                if now < retry_at:
                    self._wake.wait(min(retry_at - now, 0.5))
                    continue
                try:
                    self._open()
                except Exception as exc:
                    self._close()
                    fails += 1
                    if fails >= _FAILS_UNAVAILABLE:
                        self._ok = False
                    self._report(exc, "open the loopback stream")
                    retry_at = now + (_RETRY if fails < 20 else 2.0 * _RETRY)
                    continue
                if fails < _FAILS_UNAVAILABLE:
                    self._ok = True
                dynamics.set_tilt(self._bank.tilt)
                opened_at = last_audio = last_update = now
                next_check = now + _DEVICE_CHECK
                silent = True
            elif resumed:
                # Re-enabled before the client was released: skip what piled up.
                try:
                    self._read(discard=True)
                except Exception:
                    pass
                self._bank.reset()
                last_audio = last_update = now
                silent = True

            try:
                raw = self._read()
                if now >= next_check:
                    next_check = now + _DEVICE_CHECK
                    if self._default_changed():
                        raise _DeviceChanged()
            except Exception as exc:
                healthy = now - opened_at >= _HEALTHY_AFTER
                self._close()
                self._levels = zero
                dynamics.clear()
                if isinstance(exc, _DeviceChanged):
                    log.debug("Audio spectrum: default output device changed")
                    retry_at = now
                else:
                    if not healthy:
                        fails += 1
                        if fails >= _FAILS_UNAVAILABLE:
                            self._ok = False
                    self._report(exc, "read the loopback stream")
                    retry_at = now + (_RETRY_QUICK if healthy else _RETRY)
                continue

            if fails and now - opened_at >= _HEALTHY_AFTER:
                fails = 0
                self._fail_key = None
                self._ok = True

            # A stream of digital zeros (a paused player) is the same thing as
            # no packets at all (nothing playing): silence.
            if raw and raw.count(0) != len(raw):
                if silent:
                    silent = False
                    self._bank.reset()
                self._bank.feed(raw)
                last_audio = now
                self._levels = dynamics.update(self._bank.analyze(), min(now - last_update, 0.1))
                last_update = now
            elif now - last_audio > _SILENCE_AFTER:
                silent = True
                self._levels = dynamics.update(None, min(now - last_update, 0.1))
                last_update = now

            if not self._on:
                self._levels = zero
            delay = _POLL - (monotonic() - now)
            time.sleep(delay if delay > 0.004 else 0.004)

    def _open(self) -> None:
        if self._enumerator is None:
            self._enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
            )
        device = self._enumerator.GetDefaultAudioEndpoint(_E_RENDER, _E_CONSOLE)
        device_key = _device_id(device)
        client = device.Activate(IAudioClient._iid_, comtypes.CLSCTX_ALL, None)
        client = client.QueryInterface(IAudioClient)
        mix = client.GetMixFormat()
        address = ctypes.cast(mix, ctypes.c_void_p).value
        try:
            fmt = _parse_format(address)
            client.Initialize(
                _SHAREMODE_SHARED, _STREAMFLAGS_LOOPBACK, _BUFFER_HNS, 0, mix, None
            )
        finally:
            del mix
            _CoTaskMemFree(address)
        capture = client.GetService(_IAudioCaptureClient._iid_)
        capture = capture.QueryInterface(_IAudioCaptureClient)
        bank = _FilterBank(self._bands, *fmt)
        client.Start()
        self._client = client
        self._capture = capture
        self._device_key = device_key
        self._frame_bytes = fmt[2]
        self._bank = bank
        log.debug("Audio spectrum: capturing %d Hz, %d ch, '%s' samples", fmt[0], fmt[1], fmt[3])

    def _close(self) -> None:
        """Stop the stream and release every COM object.  Never raises."""
        client = self._client
        self._capture = None
        self._client = None
        self._enumerator = None
        self._device_key = None
        self._bank = None
        if client is not None:
            try:
                client.Stop()
            except Exception:
                pass
            del client

    def _read(self, discard: bool = False) -> bytes:
        """Drain the packets that are waiting; b"" when there are none."""
        capture = self._capture
        frame_bytes = self._frame_bytes
        chunks = []
        for _ in range(_MAX_PACKETS):
            if not capture.GetNextPacketSize():
                break
            data, frames, flags = capture.GetBuffer(None, None)
            try:
                if frames and not discard:
                    size = frames * frame_bytes
                    if flags & _BUFFERFLAGS_SILENT or not data:
                        chunks.append(bytes(size))
                    else:
                        chunks.append(ctypes.string_at(data, size))
            finally:
                capture.ReleaseBuffer(frames)
        if len(chunks) == 1:
            return chunks[0]
        return b"".join(chunks)

    def _default_changed(self) -> bool:
        device = self._enumerator.GetDefaultAudioEndpoint(_E_RENDER, _E_CONSOLE)
        return _device_id(device) != self._device_key

    def _report(self, exc: BaseException, action: str) -> None:
        """Log a failure once; repeats of the same failure stay silent."""
        code = getattr(exc, "hresult", None)
        if isinstance(code, int):
            code &= 0xFFFFFFFF
        key = (action, type(exc).__name__, code if code is not None else str(exc))
        if key == self._fail_key:
            return
        self._fail_key = key
        expected = code in _QUIET_HRESULTS
        log.log(
            logging.DEBUG if expected else logging.WARNING,
            "Audio spectrum: could not %s (%s%s); retrying quietly",
            action,
            type(exc).__name__,
            " 0x%08X" % code if code is not None else ": %s" % exc,
        )


class _DeviceChanged(Exception):
    """The default render endpoint is no longer the one being captured."""


# ------------------------------------------------------------------- demo --

def _demo(seconds: float = 15.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.setLevel(logging.DEBUG)
    analyzer = SpectrumAnalyzer()
    analyzer.start()
    analyzer.set_enabled(True)
    width = 10
    print("Play some music. Bars: bass on the left. (%d s)" % seconds)
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            bars = " ".join(
                ("#" * int(round(v * width))).ljust(width, ".") for v in analyzer.levels()
            )
            status = "" if analyzer.available() else "  [unavailable]"
            sys.stdout.write("\r" + bars + status + "   ")
            sys.stdout.flush()
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        analyzer.stop()
        print()


if __name__ == "__main__":
    _demo()
