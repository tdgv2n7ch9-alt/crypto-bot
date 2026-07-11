"""
BEST TRADE — Level Watch: наблюдение за РУЧНОЙ дневной разметкой уровней интереса
(аналитик, например "Королев", размечает зоны на 4h — НЕ вычисляется ботом).

Полностью АДДИТИВНЫЙ, изолированный модуль. Не трогает боевую сигнальную логику:
- НЕ пишет в TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS/TOP_SPOT_SIGNALS;
- НЕ вызывает signal_journal.log_signal(), не создаёт "сигналов" в понятиях бота;
- НЕ гейтует и не участвует ни в каком скоринге (Rocket Score, pro_score и т.д.);
- Единственный эффект — текстовый алерт owner-чату, когда цена подходит к зоне или
  касается её. Чисто информационно, решение — за владельцем.

Конфиг — journal/watch_zones.json (НЕ в коде), чтобы владелец обновлял разметку
ежедневно с телефона через /zones_set, без деплоя. Формат:
  {"updated": "YYYY-MM-DD", "source": "Королев 4h",
   "ETHUSDT": [{"side": "LONG"|"SHORT", "lo": float, "hi": float, "prio": int,
                "note": str (опционально)}, ...]}

Правило РЕПЛЕЙСА: новая дневная разметка ПОЛНОСТЬЮ заменяет активный файл (не
дописывает поверх старого) — старая версия сначала копируется в
journal/watch_zones_history/<updated-дата-старой-версии>.json, история для
аналитики, актив всегда один документ на "сегодня".

Транспорт цены: Bybit public REST `/v5/market/tickers` (та же точка входа, что уже
использует whale_radar.py) — периодический опрос вместо WS: зон/символов мало,
частота обновления не критична, отдельное WS-соединение ради этого не оправдано.

Персистентность /zones_set через редеплой: Railway filesystem эфемерна (тот же
нюанс, что у journal/signals.json/shadow_signals.json) — правка владельца с
телефона, сохранённая ТОЛЬКО локально, пропала бы при следующем push в этот же
день. Поэтому replace_watch_zones() best-effort пушит новый конфиг в GitHub
Contents API (тот же паттерн, что signal_journal.py/shadow_engine.py, переиспользует
их _github_configured/_validate_github_token/_github_api_base/_github_headers), а
startup_sync() при старте процесса подтягивает GitHub-версию поверх локальной (той,
что была закоммичена последним деплоем кода) — GitHub, а не git-репозиторий, источник
истины для АКТУАЛЬНОЙ на сегодня разметки.
"""
import asyncio
import base64
import json
import os
import time

import requests

import signal_journal  # переиспользуем _github_configured/_validate_github_token/... (тот же паттерн, что shadow_engine.py)

BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
GITHUB_WATCH_ZONES_PATH = "journal/watch_zones.json"

APPROACH_PCT = 1.5          # алерт "подходит", если цена в пределах X% от границы зоны
COOLDOWN_SEC = 60 * 60      # кулдаун на (символ, зона, тип алерта) -- не спамить
POLL_INTERVAL_SEC = 30      # период опроса цены

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCH_ZONES_FILE = os.path.join(_BASE_DIR, "journal", "watch_zones.json")
WATCH_ZONES_HISTORY_DIR = os.path.join(_BASE_DIR, "journal", "watch_zones_history")

_META_KEYS = ("updated", "source")

# Лог касаний зон (Этап 4 АПГРЕЙД 11.07, "Метрики дня") -- тот же JSONL-per-день
# паттерн, что whale_radar.py (EVENTS_DIR/_events_path/append_event). ЧЕСТНО: это
# ЛОКАЛЬНЫЙ файл на диске Railway-контейнера, не синхронизируется в GitHub -- при
# редеплое/рестарте процесса теряется (тот же нюанс, что у data/whale/ в
# whale_radar.py). Для дневного дайджеста это значит: касания ДО последнего
# рестарта процесса в этот день не попадут в отчёт -- честно, не выдаётся за
# полную историю дня.
EVENTS_DIR = os.path.join(_BASE_DIR, "data", "level_watch")


