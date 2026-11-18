"""The settings dialog of Default Island, its reminder dialog and their controls.

Both dialogs are frameless and translucent. AnimatedDialog paints their dark
surface and its shadow itself and runs the opening and closing animation.
The island shows them with show(), never exec(). Closing always ends in
done(), so the island learns about it through finished(int).
"""
import logging
import math
import time
from datetime import date, datetime, timedelta

from PyQt6.QtCore import (
    QDateTime, QEasingCurve, QEvent, QLocale, QPoint, QPointF, QRect, QRectF,
    QSize, Qt, QTimer, QUrl, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QDesktopServices, QFont, QFontMetricsF,
    QIntValidator, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
try:
    from island_notifications import toasts_enabled
except Exception:  # an optional module must not stop the settings window
    toasts_enabled = None
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDateTimeEdit,
    QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QListView, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)
from island_core import (
    ACCENT_LABELS, ACCENT_PRESETS, APP_VERSION, DEFAULT_ACCENT, FPS_PRESETS,
    FPS_RANGE, LYRICS_OFFSET_LIMIT, MAX_CITY_LENGTH, MAX_REMINDER_TEXT, MAX_REMINDERS,
    QUICK_BUTTONS, SCALE_RANGE,
    THEMES, VOLUME_TARGETS, load_reminders, make_look, mix_colors,
    new_reminder, next_daily_time, paint_island_surface, paint_soft_shadow,
    parse_hotkey, round_pen, rounded_rect_path, sanitize_config, save_config,
    save_reminders, set_auto_start, ui_font, with_alpha,
)
from island_i18n import LANGUAGES, language, register, tr

register({
    # Without music, weather and calls.
    "Без музыки": ("Without music", "Без музики"),
    "Когда ничего не играет, остров показывает": (
        "When nothing plays, the island shows", "Коли нічого не грає, острів показує",
    ),
    "Часы": ("Clock", "Годинник"),
    "Погода": ("Weather", "Погода"),
    "Время и дату; в развёрнутом острове ещё погоду и загрузку компьютера": (
        "The time and date; the open island adds the weather and the computer's load",
        "Час і дату; у розгорнутому острові ще погоду й навантаження комп'ютера",
    ),
    "Температуру и погоду в вашем городе": (
        "The temperature and the weather in your city", "Температуру й погоду у вашому місті",
    ),
    "Укажите город ниже — пока его нет, остров показывает часы": (
        "Enter a city below — until then the island shows the clock",
        "Вкажіть місто нижче — поки його немає, острів показує годинник",
    ),
    "Загрузку процессора и памяти": ("Processor and memory load", "Навантаження процесора й пам'яті"),
    "Маленькую пустую капсулу; наведите мышь, чтобы увидеть время": (
        "A small empty capsule; hover over it to see the time",
        "Маленьку порожню капсулу; наведіть мишу, щоб побачити час",
    ),
    "Город для погоды": ("City for the weather", "Місто для погоди"),
    "Например, Киев": ("For example, London", "Наприклад, Київ"),
    "Пусто — без погоды. Чтобы узнать погоду, название города отправляется на open-meteo.com": (
        "Empty — no weather. To get the weather, the city name is sent to open-meteo.com",
        "Порожньо — без погоди. Щоб дізнатися погоду, назва міста надсилається на open-meteo.com",
    ),
    "Звонки": ("Calls", "Дзвінки"),
    "Перевод текста": ("Lyrics translation", "Переклад тексту"),
    "Перевод поющейся строки под ней в развёрнутом острове. Строки песни отправляются в Google Переводчик": (
        "The sung line translated under it in the open island. The lines are sent to Google Translate",
        "Переклад рядка, що співається, під ним у розгорнутому острові. Рядки пісні надсилаються в Google Перекладач",
    ),
    "Чтобы найти текст, название трека и имя исполнителя отправляются на lrclib.net, а если там нет — на music.163.com": (
        "To find the lyrics, the track title and artist are sent to lrclib.net, and to music.163.com if it has none",
        "Щоб знайти текст, назва треку й ім'я виконавця надсилаються на lrclib.net, а якщо там немає — на music.163.com",
    ),
    "Оранжевая — микрофон, зелёная — камера, как на iPhone": (
        "Orange for the microphone, green for the camera, as on an iPhone",
        "Помаранчева — мікрофон, зелена — камера, як на iPhone",
    ),
    "В острове": ("In the island", "В острові"),
    "Рядом с островом": ("Beside the island", "Поруч з островом"),
    "Где показывать точки": ("Where the dots show", "Де показувати точки"),
    "Запись экрана": ("Screen recording", "Запис екрана"),
    "Красная точка с таймером, пока экран записывают или показывают: OBS, Xbox Game Bar, «Ножницы», показ экрана в Discord или браузере": (
        "A red dot with a timer while the screen is recorded or shared: OBS, Xbox Game Bar, Snipping Tool, screen sharing in Discord or a browser",
        "Червона точка з таймером, поки екран записують або показують: OBS, Xbox Game Bar, «Ножиці», показ екрана в Discord чи браузері",
    ),
    "Громкость и яркость": ("Volume and brightness", "Гучність і яскравість"),
    "Когда громкость или яркость меняется клавишами, остров показывает полоску": (
        "When the volume or brightness changes with the keys, the island shows a bar",
        "Коли гучність або яскравість змінюється клавішами, острів показує смужку",
    ),
    "Буфер обмена": ("Clipboard", "Буфер обміну"),
    "Остров показывает, что скопировано; кнопка «Буфер обмена» в разделе «Кнопки» хранит последние 10 копий. Пароли из менеджеров паролей не показываются": (
        "The island shows what was copied; the Clipboard button (Buttons section) keeps the last 10 copies. Passwords from password managers are not shown",
        "Острів показує, що скопійовано; кнопка «Буфер обміну» в розділі «Кнопки» зберігає останні 10 копій. Паролі з менеджерів паролів не показуються",
    ),
    "Сейчас уведомления Windows выключены, поэтому остров не узнает о входящем звонке. Включите их в Параметрах Windows → Система → Уведомления": (
        "Windows notifications are off now, so the island cannot learn about an incoming call. Turn them on in Windows Settings → System → Notifications",
        "Зараз сповіщення Windows вимкнено, тому острів не дізнається про вхідний дзвінок. Увімкніть їх у Параметрах Windows → Система → Сповіщення",
    ),
    "Открыть настройки уведомлений": ("Open notification settings", "Відкрити налаштування сповіщень"),
    "В списке запущенные программы. Громкость меняется, когда программа воспроизводит звук; нет нужной — запустите её и откройте настройки снова": (
        "The list shows running programs. The volume changes while the program plays sound; missing one? Start it and open the settings again",
        "У списку запущені програми. Гучність змінюється, коли програма відтворює звук; немає потрібної — запустіть її й відкрийте налаштування знову",
    ),
    "Кнопки": ("Buttons", "Кнопки"),
    "Какие кнопки есть на острове, или только музыка": (
        "Which buttons the island has, or music only", "Які кнопки є на острові, або тільки музика",
    ),
    "Режим": ("Mode", "Режим"),
    "Обновлять автоматически": ("Update automatically", "Оновлювати автоматично"),
    "Новая версия скачивается с GitHub и ставится сама, островок перезапускается": (
        "A new version is downloaded from GitHub and installs by itself; the island restarts",
        "Нова версія завантажується з GitHub і встановлюється сама, острів перезапускається",
    ),
    "Не беспокоить": ("Do not disturb", "Не турбувати"),
    "Только музыка": ("Music only", "Тільки музика"),
    "Остров только для музыки: без таймера, кнопок, полки файлов, индикаторов и уведомлений. Звонки и напоминания остаются": (
        "An island just for music: no timer, buttons, file shelf, indicators or notifications. Calls and reminders stay",
        "Острів лише для музики: без таймера, кнопок, полиці файлів, індикаторів і сповіщень. Дзвінки й нагадування лишаються",
    ),
    "Кнопки плеера": ("Player buttons", "Кнопки плеєра"),
    "Без неё настройки открываются из значка в трее": (
        "Without it the settings open from the tray icon", "Без неї налаштування відкриваються зі значка в треї",
    ),
    "Колокольчик: прячет уведомления": ("The bell: hides notifications", "Дзвіночок: ховає сповіщення"),
    "Колёсико мыши над островом всё равно меняет громкость": (
        "The mouse wheel over the island still changes the volume",
        "Коліщатко миші над островом однаково змінює гучність",
    ),
    "Лайк": ("Like", "Лайк"),
    "Сердечко на SoundCloud и других сайтах с расширением": (
        "The heart on SoundCloud and other sites with the extension",
        "Сердечко на SoundCloud та інших сайтах із розширенням",
    ),
    "Последние скопированные тексты и картинки; нажмите, чтобы скопировать снова": (
        "The latest copied texts and images; click one to copy it again",
        "Останні скопійовані тексти й зображення; натисніть, щоб скопіювати знову",
    ),
    "Входящий звонок с кнопками «Принять» и «Отклонить» и таймер разговора в Discord, Telegram, WhatsApp, Teams, Zoom и других": (
        "An incoming call with Answer and Decline buttons, and a call timer in Discord, Telegram, WhatsApp, Teams, Zoom and others",
        "Вхідний дзвінок із кнопками «Прийняти» й «Відхилити» та таймер розмови в Discord, Telegram, WhatsApp, Teams, Zoom та інших",
    ),
    "Входящие звонки видны, когда в Windows включены уведомления и есть доступ к ним": (
        "Incoming calls show when Windows notifications are on and the island may read them",
        "Вхідні дзвінки видно, коли у Windows увімкнено сповіщення й є доступ до них",
    ),
    # Window, sections and footer.
    "Default Island · Настройки": ("Default Island · Settings", "Default Island · Налаштування"),
    "Разделы настроек": ("Settings sections", "Розділи налаштувань"),
    "Закрыть": ("Close", "Закрити"),
    "Сохранить": ("Save", "Зберегти"),
    "Отмена": ("Cancel", "Скасувати"),
    "Изменения вступят в силу после сохранения": (
        "Changes take effect when you save",
        "Зміни наберуть чинності після збереження",
    ),
    "Напоминания сохраняются сразу, без кнопки «Сохранить»": (
        "Reminders are saved right away, no need to press Save",
        "Нагадування зберігаються одразу, без кнопки «Зберегти»",
    ),
    "Это сочетание уже занято другой программой": (
        "Another program already uses this shortcut",
        "Це сполучення вже зайняте іншою програмою",
    ),
    "Не удалось сохранить: {error}": ("Couldn't save: {error}", "Не вдалося зберегти: {error}"),
    "Внешний вид": ("Appearance", "Вигляд"),
    "Тема, цвет и то, что показывает остров": (
        "Theme, colour and what the island shows",
        "Тема, колір і те, що показує острів",
    ),
    "Размер и положение": ("Size & position", "Розмір і положення"),
    "Насколько крупный остров и где он живёт": (
        "How big the island is and where it lives",
        "Наскільки великий острів і де він живе",
    ),
    "Музыка": ("Music", "Музика"),
    "Текст песен, визуализатор и громкость": (
        "Lyrics, visualizer and volume",
        "Тексти пісень, візуалізатор і гучність",
    ),
    "Индикаторы": ("Indicators", "Індикатори"),
    "Что остров показывает, кроме музыки": (
        "What the island shows besides music",
        "Що острів показує, крім музики",
    ),
    "Быстрые кнопки": ("Quick buttons", "Швидкі кнопки"),
    "Кнопки в развёрнутом острове": (
        "Buttons in the expanded island",
        "Кнопки в розгорнутому острові",
    ),
    "Напоминания": ("Reminders", "Нагадування"),
    "Остров напомнит в нужное время": (
        "The island reminds you at the right time",
        "Острів нагадає в потрібний час",
    ),
    "Система": ("System", "Система"),
    "Язык, горячая клавиша, запуск и обновления": (
        "Language, hotkey, startup and updates",
        "Мова, гаряча клавіша, запуск і оновлення",
    ),
    # Appearance.
    "Зоны острова": ("Island zones", "Зони острова"),
    "Нажмите на зону, чтобы выбрать, что в ней показывать": (
        "Click a zone to choose what it shows",
        "Натисніть на зону, щоб вибрати, що в ній показувати",
    ),
    "Зоны острова: слева, в центре, справа": (
        "Island zones: left, centre, right",
        "Зони острова: ліворуч, у центрі, праворуч",
    ),
    "Обложка": ("Artwork", "Обкладинка"),
    "Пусто": ("Nothing", "Порожньо"),
    "Трек и текст": ("Track & lyrics", "Трек і текст"),
    "Пинг": ("Ping", "Пінг"),
    "CPU и RAM": ("CPU & RAM", "CPU і RAM"),
    "Видеокарта": ("GPU", "Відеокарта"),
    "Время": ("Clock", "Час"),
    "Визуализатор": ("Visualizer", "Візуалізатор"),
    "Повтор": ("Repeat", "Повтор"),
    "Тема": ("Theme", "Тема"),
    "Обычная": ("Classic", "Звичайна"),
    "Liquid Glass · светлая": ("Liquid Glass · light", "Liquid Glass · світла"),
    "Акцентный цвет": ("Accent colour", "Акцентний колір"),
    "Обложка на фоне": ("Artwork backdrop", "Обкладинка на тлі"),
    "Размытая обложка трека заполняет развёрнутый остров": (
        "The blurred artwork fills the expanded island",
        "Розмита обкладинка треку заповнює розгорнутий острів",
    ),
    "Стекло": ("Glass", "Скло"),
    "Плотность стекла": ("Glass density", "Щільність скла"),
    "Насколько стекло скрывает то, что под ним": (
        "How much the glass hides what is behind it",
        "Наскільки скло приховує те, що під ним",
    ),
    "Оттенок": ("Tint", "Відтінок"),
    "Насколько акцентный цвет окрашивает стекло": (
        "How strongly the accent colours the glass",
        "Наскільки акцентний колір фарбує скло",
    ),
    "Блик": ("Glint", "Відблиск"),
    "По стеклу пробегает полоска света при наведении и смене трека": (
        "A streak of light runs across the glass on hover and when the track changes",
        "Склом пробігає смужка світла під час наведення і зміни треку",
    ),
    "Настройки стекла работают только в темах Liquid Glass": (
        "Glass settings only apply to the Liquid Glass themes",
        "Налаштування скла діють лише в темах Liquid Glass",
    ),
    # Size and position.
    "Размер": ("Size", "Розмір"),
    "Свёрнутый остров": ("Collapsed island", "Згорнутий острів"),
    "Развёрнутый остров": ("Expanded island", "Розгорнутий острів"),
    "{value} %": ("{value}%", "{value} %"),
    "Название трека в свёрнутом острове": (
        "Track name in the collapsed island",
        "Назва треку в згорнутому острові",
    ),
    "Целиком": ("Full", "Повністю"),
    "Коротко": ("Short", "Коротко"),
    "Остров растягивается, чтобы вместить название трека": (
        "The island stretches to fit the track name",
        "Острів розтягується, щоб умістити назву треку",
    ),
    "Остров не меняет размер, длинное название заканчивается «…»": (
        "The island keeps its size; a long name ends with “…”",
        "Острів не змінює розмір, довга назва закінчується «…»",
    ),
    "Положение": ("Position", "Положення"),
    "Монитор": ("Monitor", "Монітор"),
    "Основной монитор": ("Primary monitor", "Основний монітор"),
    "{name} · не подключён": ("{name} · not connected", "{name} · не під'єднано"),
    "На другом мониторе остров встанет сверху по центру": (
        "On another monitor the island starts at the top centre",
        "На іншому моніторі острів стане зверху по центру",
    ),
    "Сменить положение": ("Move the island", "Змінити положення"),
    "Вернуть в центр": ("Back to centre", "Повернути в центр"),
    "Перетащите остров в любое место: он прилипает к центру и углам, а внизу экрана раскрывается вверх": (
        "Drag the island anywhere: it snaps to the centre and the corners, and at the bottom of the screen it opens upwards",
        "Перетягніть острів будь-куди: він прилипає до центру й кутів, а внизу екрана розкривається вгору",
    ),
    # Music.
    "Текст песен": ("Lyrics", "Тексти пісень"),
    "Синхронный текст песен": ("Synced lyrics", "Синхронний текст пісень"),
    "Подсветка слов": ("Word highlighting", "Підсвічування слів"),
    "Сдвиг текста": ("Lyrics offset", "Зсув тексту"),
    "без сдвига": ("no offset", "без зсуву"),
    "{value} с": ("{value} s", "{value} с"),
    "Если слова подсвечиваются раньше голоса, двигайте вправо; если позже — влево": (
        "If words light up before the voice, move right; if after it, move left",
        "Якщо слова підсвічуються раніше за голос, рухайте вправо; якщо пізніше — вліво",
    ),
    "Спетые слова заливаются цветом песни, как в караоке": (
        "Sung words fill with the song's colour, karaoke style",
        "Проспівані слова заливаються кольором пісні, як у караоке",
    ),
    "Чтобы найти текст, название трека и имя исполнителя отправляются на сайт lrclib.net": (
        "To find the lyrics, the track title and the artist are sent to lrclib.net",
        "Щоб знайти текст, назва треку та ім'я виконавця надсилаються на сайт lrclib.net",
    ),
    "Анимация": ("Animation", "Анімація"),
    "Настоящий визуализатор": ("Live visualizer", "Справжній візуалізатор"),
    "Полоски двигаются под звук, который сейчас играет": (
        "The bars move to the sound that is actually playing",
        "Смужки рухаються під звук, який зараз грає",
    ),
    "Анимация смены трека": ("Track change animation", "Анімація зміни треку"),
    "Название и обложка плавно сменяются при переключении трека": (
        "The title and artwork change smoothly when the track switches",
        "Назва й обкладинка плавно змінюються під час перемикання треку",
    ),
    "Громкость": ("Volume", "Гучність"),
    "Ползунок громкости управляет": ("The volume slider controls", "Повзунок гучності керує"),
    "Вся система (Windows)": ("The whole system (Windows)", "Уся система (Windows)"),
    # Indicators.
    "На острове": ("On the island", "На острові"),
    "Кнопка таймера": ("Timer button", "Кнопка таймера"),
    "Кнопка и компактный таймер в развёрнутом острове": (
        "A button and a compact timer in the expanded island",
        "Кнопка й компактний таймер у розгорнутому острові",
    ),
    "Кнопка микрофона": ("Microphone button", "Кнопка мікрофона"),
    "Быстро выключить и включить микрофон": (
        "Mute and unmute the microphone in one click",
        "Швидко вимкнути й увімкнути мікрофон",
    ),
    "Микрофон не найден": ("No microphone found", "Мікрофон не знайдено"),
    "Точки приватности": ("Privacy dots", "Точки приватності"),
    "Точка на острове, пока приложение пользуется микрофоном или камерой": (
        "A dot on the island while an app uses the microphone or camera",
        "Крапка на острові, поки програма користується мікрофоном або камерою",
    ),
    "Раскладка клавиатуры": ("Keyboard layout", "Розкладка клавіатури"),
    "Показывает смену языка ввода и Caps Lock": (
        "Shows input language changes and Caps Lock",
        "Показує зміну мови введення і Caps Lock",
    ),
    "Оповещения": ("Alerts", "Сповіщення"),
    "Батарея": ("Battery", "Батарея"),
    "Зарядка подключена или заряд на исходе": (
        "Charger plugged in or battery running low",
        "Заряджання під'єднано або заряд закінчується",
    ),
    "Устройства": ("Devices", "Пристрої"),
    "Подключение наушников, колонок и других устройств": (
        "Headphones, speakers and other devices connecting",
        "Під'єднання навушників, колонок та інших пристроїв",
    ),
    "Загрузки": ("Downloads", "Завантаження"),
    "Прогресс загрузок в браузере. Нужно расширение Default Island для браузера": (
        "Download progress from your browser. Needs the Default Island browser extension",
        "Прогрес завантажень у браузері. Потрібне розширення Default Island для браузера",
    ),
    "Уведомления Windows": ("Windows notifications", "Сповіщення Windows"),
    "Уведомления приложений появляются на острове": (
        "App notifications appear on the island",
        "Сповіщення програм з'являються на острові",
    ),
    "Проверяем доступ к уведомлениям…": (
        "Checking access to notifications…",
        "Перевіряємо доступ до сповіщень…",
    ),
    "Доступ к уведомлениям есть": ("Notification access granted", "Доступ до сповіщень є"),
    "Нет доступа: разрешите его в Параметрах Windows → Конфиденциальность → Уведомления": (
        "No access: allow it in Windows Settings → Privacy → Notifications",
        "Немає доступу: дозвольте його в Параметрах Windows → Конфіденційність → Сповіщення",
    ),
    "Недоступно в этой версии Windows": (
        "Not available in this version of Windows",
        "Недоступно в цій версії Windows",
    ),
    # Quick buttons.
    "Что показывать в развёрнутом острове": (
        "What to show in the expanded island",
        "Що показувати в розгорнутому острові",
    ),
    "Устройство вывода звука": ("Sound output", "Пристрій виведення звуку"),
    "Переключить колонки, наушники или монитор": (
        "Switch between speakers, headphones or a monitor",
        "Перемкнути колонки, навушники чи монітор",
    ),
    "Микрофон": ("Microphone", "Мікрофон"),
    "Выключить или включить микрофон": (
        "Mute or unmute the microphone",
        "Вимкнути чи увімкнути мікрофон",
    ),
    "Скриншот области": ("Area screenshot", "Знімок області"),
    "Выделить часть экрана и сделать снимок": (
        "Select part of the screen and take a shot",
        "Виділити частину екрана й зробити знімок",
    ),
    "Блокировка экрана": ("Lock screen", "Блокування екрана"),
    "Заблокировать компьютер одним нажатием": (
        "Lock the computer in one click",
        "Заблокувати комп'ютер одним натисканням",
    ),
    "Напоминание": ("Reminder", "Нагадування"),
    "Быстро добавить напоминание": ("Add a reminder in a moment", "Швидко додати нагадування"),
    "Ни одной кнопки не выбрано — строки кнопок в острове не будет": (
        "No buttons selected: the island won't show a button row",
        "Жодної кнопки не вибрано — рядка кнопок в острові не буде",
    ),
    # Reminders.
    "Новое напоминание": ("New reminder", "Нове нагадування"),
    "О чём напомнить?": ("What should I remind you about?", "Про що нагадати?"),
    "Когда": ("When", "Коли"),
    "Через 5 минут": ("In 5 minutes", "За 5 хвилин"),
    "Через 15 минут": ("In 15 minutes", "За 15 хвилин"),
    "Через 30 минут": ("In 30 minutes", "За 30 хвилин"),
    "Через час": ("In an hour", "За годину"),
    "Завтра в это же время": ("Tomorrow, same time", "Завтра в цей самий час"),
    "Точное время": ("Exact time", "Точний час"),
    "Каждый день": ("Every day", "Щодня"),
    "Как будильник: напомнит в это время каждый день": (
        "Like an alarm clock: a reminder at this time every day",
        "Як будильник: нагадуватиме в цей час щодня",
    ),
    "Добавить": ("Add", "Додати"),
    "Напишите, о чём напомнить": ("Write what to remind you about", "Напишіть, про що нагадати"),
    "Это время уже прошло": ("That time has already passed", "Цей час уже минув"),
    "Уже {count} напоминаний — удалите ненужные": (
        "You already have {count} reminders: delete some first",
        "Уже {count} нагадувань — видаліть непотрібні",
    ),
    "Не удалось сохранить напоминания: {error}": (
        "Couldn't save reminders: {error}",
        "Не вдалося зберегти нагадування: {error}",
    ),
    "Напоминание добавлено": ("Reminder added", "Нагадування додано"),
    "Запланированные": ("Scheduled", "Заплановані"),
    "Напоминаний пока нет": ("No reminders yet", "Нагадувань поки немає"),
    "Удалить напоминание": ("Delete reminder", "Видалити нагадування"),
    "Сегодня в {time}": ("Today at {time}", "Сьогодні о {time}"),
    "Завтра в {time}": ("Tomorrow at {time}", "Завтра о {time}"),
    "{date} в {time}": ("{date} at {time}", "{date} о {time}"),
    "Каждый день в {time}": ("Every day at {time}", "Щодня о {time}"),
    "января": ("January", "січня"),
    "февраля": ("February", "лютого"),
    "марта": ("March", "березня"),
    "апреля": ("April", "квітня"),
    "мая": ("May", "травня"),
    "июня": ("June", "червня"),
    "июля": ("July", "липня"),
    "августа": ("August", "серпня"),
    "сентября": ("September", "вересня"),
    "октября": ("October", "жовтня"),
    "ноября": ("November", "листопада"),
    "декабря": ("December", "грудня"),
    # System.
    "Язык": ("Language", "Мова"),
    "Язык интерфейса": ("Interface language", "Мова інтерфейсу"),
    "Язык сменится после сохранения": (
        "The language changes after you save",
        "Мова зміниться після збереження",
    ),
    "Плавность": ("Smoothness", "Плавність"),
    "Кадров в секунду": ("Frames per second", "Кадрів на секунду"),
    "От 30 до 240. Больше — плавнее, но выше нагрузка на компьютер": (
        "From 30 to 240. Higher is smoother but works the computer harder",
        "Від 30 до 240. Більше — плавніше, але вище навантаження на комп'ютер",
    ),
    "Можно от 30 до 240 — поставили {value}": (
        "The range is 30 to 240, so it is set to {value}",
        "Можна від 30 до 240 — встановлено {value}",
    ),
    "Режим призрака": ("Ghost mode", "Режим привида"),
    "Сочетание клавиш, которое включает и выключает режим призрака. Нажмите на поле и задайте его": (
        "The shortcut that turns ghost mode on and off. Click the field and press it",
        "Сполучення клавіш, яке вмикає і вимикає режим привида. Натисніть на поле й задайте його",
    ),
    "Нажмите сочетание… (Esc — отмена)": (
        "Press a shortcut… (Esc to cancel)",
        "Натисніть сполучення… (Esc — скасувати)",
    ),
    "Горячая клавиша режима призрака": ("Ghost mode hotkey", "Гаряча клавіша режиму привида"),
    "Запуск": ("Startup", "Запуск"),
    "Запускать вместе с Windows": ("Start with Windows", "Запускати разом із Windows"),
    "Скрывать в полноэкранном режиме": ("Hide in full screen", "Ховати в повноекранному режимі"),
    "В играх и при просмотре видео на весь экран": (
        "In games and full-screen video",
        "В іграх і під час перегляду відео на весь екран",
    ),
    "Значок в области уведомлений": ("Tray icon", "Значок в області сповіщень"),
    "Возле часов на панели задач: оттуда можно открыть настройки и выйти": (
        "Next to the clock on the taskbar: open settings or quit from there",
        "Біля годинника на панелі завдань: звідти можна відкрити налаштування й вийти",
    ),
    "Обновления": ("Updates", "Оновлення"),
    "Проверять обновления": ("Check for updates", "Перевіряти оновлення"),
    "Остров сам сообщит, когда выйдет новая версия": (
        "The island tells you when a new version is out",
        "Острів сам повідомить, коли вийде нова версія",
    ),
    "Проверить сейчас": ("Check now", "Перевірити зараз"),
    "Проверяем…": ("Checking…", "Перевіряємо…"),
    "Версия {version}": ("Version {version}", "Версія {version}"),
    "Закрыть Default Island": ("Quit Default Island", "Закрити Default Island"),
})

