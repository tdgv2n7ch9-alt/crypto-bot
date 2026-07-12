"""
etherscan_whale.py -- отслеживание крупных переводов проектного токена на биржи
через Etherscan API V2 (Пакет 9 М4). Приоритет владельца: бэктест LAB показал,
что апрельский перевод 196M токенов (100M на Bitget deposit-адреса 8 апреля,
далее ~96-97M) -- сильнейший ранний сигнал в этом кейсе, и он БЕСПЛАТНЫЙ (не
Etherscan PRO, в отличие от tokenholderlist -- см. rug_radar.py докстринг).

Источник данных -- ТОЛЬКО бесплатный, проверено web-поиском 2026-07-12
(уточнено владельцем при добавлении ключа, тем же числом):
  - Etherscan API V2 (docs.etherscan.io/api-reference/endpoint/tokentx) --
    ERC20/BEP20-переводы по адресу контракта, свободный тир: 5 req/sec,
    100k req/day, МАКСИМУМ 1000 записей на запрос (понижено с 10000 с
    1.7.2026 -- не 10000, честно исправлено). Троттлинг -- глобальный лок +
    минимальный интервал между запросами (_ETHERSCAN_MIN_INTERVAL, 4/sec
    с запасом), тот же паттерн, что bot.py's _cg_get(). "Get Internal
    Transactions by Block Range" ушёл в Pro-тир -- этот модуль его НЕ
    использует (только tokentx), проверено -- ничего чинить не пришлось.
  - ОДИН API-ключ покрывает Ethereum И BSC через параметр chainid (V2
    multichain, docs.etherscan.io/etherscan-api-v2-multichain) -- LAB и
    похожие токены торгуются в основном на BSC (см. живые тикеры из
    rug_radar: "Uniswap V4 (BSC)", "Pancakeswap Infinity CLMM (BSC)").
  - Контракт-адрес токена по чейнам -- CoinGecko `/coins/{id}` поле
    `platforms` (уже фетчится параллельно для rug_radar.fetch_coingecko_detail,
    можно переиспользовать тот же ответ, доп. вызов не нужен).

Список адресов бирж -- ЧЕСТНОЕ ОГРАНИЧЕНИЕ: Etherscan не отдаёт публичные
label'ы (Binance/Bitget/...) через бесплатный API (сам labelcloud/directory --
только веб-страницы, не документированный API-эндпоинт, проверено
2026-07-12). Поэтому здесь -- курируемый статический список ИЗВЕСТНЫХ
адресов горячих кошельков крупных бирж, взятый с публичных страниц
etherscan.io/address/... (см. KNOWN_EXCHANGE_ADDRESSES, каждый адрес --
с URL-источником в комментарии). Это НЕ полный охват: перевод на биржевой
адрес, отсутствующий в списке (новый кошелёк, менее крупная биржа), не будет
пойман -- детектор находит СОВПАДЕНИЯ со списком, не гарантирует полноту.
Честно отражено в результате как `matched_against_known_list_only: True`.
"""
import os
import threading
import time

import requests

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
ETHERSCAN_V2_BASE = "https://api.etherscan.io/v2/api"

CHAIN_IDS = {
    "ethereum": 1,
    "binance-smart-chain": 56,
}

# Free tier лимиты (проверено владельцем живьём + docs.etherscan.io, 2026-07-12):
# 5 запросов/сек, 100k/день, максимум 1000 записей на запрос (понижено с 10000
# с 1.7.2026). Троттлинг -- тот же паттерн, что bot.py's _cg_get() (глобальный
# лок + минимальный интервал между запросами).
_ETHERSCAN_LOCK = threading.Lock()
_ETHERSCAN_LAST_CALL_TS = 0.0
_ETHERSCAN_MIN_INTERVAL = 0.25  # 4 req/sec -- с запасом от лимита 5/sec
MAX_RECORDS_PER_REQUEST = 1000

# Курируемый список -- ТОЛЬКО крупные, публично подтверждённые горячие
# кошельки (проверено живьём на etherscan.io/address/<addr> 2026-07-12).
# Расширяется по мере необходимости, НЕ претендует на полноту.
KNOWN_EXCHANGE_ADDRESSES = {
    # Binance (Ethereum) -- etherscan.io/address/0xf977814e90da44bfa03b6295a0616a897441acec
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance: Hot Wallet 20",
    # Binance (Ethereum) -- etherscan.io/address/0x28c6c06298d514db089934071355e5743bf21d60
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance 14",
    # Binance (Ethereum) -- etherscan.io/address/0x21a31ee1afc51d94c2efccaa2092ad1028285549
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance 15",
}

LARGE_TRANSFER_USD_MIN = 100_000  # порог "крупного" перевода для флага


def get_token_contracts(cg_detail: dict) -> dict:
    """Извлекает адреса контракта токена по чейнам из уже полученного
    CoinGecko cg_detail (см. rug_radar.fetch_coingecko_detail) -- поле
    `platforms`. Возвращает {"ethereum": "0x...", "binance-smart-chain": "0x..."},
    пустой dict если platforms отсутствует/пуст."""
    platforms = (cg_detail or {}).get("platforms") or {}
    return {chain: addr for chain, addr in platforms.items()
            if chain in CHAIN_IDS and addr}


