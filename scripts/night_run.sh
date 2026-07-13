#!/usr/bin/env bash
# scripts/night_run.sh -- страховка ночной сессии.
#
# Держит `claude --dangerously-skip-permissions --continue` в бесконечном
# цикле под caffeinate (машина не засыпает, пока сессия идёт): если процесс
# оборвался или упал -- пауза 15с и перезапуск. Каждый запуск/перезапуск
# логируется в logs/night_run.log с датой-временем и кодом выхода
# предыдущего процесса. Lock-файл /tmp/night_run.lock не даёт запустить
# второй экземпляр параллельно в этом же репозитории.
#
# Запуск: nohup ~/crypto-bot/scripts/night_run.sh &
# Остановка и правила -- см. CLAUDE.md, раздел "Ночной запуск".

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/night_run.log"
LOCK_FILE="/tmp/night_run.lock"
MAX_LOG_BYTES=$((10 * 1024 * 1024))
RESTART_DELAY_SEC=15

mkdir -p "$LOG_DIR"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_FILE"
}

rotate_log_if_needed() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null || wc -c < "$LOG_FILE")
    if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
        mv -f "$LOG_FILE" "${LOG_FILE}.1"
    fi
}

# ── Lock: не даём запустить второй экземпляр параллельно в этом репо ───────
if [ -f "$LOCK_FILE" ]; then
    old_pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "already running (PID $old_pid)"
        exit 1
    fi
    # PID из lock-файла мёртв (краш/reboot без штатного выхода) -- протухший
    # lock, честно чистим и продолжаем, а не блокируем страховку навсегда.
    rm -f "$LOCK_FILE"
fi
echo "$$" > "$LOCK_FILE"

cleanup() {
    rm -f "$LOCK_FILE"
    exit 0
}
trap cleanup EXIT INT TERM

cd "$REPO_DIR"

log "night_run.sh стартовал (PID $$)"

while true; do
    rotate_log_if_needed
    log "запуск: caffeinate -dims claude --dangerously-skip-permissions --continue"
    caffeinate -dims claude --dangerously-skip-permissions --continue
    exit_code=$?
    rotate_log_if_needed
    log "claude завершился, exit_code=${exit_code} -- пауза ${RESTART_DELAY_SEC}с и перезапуск"
    sleep "$RESTART_DELAY_SEC"
done
