"""
BEST TRADE — Памп-радар v1 (расширение Pump Detector, Этап 2+3 дорожной карты)
Binance Futures WebSocket (не заблокирован на Railway, в отличие от REST).
Данные для скоринга/алертов — только WS (klines) + CoinGecko/CMC через bot.py, без Binance REST.

Машина состояний на символ (pump_watch):
  PUMP_DETECTED -> WATCHING -> REVERSAL_CONFIRMED -> (PROMOTED | остаётся confirmed)
                            \-> EXPIRED (30 мин без разворота)

Запускается внутри bot.py через asyncio.create_task() (тот же процесс/event loop),
получает готовые функции из bot.py через PumpContext вместо собственных заглушек.
"""

import asyncio
import io
import json
import statistics
import time
from collections import deque

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import requests
import websockets

import live_prices

# ── Конфигурация ────────────────────────────────────────────────
WINDOW_DAYS = 14
Z_SCORE_THRESHOLD = 3.0
VOLUME_MULT_THRESHOLD = 5.0
CANDLE_INTERVAL = "1m"
ALERT_COOLDOWN_SEC = 900          # не спамить чаще раза в 15 мин на символ (PUMP_DETECTED)
WATCH_TIMEOUT_SEC = 30 * 60       # WATCHING/PUMP_DETECTED без разворота -> EXPIRED
REVERSAL_DRAWDOWN_PCT = 3.0       # откат от пика для REVERSAL_CONFIRMED
REVERSAL_RED_STREAK = 2           # мин. кол-во красных 1м свечей подряд
REVERSAL_VOL_MULT = 1.5           # объём отката >= x от среднего
SL_BUFFER_PCT = 1.5               # SL выше пика на +1.5%
PROMOTE_SCORE_THRESHOLD = 60      # порог pro_analysis().pro_score для PROMOTED (замена "_ks score" —
                                   # отдельного Kira|ICT скорера в коде нет, используем существующий
                                   # ICT/SMC скорер pro_analysis() как эквивалент)
PROMOTE_MIN_RR = 2.0              # R:R >= 1:2
MEMECOIN_MCAP_USD = 50_000_000    # ниже — помечаем ⚠️ МЕМКОИН
CHART_CANDLES = 90                # свечей 1м в чарт к алерту
TOP_N_SYMBOLS = 20                # стартовый набор — топ-N по объёму Binance Futures
SYMBOL_REFRESH_SEC = 6 * 3600     # как часто пересобирать список топ-N символов

BG, GREEN, RED, WHITE, GRAY, YELLOW = "#0D1421", "#16C784", "#EA3943", "#FFFFFF", "#7B8BB2", "#F0B90B"

# ── Состояние (в памяти процесса, персистентность не нужна в v1) ─
_volume_history = {}              # symbol -> deque(volumes)
_candle_history = {}              # symbol -> deque({"t","o","h","l","c","v"})
_last_alert_ts = {}                # symbol -> ts (cooldown на PUMP_DETECTED)
pump_watch = {}                    # symbol -> {...state...}
pump_history = deque(maxlen=500)   # завершённые наблюдения за последние 24ч (для раздела бота)
_subscriptions = {}                # symbol -> set(chat_id)
_current_symbols = []              # активный список отслеживаемых символов (topN)
_symbols_ts = 0.0


class PumpContext:
    """Набор функций из bot.py, внедряемых в детектор — чтобы не дублировать логику
    (killzone, OI-матрица, funding, скоринг) и не тащить сюда Binance REST."""
    def __init__(self, bot, owner_chat_id, get_coin_by_symbol, full_analysis, pro_analysis,
                 get_killzone_status, get_funding_pct, get_oi_usd, get_oi_change,
                 add_top_short_signal):
        self.bot = bot
        self.owner_chat_id = owner_chat_id
        self.get_coin_by_symbol = get_coin_by_symbol
        self.full_analysis = full_analysis
        self.pro_analysis = pro_analysis
        self.get_killzone_status = get_killzone_status
        self.get_funding_pct = get_funding_pct
        self.get_oi_usd = get_oi_usd
        self.get_oi_change = get_oi_change
        self.add_top_short_signal = add_top_short_signal


