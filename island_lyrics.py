"""Time-synced lyrics from LRCLIB (https://lrclib.net), cached on disk.

Only the track title, the artist and the duration leave the machine. A track
without lyrics is not asked about again for a day, and network trouble backs
off for a few minutes.
"""
import bisect
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://lrclib.net/api"
USER_AGENT = "DefaultIsland/2.0 (+https://github.com/lourchik69/default-island)"
NETEASE_SEARCH = "https://music.163.com/api/search/get"
NETEASE_LYRIC = "https://music.163.com/api/song/lyric"
NETEASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://music.163.com/",
}
TRANSLATE_API = "https://translate.googleapis.com/translate_a/single"
MAX_TRANSLATE_CHARS = 4500
_CREDIT = re.compile(r"[㐀-鿿].*[:：]|[:：].*[㐀-鿿]")
TIMEOUT_SEC = 7
NEGATIVE_TTL_SEC = 24 * 3600
POSITIVE_TTL_SEC = 60 * 24 * 3600
BACKOFF_SEC = 300
MAX_CACHE_FILES = 400
MAX_MEMORY = 64


@dataclass(frozen=True)
class Progress:
    """Where the singer is inside the current line."""
    text: str
    word_start: int    # character span of the word being sung
    word_end: int
    word_share: float  # 0..1 of that word already sung


_WORD = re.compile(r"\S+")
# Typical singing pace, used when the file has no per-word times.
SEC_PER_CHAR = 0.075
LINE_LEAD_SEC = 0.35
MAX_LINE_SEC = 12.0


@dataclass(frozen=True)
class Lyrics:
    found: bool
    synced: bool
    lines: tuple = ()
    _starts: tuple = ()
    # Per line: start times of its words (enhanced LRC), or () if unknown.
    words: tuple = ()

    def index_at(self, position):
        """Index of the line sung at `position` seconds; -1 before the first."""
        return bisect.bisect_right(self._starts, position) - 1

    def progress_at(self, position):
        """Progress inside the line sung at `position`, or None."""
        if not self.synced:
            return None
        index = self.index_at(position)
        if index < 0 or index >= len(self.lines):
            return None
        start, text = self.lines[index]
        spans = [(m.start(), m.end()) for m in _WORD.finditer(text)]
        if not spans:
            return None
        following = self._starts[index + 1] if index + 1 < len(self._starts) else start + MAX_LINE_SEC
        times = self.words[index] if index < len(self.words) else ()
        if len(times) != len(spans):
            # Spread the line over a natural singing time, by word length,
            # never running into the next line.
            weights = [end - begin + 1 for begin, end in spans]
            chars = sum(weights)
            gap = following - start
            # A line is usually sung right up to the next one, so spread the
            # words over nearly the whole gap; only a long instrumental gap
            # is cut short, so the words are not stretched out.
            sung_for = max(LINE_LEAD_SEC + chars * SEC_PER_CHAR,
                           min(gap * 0.95, 1.0 + chars * SEC_PER_CHAR * 3.0))
            sung_for = max(0.4, min(sung_for, gap - 0.15))
            total, times, acc = float(sum(weights)), [], 0.0
            for weight in weights:
                times.append(start + sung_for * acc / total)
                acc += weight
            ends = times[1:] + [start + sung_for]
        else:
            ends = list(times[1:]) + [min(following, times[-1] + 0.6)]
        current = max(0, bisect.bisect_right(times, position) - 1)
        length = max(0.05, ends[current] - times[current])
        share = max(0.0, min(1.0, (position - times[current]) / length))
        begin, end = spans[current]
        return Progress(text, begin, end, share)


def make_lyrics(found, synced, lines, words=None):
    """lines: [(start, text)]; words: matching [(word start times)] or None."""
    order = sorted(range(len(lines)), key=lambda i: lines[i][0])
    lines = tuple(tuple(lines[i]) for i in order)
    words = tuple(tuple(words[i]) for i in order) if words and len(words) == len(order) else ()
    return Lyrics(found, synced, lines, tuple(start for start, _text in lines), words)


