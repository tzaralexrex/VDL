# -*- coding: utf-8 -*-
"""
Normalize YouTube-style alternating/overlapping SRT into sequential, non-overlapping
subtitles according to the user's precise timing rules.

Filename: nys.py

Usage:
  python nys.py input.srt output.srt
  python nys.py input.srt
  python nys.py input   (will look for input.srt)
  python nys.py -a     (process all .srt files in current dir)
  python nys.py -r     (process all .srt files recursively)

Added features:
- support for flags -o / --overwrite (overwrite input files)
- support for flags -b / --backup (create input.srt.bak before overwrite)
- support for glued short flags: e.g. -bao == -b -a -o
"""

import re
import sys
import os
import shutil
from dataclasses import dataclass
from typing import List

# --- Параметры, которые можно настроить ---
MIN_DISPLAY_MS = 200           # минимальная длительность любого итогового блока, ms
INTER_CAPTION_GAP_MS = 0       # "межтитровый интервал" в ms (вычитается из start(next) при необходимости)

# --- Вспомогательные функции ---
def parse_time_to_ms(t: str) -> int:
    h, m, s_ms = t.split(":")
    s, ms = s_ms.split(",")
    return (int(h)*3600 + int(m)*60 + int(s)) * 1000 + int(ms)

def ms_to_srt_time(ms: int) -> str:
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms = ms % 1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

@dataclass
class Caption:
    idx: int
    start: int
    end: int
    text: str

SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s-->\s(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n{2,}|\Z)",
    re.DOTALL
)

