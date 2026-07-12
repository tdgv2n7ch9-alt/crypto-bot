"""
derivatives_extra.py -- чистые функции для Options Skew и Liquidation Heatmap
(Пакет 9 М3). Работают над уже полученными сырыми ответами Deribit/OKX (те же
данные, что bot.get_options_data()/get_liq_data() уже фетчат) -- без
дополнительных сетевых вызовов. ИНФОРМАЦИОННЫЙ слой: постоянная граница
CLAUDE.md -- торговую/сигнальную логику не менять без "да" владельца, эти
метрики нигде не подаются в pro_score/rocket/скоринг сигналов.

**Options Skew** -- ЧЕСТНОЕ УПРОЩЕНИЕ (по аналогии с compute_max_pain() в
bot.py, тот же принцип: упрощение задокументировано, не скрыто): классический
25-delta risk reversal требует греков (delta) по каждому инструменту, которых
Deribit `get_book_summary_by_currency` НЕ отдаёт (проверено живьём 2026-07-12 --
поля: high/low/last/mark_iv/instrument_name/underlying_price/open_interest и
т.д., delta отсутствует). Вместо истинной 25-дельты используется MONEYNESS-
BAND proxy: средняя mark_iv путов со страйком в [0.85, 0.95]xSpot минус
средняя mark_iv коллов со страйком в [1.05, 1.15]xSpot, на БЛИЖАЙШЕЙ
экспирации (по объёму открытых контрактов, не по дате -- чтобы не ловить
мёртвые низколиквидные серии). Положительный skew = путы дороже = страх
даунсайда; отрицательный = коллы дороже = FOMO апсайда.

**Liquidation Heatmap** -- РЕТРОСПЕКТИВНЫЙ (не forward-looking): кластеризует
уже СЛУЧИВШИЕСЯ ликвидации (OKX `liquidation-orders`, `state=filled`) по
ценовым бакетам вокруг текущей цены. Это НЕ прогноз, где будущие стопы
сработают (та функциональность потребовала бы знания реальных позиций/плеч
живых трейдеров -- недоступно бесплатно нигде) -- это карта, ГДЕ ликвидности
уже было больше всего за последнее время, честно помечено `retrospective:
True` в результате.
"""

SKEW_PUT_MONEYNESS = (0.85, 0.95)   # OTM put strike band relative to spot
SKEW_CALL_MONEYNESS = (1.05, 1.15)  # OTM call strike band relative to spot

LIQ_HEATMAP_BUCKET_PCT = 1.0   # ширина ценового бакета, % от текущей цены
LIQ_HEATMAP_TOP_N = 5          # сколько топ-бакетов возвращать


def _parse_expiry(instrument_name: str):
    """'BTC-28AUG26-46000-P' -> '28AUG26'. None при неожиданном формате."""
    parts = instrument_name.split("-")
    return parts[1] if len(parts) == 4 else None


