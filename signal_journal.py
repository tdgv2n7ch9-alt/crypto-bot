"""
BEST TRADE — Signal Journal (paper-trading трекер)

Логирует каждый сгенерированный сигнал (ТОП ЛОНГ/ШОРТ/СПОТ, x100, Памп-радар) и
отслеживает его реальную отработку через live_prices (PENDING -> ENTERED ->
TPx_HIT/SL_HIT, либо EXPIRED без входа за 72ч) — только наблюдение, никакого влияния
на генерацию сигналов.

Хранение: JSON-файл в рабочей директории (Railway ephemeral — при редеплое история
обнуляется, это принято) + in-memory. Каждая запись несёт schema_version для будущей
миграции формата.
"""

import asyncio
import json
import os
import time
from datetime import datetime

import pytz

import live_prices

SCHEMA_VERSION = 1
JOURNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_journal.json")
TZ = pytz.timezone("Europe/Istanbul")   # UTC+3, как остальной бот

PENDING_EXPIRE_SEC = 72 * 3600     # 72ч без входа в зону -- EXPIRED
TRACK_INTERVAL_SEC = 30

TERMINAL_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "EXPIRED"}
OUTCOME_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"}  # исходы после входа (не EXPIRED)

_journal = {}      # id (int) -> record dict
_next_id = 1
_bot = None
_owner_chat_id = None


def init(bot, owner_chat_id):
    """Вызывается один раз при старте бота — нужен для owner-уведомлений об исходах."""
    global _bot, _owner_chat_id
    _bot = bot
    _owner_chat_id = owner_chat_id
    _load()


def _load():
    global _journal, _next_id
    if not os.path.exists(JOURNAL_FILE):
        return
    try:
        with open(JOURNAL_FILE, "r") as f:
            data = json.load(f)
        _journal = {int(k): v for k, v in data.get("records", {}).items()}
        _next_id = data.get("next_id", 1)
    except Exception as e:
        print(f"Signal Journal: load failed ({e}), starting fresh")
        _journal = {}
        _next_id = 1


def _save():
    try:
        with open(JOURNAL_FILE, "w") as f:
            json.dump({"schema_version": SCHEMA_VERSION, "next_id": _next_id,
                       "records": _journal}, f)
    except Exception as e:
        print(f"Signal Journal: save failed ({e})")


def log_signal(source: str, symbol: str, direction: str, price_at_signal: float,
               entry_lo: float, entry_hi: float, sl: float,
               tp1: float = None, tp2: float = None, tp3: float = None,
               rr: float = None, rocket_score=None,
               ema_stack=None, sweep=None) -> int:
    """Логирует новый сигнал, статус PENDING. direction: "long"/"short". Для скалярного
    входа (не зоны) передать одно и то же значение в entry_lo и entry_hi. Только
    наблюдение — вызывается ПОСЛЕ уже принятого решения сгенерировать сигнал, не влияет
    на него.

    ema_stack: снимок ta_extra.ema_context() на момент сигнала (или None), sweep: снимок
    ta_extra.detect_sweep() -- какой из них был актуален на момент сигнала (или None).
    Хранятся как есть (просто для последующего статистического анализа "улучшают ли эти
    факторы win rate" — сама отработка сигнала их не использует)."""
    global _next_id
    rec_id = _next_id
    _next_id += 1
    now = time.time()
    rec = {
        "id": rec_id, "schema_version": SCHEMA_VERSION,
        "ts": now, "timestamp": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": source, "symbol": symbol.upper().replace("USDT", ""),
        "direction": direction,
        "entry_lo": entry_lo, "entry_hi": entry_hi, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr": rr, "rocket_score": rocket_score,
        "ema_stack": ema_stack, "sweep": sweep,
        "price_at_signal": price_at_signal,
        "status": "PENDING",
        "entered_ts": None, "entered_price": None,
        "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
    }
    _journal[rec_id] = rec
    _save()
    return rec_id


def _touches_entry(price, entry_lo, entry_hi):
    lo, hi = (entry_lo, entry_hi) if entry_lo <= entry_hi else (entry_hi, entry_lo)
    return lo <= price <= hi


def _sl_hit(direction, price, sl):
    if sl is None:
        return False
    return price <= sl if direction == "long" else price >= sl


def _tp_hit(direction, price, tp):
    if tp is None:
        return False
    return price >= tp if direction == "long" else price <= tp


def _check_outcome(direction, price, sl, tp1, tp2, tp3):
    """Первый достигнутый уровень с момента входа: (status, level) либо (None, None).
    SL приоритетнее (консервативно, честная статистика), иначе берём САМЫЙ дальний
    реально достигнутый TP -- при 30-секундном опросе, если цена уже на TP2, значит
    прошла и TP1, публикуем лучший фактически достигнутый уровень."""
    if _sl_hit(direction, price, sl):
        return "SL_HIT", "sl"
    if _tp_hit(direction, price, tp3):
        return "TP3_HIT", "tp3"
    if _tp_hit(direction, price, tp2):
        return "TP2_HIT", "tp2"
    if _tp_hit(direction, price, tp1):
        return "TP1_HIT", "tp1"
    return None, None


