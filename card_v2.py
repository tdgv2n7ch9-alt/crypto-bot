"""
card_v2.py -- «Понятная карточка v2» (Пакет 13, Меню v2, спецификация владельца
2026-07-13, 10 блоков). ЧИСТЫЕ функции форматирования поверх УЖЕ ВЫЧИСЛЕННЫХ
данных сигнала -- никакая сигнальная логика/гейты/формулы здесь НЕ пересчитываются
и не меняются (см. CLAUDE.md "железные границы": "торговую/сигнальную логику,
пороги и формулы... не менять без явного одобрения владельца" -- этот модуль
только ПЕРЕСТАВЛЯЕТ уже посчитанное, тот же принцип, что rug_radar.py/
level_watch.py: dependency injection, вызывающая сторона (bot.py) уже добыла
данные, здесь их разбор и не более).

Источники данных, которые сюда ПОДАЮТСЯ готовыми (см. разведку Пакета 13,
PROGRESS.md):
  - чек-лист K-LVL/ICT: fa_engine._checklist() -> result["block5_checklist"]
    {"items": [(name, bool), ...], "score": int 0-6}
  - Rocket Score: fa_engine -> result["block12_rocket"]
    {"score": int 0-100, "factors": [(label, delta), ...]}  -- ИСТОЧНИК
    "силы сетапа" и ЗА/ПРОТИВ (delta>0 -- за, delta<0 -- против)
  - позиционный сайзинг: арифметика поверх risk_usd = deposit*pct/100 (та же
    формула, что bot.calc_position_size(), здесь только таблица по нескольким
    депозитам -- нового гейта/формулы нет, чистое умножение)
  - ликвидационные кластеры: level_watch.format_liquidation_cluster_line()
  - killzone: bot.get_killzone_status()
  - BTC-контекст: bot.get_btc_market_context()
  - rug-риск: rug_radar.format_rug_risk_line()
  - молодая монета: new_coin_scan.format_young_coin_flag()
  - события: event_radar.read_recent_events(), отфильтрованные по символу

«Ёмкость зоны» (капитал, п.6 спецификации) требует данных биржевого стакана
(глубина книги), которых НЕТ в готовом виде как чистая функция без сети/live-
состояния (см. разведку -- whale_radar держит книгу в памяти живого процесса,
не как переиспользуемая чистая функция). Честно "н/д" всегда в этой версии --
параметр `zone_capacity_usd=None` -- то же самое допущение, что явно
разрешено спецификацией владельца ("или честно «н/д»").
"""

SEP = "━━━━━━━━━━━━━━━━━━━━"
MAX_SIGNAL_LINE_CHARS = 36  # спецификация п.10 -- сигнальная часть под iPhone-экран

TRAFFIC_ENTRY_ACTUAL = "🟢 ВХОД АКТУАЛЕН"
TRAFFIC_WAIT_PRICE = "🟡 ЖДЁМ ЦЕНУ"
TRAFFIC_DO_NOT_ENTER = "🔴 НЕ ВХОДИТЬ"

VERDICT_ACT = "act"
VERDICT_REDUCED = "reduced"
VERDICT_WATCH_CLOSE = "watch_close"
VERDICT_OBSERVE = "observe"

GENERIC_CONS_POOL = [
    "Рынок может развернуться на новостях/ликвидациях без предупреждения",
    "R:R описывает потенциал, не гарантирует исполнение по заявленным ценам "
    "(проскальзывание, тонкая ликвидность вдали от топ-бирж)",
    "Уровень актуален на момент анализа -- может устареть при быстром движении цены",
]

DEPOSITS_USD = (1000, 10000, 100000, 1000000)
RISK_PCTS = (1, 2, 3)


# ── Блок 1: светофор ─────────────────────────────────────────────────────

