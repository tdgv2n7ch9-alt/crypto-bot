"""
card_format.py -- единый модуль форматирования цен по тику (владелец, П-Визуал v2,
задача #207).

Найдено живьём при подготовке этого модуля: в коде уже есть ДВА разных
форматтера цены с РАЗНЫМИ порогами точности для одних и тех же диапазонов --
`bot.fp()` (>=1000 -> ",.2f", >=1 -> ".4f", >=0.01 -> ".5f", иначе ".8f") и
`card_v2.default_price_fmt()` (>=1000 -> ",.0f", >=1 -> ",.2f", >=0.01 -> ".4f",
иначе ".6f"). Одна и та же цена показывается РАЗНЫМИ числами на разных
карточках -- прямая иллюстрация того, почему нужен один канонический модуль
(задача #207), а не что оба существующих форматтера "сломаны" по отдельности.

Оба старых форматтера -- эвристика ПО МАГНИТУДЕ цены (округли по диапазону,
в котором лежит число), не по РЕАЛЬНОМУ тику биржи (`priceFilter.tickSize`
инструмента). Магнитудная эвристика -- источник того же КЛАССА бага, что
нашли на ZEC-карточке (задача #211, TP2/TP3 из ta_extra) -- родственная, но
ОТДЕЛЬНАЯ проблема (там дело в расчёте целей, не в форматировании; здесь --
чисто display: магнитудная эвристика может дать МЕНЬШЕ знаков, чем требует
реальный тик конкретной монеты, теряя значащие цифры видимо для человека).

Канонический путь: `format_price(value, symbol=None)` -- если `symbol`
передан и тик реально известен (`get_tick_size()`, живой Bybit
instruments-info, кэш 24ч) -- число знаков берётся из тика; иначе честный
fallback на магнитудную эвристику (компромисс между двумя старыми
функциями, не изобретает новую точность произвольно).

Это ТОЛЬКО модуль форматирования (задача #207) -- единый шаблон карточки
(5 блоков, задача #208) и подключение во все карточки (задача #209) --
следующие отдельные шаги, здесь НЕ выполняются.
"""
import logging
import time

import requests

import ta_extra

log = logging.getLogger(__name__)

BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
REQUEST_TIMEOUT_SEC = 10
TICK_CACHE_TTL_SEC = 24 * 3600  # тик-сайз инструмента меняется исключительно
# редко -- избегаем лишнего сетевого вызова на каждый рендер карточки

_TICK_CACHE = {}  # symbol -> (tick_size: float, fetched_ts: float)


def _decimals_from_tick(tick_size: float) -> int:
    """0.10 -> 1, 0.000001 -> 6, 1.0/25.0 -> 0 (тик >=1 не подразумевает
    дробных цен, даже если формально имеет вид "1.0")."""
    if tick_size is None or tick_size <= 0:
        return 2
    s = f"{tick_size:.10f}".rstrip("0")
    if "." not in s:
        return 0
    return max(0, len(s.split(".")[1]))


def fetch_tick_size(symbol: str) -> float:
    """Живой запрос к Bybit instruments-info (linear). Честно возвращает None
    при отказе/отсутствии символа -- вызывающий код обязан фоллбэкнуться на
    магнитудную эвристику, НЕ выдумывать тик."""
    try:
        r = requests.get(BYBIT_INSTRUMENTS_URL, params={
            "category": "linear", "symbol": symbol,
        }, timeout=REQUEST_TIMEOUT_SEC)
        d = r.json()
        lst = d.get("result", {}).get("list", [])
        if not lst:
            return None
        tick = lst[0].get("priceFilter", {}).get("tickSize")
        return float(tick) if tick else None
    except Exception as e:
        log.info(f"card_format: fetch_tick_size({symbol}) failed: {e}")
        return None


