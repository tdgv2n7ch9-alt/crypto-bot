"""
BEST TRADE — BUY/SELL сигнальный контур: проактивные торговые оповещения владельцу.

Двухступенчатая архитектура поверх существующих модулей:

  СТУПЕНЬ 1 (скринер кандидатов, каждые STAGE1_INTERVAL_MIN мин, дёшево): funding-
  экстремум ИЛИ OI-сёрдж (+OI_SURGE_PCT% за OI_SURGE_LOOKBACK_SEC при цене в диапазоне
  <= OI_SURGE_PRICE_RANGE_PCT%) ИЛИ свежий свип ликвидности на 4h. Funding/OI берутся из
  bot._fetch_coingecko_oi_map() — ОДИН общий кэшированный (90с) вызов на ВСЕ символы
  разом, не новый API-бюджет. Свип проверяется Bybit-свечами (дёшево после миграции
  candle-источника на Bybit) и ТОЛЬКО для символов, не прошедших funding/OI-фильтр
  дёшево — не гоняем свечи по всей вселенной без нужды.

  СТУПЕНЬ 2 (глубокая проверка, fa_engine.build_full_analysis): кандидат становится
  торговым алертом, только если fa_engine Блок 11 сам решил has_setup=True (чеклист >=4/6,
  R:R-гейт пройден, bias не NEUTRAL) — используем ГОТОВОЕ решение fa_engine, не дублируем
  его критерии второй раз. Кандидат, не прошедший ступень 2, тихо уходит в лог, без алерта.

EXIT-половина: каждый отправленный торговый алерт логируется в signal_journal
(source="signal_loop") — тот трекер (run_tracker(), уже запущен bot.py) даёт персистентную
win-rate статистику для /journal. Параллельно свой, более богатый трекер (_active_signals)
даёт последовательные уведомления по ходу сделки (вход активен -> TP1 (SL в безубыток) ->
TP2 (SL на TP1) -> TP3/SL, плюс независимое предупреждение о структурном развороте против
позиции) — обычный однократный signal_journal-трекер рассчитан на единственный терминальный
исход ради честной агрегированной статистики по всем источникам сразу, а не на пошаговое
сопровождение одной открытой сделки, поэтому многоступенчатая логика реализована здесь
отдельно, а не встроена в signal_journal.py (не хотим менять семантику, общую для всех
остальных источников).

Все пороги ниже — именованные константы, специально для калибровки без переписывания
логики.
"""

import time
from collections import deque
from datetime import datetime

import pytz

import ta_extra
import signal_journal
import fa_engine

# ── Пороги (калибруемые константы) ──────────────────────────────────────────
STAGE1_INTERVAL_MIN = 15
EXIT_TRACKER_INTERVAL_MIN = 2

FUNDING_EXTREME_NEG_PCT = -0.10      # funding <= -0.10% -- шорты перегреты, риск сквиза вверх
FUNDING_EXTREME_POS_PCT = 0.15       # funding >= +0.15% -- лонги перегреты, риск сквиза вниз

OI_SURGE_PCT = 15.0                  # рост OI >= 15% за OI_SURGE_LOOKBACK_SEC
OI_SURGE_LOOKBACK_SEC = 4 * 3600
OI_SURGE_PRICE_RANGE_PCT = 2.0       # ...при цене в диапазоне <= 2% за то же окно
OI_HISTORY_MAXLEN = 20               # ~20 * 15 мин = 5ч запаса на LOOKBACK=4ч

SWEEP_CANDLES_INTERVAL = "4h"
SWEEP_CANDLES_LIMIT = 60

CANDIDATE_COOLDOWN_SEC = 4 * 3600    # кулдаун 4ч/символ на СТУПЕНИ 1
MAX_DAILY_ALERTS = 5                 # максимум торговых алертов в сутки
MIN_VOL_USD = 5_000_000              # минимальный 24ч объём для кандидата
PRE_ENTRY_EXPIRE_SEC = 72 * 3600     # как signal_journal.PENDING_EXPIRE_SEC -- не висим вечно

