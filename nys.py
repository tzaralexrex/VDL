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

Logic rules implemented (as you requested):
- "Начало интервала" для каждого итогового блока всегда берётся как начало верхней
  строчки (верхнего) исходного субтитра из файла.
- Если блок состоит из пары (верхняя + нижняя) — конец блока равен концу второй
  (нижней) строчки, **если она не перекрывается** следующим исходным субтитром.
- Если же нижняя строчка перекрывает следующий исходный субтитр, то конец блока
  устанавливается равным началу первой строчки следующего исходного субтитра
  минус "межтитровый интервал" (конфигурируемая величина, по умолчанию 0 ms).
- Для одиночных блоков (без пары) действует та же логика: конец = min(original_end, start_next - pad).
- Скрипт гарантирует, что итоговые блоки **не перекрываются** и имеют разумную
  минимальную длительность (MIN_DISPLAY_MS).
"""

import re
import sys
import os
from dataclasses import dataclass
from typing import List

# --- Параметры, которые можно настроить ---
MIN_DISPLAY_MS = 200           # минимальная длительность любого итогового блока, ms
INTER_CAPTION_GAP_MS = 0       # "межтитровый интервал" (тот, что вы просили вычитать из следующего старта)

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
    временные границы по вашим правилам.
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
                block_start = top.start  # явно: начало = начало верхней строки

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

        # если сюда попали — топ либо одиночный, либо не образует пару
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

    # Финальная проверка: убедимся, что блоки строго не накладываются и идут по времени
    for k in range(1, len(out)):
        prev = out[k-1]
        cur = out[k]
        if cur.start < prev.end:
            # если по какой-то причине начальная позиция следующего < конца предыдущего,
            # мы корректируем конец предыдущего, но никогда не сдвигаем начало (по правилу).
            prev.end = cur.start
            # и гарантируем минимальную длительность
            if prev.end < prev.start + MIN_DISPLAY_MS:
                prev.end = prev.start + MIN_DISPLAY_MS
                # если поправка предыдушего сделала его пересекающимся с текущим, аккуратно
                if prev.end > cur.start:
                    # в редком случае — сдвинем текущий старт минимально вправо
                    cur.start = prev.end
                    if cur.end < cur.start + MIN_DISPLAY_MS:
                        cur.end = cur.start + MIN_DISPLAY_MS

    # Переиндексация на всякий случай
    for idx, c in enumerate(out, start=1):
        if c.end < c.start:
            c.end = c.start + MIN_DISPLAY_MS
        c.idx = idx

    return out


def write_srt(path: str, caps: List[Caption]):
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(caps, start=1):
            f.write(f"{i}\n{ms_to_srt_time(c.start)} --> {ms_to_srt_time(c.end)}\n{c.text}\n\n")


def process_file(inp: str, outp: str):
    if not os.path.exists(inp):
        print(f"Input file not found: {inp}")
        return
    caps = parse_srt(inp)
    norm = normalize_by_pairs_strict(caps)
    write_srt(outp, norm)
    print(f"Processed {inp} -> {outp} ({len(norm)} blocks)")


def process_all_in_dir(recursive=False):
    if recursive:
        for root, dirs, files in os.walk('.'):
            for f in files:
                if f.lower().endswith('.srt'):
                    path = os.path.join(root, f)
                    base, ext = os.path.splitext(path)
                    outp = base + "_normalized.srt"
                    process_file(path, outp)
    else:
        files = [f for f in os.listdir('.') if f.lower().endswith('.srt')]
        if not files:
            print("No .srt files found in current directory.")
            sys.exit(1)
        for f in files:
            base, ext = os.path.splitext(f)
            outp = base + "_normalized.srt"
            process_file(f, outp)


def main():
    print("Normalize Youtube subtitles\n")
    if len(sys.argv) == 3:
        inp, outp = sys.argv[1], sys.argv[2]
        process_file(inp, outp)
    elif len(sys.argv) == 2:
        arg = sys.argv[1].lower()
        if arg in ("-a", "-all", "--all"):
            process_all_in_dir(recursive=False)
        elif arg in ("-r", "--recursive"):
            process_all_in_dir(recursive=True)
        else:
            inp = sys.argv[1]
            if not inp.lower().endswith(".srt"):
                inp = inp + ".srt"
            base, ext = os.path.splitext(inp)
            outp = base + "_normalized.srt"
            process_file(inp, outp)
    else:
        print("Usage: python nys.py input.srt [output.srt]\n"
              "       python nys.py -a | -all | --all\n"
              "       python nys.py -r | --recursive")
        sys.exit(2)


if __name__ == "__main__":
    main()
