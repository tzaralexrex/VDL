# Universal Video Downloader with Cookie Browser Support

from pathlib import Path
from datetime import datetime
from shutil import which
import subprocess
import sys
import re
import time
import traceback
import http.cookiejar
import ctypes
import importlib
import os
import sys

# --- Автоимпорт и автоустановка requests и packaging ---
def ensure_base_dependencies():
    """
    Проверяет и при необходимости устанавливает requests и packaging.
    Импортирует их глобально для дальнейшего использования.
    """

    base_packages = ["requests", "packaging"]
    for pkg in base_packages:
        try:
            importlib.import_module(pkg)
        except ImportError:
            print(f"[!] Необходимый модуль {pkg} не найден. Устанавливаем...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
    # Глобальный импорт
    global requests, packaging
    import requests
    import packaging
    from packaging.version import parse as parse_version
    packaging.parse_version = parse_version  # для удобства

ensure_base_dependencies()

try:
    from importlib.metadata import version as get_version, PackageNotFoundError
except ImportError:
    from importlib_metadata import version as get_version, PackageNotFoundError  # type: ignore

# --- Универсальный импорт и автообновление внешних модулей ---
def import_or_update(module_name, pypi_name=None, min_version=None):
    """
    Импортирует модуль, при необходимости устанавливает или обновляет его до актуальной версии с PyPI.
    :param module_name: имя для importlib.import_module
    :param pypi_name: имя пакета на PyPI (если отличается)
    :param min_version: минимальная версия (опционально)
    :return: импортированный модуль
    """
    pypi_name = pypi_name or module_name
    try:
        module = importlib.import_module(module_name)
        # Проверка актуальности версии
        try:
            resp = requests.get(f"https://pypi.org/pypi/{pypi_name}/json", timeout=5)
            if resp.ok:
                latest = resp.json()['info']['version']
                try:
                    installed = get_version(pypi_name)
                except PackageNotFoundError:
                    installed = getattr(module, '__version__', None)
                if installed and packaging.parse_version(installed) < packaging.parse_version(latest):
                    print(f"[!] Доступна новая версия {pypi_name}: {installed} → {latest}. Обновляем...")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", pypi_name])
                    module = importlib.reload(module)
        except Exception as e:
            print(f"[!] Не удалось проверить или обновить {pypi_name}: {e}")
        if min_version:
            try:
                installed = get_version(pypi_name)
            except PackageNotFoundError:
                installed = getattr(module, '__version__', None)
            if installed and packaging.parse_version(installed) < packaging.parse_version(min_version):
                print(f"[!] Требуется версия {min_version} для {pypi_name}, обновляем...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", f"{pypi_name}>={min_version}"])
                module = importlib.reload(module)
        return module
    except ImportError:
        print(f"[!] {pypi_name} не установлен. Устанавливаем...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pypi_name])
        return importlib.import_module(module_name)

# Импорт сторонних модулей через универсальную функцию
yt_dlp = import_or_update('yt_dlp')
browser_cookie3 = import_or_update('browser_cookie3')
colorama = import_or_update('colorama')
psutil = import_or_update('psutil')

from yt_dlp.utils import DownloadError
from browser_cookie3 import BrowserCookieError
from colorama import init, Fore, Style

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    tk = None

init(autoreset=True)  # инициализация colorama и автоматический сброс цвета после каждого print

DEBUG = 1  # Глобальная переменная для включения/выключения отладки
DEBUG_APPEND = 1 # 0 = перезаписывать лог при каждом запуске, 1 = дописывать к существующему логу

DEBUG_FILE = 'debug.log'

COOKIES_FB = 'cookies_fb.txt'
COOKIES_YT = 'cookies_yt.txt'
COOKIES_VI = 'cookies_vi.txt'   # Vimeo
COOKIES_RT = 'cookies_rt.txt'   # Rutube
COOKIES_VK = 'cookies_vk.txt'   # VK
COOKIES_GOOGLE = "cookies_google.txt"

MAX_RETRIES = 5  # Максимум попыток повторной загрузки при обрывах

debug_file_initialized = False

def cookie_file_is_valid(platform: str, cookie_path: str) -> bool:
    """
    Быстро проверяет, «жив» ли куки-файл.
    Для YouTube берём главную страницу, для Facebook — тоже.
    Возвращает True, если запрос прошёл без ошибки авторизации.
    """
    test_url = "https://www.youtube.com" if platform == "youtube" else "https://www.facebook.com"
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "cookiefile": cookie_path,
            "extract_flat": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(test_url, download=False)
        return True
    except DownloadError:
        return False
    except Exception:
        return False

def detect_ffmpeg_path():
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    local_path = os.path.join(script_dir, "ffmpeg.exe")
    log_debug(f"Поиск ffmpeg: Проверяем локальный путь: {local_path}")
    if os.path.isfile(local_path):
        log_debug(f"FFmpeg найден по локальному пути: {local_path}")
        return local_path
    system_path = which("ffmpeg")
    log_debug(f"Поиск ffmpeg: Проверяем системный PATH: {system_path}")
    if system_path and os.path.isfile(system_path):
        log_debug(f"FFmpeg найден в системном PATH: {system_path}")
        return system_path
    log_debug("FFmpeg не найден ни по локальному пути, ни в системном PATH.")
    return None

def log_debug(message):
    global debug_file_initialized

    if not DEBUG:
        return

    log_line = f"[{datetime.now()}] {message}\n"

    if not debug_file_initialized:
        mode = 'a' if DEBUG_APPEND else 'w'
        with open(DEBUG_FILE, mode, encoding='utf-8') as f:
            if DEBUG_APPEND:
                # Добавляем разделитель и заголовок нового сеанса только при дописывании
                f.write(f"\n{'='*60}\n--- Начинается новый сеанс отладки [{datetime.now()}] ---\n")
            # В режиме 'w' просто начинаем с первой строки
            f.write(log_line)
        debug_file_initialized = True
    else:
        with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)

