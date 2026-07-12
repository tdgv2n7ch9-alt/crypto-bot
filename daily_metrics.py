"""
BEST TRADE — «Метрики дня»: ежедневная сводка owner-чату в 21:00 UTC+3
(АПГРЕЙД 11.07, Этап 4). Аддитивно — только читает уже существующие источники
данных, ничего не решает про сигналы/скоринг, отправляет ОДНО сообщение.

Источники по пунктам ТЗ:
1. Сигналы за день (создано/TP/SL) — signal_journal.get_daily_digest_stats().
2. Shadow vs live расхождения — journal/shadow_signals.json (тот же файл, что
   владелец уже видел в диагностике "shadow_signals.json пуст"), окно 24ч.
3. Whale-события топ-3 — whale_radar EVENTS_DIR (JSONL по дате, топ-3 по size_usd).
4. Касания level-watch зон — level_watch EVENTS_DIR (тот же JSONL-паттерн, Этап 4
   добавил логирование в level_watch.check_and_alert()).
5. Здоровье источников — bot.get_data_source_status() (тот же источник, что /health).

ЧЕСТНО про пп. 3/4: whale/level-watch события пишутся в ЛОКАЛЬНЫЙ файл на диске
Railway-контейнера (см. докстринги whale_radar.EVENTS_DIR/level_watch.EVENTS_DIR)
— НЕ синхронизируются в GitHub. Редеплой/рестарт процесса в течение дня обрезает
историю "с начала дня" до "с последнего рестарта" — дайджест это не маскирует,
просто честно показывает, что реально накопилось в процессе на момент отправки.
"""
import glob
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import level_watch
import security_log
import signal_journal
import whale_radar

DIGEST_HOUR_UTC3 = 21
DIGEST_WINDOW_SEC = 24 * 3600


