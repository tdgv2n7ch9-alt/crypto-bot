"""
tools/deploy_watch_check.py -- ПАКЕТ deploy-resilience (владелец, 2026-07-14):
проверка "затронул ли этот коммит Watch Paths из railway.json", вынесена в
отдельный тестируемый модуль (не встроена прямо в tools/deploy.sh) -- та же
логика используется скриптом и покрывается pytest.

Читает `build.watchPatterns` из railway.json ЖИВЬЁМ (не хардкод-копию) --
если владелец поменяет паттерны в railway.json, эта проверка не разойдётся
с реальным поведением Railway автоматически.
"""
import fnmatch
import json
import os
import subprocess
import sys


def load_watch_patterns(railway_json_path: str = None) -> list:
    """Читает build.watchPatterns из railway.json. Пустой список, если файла
    нет или поле отсутствует -- честно, не выдумывает паттерны по умолчанию."""
    if railway_json_path is None:
        railway_json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "railway.json")
    try:
        with open(railway_json_path) as f:
            cfg = json.load(f)
    except Exception:
        return []
    return cfg.get("build", {}).get("watchPatterns", [])


def matches_pattern(path: str, pattern: str) -> bool:
    """Один файл против одного паттерна из watchPatterns. Три формы,
    наблюдаемые в railway.json этого проекта:
      - 'dir/**' -- рекурсивный префикс (path == dir ИЛИ начинается с dir/).
      - паттерн без '/' (например '*.py', 'requirements.txt') -- root-level
        файл ИЛИ совпадение по basename (наблюдаемое поведение: bot.py в
        корне триггерит деплой на '*.py').
      - остальное -- обычный fnmatch по полному пути."""
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "/" not in pattern:
        # Находка (поймана тестом): fnmatch НЕ path-aware -- "*" у него
        # пересекает "/", поэтому fnmatch("tools/x.py", "*.py") == True,
        # что неверно для root-level паттерна. Только явная root-level
        # проверка, без фоллбека на "обычный" fnmatch по полному пути.
        return "/" not in path and fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(path, pattern)


def changed_files_in_range(base_ref: str, head_ref: str = "HEAD", cwd: str = None) -> list:
    """ДЕФЕКТ (владелец, найдено живьём 2026-07-14): `tools/deploy.sh` раньше
    диффал ТОЛЬКО `HEAD~1..HEAD` -- последний коммит, а не весь диапазон,
    реально запушенный этим вызовом скрипта. При многокоммитном пуше (когда
    несколько `git commit` накопились локально ДО вызова deploy.sh --
    штатный сценарий при батч-пуше нескольких пакетов) это теряло файлы из
    более ранних коммитов: 7-коммитный пуш 2026-07-14 дал ложный
    `watchPatterns hit: no`, хотя 3 из 7 коммитов трогали `bot.py`.

    `git diff --name-only <base_ref>..<head_ref>` -- ВЕСЬ диапазон.
    `base_ref` ДОЛЖЕН быть состоянием `origin/main` СХВАЧЕННЫМ ДО `git push`
    (вызывающая сторона обязана закэшировать `git rev-parse origin/main`
    сразу после последнего успешного `git fetch`+`rebase`, ДО самого push)
    -- ПОСЛЕ push `origin/main` == `head_ref`, диапазон стал бы пустым, а
    "весь диапазон пуша" схлопнулся бы обратно в тот же баг, только по
    другой причине.

    Пустой список при любой ошибке git (невалидный ref, не git-репозиторий,
    таймаут) -- честно, не выдумывает список файлов на сбое."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}..{head_ref}"],
            cwd=cwd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.strip().split("\n") if line]
    except Exception:
        return []


def touches_watch_path(changed_files: list, patterns: list = None) -> bool:
    """True, если хотя бы один изменённый файл попадает хотя бы под один
    watchPattern -- т.е. Railway ДОЛЖЕН был задеплоить этот коммит."""
    if patterns is None:
        patterns = load_watch_patterns()
    return any(matches_pattern(f, p) for f in changed_files for p in patterns)


if __name__ == "__main__":
    # tools/deploy.sh вызывает ОДНИМ из двух способов:
    #   DEPLOY_WATCH_BASE_REF=<pre-push origin/main SHA> python3 tools/deploy_watch_check.py
    #     -- ПРЕДПОЧТИТЕЛЬНЫЙ путь (владелец, дефект 2026-07-14): диапазон
    #     считается ЗДЕСЬ, самим модулем (git diff base_ref..HEAD), не
    #     доверяет bash-подсчёту -- покрывает ВЕСЬ диапазон реально
    #     запушенных коммитов, не только последний.
    #   CHANGED_FILES_ENV=<file1>\n<file2>... python3 tools/deploy_watch_check.py
    #     -- обратная совместимость/тесты, вызывающая сторона уже даёт
    #     готовый список файлов.
    base_ref = os.environ.get("DEPLOY_WATCH_BASE_REF", "").strip()
    if base_ref:
        changed = changed_files_in_range(base_ref)
    else:
        changed = os.environ.get("CHANGED_FILES_ENV", "").strip().split("\n")
        changed = [f for f in changed if f]
    print("yes" if touches_watch_path(changed) else "no")