# --- Palette of the dialogs ----------------------------------------------------
# The settings stay dark whatever theme the island uses.
TEXT = QColor("#EFEBE3")
TEXT_STRONG = QColor("#F4F0E8")
ROW_TEXT = QColor("#E4DFD5")
HOVER_TEXT = QColor("#CFC9BE")
MUTED = QColor("#A8A298")
FAINT = QColor("#8A857C")
DISABLED = QColor("#6F6A63")
UI_ACCENT = QColor("#C6A27A")
ERROR_TEXT = QColor("#D49C90")
FIELD = QColor("#211F1D")
FIELD_BORDER = QColor("#34312D")
ROW_HOVER = QColor("#242220")
SURFACE_RADIUS = 22.0
SIDEBAR_WIDTH = 212
# Text flags for paragraphs drawn by hand.
WRAP = (
    Qt.AlignmentFlag.AlignLeft.value
    | Qt.AlignmentFlag.AlignTop.value
    | Qt.TextFlag.TextWordWrap.value
)
LINE = Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value
CENTER = Qt.AlignmentFlag.AlignCenter.value

STYLE = """
QDialog { background: transparent; }
QWidget { background: transparent; color: #EFEBE3; font-family: 'Segoe UI Variable Text', 'Segoe UI'; font-size: 13px; }
QLabel { color: #A8A298; }
QLabel:disabled { color: #5E5A54; }
QLabel#heading { color: #F4F0E8; font-family: 'Segoe UI Variable Display', 'Segoe UI'; font-size: 20px; font-weight: 600; }
QLabel#subheading { color: #8A857C; font-size: 12px; }
QLabel#brandName { color: #F4F0E8; font-weight: 600; }
QLabel#brandVersion { color: #7C776F; font-size: 11px; }
QLabel#caption { color: #8A857C; font-size: 12px; font-weight: 600; padding: 12px 0 2px 6px; }
QLabel#rowTitle { color: #E4DFD5; }
QLabel#rowTitle:disabled { color: #6F6A63; }
QLabel#rowValue { color: #EFEBE3; font-weight: 600; }
QLabel#rowValue:disabled { color: #6F6A63; }
QLabel#hint { color: #8A857C; font-size: 12px; }
QLabel#hint:disabled { color: #5E5A54; }
QLabel#error { color: #D49C90; font-size: 12px; }
QLabel#success { color: #A5B99B; font-size: 12px; }
QLabel#empty { color: #7C776F; padding: 16px; }
QFrame#card { background: #1D1C1A; border: 1px solid #2A2825; border-radius: 16px; }
QFrame#rule { background: #262421; border: 0; }
QPushButton { background: #24221F; color: #EFEBE3; border: 1px solid #34312D; border-radius: 12px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { background: #2C2A26; border-color: #46423C; }
QPushButton:pressed { background: #201E1C; }
QPushButton:focus { border-color: #C6A27A; }
QPushButton:disabled { color: #6F6A63; background: #201F1C; border-color: #2D2A27; }
QPushButton#saveButton { background: #E9DFCF; color: #1A1816; border: 2px solid #E9DFCF; padding: 7px 20px; }
QPushButton#saveButton:hover { background: #F3EBDD; border-color: #F3EBDD; }
QPushButton#saveButton:pressed { background: #D9CDB9; border-color: #D9CDB9; }
QPushButton#saveButton:focus { border-color: #C6A27A; }
QPushButton#saveButton:disabled { background: #3A3631; border-color: #3A3631; color: #7C776F; }
QPushButton#quitButton { background: transparent; color: #D49C90; border: 1px solid #4A3632; }
QPushButton#quitButton:hover { background: #2A201E; border-color: #6B4B44; }
QPushButton#quitButton:focus { border-color: #D49C90; }
QPushButton#chip { background: #211F1D; color: #CFC9BE; border: 1px solid #34312D; border-radius: 13px; padding: 5px 12px; font-size: 12px; }
QPushButton#chip:hover { border-color: #4A4640; color: #F4F0E8; }
QPushButton#chip:checked { background: #3A332A; border-color: #C6A27A; color: #F4F0E8; }
QPushButton#chip:focus { border-color: #C6A27A; }
QPushButton#hotkeyButton { background: #211F1D; padding: 9px 12px; text-align: left; }
QPushButton#hotkeyButton[recording="true"] { background: #2A2620; border-color: #C6A27A; color: #E9DFCF; }
QComboBox { background: #211F1D; color: #EFEBE3; border: 1px solid #34312D; border-radius: 12px; padding: 8px 12px; }
QComboBox:hover { border-color: #46423C; }
QComboBox:focus { border-color: #C6A27A; }
QComboBox::drop-down { border: 0; width: 32px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QLineEdit { background: transparent; border: 0; padding: 0; color: #EFEBE3; selection-background-color: #5A4A38; }
QComboBox QAbstractItemView { background: #211F1D; color: #EFEBE3; border: 1px solid #3B3833; border-radius: 10px; padding: 4px; outline: 0; }
QComboBox QAbstractItemView::item { min-height: 30px; padding: 0 8px; border-radius: 7px; }
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected { background: #34312C; color: #F4F0E8; }
QLineEdit, QDateTimeEdit { background: #211F1D; color: #EFEBE3; border: 1px solid #34312D; border-radius: 12px; padding: 8px 12px; selection-background-color: #5A4A38; selection-color: #F4F0E8; }
QLineEdit:hover, QDateTimeEdit:hover { border-color: #46423C; }
QLineEdit:focus, QDateTimeEdit:focus { border-color: #C6A27A; }
QDateTimeEdit QLineEdit { background: transparent; border: 0; padding: 0; }
QDateTimeEdit::up-button, QDateTimeEdit::down-button { width: 0; border: 0; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #3A3733; border-radius: 3px; min-height: 36px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: #4C4842; }
QScrollBar::handle:vertical:disabled { background: transparent; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QMenu { background: #211F1D; color: #EFEBE3; border: 1px solid #3B3833; border-radius: 10px; padding: 5px; }
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 7px; }
QMenu::item:selected { background: #34312C; }
QMenu::item:checked { color: #E9C9A0; font-weight: 600; }
QMenu::indicator { width: 0; height: 0; }
QToolTip { background: #2A2825; color: #EFEBE3; border: 1px solid #3B3833; padding: 4px 8px; }
"""