def _read_jsonl_events(events_dir: str, filename_prefix: str, now_ts: float = None,
                        window_sec: float = DIGEST_WINDOW_SEC) -> list:
    """Читает события за окно [now-window_sec, now], объединяя ВСЕ JSONL-файлы дат
    (UTC), которые окно пересекает -- не только "сегодняшний" файл (та же ротация
    по дате, что append_event() в whale_radar.py/level_watch.py). Окно почти всегда
    пересекает полночь UTC (для любого окна, не кратного ровно суткам от 00:00 UTC)
    -- найдено при подготовке М2 "Утренняя сводка 08:30" («Пакетный ритм» пакет 2):
    08:30 Europe/Istanbul = 05:30 UTC, окно 12ч начинается в 17:30 UTC ПРЕДЫДУЩЕГО
    дня -- старая версия (читала только файл "сегодняшней" даты) потеряла бы почти
    всю ночь. Задним числом чинит и вечерний дайджест (Этап 4) тем же фиксом --
    честное ограничение осталось прежним только по сути (события ДО последнего
    рестарта процесса всё ещё не видны, эфемерный диск), не по границе полуночи."""
    now = now_ts if now_ts is not None else time.time()
    start = now - window_sec
    dates = set()
    cur = start
    while cur <= now:
        dates.add(datetime.fromtimestamp(cur, tz=timezone.utc).strftime("%Y-%m-%d"))
        cur += 86400
    dates.add(datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d"))

    events = []
    for date_str in sorted(dates):
        path = os.path.join(events_dir, f"{filename_prefix}-{date_str}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"daily_metrics: не удалось прочитать {path}: {e}")
    return events


def top_whale_events_today(n: int = 3, now_ts: float = None, window_sec: float = DIGEST_WINDOW_SEC) -> list:
    events = _read_jsonl_events(whale_radar.EVENTS_DIR, "whale_events", now_ts, window_sec)
    events.sort(key=lambda e: e.get("size_usd", 0), reverse=True)
    return events[:n]


def level_watch_touches_today(now_ts: float = None, window_sec: float = DIGEST_WINDOW_SEC) -> list:
    return _read_jsonl_events(level_watch.EVENTS_DIR, "level_watch_events", now_ts, window_sec)


def _format_top_discrepancy(records: list) -> dict:
    """Пакет 6 М3 (владелец, "ДА"): один самый заметный shadow-кандидат окна --
    приоритет (1) promoted_live=true (реально ушёл в бой -- самое редкое и значимое
    расхождение), (2) больше всего patches_affected (наиболее насыщенный
    расхождениями кандидат), (3) первый попавшийся, если ни у кого нет ни того,
    ни другого. None, если записей нет -- честно, не выдумываем пример на пустых
    данных."""
    if not records:
        return None
    promoted = [r for r in records if r.get("promoted_live") is True]
    pool = promoted if promoted else records
    pool = sorted(pool, key=lambda r: len(r.get("patches_affected") or []), reverse=True)
    top = pool[0]
    discrepancy_list = top.get("discrepancy") or []
    detail = discrepancy_list[0] if discrepancy_list else (
        "нет явного текстового расхождения (patches_affected: "
        f"{', '.join(top.get('patches_affected') or []) or 'нет'})"
    )
    return {
        "symbol": top.get("symbol"),
        "direction": top.get("direction"),
        "promoted_live": top.get("promoted_live"),
        "detail": detail,
    }


def shadow_vs_live_today(now_ts: float = None, window_sec: float = DIGEST_WINDOW_SEC) -> dict:
    """journal/shadow_signals.json -- локальный файл читается напрямую (тот же файл,
    что shadow_engine.py пишет/синкает в GitHub), без сети. promoted_live=False
    означает: shadow нашёл кандидата, который НЕ прошёл в боевую выдачу -- это и
    есть "расхождение shadow vs live" в буквальном смысле ТЗ.

    Пакет 6 М3 (владелец, "ДА"): расширено срезами по гейтам/патчам и топ-1
    расхождением -- то, чего не хватало INSIGHTS-разборам Блока 12/Пакета 5 М5 в
    самих ежедневных сводках, не только в ручных чекпоинтах PROGRESS.md."""
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - window_sec
    result = {"total": 0, "promoted": 0, "not_promoted": 0, "dead_zone_penalized": 0,
              "gate_reasons": {}, "patches_affected": {}, "top_discrepancy": None}
    try:
        if not os.path.exists(shadow_engine_file()):
            return result
        with open(shadow_engine_file(), encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", [])
        todays = [r for r in records if r.get("ts", 0) >= cutoff]
        result["total"] = len(todays)
        result["promoted"] = sum(1 for r in todays if r.get("promoted_live") is True)
        result["not_promoted"] = sum(1 for r in todays if r.get("promoted_live") is False)
        result["dead_zone_penalized"] = sum(1 for r in todays if r.get("dead_zone"))

        gate_counter = Counter()
        patch_counter = Counter()
        for r in todays:
            for g in (r.get("gate_reasons") or []):
                gate_counter[g] += 1
            for p in (r.get("patches_affected") or []):
                patch_counter[p] += 1
        result["gate_reasons"] = dict(gate_counter)
        result["patches_affected"] = dict(patch_counter)
        result["top_discrepancy"] = _format_top_discrepancy(todays)
    except Exception as e:
        print(f"daily_metrics: shadow_vs_live_today failed: {e}")
    return result


def shadow_engine_file() -> str:
    """Отдельная функция ради monkeypatch в тестах (не тянуть shadow_engine.py как
    полный импорт ради одной константы пути)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "shadow_signals.json")


def source_health_summary(bot_module) -> dict:
    """{name: "ok"|"stale"|"down"} по тому же _DATA_SOURCE_STATUS, что /health --
    один источник правды, не второй параллельный health-чек."""
    ds = bot_module.get_data_source_status()
    out = {}
    now = time.time()
    for name, status in ds.items():
        if status.get("ok") is None:
            out[name] = "н/д"
        elif status.get("ok"):
            out[name] = "ok"
        else:
            out[name] = "down"
    return out


def build_daily_digest(bot_module, now_ts: float = None) -> str:
    """Собирает текст дайджеста. Чистая (кроме файлового I/O) функция -- легко
    тестируется без сети/Telegram."""
    now = now_ts if now_ts is not None else time.time()
    dt_local = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=3)))
    date_str = dt_local.strftime("%d.%m.%Y")

    js = signal_journal.get_daily_digest_stats(window_sec=DIGEST_WINDOW_SEC, now_ts=now)
    shadow = shadow_vs_live_today(now_ts=now)
    whales = top_whale_events_today(n=3, now_ts=now)
    touches = level_watch_touches_today(now_ts=now)
    health = source_health_summary(bot_module)

    lines = [
        f"📊 *BEST TRADE — МЕТРИКИ ДНЯ, {date_str}*",
        "",
        "📈 *Сигналы:*",
        f"  Создано: {js['created_count']}",
        f"  Закрыто: {js['closed_count']}  (TP: {js['wins']}, SL: {js['losses']})",
    ]
    if js["win_rate_today"] is not None:
        lines.append(f"  Win rate дня: {js['win_rate_today']}%")
    else:
        lines.append("  Win rate дня: н/д (0 закрытых сделок с исходом)")

    lines += ["", "🔮 *Shadow vs live:*"]
    if shadow["total"] == 0:
        lines.append("  За сутки shadow-контур ничего не зафиксировал")
    else:
        lines.append(f"  Кандидатов: {shadow['total']}  "
                      f"(в бой: {shadow['promoted']}, только shadow: {shadow['not_promoted']})")
        if shadow["dead_zone_penalized"]:
            lines.append(f"  Из них в Мёртвой зоне: {shadow['dead_zone_penalized']}")
        if shadow["gate_reasons"]:
            top_gates = sorted(shadow["gate_reasons"].items(), key=lambda kv: kv[1], reverse=True)[:3]
            lines.append("  Топ причин отказа: " + ", ".join(f"{g} ({c})" for g, c in top_gates))
        if shadow["patches_affected"]:
            top_patches = sorted(shadow["patches_affected"].items(), key=lambda kv: kv[1], reverse=True)
            lines.append("  Патчи 02-05: " + ", ".join(f"{p} ({c})" for p, c in top_patches))
        td = shadow.get("top_discrepancy")
        if td:
            mark = "✅ promoted" if td["promoted_live"] else "теневой"
            lines.append(f"  Топ-1 расхождение ({mark}): {td['symbol']} {td['direction']} — {td['detail']}")

    lines += ["", "🐋 *Whale-события, топ-3:*"]
    if not whales:
        lines.append("  Нет событий (либо процесс перезапускался сегодня — см. примечание)")
    else:
        for w in whales:
            side = w.get("side", "?")
            sym = w.get("symbol", "?")
            usd = w.get("size_usd", 0)
            wtype = "принт" if w.get("type") == "whale_trade" else "лимитка"
            lines.append(f"  {sym} {side} ${usd:,.0f} ({wtype})")

    lines += ["", "🎯 *Касания level-watch зон:*"]
    if not touches:
        lines.append("  Нет касаний сегодня")
    else:
        lines.append(f"  Всего: {len(touches)}")
        seen_syms = sorted({t.get("symbol", "?") for t in touches})
        lines.append(f"  Символы: {', '.join(seen_syms)}")

    lines += ["", "🩺 *Здоровье источников:*"]
    bad = [f"{name}: {status}" for name, status in health.items() if status == "down"]
    if bad:
        lines.append("  " + " · ".join(bad))
    else:
        lines.append("  Все источники в норме")

    sec = security_log.get_daily_summary(now_ts=now)
    lines += ["", "🔐 *Security-лог за сутки:*"]
    if sec["total"] == 0:
        lines.append("  Событий нет")
    else:
        by_type = sec["by_type"]
        lines.append(f"  Всего: {sec['total']}")
        notable = {k: by_type[k] for k in (
            security_log.EVENT_DENIED, security_log.EVENT_RATE_LIMITED,
            security_log.EVENT_FLOOD_GUARD, security_log.EVENT_AUTO_BAN,
            security_log.EVENT_GRANT, security_log.EVENT_REVOKE,
            security_log.EVENT_INVITE_GENERATED, security_log.EVENT_INVITE_REDEEMED,
            security_log.EVENT_LOCKDOWN, security_log.EVENT_UNLOCK,
        ) if by_type.get(k)}
        if notable:
            lines.append("  " + " · ".join(f"{k}: {v}" for k, v in notable.items()))

    lines += ["", "_whale/level-watch события — с последнего рестарта процесса, не всегда с полуночи (эфемерный диск)_"]

    text = "\n".join(lines)
    if len(text) > 4090:
        text = text[:4087] + "..."
    return text


async def send_daily_digest(bot, owner_id: int) -> None:
    """Точка входа для scheduler.add_job(..., "cron", hour=21, minute=0). `bot`
    (Telegram Bot instance) передаётся тем же способом, что run_daily_backup."""
    import bot as bot_module  # локальный импорт -- избегаем цикла bot.py <-> daily_metrics.py
    try:
        text = build_daily_digest(bot_module)
        await bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"daily_metrics: send_daily_digest failed: {e}")
