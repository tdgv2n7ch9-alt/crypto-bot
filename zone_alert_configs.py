"""
zone_alert_configs.py -- декларативные конфиги символов для zone_alert_monitor.py.
Каждая функция build_<symbol>_triggers() возвращает список триггеров (see
zone_alert_monitor.check_zone() докстринг за формат) -- держит движок общим,
а данные конкретного наряда владельца -- отдельно, легко добавлять новые.
"""

# === KAITOUSDT SHORT -- внешний источник (external, НЕ author), 2026-07-15 ===

KAITO_SYMBOL = "KAITOUSDT"

KAITO_PROFILE_LINE = (
    "📊 KAITO профиль (2026-07-15, живая проверка): капа $179.7M (CoinGecko), "
    "OI(Bybit-only) $5.2M, в обращении 24.1% от max supply 1B (vesting-навес). "
    "🩸 orderbook глубина ~$65-66K/сторону на Bybit (топ-50) -- тонко относительно "
    "капы и объёма $43M/24ч (крупная метрика, наша методология отличается от "
    "источника владельца, не претендуем на точное совпадение). "
    "🔓 Разлок: НЕ удалось проверить (страница CMC unlock -- JS-рендер, недоступна "
    "нашими текущими средствами) -- честно н/д, не показываем маркер без подтверждения."
)


def build_kaito_triggers() -> list:
    return [
        {"name": "limit1", "type": "touch", "level": 0.790, "timeframe": "15",
         "text": "🎯 KAITO: касание первой лимитки 0.790 (зона SHORT, tier external)",
         "scalp_direction": "short"},
        {"name": "limit_full", "type": "touch", "level": 0.830, "timeframe": "15",
         "text": "🎯 KAITO: полный набор лимиток DCA (0.790 / 0.815 / 0.830, средняя ~0.804)",
         "scalp_direction": "short"},
        {"name": "invalidation", "type": "close_above", "level": 0.856, "timeframe": "60",
         "text": "🚫 KAITO: сетап отменён -- закреп 1H выше 0.856"},
        {"name": "target1", "type": "touch", "level": 0.737, "timeframe": "15",
         "text": "🎯 KAITO: цель 1 достигнута (0.737) -- частичная фиксация"},
        {"name": "target2", "type": "touch", "level": 0.721, "timeframe": "15",
         "text": "🎯 KAITO: цель 2 достигнута (0.721) -- частичная фиксация"},
    ]


# === AVAXUSDT LONG -- две зоны от автора (tier author, 1в1), 2026-07-15 ===

AVAX_SYMBOL = "AVAXUSDT"
AVAX_SPOT_NOTE = "спот: см. портфель"


def build_avax_triggers() -> list:
    return [
        {"name": "zoneA_entry", "type": "touch", "level": 6.66, "timeframe": "15",
         "text": "🎯 AVAX: вход в зону A (6.66) -- консервативная лимитка, tier author",
         "scalp_direction": "long"},
        {"name": "zoneB_transition", "type": "touch", "level": 6.55, "timeframe": "15",
         "text": "⚠️ AVAX: переход к сценарию B (6.55) -- зона A под угрозой, свип-сценарий активируется",
         "scalp_direction": "long"},
        {"name": "zoneB_full", "type": "touch", "level": 6.46, "timeframe": "15",
         "text": "🎯 AVAX: полный набор зоны B (6.46) -- агрессивный свип-сценарий",
         "scalp_direction": "long"},
        {"name": "invalidation", "type": "close_below", "level": 6.30, "timeframe": "60",
         "text": "🚫 AVAX: идея отменена -- закреп 1H ниже 6.30"},
        {"name": "target1", "type": "touch", "level": 6.834, "timeframe": "15",
         "text": f"🎯 AVAX: цель 1 достигнута (6.834) -- частичная фиксация\n{AVAX_SPOT_NOTE}"},
        {"name": "target2", "type": "touch", "level": 6.947, "timeframe": "15",
         "text": f"🎯 AVAX: финальная цель достигнута (6.947)\n{AVAX_SPOT_NOTE}"},
    ]
