"""pytest: run_exit_tracker() "вход активен" алерт -- кликабельная ссылка на исходную
карточку сигнала (владелец, 2026-07-17, задача #272). Прогоняет ОДИН тик через
предзаполненный signal_loop._active_signals, не проходя через _send_alert() целиком
(та требует полноценного fa_engine-результата/чарта -- избыточно для этого теста,
сама сборка origin_msg_id из _send_alert покрыта отдельно смоук-проверкой при
приёмке). live_prices.get_live_price monkeypatch'ится, чтобы не ходить в сеть."""
import asyncio

import live_prices
import signal_loop as sl


class _FakeTgBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _base_signal(**overrides):
    rec = {
        "symbol": "SEIUSDT", "direction": "long", "chat_id": 7009350191,
        "entry_lo": 0.4700, "entry_hi": 0.4720,
        "sl": 0.4500, "tp1": 0.5000, "tp2": 0.5200, "tp3": 0.5500,
        "entered": False, "entered_price": None,
        "tp1_hit": False, "tp2_hit": False, "closed": False,
        "structure_warned": False, "journal_id": 1,
        "created_ts": 10_000_000.0, "origin_msg_id": None,
    }
    rec.update(overrides)
    return rec


def _run_one_tick(monkeypatch, price):
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (price, 0))
    tg_bot = _FakeTgBot()
    asyncio.run(sl.run_exit_tracker(bot=None, tg_bot=tg_bot))
    return tg_bot


def test_entry_alert_includes_link_when_origin_msg_id_present(monkeypatch):
    sl._active_signals.clear()
    sl._active_signals[1] = _base_signal(origin_msg_id=555)
    tg_bot = _run_one_tick(monkeypatch, price=0.4710)  # внутри entry-зоны
    assert len(tg_bot.sent) == 1
    chat_id, text = tg_bot.sent[0]
    assert "вход активен" in text
    assert '<a href="https://t.me/c/7009350191/555">Сигнал</a>' in text


def test_entry_alert_omits_link_when_origin_msg_id_absent(monkeypatch):
    # старые сигналы до фичи (или сама отправка карточки упала) -- без ссылки, не падает
    sl._active_signals.clear()
    sl._active_signals[1] = _base_signal(origin_msg_id=None)
    tg_bot = _run_one_tick(monkeypatch, price=0.4710)
    assert len(tg_bot.sent) == 1
    chat_id, text = tg_bot.sent[0]
    assert "вход активен" in text
    assert "<a href" not in text