def get_tick_size(symbol: str, fetch_fn=fetch_tick_size) -> float:
    """Кэш TICK_CACHE_TTL_SEC поверх fetch_tick_size(). `fetch_fn` -- для
    тестов (без реального сетевого вызова)."""
    cached = _TICK_CACHE.get(symbol)
    if cached and time.time() - cached[1] < TICK_CACHE_TTL_SEC:
        return cached[0]
    tick = fetch_fn(symbol)
    if tick is not None:
        _TICK_CACHE[symbol] = (tick, time.time())
    return tick


def _magnitude_decimals(value: float) -> int:
    """Честный fallback, когда тик недоступен (символ н/д на Bybit linear,
    сетевой отказ, символ не передан). Компромисс между bot.fp() и
    card_v2.default_price_fmt() -- не более и не менее точный, чем нужно,
    чтобы микрокапы (например $0.0120) не округлялись до "0" (та же находка,
    что уже зафиксирована в card_v2.default_price_fmt() докстринге)."""
    v = abs(value)
    if v >= 1000:
        return 2
    if v >= 1:
        return 4
    if v >= 0.01:
        return 5
    return 8


def format_price(value: float, symbol: str = None, tick_size: float = None,
                  get_tick_size_fn=get_tick_size) -> str:
    """Канонический форматтер цены -- единственная точка правды для всех
    карточек (после задачи #209). Приоритет источника точности:
    1. явно переданный `tick_size` (вызывающая сторона уже знает тик);
    2. `get_tick_size_fn(symbol)`, если передан `symbol`;
    3. честная магнитудная эвристика (`_magnitude_decimals`).

    Тысячи -- через запятую всегда (существующая конвенция обоих старых
    форматтеров, сохранена для визуальной совместимости)."""
    decimals = None
    if tick_size is not None:
        decimals = _decimals_from_tick(tick_size)
    elif symbol is not None:
        t = get_tick_size_fn(symbol)
        if t is not None:
            decimals = _decimals_from_tick(t)
    if decimals is None:
        decimals = _magnitude_decimals(value)
    return f"{value:,.{decimals}f}"


# ── Единый шаблон 5 блоков (владелец, П-Визуал v2, задача #208, точная
# спецификация 2026-07-16) ──────────────────────────────────────────────
#
# ЧИСТЫЕ функции форматирования поверх УЖЕ ВЫЧИСЛЕННЫХ данных сигнала --
# та же дисциплина, что card_v2.py (см. его докстринг): сигнальная
# логика/гейты/формулы (R:R, чек-лист, риск-скоринг, обоснование SL) сюда
# НЕ передаются как задача этого модуля -- вызывающая сторона (bot.py) уже
# посчитала их (ta_extra.build_trade_from_structure(), fa_engine._checklist()
# и т.п.), здесь только сборка текста карточки. Разделитель между блоками --
# SEP (короче, чем card_v2.SEP -- отдельная спецификация владельца для
# этого шаблона, не путать с card_v2.py).

SEP = "━━━━━"
DIRECTION_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}
DCA_SPLIT_PCT = (50, 30, 20)  # спецификация -- лимитки DCA 50/30/20
DEPOSIT_RISK_PCTS = (1, 2, 3)  # риск-таблица 1/2/3% депозита
CHECKLIST_MAX = 6  # Kira|ICT чеклист, fa_engine._checklist() -- X/6, не выдумано здесь
RR_GATE = ta_extra.SR_MIN_RR_TP1  # 1.5 -- существующий гейт, единственный источник правды


def compute_avg_entry(entries: list) -> float:
    """`entries`: [(price, pct), ...] (обычно DCA 50/30/20) -- средневзвешенная
    цена входа. Один элемент -- средняя равна самой этой цене (триггерный
    вход)."""
    if len(entries) == 1:
        return entries[0][0]
    total_pct = sum(pct for _, pct in entries)
    return sum(price * pct for price, pct in entries) / total_pct