def _compute_actual_r(rec, level):
    entered_price = rec.get("entered_price")
    sl = rec.get("sl")
    if entered_price is None or sl is None:
        return None
    risk = abs(entered_price - sl) or 1e-9
    if level == "sl":
        exit_price, sign = sl, -1
    else:
        exit_price = rec.get(level)
        if exit_price is None:
            return None
        sign = 1
    reward = abs(exit_price - entered_price)
    return round(sign * reward / risk, 2)


async def _notify_outcome(rec):
    if _bot is None or _owner_chat_id is None:
        return
    if rec.get("entered_ts") and rec.get("outcome_ts"):
        mins = (rec["outcome_ts"] - rec["entered_ts"]) / 60
        time_str = f"{mins:.0f}мин" if mins < 60 else f"{mins/60:.1f}ч"
    else:
        time_str = "?"
    r_str = f"{rec['actual_r']:+.2f}R" if rec.get("actual_r") is not None else "?"
    text = f"{rec['symbol']} | {rec['source']} | {rec['outcome']} | {r_str} | {time_str} от входа"
    try:
        await _bot.send_message(_owner_chat_id, text)
    except Exception as e:
        print(f"Signal Journal: не удалось отправить уведомление об исходе: {e}")


async def run_tracker():
    """Каждые 30с сверяет активные записи с live_prices, обновляет статус. Только
    наблюдение -- не влияет на генерацию сигналов."""
    while True:
        now = time.time()
        changed = False
        for rec in list(_journal.values()):
            if rec["status"] in TERMINAL_STATUSES:
                continue
            price, _age = live_prices.get_live_price(rec["symbol"])
            if price is None:
                continue

            if rec["status"] == "PENDING":
                if _touches_entry(price, rec["entry_lo"], rec["entry_hi"]):
                    rec["status"] = "ENTERED"
                    rec["entered_ts"] = now
                    rec["entered_price"] = price
                    changed = True
                elif now - rec["ts"] > PENDING_EXPIRE_SEC:
                    rec["status"] = "EXPIRED"
                    rec["outcome"] = "EXPIRED"
                    rec["outcome_ts"] = now
                    changed = True

            elif rec["status"] == "ENTERED":
                status, level = _check_outcome(rec["direction"], price, rec["sl"],
                                                rec["tp1"], rec["tp2"], rec["tp3"])
                if status:
                    rec["status"] = status
                    rec["outcome"] = status
                    rec["outcome_level"] = level
                    rec["outcome_ts"] = now
                    rec["actual_r"] = _compute_actual_r(rec, level)
                    changed = True
                    await _notify_outcome(rec)

        if changed:
            _save()
        await asyncio.sleep(TRACK_INTERVAL_SEC)


def get_status_counts():
    """Для /radar_status: (активных, закрытых)."""
    active = sum(1 for r in _journal.values() if r["status"] not in TERMINAL_STATUSES)
    closed = sum(1 for r in _journal.values() if r["status"] in TERMINAL_STATUSES)
    return active, closed


def get_journal_summary(window_sec=None) -> dict:
    """Сводка для /journal. window_sec=None -- за всё время."""
    now = time.time()
    recs = list(_journal.values())
    if window_sec is not None:
        recs = [r for r in recs if now - r["ts"] <= window_sec]

    total = len(recs)
    entered = [r for r in recs if r.get("entered_ts") is not None]
    closed_with_outcome = [r for r in recs if r.get("outcome") in OUTCOME_STATUSES]
    wins = [r for r in closed_with_outcome if r["outcome"] != "SL_HIT"]
    losses = [r for r in closed_with_outcome if r["outcome"] == "SL_HIT"]

    entered_pct = round(len(entered) / total * 100, 1) if total else None
    win_rate = round(len(wins) / len(closed_with_outcome) * 100, 1) if closed_with_outcome else None
    r_values = [r["actual_r"] for r in closed_with_outcome if r.get("actual_r") is not None]
    avg_r = round(sum(r_values) / len(r_values), 2) if r_values else None

    by_source = {}
    for r in recs:
        s = r["source"]
        agg = by_source.setdefault(s, {"total": 0, "wins": 0, "losses": 0})
        agg["total"] += 1
        if r.get("outcome") == "SL_HIT":
            agg["losses"] += 1
        elif r.get("outcome") in ("TP1_HIT", "TP2_HIT", "TP3_HIT"):
            agg["wins"] += 1

    return {
        "total": total, "entered_count": len(entered), "entered_pct": entered_pct,
        "win_rate": win_rate, "avg_r": avg_r,
        "wins": len(wins), "losses": len(losses),
        "by_source": by_source,
    }
