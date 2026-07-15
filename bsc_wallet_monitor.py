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
эндпоинтов блокируют его полностью). Рабочие эндпоинты с ограничением диапазона
блоков на запрос: 1rpc.io/bnb (50) и bsc-mainnet.public.blastapi.io (10, резерв).
eth_call (balanceOf и т.п.) работает и на dataseed-нодах -- используется отдельно.
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
    "0x73d8bd54f7cf5fab43fe4ef40a62d390644946db",  # #3, теперь маркирован BscScan как
    # "Binance: Alpha 2.0 Router Proxy" -- подтверждённый отток -9.03% относительно
    # прошлого снапшота владельца, см. PUMP_REVERSAL_CASES.md
]

ALERT_THRESHOLD_USD = 200_000

RPC_PROVIDERS = [
    {"url": "https://1rpc.io/bnb", "max_range": 50},
    {"url": "https://bsc-mainnet.public.blastapi.io", "max_range": 10},
]
RPC_ETH_CALL = "https://bsc-dataseed.binance.org"  # eth_call работает, eth_getLogs -- нет

POLL_INTERVAL_SEC = 60
REQUEST_TIMEOUT_SEC = 10  # владелец, находка 2026-07-15: жёсткий потолок на КАЖДЫЙ
# сетевой вызов -- см. критический регресс ниже (было до 15с без единого таймаут-бюджета)
MAX_BLOCKS_PER_TICK = 500  # владелец: не пытаться нагнать весь бэклог за один тик --
# при 27+ последовательных отказах 1rpc.io в одном тике (см. живая находка) блокирующий
# цикл без этого предела мог растянуться на минуты. Остаток докатывается следующими
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


def _rpc_call(url: str, method: str, params: list, timeout: int = REQUEST_TIMEOUT_SEC) -> dict:
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


def _wallet_topics() -> list:
    """topics[1] со списком адресов -- JSON-RPC трактует список внутри позиции
    топика как OR, так что один запрос покрывает переводы FROM любого из
    отслеживаемых кошельков одновременно."""
    return [TRANSFER_TOPIC, ["0x" + w[2:].rjust(64, "0").lower() for w in AKE_WATCHED_WALLETS]]


def get_transfer_logs(from_block: int, to_block: int) -> tuple:
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
    обработке и дублированным алертам на следующем тике."""
    topics = _wallet_topics()
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
    to_block = min(latest, from_block + MAX_BLOCKS_PER_TICK - 1)

    logs, incomplete, last_processed_block = await run_in_executor_fn(get_transfer_logs, from_block, to_block)
    price = await run_in_executor_fn(get_ake_price_usd)
    new_count = 0

    for lg in logs:
        ev = decode_transfer_log(lg)
        ev["usd"] = ev["amount"] * price if price > 0 else None
        ev["price_used"] = price
        ev["ts"] = time.time()
        _append_event(ev)
        new_count += 1

        if ev["usd"] is not None and ev["usd"] >= ALERT_THRESHOLD_USD:
            try:
                await send_system_fn(bot, format_alert_text(ev), critical=True)
            except Exception as e:
                log.error(f"bsc_wallet_monitor: send_system failed: {e}")

    # Продвигаем указатель до РЕАЛЬНО обработанной границы (даже при incomplete --
    # partial success не должен приводить к повторной обработке уже покрытых
    # блоков и дублированным алертам на следующем тике).
    if last_processed_block >= from_block - 1:
        state["last_scanned_block"] = max(last_scanned, last_processed_block)

    if incomplete:
        # Владелец: "честный skip тика с log.error и [SYS]-уведомлением раз в
        # 15 мин (не молчать и не копить)".
        log.error(f"bsc_wallet_monitor: тик неполный (обработано до блока "
                  f"{last_processed_block} из {from_block}-{latest}), все RPC-провайдеры отказали")
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
