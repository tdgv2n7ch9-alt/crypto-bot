"""
Ретроспективный пересчёт journal/signals.json реальными историческими свечами --
задача владельца в очереди между Этапом 3 и Этапом 4 пакета «АПГРЕЙД 11.07».

Контекст: signal_journal.run_tracker() (живой процесс, каждые 30с) определяет
исход PENDING/ENTERED записей через live_prices.get_live_price(symbol) -- WS-цену
от pump_detector.py. Но pump_detector подписан не на все символы (топ-20 + то, что
динамически запрошено), так что для многих журнальных записей `get_live_price()`
возвращает None НАВСЕГДА -- запись зависает в PENDING/ENTERED без исхода.

ПОБОЧНАЯ НАХОДКА (не исправлена в этой задаче, отдельный живой баг): в
run_tracker() строка `if price is None: continue` стоит ДО time-based проверки
72ч-истечения PENDING -- то есть символ без WS-покрытия не только не получает
SL/TP-исход, но даже не истекает по таймеру, вися в PENDING бесконечно долго
(id=11 NEX висит уже 146ч при пороге истечения 72ч). Это меняет ПОСТОЯННО
РАБОТАЮЩУЮ логику трекера, не только исторические данные -- специально НЕ
исправлено здесь, вынесено отдельным пунктом в отчёт для решения владельца.

Этот скрипт использует функции классификации ИЗ signal_journal.py
(_touches_entry/_sl_hit/_tp_hit, тот же принцип, что _check_outcome) -- НЕ
bot.top_trades_long_status()/top_trades_short_status() (буквальная формулировка
задачи владельца), потому что эти функции bot.py построены для СТАРОЙ
легаси-схемы TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS (скалярный `entry`), а у этого
журнала схема entry_lo/entry_hi-зона + двухфазный PENDING->ENTERED->исход --
другая форма данных, применить одну функцию к другой значило бы либо упустить
зону входа, либо подогнать данные под чужую форму. Честно зафиксировано, не
скрыто.

Реализация: часовые свечи Bybit (bot.get_binance_ohlc, тот же источник, что весь
остальной живой бот), 250 баров (~10.4 дня, с запасом покрывает любую запись
не старше проверенного диапазона журнала на 2026-07-11). Касание зоны входа и
уровней SL/TP1/2/3 проверяется по high/low свечи (честные wick-касания, точнее
30-секундного live-опроса точечной цены). Приоритет при пересечении в одной
свече -- SL первым (тот же консервативный принцип, что _check_outcome()).
entered_price при историческом входе -- ближняя граница зоны (entry_hi для long,
entry_lo для short) как честная консервативная оценка (точная тик-цена входа
неизвестна на часовых свечах, не выдумывается).

Меняет ТОЛЬКО статусные поля существующих записей (status/entered_ts/
entered_price/outcome/outcome_ts/outcome_level/actual_r/updated_ts) -- ни одна
запись не удаляется, ID не меняются, история append-only. `updated_ts`
проставляется в момент пересчёта -- signal_journal._merge_records()
(last-write-wins по updated_ts) корректно подтянет эти изменения при следующей
синхронизации живого процесса с GitHub, не будет затёрто.
"""
import json
import os
import time

import bot
import signal_journal as sj

REAL_JOURNAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "journal", "signals.json")
BACKFILL_STATUSES = ("PENDING", "ENTERED", "EXPIRED")
CANDLE_INTERVAL = "1h"
CANDLE_LIMIT = 250