def format_header_block(direction: str, symbol: str, timeframe: str,
                         rocket_score: int = None, setup_type: str = None) -> list:
    """Блок 1 -- ЗАГОЛОВОК. 1-2 строки: направление+тикер+ТФ(+Rocket Score),
    и опционально тип сетапа отдельной строкой (AMD/Sweep/CHoCH-ретест/
    зона автора/скальп и т.п. -- сам текст типа передаётся вызывающей
    стороной, здесь не классифицируется)."""
    emoji = DIRECTION_EMOJI.get(direction.upper(), "⚪")
    line1 = f"{emoji} {direction.upper()} {symbol} | {timeframe}"
    if rocket_score is not None:
        line1 += f" | 🚀 {rocket_score}/100"
    lines = [line1]
    if setup_type:
        lines.append(setup_type)
    return lines


def format_entry_block(entries: list, price_fmt=format_price,
                        single_condition_note: str = None) -> list:
    """Блок 2 -- 📍 ВХОД. `entries`: [(price, pct), ...] для DCA (обычно 3
    лимитки 50/30/20) ИЛИ список из ОДНОЙ пары `[(price, 100)]` для
    триггерного входа -- тогда `single_condition_note` (например "после
    закрепа ниже X") добавляется пометкой к единственной строке."""
    lines = ["📍 ВХОД"]
    if len(entries) == 1:
        price = entries[0][0]
        note = f" -- {single_condition_note}" if single_condition_note else ""
        lines.append(f"{price_fmt(price)}{note}")
        return lines
    avg = compute_avg_entry(entries)
    for price, pct in entries:
        lines.append(f"{pct}%: {price_fmt(price)}")
    lines.append(f"Средняя: {price_fmt(avg)}")
    return lines


def format_sl_block(sl_price: float, avg_entry: float, reason: str,
                     price_fmt=format_price) -> list:
    """Блок 3 -- 🛑 SL. `reason` -- готовая короткая фраза от вызывающей
    стороны ("за структурой", "за хаем +4%" и т.п.) -- эта функция её не
    придумывает, только вставляет."""
    risk_pct = abs(sl_price - avg_entry) / avg_entry * 100 if avg_entry else 0.0
    lines = [f"🛑 SL: {price_fmt(sl_price)} ({risk_pct:.1f}% от входа)"]
    if reason:
        lines.append(f"  {reason}")
    return lines


def format_targets_block(tps: list, avg_entry: float, price_fmt=format_price,
                          rr_gate: float = RR_GATE) -> list:
    """Блок 4 -- 🎯 ЦЕЛИ. `tps`: [{"price": float, "rr": float}, ...] -- R:R
    КАЖДОЙ цели уже посчитан вызывающей стороной (`ta_extra`/`bot.py` --
    `abs(tp-price)/risk`, единственная формула, не дублируется здесь).
    Список ЗАЩИТНО пересортирован по возрастанию удалённости от входа
    (находка #211 -- "мягкая защита на уровне рендера": TP-порядок в
    карточке всегда по возрастанию, даже если апстрим когда-нибудь опять
    отдаст немонотонный список -- см. PROGRESS.md 2026-07-15). Нижняя
    строка -- минимальный R:R сделки с ⚠️, если он ниже `rr_gate`
    (по умолчанию `ta_extra.SR_MIN_RR_TP1` -- существующий боевой гейт,
    не новое число)."""
    ordered = sorted(tps, key=lambda t: abs(t["price"] - avg_entry))
    lines = ["🎯 ЦЕЛИ"]
    for i, t in enumerate(ordered, 1):
        pct = abs(t["price"] - avg_entry) / avg_entry * 100 if avg_entry else 0.0
        lines.append(f"TP{i}: {price_fmt(t['price'])} ({pct:+.1f}%) R:R 1:{t['rr']:.1f}")
    rr_min = min((t["rr"] for t in ordered), default=0.0)
    marker = "" if rr_min >= rr_gate else " ⚠️"
    lines.append(f"⚖️ R:R min: {rr_min:.1f}{marker}")
    return lines


