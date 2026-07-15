"""
methodology_content.py -- Пакет П-Библиотека, Этап 1 (владелец, 2026-07-15):
раздел 🎓 МЕТОДОЛОГИЯ в ОБУЧЕНИЕ. Источник -- `knowledge/METHODOLOGY_CORE.md`
(уже существующий, ранее написанный документ проекта, НЕ PDF владельца --
контент читается программно из .md-файла, single source of truth ОСТАЁТСЯ
сам файл, здесь только парсер+рендер-конвертер. Правка методики -- правка
.md-файла, не этого модуля.

Разбивка на темы -- по существующим заголовкам `## N. Title` файла (21 тема
на момент написания + необязательное "Введение" из преамбулы перед первой
темой) -- контент каждой темы НЕ пересказывается и НЕ сокращается, только
конвертируется под безопасный Telegram legacy Markdown (см.
`to_telegram_markdown()` ниже).
"""
import os
import re

METHODOLOGY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "knowledge", "METHODOLOGY_CORE.md")

_HEADER_RE = re.compile(r'^## (.+)$', re.MULTILINE)
_NUMBERED_RE = re.compile(r'^(\d+)\.\s*(.+)$')
_CODE_SPAN_RE = re.compile(r'`[^`]*`')
_BOLD_RE = re.compile(r'\*\*([^*]+?)\*\*')


def load_methodology_sections(path: str = None) -> list:
    """Парсит METHODOLOGY_CORE.md на секции по заголовкам `## ...`. Секции с
    ведущим числом ("## 9. R:R-правила") получают `id` = это число (строка) и
    `title` без номера; остальные (например финальное "## Как читать этот
    документ") получают `id` -- слаг из заголовка. Преамбула ДО первого `##`
    (за вычетом самого H1-заголовка файла) -- отдельная секция id="intro".
    Возвращает [] с честным логом, если файл не найден -- не выдумывает
    контент."""
    path = path or METHODOLOGY_PATH
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    matches = list(_HEADER_RE.finditer(text))
    sections = []

    if matches:
        intro_raw = text[:matches[0].start()]
        intro_lines = intro_raw.split("\n", 1)
        intro_body = intro_lines[1].strip() if len(intro_lines) > 1 else ""
        if intro_body:
            sections.append({"id": "intro", "title": "Введение", "body": intro_body})

    for i, m in enumerate(matches):
        header = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        num_match = _NUMBERED_RE.match(header)
        if num_match:
            sec_id = num_match.group(1)
            title = num_match.group(2).strip()
        else:
            # ASCII-safe id для Telegram callback_data -- заголовок может
            # быть кириллицей ("Как читать этот документ"), сам callback_data
            # должен оставаться латиницей/цифрами (тот же принцип, что и
            # символы-тикеры везде в bot.py).
            sec_id = f"extra{i}"
            title = header
        sections.append({"id": sec_id, "title": title, "body": body})

    return sections


def _escape_chars(s: str) -> str:
    """Экранирует '_' и одиночный '*' -- буквальный текст, видимый
    пользователю, НЕ меняется (Telegram рендерит `\\_`/`\\*` как обычные
    символы). Нужно, потому что METHODOLOGY_CORE.md -- технический документ
    с код-идентификаторами вроде `pump_detector.py`/`MISMATCH_REPORT.md`,
    не всегда обёрнутыми в backtick в исходнике -- голый '_' вне code-спана
    для Telegram legacy Markdown -- маркер курсива, нечётное количество ->
    `Can't parse entities`."""
    return s.replace("_", "\\_").replace("*", "\\*")


def _process_plain_segment(segment: str) -> str:
    """Обрабатывает текст ВНЕ backtick-спанов: `**bold**` -> `*bold*`
    (содержимое bold тоже экранируется на случай код-идентификаторов внутри
    выделения), всё остальное экранируется целиком через `_escape_chars`."""
    out = []
    last = 0
    for m in _BOLD_RE.finditer(segment):
        out.append(_escape_chars(segment[last:m.start()]))
        out.append("*" + _escape_chars(m.group(1)) + "*")
        last = m.end()
    out.append(_escape_chars(segment[last:]))
    return "".join(out)


def to_telegram_markdown(text: str) -> str:
    """Конвертирует обычный Markdown METHODOLOGY_CORE.md в безопасный
    Telegram legacy Markdown: backtick-спаны (` `` `) остаются НЕТРОНУТЫМИ
    (Telegram не парсит markdown внутри них -- код-идентификаторы там уже
    безопасны как есть), всё остальное проходит через
    `_process_plain_segment` (bold-конверсия + экранирование stray '_'/'*')."""
    parts = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        parts.append(_process_plain_segment(text[last:m.start()]))
        parts.append(m.group())
        last = m.end()
    parts.append(_process_plain_segment(text[last:]))
    return "".join(parts)


def find_section(section_id: str, path: str = None):
    """Возвращает секцию по id, или None -- честно, не выдумывает."""
    for s in load_methodology_sections(path):
        if s["id"] == section_id:
            return s
    return None
