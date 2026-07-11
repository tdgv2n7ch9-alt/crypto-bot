"""
pytest для bot.check_deploy_freshness()/_fetch_main_head_sync() -- «Пакетный ритм»,
п.2: алерт owner-чату, если main ушёл вперёд >15 мин, а этот процесс всё ещё на
старом коммите (живая находка сессии 2026-07-11 -- Railway SKIPPED-нул несколько
деплоев подряд без единого алерта, обнаружено только ручной проверкой). Сеть/GitHub
замоканы -- никакого реального API-вызова.
"""
import asyncio
import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _iso(age_sec: float) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_sec)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def setup_function(_):
    # Изолируем модульное состояние между тестами -- анти-спам set() персистентен
    # на модуле, иначе тесты друг друга ломают порядком выполнения.
    bot._deploy_check_boot_sha["sha"] = "aaa1111"
    bot._deploy_check_boot_sha["date"] = None
    bot._deploy_alerted_shas.clear()


# ── _fetch_main_head_sync() ──

def test_fetch_main_head_returns_none_when_github_not_configured():
    with patch("bot.signal_journal._github_configured", return_value=False):
        sha, date = bot._fetch_main_head_sync()
    assert sha is None and date is None


def test_fetch_main_head_parses_sha_and_date():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"sha": "deadbeef123", "commit": {"committer": {"date": "2026-07-11T12:00:00Z"}}}
    fake_resp.raise_for_status = lambda: None
    with patch("bot.signal_journal._github_configured", return_value=True), \
         patch("bot.requests.get", return_value=fake_resp):
        sha, date = bot._fetch_main_head_sync()
    assert sha == "deadbeef123"
    assert date == "2026-07-11T12:00:00Z"


def test_fetch_main_head_network_failure_is_honest_none():
    with patch("bot.signal_journal._github_configured", return_value=True), \
         patch("bot.requests.get", side_effect=Exception("network down")):
        sha, date = bot._fetch_main_head_sync()
    assert sha is None and date is None


# ── check_deploy_freshness() ──

def test_no_alert_when_boot_sha_not_set():
    bot._deploy_check_boot_sha["sha"] = None
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()


def test_no_alert_when_main_matches_boot_sha():
    with patch("bot._fetch_main_head_sync", return_value=("aaa1111", _iso(20 * 60))):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()


def test_no_alert_when_push_younger_than_threshold():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(5 * 60))):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()


def test_alerts_when_push_older_than_threshold_and_sha_differs():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_called_once()
    call_text = fake_bot.send_message.call_args[0][1]
    assert "aaa1111" in call_text
    assert "bbb2222" in call_text


def test_no_duplicate_alert_for_same_stuck_sha():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    assert fake_bot.send_message.call_count == 1


def test_no_alert_when_fetch_fails():
    with patch("bot._fetch_main_head_sync", return_value=(None, None)):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()


def test_malformed_date_is_honest_no_crash_no_alert():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", "not-a-date")):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()