CHECKLIST_MIN = fa_engine.CHECKLIST_MIN_FOR_TRADE  # единый источник с fa_engine, не дублируем число

TZ = pytz.timezone("Europe/Istanbul")

# ── Состояние (in-memory, как и другие once-per-run кэши бота — сбрасывается при рестарте) ──
_oi_history = {}          # symbol -> deque[(ts, oi_usd, price)]
_last_candidate_ts = {}   # symbol -> ts последнего попадания в кандидаты (кулдаун ступени 1)
_daily = {"day": None, "count": 0}
_active_signals = {}      # alert_id -> состояние богатого EXIT-трекера
_next_alert_id = 1


def _log(msg):
    print(f"[signal_loop] {msg}")


def _today_key():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _roll_daily_if_needed():
    today = _today_key()
    if _daily["day"] != today:
        _daily["day"] = today
        _daily["count"] = 0


def _daily_cap_reached() -> bool:
    _roll_daily_if_needed()
    return _daily["count"] >= MAX_DAILY_ALERTS


def _register_alert_sent():
    _roll_daily_if_needed()
    _daily["count"] += 1


def _update_oi_history(symbol, ts, oi_usd, price):
    hist = _oi_history.setdefault(symbol, deque(maxlen=OI_HISTORY_MAXLEN))
    hist.append((ts, oi_usd, price))


def _oi_surge_and_range(symbol, now_ts):
    """(oi_change_pct, price_range_pct) за последние OI_SURGE_LOOKBACK_SEC, либо (None, None)
    при нехватке истории (контур только запустился, ещё не накопил ~4ч снапшотов)."""
    hist = _oi_history.get(symbol)
    if not hist or len(hist) < 2:
        return None, None
    cutoff = now_ts - OI_SURGE_LOOKBACK_SEC
    window = [h for h in hist if h[0] >= cutoff]
    if len(window) < 2:
        return None, None
    oi_start, oi_now = window[0][1], window[-1][1]
    prices = [h[2] for h in window]
    if oi_start <= 0 or not prices or min(prices) <= 0:
        return None, None
    oi_change_pct = (oi_now - oi_start) / oi_start * 100
    price_range_pct = (max(prices) - min(prices)) / min(prices) * 100
    return oi_change_pct, price_range_pct


