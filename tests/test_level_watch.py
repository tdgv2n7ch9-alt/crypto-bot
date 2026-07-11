"""
pytest для level_watch.py -- уровневый вотчер дневной разметки владельца (ETH,
Королев 4h, 2026-07-11). Покрывает чистые функции (zone_state/distance_pct/
format_level_alert/scan_zones), кулдаун (LevelWatchState) и файловую логику
(load/replace, реплейс с архивом в изолированной временной директории -- боевой
journal/watch_zones.json не трогается тестами). Сеть (fetch_price, GitHub-синк) не
тестируется здесь -- покрыто смоуком/ручной проверкой.
"""
import asyncio
import json
import os

import level_watch as lw


def _zone(side="LONG", lo=100.0, hi=110.0, prio=1, note=None):
    z = {"side": side, "lo": lo, "hi": hi, "prio": prio}
    if note:
        z["note"] = note
    return z


# ── Чистые функции: zone_state/distance_pct ──────────────────────────────────

def test_zone_state_in_zone():
    z = _zone(lo=100.0, hi=110.0)
    assert lw.zone_state(105.0, z) == "in_zone"
    assert lw.zone_state(100.0, z) == "in_zone"
    assert lw.zone_state(110.0, z) == "in_zone"


def test_zone_state_approaching_below():
    z = _zone(lo=100.0, hi=110.0)
    assert lw.zone_state(99.0, z) == "approaching"  # ~1.01% away


def test_zone_state_approaching_above():
    z = _zone(lo=100.0, hi=110.0)
    assert lw.zone_state(111.5, z) == "approaching"  # ~1.35% away


def test_zone_state_far_away_returns_none():
    z = _zone(lo=100.0, hi=110.0)
    assert lw.zone_state(50.0, z) is None
    assert lw.zone_state(200.0, z) is None


def test_zone_state_custom_approach_pct():
    z = _zone(lo=100.0, hi=110.0)
    price = 110.0 * 1.03
    assert lw.zone_state(price, z, approach_pct=1.5) is None
    assert lw.zone_state(price, z, approach_pct=5.0) == "approaching"


def test_distance_pct_zero_inside_zone():
    assert lw.distance_pct(105.0, _zone(lo=100.0, hi=110.0)) == 0.0


def test_distance_pct_positive_outside_zone():
    z = _zone(lo=100.0, hi=110.0)
    assert lw.distance_pct(99.0, z) > 0
    assert lw.distance_pct(111.0, z) > 0


def test_scan_zones_returns_only_active_zones():
    zones = [_zone(lo=100.0, hi=110.0), _zone(lo=490.0, hi=510.0)]
    result = lw.scan_zones(500.0, zones)
    assert len(result) == 1
    assert result[0][1] == "in_zone"


# ── format_level_alert ────────────────────────────────────────────────────────

def test_format_level_alert_contains_required_fields():
    z = _zone(side="SHORT", lo=1876.10, hi=1880.00, note="галочка автора")
    text = lw.format_level_alert("ETHUSDT", z, 1878.0, "in_zone",
                                  source="Королев 4h", updated="2026-07-11")
    assert "ETHUSDT" in text
    assert "SHORT" in text
    assert "1876.1" in text and "1880.0" in text
    assert "1878.00" in text
    assert "галочка автора" in text
    assert "Королев 4h" in text
    assert "2026-07-11" in text
    assert "ЦЕНА В ЗОНЕ" in text


def test_format_level_alert_approaching_shows_nonzero_distance():
    z = _zone(side="LONG", lo=1694.49, hi=1705.87)
    text = lw.format_level_alert("ETHUSDT", z, 1710.0, "approaching",
                                  source="Королев 4h", updated="2026-07-11")
    assert "Подходит к зоне" in text
    assert "0.00%" not in text


def test_format_level_alert_no_note_omits_note_line():
    z = _zone(side="LONG", lo=100.0, hi=110.0)
    text = lw.format_level_alert("ETHUSDT", z, 105.0, "in_zone")
    assert "note" not in text.lower()


# ── LevelWatchState (кулдаун) ─────────────────────────────────────────────────

def test_cooldown_blocks_repeat():
    state = lw.LevelWatchState()
    z = _zone()
    assert state.should_alert("ETHUSDT", z, "in_zone", now=10_000_000.0) is True
    assert state.should_alert("ETHUSDT", z, "in_zone", now=10_000_000.0 + 10) is False
    assert state.should_alert("ETHUSDT", z, "in_zone", now=10_000_000.0 + lw.COOLDOWN_SEC + 1) is True