def parse_srt(path: str) -> List[Caption]:
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
    Формируем блоки по строгой парной логике (без "скроллинга") и выставляем
    временные границы.
    """
    out: List[Caption] = []
    i = 0
    n = len(caps)

    while i < n:
        top = caps[i]
        # заранее определим есть ли нижняя строчка (следующий) и является ли она перекрывающей
        if i + 1 < n:
            bottom = caps[i+1]
            # считаем пару, только если интервалы пересекаются (или касаются):
            if not (top.end <= bottom.start or bottom.end <= top.start):
                # блок — пара top+bottom
                block_start = top.start  # начало = начало верхней строки

                # определяем конец в зависимости от следующего исходника
                if i + 2 < n:
                    next_first_start = caps[i+2].start
                    # если нижняя не перекрывается с next — оставляем её конец
                    if bottom.end <= next_first_start:
                        block_end = bottom.end
                    else:
                        # иначе конец = start(next) - INTER_CAPTION_GAP_MS
                        block_end = next_first_start - INTER_CAPTION_GAP_MS
                else:
                    # нет следующего — конец = конец нижней
                    block_end = bottom.end

                # защита: блок должен быть не короче MIN_DISPLAY_MS
                if block_end < block_start + MIN_DISPLAY_MS:
                    block_end = block_start + MIN_DISPLAY_MS

                out.append(Caption(len(out)+1, block_start, block_end, top.text + "\n" + bottom.text))
                i += 2
                continue

        # топ — одиночный или не образует пару
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

    # Финальная проверка: корректируем пересечения, но не сдвигаем начало блоков.
    for k in range(1, len(out)):
        prev = out[k-1]
        cur = out[k]
        if cur.start < prev.end:
            # корректируем конец предыдущего, гарантируем минимальную длительность
            prev.end = cur.start
            if prev.end < prev.start + MIN_DISPLAY_MS:
                prev.end = prev.start + MIN_DISPLAY_MS
                # если теперь prev.end вновь пересекает cur, сдвинем cur.start минимально вправо
                if prev.end > cur.start:
                    cur.start = prev.end
                    if cur.end < cur.start + MIN_DISPLAY_MS:
                        cur.end = cur.start + MIN_DISPLAY_MS

    # Переиндексация и защита
    for idx, c in enumerate(out, start=1):
        if c.end < c.start:
            c.end = c.start + MIN_DISPLAY_MS
        c.idx = idx

    return out

def write_srt(path: str, caps: List[Caption]):
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, start=1):
            f.write(f"{i}\n{ms_to_srt_time(c.start)} --> {ms_to_srt_time(c.end)}\n{c.text}\n\n")

def process_file(inp: str, outp: str, overwrite: bool = False, backup: bool = False):
    if not os.path.exists(inp):
        print(f"Input file not found: {inp}")
        return
    caps = parse_srt(inp)
    norm = normalize_by_pairs_strict(caps)

    target = outp
    if overwrite:
        # если нужно бэкапить — делаем копию исходника перед записью
        if backup:
            bakfile = inp + ".bak"
            if not os.path.exists(bakfile):
                shutil.copy2(inp, bakfile)
                print(f"Backup created: {bakfile}")
        target = inp

    write_srt(target, norm)
    print(f"Processed {inp} -> {target} ({len(norm)} blocks)")

def process_all_in_dir(recursive: bool = False, overwrite: bool = False, backup: bool = False):
    if recursive:
        for root, dirs, files in os.walk('.'):
            for f in files:
                if f.lower().endswith('.srt'):
                    path = os.path.join(root, f)
                    base, ext = os.path.splitext(path)
                    outp = base + "_normalized.srt"
                    process_file(path, outp, overwrite=overwrite, backup=backup)
    else:
        files = [f for f in os.listdir('.') if f.lower().endswith('.srt')]
        if not files:
            print("No .srt files found in current directory.")
            sys.exit(1)
        for f in files:
            base, ext = os.path.splitext(f)
            outp = base + "_normalized.srt"
            process_file(f, outp, overwrite=overwrite, backup=backup)

# --- Arg parsing helpers (склеенные короткие ключи поддерживаются) ---
def parse_args(argv: List[str]):
    """
    Возвращает (files, flags_set).
    Поддерживает:
      - отдельные длинные флаги (--overwrite), короткие (-o), а также склеенные короткие (-bao).
      - специальный случай: токен '-all' оставляем как есть (альтернатива -a)
    """
    files = []
    flags = set()
    for token in argv:
        if token.startswith("--"):
            flags.add(token.lower())
        elif token.startswith("-") and len(token) > 1:
            low = token.lower()
            # treat '-all' as whole alias (to keep backward compatibility)
            if low == "-all":
                flags.add("-all")
            else:
                # склеенные короткие ключи: -bao -> -b, -a, -o
                if len(token) > 2:
                    # если это явно один из известных длинных алиасов, берем как флаг
                    if low in ("-all",):
                        flags.add(low)
                    else:
                        for ch in token[1:]:
                            flags.add(("-" + ch).lower())
                else:
                    flags.add(low)
        else:
            files.append(token)
    return files, flags

def main():
    print("Normalize Youtube subtitles\n")

    # parse args
    argv = sys.argv[1:]
    if not argv:
        print("Usage: python nys.py input.srt [output.srt]\n"
              "       python nys.py -a | -all | --all [-o] [-b]\n"
              "       python nys.py -r | --recursive [-o] [-b]\n")
        sys.exit(2)

    files, flags = parse_args(argv)

    # Интерпретация флагов
    all_mode = any(f in flags for f in ("-a", "-all", "--all"))
    rec_mode = any(f in flags for f in ("-r", "--recursive"))
    overwrite = any(f in flags for f in ("-o", "--overwrite"))
    backup = any(f in flags for f in ("-b", "--backup")) and overwrite  # backup действителен только при overwrite

    # Если пользователь ввёл два имени файл -> исполользуем их и ИГНОРИРУЕМ флаг overwrite
    if len(files) >= 2:
        inp = files[0]
        outp = files[1]
        if not inp.lower().endswith(".srt"):
            inp = inp + ".srt"
        process_file(inp, outp, overwrite=False, backup=False)
        return

    # Если пользователь запросил обработку всех файлов / рекурсивную
    if all_mode:
        process_all_in_dir(recursive=False, overwrite=overwrite, backup=backup)
        return
    if rec_mode:
        process_all_in_dir(recursive=True, overwrite=overwrite, backup=backup)
        return

    # Режим одного файла
    if len(files) == 1:
        inp = files[0]
        if not inp.lower().endswith(".srt"):
            inp = inp + ".srt"
        if overwrite:
            outp = inp  # will be handled inside process_file (and backup if requested)
        else:
            base, ext = os.path.splitext(inp)
            outp = base + "_normalized.srt"
        process_file(inp, outp, overwrite=overwrite, backup=backup)
        return

    # Файлы или гументы не заданы
    print("Error: no input file specified and no mode selected.\n"
          "Usage: python nys.py input.srt [output.srt] | -a | -r  (use -o to overwrite, -b to backup when overwriting)")
    sys.exit(2)

if __name__ == "__main__":
    main()
