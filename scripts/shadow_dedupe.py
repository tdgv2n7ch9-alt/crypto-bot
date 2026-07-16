"""
scripts/shadow_dedupe.py -- владелец, задача #245 (2026-07-16): персистентная
санация journal/archive/shadow_signals_*.json.

Диагноз (см. PROGRESS.md, задача #245): дубли/вне-порядок живут ИСКЛЮЧИТЕЛЬНО
в архиве (0 в активном файле -- shadow_engine._write_local() уже идемпотентен,
задача #245 часть 2). Прошлая санация (задача #233, tools/dedup_shadow_archive.py
--apply) была применена ТОЛЬКО локально на тогдашнем контейнере и никогда не
закоммичена в git -- каждый следующий redeploy тянул архив заново из git, где
дубли остались нетронутыми, поэтому регресс воскресал. Эта санация -- ПЕРСИСТЕНТНАЯ:
результат коммитится в GitHub через Git Data API (атомарный многофайловый коммит,
владелец: "Многофайловый коммит — через Git Data API"), не полагается на
локальное состояние контейнера.

Стратегия (та же, что доказала себя в tools/dedup_shadow_archive.py, задача #233,
обновлена под uid из shadow_engine._record_uid вместо (symbol, ts)):
  1. Для каждого journal/archive/shadow_signals_*.json (в порядке сортировки
     имён -- хронологический для naming convention shadow_signals_<from>_<to>.json)
     -- дедуп по uid, оставляя ПЕРВОЕ вхождение (порядок в файле = порядок
     записи). Дедуп ГЛОБАЛЬНЫЙ через все файлы (общее `seen`-множество,
     находка #233: дедуп только внутри каждого файла пропускал дубли,
     размазанные МЕЖДУ файлами).
  2. Каждый файл после дедупа сортируется по ts -- убирает "вне порядка"
     структурно, без отдельной пометки (в отличие от writer'а: здесь мы
     ЗНАЕМ правильный порядок и восстанавливаем его, не просто помечаем).
  3. ПЕРЕД правкой -- бэкап оригиналов (все archive-файлы КАК ЕСТЬ, без
     изменений) в journal/backup_shadow_<YYYYMMDD>.json, синк в GitHub
     (Git Data API) -- если этот шаг не удался, дальше не идём.
  4. Дедупленные+отсортированные файлы коммитятся ОДНИМ атомарным
     multi-file коммитом (Git Data API: blob на файл -> tree -> commit ->
     обновление ref).

Запуск: `python3 scripts/shadow_dedupe.py` -- read-only отчёт (dry-run,
ничего не меняет, не коммитит). `--apply` -- реально переписывает файлы
локально И коммитит в GitHub. `--no-github` -- только локальная санация +
файл бэкапа на диске, без сетевых вызовов (для тестов/отладки).
"""
import argparse
import base64
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import shadow_engine
import signal_journal

ARCHIVE_GLOB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "journal", "archive", "shadow_signals_*.json")
BACKUP_PATH_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal", "backup_shadow_{date}.json")
GITHUB_BACKUP_PATH_TEMPLATE = "journal/backup_shadow_{date}.json"


def dedupe_and_sort(records: list, seen: set = None) -> tuple:
    """Возвращает (clean_records, removed_count, seen). `seen` -- ГЛОБАЛЬНОЕ
    множество uid, накопленное по файлам, обработанным РАНЬШЕ (см. докстринг
    модуля -- та же логика, что доказала себя в задаче #233). Записи без
    symbol/ts (битая схема) НЕ трогаются -- проходят как есть, честно не
    пытаемся дедупить/сортировать то, что не умеем ключевать/упорядочить."""
    if seen is None:
        seen = set()
    clean = []
    broken = []
    removed = 0
    for r in records:
        if not isinstance(r, dict) or not r.get("symbol") or r.get("ts") is None:
            broken.append(r)
            continue
        uid = shadow_engine._record_uid(r)
        if uid in seen:
            removed += 1
            continue
        seen.add(uid)
        clean.append(r)
    clean.sort(key=lambda r: r.get("ts") or 0)
    return clean + broken, removed, seen