def test_cooldown_independent_per_alert_type():
    state = lw.LevelWatchState()
    z = _zone()
    assert state.should_alert("ETHUSDT", z, "approaching", now=10_000_000.0) is True
    assert state.should_alert("ETHUSDT", z, "in_zone", now=10_000_000.0) is True


def test_cooldown_independent_per_zone():
    state = lw.LevelWatchState()
    z1, z2 = _zone(lo=100.0, hi=110.0), _zone(lo=200.0, hi=210.0)
    assert state.should_alert("ETHUSDT", z1, "in_zone", now=10_000_000.0) is True
    assert state.should_alert("ETHUSDT", z2, "in_zone", now=10_000_000.0) is True


def test_cooldown_independent_per_symbol():
    state = lw.LevelWatchState()
    z = _zone()
    assert state.should_alert("ETHUSDT", z, "in_zone", now=10_000_000.0) is True
    assert state.should_alert("BTCUSDT", z, "in_zone", now=10_000_000.0) is True


# ── check_and_alert (комбинация scan + cooldown + отправка) ──────────────────

def test_check_and_alert_sends_and_respects_cooldown():
    state = lw.LevelWatchState()
    zones = [_zone(lo=100.0, hi=110.0)]
    sent_log = []

    async def _fake_send(owner_id, text):
        sent_log.append((owner_id, text))

    async def run():
        r1 = await lw.check_and_alert(_fake_send, 42, state, "ETHUSDT", 105.0, zones, now=10_000_000.0)
        assert len(r1) == 1
        r2 = await lw.check_and_alert(_fake_send, 42, state, "ETHUSDT", 105.0, zones, now=10_000_000.0 + 5)
        assert r2 == []

    asyncio.run(run())
    assert len(sent_log) == 1
    assert sent_log[0][0] == 42


def test_check_and_alert_no_bot_send_still_returns_text():
    state = lw.LevelWatchState()
    zones = [_zone(lo=100.0, hi=110.0)]

    async def run():
        return await lw.check_and_alert(None, 42, state, "ETHUSDT", 105.0, zones, now=10_000_000.0)

    assert len(asyncio.run(run())) == 1


# ── Файловая логика: load/replace (изолированная временная директория) ───────

def _cfg(updated="2026-07-11", source="Королев 4h"):
    return {
        "updated": updated, "source": source,
        "ETHUSDT": [
            {"side": "LONG", "lo": 1694.49, "hi": 1705.87, "prio": 1},
            {"side": "LONG", "lo": 1556.57, "hi": 1588.43, "prio": 2},
            {"side": "LONG", "lo": 1414.45, "hi": 1436.73, "prio": 3},
            {"side": "SHORT", "lo": 1876.10, "hi": 1880.00, "prio": 1, "note": "галочка автора"},
            {"side": "SHORT", "lo": 1974.21, "hi": 2018.09, "prio": 2},
        ],
    }


def test_load_watch_zones_missing_file_returns_honest_empty(tmp_path):
    path = str(tmp_path / "nope.json")
    cfg = lw.load_watch_zones(path)
    assert cfg["updated"] is None
    assert cfg["source"] is None


def test_load_watch_zones_reads_existing_file(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_cfg(), f)
    cfg = lw.load_watch_zones(path)
    assert cfg["updated"] == "2026-07-11"
    assert len(cfg["ETHUSDT"]) == 5


def test_load_watch_zones_matches_task_spec(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_cfg(), f)
    cfg = lw.load_watch_zones(path)
    eth = cfg["ETHUSDT"]
    longs = [z for z in eth if z["side"] == "LONG"]
    shorts = [z for z in eth if z["side"] == "SHORT"]
    assert len(longs) == 3
    assert len(shorts) == 2
    assert any(z["lo"] == 1876.10 and z["hi"] == 1880.00 and z.get("note") == "галочка автора"
               for z in shorts)
    assert any(z["lo"] == 1974.21 and z["hi"] == 2018.09 for z in shorts)


