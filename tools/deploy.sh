#!/usr/bin/env bash
# tools/deploy.sh -- единая точка пуша с деплой-верификацией (владелец,
# ПАКЕТ deploy-resilience, 2026-07-14). Все будущие пуши -- только через
# этот скрипт, см. CLAUDE.md "Устойчивость к обрывам API" п.4.
#
# Что делает:
#   1. rebase-retry push (тот же паттерн, что использовался вручную весь
#      вечер -- конкурентные auto-коммиты живого бота).
#   2. Определяет, затронул ли этот пуш watchPatterns из railway.json
#      (*.py в корне, requirements.txt, Dockerfile, railway.json,
#      backtest/**, tests/**, patches/** -- см. живое чтение файла, не
#      хардкод копии, чтобы не разойтись с реальным конфигом).
#   3. Пауза 3 минуты (как просил владелец) -- деплой должен успеть
#      стартовать и завершиться на обычных коммитах.
#   4. Сравнивает коммит контейнера (`railway deployment list`) с
#      запушенным. SUCCESS -- готово. SKIPPED, когда watchPatterns НЕ
#      затронуты -- ОЖИДАЕМО (journal/docs-only коммит), не проблема,
#      честно логируется как таковое. SKIPPED, когда watchPatterns
#      затронуты -- НЕОЖИДАННО, автоматический минимальный триггер-коммит
#      в bot.py (комментарий с меткой времени) + повтор пуша + повторная
#      проверка (до 2 попыток).
#   5. Результат -- в logs/deploy.log И в Telegram владельцу.
#
# Использование: tools/deploy.sh "текст коммита"
#   (коммит должен быть уже сделан -- git add + git commit ДО вызова
#   скрипта; скрипт только пушит и верифицирует, не создаёт коммит сам,
#   кроме автоматического триггера на шаге 4).

set -uo pipefail
cd "$(dirname "$0")/.."

LOG_FILE="logs/deploy.log"
mkdir -p logs

_log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

_notify_owner() {
    # Best-effort -- отправка не должна ронять скрипт при сбое сети/токена.
    # Текст передаётся через переменную окружения (не интерполяцией в
    # исходник Python) -- безопасно при любых спецсимволах/кавычках.
    DEPLOY_NOTIFY_TEXT="$1" railway run python3 -c "
import os, requests
token = os.environ.get('BOT_TOKEN')
owner_id = int(os.getenv('OWNER_CHAT_ID', '7009350191'))
text = os.environ.get('DEPLOY_NOTIFY_TEXT', '')
if token and text:
    try:
        requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                       json={'chat_id': owner_id, 'text': text}, timeout=10)
    except Exception as e:
        print(f'notify failed: {e}')
" >> "$LOG_FILE" 2>&1 || _log "WARN: Telegram notify failed (see log above)"
}

# ── шаг 1: rebase-retry push ──
_log "=== deploy.sh started ==="
PUSH_OK=0
for i in 1 2 3 4 5; do
    git fetch origin main -q
    if ! git rebase origin/main -q; then
        _log "FATAL: rebase conflict on attempt $i -- manual intervention needed"
        git rebase --abort 2>/dev/null || true
        _notify_owner "deploy.sh: FATAL rebase conflict, нужен ручной разбор"
        exit 1
    fi
    # ДЕФЕКТ (владелец, найдено живьём 2026-07-14): раньше диапазон для
    # watchPatterns-проверки считался ПОСЛЕ push как HEAD~1..HEAD --
    # терял файлы из более ранних коммитов при многокоммитном пуше
    # (7-коммитный пуш дал ложный "watchPatterns hit: no"). Схватываем
    # origin/main ЗДЕСЬ -- сразу после fetch+rebase, ДО push -- это
    # состояние ровно перед пушем ЭТОЙ попытки. После успешного push
    # origin/main == HEAD, диапазон схлопнулся бы в пустой -- поэтому
    # обязательно ДО, не после.
    PRE_PUSH_BASE=$(git rev-parse origin/main)
    PUSH_OUT=$(git push origin main 2>&1)
    if echo "$PUSH_OUT" | grep -q "main -> main"; then
        PUSH_OK=1
        break
    fi
    _log "push attempt $i failed, retrying: $PUSH_OUT"
    sleep 2
done

if [ "$PUSH_OK" -ne 1 ]; then
    _log "FATAL: push failed after 5 attempts"
    _notify_owner "deploy.sh: FATAL push failed после 5 попыток"
    exit 1
fi

PUSHED_COMMIT=$(git rev-parse HEAD)
PUSHED_SHORT=$(git rev-parse --short HEAD)
_log "pushed commit: $PUSHED_COMMIT"

# ── шаг 2: определить, затронуты ли watchPatterns ──
# ВЕСЬ диапазон пуша ($PRE_PUSH_BASE, схваченный ДО push на шаге 1 ..
# HEAD) -- не только последний коммит (см. дефект выше). Сам диапазон
# считает deploy_watch_check.py (тестируемо, git diff внутри Python, не
# полагается на bash-парсинг многострочного вывода).
CHANGED_FILES=$(git diff --name-only "$PRE_PUSH_BASE" HEAD 2>/dev/null || echo "")
_log "changed files in pushed range ($PRE_PUSH_BASE..$PUSHED_COMMIT): $CHANGED_FILES"