NOT_FOUND = make_lyrics(False, False, ())

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.,]\d{1,3})?)\]")
_WORD_TAG = re.compile(r"<\d{1,3}:\d{1,2}(?:[.,]\d{1,3})?>")
_WORD_TAG_SPLIT = re.compile(r"<(\d{1,3}):(\d{1,2}(?:[.,]\d{1,3})?)>")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)


def _seconds(minutes, seconds):
    return int(minutes) * 60 + float(seconds.replace(",", "."))


def _word_times(body, offset):
    """Start times of the words in an enhanced-LRC line, or () if not every
    word has its own <mm:ss.xx> tag."""
    parts = _WORD_TAG_SPLIT.split(body)
    times = []
    words_without_tag = len(parts[0].split())
    for index in range(1, len(parts), 3):
        stamp = _seconds(parts[index], parts[index + 1]) - offset
        count = len(parts[index + 2].split())
        if count > 1:
            return ()
        if count == 1:
            times.append(max(0.0, stamp))
    if words_without_tag or not times or times != sorted(times):
        return ()
    return tuple(times)


def parse_lrc_words(text):
    """([(seconds, line)], [word start times per line]) from LRC text."""
    offset = 0.0
    match = _OFFSET.search(text or "")
    if match:
        # A positive offset makes the lyrics appear sooner.
        offset = int(match.group(1)) / 1000.0
    lines, words = [], []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stamps = list(_TIME_TAG.finditer(raw))
        if not stamps:
            continue
        body = _TIME_TAG.sub("", raw)
        line = " ".join(_WORD_TAG.sub("", body).split())
        times = _word_times(body, offset) if len(stamps) == 1 else ()
        for stamp in stamps:
            lines.append((max(0.0, _seconds(stamp.group(1), stamp.group(2)) - offset), line))
            words.append(times)
    return lines, words


def parse_lrc(text):
    """[(seconds, line)] from LRC text; empty if it has no timestamps."""
    return parse_lrc_words(text)[0]


_NOISE = re.compile(
    r"[\(\[](?:official(?: music| lyric)?(?: video| audio)?|music video|lyric(?:s)? video|lyrics?|"
    r"audio|video|visuali[sz]er|hd|hq|4k(?: remaster(?:ed)?)?|remaster(?:ed)?(?: \d{4})?|\d{4} remaster(?:ed)?|"
    r"radio edit|explicit|clean|mv|m/v|official|премьера[^\)\]]*|клип[^\)\]]*|"
    r"official visualizer|live)[\)\]]",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring|при уч\.)\s+[^\)\]]*[\)\]]?", re.IGNORECASE)
_DASH_SUFFIX = re.compile(r"\s+-\s+(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?.*$|\s+-\s+radio edit$", re.IGNORECASE)


def _strip_symbols(text):
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("S", "C") or ch in "&'")


def clean_title(title, artist):
    """(title, artist) with video noise removed and "Artist - Title" split."""
    title = _strip_symbols(title or "")
    artist = _strip_symbols(artist or "")
    artist = re.sub(r"\s*-\s*topic$", "", artist, flags=re.IGNORECASE).strip()
    title = title.split(" | ")[0]
    for _ in range(3):
        title = _NOISE.sub("", title)
    title = _FEAT.sub("", title)
    title = _DASH_SUFFIX.sub("", title)
    if " - " in title:
        left, right = (part.strip() for part in title.split(" - ", 1))
        if not artist or _norm(artist) in _norm(left) or _norm(left) in _norm(artist):
            artist, title = artist or left, right
    title = " ".join(title.replace("\"", "").split()).strip(" -–—")
    artist = _FEAT.sub("", artist)
    artist = re.split(r"\s*(?:,|&| x | feat\.? | ft\.? )\s*", artist, maxsplit=1)[0]
    return title, " ".join(artist.split())


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "").casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _similar(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) * 0.5 + 0.45
    # Share of letter pairs in common.
    pairs_a = {a[i:i + 2] for i in range(len(a) - 1)}
    pairs_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not pairs_a or not pairs_b:
        return 0.0
    return 2 * len(pairs_a & pairs_b) / (len(pairs_a) + len(pairs_b))


