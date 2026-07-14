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
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

import daily_metrics
import event_radar
import shadow_engine
import signal_journal

MORNING_HOUR_UTC3 = 8
MORNING_MINUTE_UTC3 = 30
MORNING_WINDOW_SEC = 12 * 3600

PROGRESS_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PROGRESS.md")
NIGHT_STATUS_TAIL_CHARS = 40_000
NIGHT_STATUS_MAX_LINES = 12

# НОЧЬ#3 (владелец, Н4): "Утренняя сводка обязана включать статус Пакета 18
# по пунктам". Пакет 18 -- фиксированный, уже завершённый факт этой сессии
# (см. PROGRESS.md, "DoD Пакета 18") -- статичная таблица, не пересчитывается
# заново каждое утро (в отличие от night_package_status_summary() ниже,
# которая читает live-хвост PROGRESS.md для БУДУЩИХ ночей). Формат таблицы
# в PROGRESS.md (markdown `| № | Статус |`) не совпадает с grep-соглашением
# `**Статус...**`, поэтому отдельная константа, а не общий парсер.
PACKET18_STATUS_TABLE = [
    ("1", "✅ готово (LEGACY-снос + деплой + живой лог)"),
    ("2", "✅ готово (деплой)"),
    ("3", "✅ готово (деплой + живой лог: прогрев подтверждён)"),
    ("4", "✅ готово (деплой)"),
    ("5", "✅ готово (деплой)"),
    ("6", "✅ готово (деплой)"),
    ("7", "✅ подтверждено (задеплоено ранее в сессии)"),
    ("8", "✅ готово (деплой)"),
    ("9", "✅ готово (деплой)"),
    ("10", "✅ готово (документация, деплоя не требует)"),
    ("11", "✅ готово (деплой)"),
    ("13", "✅ готово (деплой)"),
]

# НОЧЬ#3 (владелец, Н4): "ответ по п.1 (BTC zone-touch: был/не был/причина)".
# Диагностический факт, зафиксированный один раз в PROGRESS.md (см. "Пакет 18
# п.1") -- не измеряется заново каждое утро, статична по той же причине, что
# PACKET18_STATUS_TABLE выше.
PACKET18_ITEM1_FINDING = (
    "БЫЛ -- BTC зона 61840.9-62285.0 касалась вечером 13.07, live-alert "
    "прошёл через unified-путь check_watchlist_alerts_from_level_watch() "
    "(не LEGACY -- код доказательно был недостижим без оверрайда "
    "ZONES_UNIFIED на Railway, которого не было). LEGACY-ветка снесена."
)

# НОЧЬ#3 (владелец, Н4): "статус ночных блоков" -- Н1-Н4/Н8, та же логика
# статичности, что PACKET18_STATUS_TABLE (факт этой конкретной ночи, не
# пересчитывается). Обновляется вручную по ходу ночи -- следующая ночная
# сессия заведёт свой список под свои блоки, эта константа не претендует
# быть общим шаблоном (в отличие от contour_readiness_lines()/
# author_zones_lines(), которые живые и переиспользуются каждую ночь).
NIGHT3_BLOCKS_STATUS = [
    ("Н1", "✅ готово (SHADOW_ANALYSIS.md -- tz13/Патч05/Патч09/EMA-стек срезы)"),
    ("Н2", "✅ готово (транскрибация подтверждена 100%, Блок 7 +2 файла в EVOLUTION.md)"),
    ("Н3", "✅ готово (EVENT-RADAR М5 -- сводка ликвидности в On-Chain карточке)"),
    ("Н4", "✅ готово (этот блок -- обязательные поля утренней сводки)"),
]


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


def contour_readiness_lines() -> list:
    """НОЧЬ#3 (владелец, Н4/Н8): "тень одной таблицей -- n, готово ли к
    решению (да/нет/сколько осталось)". Переиспользует
    shadow_engine.contour_readiness_summary()/ema_stack_readiness_summary()
    (НОЧЬ#3 Н4) -- живой пересчёт по journal/shadow_signals.json на диске,
    не статика (в отличие от PACKET18_STATUS_TABLE выше -- контуры копятся
    и завтра дадут другие числа)."""
    contours = shadow_engine.contour_readiness_summary()
    ema = shadow_engine.ema_stack_readiness_summary()
    lines = []
    labels = {"tz13": "tz13", "patch05_bpr": "Патч 05 (BPR)", "patch09_oi": "Патч 09 (OI/funding/L-S)"}
    for key, label in labels.items():
        c = contours[key]
        status = "готово" if c["ready"] else f"нет, осталось {c['remaining']}"
        lines.append(f"  {label}: n={c['n']}/{c['threshold']} -- {status}")
    ema_status = "готово (окно закрыто)" if ema["ready"] else \
        f"нет, {ema['elapsed_hours']:.1f}/{ema['window_hours']:.0f}ч окна"
    lines.append(f"  EMA-стек: n={ema['n']} -- {ema_status}")
    return lines


