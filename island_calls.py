"""Phone and voice calls for the island.

An incoming call is recognised by the toast the calling app shows (Phone
Link, WhatsApp, Viber, Skype, Teams…). A call in progress is recognised by a
known calling app capturing from the microphone (Discord, Telegram, Teams,
Zoom…). "Answer" and "Decline" press the buttons of that toast through UI
Automation, so they work with any app whose toast has such buttons.
"""
import ctypes
import ctypes.wintypes
import logging
import os
import queue
import re
import threading
import time

# Process / package / toast names of calling apps (case-folded, no ".exe").
_APPS = {
    "discord": "Discord", "discordptb": "Discord", "discordcanary": "Discord",
    "telegram": "Telegram", "telegram desktop": "Telegram", "ayugram": "AyuGram",
    "kotatogram": "Telegram", "64gram": "Telegram",
    "whatsapp": "WhatsApp", "whatsappdesktop": "WhatsApp", "whatsapp.root": "WhatsApp",
    "teams": "Teams", "ms-teams": "Teams", "msteams": "Teams", "microsoft teams": "Teams",
    "microsoft teams (work or school)": "Teams", "microsoft teams classic": "Teams",
    "zoom": "Zoom", "zoom workplace": "Zoom",
    "skype": "Skype", "skypeapp": "Skype",
    "viber": "Viber",
    "signal": "Signal",
    "slack": "Slack",
    "element": "Element",
    "messenger": "Messenger",
    "ts3client_win64": "TeamSpeak", "ts3client_win32": "TeamSpeak", "teamspeak": "TeamSpeak",
    "yourphone": "Phone Link", "phoneexperiencehost": "Phone Link", "phone link": "Phone Link",
    "связь с телефоном": "Phone Link", "зв'язок із телефоном": "Phone Link",
    "зв’язок із телефоном": "Phone Link",
}
PHONE_LINK = "Phone Link"

# An incoming call in a toast (Russian, English, Ukrainian): a line that
# begins with one of these...
_CALL_STARTS = (
    "входящий звонок", "входящий вызов", "входящий видеозвонок", "входящий аудиозвонок",
    "входящий голосовой", "входящий видеовызов", "входящий групповой",
    "incoming call", "incoming voice call", "incoming video call", "incoming audio call",
    "incoming group call", "вхідний дзвінок", "вхідний виклик", "вхідний відеодзвінок",
    "вхідний аудіодзвінок", "вхідний голосовий",
)
# ...or ends with one of these ("Мама звонит", "Anna is calling you"). A chat
# message that only mentions a call matches neither.
_CALL_ENDS = (
    "звонит вам", "вызывает вас", "is calling you", "calling you", "дзвонить вам",
    "телефонує вам",
)
# ...and of a call that is already over.
_FINISHED = (
    "пропущен", "missed", "пропущений", "не отвечен", "завершен", "завершён", "ended",
    "declined", "отклонен", "отклонён", "відхилено", "завершено",
)
_ACCEPT = (
    "принять", "ответить", "взять трубку", "accept", "answer", "pick up",
    "прийняти", "відповісти",
)
_DECLINE = (
    "отклонить", "сбросить", "отказаться", "decline", "reject", "hang up", "ignore",
    "игнорировать", "відхилити", "ігнорувати", "скинути",
)
# Toast buttons that only look like answers.
_NOT_ANSWER = ("сообщ", "message", "текст", "text", "повідом")
# Toasts live in a shell CoreWindow ("New notification").
TOAST_WINDOW_CLASSES = ("Windows.UI.Core.CoreWindow",)


def _normal(name):
    name = " ".join(str(name or "").split()).casefold()
    return name[:-4] if name.endswith(".exe") else name


def call_app(name):
    """Display name of a calling app from a process, package or toast app
    name, or None when it is not one."""
    return _APPS.get(_normal(name))


def _call_line(line):
    """None when the line does not announce a call; else the caller named
    in it ("" when it names nobody)."""
    original = re.sub(r"^\W+", "", line).strip()
    folded = original.casefold()
    if folded.startswith(_CALL_STARTS):
        return ""
    trimmed = folded.rstrip(" .!…")
    for phrase in _CALL_ENDS:
        if trimmed.endswith(phrase) and (len(trimmed) == len(phrase) or trimmed[-len(phrase) - 1] == " "):
            return original[: len(trimmed) - len(phrase)].strip(" ,:-—")
    return None


def incoming_call(toast):
    """(caller, app) when the toast announces an incoming call, else None."""
    lines = [line.strip() for line in (toast.title, *toast.body.splitlines()) if line and line.strip()]
    named = [_call_line(line) for line in lines]
    if all(name is None for name in named):
        return None
    if any(word in "\n".join(lines).casefold() for word in _FINISHED):
        return None
    app = call_app(toast.app) or toast.app.strip()
    # The caller is a line that is not the "incoming call" one, or the name
    # in front of "is calling".
    caller = next(
        (
            line for line, name in zip(lines, named)
            if name is None and not call_app(line) and line.casefold() != toast.app.casefold()
        ),
        "",
    ) or next((name for name in named if name), "")
    return caller, app


