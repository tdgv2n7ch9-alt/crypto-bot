"""
BEST TRADE — Фаза C, каркас on-chain метрик («Пакетный ритм» пакет 2, М5).

ЧЕСТНАЯ НАХОДКА (Truth Protocol -- проверено WebFetch на docs.glassnode.com/
basic-api/api и studio.glassnode.com/pricing, 2026-07-11): **у Glassnode НЕТ
по-настоящему бесплатного API-тира.**
- docs.glassnode.com/basic-api/api: программный доступ начинается с Advanced
  ($49/мес) -- даёт только ограниченный "Light API" (14 дней истории, только
  дневное разрешение (1d), 50 запросов/день, без bulk-эндпоинтов).
- studio.glassnode.com/pricing: та же страница утверждает, что Advanced НЕ
  включает API вовсе -- доступ только на Professional (custom pricing,
  "опциональный add-on").
Источники расходятся в деталях (какой именно платный тир открывает API), но
СОГЛАСНЫ в главном: тира с $0 и программным доступом нет ни на одной странице.
Это расходится с посылкой задачи "Glassnode free tier" -- честно зафиксировано,
не подгоняю под ожидание.

Возможная бесплатная альтернатива (проверена частично, НЕ до конца, НЕ
интегрирована): BGeometrics (bitcoin-data.com / portal.bitcoin-data.com) --
подтверждённый $0/мес тир (10 запросов/час, 15/день, история 4 года), сайт
заявляет SOPR/MVRV/NVT в своём API, но НЕ подтверждено, какие метрики именно
входят в БЕСПЛАТНЫЙ тир против платных (Advanced+) -- страница пейволла явно
показывает "Data M2" только с Advanced, про SOPR/MVRV/NVT/Puell/LTH-STH по
тирам умалчивает. Puell Multiple и LTH/STH supply вообще не упомянуты на
главной странице API. Решение по источнику -- за владельцем (Уровень 3).

Этот модуль -- ТОЛЬКО каркас: конфигурация источника через переменные
окружения (не хардкод), честное "источник не настроен" состояние для
карточки On-Chain (тот же принцип честности, что уже был у прежней заглушки
"Раздел в разработке — реального источника данных... пока нет"), хук для
shadow-скоринга (аддитивно, боевой скоринг НИГДЕ не трогает). Реальный фетч
данных НЕ подключён -- ждёт решения владельца по источнику.
"""
import os

SUPPORTED_METRICS = ("sopr", "mvrv", "nvt", "puell", "lth_sth_supply")

ONCHAIN_DATA_SOURCE = os.getenv("ONCHAIN_DATA_SOURCE", "").strip()  # "" -- не настроен
ONCHAIN_API_KEY = os.getenv("ONCHAIN_API_KEY", "").strip()

_KNOWN_SOURCES = {
    "glassnode": "требует платной подписки (Advanced $49/мес или Professional) -- "
                 "не подключён без явного решения владельца о бюджете",
    "bgeometrics": "вероятно бесплатен (подтверждён $0/мес тир), но покрытие "
                   "SOPR/MVRV/NVT/Puell/LTH-STH по тирам не до конца проверено -- "
                   "фетчер не реализован в этом пакете",
}


def is_configured() -> bool:
    """Честно: настроен -- значит указан И известный источник, И ключ. Пустая
    строка (дефолт) -- НЕ настроен, не притворяемся, что что-то есть."""
    return bool(ONCHAIN_DATA_SOURCE and ONCHAIN_API_KEY)


def get_onchain_metrics(symbol: str = "BTC") -> dict:
    """Возвращает {"ok": False, "reason": ...} честно, пока источник не
    настроен и фетчер не реализован -- НЕ выдумывает нули/цифры вместо данных
    (тот же принцип честности, что вся остальная работа этого пакета).
    Каркас готов принять реальный фетчер после решения владельца по источнику
    -- эта функция единственная точка входа, которую нужно будет заменить."""
    if not ONCHAIN_DATA_SOURCE:
        return {"ok": False, "reason": "источник on-chain данных не настроен -- "
                                        "решение по Glassnode (платный) / BGeometrics "
                                        "(вероятно бесплатный, не до конца проверен) / "
                                        "другому источнику ждёт владельца"}
    if ONCHAIN_DATA_SOURCE not in _KNOWN_SOURCES:
        return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' не распознан "
                                        f"(известны: {', '.join(_KNOWN_SOURCES)})"}
    if not ONCHAIN_API_KEY:
        return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' задан, но "
                                        f"ONCHAIN_API_KEY не установлен"}
    # TODO (следующий пакет, после решения владельца по источнику): реальный
    # фетчер для ONCHAIN_DATA_SOURCE. Каркас, не реализация -- см. докстринг модуля.
    return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' настроен, но "
                                    f"фетчер для него ещё не реализован (каркас Этапа М5)"}


def shadow_score_adjustment(metrics: dict) -> dict:
    """Хук для shadow-скоринга (Фаза C, аддитивно) -- боевой скоринг НИГДЕ не
    читает этот модуль. Пока честно возвращает "нет данных" -- формула
    поправки скоринга по SOPR/MVRV/NVT/Puell/LTH-STH ещё не спроектирована,
    ждёт и решения по источнику, и отдельного шага дизайна формулы."""
    if not metrics.get("ok"):
        return {"available": False, "adjustment": 0, "reason": metrics.get("reason")}
    return {"available": False, "adjustment": 0,
            "reason": "метрики получены, но формула shadow-скоринга ещё не спроектирована"}


def format_onchain_card_text(symbol: str = "BTC") -> str:
    """Текст для карточки "🔗 On-Chain" (bot.py, callback_data="onchain_info") --
    честное состояние вместо заглушки-строки, но пока говорит то же самое по
    смыслу (источник не настроен), просто через реальный код, готовый принять
    источник, а не хардкод-текст."""
    metrics = get_onchain_metrics(symbol)
    if metrics["ok"]:
        return "\n".join(f"{k}: {v}" for k, v in metrics.items() if k != "ok")
    return f"🚧 Раздел в разработке — {metrics['reason']}."