def paint_focus_ring(painter, rect, radius):
    """The accent ring that marks the control the keyboard is on."""
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(with_alpha(UI_ACCENT, 210), 1.5))
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()


def draw_icon(painter, name, rect, color):
    """A small line icon drawn in a 20×20 box scaled to `rect`."""
    painter.save()
    scale = min(rect.width(), rect.height()) / 20.0
    painter.translate(rect.center() - QPointF(10.0 * scale, 10.0 * scale))
    painter.scale(scale, scale)
    painter.setPen(round_pen(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if name == "appearance":
        painter.drawEllipse(QPointF(10, 10), 7, 7)
        half = QPainterPath()
        half.moveTo(10, 3)
        half.arcTo(QRectF(3, 3, 14, 14), 90, -180)
        half.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(half)
    elif name == "size":
        painter.drawRoundedRect(QRectF(4, 7.5, 12, 5), 2.5, 2.5)
        for x, y, dx, dy in ((2, 2, 1, 1), (18, 2, -1, 1), (2, 18, 1, -1), (18, 18, -1, -1)):
            painter.drawPolyline([QPointF(x, y + 3 * dy), QPointF(x, y), QPointF(x + 3 * dx, y)])
    elif name == "music":
        painter.setBrush(color)
        painter.drawEllipse(QPointF(6.8, 14.6), 2.6, 2.2)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(9.4, 14.4), QPointF(9.4, 3.5))
        painter.drawPolyline([QPointF(9.4, 3.5), QPointF(15.5, 5.0), QPointF(15.5, 8.0), QPointF(9.4, 6.6)])
    elif name == "indicators":
        painter.drawEllipse(QPointF(10, 10), 7, 7)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(10, 10), 2.4, 2.4)
    elif name == "quick":
        for x, y in ((3, 3), (11, 3), (3, 11), (11, 11)):
            painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.8, 1.8)
    elif name in ("reminders", "note"):
        bell = QPainterPath()
        bell.moveTo(5.5, 14)
        bell.lineTo(5.5, 9)
        bell.arcTo(QRectF(5.5, 4.5, 9, 9), 180, -180)
        bell.lineTo(14.5, 14)
        painter.drawPath(bell)
        painter.drawLine(QPointF(4, 14), QPointF(16, 14))
        painter.drawLine(QPointF(10, 2.8), QPointF(10, 4.5))
        painter.drawArc(QRectF(8.2, 14.8, 3.6, 3.0), 180 * 16, 180 * 16)
    elif name == "system":
        painter.drawEllipse(QPointF(10, 10), 5.0, 5.0)
        painter.drawEllipse(QPointF(10, 10), 1.8, 1.8)
        for step in range(8):
            angle = math.radians(step * 45 + 22.5)
            painter.drawLine(
                QPointF(10 + 5.0 * math.cos(angle), 10 + 5.0 * math.sin(angle)),
                QPointF(10 + 7.4 * math.cos(angle), 10 + 7.4 * math.sin(angle)),
            )
    elif name == "output":
        painter.drawPolygon([
            QPointF(3, 8), QPointF(6, 8), QPointF(10, 4.5),
            QPointF(10, 15.5), QPointF(6, 12), QPointF(3, 12),
        ])
        painter.drawArc(QRectF(7.5, 6.5, 7, 7), -50 * 16, 100 * 16)
        painter.drawArc(QRectF(5.5, 3.5, 13, 13), -50 * 16, 100 * 16)
    elif name == "mic":
        painter.drawRoundedRect(QRectF(7.5, 2.5, 5, 9), 2.5, 2.5)
        painter.drawArc(QRectF(4.5, 4.5, 11, 10), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(10, 14.5), QPointF(10, 17.5))
    elif name == "screenshot":
        for x, y, dx, dy in ((3, 3, 1, 1), (17, 3, -1, 1), (3, 17, 1, -1), (17, 17, -1, -1)):
            painter.drawPolyline([QPointF(x, y + 3.5 * dy), QPointF(x, y), QPointF(x + 3.5 * dx, y)])
        painter.drawEllipse(QPointF(10, 10), 2.6, 2.6)
    elif name == "clipboard":
        painter.drawRoundedRect(QRectF(4.5, 4.5, 11, 13), 2, 2)
        painter.drawRoundedRect(QRectF(7.5, 2.5, 5, 3.5), 1.2, 1.2)
        painter.drawLine(QPointF(7.5, 10), QPointF(12.5, 10))
        painter.drawLine(QPointF(7.5, 13), QPointF(11, 13))
    elif name == "lock":
        painter.drawRoundedRect(QRectF(4.5, 9, 11, 8.5), 2, 2)
        shackle = QPainterPath()
        shackle.moveTo(7, 9)
        shackle.lineTo(7, 6.5)
        shackle.arcTo(QRectF(7, 3.5, 6, 6), 180, -180)
        shackle.lineTo(13, 9)
        painter.drawPath(shackle)
    elif name == "repeat":
        painter.drawArc(QRectF(4, 4, 12, 12), 70 * 16, 290 * 16)
        tip = QPointF(10 + 6 * math.cos(math.radians(70)), 10 - 6 * math.sin(math.radians(70)))
        painter.drawPolyline([tip + QPointF(-3.0, -2.2), tip, tip + QPointF(-2.4, 2.8)])
    elif name == "close":
        painter.drawLine(QPointF(6, 6), QPointF(14, 14))
        painter.drawLine(QPointF(14, 6), QPointF(6, 14))
    elif name == "check":
        painter.setPen(round_pen(color, 2.0))
        painter.drawPolyline([QPointF(5.5, 10.5), QPointF(8.6, 13.5), QPointF(14.5, 7)])
    painter.restore()


def format_reminder_time(at, repeat):
    """When a reminder is due, in words, without the process locale."""
    moment = datetime.fromtimestamp(at)
    clock = f"{moment.hour:02d}:{moment.minute:02d}"
    if repeat == "daily":
        return tr("Каждый день в {time}").format(time=clock)
    today = date.today()
    if moment.date() == today:
        return tr("Сегодня в {time}").format(time=clock)
    if moment.date() == today + timedelta(days=1):
        return tr("Завтра в {time}").format(time=clock)
    months = (
        tr("января"), tr("февраля"), tr("марта"), tr("апреля"), tr("мая"),
        tr("июня"), tr("июля"), tr("августа"), tr("сентября"), tr("октября"),
        tr("ноября"), tr("декабря"),
    )
    day = f"{moment.day} {months[moment.month - 1]}"
    if moment.year != today.year:
        day += f" {moment.year}"
    return tr("{date} в {time}").format(date=day, time=clock)


def store_reminder(island, reminder):
    """Add a reminder to the file and tell the island. Raises ValueError
    when the list is full and OSError when the file cannot be written."""
    reminders = load_reminders()
    if len(reminders) >= MAX_REMINDERS:
        raise ValueError(
            tr("Уже {count} напоминаний — удалите ненужные").format(count=len(reminders))
        )
    reminders.append(reminder)
    save_reminders(reminders)
    island.reminders_changed()


def delete_reminder(island, reminder_id):
    reminders = [item for item in load_reminders() if item["id"] != reminder_id]
    save_reminders(reminders)
    island.reminders_changed()


# --- Small building blocks -------------------------------------------------------
class Interactive:
    """Mixin for hand-drawn controls: repaint on hover, and remember whether
    the focus came from the keyboard so the focus ring shows for Tab but not
    after a click."""

    keyboard_focus = False

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def shows_focus(self):
        return self.hasFocus() and self.keyboard_focus


def text_block_height(font, text, width):
    if not text:
        return 0.0
    return QFontMetricsF(font).boundingRect(QRectF(0, 0, width, 100000), WRAP, text).height()


def small_font(font):
    smaller = QFont(font)
    smaller.setPixelSize(12)
    return smaller


