"""Shared pieces of Default Island: constants, settings and painting helpers."""
import ctypes
import json
import logging
import math
import os
import sys
import time
import uuid

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainterPath, QPen, QRadialGradient,
)

from island_i18n import LANGUAGE_CODES, register, system_language, tr

APP_NAME = "Default Island"
APP_VERSION = "2.1.1"
# Where new versions are published; the update check reads its releases.
GITHUB_REPO = "lourchik69/default-island"
APP_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "DefaultIsland",
)
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")
LOG_FILE = os.path.join(APP_DATA_DIR, "default-island.log")
REMINDERS_FILE = os.path.join(APP_DATA_DIR, "reminders.json")
LYRICS_CACHE_DIR = os.path.join(APP_DATA_DIR, "lyrics")
MAX_TIMER_SECONDS = 359999
MAX_REMINDERS = 50
MAX_REMINDER_TEXT = 200

# --- Look ------------------------------------------------------------------
# Warm graphite with a sand accent. Every colour is deliberately muted: the
# island sits on top of other work and must never be the brightest thing on
# the screen.
INK_TOP = QColor(28, 26, 24)
INK_BOTTOM = QColor(12, 11, 10)
GLASS_TOP = QColor(44, 41, 37)
GLASS_BOTTOM = QColor(17, 16, 14)
LIGHT_GLASS_TOP = QColor(248, 247, 245)
LIGHT_GLASS_BOTTOM = QColor(216, 216, 220)
# The glass takes a little of the accent where the light enters (top left)
# and of a neighbouring hue in the opposite corner.
GLASS_TINT_NEAR = 0.24
GLASS_TINT_FAR = 0.16
GLASS_HUE_DRIFT = 310  # degrees around the colour wheel
TEXT_PRIMARY = QColor(244, 240, 232)
TEXT_SECONDARY = QColor(170, 164, 154)
TEXT_TERTIARY = QColor(126, 121, 113)
DANGER_COLOR = QColor(214, 142, 130)
DEFAULT_ACCENT = QColor("#D6B58F")
# The keys are stored in config.json; the older ones keep their names from
# the time the presets were bright, so saved settings stay valid.
ACCENT_PRESETS = {
    "Sand": QColor("#D6B58F"),
    "Orange": QColor("#CC9175"),
    "Pink": QColor("#C9A1A7"),
    "Purple": QColor("#ABA4CB"),
    "Blue": QColor("#94AAC5"),
    "Cyan": QColor("#8FB7BC"),
    "Lime": QColor("#A5B99B"),
}
# Russian source texts; show them through tr().
ACCENT_LABELS = {
    "Auto": "Авто · по обложке трека",
    "Sand": "Песочный",
    "Orange": "Терракотовый",
    "Pink": "Пыльная роза",
    "Purple": "Лавандовый",
    "Blue": "Стальной синий",
    "Cyan": "Туманный голубой",
    "Lime": "Шалфей",
}
FONT_TEXT = ("Segoe UI Variable Text", "Segoe UI")
FONT_DISPLAY = ("Segoe UI Variable Display", "Segoe UI")

# Island geometry, in the island's own units. One unit is one pixel at 100%
# size; the size settings scale everything drawn from these numbers.
COLLAPSED_HEIGHT = 36.0
EXPANDED_WIDTH = 370.0
EXPANDED_HEIGHT = 152.0
# With the timer row under the controls, or the timer panel open.
EXPANDED_HEIGHT_TIMER = 180.0
COLLAPSED_RADIUS = 18.0
EXPANDED_RADIUS = 30.0

