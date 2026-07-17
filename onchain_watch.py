"""onchain_watch.py -- мониторинг разлока BANK (Lorenzo Protocol, BSC) на биржи
(владелец, 2026-07-17, срочный наряд -- разлок $2.08M / ~6% mcap сегодня, см.
bank_setup_monitor.UNLOCK_DATE_ISO).

Контракт 0x3aee7602b612de36088f3ffed8c8f10e86ebf2bf -- владелец. Живьём
подтверждён (2026-07-17, CoinGecko contract lookup): id=lorenzo-protocol,
platform=binance-smart-chain, decimal_place=18.

Изначальный ТЗ владельца (onchain_ake.py) предполагал Etherscan V2 tokentx API.
Живая проверка (2026-07-17, тот же ETHERSCAN_API_KEY из Railway) вернула ТОТ ЖЕ
отказ, что уже задокументирован в bsc_wallet_monitor.py (2026-07-15): "Free API
access is not supported for this chain" для BSC на текущем тарифе Etherscan --
не гадаю, не притворяюсь, что V2-эндпоинт покрывает то, что не покрывал V1.
Вместо tokentx использую уже проверенный живьём путь bsc_wallet_monitor.py --
eth_getLogs через QuickNode/blastapi JSON-RPC. Импортирую RPC_PROVIDERS/
_rpc_call/get_transfer_logs/get_latest_block ОТТУДА (не дублирую) -- в
частности, ОБЩИЙ счётчик QuickNode credits (_record_quicknode_call считается
внутри _rpc_call) теперь считает вызовы ОБОИХ мониторов (AKE и BANK) в один
бюджет (bsc_wallet_monitor.MONTHLY_CREDIT_BUDGET) -- если бюджет станет тесным
на двух мониторах разом, это увидит quicknode_budget_report() владельца.

Владелец, 2026-07-17: конкретных адресов-получателей разлока НЕ продиктовано
(в отличие от AKE, где владелец вручную скопировал холдеров с BscScan) --
вместо watch-листа конкретных адресов слежу за ЛЮБЫМ крупным переводом BANK
(>= LARGE_TRANSFER_THRESHOLD_BANK) прямо с контракта или дальше по цепочке:
  1. Крупный перевод С контракта (or любой адрес) -- получатель добавляется в
     tracked-set (persist в STATE_FILE), тихий shadow-лог, БЕЗ алерта (первый
     хоп сам по себе не значим -- разлок может идти через несколько
     промежуточных адресов, как было с AKE-прокладками).
  2. Перевод С tracked-адреса НА известный CEX-адрес депозита -- critical
     алерт "получатель разлока -> биржа" (высокий приоритет, вне зависимости
     от суммы -- сам факт значим, тот же принцип, что и DEX_ROUTER_ADDRESSES
     в bsc_wallet_monitor.py).
  3. Перевод С ЛЮБОГО (не обязательно tracked) адреса НА известный CEX выше
     DIRECT_TO_CEX_ALERT_THRESHOLD_USD -- отдельный, менее приоритетный алерт
     (может быть обычная торговля, не обязательно разлок, честно
     промаркирован как "предположение").

CEX-адреса депозита (BSC) -- сверены живым web-поиском BscScan-тегов
2026-07-17 (заголовок страницы BscScan = публичный тег, тот же класс
источника, что и DEX_ROUTER_ADDRESSES в bsc_wallet_monitor.py). Gate.io --
живой BSC-адрес НЕ найден в этом раунде проверки, честно отсутствует в списке
(не гадаю), покрытие CEX неполное -- см. CEX_DEPOSIT_ADDRESSES докстринг.
"""
import json
import logging
import os
import time

import requests

import bsc_wallet_monitor as _bwm

log = logging.getLogger(__name__)

BANK_CONTRACT = "0x3aee7602b612de36088f3ffed8c8f10e86ebf2bf"  # владелец, 2026-07-17;
# сверено живьём CoinGecko contract-lookup -> id=lorenzo-protocol, platform=
# binance-smart-chain (chainid=56)
BANK_DECIMALS = 18  # CoinGecko detail_platforms.binance-smart-chain.decimal_place,
# сверено живьём 2026-07-17
TRANSFER_TOPIC = _bwm.TRANSFER_TOPIC  # тот же стандартный keccak256(
# "Transfer(address,address,uint256)"), не специфичен для токена

# Владелец: цикл 5 мин (не 1 мин, как у bsc_wallet_monitor/AKE) -- разлок
# менее частый/срочный по темпу событий, чем текущий AKE-кейс
POLL_INTERVAL_SEC = 5 * 60
REQUEST_TIMEOUT_SEC = 10  # владелец, тот же жёсткий потолок на КАЖДЫЙ сетевой
# вызов, что и в bsc_wallet_monitor.py (критический регресс #240)
MAX_BLOCKS_PER_TICK = 50  # тот же порядок, что и bsc_wallet_monitor.py; при 5-
# минутном цикле BSC (~3с/блок) это ~100 блоков в реальности -- остаток
# докатывается следующим тиком, как и там

