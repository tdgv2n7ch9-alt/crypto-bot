"""
miniapp_api.py -- П-MiniApp Этап 1 (владелец, ТЗ docs/TZ_P-MiniApp_v1.md): read-only
JSON API-слой для будущего Telegram Mini App поверх УЖЕ СУЩЕСТВУЮЩИХ функций бота.
Живёт ВНУТРИ worker-процесса (тот же event loop, что run_polling() -- см.
start_miniapp_api_server(), запускается из bot.py той же asyncio.create_task()-точкой,
что whale radar/pump detector).

Железные ограничения ТЗ (раздел 0), соблюдённые здесь:
  1. Один источник истины -- каждый route зовёт УЖЕ существующую функцию бота
     (closed_outcomes_report, _limitki_collect_zones/_limitki_zone_status,
     glossary.TERMS), ничего не пересчитывает заново.
  2. Read-only v1 -- ни одного write-эндпоинта.
  5. Бюджеты данных -- ни одного нового потребителя внешних API (zones использует
     УЖЕ существующий get_top500()-кэш бота, тот же путь, что текстовый экран
     ЛИМИТКИ).

Область этого инкремента (честно, не всё ТЗ): только /api/v1/track-record,
/api/v1/zones, /api/v1/glossary + /api/v1/health. dashboard/signals/portfolio
НЕ реализованы в этом инкременте -- см. PROGRESS.md "П-MiniApp Этап 1" за
причинами (недостающие агрегаторы для dashboard, зависимость signals от живых
глобалов TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS требует отдельного дизайн-решения,
portfolio требует xlsx-wiring).
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from aiohttp import web

log = logging.getLogger(__name__)

MINIAPP_API_PORT_ENV = "MINIAPP_API_PORT"
DEFAULT_PORT = 8080

# Раздел 6 ТЗ: whitelist chat_id (v1 -- только владелец). Поле оставлено как
# множество (не одиночная константа), чтобы будущий allowed_ids (клуб, ТЗ
# раздел 1) расширялся без смены формы кода, хотя сам список СЕЙЧАС -- один id.
ALLOWED_CHAT_IDS = {7009350191}

# Раздел 6 ТЗ: "просроченный auth_date (>1ч) -> 401".
INIT_DATA_MAX_AGE_SEC = 3600

# Раздел 1 ТЗ: "Кэш ответов 5-30 сек в памяти". Разные эндпоинты -- разная
# "цена" пересчёта, поэтому TTL задаётся per-route, не одной глобальной константой.
CACHE_TTL_TRACK_RECORD = 20
CACHE_TTL_ZONES = 10
CACHE_TTL_GLOSSARY = 300  # словарь меняется редко (правки в glossary.py, не рантайм)

# Раздел 5 ТЗ п.4: "Rate-limit на API (token bucket per chat_id)". СОЗНАТЕЛЬНО
# независимое состояние от access_control._command_history/_cooldown_until --
# те делят память с Telegram-командным flood-guard (владелец там ИСКЛЮЧЁН из
# лимита самим вызывающим кодом enforce(), но сама check_rate_limit() такого
# исключения не делает) -- смешивать бюджет API-поллинга Mini App с защитой от
# спама командами боту было бы двумя разными задачами в одном счётчике. Здесь
# переиспользуется только чистый примитив access_control._prune_window().
RATE_LIMIT_MAX_PER_MIN = 60
RATE_LIMIT_WINDOW_SEC = 60.0
_api_call_history: dict = {}  # chat_id -> [timestamps]


def verify_telegram_init_data(init_data: str, bot_token: str,
                               max_age_sec: int = INIT_DATA_MAX_AGE_SEC,
                               now: float = None) -> dict:
    """HMAC-SHA256 валидация Telegram WebApp initData (официальная схема:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Возвращает {"ok": True, "user_id": int, "auth_date": int} при успехе, иначе
    {"ok": False, "reason": str} -- честная причина отказа (для 401-ответа и
    логов), НИКОГДА не бросает исключение на невалидном/пустом входе."""
    if not init_data:
        return {"ok": False, "reason": "empty initData"}
    if not bot_token:
        return {"ok": False, "reason": "bot token not configured"}
    try:
        pairs = parse_qsl(init_data, strict_parsing=True)
    except ValueError:
        return {"ok": False, "reason": "malformed initData"}

    data = {}
    for k, v in pairs:
        data[k] = v  # повтор ключа -- берём последний, как urllib сам делает при dict-сборке

    received_hash = data.pop("hash", None)
    if not received_hash:
        return {"ok": False, "reason": "missing hash"}

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return {"ok": False, "reason": "signature mismatch"}

    auth_date_raw = data.get("auth_date")
    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "missing/invalid auth_date"}

    now = now if now is not None else time.time()
    if now - auth_date > max_age_sec:
        return {"ok": False, "reason": "auth_date expired"}

    user_raw = data.get("user")
    if not user_raw:
        return {"ok": False, "reason": "missing user field"}
    try:
        user = json.loads(user_raw)
        user_id = int(user["id"])
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "reason": "malformed user field"}

    return {"ok": True, "user_id": user_id, "auth_date": auth_date}


def check_api_rate_limit(chat_id: int, now: float = None,
                          max_per_min: int = RATE_LIMIT_MAX_PER_MIN,
                          window_sec: float = RATE_LIMIT_WINDOW_SEC,
                          history: dict = None) -> bool:
    """True -- запрос разрешён. Отдельное состояние от Telegram-командного
    flood-guard, см. докстринг модуля выше. `history` -- для тестов на чистом
    словаре (не трогает модульный _api_call_history)."""
    from access_control import _prune_window
    now = now if now is not None else time.time()
    history = _api_call_history if history is None else history
    hist = _prune_window(history.get(chat_id, []), now, window_sec)
    hist.append(now)
    history[chat_id] = hist
    return len(hist) <= max_per_min


class _TTLCache:
    """Простой in-memory TTL-кэш, тот же принцип, что _opts_cache/_opts_ts в
    bot.py (get_options_data()), но параметризован per-instance (не глобальные
    переменные), чтобы у каждого route был свой независимый TTL/слот."""

    def __init__(self, ttl_sec: float):
        self.ttl_sec = ttl_sec
        self._value = None
        self._ts = 0.0

    def get(self, now: float = None):
        now = now if now is not None else time.time()
        if self._value is not None and (now - self._ts) < self.ttl_sec:
            return self._value
        return None

    def set(self, value, now: float = None):
        self._value = value
        self._ts = now if now is not None else time.time()


def _json_response(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def _auth_middleware_factory(bot_module):
    @web.middleware
    async def _auth_middleware(request: web.Request, handler):
        if request.path == "/api/v1/health":
            return await handler(request)

        init_data = request.headers.get("X-Telegram-Init-Data", "")
        bot_token = getattr(bot_module, "BOT_TOKEN", None)
        verdict = verify_telegram_init_data(init_data, bot_token)
        if not verdict["ok"]:
            log.error(f"[MINIAPP-API] auth reject: {verdict['reason']}")
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)

        chat_id = verdict["user_id"]
        if chat_id not in ALLOWED_CHAT_IDS:
            log.error(f"[MINIAPP-API] auth reject: chat_id {chat_id} not whitelisted")
            return _json_response({"ok": False, "error": "forbidden"}, status=403)

        if not check_api_rate_limit(chat_id):
            return _json_response({"ok": False, "error": "rate limited"}, status=429)

        request["chat_id"] = chat_id
        return await handler(request)

    return _auth_middleware


async def _handle_health(request: web.Request) -> web.Response:
    return _json_response({"ok": True, "status": "live", "ts": time.time()})


def _make_track_record_handler(bot_module, cache: _TTLCache):
    async def _handle(request: web.Request) -> web.Response:
        cached = cache.get()
        if cached is not None:
            return _json_response(cached)
        import shadow_outcome_analysis
        try:
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(None, shadow_outcome_analysis.closed_outcomes_report)
        except Exception as e:
            log.error(f"[MINIAPP-API] track-record build failed: {e}")
            return _json_response({"ok": False, "error": "internal error"}, status=500)
        payload = {"ok": True, "data": report}
        cache.set(payload)
        return _json_response(payload)
    return _handle


def _make_zones_handler(bot_module, cache: _TTLCache):
    async def _handle(request: web.Request) -> web.Response:
        cached = cache.get()
        if cached is not None:
            return _json_response(cached)
        try:
            items = bot_module._limitki_collect_zones()
            zones = []
            for it in items:
                symbol = it["symbol"]
                zone = it["zone"]
                side = zone["side"]
                lo, hi = zone["lo"], zone["hi"]
                cancelled = bool(zone.get("cancelled"))
                price = 0.0
                try:
                    coins = bot_module.get_top500()
                    pair = f"{symbol}USDT"
                    coin = next((c for c in coins if c["symbol"] == pair), None)
                    price = (coin.get("quote", {}).get("USDT", {}).get("price", 0)
                              if coin else 0) or 0
                except Exception as e:
                    log.error(f"[MINIAPP-API] zones price lookup {symbol}: {e}")
                status, distance_pct = bot_module._limitki_zone_status(
                    side, lo, hi, price, cancelled=cancelled)
                zones.append({
                    "symbol": symbol, "side": side, "lo": lo, "hi": hi,
                    "prio": zone.get("prio"), "tier": zone.get("tier"),
                    "note": zone.get("note"), "cancelled": cancelled,
                    "price": price or None, "status": status,
                    "distance_pct": distance_pct,
                })
        except Exception as e:
            log.error(f"[MINIAPP-API] zones build failed: {e}")
            return _json_response({"ok": False, "error": "internal error"}, status=500)
        payload = {"ok": True, "data": zones}
        cache.set(payload)
        return _json_response(payload)
    return _handle


def _make_glossary_handler(cache: _TTLCache):
    async def _handle(request: web.Request) -> web.Response:
        cached = cache.get()
        if cached is not None:
            return _json_response(cached)
        import glossary
        payload = {"ok": True, "data": glossary.TERMS}
        cache.set(payload)
        return _json_response(payload)
    return _handle


def build_app(bot_module) -> web.Application:
    """Собирает aiohttp.Application с middleware+routes, БЕЗ запуска сервера --
    отдельная функция для тестируемости через aiohttp.test_utils без реального
    TCP-порта."""
    app = web.Application(middlewares=[_auth_middleware_factory(bot_module)])
    app["track_record_cache"] = _TTLCache(CACHE_TTL_TRACK_RECORD)
    app["zones_cache"] = _TTLCache(CACHE_TTL_ZONES)
    app["glossary_cache"] = _TTLCache(CACHE_TTL_GLOSSARY)
    app.router.add_get("/api/v1/health", _handle_health)
    app.router.add_get("/api/v1/track-record", _make_track_record_handler(bot_module, app["track_record_cache"]))
    app.router.add_get("/api/v1/zones", _make_zones_handler(bot_module, app["zones_cache"]))
    app.router.add_get("/api/v1/glossary", _make_glossary_handler(app["glossary_cache"]))
    return app


async def start_miniapp_api_server(bot_module, port: int = None) -> web.AppRunner:
    """Запускается ОДИН раз из bot.py post_init (тот же паттерн, что
    _start_pump_detector -- asyncio.create_task в уже работающем event loop
    run_polling()). Возвращает AppRunner -- вызывающая сторона может остановить
    (runner.cleanup()) при необходимости, хотя v1 не предполагает штатной
    остановки до конца процесса."""
    import os
    port = port if port is not None else int(os.getenv(MINIAPP_API_PORT_ENV, str(DEFAULT_PORT)))
    app = build_app(bot_module)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        log.info(f"[MINIAPP-API] started on 0.0.0.0:{port}")
    except Exception as e:
        log.error(f"[MINIAPP-API] failed to start on port {port}: {e}")
        raise
    return runner
