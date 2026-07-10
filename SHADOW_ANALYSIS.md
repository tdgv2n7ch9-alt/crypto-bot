# SHADOW_ANALYSIS.md — почасовой разбор теневого контура (ночная сессия #2)

Источник данных: `journal/shadow_signals.json` (GitHub Contents API, тот же приватный
репо, путь отдельный от `journal/signals.json`) — пишется `shadow_engine.log_shadow_async()`
из `signal_loop._send_alert()` и `pump_detector._confirm_pump_reversal()` при каждом
живом сигнале/pump-реверсале, см. `SHADOW_MODE.md`. Формат: список записей с полями
`patches_affected`/`discrepancy` (Блок 1) либо `type: "pump_reversal_shadow"` (Блок 4).

Протокол правды: запись за час пишется ТОЛЬКО если есть реальные новые данные за этот
час. Если новых записей нет — честная строка "нет новых shadow-записей", без выдуманной
статистики на пустых данных.

---

## 2026-07-11 00:30 (первая проверка)

`git show origin/main:journal/shadow_signals.json` → `fatal: path does not exist in
'origin/main'`. Файл ещё не создан на GitHub — контур запущен этой ночью (коммит
`f678297`), живого сигнального алерта или pump-реверсала с момента деплоя ещё не было
(либо Railway ещё не подхватил новый код — не проверено, доступа к Railway API/CLI в
этой сессии нет, см. `AUDIT.md`/более ранние записи PROGRESS.md о неавторизованном
`railway status`). Нет новых shadow-записей за этот час — не выдумываю статистику.