# --- Settings --------------------------------------------------------------
CONFIG_VERSION = 7
THEMES = ("Default", "Liquid Glass", "Liquid Glass Light")
LEFT_ZONES = ("Album Art", "None")
CENTER_ZONES = ("Track / Lyrics", "Ping", "CPU / RAM", "GPU", "Time", "Weather")
RIGHT_ZONES = ("Visualizer", "Repeat Button", "None")
# What the island shows while no player has a track.
IDLE_VIEWS = ("clock", "weather", "system", "blank")
MAX_CITY_LENGTH = 60
VOLUME_TARGETS = ("Brave", "Discord", "Master Windows")
# "full": the collapsed island grows to fit the track name.
# "short": it keeps one size and a long name ends with "…".
COLLAPSED_TITLE_MODES = ("full", "short")
POSITION_EDGES = ("top", "bottom")
# Buttons the user may put in the expanded island, in this order.
QUICK_BUTTONS = ("output", "mic", "screenshot", "lock", "note", "clipboard")
# Where the microphone / camera / recording dots are drawn.
PRIVACY_PLACES = ("inside", "beside")
SCALE_RANGE = (70, 160)  # percent
FPS_RANGE = (30, 240)
LYRICS_OFFSET_LIMIT = 30  # tenths of a second either way
FPS_PRESETS = (30, 60, 90, 120, 144, 165, 240)
DEFAULT_HOTKEY = "Ctrl+Shift+G"
DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "language": "ru",
    "theme": "Default",
    "accent_color": "Auto",
    # 0 = almost clear glass, 100 = almost solid. Glass themes only.
    "glass_opacity": 50,
    # How strongly the accent colours the glass, 0-100.
    "glass_tint": 50,
    # A streak of light crosses the glass on hover and on a new track.
    "glass_glint": True,
    # The blurred artwork fills the expanded island.
    "cover_backdrop": True,
    # Size of the island while collapsed and while expanded, percent.
    "scale_collapsed": 100,
    "scale_expanded": 100,
    "fps": 60,
    # Name of the screen the island lives on; "" is the primary screen.
    "monitor": "",
    # Centre of the island and the edge of the capsule it hangs from, in
    # desktop pixels. None keeps it centred near the top of the screen.
    "pos_x": None,
    "pos_y": None,
    # "bottom": the island sits low on the screen and opens upwards.
    "pos_edge": "top",
    "left_zone": "Album Art",
    "center_zone": "Track / Lyrics",
    "right_zone": "Visualizer",
    "collapsed_title": "full",
    # Without music: "clock" (time and date), "weather", "system" (CPU and
    # RAM) or "blank" (a small empty capsule).
    "idle_view": "clock",
    # City for the weather; empty: no weather, nothing is sent anywhere.
    "weather_city": "",
    # Incoming calls (from toasts) and a timer while a calling app talks.
    "calls_enabled": True,
    # Only the player: no side buttons, quick buttons, timer, indicators or
    # notifications (calls and reminders still show). See IslandConfig.
    "music_only": False,
    "gear_button": True,
    "dnd_button": True,
    "volume_button": True,
    "repeat_button": True,
    "like_button": True,
    # The system volume and screen brightness shown on the island when they change.
    "hud_enabled": True,
    # What is copied shows on the island; the "clipboard" button keeps a history.
    "clipboard_enabled": True,
    # A red dot with a timer while the screen is recorded or shared.
    "recording_enabled": True,
    "privacy_dots_place": "inside",
    # Each line of the lyrics translated into the interface language (Google).
    "lyrics_translate": False,
    # Videos on youtube.com count as music (YouTube Music always does).
    "youtube_video": False,
    # Synced lyrics: the track name and artist are sent to lrclib.net.
    "lyrics_enabled": False,
    # Sung words fill with the track's colour, karaoke style.
    "lyrics_karaoke": True,
    # Shift of the lyrics in tenths of a second; positive shows them later.
    "lyrics_offset": 0,
    "real_visualizer": True,
    "track_animation": True,
    "timer_button_enabled": True,
    "timer_configured": False,
    "timer_duration_seconds": 300,
    "mic_button_enabled": True,
    "privacy_dots_enabled": True,
    "layout_indicator_enabled": True,
    "battery_alerts_enabled": True,
    "device_alerts_enabled": True,
    "downloads_enabled": True,
    "notifications_enabled": True,
    "update_check_enabled": True,
    # New versions are downloaded and installed silently (chosen in the installer).
    "auto_update": False,
    "tray_enabled": True,
    "quick_buttons": ["output", "screenshot", "note"],
    "ghost_hotkey": DEFAULT_HOTKEY,
    "volume_target": "Brave",
    "auto_start": False,
    "hide_fullscreen": False,
    "dnd_mode": False,
}
# On/off settings; each must be a bool in config.json.
BOOL_KEYS = tuple(
    key
    for key, value in DEFAULT_CONFIG.items()
    if isinstance(value, bool)
)