def compute_traffic_light(price: float, zone_lo: float, zone_hi: float,
                           invalidated: bool = False) -> str:
    """`invalidated` -- решает вызывающая сторона (например, уже пробит SL
    или `trade["rr_gate_pass"] is False`) -- эта функция гейт не пересчитывает,
    только решает, какую из 3 меток показать."""
    if invalidated:
        return TRAFFIC_DO_NOT_ENTER
    lo, hi = min(zone_lo, zone_hi), max(zone_lo, zone_hi)
    if lo <= price <= hi:
        return TRAFFIC_ENTRY_ACTUAL
    return TRAFFIC_WAIT_PRICE


# ── Блок 2: сила сетапа + вердикт ────────────────────────────────────────

def compute_verdict(score: int, missing_confirmation: str = None) -> dict:
    """score -- Rocket Score 0-100 (fa_engine, УЖЕ посчитан). Пороги и метки --
    ровно по спецификации владельца, не изобретены здесь."""
    if score >= 80:
        return {"tier": VERDICT_ACT, "label": "Действовать", "detail": None}
    if score >= 60:
        return {"tier": VERDICT_REDUCED, "label": "Уменьшенный объём", "detail": None}
    if score >= 40:
        detail = missing_confirmation or "ждём дополнительное подтверждение"
        return {"tier": VERDICT_WATCH_CLOSE, "label": "Присмотреться", "detail": detail}
    return {"tier": VERDICT_OBSERVE, "label": "Наблюдение", "detail": None}


def format_strength_block(score: int, checklist_items: list, missing_confirmation: str = None) -> list:
    """checklist_items: [(name: str, ok: bool), ...] -- fa_engine block5, 6 пунктов."""
    verdict = compute_verdict(score, missing_confirmation)
    lines = [f"💪 *Сила сетапа: {score}/100* -- {verdict['label']}"]
    if verdict["detail"]:
        lines.append(f"  ({verdict['detail']})")
    for name, ok in checklist_items:
        lines.append(f"  {'✅' if ok else '❌'} {name}")
    return lines


# ── Блок 3: ЧТО ДЕЛАТЬ ────────────────────────────────────────────────────

def default_price_fmt(v: float) -> str:
    """Магнитуда-зависимое форматирование цены -- честная находка при сборке
    приёмочного мокапа Меню v2 (2026-07-13): фиксированный `.0f` округлял
    микрокапы вида $0.0120 (обычная цена x100-кандидата) до "0". Пороги --
    тот же принцип, что общепринятое отображение цены крипто-активов
    (BTC-масштаб -- целые, альты -- 2 знака, микрокапы -- 4-6 знаков)."""
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.6f}"


def format_what_to_do(direction: str, entries: list, sl: float, sl_risk_pct: float,
                       tps: list, deposit_1000: dict, invalidation_note: str,
                       valid_until_note: str, price_fmt=default_price_fmt) -> dict:
    """entries: [(price, pct), ...] по спецификации 50/30/20 (тот же принцип
    DCA-разбивки, что уже используется в x100-карточке, entry1/2/3).
    tps: [{"price": float, "sell_pct": int, "stop_note": str}, ...] -- stop_note
    короткий (напр. "б/у" -- см. glossary.annotate("безубыток") для расшифровки
    при первом употреблении в карточке, не здесь).
    deposit_1000: {1: {"risk_usd":...}, 2: {...}, 3: {...}} -- депозит $1000
    при риске 1/2/3% (см. compute_capital_table).

    Возвращает {"signal_lines": [...], "prose_lines": [...]} -- РАЗДЕЛЬНО:
    signal_lines -- структурные строки (цены/проценты/суммы), под лимит
    MAX_SIGNAL_LINE_CHARS по спецификации; prose_lines -- свободный текст
    (условие отмены/срок действия), естественно длиннее предложения, лимит
    строк на них спецификацией не подразумевается (это не "сигнальная часть",
    а пояснение)."""
    signal_lines = ["📋 *ЧТО ДЕЛАТЬ*", ""]
    for i, (price, pct) in enumerate(entries, 1):
        signal_lines.append(f"{i}️⃣ {pct}%: {price_fmt(price)}")
    signal_lines.append(f"🛑 SL 100%: {price_fmt(sl)} (риск {sl_risk_pct:.1f}%)")
    for pct in RISK_PCTS:
        d = deposit_1000.get(pct, {})
        risk_usd = d.get("risk_usd")
        if risk_usd is not None:
            signal_lines.append(f"  $1000 @{pct}%: ${risk_usd:,.0f}")
    signal_lines.append("")
    for i, tp in enumerate(tps, 1):
        note = f" {tp['stop_note']}" if tp.get("stop_note") else ""
        signal_lines.append(f"🎯 TP{i} {price_fmt(tp['price'])}: {tp['sell_pct']}%{note}")

    prose_lines = [
        f"❌ НЕ входить если: {invalidation_note}",
        f"⏳ Актуально: {valid_until_note}",
    ]
    return {"signal_lines": signal_lines, "prose_lines": prose_lines,
            "all_lines": signal_lines + [""] + prose_lines}


