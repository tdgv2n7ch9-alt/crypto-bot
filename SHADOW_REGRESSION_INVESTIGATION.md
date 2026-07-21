# Расследование "shadow-регресса" (владелец, 2026-07-21, READ-ONLY)

Задача: найти точную дату/коммит, когда shadow-поля (`amd_phase_methodology`,
`tz13_score`, `tz13_setup_type`, `inducement_swept`, `whale_klvl_confluence`,
`bpr_confluence`, `shadow_rr_gate_pass`, `oi_funding_ls_shadow`) начали писаться
как `None` у 6 сделок (ZEC, AKT, US#2, CFG, UB, VVV, 2026-07-18..07-21), после
NIGHT (2026-07-17, последняя "здоровая" по данным `RETRO_WR_DIAGNOSIS.md`).

## ВЕРДИКТ: регресса в shadow-пайплайне НЕТ. Ложная тревога -- баг в моём
## собственном скрипте-экстракторе `RETRO_WR_DIAGNOSIS.md`, не в bot.py/shadow_engine.py.

Живая проверка на контейнере (`railway ssh`, read-only, только чтение JSON) --
корректная shadow-запись для КАЖДОЙ из 6 сделок СУЩЕСТВУЕТ на диске, с ПОЛНЫМ
набором полей, и правильно связана с журналом (`live_journal_id` совпадает с
записью в `journal/signals.json`). Пример (AKT, `journal_id=438`):

```
ts=1784499877.77  type=None            source=send_scheduled  live_journal_id=438  shadow_rr_gate_pass=True
ts=1784499882.36  type=auto_derivatives_shadow                 live_journal_id=None shadow_rr_gate_pass=None
ts=1784499900.54  type=auto_options_shadow                     live_journal_id=None shadow_rr_gate_pass=None
ts=1784499903.58  type=auto_liquidation_shadow                 live_journal_id=None shadow_rr_gate_pass=None
ts=1784499906.32  type=auto_onchain_shadow                     live_journal_id=None shadow_rr_gate_pass=None
```

Та же картина подтверждена живьём для ZEC, CFG, VVV (полный лог проверки ниже);
UB -- тот же паттерн из 4 идущих подряд auto_*_shadow записей (send_scheduled-
запись за пределами узкого окна поиска ±60с, но сигнатура идентична).

## Корневая причина

`bot.send_scheduled()` (см. bot.py ~7359-7420) для КАЖДОГО promoted-кандидата
вызывает **5 отдельных** async shadow-writer'ов подряд, с разницей в несколько
секунд:

1. `shadow_engine.log_send_scheduled_shadow_async()` -- **правильная** запись,
   `source="send_scheduled"`, содержит ВСЕ поля из `compute_shadow()`
   (`amd_phase_methodology`, `tz13_score`, `inducement`, `whale_klvl_confluence`,
   `bpr_confluence`, `shadow_rr_gate_pass`, `oi_funding_ls_shadow` и т.д.) +
   `live_journal_id`, ссылающийся на реальную запись в `journal/signals.json`.
2. `log_auto_ema_stack_shadow_async()` -- Фаза B, no-op пока флаг False (не
   писал данных до включения).
3. `log_auto_derivatives_shadow_async()` -- `type="auto_derivatives_shadow"`,
   CVD/premium поля, **НЕ содержит** amd_phase/tz13/inducement вообще (другая
   схема записи).
4. Аналогично `auto_options_shadow`, `auto_liquidation_shadow`.
5. `auto_onchain_shadow` -- **последняя** по времени запись в кластере (fear &
   greed, DefiLlama TVL/stablecoins), тоже без amd_phase/tz13/inducement.

`RETRO_WR_DIAGNOSIS.md` строился скриптом (`/tmp/wr_full_extract*.json`,
локальный, не в git), который матчил shadow-запись к сделке журнала **по
ближайшему `ts` для данного `symbol`, без фильтра по `type`/`source`**. Пока
Фаза-B контуры были выключены (`*_AUTO_SHADOW_ENABLED=False`), на кандидата
писалась ОДНА shadow-запись -- "ближайший по времени" совпадал с правильной
записью случайно, у скрипта не было шанса ошибиться. Как только владелец
включил Фаза-B контуры (см. `PROGRESS.md`, 2026-07-18 14:04-14:07: "Live/Патч05
впервые прошли min_outcomes=20 ... Фаза-B контуры начали писать данные"),
`send_scheduled()` стал писать 5 записей подряд на кандидата -- скрипт, не
зная о новых `type`, брал **последнюю** запись в кластере по времени
(`auto_onchain_shadow`, т.к. её async-таск в `send_scheduled()` запускается
последним) вместо записи с `source="send_scheduled"`. Отсюда точное совпадение
даты "регресса" (~2026-07-18) с датой включения Фаза-B контуров -- совпадение
причины, не совпадение случайности.

## Почему это НЕ тот же класс, что P0 GitHub-422 (проверено, чтобы исключить)

Отдельно проверено по `git log` на `journal/shadow_signals.json`: коммиты
`shadow: N записей` идут НЕПРЕРЫВНО (без пропусков) через оба момента ZEC
(2026-07-18 18:18 +03) и AKT (2026-07-20 01:25 +03) -- P0-инцидент (сначала
07-17 20:06 -> 07-18 11:45, затем 07-20 07:32 -> 07-21 14:11) в оба этих
конкретных момента синк был здоров. (Для US#2/CFG/UB/VVV, чьи ts ПОПАДАЮТ во
второе P0-окно 07-20/07-21 -- неважно: живая проверка на контейнере ПОСЛЕ
восстановления показала полные записи, т.е. проблема с ними тоже НЕ потеря
данных, а тот же баг матчинга.)

## Предлагаемый фикс (НЕ применён -- только диагноз)

Правка нужна в скрипте retro-диагноза (локальный, не в репозитории), не в
`bot.py`/`shadow_engine.py`: при матчинге shadow-записи к сделке фильтровать
по `source == "send_scheduled"` (или `type is None` -- эквивалентно для этого
контура) ДО выбора ближайшей по `ts`. `RETRO_WR_DIAGNOSIS.md` требует
перегенерации Шага 3 (корреляционная таблица amd_phase/tz13/inducement) с
исправленным матчингом -- скорее всего "Честная находка №1" (6 сделок без
shadow-данных) в текущем виде документа снимается целиком, а сами 6 сделок
возвращаются в корреляционный анализ с их РЕАЛЬНЫМИ (не null) значениями.
Это отдельная (небольшая) задача, не входит в объём этого расследования.

## Значение для паузы эмиссии сигналов

Пауза `PAUSE_LIVE_SIGNAL_EMISSION` была введена на основании предположения
"shadow pipeline broken" -- расследование показывает, что shadow-пайплайн
(`compute_shadow()`/`log_send_scheduled_shadow_async()`) РАБОТАЕТ корректно,
проблема была в отдельном локальном аналитическом скрипте. Снятие паузы --
решение владельца (не принимается этой сессией самостоятельно, см. DoD паузы).
