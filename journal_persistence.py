"""journal_persistence.py -- владелец, 2026-07-16 (КРИТИЧНО, блокер ончейн-
аналитики -- живьём поймано на AKE-расследовании: собственный редеплой стёр
3-часовую историю bsc_wallet_events.json посреди срочного разбора).

Railway journal/ -- эфемерный диск, обнуляется при каждом РЕАЛЬНОМ редеплое
(см. shadow_engine.py докстринг, тот же факт задокументирован там). Вместо
платного Railway volume (billing-решение, точка владельца по CLAUDE.md --
"любые вопросы денег" не решается сессией без явного "да") -- append-push
в GitHub раз в SYNC_INTERVAL_SEC + restore на старте, ТОТ ЖЕ паттерн, что
signal_journal.py уже годами использует для journal/signals.json (GET sha ->
merge/overwrite -> PUT с sha, ретрай на 409 conflict).

Область действия -- НЕ journal/shadow_signals.json (у него уже есть
собственный, отдельно проверенный механизм ротации+архивации в git через
shadow_engine.py/scripts/shadow_dedupe.py, трогать не нужно) и НЕ
journal/watch_zones.json (уже синкается отдельно, "[LEVEL-WATCH]
startup_sync"). Здесь -- то, что раньше НЕ персистилось вообще: state-файлы
мониторов (bank/ake/zone_alert) и AKE wallet-поллер.

Restore -- ТОЛЬКО если локального файла ещё нет (свежий редеплой) -- не
перезаписывает локальное состояние, если процесс просто продолжает жить
(рестарт джобов без пересоздания контейнера, или volume когда-нибудь
появится). Честно: окно потери -- до SYNC_INTERVAL_SEC (партия между
последним push и падением), не ноль, но несравнимо лучше полного обнуления
на каждом деплое."""
import base64
import glob
import json
import logging
import os
import tempfile

import requests

import signal_journal
import shadow_engine  # _git_sync_lock (общая сериализация с shadow-пушем) + _run_git/_git_remote_url (переиспользуем, не дублируем маскировку токена)

log = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# (локальный путь относительно repo root == repo-путь на GitHub, файлы
# лежат по тем же именам в обоих местах)
SYNCED_FILES = [
    # bank_setup_state.json/onchain_watch_state.json/onchain_watch_events.json
    # убраны -- владелец, 2026-07-19: BANK-сетап снят, оба монитора
    # архивированы (archive/bank_setup_monitor.py, archive/onchain_watch.py),
    # синкать состояние мёртвых мониторов незачем.
    "journal/ake_setup_state.json",
    "journal/bsc_wallet_events.json",
    "journal/bsc_wallet_monitor_state.json",
    "journal/pump_radar_state.json",
]
ZONE_ALERT_STATE_GLOB = "journal/zone_alert_state_*.json"  # динамический список символов
SYNC_INTERVAL_SEC = 15 * 60  # владелец: "раз в 15 мин"


def _local_path(rel: str) -> str:
    return os.path.join(_REPO_ROOT, rel)


def _get_file_sync(repo_path: str):
    """GET repo_path из GitHub. (obj, sha) либо (None, None) при отсутствии/ошибке."""
    if not signal_journal._github_configured():
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{repo_path}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        return json.loads(content), data["sha"]
    except Exception as e:
        log.error(f"journal_persistence: GET {repo_path} failed: {e}")
        return None, None


