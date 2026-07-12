"""
BEST TRADE — «Утренняя сводка»: итог ночи owner-чату в 08:30 TZ бота
(«Пакетный ритм» пакет 2, М2 -- владелец подтвердил, что это НОВАЯ фича, не
существовавшая ранее, несмотря на формулировку регламента). Тот же паттерн,
что daily_metrics.py «Метрики дня» 21:00 -- переиспользует ЕЁ helper-функции
(signal_journal.get_daily_digest_stats, daily_metrics.shadow_vs_live_today/
top_whale_events_today/level_watch_touches_today/source_health_summary) с
окном 12ч вместо 24ч, плюс новая секция "деплой-статус" (переиспользует
bot._deploy_check_boot_sha/_fetch_main_head_sync -- то же состояние, что
check_deploy_freshness(), без отправки отдельного алерта, просто честный текст).

Тот же scheduler бота на Railway (APScheduler cron, регистрируется заново в
post_init() при каждом старте) -- НЕ завязан на сессию Claude Code никаким
образом, тот же принцип, что уже подтверждён живыми логами для send_daily_digest.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

import daily_metrics
import signal_journal

MORNING_HOUR_UTC3 = 8
MORNING_MINUTE_UTC3 = 30
MORNING_WINDOW_SEC = 12 * 3600

PROGRESS_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PROGRESS.md")
NIGHT_STATUS_TAIL_CHARS = 40_000
NIGHT_STATUS_MAX_LINES = 12


def night_package_status_summary(progress_md_path: str = None,
                                  tail_chars: int = NIGHT_STATUS_TAIL_CHARS) -> list:
    """Пакет 11 М7 (владелец-запрос -- блок "Ночной пакет: готово/SKIPPED" в
    утренней сводке из PROGRESS.md автоматически). Best-effort выжимка, НЕ
    структурированный парсер: PROGRESS.md остаётся прозой для человека, а не
    машиночитаемым форматом, поэтому это просто grep по уже сложившемуся в
    сессиях соглашению записи `**Статус ...: ГОТОВ/SKIPPED/НЕ ЗАКРЫТ/ПЕРЕНЕСЁН**`
    в хвосте файла (последние `tail_chars` символов -- одна ночная сессия).
    Если соглашение когда-нибудь изменится -- список будет просто пустым
    (честная строка в дайджесте), не сломает сводку и не выдумает статус.

    `progress_md_path=None` -- разрешается в модульный PROGRESS_MD_PATH ВНУТРИ
    функции (не как default-параметр), чтобы monkeypatch на PROGRESS_MD_PATH
    в тестах реально применялся -- default-параметр Python связывается один раз
    при определении функции, а не при каждом вызове."""
    if progress_md_path is None:
        progress_md_path = PROGRESS_MD_PATH
    if not os.path.exists(progress_md_path):
        return []
    try:
        with open(progress_md_path, encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_chars))
            tail = f.read()
    except Exception:
        return []
    matches = re.findall(r"\*\*Статус[^*]*\*\*", tail)
    return [m.strip("*").strip() for m in matches[-NIGHT_STATUS_MAX_LINES:]]


def deploy_status_summary(bot_module) -> str:
    """Живой деплой-статус -- переиспользует bot._deploy_check_boot_sha/
    _fetch_main_head_sync() (то же состояние, что check_deploy_freshness()),
    просто текст для сводки, без побочного алерта."""
    boot_sha = bot_module._deploy_check_boot_sha.get("sha")
    if boot_sha is None:
        return "н/д (GitHub недоступен был при старте процесса)"
    sha, _date_str = bot_module._fetch_main_head_sync()
    if not sha:
        return f"на коммите `{boot_sha[:7]}` (не удалось проверить main прямо сейчас)"
    if sha == boot_sha:
        return f"актуален, коммит `{sha[:7]}` (совпадает с HEAD main)"
    return f"⚠️ main ушёл вперёд -- процесс на `{boot_sha[:7]}`, main на `{sha[:7]}`"


def build_morning_digest(bot_module, now_ts: float = None) -> str:
    """Собирает текст утренней сводки. Чистая (кроме файлового I/O) функция --
    легко тестируется без сети/Telegram, тот же принцип, что build_daily_digest()."""
    now = now_ts if now_ts is not None else time.time()
    dt_local = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=3)))
    date_str = dt_local.strftime("%d.%m.%Y")

    js = signal_journal.get_daily_digest_stats(window_sec=MORNING_WINDOW_SEC, now_ts=now)
    shadow = daily_metrics.shadow_vs_live_today(now_ts=now, window_sec=MORNING_WINDOW_SEC)
    whales = daily_metrics.top_whale_events_today(n=3, now_ts=now, window_sec=MORNING_WINDOW_SEC)
    touches = daily_metrics.level_watch_touches_today(now_ts=now, window_sec=MORNING_WINDOW_SEC)
    health = daily_metrics.source_health_summary(bot_module)
    deploy = deploy_status_summary(bot_module)

    lines = [
        f"🌅 *BEST TRADE — УТРЕННЯЯ СВОДКА, {date_str}*",
        "_Итог ночи (последние 12ч)_",
        "",
        "📈 *Сигналы за ночь:*",
        f"  Создано: {js['created_count']}",
        f"  Закрыто: {js['closed_count']}  (TP: {js['wins']}, SL: {js['losses']})",
    ]
    if js["win_rate_today"] is not None:
        lines.append(f"  Win rate: {js['win_rate_today']}%")
    else:
        lines.append("  Win rate: н/д (0 закрытых сделок с исходом)")

    lines += ["", "🔮 *Shadow-события:*"]
    if shadow["total"] == 0:
        lines.append("  За ночь shadow-контур ничего не зафиксировал")
    else:
        lines.append(f"  Кандидатов: {shadow['total']}  "
                      f"(в бой: {shadow['promoted']}, только shadow: {shadow['not_promoted']})")
        if shadow["dead_zone_penalized"]:
            lines.append(f"  Из них в Мёртвой зоне: {shadow['dead_zone_penalized']}")
        if shadow.get("gate_reasons"):
            top_gates = sorted(shadow["gate_reasons"].items(), key=lambda kv: kv[1], reverse=True)[:3]
            lines.append("  Топ причин отказа: " + ", ".join(f"{g} ({c})" for g, c in top_gates))
        if shadow.get("patches_affected"):
            top_patches = sorted(shadow["patches_affected"].items(), key=lambda kv: kv[1], reverse=True)
            lines.append("  Патчи 02-05: " + ", ".join(f"{p} ({c})" for p, c in top_patches))
        td = shadow.get("top_discrepancy")
        if td:
            mark = "✅ promoted" if td["promoted_live"] else "теневой"
            lines.append(f"  Топ-1 расхождение ({mark}): {td['symbol']} {td['direction']} — {td['detail']}")

    lines += ["", "🐋 *Whale-события, топ-3:*"]
    if not whales:
        lines.append("  Нет событий за ночь")
    else:
        for w in whales:
            side = w.get("side", "?")
            sym = w.get("symbol", "?")
            usd = w.get("size_usd", 0)
            wtype = "принт" if w.get("type") == "whale_trade" else "лимитка"
            lines.append(f"  {sym} {side} ${usd:,.0f} ({wtype})")

    lines += ["", "🎯 *Касания level-watch зон:*"]
    if not touches:
        lines.append("  Нет касаний за ночь")
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

    lines += ["", "🚀 *Деплой:*", f"  {deploy}"]

    night_statuses = night_package_status_summary()
    lines += ["", "📦 *Ночной пакет (из PROGRESS.md, последние записи):*"]
    if not night_statuses:
        lines.append("  н/д (не найдено записей `**Статус...**` в хвосте PROGRESS.md)")
    else:
        for s in night_statuses:
            lines.append(f"  {s}")

    text = "\n".join(lines)
    if len(text) > 4090:
        text = text[:4087] + "..."
    return text


async def send_morning_digest(bot, owner_id: int) -> None:
    """Точка входа для scheduler.add_job(..., "cron", hour=8, minute=30). `bot`
    (Telegram Bot instance) передаётся тем же способом, что send_daily_digest."""
    import bot as bot_module  # локальный импорт -- избегаем цикла bot.py <-> morning_metrics.py
    try:
        text = build_morning_digest(bot_module)
        await bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"morning_metrics: send_morning_digest failed: {e}")