register({
    "Некорректное сочетание клавиш": (
        "This key combination is not valid",
        "Некоректне сполучення клавіш",
    ),
    "Горячая клавиша должна содержать от одной до трёх клавиш": (
        "A hotkey must have one to three keys",
        "Гаряча клавіша має містити від однієї до трьох клавіш",
    ),
    "Номер функциональной клавиши должен быть от F1 до F24": (
        "Function keys go from F1 to F24",
        "Номер функціональної клавіші має бути від F1 до F24",
    ),
    "Клавиша «{key}» не поддерживается": (
        "The key “{key}” is not supported",
        "Клавіша «{key}» не підтримується",
    ),
    "Добавьте Ctrl, Alt или Win: одна такая клавиша перестала бы работать в других программах": (
        "Add Ctrl, Alt or Win: on its own this key would stop working in other programs",
        "Додайте Ctrl, Alt або Win: сама така клавіша перестала б працювати в інших програмах",
    ),
    "Авто · по обложке трека": ("Auto · from the artwork", "Авто · за обкладинкою треку"),
    "Песочный": ("Sand", "Пісочний"),
    "Терракотовый": ("Terracotta", "Теракотовий"),
    "Пыльная роза": ("Dusty rose", "Пильна троянда"),
    "Лавандовый": ("Lavender", "Лавандовий"),
    "Стальной синий": ("Steel blue", "Сталевий синій"),
    "Туманный голубой": ("Misty blue", "Туманний блакитний"),
    "Шалфей": ("Sage", "Шавлія"),
})

_font_cache = {}


def ui_font(size, weight=QFont.Weight.Medium, display=False, spacing=0.0):
    """A cached interface font; `display` picks the cut meant for larger text."""
    key = (size, weight, display, spacing)
    font = _font_cache.get(key)
    if font is None:
        font = QFont()
        font.setFamilies(list(FONT_DISPLAY if display else FONT_TEXT))
        font.setPointSizeF(size)
        font.setWeight(weight)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        _font_cache[key] = font
    return font


def with_alpha(color, alpha):
    """The colour with its opacity replaced (0-255)."""
    return QColor(
        color.red(), color.green(), color.blue(), max(0, min(255, int(alpha)))
    )


def white(alpha):
    return QColor(255, 255, 255, max(0, min(255, int(alpha))))


def mix_colors(first, second, amount):
    """Blend from `first` (amount 0) to `second` (amount 1)."""
    amount = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
    )


def soften_accent(color):
    """Keep the hue of a colour but bring it into the muted range of the UI."""
    hue = color.hsvHue()
    if hue < 0:
        return QColor(DEFAULT_ACCENT)
    saturation = min(color.hsvSaturation(), 96)
    value = max(184, min(224, color.value()))
    return QColor.fromHsv(hue, saturation, value)


def round_pen(color, width):
    """A pen with round ends and corners, as every icon here is drawn."""
    return QPen(
        color,
        width,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )


def rounded_rect_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class Look:
    """How one theme draws the island: its surface and its ink.

    Everything painted on the island takes its colours from here, so a
    theme is one of these objects and nothing else. Treat it as read-only.
    """

    def __init__(
        self,
        glass=False,
        light=False,
        base_top=INK_TOP,
        base_bottom=INK_BOTTOM,
        alpha_top=255,
        alpha_bottom=255,
        tint=0.0,
        primary=TEXT_PRIMARY,
        secondary=TEXT_SECONDARY,
        tertiary=TEXT_TERTIARY,
        knob=QColor(255, 253, 248),
        panel=QColor(30, 28, 26),
        shadow=1.0,
    ):
        self.glass = glass
        self.light = light
        self.base_top = base_top
        self.base_bottom = base_bottom
        # How much of what is under the island the surface hides (0-255).
        self.alpha_top = alpha_top
        self.alpha_bottom = alpha_bottom
        # Strength of the accent colouring of the glass: 0 none, 1 normal,
        # 2 strong.
        self.tint = tint
        # Text and icons, from the most to the least important.
        self.primary = primary
        self.secondary = secondary
        self.tertiary = tertiary
        # Slider handles and other solid marks.
        self.knob = knob
        # Body of the small panels that float over the island's content.
        self.panel = panel
        # Strength of the island's own shadow, 0-1.
        self.shadow = shadow

    def veil(self, alpha):
        """A translucent layer that lifts a control off the surface: light
        on the dark themes, dark on the light one."""
        level = 0 if self.light else 255
        return QColor(level, level, level, max(0, min(255, int(alpha))))

    def text_shadow(self, alpha):
        """The colour put one pixel under text on glass so it stays readable
        over whatever shows through; None where the surface is solid."""
        if not self.glass:
            return None
        if self.light:
            return QColor(255, 255, 255, max(0, min(255, int(alpha * 0.55))))
        return QColor(0, 0, 0, max(0, min(255, int(alpha * 0.5))))