def test_replace_watch_zones_creates_file_when_none_exists(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    history_dir = str(tmp_path / "history")
    ok = lw.replace_watch_zones(_cfg(), path=path, history_dir=history_dir)
    assert ok is True
    assert os.path.exists(path)
    assert not os.listdir(history_dir)  # ничего не было ДО -- архивировать нечего


def test_replace_watch_zones_archives_old_before_overwrite(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    history_dir = str(tmp_path / "history")

    old_cfg = _cfg(updated="2026-07-10", source="Королев 4h (вчера)")
    lw.replace_watch_zones(old_cfg, path=path, history_dir=history_dir)

    new_cfg = _cfg(updated="2026-07-11", source="Королев 4h")
    ok = lw.replace_watch_zones(new_cfg, path=path, history_dir=history_dir)
    assert ok is True

    # активный файл -- новая версия
    active = lw.load_watch_zones(path)
    assert active["updated"] == "2026-07-11"

    # старая версия целиком в архиве под СВОЕЙ датой
    archived_path = os.path.join(history_dir, "2026-07-10.json")
    assert os.path.exists(archived_path)
    archived = lw.load_watch_zones(archived_path)
    assert archived["updated"] == "2026-07-10"
    assert archived["source"] == "Королев 4h (вчера)"


def test_replace_watch_zones_is_full_replace_not_merge(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    history_dir = str(tmp_path / "history")

    old_cfg = _cfg()
    old_cfg["BTCUSDT"] = [{"side": "LONG", "lo": 1.0, "hi": 2.0, "prio": 1}]
    lw.replace_watch_zones(old_cfg, path=path, history_dir=history_dir)

    new_cfg = {"updated": "2026-07-12", "source": "Королев 4h", "ETHUSDT": []}
    lw.replace_watch_zones(new_cfg, path=path, history_dir=history_dir)

    active = lw.load_watch_zones(path)
    assert "BTCUSDT" not in active  # старый символ НЕ пережил реплейс (не merge)
    assert active["ETHUSDT"] == []


def test_replace_watch_zones_multiple_days_each_archived_separately(tmp_path):
    path = str(tmp_path / "watch_zones.json")
    history_dir = str(tmp_path / "history")

    lw.replace_watch_zones(_cfg(updated="2026-07-09"), path=path, history_dir=history_dir)
    lw.replace_watch_zones(_cfg(updated="2026-07-10"), path=path, history_dir=history_dir)
    lw.replace_watch_zones(_cfg(updated="2026-07-11"), path=path, history_dir=history_dir)

    assert os.path.exists(os.path.join(history_dir, "2026-07-09.json"))
    assert os.path.exists(os.path.join(history_dir, "2026-07-10.json"))
    assert lw.load_watch_zones(path)["updated"] == "2026-07-11"


# ── run_level_watch (файл + чистые функции вместе, без сети) ─────────────────

def test_run_level_watch_reads_config_and_alerts(tmp_path, monkeypatch):
    path = str(tmp_path / "watch_zones.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_cfg(), f)

    def fake_fetch(symbol):
        return 1700.0  # внутри prio-1 LONG зоны 1694.49-1705.87

    monkeypatch.setattr(lw, "fetch_price", fake_fetch)
    sent = []

    async def _fake_send(owner_id, text):
        sent.append(text)

    async def run():
        return await lw.run_level_watch(_fake_send, 42, path=path, iterations=2, poll_interval_sec=0)

    state = asyncio.run(run())
    assert isinstance(state, lw.LevelWatchState)
    assert len(sent) == 1  # первый тик алертит, второй блокирует кулдаун
    assert "Королев 4h" in sent[0]
    assert "2026-07-11" in sent[0]


def test_run_level_watch_picks_up_replaced_config_mid_run(tmp_path, monkeypatch):
    # владелец обновил зоны через /zones_set МЕЖДУ тиками -- следующий тик должен
    # увидеть новый конфиг (перечитывается с диска на каждом тике, не кэшируется)
    path = str(tmp_path / "watch_zones.json")
    history_dir = str(tmp_path / "history")
    lw.replace_watch_zones(_cfg(updated="2026-07-11"), path=path, history_dir=history_dir)

    # разные цены на двух тиках -- бьют в РАЗНЫЕ зоны, иначе второй тик заблокировал
    # бы кулдаун той же зоны (lo,hi не меняются между "вчера"/"сегодня" в _cfg()) и
    # тест бы не отличал "не пришёл алерт из-за кулдауна" от "конфиг не перечитался"
    call_count = {"n": 0}

    def fake_fetch(symbol):
        call_count["n"] += 1
        return 1700.0 if call_count["n"] == 1 else 2000.0  # 2-й тик: prio-2 SHORT зона

    monkeypatch.setattr(lw, "fetch_price", fake_fetch)
    sent = []

    async def _fake_send(owner_id, text):
        sent.append(text)

    async def run():
        state = lw.LevelWatchState()
        await lw.run_level_watch(_fake_send, 42, path=path, state=state, iterations=1, poll_interval_sec=0)
        # владелец обновляет зоны (новая дата) между тиками
        lw.replace_watch_zones(_cfg(updated="2026-07-12"), path=path, history_dir=history_dir)
        await lw.run_level_watch(_fake_send, 42, path=path, state=state, iterations=1, poll_interval_sec=0)

    asyncio.run(run())
    assert "2026-07-11" in sent[0]
    assert "2026-07-12" in sent[1]  # второй прогон уже видит новую разметку
