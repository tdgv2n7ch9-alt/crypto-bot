"""
BEST TRADE — «Полный анализ» (fa_engine): движок 13-блочного разбора монеты для
/full SYMBOL и кнопки «Полный анализ».

Архитектура: этот модуль НЕ импортирует bot.py на верхнем уровне (bot.py импортирует
fa_engine, а не наоборот — top-level `import bot` тут создал бы циклический импорт).
Доступ к данным bot.py (кэшированные HTTP-обёртки поверх CoinGecko/Bybit, общий
rate-limiter, get_all_coins()) идёт через отложенный `import bot` ВНУТРИ функций — к
моменту первого вызова build_full_analysis() модуль bot уже полностью загружен, так
что это безопасно и не плодит собственный кэш/лимитер параллельно с bot.py.

Вся содержательная логика (свинг-структура, EMA-стек, свипы, S/R-зоны, Elliott,
Wyckoff, FVG, equal highs/lows) — в ta_extra.py как чистые функции над уже
полученными свечами; этот модуль их оркеструет и рендерит карточку.

Бюджет HTTP: 1h/4h/1d свечи символа (3 вызова) + 1d свечи BTC для сравнения фазы,
если symbol != BTC (1 вызов) + funding/OI (1 общий кэшированный вызов на все
символы, TTL 90с) = обычно 4-5 обращений к CoinGecko на анализ, укладывается в
бюджет ≤8-10 из ТЗ. L/S ratio — отдельный вызов к Bybit, не к CoinGecko.
"""

import time

import ta_extra
import live_prices
import signal_journal

CHECKLIST_MIN_FOR_TRADE = 4          # чеклист X/6 >= этого числа допускает план сделки
POI_PROXIMITY_PCT = 1.5              # "цена у POI" — порог по расстоянию в %
MEME_RANK_THRESHOLD = 200
MEME_MCAP_THRESHOLD = 100_000_000
MEME_VOL_THRESHOLD = 10_000_000