def _events_path(dt=None) -> str:
    import datetime as _dt
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    return os.path.join(EVENTS_DIR, f"level_watch_events-{dt.strftime('%Y-%m-%d')}.jsonl")


def append_event(event: dict) -> None:
    """Дописывает одно событие касания зоны -- не бросает исключений наружу (тот же
    принцип, что whale_radar.append_event: ошибка лога не должна ронять цикл опроса)."""
    try:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        with open(_events_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"level_watch: append_event failed ({type(e).__name__}: {e})")


def fetch_price(symbol: str) -> float:
    """Текущая цена символа (Bybit linear). None при любой ошибке сети/парсинга --
    вызывающая сторона просто пропускает тик, не роняет цикл."""
    try:
        r = requests.get(BYBIT_TICKERS_URL, params={"category": "linear", "symbol": symbol.upper()},
                          timeout=10)
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        if not rows:
            return None
        return float(rows[0]["lastPrice"])
    except Exception as e:
        print(f"level_watch: fetch_price({symbol}) failed: {e}")
        return None


def _atomic_write_json(path: str, obj) -> bool:
    """Тот же паттерн, что signal_journal.py/shadow_engine.py -- временный файл в той
    же директории + os.replace (атомарно на POSIX), не полуписьмо при падении
    процесса на середине записи."""
    try:
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"level_watch: atomic write failed for {path}: {e}")
        return False


def load_watch_zones(path: str = WATCH_ZONES_FILE) -> dict:
    """Загружает активный конфиг зон. Файла нет/битый -- честный пустой конфиг
    (не выдуманные дефолты, не падение)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"updated": None, "source": None}
    except Exception as e:
        print(f"level_watch: load_watch_zones({path}) failed: {e}")
        return {"updated": None, "source": None}


def replace_watch_zones(new_config: dict, path: str = WATCH_ZONES_FILE,
                         history_dir: str = WATCH_ZONES_HISTORY_DIR) -> bool:
    """РЕПЛЕЙС (не дополнение): если активный файл уже существует, архивирует его
    ЦЕЛИКОМ в history_dir/<его собственный "updated">.json (или сегодняшней датой,
    если поле отсутствует/битое) ПЕРЕД перезаписью. Затем пишет new_config в path.
    Возвращает True/False (успех всей операции)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        if os.path.exists(path):
            old = load_watch_zones(path)
            old_date = old.get("updated") or time.strftime("%Y-%m-%d")
            history_path = os.path.join(history_dir, f"{old_date}.json")
            if not _atomic_write_json(history_path, old):
                return False
        return _atomic_write_json(path, new_config)
    except Exception as e:
        print(f"level_watch: replace_watch_zones failed: {e}")
        return False


def _github_get_watch_zones_sync():
    """GET journal/watch_zones.json из GitHub. (config, sha) либо (None, None), если
    файла нет/GitHub не настроен/ошибка — синхронно, вызывать только через
    run_in_executor (тот же паттерн, что shadow_engine._github_get_shadow_sync)."""
    if not signal_journal._github_configured():
        return None, None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"level_watch: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{GITHUB_WATCH_ZONES_PATH}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        return json.loads(content), data["sha"]
    except Exception as e:
        print(f"level_watch: GitHub GET failed ({e})")
        return None, None


