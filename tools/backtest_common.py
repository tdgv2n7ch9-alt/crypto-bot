"""
Пакет 17 (владелец, ночной пакет, 2026-07-13/14) -- общая инфраструктура для Ф1-Ф3.
bot.py НЕ трогается (владелец: "офлайн, bot.py не трогать") -- везде, где нужна
логика из bot.py, которую нельзя параметризовать без правки bot.py (killzone-часы
по историческому моменту времени), логика ЧЕСТНО ДУБЛИРУЕТСЯ read-only с явной
ссылкой на источник, а не патчится в самом bot.py.

Переиспользует существующую инфраструктуру ночной сессии #3 (backtest/engine.py,
backtest/download_history.py, backtest/historical_report.py, whale_radar.py) --
не переписывает её. Единственное новое: 72-часовое окно симуляции (владелец
явно попросил "TP/SL/72ч") -- отличается от backtest/engine.py::simulate_execution()
(MAX_HOLD_HOURS=14 дней, боевой дефолт с ночной сессии #3), честно
задокументировано как сознательное расхождение для этого пакета.

Честная находка ДО написания кода: владелец сослался на shadow_outcome_analysis.py
как источник правил симуляции ("форвардный проход цены, TP/SL/72ч") -- этот файл
на деле НЕ содержит форвардной симуляции по свечам вообще (только join
shadow_signals.json <-> signals.json по журналу). Ближайшая реальная реализация
72-часового окна в репозитории -- backtest/journal_backfill.py::replay_record(),
но там 72ч -- дедлайн ТОЛЬКО для входа (PENDING), не для резолюции TP/SL после
входа (после входа окно НЕ ограничено). Правило ниже -- НОВОЕ, написанное с нуля
под явную формулировку владельца (72ч -- полное окно удержания сделки от входа до
TP/SL/EXPIRED), не переиспользование existing функции под этим именем.
"""
import gzip
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest.download_history as dl
import backtest.engine as eng

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_CACHE_DIR = os.path.join(REPO_ROOT, "output", "backtest_cache")

MAX_HOLD_HOURS_72 = 72   # владелец, Пакет 17: "TP/SL/72ч" -- явно отличается от
                          # backtest/engine.py::MAX_HOLD_HOURS (14 дней, боевой
                          # дефолт ночной сессии #3), сознательно для этого пакета.


# ── Символы: топ-N Bybit по объёму (переиспользует whale_radar.py) ─────────

def fetch_top_symbols_uppercase(n: int) -> list:
    """whale_radar.fetch_top_symbols() отдаёт lowercase С суффиксом "usdt"
    (живая проверка: 'btcusdt', не 'btc' -- её собственная конвенция для Whale
    Radar) -- backtest/data/ и весь backtest/engine.py используют UPPERCASE
    имена БЕЗ суффикса (см. HistoricalStore._load, download_history.py:
    `f"{symbol.upper()}USDT"` сам добавляет суффикс при запросе к Bybit) --
    приводим на границе: верхний регистр + срез суффикса "USDT"."""
    import whale_radar
    syms = whale_radar.fetch_top_symbols(n=n)
    out = set()
    for s in syms:
        u = s.upper()
        if u.endswith("USDT"):
            u = u[:-4]
        if u:
            out.add(u)
    return sorted(out)


# ── Кэш: backtest/data/ (канонический, чекпоинт-резюмируемый) + зеркало в
# output/backtest_cache/ (владелец явно попросил этот путь для Пакета 17) ──

def ensure_symbols_cached(symbols: list, intervals=("1h", "4h", "1d"), log=print) -> dict:
    """Для каждого (symbol, interval): если уже есть в backtest/data/ (канонический
    чекпоинт-резюмируемый кэш, см. download_history.py, на момент Пакета 17 --
    200 символов/610 файлов от ночной сессии #3) -- НЕ перекачивает повторно
    (владелец: "чтобы повторные прогоны не тянули заново"). Только для
    ОТСУТСТВУЮЩИХ пар вызывает download_history.download_symbol_interval()
    (пейсинг RATE_LIMIT_SEC=0.15с уже встроен в саму функцию). После --
    зеркалирует ИСПОЛЬЗУЕМОЕ этим прогоном подмножество файлов в
    output/backtest_cache/ (путь, явно запрошенный владельцем для этого пакета).
    Возвращает {"downloaded": [...], "reused": [...], "missing": [...]}."""
    os.makedirs(dl.DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_CACHE_DIR, exist_ok=True)
    cp = dl._load_checkpoint()

    downloaded, reused, missing = [], [], []
    for symbol in symbols:
        cp.setdefault(symbol, {})
        for interval in intervals:
            path = os.path.join(dl.DATA_DIR, f"{symbol}_{interval}.csv.gz")
            if os.path.exists(path) and os.path.getsize(path) > 0:
                reused.append((symbol, interval))
            else:
                try:
                    stats = dl.download_symbol_interval(symbol, interval)
                except Exception as e:
                    log(f"[CACHE] {symbol}/{interval} download FAILED: {type(e).__name__}: {e}")
                    stats = {"bars": 0, "first_ts": None, "last_ts": None, "error": str(e)}
                cp[symbol][interval] = stats
                dl._save_checkpoint(cp)
                if stats.get("bars", 0) > 0:
                    downloaded.append((symbol, interval))
                else:
                    missing.append((symbol, interval))

            src = os.path.join(dl.DATA_DIR, f"{symbol}_{interval}.csv.gz")
            if os.path.exists(src):
                dst = os.path.join(OUTPUT_CACHE_DIR, f"{symbol}_{interval}.csv.gz")
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)

    log(f"[CACHE] переиспользовано {len(reused)} пар, докачано {len(downloaded)}, "
        f"недоступно {len(missing)} из {len(symbols) * len(intervals)}")
    return {"downloaded": downloaded, "reused": reused, "missing": missing}