def _matches(label, words):
    label = label.strip().casefold()
    if not label or any(part in label for part in _NOT_ANSWER):
        return False
    return any(label == word or label.startswith(word + " ") for word in words)


class ToastButtons(threading.Thread):
    """Presses "Answer" or "Decline" on the call toast on screen.

    UI Automation runs on this thread, which keeps one COM apartment for its
    whole life; the island only queues requests and collects results.
    """

    def __init__(self):
        super().__init__(daemon=True, name="call-buttons")
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._results = []

    def press(self, kind, hint=""):
        """Queue a press: kind is "accept" or "decline"; `hint` is text of
        the toast (the caller) that tells it from other toasts."""
        self._requests.put((kind, hint))

    def results(self):
        """(kind, pressed) of the presses done since the last call."""
        with self._lock:
            done, self._results = self._results, []
        return done

    def stop(self):
        self._requests.put(None)

    def run(self):
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            owns_com = True
        except OSError:
            # COM is already set up on this thread (comtypes does that when
            # it is first imported here); that apartment works as well.
            owns_com = False
        uia = module = None
        try:
            while True:
                item = self._requests.get()
                if item is None:
                    break
                kind, hint = item
                pressed = False
                try:
                    if uia is None:
                        module = self._uia_module()
                        uia = comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
                    pressed = self._press(uia, module, kind, hint)
                except Exception:
                    logging.exception("Could not press the call button")
                with self._lock:
                    self._results.append((kind, pressed))
        finally:
            uia = module = None
            if owns_com:
                comtypes.CoUninitialize()

    @staticmethod
    def _uia_module():
        """The UI Automation wrappers: bundled with the exe, or generated
        from the type library on first use."""
        try:
            from comtypes.gen import UIAutomationClient
            return UIAutomationClient
        except ImportError:
            import comtypes.client
            return comtypes.client.GetModule("UIAutomationCore.dll")

    @staticmethod
    def _press(uia, module, kind, hint):
        words = _ACCEPT if kind == "accept" else _DECLINE
        hint = hint.casefold().strip()
        root = uia.GetRootElement()
        windows = root.FindAll(module.TreeScope_Children, uia.CreateTrueCondition())
        is_button = uia.CreatePropertyCondition(module.UIA_ControlTypePropertyId, module.UIA_ButtonControlTypeId)
        candidates = []
        for index in range(windows.Length):
            window = windows.GetElement(index)
            if window.CurrentClassName not in TOAST_WINDOW_CLASSES:
                continue
            buttons = window.FindAll(module.TreeScope_Descendants, is_button)
            for number in range(buttons.Length):
                button = buttons.GetElement(number)
                if not _matches(button.CurrentName or "", words):
                    continue
                score = 0
                if hint:
                    texts = window.FindAll(module.TreeScope_Descendants, uia.CreateTrueCondition())
                    for text_index in range(min(texts.Length, 80)):
                        if hint in (texts.GetElement(text_index).CurrentName or "").casefold():
                            score = 1
                            break
                candidates.append((score, button))
        if not candidates:
            return False
        candidates.sort(key=lambda pair: -pair[0])
        pattern = candidates[0][1].GetCurrentPattern(module.UIA_InvokePatternId)
        pattern.QueryInterface(module.IUIAutomationInvokePattern).Invoke()
        return True


# --------------------------------------------------------------------------
# Bringing the calling app to the front
# --------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, ctypes.wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
_user32.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
_user32.GetWindow.restype = ctypes.wintypes.HWND
_user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
_user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_user32.SwitchToThisWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.BOOL]
_user32.SwitchToThisWindow.restype = None
_GW_OWNER = 4
_SW_RESTORE = 9


def _process_names():
    try:
        import psutil
    except ImportError:
        return {}
    names = {}
    for process in psutil.process_iter(["pid", "name"]):
        name = process.info.get("name") or ""
        names[process.info["pid"]] = os.path.splitext(name)[0]
    return names


def focus_app(app):
    """Bring a window of the calling app (display name) to the front.
    Returns False when no window of it was found."""
    names = _process_names()
    found = []

    def visit(hwnd, _param):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
            return True
        if _user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if call_app(names.get(pid.value, "")) == app:
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    if not found:
        return False
    hwnd = found[0]
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    _user32.SwitchToThisWindow(hwnd, True)
    return True


if __name__ == "__main__":
    import sys

    buttons = ToastButtons()
    buttons.start()
    buttons.press(sys.argv[1] if len(sys.argv) > 1 else "accept", sys.argv[2] if len(sys.argv) > 2 else "")
    for _ in range(40):
        time.sleep(0.25)
        done = buttons.results()
        if done:
            print(done)
            break
    buttons.stop()