def get_pump_radar_state() -> dict:
    """Для раздела бота '⚡ Памп-радар': активные наблюдения + история за 24ч."""
    now = time.time()
    active = []
    for sym, st in pump_watch.items():
        pct_from_peak = (st["peak_price"] - st.get("last_price", st["peak_price"])) / st["peak_price"] * 100 if st["peak_price"] else 0
        active.append({
            "symbol": sym, "stage": st["stage"],
            "elapsed_min": round((now - st["pump_time"]) / 60, 1),
            "pct_from_peak": round(pct_from_peak, 2),
        })
    cutoff = now - 24*3600
    hist = [h for h in pump_history if h["ts"] >= cutoff]
    return {
        "active": active,
        "history_24h": {
            "detected": len(hist),
            "reversed": sum(1 for h in hist if h["final_stage"] in ("REVERSAL_CONFIRMED", "PROMOTED")),
            "promoted": sum(1 for h in hist if h["final_stage"] == "PROMOTED"),
            "expired":  sum(1 for h in hist if h["final_stage"] == "EXPIRED"),
        },
    }


def subscribe_symbol(symbol: str, chat_id: int):
    _subscriptions.setdefault(symbol.upper(), set()).add(chat_id)


def _fmt_price(v: float) -> str:
    if v >= 1000: return f"{v:,.2f}"
    if v >= 1:    return f"{v:.4f}"
    if v >= 0.01: return f"{v:.5f}"
    return f"{v:.8f}"


def _oi_matrix_label(price_up: bool, oi_change_pct: float, funding: float) -> str:
    """Та же интерпретация OI-матрицы, что и в /market и Институционале bot.py."""
    oi_up = oi_change_pct > 0
    if price_up and oi_up:
        return "🟢 Цена↑ OI↑ — новые лонги, сильный тренд" if funding >= 0 else "🟡 Цена↑ OI↑ — шорт-сквиз возможен"
    if price_up and not oi_up:
        return "🟡 Цена↑ OI↓ — шорт-сквиз, может исчерпаться"
    if not price_up and oi_up:
        return "🔴 Цена↓ OI↑ — новые шорты, реальное давление"
    return "🟡 Цена↓ OI↓ — выход из позиций, движение слабеет"


