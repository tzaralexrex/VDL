# YouTube & Facebook Video Downloader with Cookie Browser Support

import os
import re
import json
import subprocess
import yt_dlp
from yt_dlp.utils import DownloadError # Импортируем специфическую ошибку yt-dlp
import shutil
import sys
import traceback
import http.cookiejar # Для работы с куки-файлами
import glob
import time

import browser_cookie3
from browser_cookie3 import BrowserCookieError

from shutil import which
from tkinter import filedialog, Tk
from datetime import datetime

from colorama import init, Fore, Style


init(autoreset=True)  # инициализация colorama и автоматический сброс цвета после каждого print

DEBUG = 1 # Глобальная переменная для включения/выключения отладки
DEBUG_APPEND = 1 # 0 = перезаписывать лог при каждом запуске, 1 = дописывать к существующему логу

CONFIG_FILE = 'vdl_conf.json'
DEBUG_FILE = 'debug.log'

COOKIES_FB = 'cookies_fb.txt'
COOKIES_YT = 'cookies_yt.txt'

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
    if DEBUG:
        mode = 'a' if DEBUG_APPEND else 'w' # Используем DEBUG_APPEND для определения режима
        with open(DEBUG_FILE, mode, encoding='utf-8') as f:
            if mode == 'w':
                f.write(f"--- Начинается новый сеанс отладки [{datetime.now()}] ---\n")
            f.write(f"[{datetime.now()}] {message}\n")


def get_last_folder():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            return config.get('last_folder', os.path.join(os.environ['USERPROFILE'], 'Videos'))
    return os.path.join(os.environ['USERPROFILE'], 'Videos')


def save_last_folder(folder_path):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'last_folder': folder_path}, f)


def extract_platform_and_url(raw_url):
    yt_patterns = [r'(?:youtube\.com|youtu\.be)']
    fb_patterns = [r'(?:facebook\.com|fb\.watch)']

    for pat in yt_patterns:
        if re.search(pat, raw_url):
            log_debug(f"Определена платформа: youtube для URL: {raw_url.strip()}")
            return 'youtube', raw_url.strip()

    for pat in fb_patterns:
        if re.search(pat, raw_url):
            cleaned_url = clean_facebook_url(raw_url.strip())
            log_debug(f"Определена платформа: facebook для URL: {cleaned_url}")
            return 'facebook', cleaned_url

    raise ValueError(Fore.RED + "Не удалось определить платформу (YouTube или Facebook)" + Style.RESET_ALL)


