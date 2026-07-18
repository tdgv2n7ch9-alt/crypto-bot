"""
tests/test_public_text_no_source_tokens.py -- grep-guard источников (#292, п.0,
владелец, 2026-07-18, бессрочное правило): В ЛЮБОМ подписчицком тексте
(канал/карточки/бот/mini app) запрещено упоминание внутренних методологических
источников -- имена "Kira"/"ICT", имена аналитиков (Королев и др.), названия
курсов/каналов-источников. См. `knowledge/TELEGRAM_PRODUCT_V2_DESIGN.md` §0.

Метод -- ровно по спецификации дизайн-документа: вызывает реальные
`format_*`/`assemble_*` функции с синтетическими данными (не мок, не парсинг
source-текста -- иначе тест ловил бы упоминания в докстрингах/комментариях,
которые НЕ являются подписчицким текстом) и матчит РЕЗУЛЬТАТ против списка
запрещённых токенов.

Токен "тт" сознательно НЕ включён (см. design-doc §0 -- слишком короткий и
частотный, ложно сработает на "тест"/"путь" и т.п.; нужен более узкий паттерн
-- уточнить у владельца перед реализацией отдельно).

Список расширен владельцем 2026-07-18 (правило "источники не упоминаются
нигде"): kira/ict/королев/korolev/соболев/sobolev/2trade/pixel/заговор/
zagovor/вероятност + 2 channel ID -- см. полный repo-wide аудит (отдельный
скрипт, НЕ этот pytest, результат -- отчёт владельцу на сверку, не
автоматический CI-гейт для docs/данных).

Область покрытия сейчас: `card_v2.py` (этап (а) #292 -- канон-карточка авто-
точки входа) -- это единственный модуль, который в этой сессии реально
менялся под канон. Полный проход по ВСЕМ `format_*`/`_render_*` в проекте
(bank_setup_monitor.py/event_radar.py/rug_radar.py/bot.py-меню/level_watch.py
и т.п.) -- честно, отдельная, более крупная задача, не сделана в этом файле
(см. PROGRESS.md находки: живые упоминания "ICT" в `bot.py` welcome/меню
[исправлено этой же сессией] + `glossary.py` killzone-термин [ЖИВОЙ, не
исправлен] + `level_watch.py` source-поле из `watch_zones.json` [ЖИВОЙ,
самая серьёзная находка -- реальное имя аналитика в подписчицком алерте] --
все три найдены repo-wide grep'ом отдельно от этого теста)."""
import re

import card_v2 as cv

FORBIDDEN_TOKENS = re.compile(
    r"\b(kira|ict|королев|korolev|соболев|sobolev|2trade|pixel|заговор|zagovor|"
    r"вероятност\w*)\b",
    re.IGNORECASE,
)


def _assert_clean(label: str, text: str):
    m = FORBIDDEN_TOKENS.search(text)
    assert not m, f"{label}: запрещённый токен источника {m.group(0)!r} в подписчицком тексте: {text!r}"


def test_format_strength_block_clean():
    lines = cv.format_strength_block(72, [
        ("Тренд старшего ТФ (1D) совпадает с направлением", True),
        ("Свежий свип ликвидности в пользу направления", False),
        ("Цена у зоны интереса (не в вакууме)", True),
        ("Killzone активна или близко", True),
        ("Funding не против позиции", True),
        ("R:R по структуре >= 1:1.5", False),
    ])
    _assert_clean("format_strength_block", "\n".join(lines))


def test_format_what_to_do_clean():
    r = cv.format_what_to_do(
        direction="LONG", entries=[(100.0, 50), (98.0, 30), (96.0, 20)],
        sl=94.0, sl_risk_pct=6.0,
        tps=[{"price": 104.0, "sell_pct": 40, "stop_note": "б/у"},
             {"price": 108.0, "sell_pct": 30, "stop_note": ""},
             {"price": 112.0, "sell_pct": 30, "stop_note": ""}],
        deposit_1000={1: {"risk_usd": 10}, 2: {"risk_usd": 20}, 3: {"risk_usd": 30}},
        invalidation_note="закрытие 4H ниже 94.0",
        valid_until_note="до конца текущей killzone",
    )
    _assert_clean("format_what_to_do", "\n".join(r["all_lines"]))