# ── Блок 4: ЗА / ПРОТИВ ───────────────────────────────────────────────────

def split_pros_cons(factors: list, min_cons: int = 2, max_pros: int = 5) -> dict:
    """factors: [(label, delta), ...] -- Rocket Score factors (fa_engine, УЖЕ
    посчитаны). delta>0 -- за, delta<0 -- против. Спецификация владельца
    требует МИНИМУМ 2 «против» на карточке всегда -- если реальных
    отрицательных факторов меньше, честно добавляются структурные риск-оговорки
    из GENERIC_CONS_POOL (универсально верные, не выдуманные под конкретный
    сетап) до набора минимума."""
    pros = sorted([f for f in factors if f[1] > 0], key=lambda x: -x[1])[:max_pros]
    cons = sorted([f for f in factors if f[1] < 0], key=lambda x: x[1])
    cons_labels = [c[0] for c in cons]
    i = 0
    while len(cons_labels) < min_cons and i < len(GENERIC_CONS_POOL):
        if GENERIC_CONS_POOL[i] not in cons_labels:
            cons_labels.append(GENERIC_CONS_POOL[i])
        i += 1
    return {"pros": [p[0] for p in pros], "cons": cons_labels}


def format_pros_cons(factors: list, min_cons: int = 2, max_pros: int = 5) -> list:
    split = split_pros_cons(factors, min_cons=min_cons, max_pros=max_pros)
    lines = ["⚖️ *ЗА / ПРОТИВ*", ""]
    for p in split["pros"]:
        lines.append(f"✅ {p}")
    for c in split["cons"]:
        lines.append(f"⚠️ {c}")
    return lines


# ── Блок 5: КОНТЕКСТ ──────────────────────────────────────────────────────

def format_context(higher_tf_trend: str, btc_label: str, events_lines: list,
                    rug_line: str, liq_lines: list) -> list:
    """higher_tf_trend -- строка от вызывающей стороны (например, "1D бычий").
    btc_label -- bot.get_btc_market_context()["label"] (УЖЕ посчитан).
    events_lines -- отфильтрованные по символу строки из event_radar (<=7 дней).
    rug_line -- rug_radar.format_rug_risk_line() (может быть "").
    liq_lines -- level_watch.format_liquidation_cluster_line() на зону
    entry->TP (может быть список "н/д"-строк -- честно, не пропускается)."""
    lines = [f"🧭 *КОНТЕКСТ*", "", f"  Старший ТФ: {higher_tf_trend}",
             f"  BTC-фон: {btc_label}"]
    if events_lines:
        lines.append("  События (<=7д): " + "; ".join(events_lines))
    if rug_line:
        lines.append(f"  {rug_line}")
    for l in liq_lines:
        lines.append(f"  {l}")
    return lines