def _from_record(record):
    synced, words = parse_lrc_words(record.get("syncedLyrics") or "")
    if synced:
        return make_lyrics(True, True, synced, words)
    plain = (record.get("plainLyrics") or "").strip()
    if plain:
        return make_lyrics(True, False, [(0.0, line.strip()) for line in plain.splitlines() if line.strip()])
    return NOT_FOUND


class LyricsProvider(threading.Thread):
    def __init__(self, cache_dir):
        super().__init__(daemon=True, name="lyrics")
        self.cache_dir = cache_dir
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._memory = {}
        self._wanted = None
        self._pending = set()
        self._backoff_until = 0.0
        self._stop = threading.Event()
        self._failures = 0

    # ----- Public, called from the GUI thread ------------------------------

    def request(self, title, artist, duration):
        key = self._key(title, artist)
        with self._lock:
            self._wanted = key
            if key in self._memory or key in self._pending:
                return
            self._pending.add(key)
        self._requests.put((key, title, artist, float(duration or 0.0)))

    def get(self, title, artist):
        with self._lock:
            return self._memory.get(self._key(title, artist))

    def stop(self):
        self._stop.set()
        self._requests.put(None)

    # ----- Worker -------------------------------------------------------

    @staticmethod
    def _key(title, artist):
        clean = clean_title(title, artist)
        return hashlib.sha1(f"{_norm(clean[0])}\n{_norm(clean[1])}".encode("utf-8")).hexdigest()[:20]

    def run(self):
        while not self._stop.is_set():
            item = self._requests.get()
            if item is None:
                break
            key, title, artist, duration = item
            try:
                with self._lock:
                    stale = key != self._wanted
                if stale:
                    continue  # the track changed again meanwhile
                lyrics = self._read_cache(key)
                if lyrics is None and time.time() >= self._backoff_until:
                    lyrics = self._lookup(title, artist, duration)
                    if lyrics is not None:
                        self._write_cache(key, lyrics)
                if lyrics is not None:
                    with self._lock:
                        self._memory[key] = lyrics
                        while len(self._memory) > MAX_MEMORY:
                            self._memory.pop(next(iter(self._memory)))
            except Exception:
                logging.exception("Lyrics lookup failed")
            finally:
                with self._lock:
                    self._pending.discard(key)

    def _fetch(self, path, params):
        url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                return json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def _lookup(self, title, artist, duration):
        """Lyrics, NOT_FOUND, or None when the service could not be asked.
        LRCLIB first; NetEase Cloud Music when LRCLIB has no synced text."""
        lyrics = self._lookup_lrclib(title, artist, duration)
        if lyrics is None or (lyrics.found and lyrics.synced):
            return lyrics
        try:
            other = self._lookup_netease(title, artist, duration)
        except Exception:
            logging.debug("NetEase lyrics lookup failed", exc_info=True)
            other = None
        return other if other is not None and other.found else lyrics

    def _lookup_netease(self, title, artist, duration):
        clean, singer = clean_title(title, artist)
        if not clean:
            return None
        body = urllib.parse.urlencode({"s": f"{singer} {clean}".strip(), "type": 1, "limit": 8}).encode()
        request = urllib.request.Request(NETEASE_SEARCH, data=body, headers=NETEASE_HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            found = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        songs = ((found or {}).get("result") or {}).get("songs") or []
        best, best_score = None, 0.0
        for song in songs:
            if not isinstance(song, dict) or not song.get("id"):
                continue
            score = _similar(clean, song.get("name") or "")
            artists = song.get("artists") or [{}]
            if singer:
                score = score * 0.65 + max(_similar(singer, a.get("name") or "") for a in artists) * 0.35
            length = (song.get("duration") or 0) / 1000.0
            if duration > 0 and length > 0:
                if abs(length - duration) > 8:
                    continue
                score += 0.05 if abs(length - duration) <= 2 else 0.0
            if score > best_score:
                best, best_score = song, score
        if best is None or best_score < 0.72:
            return None
        query = urllib.parse.urlencode({"id": best["id"], "lv": 1, "kv": 1, "tv": -1})
        request = urllib.request.Request(f"{NETEASE_LYRIC}?{query}", headers=NETEASE_HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        lines, words = parse_lrc_words(((data or {}).get("lrc") or {}).get("lyric") or "")
        # Credits in Chinese ("作词 : …") are not lyrics.
        kept = [i for i, (_start, text) in enumerate(lines) if text and not _CREDIT.search(text)]
        if len(kept) < 3:
            return None
        return make_lyrics(True, True, [lines[i] for i in kept], [words[i] for i in kept])

    def _lookup_lrclib(self, title, artist, duration):
        clean, singer = clean_title(title, artist)
        if not clean:
            return NOT_FOUND
        try:
            if singer:
                params = {"track_name": clean, "artist_name": singer}
                if duration > 0:
                    params["duration"] = round(duration)
                record = self._fetch("get", params)
                if isinstance(record, dict):
                    lyrics = _from_record(record)
                    if lyrics.found:
                        self._failures = 0
                        return lyrics
            query = f"{singer} {clean}".strip()
            results = self._fetch("search", {"q": query}) or []
            self._failures = 0
        except Exception:
            self._failures += 1
            self._backoff_until = time.time() + BACKOFF_SEC
            level = logging.WARNING if self._failures >= 3 else logging.DEBUG
            logging.log(level, "Could not reach the lyrics service", exc_info=True)
            return None
        best, best_score = None, 0.0
        for record in results if isinstance(results, list) else []:
            if not isinstance(record, dict):
                continue
            score = _similar(clean, record.get("trackName") or record.get("name") or "")
            if singer:
                score = score * 0.65 + _similar(singer, record.get("artistName") or "") * 0.35
            if duration > 0 and isinstance(record.get("duration"), (int, float)):
                gap = abs(record["duration"] - duration)
                if gap > 8:
                    continue  # a different recording or a different song
                score += 0.05 if gap <= 2 else 0.0
            if record.get("syncedLyrics"):
                score += 0.04
            if score > best_score:
                best, best_score = record, score
        if best is None or best_score < 0.72:
            return NOT_FOUND
        return _from_record(best)

    # ----- Disk cache ---------------------------------------------------

    def _path(self, key):
        return os.path.join(self.cache_dir, key + ".json")

    def _read_cache(self, key):
        try:
            with open(self._path(key), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            age = time.time() - float(data["saved"])
            ttl = POSITIVE_TTL_SEC if data["found"] else NEGATIVE_TTL_SEC
            if age > ttl:
                return None
            if not data["found"]:
                return NOT_FOUND
            lines = [(float(start), str(text)) for start, text in data["lines"]]
            words = [tuple(float(t) for t in times) for times in data.get("words") or ()]
            return make_lyrics(True, bool(data["synced"]), lines, words)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            return None  # a damaged file is looked up again

    def _write_cache(self, key, lyrics):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            data = {
                "saved": time.time(),
                "found": lyrics.found,
                "synced": lyrics.synced,
                "lines": [list(line) for line in lyrics.lines],
            }
            if any(lyrics.words):
                data["words"] = [list(times) for times in lyrics.words]
            temp = self._path(key) + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temp, self._path(key))
            self._trim_cache()
        except OSError:
            logging.debug("Could not cache lyrics", exc_info=True)

    def _trim_cache(self):
        files = [
            os.path.join(self.cache_dir, name)
            for name in os.listdir(self.cache_dir)
            if name.endswith(".json")
        ]
        if len(files) <= MAX_CACHE_FILES:
            return
        files.sort(key=os.path.getmtime)
        for path in files[: len(files) - MAX_CACHE_FILES]:
            try:
                os.remove(path)
            except OSError:
                pass


SAME_LANGUAGE = "same"


class Translator(threading.Thread):
    """Translates the lines of a song (Google Translate, no account), once
    per song and language; kept on disk next to the lyrics."""

    def __init__(self, cache_dir):
        super().__init__(daemon=True, name="translate")
        self.cache_dir = cache_dir
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._memory = {}  # key -> {original: translated} or SAME_LANGUAGE
        self._pending = set()
        self._backoff_until = 0.0
        self._stop = threading.Event()

    @staticmethod
    def _key(lines, target):
        text = "\n".join(lines)
        return "tr_" + target + "_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    def request(self, lines, target):
        lines = tuple(dict.fromkeys(line for line in lines if line.strip()))
        if not lines:
            return
        key = self._key(lines, target)
        with self._lock:
            if key in self._memory or key in self._pending:
                return
            self._pending.add(key)
        self._requests.put((key, lines, target))

    def get(self, lines, target):
        """{line: translation}, SAME_LANGUAGE, or None while not known."""
        lines = tuple(dict.fromkeys(line for line in lines if line.strip()))
        with self._lock:
            return self._memory.get(self._key(lines, target)) if lines else None

    def stop(self):
        self._stop.set()
        self._requests.put(None)

    def run(self):
        while not self._stop.is_set():
            item = self._requests.get()
            if item is None:
                break
            key, lines, target = item
            try:
                result = self._read(key)
                if result is None and time.time() >= self._backoff_until:
                    result = self._translate(lines, target)
                    if result is not None:
                        self._write(key, result)
                if result is not None:
                    with self._lock:
                        self._memory[key] = result
                        while len(self._memory) > MAX_MEMORY:
                            self._memory.pop(next(iter(self._memory)))
            except Exception:
                logging.exception("Lyrics translation failed")
            finally:
                with self._lock:
                    self._pending.discard(key)

    def _translate(self, lines, target):
        chunks, chunk, size = [], [], 0
        for line in lines:
            if chunk and size + len(line) + 1 > MAX_TRANSLATE_CHARS:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(line)
            size += len(line) + 1
        if chunk:
            chunks.append(chunk)
        result = {}
        for part in chunks:
            query = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": target, "dt": "t"})
            body = urllib.parse.urlencode({"q": "\n".join(part)}).encode()
            request = urllib.request.Request(
                f"{TRANSLATE_API}?{query}", data=body, headers={"User-Agent": "Mozilla/5.0"}
            )
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                    data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            except Exception:
                self._backoff_until = time.time() + BACKOFF_SEC
                logging.info("Could not reach the translator", exc_info=True)
                return None
            source = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
            if source.split("-")[0] == target:
                return SAME_LANGUAGE
            text = "".join(piece[0] for piece in data[0] if piece and isinstance(piece[0], str))
            translated = text.split("\n")
            if len(translated) != len(part):
                return None  # the lines did not come back one for one
            for original, line in zip(part, translated):
                line = line.strip()
                if line and _norm(line) != _norm(original):
                    result[original] = line
        return result

    def _path(self, key):
        return os.path.join(self.cache_dir, key + ".json")

    def _read(self, key):
        try:
            with open(self._path(key), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if time.time() - float(data["saved"]) > POSITIVE_TTL_SEC:
                return None
            if data.get("same"):
                return SAME_LANGUAGE
            return {str(k): str(v) for k, v in data["lines"].items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _write(self, key, result):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            data = {"saved": time.time()}
            if result == SAME_LANGUAGE:
                data["same"] = True
            else:
                data["lines"] = result
            temp = self._path(key) + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temp, self._path(key))
        except OSError:
            logging.debug("Could not cache a translation", exc_info=True)


if __name__ == "__main__":
    import sys
    import tempfile

    provider = LyricsProvider(os.path.join(tempfile.gettempdir(), "island-lyrics-demo"))
    provider.start()
    title, artist = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    provider.request(title, artist, duration)
    for _ in range(100):
        result = provider.get(title, artist)
        if result is not None:
            break
        time.sleep(0.1)
    print(result.found if result else None, result.synced if result else None, len(result.lines) if result else 0)
    for line in (result.lines[:3] if result else ()):
        print(line)
    provider.stop()
