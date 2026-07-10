# DOCUMENTATION.md — карта проекта crypto-bot

Составлено 2026-07-10 по факту текущего кода на `main` (не по памяти/намерениям — каждая
строка ниже сверена с реальным файлом/строкой при написании). Это карта "что есть и как
устроено", не архитектурное предложение — изменений в этом документе быть не должно без
изменений в самом коде.

## 1. Модули и их роли

| Модуль | Строк | Роль |
|---|---|---|
| `bot.py` | ~10350 | Точка входа. Telegram-хендлеры (команды/кнопки), APScheduler (все job'ы), HTTP-обёртки к CoinGecko/CMC/Bybit/Yahoo с кэшем и rate-limit, health/watchdog/integrity-check, `/stats`/`/journal`, сборка карточек сигналов. |
| `fa_engine.py` | ~700 | «Полный анализ» — 13-блочный движок для `/full` и кнопки «Полный анализ». Оркестрирует `ta_extra.py`, не содержит своей рыночной логики. |
| `ta_extra.py` | ~899 | Чистые функции над уже полученными OHLC: EMA-стек, свип ликвидности (SFP), swing-структура, S/R-зоны, K-LVL, Elliott/Wyckoff-хелперы, FVG, equal highs/lows. Ничего сам не фетчит. |
| `signal_loop.py` | ~485 | Проактивный BUY/SELL контур (2 ступени: дешёвый скринер → `fa_engine` глубокая проверка). Единственный писатель EXIT-стороны сделок в `signal_journal` для source="signal_loop". |
| `pump_detector.py` | ~1464 | Памп-радар v2. Транспорт Bybit public WS (Binance fstream геоблокирован для облачных ASN — см. §4). Машина состояний DETECTED→WATCHING→PROMOTED/CONFIRMED_NO_ACTION. |
| `signal_journal.py` | ~842 | Paper-trading трекер всех сигналов (см. §3, схема журнала). GitHub Contents API как персистентность поверх Railway-эфемерного диска. |
| `subscribers.py` | ~298 | Список чатов для автосигналов/`/start`/`/stop`. Та же GitHub-персистентность схема, что и `signal_journal.py`. |
| `reader.py` | ~416 | Отдельный процесс (не часть `bot.py`/Railway) — Telethon-клиент, слушает Telegram-каналы трейдеров + Lookonchain RSS. См. §5. |
| `chart_v3.py` / `chart_v4.py` | ~225 / ~356 | Рендер графика сделки (matplotlib). v4 добавляет мульти-ТФ POI/K-LVL зоны и стрелку сценария поверх v3; v3 остаётся live-фоллбеком, если v4 не смог отрендерить. |
| `narrative.py` | ~175 | Rule-based (без LLM) генератор связного абзаца «Разбор» из уже посчитанного `fa_engine`-результата. |
| `live_prices.py` | ~74 | In-memory кэш последней WS-цены (питается из `pump_detector.py`'s Binance/Bybit WS), используется для точных entry/SL/TP вместо лагающего CoinGecko. |
| `backtest/journal_replay.py` | ~60 | Только чтение `signal_journal.get_closed_records()` → win-rate/avg-R/max-drawdown по source и по режиму рынка. Не влияет на сигнальный цикл. |
| `tests/*.py` | — | pytest, чистые функции + консистентность реальных исторических записей журнала. Не в `requirements.txt` (не нужен в проде). |

## 2. Поток данных: источник → сигнал → канал

Три независимых пути генерации сигналов, все сходятся в `signal_journal` (наблюдение,
не влияет на отправку) и в Telegram через один и тот же bot-инстанс:

**A. Автосигналы по расписанию** (`send_scheduled`, каждые 30 мин, APScheduler) —
скан рынка → карточка → рассылка всем подписчикам (`subscribers.py`).

**B. Проактивный BUY/SELL** (`signal_loop.py`, интервал `STAGE1_INTERVAL_MIN`) —
Ступень 1 (дёшево: funding-экстремум/OI-сёрдж/свежий свип, из уже кэшированных
данных `bot._fetch_coingecko_oi_map()` + Bybit-свечи) → Ступень 2
(`fa_engine.build_full_analysis`, решение `has_setup` из блока 11: чек-лист ≥4/6,
R:R-гейт, bias не NEUTRAL) → алерт владельцу + `signal_journal.log_signal(source=
"signal_loop", ...)`. Кандидат, не прошедший ступень 2, уходит в `log_rejected()`
(локальный, не синхронизируется в GitHub) — не в Telegram.

**C. Памп-радар** (`pump_detector.py`) — Bybit WS tickers (все USDT-linear пары) →
Z-score объёма >3σ + резкое движение цены → `WATCHING` (динамическая подписка на
klines через `live_prices.request_subscription`) → `REVERSAL_CONFIRMED` →
`PROMOTED` (памп, авто) в канал, либо `CONFIRMED_NO_ACTION` (не прошёл гейт, тихо).

**Ручные команды** (`/top`, `/spot`, `/long`, `/short`, `/full`, `/coin`, `/x100`,
`/rockets`) — по запросу пользователя, тот же `fa_engine`/`ta_extra` конвейер, что и
B, но синхронно на вызов, не по расписанию.

Каждый реально отправленный торговый сигнал (A/B/C и ручные топ-сканы) логируется в
`signal_journal.log_signal(...)` (8 точек вызова в `bot.py` + 1 в `signal_loop.py`) —
**после** решения его отправить, журнал никогда не блокирует и не меняет сигнал.

Отдельно, **не пересекаясь** с A/B/C: `reader.py` — отдельный процесс, слушает внешние
Telegram-каналы трейдеров и пересылает их сигналы напрямую (свой `send_telegram()`,
не через `bot.py`/APScheduler); 2 канала из 13 в `mode="monitor"` — архивируются
локально в JSONL, **никогда** не доходят до пересылки (см. §5).

## 3. Схема журнала (`signal_journal.py`, `journal/signals.json`)

Одна запись (см. `log_signal()`, `signal_journal.py:379`):

```
id, schema_version, ts, timestamp, updated_ts,
source            -- "signal_loop" | "top_long" | "top_short" | "top_spot" | "x100" | "pump_radar" | ...
symbol            -- без суффикса USDT (BTC, не BTCUSDT)
direction         -- "long" | "short"
entry_lo, entry_hi, sl, tp1, tp2, tp3, rr, rocket_score
ema_stack, sweep  -- снимок ta_extra на момент сигнала (для пост-анализа, не используется при отработке)
levels_source     -- "structure" | "fallback_atr" | None
grade             -- "A+" | "A" | "B" | "C" | None
degraded_data     -- None | [список строк-источников с устаревшими/недоступными данными на момент сигнала]
price_at_signal, status  -- "PENDING" -> "ENTERED" -> "TP1_HIT"/"TP2_HIT"/"TP3_HIT"/"SL_HIT"/"EXPIRED"
entered_ts, entered_price, outcome, outcome_ts, outcome_level, actual_r
```

Отработка (PENDING→ENTERED→TPx/SL/EXPIRED) отслеживается фоновым трекером через
`live_prices`, наблюдение не влияет на сигналы. Персистентность: локальный JSON +
GitHub Contents API (`journal/signals.json`), last-write-wins по `updated_ts`, батчинг
коммитов раз в 5 мин. Ротация: записи закрытые >180 дней уходят в годовые архивные
файлы (`_rotate_old_records()`, раз в час). Ежедневный версионированный бэкап —
`backups/<YYYY-MM-DD>/signals.json`, 03:00 TZ бота (проверено реальным restore-тестом,
см. `RESTORE_GUIDE.md`).

Читатели журнала: `/journal`, `/stats` (обе в `bot.py`), `backtest/journal_replay.py`,
`tests/test_journal_historical_examples.py`.

## 4. Переменные окружения (имена, без значений)

| Имя | Назначение | Примечание |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot API токен | Без хардкод-дефолта в текущем коде. |
| `CMC_API_KEY` | CoinMarketCap API ключ | ⚠️ **В текущем коде есть хардкод-дефолт** (`bot.py:117`) — см. `SECRETS_AUDIT.md` находка №1, ожидает решения владельца. |
| `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` | GitHub Contents API — персистентность журнала/подписчиков/бэкапов | Общие для `signal_journal.py`/`subscribers.py`. |
| `CHANNEL_ID` | ID Telegram-канала для авто-рассылки | |
| `OWNER_CHAT_ID` | chat_id владельца — алерты watchdog/integrity-check/data-quality идут только сюда | |
| `TG_API_ID`, `TG_API_HASH` | Telethon (только `reader.py`, отдельный процесс) | Ни разу не хардкожены нигде в git-истории (см. `SECRETS_AUDIT.md` п.4). |

## 5. `reader.py` — отдельный процесс (не Railway)

Работает на Mac mini через launchd (`com.bestrade.reader.plist`), не на Railway, не
часть `bot.py`. 13 каналов в `SOURCE_CHANNELS` (`reader.py:41`): 11 в `mode="signal"`
(парсятся и пересылаются как раньше, поведение не менялось) + 2 в `mode="monitor"`
(архив-онли, добавлены в этой сессии — "Королев о Крипте", "Теория Вероятностей" —
**никогда** не доходят до `format_signal`/пересылки, только пишутся в
`knowledge/channel_archive/<slug>/<YYYY-MM>.jsonl`). Плюс Lookonchain через Nitter RSS
(on-chain данные, отдельный путь, не Telethon).

Известный незакрытый инцидент (не создан этой сессией): Telethon-сессия
`best_trade_reader.session` не авторизована с точки где-то между 2 и 10 июля 2026 —
требует интерактивной переавторизации владельцем (не автоматизируемо удалённо), план
восстановления — `RESTORE_GUIDE.md` §3.

## 6. Известные ограничения

- **Binance fstream геоблокирован для облачных ASN** (Railway: 0 пакетов из US
  West/Singapore/EU Amsterdam при рабочем Telegram/CoinGecko/GitHub) — транспорт
  `pump_detector.py` перенесён на Bybit public WS полностью, сама логика не менялась.
  Три функции с прямыми вызовами `api.binance.com` остались в коде, но подтверждено
  (`grep` по всем вызывающим) — они нигде не вызываются (dead code), живого риска нет.
- **CMC 401/429**: retry+backoff реализован только для транзиентных сетевых ошибок,
  НЕ для 401 (неверный/просроченный ключ) или 429 (rate limit) — осознанное решение
  (retry на 401 бессмыслен, на 429 может усугубить лимит), алерт владельцу идёт при
  `_SOURCE_ALERT_THRESHOLD` подряд неудач через `run_watchdog`.
  См. `_cmc_get()`/`_validate_cmc_key()` в `bot.py`.
- **DXY/S&P/Gold/VIX (Yahoo Finance)**: альтернативных документированных источников не
  найдено при прямой проверке этой сессии — Twelve Data `symbol_search` не находит DXY,
  Stooq заблокирован JS-антибот-челленджем, FRED DTWEXBGS структурно другой индекс
  (26 валют, дневная задержка). Источник не заменён; вместо этого устранена реальная
  причина прошлых багов — 4 дублированных хрупких инлайн-парсера консолидированы в
  `_fetch_yahoo_chart()` с retry+логированием статуса источника.
- **CoinGecko free OHLC** — гранулярность по диапазону `days`, не по запрошенному
  `interval` (см. докстринг `ta_extra.py`): на "1h" фактически 30-минутные бары, EMA200
  на 4h технически недоступна (180 баров < 200). Честное ограничение free-тира, не баг.
- **Railway диск эфемерный** — любое локальное хранилище (журнал, подписчики) обнуляется
  при ребилде контейнера; решено GitHub-Contents-API персистентностью с 2026-07 (после
  инцидента Nixpacks→Dockerfile, см. `PROGRESS.md`).
- **`degraded_data`-флаг** (ROADMAP П3) — не блокирует сигналы, только помечает для
  последующего анализа; на момент написания исторических данных с этим полем ещё мало
  (добавлено в этой сессии), статистическую ценность даст не раньше, чем накопится
  выборка.

## 7. Как читать этот документ

Карта отражает код на момент 2026-07-10 (после git-реконсиляции 14→134 коммитов и
всех П1-П3 правок этой сессии). Не переписывать вручную при мелких правках — при
структурных изменениях (новый модуль, новый источник данных, смена схемы журнала)
обновить соответствующий раздел, не весь документ целиком.
