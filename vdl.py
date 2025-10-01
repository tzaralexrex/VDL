# -*- coding: utf-8 -*-

# Universal Video Downloader with Cookie Browser Support
# Скрипт для скачивания видео с поддержкой куки, плейлистов, форматов, субтитров и глав.
# В случае ошибок импорта модулей при первоначальном запуске скрипта, рекомендуется установить их вручную, например:
# python3 -m pip install requests packaging colorama yt-dlp browser_cookie3 psutil ffmpeg-python

import subprocess
import sys
import re
import time
import traceback
import http.cookiejar
import importlib
import os
import platform
import argparse
import threading
import shutil
from pathlib import Path
from datetime import datetime
from shutil import which
from dataclasses import dataclass
from typing import List

system = platform.system().lower()

if system == "windows":
    import msvcrt
    import ctypes
elif system == "darwin":
    # MacOS: msvcrt и ctypes не нужны
    pass

# --- Глобальные настройки и константы ---
DEBUG = 1  # Включение/выключение отладки
DEBUG_APPEND = 1 # 0 = перезаписывать лог, 1 = дописывать к существующему
DEBUG_FILE = 'debug.log' # Имя файла журнала отладки
InitialDir = "Video"  # Папка для автоматического выбора сохранения

# --- Настройки вывода плейлистов ---
PAGE_SIZE = 20    # Количество видео на одной странице при выводе плейлиста
PAGE_TIMEOUT = 10 # Таймаут ожидания между страницами плейлиста

# --- Пути к cookie-файлам для разных платформ ---
COOKIES_FB = 'cookies_fb.txt'      # Facebook
COOKIES_YT = 'cookies_yt.txt'      # YouTube
COOKIES_VI = 'cookies_vi.txt'      # Vimeo
COOKIES_RT = 'cookies_rt.txt'      # Rutube
COOKIES_VK = 'cookies_vk.txt'      # VK
COOKIES_GOOGLE = "cookies_google.txt" # Google

MAX_RETRIES = 15  # Максимум попыток повторной загрузки при обрывах

CHECK_VER = 1  # Проверять версии зависимостей (1) или только наличие модулей (0)

# --- Настройки нормализации субтитров ---
MIN_DISPLAY_MS = 200           # Минимальная длительность блока субтитров, ms
INTER_CAPTION_GAP_MS = 0       # Межтитровый интервал, ms

debug_file_initialized = False  # Флаг инициализации файла журнала отладки

# Класс для хранения информации о субтитре (индекс, время начала/конца, текст)
@dataclass
class Caption:
    idx: int
    start: int
    end: int
    text: str

# Регулярное выражение для разбора блоков субтитров SRT
SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s-->\s(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n{2,}|\Z)",
    re.DOTALL
)

# --- Глобальные переменные для хранения пользовательских настроек ---
USER_SELECTED_SUB_LANGS = []           # Языки субтитров для интеграции
USER_SELECTED_SUB_FORMAT = None        # Формат субтитров
USER_INTEGRATE_SUBS = False           # Интегрировать субтитры в итоговый файл
USER_KEEP_SUB_FILES = True             # Сохранять отдельные файлы субтитров
USER_INTEGRATE_CHAPTERS = False        # Интегрировать главы
USER_KEEP_CHAPTER_FILE = False         # Сохранять файл глав отдельно
USER_SELECTED_VIDEO_CODEC = None       # Выбранный видеокодек
USER_SELECTED_AUDIO_CODEC = None       # Выбранный аудиокодек
USER_SELECTED_OUTPUT_FORMAT = None     # Итоговый формат файла
USER_SELECTED_CHAPTER_FILENAME = None  # Имя файла глав
USER_SELECTED_OUTPUT_NAME = None       # Имя итогового файла
USER_SELECTED_OUTPUT_PATH = None       # Путь для сохранения

# --- Импорт функций для получения версии пакета (совместимость с Python <3.8) ---
try:
    from importlib.metadata import version as get_version, PackageNotFoundError
except ImportError:
    from importlib_metadata import version as get_version, PackageNotFoundError  # type: ignore

def log_debug(message):
    """
    Записывает сообщение в файл журнала отладки, если DEBUG включён.
    Поддержка: Windows, MacOS, Linux.
    """
    global debug_file_initialized

    # Если отладка выключена — ничего не делаем
    if not DEBUG:
        return

    # Формируем строку для записи в лог
    log_line = f"[{datetime.now()}] {message}\n"

    # Если файл ещё не инициализирован — открываем в нужном режиме
    if not debug_file_initialized:
        mode = 'a' if DEBUG_APPEND else 'w'
        with open(DEBUG_FILE, mode, encoding='utf-8') as f:
            if DEBUG_APPEND:
                # В режиме дописывания — добавляем разделитель и заголовок нового сеанса
                f.write(f"\n{'='*60}\n--- Начинается новый сеанс отладки [{datetime.now()}] ---\n")
            # В режиме 'w' — просто записываем первую строку
            f.write(log_line)
        debug_file_initialized = True
    else:
        # Если файл уже инициализирован — просто дописываем строку
        with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)

# --- Импорт сторонних модулей через универсальную функцию ---
requests = importlib.import_module('requests')
packaging = importlib.import_module('packaging')
from packaging.version import parse as parse_version

## --- Универсальный импорт и автообновление внешних модулей ---
## Используется для автоматической установки и обновления зависимостей
def import_or_update(module_name, pypi_name=None, min_version=None, force_check=False):
    """
    Импортирует модуль, при необходимости устанавливает или обновляет его до актуальной версии с PyPI.
    Поддержка: Windows, MacOS, Linux.
    :param module_name: имя для importlib.import_module
    :param pypi_name: имя пакета на PyPI (если отличается)
    :param min_version: минимальная версия (опционально)
    :param force_check: принудительно проверять версии даже если CHECK_VER=0
    :return: импортированный модуль
    """

    # Определяем имя пакета для PyPI, если оно отличается от имени модуля
    pypi_name = pypi_name or module_name

    # Если не требуется проверка версии — просто импортируем модуль
    if not CHECK_VER and not force_check:
        try:
            # Модуль не найден — выводим инструкцию для пользователя и завершаем работу
            return importlib.import_module(module_name)
        except ImportError:
            # --- Модуль не найден — выводим инструкцию для пользователя и завершаем работу ---
            log_debug(f"import_or_update: ImportError: {module_name} ({pypi_name}) не найден")
            print(f"\n[!] Необходимый модуль {pypi_name} не установлен. Установите его вручную командой:\n    pip install {pypi_name}\nРабота невозможна.")
            sys.exit(1)

    # Полная проверка: наличие, версия, обновление
    print(f"Проверяю наличие и актуальность модуля {pypi_name}", end='', flush=True)
    try:
        # Импортируем модуль
        module = importlib.import_module(module_name)
        # Проверяем актуальность версии через запрос к PyPI
        try:
            resp = requests.get(f"https://pypi.org/pypi/{pypi_name}/json", timeout=5)
            if resp.ok:
                latest = resp.json()['info']['version']
                try:
                    installed = get_version(pypi_name)
                except PackageNotFoundError:
                    # --- Если не удалось получить версию через metadata — пробуем через __version__ ---
                    installed = getattr(module, '__version__', None)
                # Если установленная версия меньше актуальной — обновляем
                if installed and parse_version(installed) < parse_version(latest):
                    print()
                    print(f"[!] Доступна новая версия {pypi_name}: {installed} → {latest}. Обновляем...", end='', flush=True)
                    log_debug(f"import_or_update: обновление {pypi_name}: {installed} → {latest}")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", pypi_name])
                    module = importlib.reload(module)
            print(" - OK")
        except Exception as e:
            # --- Ошибка при проверке или обновлении — выводим причину, но не прерываем работу ---
            log_debug(f"import_or_update: ошибка проверки/обновления {pypi_name}: {e}")
            print(f"[!] Не удалось проверить или обновить {pypi_name}: {e}")
        # Проверяем минимально требуемую версию, если указана
        if min_version:
            try:
                installed = get_version(pypi_name)
            except PackageNotFoundError:
                installed = getattr(module, '__version__', None)
            if installed and parse_version(installed) < parse_version(min_version):
                print()
                print(f"[!] Требуется версия {min_version} для {pypi_name}, обновляем...")
                log_debug(f"import_or_update: обновление до min_version {min_version}")
                subprocess.check_call([sys.executable, "-m", "pip", "install", f"{pypi_name}>={min_version}"])
                module = importlib.reload(module)
        return module
    except ImportError:
        # --- Модуль не установлен — пробуем установить через pip ---
        log_debug(f"import_or_update: ImportError: {module_name} ({pypi_name}) не установлен, пробуем установить через pip")
        print(f"[!] {pypi_name} не установлен. Устанавливаем...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pypi_name])
        return importlib.import_module(module_name)

# --- Импорт сторонних модулей с автоматической установкой/обновлением ---
yt_dlp = import_or_update('yt_dlp', force_check=True)
browser_cookie3 = import_or_update('browser_cookie3')
colorama = import_or_update('colorama')
psutil = import_or_update('psutil')
# curl_cffi = import_or_update('curl_cffi')
# pyppeteer = import_or_update('pyppeteer')
ffmpeg = import_or_update('ffmpeg', 'ffmpeg-python')

from yt_dlp.utils import DownloadError
from browser_cookie3 import BrowserCookieError
from colorama import init, Fore, Style

try:
    from tkinter import filedialog, Tk
    import tkinter as tk
except ImportError:
    tk = None
    filedialog = None

init(autoreset=True)  # Инициализация colorama и автоматический сброс цвета после каждого print

def fallback_download(url):
    """
    Заглушка для fallback-скачивания.
    В дальнейшем здесь будет реализован автоматический анализ страницы и поиск видео.
    """
    print("\n" + Fore.YELLOW + "[Fallback] yt-dlp не поддерживает этот сайт. Будет предпринята попытка автоматического поиска видео на странице..." + Style.RESET_ALL)
    log_debug(f"[Fallback] Запуск fallback-скачивания для URL: {url}")
    # TODO: Реализовать дальнейшие шаги алгоритма

## --- Проверка валидности cookie-файла для платформы ---
def cookie_file_is_valid(platform: str, cookie_path: str, test_url: str = None) -> bool:
    """
    Проверяет, «жив» ли куки-файл по реальной ссылке (например, на видео).
    Если test_url не задан, используется главная страница платформы.
    Поддержка: Windows, MacOS, Linux.
    """
    if not test_url:
        # Если не указана тестовая ссылка — используем главную страницу платформы
        test_url = "https://www.youtube.com" if platform == "youtube" else "https://www.facebook.com"
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "cookiefile": cookie_path,
        }
        # extract_flat только для YouTube-плейлистов!
        if platform == "youtube":
            opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(test_url, download=False)
        return True
    except DownloadError:
        return False
    except Exception:
        return False

def detect_ffmpeg_path():
    """
    Ищет ffmpeg (ffmpeg.exe для Windows, ffmpeg для MacOS/Linux) в локальной папке и в системном PATH.
    Возвращает путь к ffmpeg или None.
    Поддержка: Windows, MacOS, Linux.
    """
    script_dir = Path(sys.argv[0]).resolve().parent
    ffmpeg_filename = "ffmpeg.exe" if system == "windows" else "ffmpeg"
    local_path = script_dir / ffmpeg_filename
    log_debug(f"Поиск ffmpeg: Проверяем локальный путь: {local_path}")
    if Path(local_path).is_file():
        log_debug(f"FFmpeg найден по локальному пути: {local_path}")
        return local_path
    system_path = which("ffmpeg")
    log_debug(f"Поиск ffmpeg: Проверяем системный PATH: {system_path}")
    if system_path and Path(system_path).is_file():
        log_debug(f"FFmpeg найден в системном PATH: {system_path}")
        return system_path
    log_debug("FFmpeg не найден ни по локальному пути, ни в системном PATH.")
    return None

def clean_url_by_platform(platform: str, url: str) -> str:
    """
    Очищает и нормализует ссылку для указанной платформы (Facebook, VK и др.).
    Поддержка: Windows, MacOS, Linux.
    """
    try:
        # Для Facebook — извлекаем ID видео по разным паттернам
        if platform == 'facebook':
            # Если ссылка уже содержит /watch/?v=..., не трогаем
            if re.search(r'/watch/\?v=\d+', url):
                return url
            fb_patterns = [
                r'/videos/(\d+)',
                r'v=(\d+)',
                r'/reel/(\d+)',
                r'/watch/\?v=(\d+)',
                r'/video.php\?v=(\d+)'
            ]
            # Перебираем паттерны, ищем совпадение
            for pattern in fb_patterns:
                match = re.search(pattern, url)
                if match:
                    video_id = match.group(1)
                    return f"https://m.facebook.com/watch/?v={video_id}&_rdr"
            # Если не удалось распознать — выбрасываем ошибку
            raise ValueError(f"{Fore.RED}Не удалось распознать ID видео Facebook{Style.RESET_ALL}")

        # Для VK — приводим ссылку к стандартному виду
        elif platform == 'vk':
            match = re.search(r'(video[-\d]+_\d+)', url)
            return f"https://vk.com/{match.group(1)}" if match else url

        # Для Vimeo — убираем якорь
        elif platform == 'vimeo':
            return url.split('#')[0]

        # Для Rutube — убираем query-параметры
        elif platform == 'rutube':
            return url.split('?')[0]

    except Exception as e:
        # Логируем ошибку, если не удалось обработать ссылку
        log_debug(f"Ошибка при очистке URL для {platform}: {e}")
    # Если ничего не подошло — возвращаем исходную ссылку
    return url

def extract_platform_and_url(raw_url: str):
    """
    Определяет платформу по ссылке и возвращает (platform, cleaned_url).
    Поддержка: Windows, MacOS, Linux.
    """
    # Очищаем входную ссылку от пробелов
    url = raw_url.strip()

    # Словарь паттернов для определения платформы
    patterns = {
        'youtube':  [r'(?:youtube\.com|youtu\.be)'],
        'facebook': [r'(?:facebook\.com|fb\.watch)'],
        'vimeo':    [r'(?:vimeo\.com)'],
        'rutube':   [r'(?:rutube\.ru)'],
        'vk':       [r'(?:vk\.com|vkontakte\.ru)'],
    }

    # Перебираем платформы и паттерны для поиска совпадения
    for platform, pats in patterns.items():
        for pat in pats:
            if re.search(pat, url, re.I):
                # Если совпало — нормализуем ссылку и возвращаем
                cleaned_url = clean_url_by_platform(platform, url)
                log_debug(f"Определена платформа: {platform} для URL: {cleaned_url}")
                return platform, cleaned_url

    # Если ни один паттерн не подошёл — возвращаем generic-режим
    log_debug("Платформа не опознана, пробуем generic-режим.")
    return "generic", url

def save_cookies_to_netscape_file(cj: http.cookiejar.CookieJar, filename: str):
    """
    Сохраняет объект CookieJar в файл Netscape-формата, который может быть использован yt-dlp.
    Поддержка: Windows, MacOS, Linux.
    """
    try:
        # Создаём объект MozillaCookieJar для сохранения в Netscape-формате
        mozilla_cj = http.cookiejar.MozillaCookieJar(filename)
        # Перебираем куки и добавляем их в объект
        for cookie in cj:
            mozilla_cj.set_cookie(cookie)
        # Сохраняем куки в файл
        mozilla_cj.save(ignore_discard=True, ignore_expires=True)
        print(Fore.GREEN + f"Куки успешно сохранены в файл: {filename}" + Style.RESET_ALL)
        log_debug(f"Куки успешно сохранены в файл: {filename}")
        return True
    except Exception as e:
        # В случае ошибки — выводим причину и логируем
        print(Fore.RED + f"Ошибка при сохранении куков в файл {filename}: {e}" + Style.RESET_ALL)
        log_debug(f"Ошибка при сохранении куков в файл {filename}:\n{traceback.format_exc()}")
        return False

def get_cookies_for_platform(platform: str, cookie_file: str, url: str = None, force_browser: bool = False) -> str | None:
    """
    Пытается получить куки: сначала из файла, затем из браузера.
    Возвращает путь к файлу куков, если куки успешно получены/загружены, иначе None.
    Safari на MacOS не поддерживается.
    Поддержка: Windows, MacOS, Linux.
    """
    # 1. Попытка загрузить куки из существующего файла
    if Path(cookie_file).exists():
        if not force_browser:
            print(Fore.CYAN + f"Проверка валидности куки-файла {cookie_file} для {platform.capitalize()}..." + Style.RESET_ALL)
            # Передаём test_url — реальную ссылку (если есть)
            test_url = url
            log_debug(f"[LOG] Проверка куки-файла: {cookie_file} для платформы {platform} по ссылке {test_url}")
            # Проверяем валидность куки-файла
            if cookie_file_is_valid(platform, cookie_file, test_url=test_url):
                print(Fore.CYAN + f"Куки-файл {cookie_file} валиден, используем для {platform.capitalize()}." + Style.RESET_ALL)
                log_debug(f"Файл куков '{cookie_file}' существует и прошёл проверку. Используем его.")
                return str(Path(cookie_file).resolve())
            else:
                # Если файл найден, но невалиден — пробуем получить свежие куки из браузера
                print(f"[!] Файл {cookie_file} найден, но авторизация не удалась. Пробуем свежие куки из браузера…")
                log_debug(f"Файл {cookie_file} найден, но не прошёл проверку. Переходим к извлечению из браузера.")
    else:
        # Если файла нет — сразу переходим к извлечению из браузера
        print(Fore.CYAN + f"Принудительный режим: пропускаем проверку и извлекаем куки из браузера." + Style.RESET_ALL)

    # 2. Попытка извлечь куки из браузера
    if system == "darwin":
        print(Fore.YELLOW + "Safari не поддерживается для автоматического получения куков. Используйте Chrome или Firefox, либо экспортируйте куки вручную." + Style.RESET_ALL)
    browsers_to_try = ['chrome', 'firefox']
    browser_functions = {
        'chrome': browser_cookie3.chrome,
        'firefox': browser_cookie3.firefox,
    }

    # Словарь доменов для каждой платформы
    platform_domains = {
        'youtube':  ['youtube.com', 'google.com'],  # fallback
        'facebook': ['facebook.com'],
        'vimeo':    ['vimeo.com'],
        'rutube':   ['rutube.ru'],
        'vk':       ['vk.com'],
    }

    print(Fore.YELLOW + f"Примечание: Для автоматического получения куков из браузера (Chrome/Firefox), "
          f"убедитесь, что он закрыт или неактивен." + Style.RESET_ALL)

    # Получаем список доменов для текущей платформы
    domains = platform_domains.get(platform, [])
    extracted_cj = None

    # Перебираем браузеры для попытки извлечения куков
    for browser in browsers_to_try:
        try:
            print(Fore.GREEN + f"Пытаемся получить куки для {platform.capitalize()} из браузера ({browser})." + Style.RESET_ALL)
            log_debug(f"Попытка получить куки для {platform.capitalize()} из браузера: {browser}")

            # Перебираем домены, пробуем получить куки
            for domain in domains:
                log_debug(f"Пробуем домен {domain} в {browser}")
                extracted_cj = browser_functions[browser](domain_name=domain)
                if extracted_cj:
                    break

            # Если удалось получить куки — сохраняем их в файл
            if extracted_cj:
                print(Fore.GREEN + f"Куки для {platform.capitalize()} успешно получены из {browser.capitalize()}." + Style.RESET_ALL)
                log_debug(f"Куки для {platform.capitalize()} успешно получены из {browser.capitalize()}.")
                if save_cookies_to_netscape_file(extracted_cj, cookie_file):
                    return str(Path(cookie_file).resolve())
                else:
                    print(Fore.RED + "Не удалось сохранить извлеченные куки в файл. Продолжаем без них." + Style.RESET_ALL)
                    log_debug("Не удалось сохранить извлеченные куки в файл.")
                    return None

        except BrowserCookieError as e:
            # Ошибка специфична для browser_cookie3 — выводим причину
            print(Fore.RED + f"Не удалось получить куки из браузера ({browser}) для {platform.capitalize()}: {e}" + Style.RESET_ALL)
            log_debug(f"BrowserCookieError при получении куков из {browser} для {platform.capitalize()}:\n{traceback.format_exc()}")
        except Exception as e:
            # Общая ошибка — выводим причину
            print(Fore.RED + f"Произошла непредвиденная ошибка при попытке получить куки из {browser} для {platform.capitalize()}: {e}" + Style.RESET_ALL)
            log_debug(f"Общая ошибка при получении куков из {browser} для {platform.capitalize()}:\n{traceback.format_exc()}")

    # Если не удалось получить куки ни одним способом — выводим инструкцию для пользователя
    print(Fore.YELLOW + f"Не удалось автоматически получить куки для {platform.capitalize()}. "
                        f"Для загрузки приватных видео {platform.capitalize()}, пожалуйста, "
                        f"экспортируйте куки в файл {cookie_file} вручную (например, с помощью расширения браузера)." + Style.RESET_ALL)
    log_debug(f"Автоматическое получение куков для {platform.capitalize()} не удалось.")
    return None