def clean_facebook_url(raw_url):
    patterns = [
        r'/videos/(\d+)',
        r'v=(\d+)',
        r'/reel/(\d+)',
        r'/watch/\?v=(\d+)',
        r'/video.php\?v=(\d+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_url)
        if match:
            video_id = match.group(1)
            return f"https://m.facebook.com/watch/?v={video_id}&_rdr"
    raise ValueError(Fore.RED + "Не удалось распознать ID видео Facebook" + Style.RESET_ALL)


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
    
    # 2. Если файл не существует, попытка извлечь куки из браузера
    browsers_to_try = ['chrome', 'firefox']
    browser_functions = {
        'chrome': browser_cookie3.chrome,
        'firefox': browser_cookie3.firefox,
    }
    
    print(Fore.YELLOW + f"Примечание: Для автоматического получения куков из браузера (Chrome/Firefox), убедитесь, что он закрыт или неактивен." + Style.RESET_ALL)

    extracted_cj = None # CookieJar object
    for browser in browsers_to_try:
        try:
            print(Fore.GREEN + f"Пытаемся получить куки для {platform.capitalize()} из вашего браузера ({browser})." + Style.RESET_ALL)
            log_debug(f"Попытка получить куки для {platform.capitalize()} из браузера: {browser}")
            
            if platform == 'facebook':
                extracted_cj = browser_functions[browser](domain_name='facebook.com')
            elif platform == 'youtube':
                # Для yt-dlp лучше всего указывать основной домен для куков, например, '.youtube.com' или '.google.com'
                # '.youtube.com' - это невалидный домен для browser_cookie3
                extracted_cj = browser_functions[browser](domain_name='youtube.com') # Более корректно
                if not extracted_cj: # Если .youtube.com не сработал, пробуем более общий .google.com
                    log_debug(f"Куки для .youtube.com не найдены в {browser}. Пробуем .google.com.")
                    extracted_cj = browser_functions[browser](domain_name='google.com')


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
    
    print(Fore.YELLOW + f"Не удалось автоматически получить куки для {platform.capitalize()}. " +
                        f"Для загрузки приватных видео {platform.capitalize()}, пожалуйста, " +
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
    """
    Пытается: 1) c куками из файла → 2) вытаскивает куки из браузера → 3) без куков.
    Если контент всё равно требует логин — выводит инструкцию и завершает работу.
    """
    # Определяем имя cookie‑файла
    cookie_path = COOKIES_FB if platform == "facebook" else COOKIES_YT

    # 1-я попытка: то, что уже есть на диске
    current_cookie = get_cookies_for_platform(platform, cookie_path)
    for attempt in ("file", "browser", "none"):
        try:
            return get_video_info(url, platform, current_cookie if attempt != "none" else None)
        except DownloadError as err:
            err_l = str(err).lower()
            need_login = any(x in err_l for x in ("login", "403", "private", "sign in"))
            if not need_login:
                raise  # ошибка не про авторизацию
            if attempt == "file":
                # 2-я попытка: принудительно берём свежие куки из браузера
                current_cookie = get_cookies_for_platform(platform, cookie_path, force_browser=True)
            elif attempt == "browser":
                # 3-я попытка: совсем без куков
                current_cookie = None
            else:
                print(f"\nВидео требует авторизации, а получить рабочие куки автоматически не удалось.\n"
                      f"Сохраните их вручную (расширением EditThisCookie, Get cookies.txt, и т.д.)\n"
                      f"и положите файл сюда: {cookie_path}\n")
                sys.exit(1)


def choose_format(formats):
    video_formats = [f for f in formats if f.get('vcodec') != 'none']
    audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']

    video_formats.sort(key=lambda f: (f.get('height') or 0, f.get('format_id', '')))
    audio_formats.sort(key=lambda f: f.get('abr') or 0)

    print("\n" + Fore.MAGENTA + "Доступные видеоформаты:" + Style.RESET_ALL)
    for i, f in enumerate(video_formats):
        fmt_id = f.get('format_id', '?')
        ext = f.get('ext', '?')
        height = f.get('height', '?')
        format_note = f.get('format_note', '')
        vcodec = f.get('vcodec', '?')
        resolution_display = f"{height}p" if height else format_note if format_note else "?p"
        print(f"{i}: {fmt_id} - {ext} - {resolution_display} - {vcodec}")

    default_video = len(video_formats) - 1
    v_choice = input(Fore.CYAN + f"Выберите видеоформат (по умолчанию {default_video}): " + Style.RESET_ALL).strip()
    v_choice = int(v_choice) if v_choice.isdigit() and 0 <= int(v_choice) < len(video_formats) else default_video
    video_fmt = video_formats[v_choice]
    log_debug(f"Выбран видеоформат ID: {video_fmt['format_id']}, Ext: {video_fmt['ext']}, Codec: {video_fmt.get('vcodec', '')}")

    audio_fmt = None
    if not audio_formats:
        print(Fore.YELLOW + "\nДоступные аудиоформаты: Отдельные аудиопотоки отсутствуют. Видео, вероятно, содержит встроенный звук." + Style.RESET_ALL)
        input(Fore.CYAN + "Нажмите Enter для продолжения (аудиопоток будет выбран автоматически, если он встроен): " + Style.RESET_ALL)
        log_debug("Отдельные аудиоформаты отсутствуют. Будет выбран встроенный аудиопоток, если доступно.")
    else:
        print("\n" + Fore.MAGENTA + "Доступные аудиоформаты:" + Style.RESET_ALL)
        for i, f in enumerate(audio_formats):
            fmt_id = f.get('format_id', '?')
            ext = f.get('ext', '?')
            abr = f.get('abr', '?')
            acodec = f.get('acodec', '?')
            print(f"{i}: {fmt_id} - {ext} - {abr}kbps - {acodec}")

        default_audio = len(audio_formats) - 1
        a_choice = input(Fore.CYAN + f"Выберите аудиоформат (по умолчанию {default_audio}): " + Style.RESET_ALL).strip()
        a_choice = int(a_choice) if a_choice.isdigit() and 0 <= int(a_choice) < len(audio_formats) else default_audio
        audio_fmt = audio_formats[a_choice]
        log_debug(f"Выбран аудиоформат ID: {audio_fmt['format_id']}, Ext: {audio_fmt.get('ext', '')}, Codec: {audio_fmt.get('acodec', '')}")

    # Возвращаем все параметры для mux
    return (
        video_fmt['format_id'],
        audio_fmt['format_id'] if audio_fmt else None,
        video_fmt['ext'],
        video_fmt.get('ext', ''),
        audio_fmt.get('ext', '') if audio_fmt else '',
        video_fmt.get('vcodec', ''),
        audio_fmt.get('acodec', '') if audio_fmt else ''
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
                ask = input(Fore.CYAN + f"Скачать также автоматические субтитры для языка '{lang}'? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL).strip()
                if ask == '0':
                    continue
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
    root = Tk()
    root.withdraw()
    initial_dir = get_last_folder()
    log_debug(f"Диалог выбора папки: Начальная директория: {initial_dir}")
    folder = filedialog.askdirectory(initialdir=initial_dir, title="Выберите папку")
    if folder:
        save_last_folder(folder)
        log_debug(f"Выбрана папка для сохранения: {folder}")
        return folder
    log_debug(f"Папка не выбрана, использована последняя/дефолтная: {initial_dir}")
    return initial_dir


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

def convert_vtt_to_srt(folder, basename, langs):
    import subprocess
    converted = []
    for lang in langs:
        vtt_file = os.path.join(folder, f"{basename}.{lang}.vtt")
        srt_file = os.path.join(folder, f"{basename}.{lang}.srt")
        if os.path.exists(vtt_file):
            try:
                subprocess.run(
                    ['ffmpeg', '-y', '-i', vtt_file, srt_file],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print(Fore.GREEN + f"Сконвертирован в .srt: {srt_file}" + Style.RESET_ALL)
                converted.append((vtt_file, srt_file))
            except Exception as e:
                print(Fore.RED + f"Ошибка при конвертации {vtt_file} → {srt_file}: {e}" + Style.RESET_ALL)
    return converted

def download_video(url, video_id, audio_id, output_path, output_name, merge_format, platform, cookie_file_path=None, subtitle_options=None):
    full_output_template = os.path.join(output_path, output_name + '.%(ext)s')
    log_debug(f"Шаблон выходного файла yt-dlp: {full_output_template}")

    ffmpeg_path = detect_ffmpeg_path()
    if not ffmpeg_path:
        print(Fore.RED + "FFmpeg не найден. Установите его и добавьте в PATH или поместите ffmpeg.exe рядом со скриптом." + Style.RESET_ALL)
        log_debug("FFmpeg не найден. Выход из download_video.")
        return None

    format_string = video_id
    if audio_id:
        format_string = f'{video_id}+{audio_id}'
        log_debug(f"Выбран формат видео+аудио: {format_string}")
    else:
        print(Fore.YELLOW + "Внимание: Отдельный аудиопоток не выбран. Будет загружен только видеопоток, или видеопоток с встроенным звуком, если доступно." + Style.RESET_ALL)
        log_debug("Отдельный аудиопоток не выбран. Будет загружен только видеопоток, или с встроенным звуком.")

    ydl_opts = {
        'format': format_string,
        'outtmpl': full_output_template,
        'merge_output_format': merge_format,
        'quiet': False,
        'ffmpeg_location': ffmpeg_path,
        'overwrites': True,
        'progress_hooks': [],  # Заполним ниже
        'continuedl': True,
        'writedescription': False,
        'writeinfojson': False,
        'writesubtitles': False,
    }
    
    if cookie_file_path:
        ydl_opts['cookiefile'] = cookie_file_path
        log_debug(f"download_video: Используем cookiefile: {cookie_file_path}")
    
    if subtitle_options:
        ydl_opts.update(subtitle_options)
        log_debug(f"Опции субтитров добавлены: {subtitle_options}")
    
    os.makedirs(output_path, exist_ok=True)
    log_debug(f"Убедились, что директория '{output_path}' существует.")
    
    max_retries = 3
    last_downloaded_filename = None  # Переменная для хранения имени конечного файла
    
    def progress_hook(d):
        nonlocal last_downloaded_filename
        if d['status'] == 'finished':
            last_downloaded_filename = d.get('filename')
            log_debug(f"Progress hook: Файл завершён и расположен по пути: {last_downloaded_filename}")
    
    ydl_opts['progress_hooks'] = [progress_hook]
    
    for attempt in range(1, max_retries + 1):
        try:
            log_debug(f"Попытка {attempt}/{max_retries}. Запуск yt-dlp для URL: {url} с опциями: {ydl_opts}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.download([url])
                log_debug(f"Загрузка yt-dlp завершена. Результат info: {info}")
    
                # --- Конвертация субтитров vtt → srt, если нужно ---
                if subtitle_options and subtitle_options.get('subtitleslangs'):
                    langs = subtitle_options['subtitleslangs']
                    converted_files = convert_vtt_to_srt(output_path, output_name, langs)
                    if converted_files:
                        for vtt, _ in converted_files:
                            try:
                                os.remove(vtt)
                                print(Fore.YELLOW + f"Удалён: {vtt}" + Style.RESET_ALL)
                            except Exception as e:
                                print(Fore.RED + f"Ошибка при удалении {vtt}: {e}" + Style.RESET_ALL)
    
                if last_downloaded_filename and os.path.exists(last_downloaded_filename):
                    log_debug(f"Файл успешно скачан: {last_downloaded_filename}")
                    return last_downloaded_filename
                else:
                    # Если по какой-то причине прогресс-хук не сработал, пытаемся найти файл вручную
                    final_file_name_expected = f"{output_name}.{merge_format}"
                    final_file_path_expected = os.path.join(output_path, final_file_name_expected)
                    log_debug(f"Ожидаемый конечный файл: {final_file_path_expected}")
    
                    if os.path.exists(final_file_path_expected):
                        log_debug(f"Файл '{final_file_path_expected}' найден с помощью os.path.exists.")
                        return final_file_path_expected
                    else:
                        log_debug(f"Файл '{final_file_path_expected}' НЕ найден с помощью os.path.exists.")
                        # Поиск по списку файлов в директории
                        try:
                            dir_contents = os.listdir(output_path)
                            log_debug(f"Содержимое директории '{output_path}': {dir_contents}")
                            for f_name in dir_contents:
                                if f_name.lower().startswith(output_name.lower()) and f_name.lower().endswith(f".{merge_format.lower()}"):
                                    found_file = os.path.join(output_path, f_name)
                                    log_debug(f"Файл найден через os.listdir: {found_file}")
                                    return found_file
                        except Exception as ex:
                            log_debug(f"Ошибка при попытке os.listdir({output_path}): {ex}")
                        return None
    
        except DownloadError as e:
            error_str = str(e)
            log_debug(f"yt-dlp DownloadError (попытка {attempt}): {error_str}")
            if "Read timed out" in error_str and attempt < max_retries:
                print(Fore.YELLOW + f"Временный сбой соединения. Повтор через 3 секунды ({attempt}/{max_retries})..." + Style.RESET_ALL)
                time.sleep(3)
                continue
            else:
                print(Fore.RED + f"Произошла ошибка во время загрузки: {e}" + Style.RESET_ALL)
                raise
        except Exception as e:
            log_debug(f"Непредвиденная ошибка во время загрузки (попытка {attempt}): {str(e)}\n{traceback.format_exc()}")
            print(Fore.RED + f"Произошла непредвиденная ошибка во время загрузки: {e}" + Style.RESET_ALL)
            raise

def main():
    print(Fore.YELLOW + "YouTube & Facebook Video Downloader")
    raw_url = input(Fore.CYAN + "Введите ссылку: " + Style.RESET_ALL).strip()
    log_debug(f"Введена ссылка: {raw_url}")

    # Переменные для хранения информации о файлах для обработки при прерывании
    output_path = None
    output_name = None
    output_format = None
    video_id = None
    audio_id = None

    try:
        platform, url = extract_platform_and_url(raw_url)

        info = safe_get_video_info(url, platform)
        cookie_file_to_use = info.get('__cookiefile__')  # значение будет None, если info не содержит его

        # log_debug(json.dumps(info, indent=2)) # Это может быть слишком объемно для лога

        video_id, audio_id, desired_ext, video_ext, audio_ext, video_codec, audio_codec = choose_format(info['formats'])

#        video_id, audio_id, default_ext = choose_format(info['formats'])
        subtitle_download_options = ask_and_select_subtitles(info)
        output_path = select_output_folder()
        output_format = ask_output_format(desired_ext)
#        output_format = ask_output_format(default_ext)

        default_title = info.get('title', 'video')
        # Очищаем заголовок от недопустимых символов для имен файлов
        safe_title = re.sub(r'[<>:"/\\|?*]', '', default_title)
        log_debug(f"Оригинальное название видео: '{default_title}', Безопасное название: '{safe_title}'")

        output_name = ask_output_filename(safe_title, output_path, output_format)
        log_debug(f"Финальное имя файла, выбранное пользователем: '{output_name}'")
        log_debug(f"subtitle_options переданы: {subtitle_download_options}")


        downloaded_file = download_video(
            url, video_id, audio_id, output_path, output_name, output_format,
            platform, cookie_file_to_use, subtitle_options=subtitle_download_options
        )

        if downloaded_file:
            current_processing_file = downloaded_file
            desired_ext = output_format.lower()

            # --- Автоматическая обработка субтитров ---
            subtitle_langs = subtitle_download_options.get('subtitleslangs') if subtitle_download_options else []
            subtitle_format = subtitle_download_options.get('subtitlesformat') if subtitle_download_options else 'srt'
            subtitle_files = []
            for lang in subtitle_langs:
                sub_path = os.path.join(output_path, f"{output_name}.{lang}.{subtitle_format}")
                # Если нужный формат отсутствует, ищем vtt и конвертируем
                if not os.path.exists(sub_path):
                    # Поиск .vtt
                    vtt_path = os.path.join(output_path, f"{output_name}.{lang}.vtt")
                    if os.path.exists(vtt_path) and subtitle_format == 'srt':
                        convert_vtt_to_srt(output_path, output_name, [lang])
                        try:
                            os.remove(vtt_path)
                            log_debug(f"Автоматически сконвертирован и удалён исходник: {vtt_path}")
                        except Exception as e:
                            log_debug(f"Ошибка при удалении исходника .vtt: {e}")
                    # Можно добавить поддержку других конвертаций, если потребуется
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
                # --- Интеграция субтитров только для MKV ---
                integrate_subs = False
                keep_sub_files = True
                subs_to_integrate = []
                if desired_ext == 'mkv' and subtitle_files:
                    print(Fore.CYAN + "\nДоступные субтитры для интеграции в MKV:")
                    for idx, (sub_file, lang) in enumerate(subtitle_files, 1):
                        print(f"{idx}: {os.path.basename(sub_file)} (язык: {lang})")
                    print(Fore.CYAN + "Введите номера субтитров для интеграции (через запятую/пробел), 0/all — все, '-' — не интегрировать: " + Style.RESET_ALL)
                    sel = input().strip()
                    if sel == '-' or sel == '':
                        integrate_subs = False
                        subs_to_integrate = []
                    elif sel == '0' or sel.lower() == 'all':
                        integrate_subs = True
                        subs_to_integrate = subtitle_files.copy()
                    else:
                        nums = [int(s) for s in re.split(r'[,\s]+', sel) if s.strip().isdigit()]
                        subs_to_integrate = [subtitle_files[i-1] for i in nums if 1 <= i <= len(subtitle_files)]
                        integrate_subs = bool(subs_to_integrate)
                    log_debug(f"Пользователь выбрал для интеграции субтитры: {subs_to_integrate}")
                    print(Fore.CYAN + "Сохранять субтитры отдельными файлами? (1 — да, 0 — нет, Enter = 1): " + Style.RESET_ALL)
                    keep_files_input = input().strip()
                    if keep_files_input == '0':
                        keep_sub_files = False
                    log_debug(f"Сохранять субтитры отдельными файлами: {keep_sub_files}")
                else:
                    integrate_subs = False
                    subs_to_integrate = []

                # --- Объединение/ремукс ---
                temp_output_file = os.path.join(output_path, f"{output_name}_muxed_temp.{desired_ext}")
                
                ffmpeg_cmd = [ffmpeg_path, '-loglevel', 'warning']
                
                # Добавляем флаг только для AVI
                if desired_ext.lower() == 'avi':
                    ffmpeg_cmd += ['-fflags', '+genpts']
                
                # Основной видео/аудиофайл
                ffmpeg_cmd += ['-i', current_processing_file]
                
                # Добавляем субтитры, если нужно интегрировать
                if integrate_subs and subs_to_integrate:
                    for sub_file, lang in subs_to_integrate:
                        ffmpeg_cmd += ['-i', sub_file]
                
                # --- Определяем режим для webm ---
                need_webm_transcode = False
                if desired_ext.lower() == 'webm':
                    # video_ext, audio_ext, video_codec, audio_codec должны быть определены из choose_format
                    if not (
                        video_ext.lower() == 'webm'
                        and audio_ext.lower() == 'webm'
                        and video_codec in ('vp8', 'vp9', 'av1')
                        and audio_codec in ('opus', 'vorbis')
                    ):
                        need_webm_transcode = True
                
                # Выбор кодеков
                if desired_ext.lower() == 'webm':
                    if need_webm_transcode:
                        ffmpeg_cmd += ['-c:v', 'libvpx-vp9', '-c:a', 'libopus']
                    else:
                        ffmpeg_cmd += ['-c', 'copy']
                else:
                    ffmpeg_cmd += ['-c', 'copy']
                
                # map для основного файла
                ffmpeg_cmd += ['-map', '0']
                
                # map и метаданные для субтитров
                if integrate_subs and subs_to_integrate:
                    for idx, (sub_file, lang) in enumerate(subs_to_integrate):
                        ffmpeg_cmd += ['-map', str(idx + 1)]
                        ffmpeg_cmd += [f'-metadata:s:s:{idx}', f'language={lang}']
                
                # Финальный выходной файл
                ffmpeg_cmd += [temp_output_file]
                
                # Логируем команду
                log_debug(f"Выполняется команда ffmpeg для объединения: {' '.join(map(str, ffmpeg_cmd))}")
                
                # Запускаем ffmpeg и логируем вывод
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    try:
                        os.remove(current_processing_file)
                    except Exception as e:
                        log_debug(f"Ошибка при удалении исходного файла после mux: {e}")
                    current_processing_file = temp_output_file
                    if integrate_subs and subs_to_integrate:
                        print(Fore.GREEN + f"Видео, аудио и субтитры объединены в {desired_ext.upper()}." + Style.RESET_ALL)
                        log_debug(f"Видео, аудио и субтитры объединены в {desired_ext.upper()}: {temp_output_file}")
                        if not keep_sub_files:
                            for sub_file, _ in subtitle_files:
                                if (sub_file, _) not in subs_to_integrate:
                                    try:
                                        os.remove(sub_file)
                                        log_debug(f"Удалён неинтегрированный файл субтитров: {sub_file}")
                                    except Exception as e:
                                        log_debug(f"Ошибка при удалении файла субтитров {sub_file}: {e}")
                    else:
                        print(Fore.GREEN + f"Видео и аудио объединены в {desired_ext}." + Style.RESET_ALL)
                        log_debug(f"Видео и аудио объединены в {desired_ext}: {temp_output_file}")
                else:
                    print(Fore.RED + "Ошибка при объединении через ffmpeg." + Style.RESET_ALL)
                    log_debug("Ошибка при объединении через ffmpeg.")
                
                
            # --- Финальное переименование ---
            final_target_filename = os.path.join(output_path, f"{output_name}.{output_format}")
            if os.path.abspath(current_processing_file) != os.path.abspath(final_target_filename):
                try:
                    os.rename(current_processing_file, final_target_filename)
                    log_debug(f"Переименован файл: {current_processing_file} -> {final_target_filename}")
                except Exception as e:
                    log_debug(f"Ошибка при переименовании финального файла: {e}")
                    print(Fore.RED + f"Ошибка при переименовании финального файла: {e}" + Style.RESET_ALL)
                    final_target_filename = current_processing_file  # fallback

            print(Fore.GREEN + f"\nГотово. Видео сохранено в: {final_target_filename}" + Style.RESET_ALL)
            log_debug(f"Видео успешно сохранено в: {final_target_filename}")

        else:
            print(Fore.YELLOW + "\nЗагрузка завершилась, но конечный файл не найден. Возможно, произошла ошибка или загрузка была прервана." + Style.RESET_ALL)
            log_debug("Загрузка завершилась, но конечный файл не найден.")

    except KeyboardInterrupt:
        print(Fore.YELLOW + "\nЗагрузка прервана пользователем." + Style.RESET_ALL)
        log_debug("Загрузка прервана пользователем (KeyboardInterrupt).")

        # Проверим, есть ли информация для обработки файлов
        if output_path and output_name:
            log_debug(f"Обработка временных файлов после прерывания. Путь: '{output_path}', Имя: '{output_name}'")
            # Находим все .part файлы, которые могли быть созданы для этого видео
            # Используем glob для поиска файлов, соответствующих шаблону.

            temp_file_pattern = os.path.join(output_path, f"{output_name}.*.part")
            part_files = glob.glob(temp_file_pattern)
            log_debug(f"Найденные .part файлы по шаблону '{temp_file_pattern}': {part_files}")

            ffmpeg_temp_pattern = os.path.join(output_path, f"{output_name}.temp.*")
            temp_files_ffmpeg = glob.glob(ffmpeg_temp_pattern)
            log_debug(f"Найденные временные файлы ffmpeg по шаблону '{ffmpeg_temp_pattern}': {temp_files_ffmpeg}")

            main_incomplete_file = os.path.join(output_path, f"{output_name}.{output_format}")
            if os.path.exists(main_incomplete_file) and os.path.getsize(main_incomplete_file) == 0:
                log_debug(f"Найден пустой основной файл: {main_incomplete_file}")
                part_files.append(main_incomplete_file)

            relevant_part_files = []
            if video_id:
                relevant_part_files.extend([
                    f for f in part_files
                    if f.startswith(os.path.join(output_path, f"{output_name}.f{video_id}"))
                    or f.startswith(os.path.join(output_path, f"{output_name}.f{video_id}."))
                ])
                log_debug(f"Relevant .part files for video ID {video_id}: {relevant_part_files}")
            if audio_id:
                relevant_part_files.extend([
                    f for f in part_files
                    if f.startswith(os.path.join(output_path, f"{output_name}.f{audio_id}"))
                    or f.startswith(os.path.join(output_path, f"{output_name}.f{audio_id}."))
                ])
                log_debug(f"Relevant .part files for audio ID {audio_id}: {relevant_part_files}")

            relevant_part_files.extend(temp_files_ffmpeg)
            relevant_part_files = list(set(relevant_part_files))  # Удаляем дубликаты
            log_debug(f"Все релевантные временные файлы: {relevant_part_files}")

            if relevant_part_files:
                print(Fore.YELLOW + "\nБыли найдены незавершенные файлы загрузки:" + Style.RESET_ALL)
                for f_path in relevant_part_files:
                    print(f"- {os.path.basename(f_path)}")

                has_video_part = any(f for f in relevant_part_files if ('.f' + str(video_id) in f or f.endswith('.part')) and '.part' in f)

                if has_video_part and audio_id is not None:
                    choice = input(Fore.CYAN + "Сохранить частично скачанный видеопоток без звука (1), удалить все временные файлы (2), или оставить как есть (Enter)? (по умолчанию: оставить): " + Style.RESET_ALL).strip()
                else:
                    choice = input(Fore.CYAN + "Удалить все временные файлы (1), или оставить как есть (Enter)? (по умолчанию: оставить): " + Style.RESET_ALL).strip()

                log_debug(f"Пользовательский выбор по временным файлам: '{choice}'")

                if choice == '1' and has_video_part and audio_id is not None:
                    video_part_file = None
                    for f in relevant_part_files:
                        if f.startswith(os.path.join(output_path, f"{output_name}.f{video_id}")) and f.endswith('.part'):
                            video_part_file = f
                            break

                    if not video_part_file:
                        largest_part_file = None
                        largest_size = 0
                        for f in relevant_part_files:
                            if f.endswith('.part') and os.path.exists(f):
                                current_size = os.path.getsize(f)
                                if current_size > largest_size:
                                    largest_size = current_size
                                    largest_part_file = f
                        if largest_part_file:
                            video_part_file = largest_part_file
                            log_debug(f"Не найден специфичный видео .part файл, используется самый большой .part файл: {video_part_file}")

                    if video_part_file:
                        try:
                            final_video_only_name = f"{output_name}_video_only.{output_format}"
                            final_video_only_path = os.path.join(output_path, final_video_only_name)

                            log_debug(f"Попытка сохранить видеопоток из '{video_part_file}' как '{final_video_only_path}'")

                            if os.path.exists(video_part_file):
                                print(Fore.GREEN + f"Попытка сохранить видеопоток как '{final_video_only_name}'..." + Style.RESET_ALL)
                                cmd = [
                                    ffmpeg_path,
                                    '-loglevel', 'info ',
                                    '-i', video_part_file,
                                    '-c', 'copy',
                                    '-map', '0:v:0',
                                    '-y',
                                    final_video_only_path
                                ]
                                log_debug(f"Запуск FFmpeg для сохранения частичного видео: {' '.join(cmd)}")
                                process = subprocess.run(cmd, capture_output=True, text=True, check=True,
                                                         creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8', errors='replace')
                                log_debug(f"FFmpeg stdout:\n{process.stdout}")
                                log_debug(f"FFmpeg stderr:\n{process.stderr}")

                                print(Fore.GREEN + f"Частичный видеопоток сохранен как: {final_video_only_path}" + Style.RESET_ALL)
                                log_debug(f"Частичный видеопоток сохранен: {final_video_only_path}")
                                for f in relevant_part_files:
                                    try:
                                        os.remove(f)
                                        log_debug(f"Удален временный файл: {f}")
                                    except OSError as e:
                                        print(Fore.RED + f"Не удалось удалить файл {f}: {e}" + Style.RESET_ALL)
                                        log_debug(f"Ошибка удаления файла {f}: {e}")
                                print(Fore.GREEN + "Все временные файлы удалены." + Style.RESET_ALL)

                            else:
                                print(Fore.RED + f"Файл '{video_part_file}' не найден для сохранения." + Style.RESET_ALL)
                                log_debug(f"Файл '{video_part_file}' не найден для сохранения.")
                                choice_after_fail = input(Fore.CYAN + "Не удалось сохранить видеопоток. Удалить остальные временные файлы (1), или оставить как есть (Enter)? (по умолчанию: оставить): " + Style.RESET_ALL).strip()
                                if choice_after_fail == '1':
                                    for f in relevant_part_files:
                                        try:
                                            os.remove(f)
                                            log_debug(f"Удален временный файл: {f}")
                                        except OSError as e:
                                            print(Fore.RED + f"Не удалось удалить файл {f}: {e}" + Style.RESET_ALL)
                                            log_debug(f"Ошибка удаления файла {f}: {e}")
                                    print(Fore.GREEN + "Все временные файлы удалены." + Style.RESET_ALL)

                        except (subprocess.CalledProcessError, FileNotFoundError) as ffmpeg_err:
                            print(Fore.RED + f"Ошибка при обработке файла с помощью FFmpeg: {ffmpeg_err}" + Style.RESET_ALL)
                            log_debug(f"Ошибка FFmpeg при сохранении частичного видео: {ffmpeg_err}\n{traceback.format_exc()}")
                            choice_after_fail = input(Fore.CYAN + "Не удалось сохранить видеопоток. Удалить все временные файлы (1), или оставить как есть (Enter)? (по умолчанию: оставить): " + Style.RESET_ALL).strip()
                            if choice_after_fail == '1':
                                for f in relevant_part_files:
                                    try:
                                        os.remove(f)
                                        log_debug(f"Удален временный файл: {f}")
                                    except OSError as e:
                                        print(Fore.RED + f"Не удалось удалить файл {f}: {e}" + Style.RESET_ALL)
                                        log_debug(f"Ошибка удаления файла {f}: {e}")
                                print(Fore.GREEN + "Все временные файлы удалены." + Style.RESET_ALL)
                    else:
                        print(Fore.RED + "Не удалось найти основной видео .part файл для сохранения." + Style.RESET_ALL)
                        log_debug("Не удалось найти основной видео .part файл для сохранения.")
                        choice_after_fail = input(Fore.CYAN + "Удалить все временные файлы (1), или оставить как есть (Enter)? (по умолчанию: оставить): " + Style.RESET_ALL).strip()
                        if choice_after_fail == '1':
                            for f in relevant_part_files:
                                try:
                                    os.remove(f)
                                    log_debug(f"Удален временный файл: {f}")
                                except OSError as e:
                                    print(Fore.RED + f"Не удалось удалить файл {f}: {e}" + Style.RESET_ALL)
                                    log_debug(f"Ошибка удаления файла {f}: {e}")
                            print(Fore.GREEN + "Все временные файлы удалены." + Style.RESET_ALL)

                elif choice == '2' or (choice == '1' and (not has_video_part or audio_id is None)):
                    for f in relevant_part_files:
                        try:
                            os.remove(f)
                            log_debug(f"Удален временный файл: {f}")
                        except OSError as e:
                            print(Fore.RED + f"Не удалось удалить файл {f}: {e}" + Style.RESET_ALL)
                            log_debug(f"Ошибка удаления файла {f}: {e}")
                    print(Fore.GREEN + "Все временные файлы удалены." + Style.RESET_ALL)
                else:
                    print(Fore.YELLOW + "Временные файлы оставлены как есть." + Style.RESET_ALL)
                    log_debug("Временные файлы оставлены как есть.")
            else:
                print(Fore.YELLOW + "Не найдено незавершенных файлов загрузки для обработки." + Style.RESET_ALL)
                log_debug("Не найдено незавершенных файлов загрузки для обработки.")

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