def _stage1_screen(bot):
    """Список кандидатов [{"symbol","reasons":[...],"coin","meme_risk"}]. bot: модуль
    bot.py, передаётся явно (тот же паттерн отложенной зависимости, что и в fa_engine.py,
    чтобы не плодить циклические импорты на уровне модуля)."""
    now_ts = time.time()
    oi_map = bot._fetch_coingecko_oi_map()
    coins_by_symbol = {c["symbol"]: c for c in bot.get_all_coins()}

    pre_candidates = []  # (symbol, reasons, coin, vol24) -- прошли volume-фильтр и кулдаун
    for symbol, data in oi_map.items():
        coin = coins_by_symbol.get(symbol)
        vol24 = coin["quote"]["USDT"].get("volume_24h", 0) if coin else 0
        if vol24 < MIN_VOL_USD:
            continue
        if now_ts - _last_candidate_ts.get(symbol, 0) < CANDIDATE_COOLDOWN_SEC:
            continue

        price, oi_usd, funding = data.get("price", 0), data.get("oi", 0), data.get("funding", 0)
        _update_oi_history(symbol, now_ts, oi_usd, price)

        reasons = []
        if funding <= FUNDING_EXTREME_NEG_PCT:
            reasons.append(f"funding {funding:.3f}% (экстремум, шорты перегреты)")
        elif funding >= FUNDING_EXTREME_POS_PCT:
            reasons.append(f"funding {funding:.3f}% (экстремум, лонги перегреты)")

        oi_change_pct, price_range_pct = _oi_surge_and_range(symbol, now_ts)
        if (oi_change_pct is not None and oi_change_pct >= OI_SURGE_PCT
                and price_range_pct is not None and price_range_pct <= OI_SURGE_PRICE_RANGE_PCT):
            reasons.append(f"OI +{oi_change_pct:.1f}% за ~4ч при цене в рендже {price_range_pct:.1f}%")

        pre_candidates.append((symbol, reasons, coin, vol24))

    candidates = []
    for symbol, reasons, coin, vol24 in pre_candidates:
        if not reasons:
            # ни funding, ни OI не сработали -- единственная причина тратить REST-вызов
            # на свечи для этого символа: проверка свежего свипа.
            try:
                c4h = bot.get_binance_ohlc(symbol, SWEEP_CANDLES_INTERVAL, SWEEP_CANDLES_LIMIT)
                sweep = ta_extra.detect_sweep(c4h) if c4h else None
            except Exception:
                sweep = None
            if not (sweep and sweep["bars_ago"] <= ta_extra.FRESH_SWEEP_BARS):
                continue
            kind = "хаёв (шорт-сигнал)" if sweep["type"] == "sweep_high" else "лоёв (лонг-сигнал)"
            reasons = [f"свежий свип {kind} на 4h ({sweep['bars_ago']} баров назад)"]

        _last_candidate_ts[symbol] = now_ts
        rank = coin.get("cmc_rank", 9999) if coin else 9999
        mcap = coin["quote"]["USDT"].get("market_cap", 0) if coin else 0
        meme_risk = (rank > fa_engine.MEME_RANK_THRESHOLD or mcap < fa_engine.MEME_MCAP_THRESHOLD
                     or vol24 < fa_engine.MEME_VOL_THRESHOLD)
        candidates.append({"symbol": symbol, "reasons": reasons, "coin": coin, "meme_risk": meme_risk})
    return candidates


def _stage2_check(symbol, coin):
    """fa_engine.build_full_analysis() dict, если Блок 11 сам решил has_setup=True, иначе
    None. Не дублируем критерии чеклиста/R:R/bias здесь — используем готовое решение
    fa_engine (единый источник истины, тот же, что и у /full)."""
    try:
        result = fa_engine.build_full_analysis(symbol, coin)
    except Exception as e:
        _log(f"{symbol}: stage2 exception {e}")
        return None
    if not result.get("ok"):
        return None
    b11 = result.get("block11_trade_plan", {})
    if not b11.get("has_setup"):
        try:
            signal_journal.log_rejected("signal_loop", symbol, b11.get("reason", "неизвестно"))
        except Exception as e:
            _log(f"{symbol}: log_rejected failed {e}")
        return None
    return result


def _format_alert_text(symbol, result, reasons):
    """HTML (не Markdown) -- см. bot.py:build_signal_text для истории бага 1: та же
    класса фрагильность (дефолтный legacy Markdown ест текст, если где-то в сообщении
    несбалансированное кол-во */_/`) актуальна и здесь при живых данных (символ,
    funding/OI-строки в reasons), даже если сам этот модуль написан заново и без
    утраченной кириллицы. html.escape() на каждое динамическое значение."""
    import html
    b11 = result["block11_trade_plan"]
    b12 = result["block12_rocket"]
    b5 = result["block5_checklist"]
    direction = b11["direction"]
    side = "SHORT" if direction == "short" else "LONG"
    emoji = "🔴" if direction == "short" else "🟢"
    sym_e = html.escape(symbol)
    lines = [
        f"{emoji} <b>{side} {sym_e} — сетап подтверждён</b>",
        "",
    ]
    lines += [f"• {html.escape(r)}" for r in reasons[:2]]
    lines.append(f"• чеклист {b5['score']}/6, Rocket Score {b12['score']}/100")
    lines += [
        "",
        f"Вход (DCA): <code>{b11['entry1']}</code> (50%) / <code>{b11['entry2']}</code> (30%) / <code>{b11['entry3']}</code> (20%)",
        f"SL: <code>{b11['sl']}</code>",
        f"TP1 <code>{b11['tp1']}</code> (R:R {b11['rr_tp1']})  TP2 <code>{b11['tp2']}</code> (R:R {b11['rr_tp2']})  "
        f"TP3 <code>{b11['tp3']}</code> (R:R {b11['rr_tp3']})",
    ]
    sizes = b11.get("position_sizes_per_1000_deposit", {})
    if sizes:
        sz_s = "  ".join(f"{pct}%: ${s['position_usd']:.0f}" for pct, s in sizes.items())
        lines.append(f"Размер позиции (на $1000 депозита, риск): {sz_s}")
    return "\n".join(lines)


