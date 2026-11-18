"""Everything the island shows besides the player.

DynamicIsland (island.py) inherits this mixin and calls its hooks from its own
tick, paint and mouse handlers. The system services behind the features live
in their own modules (island_audio, island_devices, island_inputs,
island_lyrics, island_notifications); each is optional, so a module that
fails to load only switches its feature off.
"""
import ctypes
import datetime
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from PyQt6.QtCore import QMimeData, QObject, QPointF, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QDrag, QFont, QFontMetricsF, QIcon,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from island_core import (
    APP_NAME, APP_VERSION, DANGER_COLOR, GITHUB_REPO, LYRICS_CACHE_DIR, load_reminders,
    mix_colors, next_daily_time, round_pen, rounded_rect_path, save_reminders, ui_font,
    user32_function, white, with_alpha,
)
from island_i18n import language, register, tr

try:
    from island_audio import SpectrumAnalyzer
except Exception:  # a broken optional module must not stop the island
    logging.exception("The audio visualizer is unavailable")
    SpectrumAnalyzer = None
try:
    from island_devices import DeviceMonitor, GpuMonitor, battery_status
except Exception:
    logging.exception("Device monitoring is unavailable")
    DeviceMonitor = GpuMonitor = battery_status = None
try:
    from island_inputs import (
        BrightnessWatch, Microphone, RecordingWatch, Speakers, caps_lock_on, keyboard_layout,
        list_outputs, privacy_usage, set_default_output,
    )
except Exception:
    logging.exception("Microphone and keyboard helpers are unavailable")
    Microphone = caps_lock_on = keyboard_layout = None
    list_outputs = privacy_usage = set_default_output = None
    BrightnessWatch = RecordingWatch = Speakers = None
try:
    from island_lyrics import SAME_LANGUAGE, LyricsProvider, Translator
except Exception:
    logging.exception("Lyrics are unavailable")
    LyricsProvider = Translator = None
    SAME_LANGUAGE = "same"
try:
    from island_notifications import NotificationWatcher
except Exception:
    logging.exception("Windows notifications are unavailable")
    NotificationWatcher = None
try:
    from island_calls import PHONE_LINK, ToastButtons, call_app, focus_app, incoming_call
except Exception:
    logging.exception("Calls are unavailable")
    PHONE_LINK = "Phone Link"
    ToastButtons = call_app = focus_app = incoming_call = None
try:
    from island_weather import TRANSLATIONS as WEATHER_TRANSLATIONS
    from island_weather import WeatherProvider, describe as describe_weather, format_temperature
except Exception:
    logging.exception("Weather is unavailable")
    WEATHER_TRANSLATIONS = {}
    WeatherProvider = describe_weather = format_temperature = None

register(WEATHER_TRANSLATIONS)
register({
    "Входящий звонок · {app}": ("Incoming call · {app}", "Вхідний дзвінок · {app}"),
    "Входящий звонок": ("Incoming call", "Вхідний дзвінок"),
    "Неизвестный номер": ("Unknown caller", "Невідомий номер"),
    "Связь с телефоном": ("Phone Link", "Зв'язок із телефоном"),
    "Соединение…": ("Connecting…", "З'єднання…"),
    "Звонок завершён · {time}": ("Call ended · {time}", "Дзвінок завершено · {time}"),
    "Не получилось ответить — ответьте в приложении": (
        "Could not answer — answer in the app",
        "Не вдалося відповісти — дайте відповідь у застосунку",
    ),
    "Не получилось отклонить — отклоните в приложении": (
        "Could not decline — decline in the app",
        "Не вдалося відхилити — відхиліть у застосунку",
    ),
    "Город «{city}» не найден": ("City “{city}” not found", "Місто «{city}» не знайдено"),
    "Погода: —": ("Weather: —", "Погода: —"),
    "Скопировано: {text}": ("Copied: {text}", "Скопійовано: {text}"),
    "Скопирована картинка {w}×{h}": ("Image copied {w}×{h}", "Скопійовано зображення {w}×{h}"),
    "Снова в буфере обмена": ("Back on the clipboard", "Знову в буфері обміну"),
    "Буфер обмена пуст": ("The clipboard history is empty", "Буфер обміну порожній"),
    "Буфер обмена": ("Clipboard", "Буфер обміну"),
    "Картинка {w}×{h}": ("Image {w}×{h}", "Зображення {w}×{h}"),
    "Очистить": ("Clear", "Очистити"),
    "Обновление до версии {version}": ("Updating to version {version}", "Оновлення до версії {version}"),
    "Скачивается, островок перезапустится сам": (
        "Downloading; the island restarts by itself", "Завантажується, острів перезапуститься сам",
    ),
    "Не удалось скачать обновление": ("Could not download the update", "Не вдалося завантажити оновлення"),
})
register({
    "Подключено": ("Connected", "Підключено"),
    "Отключено": ("Disconnected", "Відключено"),
    "Низкий заряд": ("Low battery", "Низький заряд"),
    "{name}: осталось {percent}%": ("{name}: {percent}% left", "{name}: залишилось {percent}%"),
    "Заряд {percent}%": ("Battery {percent}%", "Заряд {percent}%"),
    "Зарядка": ("Charging", "Заряджання"),
    "Ноутбук": ("Laptop", "Ноутбук"),
    "Напоминание": ("Reminder", "Нагадування"),
    "Загрузка завершена": ("Download finished", "Завантаження завершено"),
    "Загрузки: {count}": ("Downloads: {count}", "Завантаження: {count}"),
    "Доступна версия {version}": ("Version {version} is available", "Доступна версія {version}"),
    "Нажмите, чтобы открыть страницу загрузки": (
        "Click to open the download page",
        "Натисніть, щоб відкрити сторінку завантаження",
    ),
    "Установлена последняя версия": ("You have the latest version", "Встановлено останню версію"),
    "Не удалось проверить обновления": ("Could not check for updates", "Не вдалося перевірити оновлення"),
    "Устройство вывода": ("Output device", "Пристрій виведення"),
    "Звук: {name}": ("Sound: {name}", "Звук: {name}"),
    "Не удалось переключить устройство": ("Could not switch the device", "Не вдалося перемкнути пристрій"),
    "Нет устройств вывода": ("No output devices", "Немає пристроїв виведення"),
    "Микрофон выключен": ("Microphone off", "Мікрофон вимкнено"),
    "Микрофон включён": ("Microphone on", "Мікрофон увімкнено"),
    "Микрофон не найден": ("No microphone found", "Мікрофон не знайдено"),
    "Перетащите островок · отпустите там, где он нужен": (
        "Drag the island · drop it where you want it",
        "Перетягніть острівець · відпустіть там, де він потрібен",
    ),
    "Отпустите, чтобы положить на полку": ("Drop to put it on the shelf", "Відпустіть, щоб покласти на полицю"),
    "Полка заполнена": ("The shelf is full", "Полиця заповнена"),
    "Настройки": ("Settings", "Налаштування"),
    "Вернуть в центр": ("Back to the centre", "Повернути в центр"),
    "Режим призрака": ("Ghost mode", "Режим привида"),
    "Проверить обновления": ("Check for updates", "Перевірити оновлення"),
    "Закрыть Default Island": ("Quit Default Island", "Закрити Default Island"),
    "Скриншот": ("Screenshot", "Знімок екрана"),
    "Блокировка": ("Lock", "Блокування"),
    "Напомнить": ("Remind", "Нагадати"),
    "Звук": ("Sound", "Звук"),
    "Микрофон": ("Microphone", "Мікрофон"),
})

# Wireless device warnings fire once per step on the way down.
LOW_BATTERY_STEPS = (30, 20, 10)
LAPTOP_LOW_STEPS = (20, 10)
BANNER_SECONDS = 4.5
NOTIFICATION_SECONDS = 6.0
MAX_BANNERS = 6
MAX_SHELF_ITEMS = 12
UPDATE_CHECK_EVERY_SEC = 24 * 3600
UPDATE_RETRY_SEC = 30 * 60
# Downloads not reported for this long are dropped (the browser went away).
DOWNLOADS_STALE_SEC = 15.0
ROW_HEIGHT = 40.0
SIDE_SIZE = 28.0
QUICK_ICONS = ("output", "mic", "screenshot", "lock", "note", "clipboard")
MAX_CLIPS = 10
MAX_CLIP_IMAGES = 3
MAX_CLIP_TEXT = 200_000
HUD_SECONDS = 1.6
HUD_WIDTH = 230.0
RECORD_RED = QColor(255, 69, 58)
# Password managers mark what they copy with these formats: never shown.
SECRET_CLIP_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
    "Clipboard Viewer Ignore",
)
# An incoming call rings at most this long when its toast never goes away.
RING_MAX_SEC = 60.0
# After "Answer" the app has this long to start talking.
ANSWER_WAIT_SEC = 15.0
# A call app that let go of the microphone this long has hung up.
CALL_GRACE_SEC = 3.0
CALL_GREEN = QColor(48, 200, 94)
CALL_RED = QColor(235, 77, 75)

