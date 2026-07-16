"""
pytest для scripts/shadow_dedupe.py (владелец, задача #245, 2026-07-16) --
санация journal/archive/shadow_signals_*.json. Покрывает: uid-дедуп (не
(symbol, ts), а через shadow_engine._record_uid -- symbol+type+ts),
кросс-файловый дедуп (глобальный `seen`, находка #233), сортировка по ts,
бэкап-payload, отчёт. GitHub Git Data API функции НЕ бьют по сети в тестах
(мокаются на уровне requests).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import shadow_dedupe as sd


def _rec(symbol, ts, rtype="pump_reversal_shadow", **extra):
    return {"symbol": symbol, "ts": ts, "type": rtype, **extra}


def test_dedupe_and_sort_removes_exact_duplicate_by_uid():
    records = [_rec("AKEUSDT", 100.0), _rec("AKEUSDT", 100.0)]
    clean, removed, seen = sd.dedupe_and_sort(records)
    assert len(clean) == 1
    assert removed == 1


def test_dedupe_and_sort_keeps_different_type_same_symbol_ts():
    records = [_rec("AKEUSDT", 100.0, rtype="pump_reversal_shadow"),
               _rec("AKEUSDT", 100.0, rtype="ema_stack_shadow")]
    clean, removed, seen = sd.dedupe_and_sort(records)
    assert len(clean) == 2
    assert removed == 0


def test_dedupe_and_sort_orders_by_ts():
    records = [_rec("AKEUSDT", 300.0), _rec("BTCUSDT", 100.0), _rec("ETHUSDT", 200.0)]
    clean, removed, seen = sd.dedupe_and_sort(records)
    assert [r["ts"] for r in clean] == [100.0, 200.0, 300.0]


def test_dedupe_and_sort_preserves_broken_records_without_symbol_or_ts():
    """Записи без symbol/ts -- НЕ трогаются (не дедупятся, не участвуют в
    сортировке по ts), проходят как есть -- честно, не пытаемся ключевать
    то, что не умеем."""
    broken = {"note": "malformed, no symbol/ts"}
    records = [_rec("AKEUSDT", 100.0), broken]
    clean, removed, seen = sd.dedupe_and_sort(records)
    assert broken in clean
    assert removed == 0


def test_dedupe_and_sort_global_seen_set_catches_cross_file_duplicates():
    """Владелец, находка #233 (воспроизведена намеренно): дедуп ТОЛЬКО внутри
    одного вызова пропускает дубли, размазанные МЕЖДУ файлами -- `seen`,
    переданный между вызовами, ловит их."""
    file1 = [_rec("AKEUSDT", 100.0)]
    file2 = [_rec("AKEUSDT", 100.0), _rec("BTCUSDT", 200.0)]  # первая -- дубль file1

    clean1, removed1, seen = sd.dedupe_and_sort(file1)
    assert removed1 == 0
    clean2, removed2, seen = sd.dedupe_and_sort(file2, seen=seen)
    assert removed2 == 1  # AKEUSDT/100.0 пойман как дубль ИЗ ДРУГОГО файла
    assert len(clean2) == 1
    assert clean2[0]["symbol"] == "BTCUSDT"


def test_strip_active_overlap_with_archive_removes_matching_uid():
    """Владелец, приёмка v130 (2026-07-16): active<->archive остаточный дубль
    -- запись, чей uid уже в архиве, должна быть убрана из активного файла."""
    archived = _rec("AKEUSDT", 100.0)
    active_records = [archived, _rec("BTCUSDT", 200.0)]
    archive_uids = {sd.shadow_engine._record_uid(archived)}
    clean, removed = sd.strip_active_overlap_with_archive(active_records, archive_uids)
    assert removed == 1
    assert len(clean) == 1
    assert clean[0]["symbol"] == "BTCUSDT"


def test_strip_active_overlap_with_archive_keeps_non_overlapping():
    active_records = [_rec("AKEUSDT", 100.0), _rec("BTCUSDT", 200.0)]
    clean, removed = sd.strip_active_overlap_with_archive(active_records, set())
    assert removed == 0
    assert len(clean) == 2


def test_strip_active_overlap_with_archive_preserves_broken_records():
    broken = {"note": "no symbol/ts"}
    clean, removed = sd.strip_active_overlap_with_archive([broken], {"some-uid"})
    assert removed == 0
    assert broken in clean


def test_process_files_reports_per_file_and_total(tmp_path):
    import json
    f1 = tmp_path / "shadow_signals_a.json"
    f2 = tmp_path / "shadow_signals_b.json"
    f1.write_text(json.dumps({"schema_version": 1, "records": [
        _rec("AKEUSDT", 100.0), _rec("AKEUSDT", 100.0)]}))
    f2.write_text(json.dumps({"schema_version": 1, "records": [
        _rec("AKEUSDT", 100.0), _rec("BTCUSDT", 200.0)]}))

    reports, new_contents, total_removed = sd.process_files([str(f1), str(f2)])
    assert total_removed == 2  # 1 внутри f1, 1 межфайловый (f2 против f1)
    r1 = next(r for r in reports if r["path"] == str(f1))
    r2 = next(r for r in reports if r["path"] == str(f2))
    assert r1 == {"path": str(f1), "before": 2, "after": 1, "removed": 1}
    assert r2 == {"path": str(f2), "before": 2, "after": 1, "removed": 1}
    assert len(new_contents[str(f1)]["records"]) == 1
    assert len(new_contents[str(f2)]["records"]) == 1


def test_process_files_untouched_when_no_duplicates(tmp_path):
    import json
    f1 = tmp_path / "shadow_signals_a.json"
    f1.write_text(json.dumps({"schema_version": 1, "records": [_rec("AKEUSDT", 100.0)]}))
    reports, new_contents, total_removed = sd.process_files([str(f1)])
    assert total_removed == 0
    assert reports[0]["removed"] == 0


def test_build_backup_payload_includes_all_files_as_is(tmp_path):
    import json
    f1 = tmp_path / "shadow_signals_a.json"
    payload = {"schema_version": 1, "records": [_rec("AKEUSDT", 100.0)]}
    f1.write_text(json.dumps(payload))
    backup = sd.build_backup_payload([str(f1)])
    assert "backed_up_at" in backup
    assert backup["files"]["shadow_signals_a.json"] == payload


def test_apply_local_writes_new_contents(tmp_path):
    import json
    f1 = tmp_path / "shadow_signals_a.json"
    f1.write_text(json.dumps({"schema_version": 1, "records": [_rec("AKEUSDT", 100.0)]}))
    new_contents = {str(f1): {"schema_version": 1, "records": [_rec("BTCUSDT", 200.0)]}}
    sd.apply_local(new_contents)
    with open(f1) as f:
        data = json.load(f)
    assert data["records"][0]["symbol"] == "BTCUSDT"


def test_format_report_shows_totals_and_no_dup_case():
    reports = [{"path": "/x/a.json", "before": 5, "after": 5, "removed": 0}]
    text = sd.format_report(reports, 0)
    assert "дублей не найдено" in text
    assert "5 -> 5" in text


def test_format_report_lists_changed_files_and_totals():
    reports = [
        {"path": "/x/a.json", "before": 10, "after": 3, "removed": 7},
        {"path": "/x/b.json", "before": 4, "after": 4, "removed": 0},
    ]
    text = sd.format_report(reports, 7)
    assert "a.json: 10 -> 3 (удалено 7 дублей)" in text
    assert "b.json" not in text.split("ИТОГО")[0]  # непострадавший файл не перечислен построчно
    assert "ИТОГО: 14 -> 7 записей, удалено 7 дублей (файлов затронуто: 1/2)" in text


def test_github_multi_file_commit_uses_git_data_api_sequence(monkeypatch):
    """Проверяет ПОСЛЕДОВАТЕЛЬНОСТЬ вызовов Git Data API (ref -> commit ->
    blob(ы) -> tree -> commit -> ref-update), не бьёт по реальной сети."""
    calls = []

    def fake_get(path):
        calls.append(("GET", path))
        if path.startswith("git/refs/"):
            return {"object": {"sha": "base_commit_sha"}}
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "base_tree_sha"}}
        raise AssertionError(f"unexpected GET {path}")

    def fake_post(path, body):
        calls.append(("POST", path, body))
        if path == "git/blobs":
            return {"sha": f"blob_{len([c for c in calls if c[0] == 'POST' and c[1] == 'git/blobs'])}"}
        if path == "git/trees":
            return {"sha": "new_tree_sha"}
        if path == "git/commits":
            return {"sha": "new_commit_sha"}
        raise AssertionError(f"unexpected POST {path}")

    def fake_patch(path, body):
        calls.append(("PATCH", path, body))
        return {}

    monkeypatch.setattr(sd, "_gh_get", fake_get)
    monkeypatch.setattr(sd, "_gh_post", fake_post)
    monkeypatch.setattr(sd, "_gh_patch", fake_patch)

    result_sha = sd.github_multi_file_commit({"journal/archive/x.json": {"records": []}}, "test commit")
    assert result_sha == "new_commit_sha"
    kinds = [c[0] for c in calls]
    assert kinds == ["GET", "GET", "POST", "POST", "POST", "PATCH"]
    assert calls[-1][1] == "git/refs/heads/main"
    assert calls[-1][2]["sha"] == "new_commit_sha"


def test_github_multi_file_commit_retries_on_ref_race(monkeypatch):
    """Владелец, живая находка 2026-07-16 (первый реальный запуск): PATCH
    ref может вернуть 422, если ref сдвинулся МЕЖДУ GET и PATCH (гонка с
    конкурентным shadow-sync коммитом живого бота, тот же класс, что #242).
    Ретрай со свежим ref/base_tree обязан вытянуть коммит со второй попытки."""
    import requests as real_requests

    state = {"patch_calls": 0}

    def fake_get(path):
        if path.startswith("git/refs/"):
            return {"object": {"sha": "base_commit_sha"}}
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "base_tree_sha"}}
        raise AssertionError(f"unexpected GET {path}")

    def fake_post(path, body):
        if path == "git/blobs":
            return {"sha": "blob_x"}
        if path == "git/trees":
            return {"sha": "new_tree_sha"}
        if path == "git/commits":
            return {"sha": "new_commit_sha"}
        raise AssertionError(f"unexpected POST {path}")

    def fake_patch(path, body):
        state["patch_calls"] += 1
        if state["patch_calls"] == 1:
            resp = real_requests.Response()
            resp.status_code = 422
            raise real_requests.exceptions.HTTPError("422 race", response=resp)
        return {}

    monkeypatch.setattr(sd, "_gh_get", fake_get)
    monkeypatch.setattr(sd, "_gh_post", fake_post)
    monkeypatch.setattr(sd, "_gh_patch", fake_patch)

    result_sha = sd.github_multi_file_commit({"journal/archive/x.json": {"records": []}}, "test commit")
    assert result_sha == "new_commit_sha"
    assert state["patch_calls"] == 2  # первая попытка -- 422, вторая -- успех


def test_github_multi_file_commit_raises_after_exhausting_retries(monkeypatch):
    import requests as real_requests

    def fake_get(path):
        if path.startswith("git/refs/"):
            return {"object": {"sha": "base_commit_sha"}}
        return {"tree": {"sha": "base_tree_sha"}}

    def fake_post(path, body):
        if path == "git/blobs":
            return {"sha": "blob_x"}
        if path == "git/trees":
            return {"sha": "new_tree_sha"}
        return {"sha": "new_commit_sha"}

    def always_fails(path, body):
        resp = real_requests.Response()
        resp.status_code = 422
        raise real_requests.exceptions.HTTPError("422 always", response=resp)

    monkeypatch.setattr(sd, "_gh_get", fake_get)
    monkeypatch.setattr(sd, "_gh_post", fake_post)
    monkeypatch.setattr(sd, "_gh_patch", always_fails)

    try:
        sd.github_multi_file_commit({"journal/archive/x.json": {"records": []}}, "test", max_attempts=3)
        assert False, "должен был поднять исключение после исчерпания попыток"
    except real_requests.exceptions.HTTPError:
        pass