LARGE_TRANSFER_THRESHOLD_BANK = 50_000  # владелец не задавал точный порог --
# рабочая эвристика: 40.72M BANK / общий разлок, 50K BANK (~$3.6K по цене
# 0.07196, живая котировка Bybit 2026-07-17) -- заметная доля пути между
# распределяющим адресом и биржей, не микро-шум; ПОДЛЕЖИТ пересмотру
# владельцем на живых данных первого дня, честно отмечено как эвристика,
# не как заданный владельцем порог
DIRECT_TO_CEX_ALERT_THRESHOLD_USD = 20_000  # ниже приоритет, чем tracked-
# recipient -> CEX (см. докстринг модуля, случай 3)

# CEX-адреса депозита (BSC) -- владелец: "сверь по публичным тегам". Источник:
# живой web-поиск BscScan-тегов 2026-07-17 (страница BscScan публикует тег в
# заголовке страницы адреса, тот же класс источника, что и
# DEX_ROUTER_ADDRESSES в bsc_wallet_monitor.py). Каждый адрес ниже -- реальный
# существующий тег BscScan на момент проверки, не догадка по паттерну имени.
CEX_DEPOSIT_ADDRESSES = {
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance: Hot Wallet 20",
    "0x631fc1ea2270e98fbd9d92658ece0f5a269aa161": "Binance: Hot Wallet",
    "0xcd5f3c15120a1021155174719ec5fcf2c75adf5b": "KuCoin: Hot Wallet 1",
    "0x53f78a071d04224b8e254e243fffc6d9f2f3fa23": "KuCoin: Hot Wallet 2",
    "0x4982085c9e2f89f2ecb8131eca71afad896e89cb": "MEXC 13",
    # Gate.io -- НЕ найден живьём в этом раунде проверки (2026-07-17), честно
    # отсутствует, покрытие неполное. Прямые переводы на Gate.io не будут
    # пойманы этим списком -- ограничение известно, не скрыто.
}

_JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")
STATE_FILE = os.path.join(_JOURNAL_DIR, "onchain_watch_state.json")
EVENTS_FILE = os.path.join(_JOURNAL_DIR, "onchain_watch_events.json")
EVENTS_KEEP_MAX = 2000
TRACKED_RECIPIENTS_KEEP_MAX = 5000  # защита от неограниченного роста tracked-set
# при многодневной работе монитора


def _atomic_write_json(path: str, obj) -> bool:
    """Тот же паттерн, что и bsc_wallet_monitor._atomic_write_json/
    shadow_engine._atomic_write_json -- временный файл в той же директории +
    os.replace (атомарно на POSIX)."""
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
        log.error(f"onchain_watch: atomic write to {path} failed ({e})")
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


def decode_transfer_log(lg: dict) -> dict:
    topics = lg["topics"]
    frm = "0x" + topics[1][-40:]
    to = "0x" + topics[2][-40:]
    raw = int(lg["data"], 16)
    amount = raw / (10 ** BANK_DECIMALS)
    return {
        "block": int(lg["blockNumber"], 16),
        "tx": lg["transactionHash"],
        "from": frm,
        "to": to,
        "amount": amount,
    }


def get_bank_price_usd() -> float:
    """Живая цена BANK (Bybit linear BANKUSDT) -- тот же источник, что и
    bank_setup_monitor.py (BANK_SYMBOL). Честный 0.0 при сбое -- вызывающий
    код должен относиться к этому как к "цена н/д", НЕ считать несуществующий
    $0-перевод автоматически ниже порога."""
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": "linear", "symbol": "BANKUSDT"},
                          timeout=REQUEST_TIMEOUT_SEC)
        d = r.json()
        lst = d.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        log.info(f"onchain_watch: price fetch failed: {e}")
    return 0.0


def format_recipient_to_cex_alert_text(event: dict, cex_name: str) -> str:
    return (
        f"🚨 BANK РАЗЛОК: получатель -> биржа\n"
        f"{event['from']} -> {cex_name} ({event['to']}): "
        f"{event['amount']:,.0f} BANK"
        + (f" (~${event['usd']:,.0f})" if event['usd'] is not None else " (цена н/д)")
        + f"\nБлок {event['block']}, tx {event['tx']}\n"
          f"(адрес отправителя ранее получил крупный перевод BANK -- см. "
          f"onchain_watch_events.json)"
    )


def format_direct_to_cex_alert_text(event: dict, cex_name: str) -> str:
    return (
        f"⚠️ BANK: крупный перевод на биржу (не подтверждено, что это разлок)\n"
        f"{event['from']} -> {cex_name} ({event['to']}): "
        f"{event['amount']:,.0f} BANK"
        + (f" (~${event['usd']:,.0f})" if event['usd'] is not None else " (цена н/д)")
        + f"\nБлок {event['block']}, tx {event['tx']}"
    )