def extract_platform_and_url(raw_url: str):
    url = raw_url.strip()

    patterns = {
        'youtube':  [r'(?:youtube\.com|youtu\.be)'],
        'facebook': [r'(?:facebook\.com|fb\.watch)'],
        'vimeo':    [r'(?:vimeo\.com)'],
        'rutube':   [r'(?:rutube\.ru)'],
        'vk':       [r'(?:vk\.com|vkontakte\.ru)'],
    }

    def clean_url_by_platform(platform: str, url: str) -> str:
        try:
            if platform == 'facebook':
                fb_patterns = [
                    r'/videos/(\d+)',
                    r'v=(\d+)',
                    r'/reel/(\d+)',
                    r'/watch/\?v=(\d+)',
                    r'/video.php\?v=(\d+)'
                ]
                for pattern in fb_patterns:
                    match = re.search(pattern, url)
                    if match:
                        video_id = match.group(1)
                        return f"https://m.facebook.com/watch/?v={video_id}&_rdr"
                raise ValueError(Fore.RED + "Не удалось распознать ID видео Facebook" + Style.RESET_ALL)

            elif platform == 'vk':
                match = re.search(r'(video[-\d]+_\d+)', url)
                return f"https://vk.com/{match.group(1)}" if match else url

            elif platform == 'vimeo':
                return url.split('#')[0]

            elif platform == 'rutube':
                return url.split('?')[0]

        except Exception as e:
            log_debug(f"Ошибка при очистке URL для {platform}: {e}")
        return url

    # перебираем в фиксированном порядке (обычная проверка «известных» платформ)
    for platform, pats in patterns.items():
        for pat in pats:
            if re.search(pat, url, re.I):
                cleaned_url = clean_url_by_platform(platform, url)
                log_debug(f"Определена платформа: {platform} для URL: {cleaned_url}")
                return platform, cleaned_url

    # ничего не совпало - возвращаем «generic»
    log_debug("Платформа не опознана, пробуем generic-режим.")
    return "generic", url

    raise ValueError(Fore.RED + "Не удалось определить платформу (YouTube, Facebook, Vimeo, Rutube, VK)" + Style.RESET_ALL)


def save_cookies_to_netscape_file(cj: http.cookiejar.CookieJar, filename: str):
    """
    Сохраняет объект CookieJar в файл Netscape-формата, который может быть использован yt-dlp.
    """
    try:
        mozilla_cj = http.cookiejar.MozillaCookieJar(filename)
        for cookie in cj:
            mozilla_cj.set_cookie(cookie)
        mozilla_cj.save(ignore_discard=True, ignore_expires=True)
        print(Fore.GREEN + f"Куки успешно сохранены в файл: {filename}" + Style.RESET_ALL)
        log_debug(f"Куки успешно сохранены в файл: {filename}")
        return True
    except Exception as e:
        print(Fore.RED + f"Ошибка при сохранении куков в файл {filename}: {e}" + Style.RESET_ALL)
        log_debug(f"Ошибка при сохранении куков в файл {filename}:\n{traceback.format_exc()}")
        return False

def get_cookies_for_platform(platform: str, cookie_file: str, force_browser: bool = False) -> str | None:
    """
    Пытается получить куки: сначала из файла, затем из браузера.
    Возвращает путь к файлу куков, если куки успешно получены/загружены, иначе None.
    """

    # 1. Попытка загрузить куки из существующего файла
    if os.path.exists(cookie_file):
        if not force_browser:
            if cookie_file_is_valid(platform, cookie_file):
                print(Fore.CYAN + f"Пытаемся использовать куки из файла {cookie_file} для {platform.capitalize()}." + Style.RESET_ALL)
                log_debug(f"Файл куков '{cookie_file}' существует и прошёл проверку. Используем его.")
                return cookie_file
            else:
                print(f"[!] Файл {cookie_file} найден, но авторизация не удалась. Пробуем свежие куки из браузера…")
                log_debug(f"Файл {cookie_file} найден, но не прошёл проверку. Переходим к извлечению из браузера.")
        else:
            print(Fore.CYAN + f"Принудительный режим: пропускаем проверку и извлекаем куки из браузера." + Style.RESET_ALL)

    # 2. Попытка извлечь куки из браузера
    browsers_to_try = ['chrome', 'firefox']
    browser_functions = {
        'chrome': browser_cookie3.chrome,
        'firefox': browser_cookie3.firefox,
    }

    platform_domains = {
        'youtube':  ['youtube.com', 'google.com'],  # fallback
        'facebook': ['facebook.com'],
        'vimeo':    ['vimeo.com'],
        'rutube':   ['rutube.ru'],
        'vk':       ['vk.com'],
    }

    print(Fore.YELLOW + f"Примечание: Для автоматического получения куков из браузера (Chrome/Firefox), "
          f"убедитесь, что он закрыт или неактивен." + Style.RESET_ALL)

    domains = platform_domains.get(platform, [])
    extracted_cj = None

    for browser in browsers_to_try:
        try:
            print(Fore.GREEN + f"Пытаемся получить куки для {platform.capitalize()} из браузера ({browser})." + Style.RESET_ALL)
            log_debug(f"Попытка получить куки для {platform.capitalize()} из браузера: {browser}")

            for domain in domains:
                log_debug(f"Пробуем домен {domain} в {browser}")
                extracted_cj = browser_functions[browser](domain_name=domain)
                if extracted_cj:
                    break

            if extracted_cj:
                print(Fore.GREEN + f"Куки для {platform.capitalize()} успешно получены из {browser.capitalize()}." + Style.RESET_ALL)
                log_debug(f"Куки для {platform.capitalize()} успешно получены из {browser.capitalize()}.")
                if save_cookies_to_netscape_file(extracted_cj, cookie_file):
                    return cookie_file
                else:
                    print(Fore.RED + "Не удалось сохранить извлеченные куки в файл. Продолжаем без них." + Style.RESET_ALL)
                    log_debug("Не удалось сохранить извлеченные куки в файл.")
                    return None

        except BrowserCookieError as e:
            print(Fore.RED + f"Не удалось получить куки из браузера ({browser}) для {platform.capitalize()}: {e}" + Style.RESET_ALL)
            log_debug(f"BrowserCookieError при получении куков из {browser} для {platform.capitalize()}:\n{traceback.format_exc()}")
        except Exception as e:
            print(Fore.RED + f"Произошла непредвиденная ошибка при попытке получить куки из {browser} для {platform.capitalize()}: {e}" + Style.RESET_ALL)
            log_debug(f"Общая ошибка при получении куков из {browser} для {platform.capitalize()}:\n{traceback.format_exc()}")

    print(Fore.YELLOW + f"Не удалось автоматически получить куки для {platform.capitalize()}. "
                        f"Для загрузки приватных видео {platform.capitalize()}, пожалуйста, "
                        f"экспортируйте куки в файл {cookie_file} вручную (например, с помощью расширения браузера)." + Style.RESET_ALL)
    log_debug(f"Автоматическое получение куков для {platform.capitalize()} не удалось.")
    return None