WATCH_HIT=$(DEPLOY_WATCH_BASE_REF="$PRE_PUSH_BASE" python3 tools/deploy_watch_check.py)
_log "watchPatterns hit: $WATCH_HIT"

# ── шаг 3: пауза 3 минуты ──
_log "waiting 180s for deploy to start/finish..."
sleep 180

# ── шаг 4: проверка статуса деплоя для запушенного коммита ──
check_deploy_status() {
    local commit="$1"
    railway deployment list --limit 30 --json 2>/dev/null | DEPLOY_TARGET_COMMIT="$commit" python3 -c "
import json, os, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print('UNKNOWN')
    sys.exit(0)
target = os.environ.get('DEPLOY_TARGET_COMMIT', '')
for d in data:
    ch = d.get('meta', {}).get('commitHash', '')
    if isinstance(ch, str) and ch.startswith(target[:8]):
        print(d.get('status', 'UNKNOWN') + '|' + d.get('id', ''))
        sys.exit(0)
print('NOT_FOUND')
"
}

RESULT=$(check_deploy_status "$PUSHED_COMMIT")
STATUS="${RESULT%%|*}"
DEPLOY_ID="${RESULT##*|}"
_log "deploy status for $PUSHED_SHORT: $STATUS (deployment id: ${DEPLOY_ID:-n/a})"

if [ "$STATUS" = "SUCCESS" ]; then
    _log "OK: deployed successfully, commit $PUSHED_SHORT live"
    _notify_owner "deploy.sh: OK, коммит $PUSHED_SHORT задеплоен (SUCCESS)"
    exit 0
fi

if [ "$STATUS" = "BUILDING" ] || [ "$STATUS" = "DEPLOYING" ]; then
    _log "still in progress ($STATUS) after 3 min -- waiting additional 60s"
    sleep 60
    RESULT=$(check_deploy_status "$PUSHED_COMMIT")
    STATUS="${RESULT%%|*}"
    _log "re-check status: $STATUS"
    if [ "$STATUS" = "SUCCESS" ]; then
        _log "OK: deployed successfully (after extra wait), commit $PUSHED_SHORT live"
        _notify_owner "deploy.sh: OK, коммит $PUSHED_SHORT задеплоен (SUCCESS, после доп. паузы)"
        exit 0
    fi
fi

if [ "$STATUS" = "SKIPPED" ]; then
    if [ "$WATCH_HIT" = "no" ]; then
        _log "SKIPPED as EXPECTED -- pushed commit touched no Watch Path files (journal/docs-only)"
        _notify_owner "deploy.sh: SKIPPED ожидаемо ($PUSHED_SHORT -- не код), деплой не требовался"
        exit 0
    fi
    _log "SKIPPED but Watch Path WAS touched -- unexpected, triggering minimal bot.py commit"
    TRIGGER_TS=$(date '+%Y-%m-%dT%H:%M:%S%z')
    echo "# deploy.sh trigger: $TRIGGER_TS (SKIPPED-recovery for $PUSHED_SHORT)" >> bot.py
    git add bot.py
    git commit -q -m "deploy: trigger-коммит по Watch Paths (auto-recovery от SKIPPED $PUSHED_SHORT)"

    TRIGGER_OK=0
    for i in 1 2 3; do
        git fetch origin main -q
        git rebase origin/main -q || { _log "FATAL: rebase conflict on trigger-commit"; break; }
        if git push origin main 2>&1 | grep -q "main -> main"; then
            TRIGGER_OK=1
            break
        fi
        sleep 2
    done

    if [ "$TRIGGER_OK" -ne 1 ]; then
        _log "FATAL: trigger-commit push failed"
        _notify_owner "deploy.sh: FATAL, авто-триггер после SKIPPED не запушился, нужен ручной разбор"
        exit 1
    fi

    TRIGGER_COMMIT=$(git rev-parse HEAD)
    TRIGGER_SHORT=$(git rev-parse --short HEAD)
    _log "trigger commit pushed: $TRIGGER_COMMIT, waiting 180s"
    sleep 180
    RESULT=$(check_deploy_status "$TRIGGER_COMMIT")
    STATUS="${RESULT%%|*}"
    _log "trigger-commit deploy status: $STATUS"
    if [ "$STATUS" = "SUCCESS" ]; then
        _log "OK: recovered via trigger-commit $TRIGGER_SHORT, now live"
        _notify_owner "deploy.sh: восстановлено авто-триггером, $TRIGGER_SHORT задеплоен (SUCCESS)"
        exit 0
    fi
    _log "FATAL: still not SUCCESS after trigger-commit ($STATUS) -- manual intervention needed"
    _notify_owner "deploy.sh: FATAL, после авто-триггера всё ещё $STATUS -- нужен ручной разбор"
    exit 1
fi

_log "FATAL: unexpected status '$STATUS' for $PUSHED_SHORT"
_notify_owner "deploy.sh: FATAL, неожиданный статус '$STATUS' для $PUSHED_SHORT"
exit 1
