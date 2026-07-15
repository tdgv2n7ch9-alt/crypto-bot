"""
tools/dedup_shadow_archive.py -- владелец, задача #233 (регресс: 997 дублей/23 вне
порядка в journal/archive/*.json). Расследование: дубли на живом контейнере ПОЛНОСТЬЮ
confined к УЖЕ заархивированным записям (0 в активном файле journal/shadow_signals.json,
0 в git-копии journal/archive/), из-за чего корневая причина связывается с историческим
эпизодом ДО фикса дефекта watchdog (задача #181, "cwd-scoped pre-start guard" -- два
процесса бота писали в один shadow-файл параллельно). Дефект-источник уже устранён,
`shadow_engine._write_local()` теперь идемпотентен на запись (см. коммит рядом) -- этот
скрипт лечит УЖЕ накопленные дубли, не строит новую живую логику.

Стратегия (владелец, план Б -- "чистая перезапись с бэкапом в archive", не физическое
удаление без следа): для каждого journal/archive/shadow_signals_*.json -- дедуп по
(symbol, ts), оставляя ПЕРВОЕ вхождение каждого ключа (порядок в файле = порядок записи,
первое вхождение = оригинал). Перед перезаписью -- байт-в-байт бэкап оригинала в
journal/archive/_dedup_backup_<timestamp>/<имя файла>. Файлы без дублей -- не трогаются
(ни бэкапа, ни перезаписи).

Запуск (владелец, read-only отчёт по умолчанию): `python3 tools/dedup_shadow_archive.py`
-- только печатает, что БУДЕТ сделано. `--apply` -- реально перезаписывает (с бэкапом).
Только локальные файлы, без сети/GitHub -- канал персистентности (`_push_pending_archives_sync`)
не трогается этим скриптом.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ARCHIVE_GLOB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "journal", "archive", "shadow_signals_*.json")


def _dedup_key(rec):
    return (rec.get("symbol"), rec.get("ts"))


def dedup_records(records: list) -> tuple:
    """Возвращает (deduped_records, removed_count) -- оставляет ПЕРВОЕ вхождение
    каждого (symbol, ts). Записи без symbol/ts (битая схема) НЕ трогаются --
    проходят как есть, честно не пытаемся дедупить то, что не умеем ключевать."""
    seen = set()
    out = []
    removed = 0
    for r in records:
        if not isinstance(r, dict) or not r.get("symbol") or r.get("ts") is None:
            out.append(r)
            continue
        key = _dedup_key(r)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(r)
    return out, removed


def process_file(path: str, apply: bool, backup_dir: str) -> dict:
    with open(path) as f:
        payload = json.load(f)
    records = payload.get("records", [])
    before = len(records)
    deduped, removed = dedup_records(records)
    if removed == 0:
        return {"path": path, "before": before, "after": before, "removed": 0, "changed": False}
    if apply:
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, os.path.basename(path))
        shutil.copy2(path, backup_path)
        tmp_path = f"{path}.tmp{os.getpid()}"
        with open(tmp_path, "w") as f:
            json.dump({"schema_version": payload.get("schema_version", 1), "records": deduped},
                       f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    return {"path": path, "before": before, "after": len(deduped), "removed": removed, "changed": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="реально перезаписать файлы (с бэкапом); без флага -- только отчёт")
    args = parser.parse_args()

    files = sorted(glob.glob(ARCHIVE_GLOB))
    if not files:
        print("Нет файлов journal/archive/shadow_signals_*.json -- нечего проверять.")
        return

    backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "journal", "archive", f"_dedup_backup_{int(time.time())}")

    results = [process_file(f, args.apply, backup_dir) for f in files]
    changed = [r for r in results if r["changed"]]

    total_before = sum(r["before"] for r in results)
    total_after = sum(r["after"] for r in results)
    total_removed = sum(r["removed"] for r in results)

    print(f"Файлов проверено: {len(results)}, файлов с дублями: {len(changed)}")
    for r in changed:
        print(f"  {os.path.basename(r['path'])}: {r['before']} -> {r['after']} (-{r['removed']})")
    print(f"\nИТОГО: {total_before} -> {total_after} записей (удалено дублей: {total_removed})")
    if args.apply and changed:
        print(f"Бэкап оригиналов: {backup_dir}")
    elif changed:
        print("Это ОТЧЁТ (dry-run) -- ничего не изменено. Запусти с --apply, чтобы применить.")


if __name__ == "__main__":
    main()