def fetch_token_transfers(contract_address: str, chain: str, api_key: str = None,
                           limit: int = 200, timeout: int = 10) -> list:
    """tokentx через Etherscan API V2 -- последние `limit` переводов данного
    контракта (не по конкретному отправителю, а ВСЕ переводы токена -- чтобы
    поймать переводы С ЛЮБОГО кошелька команды/инсайдеров НА биржу, не только
    с одного известного адреса). Пустой список при отсутствии ключа/ошибке --
    вызывающая сторона (detect_large_exchange_transfers) это ожидает."""
    key = (api_key or ETHERSCAN_API_KEY).strip()
    if not key or chain not in CHAIN_IDS:
        return []
    limit = min(limit, MAX_RECORDS_PER_REQUEST)
    try:
        global _ETHERSCAN_LAST_CALL_TS
        with _ETHERSCAN_LOCK:
            wait = _ETHERSCAN_MIN_INTERVAL - (time.time() - _ETHERSCAN_LAST_CALL_TS)
            if wait > 0:
                time.sleep(wait)
            r = requests.get(ETHERSCAN_V2_BASE, params={
                "chainid": CHAIN_IDS[chain],
                "module": "account",
                "action": "tokentx",
                "contractaddress": contract_address,
                "page": 1,
                "offset": limit,
                "sort": "desc",
                "apikey": key,
            }, timeout=timeout)
            _ETHERSCAN_LAST_CALL_TS = time.time()
        data = r.json()
        if data.get("status") != "1":
            return []
        return data.get("result", []) or []
    except Exception:
        return []


def detect_large_exchange_transfers(transfers: list, token_price_usd: float,
                                     min_usd: float = LARGE_TRANSFER_USD_MIN,
                                     known_addresses: dict = None) -> dict:
    """Чистая функция -- фильтрует уже полученные transfers (см.
    fetch_token_transfers) по совпадению `to` с известным биржевым адресом И
    сумме >= min_usd. Знаки после запятой -- из самой записи (`tokenDecimal`,
    стандартное поле tokentx-ответа), не предполагается заранее. `matched_
    against_known_list_only=True` всегда -- честное напоминание, что список
    адресов не полон (см. докстринг модуля)."""
    known = known_addresses if known_addresses is not None else KNOWN_EXCHANGE_ADDRESSES
    if not transfers:
        return {"available": False, "large_transfer_usd_recent": None,
                "transfers": [], "matched_against_known_list_only": True,
                "reason": "н/д -- нет данных о переводах (нет ключа/ошибка API/токен не найден)"}
    matches = []
    for t in transfers:
        to_addr = (t.get("to") or "").lower()
        label = known.get(to_addr)
        if not label:
            continue
        try:
            raw = float(t.get("value", 0))
            decimals = int(t.get("tokenDecimal", 18))
            amount = raw / (10 ** decimals)
            usd = amount * token_price_usd
        except (ValueError, TypeError):
            continue
        if usd >= min_usd:
            matches.append({
                "hash": t.get("hash"), "to": to_addr, "exchange": label,
                "amount": amount, "usd": round(usd, 2),
                "timestamp": t.get("timeStamp"),
            })
    total_usd = sum(m["usd"] for m in matches)
    return {
        "available": True,
        "large_transfer_usd_recent": round(total_usd, 2) if matches else 0,
        "transfers": matches,
        "matched_against_known_list_only": True,
        "reason": (f"{len(matches)} перевод(а/ов) на известные биржевые адреса, "
                   f"${total_usd:,.0f}") if matches else "переводов на известные биржевые адреса не найдено",
    }


def fetch_transfer_data(cg_detail: dict, token_price_usd: float,
                         api_key: str = None) -> dict:
    """Точка входа для rug_radar.compute_rug_risk(transfer_data=...). Пытается
    ETH, затем BSC (если есть контракт на обоих -- берёт оба, суммирует).
    Возвращает {} (не dict с available=False!) при полном отсутствии ключа --
    rug_radar.detect_exchange_transfers(None) уже трактует None как "н/д",
    сохраняя единообразие с остальными detect_* при отсутствии провайдера."""
    key = (api_key or ETHERSCAN_API_KEY).strip()
    if not key:
        return {}
    contracts = get_token_contracts(cg_detail)
    if not contracts:
        return {}
    all_matches = []
    total_usd = 0.0
    for chain, addr in contracts.items():
        transfers = fetch_token_transfers(addr, chain, api_key=key)
        result = detect_large_exchange_transfers(transfers, token_price_usd)
        if result["available"]:
            all_matches.extend(result["transfers"])
            total_usd += result["large_transfer_usd_recent"] or 0
    return {"large_transfer_usd_recent": round(total_usd, 2), "transfers": all_matches}
