"""
pytest для Пакет П-Библиотека, Этап 1 (владелец, 2026-07-15): раздел
🎓 МЕТОДОЛОГИЯ в ОБУЧЕНИЕ. Источник -- methodology_content.py, парсер
knowledge/METHODOLOGY_CORE.md по темам `## N. Title`. Покрывает: (1) парсер
секций, (2) безопасная Telegram-Markdown конвертация (сбалансированные
'*'/'_'/backtick с учётом экранирования и code-спанов -- METHODOLOGY_CORE.md
плотно насыщен код-идентификаторами вроде `pump_detector.py`, не всегда
обёрнутыми в backtick в исходнике), (3) рендер экранов (список тем/текст
темы) без падения на ВСЕХ реальных темах файла, под лимит 4096.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import methodology_content as mc


def _run(coro):
    return asyncio.run(coro)


_CODE_SPAN_RE = re.compile(r'`[^`]*`')


def _active_delim_counts_outside_code(s: str) -> dict:
    """Считает '*'/'_' ТОЛЬКО вне backtick-спанов, с учётом экранирования
    (`\\_`/`\\*` -- литеральные символы, не активные markdown-делимитеры).
    Backtick-парность проверяется отдельно на всей строке (Telegram не
    парсит markdown внутри `` `...` ``, но сама пара '`' обязана быть чётной)."""
    parts = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(s):
        parts.append(s[last:m.start()])
        last = m.end()
    parts.append(s[last:])
    outside = "".join(parts)
    counts = {"*": 0, "_": 0}
    i, n = 0, len(outside)
    while i < n:
        c = outside[i]
        if c == "\\" and i + 1 < n and outside[i + 1] in ("*", "_", "`"):
            i += 2
            continue
        if c in counts:
            counts[c] += 1
        i += 1
    return counts


def _assert_telegram_markdown_safe(text: str, label: str):
    assert text.count("`") % 2 == 0, f"{label}: нечётное число backtick"
    counts = _active_delim_counts_outside_code(text)
    assert counts["*"] % 2 == 0, f"{label}: несбалансированные '*' вне code-спанов"
    assert counts["_"] == 0, f"{label}: неэкранированный '_' вне code-спанов"


# ── methodology_content.load_methodology_sections() ────────────────────────

def test_sections_parsed_and_nonempty():
    sections = mc.load_methodology_sections()
    assert len(sections) >= 20
    for s in sections:
        assert s["body"].strip(), s["id"]
        assert s["title"].strip(), s["id"]


def test_intro_section_present():
    sections = mc.load_methodology_sections()
    ids = [s["id"] for s in sections]
    assert "intro" in ids


def test_numbered_sections_have_numeric_id_and_clean_title():
    sections = mc.load_methodology_sections()
    sec1 = next(s for s in sections if s["id"] == "1")
    assert sec1["title"].startswith("Структура рынка")
    assert not sec1["title"][0].isdigit() or "." not in sec1["title"][:3]


def test_non_numbered_trailing_section_gets_ascii_safe_id():
    sections = mc.load_methodology_sections()
    non_ascii_ids = [s["id"] for s in sections if not s["id"].isascii()]
    assert non_ascii_ids == [], f"не-ASCII callback id (сломает callback_data): {non_ascii_ids}"
    ids = [s["id"] for s in sections]
    assert all(re.match(r'^[a-zA-Z0-9]+$', i) for i in ids)


def test_no_duplicate_section_ids():
    sections = mc.load_methodology_sections()
    ids = [s["id"] for s in sections]
    assert len(ids) == len(set(ids))


def test_missing_file_returns_empty_list_honest():
    assert mc.load_methodology_sections("/nonexistent/path/METHODOLOGY_CORE.md") == []


def test_find_section_known_and_unknown():
    assert mc.find_section("9")["title"].startswith("R:R")
    assert mc.find_section("does-not-exist") is None


# ── methodology_content.to_telegram_markdown() -- безопасность ─────────────

def test_bold_conversion_double_to_single_star():
    out = mc.to_telegram_markdown("**жирный текст**")
    assert out == "*жирный текст*"


def test_code_span_left_untouched():
    out = mc.to_telegram_markdown("вызов `pump_detector.py` тут")
    assert "`pump_detector.py`" in out


def test_stray_underscore_outside_code_escaped():
    out = mc.to_telegram_markdown("файл MISMATCH_REPORT.md без бэктиков")
    assert "MISMATCH\\_REPORT.md" in out
    _assert_telegram_markdown_safe(out, "stray_underscore_test")


def test_stray_underscore_inside_bold_escaped_too():
    out = mc.to_telegram_markdown("**important_variable здесь**")
    _assert_telegram_markdown_safe(out, "bold_with_underscore")


def test_underscore_inside_code_span_not_escaped():
    out = mc.to_telegram_markdown("`ta_extra._find_fractals` без проблем")
    assert "`ta_extra._find_fractals`" in out


def test_all_real_sections_produce_safe_markdown():
    sections = mc.load_methodology_sections()
    assert sections, "секции не найдены -- проверь путь к METHODOLOGY_CORE.md"
    for s in sections:
        converted = mc.to_telegram_markdown(s["body"])
        _assert_telegram_markdown_safe(converted, s["id"])


# ── рендер: список тем / текст темы (bot.py) ────────────────────────────────

class _FakeQuery:
    def __init__(self):
        self.text = None
        self.kwargs = None

    async def edit_message_text(self, text, **kw):
        self.text = text
        self.kwargs = kw


class _FakeBotEditor:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.calls.append({"text": text, "kw": kw})


def _kb_texts(kw):
    return [btn.text for row in kw["reply_markup"].inline_keyboard for btn in row]


def _kb_datas(kw):
    return [btn.callback_data for row in kw["reply_markup"].inline_keyboard for btn in row]


def test_render_metod_topic_list_first_page():
    q = _FakeQuery()
    _run(bot._mv2_render_metod(q))
    assert "МЕТОДОЛОГИЯ" in q.text
    texts = _kb_texts(q.kwargs)
    assert any("Структура рынка" in t for t in texts)
    datas = _kb_datas(q.kwargs)
    assert "show_menu" in datas
    assert "mv2_obuchenie" in datas  # back_to


def test_render_metod_topic_list_has_show_more():
    q = _FakeQuery()
    _run(bot._mv2_render_metod(q))
    datas = _kb_datas(q.kwargs)
    assert any(d and d.startswith("mv2_obuchenie_metod_more_") for d in datas)


def test_render_metod_topic_list_second_page():
    q = _FakeQuery()
    _run(bot._mv2_render_metod(q, offset=bot.METOD_TOPIC_PAGE_SIZE))
    texts = _kb_texts(q.kwargs)
    assert texts  # непустой список тем на второй странице


def test_render_metod_topic_basic():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id="9", page=0))
    call = fb.calls[-1]
    assert "R:R" in call["text"]
    assert len(call["text"]) <= 4096
    datas = _kb_datas(call["kw"])
    assert "mv2_obuchenie_metod" in datas  # back_to список тем


def test_render_metod_topic_unknown_id_honest_error():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id="nope", page=0))
    assert "не найдена" in fb.calls[-1]["text"]


def test_render_metod_topic_page_out_of_range_clamped():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id="1", page=999))
    assert fb.calls
    fb2 = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb2, chat_id=1, message_id=1, topic_id="1", page=-3))
    assert fb2.calls


def test_render_metod_topic_pagination_nav_buttons_present_for_long_section():
    """Секция 18 (14900+ символов исходника) точно требует несколько страниц."""
    fb = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id="18", page=0))
    call = fb.calls[-1]
    datas = _kb_datas(call["kw"])
    assert any(d and d.startswith("mv2_obuchenie_metod_topic_18_") for d in datas)
    assert "стр." in call["text"]


def test_render_all_real_topics_render_without_crash_under_limit():
    sections = mc.load_methodology_sections()
    for s in sections:
        fb = _FakeBotEditor()
        _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id=s["id"], page=0))
        call = fb.calls[-1]
        assert len(call["text"]) <= 4096, s["id"]
        assert "не найдена" not in call["text"], s["id"]


def test_intro_topic_renders():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_metod_topic(fb, chat_id=1, message_id=1, topic_id="intro", page=0))
    call = fb.calls[-1]
    assert "Введение" in call["text"]


# ── роутер (обучение -> методология) ────────────────────────────────────────

def test_router_mv2_obuchenie_metod_dispatches():
    class _Ctx:
        bot = None

    class _Q:
        def __init__(self):
            self.message = _Msg()
            self.text = None
            self.kwargs = None

        async def edit_message_text(self, text, **kw):
            self.text = text
            self.kwargs = kw

    class _Msg:
        chat_id = 1
        message_id = 1

    class _Update:
        def __init__(self, q):
            self.callback_query = q

    q = _Q()
    _run(bot._mv2_callback_router(_Update(q), _Ctx(), "mv2_obuchenie_metod"))
    assert "МЕТОДОЛОГИЯ" in q.text


def test_obuchenie_top_screen_has_metodologia_button():
    q = _FakeQuery()
    _run(bot._mv2_render_obuchenie(q))
    texts = _kb_texts(q.kwargs)
    assert "🎓 МЕТОДОЛОГИЯ" in texts
