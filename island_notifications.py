"""Watches Windows toast notifications so the island can show them.

Uses UserNotificationListener. For an unpackaged app (python.exe or the
PyInstaller exe) the change event is not available ("element not found"),
so the Action Center is polled twice a second and new ids are reported.
Reading notifications needs the user's consent: Windows settings → Privacy &
security → Notifications → "Let apps access your notifications".
"""
import asyncio
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass

try:
    from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
    from winrt.windows.ui.notifications.management import UserNotificationListener
    HAS_LISTENER = True
except ImportError:
    HAS_LISTENER = False

POLL_SEC = 0.5
MAX_QUEUE = 20
MAX_TITLE = 120
MAX_BODY = 300
# UserNotificationListenerAccessStatus
ACCESS_ALLOWED = 1
ACCESS_DENIED = 2


@dataclass(frozen=True)
class Toast:
    app: str
    title: str
    body: str
    received: float
    toast_id: int = 0  # Windows' id of the notification, for present()


def _clean(text, limit):
    text = "".join(ch if ch.isprintable() or ch == "\n" else " " for ch in str(text or ""))
    return " ".join(text.split())[:limit] if "\n" not in text else "\n".join(
        " ".join(line.split()) for line in text.splitlines() if line.strip()
    )[:limit]


def toasts_enabled():
    """False when notifications are switched off for all of Windows
    (Settings → System → Notifications); then no app shows any toast."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\PushNotifications") as key:
            return winreg.QueryValueEx(key, "ToastEnabled")[0] != 0
    except OSError:
        return True  # never switched: on


def _own_names():
    """Display names this app's own toasts would carry."""
    exe = os.path.splitext(os.path.basename(sys.executable))[0].casefold()
    return {"defaultisland", "default island"} | ({exe} if exe != "python" else set())


class NotificationWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="notifications")
        self._lock = threading.Lock()
        self._queue = []
        self._status = "starting" if HAS_LISTENER else "unavailable"
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._known = None  # ids seen so far; None until the first look
        self._last_error = 0.0
        self._own = _own_names()

    def status(self):
        return self._status

    def set_enabled(self, enabled):
        if enabled and not self._enabled.is_set():
            self._known = None  # what is there already is not news
            self._enabled.set()
        elif not enabled and self._enabled.is_set():
            self._enabled.clear()
            with self._lock:
                self._queue.clear()

    def poll(self):
        with self._lock:
            items, self._queue = self._queue, []
        return items

    def present(self, toast_id):
        """False once the notification is gone: the app removed it (a call
        was answered or ended) or the user dismissed it."""
        known = self._known
        return known is None or not self._enabled.is_set() or toast_id in known

    def stop(self):
        self._stop.set()
        self._enabled.set()

    def run(self):
        if not HAS_LISTENER:
            return
        loop = asyncio.new_event_loop()
        try:
            listener = UserNotificationListener.current
            access = loop.run_until_complete(listener.request_access_async())
            self._status = "ok" if int(access) == ACCESS_ALLOWED else "denied"
            while not self._stop.is_set():
                self._enabled.wait()
                if self._stop.is_set():
                    break
                try:
                    loop.run_until_complete(asyncio.wait_for(self._look(listener), 5.0))
                    self._status = "ok"
                except Exception as error:
                    winerror = getattr(error, "winerror", 0) & 0xFFFFFFFF
                    # E_ACCESSDENIED: the user switched the consent off.
                    if winerror == 0x80070005:
                        self._status = "denied"
                    if time.monotonic() - self._last_error > 300:
                        self._last_error = time.monotonic()
                        logging.warning("Could not read Windows notifications", exc_info=True)
                    self._stop.wait(5.0)
                self._stop.wait(POLL_SEC)
        except Exception:
            self._status = "unavailable"
            logging.exception("The notification watcher stopped")
        finally:
            loop.close()

    async def _look(self, listener):
        items = list(await listener.get_notifications_async(NotificationKinds.TOAST))
        ids = {item.id for item in items}
        if self._known is None:
            self._known = ids
            return
        fresh = [item for item in items if item.id not in self._known]
        self._known = ids
        for item in sorted(fresh, key=lambda n: n.creation_time.timestamp()):
            toast = self._read(item)
            if toast is None:
                continue
            with self._lock:
                # An app updating the same message must not stack it up.
                self._queue = [t for t in self._queue if (t.app, t.title, t.body) != (toast.app, toast.title, toast.body)]
                self._queue.append(toast)
                del self._queue[:-MAX_QUEUE]

    def _read(self, item):
        try:
            app = item.app_info.display_info.display_name or ""
        except Exception:
            app = ""
        if app.casefold() in self._own:
            return None
        try:
            binding = item.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
            texts = [element.text or "" for element in binding.get_text_elements()] if binding else []
        except Exception:
            texts = []
        texts = [text for text in texts if text.strip()]
        if not texts and not app:
            return None
        title = _clean(texts[0] if texts else app, MAX_TITLE)
        body = _clean("\n".join(texts[1:]), MAX_BODY)
        return Toast(app=_clean(app, 60), title=title, body=body, received=time.time(),
                     toast_id=int(item.id))


if __name__ == "__main__":
    watcher = NotificationWatcher()
    watcher.start()
    watcher.set_enabled(True)
    show_text = "--text" in sys.argv
    end = time.time() + 60
    while time.time() < end:
        for toast in watcher.poll():
            if show_text:
                print(toast)
            else:
                print(toast.app, len(toast.title), len(toast.body))
        time.sleep(0.25)
    print("status", watcher.status())
    watcher.stop()