def get_video_info(url, platform, cookie_file_path=None, cookiesfrombrowser=None):
    """
    Получает информацию о видео/плейлисте через yt-dlp.
    Поддержка: Windows, MacOS, Linux.
    """
    log_debug(f"get_video_info: Итоговая платформа: {platform}, URL: {url}")
    ydl_opts = {'quiet': True, 'skip_download': True}
    if platform == "youtube" and ("list=" in url or "/playlist" in url):
        ydl_opts['extract_flat'] = True
    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        log_debug(f"get_video_info: Используем cookiefile: {cookie_file_path}")
    if cookiesfrombrowser:
        ydl_opts['cookiesfrombrowser'] = cookiesfrombrowser
        log_debug(f"get_video_info: Пробуем cookiesfrombrowser: {cookiesfrombrowser}")

    log_debug(f"get_video_info: Запрос информации для URL: {url} с опциями: {ydl_opts}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log_debug("get_video_info: Перед вызовом ydl.extract_info")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            log_debug("get_video_info: После вызова ydl.extract_info")
            extractor = info.get('extractor', 'unknown')
            info_type = info.get('_type', 'video')
            log_debug(f"get_video_info: extractor={extractor}, _type={info_type}, title={info.get('title', 'N/A')}, id={info.get('id', 'N/A')}")
            if cookie_file_path:
                info['__cookiefile__'] = cookie_file_path
            return info
        except DownloadError as e:
            err_text = str(e)
            retriable = any(key in err_text for key in (
                "Got error:", "read,", "Read timed out", "retry", "HTTP Error 5", "HTTP Error 429", "Too Many Requests"
            ))
            log_debug(f"get_video_info: Ошибка при вызове ydl.extract_info: {e}\n{traceback.format_exc()}")
            if retriable and attempt < MAX_RETRIES:
                print(Fore.YELLOW + f"Ошибка получения информации о видео (попытка {attempt}/{MAX_RETRIES}) – повтор через 5 с…" + Style.RESET_ALL)
                time.sleep(5)
                continue
            else:
                raise
        except Exception as e:
            log_debug(f"get_video_info: Ошибка при вызове ydl.extract_info: {e}\n{traceback.format_exc()}")
            raise

def is_video_unavailable_error(err):
    """
    Проверяет, относится ли ошибка к недоступности видео (премьера, удалено, скрыто и т.п.)
    Поддержка: Windows, MacOS, Linux.
    """
    err_text = str(err).lower()
    return any(x in err_text for x in [
        "premiere", "not yet available", "is unavailable", "is private", "is scheduled",
        "video unavailable", "this video is unavailable", "this video is private",
        "this video is scheduled", "this video is not yet available", "has been removed",
        "has been deleted", "is no longer available"
    ])

def safe_get_video_info(url: str, platform: str, cookie_file_to_use=None):
    """
    Безопасно получает информацию о видео, пробует разные куки и режимы.
    Поддержка: Windows, MacOS, Linux.
    """
    # Если путь к куки-файлу уже получен — используем его
    if cookie_file_to_use:
        try:
            print(Fore.CYAN + "Получение информации о видео..." + Style.RESET_ALL)
            return get_video_info(url, platform, cookie_file_to_use)
        except DownloadError as err:
            err_l = str(err).lower()
            need_login = any(x in err_l for x in ("login", "403", "private", "sign in", "unauthorized"))
            if not need_login:
                raise
            # Если требуется авторизация, пробуем cookiesfrombrowser
            for browser in ("chrome", "firefox"):
                try:
                    log_debug(f"safe_get_video_info: Пробуем cookiesfrombrowser: {browser}")
                    return get_video_info(url, platform, cookiesfrombrowser=browser)
                except DownloadError as err2:
                    log_debug(f"safe_get_video_info: cookiesfrombrowser {browser} не сработал: {err2}")
                    continue
            print(f"\nВидео требует авторизации, а получить рабочие куки автоматически не удалось.\n"
                  f"Сохраните их вручную и положите файл сюда: {cookie_file_to_use}\n")
            raise DownloadError("Видео требует авторизации, а получить рабочие куки автоматически не удалось. Пропуск.")

    else:
        # ----- generic -----
        # порядок попыток: Google-куки → VK-куки → пользовательский cookies.txt → без куков
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
                continue           # иначе переходим к след. cookie-файлу
        # (аналогично добавить попытку cookiesfrombrowser)
        for browser in ("chrome", "firefox"):
            try:
                log_debug(f"safe_get_video_info: generic: Пробуем cookiesfrombrowser: {browser}")
                return get_video_info(url, platform, cookiesfrombrowser=browser)
            except DownloadError as err2:
                log_debug(f"safe_get_video_info: generic: cookiesfrombrowser {browser} не сработал: {err2}")
                continue
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
    raise DownloadError(f"Не удалось получить рабочие куки для сайта {site_domain}, видео пропущено.")

def choose_format(formats, auto_mode=False, bestvideo=False, bestaudio=False):
    """
    Позволяет выбрать видео- и аудиоформат из списка доступных.
    Поддержка: Windows, MacOS, Linux.
    Возвращает кортеж с параметрами формата:
        (video_id, audio_id|None,
         desired_ext, video_ext, audio_ext,
         video_codec, audio_codec)
    """
    # --- Автоматический выбор для трансляций ---
    # Проверяем, есть ли признак live
    is_live = any(f.get('protocol') == 'm3u8' or f.get('ext') == 'm3u8' for f in formats)
    if is_live:
        live_formats = [f for f in formats if f.get('ext') == 'm3u8']
        if live_formats:
            best_live = live_formats[-1]
            print(Fore.YELLOW + "\nЭто трансляция! Доступны только потоковые форматы (m3u8)." + Style.RESET_ALL)
            log_debug("Автоматический выбор m3u8 для трансляции.")
            return (
                best_live["format_id"],
                None,
                "mp4",           # итоговый контейнер
                "m3u8", "",      # video_ext, audio_ext
                best_live.get("vcodec", ""),
                ""
            )
        else:
            print(Fore.RED + "\nДля трансляции не найден ни один потоковый формат m3u8!" + Style.RESET_ALL)
            log_debug("Нет доступных m3u8 форматов для трансляции.")
            return (None, None, "mp4", "", "", "", "")
    # --------------------------- сортировка ---------------------------
    video_formats = [f for f in formats if f.get("vcodec") != "none"]
    audio_formats = [f for f in formats if f.get("acodec") != "none"
                     and f.get("vcodec") == "none"]

    video_formats.sort(key=lambda f: (f.get("height") or 0,
                                      f.get("format_id", "")))
    # --- Логируем список форматов для отладки ---
    log_debug("Список видеоформатов после сортировки:")
    for idx, f in enumerate(video_formats):
        log_debug(f"{idx}: id={f.get('format_id')} ext={f.get('ext')} height={f.get('height')}")

    # --- Ищем лучший mp4 по высоте ---
    mp4_indexes = [i for i, f in enumerate(video_formats) if f.get("ext", "").lower() == "mp4"]
    if mp4_indexes:
        best_mp4_index = max(mp4_indexes, key=lambda i: video_formats[i].get("height") or 0)
        default_video = best_mp4_index
        log_debug(f"Лучший mp4: индекс={best_mp4_index}, height={video_formats[best_mp4_index].get('height')}")
    else:
        default_video = len(video_formats) - 1
        log_debug(f"mp4 не найден, default_video={default_video}")   

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

    if auto_mode:
        v_choice = default_video
    else:
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
        if not auto_mode:
            input(Fore.CYAN + "Enter для продолжения…" + Style.RESET_ALL)
    else:
        print("\n" + Fore.MAGENTA + "Доступные аудиоформаты:" + Style.RESET_ALL)
        for i, f in enumerate(audio_formats):
            fmt_id = f.get("format_id", "?")
            ext    = f.get("ext", "?")
            abr    = f.get("abr") or "?"
            acodec = f.get("acodec", "?")
            print(f"{i}: {fmt_id}  –  {ext}  –  {abr} kbps  –  {acodec}")

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
        # --- Новый способ выбора лучшего совместимого аудиоформата ---
        allowed = compat.get(video_ext, {af["ext"] for af in audio_formats})
        compatible_audios = [
            (i, f) for i, f in enumerate(audio_formats)
            if f.get("ext", "").lower() in allowed
        ]
        if compatible_audios:
            # Сортируем по abr (по убыванию) и без -drc
            compatible_audios.sort(key=lambda x: (
                '-drc' in x[1].get("format_id", ""),  # сначала без drc
                -(x[1].get("abr") or 0)               # потом по убыванию abr
            ))
            default_audio = compatible_audios[0][0]
        else:
            default_audio = len(audio_formats) - 1

        while True:
            if auto_mode:
                a_choice = default_audio
            else:
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

def ask_and_select_subtitles(info, auto_mode=False):
    """ 
    Обрабатывает наличие вложенных и автоматических субтитров, формирует выбор пользователя
    Поддержка: Windows, MacOS, Linux.
    Запрашивает у пользователя выбор субтитров и их формата.
    Возвращает словарь с параметрами загрузки субтитров.
    """
    write_automatic = False
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

        if auto_mode:
            selected_langs = numbered
            # В авто-режиме не спрашиваем про автоматические субтитры, просто не скачиваем их отдельно
            write_automatic = False
        else:
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
        if auto_mode:
            # В авто-режиме выбираем en и ru, если есть, иначе все доступные
            default_langs = ['en', 'ru']
            selected_langs = [lang for lang in default_langs if lang in auto_langs]
            if not selected_langs:
                selected_langs = auto_langs
            print(Fore.GREEN + f"Автоматически выбраны автоматические субтитры: {', '.join(selected_langs)}" + Style.RESET_ALL)
        else:
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
        write_automatic = True if selected_langs else False
        normalize_auto_subs = False
        keep_original_auto_subs = False
        if write_automatic and not auto_mode:
            norm_ans = input(Fore.CYAN + "Нормализовать автоматические субтитры (убрать перекрытия таймингов)? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            normalize_auto_subs = (norm_ans != "0")
            if normalize_auto_subs:
                keep_ans = input(Fore.CYAN + "Сохранять оригинальные автоматические субтитры? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                keep_original_auto_subs = (keep_ans == "1")
            # --- Дописывать ли .auto к автоматическим субтитрам ---
            auto_suffix_ans = input(Fore.CYAN + "Дописать суффикс .auto к автоматическим субтитрам? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            add_auto_suffix = (auto_suffix_ans != "0")

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
    if auto_mode:
        sub_format = default_format
        print(Fore.GREEN + f"Автоматически выбран формат субтитров: {sub_format}" + Style.RESET_ALL)
    else:
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
        'subtitlesformat': sub_format,
        'normalize_auto_subs': normalize_auto_subs if write_automatic else False,
        'keep_original_auto_subs': keep_original_auto_subs if write_automatic else False,
        'automatic_subtitles_langs': sorted(download_automatics) if write_automatic else [],
        'add_auto_suffix': add_auto_suffix if write_automatic else True  # по умолчанию True
    }

def select_output_folder(auto_mode=False):
    """
    Запрашивает у пользователя папку для сохранения файлов.
    В автоматическом режиме выбирает папку по умолчанию.
    Поддержка: Windows, MacOS, Linux.
    """
    print("\n" + Fore.CYAN + "Выберите папку для сохранения видео" + Style.RESET_ALL)
    system = platform.system().lower()
    if auto_mode:
        folder = Path(InitialDir).resolve()
        folder.mkdir(exist_ok=True)
        print(Fore.GREEN + f"Автоматически выбрана папка: {folder}" + Style.RESET_ALL)
        return folder
    if system == "windows" and tk is not None and filedialog is not None:
        try:
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
            if folder:
                return Path(folder).resolve()
            else:
                print(Fore.YELLOW + "Папка не выбрана. Попробуйте снова." + Style.RESET_ALL)
        except Exception as e:
            print(Fore.YELLOW + f"Ошибка при открытии диалога выбора папки: {e}" + Style.RESET_ALL)
            log_debug(f"Ошибка выбора папки через tkinter: {e}")
    # Fallback для не-Windows или если tkinter не работает
    while True:
        if auto_mode:
            folder = Path(InitialDir).resolve()
            folder.mkdir(exist_ok=True)
            print(Fore.GREEN + f"Автоматически выбрана папка: {folder}" + Style.RESET_ALL)
            return folder
        print(Fore.CYAN + f"Введите путь к папке для сохранения (Enter — использовать папку по умолчанию: {InitialDir}): " + Style.RESET_ALL)
        folder = input().strip()
        if not folder:
            folder_path = Path(InitialDir).resolve()
            print(Fore.GREEN + f"Используется папка по умолчанию: {folder_path}" + Style.RESET_ALL)
        else:
            folder_path = Path(folder).resolve()
        # Проверка на запрещённые символы
        if any(s in folder_path.name for s in ['*', '?', '<', '>', '|', '"', ':']):
            print(Fore.RED + "Путь содержит запрещённые символы. Попробуйте снова." + Style.RESET_ALL)
            continue
        if not folder_path.is_dir():
            print(Fore.RED + f"Папка '{folder_path}' не существует." + Style.RESET_ALL)
            create_ans = input(Fore.CYAN + "Создать эту папку? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            if create_ans in ("", "1"):
                try:
                    folder_path.mkdir(parents=True, exist_ok=True)
                    print(Fore.GREEN + f"Папка '{folder_path}' создана." + Style.RESET_ALL)
                    return folder_path
                except Exception as e:
                    print(Fore.RED + f"Не удалось создать папку: {e}" + Style.RESET_ALL)
                    continue
            else:
                print(Fore.YELLOW + "Введите путь к существующей папке." + Style.RESET_ALL)
                continue
        return folder_path

def ask_output_filename(default_name, output_path, output_format, auto_mode=False):
    """
    Запрашивает имя файла, проверяет существование и предлагает варианты при совпадении.
    Поддержка: Windows, MacOS, Linux.
    """
    current_name = default_name
    log_debug(f"Предлагаемое имя файла (по умолчанию): {default_name}")

    clipboard_copied = False

    # --- Копируем имя файла в буфер обмена Windows ---
    if platform.system().lower() == "windows":
        try:
            clipboard_text = f"{current_name}"
            # Используем UTF-16LE для корректной работы clip с кириллицей
            subprocess.run('clip', input=clipboard_text.encode('utf-16le'), check=True)
            clipboard_copied = True
        except Exception as e:
            log_debug(f"Не удалось скопировать имя файла в буфер обмена: {e}")

    while True:
        proposed_full_path = Path(output_path) / f"{current_name}.{output_format}"
        log_debug(f"Проверка имени файла: {proposed_full_path}")

        print(f"\n{Fore.MAGENTA}Предлагаемое имя файла: {Fore.GREEN}{current_name}.{output_format}{Style.RESET_ALL}")
        if clipboard_copied:
            print(Fore.YELLOW + f"(Имя файла скопировано в буфер обмена)" + Style.RESET_ALL)
            clipboard_copied = False  # Показываем только один раз

        if auto_mode:
            # Если файл существует — добавить индекс, иначе использовать текущее имя
            if Path(proposed_full_path).exists():
                idx = 1
                while True:
                    indexed_name = f"{current_name}_{idx}"
                    indexed_full_path = Path(output_path) / f"{indexed_name}.{output_format}"
                    if not indexed_full_path.exists():
                        return indexed_name
                    idx += 1
            else:
                return current_name

        name_input = input(Fore.CYAN + "Введите имя файла (Enter — оставить по умолчанию): " + Style.RESET_ALL).strip()

        if not name_input:  # Пользователь нажал Enter, использует предложенное имя
            if Path(proposed_full_path).exists():
                print(Fore.YELLOW + f"Файл '{current_name}.{output_format}' уже существует." + Style.RESET_ALL)
                log_debug(f"Файл '{proposed_full_path}' существует. Запрос действия.")
                choice = input(Fore.CYAN + "Перезаписать (0), выбрать другое имя (1), или добавить индекс (2)? (по умолчанию: 2): " + Style.RESET_ALL).strip()

                if choice == '0':
                    print(Fore.RED + f"ВНИМАНИЕ: Файл '{current_name}.{output_format}' будет перезаписан." + Style.RESET_ALL)
                    log_debug(f"Выбрано: перезаписать файл '{proposed_full_path}'.")
                    return current_name  # Возвращаем текущее имя для перезаписи
                elif choice == '1':
                    # Предлагаем пользователю ввести новое имя
                    print(Fore.CYAN + "Введите новое имя файла: " + Style.RESET_ALL)
                    new_name = input().strip()
                    log_debug(f"Выбрано: ввести новое имя. Введено: '{new_name}'.")
                    if new_name:
                        current_name = new_name
                    else:  # Если пользователь ничего не ввел, возвращаемся к началу цикла
                        print(Fore.YELLOW + "Имя файла не было введено. Попробуйте снова." + Style.RESET_ALL)
                        log_debug("Новое имя файла не введено. Повторный запрос.")
                        continue
                else:  # '2' или любой другой некорректный ввод - добавляем индекс
                    idx = 1
                    while True:
                        indexed_name = f"{current_name}_{idx}"
                        indexed_full_path = Path(output_path) / f"{indexed_name}.{output_format}"
                        log_debug(f"Выбрано: добавить индекс. Проверка индексированного имени: {indexed_full_path}")
                        if not indexed_full_path.exists():
                            print(Fore.GREEN + f"Файл будет сохранен как '{indexed_name}.{output_format}'." + Style.RESET_ALL)
                            log_debug(f"Выбрано: использовать индексированное имя '{indexed_name}'.")
                            return indexed_name
                        idx += 1
            else:
                log_debug(f"Файл '{proposed_full_path}' не существует. Используем это имя.")
                return current_name  # Файл не существует, можно использовать это имя
        else:  # Пользователь ввел новое имя
            new_name = name_input
            new_full_path = Path(output_path) / f"{new_name}.{output_format}"
            log_debug(f"Пользователь ввел новое имя: '{new_name}'. Проверка: {new_full_path}")
            if new_full_path.exists():
                print(Fore.YELLOW + f"Файл '{new_full_path}' уже существует." + Style.RESET_ALL)
                log_debug(f"Новое имя '{new_full_path}' уже существует. Запрос действия.")
                choice = input(Fore.CYAN + "Перезаписать (0), выбрать другое имя (1), или добавить индекс (2)? (по умолчанию: 2): " + Style.RESET_ALL).strip()

                if choice == '0':
                    print(Fore.RED + f"ВНИМАНИЕ: Файл '{new_full_path}' будет перезаписан." + Style.RESET_ALL)
                    log_debug(f"Выбрано: перезаписать файл '{new_full_path}'.")
                    return new_name
                elif choice == '1':
                    current_name = new_name  # Устанавливаем новое имя для следующей итерации
                    log_debug(f"Выбрано: ввести другое имя. Переход к следующей итерации.")
                    continue  # Возвращаемся к началу цикла, чтобы запросить новое имя
                else:  # '2' или любой другой некорректный ввод - добавляем индекс
                    idx = 1
                    while True:
                        indexed_name = f"{new_name}_{idx}"
                        indexed_full_path = Path(output_path) / f"{indexed_name}.{output_format}"
                        log_debug(f"Выбрано: добавить индекс. Проверка индексированного имени: {indexed_full_path}")
                        if not indexed_full_path.exists():
                            print(Fore.GREEN + f"Файл будет сохранен как '{indexed_name}.{output_format}'." + Style.RESET_ALL)
                            log_debug(f"Выбрано: использовать индексированное имя '{indexed_name}'.")
                            return indexed_name
                        idx += 1
            else:
                log_debug(f"Введенное имя '{new_full_path}' не существует. Используем его.")
                return new_name  # Введенное имя не существует, используем его
            
def ask_output_format(default_format, auto_mode=False, subtitle_options=None, has_chapters=False):
    """
    Запрашивает у пользователя желаемый выходной формат файла.
    Если выбраны встроенные субтитры или главы, по умолчанию предлагается mkv.
    Поддержка: Windows, MacOS, Linux.
    """
    formats = ['mp4', 'mkv', 'avi', 'webm']
    # --- Определяем дефолтный формат ---
    mkv_needed = False
    if subtitle_options:
        # Если выбраны встроенные субтитры
        if subtitle_options.get('writesubtitles') or subtitle_options.get('writeautomaticsub'):
            mkv_needed = True
    if has_chapters:
        mkv_needed = True
    if mkv_needed:
        default_format = 'mkv'
    print("\n" + Fore.MAGENTA + "Выберите выходной формат:" + Style.RESET_ALL)
    for i, f in enumerate(formats):
        print(f"{i}: {f}")
    try:
        default_format_index = formats.index(default_format)
    except ValueError:
        default_format = 'mp4'
        default_format_index = formats.index(default_format)
    log_debug(f"Начальный/дефолтный выходной формат: {default_format} (индекс {default_format_index})")
    if auto_mode:
        print(Fore.GREEN + f"Использование формата по умолчанию: {default_format}" + Style.RESET_ALL)
        log_debug(f"Выбран формат по умолчанию: {default_format} (auto_mode)")
        return default_format
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

def phook(d, last_file_ref, subtitle_options=None, output_name=None, output_path=None):
    """
    Хук для yt-dlp: сохраняет имя скачанного файла.
    Если скачан файл автоматических субтитров — сразу нормализует его.
    """
    if d['status'] == 'finished':
        last_file_ref[0] = d.get('filename')
        log_debug(f"Файл скачан: {last_file_ref[0]}")
        # --- Нормализация автоматических субтитров сразу после скачивания ---
        if subtitle_options and subtitle_options.get('writeautomaticsub'):
            normalize_auto = subtitle_options.get('normalize_auto_subs', False)
            keep_bak = subtitle_options.get('keep_original_auto_subs', False)
            sub_format = subtitle_options.get('subtitlesformat', 'srt')
            auto_langs = subtitle_options.get('automatic_subtitles_langs', [])
            fname = d.get('filename')
            # Проверяем, что это файл автоматических субтитров
            for lang in auto_langs:
                expected_file = str(Path(output_path) / f"{output_name}.{lang}.{sub_format}")
                if fname and fname.lower() == expected_file.lower() and normalize_auto and sub_format == "srt":
                    try:
                        normalize_srt_file(fname, overwrite=True, backup=keep_bak)
                        print(Fore.GREEN + f"Автоматические субтитры для '{lang}' нормализованы: {fname}" + Style.RESET_ALL)
                        log_debug(f"Автоматические субтитры для '{lang}' нормализованы: {fname}")
                    except Exception as e:
                        print(Fore.RED + f"Ошибка нормализации субтитров {fname}: {e}" + Style.RESET_ALL)
                        log_debug(f"Ошибка нормализации субтитров {fname}: {e}")

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
    full_tmpl = str(Path(output_path) / f"{output_name}.%(ext)s")
    log_debug(f"yt-dlp outtmpl: {full_tmpl}")

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

    # ---------------- 2. Базовые опции yt-dlp --------------------------
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

    # --- Проверка наличия субтитров ---
    if subtitle_options:
        sub_format = subtitle_options.get('subtitlesformat', 'srt')
        langs = subtitle_options.get('subtitleslangs', [])
        skip_subs = []
        for lang in langs:
            sub_file = Path(output_path) / f"{output_name}.{lang}.{sub_format}"
            if sub_file.exists() and sub_file.stat().st_size > 1024:
                skip_subs.append(lang)
        if len(skip_subs) == len(langs):
            subtitle_options['writesubtitles'] = False
            subtitle_options['writeautomaticsub'] = False
        else:
            subtitle_options['subtitleslangs'] = [lang for lang in langs if lang not in skip_subs]
        ydl_opts.update(subtitle_options)

    # --- Проверка наличия файла глав ---
    if subtitle_options and subtitle_options.get('writechapters'):
        chapter_file = Path(output_path) / f"{output_name}.ffmeta"
        if chapter_file.exists() and chapter_file.stat().st_size > 512:
            subtitle_options['writechapters'] = False
        ydl_opts.update(subtitle_options)

    # --- live_from_start для трансляций ---
    try:
        info = get_video_info(url, platform, cookie_file_path)
        # Если это трансляция (is_live) или формат m3u8 — добавляем опцию
        if info.get('is_live') or (isinstance(info.get('formats'), list) and any(f.get('ext') == 'm3u8' for f in info['formats'])):
            ydl_opts['live_from_start'] = True
            log_debug("Добавлена опция live_from_start для трансляции.")
    except Exception as e:
        log_debug(f"Не удалось определить is_live: {e}")

    if manifest_mode:                 # DASH/HLS – склейку доверяем yt-dlp
        ydl_opts['postprocessors'] = [{'key': 'FFmpegMerger'}]
        log_debug("Обнаружен поток-манифест – задействуем FFmpegMerger.")
    else:
        ydl_opts['merge_output_format'] = merge_format
        log_debug(f"merge_output_format = {merge_format}")

    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        log_debug(f"cookiefile = {cookie_file_path}")

    if subtitle_options:
        ydl_opts.update(subtitle_options)

    # ---------------- 3. progress-hook & подготовка --------------------
    Path(output_path).mkdir(parents=True, exist_ok=True)
    last_file = [None]
    ydl_opts['progress_hooks'] = [lambda d: phook(d, last_file, subtitle_options, output_name, output_path)]
 
    # ---------------- 4. Загрузка с повторами --------------------------
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log_debug(f"Запуск yt-dlp, попытка {attempt}/{MAX_RETRIES}: {ydl_opts}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # ---- поиск итогового файла ----
            candidate = last_file[0] or full_tmpl.replace('%(ext)s', merge_format)
            if Path(candidate).is_file():
                return candidate

            base_low = output_name.lower()
            for fn in Path(output_path).iterdir():
                if fn.name.lower().startswith(base_low) and fn.name.lower().endswith(f'.{merge_format}'):
                    return str(fn.resolve())

            return None

        except DownloadError as e:
            err_text = str(e)
            retriable = any(key in err_text for key in (
                "Got error:", "read,", "Read timed out", "retry", "HTTP Error 5",
            ))

            # --- Повтор для ошибок загрузки субтитров ---
            is_subtitle_error = "subtitles" in err_text.lower() or "caption" in err_text.lower()
            retriable_sub = any(key in err_text for key in (
                "HTTP Error 429", "Too Many Requests", "HTTP Error 5", "timed out", "connection", "retry"
            ))

            log_debug(f"DownloadError: {err_text} (retriable={retriable})")

            if is_subtitle_error and retriable_sub and attempt < MAX_RETRIES:
                if "HTTP Error 429" in err_text or "Too Many Requests" in err_text:
                    print(Fore.YELLOW + f"Слишком много запросов к субтитрам (429). Ждём 60 секунд..." + Style.RESET_ALL)
                    log_debug("Получен HTTP 429 при скачивании субтитров, увеличиваем паузу до 60 секунд.")
                    time.sleep(60)
                else:
                    print(Fore.YELLOW + f"Ошибка загрузки субтитров (попытка {attempt}/{MAX_RETRIES}) – повтор через 5 с…" + Style.RESET_ALL)
                    time.sleep(5)
                continue

            # --- Обработка ошибки HTTP 416 ---
            if "HTTP Error 416" in err_text or "Requested range not satisfiable" in err_text:
                # Попробуем найти .part-файл и сравнить его размер с ожидаемым
                part_file = None
                final_file = None
                base_path = Path(output_path) / output_name
                found_parts = []
                # Возможные расширения
                ext_try = None
                for ext_try in ("mp4", "mkv", "webm", "avi", "m4a", "mp3"):
                    # Стандартный вариант
                    candidate_part = str(base_path) + f".{ext_try}.part"
                    candidate_final = str(base_path) + f".{ext_try}"
                    if Path(candidate_part).exists():
                        part_file = candidate_part
                        final_file = candidate_final
                        found_parts.append(candidate_part)
                        break
                    # Вариант с суффиксом .f{video_id}
                    candidate_part2 = str(base_path) + f".f{video_id}.{ext_try}.part"
                    candidate_final2 = str(base_path) + f".f{video_id}.{ext_try}"
                    if Path(candidate_part2).exists():
                        part_file = candidate_part2
                        final_file = candidate_final2
                        found_parts.append(candidate_part2)
                        break
                    # Вариант с суффиксом .f{audio_id}
                    if audio_id:
                        candidate_part3 = str(base_path) + f".f{audio_id}.{ext_try}.part"
                        candidate_final3 = str(base_path) + f".f{audio_id}.{ext_try}"
                        if Path(candidate_part3).exists():
                            part_file = candidate_part3
                            final_file = candidate_final3
                            found_parts.append(candidate_part3)
                            break
                log_debug(f"Проверены .part-файлы: {found_parts}")
                if part_file and final_file:
                    part_size = Path(part_file).stat().st_size
                    log_debug(f"Найден .part-файл: {part_file}, размер: {part_size}")
                    try:
                        info = get_video_info(url, platform, cookie_file_path)
                        formats = info.get("formats", [])
                        expected_size = None
                        for f in formats:
                            if f.get("ext") == ext_try and f.get("filesize"):
                                expected_size = f["filesize"]
                                break
                        log_debug(f"Ожидаемый размер: {expected_size}")

                        # --- Проверяем аудиофайл, если скачивается отдельно ---
                        audio_part_file = None
                        audio_final_file = None
                        audio_expected_size = None
                        audio_ok = True
                        if audio_id:
                            for ext_try_a in ("m4a", "mp3", "webm", "aac"):
                                candidate_audio_part = str(base_path) + f".f{audio_id}.{ext_try_a}.part"
                                candidate_audio_final = str(base_path) + f".f{audio_id}.{ext_try_a}"
                                if Path(candidate_audio_part).exists():
                                    audio_part_file = candidate_audio_part
                                    audio_final_file = candidate_audio_final
                                    for f in formats:
                                        if f.get("format_id") == str(audio_id) and f.get("ext") == ext_try_a and f.get("filesize"):
                                            audio_expected_size = f["filesize"]
                                            break
                                    break
                            if audio_part_file:
                                audio_part_size = Path(audio_part_file).stat().st_size
                                audio_ok = (audio_expected_size and audio_part_size >= audio_expected_size) or (not audio_expected_size and audio_part_size > 5 * 1024 * 1024)

                        # --- Переименовываем видео и аудио, если оба скачаны ---
                        video_ok = (expected_size and part_size >= expected_size) or (not expected_size and part_size > 10 * 1024 * 1024)
                        if video_ok and audio_ok:
                            if part_file is not None and final_file is not None:
                                Path(part_file).rename(final_file)
                                log_debug(f"Переименован видеофайл: {part_file} → {final_file}")
                                print(Fore.YELLOW + f"\nФайл {part_file} был скачан полностью, переименован в {final_file}." + Style.RESET_ALL)
                            if audio_id and audio_part_file is not None and audio_final_file is not None:
                                Path(audio_part_file).rename(audio_final_file)
                                log_debug(f"Переименован аудиофайл: {audio_part_file} → {audio_final_file}")
                                print(Fore.YELLOW + f"\nАудиофайл {audio_part_file} был скачан полностью, переименован в {audio_final_file}." + Style.RESET_ALL)
                            # После переименования НЕ возвращаем, а продолжаем выполнение!
                        elif video_ok and not audio_ok and audio_id:
                            print(Fore.YELLOW + f"\nВидео скачано, но аудиофайл ещё не завершён. Ожидание аудио..." + Style.RESET_ALL)
                            log_debug("Видео скачано, аудио не завершено. Продолжаем попытки.")
                        elif not video_ok:
                            print(Fore.YELLOW + f"\nВидео ещё не завершено. Ожидание..." + Style.RESET_ALL)
                            log_debug("Видео не завершено. Продолжаем попытки.")
                        # Не возвращаем, чтобы цикл попыток продолжался!
                    except Exception as info_err:
                        log_debug(f"Ошибка при попытке получить размер видео/аудио: {info_err}")
                else:
                    log_debug("HTTP 416: .part-файл не найден или не удалось обработать.")
                # Если не удалось обработать — пробрасываем ошибку дальше

            # Доп. проверка на блокировку .part-файла
            if "being used by another process" in err_text or "access is denied" in err_text.lower():
                log_debug("Попытка устранить блокировку .part-файла.")
                try:
                    part_file = None
                    base_path = Path(output_path) / output_name
                    for ext_try in ("mp4", "mkv", "webm", "avi", "m4a", "mp3"):
                        # Стандартный вариант
                        part_candidate = str(base_path) + f".{ext_try}.part"
                        if Path(part_candidate).exists():
                            part_file = part_candidate
                            break
                        # Вариант с суффиксом .f{video_id}
                        part_candidate2 = str(base_path) + f".f{video_id}.{ext_try}.part"
                        if Path(part_candidate2).exists():
                            part_file = part_candidate2
                            break
                        # Вариант с суффиксом .f{audio_id}
                        if audio_id:
                            part_candidate3 = str(base_path) + f".f{audio_id}.{ext_try}.part"
                            if Path(part_candidate3).exists():
                                part_file = part_candidate3
                                break

                    if part_file:
                        try:
                            for proc in psutil.process_iter(['pid', 'name']):
                                try:
                                    for f in proc.open_files():
                                        if Path(f.path).resolve() == Path(part_file).resolve():
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
                            Path(part_file).unlink()
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
                    new_cookie_file = get_cookies_for_platform(platform, cookie_map[platform], url)
                    if new_cookie_file:
                        cookie_file_path = new_cookie_file
                        ydl_opts['cookiefile'] = cookie_file_path
                        log_debug(f"Перед повтором обновили cookiefile: {cookie_file_path}")
                print(Fore.YELLOW + f"Обрыв загрузки (попытка {attempt}/{MAX_RETRIES}) – повтор через 5 с…" + Style.RESET_ALL)
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
    """
    Сохраняет главы видео в файл ffmetadata для интеграции в MKV.
    Поддержка: Windows, MacOS, Linux.
    """
    try:
        path = str(Path(path).resolve())
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

def parse_args():
    """
    Парсит аргументы командной строки.
    Поддержка: Windows, MacOS, Linux.
    """
    parser = argparse.ArgumentParser(description="Universal Video Downloader", add_help=True)
    parser.add_argument('url', nargs='?', help='Ссылка на видео или плейлист')
    parser.add_argument('--auto', '-a', action='store_true', help='Автоматический режим (не задавать вопросов)')
    parser.add_argument('--bestvideo', action='store_true', help='Использовать bestvideo')
    parser.add_argument('--bestaudio', action='store_true', help='Использовать bestaudio')
    # Для совместимости с одиночным тире и без тире
    # Собираем все sys.argv, ищем вручную
    args, unknown = parser.parse_known_args()
    # Если url не найден, ищем вручную первый аргумент, похожий на ссылку
    if not args.url:
        for arg in sys.argv[1:]:
            if re.match(r'^https?://', arg):
                args.url = arg
                break
    # Поддержка ключей без тире (например, auto, bestvideo)
    for arg in sys.argv[1:]:
        if arg.lower() == 'auto':
            args.auto = True
        if arg.lower() == 'bestvideo':
            args.bestvideo = True
        if arg.lower() == 'bestaudio':
            args.bestaudio = True
    return args

def parse_selection(selection, total):
    """
    Расширенный парсер выбора номеров видео:
    - Поддержка диапазонов (1-5, 3-)
    - Открытый конец (7-), в середине списка трактуется как диапазон до следующего числа
    - Проверка на ошибки диапазонов и номеров
    - Вывод предупреждений при ошибках
    Поддержка: Windows, MacOS, Linux.
    """
    result = set()
    errors = []
    if not selection or selection.strip() == '0':
        return set(range(1, total + 1))
    parts = [p.strip() for p in re.split(r'[ ,;]+', selection) if p.strip()]
    i = 0
    while i < len(parts):
        part = parts[i]
        if '-' in part:
            # Диапазон N-M
            if re.fullmatch(r'\d+-\d+', part):
                start, end = map(int, part.split('-', 1))
                if start > end:
                    errors.append(f"Диапазон '{part}': начало больше конца")
                elif not (1 <= start <= total) or not (1 <= end <= total):
                    errors.append(f"Диапазон '{part}' вне диапазона 1-{total}")
                else:
                    result.update(range(start, end + 1))
                i += 1
                continue
            # Открытый диапазон N-
            elif re.fullmatch(r'\d+-', part):
                start = int(part[:-1])
                # Если это не последний элемент и следующий — число, трактуем как диапазон N-M
                if i + 1 < len(parts) and parts[i + 1].isdigit():
                    end = int(parts[i + 1])
                    if start > end:
                        errors.append(f"Диапазон '{start}-{end}': начало больше конца")
                    elif not (1 <= start <= total) or not (1 <= end <= total):
                        errors.append(f"Диапазон '{start}-{end}' вне диапазона 1-{total}")
                    else:
                        result.update(range(start, end + 1))
                    i += 2
                    continue
                else:
                    # Открытый диапазон до конца
                    if 1 <= start <= total:
                        result.update(range(start, total + 1))
                    else:
                        errors.append(f"Открытый диапазон '{part}' вне диапазона 1-{total}")
                    i += 1
                    continue
            else:
                errors.append(f"Некорректный диапазон: '{part}'")
                i += 1
                continue
        else:
            # Одиночное число
            if part.isdigit():
                num = int(part)
                if 1 <= num <= total:
                    result.add(num)
                else:
                    errors.append(f"Номер '{num}' вне диапазона 1-{total}")
            else:
                errors.append(f"Некорректный номер: '{part}'")
            i += 1
    if errors:
        print(Fore.YELLOW + "Внимание! Обнаружены ошибки в выборе номеров:")
        for err in errors:
            print(f"  - {err}")
        print(Style.RESET_ALL)
    return sorted(result)

def find_by_format_id(formats, fmt_id, is_video=True):
    """
    Ищет формат по format_id среди доступных.
    Поддержка: Windows, MacOS, Linux.
    """
    for f in formats:
        if f.get('format_id') == fmt_id:
            if is_video and f.get('vcodec') != 'none':
                return f
            if not is_video and f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                return f
    return None

def get_compatible_exts(ext):
    """
    Возвращает совместимые расширения для контейнера.
    Поддержка: Windows, MacOS, Linux.
    """
    compat = {
        'mp4':  {'mp4', 'm4a'},
        'm4a':  {'mp4', 'm4a'},
        'webm': {'webm'},
        'mkv':  {'mp4', 'm4a', 'webm'},
        'avi':  {'avi', 'mp3', 'aac'},
    }
    return compat.get(ext, {ext})

def find_best_video(formats, ref_ext):
    """
    Ищет лучший видеоформат по расширению.
    Поддержка: Windows, MacOS, Linux.
    """
    # Сначала ищем лучший mp4
    mp4_candidates = [f for f in formats if f.get('vcodec') != 'none' and f.get('ext', '').lower() == 'mp4']
    if mp4_candidates:
        return max(mp4_candidates, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    # Потом ищем совместимые с ref_ext
    compatible_exts = get_compatible_exts(ref_ext)
    candidates = [f for f in formats if f.get('vcodec') != 'none' and f.get('ext') in compatible_exts]
    if candidates:
        return max(candidates, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    candidates = [f for f in formats if f.get('vcodec') != 'none']
    if candidates:
        return max(candidates, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    return None

def find_best_audio(formats, ref_ext):
    """
    Ищет лучший аудиоформат по расширению.
    Поддержка: Windows, MacOS, Linux.
    """
    compatible_exts = get_compatible_exts(ref_ext)
    candidates = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('ext') in compatible_exts]
    if candidates:
        return max(candidates, key=lambda f: (f.get('abr') or 0))
    candidates = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
    if candidates:
        return max(candidates, key=lambda f: (f.get('abr') or 0))
    return None

def get_unique_filename(base_name, output_path, output_format):
    """
    Генерирует уникальное имя файла, если файл уже существует.
    Поддержка: Windows, MacOS, Linux.
    """
    candidate = Path(output_path) / f"{base_name}.{output_format}"
    if not candidate.exists():
        return base_name
    idx = 2
    while True:
        candidate_path = Path(output_path) / f"{base_name}_{idx}.{output_format}"
        if not candidate_path.exists():
            return f"{base_name}_{idx}"
        idx += 1

def safe_join(base, *paths):
    """
    Безопасно объединяет пути, защищает от path-injection.
    Поддержка: Windows, MacOS, Linux.
    """
    # Собирает путь и проверяет, что он внутри base
    joined = Path(base).resolve().joinpath(*paths).resolve()
    if not str(joined).startswith(str(Path(base).resolve())):
        raise ValueError(f"Попытка path-injection: {joined} вне {base}")
    return str(joined)

def mux_mkv_with_subs_and_chapters(
    downloaded_file, output_name, output_path,
    subs_to_integrate_langs, subtitle_download_options,
    integrate_subs, keep_sub_files,
    integrate_chapters, keep_chapter_file, chapter_filename
):
    """
    Объединяет видео, субтитры и главы в итоговый MKV-файл.
    Защищено от передачи None в операции с путями и от path-injection.
    Поддержка: Windows, MacOS, Linux.
    """
    try:
        # Проверка и нормализация output_path
        output_path_abs = Path(output_path).resolve()
    except Exception as e:
        log_debug(f"mux_mkv: неверный output_path: {e}")
        print(Fore.RED + "Ошибка: некорректный путь для сохранения." + Style.RESET_ALL)
        return False

    # downloaded_file обязателен и должен существовать
    if not downloaded_file:
        log_debug("mux_mkv: downloaded_file is None")
        print(Fore.RED + "Ошибка: путь к загруженному файлу не задан." + Style.RESET_ALL)
        return False
    try:
        dl_path = Path(downloaded_file)
        if not dl_path.is_absolute():
            dl_path = (output_path_abs / downloaded_file).resolve()
    except Exception:
        dl_path = Path(downloaded_file)
    if not dl_path.exists():
        log_debug(f"mux_mkv: загруженный файл не найден: {downloaded_file}")
        print(Fore.RED + "Ошибка: загруженный файл не найден." + Style.RESET_ALL)
        return False

    # Защита имени: убираем запрещённые символы, не трогаем расширение
    safe_output_name = re.sub(r'[\\/:"*?<>|]+', '', str(output_name)).strip()
    safe_output_name = safe_output_name.lstrip('.').rstrip('.')
    if not safe_output_name:
        log_debug("mux_mkv: некорректное имя файла после очистки")
        print(Fore.RED + "Ошибка: некорректное имя итогового файла." + Style.RESET_ALL)
        return False

    # Формируем список входных файлов для ffmpeg корректно (без лишних кавычек)
    ffmpeg_cmd = ['ffmpeg', '-y', '-loglevel', 'error']
    input_paths = []

    # основной входной файл (видео или уже собранный yt-dlp mkv/mp4)
    input_paths.append(str(dl_path))

    # субтитры (если интегрируем)
    sub_paths = []
    if integrate_subs and subtitle_download_options:
        sub_fmt = subtitle_download_options.get('subtitlesformat', 'srt')
        for lang in (subs_to_integrate_langs or []):
            cand = output_path_abs / f"{safe_output_name}.{lang}.{sub_fmt}"
            if cand.exists():
                sub_paths.append(cand)

    # главы (ffmetadata) — chapter_filename может быть None, Path или str
    chap_path = None
    if integrate_chapters and chapter_filename:
        try:
            if isinstance(chapter_filename, (str,)):
                maybe = Path(chapter_filename)
                if not maybe.is_absolute():
                    maybe = (output_path_abs / chapter_filename).resolve()
                chap_path = maybe
            elif isinstance(chapter_filename, Path):
                chap_path = chapter_filename if chapter_filename.is_absolute() else (output_path_abs / chapter_filename).resolve()
            if not chap_path.exists():
                chap_path = None
        except Exception as e:
            log_debug(f"mux_mkv: ошибка с chapter_filename: {e}")
            chap_path = None

    # Добавляем все входные файлы в команду ffmpeg: для каждого подаём '-i', path
    for p in [input_paths[0]] + [str(p) for p in sub_paths] + ([str(chap_path)] if chap_path else []):
        ffmpeg_cmd += ['-i', p]

    # Если есть субтитры — выставляем language метаданные для каждой субтитровой дорожки
    if sub_paths and subtitle_download_options:
        for sub_idx, lang in enumerate([p.name.split('.')[-2] if p.name.count('.')>=2 else '' for p in sub_paths]):
            # metadata для субтитровых дорожек
            ffmpeg_cmd += [f'-metadata:s:s:{sub_idx}', f'language={lang}']

    # Если есть файл глав (поместим его последним входом), укажем -map_metadata на индекс последнего входа
    if chap_path:
        metadata_input_index = len([input_paths[0]] + sub_paths)  # индекс входа с метаданными
        ffmpeg_cmd += ['-map_metadata', str(metadata_input_index)]

    # Карта дорожек: основной поток — map 0, субтитры — последующие индексы
    ffmpeg_cmd += ['-map', '0']
    for idx in range(1, 1 + len(sub_paths)):
        ffmpeg_cmd += ['-map', str(idx)]

    # Копирование кодеков
    final_mkv = output_path_abs / f"{safe_output_name}_muxed.mkv"
    ffmpeg_cmd += ['-c', 'copy', str(final_mkv)]

    print(Fore.YELLOW + f"\nВыполняется объединение дорожек и глав в MKV..." + Style.RESET_ALL)
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(Fore.GREEN + f"Файл успешно собран: {final_mkv}" + Style.RESET_ALL)
    except Exception as e:
        log_debug(f"mux_mkv: ошибка при сборке ffmpeg: {e}\n{traceback.format_exc()}")
        print(Fore.RED + f"Ошибка при muxing: {e}" + Style.RESET_ALL)
        return False

    # После успешного создания final_mkv — выполняем замену / очистку
    try:
        # определяем orig_file_path — куда должен быть переименован final_mkv
        # если dl_path внутри output_path_abs — используем его имя; иначе указываем dl_path
        try:
            if Path(dl_path).resolve().parent == output_path_abs:
                orig_file_path = dl_path.resolve()
            else:
                # если dl_path вне целевой папки — формируем целевой путь с именем safe_output_name + исходное расширение
                orig_file_path = output_path_abs / dl_path.name
        except Exception:
            orig_file_path = dl_path

        # удаляем существующий итоговый файл, если он есть и это не тот же файл
        try:
            if orig_file_path.exists() and orig_file_path.resolve() != final_mkv.resolve():
                orig_file_path.unlink()
        except Exception as e:
            log_debug(f"mux_mkv: не удалось удалить старый итоговый файл {orig_file_path}: {e}")

        # заменяем (перемещаем) final_mkv в orig_file_path
        try:
            Path(final_mkv).replace(orig_file_path)
            print(Fore.GREEN + f"Файл сохранён как: {orig_file_path}" + Style.RESET_ALL)
            log_debug(f"mux_mkv: {final_mkv} -> {orig_file_path}")
        except Exception as e:
            log_debug(f"mux_mkv: ошибка при переименовании {final_mkv} -> {orig_file_path}: {e}\n{traceback.format_exc()}")
            print(Fore.RED + f"Ошибка при замене итогового файла: {e}" + Style.RESET_ALL)
            return False

        # Удаляем файлы субтитров, если нужно
        if integrate_subs and not keep_sub_files:
            for p in sub_paths:
                try:
                    if p.exists():
                        p.unlink()
                        print(Fore.YELLOW + f"Удалён файл субтитров: {p}" + Style.RESET_ALL)
                except Exception as e:
                    log_debug(f"mux_mkv: не удалось удалить субтитр {p}: {e}")

        # Удаляем файл глав, если нужно
        if integrate_chapters and chap_path and not keep_chapter_file:
            try:
                if chap_path.exists():
                    chap_path.unlink()
                    print(Fore.YELLOW + f"Удалён файл глав: {chap_path.name}" + Style.RESET_ALL)
            except Exception as e:
                log_debug(f"mux_mkv: не удалось удалить файл глав {chap_path}: {e}")

    except Exception as final_err:
        log_debug(f"mux_mkv: финальная обработка завершилась с ошибкой: {final_err}\n{traceback.format_exc()}")
        print(Fore.RED + f"Ошибка при финальной обработке файла: {final_err}" + Style.RESET_ALL)
        return False

    return True

def wait_keys(wait_only_enter, enter_pressed, timeout):
    """
    Ожидает нажатие клавиши Enter или таймаут, поддерживает паузу по Space.
    Поддержка: Windows, MacOS, Linux.
    """
    system = platform.system().lower()
    try:
        if system == "windows":
            start_time = time.time()
            last_sec = -1
            while True:
                elapsed = time.time() - start_time
                left = int(timeout - elapsed)
                if not wait_only_enter[0] and left != last_sec and left >= 0:
                    print(f"\rОжидание... {left} сек. ", end='', flush=True)
                    last_sec = left
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch == '\r' or ch == '\n':
                        print()
                        enter_pressed[0] = True
                        return
                    elif ch == ' ':
                        wait_only_enter[0] = True
                        print(Fore.CYAN + "\nПауза: нажмите Enter для продолжения..." + Style.RESET_ALL)
                        while True:
                            if msvcrt.kbhit():
                                ch2 = msvcrt.getwch()
                                if ch2 == '\r' or ch2 == '\n':
                                    print()
                                    enter_pressed[0] = True
                                    return
                            time.sleep(0.05)
                if not wait_only_enter[0] and elapsed >= timeout:
                    print()
                    return
                time.sleep(0.05)
        else:
            # Кроссплатформенный вариант: input с таймаутом через поток
            import threading

            def wait_input(flag):
                input()
                flag[0] = True

            flag = [False]
            input_thread = threading.Thread(target=wait_input, args=(flag,))
            input_thread.daemon = True
            input_thread.start()
            for left in range(timeout, 0, -1):
                print(f"\rОжидание... {left} сек. ", end='', flush=True)
                input_thread.join(1)
                if flag[0]:
                    print()
                    enter_pressed[0] = True
                    return
            print()
            return
    except Exception:
        print()
        return

def print_playlist_paginated(entries, page_size=PAGE_SIZE, timeout=PAGE_TIMEOUT, playlist_title=None, auto_mode=False):
    """
    Выводит список видео плейлиста порциями по page_size.
    После каждой порции ждёт Enter или timeout секунд.
    Если нажата Space — таймер останавливается, ждём только Enter.
    После полного вывода спрашивает, сохранить ли список в файл.
    Возвращает путь к сохранённому файлу списка (или None).
    Поддержка: Windows, MacOS, Linux.
    """
    log_debug(f"print_playlist_paginated: entries_count={len(entries)}, title={playlist_title}")
    total = len(entries)
    all_lines = []
    saved_list_path = None

    # --- Определяем основной канал (channel_id) ---
    main_channel_id = None
    for entry in entries:
        if entry.get('channel_id'):
            main_channel_id = entry['channel_id']
            break

    # --- Проверяем наличие чужих видео ---
    has_other_channel = False
    for entry in entries:
        if entry.get('channel_id') and main_channel_id and entry['channel_id'] != main_channel_id:
            has_other_channel = True
            break

    if has_other_channel:
        print(Fore.YELLOW + "В плейлисте присутствуют видео с других каналов, номер — в квадратных скобках" + Style.RESET_ALL)

    for start in range(0, total, page_size):
        end = min(start + page_size, total)
        for idx in range(start, end):
            entry = entries[idx]
            title = entry.get('title') or entry.get('id') or f'Видео {idx+1}'
            # --- Если видео с другого канала, выводим номер в квадратных скобках ---
            if entry.get('channel_id') and main_channel_id and entry['channel_id'] != main_channel_id:
                line = f"[{idx+1}]. {title}"
            else:
                line = f"{idx+1}. {title}"
            print(line)
            all_lines.append(line)
        if end < total:
            print(Fore.CYAN + f"\nПоказано {end} из {total}. Enter — далее, Space — пауза, или ожидание {timeout} сек..." + Style.RESET_ALL)
            wait_only_enter = [False]
            enter_pressed = [False]

            wait_keys(wait_only_enter, enter_pressed, timeout)
    # --- После полного вывода ---
    if playlist_title:
        default_filename = f"{playlist_title}.txt"
    else:
        default_filename = "playlist.txt"

    save_list = True  # по умолчанию сохраняем
    if auto_mode:
        answer = "1"
    else:
        answer = input(
            Fore.CYAN + f"\nСохранить список видео в файл '{default_filename}'? (Enter — сохранить, 0 или - — не сохранять): " + Style.RESET_ALL
        ).strip()
    if answer in ("0", "-"):
        save_list = False
    if save_list:
        try:
            with open(default_filename, "w", encoding="utf-8") as f:
                for line in all_lines:
                    f.write(line + "\n")
            print(Fore.GREEN + f"Список сохранён в файл: {default_filename}" + Style.RESET_ALL)
            saved_list_path = str(Path(default_filename).resolve())
        except Exception as e:
            print(Fore.RED + f"Ошибка при сохранении файла: {e}" + Style.RESET_ALL)
    return saved_list_path

def check_mkv_integrity(filepath, expected_video_codec=None, expected_audio_codec=None, expected_sub_langs=None, expected_chapters=False):
    """
    Проверяет, что в MKV-файле присутствуют нужные дорожки (видео, аудио, субтитры, главы).
    expected_sub_langs — список языков субтитров (['ru', 'en'] и т.д.)
    expected_chapters — True/False (ожидаются ли главы)
    Возвращает True, если всё соответствует, иначе False.
    Поддержка: Windows, MacOS, Linux.
    """
    try:
        probe = ffmpeg.probe(filepath)
        streams = probe.get('streams', [])
        video_ok = audio_ok = subs_ok = chaps_ok = True

        # Проверка видео
        if expected_video_codec:
            video_ok = any(s['codec_type'] == 'video' and expected_video_codec in s.get('codec_name', '') for s in streams)
        # Проверка аудио
        if expected_audio_codec:
            audio_ok = any(s['codec_type'] == 'audio' and expected_audio_codec in s.get('codec_name', '') for s in streams)
        # Проверка субтитров
        if expected_sub_langs:
            found_langs = [s.get('tags', {}).get('language', '').lower() for s in streams if s['codec_type'] == 'subtitle']
            subs_ok = all(lang.lower() in found_langs for lang in expected_sub_langs)
        # Проверка глав
        chaps_ok = True
        if expected_chapters:
            chaps_ok = 'chapters' in probe and len(probe['chapters']) > 0

        return video_ok and audio_ok and subs_ok and chaps_ok
    except Exception as e:
        log_debug(f"Ошибка при проверке MKV: {e}")
        return False

def expand_channel_entries(entries, platform, cookie_file_to_use, level=0):
    """
    Рекурсивно раскрывает только разделы/плейлисты, но НЕ делает запросов к каждому видео.
    Возвращает список элементов, где каждый — это видео (url/id/title), но без подробной info.
    Поддержка: Windows, MacOS, Linux.
    """
    expanded = []
    indent = "  " * level
    for entry in entries:
        # Если это раздел/плейлист — раскрываем его
        if entry.get('_type') == 'playlist' or ('url' in entry and not entry.get('formats') and not entry.get('ie_key') == 'Youtube'):
            title = entry.get('title') or entry.get('id') or entry.get('url')
            url = entry.get('url') or entry.get('webpage_url')
            if url:
                info = safe_get_video_info(url, platform, cookie_file_to_use)
                subentries = info.get('entries', [])
                print(Fore.MAGENTA + f"{indent}→ Найден раздел/плейлист: {title} ({len(subentries)} видео)" + Style.RESET_ALL)
                expanded.extend(expand_channel_entries(subentries, platform, cookie_file_to_use, level=level+1))
        # Если это видео (url/id/title), но НЕ плейлист — просто добавляем, не раскрываем!
        elif entry.get('_type') == 'url' or ('url' in entry and not entry.get('_type')):
            expanded.append(entry)
        # Иногда yt-dlp возвращает видео с _type='video' (например, для одиночных видео)
        elif entry.get('_type') == 'video' or 'formats' in entry:
            expanded.append(entry)
    return expanded

def has_nested_playlists(pls):
    """
    Проверяет, есть ли вложенные плейлисты.
    Поддержка: Windows, MacOS, Linux.
    """
    return any(pl.get("sub_playlists") for pl in pls)

def collect_playlists(entries, platform, cookie_file_to_use, level=0):
    """
    Рекурсивно строит структуру: [{title, videos, sub_playlists}]
    Корректно различает настоящие видео и плейлисты для YouTube /playlists.
    Поддержка: Windows, MacOS, Linux.
    """
    log_debug(f"collect_playlists: level={level}, entries_count={len(entries)}")
    playlists = []
    videos = []
    playlist_entries = []
    video_entries = []

    for e in entries:
        # Если это плейлист (YouTube: _type=url и url содержит playlist?list=...)
        if (e.get('_type') == 'playlist' or
            (e.get('_type') == 'url' and 'playlist?list=' in (e.get('url') or ''))):
            playlist_entries.append(e)
        # Если это видео
        elif e.get('_type') in ('url', 'video') or ('formats' in e):
            video_entries.append(e)

    for entry in playlist_entries:
        title = entry.get('title') or entry.get('id') or entry.get('url')
        url = entry.get('url') or entry.get('webpage_url')
        info = safe_get_video_info(url, platform, cookie_file_to_use)
        subentries = info.get('entries', [])
        log_debug(f"collect_playlists: subentries для '{title}' (count={len(subentries)})")
        # Разделяем вложенные плейлисты и видео
        sub_playlist_entries = [e for e in subentries if (e.get('_type') == 'playlist' or (e.get('_type') == 'url' and 'playlist?list=' in (e.get('url') or '')))]
        sub_video_entries = [e for e in subentries if e.get('_type') in ('url', 'video') or ('formats' in e)]
        sub_playlists = collect_playlists(subentries, platform, cookie_file_to_use, level=level+1) if sub_playlist_entries else []
        only_videos = len(subentries) == len(sub_video_entries)
        if only_videos:
            playlists.append({
                "title": title,
                "videos": sub_video_entries,
                "sub_playlists": []
            })
        else:
            playlists.append({
                "title": title,
                "videos": [],
                "sub_playlists": sub_playlists
            })

    if video_entries:
        playlists.append({
            "title": "Без раздела",
            "videos": video_entries,
            "sub_playlists": []
        })
    log_debug(f"collect_playlists: playlists на уровне {level}: {str(playlists)[:500]}")
    return playlists

def process_playlists(playlists, output_path, auto_mode, platform, args, cookie_file_to_use, parent_path=""):
    """
    Обрабатывает плейлисты: выводит, спрашивает, запускает скачивание.
    Поддержка: Windows, MacOS, Linux.
    """
    for pl in playlists:
        pl_title = pl["title"] or "playlist"
        print(Fore.MAGENTA + f"\nНачало обработки плейлиста: {pl_title}" + Style.RESET_ALL)        
        folder = Path(output_path) / re.sub(r'[<>:"/\\|?*!]', '', pl_title)
        if pl["videos"]:
            print(Fore.CYAN + f"\nПлейлист: {pl_title} ({len(pl['videos'])} видео)" + Style.RESET_ALL)
            saved_list_path = print_playlist_paginated(pl["videos"], page_size=PAGE_SIZE, timeout=PAGE_TIMEOUT, playlist_title=pl_title)
            if saved_list_path and Path(saved_list_path).is_file():
                try:
                    dest_path = folder / Path(saved_list_path).name
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.move(saved_list_path, dest_path)
                    print(Fore.GREEN + f"Список видео перемещён в папку плейлиста: {dest_path}" + Style.RESET_ALL)
                except Exception as e:
                    print(Fore.RED + f"Не удалось переместить файл списка: {e}" + Style.RESET_ALL)
            print(Fore.CYAN + "\nВведите номера видео для скачивания (через запятую, пробелы, диапазоны через тире).\nEnter или 0 — скачать все:" + Style.RESET_ALL)
            sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL)
            selected_indexes = parse_selection(sel, len(pl["videos"]))
            selected_indexes = sorted(selected_indexes)
            if not selected_indexes:
                print(Fore.YELLOW + "Не выбрано ни одного видео. Пропуск плейлиста." + Style.RESET_ALL)
                continue
            print(Fore.GREEN + f"Будут скачаны видео: {', '.join(str(i) for i in selected_indexes)}" + Style.RESET_ALL)
            log_debug(f"Выбраны номера видео для скачивания из '{pl_title}': {selected_indexes}")

            # --- Новый блок: выбор ручной/автоматический режим для этого подплейлиста ---
            cmdline_auto_mode = auto_mode
            local_auto_mode = cmdline_auto_mode
            if not cmdline_auto_mode:
                user_auto = input(Fore.CYAN + "\nВыбрать параметры вручную для каждого видео? (1 — вручную, 0 — автоматически, Enter = 0): " + Style.RESET_ALL).strip()
                local_auto_mode = False if user_auto == '1' else True

            if local_auto_mode:
                first_idx = selected_indexes[0]
                entry = pl["videos"][first_idx - 1]
                entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                if not entry_url:
                    print(Fore.RED + f"Не удалось получить ссылку для первого видео. Пропуск." + Style.RESET_ALL)
                    continue
                print(Fore.YELLOW + f"\n=== Видео {first_idx} из плейлиста '{pl_title}' (выбор параметров) ===" + Style.RESET_ALL)
                try:
                    entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                except DownloadError as e:
                    if is_video_unavailable_error(e):
                        print(Fore.YELLOW + f"Видео {first_idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                        log_debug(f"Видео {first_idx} недоступно: {e}")
                        continue
                    else:
                        print(f"\n{Fore.RED}Ошибка загрузки видео {first_idx}: {e}{Style.RESET_ALL}")
                        log_debug(f"Ошибка загрузки видео {first_idx}: {e}")
                        continue
                except Exception as e:
                    print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {first_idx}: {e}{Style.RESET_ALL}")
                    log_debug(f"Ошибка при скачивании видео {first_idx}: {e}\n{traceback.format_exc()}")
                    continue

                video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'], auto_mode=False, bestvideo=args.bestvideo, bestaudio=args.bestaudio)
                subtitle_download_options = ask_and_select_subtitles(entry_info, auto_mode=False)
                output_format = ask_output_format(
                    desired_ext,
                    auto_mode=False,
                    subtitle_options=subtitle_download_options,
                    has_chapters=has_chapters
                )

                for idx in selected_indexes:
                    entry = pl["videos"][idx - 1]
                    entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                    if not entry_url:
                        print(Fore.RED + f"Не удалось получить ссылку для видео {idx}. Пропуск." + Style.RESET_ALL)
                        continue
                    print(Fore.YELLOW + f"\n=== Видео {idx} из плейлиста '{pl_title}' (автоматический режим) ===" + Style.RESET_ALL)
                    try:
                        try:
                            entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                        except DownloadError as e:
                            if is_video_unavailable_error(e):
                                print(Fore.YELLOW + f"Видео {idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                                log_debug(f"Видео {idx} недоступно: {e}")
                                continue
                            else:
                                print(f"\n{Fore.RED}Ошибка загрузки видео {idx}: {e}{Style.RESET_ALL}")
                                log_debug(f"Ошибка загрузки видео {idx}: {e}")
                                continue
                        except Exception as e:
                            print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
                            log_debug(f"Ошибка при скачивании видео {idx}: {e}\n{traceback.format_exc()}")
                            continue
                        video_fmt_auto = find_by_format_id(entry_info['formats'], video_id, is_video=True)
                        audio_fmt_auto = find_by_format_id(entry_info['formats'], audio_id, is_video=False) if audio_id else None
                        if not video_fmt_auto:
                            video_fmt_auto = find_best_video(entry_info['formats'], video_ext)
                        if audio_id and not audio_fmt_auto:
                            audio_fmt_auto = find_best_audio(entry_info['formats'], audio_ext)
                        video_id_auto = video_fmt_auto.get('format_id') if video_fmt_auto else None
                        audio_id_auto = audio_fmt_auto.get('format_id') if audio_fmt_auto else None
                        default_title = entry_info.get('title', f'video_{idx}')
                        safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                        output_name = get_unique_filename(safe_title, folder, output_format)
                        downloaded_file = download_video(
                            entry_url, video_id_auto, audio_id_auto, folder, output_name, output_format,
                            platform, cookie_file_to_use, subtitle_options=subtitle_download_options
                        )
                        if downloaded_file:
                            print(Fore.GREEN + f"Видео {idx} успешно скачано: {downloaded_file}" + Style.RESET_ALL)
                        else:
                            print(Fore.RED + f"Ошибка при скачивании видео {idx}." + Style.RESET_ALL)
                    except Exception as e:
                        print(f"\n{Fore.RED}Ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
            else:
                # --- Ручной режим: параметры для каждого видео ---
                for idx in selected_indexes:
                    entry = pl["videos"][idx - 1]
                    entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                    if not entry_url:
                        print(Fore.RED + f"Не удалось получить ссылку для видео {idx}. Пропуск." + Style.RESET_ALL)
                        continue
                    print(Fore.YELLOW + f"\n=== Видео {idx} из плейлиста '{pl_title}' ===" + Style.RESET_ALL)
                    try:
                        try:
                            entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                        except DownloadError as e:
                            if is_video_unavailable_error(e):
                                print(Fore.YELLOW + f"Видео {idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                                log_debug(f"Видео {idx} недоступно: {e}")
                                continue
                            else:
                                print(f"\n{Fore.RED}Ошибка загрузки видео {idx}: {e}{Style.RESET_ALL}")
                                log_debug(f"Ошибка загрузки видео {idx}: {e}")
                                continue
                        except Exception as e:
                            print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
                            log_debug(f"Ошибка при скачивании видео {idx}: {e}\n{traceback.format_exc()}")
                            continue
                        video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'])
                        subtitle_download_options = ask_and_select_subtitles(entry_info)
                        output_format = ask_output_format(
                            desired_ext,
                            auto_mode=False,
                            subtitle_options=subtitle_download_options,
                            has_chapters=has_chapters
                        )
                        default_title = entry_info.get('title', f'video_{idx}')
                        safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                        output_name = get_unique_filename(safe_title, folder, output_format)
                        downloaded_file = download_video(
                            entry_url, video_id, audio_id, folder, output_name, output_format,
                            platform, cookie_file_to_use, subtitle_options=subtitle_download_options
                        )
                        if downloaded_file:
                            print(Fore.GREEN + f"Видео {idx} успешно скачано: {downloaded_file}" + Style.RESET_ALL)
                        else:
                            print(Fore.RED + f"Ошибка при скачивании видео {idx}." + Style.RESET_ALL)
                    except Exception as e:
                        print(f"\n{Fore.RED}Ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
        if pl["sub_playlists"]:
            process_playlists(pl["sub_playlists"], folder, auto_mode, platform, args, cookie_file_to_use, f"{parent_path}/{pl_title}")

def print_playlists_tree(playlists, level=0):
    """
    Выводит дерево плейлистов с вложенностью.
    Поддержка: Windows, MacOS, Linux.
    """
    log_debug(f"print_playlists_tree: level={level}, playlists_count={len(playlists)}")
    for pl in playlists:
        indent = "  " * level
        print(f"{indent}- {pl['title'] or 'Без названия'} ({len(pl['videos'])} видео)")
        if pl["sub_playlists"]:
            print_playlists_tree(pl["sub_playlists"], level+1)

def collect_user_choices_for_playlists(playlists, output_path, auto_mode, platform, args, cookie_file_to_use, parent_path="", selected_video_ids=None):
    """
    Рекурсивно собирает пользовательские выборы по всем плейлистам.
    Возвращает список задач на скачивание: [{folder, entry, параметры...}]
    Поддержка: Windows, MacOS, Linux.
    """
    if selected_video_ids is None:
        selected_video_ids = {}

    tasks = []
    for pl in playlists:
        pl_title = pl["title"] or "playlist"
        print(Fore.MAGENTA + f"\nНачало обработки плейлиста: {pl_title}" + Style.RESET_ALL)        
        folder = Path(output_path) / re.sub(r'[<>:"/\\|?*!]', '', pl_title)
        add_index_prefix = True
        if pl["videos"]:
            answer = input(
                Fore.CYAN + f"\nДобавлять номер видео в начале имени файла для плейлиста '{pl_title}'? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL
            ).strip()
            if answer == "0":
                add_index_prefix = False
            log_debug(f"add_index_prefix для плейлиста '{pl_title}' = {add_index_prefix}")

            print(Fore.CYAN + f"\nПлейлист: {pl_title} ({len(pl['videos'])} видео)" + Style.RESET_ALL)
            saved_list_path = print_playlist_paginated(pl["videos"], page_size=PAGE_SIZE, timeout=PAGE_TIMEOUT, playlist_title=pl_title)
            if saved_list_path and Path(saved_list_path).is_file():
                try:
                    dest_path = folder / Path(saved_list_path).name
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.move(saved_list_path, dest_path)
                    print(Fore.GREEN + f"Список видео перемещён в папку плейлиста: {dest_path}" + Style.RESET_ALL)
                except Exception as e:
                    print(Fore.RED + f"Не удалось переместить файл списка: {e}" + Style.RESET_ALL)
            print(Fore.CYAN + "\nВведите номера видео для скачивания (через запятую, пробелы, диапазоны через тире).\nEnter или 0 — скачать все:" + Style.RESET_ALL)
            sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL)
            selected_indexes = parse_selection(sel, len(pl["videos"]))
            selected_indexes = sorted(selected_indexes)
            if not selected_indexes:
                print(Fore.YELLOW + "Не выбрано ни одного видео. Пропуск плейлиста." + Style.RESET_ALL)
            else:
                print(Fore.GREEN + f"Будут скачаны видео: {', '.join(str(i) for i in selected_indexes)}" + Style.RESET_ALL)
                log_debug(f"Выбраны номера видео для скачивания из '{pl_title}': {selected_indexes}")

                cmdline_auto_mode = auto_mode
                local_auto_mode = cmdline_auto_mode
                if not cmdline_auto_mode:
                    user_auto = input(Fore.CYAN + "\nВыбрать параметры вручную для каждого видео? (1 — вручную, 0 — автоматически, Enter = 0): " + Style.RESET_ALL).strip()
                    local_auto_mode = False if user_auto == '1' else True

                if local_auto_mode:
                    first_idx = selected_indexes[0]
                    entry = pl["videos"][first_idx - 1]
                    entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                    if not entry_url:
                        print(Fore.RED + f"Не удалось получить ссылку для первого видео. Пропуск." + Style.RESET_ALL)
                    else:
                        print(Fore.YELLOW + f"\n=== Видео {first_idx} из плейлиста '{pl_title}' (выбор параметров) ===" + Style.RESET_ALL)
                        try:
                            entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                        except DownloadError as e:
                            if is_video_unavailable_error(e):
                                print(Fore.YELLOW + f"Видео {first_idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                                log_debug(f"Видео {first_idx} недоступно: {e}")
                                continue
                            else:
                                print(f"\n{Fore.RED}Ошибка загрузки видео {first_idx}: {e}{Style.RESET_ALL}")
                                log_debug(f"Ошибка загрузки видео {first_idx}: {e}")
                                continue
                        except Exception as e:
                            print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {first_idx}: {e}{Style.RESET_ALL}")
                            log_debug(f"Ошибка при скачивании видео {first_idx}: {e}\n{traceback.format_exc()}")
                            continue
                        video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'], auto_mode=False, bestvideo=args.bestvideo, bestaudio=args.bestaudio)
                        subtitle_download_options = ask_and_select_subtitles(entry_info, auto_mode=False)
                        output_format = ask_output_format(
                            desired_ext,
                            auto_mode=False,
                            subtitle_options=subtitle_download_options,
                            has_chapters=has_chapters
                        )
                        # Можно добавить обработку глав и субтитров, если нужно

                        for idx in selected_indexes:
                            entry = pl["videos"][idx - 1]
                            entry_id = entry.get('id') or entry.get('url') or entry.get('webpage_url')
                            if entry_id in selected_video_ids:
                                prev_pl = selected_video_ids[entry_id]
                                print(Fore.YELLOW + f"\nВидео '{entry.get('title', entry_id)}' уже выбрано для скачивания в плейлисте '{prev_pl}'.")
                                ans = input(Fore.CYAN + "Скачать ещё раз? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                                if ans != "1":
                                    print(Fore.YELLOW + "Пропуск этого видео." + Style.RESET_ALL)
                                    continue
                            selected_video_ids[entry_id] = pl_title
                            # --- Формируем имя с префиксом, если нужно ---
                            default_title = entry.get('title', f'video_{idx}')
                            safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                            if add_index_prefix:
                                safe_title = f"{idx:02d} {safe_title}"
                            tasks.append({
                                "folder": folder,
                                "entry": entry,
                                "video_id": video_id,
                                "audio_id": audio_id,
                                "output_format": output_format,
                                "subtitle_options": subtitle_download_options,
                                "platform": platform,
                                "cookie_file_to_use": cookie_file_to_use,
                                "add_index_prefix": add_index_prefix,
                                "index_number": idx,
                                "safe_title": safe_title,
                            })
                else:
                    # Ручной режим: параметры для каждого видео
                    for idx in selected_indexes:
                        entry = pl["videos"][idx - 1]
                        entry_id = entry.get('id') or entry.get('url') or entry.get('webpage_url')
                        if entry_id in selected_video_ids:
                            prev_pl = selected_video_ids[entry_id]
                            print(Fore.YELLOW + f"\nВидео '{entry.get('title', entry_id)}' уже выбрано для скачивания в плейлисте '{prev_pl}'.")
                            ans = input(Fore.CYAN + "Скачать ещё раз? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                            if ans != "1":
                                print(Fore.YELLOW + "Пропуск этого видео." + Style.RESET_ALL)
                                continue
                        selected_video_ids[entry_id] = pl_title
                        entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                        if not entry_url:
                            print(Fore.RED + f"Не удалось получить ссылку для видео {idx}. Пропуск." + Style.RESET_ALL)
                            continue
                        print(Fore.YELLOW + f"\n=== Видео {idx} из плейлиста '{pl_title}' ===" + Style.RESET_ALL)
                        entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                        video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'])
                        subtitle_download_options = ask_and_select_subtitles(entry_info)
                        output_format = ask_output_format(
                            desired_ext,
                            auto_mode=False,
                            subtitle_options=subtitle_download_options,
                            has_chapters=has_chapters
                        )
                        default_title = entry.get('title', f'video_{idx}')
                        safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                        if add_index_prefix:
                            safe_title = f"{idx:02d} {safe_title}"
                        tasks.append({
                            "folder": folder,
                            "entry": entry,
                            "video_id": video_id,
                            "audio_id": audio_id,
                            "output_format": output_format,
                            "subtitle_options": subtitle_download_options,
                            "platform": platform,
                            "cookie_file_to_use": cookie_file_to_use,
                            "add_index_prefix": add_index_prefix,
                            "index_number": idx,
                            "safe_title": safe_title,
                        })
        # Рекурсивно для подплейлистов
        if pl["sub_playlists"]:
            tasks.extend(collect_user_choices_for_playlists(
                pl["sub_playlists"], folder, auto_mode, platform, args, cookie_file_to_use, f"{parent_path}/{pl_title}", selected_video_ids
            ))
    return tasks

def download_tasks(tasks):
    """
    Выполняет скачивание по списку задач, собранных collect_user_choices_for_playlists.
    Поддержка: Windows, MacOS, Linux.
    """
    for task in tasks:
        entry = task["entry"]
        entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
        if not entry_url:
            print(Fore.RED + "Не удалось получить ссылку для видео. Пропуск." + Style.RESET_ALL)
            continue

        entry_info = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                entry_info = safe_get_video_info(entry_url, task["platform"], task["cookie_file_to_use"])
                break
            except DownloadError as e:
                err_text = str(e).lower()
                if is_video_unavailable_error(e):
                    print(Fore.YELLOW + f"Видео недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                    log_debug(f"Видео недоступно: {e}")
                    break
                if "cannot parse data" in err_text or "extractorerror" in err_text or "unsupported site" in err_text:
                    fallback_download(entry_url)
                    break
                if "network" in err_text or "timeout" in err_text or "connection" in err_text or "http error" in err_text:
                    print(Fore.RED + f"Ошибка сети при получении информации о видео (попытка {attempt}/{MAX_RETRIES}). Проверьте интернет и попробуйте снова." + Style.RESET_ALL)
                    if attempt < MAX_RETRIES:
                        time.sleep(5)
                        continue
                    else:
                        break
                print(Fore.RED + f"Ошибка загрузки видео: {e}" + Style.RESET_ALL)
                log_debug(f"Ошибка загрузки видео: {e}")
                break
            except Exception as e:
                print(Fore.RED + f"Непредвидённая ошибка при скачивании видео: {e}" + Style.RESET_ALL)
                log_debug(f"Ошибка при скачивании видео: {e}\n{traceback.format_exc()}")
                break
        if not entry_info:
            continue  # если не удалось получить entry_info, пропускаем видео

        formats = entry_info.get('formats', [])

        # --- Fallback-поиск формата, если выбранный не найден ---
        video_id = task["video_id"]
        audio_id = task["audio_id"]
        video_ext = None
        audio_ext = None

        # Ищем видеоформат по format_id
        video_fmt = find_by_format_id(formats, video_id, is_video=True)
        if not video_fmt:
            # Если не найден — ищем лучший совместимый
            video_fmt = find_best_video(formats, task["output_format"])
            if video_fmt:
                print(Fore.YELLOW + f"Для видео '{entry_info.get('title', entry_url)}' не найден выбранный формат ({video_id}), выбран ближайший: {video_fmt.get('format_id')}" + Style.RESET_ALL)
                log_debug(f"Fallback: не найден видеоформат {video_id}, выбран {video_fmt.get('format_id')}")
            else:
                print(Fore.RED + f"Не найден подходящий видеоформат для '{entry_info.get('title', entry_url)}'. Пропуск." + Style.RESET_ALL)
                log_debug(f"Не найден подходящий видеоформат для {entry_url}")
                continue
        video_id_final = video_fmt.get('format_id')
        video_ext = video_fmt.get('ext', '')

        # Аналогично для аудио
        audio_fmt = None
        audio_id_final = None
        if audio_id:
            audio_fmt = find_by_format_id(formats, audio_id, is_video=False)
            if not audio_fmt:
                audio_fmt = find_best_audio(formats, task["output_format"])
                if audio_fmt:
                    print(Fore.YELLOW + f"Для видео '{entry_info.get('title', entry_url)}' не найден выбранный аудиоформат ({audio_id}), выбран ближайший: {audio_fmt.get('format_id')}" + Style.RESET_ALL)
                    log_debug(f"Fallback: не найден аудиоформат {audio_id}, выбран {audio_fmt.get('format_id')}")
                else:
                    print(Fore.YELLOW + f"Не найден подходящий аудиоформат для '{entry_info.get('title', entry_url)}'. Будет использован звук из видео." + Style.RESET_ALL)
            if audio_fmt:
                audio_id_final = audio_fmt.get('format_id')
                audio_ext = audio_fmt.get('ext', '')

        # --- Используем safe_title из task, если есть ---
        if "safe_title" in task:
            output_name = get_unique_filename(task["safe_title"], task["folder"], task["output_format"])
        else:
            default_title = entry_info.get('title', 'video')
            safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
            output_name = get_unique_filename(safe_title, task["folder"], task["output_format"])

        downloaded_file = download_video(
            entry_url, video_id_final, audio_id_final, task["folder"], output_name, task["output_format"],
            task["platform"], task["cookie_file_to_use"], subtitle_options=task["subtitle_options"]
        )
        if downloaded_file:
            print(Fore.GREEN + f"Видео успешно скачано: {downloaded_file}" + Style.RESET_ALL)
            # --- Переименование автоматических субтитров с .auto ---
            subtitle_download_options = task.get("subtitle_options")
            output_name = output_name  # уже определён выше
            output_path = task["folder"]
            if subtitle_download_options:
                auto_suffix = subtitle_download_options.get('add_auto_suffix', True)
                auto_langs = subtitle_download_options.get('automatic_subtitles_langs', [])
                sub_format = subtitle_download_options.get('subtitlesformat', 'srt')
                if auto_suffix and output_name and output_path:
                    for lang in auto_langs:
                        orig_file = Path(output_path) / f"{output_name}.{lang}.{sub_format}"
                        new_file = Path(output_path) / f"{output_name}.{lang}.auto.{sub_format}"
                        if orig_file.exists():
                            try:
                                orig_file.rename(new_file)
                                print(Fore.YELLOW + f"Файл автоматических субтитров переименован: {new_file.name}" + Style.RESET_ALL)
                                log_debug(f"Переименован файл автоматических субтитров: {orig_file} -> {new_file}")
                            except Exception as e:
                                print(Fore.RED + f"Ошибка при переименовании субтитров: {e}" + Style.RESET_ALL)
                                log_debug(f"Ошибка при переименовании субтитров: {e}")
        else:
            print(Fore.RED + f"Ошибка при скачивании видео." + Style.RESET_ALL)

def is_youtube_channel_url(url: str) -> bool:
    """
    Проверяет, является ли ссылка ссылкой на канал YouTube (не на видео, не на плейлист).
    https://www.youtube.com/@username
    https://www.youtube.com/channel/UC...
    https://www.youtube.com/c/...
    но не содержит /playlists, /videos, /shorts, /live, /community и т.п.
    Поддержка: Windows, MacOS, Linux.
    """
    channel_patterns = [
        r'^https?://(www\.)?youtube\.com/@[^/]+/?$',
        r'^https?://(www\.)?youtube\.com/channel/[^/]+/?$',
        r'^https?://(www\.)?youtube\.com/c/[^/]+/?$',
    ]
    for pat in channel_patterns:
        if re.match(pat, url, re.I):
            return True
    return False

def is_youtube_playlists_url(url: str) -> bool:
    """
    Проверяет, является ли ссылка ссылкой на раздел плейлистов YouTube-канала.
    Поддержка: Windows, MacOS, Linux.
    """
    return bool(re.match(r'^https?://(www\.)?youtube\.com/(c/|channel/|@)[^/]+/playlists/?$', url, re.I))

def get_youtube_playlists_url(channel_url: str) -> str:
    """
    Возвращает ссылку на раздел плейлистов для данного канала.
    Поддержка: Windows, MacOS, Linux.
    """
    if channel_url.endswith('/'):
        return channel_url + 'playlists'
    else:
        return channel_url + '/playlists'

def parse_time_to_ms(t: str) -> int:
    """
    Преобразует строку времени SRT в миллисекунды.
    Поддержка: Windows, MacOS, Linux.
    """
    h, m, s_ms = t.split(":")
    s, ms = s_ms.split(",")
    return (int(h)*3600 + int(m)*60 + int(s)) * 1000 + int(ms)

def ms_to_srt_time(ms: int) -> str:
    """
    Преобразует миллисекунды в строку времени SRT.
    Поддержка: Windows, MacOS, Linux.
    """
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms = ms % 1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def parse_srt(path: str) -> List[Caption]:
    """
    Парсит SRT-файл, возвращает список Caption.
    Поддержка: Windows, MacOS, Linux.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    caps: List[Caption] = []
    for m in SRT_BLOCK_RE.finditer(content):
        idx = int(m.group(1))
        start = parse_time_to_ms(m.group(2))
        end = parse_time_to_ms(m.group(3))
        text = re.sub(r"[ \t]+\n", "\n", m.group(4).strip())
        text = re.sub(r"[ \t]{2,}", " ", text)
        if end <= start or not text.strip():
            continue
        caps.append(Caption(idx, start, end, text))
    caps.sort(key=lambda c: (c.start, c.end, c.idx))
    return caps

def normalize_by_pairs_strict(caps: List[Caption]) -> List[Caption]:
    """
    Нормализует субтитры: убирает перекрытия, объединяет блоки.
    Поддержка: Windows, MacOS, Linux.
    """
    out: List[Caption] = []
    i = 0
    n = len(caps)
    while i < n:
        top = caps[i]
        if i + 1 < n:
            bottom = caps[i+1]
            if not (top.end <= bottom.start or bottom.end <= top.start):
                block_start = top.start
                if i + 2 < n:
                    next_first_start = caps[i+2].start
                    if bottom.end <= next_first_start:
                        block_end = bottom.end
                    else:
                        block_end = next_first_start - INTER_CAPTION_GAP_MS
                else:
                    block_end = bottom.end
                if block_end < block_start + MIN_DISPLAY_MS:
                    block_end = block_start + MIN_DISPLAY_MS
                out.append(Caption(len(out)+1, block_start, block_end, top.text + "\n" + bottom.text))
                i += 2
                continue
        block_start = top.start
        if i + 1 < n:
            next_start = caps[i+1].start
            if top.end <= next_start:
                block_end = top.end
            else:
                block_end = next_start - INTER_CAPTION_GAP_MS
        else:
            block_end = top.end
        if block_end < block_start + MIN_DISPLAY_MS:
            block_end = block_start + MIN_DISPLAY_MS
        out.append(Caption(len(out)+1, block_start, block_end, top.text))
        i += 1
    for k in range(1, len(out)):
        prev = out[k-1]
        cur = out[k]
        if cur.start < prev.end:
            prev.end = cur.start
            if prev.end < prev.start + MIN_DISPLAY_MS:
                prev.end = prev.start + MIN_DISPLAY_MS
                if prev.end > cur.start:
                    cur.start = prev.end
                    if cur.end < cur.start + MIN_DISPLAY_MS:
                        cur.end = cur.start + MIN_DISPLAY_MS
    for idx, c in enumerate(out, start=1):
        if c.end < c.start:
            c.end = c.start + MIN_DISPLAY_MS
        c.idx = idx
    return out

def write_srt(path: str, caps: List[Caption]):
    """
    Записывает список Caption в SRT-файл.
    Поддержка: Windows, MacOS, Linux.
    """
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, start=1):
            f.write(f"{i}\n{ms_to_srt_time(c.start)} --> {ms_to_srt_time(c.end)}\n{c.text}\n\n")

def normalize_srt_file(inp: str, overwrite: bool = True, backup: bool = False):
    """
    Нормализует SRT-файл, создаёт резервную копию при необходимости.
    Поддержка: Windows, MacOS, Linux.
    """
    if not Path(inp).exists():
        print(f"Input file not found: {inp}")
        return
    caps = parse_srt(inp)
    norm = normalize_by_pairs_strict(caps)
    if overwrite:
        if backup:
            bakfile = Path(inp).with_suffix('.bak')
            if not bakfile.exists():
                Path(inp).replace(bakfile)
                print(f"Backup created: {bakfile}")
        target = inp
    else:
        target = str(Path(inp).with_suffix('')) + "_normalized.srt"
    write_srt(target, norm)
    print(f"Processed {inp} -> {target} ({len(norm)} blocks)")

def main():
    """
    Главная функция: запускает обработку, парсинг, скачивание.
    Весь пользовательский ввод и основной цикл работы.
    Поддержка: Windows, MacOS, Linux.
    """
    global USER_SELECTED_SUB_LANGS, USER_SELECTED_SUB_FORMAT, USER_INTEGRATE_SUBS, USER_KEEP_SUB_FILES
    global USER_INTEGRATE_CHAPTERS, USER_KEEP_CHAPTER_FILE, USER_SELECTED_VIDEO_CODEC, USER_SELECTED_AUDIO_CODEC
    global USER_SELECTED_OUTPUT_FORMAT, USER_SELECTED_CHAPTER_FILENAME, USER_SELECTED_OUTPUT_NAME, USER_SELECTED_OUTPUT_PATH

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

    args = parse_args()
    auto_mode = args.auto
    raw_url = args.url
    while True:
        if not raw_url:
            raw_url = input(Fore.CYAN + "Введите ссылку: " + Style.RESET_ALL).strip()
            if not raw_url:
                continue
        else:
            print(Fore.CYAN + f"Ссылка получена из командной строки: {raw_url}" + Style.RESET_ALL)
        log_debug(f"Введена ссылка: {raw_url}")

        platform, url = extract_platform_and_url(raw_url)
        log_debug(f"Определена платформа: {platform}, очищенный URL: {url}")

        # --- Единоразовая проверка куки и ссылки с повторными попытками ---
        cookie_map = {
            "youtube":  COOKIES_YT,
            "facebook": COOKIES_FB,
            "vimeo":    COOKIES_VI,
            "rutube":   COOKIES_RT,
            "vk":       COOKIES_VK,
        }
        cookie_file_to_use = None
        info = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if platform in cookie_map:
                    cookie_file_to_use = get_cookies_for_platform(platform, cookie_map[platform], url)
                else:
                    cookie_file_to_use = None

                info = get_video_info(url, platform, cookie_file_to_use)
                break  # если успешно, выходим из цикла
            except DownloadError as e:
                err_text = str(e).lower()
                if "not a valid url" in err_text or "is not a valid url" in err_text:
                    print(Fore.RED + "Введена некорректная ссылка. Попробуйте снова." + Style.RESET_ALL)
                    raw_url = None
                    break
                if "cannot parse data" in err_text or "extractorerror" in err_text or "unsupported site" in err_text:
                    fallback_download(url)
                    raw_url = None
                    break
                if "network" in err_text or "timeout" in err_text or "connection" in err_text or "http error" in err_text:
                    print(Fore.RED + f"Ошибка сети при получении информации о видео (попытка {attempt}/{MAX_RETRIES}). Проверьте интернет и попробуйте снова." + Style.RESET_ALL)
                    if attempt < MAX_RETRIES:
                        time.sleep(5)
                        continue
                    else:
                        raw_url = None
                        break
                print(Fore.RED + f"Ошибка загрузки видео: {e}" + Style.RESET_ALL)
                raw_url = None
                break
            except Exception as e:
                print(Fore.RED + f"Непредвидённая ошибка: {e}" + Style.RESET_ALL)
                log_debug(f"main: ошибка при проверке ссылки/куки: {e}\n{traceback.format_exc()}")
                raw_url = None
                break

        if not info:
            continue  # если не удалось получить info, запрашиваем ссылку заново

        # После успешной проверки raw_url больше не нужен
        raw_url = None
        break

    # --- ИНИЦИАЛИЗАЦИЯ переменных для предотвращения ошибок ---
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
    saved_list_path = None

#    try:
    # platform и url уже определены выше!
    if platform == "youtube" and (is_youtube_channel_url(url) or is_youtube_playlists_url(url)):
        selected_video_ids = {}

        channel_url = url
        if is_youtube_playlists_url(url):
            channel_url = re.sub(r'/playlists/?$', '', url)
        print(Fore.YELLOW + "Получаем информацию по разделам канала..." + Style.RESET_ALL)
        info_channel = safe_get_video_info(channel_url, platform, cookie_file_to_use)
        channel_entries = info_channel.get('entries', [])
        sections = []
        for idx, entry in enumerate(channel_entries, 1):
            title = entry.get('title') or entry.get('id') or entry.get('url') or f"Раздел {idx}"
            sections.append((idx, title, entry))

        playlists_struct = []
        info_playlists = None
        section_playlists = []

        # --- Сперва плейлисты, если ссылка на /playlists ---
        if is_youtube_playlists_url(url):
            print(Fore.YELLOW + "\nОбнаружена ссылка на раздел плейлистов канала YouTube." + Style.RESET_ALL)
            playlists_url = get_youtube_playlists_url(channel_url)
            print(Fore.YELLOW + "Получаем информацию по плейлистам канала..." + Style.RESET_ALL)
            info_playlists = safe_get_video_info(playlists_url, platform, cookie_file_to_use)
            playlists_entries = info_playlists.get('entries', [])
            # Выводим список плейлистов
            playlist_infos = []
            for idx, pl in enumerate(playlists_entries, 1):
                pl_title = pl.get('title') or pl.get('id') or pl.get('url') or f"Плейлист {idx}"
                pl_url = pl.get('url') or pl.get('webpage_url')
                count = 0
                if pl_url:
                    try:
                        pl_info = safe_get_video_info(pl_url, platform, cookie_file_to_use)
                        count = len(pl_info.get('entries', []))
                    except Exception:
                        count = 0
                playlist_infos.append((idx, pl_title, count))
            print(Fore.MAGENTA + "\nПлейлисты канала:" + Style.RESET_ALL)
            for idx, pl_title, count in playlist_infos:
                print(f"{idx}: {pl_title} ({count} видео)")
            sel_pl = input(Fore.CYAN + "Введите номера плейлистов для скачивания (через пробел, запятую, диапазоны через тире; Enter — все): " + Style.RESET_ALL).strip()
            if not sel_pl:
                selected_playlists = playlists_entries
            else:
                selected_pl_indexes = parse_selection(sel_pl, len(playlists_entries))
                selected_playlists = [pl for idx, pl in enumerate(playlists_entries, 1) if idx in selected_pl_indexes]
            playlists_struct = collect_playlists(selected_playlists, platform, info_playlists.get('__cookiefile__'))

            # После выбора плейлистов спрашиваем про разделы
            answer = input(Fore.CYAN + "\nХотите также скачать видео из других разделов канала (Видео, Shorts и т.д.), которые не входят в плейлисты? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
            want_sections = (answer == "1")
            if want_sections:
                print(Fore.MAGENTA + "\nРазделы канала:" + Style.RESET_ALL)
                for idx, title, entry in sections:
                    count = len(entry.get('entries', [])) if entry.get('entries') else 0
                    print(f"{idx}: {title} ({count} видео)")
                sel = input(Fore.CYAN + "Введите номера разделов для скачивания (через запятую, Enter — все): " + Style.RESET_ALL).strip()
                if not sel:
                    selected_sections = [entry for _, _, entry in sections]
                else:
                    selected_indexes = [int(s) for s in re.split(r'[ ,;]+', sel) if s.strip().isdigit()]
                    selected_sections = [entry for idx, _, entry in sections if idx in selected_indexes]
                for idx, section in enumerate(selected_sections, 1):
                    title = section.get('title') or section.get('id') or section.get('url') or f"Раздел {idx}"
                    section_entries = section.get('entries', [])
                    section_playlists.append({
                        "title": title,
                        "videos": section_entries,
                        "sub_playlists": []
                    })
        else:
            # Ссылка на канал, сперва разделы
            print(Fore.YELLOW + "\nОбнаружена ссылка на канал YouTube." + Style.RESET_ALL)
            print(Fore.MAGENTA + "\nРазделы канала:" + Style.RESET_ALL)
            for idx, title, entry in sections:
                count = len(entry.get('entries', [])) if entry.get('entries') else 0
                print(f"{idx}: {title} ({count} видео)")
            sel = input(Fore.CYAN + "Введите номера разделов для скачивания (через запятую, Enter — все): " + Style.RESET_ALL).strip()
            if not sel:
                selected_sections = [entry for _, _, entry in sections]
            else:
                selected_indexes = [int(s) for s in re.split(r'[ ,;]+', sel) if s.strip().isdigit()]
                selected_sections = [entry for idx, _, entry in sections if idx in selected_indexes]
            for idx, section in enumerate(selected_sections, 1):
                title = section.get('title') or section.get('id') or section.get('url') or f"Раздел {idx}"
                section_entries = section.get('entries', [])
                section_playlists.append({
                    "title": title,
                    "videos": section_entries,
                    "sub_playlists": []
                })
            # После выбора разделов спрашиваем про плейлисты
            answer = input(Fore.CYAN + "\nХотите также скачать видео из плейлистов этого канала? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            want_playlists = (answer != "0")
            if want_playlists:
                playlists_url = get_youtube_playlists_url(channel_url)
                print(Fore.YELLOW + "Получаем информацию по плейлистам канала..." + Style.RESET_ALL)
                info_playlists = safe_get_video_info(playlists_url, platform, cookie_file_to_use)
                playlists_entries = info_playlists.get('entries', [])
                playlist_infos = []
                for idx, pl in enumerate(playlists_entries, 1):
                    pl_title = pl.get('title') or pl.get('id') or pl.get('url') or f"Плейлист {idx}"
                    pl_url = pl.get('url') or pl.get('webpage_url')
                    count = 0
                    if pl_url:
                        try:
                            pl_info = safe_get_video_info(pl_url, platform, cookie_file_to_use)
                            count = len(pl_info.get('entries', []))
                        except Exception:
                            count = 0
                    playlist_infos.append((idx, pl_title, count))
                print(Fore.MAGENTA + "\nПлейлисты канала:" + Style.RESET_ALL)
                for idx, pl_title, count in playlist_infos:
                    print(f"{idx}: {pl_title} ({count} видео)")
                sel_pl = input(Fore.CYAN + "Введите номера плейлистов для скачивания (через пробел, запятую, диапазоны через тире; Enter — все): " + Style.RESET_ALL).strip()
                if not sel_pl:
                    selected_playlists = playlists_entries
                else:
                    selected_pl_indexes = parse_selection(sel_pl, len(playlists_entries))
                    selected_playlists = [pl for idx, pl in enumerate(playlists_entries, 1) if idx in selected_pl_indexes]
                playlists_struct = collect_playlists(selected_playlists, platform, info_playlists.get('__cookiefile__'))

        # Собираем задачи
        all_tasks = []
        output_path = select_output_folder(auto_mode=False)
        if section_playlists:
            all_tasks += collect_user_choices_for_playlists(
                section_playlists, output_path, auto_mode, platform, args, info_channel.get('__cookiefile__'), selected_video_ids=selected_video_ids
            )
        if playlists_struct:
            all_tasks += collect_user_choices_for_playlists(
                playlists_struct, output_path, auto_mode, platform, args, info_playlists.get('__cookiefile__'), selected_video_ids=selected_video_ids
            )
        if all_tasks:
            print(Fore.YELLOW + "\nВсе параметры выбраны. Начинается скачивание всех выбранных видео..." + Style.RESET_ALL)
            download_tasks(all_tasks)
            print(Fore.CYAN + "\nВсе выбранные видео обработаны." + Style.RESET_ALL)
        else:
            print(Fore.YELLOW + "Нет выбранных видео для скачивания." + Style.RESET_ALL)
        return

    info = safe_get_video_info(url, platform, cookie_file_to_use)
    cookie_file_to_use = info.get('__cookiefile__')

    # --- Обработка плейлиста ---
    if info.get('_type') == 'playlist' or 'entries' in info:
        entries = info.get('entries', [])
        log_debug(f"main: entries (type={type(entries)}, len={len(entries) if hasattr(entries, '__len__') else 'N/A'}): {str(entries)[:500]}")
        # --- Строим структуру плейлистов ---
        print(Fore.YELLOW + "\nАнализируем структуру канала/плейлиста, ищем вложенные плейлисты..." + Style.RESET_ALL)
        playlists_struct = collect_playlists(info.get('entries', []), platform, cookie_file_to_use)
        log_debug(f"main: playlists_struct (type={type(playlists_struct)}, len={len(playlists_struct) if hasattr(playlists_struct, '__len__') else 'N/A'}): {str(playlists_struct)[:500]}")

        if playlists_struct and all(
            pl.get("videos") == [] and pl.get("sub_playlists") == [] and pl.get("url")
            for pl in playlists_struct
        ):
            log_debug("main: Ветка — только плейлисты верхнего уровня (страница /playlists)")
            print(Fore.YELLOW + "\nОбнаружены плейлисты канала! Будет произведён обход по каждому из них." + Style.RESET_ALL)
            output_path = select_output_folder(auto_mode=False)
            USER_SELECTED_OUTPUT_PATH = output_path
            print(Fore.YELLOW + "\nНайдены плейлисты:" + Style.RESET_ALL)
            print_playlists_tree(playlists_struct)
            tasks = collect_user_choices_for_playlists(playlists_struct, output_path, auto_mode, platform, args, cookie_file_to_use, selected_video_ids={})
            print(Fore.YELLOW + "\nВсе параметры выбраны. Начинается скачивание всех выбранных видео..." + Style.RESET_ALL)
            download_tasks(tasks)
            print(Fore.CYAN + "\nВсе выбранные видео из всех плейлистов обработаны." + Style.RESET_ALL)
            return

        if not has_nested_playlists(playlists_struct) and len(playlists_struct) == 1:
            log_debug("main: Ветка — один плейлист, без вложенных")
            pl = playlists_struct[0]
            # Обычный режим, когда videos — это видео
            all_videos = []
            for pl in playlists_struct:
                all_videos.extend(pl["videos"])
            log_debug(f"main: all_videos (len={len(all_videos)}): {str(all_videos)[:500]}")
            print(Fore.YELLOW + f"\nВсего найдено видео: {len(all_videos)}" + Style.RESET_ALL)
            if not all_videos:
                print(Fore.RED + "В канале не найдено ни одного видео." + Style.RESET_ALL)
                return
            playlist_title = info.get('title') or "playlist"
            saved_list_path = print_playlist_paginated(all_videos, page_size=PAGE_SIZE, timeout=PAGE_TIMEOUT, playlist_title=playlist_title)
        else:
            # Есть вложенные плейлисты
            log_debug("main: Ветка — есть вложенные плейлисты")
            log_debug(f"main: playlists_struct (подробно): {str(playlists_struct)[:1000]}")
            print(Fore.YELLOW + "\nОбнаружены вложенные плейлисты! Будет произведён обход по каждому из них." + Style.RESET_ALL)
            output_path = select_output_folder(auto_mode=False)
            USER_SELECTED_OUTPUT_PATH = output_path

            # Выводим список всех плейлистов с количеством видео
            print(Fore.YELLOW + "\nНайдены вложенные плейлисты:" + Style.RESET_ALL)

            print_playlists_tree(playlists_struct)

            # process_playlists(playlists_struct, output_path, auto_mode, platform, args, cookie_file_to_use)
            # --- Новый порядок: сначала собираем все задачи, потом скачиваем ---
            tasks = collect_user_choices_for_playlists(playlists_struct, output_path, auto_mode, platform, args, cookie_file_to_use, selected_video_ids={})
            print(Fore.YELLOW + "\nВсе параметры выбраны. Начинается скачивание всех выбранных видео..." + Style.RESET_ALL)
            download_tasks(tasks)
            print(Fore.CYAN + "\nВсе выбранные видео из всех плейлистов обработаны." + Style.RESET_ALL)
            return
        # --- Спрашиваем про добавление индекса к имени файла ---
        add_index_prefix = True
        answer = input(Fore.CYAN + "\nДобавлять номер видео в начале имени файла? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
        if answer == "0":
            add_index_prefix = False
        log_debug(f"add_index_prefix = {add_index_prefix}")

        print(Fore.CYAN + "\nВведите номера видео для скачивания (через запятую, пробелы, диапазоны через тире).\nEnter или 0 — скачать все:" + Style.RESET_ALL)
        sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL)
        selected_indexes = parse_selection(sel, len(entries))
        selected_indexes = sorted(selected_indexes)  # всегда список, чтобы можно было обращаться по индексу
        if not selected_indexes:
            print(Fore.YELLOW + "Не выбрано ни одного видео. Завершение." + Style.RESET_ALL)
            return
        print(Fore.GREEN + f"Будут скачаны видео: {', '.join(str(i) for i in selected_indexes)}" + Style.RESET_ALL)
        log_debug(f"Выбраны номера видео для скачивания: {selected_indexes}")

        cmdline_auto_mode = auto_mode  # сохраняем, что было в аргументах
        if not cmdline_auto_mode:
            user_auto = input(Fore.CYAN + "\nВыбрать параметры вручную для каждого видео? (1 — вручную, 0 — автоматически, Enter = 0): " + Style.RESET_ALL).strip()
            auto_mode = False if user_auto == '1' else True
        # если был --auto, auto_mode уже True и не спрашиваем пользователя

        # --- Если автоматический режим, запрашиваем параметры только для первого видео ---
        if auto_mode:
            first_idx = selected_indexes[0]
            entry = entries[first_idx - 1]
            entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
            if not entry_url:
                print(Fore.RED + f"Не удалось получить ссылку для первого видео. Прерывание." + Style.RESET_ALL)
                log_debug(f"Нет ссылки для первого видео {first_idx}")
                return
            print(Fore.YELLOW + f"\n=== Видео {first_idx} из плейлиста (выбор параметров) ===" + Style.RESET_ALL)
            entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
            cookie_file_to_use = entry_info.get('__cookiefile__')
            chapters = entry_info.get("chapters")
            has_chapters = isinstance(chapters, list) and len(chapters) > 0
            # ВАЖНО: auto_mode=False для первого видео!
            video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'], auto_mode=False, bestvideo=args.bestvideo, bestaudio=args.bestaudio)                
            USER_SELECTED_VIDEO_CODEC = video_codec
            USER_SELECTED_AUDIO_CODEC = audio_codec
            if video_id == "bestvideo+bestaudio/best":
                quality_map = {
                    "0": ("bestvideo+bestaudio/best", "Максимальное"),
                    "1": ("bestvideo[height<=1080]+bestaudio/best", "≤ 1080p"),
                    "2": ("bestvideo[height<=720]+bestaudio/best",  "≤ 720p"),
                    "3": ("bestvideo[height<=480]+bestaudio/best",  "≤ 480p"),
                    "4": ("bestvideo[height<=360]+bestaudio/best",  "≤ 360p"),
                }
                print(Fore.CYAN + "\nВыберите желаемое качество DASH/HLS:" + Style.RESET_ALL)
                for key, (_, label) in quality_map.items():
                    print(f"{key}: {label}")
                choice = input(Fore.CYAN + "Номер (Enter = 0): " + Style.RESET_ALL).strip() or "0"
                selected = quality_map.get(choice, quality_map["0"])
                video_id = selected[0]
                log_debug(f"Пользователь выбрал профиль DASH: {video_id}")
            subtitle_download_options = ask_and_select_subtitles(entry_info, auto_mode=False)
            save_chapter_file = False
            integrate_chapters = False
            keep_chapter_file = False
            chapter_filename = None
            USER_INTEGRATE_CHAPTERS = integrate_chapters
            USER_KEEP_CHAPTER_FILE = keep_chapter_file
            USER_SELECTED_CHAPTER_FILENAME = chapter_filename
            if has_chapters:
                ask_chaps = input(Fore.CYAN + "Видео содержит главы. Сохранить главы в файл? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                save_chapter_file = ask_chaps != "0"
                log_debug(f"Пользователь выбрал сохранить главы: {save_chapter_file}")
            output_path = select_output_folder(auto_mode=False)
            USER_SELECTED_OUTPUT_PATH = output_path
            if saved_list_path and Path(saved_list_path).is_file():
                try:
                    dest_path = Path(output_path) / Path(saved_list_path).name
                    Path(saved_list_path).replace(dest_path)
                    print(Fore.GREEN + f"Список видео перемещён в папку сохранения: {dest_path}" + Style.RESET_ALL)
                except Exception as e:
                    print(Fore.RED + f"Не удалось переместить файл списка: {e}" + Style.RESET_ALL)
            output_format = ask_output_format(
                desired_ext,
                auto_mode=False,
                subtitle_options=subtitle_download_options,
                has_chapters=has_chapters
            )
            USER_SELECTED_OUTPUT_FORMAT = output_format
            integrate_subs = False
            keep_sub_files = True
            subs_to_integrate_langs = []
            USER_SELECTED_SUB_LANGS = subs_to_integrate_langs.copy()
            USER_SELECTED_SUB_FORMAT = subtitle_download_options.get('subtitlesformat') if subtitle_download_options else None
            USER_INTEGRATE_SUBS = integrate_subs
            USER_KEEP_SUB_FILES = keep_sub_files
            if output_format.lower() == 'mkv' and subtitle_download_options and subtitle_download_options.get('subtitleslangs'):
                available_langs = subtitle_download_options['subtitleslangs']
                print(Fore.CYAN + "\nКакие субтитры интегрировать в итоговый MKV?"
                        "\n  Введите номера или коды языков (через запятую или пробел)."
                        "\n  Enter, 0 или all — интегрировать ВСЕ."
                        "\n  «-» (минус) — не интегрировать ничего." + Style.RESET_ALL)
                for sidx, lang in enumerate(available_langs, 1):
                    print(f"{sidx}: {lang}")
                sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL).strip()
                if sel in ("", "0", "all"):
                    integrate_subs = True
                    subs_to_integrate_langs = available_langs.copy()
                elif sel == "-":
                    integrate_subs = False
                else:
                    parts = [s.strip() for s in re.split(r"[\s,]+", sel) if s.strip()]
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
            if output_format.lower() == 'mkv' and has_chapters:
                chaps = input(Fore.CYAN + "Интегрировать главы в MKV? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                integrate_chapters = chaps != "0"
                log_debug(f"Интеграция глав: {integrate_chapters}")
                if integrate_chapters:
                    keep = input(Fore.CYAN + "Сохранять файл с главами отдельно? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                    keep_chapter_file = keep == "1"
                    log_debug(f"Сохраняем ли файл глав отдельно: {keep_chapter_file}")
            default_title = entry_info.get('title', f'video_{first_idx}')
            safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
            if add_index_prefix:
                safe_title = f"{first_idx:02d} {safe_title}"
            log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")
            output_name = ask_output_filename(safe_title, output_path, output_format, auto_mode=False)
            USER_SELECTED_OUTPUT_NAME = output_name
            log_debug(f"Финальное имя файла, выбранное пользователем: '{output_name}'")
            if (save_chapter_file or integrate_chapters) and has_chapters:
                chapter_filename = Path(output_path) / f"{output_name}.ffmeta"
                save_chapters_to_file(chapters, str(chapter_filename))
            log_debug(f"subtitle_options переданы: {subtitle_download_options}")
            downloaded_file = download_video(
                entry_url, video_id, audio_id, output_path, output_name, output_format,
                platform, cookie_file_to_use, subtitle_options=subtitle_download_options
            )
            if downloaded_file:
                print(Fore.GREEN + f"Видео {first_idx} успешно скачано: {downloaded_file}" + Style.RESET_ALL)
            else:
                print(Fore.RED + f"Ошибка при скачивании видео {first_idx}." + Style.RESET_ALL)

            if output_format.lower() == 'mkv' and (integrate_subs or integrate_chapters):
                if downloaded_file and Path(downloaded_file).is_file():
                    mux_mkv_with_subs_and_chapters(
                        downloaded_file, output_name, output_path,
                        subs_to_integrate_langs, subtitle_download_options,
                        integrate_subs, keep_sub_files,
                        integrate_chapters, keep_chapter_file, chapter_filename
                    )
                else:
                    print(Fore.RED + "Ошибка: итоговый файл для интеграции не найден." + Style.RESET_ALL)
                    log_debug("Ошибка: итоговый файл для интеграции не найден.")
            # --- Для остальных видео применяем те же параметры ---
            for idx in selected_indexes[1:]:
                entry = entries[idx - 1]
                entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                if not entry_url:
                    print(Fore.RED + f"Не удалось получить ссылку для видео {idx}. Пропуск." + Style.RESET_ALL)
                    log_debug(f"Нет ссылки для видео {idx}")
                    continue
                print(Fore.YELLOW + f"\n=== Видео {idx} из плейлиста (автоматический режим) ===" + Style.RESET_ALL)
                try:
                    entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                    cookie_file_to_use = entry_info.get('__cookiefile__')
                    chapters = entry_info.get("chapters")
                    has_chapters = isinstance(chapters, list) and len(chapters) > 0
                    video_fmt_auto = find_by_format_id(entry_info['formats'], video_id, is_video=True)
                    audio_fmt_auto = find_by_format_id(entry_info['formats'], audio_id, is_video=False) if audio_id else None

                    if not video_fmt_auto:
                        video_fmt_auto = find_best_video(entry_info['formats'], video_ext)
                    if audio_id and not audio_fmt_auto:
                        audio_fmt_auto = find_best_audio(entry_info['formats'], audio_ext)
                        video_id_auto = video_fmt_auto.get('format_id') if video_fmt_auto else None
                    video_id_auto = video_fmt_auto.get('format_id') if video_fmt_auto else None
                    audio_id_auto = audio_fmt_auto.get('format_id') if audio_fmt_auto else None
                    # ---
                    default_title = entry_info.get('title', f'video_{idx}')
                    safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                    if add_index_prefix:
                        safe_title = f"{idx:02d} {safe_title}"
                    log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")
                    output_name = get_unique_filename(safe_title, output_path, output_format)
                    log_debug(f"Финальное имя файла (автоматически): '{output_name}' (автоматический режим)")
                    if (save_chapter_file or integrate_chapters) and has_chapters:
                        chapter_filename = Path(output_path) / f"{output_name}.ffmeta"
                        save_chapters_to_file(chapters, str(chapter_filename))
                    log_debug(f"subtitle_options переданы: {subtitle_download_options}")
                    downloaded_file = download_video(
                        entry_url, video_id_auto, audio_id_auto, output_path, output_name, output_format,
                        platform, cookie_file_to_use, subtitle_options=subtitle_download_options
                    )
                    if downloaded_file:
                        print(Fore.GREEN + f"Видео {idx} успешно скачано: {downloaded_file}" + Style.RESET_ALL)
                    else:
                        print(Fore.RED + f"Ошибка при скачивании видео {idx}." + Style.RESET_ALL)

                    if output_format.lower() == 'mkv' and (integrate_subs or integrate_chapters):
                        if downloaded_file and Path(downloaded_file).is_file():
                            mux_mkv_with_subs_and_chapters(
                                downloaded_file, output_name, output_path,
                                subs_to_integrate_langs, subtitle_download_options,
                                integrate_subs, keep_sub_files,
                                integrate_chapters, keep_chapter_file, chapter_filename
                            )
                        else:
                            print(Fore.RED + "Ошибка: итоговый файл для интеграции не найден." + Style.RESET_ALL)
                            log_debug("Ошибка: итоговый файл для интеграции не найден.")

                except KeyboardInterrupt:
                    print(Fore.YELLOW + "\nЗагрузка прервана пользователем." + Style.RESET_ALL)
                    log_debug("Загрузка прервана пользователем (KeyboardInterrupt) в плейлисте.")
                    return
                except DownloadError as e:
                    if is_video_unavailable_error(e):
                        print(Fore.YELLOW + f"Видео {idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                        log_debug(f"Видео {idx} недоступно: {e}")
                        continue
                    else:
                        print(f"\n{Fore.RED}Ошибка загрузки видео {idx}: {e}{Style.RESET_ALL}")
                        log_debug(f"Ошибка загрузки видео {idx}: {e}")
                        continue
                except Exception as e:
                    print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
                    log_debug(f"Ошибка при скачивании видео {idx}: {e}\n{traceback.format_exc()}")
            print(Fore.CYAN + "\nВсе выбранные видео из плейлиста обработаны." + Style.RESET_ALL)
            return  # После плейлиста завершаем выполнение
        else:
            # --- Ручной режим: для каждого видео параметры запрашиваются отдельно ---
            for idx in selected_indexes:
                entry = entries[idx - 1]
                entry_url = entry.get('url') or entry.get('webpage_url') or entry.get('id')
                if not entry_url:
                    print(Fore.RED + f"Не удалось получить ссылку для видео {idx}. Пропуск." + Style.RESET_ALL)
                    log_debug(f"Нет ссылки для видео {idx}")
                    continue
                print(Fore.YELLOW + f"\n=== Видео {idx} из плейлиста ===" + Style.RESET_ALL)
                try:
                    try:
                        entry_info = safe_get_video_info(entry_url, platform, cookie_file_to_use)
                        cookie_file_to_use = entry_info.get('__cookiefile__')
                        chapters = entry_info.get("chapters")
                        has_chapters = isinstance(chapters, list) and len(chapters) > 0
                    except DownloadError as e:
                        if is_video_unavailable_error(e):
                            print(Fore.YELLOW + f"Видео {idx} ещё недоступно (премьера/скрыто/удалено). Пропуск." + Style.RESET_ALL)
                            log_debug(f"Видео {idx} недоступно: {e}")
                            continue
                        else:
                            print(f"\n{Fore.RED}Ошибка загрузки видео {idx}: {e}{Style.RESET_ALL}")
                            log_debug(f"Ошибка загрузки видео {idx}: {e}")
                            continue
                    except Exception as e:
                        print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
                        log_debug(f"Ошибка при скачивании видео {idx}: {e}\n{traceback.format_exc()}")
                        continue
                    video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(entry_info['formats'])
                    USER_SELECTED_VIDEO_CODEC = video_codec
                    USER_SELECTED_AUDIO_CODEC = audio_codec
                    if video_id == "bestvideo+bestaudio/best":
                        quality_map = {
                            "0": ("bestvideo+bestaudio/best", "Максимальное"),
                            "1": ("bestvideo[height<=1080]+bestaudio/best", "≤ 1080p"),
                            "2": ("bestvideo[height<=720]+bestaudio/best",  "≤ 720p"),
                            "3": ("bestvideo[height<=480]+bestaudio/best",  "≤ 480p"),
                            "4": ("bestvideo[height<=360]+bestaudio/best",  "≤ 360p"),
                        }
                        print(Fore.CYAN + "\nВыберите желаемое качество DASH/HLS:" + Style.RESET_ALL)
                        for key, (_, label) in quality_map.items():
                            print(f"{key}: {label}")
                        choice = input(Fore.CYAN + "Номер (Enter = 0): " + Style.RESET_ALL).strip() or "0"
                        selected = quality_map.get(choice, quality_map["0"])
                        video_id = selected[0]
                        log_debug(f"Пользователь выбрал профиль DASH: {video_id}")
                    subtitle_download_options = ask_and_select_subtitles(entry_info)
                    save_chapter_file = False
                    integrate_chapters = False
                    keep_chapter_file = False
                    chapter_filename = None
                    USER_INTEGRATE_CHAPTERS = integrate_chapters
                    USER_KEEP_CHAPTER_FILE = keep_chapter_file
                    USER_SELECTED_CHAPTER_FILENAME = chapter_filename
                    if has_chapters:
                        ask_chaps = input(Fore.CYAN + "Видео содержит главы. Сохранить главы в файл? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                        save_chapter_file = ask_chaps != "0"
                        log_debug(f"Пользователь выбрал сохранить главы: {save_chapter_file}")
                    output_path = select_output_folder()
                    USER_SELECTED_OUTPUT_PATH = output_path
                    if saved_list_path and Path(saved_list_path).is_file():
                        try:
                            dest_path = Path(output_path) / Path(saved_list_path).name
                            Path(saved_list_path).replace(dest_path)
                            print(Fore.GREEN + f"Список видео перемещён в папку сохранения: {dest_path}" + Style.RESET_ALL)
                        except Exception as e:
                            print(Fore.RED + f"Не удалось переместить файл списка: {e}" + Style.RESET_ALL)
                    output_format = ask_output_format(
                        desired_ext,
                        auto_mode=False,
                        subtitle_options=subtitle_download_options,
                        has_chapters=has_chapters
                    )
                    USER_SELECTED_OUTPUT_FORMAT = output_format
                    integrate_subs = False
                    keep_sub_files = True
                    subs_to_integrate_langs = []
                    USER_SELECTED_SUB_LANGS = subs_to_integrate_langs.copy()
                    USER_SELECTED_SUB_FORMAT = subtitle_download_options.get('subtitlesformat') if subtitle_download_options else None
                    USER_INTEGRATE_SUBS = integrate_subs
                    USER_KEEP_SUB_FILES = keep_sub_files
                    if output_format.lower() == 'mkv' and subtitle_download_options and subtitle_download_options.get('subtitleslangs'):
                        available_langs = subtitle_download_options['subtitleslangs']
                        print(Fore.CYAN + "\nКакие субтитры интегрировать в итоговый MKV?"
                            "\n  Введите номера или коды языков (через запятую или пробел)."
                            "\n  Enter, 0 или all — интегрировать ВСЕ."
                            "\n  «-» (минус) — не интегрировать ничего." + Style.RESET_ALL)
                        for sidx, lang in enumerate(available_langs, 1):
                            print(f"{sidx}: {lang}")
                        sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL).strip()
                        if sel in ("", "0", "all"):
                            subs_to_integrate_langs = available_langs.copy()
                            integrate_subs = True
                        elif sel == "-":
                            integrate_subs = False
                        else:
                            parts = [s.strip() for s in re.split(r"[\s,]+", sel) if s.strip()]
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
                    if output_format.lower() == 'mkv' and has_chapters:
                        chaps = input(Fore.CYAN + "Интегрировать главы в MKV? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                        integrate_chapters = chaps != "0"
                        log_debug(f"Интеграция глав: {integrate_chapters}")
                        if integrate_chapters:
                            keep = input(Fore.CYAN + "Сохранять файл с главами отдельно? (1 — да, 0 — нет, Enter = 0): " + Style.RESET_ALL).strip()
                            keep_chapter_file = keep == "1"
                            log_debug(f"Сохраняем ли файл глав отдельно: {keep_chapter_file}")
                    default_title = entry_info.get('title', f'video_{idx}')
                    safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
                    if add_index_prefix:
                        safe_title = f"{idx:02d} {safe_title}"
                    log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")
                    # --- Автоматический подбор имени файла, если файл уже существует ---
                    output_name = get_unique_filename(safe_title, output_path, output_format)
                    log_debug(f"Финальное имя файла (автоматически): '{output_name}'")
                    if (save_chapter_file or integrate_chapters) and has_chapters:
                        chapter_filename = Path(output_path) / f"{output_name}.ffmeta"
                        save_chapters_to_file(chapters, str(chapter_filename))
                    log_debug(f"subtitle_options переданы: {subtitle_download_options}")
                    downloaded_file = download_video(
                        entry_url, video_id, audio_id, output_path, output_name, output_format,
                        platform, cookie_file_to_use, subtitle_options=subtitle_download_options
                    )
                    if downloaded_file:
                        print(Fore.GREEN + f"Видео {idx} успешно скачано: {downloaded_file}" + Style.RESET_ALL)
                    else:
                        print(Fore.RED + f"Ошибка при скачивании видео {idx}." + Style.RESET_ALL)

                    if output_format.lower() == 'mkv' and (integrate_subs or integrate_chapters):
                        if downloaded_file and Path(downloaded_file).is_file():
                            mux_mkv_with_subs_and_chapters(
                                downloaded_file, output_name, output_path,
                                subs_to_integrate_langs, subtitle_download_options,
                                integrate_subs, keep_sub_files,
                                integrate_chapters, keep_chapter_file, chapter_filename
                            )
                        else:
                            print(Fore.RED + "Ошибка: итоговый файл для интеграции не найден." + Style.RESET_ALL)
                            log_debug("Ошибка: итоговый файл для интеграции не найден.")

                except KeyboardInterrupt:
                    print(Fore.YELLOW + "\nЗагрузка прервана пользователем." + Style.RESET_ALL)
                    log_debug("Загрузка прервана пользователем (KeyboardInterrupt) в плейлисте.")
                
                    return
                except DownloadError as e:
                    print(f"\n{Fore.RED}Ошибка загрузки видео {idx}: {e}{Style.RESET_ALL}")
                except Exception as e:
                    print(f"\n{Fore.RED}Непредвидённая ошибка при скачивании видео {idx}: {e}{Style.RESET_ALL}")
                    log_debug(f"Ошибка при скачивании видео {idx}: {e}\n{traceback.format_exc()}")
            print(Fore.CYAN + "\nВсе выбранные видео из плейлиста обработаны." + Style.RESET_ALL)
            return  # После плейлиста завершаем выполнение
    # --- Одиночное видео ---
    else:
        print(Fore.YELLOW + "\nОбнаружено одиночное видео." + Style.RESET_ALL)
        log_debug("Обнаружено одиночное видео.")
        chapters = info.get("chapters")
        has_chapters = isinstance(chapters, list) and len(chapters) > 0
        try:
            video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(info['formats'])
        except DownloadError as e:
            err_text = str(e).lower()
            if is_video_unavailable_error(e):
                print(Fore.YELLOW + "Видео недоступно (премьера/скрыто/удалено). Завершение." + Style.RESET_ALL)
                log_debug(f"Одиночное видео недоступно: {e}")
                return
            elif "cannot parse data" in err_text or "extractorerror" in err_text or "unsupported site" in err_text:
                fallback_download(url)
                return
            else:
                print(Fore.RED + f"Ошибка загрузки видео: {e}" + Style.RESET_ALL)
                log_debug(f"Ошибка загрузки видео: {e}")
                return
        except Exception as e:
            print(Fore.RED + f"Непредвидённая ошибка при скачивании видео: {e}" + Style.RESET_ALL)
            log_debug(f"Ошибка при скачивании видео: {e}\n{traceback.format_exc()}")
            return
        USER_SELECTED_VIDEO_CODEC = video_codec
        USER_SELECTED_AUDIO_CODEC = audio_codec
        if video_id == "bestvideo+bestaudio/best":
            quality_map = {
                "0": ("bestvideo+bestaudio/best", "Максимальное"),
                "1": ("bestvideo[height<=1080]+bestaudio/best", "≤ 1080p"),
                "2": ("bestvideo[height<=720]+bestaudio/best",  "≤ 720p"),
                "3": ("bestvideo[height<=480]+bestaudio/best",  "≤ 480p"),
                "4": ("bestvideo[height<=360]+bestaudio/best",  "≤ 360p"),
            }
            print(Fore.CYAN + "\nВыберите желаемое качество DASH/HLS:" + Style.RESET_ALL)
            for key, (_, label) in quality_map.items():
                print(f"{key}: {label}")
            choice = input(Fore.CYAN + "Номер (Enter = 0): " + Style.RESET_ALL).strip() or "0"
            selected = quality_map.get(choice, quality_map["0"])
            video_id = selected[0]
            log_debug(f"Пользователь выбрал профиль DASH: {video_id}")
        subtitle_download_options = ask_and_select_subtitles(info)
        save_chapter_file = False
        integrate_chapters = False
        keep_chapter_file = False
        chapter_filename = None
        USER_INTEGRATE_CHAPTERS = integrate_chapters
        USER_KEEP_CHAPTER_FILE = keep_chapter_file
        USER_SELECTED_CHAPTER_FILENAME = chapter_filename
        if has_chapters:
            ask_chaps = input(Fore.CYAN + "Видео содержит главы. Сохранить главы в файл? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            save_chapter_file = ask_chaps != "0"
            log_debug(f"Пользователь выбрал сохранить главы: {save_chapter_file}")
        output_path = select_output_folder()
        USER_SELECTED_OUTPUT_PATH = output_path
        if saved_list_path and Path(saved_list_path).is_file():
            try:
                dest_path = Path(output_path) / Path(saved_list_path).name
                Path(saved_list_path).replace(dest_path)
                print(Fore.GREEN + f"Список видео перемещён в папку сохранения: {dest_path}" + Style.RESET_ALL)
            except Exception as e:
                print(Fore.RED + f"Не удалось переместить файл списка: {e}" + Style.RESET_ALL)
        output_format = ask_output_format(
            desired_ext,
            subtitle_options=subtitle_download_options,
            has_chapters=has_chapters
        )
        USER_SELECTED_OUTPUT_FORMAT = output_format
        integrate_subs = False
        keep_sub_files = True
        subs_to_integrate_langs = []
        USER_SELECTED_SUB_LANGS = subs_to_integrate_langs.copy()
        USER_SELECTED_SUB_FORMAT = subtitle_download_options.get('subtitlesformat') if subtitle_download_options else None
        USER_INTEGRATE_SUBS = integrate_subs
        USER_KEEP_SUB_FILES = keep_sub_files
        if output_format.lower() == 'mkv' and subtitle_download_options and subtitle_download_options.get('subtitleslangs'):
            available_langs = subtitle_download_options['subtitleslangs']
            print(Fore.CYAN + "\nКакие субтитры интегрировать в итоговый MKV?"
                    "\n  Введите номера или коды языков (через запятую или пробел)."
                    "\n  Enter, 0 или all — интегрировать ВСЕ."
                    "\n  «-» (минус) — не интегрировать ничего." + Style.RESET_ALL)
            for sidx, lang in enumerate(available_langs, 1):
                print(f"{sidx}: {lang}")
            sel = input(Fore.CYAN + "Ваш выбор: " + Style.RESET_ALL).strip()
            if sel in ("", "0", "all"):
                integrate_subs = True
                subs_to_integrate_langs = available_langs
            elif sel == "-":
                integrate_subs = False
            else:
                parts = [s.strip() for s in re.split(r'[,\s]+', sel) if s.strip()]
                for p in parts:
                    if p.isdigit():
                        i = int(p) - 1
                        if 0 <= i < len(available_langs):
                            subs_to_integrate_langs.append(available_langs[i])
                    elif p in available_langs:
                        subs_to_integrate_langs.append(p)
                integrate_subs = bool(subs_to_integrate_langs)
        log_debug(f"Интеграция субтитров: {integrate_subs}, языки: {subs_to_integrate_langs}, keep files: {keep_sub_files}")
        if output_format.lower() == 'mkv' and has_chapters:
            ask_chaps = input(Fore.CYAN + "Интегрировать главы в MKV? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
            integrate_chapters = ask_chaps != "0"
            log_debug(f"Пользователь выбрал интеграцию глав: {integrate_chapters}")
        default_title = info.get('title', 'video')
        safe_title = re.sub(r'[<>:"/\\|?*!]', '', default_title)
        log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")
        output_name = ask_output_filename(safe_title, output_path, output_format)
        USER_SELECTED_OUTPUT_NAME = output_name            
        log_debug(f"Финальное имя файла, выбранное пользователем: '{output_name}'")
        if (save_chapter_file or integrate_chapters) and has_chapters:
            chapter_filename = Path(output_path) / f"{output_name}.ffmeta"
            save_chapters_to_file(chapters, str(chapter_filename))
        # Запуск загрузки видео
        downloaded_file = download_video(
            url, video_id, audio_id,
            output_path, output_name,
            output_format, platform,
            cookie_file_to_use,
            subtitle_download_options
        )
        if downloaded_file:
            print(Fore.GREEN + f"\nВидео успешно скачано: {downloaded_file}" + Style.RESET_ALL)
            # --- Переименование автоматических субтитров с .auto ---
            if subtitle_download_options:
                auto_suffix = subtitle_download_options.get('add_auto_suffix', True)
                auto_langs = subtitle_download_options.get('automatic_subtitles_langs', [])
                sub_format = subtitle_download_options.get('subtitlesformat', 'srt')
                output_name = USER_SELECTED_OUTPUT_NAME
                output_path = USER_SELECTED_OUTPUT_PATH
                if auto_suffix and output_name and output_path:
                    for lang in auto_langs:
                        orig_file = Path(output_path) / f"{output_name}.{lang}.{sub_format}"
                        new_file = Path(output_path) / f"{output_name}.{lang}.auto.{sub_format}"
                        if orig_file.exists():
                            try:
                                orig_file.rename(new_file)
                                print(Fore.YELLOW + f"Файл автоматических субтитров переименован: {new_file.name}" + Style.RESET_ALL)
                                log_debug(f"Переименован файл автоматических субтитров: {orig_file} -> {new_file}")
                            except Exception as e:
                                print(Fore.RED + f"Ошибка при переименовании субтитров: {e}" + Style.RESET_ALL)
                                log_debug(f"Ошибка при переименовании субтитров: {e}")
        else:
            print(Fore.RED + "\nОшибка при скачивании видео." + Style.RESET_ALL)

        if output_format.lower() == 'mkv' and (integrate_subs or integrate_chapters):
            if downloaded_file and Path(downloaded_file).is_file():
                mux_mkv_with_subs_and_chapters(
                    downloaded_file, output_name, output_path,
                    subs_to_integrate_langs, subtitle_download_options,
                    integrate_subs, keep_sub_files,
                    integrate_chapters, keep_chapter_file, chapter_filename
                )
            else:
                print(Fore.RED + "Ошибка: итоговый файл для интеграции не найден." + Style.RESET_ALL)
                log_debug("Ошибка: итоговый файл для интеграции не найден.")

    # --- Блок финальной проверки итогового файла ---
    final_file = None
    if 'downloaded_file' in locals() and downloaded_file and Path(downloaded_file).is_file():
        final_file = downloaded_file
    # Для плейлистов можно добавить аналогично, если нужно

    if final_file and output_format.lower() == 'mkv':
        log_debug(f"Финальная проверка MKV-файла: {final_file}")
        # Собираем ожидаемые параметры
        expected_video_codec = video_codec if 'video_codec' in locals() else None
        expected_audio_codec = audio_codec if 'audio_codec' in locals() else None
        expected_sub_langs = subs_to_integrate_langs if integrate_subs else []
        expected_chapters = integrate_chapters

        ok = check_mkv_integrity(
            final_file,
            expected_video_codec=USER_SELECTED_VIDEO_CODEC,
            expected_audio_codec=USER_SELECTED_AUDIO_CODEC,
            expected_sub_langs=USER_SELECTED_SUB_LANGS if USER_INTEGRATE_SUBS else [],
            expected_chapters=USER_INTEGRATE_CHAPTERS
        )
        if not ok:
            log_debug("Файл MKV не соответствует выбранным опциям — запускаем принудительную интеграцию.")
            print(Fore.YELLOW + "Файл MKV не содержит все выбранные дорожки. Запускается повторная интеграция..." + Style.RESET_ALL)
            if downloaded_file and Path(downloaded_file).is_file():
                mux_mkv_with_subs_and_chapters(
                    downloaded_file, output_name, output_path,
                    subs_to_integrate_langs, subtitle_download_options,
                    integrate_subs, keep_sub_files,
                    integrate_chapters, keep_chapter_file, chapter_filename
                )
            else:
                print(Fore.RED + "Ошибка: итоговый файл для интеграции не найден." + Style.RESET_ALL)
                log_debug("Ошибка: итоговый файл для интеграции не найден.")
        else:
            log_debug("Файл MKV успешно прошёл финальную проверку по всем выбранным опциям.") 

if __name__ == '__main__':
    main()