# ── Блок 6: КАПИТАЛ ───────────────────────────────────────────────────────

def compute_capital_table(price: float, sl: float) -> dict:
    """Чистая арифметика (risk_usd = deposit*pct/100, position_usd = risk_usd /
    sl_distance_pct) -- ТА ЖЕ формула, что bot.calc_position_size(), просто
    сведена в таблицу по нескольким депозитам сразу (новой формулы/гейта нет).
    Возвращает {deposit: {pct: {"risk_usd", "position_usd"}}}."""
    sl_distance_pct = abs(price - sl) / price * 100 if price else 0
    table = {}
    for dep in DEPOSITS_USD:
        table[dep] = {}
        for pct in RISK_PCTS:
            risk_usd = dep * pct / 100
            position_usd = (risk_usd / (sl_distance_pct / 100)) if sl_distance_pct else None
            table[dep][pct] = {"risk_usd": risk_usd, "position_usd": position_usd}
    return table


def format_capital_block(capital_table: dict, zone_capacity_usd: float = None) -> list:
    lines = ["💰 *КАПИТАЛ*", ""]
    for dep in DEPOSITS_USD:
        row = capital_table.get(dep, {})
        parts = []
        for pct in RISK_PCTS:
            r = row.get(pct, {})
            risk_usd = r.get("risk_usd")
            if risk_usd is not None:
                parts.append(f"{pct}%: ${risk_usd:,.0f}")
        dep_label = f"${dep:,}" if dep < 1_000_000 else f"${dep // 1_000_000}M"
        lines.append(f"  {dep_label}: " + " · ".join(parts))
    if zone_capacity_usd is not None:
        lines.append(f"  Ёмкость зоны: ~${zone_capacity_usd:,.0f} без сдвига >0.3%")
    else:
        lines.append("  Ёмкость зоны: н/д")
    return lines


# ── Блок 7: ТАЙМИНГ ───────────────────────────────────────────────────────

def format_timing(killzone_active: bool, killzone_name: str, next_killzone_name: str,
                   next_killzone_in_min: int, distance_to_zone_pct: float) -> list:
    lines = ["🕐 *ТАЙМИНГ*", ""]
    if killzone_active:
        lines.append(f"  Сейчас: killzone {killzone_name} активна")
    elif next_killzone_in_min is not None:
        lines.append(f"  Позже: следующая killzone {next_killzone_name} через {next_killzone_in_min} мин")
    else:
        lines.append("  Killzone: н/д")
    if distance_to_zone_pct is not None:
        lines.append(f"  До зоны: {distance_to_zone_pct:+.2f}%")
    return lines


# ── Блок 10 (частично): типографика ──────────────────────────────────────

def check_signal_line_lengths(lines: list, max_chars: int = MAX_SIGNAL_LINE_CHARS) -> list:
    """Возвращает индексы строк ДЛИННЕЕ max_chars -- используется в golden-тесте
    формата, не режет текст автоматически (обрезка вслепую может испортить
    цену/смысл -- лучше явный тест, который поймает регрессию до продакшена)."""
    return [i for i, l in enumerate(lines) if len(l) > max_chars]


def assemble_card_v2(traffic_light: str, symbol: str, direction: str,
                      strength_lines: list, what_to_do_lines: list,
                      pros_cons_lines: list, context_lines: list,
                      capital_lines: list, timing_lines: list) -> str:
    """Главная точка сборки -- склеивает блоки с пустой строкой между секциями
    и разделителем SEP, спецификация п.10."""
    sections = [
        [f"{traffic_light} -- {symbol} {direction}"],
        strength_lines,
        what_to_do_lines,
        pros_cons_lines,
        context_lines,
        capital_lines,
        timing_lines,
    ]
    out = []
    for i, sec in enumerate(sections):
        if i > 0:
            out.append(SEP)
        out.extend(sec)
    return "\n".join(out)
