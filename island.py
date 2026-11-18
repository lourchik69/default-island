import asyncio
import ctypes
import ctypes.wintypes
import datetime
import hashlib
import json
import logging
import logging.handlers
import math
import os
import queue
import random
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
from PyQt6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRegion,
)
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
from pycaw.pycaw import AudioUtilities
from island_core import (
    ACCENT_PRESETS, APP_DATA_DIR, APP_NAME, COLLAPSED_HEIGHT, COLLAPSED_RADIUS,
    CONFIG_FILE, DANGER_COLOR, DEFAULT_ACCENT, EXPANDED_HEIGHT,
    EXPANDED_HEIGHT_TIMER, EXPANDED_RADIUS, EXPANDED_WIDTH, LOG_FILE, IslandConfig, is_number,
    load_config, make_look, mix_colors, paint_glass_tint, paint_island_surface,
    paint_soft_shadow, parse_hotkey, round_pen, rounded_rect_path, save_config,
    soften_accent, surface_fill, take_installer_choices, ui_font, user32_function, white,
    with_alpha,
)
from island_settings import SettingsWindow
from island_i18n import register, set_language, tr
from island_extras import HUD_WIDTH, ExtrasMixin


try:
    import winrt.windows.media.control as wmc
    import winrt.windows.storage.streams as wss
    HAS_WINRT = True
except ImportError:
    HAS_WINRT = False


register({
    "{app} сейчас не воспроизводит звук": ("{app} is not playing any sound", "{app} зараз не відтворює звук"),
    "Без названия": ("Untitled", "Без назви"),
    "Горячая клавиша занята": ("The hotkey is taken", "Гаряча клавіша зайнята"),
    "Задайте время больше 00:00:00": ("Set a time above 00:00:00", "Задайте час більший за 00:00:00"),
    "Не удалось изменить громкость": ("Could not change the volume", "Не вдалося змінити гучність"),
    "Не удалось переключить режим призрака": ("Could not switch ghost mode", "Не вдалося перемкнути режим привида"),
    "Не удалось сохранить настройки": ("Could not save the settings", "Не вдалося зберегти налаштування"),
    "Ничего не играет": ("Nothing is playing", "Нічого не грає"),
    "Обновите расширение в браузере": ("Update the browser extension", "Оновіть розширення в браузері"),
    "Выберите плейлист в браузере": ("Pick a playlist in the browser", "Виберіть плейлист у браузері"),
    "Ошибка сохранения": ("Saving failed", "Помилка збереження"),
    "Перезапустите браузер (расширение)": ("Restart the browser (extension)", "Перезапустіть браузер (розширення)"),
    "Пинг: {ms} мс": ("Ping: {ms} ms", "Пінг: {ms} мс"),
    "Пинг: нет сети": ("Ping: offline", "Пінг: немає мережі"),
    "Пинг: …": ("Ping: …", "Пінг: …"),
    "Режим призрака включён": ("Ghost mode on", "Режим привида увімкнено"),
    "Режим призрака выключен": ("Ghost mode off", "Режим привида вимкнено"),
    "Таймер завершён": ("Timer finished", "Таймер завершено"),
    "Таймер": ("Timer", "Таймер"),
    "Секундомер": ("Stopwatch", "Секундомір"),
    "ЧАС": ("HRS", "ГОД"),
    "МИН": ("MIN", "ХВ"),
    "СЕК": ("SEC", "СЕК"),
    "Старт": ("Start", "Старт"),
    "Пауза": ("Pause", "Пауза"),
    "Стоп": ("Stop", "Стоп"),
    "ТАЙМЕР": ("TIMER", "ТАЙМЕР"),
    "СЕК.": ("SW", "СЕК."),
    "СТАРТ": ("START", "СТАРТ"),
    "ПАУЗА": ("PAUSE", "ПАУЗА"),
    "СТОП": ("STOP", "СТОП"),
    "Не удалось запустить приложение:": ("Could not start the app:", "Не вдалося запустити застосунок:"),
})
# Windows media keys, used when no media session accepts a command.
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
SESSION_PLAYING = 4
SESSION_PAUSED = 5

# The island paints in its own units on a canvas of this size; the window is
# the canvas scaled by the size settings.
CANVAS_W = 500.0


# --- Browser extension bridge ---------------------------------------------
BRIDGE_PORT = 28491
BRIDGE_PROTOCOL_VERSION = 3
# The previous extension keeps working, without what version 3 added.
BRIDGE_LEGACY_VERSION = 2
MAX_DOWNLOADS = 20
# A report is kept open this long waiting for a command (long poll); the
# extension sends its next report as soon as the answer arrives.
BRIDGE_HOLD_SEC = 0.2
# Without a report for this long the extension is considered gone and the
# island shows what Windows knows about the playing media instead.
BRIDGE_FRESH_SEC = 2.0
# A second SoundCloud tab may take over after the first one is silent this long.
BRIDGE_TAB_TIMEOUT_SEC = 3.0
# Commands nobody picked up in time are dropped, so they do not fire
# unexpectedly when the SoundCloud tab comes back.
BRIDGE_COMMAND_TTL_SEC = 3.0
BRIDGE_MAX_BODY = 64 * 1024
MAX_TEXT_LENGTH = 300

# --- Playback state --------------------------------------------------------
# After a click the island shows the requested state right away and ignores
# reports that still contradict it for this long.
OPTIMISTIC_HOLD_SEC = 2.5
# The local clock is corrected only when it drifts further than this from the
# reported position (seek in the player, restarted track).
POSITION_RESYNC_SEC = 0.35
# Tolerance for positions reported in whole seconds.
POSITION_RESYNC_COARSE_SEC = 1.5
SYSTEM_POLL_SEC = 0.35
SYSTEM_FAST_POLL_SEC = 0.12
SYSTEM_POLL_TIMEOUT_SEC = 8.0
MAX_COVER_BYTES = 8 * 1024 * 1024



