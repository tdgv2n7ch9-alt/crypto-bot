"""
new_coin_scan.py -- EVENT-RADAR М4 (Пакет 13, 2026-07-13), узкий вариант по решению
владельца. См. KNOWLEDGE_GAPS.md "Новые листинги CoinGecko" -- CoinGecko
`/coins/list/new` живьём подтверждён PRO-only (`error_code: 10005`), бесплатной
альтернативы (DexScreener token-profiles/latest) владелец не одобрил (скам/памп-шум
без биржевого отбора, не тот же смысл, что "новый листинг").

Решение владельца: без новых источников -- возраст через уже отслеживаемую ботом
вселенную (`get_all_coins()`, ~729 монет по обороту CoinGecko), метод
genesis_date/atl_date (`rug_radar.compute_age_days()`, тот же метод, что уже
использует RUG-RADAR, вынесен в переиспользуемую функцию именно для этого).
Честное ограничение: покрывает только то, что уже попало в топ-729 по обороту --
НЕ самые свежие/мелкие листинги, которые ещё не набрали объём.

Мемкоин-флаг: CoinGecko `categories` содержит явную метку "Meme" для мемкоинов
(проверено живьём на dogecoin -- categories включает "Meme", "Dog-Themed" и т.д.)
-- используется напрямую, не keyword-эвристика по названию/тикеру.
"""

YOUNG_COIN_MAX_DAYS = 30


def is_young_coin(age_days) -> bool:
    return age_days is not None and age_days < YOUNG_COIN_MAX_DAYS


def is_memecoin(cg_detail: dict) -> bool:
    """CoinGecko `categories` -- честная метка от самого CoinGecko, не keyword-
    эвристика по названию (см. докстринг модуля, проверено живьём на dogecoin)."""
    if not cg_detail:
        return False
    categories = cg_detail.get("categories") or []
    return any("meme" in (c or "").lower() for c in categories)


def format_young_coin_flag(age_days, age_is_approx: bool = False, cg_detail: dict = None) -> str:
    """Строка для карточки x100/Памп-радара/ТОЧЕК (см. PROGRESS.md -- ТОЧКИ ещё не
    существует как отдельная карточка, будет добавлена вместе с Меню v2).
    Пустая строка, если возраст неизвестен или >= 30 дней -- не засоряем карточку."""
    if not is_young_coin(age_days):
        return ""
    approx_note = " (approx по ATL)" if age_is_approx else ""
    meme_note = " · 🃏 мемкоин" if is_memecoin(cg_detail) else ""
    return f"🌱 МОЛОДАЯ (<30 дней, ~{age_days}д{approx_note}){meme_note}"