async def _discover_top_symbols() -> list:
    """Топ-N Binance Futures перпетуалов по 24h объёму через CoinGecko /derivatives
    (Binance REST запрещён — используем ту же точку входа, что и OI/funding в bot.py)."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/derivatives", timeout=15)
        r.raise_for_status()
        rows = [x for x in r.json()
                if x.get("contract_type") == "perpetual" and "Binance" in (x.get("market") or "")
                and x.get("symbol", "").endswith("USDT")]
        rows.sort(key=lambda x: float(x.get("volume_24h") or 0), reverse=True)
        syms = [x["symbol"].lower() for x in rows[:TOP_N_SYMBOLS]]
        return syms or ["btcusdt", "ethusdt", "solusdt"]
    except Exception as e:
        print(f"Pump Radar: symbol discovery failed ({e}), falling back to BTC/ETH/SOL")
        return ["btcusdt", "ethusdt", "solusdt"]


def _ensure_history(symbol: str):
    if symbol not in _volume_history:
        _volume_history[symbol] = deque(maxlen=60 * 24 * WINDOW_DAYS)
    if symbol not in _candle_history:
        _candle_history[symbol] = deque(maxlen=CHART_CANDLES + 10)


def _has_new_dynamic_symbols() -> bool:
    """Есть ли символы, запросившие live-цену (карточка в bot.py), но ещё не в WS-подписке."""
    for sym in live_prices.pending_subscriptions():
        if f"{sym.lower()}usdt" not in _current_symbols:
            return True
    return False


def _merge_dynamic_symbols() -> bool:
    """Добавляет в _current_symbols символы, запросившие live-цену вне топ-N. Возвращает True,
    если список изменился (тогда WS нужно переподключить с новым набором стримов)."""
    global _current_symbols
    added = False
    for sym in live_prices.pending_subscriptions():
        s_l = f"{sym.lower()}usdt"
        if s_l not in _current_symbols:
            _current_symbols.append(s_l)
            added = True
    return added


def compute_zscore(symbol, current_volume):
    hist = _volume_history[symbol]
    if len(hist) < 100:
        return None, None
    mean = statistics.mean(hist)
    stdev = statistics.pstdev(hist) or 1e-9
    z = (current_volume - mean) / stdev
    mult = current_volume / (mean or 1e-9)
    return round(z, 2), round(mult, 2)


def _avg_volume(symbol) -> float:
    hist = _volume_history[symbol]
    return statistics.mean(hist) if hist else 1.0


# ── Чарт ─────────────────────────────────────────────────────────

def _build_chart(symbol: str, watch: dict) -> io.BytesIO:
    candles = list(_candle_history.get(symbol, []))[-CHART_CANDLES:]
    if len(candles) < 5:
        return None

    fig, (ax_p, ax_v) = plt.subplots(2, 1, figsize=(10, 7), facecolor=BG,
                                      gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    for ax in (ax_p, ax_v):
        ax.set_facecolor(BG)
        ax.tick_params(colors=WHITE, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(GRAY)

    xs = list(range(len(candles)))
    avg_vol = statistics.mean([c["v"] for c in candles]) or 1.0
    vol_std = statistics.pstdev([c["v"] for c in candles]) or 1.0

    for i, c in enumerate(candles):
        color = GREEN if c["c"] >= c["o"] else RED
        ax_p.plot([i, i], [c["l"], c["h"]], color=color, linewidth=1)
        ax_p.add_patch(patches.Rectangle((i - 0.3, min(c["o"], c["c"])), 0.6,
                                          max(abs(c["c"] - c["o"]), c["h"]*0.0001),
                                          color=color))
        vol_color = YELLOW if (c["v"] - avg_vol) / vol_std > 3 else (GREEN if c["c"] >= c["o"] else RED)
        ax_v.bar(i, c["v"], color=vol_color, width=0.7)

    # Пик пампа
    peak = watch["peak_price"]
    ax_p.axhline(peak, color=YELLOW, linestyle="--", linewidth=1, label=f"Пик {_fmt_price(peak)}")

    # Зона входа (шорт) и SL/TP линии, если уже посчитаны
    if watch.get("entry_lo") and watch.get("entry_hi"):
        ax_p.axhspan(watch["entry_lo"], watch["entry_hi"], color=RED, alpha=0.15)
    for key, color, lbl in [("sl", RED, "SL"), ("tp1", GREEN, "TP1"), ("tp2", GREEN, "TP2")]:
        if watch.get(key):
            ax_p.axhline(watch[key], color=color, linestyle=":", linewidth=1)
            ax_p.text(len(candles)-1, watch[key], f" {lbl} {_fmt_price(watch[key])}",
                       color=color, fontsize=8, va="center")

    ax_p.set_title(f"{symbol.upper()} · 1m · детект {time.strftime('%H:%M UTC', time.gmtime(watch['pump_time']))}",
                    color=WHITE, fontsize=11, loc="left")
    ax_p.text(0.99, 0.02, "BEST TRADE 👑", color=GRAY, fontsize=9, alpha=0.6,
               ha="right", va="bottom", transform=ax_p.transAxes)
    ax_p.legend(loc="upper left", fontsize=8, facecolor=BG, labelcolor=WHITE, framealpha=0.3)
    ax_v.set_ylabel("Vol", color=GRAY, fontsize=8)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG, dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Композиция алертов ──────────────────────────────────────────

def _risk_block(entry: float, sl: float) -> str:
    risk_pct = abs(sl - entry) / entry * 100 if entry else 0
    lines = ["💰 *Риск на депозит:*"]
    for dep_risk in (1, 2, 3):
        if risk_pct > 0:
            size_pct = dep_risk / risk_pct * 100
            lines.append(f"  {dep_risk}% депозита → размер позиции ~{size_pct:.0f}% от депозита")
    return "\n".join(lines)


async def _compose_alert(ctx: PumpContext, symbol: str, watch: dict, stage_title: str,
                          extra_lines: list) -> str:
    sym = symbol.upper().replace("USDT", "")
    price = watch.get("last_price", watch["peak_price"])
    pct_move = (price - watch["detect_price"]) / watch["detect_price"] * 100 if watch["detect_price"] else 0

    funding = 0.0; oi_now = 0.0; oi_chg = 0.0
    try:
        funding = ctx.get_funding_pct(sym)
        oi_now = ctx.get_oi_usd(sym)
        oi_chg = ctx.get_oi_change(sym)
    except Exception:
        pass

    kz_name = "?"
    try:
        kz = ctx.get_killzone_status()
        kz_name = kz["active"]["name"]
    except Exception:
        pass

    oi_line = _oi_matrix_label(price_up=pct_move > 0, oi_change_pct=oi_chg, funding=funding)

    memecoin_line = ""
    try:
        coin = ctx.get_coin_by_symbol(sym)
        mcap = (coin.get("quote", {}).get("USDT", {}).get("market_cap", 0) or 0) if coin else 0
        if 0 < mcap < MEMECOIN_MCAP_USD:
            memecoin_line = "\n⚠️ *МЕМКОИН* — низкая капитализация, повышенный риск манипуляции"
    except Exception:
        pass

    _, price_age = live_prices.get_live_price(sym)
    price_fresh = live_prices.freshness_label(price_age)

    SEP = "━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"⚡ *ПАМП-РАДАР — {stage_title}*",
        f"*{sym}/USDT*{memecoin_line}",
        SEP, "",
        f"📍 Цена: `{_fmt_price(price)}`  _{price_fresh}_  ({pct_move:+.1f}% от детекта)",
        f"📊 Объём: x{watch.get('volume_mult', 0):.1f} от нормы · Z-Score: {watch.get('z_score', 0):.1f}σ",
        f"📈 Funding: {funding:+.4f}%",
        f"📊 OI: ${oi_now/1e6:.1f}M ({oi_chg:+.1f}% за 5 мин) — {oi_line}",
        f"⏰ Сессия: {kz_name}",
        "",
    ]
    lines.extend(extra_lines)
    lines.append(SEP)
    return "\n".join(lines)


async def _send_alert(ctx: PumpContext, symbol: str, text: str, watch: dict, subscribe_cb_data: str):
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Следить", callback_data=subscribe_cb_data)]])
    except Exception:
        kb = None

    chart = None
    try:
        chart = _build_chart(symbol, watch)
    except Exception as e:
        print(f"Pump Radar: chart build failed for {symbol}: {e}")

    try:
        if chart:
            await ctx.bot.send_photo(ctx.owner_chat_id, photo=chart, caption=text,
                                      parse_mode="Markdown", reply_markup=kb)
        else:
            await ctx.bot.send_message(ctx.owner_chat_id, text, parse_mode="Markdown",
                                        reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        print(f"Pump Radar: send failed: {e}")

    # уведомить подписчиков символа (без карты/кнопки, короткий текст)
    subs = _subscriptions.get(symbol.upper().replace("USDT", ""), set())
    for cid in subs:
        if cid == ctx.owner_chat_id:
            continue
        try:
            await ctx.bot.send_message(cid, text, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            pass


async def _notify_subscribers_zone(ctx: PumpContext, symbol: str, watch: dict, event: str):
    sym = symbol.upper().replace("USDT", "")
    subs = _subscriptions.get(sym, set())
    if not subs:
        return
    text = {
        "entry": f"🔔 *{sym}* — цена вошла в зону входа `{_fmt_price(watch.get('entry_lo',0))}–{_fmt_price(watch.get('entry_hi',0))}`",
        "tp1":   f"🔔 *{sym}* — TP1 достигнут, двигай стоп в безубыток",
        "sl":    f"🔔 *{sym}* — цена у SL-зоны `{_fmt_price(watch.get('sl',0))}`, внимание",
    }.get(event)
    if not text:
        return
    for cid in subs:
        try:
            await ctx.bot.send_message(cid, text, parse_mode="Markdown")
        except Exception:
            pass


# ── Машина состояний ──────────────────────────────────────────────

async def _try_promote(ctx: PumpContext, symbol: str, watch: dict):
    sym = symbol.upper().replace("USDT", "")
    try:
        coin = ctx.get_coin_by_symbol(sym)
        if not coin:
            return
        pa = ctx.pro_analysis(sym, coin)
        entry = watch["entry_lo"] or watch["last_price"]
        sl = watch["sl"]
        tp1 = watch["tp1"]
        rr = abs(entry - tp1) / abs(sl - entry) if sl != entry else 0
        if pa.get("ok") and pa.get("direction") == "short" and pa.get("pro_score", 0) >= PROMOTE_SCORE_THRESHOLD and rr >= PROMOTE_MIN_RR:
            ctx.add_top_short_signal(sym, {
                "time": None, "entry": entry, "tp1": tp1, "tp2": watch["tp2"],
                "sl": sl, "rr": round(rr, 2), "status": "active",
                "note": "⚡ из Памп-радара",
            })
            watch["stage"] = "PROMOTED"
            text = await _compose_alert(ctx, symbol, watch, "PROMOTED ✅",
                                         [f"✅ Добавлено в ТОП ШОРТ (score {pa.get('pro_score',0)}, R:R 1:{rr:.1f})"])
            await _send_alert(ctx, symbol, text, watch, f"pump_sub_{sym}")
    except Exception as e:
        print(f"Pump Radar: promote check {symbol}: {e}")


def _finalize(symbol: str, watch: dict, final_stage: str):
    pump_history.append({"symbol": symbol.upper().replace("USDT",""), "ts": time.time(),
                          "final_stage": final_stage})
    pump_watch.pop(symbol, None)


async def handle_kline(ctx: PumpContext, symbol: str, kline: dict):
    _ensure_history(symbol)
    is_closed = kline.get("x", False)
    close = float(kline["c"]); open_ = float(kline["o"])
    high = float(kline["h"]); low = float(kline["l"])
    volume = float(kline["v"])

    # Live-цена — на каждый тик, а не только на закрытии свечи (kline "c" обновляется
    # ~раз в секунду для текущей формирующейся свечи задолго до её закрытия).
    live_prices.update_price(symbol, close)

    watch = pump_watch.get(symbol)
    if watch:
        watch["last_price"] = close

    if not is_closed:
        if watch:
            await _check_subscriber_zones(ctx, symbol, watch)
        return

    _candle_history[symbol].append({"t": kline.get("t", 0), "o": open_, "h": high, "l": low, "c": close, "v": volume})

    z, mult = compute_zscore(symbol, volume)
    avg_vol_before = _avg_volume(symbol)
    _volume_history[symbol].append(volume)

    now = time.time()

    # --- Нет активного наблюдения: проверить триггер PUMP_DETECTED ---
    if not watch:
        if z is None or z <= Z_SCORE_THRESHOLD or mult <= VOLUME_MULT_THRESHOLD:
            return
        if now - _last_alert_ts.get(symbol, 0) < ALERT_COOLDOWN_SEC:
            return
        _last_alert_ts[symbol] = now
        watch = {
            "stage": "WATCHING", "peak_price": close, "detect_price": close, "last_price": close,
            "pump_time": now, "z_score": z, "volume_mult": mult,
            "red_streak": 0, "entry_lo": None, "entry_hi": None, "sl": None, "tp1": None, "tp2": None,
        }
        pump_watch[symbol] = watch
        text = await _compose_alert(ctx, symbol, watch, "PUMP DETECTED 🚀",
                                     ["🎯 Сценарий: возможен шорт после разворота",
                                      "⏳ Наблюдаю за откатом до 30 минут..."])
        await _send_alert(ctx, symbol, text, watch, f"pump_sub_{symbol.upper().replace('USDT','')}")
        return

    # --- Есть активное наблюдение ---
    if watch["stage"] in ("PUMP_DETECTED", "WATCHING"):
        if close > watch["peak_price"]:
            watch["peak_price"] = close
            watch["red_streak"] = 0

        watch["red_streak"] = watch["red_streak"] + 1 if close < open_ else 0
        drawdown = (watch["peak_price"] - close) / watch["peak_price"] * 100 if watch["peak_price"] else 0

        reversal = (drawdown >= REVERSAL_DRAWDOWN_PCT
                    and watch["red_streak"] >= REVERSAL_RED_STREAK
                    and volume >= REVERSAL_VOL_MULT * avg_vol_before)

        if reversal:
            watch["stage"] = "REVERSAL_CONFIRMED"
            peak = watch["peak_price"]
            watch["sl"] = round(peak * (1 + SL_BUFFER_PCT/100), 8)
            watch["entry_hi"] = peak * 0.995
            watch["entry_lo"] = close
            watch["tp1"] = close * 0.97
            watch["tp2"] = close * 0.94
            text = await _compose_alert(ctx, symbol, watch, "REVERSAL CONFIRMED 🔻",
                                         [f"🎯 Зона входа (шорт): `{_fmt_price(watch['entry_lo'])}–{_fmt_price(watch['entry_hi'])}`",
                                          f"🛑 SL: `{_fmt_price(watch['sl'])}` (пик +{SL_BUFFER_PCT}%)",
                                          f"🎯 TP1: `{_fmt_price(watch['tp1'])}`  TP2: `{_fmt_price(watch['tp2'])}`",
                                          _risk_block(watch["entry_lo"], watch["sl"]),
                                          "",
                                          "🛡 *Position Protection:* если уже в позиции — частичная фиксация на TP1, "
                                          "трейлинг-стоп в безубыток после TP1."])
            await _send_alert(ctx, symbol, text, watch, f"pump_sub_{symbol.upper().replace('USDT','')}")
            await _try_promote(ctx, symbol, watch)
            return

        if now - watch["pump_time"] > WATCH_TIMEOUT_SEC:
            _finalize(symbol, watch, "EXPIRED")
            return


async def _check_subscriber_zones(ctx: PumpContext, symbol: str, watch: dict):
    if watch["stage"] != "REVERSAL_CONFIRMED":
        return
    price = watch["last_price"]
    if watch.get("entry_lo") and watch.get("entry_hi") and watch["entry_lo"] <= price <= watch["entry_hi"] \
            and not watch.get("_notified_entry"):
        watch["_notified_entry"] = True
        await _notify_subscribers_zone(ctx, symbol, watch, "entry")
    if watch.get("tp1") and price <= watch["tp1"] and not watch.get("_notified_tp1"):
        watch["_notified_tp1"] = True
        await _notify_subscribers_zone(ctx, symbol, watch, "tp1")
    if watch.get("sl") and price >= watch["sl"] * 0.998 and not watch.get("_notified_sl"):
        watch["_notified_sl"] = True
        await _notify_subscribers_zone(ctx, symbol, watch, "sl")


# ── Точка входа ────────────────────────────────────────────────

async def run_pump_detector(bot, owner_chat_id, get_coin_by_symbol, full_analysis,
                             pro_analysis=None, get_killzone_status=None, get_funding_pct=None,
                             get_oi_usd=None, get_oi_change=None, add_top_short_signal=None):
    """Точка входа для asyncio.create_task() из bot.py."""
    global _current_symbols, _symbols_ts

    ctx = PumpContext(bot, owner_chat_id, get_coin_by_symbol, full_analysis, pro_analysis,
                       get_killzone_status, get_funding_pct, get_oi_usd, get_oi_change,
                       add_top_short_signal)

    _current_symbols = await _discover_top_symbols()
    _merge_dynamic_symbols()
    _symbols_ts = time.time()
    for s in _current_symbols:
        _ensure_history(s)
    print(f"Pump Radar: подключение к {len(_current_symbols)} символам (top-{TOP_N_SYMBOLS} по объёму)")

    while True:
        if time.time() - _symbols_ts > SYMBOL_REFRESH_SEC:
            try:
                new_syms = await _discover_top_symbols()
                if new_syms:
                    _current_symbols = new_syms
            except Exception as e:
                print(f"Pump Radar: symbol refresh failed: {e}")
            _symbols_ts = time.time()

        if _merge_dynamic_symbols():
            for s in _current_symbols:
                _ensure_history(s)
            print(f"Pump Radar: список символов обновлён ({len(_current_symbols)}, live-цены/динамические подписки)")

        ws_url = "wss://fstream.binance.com/stream?streams=" + "/".join(
            f"{s}@kline_{CANDLE_INTERVAL}" for s in _current_symbols)
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                print("Pump Radar: соединение установлено")
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        # каждые 30с проверяем: не запросили ли карточку по символу вне текущей
                        # подписки (live_prices.request_subscription) — если да, переподключаемся
                        # с расширенным списком, иначе просто продолжаем слушать.
                        if _has_new_dynamic_symbols():
                            break
                        continue
                    payload = json.loads(message)
                    stream_data = payload.get("data", {})
                    symbol = stream_data.get("s", "").lower()
                    kline = stream_data.get("k")
                    if symbol and kline:
                        await handle_kline(ctx, symbol, kline)
                    if _has_new_dynamic_symbols():
                        break  # реагируем на динамический запрос подписки без ожидания таймаута
        except Exception as e:
            print(f"Pump Radar: соединение разорвано ({e}), переподключение через 5 сек")
            await asyncio.sleep(5)

        # чистим протухшие наблюдения между переподключениями
        now = time.time()
        for sym in list(pump_watch.keys()):
            w = pump_watch[sym]
            if w["stage"] in ("PUMP_DETECTED", "WATCHING") and now - w["pump_time"] > WATCH_TIMEOUT_SEC:
                _finalize(sym, w, "EXPIRED")