def _put_file_sync(repo_path: str, obj, sha) -> str:
    """PUT repo_path (создаёт либо обновляет). Возвращает новый sha, "conflict"
    при 409 (sha устарел), None при прочей ошибке."""
    if not signal_journal._github_configured():
        return None
    try:
        body = {
            "message": f"journal-sync: {repo_path}",
            "content": base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{signal_journal._github_api_base()}/contents/{repo_path}",
                          headers=signal_journal._github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            return "conflict"
        r.raise_for_status()
        return r.json()["content"]["sha"]
    except Exception as e:
        log.error(f"journal_persistence: PUT {repo_path} failed: {e}")
        return None


def _list_journal_dir_sync() -> list:
    """Имена файлов в journal/ на GitHub -- нужно для восстановления
    zone_alert_state_*.json на свежем диске, где локального списка символов
    ещё нет (узнать, ЧТО восстанавливать, можно только из самого GitHub)."""
    if not signal_journal._github_configured():
        return []
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/journal",
                          headers=signal_journal._github_headers(), timeout=15)
        r.raise_for_status()
        return [item["name"] for item in r.json() if item.get("type") == "file"]
    except Exception as e:
        log.error(f"journal_persistence: list journal/ failed: {e}")
        return []


def restore_file_sync(repo_path: str) -> bool:
    """Если локального файла НЕТ -- восстанавливает из GitHub. Если файл уже
    существует (тот же процесс, volume, или уже восстановлен) -- не трогает.
    Возвращает True, если реально восстановил."""
    local = _local_path(repo_path)
    if os.path.exists(local):
        return False
    remote, _sha = _get_file_sync(repo_path)
    if remote is None:
        return False
    tmp = f"{local}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(remote, f, ensure_ascii=False, indent=2)
        os.replace(tmp, local)
        log.info(f"journal_persistence: восстановлен {repo_path} из GitHub")
        return True
    except Exception as e:
        log.error(f"journal_persistence: локальная запись {repo_path} после restore не удалась: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def restore_all_sync() -> dict:
    """Вызывается ОДИН раз при старте, ДО того, как мониторы сделают первый
    тик -- иначе они сами создадут пустой state-файл раньше, чем мы успеем
    его восстановить, и restore_file_sync() увидит "файл уже есть", не тронет."""
    restored = []
    for repo_path in SYNCED_FILES:
        if restore_file_sync(repo_path):
            restored.append(repo_path)
    for name in _list_journal_dir_sync():
        if name.startswith("zone_alert_state_") and name.endswith(".json"):
            repo_path = f"journal/{name}"
            if restore_file_sync(repo_path):
                restored.append(repo_path)
    if restored:
        log.info(f"journal_persistence: восстановлено {len(restored)} файлов из GitHub: {restored}")
    return {"restored": restored}


def _discover_zone_alert_state_files() -> list:
    """journal/zone_alert_state_<symbol>.json -- локально уже существующие
    (для периодического push, в отличие от restore, где список берём из
    GitHub -- см. restore_all_sync)."""
    pattern = _local_path(ZONE_ALERT_STATE_GLOB)
    return [os.path.relpath(p, _REPO_ROOT) for p in glob.glob(pattern)]


def sync_file_sync(repo_path: str) -> bool:
    """Пушит текущее локальное содержимое в GitHub (создаёт либо обновляет),
    один ретрай со свежим sha на 409-конфликт (та же гонка, что #242/#245 --
    concurrent sync с другого источника)."""
    local = _local_path(repo_path)
    if not os.path.exists(local):
        return False
    try:
        with open(local) as f:
            obj = json.load(f)
    except Exception as e:
        log.error(f"journal_persistence: чтение {repo_path} для sync не удалось: {e}")
        return False
    for _attempt in range(2):
        _remote, sha = _get_file_sync(repo_path)
        result = _put_file_sync(repo_path, obj, sha)
        if result == "conflict":
            continue
        return bool(result)
    return False


# --- Батч-пуш через git CLI (владелец, ДА, 2026-07-22) ----------------------------------
# Живая находка: sync_all_sync() пушил 7 файлов ОДНИМ ЗА ДРУГИМ через GitHub Contents API
# (sync_file_sync() -> _put_file_sync(), 7 отдельных PUT = 7 отдельных коммитов за ~8с) --
# это не только раздувало историю, но и создавало окно, в которое залетал shadow-пуш
# (shadow_engine._push_shadow_via_git_cli()), получая "[rejected] fetch first" на часть из
# своих 5 попыток (не постоянный сбой -- ретрай shadow обычно догонял к 5-й попытке, но
# сама коллизия была реальной и полностью устранимой). Фикс -- тот же native `git push`
# паттерн, что уже проверен в shadow_engine.py (sparse-checkout + fetch/checkout/commit/
# push с ретраем), но для ЭТИХ файлов и в ОТДЕЛЬНОЙ рабочей директории (не делим checkout
# с shadow -- разные пути, никакого смысла материализовывать их в одном дереве), плюс
# ОБЩИЙ shadow_engine._git_sync_lock -- гарантирует, что shadow-пуш и этот пуш никогда не
# окажутся в полёте одновременно, устраняя саму коллизию, а не просто переживая её ретраем.
_JP_GIT_SYNC_DIR = os.path.join(tempfile.gettempdir(), "journal_persistence_git_sync")
_JP_SPARSE_PATTERNS = list(SYNCED_FILES) + [ZONE_ALERT_STATE_GLOB]


def _jp_ensure_git_sync_dir() -> bool:
    """Аналог shadow_engine._ensure_git_sync_dir(), отдельная рабочая директория и свой
    набор sparse-checkout путей (файлы ЭТОГО модуля). Ленивая инициализация -- один раз за
    время жизни процесса, переживает между вызовами _push_all_via_git_cli()."""
    if os.path.isdir(os.path.join(_JP_GIT_SYNC_DIR, ".git")):
        return True
    try:
        os.makedirs(_JP_GIT_SYNC_DIR, exist_ok=True)
        rc, out, err = shadow_engine._run_git(["init", "-q"], cwd=_JP_GIT_SYNC_DIR)
        if rc != 0:
            log.error(f"journal_persistence: git init sync-репо не удался: {err[:300]}")
            return False
        shadow_engine._run_git(["config", "user.email", "journal-sync@bestrade.local"], cwd=_JP_GIT_SYNC_DIR)
        shadow_engine._run_git(["config", "user.name", "BEST TRADE journal-sync"], cwd=_JP_GIT_SYNC_DIR)
        shadow_engine._run_git(["sparse-checkout", "init", "--no-cone"], cwd=_JP_GIT_SYNC_DIR)
        # --skip-checks -- ЗДЕСЬ (в отличие от shadow_engine, у которой один точный файл
        # без wildcard) паттерн включает ZONE_ALERT_STATE_GLOB (`*`) -- без --no-cone на
        # init + --skip-checks на set git отказывается принимать non-cone gitignore-стиль
        # маску, требуя "specify directories rather than patterns" (найдено живьём тестом).
        rc, out, err = shadow_engine._run_git(["sparse-checkout", "set", "--skip-checks"] + _JP_SPARSE_PATTERNS,
                                               cwd=_JP_GIT_SYNC_DIR, timeout=15)
        if rc != 0:
            log.error(f"journal_persistence: git sparse-checkout не удался: {err[:300]}")
            return False
        return True
    except Exception as e:
        log.error(f"journal_persistence: инициализация git sync-репо упала: {e}")
        return False


def _push_all_via_git_cli(files: list, max_attempts: int = 5) -> dict:
    """Батчит запись/пуш нескольких journal-файлов В ОДИН git-коммит вместо N отдельных
    GitHub Contents API PUT. `files` -- список (repo_path, local_abs_path) уже
    подтверждённых существующими локально. Возвращает {"synced": [...]} -- либо полный
    список путей (весь батч закоммичен и запушен ОДНИМ коммитом), либо [] (ничего не
    запушено -- best-effort, как и раньше, локальные файлы уже надёжны сами по себе,
    просто GitHub-бэкап подождёт следующего цикла).

    Единая критическая секция -- под shadow_engine._git_sync_lock, чтобы никогда не
    оказаться в полёте одновременно с shadow-пушем (устраняет живую находку 2026-07-22:
    "[rejected] fetch first" из-за коллизии этих двух независимых пушей)."""
    result = {"synced": []}
    if not files:
        return result
    if not signal_journal._github_configured():
        return result
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        log.error(f"journal_persistence: {token_issue}")
        return result
    if not _jp_ensure_git_sync_dir():
        return result

    remote_url = shadow_engine._git_remote_url()

    with shadow_engine._git_sync_lock:
        for attempt in range(max_attempts):
            rc, out, err = shadow_engine._run_git(
                ["fetch", "-q", "--filter=blob:none", "--depth=1", remote_url, "main"],
                cwd=_JP_GIT_SYNC_DIR, timeout=60)
            if rc != 0:
                log.error(f"journal_persistence: git-sync fetch попытка {attempt + 1}/{max_attempts} "
                          f"не удалась: {err[:300]}")
                continue

            rc, out, err = shadow_engine._run_git(["checkout", "-q", "-B", "main", "FETCH_HEAD"],
                                                   cwd=_JP_GIT_SYNC_DIR, timeout=30)
            if rc != 0:
                log.error(f"journal_persistence: git-sync checkout попытка {attempt + 1}/{max_attempts} "
                          f"не удалась: {err[:300]}")
                continue

            write_failed = False
            for repo_path, local_abs in files:
                target = os.path.join(_JP_GIT_SYNC_DIR, repo_path)
                try:
                    with open(local_abs) as f:
                        obj = json.load(f)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False)
                except Exception as e:
                    log.error(f"journal_persistence: git-sync запись {repo_path} не удалась: {e}")
                    write_failed = True
            if write_failed:
                continue

            for repo_path, _local_abs in files:
                shadow_engine._run_git(["add", repo_path], cwd=_JP_GIT_SYNC_DIR, timeout=30)
            rc, out, err = shadow_engine._run_git(
                ["commit", "-q", "-m", f"journal-sync: {len(files)} файлов"],
                cwd=_JP_GIT_SYNC_DIR, timeout=20)
            if rc != 0:
                if "nothing to commit" in (out + err).lower():
                    result["synced"] = [p for p, _ in files]
                    return result
                log.error(f"journal_persistence: git-sync commit попытка {attempt + 1}/{max_attempts} "
                          f"не удалась: {err[:300]}")
                continue

            rc, out, err = shadow_engine._run_git(["push", "-q", remote_url, "HEAD:refs/heads/main"],
                                                   cwd=_JP_GIT_SYNC_DIR, timeout=90)
            if rc == 0:
                result["synced"] = [p for p, _ in files]
                return result
            log.error(f"journal_persistence: git-sync push попытка {attempt + 1}/{max_attempts} "
                      f"не удалась: {err[:300]} -- ретрай со свежего main")
    return result


def sync_all_sync() -> dict:
    """Периодический push (scheduler.add_job, interval SYNC_INTERVAL_SEC). Владелец, ДА,
    2026-07-22: батч ВСЕХ файлов В ОДИН git-коммит (было -- 7 отдельных GitHub Contents
    API PUT подряд, каждый свой коммит, гонявшихся за main-ref с shadow-пушем) -- см.
    _push_all_via_git_cli()."""
    repo_paths = list(SYNCED_FILES) + _discover_zone_alert_state_files()
    files = [(p, _local_path(p)) for p in repo_paths if os.path.exists(_local_path(p))]
    result = _push_all_via_git_cli(files)
    return {"synced": result["synced"], "attempted": len(repo_paths)}


async def sync_all(bot=None, run_in_executor_fn=None) -> dict:
    """Джоб-обёртка. `run_in_executor_fn` -- для тестов, в проде -- обычный
    loop.run_in_executor (тот же паттерн, что во всех остальных мониторах
    этого проекта, см. bsc_wallet_monitor.py критический регресс 2026-07-15)."""
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)
    return await run_in_executor_fn(sync_all_sync)