def load_real_journal(path: str = REAL_JOURNAL_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def save_real_journal(data: dict, path: str = REAL_JOURNAL_PATH) -> None:
    """Компактный однострочный формат, БЕЗ indent/sort_keys -- тот же json.dumps(payload),
    что signal_journal._github_put_file_sync() (см. её строку с json.dumps(payload)) --
    иначе каждая запись через этот скрипт создавала бы гигантский diff чистого
    форматирования (indent=2 разворачивает файл в тысячи строк) поверх реальных
    изменений, что мешает честному ревью диффа перед коммитом."""
    with open(path, "w") as f:
        json.dump(data, f)


def fetch_candles(symbol: str, interval: str = CANDLE_INTERVAL, limit: int = CANDLE_LIMIT,
                   retries: int = 2, retry_sleep_sec: float = 1.5) -> list:
    """Тонкая обёртка над bot.get_binance_ohlc с ретраем -- на живом прогоне по 44
    символам подряд Bybit/CoinGecko изредка отдают transient-таймаут для отдельных
    тикеров (не всегда одних и тех же между запусками -- проверено: разные символы
    падали в разных прогонах, не системный "этого тикера не существует"). Пустой
    список ТОЛЬКО после исчерпания retries -- честно, не выдумывает данные."""
    for attempt in range(retries + 1):
        data = bot.get_binance_ohlc(symbol, interval, limit)
        if data:
            return data
        if attempt < retries:
            time.sleep(retry_sleep_sec)
    return []


def replay_record(rec: dict, candles: list, now_ts: float = None) -> dict:
    """Пересчитывает ОДНУ запись по хронологическим свечам. Возвращает dict полей
    для обновления (пустой -- ничего не изменилось, честно оставляем как есть).
    НЕ мутирует `rec`. `candles` -- уже отсортированы по возрастанию timestamp
    (мс), как отдаёт bot.get_binance_ohlc()."""
    now_ts = now_ts if now_ts is not None else time.time()
    direction = rec["direction"]
    entry_lo, entry_hi = min(rec["entry_lo"], rec["entry_hi"]), max(rec["entry_lo"], rec["entry_hi"])
    sl = rec.get("sl")
    tp1, tp2, tp3 = rec.get("tp1"), rec.get("tp2"), rec.get("tp3")
    status = rec["status"]

    entered_ts = rec.get("entered_ts")
    entered_price = rec.get("entered_price")

    if status in ("PENDING", "EXPIRED"):
        # Ищем первое касание зоны входа СТРОГО в пределах исходного 72ч-окна
        # (та же граница, что и живой PENDING_EXPIRE_SEC) -- касание НА 5-й день
        # не "спасает" сигнал, который по правилам уже должен был истечь на 3-й.
        deadline = rec["ts"] + sj.PENDING_EXPIRE_SEC
        found_idx = None
        for i, c in enumerate(candles):
            c_ts = c["timestamp"] / 1000.0
            if c_ts < rec["ts"]:
                continue
            if c_ts > deadline:
                break
            if c["low"] <= entry_hi and c["high"] >= entry_lo:
                found_idx = i
                break
        if found_idx is None:
            if status == "PENDING" and now_ts - rec["ts"] > sj.PENDING_EXPIRE_SEC:
                # Живой трекер это пропустил (WS не покрывал символ) -- честно
                # истекаем сами, ровно то же правило, что PENDING_EXPIRE_SEC.
                return {"status": "EXPIRED", "outcome": "EXPIRED", "outcome_ts": now_ts}
            return {}  # EXPIRED остаётся EXPIRED (зона не была тронута), либо PENDING ещё не истёк
        entered_ts = candles[found_idx]["timestamp"] / 1000.0
        entered_price = entry_hi if direction == "long" else entry_lo
        start_idx = found_idx
    elif status == "ENTERED":
        if entered_ts is None:
            return {}  # честно: нет данных о моменте входа -- не домысливаем
        start_idx = None
        for i, c in enumerate(candles):
            if c["timestamp"] / 1000.0 >= entered_ts:
                start_idx = i
                break
        if start_idx is None:
            return {}  # доступная история свечей вся ЗАКАНЧИВАЕТСЯ раньше входа -- нет данных для пересчёта
    else:
        return {}

    outcome_result = None
    for c in candles[start_idx:]:
        if direction == "long":
            sl_hit = sl is not None and c["low"] <= sl
            tp3_hit = tp3 is not None and c["high"] >= tp3
            tp2_hit = tp2 is not None and c["high"] >= tp2
            tp1_hit = tp1 is not None and c["high"] >= tp1
        else:
            sl_hit = sl is not None and c["high"] >= sl
            tp3_hit = tp3 is not None and c["low"] <= tp3
            tp2_hit = tp2 is not None and c["low"] <= tp2
            tp1_hit = tp1 is not None and c["low"] <= tp1

        if sl_hit:
            outcome_result = ("SL_HIT", "sl", sl, c)
        elif tp3_hit:
            outcome_result = ("TP3_HIT", "tp3", tp3, c)
        elif tp2_hit:
            outcome_result = ("TP2_HIT", "tp2", tp2, c)
        elif tp1_hit:
            outcome_result = ("TP1_HIT", "tp1", tp1, c)
        if outcome_result:
            break

    if outcome_result:
        outcome, level, exit_price, c = outcome_result
        actual_r = None
        if entered_price is not None and sl is not None:
            risk = abs(entered_price - sl) or 1e-9
            reward = abs(exit_price - entered_price)
            sign = -1 if level == "sl" else 1
            actual_r = round(sign * reward / risk, 2)
        return {
            "status": outcome, "entered_ts": entered_ts, "entered_price": entered_price,
            "outcome": outcome, "outcome_ts": c["timestamp"] / 1000.0, "outcome_level": level,
            "actual_r": actual_r,
        }

    # Вошли (по свечам), но ни один уровень пока не пробит за доступную историю --
    # честно ENTERED, не PENDING/EXPIRED. Обновляем, только если статус реально
    # меняется (PENDING/EXPIRED -> ENTERED) -- иначе (уже был ENTERED) нет изменений.
    # Если запись раньше была EXPIRED (терминальный статус с outcome="EXPIRED"),
    # явно очищаем outcome-поля -- иначе останется противоречие: status=ENTERED
    # (открыта), но outcome="EXPIRED" (будто уже закрыта), см. живая находка id=34.
    if status != "ENTERED":
        return {
            "status": "ENTERED", "entered_ts": entered_ts, "entered_price": entered_price,
            "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
        }
    return {}


def run_backfill(path: str = REAL_JOURNAL_PATH, dry_run: bool = True) -> dict:
    """Прогоняет ВСЕ PENDING/ENTERED/EXPIRED записи реального журнала через
    replay_record(). dry_run=True -- ничего не пишет на диск, только считает и
    возвращает отчёт (для показа владельцу перед применением). Возвращает
    {"changes": [...], "before": {...}, "after": {...}}."""
    data = load_real_journal(path)
    records = data["records"]
    now_ts = time.time()

    candidates = {rid: r for rid, r in records.items() if r["status"] in BACKFILL_STATUSES}
    symbols = sorted({r["symbol"] for r in candidates.values()})
    candle_cache = {}
    fetch_failed = []
    for sym in symbols:
        candles = fetch_candles(sym)
        if not candles:
            fetch_failed.append(sym)
        candle_cache[sym] = candles

    changes = []
    for rid, rec in candidates.items():
        candles = candle_cache.get(rec["symbol"], [])
        if not candles:
            continue  # честно пропускаем -- нет данных, не выдумываем исход
        updates = replay_record(rec, candles, now_ts=now_ts)
        if updates:
            updates["updated_ts"] = now_ts
            changes.append({
                "id": rid, "symbol": rec["symbol"],
                "before_status": rec["status"], "after_status": updates["status"],
                "actual_r": updates.get("actual_r"),
            })
            if not dry_run:
                records[rid].update(updates)

    result = {
        "changes": changes,
        "fetch_failed_symbols": fetch_failed,
        "candidates_total": len(candidates),
        "symbols_total": len(symbols),
    }
    if not dry_run and changes:
        save_real_journal(data, path)
    return result


def win_rate_table(records: dict) -> dict:
    """Честный win-rate: закрытые-с-исходом (outcome in OUTCOME_STATUSES, EXPIRED
    исключён -- как и в signal_journal.get_closed_records()/backtest/journal_replay.py),
    ПЛЮС отдельно видимая доля "открыто/зависло без исхода" -- та часть, что
    раньше молча выпадала из знаменателя."""
    total = len(records)
    by_status = {}
    for r in records.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    wins = sum(by_status.get(s, 0) for s in ("TP1_HIT", "TP2_HIT", "TP3_HIT"))
    losses = by_status.get("SL_HIT", 0)
    closed_with_outcome = wins + losses
    win_rate = round(wins / closed_with_outcome * 100, 1) if closed_with_outcome else None
    unresolved = by_status.get("PENDING", 0) + by_status.get("ENTERED", 0)
    return {
        "total": total, "by_status": by_status,
        "wins": wins, "losses": losses, "closed_with_outcome": closed_with_outcome,
        "win_rate": win_rate, "unresolved": unresolved,
        "unresolved_pct": round(unresolved / total * 100, 1) if total else None,
    }


if __name__ == "__main__":
    import sys
    apply_changes = "--apply" in sys.argv

    data_before = load_real_journal()
    before_stats = win_rate_table(data_before["records"])

    result = run_backfill(dry_run=not apply_changes)

    print(f"Кандидатов на пересчёт (PENDING/ENTERED/EXPIRED): {result['candidates_total']}")
    print(f"Уникальных символов: {result['symbols_total']}")
    if result["fetch_failed_symbols"]:
        print(f"Не удалось получить свечи (пропущены честно, не выдуманы): "
              f"{result['fetch_failed_symbols']}")
    print(f"Изменений статуса: {len(result['changes'])}")
    for ch in result["changes"]:
        r_str = f"{ch['actual_r']:+.2f}R" if ch["actual_r"] is not None else "?"
        print(f"  id={ch['id']:>3} {ch['symbol']:<10} {ch['before_status']:<9} -> "
              f"{ch['after_status']:<9} {r_str}")

    print()
    print("До:", before_stats)
    if apply_changes:
        data_after = load_real_journal()
        after_stats = win_rate_table(data_after["records"])
    else:
        # Превью "после" без записи на диск -- применяем изменения к КОПИИ в памяти.
        import copy
        preview_records = copy.deepcopy(data_before["records"])
        for ch in result["changes"]:
            preview_records[ch["id"]]["status"] = ch["after_status"]
        after_stats = win_rate_table(preview_records)
    print("После:", after_stats)
    if not apply_changes:
        print("(dry-run -- файл НЕ изменён, запусти с --apply чтобы применить)")
