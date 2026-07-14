#!/usr/bin/env bash
# scripts/watchdog.sh -- ПАКЕТ WATCHDOG (владелец, 2026-07-14): доработка
# night_run.sh по урокам §НОЧЬ -- убирает ручные пинки после обрывов API.
#
# Отличия от night_run.sh:
#   1. Перед стартом -- если в ЭТОМ репозитории уже есть живой процесс
#      claude (cwd совпадает с REPO_DIR), НЕ стартует (защита от гонки двух
#      сессий на одном репо), только логирует и выходит. ДЕФЕКТ со стенда
#      (владелец, живой кейс 2026-07-14): исходная версия проверяла ВСЕ
#      claude на машине без учёта cwd -- claude другого проекта (Академия,
#      dr_linessa_bot) блокировал старт watchdog для crypto-bot. Исправлено
#      через `lsof -a -p <PID> -d cwd -Fn` на каждый найденный PID; чужие
#      cwd игнорируются и логируются, но не трогаются никакими сигналами.
#   2. Только foreground, без nohup/& внутри скрипта -- владелец запускает
#      сам в отдельной вкладке терминала (см. одну строку запуска в конце
#      файла).
#   3. Перезапуск после падения/выхода claude -- не "голый" рестарт, а
#      `--resume <текущий session-id>` + стартовое сообщение "Продолжай с
#      точки обрыва по PROGRESS.md и хвосту git log" (тот же принцип, что и
#      в CLAUDE.md "Устойчивость к обрывам API", п.3 -- только
#      автоматизированный, без ожидания владельца).
#   4. logs/watchdog.log с ротацией 10МБ (тот же паттерн, что и
#      night_run.log).
#   5. Lock-файл против двойного запуска. Известный баг co стенда:
#      cleanup(), навешенный на `trap ... EXIT`, сам вызывал `exit 0` --
#      это повторно триггерит EXIT-trap (exit внутри EXIT-обработчика
#      реентерит тот же trap) и может замаскировать реальный код выхода
#      скрипта. Исправлено: cleanup() только чистит lock и НЕ вызывает
#      exit; INT/TERM вызывают cleanup явно и exit'ятся сами, EXIT просто
#      выполняет cleanup и даёт скрипту завершиться естественным кодом.
#   6. Никогда не убивает чужие процессы -- только читает `pgrep`/`ps`.
#
# Запуск (см. также однострочную инструкцию в конце файла):
#   ~/crypto-bot/scripts/watchdog.sh
#   (в отдельной вкладке терминала, БЕЗ nohup -- foreground)
#
# Остановка: Ctrl+C в этой же вкладке (INT) или `kill $(cat /tmp/crypto_bot_watchdog.lock)`
# (TERM) -- оба штатно снимают lock. Не запускать одновременно с
# night_run.sh или интерактивной сессией claude в этом репозитории.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/watchdog.log"
LOCK_FILE="/tmp/crypto_bot_watchdog.lock"
MAX_LOG_BYTES=$((10 * 1024 * 1024))
RESTART_DELAY_SEC=30
RESUME_PROMPT="Продолжай с точки обрыва по PROGRESS.md и хвосту git log"

# Точка внедрения для стендового теста (DoD): по умолчанию -- реальный
# claude CLI. WATCHDOG_CLAUDE_CMD позволяет тестовому стенду подставить
# безопасный процесс-заглушку вместо реального (реальный claude нельзя
# безопасно запускать вложенно из-под самого себя -- живые API-ресурсы,
# риск зависания без интерактивного терминала). В боевом запуске
# переменная не задаётся -- используется настоящий "claude".
CLAUDE_BIN="${WATCHDOG_CLAUDE_CMD:-claude}"
CLAUDE_PROC_NAME="$(basename "$CLAUDE_BIN")"

mkdir -p "$LOG_DIR"

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

rotate_log_if_needed() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null || wc -c < "$LOG_FILE")
    if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
        mv -f "$LOG_FILE" "${LOG_FILE}.1"
    fi
}

# ── п.1: живой claude В ЭТОМ РЕПОЗИТОРИИ -- не стартуем, гонка двух сессий
# запрещена. ДЕФЕКТ со стенда (владелец, живой кейс): pgrep -x claude видит
# ВСЕ процессы claude на Mac, включая другие проекты (например Академию,
# dr_linessa_bot) -- guard блокировал старт watchdog для crypto-bot из-за
# claude, работающего совсем в другом репозитории. Исправлено: для каждого
# найденного PID проверяем реальный cwd через `lsof -a -p <PID> -d cwd -Fn`
# и блокируем старт ТОЛЬКО если cwd == REPO_DIR. Чужие claude (другой cwd)
# игнорируются -- пишем в лог факт обнаружения, но НЕ трогаем процесс
# никакими сигналами.
_pid_cwd() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