SOLID_LOOK = Look()


def make_look(config):
    """The look for the theme and glass settings in `config`."""
    theme = config.get("theme", "Default")
    if theme not in ("Liquid Glass", "Liquid Glass Light"):
        return SOLID_LOOK
    opacity = config.get("glass_opacity", 50)
    tint = max(0.0, min(2.0, config.get("glass_tint", 50) / 50.0))
    if theme == "Liquid Glass Light":
        # Dark ink on pale glass needs more body than light ink on dark glass.
        alpha_top = round(max(60.0, min(244.0, 168.0 + (opacity - 50) * 1.9)))
        return Look(
            glass=True,
            light=True,
            base_top=LIGHT_GLASS_TOP,
            base_bottom=LIGHT_GLASS_BOTTOM,
            alpha_top=alpha_top,
            alpha_bottom=min(250, alpha_top + 22),
            tint=tint,
            primary=QColor(26, 25, 28),
            secondary=QColor(74, 72, 78),
            tertiary=QColor(112, 110, 118),
            knob=QColor(34, 33, 36),
            panel=QColor(244, 243, 241),
            shadow=0.6,
        )
    alpha_top = round(max(14.0, min(226.0, 112.0 + (opacity - 50) * 2.2)))
    return Look(
        glass=True,
        base_top=GLASS_TOP,
        base_bottom=GLASS_BOTTOM,
        alpha_top=alpha_top,
        alpha_bottom=min(250, alpha_top + 34),
        tint=tint,
        shadow=0.85,
    )


def glass_companion(accent):
    """A neighbouring hue for the far corner of the glass, so its tint
    drifts from one colour to another instead of being flat."""
    hue = accent.hsvHue()
    if hue < 0:
        return QColor(accent)
    return QColor.fromHsv(
        (hue + GLASS_HUE_DRIFT) % 360, accent.hsvSaturation(), accent.value()
    )


def glass_fill(rect, accent, look, alpha=255):
    """The body of the glass: a diagonal gradient that is lighter and
    tinted where the light enters and deep in the opposite corner."""
    share = alpha / 255.0
    middle = mix_colors(look.base_top, look.base_bottom, 0.55)
    fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
    fill.setColorAt(
        0.0,
        with_alpha(
            mix_colors(look.base_top, accent, GLASS_TINT_NEAR * look.tint),
            look.alpha_top * share,
        ),
    )
    fill.setColorAt(
        0.55, with_alpha(middle, (look.alpha_top + look.alpha_bottom) / 2 * share)
    )
    fill.setColorAt(
        1.0,
        with_alpha(
            mix_colors(
                look.base_bottom, glass_companion(accent), GLASS_TINT_FAR * look.tint
            ),
            look.alpha_bottom * share,
        ),
    )
    return fill


def paint_glass_tint(painter, rect, accent, look, alpha=255):
    """Colour pooling softly in two opposite corners of the glass. The
    painter must already be clipped to the shape of the glass."""
    share = alpha / 255.0 * look.tint
    if share <= 0.01:
        return
    reach = max(rect.width() * 0.72, rect.height() * 1.5)
    for corner, color, peak in (
        (rect.topLeft(), accent, 58),
        (rect.bottomRight(), glass_companion(accent), 50),
    ):
        pool = QRadialGradient(corner, reach)
        pool.setColorAt(0.0, with_alpha(color, peak * share))
        pool.setColorAt(0.5, with_alpha(color, peak * share * 0.32))
        pool.setColorAt(1.0, with_alpha(color, 0))
        painter.fillRect(rect, QBrush(pool))


def surface_fill(pill, accent, look, alpha=255):
    """The brush that fills the island's body in this look."""
    if look.glass:
        return QBrush(glass_fill(pill, accent, look, alpha))
    fill = QLinearGradient(pill.topLeft(), pill.bottomLeft())
    fill.setColorAt(0.0, with_alpha(look.base_top, alpha))
    fill.setColorAt(1.0, with_alpha(look.base_bottom, alpha))
    return QBrush(fill)


