"""
pytest для Пакет 18, п.4 (владелец, кейс KITE 20:59: OI-матрица показывала
"Цена↓ OI↑" -- красный квадрант, "новые шорты, реальное давление" -- а
сценарий Памп-радара всё равно писал "возможен лонг после отскока от дна",
без единого упоминания OI). pump_detector._scenario_lines()/_start_watch()
теперь согласуют текст сценария с ta_extra.classify_oi_matrix() по таблице
владельца. Тест на каждый квадрант матрицы (up_up/up_down/down_up/down_down)
+ near_zero, отдельно для DUMP и PUMP направлений.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pump_detector as pd


class _FakeBot:
    def __init__(self):
        self.sent_texts = []

    async def send_message(self, chat_id, text, **kw):
        self.sent_texts.append(text)

    async def send_photo(self, chat_id, photo, caption, **kw):
        self.sent_texts.append(caption)


class _FakeCtx:
    def __init__(self, oi_change=0.0):
        self.bot = _FakeBot()
        self.owner_chat_id = 999
        self._oi_change = oi_change
        self._coin = {"quote": {"USDT": {
            "market_cap": 5_000_000_000, "volume_24h": 200_000_000,
            "percent_change_30d": 10.0, "price": 100.0}}}
        self._cg_detail = {}

    def get_coin_by_symbol(self, sym):
        return self._coin

    def get_cg_detail(self, sym):
        return self._cg_detail

    def get_funding_pct(self, sym):
        return 0.0

    def get_oi_usd(self, sym):
        return 1e7

    def get_oi_change(self, sym):
        return self._oi_change

    def get_killzone_status(self):
        return {"active": {"name": "NY Open", "quality": "A"}, "is_good": True, "next": None}


def _patch_common(monkeypatch):
    monkeypatch.setattr(pd, "_build_chart", lambda *a, **kw: None)
    monkeypatch.setattr(pd, "_ensure_history", lambda sym: None)
    monkeypatch.setattr(pd.etherscan_whale, "fetch_transfer_data", lambda *a, **kw: None)
    monkeypatch.setattr(pd.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 5, "warn": False, "alert": False, "reasons": []})


# ── _scenario_lines() unit-уровень: прямая проверка каждого квадранта ──────

def test_dump_down_up_quadrant_kite_case():
    """Кейс KITE 20:59 воспроизведён напрямую: DUMP + OI растёт (down_up) --
    "давление шортов", НЕ "возможен лонг"."""
    lines = pd._scenario_lines("dump", oi_change_pct=0.5, rug_warn=False)
    text = "\n".join(lines)
    assert "давление шортов, разворот не подтверждён" in text
    assert "возможен лонг" not in text


def test_dump_down_down_quadrant():
    lines = pd._scenario_lines("dump", oi_change_pct=-0.5, rug_warn=False)
    text = "\n".join(lines)
    assert "выход из позиций, ждать признаков разворота" in text
    assert "возможен лонг" not in text


def test_dump_near_zero_quadrant_keeps_original_text():
    lines = pd._scenario_lines("dump", oi_change_pct=0.02, rug_warn=False)
    text = "\n".join(lines)
    assert "возможен лонг после отскока от дна" in text


def test_dump_no_data_quadrant_keeps_original_text():
    lines = pd._scenario_lines("dump", oi_change_pct=None, rug_warn=False)
    text = "\n".join(lines)
    assert "возможен лонг после отскока от дна" in text


def test_dump_rug_warn_overrides_oi_matrix_regardless_of_quadrant():
    """rug_warn приоритетнее OI-матрицы -- даже на down_down (OI-аргумент
    "за" лонг) навес инсайдеров всё равно должен победить."""
    lines = pd._scenario_lines("dump", oi_change_pct=-0.5, rug_warn=True)
    text = "\n".join(lines)
    assert "НЕ торговать против навеса" in text
    assert "выход из позиций" not in text


def test_pump_up_up_quadrant():
    lines = pd._scenario_lines("pump", oi_change_pct=0.5, rug_warn=False)
    text = "\n".join(lines)
    assert "новые лонги, тренд силён — разворот не подтверждён" in text
    assert "возможен шорт после разворота" not in text


def test_pump_up_down_quadrant_keeps_original_reversal_scenario():
    lines = pd._scenario_lines("pump", oi_change_pct=-0.5, rug_warn=False)
    text = "\n".join(lines)
    assert "возможен шорт после разворота" in text


def test_pump_near_zero_quadrant_keeps_original_text():
    lines = pd._scenario_lines("pump", oi_change_pct=0.02, rug_warn=False)
    text = "\n".join(lines)
    assert "возможен шорт после разворота" in text


# ── _start_watch() интеграционно: полный алерт с реальным OI-снапшотом ─────

def test_start_watch_dump_down_up_end_to_end(monkeypatch):
    _patch_common(monkeypatch)
    ctx = _FakeCtx(oi_change=0.5)  # Цена↓ OI↑ -- KITE-случай
    asyncio.run(pd._start_watch(ctx, "KITEUSDT", "dump", 1.0, 4.0, 3.0))
    text = ctx.bot.sent_texts[0]
    assert "давление шортов, разворот не подтверждён" in text
    assert "возможен лонг" not in text


def test_start_watch_oi_fetched_exactly_once(monkeypatch):
    """Регрессия (см. докстринг _compose_alert, кейс EVAA): ctx.get_oi_change()
    мутирует историю на каждый вызов -- _start_watch() обязан фетчить OI РОВНО
    один раз и передать market_snapshot в _compose_alert(), а не дать ей
    фетчить повторно (иначе второй вызов увидел бы уже "погашенную" дельту)."""
    _patch_common(monkeypatch)
    calls = []
    ctx = _FakeCtx(oi_change=0.5)
    orig = ctx.get_oi_change
    ctx.get_oi_change = lambda sym: (calls.append(sym) or orig(sym))
    asyncio.run(pd._start_watch(ctx, "KITEUSDT", "dump", 1.0, 4.0, 3.0))
    assert len(calls) == 1