async def check_bank_unlock(bot, send_system_fn=None, run_in_executor_fn=None) -> int:
    """Джоб (scheduler.add_job, interval POLL_INTERVAL_SEC=5мин): сканирует
    новые блоки с последнего прогона (ограничено MAX_BLOCKS_PER_TICK за раз),
    ловит ВСЕ Transfer-события контракта BANK_CONTRACT (topics=[TRANSFER_TOPIC]
    без адресного фильтра -- см. докстринг модуля, нет заданного владельцем
    watch-листа адресов), обновляет tracked-set получателей и алертит на
    tracked->CEX / прямой->CEX переводы. `send_system_fn`/`run_in_executor_fn`
    внедряются для тестов, в проде берутся из bot.py/asyncio (локальный
    импорт -- без цикла на уровне модуля).

    Все блокирующие сетевые вызовы (get_latest_block/get_transfer_logs/
    get_bank_price_usd) идут через run_in_executor -- тот же паттерн, что и
    check_ake_wallets в bsc_wallet_monitor.py (критический регресс #240:
    синхронные вызовы внутри async-корутины блокируют весь event loop бота).
    """
    if send_system_fn is None:
        import bot as bot_module
        send_system_fn = bot_module.send_system
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)

    latest, prov = await run_in_executor_fn(_bwm.get_latest_block)
    if latest is None:
        log.error("onchain_watch: ни один RPC-провайдер не ответил на eth_blockNumber")
        return 0
    log.info(f"onchain_watch: тик через {prov['url']}, latest={latest}")

    state = _load_state()
    last_scanned = state.get("last_scanned_block")
    if last_scanned is None:
        state["last_scanned_block"] = latest
        state.setdefault("tracked_recipients", [])
        _save_state(state)
        log.info(f"onchain_watch: первый запуск, старт с блока {latest}")
        return 0

    from_block = last_scanned + 1
    if from_block > latest:
        return 0
    to_block = min(latest, from_block + MAX_BLOCKS_PER_TICK - 1)

    logs, incomplete, last_processed_block = await run_in_executor_fn(
        _bwm.get_transfer_logs, from_block, to_block, [TRANSFER_TOPIC], BANK_CONTRACT)

    price = await run_in_executor_fn(get_bank_price_usd)
    new_count = 0

    tracked = set(a.lower() for a in state.get("tracked_recipients", []))
    cex_addrs = CEX_DEPOSIT_ADDRESSES  # уже lowercase-ключи

    for lg in logs:
        ev = decode_transfer_log(lg)
        ev["usd"] = ev["amount"] * price if price > 0 else None
        ev["price_used"] = price
        ev["ts"] = time.time()

        frm, to = ev["from"], ev["to"]
        cex_name = cex_addrs.get(to)

        if cex_name is not None and frm in tracked:
            _append_event({**ev, "kind": "tracked_recipient_to_cex", "cex": cex_name})
            new_count += 1
            try:
                await send_system_fn(bot, format_recipient_to_cex_alert_text(ev, cex_name), critical=True)
            except Exception as e:
                log.error(f"onchain_watch: send_system (tracked->cex) failed: {e}")
        elif cex_name is not None and ev["usd"] is not None and ev["usd"] >= DIRECT_TO_CEX_ALERT_THRESHOLD_USD:
            _append_event({**ev, "kind": "direct_to_cex", "cex": cex_name})
            new_count += 1
            try:
                await send_system_fn(bot, format_direct_to_cex_alert_text(ev, cex_name), critical=True)
            except Exception as e:
                log.error(f"onchain_watch: send_system (direct->cex) failed: {e}")
        elif cex_name is None and ev["amount"] >= LARGE_TRANSFER_THRESHOLD_BANK:
            # Первый хоп -- тихий shadow-лог, получатель уходит в tracked-set
            _append_event({**ev, "kind": "large_transfer_tracked"})
            new_count += 1
            if to not in tracked:
                tracked.add(to)
                log.info(f"onchain_watch: новый tracked-получатель {to} "
                         f"({ev['amount']:,.0f} BANK)")

    tracked_list = list(tracked)
    if len(tracked_list) > TRACKED_RECIPIENTS_KEEP_MAX:
        tracked_list = tracked_list[-TRACKED_RECIPIENTS_KEEP_MAX:]
    state["tracked_recipients"] = tracked_list

    if last_processed_block >= from_block - 1:
        state["last_scanned_block"] = max(last_scanned, last_processed_block)

    if incomplete:
        log.error(f"onchain_watch: тик неполный (обработано до блока "
                  f"{last_processed_block} из {from_block}-{latest}), все RPC-провайдеры отказали")
        last_notify = state.get("last_source_down_notify_ts", 0)
        if time.time() - last_notify >= 15 * 60:
            try:
                await send_system_fn(bot, "⚠️ BANK-поллер (onchain_watch): все RPC-провайдеры "
                                           "отказали -- мониторинг разлока временно недоступен, "
                                           "повторные попытки продолжаются", critical=True)
            except Exception as e:
                log.error(f"onchain_watch: не удалось отправить honest down-notify: {e}")
            state["last_source_down_notify_ts"] = time.time()

    state["last_run_ts"] = time.time()
    _save_state(state)
    return new_count