def process_files(files: list) -> tuple:
    """Возвращает (per_file_reports: list[dict], new_contents: dict[path,dict],
    total_removed: int). Ничего не пишет на диск -- чистая функция для
    тестируемости, запись -- ответственность вызывающей стороны (apply_local)."""
    seen = set()
    reports = []
    new_contents = {}
    total_removed = 0
    for path in files:
        with open(path) as f:
            payload = json.load(f)
        records = payload.get("records", [])
        before = len(records)
        clean, removed, seen = dedupe_and_sort(records, seen)
        reports.append({"path": path, "before": before, "after": len(clean), "removed": removed})
        new_contents[path] = {"schema_version": payload.get("schema_version", 1), "records": clean}
        total_removed += removed
    return reports, new_contents, total_removed


def build_backup_payload(files: list) -> dict:
    """Бэкап ОРИГИНАЛОВ (до какой-либо правки) -- владелец: "ПЕРЕД правкой --
    бэкап оригинала". Один объединённый файл, а не по одному на архив, как
    просил владелец буквально ("journal/backup_shadow_YYYYMMDD.json")."""
    per_file = {}
    for path in files:
        with open(path) as f:
            per_file[os.path.basename(path)] = json.load(f)
    return {"schema_version": 1, "backed_up_at": datetime.now(timezone.utc).isoformat(),
            "files": per_file}


def apply_local(new_contents: dict) -> None:
    """Реально переписывает archive-файлы на диске -- ТОЛЬКО файлы, где
    что-то реально изменилось (removed>0 уже отфильтровано вызывающей
    стороной), atomic write (temp+replace), тот же паттерн, что
    shadow_engine._atomic_write_json."""
    for path, payload in new_contents.items():
        tmp_path = f"{path}.tmp{os.getpid()}"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


# ── GitHub Git Data API (владелец: "Многофайловый коммит — через Git Data API") ──
# Contents API (уже используется в shadow_engine.py/signal_journal.py) -- ОДИН
# файл за запрос, не атомарно через несколько файлов (последовательные PUT
# могут гонять друг друга -- см. живой инцидент #242, где push в main
# "смывался" гонкой с shadow-sync коммитами живого бота, хотя там был другой
# механизм). Git Data API -- ниже уровнем (blob->tree->commit->ref), даёт
# ОДИН атомарный коммит на много файлов сразу.