# ── Killzone на исторический момент (bot.py НЕ трогается -- честная дублика-
# ция read-only логики bot.get_killzone_status(), см. докстринг модуля) ────

_KZ_ZONES_TEMPLATE = [
    {"name": "🌏 Азиатская сессия", "start": 0 * 60, "end": 8 * 60, "quality": "B"},
    {"name": "🇬🇧 Лондон Open", "start": 9 * 60, "end": 12 * 60, "quality": "A+"},
    {"name": "🇺🇸 NY Open", "start": 14 * 60, "end": 17 * 60, "quality": "A"},
    {"name": "🇬🇧 Лондон Close", "start": 17 * 60, "end": 19 * 60, "quality": "B"},
    {"name": "🇺🇸 NY Close", "start": 23 * 60, "end": 24 * 60, "quality": "C"},
]

TZ_UTC3 = timezone(timedelta(hours=3))


def killzone_status_at(dt_utc: datetime) -> dict:
    """Дубликат bot.get_killzone_status() (bot.py:7840), read-only, параметризован
    по историческому моменту вместо datetime.now(TZ) -- см. докстринг модуля:
    bot.py не трогается, добавить параметр в саму функцию нельзя в рамках этого
    пакета. Таблица зон -- byte-for-byte копия часов из bot.py:7876-7887 на
    момент чтения (2026-07-13); если владелец поменяет часы в bot.py, эта копия
    рассинхронизируется -- честно, не переиспользуемый источник правды, разовый
    снимок для офлайн-бэктеста."""
    dt_local = dt_utc.astimezone(TZ_UTC3)
    hm = dt_local.hour * 60 + dt_local.minute
    zones = [dict(z) for z in _KZ_ZONES_TEMPLATE]

    active = None
    for z in zones:
        if z["start"] <= hm < z["end"]:
            active = z
            active["remaining_min"] = z["end"] - hm
            break

    future = [(z, z["start"] - hm if z["start"] > hm else z["start"] + 24 * 60 - hm) for z in zones]
    future.sort(key=lambda x: x[1])
    next_zone = None
    if future:
        next_zone = dict(future[0][0])
        next_zone["in_min"] = future[0][1]

    if active:
        is_good = active["quality"] in ("A+", "A")
    else:
        is_good = False
        active = {"name": "⚪ Dead Zone", "quality": "D", "remaining_min": 0}

    return {"active": active, "next": next_zone, "is_good": is_good, "hour": dt_local.hour}


# ── Симуляция исполнения: форвардный проход 1H-свечей, окно 72ч ────────────

def simulate_execution_72h(store: eng.HistoricalStore, symbol: str, direction: str,
                            entry: float, sl: float, tp1: float, tp2: float,
                            tp3: float, start_ms: int) -> dict:
    """Форвардный проход 1H-свечей от start_ms, окно MAX_HOLD_HOURS_72 (72ч) --
    владелец, Пакет 17: "TP/SL/72ч". Механика идентична
    backtest/engine.py::simulate_execution() (SL приоритетнее TP при совпадении
    в одной свече -- консервативно, тот же принцип, что и везде в проекте), ТОЛЬКО
    окно другое (72ч вместо боевого дефолта 14 дней) -- см. докстринг модуля."""
    c1h = store.full_series(symbol, "1h")
    ts_1h = [c["timestamp"] for c in c1h]
    import bisect
    idx = bisect.bisect_left(ts_1h, start_ms)
    cutoff_ms = start_ms + MAX_HOLD_HOURS_72 * 3600 * 1000
    risk = abs(entry - sl) or 1e-9

    def r_at(level):
        diff = (level - entry) if direction == "long" else (entry - level)
        return round(diff / risk, 3)

    for i in range(idx, len(c1h)):
        c = c1h[i]
        if c["timestamp"] > cutoff_ms:
            break
        if eng._hit(direction, c, sl, is_tp=False):
            return {"outcome": "SL_HIT", "actual_r": -1.0, "outcome_ts": c["timestamp"]}
        if tp3 and eng._hit(direction, c, tp3, is_tp=True):
            return {"outcome": "TP3_HIT", "actual_r": r_at(tp3), "outcome_ts": c["timestamp"]}
        if tp2 and eng._hit(direction, c, tp2, is_tp=True):
            return {"outcome": "TP2_HIT", "actual_r": r_at(tp2), "outcome_ts": c["timestamp"]}
        if tp1 and eng._hit(direction, c, tp1, is_tp=True):
            return {"outcome": "TP1_HIT", "actual_r": r_at(tp1), "outcome_ts": c["timestamp"]}
    return {"outcome": "EXPIRED", "actual_r": 0.0, "outcome_ts": cutoff_ms}
