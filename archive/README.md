# archive/

Код, снятый с эксплуатации решением владельца. Не импортируется живым ботом
(`bot.py`) — сохранён для истории, не для повторного использования без
отдельного явного "да".

- **`reader.py`** — демон мониторинга Telegram-каналов (Telethon). Снят
  решением владельца 2026-07-18 (repo-wide grep-аудит #292, п.3): часть
  правила "источники не упоминаются нигде" — конфиг `SOURCE_CHANNELS`
  содержал имена/названия каналов-источников прямо в коде. launchd-джоба
  `com.bestrade.reader` выгружена (`launchctl unload`), сам `.plist`
  (содержит секреты — `BOT_TOKEN`/`TG_API_HASH`) перемещён ВНЕ репозитория
  в `~/crypto-bot-secrets-backup/`, не в git.
- **`card_format.py`** — более старый форматтер карточек, ЗАМЕНЁН `card_v2.py`
  (Пакет 13, 2026-07-13). Подтверждено `grep`: нигде не импортируется
  `bot.py` — мёртвый код, только собственный тест его использовал.
- **`tools/telethon_reauth.py`/`telethon_reauth_temp.py`** — ручные скрипты
  реавторизации Telethon-сессии `reader.py`. Не нужны без самого `reader.py`.
- **`tests/test_reader_parsing.py`/`test_card_format.py`** — тесты
  архивированного кода, исключены из pytest-сборки (`pytest.ini`,
  `testpaths = tests`, `archive/` вне области сборки).
- **`bank_setup_monitor.py`** — условный SHORT-сетап BANKUSDT (CHoCH ->
  ретест -> инвалидация, СРОЧНЫЙ наряд владельца 2026-07-15). Снят
  решением владельца 2026-07-19: сетап мёртв, 5-я волна обналичивания
  транзит->Binance 51 подтверждает завершение разлока. См.
  `knowledge/METHODOLOGY_CORE.md` §22 (кейс закрыт).
- **`onchain_watch.py`** — мониторинг разлока BANK (Lorenzo Protocol, BSC)
  на биржи (получатели крупных переводов -> биржевые депозиты, включая
  адрес "Binance 51"). Снят тем же решением владельца 2026-07-19, та же
  причина (сетап BANK закрыт).
- **`test_bank_setup_monitor.py`/`test_onchain_watch.py`** — тесты
  архивированных модулей выше, та же логика исключения из pytest-сборки.
- **`journal/bank_setup_state.json`, `journal/onchain_watch_state.json`,
  `journal/onchain_watch_events.json`** — последний известный снапшот
  состояния/событий архивированных мониторов выше (перенесены из
  `journal/`, больше не синкаются `journal_persistence.py`).
