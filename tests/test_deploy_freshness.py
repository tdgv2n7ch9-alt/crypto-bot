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
    # Владелец, 2026-07-15, п.4: railway up/--ci в обход deploy.sh запрещены --
    # алерт больше не должен подсказывать этот путь как рекомендацию.
    assert "railway up" not in call_text
    assert "deploy.sh" in call_text


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


# ── _is_deploy_irrelevant_diff() -- Пакет 6 М2 ──

def test_irrelevant_diff_empty_list_is_false():
    assert bot._is_deploy_irrelevant_diff([]) is False


def test_irrelevant_diff_journal_only_is_true():
    assert bot._is_deploy_irrelevant_diff(["journal/signals.json", "journal/shadow_signals.json"]) is True


def test_irrelevant_diff_chat_ids_is_true():
    assert bot._is_deploy_irrelevant_diff(["data/chat_ids.json"]) is True


def test_irrelevant_diff_backups_is_true():
    assert bot._is_deploy_irrelevant_diff(["backups/2026-07-12/signals.json"]) is True


def test_irrelevant_diff_mixed_with_code_is_false():
    assert bot._is_deploy_irrelevant_diff(["journal/signals.json", "bot.py"]) is False


def test_irrelevant_diff_pure_code_is_false():
    assert bot._is_deploy_irrelevant_diff(["bot.py", "ta_extra.py"]) is False


# ── ДЕФЕКТ владельца, 2026-07-15: PROGRESS.md/docs не в старом allowlist'е,
# ложно-положительный алерт на диапазоне 72ccdc8..47ddbd0 (только PROGRESS.md
# + journal/*, SKIPPED был корректен). Фикс -- watchPatterns из railway.json
# как авторитетный источник вместо ручного дубликата списка.

def test_irrelevant_diff_docs_only_is_true():
    assert bot._is_deploy_irrelevant_diff(["PROGRESS.md"]) is True


def test_irrelevant_diff_docs_mixed_with_journal_is_true():
    """Точное воспроизведение диапазона 72ccdc8..47ddbd0 -- PROGRESS.md +
    несколько journal-файлов, ни один не попадает под watchPatterns."""
    assert bot._is_deploy_irrelevant_diff(
        ["PROGRESS.md", "journal/shadow_signals.json", "journal/signals.json",
         "journal/archive/shadow_signals_20260711_20260712_4.json"]) is True


def test_irrelevant_diff_docs_dir_is_true():
    assert bot._is_deploy_irrelevant_diff(["docs/TZ_P-MiniApp_v1.md"]) is True


def test_irrelevant_diff_docs_mixed_with_code_is_false():
    assert bot._is_deploy_irrelevant_diff(["PROGRESS.md", "bot.py"]) is False


def test_irrelevant_diff_uses_live_watch_patterns_not_hardcoded(monkeypatch):
    """Список НЕ хардкожен в bot.py -- читается через tools.deploy_watch_check.
    touches_watch_path()'s дефолтный аргумент patterns=None вызывает
    load_watch_patterns() -- подменяем ЕЁ, доказывая отсутствие дублирующего
    хардкода паттернов внутри bot.py."""
    from tools import deploy_watch_check as dwc
    monkeypatch.setattr(dwc, "load_watch_patterns", lambda *a, **kw: ["PROGRESS.md"])
    assert bot._is_deploy_irrelevant_diff(["PROGRESS.md"]) is False
    assert bot._is_deploy_irrelevant_diff(["journal/signals.json"]) is True


# ── _compare_commits_sync() ──

def test_compare_commits_none_when_github_not_configured():
    with patch("bot.signal_journal._github_configured", return_value=False):
        result = bot._compare_commits_sync("aaa", "bbb")
    assert result is None


def test_compare_commits_parses_filenames():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"files": [{"filename": "journal/signals.json"}, {"filename": "bot.py"}]}
    fake_resp.raise_for_status = lambda: None
    with patch("bot.signal_journal._github_configured", return_value=True), \
         patch("bot.requests.get", return_value=fake_resp):
        result = bot._compare_commits_sync("aaa", "bbb")
    assert result == ["journal/signals.json", "bot.py"]


def test_compare_commits_network_failure_is_honest_none():
    with patch("bot.signal_journal._github_configured", return_value=True), \
         patch("bot.requests.get", side_effect=Exception("network down")):
        result = bot._compare_commits_sync("aaa", "bbb")
    assert result is None


# ── check_deploy_freshness() -- journal-only diff advances baseline, no alert ──

def test_no_alert_and_boot_sha_advances_when_diff_is_journal_only():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))), \
         patch("bot._compare_commits_sync", return_value=["journal/signals.json"]):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_not_called()
    assert bot._deploy_check_boot_sha["sha"] == "bbb2222"


def test_alert_still_fires_when_diff_includes_code_file():
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))), \
         patch("bot._compare_commits_sync", return_value=["journal/signals.json", "bot.py"]):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_called_once()
    assert bot._deploy_check_boot_sha["sha"] == "aaa1111"  # НЕ сдвинулся -- деплой реально нужен


def test_alert_still_fires_when_compare_unavailable():
    # GitHub compare недоступен (None) -- честно падаем обратно на прежнее
    # age-based поведение, не считаем безопасным по умолчанию.
    with patch("bot._fetch_main_head_sync", return_value=("bbb2222", _iso(20 * 60))), \
         patch("bot._compare_commits_sync", return_value=None):
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        asyncio.run(bot.check_deploy_freshness(fake_bot))
    fake_bot.send_message.assert_called_once()