def _github_put_watch_zones_sync(config: dict, sha):
    """PUT journal/watch_zones.json. Новый sha при успехе, "conflict" при 409
    (устаревший sha — вызывающий должен перечитать и повторить), None при ошибке."""
    if not signal_journal._github_configured():
        return None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"level_watch: {token_issue}")
        return None
    try:
        body = {
            "message": f"level_watch: разметка {config.get('updated', '?')} ({config.get('source', '?')})",
            "content": base64.b64encode(
                json.dumps(config, ensure_ascii=False, indent=2).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{signal_journal._github_api_base()}/contents/{GITHUB_WATCH_ZONES_PATH}",
                          headers=signal_journal._github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            return "conflict"
        r.raise_for_status()
        return r.json()["content"]["sha"]
    except Exception as e:
        print(f"level_watch: GitHub PUT failed ({e})")
        return None


async def sync_watch_zones_to_github(config: dict) -> bool:
    """Best-effort пуш конфига в GitHub ПОСЛЕ успешной локальной записи
    (replace_watch_zones уже отработал) — Railway ephemeral, без этого /zones_set
    пережил бы только до следующего редеплоя кода. Один повтор при 409-конфликте
    (перечитать sha, попробовать снова), не критично при неудаче — локальная копия
    уже сохранена."""
    try:
        loop = asyncio.get_event_loop()
        _, sha = await loop.run_in_executor(None, _github_get_watch_zones_sync)
        result = await loop.run_in_executor(None, _github_put_watch_zones_sync, config, sha)
        if result == "conflict":
            _, sha2 = await loop.run_in_executor(None, _github_get_watch_zones_sync)
            result = await loop.run_in_executor(None, _github_put_watch_zones_sync, config, sha2)
        return bool(result)
    except Exception as e:
        print(f"level_watch: sync_watch_zones_to_github failed: {e}")
        return False


async def startup_sync(path: str = WATCH_ZONES_FILE) -> bool:
    """При старте процесса подтягивает конфиг из GitHub поверх локального (если
    GitHub его знает) — после редеплоя кода локальный файл на Railway это то, что
    было в git на момент коммита, НЕ последняя правка владельца через /zones_set.
    GitHub, не git-репозиторий, источник истины для актуальной разметки. Не падает
    и не трогает локальный файл, если GitHub не настроен/пуст/недоступен (честно,
    остаётся то, что было закоммичено)."""
    try:
        loop = asyncio.get_event_loop()
        remote_config, _ = await loop.run_in_executor(None, _github_get_watch_zones_sync)
        if remote_config:
            return _atomic_write_json(path, remote_config)
        return False
    except Exception as e:
        print(f"level_watch: startup_sync failed: {e}")
        return False


def zone_state(price: float, zone: dict, approach_pct: float = APPROACH_PCT) -> str:
    """"in_zone" (цена внутри [lo,hi]), "approaching" (цена вне зоны, но в пределах
    approach_pct% от ближайшей границы — % считается ОТ ТЕКУЩЕЙ ЦЕНЫ, тот же принцип,
    что distance_pct везде в проекте, см. ta_extra.py/fa_engine.py), либо None (далеко).
    Чистая функция, без сети — тестируется напрямую."""
    lo, hi = zone["lo"], zone["hi"]
    if lo <= price <= hi:
        return "in_zone"
    if price < lo:
        dist_pct = (lo - price) / price * 100
    else:
        dist_pct = (price - hi) / price * 100
    if dist_pct <= approach_pct:
        return "approaching"
    return None


def distance_pct(price: float, zone: dict) -> float:
    """Расстояние до зоны в % от текущей цены — 0.0, если цена уже внутри."""
    lo, hi = zone["lo"], zone["hi"]
    if lo <= price <= hi:
        return 0.0
    if price < lo:
        return round((lo - price) / price * 100, 3)
    return round((price - hi) / price * 100, 3)


def format_level_alert(symbol: str, zone: dict, price: float, state: str,
                        source: str = None, updated: str = None) -> str:
    """Сторона, зона, цена, % до зоны, дата разметки — как в задаче."""
    side = zone["side"]
    lo, hi = zone["lo"], zone["hi"]
    dist = distance_pct(price, zone)
    header = "🎯 ЦЕНА В ЗОНЕ" if state == "in_zone" else "📍 Подходит к зоне"
    note = zone.get("note")
    note_line = f"\n{note}" if note else ""
    src_bits = [b for b in (source, updated) if b]
    src_line = " / ".join(src_bits) if src_bits else "?"
    return (
        f"{header} — {symbol} {side}\n"
        f"Зона: {lo}–{hi}{note_line}\n"
        f"Цена: {price:.2f}  ·  до зоны: {dist:.2f}%\n"
        f"Разметка: {src_line}"
    )


class LevelWatchState:
    """Кулдаун на (символ, зона, тип алерта). Идентификатор зоны — (lo, hi) как
    натуральный ключ (границы зоны сами по себе уникальны в пределах символа) —
    не полагается на индекс/порядок в конфиге, устойчиво к правкам через /zones_set."""

    def __init__(self):
        self._cooldowns = {}  # (symbol, lo, hi, alert_type) -> last_alert_ts

    def should_alert(self, symbol: str, zone: dict, alert_type: str, now: float = None) -> bool:
        now = now if now is not None else time.time()
        key = (symbol, zone["lo"], zone["hi"], alert_type)
        last = self._cooldowns.get(key, 0)
        if now - last < COOLDOWN_SEC:
            return False
        self._cooldowns[key] = now
        return True


def scan_zones(price: float, zones: list, approach_pct: float = APPROACH_PCT) -> list:
    """Возвращает [(zone, state), ...] для всех зон с ненулевым состоянием (in_zone/
    approaching) — без учёта кулдауна, чистая функция для тестов/отладки."""
    out = []
    for zone in zones:
        state = zone_state(price, zone, approach_pct)
        if state:
            out.append((zone, state))
    return out


async def check_and_alert(bot_send, owner_id, state: LevelWatchState, symbol: str,
                           price: float, zones: list, source: str = None,
                           updated: str = None, now: float = None) -> list:
    """Сканирует zones на текущей price, шлёт алерт (через bot_send, если задан) на
    каждую зону, прошедшую кулдаун. Возвращает список отправленных текстов (для
    тестов/логирования). bot_send: async def bot_send(owner_id, text) -- инъекция
    вместо прямого импорта bot.py (тот же принцип, что PumpContext/get_whale_zones)."""
    sent = []
    for zone, zstate in scan_zones(price, zones):
        if not state.should_alert(symbol, zone, zstate, now=now):
            continue
        text = format_level_alert(symbol, zone, price, zstate, source=source, updated=updated)
        sent.append(text)
        append_event({
            "ts": now if now is not None else time.time(), "symbol": symbol.upper(),
            "side": zone.get("side"), "zone_lo": zone.get("lo"), "zone_hi": zone.get("hi"),
            "state": zstate, "price": price,
        })
        if bot_send is not None:
            try:
                await bot_send(owner_id, text)
            except Exception as e:
                print(f"level_watch: alert send failed for {symbol}: {e}")
    return sent


async def run_level_watch(bot_send, owner_id, path: str = WATCH_ZONES_FILE,
                           state: LevelWatchState = None,
                           poll_interval_sec: float = POLL_INTERVAL_SEC,
                           iterations: int = None) -> LevelWatchState:
    """Бесконечный цикл опроса (iterations=None) — для боевого процесса; iterations=N
    для смоука/тестов (N тиков, затем возврат). `state` можно передать существующий,
    чтобы вызывающая сторона держала ссылку на кулдауны между вызовами (тот же
    принцип, что whale_radar.run_whale_radar(state=...)). Конфиг ПЕРЕЧИТЫВАЕТСЯ с
    диска на КАЖДОМ тике -- дёшево (маленький локальный JSON), зато /zones_set
    (владелец меняет разметку с телефона) подхватывается без рестарта процесса."""
    state = state if state is not None else LevelWatchState()
    i = 0
    while iterations is None or i < iterations:
        config = load_watch_zones(path)
        source = config.get("source")
        updated = config.get("updated")
        for symbol, zones in config.items():
            if symbol in _META_KEYS or not isinstance(zones, list):
                continue
            price = fetch_price(symbol)
            if price is not None:
                await check_and_alert(bot_send, owner_id, state, symbol, price, zones,
                                       source=source, updated=updated)
        i += 1
        if iterations is None or i < iterations:
            await asyncio.sleep(poll_interval_sec)
    return state


if __name__ == "__main__":
    async def _print_send(owner_id, text):
        print(f"--- ALERT (owner_id={owner_id}) ---\n{text}\n")

    cfg = load_watch_zones()
    print(f"Level Watch smoke: {cfg.get('source')} / {cfg.get('updated')}, "
          f"{POLL_INTERVAL_SEC}с интервал, 3 тика")
    asyncio.run(run_level_watch(_print_send, 0, iterations=3, poll_interval_sec=5))