class ToggleSwitch(Interactive, QCheckBox):
    """A check box drawn as a row: a title with an optional explanation on
    the left and a switch on the right. The whole row toggles."""

    TRACK_W, TRACK_H = 38.0, 22.0
    PAD_X, PAD_Y = 10.0, 9.0

    def __init__(self, text, hint=""):
        super().__init__(text)
        self._hint = hint
        self._knob = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_knob)
        self.toggled.connect(self._on_toggled)

    def set_hint(self, hint):
        if hint != self._hint:
            self._hint = hint
            self.updateGeometry()
            self.update()

    def hint(self):
        return self._hint

    def _set_knob(self, value):
        self._knob = float(value)
        self.update()

    def _on_toggled(self, checked):
        self._animation.stop()
        if not self.isVisible():
            self._set_knob(1.0 if checked else 0.0)
            return
        self._animation.setStartValue(self._knob)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def setChecked(self, checked):
        super().setChecked(checked)
        # A value set from code before the row is seen must not animate later.
        if not self.isVisible():
            self._set_knob(1.0 if checked else 0.0)

    def _text_width(self, width):
        return max(60.0, width - 2 * self.PAD_X - self.TRACK_W - 16)

    def _blocks(self, width):
        text_width = self._text_width(width)
        title = text_block_height(self.font(), self.text(), text_width)
        hint = text_block_height(small_font(self.font()), self._hint, text_width)
        return title, hint

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        title, hint = self._blocks(width)
        return int(max(44, math.ceil(title + (hint + 2 if hint else 0) + 2 * self.PAD_Y)))

    def sizeHint(self):
        return QSize(320, self.heightForWidth(320))

    def minimumSizeHint(self):
        return QSize(180, 44)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        enabled = self.isEnabled()
        row = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.underMouse() and enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ROW_HOVER)
            painter.drawRoundedRect(row, 10, 10)
        if self.shows_focus():
            paint_focus_ring(painter, row, 10)

        text_width = self._text_width(self.width())
        title, hint = self._blocks(self.width())
        top = (self.height() - title - (hint + 2 if hint else 0)) / 2
        painter.setFont(self.font())
        painter.setPen(ROW_TEXT if enabled else DISABLED)
        painter.drawText(QRectF(self.PAD_X, top, text_width, title + 2), WRAP, self.text())
        if hint:
            painter.setFont(small_font(self.font()))
            painter.setPen(FAINT if enabled else QColor("#5E5A54"))
            painter.drawText(
                QRectF(self.PAD_X, top + title + 2, text_width, hint + 2), WRAP, self._hint
            )

        track = QRectF(
            self.width() - self.PAD_X - self.TRACK_W,
            (self.height() - self.TRACK_H) / 2,
            self.TRACK_W,
            self.TRACK_H,
        )
        track_color = mix_colors(QColor("#35322D"), UI_ACCENT, self._knob)
        if self.underMouse() and enabled:
            track_color = track_color.lighter(110)
        painter.setOpacity(1.0 if enabled else 0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        knob_radius = self.TRACK_H / 2 - 3
        knob_x = track.left() + 3 + knob_radius + self._knob * (self.TRACK_W - self.TRACK_H)
        painter.setBrush(QColor(0, 0, 0, 55))
        painter.drawEllipse(QPointF(knob_x, track.center().y() + 1), knob_radius, knob_radius)
        painter.setBrush(mix_colors(QColor("#B5AFA4"), QColor("#FBF8F1"), self._knob))
        painter.drawEllipse(QPointF(knob_x, track.center().y()), knob_radius, knob_radius)


class SoftSlider(Interactive, QSlider):
    """A thin slider with a round knob in the dialog's palette. A click
    anywhere on it jumps there; the wheel only works once it has focus, so
    scrolling the page never changes a value by accident."""

    KNOB = 8.0

    def __init__(self, low, high, page_step=10):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(low, high)
        self.setSingleStep(1)
        self.setPageStep(page_step)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _span(self):
        return self.KNOB + 1.0, self.width() - self.KNOB - 1.0

    def _x_for(self, value):
        left, right = self._span()
        if self.maximum() <= self.minimum():
            return left
        return left + (value - self.minimum()) / (self.maximum() - self.minimum()) * (right - left)

    def _value_at(self, x):
        left, right = self._span()
        share = 0.0 if right <= left else max(0.0, min(1.0, (x - left) / (right - left)))
        return round(self.minimum() + share * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        self.setValue(self._value_at(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            self.update()

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        left, right = self._span()
        middle = self.height() / 2.0
        knob_x = self._x_for(self.value())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#35322D"))
        painter.drawRoundedRect(QRectF(left, middle - 2, right - left, 4), 2, 2)
        painter.setBrush(UI_ACCENT if enabled else QColor("#4A4640"))
        painter.drawRoundedRect(QRectF(left, middle - 2, knob_x - left, 4), 2, 2)
        radius = self.KNOB + (1.0 if (self.underMouse() or self.isSliderDown()) and enabled else 0.0)
        if self.shows_focus():
            painter.setBrush(with_alpha(UI_ACCENT, 60))
            painter.drawEllipse(QPointF(knob_x, middle), radius + 5, radius + 5)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawEllipse(QPointF(knob_x, middle + 1.2), radius, radius)
        painter.setBrush(QColor("#FBF8F1") if enabled else QColor("#6F6A63"))
        painter.drawEllipse(QPointF(knob_x, middle), radius, radius)


class SliderRow(QWidget):
    """A titled slider with its value shown on the right."""

    def __init__(self, title, slider, formatter, hint=""):
        super().__init__()
        self.slider = slider
        self.formatter = formatter
        # The slider reaches 8 px further out than the text on each side,
        # so its track starts right under the title.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 6)
        layout.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(8, 0, 8, 0)
        top.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("rowTitle")
        self.value = QLabel()
        self.value.setObjectName("rowValue")
        top.addWidget(self.title, 1)
        top.addWidget(self.value)
        layout.addLayout(top)
        layout.addWidget(slider)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("hint")
            hint_label.setWordWrap(True)
            hint_label.setContentsMargins(8, 0, 8, 0)
            layout.addWidget(hint_label)
        slider.setAccessibleName(title)
        slider.valueChanged.connect(self._show_value)
        self._show_value(slider.value())

    def _show_value(self, value):
        self.value.setText(self.formatter(value))


class SegmentedControl(Interactive, QWidget):
    """A row of mutually exclusive choices with a sliding highlight."""

    changed = pyqtSignal(str)

    def __init__(self, options, current):
        super().__init__()
        self.options = list(options)
        values = [value for value, _label in self.options]
        self.index = values.index(current) if current in values else 0
        self._position = float(self.index)
        self.hovered = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self.setMinimumWidth(120)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._move_highlight)

    def value(self):
        return self.options[self.index][0]

    def sizeHint(self):
        metrics = QFontMetricsF(ui_font(9.75, QFont.Weight.DemiBold))
        widest = max(metrics.horizontalAdvance(label) for _value, label in self.options)
        return QSize(int((widest + 32) * len(self.options)) + 6, 38)

    def _move_highlight(self, value):
        self._position = float(value)
        self.update()

    def set_index(self, index):
        index = max(0, min(len(self.options) - 1, index))
        if index == self.index:
            return
        self.index = index
        self._animation.stop()
        if self.isVisible():
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(float(index))
            self._animation.start()
        else:
            self._move_highlight(float(index))
        self.update()
        self.changed.emit(self.value())

    def _segment(self, position):
        width = (self.width() - 6.0) / len(self.options)
        return QRectF(3.0 + position * width, 3.0, width, self.height() - 6.0)

    def _index_at(self, x):
        width = (self.width() - 6.0) / len(self.options)
        return max(0, min(len(self.options) - 1, int((x - 3.0) // width)))

    def mouseMoveEvent(self, event):
        hovered = self._index_at(event.position().x())
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()

    def leaveEvent(self, event):
        self.hovered = None
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_index(self._index_at(event.position().x()))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.set_index(self.index - 1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.set_index(self.index + 1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(UI_ACCENT if self.shows_focus() else FIELD_BORDER, 1.0))
        painter.setBrush(FIELD)
        painter.drawRoundedRect(frame, 12, 12)
        highlight = self._segment(self._position)
        painter.setPen(QPen(QColor("#46423C"), 1.0))
        painter.setBrush(QColor("#34312C"))
        painter.drawRoundedRect(highlight.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        font = ui_font(9.75, QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for index, (_value, label) in enumerate(self.options):
            rect = self._segment(index).adjusted(8, 0, -8, 0)
            if index == self.index:
                painter.setPen(TEXT_STRONG)
            elif index == self.hovered:
                painter.setPen(HOVER_TEXT)
            else:
                painter.setPen(FAINT)
            text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, rect.width())
            painter.drawText(rect, CENTER, text)


class SoftComboBox(QComboBox):
    """A combo box with a drawn chevron and a rounded drop-down list. The
    wheel only works once it has focus, so scrolling the page is safe."""

    def __init__(self):
        super().__init__()
        # A real list view, so the style sheet can shape the items.
        self.setView(QListView())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        popup = self.view().window()
        popup.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(round_pen(MUTED, 1.5))
        center_x = self.width() - 18.0
        center_y = self.height() / 2.0
        chevron = QPainterPath()
        chevron.moveTo(center_x - 4.0, center_y - 2.0)
        chevron.lineTo(center_x, center_y + 2.0)
        chevron.lineTo(center_x + 4.0, center_y - 2.0)
        painter.drawPath(chevron)


class DateTimeField(QDateTimeEdit):
    """Date and time typed with digits only, the same in every language."""

    def __init__(self):
        super().__init__()
        self.setLocale(QLocale.c())
        self.setDisplayFormat("dd.MM.yyyy   HH:mm")
        self.setCalendarPopup(False)
        # The step buttons are hidden by the style sheet (width 0) rather
        # than by NoButtons, which with a padded style sheet squeezes the
        # text field to one pixel. Arrow keys and the wheel still step.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(170)
        # The style sheet's padding does not reach the inner text field.
        self.lineEdit().setTextMargins(8, 0, 0, 0)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class IconButton(Interactive, QAbstractButton):
    """A round, borderless button with a drawn icon."""

    def __init__(self, icon, label, size=32, danger=False):
        super().__init__()
        self.icon_name = icon
        self.danger = danger
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(label)
        self.setAccessibleName(label)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hovered = self.underMouse() or self.isDown()
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3A2724") if self.danger else QColor("#2A2825"))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if self.shows_focus():
            paint_focus_ring(painter, rect, rect.height() / 2)
        color = (ERROR_TEXT if self.danger else TEXT_STRONG) if hovered else MUTED
        inset = rect.width() * 0.22
        draw_icon(painter, self.icon_name, rect.adjusted(inset, inset, -inset, -inset), color)


class ElidedLabel(QLabel):
    """One line of text that ends with "…" when it does not fit."""

    def minimumSizeHint(self):
        return QSize(24, super().minimumSizeHint().height())

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(hint.width(), 240), hint.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.width()
        )
        painter.drawText(QRectF(self.rect()), LINE, text)


class FlowLayout(QLayout):
    """Lays its items out in rows that wrap, like words in a line."""

    def __init__(self, spacing=8):
        super().__init__()
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, apply):
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x = rect.x()
                y += line + self._gap
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()


# --- The island-shaped controls ----------------------------------------------------
class ZonePicker(Interactive, QWidget):
    """The three zones of the island, drawn as the island itself: a capsule
    in the chosen theme over a small sketch of a desktop, so glass shows
    what lies under it. A click on a third opens its choices."""

    PILL_HEIGHT = 44.0
    # The capsule sits this far into the desktop sketch; the bottom room
    # holds its whole shadow.
    PILL_TOP, STAGE_BOTTOM = 18.0, 30.0
    SHADOW_SIZE = 0.6

    changed = pyqtSignal(str, str)

    def __init__(self, config, keys, options):
        super().__init__()
        self.config = config
        self.keys = keys
        self.options = options
        self.look = make_look(config)
        self.accent = QColor(DEFAULT_ACCENT)
        self.hovered = None
        self.open_zone = None
        # The zone the keyboard is on; None while the mouse drives the picker.
        self.focus_zone = None
        # One menu, refilled for each zone, instead of a new one per click.
        self.menu = QMenu(self)
        self.menu.setWindowFlags(
            self.menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("Зоны острова: слева, в центре, справа"))
        self.setFixedHeight(int(self.PILL_TOP + self.PILL_HEIGHT + self.STAGE_BOTTOM))
        self.setMinimumWidth(280)

    def set_look(self, look, accent):
        """Draw the capsule as the island looks in this theme and accent."""
        self.look = look
        self.accent = QColor(accent)
        self.update()

    def pill_rect(self):
        width = max(0.0, min(self.width() - 40.0, 440.0))
        return QRectF((self.width() - width) / 2, self.PILL_TOP, width, self.PILL_HEIGHT)

    def zone_rect(self, index):
        pill = self.pill_rect()
        width = pill.width() / len(self.keys)
        return QRectF(pill.left() + index * width, pill.top(), width, pill.height())

    def zone_at(self, position):
        pill = self.pill_rect()
        if pill.width() <= 0 or not pill.contains(position):
            return None
        index = int((position.x() - pill.left()) / (pill.width() / len(self.keys)))
        return max(0, min(len(self.keys) - 1, index))

    def zone_label(self, index):
        selected = self.config.get(self.keys[index])
        return next((name for value, name in self.options[index] if value == selected), "—")

    def set_hovered(self, zone):
        if zone != self.hovered:
            self.hovered = zone
            self.update()
        if zone is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def open_menu(self, index):
        self.menu.clear()  # deletes the actions of the previous zone
        key = self.keys[index]
        for value, label in self.options[index]:
            action = self.menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.config[key] == value)
            action.triggered.connect(lambda checked=False, v=value: self.set_value(key, v))
        zone = self.zone_rect(index)
        self.menu.setMinimumWidth(int(zone.width()))
        self.open_zone = index
        self.update()
        self.menu.exec(self.mapToGlobal(QPoint(int(zone.left()), int(zone.bottom()) + 6)))
        self.open_zone = None
        # The mouse may have left the zone while the menu was open.
        self.set_hovered(self.zone_at(QPointF(self.mapFromGlobal(QCursor.pos()))))
        self.update()

    def set_value(self, key, value):
        self.config[key] = value
        self.update()
        self.changed.emit(key, value)

    def mouseMoveEvent(self, event):
        self.set_hovered(self.zone_at(event.position()))

    def leaveEvent(self, event):
        self.set_hovered(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        zone = self.zone_at(event.position())
        if zone is not None and event.button() == Qt.MouseButton.LeftButton:
            self.focus_zone = None
            self.open_menu(zone)

    def focusInEvent(self, event):
        if event.reason() == Qt.FocusReason.TabFocusReason:
            self.focus_zone = 0
        elif event.reason() == Qt.FocusReason.BacktabFocusReason:
            self.focus_zone = len(self.keys) - 1
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        # Its own menu takes the focus for a moment; that is not leaving.
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.focus_zone = None
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        current = 0 if self.focus_zone is None else self.focus_zone
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if key == Qt.Key.Key_Left else 1
            if self.focus_zone is not None:
                current = max(0, min(len(self.keys) - 1, current + step))
            self.focus_zone = current
            self.update()
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
            self.focus_zone = current
            self.open_menu(current)
        else:
            super().keyPressEvent(event)

    def _paint_stage(self, painter, stage):
        """A muted desktop with a pale window on it, for the glass to show."""
        backdrop = QLinearGradient(stage.topLeft(), stage.bottomRight())
        backdrop.setColorAt(0.0, QColor("#2D3846"))
        backdrop.setColorAt(0.55, QColor("#463C48"))
        backdrop.setColorAt(1.0, QColor("#5A4936"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(backdrop))
        painter.drawRoundedRect(stage, 14, 14)
        window = QRectF(
            stage.left() + stage.width() * 0.07, stage.top() + 10,
            stage.width() * 0.4, stage.height() - 20,
        )
        painter.setBrush(QColor(214, 208, 198, 225))
        painter.drawRoundedRect(window, 7, 7)
        painter.setBrush(QColor(120, 114, 106, 150))
        for row in range(4):
            line = QRectF(window.left() + 12, window.top() + 12 + row * 15, window.width() * (0.7 - row * 0.12), 5)
            painter.drawRoundedRect(line, 2.5, 2.5)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        look = self.look
        stage = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.save()
        painter.setClipPath(rounded_rect_path(stage, 14))
        self._paint_stage(painter, stage)
        pill = self.pill_rect()
        radius = pill.height() / 2.0
        shape = rounded_rect_path(pill, radius)
        # As on the screen, the shadow lies around the island, not under it.
        around = QPainterPath()
        around.addRect(stage)
        painter.setClipPath(around.subtracted(shape), Qt.ClipOperation.IntersectClip)
        paint_soft_shadow(painter, pill, radius, look.shadow, self.SHADOW_SIZE)
        painter.restore()
        paint_island_surface(painter, pill, radius, self.accent, look, playing=0.7)

        painter.save()
        painter.setClipPath(shape)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(len(self.keys)):
            if index == self.open_zone:
                painter.fillRect(self.zone_rect(index), look.veil(34))
            elif index == self.hovered:
                painter.fillRect(self.zone_rect(index), look.veil(20))
        painter.restore()

        painter.setPen(round_pen(look.veil(70 if look.glass else 44), 1.2))
        for index in range(1, len(self.keys)):
            x = self.zone_rect(index).left()
            painter.drawLine(QPointF(x, pill.top() + 12.0), QPointF(x, pill.bottom() - 12.0))

        font = ui_font(10, QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for index in range(len(self.keys)):
            rect = self.zone_rect(index).adjusted(12.0, -1.0, -12.0, -1.0)
            text = metrics.elidedText(self.zone_label(index), Qt.TextElideMode.ElideRight, rect.width())
            empty = self.config.get(self.keys[index]) == "None"
            shadow = look.text_shadow(150 if empty else 230)
            if shadow is not None:
                # The same soft shadow the island puts under its text.
                painter.setPen(shadow)
                painter.drawText(rect.translated(0.0, 1.0), CENTER, text)
            painter.setPen(look.tertiary if empty else look.primary)
            painter.drawText(rect, CENTER, text)

        if self.hasFocus() and self.focus_zone is not None:
            ring = self.zone_rect(self.focus_zone).adjusted(5.0, 5.0, -5.0, -5.0)
            paint_focus_ring(painter, ring, ring.height() / 2.0)


class AccentPicker(Interactive, QWidget):
    """A row of colour swatches; the first one follows the track artwork.
    Arrow keys move between them."""

    SWATCH = 24.0
    GAP = 12.0
    changed = pyqtSignal(str)

    def __init__(self, current):
        super().__init__()
        self.keys = ("Auto", *ACCENT_PRESETS)
        self.current = current if current in self.keys else "Auto"
        self.hovered = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("Акцентный цвет"))
        self.setFixedHeight(40)
        self.setMinimumWidth(int(len(self.keys) * (self.SWATCH + self.GAP) + 8))

    def currentData(self):
        return self.current

    def swatch_center(self, index):
        return QPointF(6.0 + self.SWATCH / 2 + index * (self.SWATCH + self.GAP), self.height() / 2.0)

    def key_at(self, position):
        reach = (self.SWATCH + self.GAP) / 2
        for index, key in enumerate(self.keys):
            center = self.swatch_center(index)
            if abs(position.x() - center.x()) <= reach and abs(position.y() - center.y()) <= reach:
                return key
        return None

    def choose(self, key):
        if key != self.current:
            self.current = key
            self.changed.emit(key)
        self.update()

    def mouseMoveEvent(self, event):
        hovered = self.key_at(event.position())
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()

    def leaveEvent(self, event):
        self.hovered = None
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        key = self.key_at(event.position())
        if key is not None and event.button() == Qt.MouseButton.LeftButton:
            self.choose(key)

    def keyPressEvent(self, event):
        index = self.keys.index(self.current)
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.choose(self.keys[max(0, index - 1)])
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.choose(self.keys[min(len(self.keys) - 1, index + 1)])
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.SWATCH / 2
        for index, key in enumerate(self.keys):
            center = self.swatch_center(index)
            if key == "Auto":
                # A soft colour wheel of the presets stands for "from the artwork".
                wheel = QConicalGradient(center, 90)
                colors = list(ACCENT_PRESETS.values())
                for step, color in enumerate(colors):
                    wheel.setColorAt(step / len(colors), color)
                wheel.setColorAt(1.0, colors[0])
                brush = QBrush(wheel)
            else:
                brush = QBrush(ACCENT_PRESETS[key])
            grow = 1.0 if key == self.hovered and key != self.current else 0.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(brush)
            painter.drawEllipse(center, radius - 2 + grow, radius - 2 + grow)
            if key == self.current:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                ring = UI_ACCENT if self.shows_focus() else QColor("#EFEBE3")
                painter.setPen(QPen(ring, 2.0 if self.shows_focus() else 1.5))
                painter.drawEllipse(center, radius + 1.5, radius + 1.5)

        label_left = self.swatch_center(len(self.keys)).x() - radius + 2
        width = self.width() - label_left
        if width > 40:
            painter.setPen(MUTED)
            painter.setFont(self.font())
            text = self.fontMetrics().elidedText(
                tr(ACCENT_LABELS[self.hovered or self.current]), Qt.TextElideMode.ElideRight, int(width)
            )
            painter.drawText(QRectF(label_left, 0, width, self.height()), LINE, text)


class HotkeyRecorderButton(QPushButton):
    """Shows the hotkey; a click starts recording the next combination.

    A combination the island cannot use is not accepted: `problem` carries
    the reason to show, and recording goes on until a valid one or Esc."""

    recorded = pyqtSignal(str)
    problem = pyqtSignal(str)

    MODIFIER_KEYS = (
        Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
        Qt.Key.Key_AltGr, Qt.Key.Key_Meta,
    )

    def __init__(self, current_text):
        super().__init__(current_text)
        self.setObjectName("hotkeyButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tr("Горячая клавиша режима призрака"))
        self.recording = False
        self.hotkey = current_text
        self.clicked.connect(self.start_recording)

    def _set_recording(self, recording):
        self.recording = recording
        self.setProperty("recording", "true" if recording else "false")
        # Re-apply the style sheet so the [recording] rule takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(tr("Нажмите сочетание… (Esc — отмена)") if recording else self.hotkey)

    def start_recording(self):
        self._set_recording(True)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def cancel_recording(self):
        if self.recording:
            self._set_recording(False)

    def set_hotkey(self, hotkey):
        self.hotkey = hotkey
        self._set_recording(False)

    def focusOutEvent(self, event):
        # Leaving the button mid-recording must not keep the prompt as the hotkey.
        self.cancel_recording()
        super().focusOutEvent(event)

    @staticmethod
    def key_name(event):
        """The name parse_hotkey knows for the pressed key."""
        key = int(event.key())
        native = event.nativeVirtualKey()
        if 0x60 <= native <= 0x69:
            # The digits of the numeric keypad are keys of their own.
            return f"Num{native - 0x60}"
        if (
            event.modifiers() & Qt.KeyboardModifier.KeypadModifier
            and Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value
        ):
            return f"Num{key - Qt.Key.Key_0.value}"
        # Shift changes the reported key ("1" becomes "!"), so letters and
        # digits are taken from the physical key instead.
        if 0x30 <= native <= 0x39 or 0x41 <= native <= 0x5A:
            return chr(native)
        if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F24.value:
            return f"F{key - Qt.Key.Key_F1.value + 1}"
        return QKeySequence(key).toString()

    def keyPressEvent(self, event):
        if not self.recording:
            super().keyPressEvent(event)
            return
        if event.key() in self.MODIFIER_KEYS:
            return
        mods = event.modifiers()
        if event.key() == Qt.Key.Key_Escape and not (
            mods & ~Qt.KeyboardModifier.KeypadModifier
        ):
            self.cancel_recording()
            return
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")
        parts.append(self.key_name(event) or "?")
        hotkey = "+".join(parts)
        try:
            parse_hotkey(hotkey)
        except ValueError as error:
            self.problem.emit(str(error))
            return
        self.set_hotkey(hotkey)
        self.recorded.emit(hotkey)


class CheckRow(Interactive, QAbstractButton):
    """A row with an icon, a title and an explanation, ticked on the right."""

    PAD_X, PAD_Y, ICON, CHECK = 10.0, 9.0, 34.0, 22.0

    def __init__(self, icon, title, hint):
        super().__init__()
        self.icon_name = icon
        self.title = title
        self.hint = hint
        self.setText(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setAccessibleDescription(hint)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _text_left(self):
        return self.PAD_X + self.ICON + 12

    def _text_width(self, width):
        return max(60.0, width - self._text_left() - self.CHECK - self.PAD_X - 12)

    def _blocks(self, width):
        text_width = self._text_width(width)
        return (
            text_block_height(self.font(), self.title, text_width),
            text_block_height(small_font(self.font()), self.hint, text_width),
        )

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        title, hint = self._blocks(width)
        return int(max(54, math.ceil(title + hint + 2 + 2 * self.PAD_Y)))

    def sizeHint(self):
        return QSize(320, self.heightForWidth(320))

    def minimumSizeHint(self):
        return QSize(200, 54)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ROW_HOVER)
            painter.drawRoundedRect(row, 10, 10)
        if self.shows_focus():
            paint_focus_ring(painter, row, 10)
        checked = self.isChecked()
        tile = QRectF(self.PAD_X, (self.height() - self.ICON) / 2, self.ICON, self.ICON)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3A332A") if checked else QColor("#262420"))
        painter.drawRoundedRect(tile, 10, 10)
        draw_icon(painter, self.icon_name, tile.adjusted(8, 8, -8, -8), QColor("#E9C9A0") if checked else MUTED)

        text_width = self._text_width(self.width())
        title, hint = self._blocks(self.width())
        top = (self.height() - title - hint - 2) / 2
        painter.setFont(self.font())
        painter.setPen(ROW_TEXT)
        painter.drawText(QRectF(self._text_left(), top, text_width, title + 2), WRAP, self.title)
        painter.setFont(small_font(self.font()))
        painter.setPen(FAINT)
        painter.drawText(QRectF(self._text_left(), top + title + 2, text_width, hint + 2), WRAP, self.hint)

        mark = QRectF(self.width() - self.PAD_X - self.CHECK, (self.height() - self.CHECK) / 2, self.CHECK, self.CHECK)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(UI_ACCENT)
            painter.drawEllipse(mark)
            draw_icon(painter, "check", mark.adjusted(3, 3, -3, -3), QColor("#1A1816"))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#5A5650") if self.underMouse() else QColor("#46423C"), 1.5))
            painter.drawEllipse(mark.adjusted(1, 1, -1, -1))


class NavButton(Interactive, QAbstractButton):
    """One section in the sidebar. The sidebar paints the highlight."""

    step = pyqtSignal(int)

    def __init__(self, icon, text):
        super().__init__()
        self.icon_name = icon
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left):
            self.step.emit(-1)
        elif event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Right):
            self.step.emit(1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        checked = self.isChecked()
        hovered = self.underMouse()
        if hovered and not checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.drawRoundedRect(rect, 11, 11)
        if self.shows_focus():
            paint_focus_ring(painter, rect, 11)
        icon_color = QColor("#E9C9A0") if checked else (HOVER_TEXT if hovered else FAINT)
        draw_icon(painter, self.icon_name, QRectF(14, (self.height() - 18) / 2, 18, 18), icon_color)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold if checked else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(TEXT_STRONG if checked else (HOVER_TEXT if hovered else MUTED))
        text_rect = QRectF(44, 0, self.width() - 52, self.height())
        text = QFontMetricsF(font).elidedText(self.text(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, LINE, text)


class BrandMark(QWidget):
    """A tiny island: the mark next to the app name in the sidebar."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(38, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QRectF(2, 5, 34, 14)
        paint_island_surface(painter, pill, 7, UI_ACCENT, make_look({}), playing=1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(UI_ACCENT)
        painter.drawEllipse(QPointF(pill.right() - 7, pill.center().y()), 2.2, 2.2)
        painter.setBrush(QColor(244, 240, 232, 150))
        painter.drawRoundedRect(QRectF(pill.left() + 6, pill.center().y() - 1.2, 12, 2.4), 1.2, 1.2)


class Sidebar(QWidget):
    """The list of sections with a highlight that glides to the chosen one."""

    changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.buttons = []
        self.index = 0
        self._from_y = None
        self._glide = 1.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 16)
        layout.setSpacing(2)
        self.brand = QWidget()
        brand_layout = QHBoxLayout(self.brand)
        brand_layout.setContentsMargins(8, 0, 0, 0)
        brand_layout.setSpacing(10)
        brand_layout.addWidget(BrandMark())
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("Default Island")
        name.setObjectName("brandName")
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("brandVersion")
        for label in (name, version):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            names.addWidget(label)
        brand_layout.addLayout(names)
        brand_layout.addStretch()
        layout.addWidget(self.brand)
        layout.addSpacing(18)
        self._list = layout
        self.setAccessibleName(tr("Разделы настроек"))
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(240)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._set_glide)

    def add(self, icon, text):
        button = NavButton(icon, text)
        index = len(self.buttons)
        button.clicked.connect(lambda checked=False, i=index: self.select(i))
        button.step.connect(self._step)
        self.buttons.append(button)
        self._list.addWidget(button)
        return button

    def finish(self):
        self._list.addStretch()
        self.select(0, animate=False)

    def _set_glide(self, value):
        self._glide = float(value)
        self.update()

    def _highlight_y(self):
        target = float(self.buttons[self.index].y())
        if self._from_y is None or self._glide >= 1.0:
            return target
        return self._from_y + (target - self._from_y) * self._glide

    def _step(self, step):
        index = max(0, min(len(self.buttons) - 1, self.index + step))
        self.select(index)
        self.buttons[index].setFocus(Qt.FocusReason.TabFocusReason)

    def select(self, index, animate=True):
        if self.buttons and animate and self.isVisible() and index != self.index:
            self._from_y = self._highlight_y()
            self._glide = 0.0
            self._animation.stop()
            self._animation.start()
        self.index = index
        for position, button in enumerate(self.buttons):
            button.setChecked(position == index)
            # Only the current section is a Tab stop; arrows move between them.
            button.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if position == index else Qt.FocusPolicy.ClickFocus
            )
        self.update()
        self.changed.emit(index)

    def paintEvent(self, event):
        if not self.buttons:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        button = self.buttons[self.index]
        pill = QRectF(button.x() + 1, self._highlight_y() + 1, button.width() - 2, button.height() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2A2825"))
        painter.drawRoundedRect(pill, 11, 11)
        painter.setBrush(UI_ACCENT)
        painter.drawRoundedRect(QRectF(pill.left() + 3, pill.center().y() - 8, 3, 16), 1.5, 1.5)


def make_card():
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(0)
    return card, layout


def make_caption(text):
    caption = QLabel(text)
    caption.setObjectName("caption")
    return caption


def make_hint(text, name="hint"):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def add_field(layout, title, control, hint=None):
    """A titled control inside a card, with an optional explanation under
    it. Returns the explanation label, or None."""
    box = QWidget()
    box_layout = QVBoxLayout(box)
    box_layout.setContentsMargins(10, 8, 10, 10)
    box_layout.setSpacing(7)
    if title:
        label = QLabel(title)
        label.setObjectName("rowTitle")
        box_layout.addWidget(label)
        control.setAccessibleName(title)
    box_layout.addWidget(control)
    hint_label = None
    if hint is not None:
        hint_label = make_hint(hint)
        box_layout.addWidget(hint_label)
    layout.addWidget(box)
    return hint_label


# --- Reminders ---------------------------------------------------------------------
class ReminderForm(QWidget):
    """Text, when and "every day": the form that adds one reminder."""

    added = pyqtSignal()
    REFRESH_MS = 20000

    def __init__(self, island):
        super().__init__()
        self.island = island
        self._delay = "15m"
        self._filling = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(10, 8, 10, 4)
        top_layout.setSpacing(8)
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText(tr("О чём напомнить?"))
        self.text_edit.setMaxLength(MAX_REMINDER_TEXT)
        self.text_edit.setAccessibleName(tr("О чём напомнить?"))
        self.text_edit.returnPressed.connect(self.add)
        self.text_edit.textEdited.connect(lambda _text: self.show_status(""))
        top_layout.addWidget(self.text_edit)
        when = QLabel(tr("Когда"))
        when.setObjectName("rowTitle")
        top_layout.addSpacing(2)
        top_layout.addWidget(when)

        chips_box = QWidget()
        chips = FlowLayout(6)
        chips_box.setLayout(chips)
        self.chips = {}
        for key, text in (
            ("5m", tr("Через 5 минут")),
            ("15m", tr("Через 15 минут")),
            ("30m", tr("Через 30 минут")),
            ("1h", tr("Через час")),
            ("day", tr("Завтра в это же время")),
        ):
            chip = QPushButton(text)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            chip.clicked.connect(lambda checked=False, k=key: self.choose_delay(k))
            chips.addWidget(chip)
            self.chips[key] = chip
        top_layout.addWidget(chips_box)

        exact = QHBoxLayout()
        exact.setSpacing(10)
        exact_label = QLabel(tr("Точное время"))
        exact_label.setObjectName("hint")
        self.when_edit = DateTimeField()
        self.when_edit.setAccessibleName(tr("Точное время"))
        self.when_edit.dateTimeChanged.connect(self._exact_time_edited)
        exact.addWidget(exact_label)
        exact.addWidget(self.when_edit)
        exact.addStretch()
        top_layout.addLayout(exact)
        layout.addWidget(top)

        self.daily = ToggleSwitch(tr("Каждый день"), tr("Как будильник: напомнит в это время каждый день"))
        self.daily.toggled.connect(lambda _checked: self.show_status(""))
        layout.addWidget(self.daily)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(10, 6, 10, 10)
        bottom_layout.setSpacing(12)
        self.status = make_hint("")
        bottom_layout.addWidget(self.status, 1)
        self.add_button = QPushButton(tr("Добавить"))
        self.add_button.setObjectName("saveButton")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add)
        bottom_layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(bottom)

        # Keeps "in 15 minutes" from going stale in the exact-time field.
        self._refresh = QTimer(self)
        self._refresh.setInterval(self.REFRESH_MS)
        self._refresh.timeout.connect(self._fill_exact_time)
        self._refresh.start()
        self.choose_delay("15m")

    def _delay_target(self):
        now = QDateTime.currentDateTime()
        if self._delay == "day":
            return now.addDays(1)
        minutes = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[self._delay]
        return now.addSecs(minutes * 60)

    def choose_delay(self, key):
        self._delay = key
        for chip_key, chip in self.chips.items():
            chip.setChecked(chip_key == key)
        self._fill_exact_time()
        self.show_status("")

    def _fill_exact_time(self):
        if self._delay is None:
            return
        self._filling = True
        self.when_edit.setDateTime(self._delay_target())
        self._filling = False

    def _exact_time_edited(self, _value):
        if self._filling:
            return
        self._delay = None
        for chip in self.chips.values():
            chip.setChecked(False)
        self.show_status("")

    def chosen_time(self):
        """The moment picked, as time.time() seconds."""
        if self._delay is not None:
            return self._delay_target().toMSecsSinceEpoch() / 1000.0
        moment = self.when_edit.dateTime()
        # Whole minutes: the field shows no seconds.
        return moment.toSecsSinceEpoch() - moment.time().second()

    def show_status(self, text, kind="hint"):
        self.status.setText(text)
        if self.status.objectName() != kind:
            self.status.setObjectName(kind)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def add(self):
        text = " ".join(self.text_edit.text().split())
        if not text:
            self.show_status(tr("Напишите, о чём напомнить"), "error")
            self.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        at = self.chosen_time()
        repeat = "daily" if self.daily.isChecked() else "none"
        if repeat == "daily":
            at = next_daily_time(at)
        elif at <= time.time():
            self.show_status(tr("Это время уже прошло"), "error")
            return
        try:
            store_reminder(self.island, new_reminder(text, at, repeat))
        except ValueError as error:
            self.show_status(str(error), "error")
            return
        except OSError as error:
            logging.exception("Could not save reminders")
            self.show_status(tr("Не удалось сохранить напоминания: {error}").format(error=error), "error")
            return
        self.text_edit.clear()
        self.show_status(tr("Напоминание добавлено"), "success")
        self.added.emit()


class ReminderRow(Interactive, QWidget):
    """One stored reminder: its text, when it is due and a delete button."""

    def __init__(self, reminder, on_delete):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 6, 7)
        layout.setSpacing(12)
        self.repeat = reminder["repeat"]
        icon = QWidget()
        icon.setFixedSize(30, 30)
        self.icon = icon
        layout.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = ElidedLabel(reminder["text"])
        title.setObjectName("rowTitle")
        title.setToolTip(reminder["text"])
        when = QLabel(format_reminder_time(reminder["at"], reminder["repeat"]))
        when.setObjectName("hint")
        texts.addWidget(title)
        texts.addWidget(when)
        layout.addLayout(texts, 1)
        delete = IconButton("close", tr("Удалить напоминание"), size=30, danger=True)
        delete.clicked.connect(lambda: on_delete(reminder["id"]))
        layout.addWidget(delete)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ROW_HOVER)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        tile = QRectF(self.icon.geometry())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#262420"))
        painter.drawRoundedRect(tile, 9, 9)
        draw_icon(
            painter, "repeat" if self.repeat == "daily" else "note",
            tile.adjusted(7, 7, -7, -7), QColor("#E9C9A0"),
        )


class ReminderList(QWidget):
    """The stored reminders, soonest first."""

    def __init__(self, island):
        super().__init__()
        self.island = island
        self.shown = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        layout.addLayout(self.rows)
        self.error = make_hint("", "error")
        self.error.setContentsMargins(10, 4, 10, 6)
        self.error.hide()
        layout.addWidget(self.error)
        self.reload()

    def reload(self):
        reminders = load_reminders()
        if reminders == self.shown:
            return
        self.shown = reminders
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        if not reminders:
            empty = QLabel(tr("Напоминаний пока нет"))
            empty.setObjectName("empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.addWidget(empty)
        for reminder in reminders:
            self.rows.addWidget(ReminderRow(reminder, self.delete))

    def delete(self, reminder_id):
        try:
            delete_reminder(self.island, reminder_id)
        except OSError as error:
            logging.exception("Could not save reminders")
            self.error.setText(tr("Не удалось сохранить напоминания: {error}").format(error=error))
            self.error.show()
            return
        self.error.hide()
        self.reload()


# --- Dialogs --------------------------------------------------------------------
class AnimatedDialog(QDialog):
    """A frameless dark dialog whose surface opens with a springy fade and
    scale and closes with a quick shrink.

    While it animates, the dialog paints one picture of itself, taken with
    the shadow, and hides its real content; so the shadow scales with the
    surface, nothing flickers and the window is never left half-transparent:
    the real content returns at full opacity as soon as the animation ends,
    is cut short, or the dialog is hidden.
    """

    # Room around the surface for its shadow.
    SIDE, TOP, BOTTOM = 28, 22, 36
    OPEN_MS, CLOSE_MS = 300, 180
    START_FALLBACK_MS = 80
    # (opacity, scale, downward offset) of the surface.
    SHOWN = (1.0, 1.0, 0.0)
    BEFORE_OPEN = (0.0, 0.92, 18.0)
    AFTER_CLOSE = (0.0, 0.955, 8.0)

    def __init__(self, island):
        super().__init__(
            island,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.island = island
        # Without this the corners around the rounded surface are painted solid.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.SIDE, self.TOP, self.SIDE, self.BOTTOM)
        self.surface = QWidget()
        outer.addWidget(self.surface)

        self._background = None
        self._background_key = None
        self._snapshot = None
        self._visual = self.SHOWN
        self._from = self.SHOWN
        self._to = self.SHOWN
        self._opening = False
        self._closing = False
        # Inside the real QDialog.done(): hiding then is the end, not a cut.
        self._finishing = False
        # From the first show until finished.
        self._open = False
        self._pending_result = QDialog.DialogCode.Rejected.value
        self._focus_widget = None
        self._needs_placing = True
        self._drag_offset = None
        # An opening waits for the first frame on screen (see _start_pending).
        self._start_pending = False
        self._start_fallback = QTimer(self)
        self._start_fallback.setSingleShot(True)
        self._start_fallback.setInterval(self.START_FALLBACK_MS)
        self._start_fallback.timeout.connect(self._start_animation)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._animate_frame)
        self._animation.finished.connect(self._animation_done)
        self._out_back = QEasingCurve(QEasingCurve.Type.OutBack)
        self._out_cubic = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._in_cubic = QEasingCurve(QEasingCurve.Type.InCubic)
        self._in_quad = QEasingCurve(QEasingCurve.Type.InQuad)

    # Hooks for the dialogs.
    def preferred_size(self, area):
        """The window size, shadow room included, on a screen area."""
        return self.sizeHint().boundedTo(area.size() - QSize(24, 24))

    def initial_focus(self):
        return None

    def paint_surface_extras(self, painter, rect, shape):
        """Paint on the surface background (already clipped to `shape`)."""

    def make_draggable(self, widget):
        widget.installEventFilter(self)

    def mark_for_placing(self):
        """Centre the dialog on the island's screen when it is next shown."""
        self._needs_placing = True

    # Placement.
    def _island_screen(self):
        screen = self.island.screen() if isinstance(self.island, QWidget) else None
        return screen or QApplication.primaryScreen()

    def _place(self):
        area = self._island_screen().availableGeometry()
        size = self.preferred_size(area)
        self.resize(size)
        left = area.left() + max(0, (area.width() - size.width()) // 2)
        top = area.top() + max(0, (area.height() - size.height()) // 2)
        self.move(left, top)

    # Painting.
    def _surface_rect(self):
        return QRectF(
            self.SIDE, self.TOP,
            self.width() - 2 * self.SIDE, self.height() - self.TOP - self.BOTTOM,
        )

    def _background_pixmap(self):
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), ratio)
        if key != self._background_key:
            pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self._surface_rect()
            paint_soft_shadow(painter, rect, SURFACE_RADIUS, 0.95, 0.75)
            shape = rounded_rect_path(rect, SURFACE_RADIUS)
            body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            body.setColorAt(0.0, QColor("#1B1A18"))
            body.setColorAt(1.0, QColor("#131211"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(body))
            painter.drawPath(shape)
            painter.save()
            painter.setClipPath(shape)
            self.paint_surface_extras(painter, rect, shape)
            painter.restore()
            edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            edge.setColorAt(0.0, QColor("#3A3732"))
            edge.setColorAt(1.0, QColor("#262421"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QBrush(edge), 1.0))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), SURFACE_RADIUS - 0.5, SURFACE_RADIUS - 0.5)
            painter.end()
            self._background = pixmap
            self._background_key = key
        return self._background

    def paintEvent(self, event):
        if self._start_pending:
            self._start_animation()
        painter = QPainter(self)
        if self._snapshot is None:
            painter.drawPixmap(0, 0, self._background_pixmap())
            return
        opacity, scale, offset = self._visual
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        center = self._surface_rect().center()
        painter.translate(center.x(), center.y() + offset)
        painter.scale(scale, scale)
        painter.translate(-center.x(), -center.y())
        painter.drawPixmap(0, 0, self._snapshot)

    # Animation.
    def _current_focus(self):
        widget = QApplication.focusWidget()
        return widget if widget is not None and self.surface.isAncestorOf(widget) else None

    def _begin(self, opening):
        self._animation.stop()
        if self._snapshot is None:
            self._focus_widget = self._current_focus() or self._focus_widget
            self._snapshot = self.grab()
            self.surface.hide()
            self._visual = self.BEFORE_OPEN if opening else self.SHOWN
        self._opening = opening
        self._closing = not opening
        self._from = self._visual
        self._to = self.SHOWN if opening else self.AFTER_CLOSE
        self._animation.setDuration(self.OPEN_MS if opening else self.CLOSE_MS)
        if opening and not self._from[0]:
            # A window takes a few frames to appear; started now, the first
            # tenth of the animation would play while nothing is visible.
            # So it starts with the first paint, or a moment later at most.
            self._start_pending = True
            self._start_fallback.start()
        else:
            self._start_animation()
        self.update()

    def _start_animation(self):
        self._start_pending = False
        self._start_fallback.stop()
        if self._snapshot is not None and self._animation.state() != QVariantAnimation.State.Running:
            self._animation.start()

    def _animate_frame(self, value):
        progress = float(value)
        if self._opening:
            fade = self._out_cubic.valueForProgress(min(1.0, progress / 0.6))
            grow = self._out_back.valueForProgress(progress)
            rise = self._out_cubic.valueForProgress(progress)
        else:
            fade = self._in_quad.valueForProgress(progress)
            grow = rise = self._in_cubic.valueForProgress(progress)
        (o0, s0, y0), (o1, s1, y1) = self._from, self._to
        self._visual = (o0 + (o1 - o0) * fade, s0 + (s1 - s0) * grow, y0 + (y1 - y0) * rise)
        self.update()

    def _restore_surface(self):
        self._start_pending = False
        self._animation.stop()
        self._opening = self._closing = False
        self._snapshot = None
        self._visual = self.SHOWN
        self.surface.show()
        self.update()

    def _restore_focus(self):
        target = self._focus_widget
        self._focus_widget = None
        try:
            usable = target is not None and target.isVisible() and target.isEnabled()
        except RuntimeError:  # the widget was deleted meanwhile
            usable = False
        if not usable:
            target = self.initial_focus()
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _animation_done(self):
        if self._opening:
            self._restore_surface()
            self._restore_focus()
        elif self._closing:
            # The real content comes back before the window is hidden; the
            # hide happens right away, before anything is painted.
            self._restore_surface()
            self._finish(self._pending_result)

    def _finish(self, result):
        self._open = False
        self._finishing = True
        try:
            super().done(result)
        finally:
            self._finishing = False

    # QDialog.
    def setVisible(self, visible):
        if visible and self._closing:
            # Shown again while closing: turn around instead of finishing.
            self._begin(opening=True)
            return
        if visible and not self.isVisible() and self._needs_placing:
            self._needs_placing = False
            self._place()
        super().setVisible(visible)

    def showEvent(self, event):
        super().showEvent(event)
        self._open = True
        self.raise_()
        self.activateWindow()
        self._begin(opening=True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._finishing:
            return
        self._focus_widget = self._current_focus() or self._focus_widget
        was_closing = self._closing
        self._restore_surface()
        if was_closing:
            # Hidden from outside while closing: still end properly.
            self._finish(self._pending_result)

    def done(self, result):
        self._pending_result = int(result)
        if not self.isVisible():
            self._restore_surface()
            self._finish(self._pending_result)
        elif not self._closing:
            self._begin(opening=False)

    def closeEvent(self, event):
        # close() animates like × and Esc; the window hides when it ends.
        event.ignore()
        if self._open:
            self.reject()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._background_key = None

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        if (
            kind == QEvent.Type.MouseMove
            and self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return True
        if kind == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
            self._drag_offset = None
            return True
        return super().eventFilter(watched, event)


def audio_apps():
    """Names (without ".exe") of the running programs that have an audio
    session in Windows: the ones whose own volume can be set."""
    try:
        import os
        import sys
        from pycaw.pycaw import AudioUtilities
        own = os.path.splitext(os.path.basename(sys.executable))[0].casefold()
        names = {}
        for session in AudioUtilities.GetAllSessions():
            try:
                process = session.Process
                name = os.path.splitext(process.name())[0] if process else ""
            except Exception:
                continue  # the program closed meanwhile
            folded = name.casefold()
            if name and folded not in (own, "python", "pythonw", "audiodg") and folded not in names:
                names[folded] = name
        return sorted(names.values(), key=str.casefold)
    except Exception:
        logging.info("Could not list the programs with sound", exc_info=True)
        return []


# Parts of Windows that have windows but no sound of their own.
_SHELL_PROCESSES = {
    "explorer", "applicationframehost", "shellexperiencehost", "searchhost", "searchapp",
    "startmenuexperiencehost", "textinputhost", "systemsettings", "lockapp", "dwm",
    "python", "pythonw", "defaultisland", "setup", "taskmgr", "shellhost", "widgets",
    "widgetservice", "gamebar", "nvidia overlay", "rundll32", "conhost",
}


def windowed_apps():
    """Names (without ".exe") of running programs with a visible window."""
    try:
        import ctypes
        import ctypes.wintypes
        import os
        import psutil
        user32 = ctypes.WinDLL("user32")
        proc = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.GetWindow.restype = ctypes.wintypes.HWND
        user32.GetWindowLongW.restype = ctypes.c_long
        pids = set()

        def visit(hwnd, _param):
            if (
                user32.IsWindowVisible(hwnd)
                and not user32.GetWindow(hwnd, 4)  # GW_OWNER: dialogs belong to their app
                and user32.GetWindowTextLengthW(hwnd) > 0
                and not user32.GetWindowLongW(hwnd, -20) & 0x80  # WS_EX_TOOLWINDOW
            ):
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(pid.value)
            return True

        user32.EnumWindows(proc(visit), 0)
        names = {}
        own = os.getpid()
        for pid in pids - {own}:
            try:
                name = os.path.splitext(psutil.Process(pid).name())[0]
            except psutil.Error:
                continue
            if name and name.casefold() not in _SHELL_PROCESSES:
                names.setdefault(name.casefold(), name)
        return sorted(names.values(), key=str.casefold)
    except Exception:
        logging.info("Could not list the running programs", exc_info=True)
        return []


def volume_choices(current):
    """(label, value) for the volume slider target: the usual players, the
    programs with sound now, the other running programs, the saved choice,
    and the whole system."""
    values = list(VOLUME_TARGETS[:-1])
    for name in audio_apps() + windowed_apps():
        if not any(name.casefold() == value.casefold() for value in values):
            values.append(name)
    if current and current != "Master Windows" and not any(current.casefold() == v.casefold() for v in values):
        values.append(current)
    labels = [(value[:1].upper() + value[1:], value) for value in values]
    labels.append((tr("Вся система (Windows)"), "Master Windows"))
    return labels


def no_auto_default(dialog):
    """Enter in a text field must not press a button of the dialog."""
    for button in dialog.findChildren(QPushButton):
        button.setAutoDefault(False)
        button.setDefault(False)


class SettingsWindow(AnimatedDialog):
    # The settings this dialog edits. Everything else in the configuration
    # (island position, timer, do-not-disturb) must survive a save untouched;
    # the monitor is saved only when it was changed here.
    EDITABLE_KEYS = (
        "language", "theme", "accent_color", "glass_opacity", "glass_tint",
        "glass_glint", "cover_backdrop", "left_zone", "center_zone",
        "right_zone", "idle_view", "weather_city", "calls_enabled",
        "music_only", "gear_button", "dnd_button", "volume_button", "repeat_button",
        "like_button", "hud_enabled", "clipboard_enabled", "recording_enabled",
        "privacy_dots_place", "lyrics_translate",
        "scale_collapsed", "scale_expanded", "collapsed_title",
        "lyrics_enabled", "lyrics_karaoke", "lyrics_offset", "real_visualizer", "track_animation", "volume_target",
        "timer_button_enabled", "mic_button_enabled", "privacy_dots_enabled",
        "layout_indicator_enabled", "battery_alerts_enabled",
        "device_alerts_enabled", "downloads_enabled", "notifications_enabled",
        "quick_buttons", "fps", "ghost_hotkey", "auto_start", "hide_fullscreen",
        "tray_enabled", "update_check_enabled", "auto_update",
    )
    POSITION_KEYS = ("pos_x", "pos_y", "pos_edge", "monitor")
    GLASS_THEMES = ("Liquid Glass", "Liquid Glass Light")
    PREFERRED = QSize(920, 720)
    STATUS_POLL_MS = 1500
    REMINDER_POLL_MS = 30000

    def __init__(self, island):
        super().__init__(island)
        self.draft = dict(island.config)
        # The stored choice, not what "music only" leaves in effect.
        self.draft["quick_buttons"] = list(dict.get(island.config, "quick_buttons", []))
        self.toggles = {}
        self.setWindowTitle(tr("Default Island · Настройки"))

        root = QHBoxLayout(self.surface)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar()
        self.make_draggable(self.sidebar.brand)
        root.addWidget(self.sidebar)

        column = QVBoxLayout()
        column.setContentsMargins(26, 18, 12, 16)
        column.setSpacing(10)
        root.addLayout(column, 1)

        header = QWidget()
        self.make_draggable(header)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 4, 0)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("heading")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("subheading")
        for label in (self.page_title, self.page_subtitle):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            titles.addWidget(label)
        header_layout.addLayout(titles, 1)
        column.addWidget(header)

        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        self.pages = []
        for icon, title, subtitle, build in (
            ("appearance", tr("Внешний вид"), tr("Тема, цвет и то, что показывает остров"), self.build_appearance),
            ("size", tr("Размер и положение"), tr("Насколько крупный остров и где он живёт"), self.build_size),
            ("music", tr("Музыка"), tr("Текст песен, визуализатор и громкость"), self.build_music),
            ("indicators", tr("Индикаторы"), tr("Что остров показывает, кроме музыки"), self.build_indicators),
            ("quick", tr("Кнопки"), tr("Какие кнопки есть на острове, или только музыка"), self.build_quick_buttons),
            ("reminders", tr("Напоминания"), tr("Остров напомнит в нужное время"), self.build_reminders),
            ("system", tr("Система"), tr("Язык, горячая клавиша, запуск и обновления"), self.build_system),
        ):
            self.sidebar.add(icon, title)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # The bar always takes its room, so every section has the same
            # width; with nothing to scroll it is disabled and draws nothing.
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            bar = scroll.verticalScrollBar()
            bar.rangeChanged.connect(lambda low, high, b=bar: b.setEnabled(high > low))
            body = QWidget()
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 14, 12)
            body_layout.setSpacing(6)
            build(body_layout)
            body_layout.addStretch()
            scroll.setWidget(body)
            self.stack.addWidget(scroll)
            self.pages.append((title, subtitle, scroll))

        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        column.addWidget(rule)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 4, 0)
        footer.setSpacing(10)
        self.footer_note = make_hint("")
        footer.addWidget(self.footer_note, 1)
        self.cancel_button = QPushButton(tr("Отмена"))
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.save_button = QPushButton(tr("Сохранить"))
        self.save_button.setObjectName("saveButton")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self.save_settings)
        footer.addWidget(self.save_button)
        column.addLayout(footer)

        # Created last so it comes last in the Tab order.
        close_button = IconButton("close", tr("Закрыть"))
        close_button.clicked.connect(self.reject)
        header_layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)

        self.sidebar.changed.connect(self.show_page)
        self.sidebar.finish()
        no_auto_default(self)
        self.sync_look()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(self.STATUS_POLL_MS)
        self._status_timer.timeout.connect(self.refresh_notification_hint)
        if self.island.notification_status() == "starting":
            self._status_timer.start()
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(self.REMINDER_POLL_MS)
        # The island removes reminders as they fire.
        self._reminder_timer.timeout.connect(self.reminder_list.reload)
        self._reminder_timer.start()

    # --- Frame -----------------------------------------------------------------
    def preferred_size(self, area):
        return self.PREFERRED.boundedTo(area.size() - QSize(24, 24))

    def initial_focus(self):
        return self.sidebar.buttons[self.sidebar.index]

    def paint_surface_extras(self, painter, rect, shape):
        strip = QRectF(rect.left(), rect.top(), SIDEBAR_WIDTH, rect.height())
        painter.fillRect(strip, QColor("#161514"))
        painter.fillRect(QRectF(strip.right() - 1, rect.top(), 1, rect.height()), QColor("#262421"))

    def show_page(self, index):
        title, subtitle, page = self.pages[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        self.stack.setCurrentIndex(index)
        self.footer_note.setObjectName("hint")
        self.footer_note.style().unpolish(self.footer_note)
        self.footer_note.style().polish(self.footer_note)
        self.footer_note.setText(
            tr("Напоминания сохраняются сразу, без кнопки «Сохранить»")
            if page is self.pages[5][2]
            else tr("Изменения вступят в силу после сохранения")
        )
        if self.isVisible() and self._snapshot is None:
            # A short fade-in of the new section.
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            fade = QVariantAnimation(page)
            fade.setDuration(170)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.valueChanged.connect(effect.setOpacity)
            fade.finished.connect(lambda: page.setGraphicsEffect(None))
            fade.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def show_error(self, text):
        self.footer_note.setText(text)
        self.footer_note.setObjectName("error")
        self.footer_note.style().unpolish(self.footer_note)
        self.footer_note.style().polish(self.footer_note)

    # --- Helpers -----------------------------------------------------------------
    def add_toggle(self, layout, key, text, hint=""):
        toggle = ToggleSwitch(text, hint)
        toggle.setChecked(bool(self.draft[key]))
        toggle.toggled.connect(lambda checked, k=key: self.draft.__setitem__(k, checked))
        layout.addWidget(toggle)
        self.toggles[key] = toggle
        return toggle

    def combo(self, items, current):
        """A SoftComboBox of (label, value) items, current value selected."""
        combo = SoftComboBox()
        for label, value in items:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def draft_accent(self):
        key = self.draft["accent_color"]
        if key == "Auto":
            color = getattr(self.island, "auto_accent_color", None)
            return QColor(color) if isinstance(color, QColor) and color.isValid() else QColor(DEFAULT_ACCENT)
        return QColor(ACCENT_PRESETS.get(key, DEFAULT_ACCENT))

    def sync_look(self, *_):
        """Show the zone picker as the island would look with the draft
        theme, accent and glass, and enable the glass controls for glass."""
        self.zone_picker.set_look(make_look(self.draft), self.draft_accent())
        glass = self.draft["theme"] in self.GLASS_THEMES
        for widget in (self.opacity_row, self.tint_row, self.toggles["glass_glint"]):
            widget.setEnabled(glass)
        self.glass_note.setVisible(not glass)

    # --- Sections ------------------------------------------------------------------
    def build_appearance(self, layout):
        layout.addWidget(make_caption(tr("Зоны острова")))
        card, card_layout = make_card()
        picker_box = QWidget()
        picker_layout = QVBoxLayout(picker_box)
        picker_layout.setContentsMargins(8, 8, 8, 6)
        picker_layout.setSpacing(8)
        self.zone_picker = ZonePicker(
            self.draft,
            ("left_zone", "center_zone", "right_zone"),
            (
                (("Album Art", tr("Обложка")), ("None", tr("Пусто"))),
                (
                    ("Track / Lyrics", tr("Трек и текст")),
                    ("Ping", tr("Пинг")),
                    ("CPU / RAM", tr("CPU и RAM")),
                    ("GPU", tr("Видеокарта")),
                    ("Time", tr("Время")),
                    ("Weather", tr("Погода")),
                ),
                (("Visualizer", tr("Визуализатор")), ("Repeat Button", tr("Повтор")), ("None", tr("Пусто"))),
            ),
        )
        picker_layout.addWidget(self.zone_picker)
        hint = make_hint(tr("Нажмите на зону, чтобы выбрать, что в ней показывать"))
        hint.setContentsMargins(4, 0, 4, 0)
        picker_layout.addWidget(hint)
        card_layout.addWidget(picker_box)
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Без музыки")))
        card, card_layout = make_card()
        self.idle_control = SegmentedControl(
            (
                ("clock", tr("Часы")),
                ("weather", tr("Погода")),
                ("system", tr("Система")),
                ("blank", tr("Пусто")),
            ),
            self.draft["idle_view"],
        )
        self.idle_hint = add_field(
            card_layout, tr("Когда ничего не играет, остров показывает"), self.idle_control, ""
        )
        self.idle_control.changed.connect(self.set_idle_view)
        self.city_edit = QLineEdit(self.draft["weather_city"])
        self.city_edit.setPlaceholderText(tr("Например, Киев"))
        self.city_edit.setMaxLength(MAX_CITY_LENGTH)
        self.city_edit.textChanged.connect(self.set_weather_city)
        add_field(
            card_layout, tr("Город для погоды"), self.city_edit,
            tr("Пусто — без погоды. Чтобы узнать погоду, название города отправляется на open-meteo.com"),
        )
        self.set_idle_view(self.draft["idle_view"])
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Тема")))
        card, card_layout = make_card()
        self.theme_control = SegmentedControl(
            zip(THEMES, (tr("Обычная"), "Liquid Glass", tr("Liquid Glass · светлая"))),
            self.draft["theme"],
        )
        self.theme_control.setAccessibleName(tr("Тема"))
        self.theme_control.changed.connect(self.set_theme)
        add_field(card_layout, "", self.theme_control)
        self.accent_picker = AccentPicker(self.draft["accent_color"])
        self.accent_picker.changed.connect(self.set_accent)
        add_field(card_layout, tr("Акцентный цвет"), self.accent_picker)
        self.add_toggle(
            card_layout, "cover_backdrop", tr("Обложка на фоне"),
            tr("Размытая обложка трека заполняет развёрнутый остров"),
        )
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Стекло")))
        card, card_layout = make_card()
        self.glass_note = make_hint(tr("Настройки стекла работают только в темах Liquid Glass"))
        self.glass_note.setContentsMargins(10, 6, 10, 2)
        card_layout.addWidget(self.glass_note)
        self.opacity_slider = SoftSlider(0, 100)
        self.opacity_slider.setValue(self.draft["glass_opacity"])
        self.opacity_row = SliderRow(
            tr("Плотность стекла"), self.opacity_slider, str,
            tr("Насколько стекло скрывает то, что под ним"),
        )
        self.opacity_slider.valueChanged.connect(lambda value: self.set_draft_live("glass_opacity", value))
        card_layout.addWidget(self.opacity_row)
        self.tint_slider = SoftSlider(0, 100)
        self.tint_slider.setValue(self.draft["glass_tint"])
        self.tint_row = SliderRow(
            tr("Оттенок"), self.tint_slider, str,
            tr("Насколько акцентный цвет окрашивает стекло"),
        )
        self.tint_slider.valueChanged.connect(lambda value: self.set_draft_live("glass_tint", value))
        card_layout.addWidget(self.tint_row)
        self.add_toggle(
            card_layout, "glass_glint", tr("Блик"),
            tr("По стеклу пробегает полоска света при наведении и смене трека"),
        )
        layout.addWidget(card)

    def set_idle_view(self, view):
        self.draft["idle_view"] = view
        if view == "weather" and not self.draft["weather_city"]:
            text = tr("Укажите город ниже — пока его нет, остров показывает часы")
        else:
            text = {
                "clock": tr("Время и дату; в развёрнутом острове ещё погоду и загрузку компьютера"),
                "weather": tr("Температуру и погоду в вашем городе"),
                "system": tr("Загрузку процессора и памяти"),
                "blank": tr("Маленькую пустую капсулу; наведите мышь, чтобы увидеть время"),
            }.get(view, "")
        self.idle_hint.setText(text)

    def set_weather_city(self, text):
        self.draft["weather_city"] = " ".join(text.split())
        self.set_idle_view(self.draft["idle_view"])

    def set_theme(self, theme):
        self.draft["theme"] = theme
        self.sync_look()

    def set_accent(self, key):
        self.draft["accent_color"] = key
        self.sync_look()

    def set_draft_live(self, key, value):
        self.draft[key] = value
        self.sync_look()

    def build_size(self, layout):
        layout.addWidget(make_caption(tr("Размер")))
        card, card_layout = make_card()
        low, high = SCALE_RANGE

        def percent(steps):
            return tr("{value} %").format(value=steps * 5)

        # The sliders count in steps of 5 %.
        for key, title in (
            ("scale_collapsed", tr("Свёрнутый остров")),
            ("scale_expanded", tr("Развёрнутый остров")),
        ):
            slider = SoftSlider(low // 5, high // 5, page_step=2)
            slider.setValue(round(self.draft[key] / 5))
            slider.valueChanged.connect(lambda steps, k=key: self.draft.__setitem__(k, steps * 5))
            card_layout.addWidget(SliderRow(title, slider, percent))
            setattr(self, f"{key}_slider", slider)
        self.title_control = SegmentedControl(
            (("full", tr("Целиком")), ("short", tr("Коротко"))), self.draft["collapsed_title"]
        )
        self.title_hint = add_field(
            card_layout, tr("Название трека в свёрнутом острове"), self.title_control, ""
        )
        self.title_control.changed.connect(self.set_collapsed_title)
        self.set_collapsed_title(self.draft["collapsed_title"])
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Положение")))
        card, card_layout = make_card()
        self.monitor_combo = SoftComboBox()
        self.fill_monitors()
        self.monitor_combo.currentIndexChanged.connect(self.set_monitor)
        add_field(
            card_layout, tr("Монитор"), self.monitor_combo,
            tr("На другом мониторе остров встанет сверху по центру"),
        )
        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(10, 4, 10, 4)
        buttons_layout.setSpacing(8)
        self.move_button = QPushButton(tr("Сменить положение"))
        self.move_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_button.clicked.connect(self.begin_positioning)
        self.center_button = QPushButton(tr("Вернуть в центр"))
        self.center_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.center_button.clicked.connect(self.reset_position)
        buttons_layout.addWidget(self.move_button)
        buttons_layout.addWidget(self.center_button)
        buttons_layout.addStretch()
        card_layout.addWidget(buttons)
        explanation = make_hint(tr(
            "Перетащите остров в любое место: он прилипает к центру и углам, "
            "а внизу экрана раскрывается вверх"
        ))
        explanation.setContentsMargins(10, 2, 10, 10)
        card_layout.addWidget(explanation)
        layout.addWidget(card)

    def set_collapsed_title(self, mode):
        self.draft["collapsed_title"] = mode
        self.title_hint.setText(
            tr("Остров растягивается, чтобы вместить название трека")
            if mode == "full"
            else tr("Остров не меняет размер, длинное название заканчивается «…»")
        )

    def fill_monitors(self):
        """List the connected screens; keep a saved one that is gone."""
        combo = self.monitor_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("Основной монитор"), "")
        for screen in QApplication.screens():
            name = screen.name()
            if not name or combo.findData(name) >= 0:
                continue
            size = screen.geometry().size()
            ratio = screen.devicePixelRatio()
            combo.addItem(f"{name} · {round(size.width() * ratio)}×{round(size.height() * ratio)}", name)
        current = self.draft["monitor"]
        if current and combo.findData(current) < 0:
            combo.addItem(tr("{name} · не подключён").format(name=current), current)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.blockSignals(False)

    def set_monitor(self, index):
        value = self.monitor_combo.itemData(index)
        if isinstance(value, str):
            self.draft["monitor"] = value

    def begin_positioning(self):
        self.hide()
        self.island.begin_positioning(self.positioning_finished)

    def positioning_finished(self):
        if not self._open:
            return  # the dialog was closed meanwhile
        # The island saved where it now is; that is the new starting point.
        for key in self.POSITION_KEYS:
            self.draft[key] = self.island.config[key]
        self.fill_monitors()
        self.mark_for_placing()
        self.show()

    def reset_position(self):
        self.island.reset_position()
        for key in ("pos_x", "pos_y", "pos_edge"):
            self.draft[key] = self.island.config[key]

    def build_music(self, layout):
        layout.addWidget(make_caption(tr("Текст песен")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "lyrics_enabled", tr("Синхронный текст песен"),
            tr("Чтобы найти текст, название трека и имя исполнителя отправляются на lrclib.net, "
               "а если там нет — на music.163.com"),
        )
        self.add_toggle(
            card_layout, "lyrics_translate", tr("Перевод текста"),
            tr("Перевод поющейся строки под ней в развёрнутом острове. "
               "Строки песни отправляются в Google Переводчик"),
        )
        self.add_toggle(
            card_layout, "lyrics_karaoke", tr("Подсветка слов"),
            tr("Спетые слова заливаются цветом песни, как в караоке"),
        )

        def shift_label(tenths):
            if tenths == 0:
                return tr("без сдвига")
            value = f"{tenths / 10:+.1f}"
            if language() != "en":
                value = value.replace(".", ",")
            return tr("{value} с").format(value=value)

        limit = LYRICS_OFFSET_LIMIT
        self.lyrics_offset_slider = SoftSlider(-limit, limit, page_step=5)
        self.lyrics_offset_slider.setValue(int(self.draft["lyrics_offset"]))
        self.lyrics_offset_slider.valueChanged.connect(
            lambda tenths: self.draft.__setitem__("lyrics_offset", int(tenths))
        )
        card_layout.addWidget(SliderRow(
            tr("Сдвиг текста"), self.lyrics_offset_slider, shift_label,
            tr("Если слова подсвечиваются раньше голоса, двигайте вправо; если позже — влево"),
        ))
        layout.addWidget(card)
        layout.addWidget(make_caption(tr("Анимация")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "real_visualizer", tr("Настоящий визуализатор"),
            tr("Полоски двигаются под звук, который сейчас играет"),
        )
        self.add_toggle(
            card_layout, "track_animation", tr("Анимация смены трека"),
            tr("Название и обложка плавно сменяются при переключении трека"),
        )
        layout.addWidget(card)
        layout.addWidget(make_caption(tr("Громкость")))
        card, card_layout = make_card()
        self.volume_combo = self.combo(volume_choices(self.draft["volume_target"]), self.draft["volume_target"])
        self.volume_combo.currentIndexChanged.connect(
            lambda index: self.draft.__setitem__("volume_target", self.volume_combo.itemData(index))
        )
        add_field(
            card_layout, tr("Ползунок громкости управляет"), self.volume_combo,
            tr("В списке запущенные программы. Громкость меняется, когда программа "
               "воспроизводит звук; нет нужной — запустите её и откройте настройки снова"),
        )
        layout.addWidget(card)

    def build_indicators(self, layout):
        layout.addWidget(make_caption(tr("На острове")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "timer_button_enabled", tr("Кнопка таймера"),
            tr("Кнопка и компактный таймер в развёрнутом острове"),
        )
        microphone = self.island.microphone_available()
        mic = self.add_toggle(
            card_layout, "mic_button_enabled", tr("Кнопка микрофона"),
            tr("Быстро выключить и включить микрофон") if microphone else tr("Микрофон не найден"),
        )
        mic.setEnabled(microphone)
        self.add_toggle(
            card_layout, "privacy_dots_enabled", tr("Точки приватности"),
            tr("Оранжевая — микрофон, зелёная — камера, как на iPhone"),
        )
        self.dots_place = SegmentedControl(
            (("inside", tr("В острове")), ("beside", tr("Рядом с островом"))),
            self.draft["privacy_dots_place"],
        )
        self.dots_place.changed.connect(lambda value: self.draft.__setitem__("privacy_dots_place", value))
        add_field(card_layout, tr("Где показывать точки"), self.dots_place)
        self.add_toggle(
            card_layout, "recording_enabled", tr("Запись экрана"),
            tr("Красная точка с таймером, пока экран записывают или показывают: "
               "OBS, Xbox Game Bar, «Ножницы», показ экрана в Discord или браузере"),
        )
        self.add_toggle(
            card_layout, "hud_enabled", tr("Громкость и яркость"),
            tr("Когда громкость или яркость меняется клавишами, остров показывает полоску"),
        )
        self.add_toggle(
            card_layout, "clipboard_enabled", tr("Буфер обмена"),
            tr("Остров показывает, что скопировано; кнопка «Буфер обмена» в разделе «Кнопки» "
               "хранит последние 10 копий. Пароли из менеджеров паролей не показываются"),
        )
        self.add_toggle(
            card_layout, "layout_indicator_enabled", tr("Раскладка клавиатуры"),
            tr("Показывает смену языка ввода и Caps Lock"),
        )
        layout.addWidget(card)
        layout.addWidget(make_caption(tr("Оповещения")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "battery_alerts_enabled", tr("Батарея"),
            tr("Зарядка подключена или заряд на исходе"),
        )
        self.add_toggle(
            card_layout, "device_alerts_enabled", tr("Устройства"),
            tr("Подключение наушников, колонок и других устройств"),
        )
        self.add_toggle(
            card_layout, "downloads_enabled", tr("Загрузки"),
            tr("Прогресс загрузок в браузере. Нужно расширение Default Island для браузера"),
        )
        self.add_toggle(card_layout, "notifications_enabled", tr("Уведомления Windows"))
        self.refresh_notification_hint()
        self.add_toggle(
            card_layout, "calls_enabled", tr("Звонки"),
            tr("Входящий звонок с кнопками «Принять» и «Отклонить» и таймер разговора в "
               "Discord, Telegram, WhatsApp, Teams, Zoom и других") + "\n"
            + tr("Входящие звонки видны, когда в Windows включены уведомления и есть доступ к ним"),
        )
        if toasts_enabled is not None and not toasts_enabled():
            warning = make_hint(tr(
                "Сейчас уведомления Windows выключены, поэтому остров не узнает о входящем "
                "звонке. Включите их в Параметрах Windows → Система → Уведомления"
            ), "error")
            warning.setContentsMargins(10, 2, 10, 4)
            card_layout.addWidget(warning)
            open_button = QPushButton(tr("Открыть настройки уведомлений"))
            open_button.setCursor(Qt.CursorShape.PointingHandCursor)
            open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:notifications")))
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 0, 10, 10)
            row_layout.addWidget(open_button)
            row_layout.addStretch()
            card_layout.addWidget(row)
        layout.addWidget(card)

    def refresh_notification_hint(self):
        status = self.island.notification_status()
        states = {
            "starting": tr("Проверяем доступ к уведомлениям…"),
            "ok": tr("Доступ к уведомлениям есть"),
            "denied": tr("Нет доступа: разрешите его в Параметрах Windows → Конфиденциальность → Уведомления"),
            "unavailable": tr("Недоступно в этой версии Windows"),
        }
        lines = [tr("Уведомления приложений появляются на острове")]
        if status in states:
            lines.append(states[status])
        self.toggles["notifications_enabled"].set_hint("\n".join(lines))
        if status != "starting" and self._status_timer_running():
            self._status_timer.stop()

    def _status_timer_running(self):
        timer = getattr(self, "_status_timer", None)
        return timer is not None and timer.isActive()

    def build_quick_buttons(self, layout):
        layout.addWidget(make_caption(tr("Режим")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "music_only", tr("Только музыка"),
            tr("Остров только для музыки: без таймера, кнопок, полки файлов, индикаторов и "
               "уведомлений. Звонки и напоминания остаются"),
        )
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Кнопки плеера")))
        card, card_layout = make_card()
        for key, title, hint in (
            ("gear_button", tr("Настройки"), tr("Без неё настройки открываются из значка в трее")),
            ("dnd_button", tr("Не беспокоить"), tr("Колокольчик: прячет уведомления")),
            ("volume_button", tr("Громкость"), tr("Колёсико мыши над островом всё равно меняет громкость")),
            ("repeat_button", tr("Повтор"), ""),
            ("like_button", tr("Лайк"), tr("Сердечко на SoundCloud и других сайтах с расширением")),
        ):
            self.add_toggle(card_layout, key, title, hint)
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Быстрые кнопки")))
        card, card_layout = make_card()
        self.quick_rows = {}
        for key, title, hint in (
            ("output", tr("Устройство вывода звука"), tr("Переключить колонки, наушники или монитор")),
            ("mic", tr("Микрофон"), tr("Выключить или включить микрофон")),
            ("screenshot", tr("Скриншот области"), tr("Выделить часть экрана и сделать снимок")),
            ("lock", tr("Блокировка экрана"), tr("Заблокировать компьютер одним нажатием")),
            ("note", tr("Напоминание"), tr("Быстро добавить напоминание")),
            ("clipboard", tr("Буфер обмена"), tr("Последние скопированные тексты и картинки; нажмите, чтобы скопировать снова")),
        ):
            row = CheckRow(key, title, hint)
            row.setChecked(key in self.draft["quick_buttons"])
            row.toggled.connect(self.update_quick_buttons)
            card_layout.addWidget(row)
            self.quick_rows[key] = row
        layout.addWidget(card)
        self.quick_empty = make_hint(tr("Ни одной кнопки не выбрано — строки кнопок в острове не будет"))
        self.quick_empty.setContentsMargins(6, 4, 6, 0)
        layout.addWidget(self.quick_empty)
        self.update_quick_buttons()

    def update_quick_buttons(self, *_):
        # Always stored in the fixed order, whatever order they were ticked in.
        self.draft["quick_buttons"] = [key for key in QUICK_BUTTONS if self.quick_rows[key].isChecked()]
        self.quick_empty.setVisible(not self.draft["quick_buttons"])

    def build_reminders(self, layout):
        layout.addWidget(make_caption(tr("Новое напоминание")))
        card, card_layout = make_card()
        self.reminder_form = ReminderForm(self.island)
        card_layout.addWidget(self.reminder_form)
        layout.addWidget(card)
        layout.addWidget(make_caption(tr("Запланированные")))
        card, card_layout = make_card()
        self.reminder_list = ReminderList(self.island)
        card_layout.addWidget(self.reminder_list)
        layout.addWidget(card)
        self.reminder_form.added.connect(self.reminder_list.reload)

    def build_system(self, layout):
        layout.addWidget(make_caption(tr("Язык")))
        card, card_layout = make_card()
        self.language_combo = self.combo([(name, code) for code, name in LANGUAGES], self.draft["language"])
        self.language_combo.currentIndexChanged.connect(
            lambda index: self.draft.__setitem__("language", self.language_combo.itemData(index))
        )
        add_field(card_layout, tr("Язык интерфейса"), self.language_combo, tr("Язык сменится после сохранения"))
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Плавность")))
        card, card_layout = make_card()
        self.fps_combo = SoftComboBox()
        self.fps_combo.setEditable(True)
        self.fps_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.fps_combo.lineEdit().setValidator(QIntValidator(0, 9999, self.fps_combo))
        for preset in FPS_PRESETS:
            self.fps_combo.addItem(str(preset), preset)
        self.fps_combo.setEditText(str(self.draft["fps"]))
        self.fps_combo.setMaximumWidth(220)
        self.fps_combo.lineEdit().editingFinished.connect(self.commit_fps)
        self.fps_combo.activated.connect(lambda _index: self.commit_fps())
        self.fps_hint = add_field(
            card_layout, tr("Кадров в секунду"), self.fps_combo,
            tr("От 30 до 240. Больше — плавнее, но выше нагрузка на компьютер"),
        )
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Режим призрака")))
        card, card_layout = make_card()
        self.hotkey_button = HotkeyRecorderButton(self.draft["ghost_hotkey"])
        self.hotkey_button.recorded.connect(self.set_hotkey)
        self.hotkey_button.problem.connect(self.hotkey_problem)
        self.hotkey_hint = add_field(
            card_layout, "", self.hotkey_button,
            tr("Сочетание клавиш, которое включает и выключает режим призрака. Нажмите на поле и задайте его"),
        )
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Запуск")))
        card, card_layout = make_card()
        self.add_toggle(card_layout, "auto_start", tr("Запускать вместе с Windows"))
        self.add_toggle(
            card_layout, "hide_fullscreen", tr("Скрывать в полноэкранном режиме"),
            tr("В играх и при просмотре видео на весь экран"),
        )
        self.add_toggle(
            card_layout, "tray_enabled", tr("Значок в области уведомлений"),
            tr("Возле часов на панели задач: оттуда можно открыть настройки и выйти"),
        )
        layout.addWidget(card)

        layout.addWidget(make_caption(tr("Обновления")))
        card, card_layout = make_card()
        self.add_toggle(
            card_layout, "update_check_enabled", tr("Проверять обновления"),
            tr("Остров сам сообщит, когда выйдет новая версия"),
        )
        self.add_toggle(
            card_layout, "auto_update", tr("Обновлять автоматически"),
            tr("Новая версия скачивается с GitHub и ставится сама, островок перезапускается"),
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 4, 10, 10)
        version = QLabel(tr("Версия {version}").format(version=APP_VERSION))
        version.setObjectName("hint")
        row_layout.addWidget(version, 1)
        self.update_button = QPushButton(tr("Проверить сейчас"))
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.clicked.connect(self.check_updates)
        self._update_button_reset = QTimer(self)
        self._update_button_reset.setSingleShot(True)
        self._update_button_reset.setInterval(2500)
        self._update_button_reset.timeout.connect(self._update_button_ready)
        row_layout.addWidget(self.update_button)
        card_layout.addWidget(row)
        layout.addWidget(card)

        layout.addSpacing(14)
        # The island has no window frame, so this is a way out besides the tray.
        quit_button = QPushButton(tr("Закрыть Default Island"))
        quit_button.setObjectName("quitButton")
        quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_button.clicked.connect(self.quit_app)
        layout.addWidget(quit_button)

    def commit_fps(self):
        """Take the typed frame rate, kept within FPS_RANGE."""
        text = self.fps_combo.currentText().strip()
        low, high = FPS_RANGE
        if not text.isdigit():
            self.fps_combo.setEditText(str(self.draft["fps"]))
            return
        typed = int(text)
        value = max(low, min(high, typed))
        self.draft["fps"] = value
        self.fps_combo.setEditText(str(value))
        self.fps_hint.setText(
            tr("Можно от 30 до 240 — поставили {value}").format(value=value)
            if value != typed
            else tr("От 30 до 240. Больше — плавнее, но выше нагрузка на компьютер")
        )

    def set_hotkey(self, hotkey):
        self.draft["ghost_hotkey"] = hotkey
        self.hotkey_hint.setObjectName("hint")
        self.hotkey_hint.style().unpolish(self.hotkey_hint)
        self.hotkey_hint.style().polish(self.hotkey_hint)
        self.hotkey_hint.setText(
            tr("Сочетание клавиш, которое включает и выключает режим призрака. Нажмите на поле и задайте его")
        )

    def hotkey_problem(self, message):
        self.hotkey_hint.setText(message)
        self.hotkey_hint.setObjectName("error")
        self.hotkey_hint.style().unpolish(self.hotkey_hint)
        self.hotkey_hint.style().polish(self.hotkey_hint)

    def check_updates(self):
        self.island.check_for_updates(manual=True)
        # The island reports the result itself; this only acknowledges the click.
        self.update_button.setEnabled(False)
        self.update_button.setText(tr("Проверяем…"))
        self._update_button_reset.start()

    def _update_button_ready(self):
        self.update_button.setEnabled(True)
        self.update_button.setText(tr("Проверить сейчас"))

    def quit_app(self):
        self.reject()
        QApplication.quit()

    # --- Saving --------------------------------------------------------------------
    def save_settings(self):
        self.hotkey_button.cancel_recording()
        self.commit_fps()
        island = self.island
        hotkey_ok = False
        try:
            parse_hotkey(self.draft["ghost_hotkey"])
            if not island.register_ghost_hotkey(self.draft["ghost_hotkey"]):
                raise OSError(tr("Это сочетание уже занято другой программой"))
            hotkey_ok = True
            set_auto_start(self.draft["auto_start"])
            updated = dict(island.config)
            updated.update({key: self.draft[key] for key in self.EDITABLE_KEYS})
            updated["quick_buttons"] = list(self.draft["quick_buttons"])
            if self.draft["monitor"] != island.config["monitor"]:
                # A new screen: start at its top centre.
                updated.update(monitor=self.draft["monitor"], pos_x=None, pos_y=None, pos_edge="top")
            updated = sanitize_config(updated)
            save_config(updated)
            island.config.update(updated)
            island.apply_settings()
        except (OSError, ValueError) as error:
            island.register_ghost_hotkey(island.config["ghost_hotkey"])
            try:
                set_auto_start(island.config["auto_start"])
            except OSError:
                logging.exception("Could not roll back the auto-start setting")
            if not hotkey_ok:
                # Show the problem where it can be fixed.
                self.sidebar.select(len(self.pages) - 1)
                self.hotkey_problem(str(error))
            self.show_error(tr("Не удалось сохранить: {error}").format(error=error))
            return
        self.accept()


class ReminderDialog(AnimatedDialog):
    """The compact "new reminder" dialog the island opens from its note
    button: the same form, the same look and animation. It closes after a
    reminder is added."""

    WIDTH = 480

    def __init__(self, island):
        super().__init__(island)
        self.setWindowTitle(tr("Новое напоминание"))
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(20, 16, 14, 12)
        layout.setSpacing(8)
        header = QWidget()
        self.make_draggable(header)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 0, 0, 0)
        title = QLabel(tr("Новое напоминание"))
        title.setObjectName("heading")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header_layout.addWidget(title, 1)
        layout.addWidget(header)
        self.form = ReminderForm(island)
        self.form.added.connect(self.accept)
        layout.addWidget(self.form)
        close_button = IconButton("close", tr("Закрыть"))
        close_button.clicked.connect(self.reject)
        header_layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        no_auto_default(self)

    def preferred_size(self, area):
        width = min(self.WIDTH + 2 * self.SIDE, area.width() - 24)
        height = self.layout().totalHeightForWidth(width)
        return QSize(width, min(height, area.height() - 24))

    def initial_focus(self):
        return self.form.text_edit