# Names of days and months, so the date does not depend on the Windows locale.
WEEKDAYS = {
    "ru": ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"),
    "uk": ("понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
WEEKDAYS_SHORT = {
    "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
    "uk": ("пн", "вт", "ср", "чт", "пт", "сб", "нд"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
MONTHS = {
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"),
    "uk": ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня",
           "вересня", "жовтня", "листопада", "грудня"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"),
}
MONTHS_SHORT = {
    "ru": ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.",
           "нояб.", "дек."),
    "uk": ("січ.", "лют.", "бер.", "квіт.", "трав.", "черв.", "лип.", "серп.", "вер.", "жовт.",
           "лист.", "груд."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


def ease_out(share):
    share = max(0.0, min(1.0, share))
    return 1.0 - (1.0 - share) ** 3


def short_date(moment):
    """"вт, 18 нояб." / "Tue, 18 Nov"."""
    lang = language() if language() in WEEKDAYS else "ru"
    return f"{WEEKDAYS_SHORT[lang][moment.weekday()]}, {moment.day} {MONTHS_SHORT[lang][moment.month - 1]}"


def long_date(moment):
    """"Вторник, 18 ноября" / "Tuesday, 18 November"."""
    lang = language() if language() in WEEKDAYS else "ru"
    text = f"{WEEKDAYS[lang][moment.weekday()]}, {moment.day} {MONTHS[lang][moment.month - 1]}"
    return text[:1].upper() + text[1:]


class _Ring:
    """An incoming call: the toast that announced it, until it is gone."""

    def __init__(self, app, caller, toast_id, busy):
        self.app = app
        self.caller = caller
        self.toast_id = toast_id
        self.busy = busy  # calling apps already talking when it began to ring
        self.started = time.monotonic()
        self.answered_at = None


class _Call:
    """A call in progress: a calling app holds the microphone."""

    def __init__(self, app, caller=""):
        self.app = app
        self.caller = caller
        self.since = time.monotonic()
        self.quiet_since = None


class _Banner:
    """A message that grows the collapsed island for a few seconds."""

    def __init__(self, kind, title, text="", seconds=BANNER_SECONDS, value=None, url=""):
        self.kind = kind  # icon: "bell", "battery", "charge", "device", "reminder", "download", "update"
        self.title = title
        self.text = text
        self.seconds = seconds
        self.value = value  # percent for the battery icons
        self.url = url
        self.shown_at = None


class UpdateChecker(QObject):
    """Asks GitHub for the newest release, off the GUI thread; downloads its
    installer for an automatic update."""

    finished = pyqtSignal(object, bool)  # (version, page, installer url) or None; manual
    downloaded = pyqtSignal(str, str)  # (version, path of the installer) or ("", "")

    def start(self, manual):
        threading.Thread(target=self._run, args=(manual,), daemon=True).start()

    def _run(self, manual):
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "DefaultIsland-update-check", "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read(256 * 1024).decode("utf-8"))
            tag = str(data.get("tag_name") or "")
            page = str(data.get("html_url") or "")
            if not page.startswith("https://github.com/"):
                page = f"https://github.com/{GITHUB_REPO}/releases"
            installer = ""
            for asset in data.get("assets") or []:
                name = str(asset.get("name") or "").casefold()
                link = str(asset.get("browser_download_url") or "")
                if name.endswith("setup.exe") and link.startswith(f"https://github.com/{GITHUB_REPO}/"):
                    installer = link
                    break
            result = (tag.lstrip("vV"), page, installer)
        except Exception:
            logging.info("Update check failed", exc_info=True)
            result = None
        self.finished.emit(result, manual)

    def download(self, version, link):
        threading.Thread(target=self._download, args=(version, link), daemon=True).start()

    def _download(self, version, link):
        safe = "".join(ch for ch in version if ch.isalnum() or ch in ".-") or "new"
        path = os.path.join(tempfile.gettempdir(), f"DefaultIsland-Setup-{safe}.exe")
        request = urllib.request.Request(link, headers={"User-Agent": "DefaultIsland-update"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(path + ".part", "wb") as out:
                shutil.copyfileobj(response, out, 1024 * 1024)
            # An installer is tens of megabytes; anything tiny is an error page.
            if os.path.getsize(path + ".part") < 1024 * 1024:
                raise OSError("the downloaded installer is too small")
            os.replace(path + ".part", path)
        except Exception:
            logging.warning("Could not download the update", exc_info=True)
            try:
                os.remove(path + ".part")
            except OSError:
                pass
            self.downloaded.emit("", "")
            return
        self.downloaded.emit(version, path)


def version_tuple(text):
    parts = []
    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


class PositionGuide(QWidget):
    """A see-through window over the screen while the island is moved: the
    centre line it snaps to and a short hint."""

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
        self.snapped = False
        self.accent = QColor("#D6B58F")

    def cover(self, screen):
        if self.geometry() != screen.geometry():
            self.setGeometry(screen.geometry())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.width() / 2.0
        color = with_alpha(self.accent, 235) if self.snapped else white(170)
        top, bottom = QPointF(center, 0.0), QPointF(center, float(self.height()))
        # A dark edge under the line keeps it visible on light wallpapers too.
        painter.setPen(QPen(QColor(0, 0, 0, 70), 4.0 if self.snapped else 3.5))
        painter.drawLine(top, bottom)
        painter.setPen(QPen(color, 2.0 if self.snapped else 1.5, Qt.PenStyle.DashLine))
        painter.drawLine(top, bottom)
        # The hint sits in the middle of the screen, out of the island's way.
        font = ui_font(10, QFont.Weight.DemiBold)
        painter.setFont(font)
        text = tr("Перетащите островок · отпустите там, где он нужен")
        width = QFontMetricsF(font).horizontalAdvance(text) + 36.0
        box = QRectF(center - width / 2.0, self.height() / 2.0 - 18.0, width, 36.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 19, 18, 215))
        painter.drawRoundedRect(box, 18.0, 18.0)
        painter.setPen(QColor(244, 240, 232))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def make_tray_icon():
    """A small drawn icon: a dark capsule, as the island looks."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(white(120), 2.0))
    painter.setBrush(QColor(22, 21, 19))
    painter.drawRoundedRect(QRectF(4.0, 20.0, 56.0, 24.0), 12.0, 12.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#D6B58F"))
    painter.drawEllipse(QPointF(18.0, 32.0), 5.0, 5.0)
    painter.end()
    return QIcon(pixmap)


class ExtrasMixin:
    # ----- Life cycle ---------------------------------------------------

    def extras_init(self):
        self.panel = None  # "output" while that panel replaces the player
        self.panel_left_at = 0.0
        self.panel_rects = []  # (rect, hover key, action)
        self.output_devices = ()
        self.side_rects = {}  # name -> rect of the round buttons beside the capsule
        self.quick_rects = []  # (rect, name)
        self.shelf_rects = []  # (rect, index, close rect)
        self.shelf = []  # file paths
        self.shelf_drop_hover = False
        self.shelf_press = None  # (index, press position)
        self.banners = []
        self._banner_share = 0.0
        self.downloads = ()
        self.downloads_names = ()
        self._downloads_seen = 0.0
        self.mic_muted = None  # None: no microphone
        self.privacy = (False, False)  # (microphone used, camera used)
        self._last_layout = None
        self._last_caps = None
        self._next_layout_check = 0.0
        self._next_slow_check = 0.0
        self._next_reminder_check = 0.0
        self._battery = None
        self._battery_alerted = None
        self._device_alerted = {}
        self.reminders = load_reminders()
        self._track_change_at = -10.0
        self._glint_at = -10.0
        self._was_hovered = False
        self._blur_cache = None
        self.lyrics = None
        self._lyrics_track = None
        self._lyrics_retry_at = 0.0
        self._lyrics_line_shown = ""
        self.analyzer = None
        self.devices = None
        self.gpu = None
        self.microphone = None
        self.notifications = None
        self.weather = None
        self.ring = None  # _Ring while a call rings
        self.call = None  # _Call while a call is on
        self.call_rects = []  # (rect, hover key, action) of the call buttons
        self.call_buttons = None  # ToastButtons, started on the first press
        self._pressed_for = ""  # app of the last press on a call toast
        self.speakers = None
        self.brightness = None
        self.recording = None
        self.recording_state = None  # (who, since) while the screen is recorded
        self.translator = None
        self._translation_for = None  # ((title, artist), language) asked for
        self._translation = None  # {line: translation}, SAME_LANGUAGE or None
        self.hud = None  # ("volume" | "brightness", percent, muted, until)
        self._hud_seen = {}  # last volume / brightness read
        self._next_hud_check = 0.0
        # Newest first: ("text", text, None, one-line preview) or
        # ("image", label, thumbnail QPixmap, the whole QImage).
        self.clips = []
        self._clip_ignore_until = 0.0
        self._clipboard_hooked = False
        self.tray = None
        self.position_guide = None
        self.update_checker = UpdateChecker()
        self.update_checker.finished.connect(self.on_update_result)
        self.update_checker.downloaded.connect(self.on_update_downloaded)
        self._updating = False
        self._next_update_check = time.monotonic() + 30.0
        self.setAcceptDrops(True)
        for key in (
            "mic_side", "pos_done", "panel_close",
            "call_accept", "call_decline", "call_side", "call_mute", "call_open", "clip_clear",
            *(f"q_{name}" for name in QUICK_ICONS),
            *(f"clip_{index}" for index in range(MAX_CLIPS)),
            *(f"out_{index}" for index in range(8)),
            *(f"shelf_{index}" for index in range(MAX_SHELF_ITEMS)),
        ):
            self.btn_hover_anim[key] = 0.0

    def start_extra_services(self):
        def start(factory, *args):
            if factory is None:
                return None
            try:
                service = factory(*args)
                if isinstance(service, threading.Thread):
                    service.start()
                return service
            except Exception:
                logging.exception("Could not start %s", getattr(factory, "__name__", factory))
                return None

        self.analyzer = start(SpectrumAnalyzer, 6)
        self.devices = start(DeviceMonitor)
        self.gpu = start(GpuMonitor)
        self.lyrics = start(LyricsProvider, LYRICS_CACHE_DIR)
        self.notifications = start(NotificationWatcher)
        self.microphone = start(Microphone)
        self.weather = start(WeatherProvider)
        self.translator = start(Translator, LYRICS_CACHE_DIR)
        self.speakers = start(Speakers)
        self.brightness = start(BrightnessWatch)
        self.recording = start(RecordingWatch)
        if not self._clipboard_hooked:
            QApplication.clipboard().dataChanged.connect(self.on_clipboard_changed)
            self._clipboard_hooked = True
        self.extras_apply_settings()

    def stop_extra_services(self):
        for service in (
            self.analyzer, self.devices, self.gpu, self.lyrics, self.notifications,
            self.weather, self.call_buttons, self.translator, self.brightness, self.recording,
        ):
            if service is not None:
                try:
                    service.stop()
                except Exception:
                    logging.exception("Could not stop a service")
        if self.tray is not None:
            self.tray.hide()
        if self.position_guide is not None:
            self.position_guide.close()

    def extras_apply_settings(self):
        config = self.config
        if self.notifications is not None:
            # Incoming calls are read from the toasts as well.
            self.safe(
                self.notifications.set_enabled,
                config["notifications_enabled"] or config["calls_enabled"],
            )
        if self.weather is not None:
            self.safe(self.weather.set_city, config["weather_city"], language())
        if not config["calls_enabled"]:
            self.drop_ring()
            self.call = None
        if self.recording is not None:
            self.safe(self.recording.set_enabled, config["recording_enabled"])
        if not config["recording_enabled"]:
            self.recording_state = None
        if not config["clipboard_enabled"]:
            self.clips = []
            if self.panel == "clipboard":
                self.close_panels()
        if not config["hud_enabled"]:
            self.hud = None
        if dict.get(config, "music_only"):
            self.shelf = []
        if self.gpu is not None:
            self.safe(self.gpu.set_enabled, config["center_zone"] == "GPU")
        self._blur_cache = None
        self.sync_tray()
        if not config["downloads_enabled"]:
            self.downloads = ()

    def safe(self, function, *args, default=None):
        """Call into an optional service; a failure is logged, not raised."""
        try:
            return function(*args)
        except Exception:
            name = getattr(function, "__qualname__", str(function))
            last = getattr(self, "_safe_logged", {})
            now = time.monotonic()
            if now - last.get(name, -600.0) > 600.0:
                logging.exception("%s failed", name)
                last[name] = now
                self._safe_logged = last
            return default

    # ----- Tick ---------------------------------------------------------

    def extras_tick(self, now, dt, cursor):
        config = self.config
        # Opening the island sends a glint across the glass.
        if self.hovered and not self._was_hovered and self.t < 0.1:
            self._glint_at = now
        self._was_hovered = self.hovered
        if self.glint_share(now) is not None:
            self.mark_moving()
        if now - self._track_change_at < 0.6:
            self.mark_moving()

        self.update_banners(now, dt)

        if self.call_buttons is not None:
            for kind, pressed in self.safe(self.call_buttons.results, default=[]) or []:
                if pressed:
                    continue
                self.notify(
                    tr("Не получилось ответить — ответьте в приложении")
                    if kind == "accept"
                    else tr("Не получилось отклонить — отклоните в приложении"),
                    seconds=3.0,
                    force=True,
                )
                # The app's own window is where it can be done instead.
                if focus_app is not None and self._pressed_for:
                    self.safe(focus_app, self._pressed_for)

        if now >= self._next_hud_check:
            self._next_hud_check = now + 0.1
            self.check_hud(now)
        if self.hud is not None and now >= self.hud[3]:
            self.hud = None
            self.mark_moving()

        if self.analyzer is not None:
            wanted = (
                config["real_visualizer"]
                and self.is_playing
                and (config["right_zone"] == "Visualizer" or self.t > 0.02)
            )
            self.safe(self.analyzer.set_enabled, bool(wanted))

        if now >= self._next_layout_check:
            self._next_layout_check = now + 0.1
            self.check_layout()

        if now >= self._next_slow_check:
            self._next_slow_check = now + 1.0
            self.slow_checks()

        if self.lyrics is not None and config["lyrics_enabled"] and self.title:
            track = (self.title, self.artist)
            retry = (
                now >= self._lyrics_retry_at
                and self.safe(self.lyrics.get, self.title, self.artist) is None
            )
            if track != self._lyrics_track or retry:
                # Ask again each minute until an answer arrives (network trouble).
                self._lyrics_track = track
                self._lyrics_retry_at = now + 60.0
                self.safe(self.lyrics.request, self.title, self.artist, self.duration_sec)
            if config["lyrics_translate"] and self.translator is not None:
                wanted = (track, language())
                if self._translation_for != wanted or self._translation is None:
                    lyrics = self.safe(self.lyrics.get, self.title, self.artist)
                    if lyrics is not None and lyrics.found and lyrics.synced:
                        lines = tuple(text for _start, text in lyrics.lines)
                        if self._translation_for != wanted:
                            # Asked once per song; the answer is picked up below.
                            self._translation_for = wanted
                            self._translation = None
                            self.safe(self.translator.request, lines, language())
                        self._translation = self.safe(self.translator.get, lines, language())
                        if self._translation is not None:
                            self.mark_moving()
            # Repaint when the sung line changes; karaoke fills every frame
            # while a line is being sung.
            line = self.lyrics_line()
            if line != self._lyrics_line_shown:
                self._lyrics_line_shown = line
                self.mark_moving()
            elif line and self.config["lyrics_karaoke"] and self.clock_ticking:
                progress = self.lyrics_progress()
                if progress is not None and progress.word_share < 1.0:
                    self.mark_moving()

        if self.positioning or self.dragging_enabled:
            self.mark_moving()

        monotonic = time.monotonic()
        if (config["update_check_enabled"] or config["auto_update"]) and monotonic >= self._next_update_check:
            self._next_update_check = monotonic + UPDATE_CHECK_EVERY_SEC
            self.update_checker.start(False)

    def check_layout(self):
        if not self.config["layout_indicator_enabled"] or keyboard_layout is None:
            return
        layout = self.safe(keyboard_layout, default="")
        caps = self.safe(caps_lock_on, default=False)
        if self._last_layout is not None and layout and layout != self._last_layout:
            self.notify(layout, seconds=0.5, keep_width=True)
        if self._last_caps is not None and caps != self._last_caps:
            self.notify("CAPS" if caps else "caps", seconds=0.5, keep_width=True)
        if layout:
            self._last_layout = layout
        self._last_caps = caps

    def slow_checks(self):
        config = self.config
        if self.microphone is not None and config["mic_button_enabled"]:
            muted = self.safe(self.microphone.state)
            if muted != self.mic_muted:
                self.mic_muted = muted
                self.mark_moving()
        if privacy_usage is not None and config["privacy_dots_enabled"]:
            usage = self.safe(privacy_usage)
            state = (bool(usage.microphone), bool(usage.camera)) if usage else (False, False)
            if state != self.privacy:
                self.privacy = state
                self.mark_moving()
        elif self.privacy != (False, False):
            self.privacy = (False, False)
            self.mark_moving()
        recording = None
        if self.recording is not None and config["recording_enabled"]:
            recording = self.safe(self.recording.state)
        if (recording is None) != (self.recording_state is None):
            self.mark_moving()
        self.recording_state = recording

        # The browser reports every second while something downloads; one
        # that closed or crashed mid-download would leave the ring spinning.
        if self.downloads and time.monotonic() - self._downloads_seen > DOWNLOADS_STALE_SEC:
            self.downloads = ()
            self.mark_moving()

        if config["battery_alerts_enabled"]:
            self.check_laptop_battery()
        if self.devices is not None:
            for event in self.safe(self.devices.poll_events, default=[]) or []:
                self.on_device_event(event)

        if self.notifications is not None and (config["notifications_enabled"] or config["calls_enabled"]):
            for toast in self.safe(self.notifications.poll, default=[]) or []:
                hit = None
                if config["calls_enabled"] and incoming_call is not None:
                    hit = self.safe(incoming_call, toast)
                if hit is not None:
                    self.on_incoming_call(toast, *hit)
                    continue
                if not config["notifications_enabled"] or config["dnd_mode"]:
                    continue
                title = toast.title or toast.app
                text = toast.body if toast.title else ""
                banner = _Banner("bell", title, text, NOTIFICATION_SECONDS)
                banner.app = toast.app
                self.push_banner(banner)
        self.update_call()

        if time.time() >= self._next_reminder_check:
            self._next_reminder_check = time.time() + 1.0
            self.fire_due_reminders()

    def check_laptop_battery(self):
        if battery_status is None:
            return
        status = self.safe(battery_status)
        if status is None:
            return
        previous, self._battery = self._battery, status
        if previous is None:
            self._battery_alerted = None
            return
        if status.charging and not previous.charging:
            self.push_banner(_Banner("charge", tr("Зарядка"), tr("Заряд {percent}%").format(percent=status.percent), 3.0, status.percent))
            self._battery_alerted = None
        elif not status.charging:
            for step in LAPTOP_LOW_STEPS:
                if status.percent <= step and (self._battery_alerted is None or step < self._battery_alerted):
                    self._battery_alerted = step
                    self.push_banner(
                        _Banner("battery", tr("Низкий заряд"),
                                tr("{name}: осталось {percent}%").format(name=tr("Ноутбук"), percent=status.percent),
                                value=status.percent),
                        force=step <= 10,
                    )
                    break

    def on_device_event(self, event):
        device = event.device
        if event.kind == "connected" and self.config["device_alerts_enabled"]:
            text = device.name
            if device.percent is not None:
                text = f"{device.name} · {device.percent}%"
            self.push_banner(_Banner("device", tr("Подключено"), text, 3.5, device.percent))
            self._device_alerted.pop(device.device_id, None)
        elif event.kind == "disconnected" and self.config["device_alerts_enabled"]:
            self.notify(f"{tr('Отключено')}: {device.name}", seconds=2.5)
        if device.percent is None or not self.config["battery_alerts_enabled"]:
            return
        alerted = self._device_alerted.get(device.device_id)
        if alerted is not None and device.percent > alerted + 5:
            # Charged again: warn again next time it runs low.
            self._device_alerted.pop(device.device_id, None)
            alerted = None
        for step in LOW_BATTERY_STEPS:
            if device.percent <= step and (alerted is None or step < alerted):
                self._device_alerted[device.device_id] = step
                self.push_banner(
                    _Banner("battery", tr("Низкий заряд"),
                            tr("{name}: осталось {percent}%").format(name=device.name, percent=device.percent),
                            value=device.percent),
                    force=step <= 10,
                )
                break

    # ----- Reminders ----------------------------------------------------

    def reminders_changed(self):
        self.reminders = load_reminders()
        self._next_reminder_check = 0.0

    def fire_due_reminders(self):
        now = time.time()
        due = [reminder for reminder in self.reminders if reminder["at"] <= now]
        if not due:
            return
        for reminder in due:
            self.push_banner(_Banner("reminder", tr("Напоминание"), reminder["text"], 10.0), force=True)
            if reminder["repeat"] == "daily":
                reminder["at"] = next_daily_time(reminder["at"], now)
            else:
                self.reminders.remove(reminder)
        if not self.config["dnd_mode"]:
            user32_function("MessageBeep", [ctypes.c_uint], ctypes.c_int)(0x00000040)
        try:
            save_reminders(self.reminders)
        except OSError:
            logging.exception("Could not save reminders")

    # ----- Calls --------------------------------------------------------

    def on_incoming_call(self, toast, caller, app):
        if self.config["dnd_mode"]:
            return
        if self.ring is not None and self.ring.toast_id == toast.toast_id:
            return
        busy = {self.call.app} if self.call is not None else set()
        self.ring = _Ring(app, caller, toast.toast_id, busy)
        label = tr("Связь с телефоном") if app == PHONE_LINK else app
        banner = _Banner(
            "call",
            caller or tr("Неизвестный номер"),
            tr("Входящий звонок · {app}").format(app=label) if label else tr("Входящий звонок"),
            RING_MAX_SEC + 5.0,
        )
        # The call goes in front of every other message.
        self.banners = [banner] + [b for b in self.banners if b.kind != "call"]
        del self.banners[MAX_BANNERS:]
        self.mark_moving()

    def ringing(self):
        """True while an incoming call waits for an answer."""
        return self.ring is not None and self.ring.answered_at is None

    def drop_ring(self):
        self.ring = None
        if any(banner.kind == "call" for banner in self.banners):
            self.banners = [banner for banner in self.banners if banner.kind != "call"]
            self.mark_moving()

    def update_call(self):
        """Follow the call in progress and the call that rings; once a second."""
        if not self.config["calls_enabled"] or call_app is None:
            return
        now = time.monotonic()
        usage = self.safe(privacy_usage) if privacy_usage is not None else None
        talking = []
        for name in usage.microphone if usage else ():
            app = call_app(name)
            if app and app not in talking:
                talking.append(app)

        if talking:
            if self.ring is not None and self.ring.app in talking:
                app = self.ring.app  # the call that rang was answered
            elif self.call is not None and self.call.app in talking:
                app = self.call.app
            else:
                app = talking[0]
            if self.call is None or self.call.app != app:
                caller = self.ring.caller if self.ring is not None and self.ring.app == app else ""
                self.call = _Call(app, caller)
                self.mark_moving()
            self.call.quiet_since = None
        elif self.call is not None:
            if self.call.quiet_since is None:
                self.call.quiet_since = now
            elif now - self.call.quiet_since > CALL_GRACE_SEC:
                length = self.call.quiet_since - self.call.since
                self.call = None
                self.notify(tr("Звонок завершён · {time}").format(time=self.format_time(length)), seconds=2.5)
                self.mark_moving()

        ring = self.ring
        if ring is None:
            return
        if self.call is not None and (self.call.app == ring.app or self.call.app not in ring.busy):
            # Answered here or on the phone: the call took over.
            if not self.call.caller:
                self.call.caller = ring.caller
            self.drop_ring()
        elif ring.answered_at is not None:
            if now - ring.answered_at > ANSWER_WAIT_SEC:
                self.drop_ring()
        elif now - ring.started > RING_MAX_SEC or (
            self.notifications is not None
            and not self.safe(self.notifications.present, ring.toast_id, default=True)
        ):
            self.drop_ring()  # declined, missed or answered on the phone

    def answer_call(self):
        ring = self.ring
        if ring is None or ring.answered_at is not None:
            return
        ring.answered_at = time.monotonic()
        self.banners = [banner for banner in self.banners if banner.kind != "call"]
        self.press_call_button("accept", ring)
        self.notify(tr("Соединение…"), seconds=2.0, force=True)

    def decline_call(self):
        ring = self.ring
        if ring is None:
            return
        self.press_call_button("decline", ring)
        self.drop_ring()

    def press_call_button(self, kind, ring):
        self._pressed_for = ring.app
        if ToastButtons is None:
            self.notify(tr("Не получилось ответить — ответьте в приложении"), seconds=3.0, force=True)
            return
        if self.call_buttons is None:
            self.call_buttons = ToastButtons()
            self.call_buttons.start()
        self.call_buttons.press(kind, ring.caller)

    def focus_call_app(self, ringing=False):
        """Bring the app of the call (or of the ringing call) to the front."""
        call = (self.ring or self.call) if ringing else (self.call or self.ring)
        if call is not None and focus_app is not None:
            self.safe(focus_app, call.app)

    def call_duration_text(self):
        if self.call is None:
            return ""
        return self.format_time(time.monotonic() - self.call.since)

    def call_button_at(self, position):
        """Hover key of the call button under the mouse, or None."""
        for rect, key, _action in self.call_rects:
            if rect.contains(position):
                return key
        return None

    def draw_phone_glyph(self, painter, center, color, scale=1.0, down=False):
        """A telephone handset; `down` lays it down, for hanging up."""
        painter.save()
        painter.translate(center)
        painter.scale(scale, scale)
        if down:
            painter.rotate(135.0)
        painter.translate(-1.0, 1.0)
        painter.setPen(QPen(color, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The body curves round the lower left; the ear and mouth pieces
        # stick out towards the upper right.
        painter.drawArc(QRectF(-5.5, -9.0, 14.0, 14.0), 180 * 16, 90 * 16)
        painter.setPen(QPen(color, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(-5.2, -2.4), QPointF(-2.6, -3.4))
        painter.drawLine(QPointF(1.4, 4.7), QPointF(2.4, 2.1))
        painter.restore()

    def draw_call_banner(self, painter, pill, alpha, banner):
        """The ringing call: who calls, and red / green round buttons."""
        look = self.look
        cy = pill.center().y()
        icon = QRectF(pill.left() + 11.0, cy - 20.0, 40.0, 40.0)
        # Waves go out from the handset while it rings.
        phase = (time.perf_counter() % 1.3) / 1.3
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(CALL_GREEN, alpha * 0.30 * (1.0 - phase)))
        painter.drawEllipse(icon.center(), 20.0 + 7.0 * phase, 20.0 + 7.0 * phase)
        painter.setBrush(with_alpha(CALL_GREEN, alpha * 0.22))
        painter.drawEllipse(icon)
        shake = math.sin(time.perf_counter() * 30.0) * 9.0 if phase < 0.35 else 0.0
        painter.save()
        painter.translate(icon.center())
        painter.rotate(shake)
        self.draw_phone_glyph(painter, QPointF(0.0, 0.0), with_alpha(CALL_GREEN, alpha), 1.3)
        painter.restore()
        self.mark_moving()

        size = 36.0
        accept = QRectF(pill.right() - 12.0 - size, cy - size / 2.0, size, size)
        decline = QRectF(accept.left() - 10.0 - size, cy - size / 2.0, size, size)
        for rect, key, color, down, action in (
            (decline, "call_decline", CALL_RED, True, self.decline_call),
            (accept, "call_accept", CALL_GREEN, False, self.answer_call),
        ):
            self.call_rects.append((rect, key, action))
            hover = self.btn_hover_anim.get(key, 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(mix_colors(color, QColor(255, 255, 255), 0.18 * hover), alpha))
            grow = 1.5 * hover
            painter.drawEllipse(rect.adjusted(-grow, -grow, grow, grow))
            self.draw_phone_glyph(painter, rect.center(), white(alpha), 1.15, down=down)

        text_x = icon.right() + 12.0
        width = max(20.0, decline.left() - 10.0 - text_x)
        title_font = ui_font(9.5, QFont.Weight.DemiBold)
        text_font = ui_font(8.5)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setFont(title_font)
        self.draw_text(
            painter, QRectF(text_x, cy - 17.0, width, 18.0), flags,
            QFontMetricsF(title_font).elidedText(banner.title, Qt.TextElideMode.ElideRight, width),
            with_alpha(look.primary, alpha),
        )
        painter.setFont(text_font)
        self.draw_text(
            painter, QRectF(text_x, cy + 1.0, width, 16.0), flags,
            QFontMetricsF(text_font).elidedText(banner.text, Qt.TextElideMode.ElideRight, width),
            with_alpha(look.secondary, alpha),
        )

    def draw_call_row(self, painter, pill, top, alpha):
        """The call in progress, in the expanded island: who, how long, and
        buttons for the microphone and the app's window."""
        look = self.look
        call = self.call
        cy = top + ROW_HEIGHT / 2.0 - 4.0
        badge = QRectF(pill.left() + 18.0, cy - 14.0, 28.0, 28.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(CALL_GREEN, alpha * 0.22))
        painter.drawEllipse(badge)
        self.draw_phone_glyph(painter, badge.center(), with_alpha(CALL_GREEN, alpha), 0.9)

        size = 28.0
        right = pill.right() - 18.0
        buttons = [(QRectF(right - size, cy - size / 2.0, size, size), "call_open", self.focus_call_app)]
        if self.microphone_available():
            left = buttons[0][0].left() - 8.0 - size
            buttons.append((QRectF(left, cy - size / 2.0, size, size), "call_mute", self.toggle_microphone))
        for rect, key, action in buttons:
            self.call_rects.append((rect, key, action))
            hover = self.btn_hover_anim.get(key, 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.veil(alpha * (0.07 + 0.08 * hover)))
            painter.drawEllipse(rect)
            color = with_alpha(mix_colors(look.secondary, look.primary, hover), alpha)
            c = rect.center()
            if key == "call_mute":
                if self.mic_muted:
                    color = with_alpha(DANGER_COLOR, alpha)
                self.draw_mic_glyph(painter, c, color, self.mic_muted)
            else:
                # A window with an arrow out of it: open the app.
                painter.setPen(round_pen(color, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(c.x() - 6.0, c.y() - 4.0, 10.0, 10.0), 2.0, 2.0)
                painter.drawLine(QPointF(c.x() - 0.5, c.y() + 0.5), QPointF(c.x() + 6.0, c.y() - 6.0))
                painter.drawLine(QPointF(c.x() + 2.0, c.y() - 6.0), QPointF(c.x() + 6.0, c.y() - 6.0))
                painter.drawLine(QPointF(c.x() + 6.0, c.y() - 6.0), QPointF(c.x() + 6.0, c.y() - 2.0))

        text_x = badge.right() + 10.0
        width = max(20.0, buttons[-1][0].left() - 10.0 - text_x)
        app = tr("Связь с телефоном") if call.app == PHONE_LINK else call.app
        name = call.caller or app
        duration = self.call_duration_text()
        name_font = ui_font(9, QFont.Weight.DemiBold)
        time_font = ui_font(9, QFont.Weight.DemiBold)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        time_width = QFontMetricsF(time_font).horizontalAdvance(duration) + 2.0
        name_width = max(10.0, width - time_width - 10.0)
        painter.setFont(name_font)
        name = QFontMetricsF(name_font).elidedText(name, Qt.TextElideMode.ElideRight, name_width)
        shown = QFontMetricsF(name_font).horizontalAdvance(name)
        self.draw_text(painter, QRectF(text_x, cy - 9.0, name_width, 18.0), flags, name,
                       with_alpha(look.primary, alpha))
        painter.setFont(time_font)
        self.draw_text(painter, QRectF(text_x + shown + 8.0, cy - 9.0, time_width, 18.0), flags,
                       duration, with_alpha(CALL_GREEN, alpha))

    def calling(self):
        """True while a call in progress takes the collapsed island."""
        return self.call is not None and not self.ringing()

    def call_label(self):
        call = self.call
        if call is None:
            return ""
        return call.caller or (tr("Связь с телефоном") if call.app == PHONE_LINK else call.app)

    def draw_call_badge(self, painter, rect, alpha):
        """The green handset where the artwork is, during a call."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(CALL_GREEN, alpha * 0.25))
        painter.drawEllipse(rect)
        self.draw_phone_glyph(painter, rect.center(), with_alpha(CALL_GREEN, alpha), rect.width() / 30.0)

    def draw_call_timer(self, painter, right, center_y, alpha):
        """The call's length on the right of the collapsed island."""
        font = ui_font(9, QFont.Weight.DemiBold)
        painter.setFont(font)
        text = self.call_duration_text()
        width = QFontMetricsF(font).horizontalAdvance(text) + 2.0
        self.draw_text(painter, QRectF(right - width, center_y - 9.0, width, 18.0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text,
                       with_alpha(CALL_GREEN, alpha))

    def call_timer_width(self):
        return QFontMetricsF(ui_font(9, QFont.Weight.DemiBold)).horizontalAdvance(self.call_duration_text()) + 2.0

    # ----- Volume and brightness on the island ---------------------------

    def check_hud(self, now):
        """Show the system volume or the screen brightness when it changes
        outside the island (keyboard keys, the tray, another app)."""
        if not self.config["hud_enabled"]:
            return
        readings = []
        if self.speakers is not None:
            level = self.safe(self.speakers.level)
            if level is not None:
                readings.append(("volume", level[0], level[1]))
        if self.brightness is not None:
            level = self.safe(self.brightness.level)
            if level is not None:
                readings.append(("brightness", level, False))
        for kind, value, muted in readings:
            before = self._hud_seen.get(kind)
            self._hud_seen[kind] = (value, muted)
            if before is None or before == (value, muted):
                continue
            # The island's own volume control shows its own slider.
            if self.t > 0.1 or self.dragging_vol_slider:
                continue
            self.hud = (kind, value, muted, now + HUD_SECONDS)
            self.mark_moving()

    def hud_showing(self):
        return self.hud is not None and not self.ringing()

    def draw_hud(self, painter, pill, alpha):
        kind, value, muted, _until = self.hud
        look = self.look
        cy = pill.center().y()
        color = with_alpha(look.primary, alpha)
        icon = QPointF(pill.left() + 22.0, cy)
        if kind == "volume":
            self.draw_speaker_glyph(painter, icon, color, 0 if muted else value)
        else:
            painter.setPen(round_pen(color, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(icon, 3.6, 3.6)
            for ray in range(8):
                a = math.radians(ray * 45.0)
                painter.drawLine(QPointF(icon.x() + math.cos(a) * 5.6, cy + math.sin(a) * 5.6),
                                 QPointF(icon.x() + math.cos(a) * 7.4, cy + math.sin(a) * 7.4))
        font = ui_font(8.5, QFont.Weight.DemiBold)
        painter.setFont(font)
        label = "0" if muted else str(value)
        label_w = QFontMetricsF(font).horizontalAdvance("100") + 4.0
        self.draw_text(painter, QRectF(pill.right() - 16.0 - label_w, cy - 9.0, label_w, 18.0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label, color)
        left = pill.left() + 40.0
        right = pill.right() - 24.0 - label_w
        bar = QRectF(left, cy - 2.5, max(10.0, right - left), 5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(look.veil(alpha * 0.18))
        painter.drawRoundedRect(bar, 2.5, 2.5)
        share = 0.0 if muted else max(0.0, min(1.0, value / 100.0))
        if share > 0.0:
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(5.0, bar.width() * share), bar.height()), 2.5, 2.5)

    def draw_speaker_glyph(self, painter, c, color, value):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        body = QPainterPath()
        body.moveTo(c.x() - 7.0, c.y() - 2.5)
        body.lineTo(c.x() - 4.0, c.y() - 2.5)
        body.lineTo(c.x() + 0.5, c.y() - 6.5)
        body.lineTo(c.x() + 0.5, c.y() + 6.5)
        body.lineTo(c.x() - 4.0, c.y() + 2.5)
        body.lineTo(c.x() - 7.0, c.y() + 2.5)
        body.closeSubpath()
        painter.drawPath(body)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(round_pen(color, 1.4))
        if value <= 0:
            painter.drawLine(QPointF(c.x() + 3.0, c.y() - 3.0), QPointF(c.x() + 8.0, c.y() + 3.0))
            painter.drawLine(QPointF(c.x() + 8.0, c.y() - 3.0), QPointF(c.x() + 3.0, c.y() + 3.0))
            return
        for step, radius in ((1, 4.0), (34, 7.0), (67, 10.0)):
            if value >= step:
                painter.drawArc(QRectF(c.x() - radius + 1.0, c.y() - radius, radius * 2, radius * 2), -45 * 16, 90 * 16)

    # ----- Clipboard ----------------------------------------------------

    def on_clipboard_changed(self):
        if not self.config["clipboard_enabled"] or time.monotonic() < self._clip_ignore_until:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is None or self.clip_is_secret(mime):
            return
        if mime.hasImage():
            image = QApplication.clipboard().image()
            if image.isNull():
                return
            label = tr("Картинка {w}×{h}").format(w=image.width(), h=image.height())
            thumb = QPixmap.fromImage(image.scaled(
                96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            # The picture itself is kept too, so it can be copied back whole.
            clip = ("image", label, thumb, image)
            message = tr("Скопирована картинка {w}×{h}").format(w=image.width(), h=image.height())
        elif mime.hasText():
            text = mime.text()
            if not text.strip():
                return
            # The row in the history shows one line: the start, spaces folded.
            line = " ".join(text[:400].split())
            clip = ("text", text[:MAX_CLIP_TEXT], None, line)
            preview = line if len(line) <= 40 else line[:39] + "…"
            message = tr("Скопировано: {text}").format(text=f"«{preview}»")
        else:
            return
        self.clips = [clip] + [c for c in self.clips if not (c[0] == "text" and c[:2] == clip[:2])]
        del self.clips[MAX_CLIPS:]
        # Whole pictures take megabytes; only the newest few are kept.
        old_images = {id(c) for c in [c for c in self.clips if c[0] == "image"][MAX_CLIP_IMAGES:]}
        self.clips = [c for c in self.clips if id(c) not in old_images]
        self.notify(message, seconds=1.8)

    @staticmethod
    def clip_is_secret(mime):
        """Password managers mark what they copy so that clipboard viewers
        leave it alone."""
        for name in mime.formats():
            for secret in SECRET_CLIP_FORMATS:
                if secret not in name:
                    continue
                if secret != "CanIncludeInClipboardHistory":
                    return True
                data = bytes(mime.data(name))
                if data[:4] == b"\x00\x00\x00\x00":
                    return True
        return False

    def open_clipboard_panel(self):
        if not self.clips:
            self.notify(tr("Буфер обмена пуст"))
            return
        self.panel = "clipboard"
        self.panel_left_at = 0.0
        self.mark_moving()

    def clipboard_rows(self):
        fits = max(1, min(MAX_CLIPS, int((self.canvas_h - 6.0 - 56.0) // 36.0)))
        return self.clips[:fits]

    def copy_clip(self, clip):
        self._clip_ignore_until = time.monotonic() + 0.5
        if clip[0] == "image":
            QApplication.clipboard().setImage(clip[3])
        else:
            QApplication.clipboard().setText(clip[1])
        self.clips = [clip] + [c for c in self.clips if c is not clip]
        self.notify(tr("Снова в буфере обмена"), seconds=1.4)
        self.close_panels()

    def clear_clips(self):
        self.clips = []
        self.close_panels()

    def draw_clipboard_panel(self, painter, pill, alpha):
        look = self.look
        clear = QRectF(pill.right() - 120.0, pill.top() + 11.0, 70.0, 26.0)
        self.panel_rects.append((clear, "clip_clear", self.clear_clips))
        hover = self.btn_hover_anim.get("clip_clear", 0.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(look.veil(alpha * (0.06 + 0.08 * hover)))
        painter.drawRoundedRect(clear, 13.0, 13.0)
        painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
        self.draw_text(painter, clear, Qt.AlignmentFlag.AlignCenter, tr("Очистить"),
                       with_alpha(mix_colors(look.secondary, look.primary, hover), alpha))
        font = ui_font(9, QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        y = pill.top() + 44.0
        for index, clip in enumerate(self.clipboard_rows()):
            row = QRectF(pill.left() + 12.0, y, pill.width() - 24.0, 32.0)
            key = f"clip_{index}"
            self.panel_rects.append((row, key, lambda c=clip: self.copy_clip(c)))
            hover = self.btn_hover_anim.get(key, 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.veil(alpha * (0.04 + 0.07 * hover)))
            painter.drawRoundedRect(row, 12.0, 12.0)
            text_x = row.left() + 12.0
            if clip[0] == "image":
                thumb = QRectF(row.left() + 6.0, row.top() + 4.0, 24.0, 24.0)
                painter.save()
                painter.setClipPath(rounded_rect_path(thumb, 5.0))
                painter.setOpacity(alpha / 255.0)
                painter.drawPixmap(thumb, clip[2], QRectF(clip[2].rect()))
                painter.restore()
                text_x = thumb.right() + 10.0
            text = clip[1] if clip[0] == "image" else clip[3]
            width = row.right() - 12.0 - text_x
            self.draw_text(painter, QRectF(text_x, row.top(), width, row.height()),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           metrics.elidedText(text, Qt.TextElideMode.ElideRight, width),
                           with_alpha(look.primary if index == 0 else look.secondary, alpha))
            y += 36.0

    # ----- Privacy dots and screen recording ------------------------------

    def privacy_dots(self):
        """[(colour, label)] of what is in use: camera, microphone, recording."""
        mic_used, camera_used = self.privacy
        if self.calling():
            mic_used = False  # the call already says who listens
        dots = []
        if camera_used:
            dots.append((QColor(52, 199, 89), ""))
        if mic_used:
            dots.append((QColor(255, 159, 10), ""))
        if self.recording_state is not None:
            dots.append((RECORD_RED, self.format_time(time.monotonic() - self.recording_state[1])))
        return dots

    def privacy_inside(self):
        return self.config["privacy_dots_place"] == "inside"

    def privacy_dots_width(self, dots):
        width = 0.0
        font = ui_font(8.5, QFont.Weight.DemiBold)
        for _color, label in dots:
            width += 10.0
            if label:
                width += QFontMetricsF(font).horizontalAdvance(label) + 6.0
        return width

    def inside_dots_width(self):
        """Room the dots take inside the collapsed island."""
        if not self.privacy_inside():
            return 0.0
        dots = self.privacy_dots()
        return self.privacy_dots_width(dots) + 4.0 if dots else 0.0

    def draw_privacy_dots(self, painter, left, center_y, alpha, dots):
        font = ui_font(8.5, QFont.Weight.DemiBold)
        x = left
        for color, label in dots:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(color, alpha))
            painter.drawEllipse(QPointF(x + 4.0, center_y), 3.4, 3.4)
            x += 10.0
            if label:
                painter.setFont(font)
                width = QFontMetricsF(font).horizontalAdvance(label) + 2.0
                self.draw_text(painter, QRectF(x, center_y - 9.0, width, 18.0),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label,
                               with_alpha(color, alpha))
                x += width + 4.0

    def draw_inside_dots(self, painter, pill, alpha):
        """The dots inside the capsule, at its right end, as on an iPhone."""
        dots = self.privacy_dots() if self.privacy_inside() else []
        if not dots:
            return
        width = self.privacy_dots_width(dots)
        self.draw_privacy_dots(painter, pill.right() - 12.0 - width, pill.center().y(), alpha, dots)

    def lyrics_translation(self):
        """The current line in the interface language, or ""."""
        if not self.config["lyrics_translate"] or not isinstance(self._translation, dict):
            return ""
        if self._translation_for != ((self.title, self.artist), language()):
            return ""
        return self._translation.get(self.lyrics_line(), "")

    # ----- Banners ------------------------------------------------------

    def push_banner(self, banner, force=False):
        if self.config["dnd_mode"] and not force:
            return
        self.banners = [b for b in self.banners if not (b.title == banner.title and b.text == banner.text)]
        self.banners.append(banner)
        del self.banners[:-MAX_BANNERS]
        self.mark_moving()

    def current_banner(self):
        return self.banners[0] if self.banners else None

    def update_banners(self, now, dt):
        banner = self.current_banner()
        if banner is not None and banner.shown_at is None and self.t < 0.05 and not self.positioning:
            banner.shown_at = now
        # A banner waits while the island is open and does not run out then.
        if banner is not None and banner.shown_at is not None and self.t > 0.3:
            banner.shown_at = now - min(now - banner.shown_at, banner.seconds - 1.0)
        if banner is not None and banner.shown_at is not None and now - banner.shown_at > banner.seconds:
            self.banners.pop(0)
            banner = self.current_banner()
        goal = 1.0 if banner is not None and banner.shown_at is not None and not self.positioning else 0.0
        self._banner_share = self.approach(self._banner_share, goal, 0.2, dt)

    def banner_share(self):
        return self._banner_share

    def banner_size(self):
        banner = self.current_banner()
        width = 320.0
        if banner is not None:
            metrics = QFontMetricsF(ui_font(9.5, QFont.Weight.DemiBold))
            text_metrics = QFontMetricsF(ui_font(8.5))
            wanted = max(metrics.horizontalAdvance(banner.title), text_metrics.horizontalAdvance(banner.text))
            if banner.kind == "call":
                # Room for the two round buttons on the right.
                return max(320.0, min(400.0, wanted + 86.0 + 92.0)), 62.0
            width = max(240.0, min(380.0, wanted + 86.0))
        return width, 62.0

    def draw_banner(self, painter, pill, alpha):
        banner = self.current_banner()
        if banner is None or alpha <= 0:
            return
        if banner.kind == "call":
            self.draw_call_banner(painter, pill, alpha, banner)
            return
        look = self.look
        accent = self.current_accent_color()
        icon = QRectF(pill.left() + 11.0, pill.center().y() - 20.0, 40.0, 40.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(accent, alpha * 0.22))
        painter.drawEllipse(icon)
        self.draw_banner_icon(painter, banner, icon, with_alpha(accent, alpha))
        text_x = icon.right() + 12.0
        width = max(20.0, pill.right() - 16.0 - text_x)
        title_font = ui_font(9.5, QFont.Weight.DemiBold)
        text_font = ui_font(8.5)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        top = pill.center().y() - (17.0 if banner.text else 9.0)
        painter.setFont(title_font)
        self.draw_text(
            painter, QRectF(text_x, top, width, 18.0), flags,
            QFontMetricsF(title_font).elidedText(banner.title, Qt.TextElideMode.ElideRight, width),
            with_alpha(look.primary, alpha),
        )
        if banner.text:
            painter.setFont(text_font)
            self.draw_text(
                painter, QRectF(text_x, top + 18.0, width, 16.0), flags,
                QFontMetricsF(text_font).elidedText(banner.text, Qt.TextElideMode.ElideRight, width),
                with_alpha(look.secondary, alpha),
            )

    def draw_banner_icon(self, painter, banner, rect, color):
        c = rect.center()
        painter.setPen(round_pen(color, 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        kind = banner.kind
        if kind in ("battery", "charge", "device") and banner.value is not None or kind == "charge":
            body = QRectF(c.x() - 9.0, c.y() - 5.0, 16.0, 10.0)
            painter.drawRoundedRect(body, 2.5, 2.5)
            painter.drawLine(QPointF(body.right() + 2.0, c.y() - 2.0), QPointF(body.right() + 2.0, c.y() + 2.0))
            share = max(0.0, min(1.0, (banner.value or 0) / 100.0))
            if kind == "charge":
                # The fill breathes while the charger is plugged in.
                share = max(share, 0.15) * (0.75 + 0.25 * math.sin(time.perf_counter() * 4.0))
                self.mark_moving()
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(214, 142, 130) if kind == "battery" and share <= 0.2 else color
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(body.left() + 2.0, body.top() + 2.0, (body.width() - 4.0) * share, body.height() - 4.0), 1.2, 1.2)
            if kind == "charge":
                bolt = QPainterPath()
                bolt.moveTo(c.x() + 1.0, c.y() - 9.0)
                bolt.lineTo(c.x() - 3.5, c.y() + 1.0)
                bolt.lineTo(c.x() + 0.5, c.y() + 1.0)
                bolt.lineTo(c.x() - 1.0, c.y() + 9.0)
                bolt.lineTo(c.x() + 3.5, c.y() - 1.0)
                bolt.lineTo(c.x() - 0.5, c.y() - 1.0)
                bolt.closeSubpath()
                painter.setBrush(white(color.alpha()))
                painter.drawPath(bolt)
            return
        if kind == "device":
            painter.drawArc(QRectF(c.x() - 7.0, c.y() - 7.0, 14.0, 14.0), 0, 180 * 16)
            painter.drawRoundedRect(QRectF(c.x() - 8.5, c.y() - 1.0, 4.0, 7.0), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(c.x() + 4.5, c.y() - 1.0, 4.0, 7.0), 1.5, 1.5)
        elif kind == "download":
            painter.drawLine(QPointF(c.x(), c.y() - 7.0), QPointF(c.x(), c.y() + 3.0))
            painter.drawLine(QPointF(c.x() - 4.0, c.y() - 1.0), QPointF(c.x(), c.y() + 3.0))
            painter.drawLine(QPointF(c.x() + 4.0, c.y() - 1.0), QPointF(c.x(), c.y() + 3.0))
            painter.drawLine(QPointF(c.x() - 7.0, c.y() + 7.0), QPointF(c.x() + 7.0, c.y() + 7.0))
        elif kind == "update":
            painter.drawArc(QRectF(c.x() - 7.0, c.y() - 7.0, 14.0, 14.0), 40 * 16, 280 * 16)
            painter.drawLine(QPointF(c.x() + 5.5, c.y() - 7.5), QPointF(c.x() + 5.5, c.y() - 3.0))
            painter.drawLine(QPointF(c.x() + 5.5, c.y() - 3.0), QPointF(c.x() + 1.0, c.y() - 3.0))
        elif kind == "reminder":
            painter.drawEllipse(c, 7.5, 7.5)
            painter.drawLine(c, QPointF(c.x(), c.y() - 4.5))
            painter.drawLine(c, QPointF(c.x() + 3.5, c.y() + 1.5))
        else:  # bell
            bell = QPainterPath()
            bell.moveTo(c.x() - 6.0, c.y() + 4.0)
            bell.cubicTo(c.x() - 6.0, c.y() - 9.0, c.x() + 6.0, c.y() - 9.0, c.x() + 6.0, c.y() + 4.0)
            bell.lineTo(c.x() - 6.0, c.y() + 4.0)
            painter.drawPath(bell)
            painter.drawLine(QPointF(c.x() - 1.5, c.y() + 7.0), QPointF(c.x() + 1.5, c.y() + 7.0))

    def wants_open(self):
        return self.shelf_drop_hover

    # ----- Downloads ----------------------------------------------------

    def on_downloads_report(self, items):
        if not self.config["downloads_enabled"]:
            return
        names = tuple(item.name for item in items)
        if self.downloads and not items:
            finished = self.downloads_names[-1] if self.downloads_names else ""
            self.push_banner(_Banner("download", tr("Загрузка завершена"), finished, 3.5))
        self.downloads = tuple(items)
        self._downloads_seen = time.monotonic()
        if names:
            self.downloads_names = names
        self.mark_moving()

    def downloads_showing(self):
        return bool(self.downloads) and self.config["downloads_enabled"]

    def downloads_progress(self):
        total = sum(item.total for item in self.downloads if item.total > 0)
        if total <= 0:
            return None
        received = sum(min(item.received, item.total) for item in self.downloads if item.total > 0)
        return received / total

    def draw_downloads_ring(self, painter, center, alpha):
        accent = self.current_accent_color()
        radius = 6.5
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.look.veil(alpha * 0.18), 1.8))
        painter.drawEllipse(center, radius, radius)
        progress = self.downloads_progress()
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        painter.setPen(round_pen(with_alpha(accent, alpha), 1.8))
        if progress is None:
            # Unknown size: a short arc goes round.
            start = int((time.perf_counter() * 360.0) % 360.0)
            painter.drawArc(rect, -start * 16, 90 * 16)
            self.mark_moving()
        else:
            painter.drawArc(rect, 90 * 16, -int(360 * 16 * progress))
        painter.drawLine(QPointF(center.x(), center.y() - 2.5), QPointF(center.x(), center.y() + 2.0))
        painter.drawLine(QPointF(center.x() - 2.0, center.y()), QPointF(center.x(), center.y() + 2.0))
        painter.drawLine(QPointF(center.x() + 2.0, center.y()), QPointF(center.x(), center.y() + 2.0))

    # ----- Music extras -------------------------------------------------

    def spectrum_levels(self):
        if self.analyzer is None or not self.config["real_visualizer"]:
            return None
        if not self.safe(self.analyzer.available, default=False):
            return None
        levels = self.safe(self.analyzer.levels)
        return levels if levels and len(levels) >= 6 else None

    def lyrics_line(self):
        if self.lyrics is None or not self.config["lyrics_enabled"] or not self.title:
            return ""
        if self.config["center_zone"] != "Track / Lyrics" or not self.is_playing:
            return ""
        lyrics = self.safe(self.lyrics.get, self.title, self.artist)
        if lyrics is None or not lyrics.found or not lyrics.synced:
            return ""
        index = self.safe(lyrics.index_at, self.lyrics_position(), default=-1)
        if index is None or index < 0 or index >= len(lyrics.lines):
            return ""
        return lyrics.lines[index][1].strip()

    def lyrics_progress(self):
        """Karaoke progress of the line being sung, or None."""
        if not self.config["lyrics_karaoke"] or not self.lyrics_line():
            return None
        lyrics = self.safe(self.lyrics.get, self.title, self.artist)
        if lyrics is None:
            return None
        return self.safe(lyrics.progress_at, self.lyrics_position())

    def lyrics_position(self):
        """Track position to match lyrics against, with the user's shift."""
        return self.position_sec - self.config["lyrics_offset"] / 10.0

    def draw_karaoke(self, painter, rect, text, progress, alpha, centered):
        """A lyric line whose sung words fill with the track's colour; the
        word being sung fills from left to right as it is sung.

        Returns False when there is nothing to fill, so plain text is drawn."""
        if progress is None or progress.text.strip() != text.strip():
            return False
        font = painter.font()
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text)
        lead = len(text) - len(text.lstrip())
        fill = (
            metrics.horizontalAdvance(text[: lead + progress.word_start])
            + metrics.horizontalAdvance(text[lead + progress.word_start: lead + progress.word_end])
            * progress.word_share
        )
        if width <= rect.width():
            left = rect.center().x() - width / 2.0 if centered else rect.left()
        else:
            # Too long to fit: slide so the sung place stays in view.
            slide = max(0.0, min(width - rect.width(), fill - rect.width() * 0.62))
            left = rect.left() - slide
        accent = self.current_accent_color()
        sung = with_alpha(mix_colors(accent, QColor(255, 255, 255), 0.18), alpha)
        waiting = with_alpha(self.look.primary, int(alpha * 0.5))
        edge = left + fill
        gradient = QLinearGradient(edge - 5.0, 0.0, edge + 5.0, 0.0)
        gradient.setColorAt(0.0, sung)
        gradient.setColorAt(1.0, waiting)
        box = QRectF(left, rect.top(), width + 2.0, rect.height())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.save()
        painter.setClipRect(rect.adjusted(-1.0, -2.0, 1.0, 2.0))
        shadow = self.look.text_shadow(alpha)
        if shadow is not None:
            painter.setPen(shadow)
            painter.drawText(box.translated(0.0, 1.0), flags, text)
        # A soft glow of the colour under what has been sung.
        if fill > 1.0:
            glow = with_alpha(accent, int(alpha * 0.22))
            painter.setPen(glow)
            glow_clip = QRectF(left - 2.0, rect.top(), fill + 2.0, rect.height())
            painter.setClipRect(glow_clip.intersected(rect.adjusted(-1.0, -2.0, 1.0, 2.0)))
            for dx, dy in ((0.6, 0.0), (-0.6, 0.0), (0.0, 0.6)):
                painter.drawText(box.translated(dx, dy), flags, text)
            painter.setClipRect(rect.adjusted(-1.0, -2.0, 1.0, 2.0))
        painter.setPen(QPen(QBrush(gradient), 1.0))
        painter.drawText(box, flags, text)
        painter.restore()
        return True

    def gpu_text(self):
        status = self.safe(self.gpu.status) if self.gpu is not None else None
        if status is None or status.load is None:
            return "GPU: —"
        text = f"GPU: {status.load}%"
        if status.temperature is not None:
            text += f" · {status.temperature}°C"
        return text

    # ----- Without music ------------------------------------------------

    def weather_now(self):
        if self.weather is None or not self.config["weather_city"]:
            return None
        return self.safe(self.weather.current)

    def weather_text(self):
        """"+3° · Пасмурно", or "" while there is no weather."""
        weather = self.weather_now()
        if weather is None:
            return ""
        return f"{format_temperature(weather.temperature)} · {tr(describe_weather(weather.code)[1])}"

    def weather_line(self):
        """The weather with its city, for the expanded island."""
        weather = self.weather_now()
        if weather is not None:
            description = tr(describe_weather(weather.code)[1])
            return (
                f"{weather.city} {format_temperature(weather.temperature)}, "
                f"{description[:1].lower()}{description[1:]}"
            )
        city = self.config["weather_city"]
        if city and self.weather is not None and self.safe(self.weather.status) == "not_found":
            return tr("Город «{city}» не найден").format(city=city)
        return ""

    def idle_text(self):
        """The collapsed line while no player has a track."""
        view = self.config["idle_view"]
        if view == "blank":
            return ""
        if view == "weather":
            text = self.weather_text()
            if text:
                return text
        if view == "system":
            return f"CPU: {self.cached_cpu_percent}% | RAM: {self.cached_memory_percent}%"
        moment = datetime.datetime.now()
        return f"{moment:%H:%M}  ·  {short_date(moment)}"

    WEATHER_TILES = {
        "clear": ((86, 150, 232), (44, 98, 190)),
        "night": ((44, 54, 98), (20, 24, 52)),
        "partly": ((96, 140, 200), (56, 88, 150)),
        "cloud": ((112, 122, 138), (70, 78, 94)),
        "fog": ((128, 134, 142), (84, 90, 98)),
        "rain": ((74, 90, 118), (40, 50, 70)),
        "snow": ((150, 170, 204), (96, 116, 152)),
        "storm": ((64, 66, 92), (32, 32, 50)),
    }

    def draw_idle_tile(self, painter, rect, radius):
        """In place of the artwork while nothing plays: the weather, or a
        clock with the time on it."""
        weather = self.weather_now() if self.config["idle_view"] == "weather" else None
        if weather is not None:
            kind = describe_weather(weather.code)[0]
            top, bottom = self.WEATHER_TILES[
                "night" if kind in ("clear", "partly") and not weather.is_day else kind
            ]
            top, bottom = QColor(*top), QColor(*bottom)
        else:
            accent = self.current_accent_color()
            top = mix_colors(accent, QColor(0, 0, 0), 0.38)
            bottom = mix_colors(accent, QColor(0, 0, 0), 0.68)
        painter.save()
        painter.setClipPath(rounded_rect_path(rect, radius))
        tile = QLinearGradient(rect.topLeft(), rect.bottomRight())
        tile.setColorAt(0.0, top)
        tile.setColorAt(1.0, bottom)
        painter.fillRect(rect, QBrush(tile))
        if weather is not None:
            self.draw_weather_glyph(painter, rect.center(), rect.width(), kind, weather.is_day)
        else:
            self.draw_clock_face(painter, rect.center(), rect.width())
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.look.veil(24), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

    def draw_clock_face(self, painter, c, size):
        moment = datetime.datetime.now()
        radius = size * 0.34
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if size > 30.0:
            for hour in range(12):
                angle = math.radians(hour * 30.0)
                inner = radius * (0.80 if hour % 3 == 0 else 0.88)
                painter.setPen(round_pen(white(200 if hour % 3 == 0 else 110), max(1.0, size * 0.03)))
                painter.drawLine(
                    QPointF(c.x() + math.cos(angle) * inner, c.y() + math.sin(angle) * inner),
                    QPointF(c.x() + math.cos(angle) * radius, c.y() + math.sin(angle) * radius),
                )
        else:
            painter.setPen(QPen(white(190), max(1.0, size * 0.05)))
            painter.drawEllipse(c, radius, radius)
        hour_angle = math.radians((moment.hour % 12 + moment.minute / 60.0) * 30.0 - 90.0)
        minute_angle = math.radians((moment.minute + moment.second / 60.0) * 6.0 - 90.0)
        for angle, length, width in (
            (hour_angle, 0.52, max(1.4, size * 0.065)),
            (minute_angle, 0.80, max(1.1, size * 0.045)),
        ):
            painter.setPen(round_pen(white(240), width))
            painter.drawLine(
                c, QPointF(c.x() + math.cos(angle) * radius * length, c.y() + math.sin(angle) * radius * length)
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(white(255))
        painter.drawEllipse(c, max(1.2, size * 0.045), max(1.2, size * 0.045))

    def draw_weather_glyph(self, painter, c, size, kind, is_day):
        """A weather picture drawn for a 52-pixel tile and scaled to `size`."""
        painter.save()
        painter.translate(c)
        painter.scale(size / 52.0, size / 52.0)
        sun = QColor(255, 204, 76)

        def draw_sun(x, y, r):
            painter.setPen(round_pen(sun, 2.6))
            for ray in range(8):
                a = math.radians(ray * 45.0)
                painter.drawLine(
                    QPointF(x + math.cos(a) * (r + 3.5), y + math.sin(a) * (r + 3.5)),
                    QPointF(x + math.cos(a) * (r + 7.0), y + math.sin(a) * (r + 7.0)),
                )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(sun)
            painter.drawEllipse(QPointF(x, y), r, r)

        def draw_moon(x, y, r):
            moon = QPainterPath()
            moon.addEllipse(QPointF(x, y), r, r)
            bite = QPainterPath()
            bite.addEllipse(QPointF(x + r * 0.55, y - r * 0.35), r * 0.85, r * 0.85)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(246, 232, 176))
            painter.drawPath(moon.subtracted(bite))

        def draw_cloud(x, y, w, alpha=255):
            cloud = QPainterPath()
            # Winding fill: where the puffs overlap is cloud, not a hole.
            cloud.setFillRule(Qt.FillRule.WindingFill)
            cloud.addEllipse(QPointF(x - w * 0.22, y + w * 0.02), w * 0.24, w * 0.22)
            cloud.addEllipse(QPointF(x + w * 0.05, y - w * 0.12), w * 0.30, w * 0.30)
            cloud.addEllipse(QPointF(x + w * 0.30, y + w * 0.04), w * 0.20, w * 0.19)
            cloud.addRoundedRect(QRectF(x - w * 0.44, y, w * 0.92, w * 0.24), w * 0.12, w * 0.12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(242, 245, 250, alpha))
            painter.drawPath(cloud)

        if kind == "clear":
            if is_day:
                draw_sun(0.0, 0.0, 8.5)
            else:
                draw_moon(0.0, 0.0, 11.0)
        elif kind == "partly":
            if is_day:
                draw_sun(-6.0, -6.0, 6.5)
            else:
                draw_moon(-6.0, -7.0, 8.0)
            draw_cloud(4.0, 4.0, 30.0)
        elif kind == "cloud":
            draw_cloud(0.0, -1.0, 36.0)
        elif kind == "fog":
            draw_cloud(0.0, -7.0, 32.0, 210)
            painter.setPen(round_pen(QColor(235, 238, 244, 200), 2.4))
            painter.drawLine(QPointF(-13.0, 9.0), QPointF(11.0, 9.0))
            painter.drawLine(QPointF(-9.0, 14.0), QPointF(14.0, 14.0))
        else:
            draw_cloud(0.0, -6.0, 34.0)
            if kind == "rain":
                painter.setPen(round_pen(QColor(130, 190, 255), 2.4))
                for x in (-9.0, 0.0, 9.0):
                    painter.drawLine(QPointF(x + 1.5, 7.5), QPointF(x - 1.5, 14.0))
            elif kind == "snow":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255))
                for x, y in ((-9.0, 10.0), (0.0, 13.0), (9.0, 10.0)):
                    painter.drawEllipse(QPointF(x, y), 2.2, 2.2)
            else:  # storm
                bolt = QPainterPath()
                bolt.moveTo(1.5, 5.0)
                bolt.lineTo(-4.0, 13.0)
                bolt.lineTo(0.0, 13.0)
                bolt.lineTo(-2.0, 20.0)
                bolt.lineTo(5.0, 10.5)
                bolt.lineTo(1.0, 10.5)
                bolt.lineTo(3.5, 5.0)
                bolt.closeSubpath()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 214, 90))
                painter.drawPath(bolt)
        painter.restore()

    def draw_idle_info(self, painter, pill, alpha, text_x, text_max_w, cov_y):
        """The expanded island while nothing plays: the time, the date, and
        a line with the weather and the computer."""
        look = self.look
        moment = datetime.datetime.now()
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        time_font = ui_font(17, QFont.Weight.DemiBold, display=True)
        painter.setFont(time_font)
        self.draw_text(painter, QRectF(text_x, cov_y - 3.0, text_max_w, 32.0), flags,
                       f"{moment:%H:%M}", with_alpha(look.primary, alpha))
        date_font = ui_font(9, QFont.Weight.Medium)
        painter.setFont(date_font)
        self.draw_text(
            painter, QRectF(text_x, cov_y + 31.0, text_max_w, 18.0), flags,
            QFontMetricsF(date_font).elidedText(long_date(moment), Qt.TextElideMode.ElideRight, text_max_w),
            with_alpha(look.secondary, alpha),
        )
        # Where the progress bar is while music plays.
        parts = [part for part in (self.weather_line(),) if part]
        parts += [f"CPU {self.cached_cpu_percent}%", f"RAM {self.cached_memory_percent}%"]
        info_font = ui_font(8, QFont.Weight.DemiBold)
        painter.setFont(info_font)
        row = QRectF(pill.left() + 18.0, pill.top() + 79.0, pill.width() - 36.0, 16.0)
        self.draw_text(
            painter, row, Qt.AlignmentFlag.AlignCenter,
            QFontMetricsF(info_font).elidedText("  ·  ".join(parts), Qt.TextElideMode.ElideRight, row.width()),
            with_alpha(look.secondary, alpha),
        )

    def on_track_changed(self):
        if self.title and self.config["track_animation"]:
            self._track_change_at = time.perf_counter()
            self._glint_at = self._track_change_at
        self._blur_cache = None
        self._lyrics_track = None

    def track_text_motion(self):
        """(vertical shift, opacity) of the track text after a track change."""
        share = (time.perf_counter() - self._track_change_at) / 0.45
        if share >= 1.0:
            return 0.0, 1.0
        eased = ease_out(share)
        return 9.0 * (1.0 - eased), eased

    def cover_motion(self):
        """(horizontal scale, opacity) of the artwork after a track change:
        it turns like a card."""
        share = (time.perf_counter() - self._track_change_at) / 0.5
        if share >= 1.0:
            return 1.0, 1.0
        eased = ease_out(share)
        return 0.35 + 0.65 * eased, 0.3 + 0.7 * eased

    def glint_share(self, now=None):
        if not (self.look.glass and self.config["glass_glint"]):
            return None
        now = time.perf_counter() if now is None else now
        share = (now - self._glint_at) / 0.9
        return share if 0.0 <= share < 1.0 else None

    def draw_glint(self, painter, pill):
        share = self.glint_share()
        if share is None:
            return
        travel = pill.width() + 160.0
        x = pill.left() - 80.0 + travel * ease_out(share)
        peak = 120 if self.look.light else 64
        band = QLinearGradient(QPointF(x - 50.0, pill.top()), QPointF(x + 50.0, pill.bottom()))
        band.setColorAt(0.0, white(0))
        band.setColorAt(0.5, white(peak * math.sin(math.pi * share)))
        band.setColorAt(1.0, white(0))
        painter.fillRect(pill, band)

    def backdrop_painter(self, pill):
        """The blurred artwork under the expanded player."""
        if not self.config["cover_backdrop"] or self.cover_pixmap is None or self.t <= 0.02:
            return None
        if self.timer_panel_open or self.panel_open():
            return None
        key = self.cover_pixmap.cacheKey()
        if self._blur_cache is None or self._blur_cache[0] != key:
            small = self.cover_pixmap.scaled(
                10, 10, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            blurred = small.scaled(
                200, 200, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self._blur_cache = (key, blurred)
        blurred = self._blur_cache[1]
        strength = min(1.0, self.t) * (0.55 if self.look.light else 0.45)
        light = self.look.light

        def paint(painter):
            painter.setOpacity(strength)
            painter.drawPixmap(pill, blurred, QRectF(blurred.rect()))
            painter.setOpacity(1.0)
            # A veil keeps the text readable over any picture.
            veil = QColor(255, 255, 255, 70) if light else QColor(0, 0, 0, 60)
            painter.fillRect(pill, veil)

        return paint

    # ----- Rows in the expanded island ----------------------------------

    def quick_buttons(self):
        buttons = []
        for name in self.config["quick_buttons"]:
            if name == "mic" and self.mic_muted is None:
                continue
            if name == "output" and list_outputs is None:
                continue
            if name == "clipboard" and not self.config["clipboard_enabled"]:
                continue
            buttons.append(name)
        return buttons

    def extra_rows_height(self):
        height = 0.0
        if self.call is not None:
            height += ROW_HEIGHT
        if self.quick_buttons():
            height += ROW_HEIGHT
        if self.shelf:
            height += ROW_HEIGHT
        return height

    def draw_extra_rows(self, painter, pill, row_y, alpha):
        if self.call is not None:
            self.draw_call_row(painter, pill, row_y, alpha)
            row_y += ROW_HEIGHT
        buttons = self.quick_buttons()
        if buttons:
            self.draw_quick_row(painter, pill, row_y, alpha, buttons)
            row_y += ROW_HEIGHT
        if self.shelf:
            self.draw_shelf_row(painter, pill, row_y, alpha)
            row_y += ROW_HEIGHT
        return row_y

    def draw_quick_row(self, painter, pill, top, alpha, buttons):
        look = self.look
        size = 30.0
        gap = 12.0
        total = len(buttons) * size + (len(buttons) - 1) * gap
        x = pill.center().x() - total / 2.0
        y = top + (ROW_HEIGHT - size) / 2.0 - 4.0
        for name in buttons:
            rect = QRectF(x, y, size, size)
            self.quick_rects.append((rect, name))
            hover = self.btn_hover_anim.get(f"q_{name}", 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.veil(alpha * (0.07 + 0.08 * hover)))
            painter.drawEllipse(rect)
            color = with_alpha(mix_colors(look.secondary, look.primary, hover), alpha)
            if name == "mic" and self.mic_muted:
                color = with_alpha(QColor(214, 142, 130), alpha)
            self.draw_quick_icon(painter, name, rect.center(), color)
            x += size + gap

    def draw_quick_icon(self, painter, name, c, color):
        painter.setPen(round_pen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if name == "output":
            painter.drawRoundedRect(QRectF(c.x() - 6.0, c.y() - 7.0, 12.0, 14.0), 2.5, 2.5)
            painter.drawEllipse(QPointF(c.x(), c.y() + 2.0), 2.5, 2.5)
            painter.drawPoint(QPointF(c.x(), c.y() - 3.5))
        elif name == "mic":
            self.draw_mic_glyph(painter, c, color, self.mic_muted)
        elif name == "screenshot":
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                corner = QPointF(c.x() + sx * 6.5, c.y() + sy * 5.5)
                painter.drawLine(corner, QPointF(corner.x() - sx * 3.5, corner.y()))
                painter.drawLine(corner, QPointF(corner.x(), corner.y() - sy * 3.0))
            painter.drawEllipse(c, 2.2, 2.2)
        elif name == "lock":
            painter.drawRoundedRect(QRectF(c.x() - 5.5, c.y() - 1.0, 11.0, 8.0), 2.0, 2.0)
            painter.drawArc(QRectF(c.x() - 3.5, c.y() - 7.0, 7.0, 9.0), 0, 180 * 16)
        elif name == "note":
            painter.drawEllipse(c, 6.5, 6.5)
            painter.drawLine(c, QPointF(c.x(), c.y() - 4.0))
            painter.drawLine(c, QPointF(c.x() + 3.0, c.y() + 1.5))
        elif name == "clipboard":
            painter.drawRoundedRect(QRectF(c.x() - 5.5, c.y() - 5.5, 11.0, 13.0), 2.0, 2.0)
            painter.drawRoundedRect(QRectF(c.x() - 2.5, c.y() - 7.5, 5.0, 3.5), 1.2, 1.2)
            painter.drawLine(QPointF(c.x() - 2.5, c.y() + 0.5), QPointF(c.x() + 2.5, c.y() + 0.5))
            painter.drawLine(QPointF(c.x() - 2.5, c.y() + 3.5), QPointF(c.x() + 1.0, c.y() + 3.5))

    def draw_mic_glyph(self, painter, c, color, muted):
        painter.setPen(round_pen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(c.x() - 2.6, c.y() - 7.0, 5.2, 9.0), 2.6, 2.6)
        painter.drawArc(QRectF(c.x() - 5.0, c.y() - 4.0, 10.0, 9.0), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(c.x(), c.y() + 5.0), QPointF(c.x(), c.y() + 7.0))
        if muted:
            painter.setPen(round_pen(QColor(222, 84, 74, color.alpha()), 1.8))
            painter.drawLine(QPointF(c.x() - 6.0, c.y() - 7.0), QPointF(c.x() + 6.0, c.y() + 7.0))

    def draw_shelf_row(self, painter, pill, top, alpha):
        look = self.look
        x = pill.left() + 18.0
        right = pill.right() - 18.0
        font = ui_font(8.5, QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for index, path in enumerate(self.shelf):
            name = os.path.basename(path.rstrip("\\/")) or path
            width = min(150.0, metrics.horizontalAdvance(name) + 40.0)
            if x + width > right:
                break
            chip = QRectF(x, top + 4.0, width, 26.0)
            close = QRectF(chip.right() - 22.0, chip.top() + 4.0, 18.0, 18.0)
            self.shelf_rects.append((chip, index, close))
            hover = self.btn_hover_anim.get(f"shelf_{index}", 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.veil(alpha * (0.08 + 0.07 * hover)))
            painter.drawRoundedRect(chip, 13.0, 13.0)
            text = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, width - 34.0)
            self.draw_text(painter, QRectF(chip.left() + 10.0, chip.top(), width - 34.0, chip.height()),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text,
                           with_alpha(look.primary, alpha))
            painter.setPen(round_pen(with_alpha(look.tertiary, alpha), 1.3))
            cc = close.center()
            painter.drawLine(QPointF(cc.x() - 3.0, cc.y() - 3.0), QPointF(cc.x() + 3.0, cc.y() + 3.0))
            painter.drawLine(QPointF(cc.x() + 3.0, cc.y() - 3.0), QPointF(cc.x() - 3.0, cc.y() + 3.0))
            x += width + 8.0

    # ----- The output-device panel ---------------------------------------

    def panel_open(self):
        return self.panel is not None

    def close_panels(self):
        if self.panel is not None:
            self.panel = None
            self.panel_left_at = 0.0
            self.mark_moving()

    def open_output_panel(self):
        devices = self.safe(list_outputs, default=()) if list_outputs is not None else ()
        if not devices:
            self.notify(tr("Нет устройств вывода"))
            return
        # As many rows as fit on the canvas (56 + 36 per row, 6 px edge pad);
        # the current device stays in the list.
        fits = max(1, min(8, int((self.canvas_h - 6.0 - 56.0) // 36.0)))
        devices = tuple(devices)
        shown = devices[:fits]
        default = next((device for device in devices if device.is_default), None)
        if default is not None and default not in shown:
            shown = shown[:-1] + (default,)
        self.output_devices = shown
        self.panel = "output"
        self.panel_left_at = 0.0
        self.mark_moving()

    def panel_height(self):
        if self.panel == "clipboard":
            return 56.0 + 36.0 * len(self.clipboard_rows())
        if self.panel != "output":
            return 0.0
        return 56.0 + 36.0 * len(self.output_devices)

    def draw_panel(self, painter, pill, alpha):
        look = self.look
        accent = self.current_accent_color()
        if look.glass:
            # A list over see-through glass needs a calmer background.
            painter.fillRect(pill, with_alpha(look.panel, alpha * 0.6))
        painter.setFont(ui_font(10, QFont.Weight.DemiBold))
        self.draw_text(painter, QRectF(pill.left() + 20.0, pill.top() + 12.0, pill.width() - 80.0, 24.0),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       tr("Буфер обмена") if self.panel == "clipboard" else tr("Устройство вывода"),
                       with_alpha(look.primary, alpha))
        close = QRectF(pill.right() - 42.0, pill.top() + 11.0, 26.0, 26.0)
        self.panel_rects.append((close, "panel_close", self.close_panels))
        self.draw_button_hover(painter, close, self.btn_hover_anim["panel_close"], alpha)
        painter.setPen(round_pen(with_alpha(look.secondary, alpha), 1.5))
        cc = close.center()
        painter.drawLine(QPointF(cc.x() - 4.0, cc.y() - 4.0), QPointF(cc.x() + 4.0, cc.y() + 4.0))
        painter.drawLine(QPointF(cc.x() + 4.0, cc.y() - 4.0), QPointF(cc.x() - 4.0, cc.y() + 4.0))
        if self.panel == "clipboard":
            self.draw_clipboard_panel(painter, pill, alpha)
            return
        font = ui_font(9, QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        y = pill.top() + 44.0
        for index, device in enumerate(self.output_devices):
            row = QRectF(pill.left() + 12.0, y, pill.width() - 24.0, 32.0)
            key = f"out_{index}"
            self.panel_rects.append((row, key, lambda d=device: self.choose_output(d)))
            hover = self.btn_hover_anim.get(key, 0.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.veil(alpha * (0.04 + 0.07 * hover + (0.06 if device.is_default else 0.0))))
            painter.drawRoundedRect(row, 12.0, 12.0)
            dot = QPointF(row.left() + 16.0, row.center().y())
            painter.setPen(QPen(with_alpha(accent if device.is_default else look.tertiary, alpha), 1.5))
            painter.setBrush(with_alpha(accent, alpha) if device.is_default else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(dot, 4.5, 4.5)
            text_w = row.width() - 40.0
            self.draw_text(painter, QRectF(row.left() + 30.0, row.top(), text_w, row.height()),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           metrics.elidedText(device.name, Qt.TextElideMode.ElideRight, text_w),
                           with_alpha(look.primary if device.is_default else look.secondary, alpha))
            y += 36.0

    def choose_output(self, device):
        if set_default_output is None:
            return
        if self.safe(set_default_output, device.device_id, default=False):
            self.notify(tr("Звук: {name}").format(name=device.name), seconds=1.8)
            self._volume_controls = None
        else:
            self.notify(tr("Не удалось переключить устройство"))
        self.close_panels()

    # ----- Side buttons ---------------------------------------------------

    def reset_extra_rects(self):
        self.side_rects = {}
        self.quick_rects = []
        self.shelf_rects = []
        self.panel_rects = []
        self.call_rects = []

    def extra_side_rects(self):
        return tuple(self.side_rects.values())

    def extra_side_button_at(self, position):
        for name, rect in self.side_rects.items():
            if name != "privacy" and rect.adjusted(-4, -4, 4, 4).contains(position):
                return {"mic": "mic_side", "done": "pos_done", "call": "call_side"}[name]
        return None

    def draw_side_widgets(self, painter, pill):
        fade = self.side_fade()
        if fade <= 0.0:
            return
        alpha = int(255 * fade)
        x = pill.right() + 6.0
        y = pill.center().y() - SIDE_SIZE / 2.0
        if self.positioning:
            rect = QRectF(x, y, SIDE_SIZE, SIDE_SIZE)
            self.side_rects["done"] = rect
            hover = self.btn_hover_anim["pos_done"]
            self.draw_side_button(painter, rect, alpha, hover)
            c = rect.center()
            painter.setPen(round_pen(with_alpha(self.current_accent_color(), alpha), 2.0))
            painter.drawLine(QPointF(c.x() - 5.0, c.y()), QPointF(c.x() - 1.5, c.y() + 4.0))
            painter.drawLine(QPointF(c.x() - 1.5, c.y() + 4.0), QPointF(c.x() + 5.5, c.y() - 4.5))
            return
        if self.config["mic_button_enabled"] and self.mic_muted is not None:
            rect = QRectF(x, y, SIDE_SIZE, SIDE_SIZE)
            self.side_rects["mic"] = rect
            hover = self.btn_hover_anim["mic_side"]
            self.draw_side_button(painter, rect, alpha, hover)
            color = mix_colors(self.look.secondary, self.look.primary, hover)
            self.draw_mic_glyph(painter, rect.center(), with_alpha(color, alpha), self.mic_muted)
            x = rect.right() + 4.0
        dots = [] if self.privacy_inside() else self.privacy_dots()
        if dots:
            # The colours iOS uses: green for the camera, orange for the
            # microphone; red with a timer while the screen is recorded.
            width = self.privacy_dots_width(dots)
            rect = QRectF(x, pill.center().y() - 9.0, width + 8.0, 18.0)
            self.side_rects["privacy"] = rect
            self.draw_side_button(painter, rect, alpha, 0.0)
            self.draw_privacy_dots(painter, rect.left() + 4.0, rect.center().y(), alpha, dots)

    # ----- Mouse ------------------------------------------------------------

    def extra_button_at(self, position):
        for rect, key, _action in self.panel_rects + self.call_rects:
            if rect.contains(position):
                return key
        for rect, name in self.quick_rects:
            if rect.contains(position):
                return f"q_{name}"
        for rect, index, _close in self.shelf_rects:
            if rect.contains(position):
                return f"shelf_{index}"
        return None

    def extras_press(self, position, button):
        if button != Qt.MouseButton.LeftButton:
            return False
        side = self.extra_side_button_at(position)
        if side == "pos_done":
            self.finish_positioning()
            return True
        if side == "mic_side":
            self.toggle_microphone()
            return True
        if side == "call_side":
            self.focus_call_app()
            return True
        for rect, _key, action in self.panel_rects + self.call_rects:
            if rect.contains(position):
                action()
                return True
        for rect, name in self.quick_rects:
            if rect.contains(position):
                self.run_quick_button(name)
                return True
        for chip, index, close in self.shelf_rects:
            if close.contains(position):
                if index < len(self.shelf):
                    del self.shelf[index]
                self.mark_moving()
                return True
            if chip.contains(position):
                self.shelf_press = (index, position)
                return True
        return False

    def extras_move(self, position, buttons):
        if self.shelf_press is None or not buttons & Qt.MouseButton.LeftButton:
            return
        index, start = self.shelf_press
        if (position - start).manhattanLength() < 6.0 or index >= len(self.shelf):
            return
        self.shelf_press = None
        path = self.shelf[index]
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        result = drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)
        # Taken off the shelf once it was put somewhere.
        if result != Qt.DropAction.IgnoreAction and path in self.shelf:
            self.shelf.remove(path)
        self.mark_moving()

    def extras_release(self, position):
        if self.shelf_press is not None:
            index, _start = self.shelf_press
            self.shelf_press = None
            if index < len(self.shelf):
                # A click opens the file.
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.shelf[index]))

    def shelf_dragging(self):
        return self.shelf_press is not None

    def run_quick_button(self, name):
        if name == "output":
            self.open_output_panel()
        elif name == "mic":
            self.toggle_microphone()
        elif name == "screenshot":
            try:
                os.startfile("ms-screenclip:")
            except OSError:
                logging.exception("Could not start the screen snip")
        elif name == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif name == "note":
            self.open_reminder_dialog()
        elif name == "clipboard":
            self.open_clipboard_panel()

    def open_reminder_dialog(self):
        try:
            from island_settings import ReminderDialog
        except ImportError:
            self.open_settings()
            return
        if getattr(self, "reminder_dialog", None) is not None:
            self.reminder_dialog.raise_()
            return
        dialog = ReminderDialog(self)
        self.reminder_dialog = dialog

        def closed(_result=0):
            self.reminder_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(closed)
        dialog.show()

    def toggle_microphone(self):
        if self.microphone is None:
            self.notify(tr("Микрофон не найден"))
            return
        muted = self.safe(self.microphone.toggle)
        if muted is None:
            self.notify(tr("Микрофон не найден"))
            return
        self.mic_muted = muted
        self.notify(tr("Микрофон выключен") if muted else tr("Микрофон включён"), seconds=1.2, keep_width=True)
        self.mark_moving()

    def microphone_available(self):
        return self.microphone is not None and self.mic_muted is not None

    def notification_status(self):
        if self.notifications is None:
            return "unavailable"
        return self.safe(self.notifications.status, default="unavailable")

    # ----- File shelf: drag and drop ------------------------------------

    def dragEnterEvent(self, event):
        if dict.get(self.config, "music_only"):
            event.ignore()  # no file shelf in the music-only island
            return
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self.shelf_drop_hover = True
            self.mark_moving()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.shelf_drop_hover = False
        self.mark_moving()

    def dropEvent(self, event):
        self.shelf_drop_hover = False
        added = False
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path in self.shelf:
                continue
            if len(self.shelf) >= MAX_SHELF_ITEMS:
                self.notify(tr("Полка заполнена"))
                break
            self.shelf.append(path)
            added = True
        if added:
            event.acceptProposedAction()
        self.mark_moving()

    # ----- Positioning guide ---------------------------------------------

    def show_position_guide(self, visible):
        if not visible:
            if self.position_guide is not None:
                self.position_guide.hide()
            return
        if self.position_guide is None:
            self.position_guide = PositionGuide()
        self.position_guide.accent = self.current_accent_color()
        # The screen the island is on now, which may differ from the saved one.
        here = QApplication.screenAt(self.geometry().center()) if self.isVisible() else None
        self.update_position_guide(here or self.target_screen())
        self.position_guide.show()
        self.raise_()
        self.place_shadow()

    def update_position_guide(self, screen):
        guide = self.position_guide
        if guide is None or not guide.isVisible() and not self.positioning and not self.dragging_enabled:
            return
        guide.cover(screen)
        guide.snapped = self.snapped_to_center
        guide.update()
        self.raise_()

    # ----- Tray -------------------------------------------------------------

    def sync_tray(self):
        wanted = self.config["tray_enabled"] and QSystemTrayIcon.isSystemTrayAvailable()
        if not wanted:
            if self.tray is not None:
                self.tray.hide()
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(make_tray_icon(), self)
            self.tray.activated.connect(self.on_tray_activated)
            self.tray.messageClicked.connect(self.open_update_page)
        menu = QMenu()
        for text, action in (
            (tr("Настройки"), self.open_settings),
            (tr("Вернуть в центр"), self.reset_position),
            (tr("Режим призрака"), self.toggle_ghost_mode),
            (tr("Проверить обновления"), lambda: self.check_for_updates(True)),
        ):
            item = QAction(text, menu)
            item.triggered.connect(action)
            menu.addAction(item)
        menu.addSeparator()
        quit_item = QAction(tr("Закрыть Default Island"), menu)
        quit_item.triggered.connect(QApplication.quit)
        menu.addAction(quit_item)
        self._tray_menu = menu  # the tray does not keep its menu alive
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings()

    # ----- Updates ------------------------------------------------------

    def check_for_updates(self, manual=False):
        self.update_checker.start(bool(manual))

    def on_update_result(self, result, manual):
        if result is None:
            if manual:
                self.notify(tr("Не удалось проверить обновления"), force=True)
            else:
                # Often the network is not up yet right after Windows starts:
                # try again soon instead of a day later.
                self._next_update_check = min(
                    self._next_update_check, time.monotonic() + UPDATE_RETRY_SEC
                )
            return
        version, url, installer = result
        self.update_url = url
        if version and version_tuple(version) > version_tuple(APP_VERSION):
            # Only the installed program updates itself, not a run from source.
            if self.config["auto_update"] and installer and getattr(sys, "frozen", False):
                if not self._updating:
                    self._updating = True
                    self.push_banner(_Banner(
                        "update", tr("Обновление до версии {version}").format(version=version),
                        tr("Скачивается, островок перезапустится сам"), 5.0,
                    ), force=manual)
                    self.update_checker.download(version, installer)
                return
            banner = _Banner("update", tr("Доступна версия {version}").format(version=version),
                             tr("Нажмите, чтобы открыть страницу загрузки"), 8.0, url=url)
            self.push_banner(banner, force=manual)
            if self.tray is not None and self.tray.isVisible():
                self.tray.showMessage(APP_NAME, banner.title)
        elif manual:
            self.notify(tr("Установлена последняя версия"), force=True)

    def on_update_downloaded(self, version, path):
        """Run the downloaded installer silently; it closes the island,
        replaces it and starts it again."""
        if not path:
            self._updating = False
            self.notify(tr("Не удалось скачать обновление"), force=True)
            return
        try:
            subprocess.Popen(
                [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/AUTOUPDATE"],
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except OSError:
            logging.exception("Could not start the update")
            self._updating = False
            self.notify(tr("Не удалось скачать обновление"), force=True)

    def open_update_page(self):
        url = getattr(self, "update_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def banner_clicked(self, position=None):
        """A click on the collapsed island while a banner shows."""
        banner = self.current_banner()
        if banner is not None and banner.kind == "call":
            for rect, _key, action in self.call_rects:
                if position is not None and rect.adjusted(-3, -3, 3, 3).contains(position):
                    action()
                    return True
            # Elsewhere on the ringing card: the app, to answer there.
            self.focus_call_app(ringing=True)
            return True
        if banner is not None and banner.url:
            QDesktopServices.openUrl(QUrl(banner.url))
            self.banners.pop(0)
            return True
        return False