live_pids="$(pgrep -x "$CLAUDE_PROC_NAME" 2>/dev/null || true)"
own_repo_pid=""
if [ -n "$live_pids" ]; then
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        pid_cwd="$(_pid_cwd "$pid")"
        if [ "$pid_cwd" = "$REPO_DIR" ]; then
            own_repo_pid="$pid"
        elif [ -n "$pid_cwd" ]; then
            log "обнаружен посторонний claude PID $pid (cwd $pid_cwd) -- не мешает"
        else
            log "обнаружен claude PID $pid, cwd не определён (lsof недоступен/процесс завершился) -- не блокируем по неизвестному cwd"
        fi
    done <<EOF
$live_pids
EOF
fi

if [ -n "$own_repo_pid" ]; then
    log "СТОП: обнаружен живой процесс '$CLAUDE_PROC_NAME' в ЭТОМ репозитории (PID $own_repo_pid, cwd $REPO_DIR) -- watchdog не стартует, чтобы не создать гонку двух сессий на одном репозитории. Дождитесь завершения текущей сессии."
    exit 1
fi

# ── Lock: не даём запустить второй экземпляр watchdog параллельно ──
if [ -f "$LOCK_FILE" ]; then
    old_pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        log "СТОП: watchdog уже запущен (PID $old_pid)"
        exit 1
    fi
    # PID из lock-файла мёртв (краш/reboot без штатного выхода) -- протухший
    # lock, честно чистим и продолжаем, а не блокируем страховку навсегда.
    rm -f "$LOCK_FILE"
fi
echo "$$" > "$LOCK_FILE"

# ── п.5: cleanup только снимает lock, exit НЕ вызывает (см. комментарий
# в шапке файла про баг со стенда) -- INT/TERM зовут cleanup и сами
# завершаются нужным кодом; EXIT просто чистит lock при любом выходе.
cleanup() {
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

cd "$REPO_DIR"

log "watchdog.sh стартовал (PID $$, claude-бинарь: $CLAUDE_BIN)"

# ── текущий session-id этого репозитория (если есть) -- самый свежий
# транскрипт в ~/.claude/projects/<санитизированный-путь>/*.jsonl.
# Санитизация повторяет наблюдаемое поведение Claude Code: любой
# не-алфанумерик символ пути -> "-" (сверено живьём: "/Users/igorgoda/
# crypto-bot" -> "-Users-igorgoda-crypto-bot").
_project_transcript_dir() {
    local sanitized
    sanitized="$(printf '%s' "$REPO_DIR" | sed -E 's/[^a-zA-Z0-9]/-/g')"
    printf '%s/.claude/projects/%s' "$HOME" "$sanitized"
}

_discover_current_session_id() {
    local dir newest
    dir="$(_project_transcript_dir)"
    [ -d "$dir" ] || { echo ""; return; }
    newest="$(ls -t "$dir"/*.jsonl 2>/dev/null | head -1 || true)"
    [ -n "$newest" ] || { echo ""; return; }
    basename "$newest" .jsonl
}

SESSION_ID="$(_discover_current_session_id)"
if [ -n "$SESSION_ID" ]; then
    log "текущая сессия для резюме: $SESSION_ID"
else
    log "текущая сессия не найдена (нет транскриптов) -- первый запуск стартует БЕЗ --resume, id подхватим после него"
fi

while true; do
    rotate_log_if_needed
    if [ -n "$SESSION_ID" ]; then
        log "запуск: $CLAUDE_BIN --dangerously-skip-permissions --resume $SESSION_ID + стартовое сообщение"
        "$CLAUDE_BIN" --dangerously-skip-permissions --resume "$SESSION_ID" "$RESUME_PROMPT"
    else
        log "запуск: $CLAUDE_BIN --dangerously-skip-permissions (новая сессия, id ещё не известен)"
        "$CLAUDE_BIN" --dangerously-skip-permissions
    fi
    exit_code=$?
    rotate_log_if_needed
    log "claude завершился, exit_code=${exit_code}"

    if [ -z "$SESSION_ID" ]; then
        SESSION_ID="$(_discover_current_session_id)"
        [ -n "$SESSION_ID" ] && log "session-id обнаружен по факту: $SESSION_ID"
    fi

    log "пауза ${RESTART_DELAY_SEC}с -- перезапуск с --resume ${SESSION_ID:-<неизвестен, снова без resume>}"
    sleep "$RESTART_DELAY_SEC"
done