def format_risk_block(risk_table: dict, leverage_note: str = None,
                       warning_markers: list = None) -> list:
    """Блок 5 -- 💰 РИСК. `risk_table`: {1: risk_usd, 2: risk_usd, 3: risk_usd}
    -- размер риска в $ на условный депозит при 1/2/3% (арифметика
    depozit*pct/100 -- та же категория чистого умножения, что
    card_v2.compute_capital_table(), вызывающая сторона уже посчитала на
    СВОЁМ условном депозите, здесь не выбирается депозит и не считается
    позиция). `warning_markers` -- готовые строки маркеров ("⚠️ МЕМКОИН",
    "🩸 ТОНКИЙ СТАКАН", "🔓 РАЗЛОК", Dead Zone и т.п.) -- источники этих
    маркеров (rug_radar.py, killzone-статус и т.д.) вне этого модуля."""
    lines = ["💰 РИСК"]
    for pct in DEPOSIT_RISK_PCTS:
        if pct in risk_table:
            lines.append(f"{pct}%: ${risk_table[pct]:,.2f}")
    if leverage_note:
        lines.append(leverage_note)
    for marker in (warning_markers or []):
        lines.append(marker)
    return lines


def format_scalp_line(scalp_score: int, max_score: int = 6) -> str:
    """Опциональная строка между Блоком 5 и футером -- печатается только
    если вызывающая сторона (scalp_evidence.py) реально прогнала гейт,
    сама печать/непечать НЕ решается здесь."""
    return f"⚡ Скальп {scalp_score}/{max_score}"


def format_footer(checklist_score: int, symbol: str, btc_context_line: str = None,
                   counter_trend: bool = False) -> list:
    """Футер -- НЕ отдельный блок (без SEP перед ним, спецификация
    владельца): Kira|ICT чеклист N/6 + строка BTC-контекста (если есть) +
    "⚠️ контртренд" (если сетап против HTF) + хэштег #ТИКЕР."""
    lines = [f"Kira|ICT чеклист {checklist_score}/{CHECKLIST_MAX}"]
    if btc_context_line:
        lines.append(btc_context_line)
    if counter_trend:
        lines.append("⚠️ контртренд")
    lines.append(f"#{symbol.upper()}")
    return lines


def assemble_card(header_lines: list, entry_lines: list, sl_lines: list,
                   targets_lines: list, risk_lines: list, footer_lines: list,
                   scalp_line: str = None) -> str:
    """Полная карточка -- 5 блоков через SEP, затем опциональная строка
    скальпа и футер СРАЗУ под Блоком 5 без разделителя (спецификация:
    футер -- "не блок")."""
    blocks = [header_lines, entry_lines, sl_lines, targets_lines, risk_lines]
    text = f"\n{SEP}\n".join("\n".join(b) for b in blocks)
    tail_lines = []
    if scalp_line:
        tail_lines.append(scalp_line)
    tail_lines.extend(footer_lines)
    if tail_lines:
        text += "\n" + "\n".join(tail_lines)
    return text


def assemble_compact_card(header_lines: list, avg_entry: float, sl_price: float,
                           tp1_price: float, price_fmt=format_price) -> str:
    """Компакт-версия -- Блок 1 + средняя входа + SL + TP1 (спецификация).
    Кнопка [▾ Раскрыть] -- реальная Telegram inline-кнопка, собирается
    вызывающей стороной (bot.py, InlineKeyboardMarkup) -- этот модуль
    отдаёт только текст, без разметки кнопок."""
    lines = list(header_lines)
    lines.append(f"Вход: {price_fmt(avg_entry)}  SL: {price_fmt(sl_price)}  "
                 f"TP1: {price_fmt(tp1_price)}")
    return "\n".join(lines)