def author_zones_lines(bot_module) -> list:
    """НОЧЬ#3 (владелец, Н4/Н8): "число активных author-зон и их статусы
    (ЖДЁМ/В ЗОНЕ)". Переиспользует bot.author_zones_status_summary()
    (Пакет 18 п.13 логика 1в1, НОЧЬ#3 Н4 обёртка)."""
    try:
        summary = bot_module.author_zones_status_summary()
    except Exception as e:
        return [f"  н/д (ошибка: {e})"]
    counts = summary["counts"]
    lines = [f"  Всего активных author-зон: {summary['total']}"]
    lines.append(f"  ЖДЁМ ЦЕНУ: {counts.get('ЖДЁМ ЦЕНУ', 0)} · "
                  f"ЦЕНА В ЗОНЕ: {counts.get('ЦЕНА В ЗОНЕ', 0)} · "
                  f"ОТРАБОТАНА: {counts.get('ОТРАБОТАНА', 0)}")
    in_zone = [z for z in summary["zones"] if z["status"] == "ЦЕНА В ЗОНЕ"]
    if in_zone:
        lines.append("  В зоне сейчас: " + ", ".join(f"{z['symbol']} {z['side']}" for z in in_zone))
    return lines


def zone_touch_alerts_tonight_lines(bot_module, now_ts: float, window_sec: int) -> list:
    """НОЧЬ#3 (владелец, Н4): "любые ночные zone-touch алерты". Честная
    оговорка: bot.watchlist_alerted -- {symbol: последний_alert_ts}
    (кулдаун-словарь, не полный лог) -- если один и тот же символ алертил
    дважды за ночь, здесь виден только факт "алертил", не количество раз.
    Полноценный персистентный лог алертов -- отдельная задача на будущее,
    не выдаю приближение за точный счётчик."""
    try:
        alerted = bot_module.watchlist_alerted
    except Exception as e:
        return [f"  н/д (ошибка: {e})"]
    tonight = sorted(sym for sym, ts in alerted.items() if now_ts - ts <= window_sec)
    if not tonight:
        return ["  Ни одного zone-touch алерта за ночь"]
    return [f"  {len(tonight)}: " + ", ".join(tonight) +
            " (честно: словарь кулдауна хранит последний алерт на символ, "
            "не полный счётчик повторов)"]


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

    # Владелец "да" 2026-07-13 -- health-счётчик shadow-потока (та же честность про
    # None="с последнего рестарта ещё не было записи", что в /stats).
    last_shadow_ts = shadow_engine.get_last_send_scheduled_write_ts()
    lines += ["", "🩺 *Shadow-поток (send_scheduled):*"]
    if last_shadow_ts is None:
        lines.append("  Ни одной записи с последнего рестарта процесса")
    else:
        hours_ago = (now - last_shadow_ts) / 3600
        warn = " ⚠️ >2ч без записи" if hours_ago > 2 else ""
        lines.append(f"  Последняя запись: {hours_ago:.1f}ч назад{warn}")

    # EVENT-RADAR М5 (Пакет 13) -- листинги/делистинги за ночь (12ч окно, тот же
    # период, что остальная утренняя сводка).
    lines.append(event_radar.format_event_digest_section(hours=12.0, now=now))

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

    # НОЧЬ#3, Н4 (владелец): обязательные поля утренней сводки -- zone-touch
    # алерты за ночь, author-зоны (⭐ ЛИМИТКИ), shadow-готовность по контурам,
    # статус Пакета 18 по пунктам + ответ по п.1.
    lines += ["", "⭐ *Zone-touch алерты за ночь (author-зоны):*"]
    lines += zone_touch_alerts_tonight_lines(bot_module, now, MORNING_WINDOW_SEC)

    lines += ["", "⭐ *Author-зоны (⭐ ЛИМИТКИ):*"]
    lines += author_zones_lines(bot_module)

    lines += ["", "🔮 *Shadow-контуры, готовность к решению:*"]
    lines += contour_readiness_lines()

    lines += ["", "📋 *Пакет 18, статус по пунктам:*"]
    for item, status in PACKET18_STATUS_TABLE:
        lines.append(f"  п.{item}: {status}")
    lines.append(f"  Ответ по п.1 (BTC zone-touch): {PACKET18_ITEM1_FINDING}")

    lines += ["", "🌙 *Ночные блоки (НОЧЬ#3):*"]
    for block, status in NIGHT3_BLOCKS_STATUS:
        lines.append(f"  {block}: {status}")

    lines += ["", "🩺 *Здоровье источников:*"]
    # Находка 2026-07-14 (живой сбой: send_morning_digest сегодня в 08:30 НЕ
    # доставлен в Telegram, тот же класс, что ПАКЕТ 19 П0): сырые ключи
    # _DATA_SOURCE_STATUS ("coingecko_markets" и т.п.) содержат "_" --
    # нечётное число подчёркиваний в тексте с parse_mode="Markdown" ломает
    # telegram.error.BadRequest "Can't parse entities". Тот же безопасный
    # маппинг, что уже применён в welcome_text_v2().
    bad = [f"{bot_module.SOURCE_DISPLAY_LABELS.get(name, name.replace('_', ' '))}: {status}"
           for name, status in health.items() if status == "down"]
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
        log.error(f"morning_metrics: send_morning_digest failed: {e}")
