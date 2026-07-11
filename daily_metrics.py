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
from datetime import datetime, timedelta, timezone

import level_watch
import signal_journal
import whale_radar

DIGEST_HOUR_UTC3 = 21
DIGEST_WINDOW_SEC = 24 * 3600


def _read_jsonl_events(events_dir: str, filename_prefix: str, now_ts: float = None) -> list:
    """Читает события за ТЕКУЩИЕ UTC-сутки (та же ротация по дате, что append_event()
    в whale_radar.py/level_watch.py) -- события, попавшие в файл ВЧЕРАШНЕЙ UTC-даты
    (если digest шлётся вскоре после полуночи UTC), НЕ подхватываются: честное
    ограничение того же файлового паттерна, не расширяем поиск на 2 файла ради
    простоты (см. докстринг модуля про ежедневную ротацию)."""
    now = now_ts if now_ts is not None else time.time()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    path = os.path.join(events_dir, f"{filename_prefix}-{dt.strftime('%Y-%m-%d')}.jsonl")
    events = []
    if not os.path.exists(path):
        return events
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


def top_whale_events_today(n: int = 3, now_ts: float = None) -> list:
    events = _read_jsonl_events(whale_radar.EVENTS_DIR, "whale_events", now_ts)
    events.sort(key=lambda e: e.get("size_usd", 0), reverse=True)
    return events[:n]


def level_watch_touches_today(now_ts: float = None) -> list:
    return _read_jsonl_events(level_watch.EVENTS_DIR, "level_watch_events", now_ts)


def shadow_vs_live_today(now_ts: float = None, window_sec: float = DIGEST_WINDOW_SEC) -> dict:
    """journal/shadow_signals.json -- локальный файл читается напрямую (тот же файл,
    что shadow_engine.py пишет/синкает в GitHub), без сети. promoted_live=False
    означает: shadow нашёл кандидата, который НЕ прошёл в боевую выдачу -- это и
    есть "расхождение shadow vs live" в буквальном смысле ТЗ."""
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - window_sec
    result = {"total": 0, "promoted": 0, "not_promoted": 0, "dead_zone_penalized": 0}
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