def get_video_info(url, platform, cookie_file_path=None):
    ydl_opts = {'quiet': True, 'skip_download': True}
    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        log_debug(f"get_video_info: Используем cookiefile: {cookie_file_path}")

    log_debug(f"get_video_info: Запрос информации для URL: {url} с опциями: {ydl_opts}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        log_debug(f"get_video_info: Получена информация о видео. Title: {info.get('title', 'N/A')}, ID: {info.get('id', 'N/A')}")
        if cookie_file_path:
            info['__cookiefile__'] = cookie_file_path
        return info

def safe_get_video_info(url: str, platform: str):
    cookie_map = {
        "youtube":  COOKIES_YT,
        "facebook": COOKIES_FB,
        "vimeo":    COOKIES_VI,
        "rutube":   COOKIES_RT,
        "vk":       COOKIES_VK,
    }

    # Платформы, которые явно поддерживаются
    if platform in cookie_map:
        cookie_path = cookie_map[platform]
        current_cookie = get_cookies_for_platform(platform, cookie_path)

        for attempt in ("file", "browser", "none"):
            try:
                return get_video_info(url, platform, current_cookie if attempt != "none" else None)
            except DownloadError as err:
                err_l = str(err).lower()
                need_login = any(x in err_l for x in ("login", "403", "private", "sign in", "unauthorized"))
                if not need_login:
                    raise
                if attempt == "file":
                    current_cookie = get_cookies_for_platform(platform, cookie_path, force_browser=True)
                elif attempt == "browser":
                    current_cookie = None
                else:
                    print(f"\nВидео требует авторизации, а получить рабочие куки автоматически не удалось.\n"
                          f"Сохраните их вручную и положите файл сюда: {cookie_path}\n")
                    sys.exit(1)

    else:
        # ----- generic -----
        # порядок попыток: Google‑куки → VK‑куки → пользовательский cookies.txt → без куков
        fallback_cookies = [
            COOKIES_GOOGLE,      # cookies_google.txt
            COOKIES_VK,          # cookies_vk.txt
            "cookies.txt",       # универсальное имя
        ]
    
        for cookie_file in fallback_cookies + [None]:          # None = без куков
            if cookie_file and not Path(cookie_file).is_file():
                # файла нет — пропускаем тихо
                continue
            try:
                if cookie_file:
                    log_debug(f"generic: пробуем куки «{cookie_file}»")
                    cookie_path = cookie_file                  # берём файл как есть, без проверки домена
                else:
                    log_debug("generic: пробуем без куков")
                    cookie_path = None
    
                return get_video_info(url, platform, cookie_path)
    
            except DownloadError as err:
                err_l = str(err).lower()
                need_login = any(x in err_l for x in
                                 ("login", "403", "private", "sign in", "unauthorized"))
                if not need_login:
                    raise          # ошибка не про авторизацию → пробрасываем
                continue           # иначе переходим к след. cookie‑файлу
    
        # --- все попытки провалились ---
        from urllib.parse import urlparse
        site_domain = urlparse(url).hostname or "неизвестный-домен"
    
        print(
            f"\nВаш сайт ({site_domain}) требует авторизации, а попытки с Google/VK cookies не помогли.\n"
            f"Авторизуйтесь на этом сайте в браузере и сохраните куки в файл "
            f"cookies.txt (Netscape-формат). Затем поместите его рядом со скриптом и повторите попытку.\n"
            f"Если ничего не помогло, возможно, ваш сайт просто не поддерживается. Извините.\n"
        )
    log_debug(f"generic: авторизация не удалась даже с cookies.txt для {site_domain}")
    sys.exit(1)


def choose_format(formats):
    """
    Возвращает кортеж:
        (video_id, audio_id|None,
         desired_ext, video_ext, audio_ext,
         video_codec, audio_codec)
    """
    # --------------------------- сортировка ---------------------------
    video_formats = [f for f in formats if f.get("vcodec") != "none"]
    audio_formats = [f for f in formats if f.get("acodec") != "none"
                     and f.get("vcodec") == "none"]

    video_formats.sort(key=lambda f: (f.get("height") or 0,
                                      f.get("format_id", "")))
    audio_formats.sort(key=lambda f: (
        f.get("abr") or 0,
        '-drc' in f.get("format_id", "")
    ))

    # --------------------------------------------------------
    # 1. Потоки-манифесты (DASH / HLS / Smooth Streaming …)
    # --------------------------------------------------------
    manifest_exts = {"mpd", "m3u8", "ism", "f4m"}
    if any(v.get("ext") in manifest_exts for v in video_formats):
        best_vid = video_formats[-1]            # уже самый «толстый»
        print(
            Fore.YELLOW
            + f"\nОбнаружен поток-манифест ({best_vid['ext']}). "
              "По умолчанию будет скачан bestvideo+bestaudio (или best),"
              " а объединение выполнит сам yt-dlp."
            + Style.RESET_ALL
        )
        log_debug("Manifest stream detected → bestvideo+bestaudio/best")

        return (
            "bestvideo+bestaudio/best",      # video_id – спецстрока
            None,                            # audio_id не нужен
            "mp4",                           # желаемый контейнер
            best_vid["ext"], "", "", ""      # остальное – служебно
        )

    # --------------------------------------------------------
    # 2. Выводим пользователю таблицу форматов
    # --------------------------------------------------------
    print("\n" + Fore.MAGENTA + "Доступные видеоформаты:" + Style.RESET_ALL)
    for i, f in enumerate(video_formats):
        fmt_id = f.get("format_id", "?")
        ext    = f.get("ext", "?")
        height = f.get("height")
        note   = f.get("format_note", "")
        vcodec = f.get("vcodec", "?")
        rez    = f"{height}p" if height else note or "?p"
        print(f"{i}: {fmt_id}  –  {ext}  –  {rez}  –  {vcodec}")

    default_video = len(video_formats) - 1
    v_choice = input(
        Fore.CYAN + f"Выберите видеоформат (Enter = {default_video}): "
        + Style.RESET_ALL
    ).strip()
    v_choice = (int(v_choice) if v_choice.isdigit()
                and 0 <= int(v_choice) < len(video_formats)
                else default_video)

    video_fmt = video_formats[v_choice]
    log_debug(
        f"Выбран видеоформат: id={video_fmt['format_id']}, "
        f"ext={video_fmt['ext']}, vcodec={video_fmt.get('vcodec','')}"
    )

    # --------------------------------------------------------
    # 3. Аудио
    # --------------------------------------------------------
    audio_fmt = None
    if not audio_formats:
        print(
            Fore.YELLOW + "\nОтдельных аудиопотоков нет – "
            "будет использован звук, встроенный в видео."
            + Style.RESET_ALL
        )
        input(Fore.CYAN + "Enter для продолжения…" + Style.RESET_ALL)
    else:
        print("\n" + Fore.MAGENTA + "Доступные аудиоформаты:" + Style.RESET_ALL)
        for i, f in enumerate(audio_formats):
            fmt_id = f.get("format_id", "?")
            ext    = f.get("ext", "?")
            abr    = f.get("abr") or "?"
            acodec = f.get("acodec", "?")
            print(f"{i}: {fmt_id}  –  {ext}  –  {abr} kbps  –  {acodec}")

        # --- таблица совместимости контейнер/аудио-расширения ---
        compat = {
            "mp4":  {"m4a", "mp3", "aac", "mp4"},
            "webm": {"webm", "opus", "ogg"},
            "mkv":  {"m4a", "mp3", "aac", "webm", "mp4"},
            "avi":  {"mp3", "aac"},
        }
        video_ext = video_fmt["ext"].lower()
        allowed   = compat.get(video_ext,
                               {af["ext"] for af in audio_formats})

        # выбираем первый совместимый по умолчанию
        default_audio = next(
            (i for i, f in enumerate(audio_formats)
             if f.get("ext", "").lower() in allowed),
            len(audio_formats) - 1
        )

        while True:
            a_choice = input(
                Fore.CYAN + f"Выберите аудио (Enter = {default_audio}): "
                + Style.RESET_ALL
            ).strip()
            a_choice = (int(a_choice) if a_choice.isdigit()
                        and 0 <= int(a_choice) < len(audio_formats)
                        else default_audio)

            audio_fmt = audio_formats[a_choice]
            audio_ext = audio_fmt.get("ext", "").lower()

            if audio_ext in allowed:
                break

            print(
                Fore.RED + f"Несовместимо: видео {video_ext} ≠ аудио {audio_ext}."
                " Выберите другой формат." + Style.RESET_ALL
            )

        log_debug(
            f"Выбран аудио: id={audio_fmt['format_id']}, "
            f"ext={audio_ext}, acodec={audio_fmt.get('acodec','')}"
        )

    # --------------------------------------------------------
    # 4. Возвращаем выбор
    # --------------------------------------------------------
    return (
        video_fmt["format_id"],
        audio_fmt["format_id"] if audio_fmt else None,
        video_fmt["ext"],
        video_fmt["ext"],
        audio_fmt.get("ext", "") if audio_fmt else "",
        video_fmt.get("vcodec", ""),
        audio_fmt.get("acodec", "") if audio_fmt else ""
    )

def ask_and_select_subtitles(info):
    subtitles_info = info.get('subtitles') or {}
    auto_info = info.get('automatic_captions') or {}

    embedded_langs = list(subtitles_info.keys())
    auto_langs = list(auto_info.keys())

    use_embedded = bool(embedded_langs)
    use_auto = bool(auto_langs)

    selected_langs = []
    download_automatics = set()

    if not use_embedded and not use_auto:
        print(Fore.YELLOW + "Субтитры к видео не обнаружены." + Style.RESET_ALL)
        log_debug("Субтитры не найдены.")
        return None

    if use_embedded:
        print(Fore.MAGENTA + "\nК видео найдены вложенные субтитры:" + Style.RESET_ALL)
        numbered = []
        for idx, lang in enumerate(sorted(embedded_langs), start=1):
            formats = sorted({e['ext'] for e in subtitles_info[lang]})
            print(f"{idx}. {lang} — Доступные форматы: {', '.join(formats)}")
            numbered.append(lang)

        if use_auto:
            intersect = [lang for lang in numbered if lang in auto_info]
            if intersect:
                print(Fore.CYAN + f"\nТакже доступны автоматические субтитры для: {', '.join(intersect)}" + Style.RESET_ALL)

        sel = input(Fore.CYAN + "\nВведите номера или коды языков (например, '1,3' или 'en,ru'), '-' — не скачивать субтитры (по умолчанию: 0 — все): " + Style.RESET_ALL).strip()
        if sel == '-':
            print(Fore.YELLOW + "Загрузка субтитров отменена пользователем." + Style.RESET_ALL)
            return None

        if not sel or sel == '0':
            selected_langs = numbered
        else:
            parts = [s.strip() for s in re.split(r'[,\s]+', sel) if s.strip()]
            for p in parts:
                if p.isdigit():
                    i = int(p) - 1
                    if 0 <= i < len(numbered):
                        selected_langs.append(numbered[i])
                elif p in numbered:
                    selected_langs.append(p)

        selected_langs = sorted(set(selected_langs))
        if not selected_langs:
            print(Fore.YELLOW + "Неверный выбор. Субтитры загружены не будут." + Style.RESET_ALL)
            log_debug("Пустой или неверный выбор языков субтитров.")
            return None

        for lang in selected_langs:
            if lang in auto_info:
                if input(Fore.CYAN + f"Скачать автоматические субтитры для языка '{lang}'? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip() == '1':
                    download_automatics.add(lang)

        write_automatic = bool(download_automatics)

    elif use_auto:
        print(Fore.MAGENTA + "К видео доступны только автоматические субтитры для языков:" + Style.RESET_ALL)
        print(', '.join(sorted(auto_langs)))
        sel = input(Fore.CYAN + "Введите языки для загрузки автоматических субтитров (например, 'en,ru'), '-' — не загружать (по умолчанию: en, ru): " + Style.RESET_ALL).strip()
        if sel == '-':
            print(Fore.YELLOW + "Загрузка субтитров отменена пользователем." + Style.RESET_ALL)
            return None
        elif not sel:
            default_langs = ['en', 'ru']
            selected_langs = [lang for lang in default_langs if lang in auto_langs]
            print(Fore.GREEN + f"По умолчанию выбраны автоматические субтитры: {', '.join(selected_langs)}" + Style.RESET_ALL)
        elif sel == '0':
            selected_langs = auto_langs
        else:
            parts = [s.strip() for s in re.split(r'[,\s]+', sel) if s.strip()]
            for lang in parts:
                if lang in auto_langs:
                    selected_langs.append(lang)
        selected_langs = sorted(set(selected_langs))
        if not selected_langs:
            print(Fore.YELLOW + "Выбранные языки не найдены среди автоматических субтитров." + Style.RESET_ALL)
            return None
        download_automatics.update(selected_langs)
        write_automatic = True

    print(Fore.GREEN + f"Выбранные языки субтитров: {', '.join(selected_langs)}" + Style.RESET_ALL)
    log_debug(f"Выбранные субтитры: {selected_langs}, автоматические: {sorted(download_automatics)}")

    # Собираем все доступные форматы для выбранных языков
    available_formats = set()
    for lang in selected_langs:
        if lang in subtitles_info:
            available_formats.update(e['ext'] for e in subtitles_info[lang])
        if lang in auto_info:
            available_formats.update(e['ext'] for e in auto_info[lang])
    if not available_formats:
        available_formats = {'srt', 'vtt'}  # fallback
    
    default_format = 'srt' if 'srt' in available_formats else sorted(available_formats)[0]
    print(Fore.CYAN + f"\nВ каком формате сохранить субтитры? ({'/'.join(sorted(available_formats))}, Enter = {default_format}): " + Style.RESET_ALL)
    sub_format = input().strip().lower()
    if not sub_format or sub_format not in available_formats:
        sub_format = default_format
    
    print(Fore.GREEN + f"Выбранный формат субтитров: {sub_format}" + Style.RESET_ALL)
    log_debug(f"Выбранный формат субтитров: {sub_format}")
    
    return {
        'writesubtitles': use_embedded,
        'writeautomaticsub': write_automatic,
        'subtitleslangs': selected_langs,
        'subtitlesformat': sub_format
    }

def select_output_folder():
    print("\n" + Fore.CYAN + "Выберите папку для сохранения видео" + Style.RESET_ALL)
    
    # Сохраняем хэндл активного окна
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    current_thread_id = kernel32.GetCurrentThreadId()
    foreground_window = user32.GetForegroundWindow()
    user32.AttachThreadInput(user32.GetWindowThreadProcessId(foreground_window, None), current_thread_id, True)

    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Выберите папку")
    root.destroy()

    # Возвращаем фокус обратно к окну
    user32.SetForegroundWindow(foreground_window)
    return folder

def ask_output_filename(default_name, output_path, output_format):
    """
    Запрашивает имя файла, проверяет существование и предлагает варианты при совпадении.
    """
    current_name = default_name
    log_debug(f"Предлагаемое имя файла (по умолчанию): {default_name}")
    while True:
        proposed_full_path = os.path.join(output_path, current_name + '.' + output_format)
        log_debug(f"Проверка имени файла: {proposed_full_path}")
        
        print(f"\n{Fore.MAGENTA}Предлагаемое имя файла: {Fore.GREEN}{current_name}.{output_format}{Style.RESET_ALL}")
        name_input = input(Fore.CYAN + "Введите имя файла (Enter — оставить по умолчанию): " + Style.RESET_ALL).strip()
        
        if not name_input: # Пользователь нажал Enter, использует предложенное имя
            if os.path.exists(proposed_full_path):
                print(Fore.YELLOW + f"Файл '{current_name}.{output_format}' уже существует." + Style.RESET_ALL)
                log_debug(f"Файл '{proposed_full_path}' существует. Запрос действия.")
                choice = input(Fore.CYAN + "Перезаписать (0), выбрать другое имя (1), или добавить индекс (2)? (по умолчанию: 2): " + Style.RESET_ALL).strip() 
                
                if choice == '0':
                    print(Fore.RED + f"ВНИМАНИЕ: Файл '{current_name}.{output_format}' будет перезаписан." + Style.RESET_ALL)
                    log_debug(f"Выбрано: перезаписать файл '{proposed_full_path}'.")
                    return current_name # Возвращаем текущее имя для перезаписи
                elif choice == '1':
                    # Предлагаем пользователю ввести новое имя
                    print(Fore.CYAN + "Введите новое имя файла: " + Style.RESET_ALL)
                    new_name = input().strip()
                    log_debug(f"Выбрано: ввести новое имя. Введено: '{new_name}'.")
                    if new_name:
                        current_name = new_name
                    else: # Если пользователь ничего не ввел, возвращаемся к началу цикла
                        print(Fore.YELLOW + "Имя файла не было введено. Попробуйте снова." + Style.RESET_ALL)
                        log_debug("Новое имя файла не введено. Повторный запрос.")
                        continue
                else: # '2' или любой другой некорректный ввод - добавляем индекс
                    idx = 1
                    while True:
                        indexed_name = f"{current_name}_{idx}"
                        indexed_full_path = os.path.join(output_path, indexed_name + '.' + output_format)
                        log_debug(f"Выбрано: добавить индекс. Проверка индексированного имени: {indexed_full_path}")
                        if not os.path.exists(indexed_full_path):
                            print(Fore.GREEN + f"Файл будет сохранен как '{indexed_name}.{output_format}'." + Style.RESET_ALL)
                            log_debug(f"Выбрано: использовать индексированное имя '{indexed_name}'.")
                            return indexed_name
                        idx += 1
            else:
                log_debug(f"Файл '{proposed_full_path}' не существует. Используем это имя.")
                return current_name # Файл не существует, можно использовать это имя
        else: # Пользователь ввел новое имя
            new_name = name_input
            new_full_path = os.path.join(output_path, new_name + '.' + output_format)
            log_debug(f"Пользователь ввел новое имя: '{new_name}'. Проверка: {new_full_path}")
            if os.path.exists(new_full_path):
                print(Fore.YELLOW + f"Файл '{new_full_path}' уже существует." + Style.RESET_ALL)
                log_debug(f"Новое имя '{new_full_path}' уже существует. Запрос действия.")
                choice = input(Fore.CYAN + "Перезаписать (0), выбрать другое имя (1), или добавить индекс (2)? (по умолчанию: 2): " + Style.RESET_ALL).strip() 
                
                if choice == '0':
                    print(Fore.RED + f"ВНИМАНИЕ: Файл '{new_full_path}' будет перезаписан." + Style.RESET_ALL)
                    log_debug(f"Выбрано: перезаписать файл '{new_full_path}'.")
                    return new_name
                elif choice == '1':
                    current_name = new_name # Устанавливаем новое имя для следующей итерации
                    log_debug(f"Выбрано: ввести другое имя. Переход к следующей итерации.")
                    continue # Возвращаемся к началу цикла, чтобы запросить новое имя
                else: # '2' или любой другой некорректный ввод - добавляем индекс
                    idx = 1
                    while True:
                        indexed_name = f"{new_name}_{idx}"
                        indexed_full_path = os.path.join(output_path, indexed_name + '.' + output_format)
                        log_debug(f"Выбрано: добавить индекс. Проверка индексированного имени: {indexed_full_path}")
                        if not os.path.exists(indexed_full_path):
                            print(Fore.GREEN + f"Файл будет сохранен как '{indexed_name}.{output_format}'." + Style.RESET_ALL)
                            log_debug(f"Выбрано: использовать индексированное имя '{indexed_name}'.")
                            return indexed_name
                        idx += 1
            else:
                log_debug(f"Введенное имя '{new_full_path}' не существует. Используем его.")
                return new_name # Введенное имя не существует, используем его

def ask_output_format(default_format):
    formats = ['mp4', 'mkv', 'avi', 'webm']
    print("\n" + Fore.MAGENTA + "Выберите выходной формат:" + Style.RESET_ALL)
    for i, f in enumerate(formats):
        print(f"{i}: {f}")
    
    try:
        default_format_index = formats.index(default_format)
    except ValueError:
        default_format = 'mp4'
        default_format_index = formats.index(default_format)
    log_debug(f"Начальный/дефолтный выходной формат: {default_format} (индекс {default_format_index})")

    choice = input(Fore.CYAN + f"Номер формата (по умолчанию {default_format_index}: {default_format}): " + Style.RESET_ALL).strip()
    
    if not choice:
        print(Fore.GREEN + f"Использование формата по умолчанию: {default_format}" + Style.RESET_ALL)
        log_debug(f"Выбран формат по умолчанию: {default_format}")
        return default_format
    elif choice.isdigit() and 0 <= int(choice) < len(formats):
        selected_format = formats[int(choice)]
        print(Fore.GREEN + f"Выбран формат: {selected_format}" + Style.RESET_ALL)
        log_debug(f"Выбран формат: {selected_format}")
        return selected_format
    else:
        print(Fore.YELLOW + "Неверный выбор формата. Использование формата по умолчанию." + Style.RESET_ALL)
        log_debug(f"Неверный выбор формата. Используется дефолтный: {default_format}")
        return default_format

def download_video(
        url, video_id, audio_id,
        output_path, output_name,
        merge_format, platform,
        cookie_file_path=None,
        subtitle_options=None):
    """
    Скачивает (и, при необходимости, сливает) выбранные потоки.
    Возвращает путь к итоговому файлу либо None.
    """
    full_tmpl = os.path.join(output_path, output_name + '.%(ext)s')
    log_debug(f"yt‑dlp outtmpl: {full_tmpl}")

    ffmpeg_path = detect_ffmpeg_path()
    if not ffmpeg_path:
        print(Fore.RED + "FFmpeg не найден – установка обязательна." + Style.RESET_ALL)
        return None

    # ---------------- 1. Формируем строку для --format -----------------
    manifest_mode = False
    if isinstance(video_id, str) and '+' in video_id:          # bestvideo+bestaudio
        format_string = video_id
        manifest_mode = True
        log_debug(f"Используем составной формат: {format_string}")
    elif audio_id:                                             # отдельное аудио
        format_string = f'{video_id}+{audio_id}'
        log_debug(f"Выбрано два потока: {format_string}")
    else:                                                      # только видео
        format_string = video_id
        log_debug(f"Выбран один поток: {format_string}")

    # ---------------- 2. Базовые опции yt‑dlp --------------------------
    ydl_opts = {
        'format'           : format_string,
        'outtmpl'          : full_tmpl,
        'quiet'            : False,
        'ffmpeg_location'  : ffmpeg_path,
        'overwrites'       : True,
        'continuedl'       : True,
        'writedescription' : False,
        'writeinfojson'    : False,
        'writesubtitles'   : False,
        'progress_hooks'   : [],      # заполним ниже
    }

    if manifest_mode:                 # DASH/HLS – склейку доверяем yt‑dlp
        ydl_opts['postprocessors'] = [{'key': 'FFmpegMerger'}]
        log_debug("Обнаружен поток‑манифест – задействуем FFmpegMerger.")
    else:
        ydl_opts['merge_output_format'] = merge_format
        log_debug(f"merge_output_format = {merge_format}")

    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        log_debug(f"cookiefile = {cookie_file_path}")

    if subtitle_options:
        ydl_opts.update(subtitle_options)

    # ---------------- 3. progress‑hook & подготовка --------------------
    os.makedirs(output_path, exist_ok=True)
    last_file = None

    def phook(d):
        nonlocal last_file
        if d['status'] == 'finished':
            last_file = d.get('filename')
            log_debug(f"Файл скачан: {last_file}")

    ydl_opts['progress_hooks'] = [phook]

    # ---------------- 4. Загрузка с повторами --------------------------
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log_debug(f"Запуск yt‑dlp, попытка {attempt}/{MAX_RETRIES}: {ydl_opts}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # ---- поиск итогового файла ----
            candidate = last_file or full_tmpl.replace('%(ext)s', merge_format)
            if os.path.isfile(candidate):
                return candidate

            base_low = output_name.lower()
            for fn in os.listdir(output_path):
                if fn.lower().startswith(base_low) and fn.lower().endswith('.' + merge_format):
                    return os.path.join(output_path, fn)

            return None

        except DownloadError as e:
            err_text = str(e)
            retriable = any(key in err_text for key in (
                "Got error:", "read,", "Read timed out", "retry", "HTTP Error 5",
            ))

            log_debug(f"DownloadError: {err_text} (retriable={retriable})")

            # Доп. проверка на блокировку .part-файла
            if "being used by another process" in err_text or "access is denied" in err_text.lower():
                log_debug("Попытка устранить блокировку .part-файла.")
                try:
                    part_file = None
                    base_path = os.path.join(output_path, output_name)
                    for ext_try in ("m4a", "webm", "mp4", "mkv"):
                        part_candidate = (
                            base_path + f".f{audio_id}.{ext_try}.part" if audio_id
                            else base_path + f".{ext_try}.part"
                        )
                        if os.path.exists(part_candidate):
                            part_file = part_candidate
                            break

                    if part_file:
                        try:
                            import psutil
                            for proc in psutil.process_iter(['pid', 'name']):
                                try:
                                    for f in proc.open_files():
                                        if os.path.samefile(f.path, part_file):
                                            pname = proc.name()
                                            pid = proc.pid
                                            log_debug(f"Файл блокирует: {pname} (PID {pid})")
                                            print(Fore.RED + f"Файл блокирует {pname} (PID {pid}) — закрой процесс и повтори." + Style.RESET_ALL)
                                            break
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    continue
                        except ImportError:
                            log_debug("psutil не установлен — не можем определить блокирующий процесс.")

                        # Попробуем удалить файл (на свой страх и риск)
                        try:
                            os.remove(part_file)
                            log_debug("Удалили .part-файл.")
                        except Exception as del_err:
                            log_debug(f"Не удалось удалить файл: {del_err}")
                except Exception as general_err:
                    log_debug(f"Ошибка в блоке устранения блокировки: {general_err}")

            # --- Новый блок: обновление куков перед повтором ---
            if retriable and attempt < MAX_RETRIES:
                # Только для поддерживаемых платформ
                cookie_map = {
                    "youtube": COOKIES_YT,
                    "facebook": COOKIES_FB,
                    "vimeo": COOKIES_VI,
                    "rutube": COOKIES_RT,
                    "vk": COOKIES_VK,
                }
                if platform in cookie_map:
                    new_cookie_file = get_cookies_for_platform(platform, cookie_map[platform])
                    if new_cookie_file:
                        cookie_file_path = new_cookie_file
                        ydl_opts['cookiefile'] = cookie_file_path
                        log_debug(f"Перед повтором обновили cookiefile: {cookie_file_path}")
                print(Fore.YELLOW + f"Обрыв загрузки (попытка {attempt}/{MAX_RETRIES}) – повтор через 5 с…" + Style.RESET_ALL)
                time.sleep(5)
                continue
            else:
                raise


        except Exception as e:
            # Любая другая ошибка – пробрасываем после логирования
            log_debug(f"Непредвиденная ошибка (попытка {attempt}): {e}\n{traceback.format_exc()}")
            raise

    return None  # если вышли из цикла без успеха

def save_chapters_to_file(chapters, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            for i, ch in enumerate(chapters, 1):
                start = int(ch.get("start_time", 0) * 1000)
                end = int(ch.get("end_time", ch.get("start_time", 0) + 1) * 1000)
                title = ch.get("title", f"Chapter {i}")
                f.write("[CHAPTER]\n")
                f.write("TIMEBASE=1/1000\n")
                f.write(f"START={start}\n")
                f.write(f"END={end}\n")
                f.write(f"TITLE={title}\n")
        log_debug(f"Файл глав сохранён в формате ffmetadata: {path}")
        return True
    except Exception as e:
        print(Fore.RED + f"Ошибка при сохранении файла глав (ffmetadata): {e}" + Style.RESET_ALL)
        log_debug(f"Ошибка сохранения файла глав (ffmetadata): {e}")
        return False

def main():
    print(Fore.YELLOW + "Universal Video Downloader")

    # Проверка наличия ffmpeg
    ffmpeg_path = detect_ffmpeg_path()
    if not ffmpeg_path:
        print(
            "\nДля работы необходима утилита ffmpeg.\n"
            "Скачайте архив отсюда:\n"
            "  https://www.gyan.dev/ffmpeg/builds/\n"
            "или отсюда:\n"
            "  https://github.com/BtbN/FFmpeg-Builds/releases\n"
            "Извлеките из подпапки \\bin\\ ffmpeg.exe в папку рядом со скриптом\n"
            "или поместите ffmpeg в системный путь PATH.\n"
        )
        sys.exit(1)

    raw_url = input(Fore.CYAN + "Введите ссылку: " + Style.RESET_ALL).strip()
    log_debug(f"Введена ссылка: {raw_url}")

    output_path = None
    output_name = None
    output_format = None
    video_id = None
    audio_id = None

    # --- ИНИЦИАЛИЗАЦИЯ переменных для предотвращения ошибок ---
    subtitle_files = []
    subtitle_format = 'srt'
    subs_to_integrate_langs = []
    integrate_subs = False
    keep_sub_files = True
    integrate_chapters = False
    keep_chapter_file = False
    chapter_filename = None
    current_processing_file = None
    desired_ext = None
    video_ext = ''
    audio_ext = ''
    video_codec = ''
    audio_codec = ''

    try:
        platform, url = extract_platform_and_url(raw_url)
        info = safe_get_video_info(url, platform)
        cookie_file_to_use = info.get('__cookiefile__')

        chapters = info.get("chapters")
        has_chapters = isinstance(chapters, list) and len(chapters) > 0
        log_debug(f"Наличие глав: {has_chapters}")

        video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(info['formats'])

        # --- если обнаружен поток-манифест, предложим выбор качества
        if video_id == "bestvideo+bestaudio/best":
            quality_map = {
                "0": ("bestvideo+bestaudio/best", "Максимальное"),
                "1": ("bestvideo[height<=1080]+bestaudio/best", "≤ 1080p"),
                "2": ("bestvideo[height<=720]+bestaudio/best",  "≤ 720p"),
                "3": ("bestvideo[height<=480]+bestaudio/best",  "≤ 480p"),
                "4": ("bestvideo[height<=360]+bestaudio/best",  "≤ 360p"),
            }

            print(Fore.CYAN + "\nВыберите желаемое качество DASH/HLS:" + Style.RESET_ALL)
            for key, (_, label) in quality_map.items():
                print(f"{key}: {label}")
            choice = input(Fore.CYAN + "Номер (Enter = 0): " + Style.RESET_ALL).strip() or "0"
            selected = quality_map.get(choice, quality_map["0"])
            video_id = selected[0]
            log_debug(f"Пользователь выбрал профиль DASH: {video_id}")

        subtitle_download_options = ask_and_select_subtitles(info)

        # --- Главы: спросим про сохранение до выбора папки
        save_chapter_file = False
        integrate_chapters = False
        keep_chapter_file = False
        chapter_filename = None

        if has_chapters:
            ask_chaps = input(Fore.CYAN + "Видео содержит главы. Сохранить главы в файл? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            save_chapter_file = ask_chaps != "0"
            log_debug(f"Пользователь выбрал сохранить главы: {save_chapter_file}")

        output_path = select_output_folder()
        output_format = ask_output_format(desired_ext)

        # --- Субтитры: опрос об интеграции
        integrate_subs = False
        keep_sub_files = True
        subs_to_integrate_langs = []

        if output_format.lower() == 'mkv' and subtitle_download_options and subtitle_download_options.get('subtitleslangs'):
            available_langs = subtitle_download_options['subtitleslangs']
            print(Fore.CYAN + "\nКакие субтитры интегрировать в итоговый MKV?"
                  "\n  Введите номера или коды языков (через запятую или пробел)."
                  "\n  Enter, 0 или all — интегрировать ВСЕ."
                  "\n  «-» (минус) — не интегрировать ничего." + Style.RESET_ALL)
            for idx, lang in enumerate(available_langs, 1):
                print(f"{idx}: {lang}")
            sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL).strip()
            if sel in ("", "0", "all"):
                subs_to_integrate_langs = available_langs.copy()
                integrate_subs = True
            elif sel == "-":
                integrate_subs = False
            else:
                parts = [s.strip() for s in re.split(r"[,\s]+", sel) if s.strip()]
                for p in parts:
                    if p.isdigit() and 1 <= int(p) <= len(available_langs):
                        subs_to_integrate_langs.append(available_langs[int(p) - 1])
                    elif p in available_langs:
                        subs_to_integrate_langs.append(p)
                subs_to_integrate_langs = sorted(set(subs_to_integrate_langs))
                integrate_subs = bool(subs_to_integrate_langs)
            log_debug(f"Выбраны языки для интеграции: {subs_to_integrate_langs}")
            if integrate_subs:
                keep_input = input(Fore.CYAN + "Сохранять субтитры отдельными файлами? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                keep_sub_files = (keep_input != "0")
                log_debug(f"keep_sub_files = {keep_sub_files}")

        log_debug(f"Интеграция субтитров: {integrate_subs}, языки: {subs_to_integrate_langs}, keep files: {keep_sub_files}")

        # --- Главы: опрос об интеграции в MKV
        if output_format.lower() == 'mkv' and has_chapters:
            chaps = input(Fore.CYAN + "Интегрировать главы в MKV? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            integrate_chapters = chaps != "0"
            log_debug(f"Интеграция глав: {integrate_chapters}")
            if integrate_chapters:
                keep = input(Fore.CYAN + "Сохранять файл с главами отдельно? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                keep_chapter_file = keep == "1"
                log_debug(f"Сохраняем ли файл глав отдельно: {keep_chapter_file}")

        default_title = info.get('title', 'video')
        safe_title = re.sub(r'[<>:"/\\|?*]', '', default_title)
        log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")
        output_name = ask_output_filename(safe_title, output_path, output_format)
        log_debug(f"Финальное имя файла, выбранное пользователем: '{output_name}'")

        if (save_chapter_file or integrate_chapters) and has_chapters:
            chapter_filename = os.path.join(output_path, f"{output_name}.chapters.txt")
            save_chapters_to_file(chapters, chapter_filename)

        log_debug(f"subtitle_options переданы: {subtitle_download_options}")

        downloaded_file = download_video(
            url, video_id, audio_id, output_path, output_name, output_format,
            platform, cookie_file_to_use, subtitle_options=subtitle_download_options
        )

        if downloaded_file:
            current_processing_file = downloaded_file
            desired_ext = output_format.lower()
            subtitle_langs = subtitle_download_options.get('subtitleslangs') if subtitle_download_options else []
            subtitle_format = subtitle_download_options.get('subtitlesformat') if subtitle_download_options else 'srt'
            subtitle_files = []

            for lang in subtitle_langs:
                sub_path = os.path.join(output_path, f"{output_name}.{lang}.{subtitle_format}")
                if os.path.exists(sub_path):
                    subtitle_files.append((sub_path, lang))
                    log_debug(f"Для интеграции найден файл субтитров: {sub_path}")
                else:
                    log_debug(f"Файл субтитров для языка {lang} не найден (.{subtitle_format})")

            ffmpeg_path = detect_ffmpeg_path()
            if not ffmpeg_path:
                print(Fore.RED + "FFmpeg не найден. Обработка невозможна." + Style.RESET_ALL)
                log_debug("FFmpeg не найден, обработка невозможна.")
            else:
                subs_to_integrate = []
                if integrate_subs and subtitle_files:
                    subs_to_integrate = [
                        (sub_file, lang)
                        for sub_file, lang in subtitle_files
                        if not subs_to_integrate_langs or lang in subs_to_integrate_langs
                    ]

                temp_output_file = os.path.join(output_path, f"{output_name}_muxed_temp.{desired_ext}")
                ffmpeg_cmd = [ffmpeg_path, '-loglevel', 'warning']
                if desired_ext == 'avi':
                    ffmpeg_cmd += ['-fflags', '+genpts']
                ffmpeg_cmd += ['-i', current_processing_file]

                if integrate_subs and subs_to_integrate:
                    for sub_file, _ in subs_to_integrate:
                        ffmpeg_cmd += ['-i', sub_file]

                if integrate_chapters and chapter_filename and os.path.exists(chapter_filename):
                    ffmpeg_cmd += ['-f', 'ffmetadata', '-i', chapter_filename]

                need_webm_transcode = False
                if desired_ext == 'webm':
                    if not (video_ext == 'webm' and audio_ext == 'webm' and video_codec in ('vp8', 'vp9', 'av1') and audio_codec in ('opus', 'vorbis')):
                        need_webm_transcode = True

                # Кодеки
                if desired_ext == 'webm':
                    if need_webm_transcode:
                        ffmpeg_cmd += ['-c:v', 'libvpx-vp9', '-c:a', 'libopus']
                    else:
                        ffmpeg_cmd += ['-c:v', 'copy', '-c:a', 'copy']
                else:
                    ffmpeg_cmd += ['-c:v', 'copy', '-c:a', 'copy']

                if integrate_subs and subs_to_integrate:
                    ffmpeg_cmd += ['-c:s', 'srt']

                # Карты потоков
                ffmpeg_cmd += ['-map', '0']

                if integrate_subs and subs_to_integrate:
                    for idx, (_, lang) in enumerate(subs_to_integrate):
                        ffmpeg_cmd += ['-map', str(idx + 1)]
                        ffmpeg_cmd += [f'-metadata:s:s:{idx}', f'language={lang}']

                if integrate_chapters and chapter_filename:
                    chapter_input_idx = 1 + len(subs_to_integrate)
                    ffmpeg_cmd += ['-map_metadata', str(chapter_input_idx)]

                ffmpeg_cmd += [temp_output_file]
                log_debug(f"Выполняется команда ffmpeg для объединения: {' '.join(map(str, ffmpeg_cmd))}")
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)


                if result.returncode == 0:
                    try:
                        os.remove(current_processing_file)
                    except Exception as e:
                        log_debug(f"Ошибка при удалении исходного файла после mux: {e}")
                    current_processing_file = temp_output_file

                    if integrate_chapters and chapter_filename and not keep_chapter_file:
                        try:
                            os.remove(chapter_filename)
                            print(Fore.YELLOW + f"Удалён файл глав: {chapter_filename}" + Style.RESET_ALL)
                            log_debug(f"Удалён временный файл глав: {chapter_filename}")
                        except Exception as e:
                            log_debug(f"Ошибка при удалении файла глав: {e}")

                    if integrate_subs and subs_to_integrate:
                        print(Fore.GREEN + f"Видео, аудио и субтитры объединены в {desired_ext.upper()}." + Style.RESET_ALL)
                        log_debug(f"Видео, аудио и субтитры объединены в {desired_ext.upper()}: {temp_output_file}")
                        if not keep_sub_files:
                            for sub_file, _ in subs_to_integrate:
                                try:
                                    os.remove(sub_file)
                                    print(Fore.YELLOW + f"Удалён файл субтитров: {sub_file}" + Style.RESET_ALL)
                                    log_debug(f"Удалён встроенный файл субтитров: {sub_file}")
                                except Exception as e:
                                    print(Fore.RED + f"Ошибка при удалении файла субтитров {sub_file}: {e}" + Style.RESET_ALL)
                                    log_debug(f"Ошибка при удалении файла субтитров {sub_file}: {e}")
                    else:
                        print(Fore.GREEN + f"Видео и аудио объединены в {desired_ext}." + Style.RESET_ALL)
                        log_debug(f"Видео и аудио объединены в {desired_ext}: {temp_output_file}")
                else:
                    print(Fore.RED + "Ошибка при объединении через ffmpeg." + Style.RESET_ALL)
                    log_debug("Ошибка при объединении через ffmpeg.")

            final_target_filename = os.path.join(output_path, f"{output_name}.{output_format}")
            if os.path.abspath(current_processing_file) != os.path.abspath(final_target_filename):
                try:
                    if os.path.exists(final_target_filename):
                        try:
                            os.remove(final_target_filename)
                            log_debug(f"Удалён существующий файл перед переименованием: {final_target_filename}")
                        except Exception as e:
                            log_debug(f"Не удалось удалить существующий файл перед переименованием: {e}")
                            final_target_filename = current_processing_file  # fallback
                            raise e  # пробрасываем исключение, чтобы не делать переименование
                    os.rename(current_processing_file, final_target_filename)
                    log_debug(f"Переименован файл: {current_processing_file} -> {final_target_filename}")
                except Exception as e:
                    log_debug(f"Ошибка при переименовании финального файла: {e}")
                    print(Fore.RED + f"Ошибка при переименовании финального файла: {e}" + Style.RESET_ALL)
                    final_target_filename = current_processing_file  # fallback

            print(Fore.GREEN + f"\nГотово. Видео сохранено в: {final_target_filename}" + Style.RESET_ALL)
            log_debug(f"Видео успешно сохранено в: {final_target_filename}")

        else:
            print(Fore.YELLOW + "\nЗагрузка завершилась, но конечный файл не найден." + Style.RESET_ALL)
            log_debug("Загрузка завершилась, но конечный файл не найден.")

    except KeyboardInterrupt:
        print(Fore.YELLOW + "\nЗагрузка прервана пользователем." + Style.RESET_ALL)
        log_debug("Загрузка прервана пользователем (KeyboardInterrupt).")
    except DownloadError as e:
        print(f"\n{Fore.RED}Ошибка загрузки: {e}{Style.RESET_ALL}")
        log_debug(f"Ошибка загрузки (DownloadError): {str(e)}")
    except Exception as e:
        print(f"\n{Fore.RED}Произошла непредвиденная ошибка: {e}{Style.RESET_ALL}")
        log_debug(f"Произошла непредвиденная ошибка: {e}\n{traceback.format_exc()}")
    finally:
        log_debug("Завершение работы скрипта.")

if __name__ == '__main__':
    main()