def test_format_pros_cons_clean():
    factors = [
        ("EMA-стек 4H: бычий", 8), ("Свип ликвидности за", 6),
        ("Чеклист 5/6", 10), ("Фаза рынка: Накопление", 8),
        ("SMC-сетап: BOS", 8), ("OI-матрица: новые лонги", 6),
        ("Funding: небольшой перевес в лонги", 0),
        ("L/S 1.10: сбалансирован", 0),
        ("RSI-дивергенция классическая против направления", -6),
    ]
    lines = cv.format_pros_cons(factors)
    _assert_clean("format_pros_cons", "\n".join(lines))


def test_format_context_clean():
    lines = cv.format_context(
        higher_tf_trend="1D бычий", btc_label="BTC нейтрален",
        events_lines=["разлок токена через 3д"],
        rug_line="🛑 RUG-RADAR: риск 42/100",
        liq_lines=["Ликвидации 1.2x выше по зоне 102-104"],
    )
    _assert_clean("format_context", "\n".join(lines))


def test_format_capital_block_clean():
    table = cv.compute_capital_table(100.0, 94.0)
    lines = cv.format_capital_block(table, zone_capacity_usd=50000)
    _assert_clean("format_capital_block", "\n".join(lines))


def test_format_timing_clean():
    lines = cv.format_timing(killzone_active=True, killzone_name="London",
                              next_killzone_name="NY", next_killzone_in_min=None,
                              distance_to_zone_pct=0.5)
    _assert_clean("format_timing", "\n".join(lines))


def test_format_operator_risk_block_clean():
    lines = cv.format_operator_risk_block(
        oi_text="Цена↑ OI↑ — новые лонги, тренд подтверждён объёмом позиций",
        funding_label="небольшой перевес в лонги", ls_ratio=1.1,
        ls_text="L/S сбалансирован",
    )
    _assert_clean("format_operator_risk_block", "\n".join(lines))


def test_format_why_line_clean():
    line = cv.format_why_line("Действовать", top_factor_label="Чеклист 5/6")
    _assert_clean("format_why_line", line)
    line2 = cv.format_why_line("Наблюдение")
    _assert_clean("format_why_line (без top-фактора)", line2)


def test_assemble_card_v2_clean():
    strength = cv.format_strength_block(72, [("Тренд старшего ТФ (1D) совпадает с направлением", True)])
    what = cv.format_what_to_do(
        direction="LONG", entries=[(100.0, 100)], sl=94.0, sl_risk_pct=6.0,
        tps=[{"price": 104.0, "sell_pct": 100, "stop_note": ""}],
        deposit_1000={1: {"risk_usd": 10}}, invalidation_note="н/д", valid_until_note="н/д",
    )
    pros_cons = cv.format_pros_cons([("Чеклист 5/6", 10)])
    context = cv.format_context("1D бычий", "BTC нейтрален", [], "", [])
    capital = cv.format_capital_block(cv.compute_capital_table(100.0, 94.0))
    timing = cv.format_timing(True, "London", "NY", None, 0.5)
    text = cv.assemble_card_v2(cv.TRAFFIC_ENTRY_ACTUAL, "BTCUSDT", "LONG",
                                strength, what["all_lines"], pros_cons, context, capital, timing)
    _assert_clean("assemble_card_v2", text)


def test_assemble_card_v2_canon_clean():
    what = cv.format_what_to_do(
        direction="LONG", entries=[(100.0, 100)], sl=94.0, sl_risk_pct=6.0,
        tps=[{"price": 104.0, "sell_pct": 100, "stop_note": ""}],
        deposit_1000={1: {"risk_usd": 10}}, invalidation_note="н/д", valid_until_note="н/д",
    )
    op_risk = cv.format_operator_risk_block(
        oi_text="Цена↑ OI↑ — новые лонги, тренд подтверждён объёмом позиций",
        funding_label="небольшой перевес в лонги", ls_ratio=1.1, ls_text="L/S сбалансирован",
    )
    checklist = cv.format_strength_block(72, [("Тренд старшего ТФ (1D) совпадает с направлением", True)])[1:]
    why = cv.format_why_line("Действовать", top_factor_label="Чеклист 5/6")
    text = cv.assemble_card_v2_canon(
        cv.TRAFFIC_ENTRY_ACTUAL, "BTCUSDT", "LONG", phase_label="Накопление",
        pd_label="Discount", what_to_do_lines=what["all_lines"],
        operator_risk_lines=op_risk, checklist_lines=checklist, why_line=why,
    )
    _assert_clean("assemble_card_v2_canon", text)
