"""bsc_wallet_monitor.py -- мониторинг оттока топ-кошельков AKE на BSC через
публичный JSON-RPC (владелец, 2026-07-15: Etherscan free-план не покрывает BSC
ни для одного эндпоинта -- проверено живьём, tokentx/tokenholderlist/balance
всё вернуло "Free API access is not supported for this chain" -- платный
тариф НЕ покупаем, новых ключей не создаём).

Источник кошельков: владелец скопировал полные адреса со страницы холдеров
BscScan (https://bscscan.com/token/0x2c3a8Ee94dDD97244a93Bc48298f97d2C412F7Db#balances),
2026-07-15. Полный разбор кейса, живая проверка RPC-эндпоинтов и подтверждённый
отток кошелька #3 -- knowledge/PUMP_REVERSAL_CASES.md, кейс 1.

eth_getLogs НЕ работает на bsc-dataseed*.binance.org (проверено живьём, все 5
эндпоинтов блокируют его полностью). eth_call (balanceOf и т.п.) работает и на
dataseed-нодах -- используется отдельно (RPC_ETH_CALL).

Владелец, 2026-07-16: QuickNode (QUICKNODE_BSC_URL, платный выделенный эндпоинт
в Railway Variables) -- primary с возвратом MAX_BLOCKS_PER_TICK до 50; ранее
использовавшийся 1rpc.io/bnb выведен из ротации (нестабилен под нагрузкой этого
кейса -- живой инцидент 2026-07-15, см. _is_rate_limit_error докстринг ниже).
bsc-mainnet.public.blastapi.io остаётся единственным fallback-провайдером.
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

AKE_CONTRACT = "0x2c3a8ee94ddd97244a93bc48298f97d2c412f7db"  # источник: CoinGecko
# platforms.binance-smart-chain для coin id "akedo", сверено с адресом владельца
AKE_DECIMALS = 18
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Полные адреса -- владелец, разово со страницы холдеров BscScan, 2026-07-15.
# Сверены нами против ранее данных усечённых форм (начало+конец) -- совпадение 1-в-1.
AKE_WATCHED_WALLETS = [
    "0x27333Bd8c321a263B0565e69eea3b736b9d1f42c",  # #1, 18.3671% supply на момент фиксации
    "0xD229b65d50E412cC3C394233E7a53A1DAc4dA457",  # #2, 15.0000% supply на момент фиксации
    "0x73d8bd54f7cf5fab43fe4ef40a62d390644946db",  # #3 -- подтверждено живьём на BscScan
    # (2026-07-16, форк-исследование): публичный тег "Binance: Alpha 2.0 Router Proxy" --
    # роутер-контракт биржи (>$36M чужих средств в транзите по 115+ токенам), НЕ личная
    # инсайдерская позиция -- пересмотр интерпретации "18-15% supply у инсайдера" для
    # ЭТОГО конкретного адреса, см. PUMP_REVERSAL_CASES.md. Продолжаем следить как за
    # шлюзом раздачи AKE (наблюдаемый факт: транши на свежие адреса продолжаются
    # независимо от природы адреса), не как за "инсайдером".
    "0x561beec8614eaa3038f03bce7cb4f72b3d271d8e",  # "Прокладка №1" -- владелец, 2026-07-16:
    # BscScan подтвердил живьём -- создан ~19ч назад, funded 0xfA23434A...835eE0428, серия
    # Dag Swap -> OKX DEX Router 2, вывод в BUSD-T, баланс $0 (однодневка, сливает через DEX
    # сразу, не копит)
    "0x0a960739374ffb06772a4bb0d1ef96c5a8ae8e17",  # "Прокладка №2" -- тот же паттерн,
    # funded 0x25370535...0fE0A28DF, тот же владелец, 2026-07-16
]

AKE_WATCHED_RECIPIENTS = [
    "0x6aba0315493b7e6989041c91181337b662fb1b90",  # владелец, 2026-07-16: получатель траншей
    # от 0x73d8 сегодня 13:5x -- потенциальный "второй цикл" раздачи, следим как за
    # получателем (topics[2], НЕ topics[1] -- см. _recipient_topics())
]
RECIPIENT_ALERT_THRESHOLD_USD = 10_000  # владелец: "если на него пойдут суммы >$10K --
# алерт critical «второй цикл?»" -- отдельный, более чувствительный порог, чем общий
# ALERT_THRESHOLD_USD (см. ниже), намеренно ниже: ранний сигнал важнее лишнего алерта

ALERT_THRESHOLD_USD = 200_000

def _build_rpc_providers() -> list:
    """QuickNode -- primary, если QUICKNODE_BSC_URL задан (владелец, 2026-07-16,
    выделенный платный эндпоинт в Railway Variables); blastapi -- всегда
    fallback. Функция, а не константа-на-импорте -- чтобы тесты могли
    monkeypatch'ить os.environ ДО вызова и получить предсказуемый список без
    завязки на реальные Railway-переменные окружения процесса.

    max_range=5 -- живая находка сразу после первого деплоя (2026-07-16):
    аккаунт на QuickNode discover-плане, eth_getLogs код -32615 "limited to
    a 5 range" на КАЖДОМ чанке с изначально предполагавшимся max_range=50 --
    тик всё равно докатывался (fallback на blastapi отрабатывал), но с
    гарантированной лишней ошибкой на каждый 50-блочный чанк вместо чистого
    успеха. 5 -- подтверждённый живьём потолок ЭТОГО плана, не догадка."""
    providers = []
    quicknode_url = os.environ.get("QUICKNODE_BSC_URL")
    if quicknode_url:
        providers.append({"url": quicknode_url, "max_range": 5})
    else:
        log.error("bsc_wallet_monitor: QUICKNODE_BSC_URL не задан -- работаю только на fallback-провайдере")
    providers.append({"url": "https://bsc-mainnet.public.blastapi.io", "max_range": 10})
    return providers


RPC_PROVIDERS = _build_rpc_providers()
RPC_ETH_CALL = "https://bsc-dataseed.binance.org"  # eth_call работает, eth_getLogs -- нет

# Владелец, 2026-07-16, бюджет-контроль QuickNode credits -- проверено живьём
# (https://www.quicknode.com/api-credits/bsc, "All methods*" = 20 credits):
# eth_blockNumber/eth_getLogs/eth_call на BSC все под флэт-тарифом 20
# credits/вызов (ни один не входит в исключения "Large Calls" x4 или
# "Advanced APIs" x2 -- см. https://www.quicknode.com/api-credits). Точный
# месячный лимит ИМЕННО тарифа этого аккаунта ("discover") публично не
# сверен (маркетинговые страницы дают разные цифры для разных тиров, ни
# одна явно не названа "discover") -- честно не гадаем, используем порог
# тревоги, заданный владельцем напрямую (MONTHLY_CREDIT_BUDGET), а не
# вычисленный из документации.
QUICKNODE_CREDITS_PER_CALL = 20
MONTHLY_CREDIT_BUDGET = 8_000_000  # владелец: "если прогноз >8M credits"
REDUCED_MAX_BLOCKS_PER_TICK = 20  # владелец: "например, только последние 20 блоков"
_BUDGET_MIN_SAMPLE_SEC = 300  # честный "н/д", пока не накопилось хотя бы 5 мин наблюдения

POLL_INTERVAL_SEC = 60
REQUEST_TIMEOUT_SEC = 10  # владелец, находка 2026-07-15: жёсткий потолок на КАЖДЫЙ
# сетевой вызов -- см. критический регресс ниже (было до 15с без единого таймаут-бюджета)
MAX_BLOCKS_PER_TICK = 50  # владелец, 2026-07-16: возвращено на 50 после перехода на
# QuickNode как primary (выделенный платный эндпоинт, не публичный rate-limited узел
# вроде бывшего 1rpc.io -- см. докстринг модуля). Остаток докатывается следующими
# тиками (last_scanned_block продвигается только до обработанной границы, не до latest)
SOURCE_DOWN_NOTIFY_INTERVAL_SEC = 15 * 60  # владелец: "skip тика ... с [SYS]-
# уведомлением раз в 15 мин (не молчать и не копить)"

_JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")
STATE_FILE = os.path.join(_JOURNAL_DIR, "bsc_wallet_monitor_state.json")
EVENTS_FILE = os.path.join(_JOURNAL_DIR, "bsc_wallet_events.json")
EVENTS_KEEP_MAX = 2000  # простая защита от неограниченного роста активного файла


def _atomic_write_json(path: str, obj) -> bool:
    """Тот же паттерн, что и shadow_engine._atomic_write_json -- временный файл в
    той же директории + os.replace (атомарно на POSIX)."""
    tmp_path = f"{path}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log.error(f"bsc_wallet_monitor: atomic write to {path} failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _atomic_write_json(STATE_FILE, state)


def _load_events() -> list:
    try:
        with open(EVENTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _append_event(event: dict) -> None:
    events = _load_events()
    events.append(event)
    if len(events) > EVENTS_KEEP_MAX:
        events = events[-EVENTS_KEEP_MAX:]
    _atomic_write_json(EVENTS_FILE, events)


QUICKNODE_CALL_LOG_FILE = os.path.join(_JOURNAL_DIR, "quicknode_call_log.json")


def _load_call_log() -> dict:
    try:
        with open(QUICKNODE_CALL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _record_quicknode_call(method: str) -> None:
    """Владелец, 2026-07-16 (бюджет-контроль credits): считает вызовы ТОЛЬКО
    к QuickNode (не к blastapi-фолбэку -- у него нет кредитного бюджета в
    этом контексте). journal/quicknode_call_log.json -- тот же паттерн, что
    EVENTS_FILE (эфемерный диск, счёт "с последнего рестарта процесса", не
    маскируем это в отчёте, см. daily_metrics.py докстринг про тот же
    класс ограничения для whale/level-watch событий)."""
    log_data = _load_call_log()
    if "process_start_ts" not in log_data:
        log_data["process_start_ts"] = time.time()
        log_data["calls"] = {}
    calls = log_data.setdefault("calls", {})
    calls[method] = calls.get(method, 0) + 1
    _atomic_write_json(QUICKNODE_CALL_LOG_FILE, log_data)


_budget_throttled = False  # module-level -- раз включённый throttle держится до рестарта
# процесса (не мигает туда-обратно на пограничных значениях прогноза каждый тик)


def quicknode_budget_report(now_ts: float = None) -> dict:
    """Прогноз месячного расхода QuickNode credits по фактической скорости
    вызовов с последнего рестарта процесса (экстраполяция, не факт за
    календарный месяц -- честно помечено в отчёте). {"ok": False, "reason":
    ...} до накопления _BUDGET_MIN_SAMPLE_SEC наблюдения -- не гадаем на
    первых секундах жизни процесса."""
    now = now_ts if now_ts is not None else time.time()
    log_data = _load_call_log()
    start_ts = log_data.get("process_start_ts")
    if start_ts is None:
        return {"ok": False, "reason": "ни одного вызова QuickNode ещё не было"}
    elapsed_sec = now - start_ts
    if elapsed_sec < _BUDGET_MIN_SAMPLE_SEC:
        return {"ok": False, "reason": f"недостаточно данных с рестарта ({elapsed_sec:.0f}с < {_BUDGET_MIN_SAMPLE_SEC}с)"}
    calls = log_data.get("calls", {})
    total_calls = sum(calls.values())
    credits_used = total_calls * QUICKNODE_CREDITS_PER_CALL
    credits_per_day = credits_used / elapsed_sec * 86400
    credits_per_month = credits_per_day * 30
    return {
        "ok": True,
        "elapsed_hours": elapsed_sec / 3600,
        "calls_by_method": dict(calls),
        "credits_used_since_restart": credits_used,
        "credits_per_day_projected": credits_per_day,
        "credits_per_month_projected": credits_per_month,
        "over_budget": credits_per_month > MONTHLY_CREDIT_BUDGET,
        "throttled": _budget_throttled,
    }


def _effective_max_blocks_per_tick() -> int:
    """Владелец, 2026-07-16: "если прогноз >8M credits -- сократить глубину
    тика". Throttle включается один раз за жизнь процесса и держится (не
    переключается туда-обратно на каждом тике вокруг порога) -- сработавшее
    предупреждение остаётся видимым в вечернем брифе до следующего рестарта,
    честно, а не молчит после случайного возврата прогноза под порог."""
    global _budget_throttled
    if _budget_throttled:
        return REDUCED_MAX_BLOCKS_PER_TICK
    report = quicknode_budget_report()
    if report["ok"] and report["over_budget"]:
        _budget_throttled = True
        log.error(f"bsc_wallet_monitor: прогноз QuickNode credits/мес "
                  f"{report['credits_per_month_projected']:,.0f} > бюджета "
                  f"{MONTHLY_CREDIT_BUDGET:,.0f} -- сокращаю глубину тика до "
                  f"{REDUCED_MAX_BLOCKS_PER_TICK} блоков")
        return REDUCED_MAX_BLOCKS_PER_TICK
    return MAX_BLOCKS_PER_TICK


def _rpc_call(url: str, method: str, params: list, timeout: int = REQUEST_TIMEOUT_SEC) -> dict:
    if url == os.environ.get("QUICKNODE_BSC_URL"):
        _record_quicknode_call(method)
    r = requests.post(url, json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1}, timeout=timeout)
    return r.json()


def _is_rate_limit_error(d: dict) -> bool:
    """Классы ошибок, для которых имеет смысл сразу считать провайдер "мёртвым до
    конца текущего тика" вместо повторной попытки на КАЖДОМ следующем чанке --
    владелец, критический регресс 2026-07-15: 1rpc.io code -32001 "usage limit"
    повторялся 27+ раз подряд в одном тике, потому что get_transfer_logs() перед
    каждым новым чанком заново пробовал уже известный мёртвый провайдер первым."""
    err = d.get("error") or {}
    code = err.get("code")
    msg = str(err.get("message") or "").lower()
    return code in (-32001, -32005) or "usage limit" in msg or "limit exceeded" in msg or "rate limit" in msg


def get_latest_block():
    """Возвращает (block_number, provider_dict) от первого ответившего провайдера,
    (None, None) если все отказали -- вызывающий код обязан честно пропустить цикл,
    не выдумывать номер блока."""
    for prov in RPC_PROVIDERS:
        try:
            d = _rpc_call(prov["url"], "eth_blockNumber", [])
            if "result" in d:
                return int(d["result"], 16), prov
        except Exception as e:
            log.info(f"bsc_wallet_monitor: {prov['url']} eth_blockNumber failed: {e}")
    return None, None


def _addr_topic(addr: str) -> str:
    return "0x" + addr[2:].rjust(64, "0").lower()


def _wallet_topics() -> list:
    """topics[1] со списком адресов -- JSON-RPC трактует список внутри позиции
    топика как OR, так что один запрос покрывает переводы FROM любого из
    отслеживаемых кошельков одновременно."""
    return [TRANSFER_TOPIC, [_addr_topic(w) for w in AKE_WATCHED_WALLETS]]


def _recipient_topics() -> list:
    """Владелец, 2026-07-16 (AKE-расследование, задача #2): topics[2] --
    ловит переводы TO отслеживаемых получателей, ОТ ЛЮБОГО отправителя
    (topics[1]=None -- "любое значение" в этой позиции JSON-RPC). Отдельный
    запрос от _wallet_topics() -- та фильтрует по FROM, эта по TO, разные
    позиции topics, объединить в один список нельзя (AND между позициями,
    не OR)."""
    return [TRANSFER_TOPIC, None, [_addr_topic(w) for w in AKE_WATCHED_RECIPIENTS]]


def get_transfer_logs(from_block: int, to_block: int, topics: list = None) -> tuple:
    """Transfer-логи AKE от отслеживаемых кошельков в диапазоне блоков, с
    fallback между провайдерами и авто-чанкингом под лимит диапазона каждого.
    Если ВСЕ провайдеры отказали для чанка -- честно пропускает его с
    log.error(), не падает и не выдумывает данные за пропущенный диапазон.

    Владелец, критический регресс 2026-07-15 (bsc_wallet_monitor не отмечался
    5+ мин, "Радар без данных" одновременно): 1rpc.io поймал usage-лимит (-32001)
    и КАЖДЫЙ следующий чанк всё равно пробовал его первым -- 27+ идентичных
    отказов подряд в одном тике, блокирующих event loop (см. check_ake_wallets).
    Фикс: провайдер, отказавший с rate-limit-классом ошибки, помечается мёртвым
    ДО КОНЦА ЭТОГО ВЫЗОВА (`dead_providers`) -- остальные чанки сразу идут к
    следующему провайдеру в списке, не тратя время/лимит на повтор.

    Возвращает (logs, incomplete: bool, last_processed_block: int) -- incomplete=True,
    если обработка остановилась ДО to_block из-за того, что все провайдеры
    отказали (для честного [SYS]-уведомления вызывающей стороной);
    last_processed_block -- ПОСЛЕДНИЙ блок, реально покрытый успешным запросом
    (< from_block-1, если не покрыт вообще ни один) -- вызывающий код продвигает
    state ровно до этой границы, чтобы partial-success не приводил к повторной
    обработке и дублированным алертам на следующем тике.

    `topics` -- владелец, 2026-07-16 (задача #2): опционально принимает
    готовый topics-массив (например _recipient_topics()) вместо дефолтного
    _wallet_topics() -- та же функция обслуживает и sender-watch, и
    recipient-watch запросы, вся chunking/fallback-логика не дублируется."""
    topics = topics if topics is not None else _wallet_topics()
    all_logs = []
    dead_providers = set()
    last_processed_block = from_block - 1
    b = from_block
    while b <= to_block:
        got = False
        for prov in RPC_PROVIDERS:
            if prov["url"] in dead_providers:
                continue
            end = min(b + prov["max_range"] - 1, to_block)
            try:
                d = _rpc_call(prov["url"], "eth_getLogs", [{
                    "fromBlock": hex(b), "toBlock": hex(end),
                    "address": AKE_CONTRACT, "topics": topics,
                }])
                if "result" in d:
                    all_logs.extend(d["result"])
                    b = end + 1
                    last_processed_block = end
                    got = True
                    break
                log.info(f"bsc_wallet_monitor: {prov['url']} getLogs {b}-{end}: {d.get('error')}")
                if _is_rate_limit_error(d):
                    dead_providers.add(prov["url"])
                    log.info(f"bsc_wallet_monitor: {prov['url']} помечен мёртвым до конца этого тика")
            except Exception as e:
                log.info(f"bsc_wallet_monitor: {prov['url']} getLogs {b}-{end} exception: {e}")
        if len(dead_providers) >= len(RPC_PROVIDERS):
            log.error(f"bsc_wallet_monitor: ВСЕ провайдеры мертвы на блоке {b}, прерываю обработку тика")
            break
        if not got:
            skip_end = min(b + RPC_PROVIDERS[0]["max_range"] - 1, to_block)
            log.error(f"bsc_wallet_monitor: все провайдеры отказали для блоков {b}-{skip_end}, пропускаю")
            last_processed_block = skip_end
            b = skip_end + 1
    incomplete = b <= to_block
    return all_logs, incomplete, last_processed_block


def decode_transfer_log(lg: dict) -> dict:
    topics = lg["topics"]
    frm = "0x" + topics[1][-40:]
    to = "0x" + topics[2][-40:]
    raw = int(lg["data"], 16)
    amount = raw / (10 ** AKE_DECIMALS)
    return {
        "block": int(lg["blockNumber"], 16),
        "tx": lg["transactionHash"],
        "from": frm,
        "to": to,
        "amount": amount,
    }


def get_ake_price_usd() -> float:
    """Живая цена AKE (Bybit linear). Честный 0.0 при сбое -- вызывающий код
    должен относиться к этому как к "цена н/д", НЕ считать несуществующий
    $0-перевод автоматически ниже порога."""
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": "linear", "symbol": "AKEUSDT"}, timeout=8)
        d = r.json()
        lst = d.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        log.info(f"bsc_wallet_monitor: price fetch failed: {e}")
    return 0.0


def format_alert_text(event: dict) -> str:
    return (
        f"📤 AKE: разгрузка\n"
        f"Кошелёк {event['from']} перевёл {event['amount']:,.0f} AKE "
        f"(~${event['usd']:,.0f}) на {event['to']}\n"
        f"Блок {event['block']}, tx {event['tx']}"
    )


def format_recipient_alert_text(event: dict) -> str:
    """Владелец, 2026-07-16 (задача #2): отдельный текст для транша НА
    отслеживаемый адрес-получатель (AKE_WATCHED_RECIPIENTS) -- "второй
    цикл?" формулировка, честно с вопросом (гипотеза, не факт)."""
    return (
        f"🚨 AKE: транш на отслеживаемый адрес -- возможен второй цикл?\n"
        f"{event['from']} -> {event['to']}: {event['amount']:,.0f} AKE "
        f"(~${event['usd']:,.0f})\n"
        f"Блок {event['block']}, tx {event['tx']}"
    )


async def check_ake_wallets(bot, send_system_fn=None, run_in_executor_fn=None) -> int:
    """Джоб (scheduler.add_job, interval POLL_INTERVAL_SEC): сканирует новые
    блоки с последнего прогона (ограничено MAX_BLOCKS_PER_TICK за раз -- остаток
    докатывается следующими тиками), ищет Transfer от отслеживаемых кошельков,
    ВСЕ совпадения пишет в shadow-журнал (EVENTS_FILE), критический алерт
    (send_system critical=True -- оба канала по П-Каналы) при сумме перевода
    >= ALERT_THRESHOLD_USD. `send_system_fn`/`run_in_executor_fn` внедряются для
    тестов, в проде берутся из bot.py/asyncio (локальный импорт -- без цикла на
    уровне модуля). Возвращает число новых событий (для тестов/логов).

    Владелец, критический регресс 2026-07-15: ВСЕ блокирующие сетевые вызовы
    (get_latest_block/get_transfer_logs/get_ake_price_usd -- синхронные
    requests.post/get) теперь идут через run_in_executor -- раньше они
    выполнялись СИНХРОННО прямо внутри этой async-корутины, и шторм 1rpc.io
    rate-limit ошибок (десятки последовательных попыток без разрыва) блокировал
    ВЕСЬ event loop бота на секунды-минуты -- то же самое время, когда сломался
    "[SYS] Радар без данных" (тот же класс: другой job не мог получить
    управление, пока этот синхронно жрал event loop)."""
    if send_system_fn is None:
        import bot as bot_module
        send_system_fn = bot_module.send_system
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)

    latest, prov = await run_in_executor_fn(get_latest_block)
    if latest is None:
        log.error("bsc_wallet_monitor: ни один RPC-провайдер не ответил на eth_blockNumber")
        return 0
    log.info(f"bsc_wallet_monitor: тик через {prov['url']}, latest={latest}")

    state = _load_state()
    last_scanned = state.get("last_scanned_block")
    if last_scanned is None:
        # Первый запуск -- не сканируем историю, только вперёд от текущего блока
        # (избегаем случайного массового бэктеста при первом деплое).
        state["last_scanned_block"] = latest
        _save_state(state)
        log.info(f"bsc_wallet_monitor: первый запуск, старт с блока {latest}")
        return 0

    from_block = last_scanned + 1
    if from_block > latest:
        return 0
    to_block = min(latest, from_block + _effective_max_blocks_per_tick() - 1)

    logs, incomplete, last_processed_block = await run_in_executor_fn(get_transfer_logs, from_block, to_block)

    # Владелец, 2026-07-16 (AKE-расследование, задача #2): отдельный запрос
    # topics[2] -- ловит переводы TO отслеживаемых получателей (AKE_WATCHED_
    # RECIPIENTS) от ЛЮБОГО отправителя, не только watched-кошельков. Тот же
    # [from_block, to_block] диапазон, отдельный вызов -- topics[1]/topics[2]
    # это AND между позициями в JSON-RPC, объединить в один запрос нельзя.
    recipient_logs, r_incomplete, r_last_processed_block = [], False, from_block - 1
    if AKE_WATCHED_RECIPIENTS:
        recipient_logs, r_incomplete, r_last_processed_block = await run_in_executor_fn(
            get_transfer_logs, from_block, to_block, _recipient_topics())

    price = await run_in_executor_fn(get_ake_price_usd)
    new_count = 0

    # Дедуп по (tx, from, to, amount) -- перевод FROM watched-кошелька TO
    # watched-получателя попал бы в ОБА запроса иначе (напр. 0x73d8 -> 0x6aba).
    seen_keys = set()
    for lg in logs + recipient_logs:
        ev = decode_transfer_log(lg)
        key = (ev["tx"], ev["from"], ev["to"], ev["amount"])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        ev["usd"] = ev["amount"] * price if price > 0 else None
        ev["price_used"] = price
        ev["ts"] = time.time()
        _append_event(ev)
        new_count += 1

        to_is_watched_recipient = ev["to"] in {w.lower() for w in AKE_WATCHED_RECIPIENTS}
        if to_is_watched_recipient and ev["usd"] is not None and ev["usd"] >= RECIPIENT_ALERT_THRESHOLD_USD:
            try:
                await send_system_fn(bot, format_recipient_alert_text(ev), critical=True)
            except Exception as e:
                log.error(f"bsc_wallet_monitor: send_system (recipient) failed: {e}")
        elif ev["usd"] is not None and ev["usd"] >= ALERT_THRESHOLD_USD:
            try:
                await send_system_fn(bot, format_alert_text(ev), critical=True)
            except Exception as e:
                log.error(f"bsc_wallet_monitor: send_system failed: {e}")

    # Продвигаем указатель до РЕАЛЬНО обработанной границы -- МИНИМУМ из двух
    # запросов (sender/recipient могли частично отказать в разных местах),
    # не должно пропустить блок, который НЕ покрыт хотя бы одним из них.
    combined_last_processed = min(last_processed_block, r_last_processed_block)
    incomplete = incomplete or r_incomplete
    if combined_last_processed >= from_block - 1:
        state["last_scanned_block"] = max(last_scanned, combined_last_processed)

    if incomplete:
        # Владелец: "честный skip тика с log.error и [SYS]-уведомлением раз в
        # 15 мин (не молчать и не копить)".
        log.error(f"bsc_wallet_monitor: тик неполный (обработано до блока "
                  f"{combined_last_processed} из {from_block}-{latest}), все RPC-провайдеры отказали")
        last_notify = state.get("last_source_down_notify_ts", 0)
        if time.time() - last_notify >= SOURCE_DOWN_NOTIFY_INTERVAL_SEC:
            try:
                await send_system_fn(bot, "⚠️ AKE-поллер: все RPC-провайдеры (1rpc.io/blastapi) "
                                           "отказали -- ончейн-мониторинг кошельков временно "
                                           "недоступен, повторные попытки продолжаются", critical=True)
            except Exception as e:
                log.error(f"bsc_wallet_monitor: не удалось отправить honest down-notify: {e}")
            state["last_source_down_notify_ts"] = time.time()
    state["last_run_ts"] = time.time()
    _save_state(state)
    return new_count