def compute_options_skew(items: list) -> dict:
    """items -- сырой список из Deribit get_book_summary_by_currency (kind=option).
    Возвращает {"ok": bool, "skew": float|None, "expiry": str|None,
    "put_iv_avg": float|None, "call_iv_avg": float|None, "put_count": int,
    "call_count": int, "note": str}. skew=None при недостатке данных (не 0 --
    0 было бы ложным "нейтрально")."""
    if not items:
        return {"ok": False, "skew": None, "expiry": None, "put_iv_avg": None,
                "call_iv_avg": None, "put_count": 0, "call_count": 0,
                "note": "н/д -- нет данных опционов"}

    spot = None
    for it in items:
        if it.get("underlying_price"):
            spot = it["underlying_price"]
            break
    if not spot:
        return {"ok": False, "skew": None, "expiry": None, "put_iv_avg": None,
                "call_iv_avg": None, "put_count": 0, "call_count": 0,
                "note": "н/д -- нет underlying_price"}

    # ближайшая экспирация по суммарному OI (самая ликвидная серия)
    expiry_oi = {}
    for it in items:
        expiry = _parse_expiry(it.get("instrument_name", ""))
        if not expiry:
            continue
        expiry_oi[expiry] = expiry_oi.get(expiry, 0) + (it.get("open_interest", 0) or 0)
    if not expiry_oi:
        return {"ok": False, "skew": None, "expiry": None, "put_iv_avg": None,
                "call_iv_avg": None, "put_count": 0, "call_count": 0,
                "note": "н/д -- не удалось распарсить экспирации"}
    top_expiry = max(expiry_oi, key=expiry_oi.get)

    put_ivs, call_ivs = [], []
    for it in items:
        name = it.get("instrument_name", "")
        if _parse_expiry(name) != top_expiry:
            continue
        parts = name.split("-")
        if len(parts) != 4:
            continue
        try:
            strike = float(parts[2])
        except ValueError:
            continue
        opt_type = parts[3]
        iv = it.get("mark_iv")
        if iv is None:
            continue
        moneyness = strike / spot
        if opt_type == "P" and SKEW_PUT_MONEYNESS[0] <= moneyness <= SKEW_PUT_MONEYNESS[1]:
            put_ivs.append(iv)
        elif opt_type == "C" and SKEW_CALL_MONEYNESS[0] <= moneyness <= SKEW_CALL_MONEYNESS[1]:
            call_ivs.append(iv)

    if not put_ivs or not call_ivs:
        return {"ok": False, "skew": None, "expiry": top_expiry, "put_iv_avg": None,
                "call_iv_avg": None, "put_count": len(put_ivs), "call_count": len(call_ivs),
                "note": "н/д -- недостаточно опционов в moneyness-диапазоне на ближайшей экспирации"}

    put_avg = sum(put_ivs) / len(put_ivs)
    call_avg = sum(call_ivs) / len(call_ivs)
    skew = round(put_avg - call_avg, 2)
    return {"ok": True, "skew": skew, "expiry": top_expiry,
            "put_iv_avg": round(put_avg, 2), "call_iv_avg": round(call_avg, 2),
            "put_count": len(put_ivs), "call_count": len(call_ivs),
            "note": ("страх даунсайда (путы дороже)" if skew > 2 else
                     "FOMO апсайда (коллы дороже)" if skew < -2 else "нейтрально")}


def compute_liquidation_heatmap(rows: list, price_now: float,
                                 bucket_pct: float = LIQ_HEATMAP_BUCKET_PCT,
                                 top_n: int = LIQ_HEATMAP_TOP_N,
                                 contract_size: float = 0.01) -> dict:
    """rows -- сырой `data` список из OKX liquidation-orders (вложенная
    структура: rows -> details -> отдельные события с bkPx/sz/side). Кластеризует
    СУММАРНЫЙ notional ликвидаций по %-бакетам от price_now. РЕТРОСПЕКТИВНО --
    честно помечено `retrospective: True`, это не прогноз будущих уровней.

    Честно: НЕ разбивает по long/short -- в существующем `bot.get_liq_data()`
    интерпретация поля `side` (`buy`/`sell` -> какая сторона ликвидирована)
    расходится со стандартной биржевой семантикой (liquidated long -> forced
    SELL, liquidated short -> forced BUY), и это не тот участок кода, который
    эта задача трогает/чинит. Чтобы не плодить противоречивую маркировку в
    одном проекте -- здесь только агрегированный notional на бакет, без
    long/short атрибуции."""
    if not rows or not price_now or price_now <= 0:
        return {"ok": False, "retrospective": True, "buckets": [],
                "note": "н/д -- нет данных ликвидаций или текущей цены"}

    buckets = {}  # bucket_index -> notional float
    for row in rows:
        for d in row.get("details", []):
            try:
                bk_px = float(d.get("bkPx", 0))
                sz = float(d.get("sz", 0))
            except (ValueError, TypeError):
                continue
            if bk_px <= 0 or sz <= 0:
                continue
            notional = sz * contract_size * bk_px
            pct_from_now = (bk_px - price_now) / price_now * 100
            bucket_idx = round(pct_from_now / bucket_pct)
            buckets[bucket_idx] = buckets.get(bucket_idx, 0.0) + notional

    if not buckets:
        return {"ok": False, "retrospective": True, "buckets": [],
                "note": "н/д -- не удалось распарсить ни одной записи"}

    ranked = sorted(buckets.items(), key=lambda kv: -kv[1])[:top_n]
    out_buckets = []
    for idx, notional in ranked:
        price_lo = price_now * (1 + (idx - 0.5) * bucket_pct / 100)
        price_hi = price_now * (1 + (idx + 0.5) * bucket_pct / 100)
        out_buckets.append({
            "price_lo": round(price_lo, 2), "price_hi": round(price_hi, 2),
            "pct_from_now": round(idx * bucket_pct, 2),
            "notional_usd": round(notional, 2),
        })
    return {"ok": True, "retrospective": True, "buckets": out_buckets,
            "note": f"топ-{len(out_buckets)} зон недавних ликвидаций (ретроспектива, не прогноз)"}
