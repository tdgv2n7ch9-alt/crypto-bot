"""
backtest/patch01_live_vs_shadow.py -- «Пакетный ритм» пакет 2, М3. Отчёт: сколько
реальных сигналов, созданных ПОСЛЕ переноса Патча 01 в бой (killzone-hours,
коммит fbe4a35, 2026-07-11 14:38:34 +03), попали в killzone-good период по НОВЫМ
(боевым) часам против того, что сказали бы СТАРЫЕ (до-патчевые) часы для тех же
моментов времени.

Старые часы зафиксированы из git-истории (`git show fbe4a35^:bot.py`, зоны до
патча): Asia 01:00-04:00 (B), London Open 10:00-12:00 (A+), NY Open 16:00-18:00
(A), London Close 18:00-19:00 (B), NY Close 23:00-00:00 (C).
Новые (боевые) часы -- см. bot.get_killzone_status(): Asia 00:00-08:00 (B),
London Open 09:00-12:00 (A+), NY Open 14:00-16:00 (A), London Close 18:00-19:00
(B, не менялось), NY Close 23:00-00:00 (C, не менялось).

ЧЕСТНО про интерпретацию: killzone -- ОДИН из ~6 пунктов чек-листа `fa_engine`
Блока 5 (>=4/6 порог), не абсолютное вето -- этот отчёт НЕ реконструирует,
был бы ли сигнал вообще сгенерирован при старых часах (потребовало бы полного
пере-прогона чек-листа со старыми данными на момент каждого сигнала, не
сделано в рамках этого отчёта) -- только честно показывает, СКОЛЬКО из уже
реально созданных сигналов оказались в "killzone-good" под новыми часами, но
были бы "Dead Zone" под старыми -- то есть непосредственный практический эффект
патча на реальном потоке сигналов, не бэктест.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta

PATCH01_PROMOTION_TS = 1783769914.0  # 2026-07-11 14:38:34 +03 (fbe4a35)
TZ_OFFSET_HOURS = 3  # Europe/Istanbul, тот же TZ, что бот использует для now_utc3()

OLD_ZONES = [
    ("Asia", 1 * 60, 4 * 60, "B"),
    ("London Open", 10 * 60, 12 * 60, "A+"),
    ("NY Open", 16 * 60, 18 * 60, "A"),
    ("London Close", 18 * 60, 19 * 60, "B"),
    ("NY Close", 23 * 60, 24 * 60, "C"),
]

NEW_ZONES = [
    ("Asia", 0 * 60, 8 * 60, "B"),
    ("London Open", 9 * 60, 12 * 60, "A+"),
    ("NY Open", 14 * 60, 16 * 60, "A"),
    ("London Close", 18 * 60, 19 * 60, "B"),
    ("NY Close", 23 * 60, 24 * 60, "C"),
]


def _classify(hm: int, zones: list) -> tuple:
    """(is_good, quality, zone_name|None) для минут-с-полуночи `hm` по `zones`."""
    for name, start, end, quality in zones:
        if start <= hm < end:
            return quality in ("A+", "A"), quality, name
    return False, "D", None


def hm_from_ts(ts: float, tz_offset_hours: float = TZ_OFFSET_HOURS) -> int:
    """Минуты с полуночи по TZ бота (Europe/Istanbul, UTC+3) из unix-таймстампа."""
    dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=tz_offset_hours)))
    return dt.hour * 60 + dt.minute


def classify_old(ts: float) -> tuple:
    return _classify(hm_from_ts(ts), OLD_ZONES)


def classify_new(ts: float) -> tuple:
    return _classify(hm_from_ts(ts), NEW_ZONES)


def load_signals_since(promotion_ts: float = PATCH01_PROMOTION_TS,
                        path: str = None) -> list:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                 "journal", "signals.json")
    with open(path) as f:
        data = json.load(f)
    records = data.get("records", data) if isinstance(data, dict) else data
    values = records.values() if isinstance(records, dict) else records
    return [r for r in values if r.get("ts", 0) >= promotion_ts]


def build_report(signals: list, now_ts: float = None,
                  promotion_ts: float = PATCH01_PROMOTION_TS) -> dict:
    now = now_ts if now_ts is not None else time.time()
    window_hours = round((now - promotion_ts) / 3600, 1)
    rows = []
    both_good = only_new_good = only_old_good = neither_good = 0
    for r in signals:
        ts = r.get("ts", 0)
        old_good, old_q, old_zone = classify_old(ts)
        new_good, new_q, new_zone = classify_new(ts)
        rows.append({"symbol": r.get("symbol"), "ts": ts, "timestamp": r.get("timestamp"),
                      "old_good": old_good, "old_quality": old_q, "old_zone": old_zone,
                      "new_good": new_good, "new_quality": new_q, "new_zone": new_zone})
        if old_good and new_good:
            both_good += 1
        elif new_good and not old_good:
            only_new_good += 1
        elif old_good and not new_good:
            only_old_good += 1
        else:
            neither_good += 1
    return {
        "window_hours": window_hours,
        "window_complete_24h": window_hours >= 24.0,
        "total_signals": len(signals),
        "both_good": both_good,
        "only_new_good": only_new_good,
        "only_old_good": only_old_good,
        "neither_good": neither_good,
        "rows": rows,
    }


def render_markdown(report: dict) -> str:
    completeness = ("полные 24ч+" if report["window_complete_24h"]
                    else f"ЧЕСТНО НЕПОЛНОЕ окно -- {report['window_hours']}ч из 24ч")
    lines = [
        "# Патч 01 в бою -- первые сутки vs старые часы (2026-07-11, «Пакетный ритм» пакет 2, М3)",
        "",
        f"Окно: с промоушена (fbe4a35, 2026-07-11 14:38:34 +03) по момент отчёта -- {completeness}.",
        f"Сигналов создано за окно: {report['total_signals']}.",
        "",
        "Killzone -- ОДИН пункт чек-листа fa_engine Блока 5 (>=4/6), не вето -- этот "
        "отчёт НЕ утверждает, что сигналы с «only_old_good=0, only_new_good=1» не "
        "появились бы при старых часах (другие 5 пунктов чек-листа могли компенсировать) "
        "-- только честно показывает, в какой зоне (по старым/новым часам) реально "
        "оказался момент создания каждого сигнала.",
        "",
        "| Категория | Кол-во | Смысл |",
        "|---|---|---|",
        f"| both_good | {report['both_good']} | killzone-good по ОБОИМ вариантам часов |",
        f"| only_new_good | {report['only_new_good']} | good ТОЛЬКО по новым (боевым) часам -- прямой эффект патча |",
        f"| only_old_good | {report['only_old_good']} | good ТОЛЬКО по старым часам (новые сузили тут) |",
        f"| neither_good | {report['neither_good']} | Dead Zone по обоим вариантам |",
        "",
    ]
    if report["rows"]:
        lines.append("| Символ | Время (UTC+3) | Старые часы | Новые часы |")
        lines.append("|---|---|---|---|")
        for row in report["rows"]:
            old_s = f"{row['old_quality']} ({row['old_zone'] or 'Dead Zone'})"
            new_s = f"{row['new_quality']} ({row['new_zone'] or 'Dead Zone'})"
            lines.append(f"| {row['symbol']} | {row['timestamp']} | {old_s} | {new_s} |")
    return "\n".join(lines)


if __name__ == "__main__":
    signals = load_signals_since()
    report = build_report(signals)
    print(render_markdown(report))