def _build_alert_chart(symbol, result):
    """Chart v4 (chart_v4.py) как основной график для signal_loop-алертов -- это
    свинг-сигналы (funding/OI/sweep, гейт через fa_engine), а не памп/дамп-разворот, им
    подходит 2h/~120 баров, а не 5m Chart v2 (тот остаётся для пампов, см. pump_detector.py
    и его собственный вызов _build_chart в другом месте бота). zones/candles_4h уже
    посчитаны build_full_analysis() (result["zones"]/result["candles_4h"]), даёт Chart v4
    мульти-ТФ POI-прямоугольники без доп. API-вызовов. Фоллбек Chart v4 -> Chart v3 (без
    зон) -> generate_signal_chart (bot.py, Bybit REST candles), если оба почему-то не
    смогли (недостаточно баров и т.п.) — честный фоллбек, а не баг."""
    b11 = result["block11_trade_plan"]
    b1 = result.get("block1_bias", {})
    direction = b11["direction"]
    price = result["price"]

    try:
        import bot
        import chart_v4
        import chart_v3
        candles = bot.get_binance_ohlc(symbol, "2h", 120)
        key_high = (b1.get("key_high") or {}).get("price")
        key_low = (b1.get("key_low") or {}).get("price")
        entry_levels = [b11["entry1"], b11["entry2"], b11["entry3"]]
        try:
            chart = chart_v4.build_trade_chart_v4(
                symbol, candles, direction, entry_levels=entry_levels,
                sl=b11["sl"], tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
                rr=b11["rr_tp1"], key_high=key_high, key_low=key_low, tf_label="2h",
                zones=result.get("zones"), candles_4h=result.get("candles_4h"))
            if chart:
                return chart
        except Exception as e:
            _log(f"{symbol}: Chart v4 unavailable ({e}), falling back to Chart v3")
        chart = chart_v3.build_trade_chart(
            symbol, candles, direction, entry_levels=entry_levels,
            sl=b11["sl"], tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
            rr=b11["rr_tp1"], key_high=key_high, key_low=key_low, tf_label="2h")
        if chart:
            return chart
    except Exception as e:
        _log(f"{symbol}: Chart v3 unavailable ({e}), falling back to generate_signal_chart")

    try:
        import bot
        a = {"is_long": direction == "long", "price": price,
             "tp1": b11["tp1"], "tp2": b11["tp2"], "tp3": b11["tp3"],
             "sl": b11["sl"], "swing": b11["entry1"], "rsi_4h": 50.0}
        return bot.generate_signal_chart(symbol, a)
    except Exception as e:
        _log(f"{symbol}: fallback chart also failed ({e})")
        return None