def _gh_get(path: str) -> dict:
    r = requests.get(f"{signal_journal._github_api_base()}/{path}",
                      headers=signal_journal._github_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def _gh_post(path: str, body: dict) -> dict:
    r = requests.post(f"{signal_journal._github_api_base()}/{path}",
                       headers=signal_journal._github_headers(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()


def _gh_patch(path: str, body: dict) -> dict:
    r = requests.patch(f"{signal_journal._github_api_base()}/{path}",
                        headers=signal_journal._github_headers(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()


def github_multi_file_commit(files_by_repo_path: dict, message: str, branch: str = "main") -> str:
    """Атомарный многофайловый коммит через Git Data API. `files_by_repo_path`:
    {repo_relative_path: python_object_to_json_dump}. Возвращает новый commit
    sha. Поднимает исключение при любом сбое (вызывающая сторона решает, что
    делать -- в этом скрипте см. main(): сбой бэкап-коммита прерывает
    санацию, не идём дальше без подтверждённого бэкапа в GitHub)."""
    ref = _gh_get(f"git/refs/heads/{branch}")
    base_commit_sha = ref["object"]["sha"]
    base_commit = _gh_get(f"git/commits/{base_commit_sha}")
    base_tree_sha = base_commit["tree"]["sha"]

    tree_entries = []
    for repo_path, obj in files_by_repo_path.items():
        content = json.dumps(obj, ensure_ascii=False, indent=2)
        blob = _gh_post("git/blobs", {
            "content": base64.b64encode(content.encode()).decode(),
            "encoding": "base64",
        })
        tree_entries.append({"path": repo_path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    new_tree = _gh_post("git/trees", {"base_tree": base_tree_sha, "tree": tree_entries})
    new_commit = _gh_post("git/commits", {
        "message": message, "tree": new_tree["sha"], "parents": [base_commit_sha],
    })
    _gh_patch(f"git/refs/heads/{branch}", {"sha": new_commit["sha"]})
    return new_commit["sha"]


def format_report(reports: list, total_removed: int) -> str:
    lines = ["Санация shadow-архива -- отчёт:"]
    changed = [r for r in reports if r["removed"] > 0]
    if not changed:
        lines.append("  дублей не найдено -- архив уже чист")
    for r in changed:
        lines.append(f"  {os.path.basename(r['path'])}: {r['before']} -> {r['after']} "
                      f"(удалено {r['removed']} дублей)")
    total_before = sum(r["before"] for r in reports)
    total_after = sum(r["after"] for r in reports)
    lines.append(f"ИТОГО: {total_before} -> {total_after} записей, удалено {total_removed} дублей "
                 f"(файлов затронуто: {len(changed)}/{len(reports)})")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                         help="реально переписать файлы локально и закоммитить в GitHub; без флага -- dry-run")
    parser.add_argument("--no-github", action="store_true",
                         help="только локальная санация + файл бэкапа на диске, без сетевых вызовов")
    args = parser.parse_args()

    files = sorted(glob.glob(ARCHIVE_GLOB))
    if not files:
        print("Нет файлов journal/archive/shadow_signals_*.json -- нечего санировать.")
        return

    reports, new_contents, total_removed = process_files(files)
    print(format_report(reports, total_removed))

    if not args.apply:
        print("\n(dry-run -- ничего не изменено, ничего не закоммичено. Повторить с --apply.)")
        return

    if total_removed == 0:
        print("\nДублей нет -- нечего коммитить.")
        return

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_payload = build_backup_payload(files)
    backup_local_path = BACKUP_PATH_TEMPLATE.format(date=date_str)
    with open(backup_local_path, "w") as f:
        json.dump(backup_payload, f, ensure_ascii=False, indent=2)
    print(f"\nБэкап оригиналов записан локально: {backup_local_path}")

    changed_contents = {p: c for p, c in new_contents.items()
                         if next(r["removed"] for r in reports if r["path"] == p) > 0}

    if args.no_github:
        apply_local(changed_contents)
        print(f"Переписано локально файлов: {len(changed_contents)} (--no-github, без коммита в GitHub)")
        return

    if not signal_journal._github_configured():
        print("ОШИБКА: GitHub не настроен (GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO) -- "
              "прерываю, локальные файлы НЕ тронуты (без подтверждённого бэкапа в git не правим).")
        sys.exit(1)

    backup_repo_path = GITHUB_BACKUP_PATH_TEMPLATE.format(date=date_str)
    backup_commit_sha = github_multi_file_commit(
        {backup_repo_path: backup_payload},
        f"shadow_dedupe: бэкап архива перед санацией ({date_str}), задача #245")
    print(f"Бэкап синхронизирован в GitHub: коммит {backup_commit_sha[:10]}")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files_by_repo_path = {}
    for local_path, content in changed_contents.items():
        repo_path = os.path.relpath(local_path, repo_root)
        files_by_repo_path[repo_path] = content
    sanitize_commit_sha = github_multi_file_commit(
        files_by_repo_path,
        f"shadow_dedupe: санация архива, удалено {total_removed} дублей "
        f"({len(changed_contents)} файлов), задача #245")
    print(f"Санация закоммичена в GitHub: коммит {sanitize_commit_sha[:10]}")

    apply_local(changed_contents)
    print(f"Локальные копии обновлены ({len(changed_contents)} файлов).")


if __name__ == "__main__":
    main()