def _safe(label, fn, *args, **kwargs):
    """Обёртка блока: ошибка -> {"ok": False, "error": "<label>: нет данных"},
    вместо падения всего анализа. Требование ТЗ: каждый блок в try/except."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"{label}: нет данных ({e})"}


def _resolve_coin(symbol: str, bot):
    coins = bot.get_all_coins()
    coin = next((c for c in coins if c["symbol"] == symbol), None)
    if coin:
        return coin
    # Синтетический coin для символов вне топ-листа CMC (тот же паттерн, что
    # bot.py:_do_full_analysis использует как финальный фоллбек) — без async
    # _search_coin_by_symbol, т.к. build_full_analysis синхронная функция.
    test = bot.get_binance_ohlc(symbol, "4h", 5)
    price_now = test[-1]["close"] if test else 0.0
    return {
        "symbol": symbol, "slug": symbol.lower(), "cmc_rank": 9999,
        "tags": [], "name": symbol,
        "quote": {"USDT": {
            "price": price_now, "volume_24h": 0, "market_cap": 0,
            "percent_change_1h": 0, "percent_change_24h": 0,
            "percent_change_7d": 0, "percent_change_30d": 0, "percent_change_90d": 0,
        }}
    }


def build_full_analysis(symbol: str, coin: dict = None) -> dict:
    """Строит структурированный результат по 13 блокам ТЗ. symbol без USDT-суффикса.
    coin: опционально уже резолвленный dict (rank/mcap/vol/% изменения) — если не
    передан, резолвится внутри через bot.get_all_coins() + синтетический фоллбек."""
    import bot  # отложенный импорт — см. докстринг модуля

    symbol = symbol.upper().replace("USDT", "").replace("BUSD", "")
    result = {"symbol": symbol, "ok": False, "ts": time.time()}

    if coin is None:
        coin = _resolve_coin(symbol, bot)
    q = coin["quote"]["USDT"]
    rank = coin.get("cmc_rank", 9999)
    mcap = q.get("market_cap", 0) or 0
    vol24 = q.get("volume_24h", 0) or 0
    ch1h = q.get("percent_change_1h", 0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d = q.get("percent_change_7d", 0) or 0

    # ── единый набор свечей на весь анализ (см. бюджет вызовов в докстринге) ──
    c1h = bot.get_binance_ohlc(symbol, "1h", 100) or []
    c4h = bot.get_binance_ohlc(symbol, "4h", 200) or []
    c1d = bot.get_binance_ohlc(symbol, "1d", 365) or []

    if not c4h or len(c4h) < 20:
        result["error"] = "нет данных по свечам (CoinGecko недоступен или лимит запросов)"
        return result

    closes_1d = [c["close"] for c in c1d] if c1d else [c["close"] for c in c4h]
    fallback_close = c4h[-1]["close"]
    price, price_fresh = live_prices.resolve_price(symbol, fallback_close)
    result.update(ok=True, price=price, price_fresh=price_fresh, rank=rank,
                  mcap=mcap, vol24=vol24, ch1h=ch1h, ch24h=ch24h, ch7d=ch7d)

    ema_ctx = ta_extra.ema_context(c1h, c4h)
    sweep_1h = ta_extra.detect_sweep(c1h)
    sweep_4h = ta_extra.detect_sweep(c4h)
    zones = ta_extra.find_sr_zones(c1h, c4h, c1d, price, ema_ctx=ema_ctx)
    rsi_1d = ta_extra.rsi(closes_1d, 14)
    rsi_4h = ta_extra.rsi([c["close"] for c in c4h], 14)

    # ── Блок 1: Мульти-ТФ bias ──
    b1 = _safe("Блок 1 (Multi-TF bias)", ta_extra.multi_tf_bias, c1d, c4h, c1h)
    bias = b1.get("bias", "NEUTRAL")
    direction = "long" if bias == "LONG" else ("short" if bias == "SHORT" else None)
    result["block1_bias"] = b1

    # ── Блок 2: Elliott Wave ──
    b2 = _safe("Блок 2 (Elliott)", ta_extra.elliott_wave_heuristic, closes_1d, rsi_1d)
    result["block2_elliott"] = b2

    # ── Блок 3: SMC-сетап ──
    b3 = _safe("Блок 3 (SMC setup)", ta_extra.smc_setup_type, c4h)
    result["block3_smc"] = b3

    # ── Блок 4: POI ──
    def _poi():
        poi = []
        for side in ("below", "above"):
            for z in zones.get(side, [])[:4]:
                poi.append({
                    "side": side, "price": z["mid"],
                    "distance_pct": round((z["mid"] - price) / price * 100, 2),
                    "touches": z["touches"], "sources": z["sources"],
                })
        fvg = ta_extra.find_fvg_zones(c4h, price)
        for f in fvg[:6]:
            poi.append({
                "side": "above" if f["mid"] > price else "below", "price": f["mid"],
                "distance_pct": f["distance_pct"], "touches": 0, "sources": ["fvg " + f["type"]],
            })
        poi.sort(key=lambda p: abs(p["distance_pct"]))
        return {"ok": True, "poi": poi}
    b4 = _safe("Блок 4 (POI)", _poi)
    result["block4_poi"] = b4

    # ── Блок 6 нужен раньше блока 5 (свип/POI используются в чеклисте) ──
    def _liquidity():
        eq = ta_extra.equal_levels(c4h, tolerance_pct=0.3)
        sweep_line = ta_extra.format_sweep_line(sweep_1h, sweep_4h, price_fmt=lambda v: f"{v:.6g}")
        return {"ok": True, "equal_levels": eq, "sweep_line": sweep_line}
    b6 = _safe("Блок 6 (Ликвидность/ловушки)", _liquidity)
    result["block6_liquidity"] = b6

    # ── Блок 5: чеклист Kira/ICT (X/6) ──
    def _checklist():
        items = []

        # 1. тренд старшего ТФ совпадает с направлением
        struct_ok = False
        if direction == "long":
            struct_ok = "аптренд" in b1.get("structure_1d", "")
        elif direction == "short":
            struct_ok = "даунтренд" in b1.get("structure_1d", "")
        items.append(("Тренд старшего ТФ (1D) совпадает с направлением", struct_ok))

        # 2. свежий свип ликвидности
        fresh_sweep = False
        if direction:
            fresh_sweep = ta_extra.sweep_score_delta(sweep_1h, sweep_4h, direction) > 0
        items.append(("Свежий свип ликвидности в пользу направления", fresh_sweep))

        # 3. цена у POI
        poi_list = b4.get("poi", []) if b4.get("ok") else []
        near_poi = bool(poi_list) and abs(poi_list[0]["distance_pct"]) <= POI_PROXIMITY_PCT
        items.append(("Цена у зоны интереса (не в вакууме)", near_poi))

        # 4. killzone активна или близко
        kz = bot.get_killzone_status()
        kz_ok = kz["is_good"] or (kz.get("next") and kz["next"].get("in_min", 999) <= 60)
        items.append(("Killzone активна или близко", kz_ok))

        # 5. funding не против позиции
        funding = bot.get_funding_rate(symbol)
        funding_ok = True
        if funding.get("ok") and direction:
            rate = funding["rate"]
            funding_ok = not (rate > 0.05 if direction == "long" else rate < -0.05)
        items.append(("Funding не против позиции", funding_ok))

        # 6. R:R по структуре >= 1:1.5
        trade = None
        rr_ok = False
        if direction:
            trade = ta_extra.build_trade_from_structure(direction, price, zones)
            rr_ok = bool(trade and trade["rr_gate_pass"])
        items.append(("R:R по структуре ≥ 1:1.5", rr_ok))

        score = sum(1 for _, ok in items if ok)
        return {"ok": True, "items": items, "score": score, "trade": trade, "funding": funding}
    b5 = _safe("Блок 5 (Чеклист)", _checklist)
    result["block5_checklist"] = b5

    # ── Блок 7: OI-матрица + funding + L/S ──
    def _oi_matrix():
        oi_change = bot._get_oi_change(symbol)
        funding = b5.get("funding") or bot.get_funding_rate(symbol)
        ls_ratio = bot._get_ls_ratio(symbol)
        if oi_change and ch1h != 0:
            if ch1h > 0 and oi_change > 0:
                oi_text = "Цена↑ OI↑ — новые лонги, тренд подтверждён объёмом позиций"
            elif ch1h > 0 and oi_change < 0:
                oi_text = "Цена↑ OI↓ — шорт-сквиз, движение может исчерпаться"
            elif ch1h < 0 and oi_change > 0:
                oi_text = "Цена↓ OI↑ — новые шорты, реальное давление продавцов"
            else:
                oi_text = "Цена↓ OI↓ — закрытие позиций, движение слабеет"
        else:
            oi_text = "OI: недостаточно данных для интерпретации"
        ls_text = ("лонгов заметно больше шортов (риск шорт-сквиза выше)" if ls_ratio > 1.5
                   else "шортов заметно больше лонгов (риск лонг-сквиза выше)" if ls_ratio < 0.7
                   else "L/S сбалансирован")
        return {"ok": True, "oi_change": oi_change, "oi_text": oi_text,
                "funding": funding, "ls_ratio": ls_ratio, "ls_text": ls_text}
    b7 = _safe("Блок 7 (OI/Funding/L-S)", _oi_matrix)
    result["block7_oi"] = b7

    # ── Блок 8: killzone/сессия ──
    def _killzone():
        kz = bot.get_killzone_status()
        active = kz["active"]
        session_note = {
            " Asia Session": "Азия — обычно рендж, накопление ликвидности перед манипуляцией",
            " London Open": "Лондон — часто манипуляция/свип перед настоящим движением (AMD)",
            " London Close": "Закрытие Лондона — фиксация, возможен разворот интрадей",
            " NY Open": "Нью-Йорк — обычно дистрибуция/настоящее направление дня",
            " NY Close": "Закрытие NY — низкая ликвидность, повышенный шум",
        }.get(active.get("name", ""), "Dead Zone — низкая ликвидность, движения менее надёжны")
        return {"ok": True, "kz": kz, "session_note": session_note}
    b8 = _safe("Блок 8 (Killzone/сессия)", _killzone)
    result["block8_killzone"] = b8

    # ── Блок 9: фаза рынка (BTC + символ), Wyckoff-эвристика ──
    def _phase():
        sym_phase = ta_extra.wyckoff_phase_heuristic(closes_1d, price)
        if symbol == "BTC":
            btc_phase = sym_phase
        else:
            btc_c1d = bot.get_binance_ohlc("BTC", "1d", 365) or []
            btc_closes = [c["close"] for c in btc_c1d] if btc_c1d else []
            btc_phase = (ta_extra.wyckoff_phase_heuristic(btc_closes, btc_closes[-1])
                        if btc_closes else {"phase": "нет данных", "note": ""})
        return {"ok": True, "symbol_phase": sym_phase, "btc_phase": btc_phase}
    b9 = _safe("Блок 9 (Фаза рынка)", _phase)
    result["block9_phase"] = b9

    # ── Блок 10: мемкоин-фильтр ──
    meme_risk = rank > MEME_RANK_THRESHOLD or mcap < MEME_MCAP_THRESHOLD or vol24 < MEME_VOL_THRESHOLD
    result["block10_meme_risk"] = {
        "ok": True, "flagged": meme_risk,
        "reason": (f"rank #{rank}, mcap ${mcap/1e6:.0f}M, vol24h ${vol24/1e6:.0f}M" if meme_risk else ""),
    }

    # ── Блок 11: план сделки ──
    def _trade_plan():
        checklist_score = b5.get("score", 0) if b5.get("ok") else 0
        if not direction:
            return {"ok": True, "has_setup": False,
                    "reason": f"направление не определено ({b1.get('tf_agreement', 'н/д')})",
                    "wait_for": "ждать согласования 1D-структуры и 4H EMA-стека в одну сторону"}
        if checklist_score < CHECKLIST_MIN_FOR_TRADE:
            failed = [name for name, ok in b5.get("items", []) if not ok]
            return {"ok": True, "has_setup": False,
                    "reason": f"чеклист {checklist_score}/6 < {CHECKLIST_MIN_FOR_TRADE}: " + "; ".join(failed[:3]),
                    "wait_for": "ждать выполнения непройденных пунктов чеклиста (см. Блок 5)"}
        trade = b5.get("trade")
        if not trade:
            return {"ok": True, "has_setup": False,
                    "reason": "нет зоны для входа от структуры (find_sr_zones не нашёл валидной зоны)",
                    "wait_for": "ждать формирования чёткого swing-уровня (2+ касания)"}
        entry1 = ta_extra.smart_round(trade["entry1"])
        entry2 = ta_extra.smart_round(trade["entry2"])
        entry3 = ta_extra.smart_round(trade["entry3"])
        sl = ta_extra.smart_round(trade["sl"])
        tp1 = ta_extra.smart_round(trade["tp1"])
        tp2 = ta_extra.smart_round(trade["tp2"])
        tp3 = ta_extra.smart_round(trade["tp3"])
        risk_per_unit = abs(entry1 - sl) or 1e-9
        deposit = 1000.0
        sizes = {}
        for risk_pct in (1, 2, 3):
            risk_usd = deposit * risk_pct / 100
            sizes[risk_pct] = {
                "risk_usd": round(risk_usd, 2),
                "position_usd": round(risk_usd / risk_per_unit * entry1, 2),
            }
        return {
            "ok": True, "has_setup": True, "direction": direction,
            "entry1": entry1, "entry2": entry2, "entry3": entry3,
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr_tp1": trade["rr_tp1"], "rr_tp2": trade["rr_tp2"], "rr_tp3": trade["rr_tp3"],
            "dca": "50% @ entry1 / 30% @ entry2 / 20% @ entry3",
            "position_sizes_per_1000_deposit": sizes,
            "entry_zone_touches": trade["entry_zone"]["touches"],
            "entry_zone_sources": trade["entry_zone"]["sources"],
        }
    b11 = _safe("Блок 11 (План сделки)", _trade_plan)
    result["block11_trade_plan"] = b11

    # ── Блок 12: Rocket Score (агрегация) ──
    def _rocket_score():
        score = 50
        factors = []

        d_ema = ta_extra.ema_stack_score_delta(ema_ctx, direction) if direction else 0
        if d_ema: score += d_ema; factors.append((f"EMA-стек 4H {'за' if d_ema>0 else 'против'}", d_ema))

        d_sweep = ta_extra.sweep_score_delta(sweep_1h, sweep_4h, direction) if direction else 0
        if d_sweep: score += d_sweep; factors.append((f"Свип ликвидности {'за' if d_sweep>0 else 'против'}", d_sweep))

        d_elliott = b2.get("score_delta", 0) if b2.get("ok", True) else 0
        if d_elliott: score += d_elliott; factors.append((f"Elliott: {b2.get('label','')}", d_elliott))

        checklist_score = b5.get("score", 0) if b5.get("ok") else 0
        d_checklist = (checklist_score - 3) * 5
        if d_checklist: score += d_checklist; factors.append((f"Чеклист {checklist_score}/6", d_checklist))

        phase = b9.get("symbol_phase", {}).get("phase", "") if b9.get("ok") else ""
        d_phase = 0
        if direction == "long" and ("Накопление" in phase or "Маркап" in phase): d_phase = 8
        elif direction == "short" and ("Распределение" in phase or "Маркдаун" in phase): d_phase = 8
        elif direction == "long" and ("Распределение" in phase or "Маркдаун" in phase): d_phase = -8
        elif direction == "short" and ("Накопление" in phase or "Маркап" in phase): d_phase = -8
        if d_phase: score += d_phase; factors.append((f"Фаза рынка: {phase}", d_phase))

        smc_type = b3.get("type") if b3.get("ok", True) else None
        d_smc = 0
        if smc_type and "CHoCH" in smc_type: d_smc = 10
        elif smc_type and "BOS" in smc_type: d_smc = 6
        if d_smc: score += d_smc; factors.append((f"SMC-сетап: {(smc_type or '').replace('_', ' ')}", d_smc))

        entry_zone_touches = b11.get("entry_zone_touches", 0) if b11.get("has_setup") else 0
        d_polarity = 5 if entry_zone_touches >= 3 else 0
        if d_polarity: score += d_polarity; factors.append(("Polarity-уровень (3+ касания)", d_polarity))

        if meme_risk:
            score -= 20; factors.append(("Мемкоин/низкая ликвидность", -20))

        score = max(0, min(100, score))
        return {"ok": True, "score": score, "factors": factors}
    b12 = _safe("Блок 12 (Rocket Score)", _rocket_score)
    result["block12_rocket"] = b12

    # ── Блок 13: итоговый вердикт ──
    def _verdict():
        lines = []
        if bias == "NEUTRAL":
            lines.append(f"{symbol}: направление неочевидно — 1D и 4H не согласованы, лучше подождать.")
        else:
            side_ru = "рост" if direction == "long" else "падение"
            lines.append(f"{symbol}: bias {bias.lower()} ({side_ru}), {b1.get('tf_agreement','')}.")
        if b11.get("has_setup"):
            lines.append(f"Сделка от структуры готова: вход {b11['entry1']}, SL {b11['sl']}, "
                         f"R:R {b11['rr_tp1']} по TP1. Чеклист {b5.get('score',0)}/6.")
        else:
            lines.append(f"Сетапа нет: {b11.get('reason','н/д')}.")
        if meme_risk:
            lines.append("⚠️ Низкая ликвидность/мемкоин — при входе снизить размер позиции вдвое.")
        return {"ok": True, "text": " ".join(lines)}
    b13 = _safe("Блок 13 (Вердикт)", _verdict)
    result["block13_verdict"] = b13

    result["ema_ctx"] = ema_ctx
    result["sweep_1h"] = sweep_1h
    result["sweep_4h"] = sweep_4h
    result["zones"] = zones

    # ── Signal Journal: логируем только реальный план сделки, source="full_analysis" ──
    if b11.get("has_setup"):
        try:
            # entry_lo/entry_hi — фактический ценовой порядок (lo < hi), не порядок входа
            # DCA: для LONG entry1 (первый транш) выше entry3, для SHORT — наоборот
            # (см. ta_extra.build_trade_from_structure и существующий конвеншен в
            # bot.py TOP_LONG/TOP_SHORT-логировании).
            e_lo, e_hi = ((b11["entry3"], b11["entry1"]) if direction == "long"
                         else (b11["entry1"], b11["entry3"]))
            signal_journal.log_signal(
                "full_analysis", symbol, direction, price,
                entry_lo=e_lo, entry_hi=e_hi, sl=b11["sl"],
                tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
                rr=b11["rr_tp1"], rocket_score=b12.get("score"),
                ema_stack=ema_ctx, sweep=sweep_4h or sweep_1h,
                levels_source="structure", grade=None,
            )
        except Exception:
            pass  # логирование не должно ронять сам анализ

    return result


# ── Рендер карточки ──────────────────────────────────────────────────────────

def _fp(v):
    if v == 0: return "0"
    if abs(v) >= 1:
        s = f"{v:,.4f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    return f"{v:.8g}"


def _fpct(v):
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def render_full_analysis_card(result: dict) -> str:
    """Рендерит структурированный результат build_full_analysis() в одну markdown-карточку
    (8 визуальных секций, покрывающих все 13 содержательных блоков ТЗ). Возвращает
    единую строку — для отправки в Telegram с учётом лимита 4096 символов используйте
    split_card()."""
    if not result.get("ok"):
        return f"*{result.get('symbol','?')}*\n\n⚠️ {result.get('error','нет данных')}"

    sym = result["symbol"]
    price = result["price"]
    parts = []

    # 1. Заголовок
    parts.append(f"*{sym}USDT* · Полный анализ")
    parts.append(f"💰 `{_fp(price)}` {result.get('price_fresh','')}   "
                 f"Rank #{result.get('rank','?')}")
    parts.append(f"1H `{_fpct(result.get('ch1h',0))}`  24H `{_fpct(result.get('ch24h',0))}`  "
                 f"7D `{_fpct(result.get('ch7d',0))}`")
    meme = result.get("block10_meme_risk", {})
    if meme.get("flagged"):
        parts.append(f"⚠️ *ВЫСОКИЙ РИСК*: низкая ликвидность/мемкоин ({meme.get('reason','')}) — снизить размер позиции вдвое")
    parts.append("")

    # 2. Bias / Elliott / SMC-структура
    b1 = result.get("block1_bias", {})
    b2 = result.get("block2_elliott", {})
    b3 = result.get("block3_smc", {})
    parts.append(f"🧭 *Bias: {b1.get('bias','?')}*  ({b1.get('tf_agreement','н/д')})")
    for d in b1.get("detail", []):
        parts.append(f"  {d}")
    parts.append(f"🌊 Elliott: {b2.get('label', b2.get('error','нет данных'))}"
                 + (f" — {b2['note']}" if b2.get("note") else ""))
    parts.append(f"📐 SMC-сетап: {b3.get('label', b3.get('error','нет данных'))}")
    parts.append("")

    # 3. POI + Ликвидность/ловушки
    b4 = result.get("block4_poi", {})
    b6 = result.get("block6_liquidity", {})
    parts.append("🎯 *POI (зоны интереса):*")
    poi = b4.get("poi", [])[:6]
    if poi:
        for p in poi:
            arrow = "▲" if p["side"] == "above" else "▼"
            parts.append(f"  {arrow} `{_fp(p['price'])}` ({_fpct(p['distance_pct'])}) "
                         f"— {', '.join(p['sources'])}, {p['touches']} кас.")
    else:
        parts.append("  нет чётких зон")
    if b6.get("sweep_line"):
        parts.append(b6["sweep_line"])
    eq = b6.get("equal_levels", [])
    if eq:
        eq_s = ", ".join(f"{'EQH' if e['kind']=='high' else 'EQL'} `{_fp(e['price'])}`" for e in eq[:3])
        parts.append(f"🧲 Equal highs/lows (магниты стопов): {eq_s}")
    parts.append("")

    # 4. Чеклист Kira/ICT
    b5 = result.get("block5_checklist", {})
    parts.append(f"✅ *Чеклист Kira/ICT: {b5.get('score','?')}/6*")
    for name, ok in b5.get("items", []):
        parts.append(f"  {'✅' if ok else '❌'} {name}")
    parts.append("")

    # 5. OI/Funding/L-S + Killzone/сессия
    b7 = result.get("block7_oi", {})
    b8 = result.get("block8_killzone", {})
    parts.append("📊 *OI / Funding / L-S:*")
    if b7.get("ok"):
        parts.append(f"  {b7.get('oi_text','нет данных')}")
        fr = b7.get("funding", {})
        if fr.get("ok"):
            parts.append(f"  Funding `{fr['rate']:+.4f}%` — {fr.get('signal','')}")
        parts.append(f"  L/S `{b7.get('ls_ratio', 1.0):.2f}` — {b7.get('ls_text','')}")
    else:
        parts.append(f"  {b7.get('error','нет данных')}")
    kz = b8.get("kz", {}).get("active", {})
    parts.append(f"⏰ Killzone: *{kz.get('name','?')}* (`{kz.get('quality','?')}`) — {b8.get('session_note','')}")
    parts.append("")

    # 6. Фаза рынка
    b9 = result.get("block9_phase", {})
    sp = b9.get("symbol_phase", {})
    bp = b9.get("btc_phase", {})
    parts.append(f"📈 Фаза {sym}: *{sp.get('phase','н/д')}*")
    if sym != "BTC":
        parts.append(f"📈 Фаза BTC: *{bp.get('phase','н/д')}*")
    if sp.get("note"):
        parts.append(f"  _{sp['note']}_")
    parts.append("")

    # 7. План сделки + Rocket Score
    b11 = result.get("block11_trade_plan", {})
    b12 = result.get("block12_rocket", {})
    parts.append(f"🚀 *Rocket Score: {b12.get('score','?')}/100*")
    for label, delta in b12.get("factors", [])[:8]:
        parts.append(f"  {'+' if delta>0 else ''}{delta} {label}")
    parts.append("")
    if b11.get("has_setup"):
        parts += [
            f"📋 *План сделки ({b11['direction'].upper()}):*",
            f"  Вход (DCA): `{b11['entry1']}` / `{b11['entry2']}` / `{b11['entry3']}` ({b11['dca']})",
            f"  SL: `{b11['sl']}`",
            f"  TP1 `{b11['tp1']}` (R:R {b11['rr_tp1']})  TP2 `{b11['tp2']}` (R:R {b11['rr_tp2']})  "
            f"TP3 `{b11['tp3']}` (R:R {b11['rr_tp3']})",
        ]
        sizes = b11.get("position_sizes_per_1000_deposit", {})
        if sizes:
            sz_s = "  ".join(f"{pct}%: ${s['position_usd']:.0f}" for pct, s in sizes.items())
            parts.append(f"  Размер позиции (на $1000 депозита, риск): {sz_s}")
    else:
        parts += [
            "📋 *Сетапа нет:*",
            f"  {b11.get('reason','н/д')}",
            f"  Условие появления: {b11.get('wait_for','н/д')}",
        ]
    parts.append("")

    # 8. Вердикт
    b13 = result.get("block13_verdict", {})
    parts.append(f"💬 *Вердикт:* {b13.get('text','н/д')}")

    return "\n".join(parts)


def split_card(text: str, limit: int = 4096) -> list:
    """Разбивает карточку на части <= limit символов, режет по границам строк, чтобы
    не ломать markdown-сущности внутри строки."""
    if len(text) <= limit:
        return [text]
    parts = []
    lines = text.split("\n")
    buf = []
    buf_len = 0
    for line in lines:
        add_len = len(line) + 1
        if buf_len + add_len > limit and buf:
            parts.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(line)
        buf_len += add_len
    if buf:
        parts.append("\n".join(buf))
    return parts