async def _send_alert(tg_bot, chat_id, symbol, result, reasons, meme_risk, bot_module=None):
    global _next_alert_id
    b11 = result["block11_trade_plan"]
    direction = b11["direction"]
    text = _format_alert_text(symbol, result, reasons)
    if meme_risk:
        text += "\n\n⚠️ Мемкоин/низкая ликвидность — снизить размер позиции вдвое."

    chart = None
    try:
        chart = _build_alert_chart(symbol, result)
    except Exception as e:
        _log(f"{symbol}: chart build failed: {e}")

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 Полный анализ", callback_data=f"full_{symbol}"),
        InlineKeyboardButton("👁 Следить", callback_data=f"sigloop_watch_{symbol}"),
    ]])
    try:
        if chart:
            await tg_bot.send_photo(chat_id, photo=chart, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await tg_bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        _log(f"{symbol}: send with HTML failed ({e}), retrying plain")
        try:
            await tg_bot.send_message(chat_id, text, reply_markup=kb)
        except Exception as e2:
            _log(f"{symbol}: send_alert failed entirely: {e2}")
            return

    e_lo, e_hi = ((b11["entry3"], b11["entry1"]) if direction == "long"
                 else (b11["entry1"], b11["entry3"]))
    journal_id = None
    degraded_data = None
    if bot_module is not None:
        try:
            degraded_data = bot_module._data_quality_flags()
        except Exception:
            degraded_data = None
    try:
        journal_id = signal_journal.log_signal(
            "signal_loop", symbol, direction, result["price"],
            entry_lo=e_lo, entry_hi=e_hi, sl=b11["sl"],
            tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
            rr=b11["rr_tp1"], rocket_score=result["block12_rocket"]["score"],
            ema_stack=result.get("ema_ctx"), sweep=result.get("sweep_4h") or result.get("sweep_1h"),
            levels_source="structure", grade=None, degraded_data=degraded_data,
        )
    except Exception as e:
        _log(f"{symbol}: journal log failed: {e}")

    alert_id = _next_alert_id
    _next_alert_id += 1
    _active_signals[alert_id] = {
        "symbol": symbol, "direction": direction, "chat_id": chat_id,
        "entry_lo": e_lo, "entry_hi": e_hi,
        "sl": b11["sl"], "tp1": b11["tp1"], "tp2": b11["tp2"], "tp3": b11["tp3"],
        "entered": False, "entered_price": None,
        "tp1_hit": False, "tp2_hit": False, "closed": False,
        "structure_warned": False, "journal_id": journal_id,
        "created_ts": time.time(),
    }
    _log(f"{symbol}: alert sent ({direction}), journal_id={journal_id}, alert_id={alert_id}")


async def run_signal_loop(bot, tg_bot, owner_chat_id):
    """СТУПЕНЬ 1 + СТУПЕНЬ 2. Вызывается APScheduler каждые STAGE1_INTERVAL_MIN мин.
    bot: модуль bot.py, tg_bot: telegram.Bot, owner_chat_id: int."""
    try:
        candidates = _stage1_screen(bot)
    except Exception as e:
        _log(f"stage1 exception: {e}")
        return
    if not candidates:
        return
    candidates.sort(key=lambda c: c["meme_risk"])  # мемкоины -- вниз приоритета, не исключаем
    for cand in candidates:
        if _daily_cap_reached():
            _log(f"daily cap ({MAX_DAILY_ALERTS}) reached, skip remaining ({cand['symbol']}+)")
            break
        symbol = cand["symbol"]
        result = _stage2_check(symbol, cand["coin"])
        if not result:
            _log(f"{symbol}: candidate ({'; '.join(cand['reasons'])}) failed stage2 -- no alert (silent)")
            continue
        await _send_alert(tg_bot, owner_chat_id, symbol, result, cand["reasons"], cand["meme_risk"], bot_module=bot)
        _register_alert_sent()


def _sl_hit(direction, price, sl):
    return price <= sl if direction == "long" else price >= sl


def _tp_hit(direction, price, tp):
    return price >= tp if direction == "long" else price <= tp


def _touches_zone(price, lo, hi):
    lo2, hi2 = (lo, hi) if lo <= hi else (hi, lo)
    return lo2 <= price <= hi2


async def _notify(tg_bot, chat_id, text):
    try:
        await tg_bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        _log(f"notify failed: {e}")


def _check_structure_break(bot, symbol, direction):
    """Свежий свип ПРОТИВ направления либо CHoCH на 1h против направления -- сигнал
    "структура сломалась". Небольшой доп. REST-вызов (1h-свечи), но только для активно
    отслеживаемых сигналов (обычно единицы одновременно), не для всей вселенной."""
    try:
        c1h = bot.get_binance_ohlc(symbol, "1h", 100)
        if not c1h or len(c1h) < 20:
            return False
        sweep = ta_extra.detect_sweep(c1h)
        if sweep and sweep["bars_ago"] <= ta_extra.FRESH_SWEEP_BARS:
            against = ((direction == "long" and sweep["type"] == "sweep_high")
                       or (direction == "short" and sweep["type"] == "sweep_low"))
            if against:
                return True
        smc = ta_extra.smc_setup_type(c1h, bias_direction=direction)
        if smc.get("aligned") is False:  # CHoCH против направления
            return True
    except Exception:
        pass
    return False


async def run_exit_tracker(bot, tg_bot):
    """Каждые EXIT_TRACKER_INTERVAL_MIN мин: вход активен -> TP1 (SL в безубыток) -> TP2
    (SL на TP1) -> TP3/SL -> плюс независимое предупреждение о структурном развороте.
    Отдельно от signal_journal.run_tracker() (та даёт только терминальную win-rate
    статистику, эта — пошаговое сопровождение открытой сделки, см. докстринг модуля).
    bot: модуль bot.py, tg_bot: telegram.Bot — оба передаются явно APScheduler'ом
    (статичные args=[...], тот же паттерн, что и у run_signal_loop)."""
    import html
    now = time.time()
    for alert_id, st in list(_active_signals.items()):
        if st["closed"]:
            continue
        symbol, direction, chat_id = st["symbol"], st["direction"], st["chat_id"]
        sym_e = html.escape(symbol)
        try:
            import live_prices
            price, _age = live_prices.get_live_price(symbol)
        except Exception:
            price = None
        if price is None:
            continue

        if not st["entered"]:
            if _touches_zone(price, st["entry_lo"], st["entry_hi"]):
                st["entered"] = True
                st["entered_price"] = price
                await _notify(tg_bot, chat_id, f"📍 <b>{sym_e}</b>: вход активен (<code>{price}</code>)")
            elif now - st["created_ts"] > PRE_ENTRY_EXPIRE_SEC:
                st["closed"] = True  # тихо -- как PENDING_EXPIRE в signal_journal, без алерта
            continue

        if _sl_hit(direction, price, st["sl"]):
            st["closed"] = True
            label = "SL в безубытке (0R)" if st["tp1_hit"] else "SL"
            await _notify(tg_bot, chat_id, f"🛑 <b>{sym_e}</b>: {label} hit (<code>{price}</code>)")
            continue

        if st["tp2_hit"] and _tp_hit(direction, price, st["tp3"]):
            st["closed"] = True
            await _notify(tg_bot, chat_id,
                          f"🏁 <b>{sym_e}</b>: TP3 hit (<code>{price}</code>) — цель достигнута полностью")
            continue

        if st["tp1_hit"] and not st["tp2_hit"] and _tp_hit(direction, price, st["tp2"]):
            st["tp2_hit"] = True
            st["sl"] = st["tp1"]  # фиксируем прибыль -- SL на TP1
            await _notify(tg_bot, chat_id, f"🎯 <b>{sym_e}</b>: TP2 hit (<code>{price}</code>) — подтяни SL на TP1")
            continue

        if not st["tp1_hit"] and _tp_hit(direction, price, st["tp1"]):
            st["tp1_hit"] = True
            st["sl"] = st["entered_price"]  # в безубыток
            await _notify(tg_bot, chat_id, f"🎯 <b>{sym_e}</b>: TP1 hit (<code>{price}</code>) — переведи SL в безубыток")
            continue

        if not st["structure_warned"] and _check_structure_break(bot, symbol, direction):
            st["structure_warned"] = True
            await _notify(tg_bot, chat_id,
                          f"⚠️ <b>{sym_e}</b>: структура сломалась (свип/CHoCH против позиции) — рассмотри выход")


def get_active_count():
    return sum(1 for s in _active_signals.values() if not s["closed"])
