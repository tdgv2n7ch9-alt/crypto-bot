"""
pytest для Пакет П-Обучение (владелец, 2026-07-15): раздел меню 📚 ОБУЧЕНИЕ
(курс «Криптотрейдинг -- 64 урока»). Покрывает: (1) целостность
course_content.py (64 урока + 2 бонусных "-Д" + 16 модулей, без дублей),
(2) пагинация текста урока под лимит Telegram 4096, (3) рендер экранов
(список модулей/уроки модуля/текст урока/шпаргалка/приложение) --
Markdown-безопасность (чётное число одинарных "*"), корректные back_to,
условное появление кнопки [❓ Словарь], (4) фикс диспетчеризации
"glossary_*" в callback_handler (найдено живьём при разработке этого
пакета -- см. bot.py комментарий на месте фикса).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import course_content as cc
import glossary


def _run(coro):
    return asyncio.run(coro)


def _count_unescaped_asterisks(text: str) -> int:
    """Telegram legacy Markdown -- одинарная '*' маркер bold, нечётное число
    в сообщении -> BadRequest 'Can't parse entities' (тот же класс регрессии,
    что tests/test_markdown_safety.py)."""
    return text.count("*")


# ── course_content.py -- целостность ────────────────────────────────────────

def test_total_lesson_count_is_66():
    assert cc.total_lesson_count() == 66


def test_all_64_numbered_lessons_present_exactly_once():
    nums = []
    for m in cc.MODULES:
        for l in m["lessons"]:
            if l["num"].isdigit():
                nums.append(int(l["num"]))
    assert sorted(nums) == list(range(1, 65))


def test_bonus_dash_lessons_present():
    keys = {l["key"] for m in cc.MODULES for l in m["lessons"]}
    assert "33d" in keys
    assert "52d" in keys


def test_16_modules():
    assert len(cc.MODULES) == 16


def test_module_16_has_no_numbered_lessons_but_cheatsheet_and_appendix_exist():
    m16 = cc.find_module(16)
    assert m16["lessons"] == []
    assert cc.CHEATSHEET["body"]
    assert cc.APPENDIX["body"]


def test_find_lesson_returns_module_and_lesson():
    module, lesson = cc.find_lesson("9")
    assert lesson["title"] == "Risk/Reward (R/R)"
    assert module["id"] == 3


def test_find_lesson_unknown_key_honest_none():
    module, lesson = cc.find_lesson("does-not-exist")
    assert module is None and lesson is None


def test_find_module_unknown_id_honest_none():
    assert cc.find_module(999) is None


def test_no_duplicate_lesson_keys():
    keys = [l["key"] for m in cc.MODULES for l in m["lessons"]]
    assert len(keys) == len(set(keys))


# ── все тексты уроков/шпаргалки/приложения -- сбалансированные "*" ─────────

def test_all_lesson_bodies_have_even_asterisk_count():
    bad = []
    for m in cc.MODULES:
        for l in m["lessons"]:
            if _count_unescaped_asterisks(l["body"]) % 2 != 0:
                bad.append(f"урок {l['num']}")
    assert bad == [], f"нечётное число '*' (Markdown сломается): {bad}"


def test_cheatsheet_and_appendix_have_even_asterisk_count():
    assert _count_unescaped_asterisks(cc.CHEATSHEET["body"]) % 2 == 0
    assert _count_unescaped_asterisks(cc.APPENDIX["body"]) % 2 == 0


def test_methodology_notes_have_even_asterisk_count():
    for m in cc.MODULES:
        for l in m["lessons"]:
            note = l.get("methodology_note")
            if note:
                assert _count_unescaped_asterisks(note) % 2 == 0, l["num"]


# ── _obuchenie_paginate_text() ──────────────────────────────────────────────

def test_paginate_short_text_single_page():
    pages = bot._obuchenie_paginate_text("короткий текст", limit=100)
    assert pages == ["короткий текст"]


def test_paginate_empty_text_returns_one_empty_page():
    assert bot._obuchenie_paginate_text("") == [""]


def test_paginate_exact_limit_boundary_single_page():
    text = "x" * 100
    assert bot._obuchenie_paginate_text(text, limit=100) == [text]


def test_paginate_splits_on_paragraph_boundary():
    text = "первый" + "\n\n" + "б" * 50
    pages = bot._obuchenie_paginate_text(text, limit=10)
    assert len(pages) >= 2
    assert "".join(pages).replace("\n\n", "").count("б") == 50


def test_paginate_all_pages_within_limit():
    text = "\n\n".join([f"абзац номер {i} " + "слово " * 20 for i in range(30)])
    pages = bot._obuchenie_paginate_text(text, limit=200)
    assert len(pages) > 1
    for p in pages:
        assert len(p) <= 200


def test_paginate_single_huge_paragraph_no_blank_lines_hard_cut():
    text = "ы" * 500  # один "абзац" без \n вообще, длиннее лимита
    pages = bot._obuchenie_paginate_text(text, limit=100)
    assert len(pages) == 5
    for p in pages:
        assert len(p) <= 100
    assert "".join(pages) == text


def test_paginate_no_content_lost():
    text = "\n\n".join(["a" * 40, "b" * 40, "c" * 300, "d" * 10])
    pages = bot._obuchenie_paginate_text(text, limit=60)
    rejoined = "\n\n".join(pages)
    for chunk in ("a" * 40, "b" * 40, "d" * 10):
        assert chunk in rejoined
    assert rejoined.count("c") == 300


# ── рендер: список модулей ──────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self):
        self.text = None
        self.kwargs = None

    async def edit_message_text(self, text, **kw):
        self.text = text
        self.kwargs = kw


def _kb_rows(kw):
    return kw["reply_markup"].inline_keyboard


def _kb_texts(kw):
    return [btn.text for row in _kb_rows(kw) for btn in row]


def _kb_callback_datas(kw):
    return [btn.callback_data for row in _kb_rows(kw) for btn in row]


def test_render_obuchenie_shows_cheatsheet_button_and_first_page_modules():
    q = _FakeQuery()
    _run(bot._mv2_render_obuchenie(q))
    assert q.text is not None
    assert _count_unescaped_asterisks(q.text) % 2 == 0
    texts = _kb_texts(q.kwargs)
    assert course_content_cheatsheet_title() in texts
    assert "1. Основы и психология" in texts


def course_content_cheatsheet_title():
    return cc.CHEATSHEET["title"]


def test_render_obuchenie_first_page_has_show_more_button():
    q = _FakeQuery()
    _run(bot._mv2_render_obuchenie(q))
    datas = _kb_callback_datas(q.kwargs)
    assert any(d and d.startswith("mv2_obuchenie_more_") for d in datas)


def test_render_obuchenie_second_page_no_more_button_shows_remaining_modules():
    q = _FakeQuery()
    _run(bot._mv2_render_obuchenie(q, offset=bot.OBUCHENIE_MODULE_PAGE_SIZE))
    texts = _kb_texts(q.kwargs)
    assert "16. Шпаргалка + Приложение: свечные паттерны" in texts
    datas = _kb_callback_datas(q.kwargs)
    assert not any(d and d.startswith("mv2_obuchenie_more_") for d in datas)


def test_render_obuchenie_has_back_and_home_row():
    q = _FakeQuery()
    _run(bot._mv2_render_obuchenie(q))
    datas = _kb_callback_datas(q.kwargs)
    assert "show_menu" in datas


# ── рендер: список уроков модуля ────────────────────────────────────────────

class _FakeBotEditor:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.calls.append({"text": text, "kw": kw})


def test_render_module_lists_all_lessons_module_1():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_module(fb, chat_id=1, message_id=1, module_id=1))
    call = fb.calls[-1]
    texts = _kb_texts(call["kw"])
    assert "Урок 1. Четыре правила торговли без лишних рисков" in texts
    assert "Урок 2. Психология правильного трейдинга" in texts
    assert "Урок 3. Индекс страха и жадности. Бычий и медвежий рынок" in texts
    assert _count_unescaped_asterisks(call["text"]) % 2 == 0


def test_render_module_16_shows_cheat_and_appendix_buttons_not_lessons():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_module(fb, chat_id=1, message_id=1, module_id=16))
    call = fb.calls[-1]
    texts = _kb_texts(call["kw"])
    assert cc.CHEATSHEET["title"] in texts
    assert cc.APPENDIX["title"] in texts
    assert not any(t.startswith("Урок ") for t in texts)


def test_render_module_unknown_id_honest_error_not_crash():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_module(fb, chat_id=1, message_id=1, module_id=999))
    assert "не найден" in fb.calls[-1]["text"]


def test_render_module_lesson_buttons_point_to_page_0():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_module(fb, chat_id=1, message_id=1, module_id=3))
    call = fb.calls[-1]
    datas = _kb_callback_datas(call["kw"])
    assert "mv2_obuchenie_lesson_9_0" in datas  # Урок 9 (R/R) -- в модуле 3
    assert "mv2_obuchenie_lesson_33d_0" not in datas  # не модуль 3


def test_render_module_7_uses_33d_key_for_bonus_lesson():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_module(fb, chat_id=1, message_id=1, module_id=7))
    call = fb.calls[-1]
    datas = _kb_callback_datas(call["kw"])
    assert "mv2_obuchenie_lesson_33d_0" in datas
    texts = _kb_texts(call["kw"])
    assert any("33-Д" in t for t in texts)


# ── рендер: текст урока ─────────────────────────────────────────────────────

def test_render_lesson_basic_text_and_no_glossary_button_when_no_terms():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="2", page=0))
    call = fb.calls[-1]
    assert "Психология правильного трейдинга" in call["text"]
    assert "Отключай эмоции" in call["text"]
    datas = _kb_callback_datas(call["kw"])
    assert not any(d and d.startswith("glossary_") for d in datas)
    assert _count_unescaped_asterisks(call["text"]) % 2 == 0


def test_render_lesson_shows_glossary_button_when_terms_present():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="1", page=0))
    call = fb.calls[-1]
    datas = _kb_callback_datas(call["kw"])
    assert "glossary_course_1" in datas


def test_render_lesson_with_methodology_note_shown_on_last_page():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="9", page=0))
    call = fb.calls[-1]
    assert "Связь с методикой проекта" in call["text"]
    assert "METHODOLOGY_CORE" in call["text"]
    # честность: не выдаём порог курса за боевой -- оговорка должна быть текстом
    assert "1:2" in call["text"] or "R/R" in call["text"]


def test_render_lesson_unknown_key_honest_error_not_crash():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="nope", page=0))
    assert "не найден" in fb.calls[-1]["text"]


def test_render_lesson_back_to_points_to_parent_module():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="9", page=0))
    call = fb.calls[-1]
    datas = _kb_callback_datas(call["kw"])
    assert "mv2_obuchenie_mod_3" in datas  # урок 9 -- модуль 3


def test_render_lesson_page_out_of_range_clamped_not_crash():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1, lesson_key="1", page=99))
    assert fb.calls  # не упало
    fb2 = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_lesson(fb2, chat_id=1, message_id=1, lesson_key="1", page=-5))
    assert fb2.calls


def test_render_all_66_lessons_render_without_crash_and_balanced_markdown():
    for module in cc.MODULES:
        for lesson in module["lessons"]:
            fb = _FakeBotEditor()
            _run(bot._mv2_render_obuchenie_lesson(fb, chat_id=1, message_id=1,
                                                   lesson_key=lesson["key"], page=0))
            call = fb.calls[-1]
            assert _count_unescaped_asterisks(call["text"]) % 2 == 0, lesson["key"]
            assert len(call["text"]) <= 4096, lesson["key"]


# ── рендер: шпаргалка / приложение ──────────────────────────────────────────

def test_render_cheatsheet():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_static(fb, chat_id=1, message_id=1, kind="cheat", page=0))
    call = fb.calls[-1]
    assert "Риск-менеджмент" in call["text"]
    assert _count_unescaped_asterisks(call["text"]) % 2 == 0
    assert len(call["text"]) <= 4096


def test_render_appendix_has_glossary_button_marubozu():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_static(fb, chat_id=1, message_id=1, kind="appendix", page=0))
    call = fb.calls[-1]
    assert "Marubozu" in call["text"]
    datas = _kb_callback_datas(call["kw"])
    assert "glossary_course_appendix" in datas


def test_render_static_back_to_obuchenie_root():
    fb = _FakeBotEditor()
    _run(bot._mv2_render_obuchenie_static(fb, chat_id=1, message_id=1, kind="cheat", page=0))
    datas = _kb_callback_datas(fb.calls[-1]["kw"])
    assert "mv2_obuchenie" in datas


# ── glossary.py -- динамическая загрузка терминов курса ───────────────────

def test_glossary_course_terms_loaded_for_lesson_1():
    assert glossary.CARD_TERMS.get("course_1") == ["FOMO"]
    assert "FOMO" in glossary.TERMS


def test_glossary_course_appendix_terms_loaded():
    assert glossary.CARD_TERMS.get("course_appendix") == ["Marubozu"]


def test_glossary_format_card_text_for_course_lesson():
    text = glossary.format_card_glossary_text("course_10")
    assert "Мейкер" in text
    assert "Тейкер" in text


def test_glossary_format_card_text_for_lesson_without_terms_is_honest_nd():
    """Урок 9 (R/R) не привязан к terms в course_content.py -- честное 'н/д',
    не выдуманный список."""
    text = glossary.format_card_glossary_text("course_9")
    assert "н/д" in text


def test_all_course_terms_referenced_in_card_terms_exist_in_glossary_terms():
    """Каждый термин, на который ссылается урок в course_content.py, обязан
    реально существовать в glossary.TERMS -- иначе кнопка [❓ Словарь]
    покажет пустую расшифровку (честный баг конфигурации, ловим тестом)."""
    missing = []
    for module in cc.MODULES:
        for lesson in module["lessons"]:
            for term in lesson.get("terms", []):
                if term not in glossary.TERMS:
                    missing.append((lesson["key"], term))
    for term in cc.APPENDIX.get("terms", []):
        if term not in glossary.TERMS:
            missing.append(("appendix", term))
    assert missing == []


# ── фикс диспетчеризации glossary_* (найдено живьём в этом пакете) ─────────

def test_glossary_prefixed_data_no_longer_dead_branch_in_source():
    """Регресс-замок: раньше 'elif data.startswith(\"glossary_\")' существовал
    ТОЛЬКО внутри _mv2_callback_router, который callback_handler вызывал
    ТОЛЬКО при data.startswith('mv2_') -- glossary_whale/pump/x100/tochki
    были мёртвыми кнопками. Проверяем читая исходник callback_handler
    (а не мокая целиком Update/CallbackQuery -- тяжеловесно для этого
    файла): ветка 'glossary_' должна быть достижима НЕЗАВИСИМО от 'mv2_'."""
    import inspect
    src = inspect.getsource(bot.callback_handler)
    assert 'data.startswith("glossary_")' in src, \
        "callback_handler больше не содержит прямой ветки glossary_ -- регресс вернулся"


def test_mv2_callback_router_handles_glossary_course_data_directly():
    """Прямой вызов роутера (обходя callback_handler/Update-моки) -- убеждаемся,
    что сам обработчик "glossary_course_1" работает и не падает."""
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
    upd = _Update(q)
    _run(bot._mv2_callback_router(upd, _Ctx(), "glossary_course_1"))
    assert q.text is not None
    assert "FOMO" in q.text
