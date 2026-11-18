"""Current weather from Open-Meteo (https://open-meteo.com), no account needed.

Only the city typed in the settings leaves the machine: its name once, to
find where it is, then its coordinates about every 20 minutes. With no city
set nothing is asked at all.
"""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "DefaultIsland/2.0 (+https://github.com/lourchik69/default-island)"
TIMEOUT_SEC = 8
REFRESH_SEC = 20 * 60
RETRY_SEC = 120
# Weather older than this is not shown any more (the network went away).
MAX_AGE_SEC = 3 * 3600
MAX_CITY = 60


@dataclass(frozen=True)
class Weather:
    city: str            # as the geocoder names it, in the interface language
    temperature: float   # °C
    feels_like: float | None
    code: int            # WMO weather code
    is_day: bool
    fetched: float       # time.time()


# WMO weather interpretation codes: (icon, description in Russian).
_CODES = {
    0: ("clear", "Ясно"),
    1: ("partly", "Малооблачно"),
    2: ("partly", "Переменная облачность"),
    3: ("cloud", "Пасмурно"),
    45: ("fog", "Туман"),
    48: ("fog", "Туман"),
    51: ("rain", "Морось"),
    53: ("rain", "Морось"),
    55: ("rain", "Морось"),
    56: ("rain", "Ледяная морось"),
    57: ("rain", "Ледяная морось"),
    61: ("rain", "Дождь"),
    63: ("rain", "Дождь"),
    65: ("rain", "Сильный дождь"),
    66: ("rain", "Ледяной дождь"),
    67: ("rain", "Ледяной дождь"),
    71: ("snow", "Снег"),
    73: ("snow", "Снег"),
    75: ("snow", "Сильный снег"),
    77: ("snow", "Снег"),
    80: ("rain", "Ливень"),
    81: ("rain", "Ливень"),
    82: ("rain", "Ливень"),
    85: ("snow", "Снегопад"),
    86: ("snow", "Снегопад"),
    95: ("storm", "Гроза"),
    96: ("storm", "Гроза с градом"),
    99: ("storm", "Гроза с градом"),
}

TRANSLATIONS = {
    "Ясно": ("Clear", "Ясно"),
    "Малооблачно": ("Mostly clear", "Малохмарно"),
    "Переменная облачность": ("Partly cloudy", "Мінлива хмарність"),
    "Пасмурно": ("Overcast", "Хмарно"),
    "Туман": ("Fog", "Туман"),
    "Морось": ("Drizzle", "Мряка"),
    "Ледяная морось": ("Freezing drizzle", "Крижана мряка"),
    "Дождь": ("Rain", "Дощ"),
    "Сильный дождь": ("Heavy rain", "Сильний дощ"),
    "Ледяной дождь": ("Freezing rain", "Крижаний дощ"),
    "Снег": ("Snow", "Сніг"),
    "Сильный снег": ("Heavy snow", "Сильний сніг"),
    "Ливень": ("Showers", "Злива"),
    "Снегопад": ("Snow showers", "Снігопад"),
    "Гроза": ("Thunderstorm", "Гроза"),
    "Гроза с градом": ("Thunderstorm with hail", "Гроза з градом"),
}


def describe(code):
    """(icon kind, Russian description) of a WMO weather code."""
    return _CODES.get(int(code), ("cloud", "Пасмурно"))


def format_temperature(value):
    """"+3°", "−2°" or "0°", rounded to whole degrees."""
    degrees = int(round(value))
    if degrees > 0:
        return f"+{degrees}°"
    if degrees < 0:
        return f"−{-degrees}°"
    return "0°"


def clean_city(text):
    return " ".join(str(text or "").split())[:MAX_CITY]


class WeatherProvider(threading.Thread):
    """Keeps the weather of one city fresh, off the GUI thread."""

    def __init__(self):
        super().__init__(daemon=True, name="weather")
        self._lock = threading.Lock()
        self._city = ""
        self._language = "ru"
        self._place = None  # (query, name, latitude, longitude)
        self._weather = None
        self._status = "off"  # off | loading | ok | not_found | error
        self._next = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()

    # ----- Public, called from the GUI thread ------------------------------

    def set_city(self, city, language="ru"):
        city = clean_city(city)
        with self._lock:
            if (city, language) == (self._city, self._language):
                return
            if city.casefold() != self._city.casefold():
                self._weather = None
            self._city, self._language = city, language
            self._place = None
            self._status = "loading" if city else "off"
            self._next = 0.0
        self._wake.set()

    def current(self):
        """The latest weather, or None when there is none worth showing."""
        with self._lock:
            weather = self._weather
        if weather is None or time.time() - weather.fetched > MAX_AGE_SEC:
            return None
        return weather

    def status(self):
        return self._status

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ----- Worker -------------------------------------------------------

    def run(self):
        while not self._stop.is_set():
            with self._lock:
                city, language, place, due = self._city, self._language, self._place, self._next
            if city and time.time() >= due:
                ok = self._refresh(city, language, place)
                with self._lock:
                    if city == self._city and language == self._language:
                        self._next = time.time() + (REFRESH_SEC if ok else RETRY_SEC)
            with self._lock:
                wait = max(1.0, self._next - time.time()) if self._city else None
            self._wake.wait(wait)
            self._wake.clear()

    def _fetch(self, url, params):
        request = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            return json.loads(response.read(512 * 1024).decode("utf-8"))

    def _publish(self, city, language, **changes):
        with self._lock:
            if city != self._city or language != self._language:
                return  # the city changed meanwhile
            for name, value in changes.items():
                setattr(self, name, value)

    def _refresh(self, city, language, place):
        """True when the service answered (even "no such city")."""
        try:
            if place is None:
                found = self._fetch(GEOCODE_API, {
                    "name": city, "count": 1, "language": language, "format": "json",
                })
                results = found.get("results") if isinstance(found, dict) else None
                if not results:
                    self._publish(city, language, _status="not_found", _weather=None)
                    return True
                first = results[0]
                place = (
                    city,
                    clean_city(first.get("name")) or city,
                    float(first["latitude"]),
                    float(first["longitude"]),
                )
                self._publish(city, language, _place=place)
            data = self._fetch(FORECAST_API, {
                "latitude": f"{place[2]:.4f}",
                "longitude": f"{place[3]:.4f}",
                "current": "temperature_2m,apparent_temperature,weather_code,is_day",
                "timezone": "auto",
            })
            current = data["current"]
            feels = current.get("apparent_temperature")
            weather = Weather(
                city=place[1],
                temperature=float(current["temperature_2m"]),
                feels_like=float(feels) if isinstance(feels, (int, float)) else None,
                code=int(current.get("weather_code") or 0),
                is_day=bool(current.get("is_day", 1)),
                fetched=time.time(),
            )
            self._publish(city, language, _weather=weather, _status="ok")
            return True
        except Exception:
            logging.info("Weather lookup failed", exc_info=True)
            self._publish(city, language, _status="error")
            return False


if __name__ == "__main__":
    import sys

    provider = WeatherProvider()
    provider.start()
    provider.set_city(sys.argv[1] if len(sys.argv) > 1 else "London", "ru")
    for _ in range(40):
        time.sleep(0.25)
        if provider.status() not in ("loading",):
            break
    print(provider.status(), provider.current())