def paint_island_surface(
    painter, pill, radius, accent, look, playing=0.0, outline=None, backdrop=None
):
    """The body of the island: fill, light, glow and border.

    `playing` (0..1) is how strongly the glow of playing music shows.
    `outline` colours the border with the accent: "drag" while the island
    is being moved, "panel" while one of its panels is open.
    `backdrop`, if given, is called with the painter clipped to the island's
    shape right after the fill, to put a picture between the body and the
    light on it.
    """
    glass = look.glass
    shape = rounded_rect_path(pill, radius)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(surface_fill(pill, accent, look))
    painter.drawPath(shape)

    painter.save()
    painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    if backdrop is not None:
        backdrop(painter)
    if glass:
        paint_glass_tint(painter, pill, accent, look)
    # Light falling on the top of the surface.
    sheen = QLinearGradient(
        pill.topLeft(),
        QPointF(pill.left(), pill.top() + min(pill.height(), 64.0)),
    )
    sheen.setColorAt(0.0, white((96 if look.light else 34) if glass else 9))
    sheen.setColorAt(1.0, white(0))
    painter.fillRect(pill, QBrush(sheen))
    if playing > 0.01:
        # A faint glow of the accent rises from the bottom edge while
        # music plays. It is squashed into an ellipse along that edge.
        reach = pill.width() * 0.5
        glow = QRadialGradient(QPointF(0.0, 0.0), reach)
        peak = ((54 if look.light else 34) if glass else 26) * playing
        glow.setColorAt(0.0, with_alpha(accent, peak))
        glow.setColorAt(1.0, with_alpha(accent, 0))
        painter.save()
        painter.translate(pill.center().x(), pill.bottom() + 2.0)
        painter.scale(1.0, 0.5)
        painter.fillRect(QRectF(-reach, -reach, reach * 2, reach), QBrush(glow))
        painter.restore()
    painter.restore()

    edge = QLinearGradient(pill.topLeft(), pill.bottomLeft())
    if outline == "drag":
        edge.setColorAt(0.0, with_alpha(accent, 210))
        edge.setColorAt(1.0, with_alpha(accent, 150))
    elif outline == "panel":
        edge.setColorAt(0.0, with_alpha(accent, 120))
        edge.setColorAt(1.0, with_alpha(accent, 56))
    elif look.light:
        edge.setColorAt(0.0, white(235))
        edge.setColorAt(0.5, white(130))
        edge.setColorAt(1.0, white(180))
    elif glass:
        edge.setColorAt(0.0, white(96))
        edge.setColorAt(0.5, white(36))
        edge.setColorAt(1.0, white(54))
    else:
        edge.setColorAt(0.0, white(42))
        edge.setColorAt(1.0, white(17))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if glass:
        # A dark rim outside keeps the edge visible over light windows.
        painter.setPen(QPen(QColor(0, 0, 0, 34 if look.light else 44), 1.0))
        painter.drawRoundedRect(
            pill.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5
        )
        # The light bends in the rim: brightest right at the edge, gone
        # a few pixels in, with a weaker echo along the bottom.
        gain = 2.6 if look.light else 1.0
        for inset, peak in ((1.5, 34), (2.5, 17), (3.5, 8)):
            inner = QLinearGradient(pill.topLeft(), pill.bottomLeft())
            inner.setColorAt(0.0, white(peak * gain))
            inner.setColorAt(0.5, white(0))
            inner.setColorAt(1.0, white(peak * gain * 0.45))
            painter.setPen(QPen(QBrush(inner), 1.0))
            painter.drawRoundedRect(
                pill.adjusted(inset, inset, -inset, -inset),
                max(0.0, radius - inset),
                max(0.0, radius - inset),
            )
    rim = pill.adjusted(0.5, 0.5, -0.5, -0.5)
    rim_radius = max(0.0, radius - 0.5)
    painter.setPen(QPen(QBrush(edge), 1.0))
    painter.drawRoundedRect(rim, rim_radius, rim_radius)
    if glass and outline is None:
        # Two glints on opposite corners, where a curved sheet of glass
        # catches the light.
        reach = min(pill.width() * 0.5, max(pill.height() * 1.5, 54.0))
        peaks = (255, 170) if look.light else (130, 76)
        for corner, peak in zip((pill.topLeft(), pill.bottomRight()), peaks):
            glint = QRadialGradient(corner, reach)
            glint.setColorAt(0.0, white(peak))
            glint.setColorAt(1.0, white(0))
            painter.setPen(QPen(QBrush(glint), 1.0))
            painter.drawRoundedRect(rim, rim_radius, rim_radius)