def send_media_key(key):
    keybd_event = user32_function(
        "keybd_event",
        [
            ctypes.wintypes.BYTE,
            ctypes.wintypes.BYTE,
            ctypes.wintypes.DWORD,
            ctypes.c_size_t,
        ],
        None,
    )
    keybd_event(key, 0, KEYEVENTF_EXTENDEDKEY, 0)
    keybd_event(key, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


_window_titles = (0.0, ())


def visible_window_titles(max_age=1.0):
    """Titles of the visible top-level windows, reused for `max_age` seconds."""
    global _window_titles
    now = time.monotonic()
    if now - _window_titles[0] < max_age:
        return _window_titles[1]
    titles = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    is_window_visible = user32_function(
        "IsWindowVisible", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL
    )
    get_window_text_length = user32_function(
        "GetWindowTextLengthW", [ctypes.wintypes.HWND], ctypes.wintypes.INT
    )
    get_window_text = user32_function(
        "GetWindowTextW",
        [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.wintypes.INT],
        ctypes.wintypes.INT,
    )

    def callback(hwnd, lParam):
        if is_window_visible(hwnd):
            length = get_window_text_length(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                get_window_text(hwnd, buff, length + 1)
                titles.append(buff.value)
        return True

    enum_windows = user32_function(
        "EnumWindows", [WNDENUMPROC, ctypes.wintypes.LPARAM], ctypes.wintypes.BOOL
    )
    enum_windows(WNDENUMPROC(callback), 0)
    _window_titles = (now, tuple(titles))
    return _window_titles[1]


# "Title - YouTube - Brave", "Title — YouTube — Mozilla Firefox"; not YouTube Music.
_YOUTUBE_WINDOW = re.compile(r"[-–—]\s*youtube\s*(?:[-–—]|$)", re.IGNORECASE)


def is_youtube_window(title):
    return bool(_YOUTUBE_WINDOW.search(title))


def find_media_window_title(skip_youtube=False):
    """Title of a visible player window; used only when Windows media
    sessions are unavailable."""
    titles = []
    for title in visible_window_titles():
        lowered = title.casefold()
        if skip_youtube and is_youtube_window(title):
            continue
        if "soundcloud" in lowered or "spotify" in lowered or "youtube" in lowered:
            titles.append(title)
    titles.sort(key=lambda title: "soundcloud" not in title.casefold())
    for title in titles:
        clean = title.replace(" - Brave", "").replace(" - Google Chrome", "").strip()
        if clean:
            return clean
    return ""


@dataclass
class MediaState:
    """What one source (the browser extension or Windows) says is playing."""
    title: str = ""
    artist: str = ""
    # Title as the source spelled it, for matching the same track across sources.
    match_title: str = ""
    playing: bool = False
    # True only while the position is really moving (not paused or buffering).
    ticking: bool = False
    duration: float = 0.0
    position: float = 0.0
    # time.perf_counter() at which `position` was correct.
    position_time: float = 0.0
    rate: float = 1.0
    # The position has whole-second precision only.
    coarse: bool = False
    # 0 off, 1 repeat all, 2 repeat one; None when the source cannot tell.
    repeat: int | None = None
    # Whether the track is liked; None when the player has no like button.
    liked: bool | None = None
    # Which site the browser extension reports from ("soundcloud", "youtube"...).
    site: str = ""
    # time.perf_counter() when this report arrived.
    updated: float = 0.0

    @property
    def has_track(self):
        return bool(self.title)

    def position_at(self, now):
        position = self.position
        if self.ticking:
            position += max(0.0, now - self.position_time) * self.rate
        if self.duration > 0:
            position = min(position, self.duration)
        return max(0.0, position)


def normalize_title(text):
    return " ".join((text or "").casefold().split())


def same_track(first, second):
    """Whether two sources are talking about the same track."""
    if not first.has_track or not second.has_track:
        return False
    left = {normalize_title(first.title), normalize_title(first.match_title)} - {""}
    right = {normalize_title(second.title), normalize_title(second.match_title)} - {""}
    if left & right:
        return True
    # One title inside the other ("Song" and "Artist - Song") counts only
    # when the artists do not disagree: "Intro" is not "Intro to Python".
    artists = normalize_title(first.artist), normalize_title(second.artist)
    if all(artists) and artists[0] not in artists[1] and artists[1] not in artists[0]:
        if not any(artist in title for artist in artists for title in left | right):
            return False
    return any(
        len(a) >= 4 and len(b) >= 4 and (a in b or b in a)
        for a in left
        for b in right
    )


class VideoFilter:
    """Recognises videos on youtube.com (not YouTube Music), which the island
    leaves out unless the "youtube_video" setting is on.

    The browser extension says outright that a tab is YouTube. Windows only
    names the video, so a session counts as one when a browser window shows
    that title as a YouTube page. A video recognised once stays recognised
    while it keeps being seen, also after its tab goes to the background.
    """

    # A recognised title is forgotten this long after it was last confirmed.
    MEMORY_SEC = 600.0
    # A title found not to be a video is looked at again after this long.
    RECHECK_SEC = 10.0

    def __init__(self, tabs=None):
        self.hide = True
        # BrowserTabs, for videos playing in a tab behind the one in front.
        self.tabs = tabs
        self._lock = threading.Lock()
        self._videos = {}  # normalized title -> time.monotonic() last confirmed
        self._not_videos = {}  # normalized title -> time.monotonic() checked

    def remember(self, title):
        key = normalize_title(title)
        if not key:
            return
        with self._lock:
            self._videos[key] = time.monotonic()
            if len(self._videos) > 64:
                del self._videos[min(self._videos, key=self._videos.get)]

    def is_video(self, title):
        key = normalize_title(title)
        if not key:
            return False
        now = time.monotonic()
        with self._lock:
            seen = self._videos.get(key)
            known = seen is not None and now - seen < self.MEMORY_SEC
            checked = self._not_videos.get(key)
        if self._shown_as_video(key, visible_window_titles()):
            self.remember(title)
            return True
        if known:
            with self._lock:
                self._videos[key] = now  # still playing: keep it recognised
            return True
        if self.tabs is None or (checked is not None and now - checked < self.RECHECK_SEC):
            return False
        # Not in front: look through the tabs behind (a few milliseconds).
        if self._shown_as_video(key, self.tabs.names(wait=0.4)):
            self.remember(title)
            return True
        if not self.tabs.fresh():
            return False  # the tabs were not read in time: ask again next round
        with self._lock:
            self._not_videos[key] = now
            if len(self._not_videos) > 64:
                del self._not_videos[min(self._not_videos, key=self._not_videos.get)]
        return False

    @staticmethod
    def _shown_as_video(key, titles):
        return any(is_youtube_window(title) and key in normalize_title(title) for title in titles)


def measure_ping_ms(timeout=1.5):
    """Round-trip time to the internet in milliseconds, or None if offline.

    Times a TCP handshake with a public DNS server, which takes one network
    round trip and needs no administrator rights (unlike a raw ICMP ping).
    """
    for host in ("1.1.1.1", "8.8.8.8"):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                return (time.perf_counter() - started) * 1000.0
        except OSError:
            continue
    return None


def parse_bridge_state(data, now):
    """Validate one report from the browser extension."""

    def text(key):
        value = data.get(key)
        return value.strip()[:MAX_TEXT_LENGTH] if isinstance(value, str) else ""

    state = MediaState(
        title=text("title"), artist=text("artist"), updated=now, position_time=now
    )
    state.match_title = state.title
    if not state.title:
        return state
    state.playing = data.get("playing") is True
    duration, position = data.get("dur"), data.get("pos")
    if is_number(duration) and is_number(position) and duration > 0:
        state.duration = float(duration)
        state.position = max(0.0, min(float(position), state.duration))
    state.ticking = (
        state.playing and data.get("ticking") is True and state.duration > 0
    )
    state.coarse = data.get("coarse") is True
    repeat = data.get("repeat")
    if repeat in (0, 1, 2) and not isinstance(repeat, bool):
        state.repeat = int(repeat)
    if isinstance(data.get("liked"), bool):
        state.liked = data["liked"]
    site = data.get("site")
    state.site = site[:32] if isinstance(site, str) else ""
    return state


@dataclass(frozen=True)
class Download:
    """One download the browser has in progress."""
    name: str
    received: int
    total: int  # 0 when the size is not known
    paused: bool


def parse_downloads(data):
    """Validate a downloads report from the browser extension."""
    downloads = []
    items = data.get("items")
    if not isinstance(items, list):
        return ()
    for item in items[:MAX_DOWNLOADS]:
        if not isinstance(item, dict):
            continue
        received, total = item.get("received"), item.get("total")
        if not is_number(received) or received < 0:
            continue
        name = item.get("name")
        downloads.append(
            Download(
                name=name.strip()[:MAX_TEXT_LENGTH] if isinstance(name, str) else "",
                received=int(received),
                total=int(total) if is_number(total) and total > 0 else 0,
                paused=item.get("paused") is True,
            )
        )
    return tuple(downloads)


class BridgeSignals(QObject):
    report = pyqtSignal(object)  # MediaState reported by a player's tab
    # An old version of the extension is loaded; True if it still works.
    outdated = pyqtSignal(bool)
    downloads = pyqtSignal(object)  # tuple of Download in progress


class BridgeServer(threading.Thread):
    """Local HTTP endpoint the browser extension reports to.

    Every report is answered with the commands waiting for the page. A
    regular report is kept open for a moment (long poll), so a click on the
    island reaches the page at once instead of waiting for the next report.
    """

    def __init__(self, signals, videos):
        super().__init__(daemon=True)
        self.signals = signals
        self.videos = videos
        self.condition = threading.Condition()
        self.pending_commands = []
        # Only one tab may drive the island.
        self.primary_tab = None
        self.legacy_reported = False
        self.primary_playing = False
        self.primary_seen = 0.0
        self.last_sequence = 0
        self.server = None

    def queue_command(self, command):
        with self.condition:
            self.pending_commands.append((time.perf_counter(), command))
            self.condition.notify_all()

    def wait_for_commands(self, timeout, tab):
        deadline = time.perf_counter() + timeout
        with self.condition:
            while True:
                now = time.perf_counter()
                if tab != self.primary_tab:
                    # Another tab took over while this one waited: the
                    # commands are meant for that one now.
                    return []
                commands = [
                    command
                    for queued_at, command in self.pending_commands
                    if now - queued_at <= BRIDGE_COMMAND_TTL_SEC
                ]
                self.pending_commands.clear()
                if commands or now >= deadline:
                    return commands
                self.condition.wait(deadline - now)

    def _claim_tab(self, tab, state, now):
        """Decide whether this tab is the one that drives the island."""
        if tab != self.primary_tab:
            primary_gone = (
                self.primary_tab is None
                or now - self.primary_seen > BRIDGE_TAB_TIMEOUT_SEC
            )
            takes_over = (primary_gone and state.has_track) or (
                state.playing and not self.primary_playing
            )
            if not takes_over:
                return False
            self.primary_tab = tab
            self.last_sequence = 0
        self.primary_seen = now
        self.primary_playing = state.playing
        return True

    def handle_report(self, data):
        """Process one report and return the commands to send back."""
        now = time.perf_counter()
        version = data.get("v")
        if version == BRIDGE_LEGACY_VERSION:
            if not self.legacy_reported:
                self.legacy_reported = True
                self.signals.outdated.emit(True)
        elif version != BRIDGE_PROTOCOL_VERSION:
            self.signals.outdated.emit(False)
            return []
        if data.get("kind") == "downloads":
            self.signals.downloads.emit(parse_downloads(data))
            return []
        tab = data.get("tab")
        tab = tab[:32] if isinstance(tab, str) else ""
        state = parse_bridge_state(data, now)
        if state.site == "youtube" and self.videos.hide:
            if state.has_track:
                self.videos.remember(state.title)
            with self.condition:
                if tab == self.primary_tab:
                    # Let go of the tab, so another one can take over.
                    self.primary_tab = None
                    self.primary_playing = False
                    self.signals.report.emit(MediaState(updated=now, position_time=now))
            return []
        with self.condition:
            if not self._claim_tab(tab, state, now):
                return []
            # Reports travel separately and may overtake each other.
            sequence = data.get("seq")
            if not is_number(sequence) or sequence > self.last_sequence:
                if is_number(sequence):
                    self.last_sequence = int(sequence)
                self.signals.report.emit(state)
        urgent = data.get("urgent") is True
        return self.wait_for_commands(0.0 if urgent else BRIDGE_HOLD_SEC, tab)

    def run(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            # A client that stops sending cannot block its thread forever.
            timeout = 5

            def log_message(self, format, *args):
                return

            def _origin_allowed(self):
                origin = self.headers.get("Origin", "")
                return not origin or origin.startswith("chrome-extension://")

            def _send_cors(self):
                origin = self.headers.get("Origin", "")
                if origin.startswith("chrome-extension://"):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                if self.headers.get("Access-Control-Request-Private-Network") == "true":
                    self.send_header("Access-Control-Allow-Private-Network", "true")

            def do_OPTIONS(self):
                if not self._origin_allowed():
                    self.send_error(403)
                    return
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                if not self._origin_allowed():
                    self.send_error(403)
                    return
                commands = []
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if length < 0 or length > BRIDGE_MAX_BODY:
                        self.send_error(413)
                        return
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    data = json.loads(raw.decode("utf-8", errors="ignore"))
                    if not isinstance(data, dict):
                        raise ValueError("Bridge payload must be a JSON object")
                    commands = bridge.handle_report(data)
                except (ValueError, TypeError):
                    logging.warning("Ignoring invalid media bridge request", exc_info=True)
                body = json.dumps({"commands": commands}).encode("utf-8")
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # On Windows "reuse address" would let a second copy of the app
            # bind the same port and steal half of the reports.
            allow_reuse_address = False

            def handle_error(self, request, client_address):
                # The browser closed the connection (tab closed, reload).
                logging.debug("Media bridge request failed", exc_info=True)

        try:
            self.server = Server(("127.0.0.1", BRIDGE_PORT), Handler)
        except OSError:
            logging.exception("Could not start the local media bridge")
            return
        self.server.serve_forever()

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


class MediaSignals(QObject):
    state = pyqtSignal(object)  # MediaState of the Windows media session
    cover = pyqtSignal(QImage, str)  # artwork and the track it belongs to


class MediaWorker(threading.Thread):
    """Reads the playing media from Windows and sends it commands.

    Works with any player that registers with Windows (browsers, Spotify...),
    so the island is useful even without the browser extension.
    """

    MEDIA_KEYS = {
        "set_playing": VK_MEDIA_PLAY_PAUSE,
        "next": VK_MEDIA_NEXT_TRACK,
        "prev": VK_MEDIA_PREV_TRACK,
    }

    def __init__(self, signals, videos):
        super().__init__(daemon=True)
        self.signals = signals
        self.videos = videos
        self.actions = queue.Queue()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.manager = None
        self.fast_polls = 0
        self.last_error_log = 0.0
        self.cover_track = None
        self.cover_digest = None
        self.cover_settle_until = 0.0
        self.cover_recheck_at = 0.0

    def request(self, action, value=None):
        """Queue "set_playing" (bool), "seek" (seconds), "next" or "prev"."""
        self.actions.put((action, value))
        self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self.stop_event.is_set():
                try:
                    if HAS_WINRT:
                        # A player that stops answering must not freeze the
                        # island's link to every other player.
                        loop.run_until_complete(
                            asyncio.wait_for(self.poll_sessions(), SYSTEM_POLL_TIMEOUT_SEC)
                        )
                    else:
                        self.poll_window_title()
                except Exception:
                    # Reconnect to Windows on the next round.
                    self.manager = None
                    if time.monotonic() - self.last_error_log > 30:
                        logging.exception("Media polling failed")
                        self.last_error_log = time.monotonic()
                    self.press_keys_for_pending_actions()
                delay = SYSTEM_FAST_POLL_SEC if self.fast_polls > 0 else SYSTEM_POLL_SEC
                self.fast_polls = max(0, self.fast_polls - 1)
                self.wake.wait(delay)
                self.wake.clear()
        finally:
            loop.close()

    def press_keys_for_pending_actions(self):
        ran = False
        while True:
            try:
                action, _ = self.actions.get_nowait()
            except queue.Empty:
                return ran
            ran = True
            key = self.MEDIA_KEYS.get(action)
            if key:
                send_media_key(key)

    def poll_window_title(self):
        """Fallback without Windows media sessions: only a title is known."""
        self.press_keys_for_pending_actions()
        now = time.perf_counter()
        state = MediaState(updated=now, position_time=now)
        raw = find_media_window_title(skip_youtube=self.videos.hide)
        if raw:
            title = raw.split("|")[0].strip()
            artist = ""
            if " - " in title:
                artist, title = (part.strip() for part in title.split(" - ", 1))
            state.title = title[:MAX_TEXT_LENGTH]
            state.artist = artist[:MAX_TEXT_LENGTH]
            state.match_title = raw
        self.signals.state.emit(state)

    async def poll_sessions(self):
        if self.manager is None:
            self.manager = (
                await wmc.GlobalSystemMediaTransportControlsSessionManager
                .request_async()
            )
        session, info, playback, status = await self.pick_session()
        if await self.run_actions(session, status):
            # Look again soon to pick up the result of the command.
            self.fast_polls = 4
        now = time.perf_counter()
        if session is None:
            self.cover_track = None
            self.signals.state.emit(MediaState(updated=now, position_time=now))
            return
        state = self.read_state(session, info, playback, status, now)
        self.signals.state.emit(state)
        await self.refresh_cover(info, f"{state.title}\n{state.artist}")

    async def pick_session(self):
        """The session worth showing: playing first, then paused."""
        current = self.manager.get_current_session()
        current_app = current.source_app_user_model_id if current else None
        best = (None, None, None, 0)
        best_key = (0, 0)
        for session in self.manager.get_sessions():
            try:
                playback = session.get_playback_info()
                status = int(playback.playback_status) if playback else 0
                info = await session.try_get_media_properties_async()
                title = (info.title or "").strip() if info else ""
                is_current = session.source_app_user_model_id == current_app
            except Exception:
                logging.debug("Could not read a Windows media session", exc_info=True)
                continue
            if self.videos.hide and self.videos.is_video(title):
                continue
            rank = (
                3 if status == SESSION_PLAYING
                else 2 if status == SESSION_PAUSED
                else 1 if title
                else 0
            )
            key = (rank, 1 if is_current else 0)
            if rank > 0 and key > best_key:
                best, best_key = (session, info, playback, status), key
        return best

    def read_state(self, session, info, playback, status, now):
        raw_title = (info.title or "").strip() if info else ""
        artist = (info.artist or "").strip() if info else ""
        title = raw_title
        if not artist and " - " in title:
            artist, title = (part.strip() for part in title.split(" - ", 1))
        state = MediaState(
            title=(title or tr("Без названия"))[:MAX_TEXT_LENGTH],
            artist=artist[:MAX_TEXT_LENGTH],
            match_title=raw_title,
            playing=status == SESSION_PLAYING,
            updated=now,
            position_time=now,
        )
        try:
            timeline = session.get_timeline_properties()
        except Exception:
            timeline = None
        if timeline is None:
            return state
        start = timeline.start_time.total_seconds()
        duration = timeline.end_time.total_seconds() - start
        if duration <= 0:
            return state
        position = timeline.position.total_seconds() - start
        if state.playing:
            rate = playback.playback_rate if playback else None
            state.rate = float(rate) if is_number(rate) and 0 < rate <= 16 else 1.0
            # Players report the position only when something changes (play,
            # pause, seek), together with the time of that report. The current
            # position has to be worked out from the two.
            age = (
                datetime.datetime.now(datetime.timezone.utc)
                - timeline.last_updated_time
            ).total_seconds()
            if 0 < age < 24 * 3600:
                position += age * state.rate
        state.duration = duration
        state.position = max(0.0, min(position, duration))
        state.ticking = state.playing
        return state

    async def read_thumbnail(self, info):
        thumbnail = info.thumbnail if info else None
        if thumbnail is None:
            return b""
        try:
            stream = await thumbnail.open_read_async()
            try:
                size = stream.size
                if not 0 < size <= MAX_COVER_BYTES:
                    return b""
                buffer = wss.Buffer(size)
                await stream.read_async(buffer, size, wss.InputStreamOptions.NONE)
                return bytes(buffer)
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
        except Exception:
            logging.debug("Could not read the media thumbnail", exc_info=True)
            return b""

    async def refresh_cover(self, info, track):
        now = time.monotonic()
        if track != self.cover_track:
            self.cover_track = track
            self.cover_digest = None
            # Players publish the artwork a moment after the title, so keep
            # checking for a few seconds after a track change.
            self.cover_settle_until = now + 4.0
        elif now < self.cover_recheck_at:
            return
        # Later the artwork changes only with the track; a rare look is enough
        # for the players that swap it late.
        self.cover_recheck_at = now + (1.0 if now <= self.cover_settle_until else 45.0)
        data = await self.read_thumbnail(info)
        digest = hashlib.sha1(data).digest() if data else b""
        if digest == self.cover_digest:
            return
        self.cover_digest = digest
        image = QImage.fromData(data) if data else QImage()
        self.signals.cover.emit(image, track)

    async def run_actions(self, session, status):
        ran = False
        while True:
            try:
                action, value = self.actions.get_nowait()
            except queue.Empty:
                return ran
            ran = True
            accepted = False
            if session is not None:
                try:
                    if action == "seek":
                        start = session.get_timeline_properties().start_time
                        accepted = await session.try_change_playback_position_async(
                            int((value + start.total_seconds()) * 10_000_000)
                        )
                    elif action == "set_playing":
                        if (status == SESSION_PLAYING) == value:
                            accepted = True
                        elif value:
                            accepted = await session.try_play_async()
                        else:
                            accepted = await session.try_pause_async()
                        if not accepted:
                            accepted = await session.try_toggle_play_pause_async()
                    elif action == "next":
                        accepted = await session.try_skip_next_async()
                    elif action == "prev":
                        accepted = await session.try_skip_previous_async()
                except Exception:
                    logging.exception("Media command %s failed", action)
            if not accepted:
                key = self.MEDIA_KEYS.get(action)
                if key:
                    send_media_key(key)


class PingWorker(threading.Thread):
    """Measures the network round trip while the ping zone is shown."""

    def __init__(self):
        super().__init__(daemon=True)
        self.enabled = threading.Event()
        self.stop_event = threading.Event()
        self.measured = False
        self.value_ms = None

    def stop(self):
        self.stop_event.set()
        self.enabled.set()

    def run(self):
        while True:
            self.enabled.wait()
            if self.stop_event.is_set():
                return
            self.value_ms = measure_ping_ms()
            self.measured = True
            self.stop_event.wait(2.0)


class IslandShadow(QWidget):
    """The soft shadow that makes the island float above the screen.

    It is a window of its own that ignores the mouse. Painted inside the
    island's window, the shadow would swallow clicks meant for whatever lies
    under it.
    """

    # Room around the island's window for the shadow at the largest size.
    MARGIN = 80
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._shapes = ()
        self._size = 1.0

    def follow(self, island_geometry):
        margin = self.MARGIN
        self.setGeometry(island_geometry.adjusted(-margin, -margin, margin, margin))

    def set_shapes(self, shapes, size=1.0):
        """`shapes` is a tuple of (x, y, width, height, radius, strength) in
        the island window's pixels; `size` is the island's current scale."""
        if shapes != self._shapes or size != self._size:
            self._shapes = shapes
            self._size = size
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.MARGIN, self.MARGIN)
        for x, y, width, height, radius, strength in self._shapes:
            if strength > 0.01:
                paint_soft_shadow(
                    painter, QRectF(x, y, width, height), radius, strength, self._size
                )
        # Nothing under the island itself: the glass themes are see-through.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        for x, y, width, height, radius, strength in self._shapes:
            painter.drawRoundedRect(
                QRectF(x + 1.0, y + 1.0, width - 2.0, height - 2.0),
                max(0.0, radius - 1.0),
                max(0.0, radius - 1.0),
            )


class DynamicIsland(ExtrasMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.config = IslandConfig(load_config())
        # The installed program takes over what was ticked in the installer.
        if getattr(sys, "frozen", False) and take_installer_choices(self.config):
            try:
                save_config(self.config)
            except OSError:
                logging.exception("Could not save the installer's choices")
        set_language(self.config["language"])
        self.look = make_look(self.config)
        self._registered_hotkey = False

        # What is on screen; filled in from the selected media source.
        self.title = ""
        self.artist = ""
        self.is_playing = False
        self.clock_ticking = False
        self.playback_rate = 1.0
        self.position_sec = 0.0
        self.duration_sec = 0.0
        self.repeat_mode = 0
        self.repeat_available = False
        self.liked = None  # None: the player has no like button
        self.media_site = ""  # which site the extension reports from
        self.cover_pixmap = None
        self.auto_accent_color = QColor(DEFAULT_ACCENT)
        self._window_mask_key = None
        self._cover_cache = {}
        self.shadow = None

        # Media sources: the browser extension and Windows media sessions.
        self.bridge_media = MediaState()
        self.system_media = MediaState()
        # YouTube videos the island leaves out; shared with the media threads.
        self.videos = VideoFilter()
        self.videos.hide = not self.config["youtube_video"]
        self.media_source = "system"
        self.shown_track = ("", "")
        self.other_player_since = None
        self.last_media_refresh = 0.0
        self._media_view = None  # what refresh_media last asked to draw
        self.system_cover = None  # (track, pixmap, accent colour or None)
        self.cover_shown_for = None
        # Clicks the player has not confirmed yet.
        self.pending_playing = None  # (wanted, deadline, "bridge" | "system")
        self.pending_repeat = None  # (wanted mode, deadline)
        self.pending_liked = None  # (wanted, deadline)
        self.pending_seek = None  # (target seconds, time of the click)
        self.outdated_extension_notified = None
        self.legacy_extension_notified = False

        self.cached_cpu_percent = 0
        self.cached_memory_percent = 0

        self.timer_mode = "Timer"
        self.timer_active = False
        self.timer_remaining = float(self.config["timer_duration_seconds"])
        self.timer_seconds_left = int(self.timer_remaining)
        self.timer_deadline = 0.0
        self.stopwatch_elapsed = 0.0
        self.stopwatch_started = 0.0
        self.stopwatch_seconds = 0
        self.timer_panel_open = False
        self.timer_session_visible = False
        self.timer_mini_visible = bool(self.config["timer_configured"])
        self.timer_wheel_rest = 0.0
        self.keep_visible_until_cursor_leaves = False
        # After the settings close the island stays open for a moment, so
        # the eye finds it again.
        self.settings_restore_until = 0.0

        # A short message in the collapsed island.
        self.notification_text = ""
        self.notification_until = 0.0
        self.notification_keeps_width = False

        self.current_volume = 1.0
        self.saved_volume_before_mute = 0.8
        self.is_muted = False
        self.volume_overlay_until = 0.0
        self.vol_popup_anim = 0.0
        self.vol_popup_rect = QRectF()
        self.vol_track_rect = QRectF()
        self.dragging_vol_slider = False
        self.volume_sync_countdown = 0
        self._volume_controls = None  # (time found, controls of the target app)

        self.ghost_mode = False

        self.last_tick_time = time.perf_counter()
        self.track_bar_rect = QRectF()
        self.vol_rect = QRectF()
        self.prev_rect = QRectF()
        self.play_rect = QRectF()
        self.next_rect = QRectF()
        self.repeat_rect = QRectF()
        self.like_rect = QRectF()
        self.gear_rect = QRectF()
        self.dnd_rect = QRectF()
        self.timer_btn_rect = QRectF()
        self.timer_shortcut_rect = QRectF()
        self.timer_start_rect = QRectF()
        self.timer_pause_rect = QRectF()
        self.timer_stop_rect = QRectF()
        self.timer_timer_mode_rect = QRectF()
        self.timer_stopwatch_mode_rect = QRectF()
        self.timer_hour_rect = QRectF()
        self.timer_minute_rect = QRectF()
        self.timer_second_rect = QRectF()
        self.timer_close_rect = QRectF()
        self.timer_view_rect = QRectF()
        self.timer_mini_rect = QRectF()
        self.timer_mini_open_rect = QRectF()
        self.timer_mini_mode_rect = QRectF()
        self.timer_mini_start_rect = QRectF()
        self.timer_mini_pause_rect = QRectF()
        self.timer_mini_stop_rect = QRectF()
        self.hovered_bar = False
        self.seeking_bar = False
        # Eased 0..1 values behind the soft state changes.
        self.bar_hover_anim = 0.0
        self.playing_anim = 0.0

        self.canvas_w = CANVAS_W
        self.canvas_h = CANVAS_H
        # True while the island sits low on the screen and grows upwards.
        self.opens_up = False
        # The collapsed pill grows and shrinks with its text.
        self.col_w, self.col_h = 190.0, COLLAPSED_HEIGHT
        self.col_w_target = self.col_w
        # The expanded island is taller while it has more rows to show.
        self.exp_w, self.exp_h = EXPANDED_WIDTH, EXPANDED_HEIGHT
        self.collapsed_font = ui_font(9, QFont.Weight.DemiBold)
        self._text_width_cache = {}

        # How far the island is open: 0 collapsed, 1 expanded. `t` is what
        # is drawn; the spring behind it advances in fixed steps and `t` is
        # read between the last two of them.
        self.t = 0.0
        self.vel = 0.0
        self._spring_now = 0.0
        self._spring_before = 0.0
        self._spring_debt = 0.0
        self.hovered = False
        self.hovered_btn = None
        self.btn_hover_anim = {
            "vol": 0.0,
            "prev": 0.0,
            "play": 0.0,
            "next": 0.0,
            "repeat": 0.0,
            "like": 0.0,
            "gear": 0.0,
            "dnd": 0.0,
            "timer_btn": 0.0,
            "timer_start": 0.0,
            "timer_pause": 0.0,
            "timer_stop": 0.0,
            "timer_mode_timer": 0.0,
            "timer_mode_stopwatch": 0.0,
            "timer_hour": 0.0,
            "timer_minute": 0.0,
            "timer_second": 0.0,
            "timer_close": 0.0,
            "timer_mini_open": 0.0,
            "timer_mini_start": 0.0,
            "timer_mini_pause": 0.0,
            "timer_mini_stop": 0.0,
        }
        # Set by anything that changed what is drawn; the next tick paints.
        self._moving = True
        self._still_since = 0.0

        # Moving the island: Ctrl + right drag, or the settings' button.
        self.dragging_enabled = False
        self.positioning = False
        self.position_done = None
        self.drag_grab = QPoint()
        self.snapped_to_center = False

        self.eq_bars = [0.18] * 6
        # The imitated visualizer: where each bar is heading and for how long.
        self._eq_goals = [0.5] * 6
        self._eq_clock = [0.0] * 6

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.extras_init()
        self.exp_h = self.expanded_height_target()
        self.shadow = IslandShadow()
        self.apply_geometry()

        hwnd = int(self.winId())
        set_window_pos = user32_function(
            "SetWindowPos",
            [
                ctypes.wintypes.HWND,
                ctypes.wintypes.HWND,
                ctypes.wintypes.INT,
                ctypes.wintypes.INT,
                ctypes.wintypes.INT,
                ctypes.wintypes.INT,
                ctypes.wintypes.UINT,
            ],
            ctypes.wintypes.BOOL,
        )
        set_window_pos(
            hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
        )
        self._set_window_pos = set_window_pos
        if not self.register_ghost_hotkey(self.config["ghost_hotkey"]):
            self.notify(tr("Горячая клавиша занята"), seconds=3.0, force=True)

        self.start_services()
        self.sync_volume_from_system()

        self.anim_timer = QTimer(self)
        self.anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.anim_timer.timeout.connect(self.tick)
        self.anim_timer.start(self.frame_interval_ms())

        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self.update_metrics)
        self.metrics_timer.start(1000)

        self.timer_persist_timer = QTimer(self)
        self.timer_persist_timer.setSingleShot(True)
        self.timer_persist_timer.timeout.connect(self.persist_config)

        self.visibility_timer = QTimer(self)
        self.visibility_timer.timeout.connect(self.enforce_visibility)
        self.visibility_timer.start(350)

        self.settings_window = None
        if not os.path.exists(CONFIG_FILE):
            try:
                save_config(self.config)
            except OSError:
                logging.exception("Could not create the initial settings file")

    def notify(self, text, seconds=2.5, force=False, keep_width=False):
        """Show a short message in the collapsed island.

        Do-not-disturb mode hides everything except messages the user must
        see (force=True). `keep_width` shows it without resizing the island,
        for messages that last a blink.
        """
        if self.config["dnd_mode"] and not force:
            return
        self.notification_text = text
        self.notification_until = time.perf_counter() + seconds
        self.notification_keeps_width = keep_width
        self.mark_moving()
        self.update()

    def update_metrics(self):
        try:
            self.cached_cpu_percent = int(psutil.cpu_percent())
            self.cached_memory_percent = int(psutil.virtual_memory().percent)
        except (OSError, RuntimeError):
            logging.exception("Could not update system metrics")
        # Other programs change the volume too; refresh it now and then, and
        # every second while the volume control is likely to be looked at.
        self.volume_sync_countdown -= 1
        if self.volume_sync_countdown <= 0 or self.t > 0.5:
            self.volume_sync_countdown = 5
            self.sync_volume_from_system()
        self.update()

    def sync_ping_worker(self):
        if self.config["center_zone"] == "Ping":
            self.ping.enabled.set()
        else:
            self.ping.enabled.clear()

    # ----- Timer and stopwatch -------------------------------------------

    def update_timer_clock(self, now):
        """Derive the timer display from the clock, so it cannot drift."""
        if not self.timer_active:
            return
        if self.timer_mode == "Timer":
            remaining = self.timer_deadline - now
            if remaining <= 0:
                self.finish_timer()
            else:
                self.timer_remaining = remaining
                self.timer_seconds_left = math.ceil(remaining)
        else:
            self.stopwatch_seconds = int(
                self.stopwatch_elapsed + now - self.stopwatch_started
            )

    def finish_timer(self):
        self.timer_active = False
        self.timer_session_visible = False
        self.timer_mini_visible = False
        self.config["timer_configured"] = False
        self.reset_countdown()
        self.timer_persist_timer.start(500)
        if not self.config["dnd_mode"]:
            user32_function(
                "MessageBeep",
                [ctypes.wintypes.UINT],
                ctypes.wintypes.BOOL,
            )(0x00000030)
        # The user asked for this alert, so it shows even in do-not-disturb.
        self.notify(tr("Таймер завершён"), seconds=5.0, force=True)

    def reset_countdown(self):
        self.timer_remaining = float(self.config["timer_duration_seconds"])
        self.timer_seconds_left = int(self.timer_remaining)

    # ----- What is playing -----------------------------------------------

    def bridge_is_fresh(self, now):
        return (
            self.bridge_media.has_track
            and now - self.bridge_media.updated < BRIDGE_FRESH_SEC
        )

    def bridge_can_command(self):
        return self.media_source == "bridge" and self.bridge_is_fresh(
            time.perf_counter()
        )

    def on_bridge_report(self, state):
        self.bridge_media = state
        self.refresh_media()

    def on_system_report(self, state):
        self.system_media = state
        self.refresh_media()

    def on_outdated_extension(self, usable):
        """The browser runs an older extension. `usable`: it still works,
        only without what the current one adds."""
        if usable:
            if not self.legacy_extension_notified:
                self.legacy_extension_notified = True
                logging.info("The browser runs the previous version of the extension")
                self.notify(tr("Обновите расширение в браузере"), seconds=6.0)
            return
        now = time.perf_counter()
        if (
            self.outdated_extension_notified is not None
            and now - self.outdated_extension_notified < 120
        ):
            return
        self.outdated_extension_notified = now
        logging.warning(
            "The browser still runs an old Default Island extension; "
            "restart the browser to load the current one"
        )
        self.notify(tr("Перезапустите браузер (расширение)"), seconds=7.0, force=True)

    def pick_media_source(self, now):
        """Choose between the browser extension and Windows media sessions.

        The extension is the most precise source, but it only knows about
        SoundCloud. When another player is the one actually playing, that
        player is shown instead.
        """
        if not self.bridge_is_fresh(now):
            self.other_player_since = None
            return "system"
        bridge, system = self.bridge_media, self.system_media
        other_player = system.has_track and not same_track(bridge, system)
        if bridge.playing or not other_player:
            self.other_player_since = None
            return "bridge"
        if system.playing:
            # Windows learns about a pause a little later than the extension;
            # wait a moment before believing that something else is playing.
            if self.other_player_since is None:
                self.other_player_since = now
            if now - self.other_player_since > 0.8:
                return "system"
            return self.media_source
        self.other_player_since = None
        # Nothing is playing: stay with the player that was shown last.
        return self.media_source

    def refresh_media(self):
        now = time.perf_counter()
        self.last_media_refresh = now
        source = self.pick_media_source(now)
        media = self.bridge_media if source == "bridge" else self.system_media
        track = (media.title, media.artist)
        source_changed = source != self.media_source
        track_changed = source_changed or track != self.shown_track
        self.media_source = source
        self.shown_track = track
        if source_changed:
            # Unconfirmed clicks were meant for the other player.
            self.pending_playing = None
            self.pending_repeat = None
        if track_changed:
            self.pending_seek = None
            self.pending_liked = None
            self.on_track_changed()

        self.title = media.title
        self.artist = media.artist
        self.duration_sec = media.duration
        self.playback_rate = media.rate

        playing = media.playing
        if self.pending_playing is not None:
            wanted, deadline, route = self.pending_playing
            if playing == wanted:
                self.pending_playing = None
            elif now < deadline:
                playing = wanted
            elif route == "bridge" and same_track(self.bridge_media, self.system_media):
                # The page did not do it (a browser may block playback that
                # starts without a click in the tab); ask Windows instead.
                self.worker.request("set_playing", wanted)
                self.pending_playing = (wanted, now + 1.5, "system")
                playing = wanted
            else:
                self.pending_playing = None
        self.is_playing = playing
        was_ticking = self.clock_ticking
        self.clock_ticking = playing and media.ticking and media.duration > 0

        self.repeat_available = media.repeat is not None
        repeat = media.repeat or 0
        if self.pending_repeat is not None:
            wanted, deadline = self.pending_repeat
            if not self.repeat_available or repeat == wanted or now >= deadline:
                self.pending_repeat = None
            else:
                repeat = wanted
        self.repeat_mode = repeat

        # Only the extension knows about likes, and only on some sites.
        liked = media.liked if source == "bridge" else None
        if self.pending_liked is not None:
            wanted, deadline = self.pending_liked
            if liked is None or liked == wanted or now >= deadline:
                self.pending_liked = None
            else:
                liked = wanted
        self.liked = liked
        self.media_site = media.site if source == "bridge" else ""

        # A clock that just started takes the player's position exactly.
        self.sync_position(media, now, track_changed or not was_ticking)
        self.sync_cover()
        # Reports arrive several times a second even while nothing changes;
        # repaint only for what is drawn, so an idle island stays idle.
        view = (
            self.title, self.artist, self.is_playing, self.clock_ticking,
            self.repeat_available, self.repeat_mode, self.liked, self.media_site,
            self.duration_sec, round(self.position_sec, 1),
            self.cover_pixmap.cacheKey() if self.cover_pixmap is not None else None,
            self.auto_accent_color.rgba(),
        )
        if view != self._media_view:
            self._media_view = view
            self.mark_moving()

    def sync_position(self, media, now, track_changed):
        """Bring the local playback clock in line with the player."""
        if media.duration <= 0:
            self.position_sec = 0.0
            self.pending_seek = None
            return
        if self.seeking_bar:
            return  # the user is dragging the progress bar
        reported = media.position_at(now)
        if self.pending_seek is not None:
            target, started = self.pending_seek
            expected = target
            if self.clock_ticking:
                expected += (now - started) * media.rate
            if abs(reported - expected) > 1.5 and now - started < OPTIMISTIC_HOLD_SEC:
                return  # the player has not caught up with the seek yet
            self.pending_seek = None
        tolerance = POSITION_RESYNC_COARSE_SEC if media.coarse else POSITION_RESYNC_SEC
        if (
            track_changed
            or not self.clock_ticking
            or not media.ticking
            or abs(reported - self.position_sec) > tolerance
        ):
            self.position_sec = reported
        elif not media.coarse and abs(reported - self.position_sec) > 0.08:
            # Small drift (buffering, a busy player) is pulled in gently, so
            # the clock and the lyrics stay with the music without a jump.
            self.position_sec += (reported - self.position_sec) * 0.3

    def toggle_playback(self):
        now = time.perf_counter()
        wanted = not self.is_playing
        if self.bridge_can_command():
            self.bridge.queue_command({"type": "set_playing", "value": wanted})
            route = "bridge"
        else:
            self.worker.request("set_playing", wanted)
            route = "system"
        # Show the result at once; reports that still contradict it are
        # ignored until the player confirms or the wait runs out.
        self.pending_playing = (wanted, now + OPTIMISTIC_HOLD_SEC, route)
        self.is_playing = wanted
        if not wanted:
            self.clock_ticking = False
        self.update()

    def skip_track(self, direction):
        """direction is "next" or "prev"."""
        if self.bridge_can_command():
            self.bridge.queue_command({"type": direction})
        else:
            self.worker.request(direction)
        if self.duration_sec > 0:
            self.position_sec = 0.0
            self.pending_seek = (0.0, time.perf_counter())
        self.update()

    def commit_seek(self):
        """Send the position chosen on the progress bar to the player."""
        target = self.position_sec
        self.pending_seek = (target, time.perf_counter())
        if self.bridge_can_command():
            self.bridge.queue_command({"type": "seek", "sec": round(target, 3)})
        else:
            self.worker.request("seek", target)

    def cycle_repeat(self):
        if not (self.repeat_available and self.bridge_can_command()):
            return  # only SoundCloud (through the extension) supports it
        mode = (self.repeat_mode + 1) % 3
        self.pending_repeat = (mode, time.perf_counter() + OPTIMISTIC_HOLD_SEC)
        self.repeat_mode = mode
        self.bridge.queue_command({"type": "repeat", "mode": mode})
        self.update()

    # ----- Cover art -------------------------------------------------------

    def on_system_cover(self, image, track):
        if image.isNull():
            self.system_cover = None
        else:
            self.system_cover = (
                track,
                QPixmap.fromImage(image),
                self.accent_from_image(image),
            )
        self.sync_cover()
        self.update()

    def sync_cover(self):
        """Show the artwork only while it belongs to the track on screen."""
        cover = self.system_cover
        system_track = f"{self.system_media.title}\n{self.system_media.artist}"
        usable = (
            cover is not None
            and cover[0] == system_track
            and (
                self.media_source == "system"
                or same_track(self.bridge_media, self.system_media)
            )
        )
        if usable:
            self.cover_pixmap = cover[1]
            self.auto_accent_color = cover[2] or QColor(DEFAULT_ACCENT)
            self.cover_shown_for = self.shown_track
        elif self.cover_shown_for != self.shown_track:
            self.cover_pixmap = None
            self.auto_accent_color = QColor(DEFAULT_ACCENT)
            self.cover_shown_for = self.shown_track

    def current_accent_color(self):
        preset = self.config.get("accent_color", "Auto")
        if preset == "Auto":
            return QColor(self.auto_accent_color)
        return QColor(ACCENT_PRESETS.get(preset, DEFAULT_ACCENT))

    def accent_from_image(self, image):
        """Dominant colour of the artwork, muted; None for a grey image."""
        sample = image.scaled(
            12,
            12,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        hue_bins = {}
        for y in range(sample.height()):
            for x in range(sample.width()):
                color = sample.pixelColor(x, y)
                if color.alpha() < 128 or color.hue() < 0:
                    continue
                if color.saturation() < 70 or color.value() < 80:
                    continue
                bucket = int(color.hue() / 30)
                hue_bins.setdefault(bucket, []).append(color)

        if not hue_bins:
            return None
        dominant_bin = max(hue_bins, key=lambda bucket: len(hue_bins[bucket]))
        colors = hue_bins[dominant_bin]
        saturation = round(sum(color.saturation() for color in colors) / len(colors))
        value = round(sum(color.value() for color in colors) / len(colors))
        return soften_accent(QColor.fromHsv(dominant_bin * 30 + 15, saturation, value))

    def apply_settings(self):
        """Put freshly saved settings into effect."""
        set_language(self.config["language"])
        self.look = make_look(self.config)
        self._text_width_cache.clear()
        self.sync_ping_worker()
        self._volume_controls = None
        self.sync_volume_from_system()
        self.videos.hide = not self.config["youtube_video"]
        wake = getattr(self.worker, "wake", None)
        if wake is not None:
            wake.set()  # show or hide a video at once
        if not self.config["timer_button_enabled"]:
            self.timer_panel_open = False
        self.anim_timer.setInterval(self.frame_interval_ms())
        self.extras_apply_settings()
        self.apply_geometry()
        self.update()

    # ----- Collapsed pill text ------------------------------------------

    TRACK_SEPARATOR = "  ·  "

    def display_title(self):
        return self.title

    def idle(self):
        """True while no player has a track: the island shows the time, the
        weather or the computer instead (the "idle_view" setting)."""
        return not self.title

    def collapsed_text(self):
        """The line shown in the middle of the collapsed island."""
        if self.notification_showing():
            return self.notification_text
        if self.timer_session_visible:
            return self.format_timer_hms(
                self.timer_seconds_left
                if self.timer_mode == "Timer"
                else self.stopwatch_seconds
            )
        if self.calling():
            return self.call_label()
        mode = self.config["center_zone"]
        if mode == "Weather":
            return self.weather_text() or tr("Погода: —")
        if mode == "Ping":
            if not self.ping.measured:
                return tr("Пинг: …")
            if self.ping.value_ms is None:
                return tr("Пинг: нет сети")
            return tr("Пинг: {ms} мс").format(ms=round(self.ping.value_ms))
        if mode == "CPU / RAM":
            return f"CPU: {self.cached_cpu_percent}% | RAM: {self.cached_memory_percent}%"
        if mode == "GPU":
            return self.gpu_text()
        if mode == "Time":
            return time.strftime("%H:%M")
        if self.idle():
            return self.idle_text()
        line = self.lyrics_line()
        if line:
            return line
        return self.TRACK_SEPARATOR.join(
            part for part in (self.display_title(), self.artist) if part
        )

    def collapsed_shows_track(self):
        """True while the collapsed line is "title · artist"."""
        return (
            not self.notification_showing()
            and not self.timer_session_visible
            and self.config["center_zone"] == "Track / Lyrics"
            and not self.idle()
            and not self.calling()
            and not self.lyrics_line()
        )

    def idle_tile_shown(self):
        """The clock or weather tile stands where the artwork would be;
        the empty capsule ("blank") has none until the island opens."""
        return self.config["idle_view"] != "blank" or self.config["center_zone"] != "Track / Lyrics"

    def collapsed_margins(self):
        """Space taken by the cover on the left and the indicator on the right."""
        idle = self.idle()
        calling = self.calling() and not self.timer_session_visible
        left = (
            38.0
            if calling
            or self.config["left_zone"] == "Album Art" and (not idle or self.idle_tile_shown())
            else 16.0
        )
        if calling:
            right = 22.0 + self.call_timer_width()
        elif (
            self.timer_session_visible
            or (self.config["right_zone"] != "None" and not idle)
            or self.downloads_showing()
        ):
            right = 42.0
        else:
            right = 16.0
        return left, right + self.inside_dots_width()

    def expanded_height_target(self):
        """The expanded island is taller while it has more rows to show."""
        if self.timer_panel_open:
            return EXPANDED_HEIGHT_TIMER
        panel = self.panel_height()
        if panel:
            return panel
        height = EXPANDED_HEIGHT + self.extra_rows_height()
        if self.config["timer_button_enabled"] and self.timer_mini_visible:
            height += EXPANDED_HEIGHT_TIMER - EXPANDED_HEIGHT
        return height

    def text_width(self, text):
        width = self._text_width_cache.get(text)
        if width is None:
            if len(self._text_width_cache) > 200:
                self._text_width_cache.clear()
            width = QFontMetricsF(self.collapsed_font).horizontalAdvance(text)
            self._text_width_cache[text] = width
        return width

    def collapsed_width_target(self):
        low, high = COLLAPSED_WIDTH_RANGE
        if self.hud_showing():
            return HUD_WIDTH
        if self.notification_showing() and self.notification_keeps_width:
            return self.col_w_target
        follows_text = (
            self.config["collapsed_title"] == "full" or not self.collapsed_shows_track()
        )
        if not follows_text:
            return SHORT_TITLE_WIDTH
        left, right = self.collapsed_margins()
        text = self.collapsed_text()
        if not text and self.idle():
            low = BLANK_WIDTH  # the empty capsule is small
        wanted = self.text_width(text) + left + right + 6.0
        return max(low, min(high, wanted))

    def showEvent(self, event):
        super().showEvent(event)
        if self.shadow is not None:
            self.shadow.follow(self.geometry())
            self.shadow.show()
            self.place_shadow()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.shadow is not None:
            self.shadow.hide()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.shadow is not None:
            self.shadow.follow(self.geometry())

    def place_shadow(self):
        """Keep the shadow window directly under the island's window."""
        if self.shadow is None or not self.shadow.isVisible():
            return
        self._set_window_pos(
            int(self.shadow.winId()),
            int(self.winId()),
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010,
        )

    def sync_shadow(self):
        """Tell the shadow window what shapes to put a shadow under."""
        if self.shadow is None:
            return
        scale = self.view_scale()
        offset = self.view_offset(scale)
        strength = self.look.shadow
        pill = self.current_pill_rect()
        shapes = []
        for rect, radius, weight in [(pill, self.pill_radius(pill), 1.0)] + self.side_shapes():
            placed = self.window_rect(rect, scale, offset)
            shapes.append(
                (
                    round(placed.x(), 1),
                    round(placed.y(), 1),
                    round(placed.width(), 1),
                    round(placed.height(), 1),
                    round(radius * scale, 1),
                    round(strength * weight, 2),
                )
            )
        self.shadow.set_shapes(tuple(shapes), round(scale, 3))

    def enforce_visibility(self):
        if self.settings_window and self.settings_window.isVisible():
            return

        if (
            self.config["hide_fullscreen"]
            and not self.positioning
            and self.foreground_is_fullscreen()
        ):
            if self.isVisible():
                self.hide()
            return

        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()

        result = self._set_window_pos(
            int(self.winId()),
            ctypes.c_void_p(-1),
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010 | 0x0040,
        )
        if not result:
            logging.debug("Could not reassert topmost window state")
        self.place_shadow()

    def register_ghost_hotkey(self, hotkey=None):
        old_hotkey = self.config.get("ghost_hotkey", "Ctrl+Shift+G")
        requested = hotkey or old_hotkey
        modifiers, virtual_key = parse_hotkey(requested)
        register_hotkey = user32_function(
            "RegisterHotKey",
            [
                ctypes.wintypes.HWND,
                ctypes.wintypes.INT,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
            ],
            ctypes.wintypes.BOOL,
        )
        unregister = user32_function(
            "UnregisterHotKey",
            [ctypes.wintypes.HWND, ctypes.wintypes.INT],
            ctypes.wintypes.BOOL,
        )
        hwnd = int(self.winId())
        hotkey_id = 1
        if self._registered_hotkey:
            unregister(hwnd, hotkey_id)
            self._registered_hotkey = False
        registered = bool(
            register_hotkey(hwnd, hotkey_id, modifiers | 0x4000, virtual_key)
        )
        if registered:
            self._registered_hotkey = True
            return True

        if requested != old_hotkey:
            old_modifiers, old_key = parse_hotkey(old_hotkey)
            self._registered_hotkey = bool(
                register_hotkey(hwnd, hotkey_id, old_modifiers | 0x4000, old_key)
            )
        return False

    def nativeEvent(self, event_type, message):
        try:
            msg = ctypes.cast(
                int(message), ctypes.POINTER(ctypes.wintypes.MSG)
            ).contents
            if msg.message == 0x0312 and msg.wParam == 1:
                self.toggle_ghost_mode()
                return True, 0
            return False, 0
        except Exception:
            logging.exception("Could not process a native Windows message")
            return False, 0

    def foreground_is_fullscreen(self):
        get_foreground = user32_function(
            "GetForegroundWindow", [], ctypes.wintypes.HWND
        )
        is_window_visible = user32_function(
            "IsWindowVisible", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL
        )
        get_window_rect = user32_function(
            "GetWindowRect",
            [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)],
            ctypes.wintypes.BOOL,
        )
        get_window_style = user32_function(
            "GetWindowLongPtrW",
            [ctypes.wintypes.HWND, ctypes.wintypes.INT],
            ctypes.c_ssize_t,
        )
        is_zoomed = user32_function(
            "IsZoomed", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL
        )
        get_class_name = user32_function(
            "GetClassNameW",
            [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.wintypes.INT],
            ctypes.wintypes.INT,
        )
        hwnd = get_foreground()
        if not hwnd or hwnd == int(self.winId()) or not is_window_visible(hwnd):
            return False
        class_name = ctypes.create_unicode_buffer(128)
        get_class_name(hwnd, class_name, len(class_name))
        if class_name.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        if is_zoomed(hwnd):
            return False
        style = get_window_style(hwnd, -16)
        if style & (0x00C00000 | 0x00040000):
            return False
        rect = ctypes.wintypes.RECT()
        if not get_window_rect(hwnd, ctypes.byref(rect)):
            return False

        # Compare with the monitor in Windows' own pixels: Qt's screen sizes
        # are scaled by the display zoom and would not match.
        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.wintypes.DWORD),
            ]

        monitor_from_window = user32_function(
            "MonitorFromWindow",
            [ctypes.wintypes.HWND, ctypes.wintypes.DWORD],
            ctypes.wintypes.HANDLE,
        )
        get_monitor_info = user32_function(
            "GetMonitorInfoW",
            [ctypes.wintypes.HANDLE, ctypes.POINTER(MONITORINFO)],
            ctypes.wintypes.BOOL,
        )
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        monitor_default_to_nearest = 2
        monitor = monitor_from_window(hwnd, monitor_default_to_nearest)
        if not monitor or not get_monitor_info(monitor, ctypes.byref(info)):
            return False
        # A full-screen window on another monitor does not cover the island.
        if monitor != monitor_from_window(int(self.winId()), monitor_default_to_nearest):
            return False
        bounds = info.rcMonitor
        return (
            rect.right - rect.left >= (bounds.right - bounds.left) * 0.96
            and rect.bottom - rect.top >= (bounds.bottom - bounds.top) * 0.96
            and abs(rect.left - bounds.left) <= 12
            and abs(rect.top - bounds.top) <= 12
        )

    def toggle_ghost_mode(self):
        hwnd = int(self.winId())
        get_style = user32_function(
            "GetWindowLongPtrW",
            [ctypes.wintypes.HWND, ctypes.wintypes.INT],
            ctypes.c_ssize_t,
        )
        set_style = user32_function(
            "SetWindowLongPtrW",
            [ctypes.wintypes.HWND, ctypes.wintypes.INT, ctypes.c_ssize_t],
            ctypes.c_ssize_t,
        )
        ex_style = get_style(hwnd, -20)
        self.ghost_mode = not bool(ex_style & 0x00000020)
        next_style = (
            ex_style | 0x00000020
            if self.ghost_mode
            else ex_style & ~0x00000020
        )
        set_style(hwnd, -20, next_style)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            self.ghost_mode,
        )
        if self.ghost_mode:
            if self.positioning:
                self.finish_positioning()
            # A drag in progress will never see its mouse release now.
            self.seeking_bar = False
            self.dragging_vol_slider = False
            self.dragging_enabled = False
            self.unsetCursor()
            self.show_position_guide(False)
            self.timer_panel_open = False
            self.close_panels()
            self.keep_visible_until_cursor_leaves = False
            self.settings_restore_until = 0.0
            self.hovered = False
            self.set_openness(0.0)
        refreshed = self._set_window_pos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
        )
        actual_style = get_style(hwnd, -20)
        style_matches = bool(actual_style & 0x00000020) == self.ghost_mode
        if not refreshed or not style_matches:
            logging.error(
                "Could not apply Ghost mode hit testing (style_matches=%s, refreshed=%s)",
                style_matches,
                bool(refreshed),
            )
            self.notify(tr("Не удалось переключить режим призрака"), seconds=2.5, force=True)
        else:
            self.notify(
                tr("Режим призрака включён") if self.ghost_mode else tr("Режим призрака выключен"),
                seconds=1.5,
            )
        self.update()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self):
        """Release everything the app holds; safe to call more than once."""
        if self._registered_hotkey:
            unregister = user32_function(
                "UnregisterHotKey",
                [ctypes.wintypes.HWND, ctypes.wintypes.INT],
                ctypes.wintypes.BOOL,
            )
            unregister(int(self.winId()), 1)
            self._registered_hotkey = False
        if self.timer_persist_timer.isActive():
            self.timer_persist_timer.stop()
            self.persist_config()
        self.worker.stop()
        if self.videos.tabs is not None:
            self.videos.tabs.stop()
        self.ping.stop()
        self.bridge.stop()
        self.stop_extra_services()
        if self.shadow is not None:
            self.shadow.close()

    def open_settings(self):
        if self.settings_window is not None:
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        # A new dialog every time, so it always starts from the current settings.
        self.settings_window = SettingsWindow(self)
        self.settings_window.finished.connect(self.on_settings_closed)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()
        self.mark_moving()

    def tick(self):
        now = time.perf_counter()
        dt = min(0.1, max(0.0, now - self.last_tick_time))
        self.last_tick_time = now
        # Reports arrive several times a second; this also covers the cases
        # no report announces: a source going silent, a click timing out.
        if now - self.last_media_refresh > 0.25:
            self.refresh_media()
        shown_seconds = (self.timer_seconds_left, self.stopwatch_seconds)
        self.update_timer_clock(now)
        if shown_seconds != (self.timer_seconds_left, self.stopwatch_seconds):
            self.mark_moving()
        playback_active = self.is_playing
        notice_was_showing = self.notification_text and self.notification_until > 0.0
        if notice_was_showing and not self.notification_showing():
            self.notification_until = 0.0
            self.mark_moving()

        self.col_w_target = self.collapsed_width_target()
        self.col_w = self.approach(self.col_w, self.col_w_target, 0.2, dt)
        self.exp_h = self.approach(self.exp_h, self.expanded_height_target(), 0.24, dt)

        cursor_pos = self.design_pos(QPointF(self.mapFromGlobal(QCursor.pos())))
        pill_rect = self.current_pill_rect()
        expanded_zone = self.expanded_zone()
        on_side_button = self.side_button_at(cursor_pos)
        is_in_expanded = expanded_zone.contains(cursor_pos) and on_side_button is None
        dragging = self.seeking_bar or self.dragging_vol_slider or self.shelf_dragging()

        if self.ghost_mode or self.positioning or self.dragging_enabled:
            self.hovered = False
        elif self.ringing():
            # A ringing call stays a card with its two buttons; the player
            # does not open over it.
            self.hovered = False
        elif self.settings_window and self.settings_window.isVisible():
            self.hovered = True
        elif now < self.settings_restore_until:
            self.hovered = True
            if is_in_expanded:
                self.settings_restore_until = 0.0
                self.keep_visible_until_cursor_leaves = True
        elif self.timer_panel_open or self.panel_open():
            self.hovered = True
            if not is_in_expanded and not dragging and self.panel_open():
                # A panel that is not the timer closes when the mouse leaves.
                self.panel_left_at = self.panel_left_at or now
                if now - self.panel_left_at > 0.6:
                    self.close_panels()
            else:
                self.panel_left_at = 0.0
        elif self.keep_visible_until_cursor_leaves:
            self.hovered = is_in_expanded
            if not is_in_expanded:
                self.keep_visible_until_cursor_leaves = False
        elif self.t > 0.05:
            self.hovered = dragging or is_in_expanded or self.wants_open()
        else:
            self.hovered = (
                dragging
                or self.wants_open()
                or pill_rect.adjusted(-6, -6, 6, 6).contains(cursor_pos)
            )

        self.hovered_btn = None
        self.hovered_bar = False
        if self.ghost_mode:
            pass
        elif self.ringing() and self.t < 0.3:
            self.hovered_btn = self.call_button_at(cursor_pos)
        elif on_side_button is not None and self.t <= 0.15:
            self.hovered_btn = on_side_button
        elif self.t > 0.6 and not self.dragging_enabled:
            if self.timer_panel_open:
                for rect_name, animation_name in (
                    ("timer_start_rect", "timer_start"),
                    ("timer_pause_rect", "timer_pause"),
                    ("timer_stop_rect", "timer_stop"),
                    ("timer_timer_mode_rect", "timer_mode_timer"),
                    ("timer_stopwatch_mode_rect", "timer_mode_stopwatch"),
                    ("timer_hour_rect", "timer_hour"),
                    ("timer_minute_rect", "timer_minute"),
                    ("timer_second_rect", "timer_second"),
                    ("timer_close_rect", "timer_close"),
                ):
                    if getattr(self, rect_name).contains(cursor_pos):
                        self.hovered_btn = animation_name
                        break
            elif self.panel_open():
                self.hovered_btn = self.extra_button_at(cursor_pos)
            else:
                timer_row = self.config["timer_button_enabled"]
                if timer_row and self.timer_mini_open_rect.contains(cursor_pos):
                    self.hovered_btn = "timer_mini_open"
                elif timer_row and self.timer_mini_start_rect.contains(cursor_pos):
                    self.hovered_btn = "timer_mini_start"
                elif timer_row and self.timer_mini_pause_rect.contains(cursor_pos):
                    self.hovered_btn = "timer_mini_pause"
                elif timer_row and self.timer_mini_stop_rect.contains(cursor_pos):
                    self.hovered_btn = "timer_mini_stop"
                elif (
                    self.vol_popup_anim > 0.2
                    and not self.vol_popup_rect.isEmpty()
                    and self.vol_popup_rect.contains(cursor_pos)
                ):
                    self.show_volume_popup(0.6)
                    self.hovered_btn = "vol"
                elif not self.track_bar_rect.isEmpty() and self.track_bar_rect.adjusted(
                    -4, -8, 4, 8
                ).contains(cursor_pos):
                    self.hovered_bar = True
                elif self.vol_rect.contains(cursor_pos):
                    self.hovered_btn = "vol"
                    self.show_volume_popup(0.6)
                else:
                    for rect_name, animation_name in (
                        ("prev_rect", "prev"),
                        ("play_rect", "play"),
                        ("next_rect", "next"),
                        ("repeat_rect", "repeat"),
                        ("like_rect", "like"),
                        ("gear_rect", "gear"),
                        ("dnd_rect", "dnd"),
                        ("timer_btn_rect", "timer_btn"),
                    ):
                        if getattr(self, rect_name).contains(cursor_pos):
                            self.hovered_btn = animation_name
                            break
                    else:
                        self.hovered_btn = self.extra_button_at(cursor_pos)

        for key, value in self.btn_hover_anim.items():
            goal = 1.0 if self.hovered_btn == key else 0.0
            if value != goal:
                self.btn_hover_anim[key] = self.approach(value, goal, 0.28, dt)

        vol_goal = 1.0 if (now < self.volume_overlay_until or self.dragging_vol_slider) else 0.0
        self.vol_popup_anim = self.approach(self.vol_popup_anim, vol_goal, 0.28, dt)
        bar_goal = 1.0 if (self.hovered_bar or self.seeking_bar) else 0.0
        self.bar_hover_anim = self.approach(self.bar_hover_anim, bar_goal, 0.3, dt)
        self.playing_anim = self.approach(
            self.playing_anim, 1.0 if playback_active else 0.0, 0.08, dt
        )

        self.step_spring(1.0 if self.hovered else 0.0, dt)
        self.update_visualizer(playback_active, dt)

        # The clock runs between reports only while the player says the
        # position is really moving (not paused, buffering or being dragged).
        if self.clock_ticking and not self.seeking_bar and self.duration_sec > 0:
            self.position_sec = min(
                self.duration_sec, self.position_sec + dt * self.playback_rate
            )
            if self.t > 0.02:
                self.mark_moving()

        self.extras_tick(now, dt, cursor_pos)

        if self._moving:
            self._moving = False
            self._still_since = now
            if self.anim_timer.interval() != self.frame_interval_ms():
                self.anim_timer.setInterval(self.frame_interval_ms())
            self.sync_shadow()
            self.update()
        elif (
            now - self._still_since > 1.0
            and self.anim_timer.interval() != IDLE_INTERVAL_MS
        ):
            # Nothing is moving: stop drawing and only watch the mouse.
            self.anim_timer.setInterval(max(IDLE_INTERVAL_MS, self.frame_interval_ms()))

    def format_time(self, sec):
        sec = max(0, int(sec))
        if sec >= 3600:
            return f"{sec // 3600}:{(sec // 60) % 60:02d}:{sec % 60:02d}"
        return f"{sec // 60}:{sec % 60:02d}"

    def format_timer_hms(self, sec):
        sec = max(0, int(sec))
        hours = min(99, sec // 3600)
        minutes = (sec // 60) % 60
        seconds = sec % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    # ----- Drawing: shared pieces ----------------------------------------

    def draw_text(self, painter, rect, flags, text, color):
        """Text in the current font; on glass it gets a soft shadow so it
        stays readable over whatever shows through."""
        shadow = self.look.text_shadow(color.alpha())
        if shadow is not None:
            painter.setPen(shadow)
            painter.drawText(rect.translated(0.0, 1.0), flags, text)
        painter.setPen(color)
        painter.drawText(rect, flags, text)

    def draw_button_hover(self, painter, rect, hover_val, alpha, radius=None):
        """A soft highlight that fades in under the control the mouse is on."""
        if hover_val <= 0.01:
            return
        if radius is None:
            radius = min(rect.width(), rect.height()) / 2.0
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.look.veil(hover_val * alpha * 0.11))
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

    def draw_pill_button(self, painter, rect, label, alpha, hover_val, primary=False):
        radius = rect.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setFont(ui_font(9, QFont.Weight.DemiBold))
        if primary:
            fill = mix_colors(self.current_accent_color(), QColor(255, 255, 255), 0.18 * hover_val)
            painter.setBrush(with_alpha(fill, alpha * 0.94))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QColor(24, 21, 18, alpha))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        else:
            painter.setBrush(self.look.veil(alpha * (0.08 + 0.07 * hover_val)))
            painter.drawRoundedRect(rect, radius, radius)
            self.draw_text(
                painter,
                rect,
                Qt.AlignmentFlag.AlignCenter,
                label,
                with_alpha(self.look.primary, alpha),
            )

    # ----- Drawing: timer ------------------------------------------------

    def timer_progress(self):
        """Share of the countdown still left; None while the stopwatch is shown."""
        total = self.config["timer_duration_seconds"]
        if self.timer_mode != "Timer" or total <= 0:
            return None
        return max(0.0, min(1.0, self.timer_remaining / total))

    def draw_progress_ring(self, painter, center, radius, alpha, width=1.8):
        """A ring that empties as the countdown runs out."""
        ring = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.look.veil(alpha * 0.16), width))
        painter.drawEllipse(ring)
        painter.setPen(round_pen(with_alpha(self.current_accent_color(), alpha), width))
        progress = self.timer_progress()
        if progress is None:
            # The stopwatch has no end to count towards: a short arc circles.
            turn = (time.perf_counter() % 2.0) / 2.0 if self.timer_active else 0.0
            painter.drawArc(ring, int((90.0 - turn * 360.0) * 16), -90 * 16)
        elif progress > 0.0:
            painter.drawArc(ring, 90 * 16, -int(progress * 360 * 16))

    def draw_clock_hands(self, painter, center, size, color, width):
        painter.setPen(round_pen(color, width))
        painter.drawLine(QPointF(center.x(), center.y() - size), center)
        painter.drawLine(center, QPointF(center.x() + size * 0.72, center.y() + size * 0.42))

    def draw_timer_view(self, painter, pill, alpha):
        accent = self.current_accent_color()
        hover = self.btn_hover_anim
        self.timer_view_rect = pill
        mode_y = pill.top() + 14.0
        self.timer_timer_mode_rect = QRectF(pill.left() + 18.0, mode_y, 72.0, 26.0)
        self.timer_stopwatch_mode_rect = QRectF(pill.left() + 90.0, mode_y, 96.0, 26.0)
        self.timer_close_rect = QRectF(pill.right() - 44.0, mode_y, 26.0, 26.0)

        # The two modes share one segmented control.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.look.veil(alpha * 0.06))
        painter.drawRoundedRect(
            self.timer_timer_mode_rect.united(self.timer_stopwatch_mode_rect), 13.0, 13.0
        )
        painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
        for rect, mode, label, hover_key in (
            (self.timer_timer_mode_rect, "Timer", tr("Таймер"), "timer_mode_timer"),
            (self.timer_stopwatch_mode_rect, "Stopwatch", tr("Секундомер"), "timer_mode_stopwatch"),
        ):
            selected = self.timer_mode == mode
            segment = rect.adjusted(2.0, 2.0, -2.0, -2.0)
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.look.veil(alpha * 0.14))
                painter.drawRoundedRect(segment, 11.0, 11.0)
            else:
                self.draw_button_hover(painter, segment, hover[hover_key], alpha, 11.0)
            self.draw_text(
                painter,
                rect,
                Qt.AlignmentFlag.AlignCenter,
                label,
                with_alpha(self.look.primary if selected else self.look.secondary, alpha),
            )

        self.draw_button_hover(painter, self.timer_close_rect, hover["timer_close"], alpha)
        close_center = self.timer_close_rect.center()
        painter.setPen(
            round_pen(
                with_alpha(
                    mix_colors(self.look.secondary, self.look.primary, hover["timer_close"]), alpha
                ),
                1.6,
            )
        )
        for direction in (1.0, -1.0):
            painter.drawLine(
                QPointF(close_center.x() - 4.0, close_center.y() - 4.0 * direction),
                QPointF(close_center.x() + 4.0, close_center.y() + 4.0 * direction),
            )

        clock_center = QPointF(pill.left() + 68.0, pill.top() + 81.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(accent, alpha * 0.07))
        painter.drawEllipse(clock_center, 20.0, 20.0)
        self.draw_progress_ring(painter, clock_center, 20.0, alpha, 2.0)
        self.draw_clock_hands(painter, clock_center, 10.0, with_alpha(self.look.primary, alpha), 2.0)

        total_seconds = max(
            0,
            self.timer_seconds_left
            if self.timer_mode == "Timer"
            else self.stopwatch_seconds,
        )
        fields = (
            ("hour", tr("ЧАС"), min(99, total_seconds // 3600), 99),
            ("minute", tr("МИН"), (total_seconds // 60) % 60, 59),
            ("second", tr("СЕК"), total_seconds % 60, 59),
        )
        field_width = 66.0
        field_height = 62.0
        field_gap = 8.0
        fields_left = pill.left() + 108.0
        field_top = pill.top() + 50.0
        for index, (field_name, label, value, maximum) in enumerate(fields):
            field = QRectF(
                fields_left + index * (field_width + field_gap),
                field_top,
                field_width,
                field_height,
            )
            setattr(self, f"timer_{field_name}_rect", field)
            painter.setBrush(self.look.veil(alpha * 0.05))
            painter.setPen(QPen(self.look.veil(alpha * 0.07), 1.0))
            painter.drawRoundedRect(field, 14.0, 14.0)
            self.draw_button_hover(
                painter, field, hover[f"timer_{field_name}"], alpha * 0.6, 14.0
            )
            selected = QRectF(
                field.left() + 4.0, field.center().y() - 14.0, field.width() - 8.0, 28.0
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(accent, alpha * 0.15))
            painter.drawRoundedRect(selected, 9.0, 9.0)

            painter.setFont(ui_font(8, QFont.Weight.Medium))
            painter.setPen(with_alpha(self.look.tertiary, alpha * 0.85))
            painter.drawText(
                QRectF(field.left(), field.top() + 1.0, field.width(), 16.0),
                Qt.AlignmentFlag.AlignCenter,
                f"{(value - 1) % (maximum + 1):02d}",
            )
            painter.drawText(
                QRectF(field.left(), field.bottom() - 17.0, field.width(), 16.0),
                Qt.AlignmentFlag.AlignCenter,
                f"{(value + 1) % (maximum + 1):02d}",
            )
            painter.setFont(ui_font(19, QFont.Weight.DemiBold, display=True))
            self.draw_text(
                painter,
                selected,
                Qt.AlignmentFlag.AlignCenter,
                f"{value:02d}",
                with_alpha(self.look.primary, alpha),
            )
            painter.setFont(ui_font(7, QFont.Weight.DemiBold, spacing=0.8))
            painter.setPen(with_alpha(self.look.tertiary, alpha))
            painter.drawText(
                QRectF(field.left(), field.bottom() + 3.0, field.width(), 12.0),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            if index < len(fields) - 1:
                painter.setFont(ui_font(13, QFont.Weight.DemiBold))
                painter.setPen(with_alpha(self.look.tertiary, alpha))
                painter.drawText(
                    QRectF(field.right(), field.center().y() - 12.0, field_gap, 22.0),
                    Qt.AlignmentFlag.AlignCenter,
                    ":",
                )

        button_height = 32.0
        button_gap = 8.0
        button_width = (pill.width() - 36.0 - button_gap * 2) / 3.0
        button_x = pill.left() + 18.0
        button_y = pill.bottom() - 14.0 - button_height
        self.timer_start_rect = QRectF(button_x, button_y, button_width, button_height)
        self.timer_pause_rect = self.timer_start_rect.translated(button_width + button_gap, 0.0)
        self.timer_stop_rect = self.timer_pause_rect.translated(button_width + button_gap, 0.0)
        # The accent marks the next sensible action.
        self.draw_pill_button(
            painter, self.timer_start_rect, tr("Старт"), alpha, hover["timer_start"],
            primary=not self.timer_active,
        )
        self.draw_pill_button(
            painter, self.timer_pause_rect, tr("Пауза"), alpha, hover["timer_pause"],
            primary=self.timer_active,
        )
        self.draw_pill_button(
            painter, self.timer_stop_rect, tr("Стоп"), alpha, hover["timer_stop"]
        )

    def draw_mini_timer(self, painter, pill, alpha):
        if (
            not self.config["timer_button_enabled"]
            or not self.timer_mini_visible
        ):
            return

        accent = self.current_accent_color()
        hover = self.btn_hover_anim
        row = QRectF(pill.left() + 18.0, pill.bottom() - 38.0, pill.width() - 36.0, 24.0)
        self.timer_mini_rect = row
        y = row.top() + 3.0
        height = row.height() - 6.0
        button_width = 52.0
        self.timer_mini_mode_rect = QRectF(row.left() + 3.0, y, 54.0, height)
        time_rect = QRectF(row.left() + 59.0, y, 70.0, height)
        self.timer_mini_open_rect = QRectF(row.left() + 3.0, y, 126.0, height)
        self.timer_mini_stop_rect = QRectF(row.right() - 3.0 - button_width, y, button_width, height)
        self.timer_mini_pause_rect = self.timer_mini_stop_rect.translated(-button_width - 4.0, 0.0)
        self.timer_mini_start_rect = self.timer_mini_pause_rect.translated(-button_width - 4.0, 0.0)

        painter.setBrush(self.look.veil(alpha * 0.05))
        painter.setPen(
            QPen(
                with_alpha(accent, alpha * 0.5)
                if self.timer_active
                else self.look.veil(alpha * 0.09),
                1.0,
            )
        )
        painter.drawRoundedRect(row.adjusted(0.5, 0.5, -0.5, -0.5), 11.5, 11.5)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(accent, alpha * 0.17))
        painter.drawRoundedRect(self.timer_mini_mode_rect, height / 2.0, height / 2.0)
        self.draw_button_hover(
            painter, self.timer_mini_open_rect, hover["timer_mini_open"], alpha, height / 2.0
        )

        painter.setFont(ui_font(7, QFont.Weight.DemiBold, spacing=0.5))
        painter.setPen(with_alpha(mix_colors(accent, QColor(255, 255, 255), 0.3), alpha))
        painter.drawText(
            self.timer_mini_mode_rect,
            Qt.AlignmentFlag.AlignCenter,
            tr("ТАЙМЕР") if self.timer_mode == "Timer" else tr("СЕК."),
        )
        for rect, label, hover_key in (
            (self.timer_mini_start_rect, tr("СТАРТ"), "timer_mini_start"),
            (self.timer_mini_pause_rect, tr("ПАУЗА"), "timer_mini_pause"),
            (self.timer_mini_stop_rect, tr("СТОП"), "timer_mini_stop"),
        ):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.look.veil(alpha * (0.07 + 0.08 * hover[hover_key])))
            painter.drawRoundedRect(rect, height / 2.0, height / 2.0)
            painter.setPen(
                with_alpha(mix_colors(self.look.secondary, self.look.primary, hover[hover_key]), alpha)
            )
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        display_seconds = (
            self.timer_seconds_left
            if self.timer_mode == "Timer"
            and self.timer_session_visible
            else self.config["timer_duration_seconds"]
            if self.timer_mode == "Timer"
            else self.stopwatch_seconds
        )
        painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
        self.draw_text(
            painter,
            time_rect,
            Qt.AlignmentFlag.AlignCenter,
            self.format_timer_hms(display_seconds),
            with_alpha(self.look.primary, alpha),
        )

    def draw_timer_shortcut(self, painter, pill):
        """The round timer button beside the collapsed island."""
        fade = self.side_fade()
        if not self.config["timer_button_enabled"] or fade <= 0.0 or self.positioning:
            return
        shortcut = QRectF(pill.left() - 34.0, pill.center().y() - 14.0, 28.0, 28.0)
        self.timer_shortcut_rect = shortcut
        alpha = int(255 * fade)
        hover_val = self.btn_hover_anim.get("timer_btn", 0.0)
        self.draw_side_button(painter, shortcut, alpha, hover_val)

        center = shortcut.center()
        if self.timer_active:
            color = with_alpha(self.current_accent_color(), alpha)
        else:
            color = with_alpha(
                mix_colors(self.look.secondary, self.look.primary, hover_val), alpha
            )
        painter.setPen(QPen(color, 1.5))
        painter.drawEllipse(center, 6.2, 6.2)
        self.draw_clock_hands(painter, center, 3.6, color, 1.5)

    # ----- Drawing: island ------------------------------------------------

    def scaled_cover(self, size):
        """The artwork cropped to a square, at a resolution close to `size`."""
        target = 48 if size <= 26.0 else 112
        key = (self.cover_pixmap.cacheKey(), target)
        scaled = self._cover_cache.get(key)
        if scaled is None:
            if len(self._cover_cache) > 4:
                self._cover_cache.clear()
            scaled = self.cover_pixmap.scaled(
                target,
                target,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled = scaled.copy(
                (scaled.width() - target) // 2,
                (scaled.height() - target) // 2,
                target,
                target,
            )
            self._cover_cache[key] = scaled
        return scaled

    def draw_rounded_cover(self, painter, rect, radius):
        shape = rounded_rect_path(rect, radius)
        painter.save()
        painter.setClipPath(shape)
        if self.cover_pixmap:
            cover = self.scaled_cover(rect.width())
            painter.drawPixmap(rect, cover, QRectF(cover.rect()))
        else:
            # No artwork: a quiet tile with a note on it.
            tile = QLinearGradient(rect.topLeft(), rect.bottomRight())
            tile.setColorAt(0.0, QColor(58, 54, 49))
            tile.setColorAt(1.0, QColor(36, 34, 31))
            painter.fillRect(rect, QBrush(tile))
            size = rect.width()
            center = rect.center()
            note = with_alpha(self.look.secondary, 190)
            stem_x = center.x() + size * 0.05
            painter.setPen(round_pen(note, max(1.2, size * 0.05)))
            painter.drawLine(
                QPointF(stem_x, center.y() + size * 0.13),
                QPointF(stem_x, center.y() - size * 0.20),
            )
            painter.drawLine(
                QPointF(stem_x, center.y() - size * 0.20),
                QPointF(stem_x + size * 0.14, center.y() - size * 0.12),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(note)
            painter.drawEllipse(
                QPointF(center.x() - size * 0.045, center.y() + size * 0.14),
                size * 0.10,
                size * 0.08,
            )
        painter.restore()
        # A hairline keeps dark artwork from melting into the island.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.look.veil(24), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

    def draw_equalizer(self, painter, right_x, center_y, scale=1.0):
        bar_w = 2.4 * scale
        gap = 2.2 * scale
        max_h = 15.0 * scale
        total_w = len(self.eq_bars) * bar_w + (len(self.eq_bars) - 1) * gap
        start_x = right_x - total_w

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        # Grey while nothing plays, warming up to the accent with the music.
        painter.setBrush(
            mix_colors(self.look.tertiary, self.current_accent_color(), self.playing_anim)
        )
        for i, val in enumerate(self.eq_bars):
            bh = max(3.2 * scale, val * max_h)
            bx = start_x + i * (bar_w + gap)
            by = center_y - bh / 2.0
            painter.drawRoundedRect(QRectF(bx, by, bar_w, bh), bar_w / 2, bar_w / 2)
        painter.restore()

    def draw_surface(self, painter, pill, radius, accent):
        outline = None
        if self.dragging_enabled or self.positioning:
            outline = "drag"
        elif self.timer_panel_open or self.panel_open():
            outline = "panel"
        paint_island_surface(
            painter,
            pill,
            radius,
            accent,
            self.look,
            self.playing_anim,
            outline,
            self.backdrop_painter(pill),
        )

    def draw_collapsed_text(self, painter, pill, alpha):
        left_margin, right_margin = self.collapsed_margins()
        rect = QRectF(
            pill.left() + left_margin,
            pill.top() - 1.0,
            max(10.0, pill.width() - left_margin - right_margin),
            pill.height(),
        )
        painter.setFont(self.collapsed_font)
        metrics = QFontMetricsF(self.collapsed_font)
        shift, fade = self.track_text_motion()
        alpha = int(alpha * fade)
        rect.translate(0.0, shift)
        line = self.lyrics_line()
        if (
            line
            and not self.notification_showing()
            and not self.timer_session_visible
            and self.collapsed_text() == line
            and self.draw_karaoke(painter, rect, line, self.lyrics_progress(), alpha, True)
        ):
            return
        # Text that does not fit ends with "…" instead of being cut off in
        # the middle of a letter.
        text = metrics.elidedText(
            self.collapsed_text(), Qt.TextElideMode.ElideRight, rect.width()
        )
        primary = with_alpha(self.look.primary, alpha)
        title = self.display_title()
        if not (self.collapsed_shows_track() and self.artist and text.startswith(title)):
            self.draw_text(painter, rect, Qt.AlignmentFlag.AlignCenter, text, primary)
            return

        # The artist is written quieter than the title.
        rest = text[len(title):]
        title_width = metrics.horizontalAdvance(title)
        rest_width = metrics.horizontalAdvance(rest)
        x = rect.center().x() - (title_width + rest_width) / 2.0
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self.draw_text(
            painter, QRectF(x, rect.top(), title_width + 2.0, rect.height()), flags, title, primary
        )
        self.draw_text(
            painter,
            QRectF(x + title_width, rect.top(), rest_width + 2.0, rect.height()),
            flags,
            rest,
            with_alpha(self.look.secondary, alpha),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scale = self.view_scale()
        offset = self.view_offset(scale)
        painter.translate(offset)
        painter.scale(scale, scale)

        pill = self.current_pill_rect()
        radius = self.pill_radius(pill)
        self.timer_btn_rect = QRectF()
        self.timer_shortcut_rect = QRectF()
        self.timer_mini_rect = QRectF()
        self.timer_mini_open_rect = QRectF()
        self.timer_mini_mode_rect = QRectF()
        self.timer_mini_start_rect = QRectF()
        self.timer_mini_pause_rect = QRectF()
        self.timer_mini_stop_rect = QRectF()
        self.like_rect = QRectF()
        self.reset_extra_rects()

        accent = self.current_accent_color()
        self.draw_surface(painter, pill, radius, accent)
        self.draw_timer_shortcut(painter, pill)
        self.draw_side_widgets(painter, pill)

        painter.save()
        painter.setClipPath(rounded_rect_path(pill, radius))
        t = self.t
        if self.timer_panel_open and t > 0.15:
            timer_alpha = int(min(1.0, (t - 0.15) / 0.55) * 255)
            self.draw_timer_view(painter, pill, timer_alpha)
        elif self.panel_open() and t > 0.15:
            self.draw_panel(painter, pill, int(min(1.0, (t - 0.15) / 0.55) * 255))
        else:
            self.draw_player(painter, pill, t)
        self.draw_glint(painter, pill)
        painter.restore()
        self.sync_window_shape(pill, radius, scale, offset)

    def draw_expanded(self, painter, pill, alpha, cov_x, cov_y, cov_size):
        """The player: track, progress and controls."""
        hover = self.btn_hover_anim
        look = self.look
        left, right, top = pill.left(), pill.right(), pill.top()

        icon_cy = top + 30.0
        config = self.config
        # Without the gear the settings open from the tray icon, so the gear
        # stays while there is no tray icon.
        show_gear = config["gear_button"] or not config["tray_enabled"]
        show_dnd = config["dnd_button"]
        slot = right - 38.0
        self.dnd_rect = QRectF()
        self.gear_rect = QRectF()
        if show_dnd:
            self.dnd_rect = QRectF(slot, icon_cy - 13.0, 26.0, 26.0)
            slot -= 30.0
        if show_gear:
            self.gear_rect = QRectF(slot, icon_cy - 13.0, 26.0, 26.0)
            slot -= 30.0
        if show_gear:
            self.draw_button_hover(painter, self.gear_rect, hover["gear"], alpha)
            self.draw_gear_icon(
                painter,
                self.gear_rect.center(),
                with_alpha(mix_colors(look.secondary, look.primary, hover["gear"]), alpha),
            )
        if show_dnd:
            self.draw_button_hover(painter, self.dnd_rect, hover["dnd"], alpha)
            self.draw_bell_icon(painter, self.dnd_rect.center(), alpha, hover["dnd"])

        text_x = cov_x + cov_size + 12.0
        text_max_w = max(40.0, slot + 22.0 - text_x)
        if self.idle():
            self.draw_idle_info(painter, pill, alpha, text_x, text_max_w, cov_y)
            self.track_bar_rect = QRectF()
        else:
            self.draw_track_info(painter, pill, alpha, text_x, text_max_w, cov_y)

        ctrl_y = top + 118.0
        cx = pill.center().x()
        vol_btn_x = left + 27.0
        repeat_x = right - 27.0

        show_repeat = config["repeat_button"]
        self.vol_rect = (
            QRectF(vol_btn_x - 18.0, ctrl_y - 18.0, 36.0, 36.0) if config["volume_button"] else QRectF()
        )
        self.prev_rect = QRectF(cx - 74.0, ctrl_y - 18.0, 36.0, 36.0)
        self.play_rect = QRectF(cx - 19.0, ctrl_y - 19.0, 38.0, 38.0)
        self.next_rect = QRectF(cx + 38.0, ctrl_y - 18.0, 36.0, 36.0)
        self.repeat_rect = QRectF(repeat_x - 18.0, ctrl_y - 18.0, 36.0, 36.0) if show_repeat else QRectF()
        if self.liked is not None and config["like_button"]:
            like_x = repeat_x - 58.0 if show_repeat else repeat_x - 18.0
            self.like_rect = QRectF(like_x, ctrl_y - 18.0, 36.0, 36.0)

        self.draw_media_icons(painter, cx, ctrl_y, alpha, vol_btn_x, repeat_x)
        if not self.like_rect.isEmpty():
            self.draw_button_hover(painter, self.like_rect, hover["like"], alpha)
            self.draw_heart_icon(painter, self.like_rect.center(), alpha)

        self.draw_extra_rows(painter, pill, top + EXPANDED_HEIGHT, alpha)

        if self.vol_popup_anim > 0.01:
            self.draw_vertical_volume_popup(painter, vol_btn_x, top + 16.0, ctrl_y - 20.0, alpha)

        if self.t > 0.8:
            self.draw_mini_timer(painter, pill, alpha)

    def draw_track_info(self, painter, pill, alpha, text_x, text_max_w, cov_y):
        """Title, artist (or the sung line) and the progress bar."""
        look = self.look
        padding = 18.0
        left, right, top = pill.left(), pill.right(), pill.top()
        text_flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        shift, fade = self.track_text_motion()
        text_alpha = int(alpha * fade)

        title_font = ui_font(11, QFont.Weight.DemiBold, display=True)
        painter.setFont(title_font)
        self.draw_text(
            painter,
            QRectF(text_x, cov_y + 6.0 + shift, text_max_w, 20.0),
            text_flags,
            QFontMetricsF(title_font).elidedText(
                self.display_title(), Qt.TextElideMode.ElideRight, text_max_w
            ),
            with_alpha(look.primary, text_alpha),
        )
        artist_font = ui_font(9, QFont.Weight.Medium)
        painter.setFont(artist_font)
        # While lyrics are on, the line being sung takes the artist's place.
        second_line = self.lyrics_line() or self.artist
        second_color = (
            mix_colors(look.secondary, self.current_accent_color(), 0.55)
            if self.lyrics_line()
            else look.secondary
        )
        second_rect = QRectF(text_x, cov_y + 27.0 + shift, text_max_w, 18.0)
        if not (
            self.lyrics_line()
            and self.draw_karaoke(
                painter, second_rect, second_line, self.lyrics_progress(), text_alpha, False
            )
        ):
            self.draw_text(
                painter,
                second_rect,
                text_flags,
                QFontMetricsF(artist_font).elidedText(
                    second_line, Qt.TextElideMode.ElideRight, text_max_w
                ),
                with_alpha(second_color, text_alpha),
            )
        translation = self.lyrics_translation()
        if translation:
            # The sung line in the interface language, under it.
            small = ui_font(8, QFont.Weight.Medium)
            painter.setFont(small)
            self.draw_text(
                painter,
                QRectF(text_x, cov_y + 43.0 + shift, text_max_w, 15.0),
                text_flags,
                QFontMetricsF(small).elidedText(translation, Qt.TextElideMode.ElideRight, text_max_w),
                with_alpha(look.tertiary, text_alpha),
            )

        # Progress: elapsed time, the bar, time left.
        bar_y = top + 87.0
        time_font = ui_font(8, QFont.Weight.DemiBold)
        painter.setFont(time_font)
        left_str = self.format_time(self.position_sec)
        rem = max(0.0, self.duration_sec - self.position_sec)
        right_str = f"-{self.format_time(rem)}"
        progress = max(0.0, min(1.0, self.position_sec / max(1.0, self.duration_sec)))
        time_metrics = QFontMetricsF(time_font)
        label_w = max(
            30.0,
            math.ceil(
                max(
                    time_metrics.horizontalAdvance(left_str),
                    time_metrics.horizontalAdvance(right_str),
                )
            )
            + 2.0,
        )
        time_color = with_alpha(
            mix_colors(look.tertiary, look.secondary, self.bar_hover_anim), alpha
        )
        self.draw_text(
            painter,
            QRectF(left + padding, bar_y - 8.0, label_w, 15.0),
            text_flags,
            left_str,
            time_color,
        )
        self.draw_text(
            painter,
            QRectF(right - padding - label_w, bar_y - 8.0, label_w, 15.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            right_str,
            time_color,
        )

        track_x = left + padding + label_w + 10.0
        track_w = right - padding - label_w - 10.0 - track_x
        track_h = 4.0 + 2.0 * self.bar_hover_anim
        self.track_bar_rect = QRectF(track_x, bar_y - track_h / 2, track_w, track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(look.veil(alpha * 0.15))
        painter.drawRoundedRect(self.track_bar_rect, track_h / 2, track_h / 2)
        if progress > 0.0:
            fill_w = max(track_h, track_w * progress)
            painter.setBrush(with_alpha(look.primary, alpha))
            painter.drawRoundedRect(
                QRectF(track_x, bar_y - track_h / 2, fill_w, track_h),
                track_h / 2,
                track_h / 2,
            )
        if self.bar_hover_anim > 0.02 and self.duration_sec > 0:
            # The handle appears only when the bar is about to be used.
            knob_x = track_x + track_w * progress
            painter.setBrush(QColor(0, 0, 0, int(alpha * 0.25 * self.bar_hover_anim)))
            painter.drawEllipse(QPointF(knob_x, bar_y + 1.0), 5.5 * self.bar_hover_anim, 5.5 * self.bar_hover_anim)
            painter.setBrush(with_alpha(look.knob, alpha))
            painter.drawEllipse(QPointF(knob_x, bar_y), 5.0 * self.bar_hover_anim, 5.0 * self.bar_hover_anim)

    # ----- Drawing: icons -------------------------------------------------

    _gear_path = None

    @classmethod
    def gear_path(cls):
        """A gear outline around the origin, about 13 px across."""
        if cls._gear_path is None:
            path = QPainterPath()
            teeth = 8
            for tooth in range(teeth):
                angle = 2.0 * math.pi * tooth / teeth
                for index, (reach, offset) in enumerate(
                    ((5.0, -0.27), (6.6, -0.15), (6.6, 0.15), (5.0, 0.27))
                ):
                    point = QPointF(
                        reach * math.cos(angle + offset),
                        reach * math.sin(angle + offset),
                    )
                    if tooth == 0 and index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
            path.closeSubpath()
            path.addEllipse(QPointF(0.0, 0.0), 2.2, 2.2)
            cls._gear_path = path
        return cls._gear_path

    def draw_gear_icon(self, painter, center, color):
        painter.save()
        painter.translate(center)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(round_pen(color, 1.3))
        painter.drawPath(self.gear_path())
        painter.restore()

    def draw_bell_icon(self, painter, center, alpha, hover_val):
        """A bell; struck through and tinted while do-not-disturb is on."""
        silenced = self.config["dnd_mode"]
        if silenced:
            color = with_alpha(self.current_accent_color(), alpha)
        else:
            color = with_alpha(mix_colors(self.look.secondary, self.look.primary, hover_val), alpha)
        cx, cy = center.x(), center.y() - 0.5
        bell = QPainterPath()
        bell.moveTo(cx - 5.4, cy + 3.4)
        bell.cubicTo(cx - 3.9, cy + 2.0, cx - 4.0, cy - 0.4, cx - 4.0, cy - 1.6)
        bell.cubicTo(cx - 4.0, cy - 4.2, cx - 2.2, cy - 5.9, cx, cy - 5.9)
        bell.cubicTo(cx + 2.2, cy - 5.9, cx + 4.0, cy - 4.2, cx + 4.0, cy - 1.6)
        bell.cubicTo(cx + 4.0, cy - 0.4, cx + 3.9, cy + 2.0, cx + 5.4, cy + 3.4)
        bell.closeSubpath()
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(round_pen(color, 1.3))
        painter.drawPath(bell)
        painter.drawLine(QPointF(cx - 1.4, cy + 5.9), QPointF(cx + 1.4, cy + 5.9))
        if silenced:
            painter.drawLine(QPointF(cx - 6.0, cy - 5.6), QPointF(cx + 6.0, cy + 6.4))
        painter.restore()

    def draw_speaker_icon(self, painter, cx, cy, alpha):
        painter.save()
        is_off = self.is_muted or self.current_volume <= 0.01
        icon_color = with_alpha(DANGER_COLOR if is_off else self.look.primary, alpha)
        painter.setPen(round_pen(icon_color, 1.2))
        painter.setBrush(icon_color)

        speaker = QPainterPath()
        speaker.moveTo(cx - 7.5, cy - 2.6)
        speaker.lineTo(cx - 4.6, cy - 2.6)
        speaker.lineTo(cx - 1.0, cy - 6.0)
        speaker.lineTo(cx - 1.0, cy + 6.0)
        speaker.lineTo(cx - 4.6, cy + 2.6)
        speaker.lineTo(cx - 7.5, cy + 2.6)
        speaker.closeSubpath()
        painter.drawPath(speaker)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(round_pen(icon_color, 1.5))
        if is_off:
            painter.drawLine(QPointF(cx + 3.0, cy - 3.0), QPointF(cx + 8.0, cy + 3.0))
            painter.drawLine(QPointF(cx + 8.0, cy - 3.0), QPointF(cx + 3.0, cy + 3.0))
        else:
            # One wave for a quiet volume, two for a loud one.
            waves = (3.6, 7.0) if self.current_volume > 0.5 else (3.6,)
            for reach in waves:
                painter.drawArc(
                    QRectF(cx + 0.5 - reach, cy - reach, reach * 2, reach * 2),
                    -42 * 16,
                    84 * 16,
                )
        painter.restore()

    def draw_repeat_icon(self, painter, cx, cy, alpha, scale=1.0):
        painter.save()
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        cx = cy = 0.0
        if not self.repeat_available:
            # The current player cannot be told to repeat: show it dimmed.
            color = with_alpha(self.look.primary, alpha * 0.28)
        elif self.repeat_mode == 0:
            color = with_alpha(self.look.primary, alpha * 0.9)
        else:
            color = with_alpha(self.current_accent_color(), alpha)
        painter.setPen(round_pen(color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = 5.0
        left = cx - 8.4
        right = cx + 8.4
        top = cy - 5.0
        bottom = cy + 5.0
        loop_path = QPainterPath()
        loop_path.moveTo(left, cy + 1.4)
        loop_path.lineTo(left, top + r)
        loop_path.arcTo(QRectF(left, top, r * 2, r * 2), 180, -90)
        loop_path.lineTo(right - r, top)
        loop_path.arcTo(QRectF(right - r * 2, top, r * 2, r * 2), 90, -180)
        loop_path.lineTo(left + 3.0, bottom)
        painter.drawPath(loop_path)
        if self.repeat_available and self.repeat_mode == 2:
            # "1" marks repeat-one, to tell it apart from repeat-all.
            painter.setFont(ui_font(6, QFont.Weight.Bold))
            painter.drawText(
                QRectF(cx - 5.0, cy - 5.0, 10.0, 10.0),
                Qt.AlignmentFlag.AlignCenter,
                "1",
            )
        painter.restore()

    def draw_vertical_volume_popup(self, painter, center_x, top_y, bottom_y, base_alpha):
        painter.save()
        pop_alpha = int(base_alpha * min(1.0, self.vol_popup_anim))
        pill_w = 24.0
        slide_offset = (1.0 - self.vol_popup_anim) * 8.0
        y1 = top_y + slide_offset
        y2 = bottom_y + slide_offset
        pill_h = max(40.0, y2 - y1)
        self.vol_popup_rect = QRectF(center_x - pill_w / 2.0, y1, pill_w, pill_h)
        painter.setBrush(with_alpha(self.look.panel, pop_alpha * 0.97))
        painter.setPen(QPen(self.look.veil(pop_alpha * 0.16), 1.0))
        painter.drawRoundedRect(
            self.vol_popup_rect.adjusted(0.5, 0.5, -0.5, -0.5), pill_w / 2.0, pill_w / 2.0
        )
        pad_v = 11.0
        track_top = y1 + pad_v
        track_bottom = y2 - pad_v
        track_h = max(10.0, track_bottom - track_top)
        track_w = 4.0
        self.vol_track_rect = QRectF(center_x - track_w / 2.0, track_top, track_w, track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.look.veil(pop_alpha * 0.16))
        painter.drawRoundedRect(self.vol_track_rect, track_w / 2.0, track_w / 2.0)
        eff_vol = 0.0 if self.is_muted else self.current_volume
        fill_h = track_h * eff_vol
        knob_y = track_bottom - fill_h
        knob_color = with_alpha(self.look.knob, pop_alpha)
        if fill_h > 1.0:
            fill_rect = QRectF(center_x - track_w / 2.0, knob_y, track_w, fill_h)
            painter.setBrush(with_alpha(self.look.primary, pop_alpha))
            painter.drawRoundedRect(fill_rect, track_w / 2.0, track_w / 2.0)
        painter.setBrush(knob_color)
        painter.drawEllipse(QPointF(center_x, knob_y), 5.0, 5.0)
        painter.restore()

    def draw_skip_icon(self, painter, cx, cy, direction, color):
        """Two triangles pointing left (direction -1) or right (+1)."""
        painter.setPen(round_pen(color, 1.4))
        painter.setBrush(color)
        for offset in (-4.4, 4.4):
            tip = cx + offset + direction * 3.3
            base = cx + offset - direction * 3.3
            triangle = QPainterPath()
            triangle.moveTo(tip, cy)
            triangle.lineTo(base, cy - 5.0)
            triangle.lineTo(base, cy + 5.0)
            triangle.closeSubpath()
            painter.drawPath(triangle)

    def draw_media_icons(self, painter, cx, cy, alpha, vol_btn_x, repeat_x):
        painter.save()
        hover = self.btn_hover_anim
        if not self.vol_rect.isEmpty():
            self.draw_button_hover(painter, self.vol_rect, hover["vol"], alpha)
        self.draw_button_hover(painter, self.prev_rect, hover["prev"], alpha)
        self.draw_button_hover(painter, self.next_rect, hover["next"], alpha)
        if not self.repeat_rect.isEmpty():
            self.draw_button_hover(painter, self.repeat_rect, hover["repeat"], alpha)

        # Play / pause is the main action: it sits on a disc of its own.
        painter.setPen(QPen(self.look.veil(alpha * 0.07), 1.0))
        painter.setBrush(self.look.veil(alpha * (0.09 + 0.09 * hover["play"])))
        painter.drawEllipse(self.play_rect.adjusted(0.5, 0.5, -0.5, -0.5))

        if not self.vol_rect.isEmpty():
            self.draw_speaker_icon(painter, vol_btn_x, cy, alpha)
        icon_color = with_alpha(self.look.primary, alpha)
        self.draw_skip_icon(painter, cx - 56.0, cy, -1, icon_color)
        self.draw_skip_icon(painter, cx + 56.0, cy, 1, icon_color)

        painter.setBrush(icon_color)
        if self.is_playing:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(cx - 6.2, cy - 7.0, 4.2, 14.0), 1.6, 1.6)
            painter.drawRoundedRect(QRectF(cx + 2.0, cy - 7.0, 4.2, 14.0), 1.6, 1.6)
        else:
            painter.setPen(round_pen(icon_color, 2.0))
            triangle = QPainterPath()
            triangle.moveTo(cx - 3.6, cy - 6.0)
            triangle.lineTo(cx + 6.2, cy)
            triangle.lineTo(cx - 3.6, cy + 6.0)
            triangle.closeSubpath()
            painter.drawPath(triangle)

        if not self.repeat_rect.isEmpty():
            self.draw_repeat_icon(painter, repeat_x, cy, alpha)
        painter.restore()

    def seek_to_mouse_x(self, mouse_x: float):
        if self.track_bar_rect.width() <= 0 or self.duration_sec <= 0:
            return
        ratio = (mouse_x - self.track_bar_rect.left()) / self.track_bar_rect.width()
        ratio = max(0.0, min(1.0, ratio))
        self.position_sec = ratio * self.duration_sec
        self.update()

    def set_vol_from_mouse_y(self, mouse_y: float):
        if self.vol_track_rect.height() <= 0:
            return
        ratio = (self.vol_track_rect.bottom() - mouse_y) / self.vol_track_rect.height()
        self.set_volume_scalar(ratio)
        self.show_volume_popup(1.4)
        self.update()

    def target_app_volumes(self):
        """Volume controls of the app chosen in the settings.

        Listing the audio sessions is slow, so the result is reused for a
        moment: dragging the slider asks for it on every mouse move.
        """
        now = time.perf_counter()
        if self._volume_controls is not None and now - self._volume_controls[0] < 2.0:
            return self._volume_controls[1]
        target = self.config["volume_target"].lower()
        volumes = []
        for session in AudioUtilities.GetAllSessions():
            try:
                name = session.Process.name().lower() if session.Process else ""
            except psutil.Error:
                continue  # the process ended meanwhile
            if os.path.splitext(name)[0] == target:
                volumes.append(session.SimpleAudioVolume)
        self._volume_controls = (now, volumes)
        return volumes

    def sync_volume_from_system(self):
        """Show the real volume instead of assuming 100%."""
        if self.dragging_vol_slider:
            return
        try:
            if self.config["volume_target"] == "Master Windows":
                endpoint = AudioUtilities.GetSpeakers().EndpointVolume
                level = endpoint.GetMasterVolumeLevelScalar()
                muted = bool(endpoint.GetMute())
            else:
                volumes = self.target_app_volumes()
                if not volumes:
                    return  # the app is not playing anything right now
                level = max(volume.GetMasterVolume() for volume in volumes)
                muted = all(bool(volume.GetMute()) for volume in volumes)
        except Exception:
            logging.debug("Could not read the audio volume", exc_info=True)
            return
        level = max(0.0, min(1.0, float(level)))
        if level > 0.01:
            self.saved_volume_before_mute = level
        self.current_volume = level
        self.is_muted = muted or level <= 0.01

    def apply_volume(self):
        """Send current_volume and is_muted to Windows."""
        try:
            if self.config["volume_target"] == "Master Windows":
                endpoint = AudioUtilities.GetSpeakers().EndpointVolume
                endpoint.SetMute(1 if self.is_muted else 0, None)
                endpoint.SetMasterVolumeLevelScalar(self.current_volume, None)
                return
            volumes = self.target_app_volumes()
            if not volumes:
                raise LookupError(
                    tr("{app} сейчас не воспроизводит звук").format(
                        app=self.config["volume_target"]
                    )
                )
            for volume in volumes:
                volume.SetMute(1 if self.is_muted else 0, None)
                volume.SetMasterVolume(self.current_volume, None)
        except LookupError as error:
            self.notify(str(error))
        except Exception:
            # The app may have closed its audio session; look again next time.
            self._volume_controls = None
            logging.exception("Could not change the selected audio volume")
            self.notify(tr("Не удалось изменить громкость"))

    def set_volume_scalar(self, new_val: float):
        self.current_volume = max(0.0, min(1.0, new_val))
        if self.current_volume > 0.01:
            self.saved_volume_before_mute = self.current_volume
            self.is_muted = False
        else:
            self.is_muted = True
        self.apply_volume()

    def persist_config(self):
        try:
            save_config(self.config)
        except OSError as error:
            logging.exception("Could not save settings")
            self.notify(tr("Не удалось сохранить настройки"), force=True)
            QMessageBox.warning(self, tr("Ошибка сохранения"), str(error))

    def toggle_mute(self):
        # Muting keeps the volume level, so unmuting brings it back as it was.
        self.is_muted = not self.is_muted
        if not self.is_muted and self.current_volume <= 0.01:
            self.current_volume = self.saved_volume_before_mute
        self.apply_volume()
        self.update()

    def handle_timer_click(self, position):
        if not self.timer_panel_open:
            return False
        if self.timer_close_rect.contains(position):
            self.timer_panel_open = False
            self.update()
            return True
        if self.timer_timer_mode_rect.contains(position):
            self.set_timer_mode("Timer")
        elif self.timer_stopwatch_mode_rect.contains(position):
            self.set_timer_mode("Stopwatch")
        elif self.timer_start_rect.contains(position):
            self.start_timer()
        elif self.timer_pause_rect.contains(position):
            self.pause_timer()
        elif self.timer_stop_rect.contains(position):
            self.stop_timer()
        elif self.timer_view_rect.contains(position):
            return True
        else:
            return False
        self.update()
        return True

    def handle_mini_timer_click(self, position):
        if self.timer_mini_open_rect.contains(position):
            self.timer_panel_open = True
        elif self.timer_mini_start_rect.contains(position):
            self.start_timer()
        elif self.timer_mini_pause_rect.contains(position):
            self.pause_timer()
        elif self.timer_mini_stop_rect.contains(position):
            self.stop_timer()
        else:
            return False
        self.update()
        return True

    def start_timer(self):
        now = time.perf_counter()
        if self.timer_mode == "Timer":
            if self.timer_remaining <= 0:
                self.notify(tr("Задайте время больше 00:00:00"), seconds=2.0, force=True)
                return
            if not self.timer_active:
                self.timer_deadline = now + self.timer_remaining
        elif not self.timer_active:
            self.stopwatch_started = now
        self.timer_active = True
        self.timer_session_visible = True
        self.timer_mini_visible = True
        self.config["timer_configured"] = True
        self.timer_persist_timer.start(500)
        self.update()

    def pause_timer(self):
        if self.timer_active:
            now = time.perf_counter()
            if self.timer_mode == "Timer":
                self.timer_remaining = max(0.0, self.timer_deadline - now)
                self.timer_seconds_left = math.ceil(self.timer_remaining)
            else:
                self.stopwatch_elapsed += now - self.stopwatch_started
                self.stopwatch_seconds = int(self.stopwatch_elapsed)
        self.timer_active = False
        self.update()

    def stop_timer(self):
        self.timer_active = False
        self.timer_session_visible = False
        self.timer_mini_visible = False
        self.config["timer_configured"] = False
        if self.timer_mode == "Timer":
            self.reset_countdown()
        else:
            self.stopwatch_elapsed = 0.0
            self.stopwatch_seconds = 0
        self.timer_persist_timer.start(500)
        self.update()

    def adjust_timer_field(self, field_name, steps):
        if self.timer_mode != "Timer" or self.timer_active or steps == 0:
            return
        total = max(0, self.timer_seconds_left)
        hours = min(99, total // 3600)
        minutes = (total // 60) % 60
        seconds = total % 60
        if field_name == "hour":
            hours = (hours + steps) % 100
        elif field_name == "minute":
            minutes = (minutes + steps) % 60
        elif field_name == "second":
            seconds = (seconds + steps) % 60
        self.timer_seconds_left = hours * 3600 + minutes * 60 + seconds
        self.timer_remaining = float(self.timer_seconds_left)
        self.config["timer_duration_seconds"] = self.timer_seconds_left
        self.config["timer_configured"] = True
        self.timer_mini_visible = True
        self.timer_persist_timer.start(500)
        self.update()

    def mousePressEvent(self, event):
        position = self.design_pos(event.position())
        if event.button() == Qt.MouseButton.LeftButton and self.positioning:
            if self.extras_press(position, event.button()):
                return
            if self.current_pill_rect().adjusted(-6, -6, 6, 6).contains(position):
                self.start_drag(event.globalPosition().toPoint())
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.banner_share() > 0.5
            and self.t < 0.3
            and self.current_pill_rect().contains(position)
            and self.banner_clicked(position)
        ):
            return
        if event.button() == Qt.MouseButton.LeftButton and self.side_fade() > 0.0:
            side = self.side_button_at(position)
            if side == "timer_btn":
                self.timer_panel_open = True
                self.update()
                return
            if side is not None and self.extras_press(position, event.button()):
                return
        if (
            event.button() == Qt.MouseButton.RightButton
            and self.t > 0.7
            and not self.panel_open()
            and self.like_rect.contains(position)
            and not QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            # Right click on the heart: add the track to a playlist.
            self.add_to_playlist()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.t > 0.7:
            pos = position
            if self.timer_panel_open:
                if self.handle_timer_click(pos):
                    return
            elif self.panel_open():
                self.extras_press(pos, event.button())
                return
            elif (
                self.config["timer_button_enabled"]
                and self.handle_mini_timer_click(pos)
            ):
                return
            if self.gear_rect.contains(pos):
                self.open_settings()
            elif self.dnd_rect.contains(pos):
                self.config["dnd_mode"] = not self.config["dnd_mode"]
                self.persist_config()
                self.update()
            elif (
                self.vol_popup_anim > 0.4
                and not self.vol_popup_rect.isEmpty()
                and self.vol_popup_rect.contains(pos)
            ):
                self.dragging_vol_slider = True
                self.set_vol_from_mouse_y(pos.y())
            elif self.vol_rect.contains(pos):
                self.toggle_mute()
            elif self.repeat_rect.contains(pos):
                self.cycle_repeat()
            elif self.like_rect.contains(pos):
                self.toggle_like()
            elif (
                self.duration_sec > 0
                and not self.track_bar_rect.isEmpty()
                and self.track_bar_rect.adjusted(-4, -8, 4, 8).contains(pos)
            ):
                self.seeking_bar = True
                self.seek_to_mouse_x(pos.x())
            elif self.prev_rect.contains(pos):
                self.skip_track("prev")
            elif self.play_rect.contains(pos):
                self.toggle_playback()
            elif self.next_rect.contains(pos):
                self.skip_track("next")
            else:
                self.extras_press(pos, event.button())
        elif event.button() == Qt.MouseButton.RightButton:
            keys = QApplication.keyboardModifiers()
            if keys & Qt.KeyboardModifier.ControlModifier and not self.positioning:
                self.close_panels()
                self.timer_panel_open = False
                self.set_openness(0.0)
                self.start_drag(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        position = self.design_pos(event.position())
        buttons = event.buttons()
        if self.dragging_vol_slider and (buttons & Qt.MouseButton.LeftButton):
            self.set_vol_from_mouse_y(position.y())
        elif self.seeking_bar and (buttons & Qt.MouseButton.LeftButton):
            self.seek_to_mouse_x(position.x())
        elif self.dragging_enabled and (
            buttons & (Qt.MouseButton.RightButton | Qt.MouseButton.LeftButton)
        ):
            self.drag_to(event.globalPosition().toPoint())
        else:
            self.extras_move(position, buttons)
        self.mark_moving()

    def mouseReleaseEvent(self, event):
        if self.dragging_enabled and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        ):
            self.end_drag()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging_vol_slider:
                self.dragging_vol_slider = False
            elif self.seeking_bar:
                self.seeking_bar = False
                self.commit_seek()
            else:
                self.extras_release(self.design_pos(event.position()))
        self.mark_moving()

    def wheelEvent(self, event):
        position = self.design_pos(event.position())
        if self.timer_panel_open:
            if self.t > 0.7:
                for field_name in ("hour", "minute", "second"):
                    field_rect = getattr(self, f"timer_{field_name}_rect")
                    if field_rect.contains(position):
                        # Touchpads send many small steps: collect them
                        # until they add up to one notch of a wheel.
                        self.timer_wheel_rest += event.angleDelta().y() / 120.0
                        steps = int(self.timer_wheel_rest)
                        self.timer_wheel_rest -= steps
                        self.adjust_timer_field(field_name, steps)
                        event.accept()
                        return
            event.accept()
            return
        if self.panel_open():
            event.accept()
            return
        if self.config["timer_button_enabled"] and self.timer_mini_rect.contains(position):
            event.accept()
            return
        if self.t > 0.5:
            delta = event.angleDelta().y() / 120.0
            self.set_volume_scalar(self.current_volume + delta * 0.05)
            self.show_volume_popup(1.4)
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def current_pill_rect(self):
        t = self.t
        banner = self.banner_share()
        base_w, base_h = self.col_w, self.col_h
        if banner > 0.0:
            banner_w, banner_h = self.banner_size()
            base_w += (banner_w - base_w) * banner
            base_h += (banner_h - base_h) * banner
        w = base_w + (self.exp_w - base_w) * t
        h = base_h + (self.exp_h - base_h) * t
        # Whole units (and an even width, as the pill is centred) keep the
        # one-pixel border sharp instead of smeared over two rows.
        w = max(2.0, round(w / 2.0) * 2.0)
        h = max(2.0, float(round(h)))
        x = (self.canvas_w - w) / 2.0
        y = (self.canvas_h - EDGE_PAD - h) if self.opens_up else EDGE_PAD
        return QRectF(x, y, w, h)

    def pill_radius(self, pill):
        collapsed = COLLAPSED_RADIUS + (24.0 - COLLAPSED_RADIUS) * self.banner_share()
        radius = collapsed + (EXPANDED_RADIUS - collapsed) * max(0.0, self.t)
        return max(1.0, min(radius, pill.height() / 2.0))

    def start_services(self):
        """Start the threads that feed the island with what happens outside."""
        self.bridge_signals = BridgeSignals()
        self.bridge_signals.report.connect(self.on_bridge_report)
        self.bridge_signals.outdated.connect(self.on_outdated_extension)
        self.bridge_signals.downloads.connect(self.on_downloads_report)
        self.bridge = BridgeServer(self.bridge_signals, self.videos)
        self.bridge.start()
        try:
            from island_calls import BrowserTabs
            self.videos.tabs = BrowserTabs()
            self.videos.tabs.start()
        except Exception:
            logging.exception("Could not start reading the browser tabs")
            self.videos.tabs = None

        self.media_signals = MediaSignals()
        self.media_signals.state.connect(self.on_system_report)
        self.media_signals.cover.connect(self.on_system_cover)
        self.worker = MediaWorker(self.media_signals, self.videos)
        self.worker.start()

        self.ping = PingWorker()
        self.ping.start()
        self.sync_ping_worker()
        self.start_extra_services()

    def frame_interval_ms(self):
        return max(1, round(1000.0 / self.config["fps"]))

    def scale_limits(self):
        """Size of the island while collapsed and while expanded (1 = 100%)."""
        return (
            self.config["scale_collapsed"] / 100.0,
            self.config["scale_expanded"] / 100.0,
        )

    def view_scale(self):
        low, high = self.scale_limits()
        return low + (high - low) * max(0.0, min(1.0, self.t))

    def view_offset(self, scale):
        """Where the canvas's corner falls in the window at this scale: the
        island keeps its centre and the edge it hangs from."""
        x = (self.width() - self.canvas_w * scale) / 2.0
        if self.opens_up:
            y = (self.height() - EDGE_PAD) - (self.canvas_h - EDGE_PAD) * scale
        else:
            y = EDGE_PAD - EDGE_PAD * scale
        return QPointF(round(x), round(y))

    def design_pos(self, point):
        """A point of the window in the units the island is drawn in."""
        scale = self.view_scale()
        offset = self.view_offset(scale)
        return QPointF(
            (point.x() - offset.x()) / scale, (point.y() - offset.y()) / scale
        )

    def window_rect(self, rect, scale, offset):
        """A rectangle of the canvas in the window's pixels."""
        return QRectF(
            rect.x() * scale + offset.x(),
            rect.y() * scale + offset.y(),
            rect.width() * scale,
            rect.height() * scale,
        )

    def target_screen(self):
        name = self.config["monitor"]
        if name:
            for screen in QApplication.screens():
                if screen.name() == name:
                    return screen
        return QApplication.primaryScreen()

    def anchor_point(self):
        """(centre x, y of the capsule's fixed edge, opens upwards) on the
        desktop, from the settings."""
        screen = self.target_screen().geometry()
        x, y = self.config["pos_x"], self.config["pos_y"]
        opens_up = self.config["pos_edge"] == "bottom" and y is not None
        if x is not None or y is not None:
            # The saved position may belong to a monitor that is gone now.
            probe = QPoint(
                screen.center().x() if x is None else x,
                (screen.top() + DEFAULT_TOP_GAP if y is None else y)
                + (-18 if opens_up else 18),
            )
            if QApplication.screenAt(probe) is None:
                x = y = None
                opens_up = False
        if x is None:
            x = screen.center().x()
        if y is None:
            y = screen.top() + DEFAULT_TOP_GAP
        return x, y, opens_up

    def apply_geometry(self):
        """Size and place the window from the size and position settings."""
        largest = max(self.scale_limits())
        width = math.ceil(self.canvas_w * largest / 2.0) * 2
        height = math.ceil(self.canvas_h * largest)
        x, y, self.opens_up = self.anchor_point()
        top = y + EDGE_PAD - height if self.opens_up else y - EDGE_PAD
        self.setGeometry(int(x - width // 2), int(top), width, height)
        self._window_mask_key = None
        if self.shadow is not None:
            self.shadow.follow(self.geometry())
        self.mark_moving()

    def reset_position(self):
        """Back to the top centre of the chosen screen."""
        self.config["pos_x"] = None
        self.config["pos_y"] = None
        self.config["pos_edge"] = "top"
        self.apply_geometry()
        self.persist_config()

    def mark_moving(self):
        """Something drawn has changed: paint on the next tick."""
        self._moving = True

    def approach(self, value, goal, rate, dt):
        """`value` moved towards `goal`; `rate` is the share of the distance
        covered in 1/60 s, so the motion looks the same at any frame rate."""
        if value == goal:
            return value
        value += (goal - value) * (1.0 - (1.0 - rate) ** (dt * 60.0))
        if abs(goal - value) < 0.0015:
            value = goal
        self._moving = True
        return value

    def set_openness(self, value):
        """Jump to an openness without animating."""
        self.t = self._spring_now = self._spring_before = float(value)
        self.vel = 0.0
        self._spring_debt = 0.0
        self.mark_moving()

    def step_spring(self, target, dt):
        """Advance the opening animation: a spring simulated in fixed steps,
        drawn between the last two of them."""
        if self._spring_now == target and self.vel == 0.0 and self.t == target:
            return
        self._spring_debt = min(self._spring_debt + dt, SPRING_STEP * 6)
        while self._spring_debt >= SPRING_STEP:
            self._spring_debt -= SPRING_STEP
            self._spring_before = self._spring_now
            self.vel = (self.vel + (target - self._spring_now) * 0.22) * 0.72
            self._spring_now += self.vel
            if abs(target - self._spring_now) < 0.002 and abs(self.vel) < 0.002:
                self._spring_now = target
                self.vel = 0.0
        if self._spring_now == target and self._spring_before == target:
            self.t = target
            self._spring_debt = 0.0
        else:
            share = self._spring_debt / SPRING_STEP
            self.t = self._spring_before + (self._spring_now - self._spring_before) * share
            if self.vel == 0.0 and self._spring_now == target and share >= 0.999:
                self.t = target
        self._moving = True

    def notification_showing(self):
        return self.notification_until > time.perf_counter()

    def show_volume_popup(self, seconds):
        self.volume_overlay_until = max(
            self.volume_overlay_until, time.perf_counter() + seconds
        )
        self.mark_moving()

    def side_fade(self):
        """Opacity of the round buttons beside the collapsed island."""
        return max(0.0, 1.0 - self.t / 0.15)

    def side_shapes(self):
        """(rect, radius, weight) of everything drawn beside the capsule."""
        fade = self.side_fade()
        shapes = []
        if fade <= 0.0:
            return shapes
        for rect in (self.timer_shortcut_rect, *self.extra_side_rects()):
            if not rect.isEmpty():
                shapes.append((rect, rect.height() / 2.0, fade))
        return shapes

    def on_settings_closed(self, _result=0):
        window, self.settings_window = self.settings_window, None
        if window is not None:
            window.deleteLater()
        if self.positioning:
            self.finish_positioning(notify=False)
        self.keep_visible_until_cursor_leaves = False
        if not self.ghost_mode:
            # Stay open for a moment so the eye finds the island again.
            self.settings_restore_until = time.perf_counter() + 2.5
            self.hovered = True
            self.set_openness(1.0)
        if not self.isVisible():
            self.show()
        result = self._set_window_pos(
            int(self.winId()),
            ctypes.c_void_p(-1),
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010 | 0x0040,
        )
        if not result:
            logging.debug("Could not restore island after closing settings")
        self.raise_()
        self.place_shadow()
        self.update()

    def begin_positioning(self, done=None):
        """Let the user drag the island to a new place. `done` is called
        when they finish."""
        self.positioning = True
        self.position_done = done
        self.timer_panel_open = False
        self.close_panels()
        self.set_openness(0.0)
        self.hovered = False
        self.show_position_guide(True)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def finish_positioning(self, notify=True):
        if not self.positioning:
            return
        self.positioning = False
        self.dragging_enabled = False
        self.show_position_guide(False)
        self.unsetCursor()
        self.persist_config()
        done, self.position_done = self.position_done, None
        self.mark_moving()
        self.update()
        if notify and done is not None:
            done()

    def start_drag(self, global_point):
        """Begin dragging: remember where on the capsule the mouse holds it."""
        scale = self.view_scale()
        pill = self.window_rect(
            self.current_pill_rect(), scale, self.view_offset(scale)
        )
        self.dragging_enabled = True
        self.drag_grab = global_point - self.mapToGlobal(pill.topLeft().toPoint())
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.show_position_guide(True)
        self.update()

    def drag_to(self, global_point):
        """Put the collapsed capsule where the mouse drags it, snapping to
        the middle of the screen and to its edges."""
        scale = self.scale_limits()[0]
        width = self.col_w * scale
        height = self.col_h * scale
        left = global_point.x() - self.drag_grab.x()
        top = global_point.y() - self.drag_grab.y()
        screen = QApplication.screenAt(global_point) or self.target_screen()
        area = screen.availableGeometry()
        full = screen.geometry()

        center = left + width / 2.0
        self.snapped_to_center = abs(center - full.center().x()) < 14
        if self.snapped_to_center:
            center = full.center().x()
        else:
            # Corners: a small margin from the side of the screen.
            margin = 12 + width / 2.0
            if center - width / 2.0 < area.left() + 26:
                center = area.left() + margin
            elif center + width / 2.0 > area.right() - 26:
                center = area.right() - margin
            center = max(area.left() + width / 2.0, min(area.right() - width / 2.0, center))

        top = max(area.top(), min(area.bottom() - height, top))
        if top < area.top() + 26:
            top = area.top() + 8
        elif top + height > area.bottom() - 26:
            top = area.bottom() - 8 - height
        # In the lower half of the screen the island opens upwards.
        opens_up = top + height / 2.0 > full.center().y()
        self.config["monitor"] = screen.name()
        self.config["pos_x"] = int(round(center))
        self.config["pos_edge"] = "bottom" if opens_up else "top"
        self.config["pos_y"] = int(round(top + height if opens_up else top))
        self.apply_geometry()
        self.update_position_guide(screen)

    def end_drag(self):
        self.dragging_enabled = False
        self.unsetCursor()
        if not self.positioning:
            self.show_position_guide(False)
            self.persist_config()
        self.update()

    def expanded_zone(self):
        """The area the mouse may roam without the island collapsing."""
        pill = QRectF(
            (self.canvas_w - self.exp_w) / 2.0,
            (self.canvas_h - EDGE_PAD - self.exp_h) if self.opens_up else EDGE_PAD,
            self.exp_w,
            self.exp_h,
        )
        return pill.adjusted(-10.0, -10.0, 10.0, 10.0)

    def side_button_at(self, position):
        """Name of the round button beside the collapsed island under the
        mouse, or None."""
        if self.side_fade() <= 0.0:
            return None
        if (
            self.config["timer_button_enabled"]
            and not self.timer_shortcut_rect.isEmpty()
            and self.timer_shortcut_rect.adjusted(-4, -4, 4, 4).contains(position)
        ):
            return "timer_btn"
        return self.extra_side_button_at(position)

    def update_visualizer(self, playing, dt):
        """Move the six bars: with the real sound when it is available,
        otherwise with a lively imitation while music plays."""
        audible = playing and not self.is_muted and self.current_volume > 0.01
        levels = self.spectrum_levels() if audible else None
        shown = self.config["right_zone"] == "Visualizer" or self.t > 0.02
        for index, value in enumerate(self.eq_bars):
            if levels is not None:
                goal = max(0.12, min(1.0, 0.12 + levels[index] * 0.95))
                rate = 0.5
            elif audible:
                # Pick a new height a few times a second, not every frame.
                self._eq_clock[index] -= dt
                if self._eq_clock[index] <= 0.0:
                    self._eq_clock[index] = random.uniform(0.07, 0.16)
                    self._eq_goals[index] = random.uniform(0.2, 1.0)
                goal = self._eq_goals[index]
                rate = 0.3
            else:
                goal = 0.18
                rate = 0.2
            if abs(goal - value) > 0.004:
                moved = value + (goal - value) * (1.0 - (1.0 - rate) ** (dt * 60.0))
                self.eq_bars[index] = moved
                if shown:
                    self._moving = True

    def draw_side_button(self, painter, rect, alpha, hover_val):
        """The body of a round button beside the collapsed island."""
        accent = self.current_accent_color()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(surface_fill(rect, accent, self.look, alpha))
        painter.drawEllipse(rect)
        if self.look.glass:
            painter.save()
            painter.setClipPath(rounded_rect_path(rect, rect.height() / 2.0))
            paint_glass_tint(painter, rect, accent, self.look, alpha)
            painter.restore()
        self.draw_button_hover(painter, rect, hover_val, alpha)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        edge = 150 if self.look.light else 62 if self.look.glass else 30
        painter.setPen(QPen(white(edge * alpha / 255), 1.0))
        painter.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def draw_player(self, painter, pill, t):
        """The collapsed line and, as the island opens, the player."""
        # The cover and the indicator travel between their collapsed and
        # expanded places; everything else fades in or out.
        cov_size = 22.0 + 30.0 * t
        cov_x = pill.left() + 7.0 + 11.0 * t
        cov_y = pill.top() + 7.0 + 11.0 * t
        cov_radius = 6.5 + 5.5 * t
        banner = self.banner_share()
        collapsed_alpha = int(
            255 * max(0.0, min(1.0, 1.0 - t / 0.15)) * (1.0 - banner)
        )

        if banner > 0.01 and t < 0.15:
            self.draw_banner(painter, pill, int(255 * banner * (1.0 - t / 0.15)))
        idle = self.idle()
        # The volume / brightness display and a call in progress take the
        # collapsed island; the artwork comes back as it opens.
        hud = self.hud_showing()
        calling = self.calling() and not self.timer_session_visible
        content_alpha = 0 if hud else collapsed_alpha
        cover_collapsed = 0 if (hud or calling) else collapsed_alpha
        opened = 1.0 if not (hud or calling) else min(1.0, t / 0.15)
        if hud and collapsed_alpha > 0:
            self.draw_hud(painter, pill, collapsed_alpha)
        if calling and content_alpha > 0:
            self.draw_call_badge(painter, QRectF(cov_x, cov_y, cov_size, cov_size), content_alpha)
        if idle and self.config["left_zone"] == "Album Art" and (cover_collapsed > 0 or t > 0.0):
            # Nothing plays: a clock or the weather where the artwork was.
            opacity = opened if t > 0.0 else cover_collapsed / 255.0
            if not self.idle_tile_shown():
                opacity = min(1.0, t / 0.4)
            if opacity > 0.0:
                painter.save()
                painter.setOpacity(opacity)
                self.draw_idle_tile(painter, QRectF(cov_x, cov_y, cov_size, cov_size), cov_radius)
                painter.restore()
        elif not idle and self.config["left_zone"] == "Album Art" and (cover_collapsed > 0 or t > 0.0):
            turn, opacity = self.cover_motion()
            painter.save()
            painter.setOpacity((opened if t > 0.0 else cover_collapsed / 255.0) * opacity)
            # After a track change the artwork turns in like a card.
            painter.translate(cov_x + cov_size / 2.0, 0.0)
            painter.scale(turn, 1.0)
            painter.translate(-(cov_x + cov_size / 2.0), 0.0)
            self.draw_rounded_cover(
                painter, QRectF(cov_x, cov_y, cov_size, cov_size), cov_radius
            )
            painter.restore()

        # Dots inside the capsule push the indicator to the left.
        inside = self.inside_dots_width() * max(0.0, 1.0 - t / 0.15)
        eq_right = pill.right() - (12.0 + 6.0 * t) - inside
        eq_cy = pill.top() + 18.0 + 39.0 * t
        indicator_alpha = (255 if t > 0.0 else collapsed_alpha) if not hud else (255 if t > 0.0 else 0)
        if calling:
            if content_alpha > 0:
                self.draw_call_timer(painter, pill.right() - 14.0 - inside, pill.center().y(), content_alpha)
        elif self.timer_session_visible and content_alpha > 0:
            self.draw_progress_ring(
                painter, QPointF(eq_right - 7.5, eq_cy), 6.5, content_alpha
            )
        elif self.downloads_showing() and content_alpha > 0:
            self.draw_downloads_ring(
                painter, QPointF(eq_right - 7.5, eq_cy), content_alpha
            )
        elif idle:
            pass  # no music, nothing to show on the right
        elif self.config["right_zone"] == "Visualizer" and indicator_alpha > 0:
            painter.setOpacity(indicator_alpha / 255.0)
            self.draw_equalizer(painter, eq_right, eq_cy, scale=0.85 + 0.15 * t)
            painter.setOpacity(1.0)
        elif self.config["right_zone"] == "Repeat Button" and content_alpha > 0:
            # Shows the repeat mode while collapsed; the expanded island has
            # the full repeat button in its controls row.
            self.draw_repeat_icon(painter, eq_right - 9.0, eq_cy, content_alpha, scale=0.8)

        if content_alpha > 0:
            self.draw_collapsed_text(painter, pill, content_alpha)
            self.draw_inside_dots(painter, pill, content_alpha)

        if t > 0.15:
            alpha = int(min(1.0, (t - 0.15) / 0.65) * 255)
            self.draw_expanded(painter, pill, alpha, cov_x, cov_y, cov_size)

    def sync_window_shape(self, pill, radius, scale, offset):
        """Cut the window down to the island, so clicks beside it reach what
        is under it. Rebuilt only when the shape changes."""
        placed = self.window_rect(pill, scale, offset).adjusted(-1.0, -1.0, 1.0, 1.0)
        sides = [
            self.window_rect(rect, scale, offset).adjusted(-4.0, -4.0, 4.0, 4.0)
            for rect, _radius, _weight in self.side_shapes()
        ]
        key = (
            round(placed.x()),
            round(placed.y()),
            round(placed.width()),
            round(placed.height()),
            round(radius * scale),
            tuple(
                (round(r.x()), round(r.y()), round(r.width()), round(r.height()))
                for r in sides
            ),
        )
        if key == self._window_mask_key:
            return
        self._window_mask_key = key
        mask_path = QPainterPath()
        corner = radius * scale + 1.0
        mask_path.addRoundedRect(placed, corner, corner)
        mask = QRegion(mask_path.toFillPolygon().toPolygon())
        for rect in sides:
            mask = mask.united(QRegion(rect.toAlignedRect(), QRegion.RegionType.Ellipse))
        self.setMask(mask)
        # This paint was still clipped to the old shape: paint once more so
        # the part the new shape uncovered is not left empty.
        self.update()

    def draw_heart_icon(self, painter, center, alpha):
        """The like button: an outline, filled with the accent once liked."""
        heart = QPainterPath()
        x, y = center.x(), center.y() + 0.5
        heart.moveTo(x, y + 6.0)
        heart.cubicTo(x - 9.5, y - 0.5, x - 6.0, y - 8.0, x, y - 3.2)
        heart.cubicTo(x + 6.0, y - 8.0, x + 9.5, y - 0.5, x, y + 6.0)
        if self.liked:
            color = with_alpha(self.current_accent_color(), alpha)
            painter.setBrush(color)
        else:
            color = with_alpha(
                mix_colors(self.look.secondary, self.look.primary, self.btn_hover_anim["like"]),
                alpha,
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(round_pen(color, 1.5))
        painter.drawPath(heart)

    def toggle_like(self):
        if self.liked is None or not self.bridge_can_command():
            return
        wanted = not self.liked
        self.pending_liked = (wanted, time.perf_counter() + OPTIMISTIC_HOLD_SEC)
        self.liked = wanted
        self.bridge.queue_command({"type": "like", "value": wanted})
        self.update()

    def add_to_playlist(self):
        """SoundCloud: open its "Add to playlist" window in the player's tab."""
        if self.liked is None or self.media_site != "soundcloud" or not self.bridge_can_command():
            return
        self.bridge.queue_command({"type": "playlist"})
        self.notify(tr("Выберите плейлист в браузере"), seconds=2.0)

    def set_timer_mode(self, mode):
        if self.timer_mode == mode:
            return
        self.pause_timer()
        self.timer_mode = mode
        self.timer_session_visible = False
        if mode == "Timer":
            # A countdown left paused half-way starts over, so the panel and
            # the compact row never show two different times.
            self.reset_countdown()


CANVAS_H = 310.0


# Gap between the capsule and the edge of its window.
EDGE_PAD = 6.0


# How far below the top of the screen the island hangs by default.
DEFAULT_TOP_GAP = 42


# Width of the collapsed island when it does not follow the track name.
SHORT_TITLE_WIDTH = 236.0


COLLAPSED_WIDTH_RANGE = (170.0, 370.0)


# Width of the empty capsule shown without music ("blank").
BLANK_WIDTH = 124.0


# While nothing moves the island checks the mouse this often instead of
# drawing frames.
IDLE_INTERVAL_MS = 60


# The opening animation is simulated at this rate whatever the frame rate is.
SPRING_STEP = 1.0 / 60.0


def setup_logging():
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    # Rotating, so the log cannot grow without limit.
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    # PyQt ends the process on an unhandled error in an event handler unless
    # an exception hook is installed. Log it instead and keep running.
    recently_logged = {}

    def log_unhandled(exc_type, exc, traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, traceback)
            return
        # The same error in a 60 fps handler must not flood the log.
        key = (exc_type, str(exc))
        now = time.monotonic()
        if now - recently_logged.get(key, -60.0) < 30.0:
            return
        if len(recently_logged) > 100:
            recently_logged.clear()
        recently_logged[key] = now
        logging.error("Unhandled error", exc_info=(exc_type, exc, traceback))

    sys.excepthook = log_unhandled
    threading.excepthook = lambda args: log_unhandled(
        args.exc_type, args.exc_value, args.exc_traceback
    )


def acquire_single_instance():
    """Return a lock to keep for the app's lifetime, or None if it already runs."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [
        ctypes.c_void_p,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.LPCWSTR,
    ]
    create_mutex.restype = ctypes.wintypes.HANDLE
    error_already_exists = 183
    handle = create_mutex(None, False, "Local\\DefaultIslandSingleInstance")
    if handle and ctypes.get_last_error() == error_already_exists:
        return None
    # Without a handle the check is unavailable; start anyway.
    return handle or True


def main():
    setup_logging()
    instance_lock = acquire_single_instance()
    if instance_lock is None:
        logging.info("Default Island is already running; not starting a second copy")
        return 0
    app = QApplication(sys.argv)
    # The island is a tool window, so Qt does not count it as the "last
    # window": closing a dialog must not end the app.
    app.setQuitOnLastWindowClosed(False)
    try:
        island = DynamicIsland()
        island.show()
    except Exception as error:
        logging.exception("Could not start Default Island")
        QMessageBox.critical(None, APP_NAME, tr("Не удалось запустить приложение:") + f"\n{error}")
        return 1
    app.aboutToQuit.connect(island.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