# (offset down, blur, peak opacity): a tight contact shadow and a wide soft one.
SHADOW_LAYERS = ((1.0, 5.0, 0.20), (10.0, 34.0, 0.30))


def paint_soft_shadow(painter, rect, radius, strength, size=1.0):
    """The shadow the island casts, drawn around `rect`.

    `size` scales the shadow together with the island it belongs to.
    """
    # Small shapes cast a proportionally smaller shadow.
    scale = min(1.0, rect.height() / (COLLAPSED_HEIGHT * size)) * size
    for offset, blur, peak in SHADOW_LAYERS:
        offset *= scale
        blur *= scale
        if blur < 1.0:
            continue
        body = rect.translated(0.0, offset)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(255 * peak * strength)))
        painter.drawRoundedRect(body, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # One-pixel rings that fade out stand in for a blur.
        for step in range(int(blur)):
            distance = step + 0.5
            alpha = int(255 * peak * strength * (1.0 - distance / blur) ** 2.2)
            if alpha <= 0:
                break
            painter.setPen(QPen(QColor(0, 0, 0, alpha), 1.0))
            painter.drawRoundedRect(
                body.adjusted(-distance, -distance, distance, distance),
                radius + distance,
                radius + distance,
            )


def user32_function(name, argtypes, restype):
    function = getattr(ctypes.windll.user32, name)
    function.argtypes = argtypes
    function.restype = restype
    return function


def is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


HOTKEY_MODIFIERS = {"CTRL": 0x0002, "ALT": 0x0001, "SHIFT": 0x0004, "WIN": 0x0008}
HOTKEY_NAMED_KEYS = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PGUP": 0x21,
    "PAGEDOWN": 0x22,
    "PGDOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
}
# Keys that may be a hotkey on their own: nobody types with them.
HOTKEY_STANDALONE = {0x2D} | set(range(0x70, 0x70 + 24))


def parse_hotkey(value):
    """(modifier flags, virtual key) of a hotkey such as "Ctrl+Shift+G".

    Raises ValueError with a message for the user when the text is not a
    hotkey, or when it would take a typing key away from every program.
    """
    if not isinstance(value, str):
        raise ValueError(tr("Некорректное сочетание клавиш"))
    modifiers = 0
    names = value.split("+")
    if len(names) < 1 or len(names) > 3:
        raise ValueError(tr("Горячая клавиша должна содержать от одной до трёх клавиш"))

    for name in names[:-1]:
        flag = HOTKEY_MODIFIERS.get(name.strip().upper())
        if flag is None or modifiers & flag:
            raise ValueError(tr("Некорректное сочетание клавиш"))
        modifiers |= flag

    key_name = names[-1].strip().upper()
    if len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
        virtual_key = ord(key_name)
    elif key_name.startswith("F") and key_name[1:].isdigit():
        function_number = int(key_name[1:])
        if not 1 <= function_number <= 24:
            raise ValueError(tr("Номер функциональной клавиши должен быть от F1 до F24"))
        virtual_key = 0x70 + function_number - 1
    elif key_name.startswith("NUM") and key_name[3:].isdigit() and len(key_name) == 4:
        # The digits of the numeric keypad are keys of their own.
        virtual_key = 0x60 + int(key_name[3:])
    else:
        virtual_key = HOTKEY_NAMED_KEYS.get(key_name)
        if virtual_key is None:
            raise ValueError(tr("Клавиша «{key}» не поддерживается").format(key=key_name))
    # Shift alone still types a character, so it does not count here.
    strong_modifier = modifiers & (0x0001 | 0x0002 | 0x0008)
    if not strong_modifier and virtual_key not in HOTKEY_STANDALONE:
        raise ValueError(
            tr(
                "Добавьте Ctrl, Alt или Win: одна такая клавиша перестала бы "
                "работать в других программах"
            )
        )
    return modifiers, virtual_key


INT32_MAX = 2**31 - 1


def _clamped_int(value, low, high):
    return int(max(low, min(high, value)))


def sanitize_config(raw):
    """Build a complete, valid configuration from whatever was stored.

    Unknown keys are dropped and invalid values fall back to the defaults, so
    a hand-edited or outdated config.json can never stop the app from starting.
    """
    cfg = DEFAULT_CONFIG.copy()
    cfg["quick_buttons"] = list(DEFAULT_CONFIG["quick_buttons"])
    if not isinstance(raw, dict):
        cfg["language"] = system_language()
        return cfg
    version = raw.get("config_version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        version = 1

    def choice(key, allowed, renamed=None):
        value = raw.get(key)
        if isinstance(value, str):
            value = (renamed or {}).get(value, value)
            if value in allowed:
                cfg[key] = value

    def number(key, low, high):
        value = raw.get(key)
        if is_number(value):
            cfg[key] = _clamped_int(value, low, high)

    # Settings saved before languages existed were made in Russian.
    choice("language", LANGUAGE_CODES)
    choice("theme", THEMES)
    choice("accent_color", ("Auto", *ACCENT_PRESETS))
    choice("left_zone", LEFT_ZONES, {"Timer Button": "Album Art"})
    choice(
        "center_zone",
        CENTER_ZONES,
        {"CPU / GPU Temp": "CPU / RAM", "FPS & Ping": "Ping"},
    )
    choice("right_zone", RIGHT_ZONES)
    # Brave, Discord, "Master Windows", or any program by its name.
    target = raw.get("volume_target")
    if isinstance(target, str):
        target = " ".join(target.split())
        if target.casefold().endswith(".exe"):
            target = target[:-4]
        if target:
            cfg["volume_target"] = target[:64]
    choice("collapsed_title", COLLAPSED_TITLE_MODES)
    choice("idle_view", IDLE_VIEWS)
    choice("privacy_dots_place", PRIVACY_PLACES)
    choice("pos_edge", POSITION_EDGES)
    city = raw.get("weather_city")
    if isinstance(city, str):
        cfg["weather_city"] = " ".join(city.split())[:MAX_CITY_LENGTH]

    for key in BOOL_KEYS:
        if isinstance(raw.get(key), bool):
            cfg[key] = raw[key]
    if version < 2:
        cfg["hide_fullscreen"] = False

    number("glass_opacity", 0, 100)
    number("glass_tint", 0, 100)
    number("scale_collapsed", *SCALE_RANGE)
    number("scale_expanded", *SCALE_RANGE)
    number("fps", *FPS_RANGE)
    number("lyrics_offset", -LYRICS_OFFSET_LIMIT, LYRICS_OFFSET_LIMIT)

    monitor = raw.get("monitor")
    if isinstance(monitor, str):
        cfg["monitor"] = monitor[:128]

    buttons = raw.get("quick_buttons")
    if isinstance(buttons, list):
        chosen = [name for name in buttons if isinstance(name, str)]
        # Stored in the fixed order, each at most once.
        cfg["quick_buttons"] = [name for name in QUICK_BUTTONS if name in chosen]

    seconds = raw.get("timer_duration_seconds")
    if not is_number(seconds) and is_number(raw.get("timer_duration_minutes")):
        seconds = raw["timer_duration_minutes"] * 60
    if is_number(seconds):
        cfg["timer_duration_seconds"] = _clamped_int(seconds, 0, MAX_TIMER_SECONDS)

    try:
        parse_hotkey(raw.get("ghost_hotkey"))
        cfg["ghost_hotkey"] = raw["ghost_hotkey"]
    except ValueError:
        pass

    if version >= 7:
        for key in ("pos_x", "pos_y"):
            if is_number(raw.get(key)):
                cfg[key] = _clamped_int(raw[key], -INT32_MAX, INT32_MAX)
    else:
        # Up to version 6 the position was the corner of a 420-pixel-wide
        # window whose capsule began 4 pixels below its top.
        x_pos, y_pos = raw.get("x_pos"), raw.get("y_pos")
        if is_number(y_pos):
            y_pos = _clamped_int(y_pos, -INT32_MAX + 8, INT32_MAX - 8)
            # Before version 6 a negative x meant "keep the island centred".
            if is_number(x_pos) and (x_pos >= 0 or version >= 6):
                x_pos = _clamped_int(x_pos, -INT32_MAX + 512, INT32_MAX - 512)
                cfg["pos_x"] = x_pos + 210
                cfg["pos_y"] = y_pos + 4
            elif y_pos != 38:
                cfg["pos_y"] = y_pos + 4
    if cfg["pos_y"] is None:
        cfg["pos_edge"] = "top"
    return cfg


# Switched off while "music_only" is on; the user's own choices stay stored.
MUSIC_ONLY_OFF = frozenset((
    "timer_button_enabled", "mic_button_enabled", "privacy_dots_enabled",
    "layout_indicator_enabled", "battery_alerts_enabled", "device_alerts_enabled",
    "downloads_enabled", "notifications_enabled", "clipboard_enabled",
    "recording_enabled", "dnd_button",
))


class IslandConfig(dict):
    """The island's settings. Reading one through [] gives what is in effect:
    with "music_only" on, the extras listed in MUSIC_ONLY_OFF read as off and
    there are no quick buttons. Copies (dict(config)) and the saved file keep
    the stored values, so the settings window shows the user's own choices."""

    def __getitem__(self, key):
        if dict.get(self, "music_only"):
            if key in MUSIC_ONLY_OFF:
                return False
            if key == "quick_buttons":
                return []
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        return self[key] if key in self else default


INSTALLER_KEY = r"Software\Default Island"


def take_installer_choices(cfg):
    """Settings chosen in the installer ("start with Windows", "update
    automatically"), taken over once after an installation. True when the
    configuration changed."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            if not winreg.QueryValueEx(key, "Pending")[0]:
                return False
            for name, setting in (("AutoUpdate", "auto_update"), ("AutoStart", "auto_start")):
                try:
                    cfg[setting] = bool(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    pass
            winreg.DeleteValue(key, "Pending")
            return True
    except OSError:
        return False


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return sanitize_config(json.load(f))
        except (OSError, ValueError):
            logging.exception("Could not read settings from %s", CONFIG_FILE)
    return sanitize_config(None)


def _write_json(path, data):
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    temp_file = path + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp_file, path)


def save_config(cfg):
    _write_json(CONFIG_FILE, cfg)


# --- Reminders ---------------------------------------------------------------
# A reminder is a dict: {"id": str, "text": str, "at": float, "repeat": str}.
# "at" is the time.time() it is due; "repeat" is "none" or "daily" (an alarm
# clock is a daily reminder).
REMINDER_REPEATS = ("none", "daily")


def new_reminder(text, at, repeat="none"):
    return {
        "id": uuid.uuid4().hex[:12],
        "text": " ".join(str(text).split())[:MAX_REMINDER_TEXT],
        "at": float(at),
        "repeat": repeat if repeat in REMINDER_REPEATS else "none",
    }


def load_reminders():
    """Stored reminders, soonest first; an unreadable file is an empty list."""
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logging.exception("Could not read reminders from %s", REMINDERS_FILE)
        return []
    reminders = []
    if isinstance(raw, list):
        for item in raw[:MAX_REMINDERS]:
            if not isinstance(item, dict):
                continue
            text, at = item.get("text"), item.get("at")
            if not isinstance(text, str) or not text.strip() or not is_number(at):
                continue
            # Times far outside any real calendar would break the clock maths.
            if not 0 < at < 4102444800:
                continue
            reminder = new_reminder(text, at, item.get("repeat"))
            if isinstance(item.get("id"), str) and item["id"]:
                reminder["id"] = item["id"][:32]
            reminders.append(reminder)
    reminders.sort(key=lambda reminder: reminder["at"])
    return reminders


def save_reminders(reminders):
    _write_json(REMINDERS_FILE, list(reminders)[:MAX_REMINDERS])


def next_daily_time(at, now=None):
    """The next moment after `now` with the same time of day as `at`."""
    now = time.time() if now is None else now
    day = 86400.0
    if at > now:
        return at
    return at + (math.floor((now - at) / day) + 1) * day


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # Prefer pythonw.exe so no console window opens at sign-in.
    interpreter = sys.executable
    windowless = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
    if os.path.isfile(windowless):
        interpreter = windowless
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "island.py")
    return f'"{interpreter}" "{script}"'


def set_auto_start(enabled):
    import winreg

    run_key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        if enabled:
            winreg.SetValueEx(
                run_key, "DefaultIsland", 0, winreg.REG_SZ, autostart_command()
            )
        else:
            try:
                winreg.DeleteValue(run_key, "DefaultIsland")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(run_key)
