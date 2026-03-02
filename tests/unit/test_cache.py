from __future__ import annotations

from datetime import date

import pytest

from app.cache import FileCache


@pytest.mark.unit
def test_save_and_load_ready_payload(tmp_path):
    cache = FileCache(cache_dir=tmp_path, timezone_name="Europe/Moscow", retention_days=30)
    profile = "denis"
    target = date(2026, 2, 27)
    payload = {"status": "ready", "value": 42}

    assert cache.save_ready(profile, "recovery", target, payload) is True

    loaded = cache.load_ready(profile, "recovery", target)
    assert loaded == payload


@pytest.mark.unit
def test_non_ready_payload_is_not_saved(tmp_path):
    cache = FileCache(cache_dir=tmp_path, timezone_name="Europe/Moscow", retention_days=30)
    profile = "denis"
    target = date(2026, 2, 27)

    assert cache.save_ready(profile, "recovery", target, {"status": "pending"}) is False
    assert cache.load_ready(profile, "recovery", target) is None


@pytest.mark.unit
def test_corrupted_cache_file_is_ignored(tmp_path):
    cache = FileCache(cache_dir=tmp_path, timezone_name="Europe/Moscow", retention_days=30)
    profile = "denis"
    target = date(2026, 2, 27)
    path = tmp_path / profile / "recovery_2026-02-27.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    assert cache.load_ready(profile, "recovery", target) is None


@pytest.mark.unit
def test_cleanup_deletes_only_expired_json_files(tmp_path):
    cache = FileCache(cache_dir=tmp_path, timezone_name="Europe/Moscow", retention_days=30)
    (tmp_path / "denis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "denis" / "recovery_2026-01-10.json").write_text('{"status":"ready"}', encoding="utf-8")
    (tmp_path / "denis" / "recovery_2026-01-28.json").write_text('{"status":"ready"}', encoding="utf-8")
    (tmp_path / "denis" / "invalid_name.json").write_text('{"status":"ready"}', encoding="utf-8")

    deleted = cache.cleanup_expired(today=date(2026, 2, 27))

    assert deleted == 1
    assert not (tmp_path / "denis" / "recovery_2026-01-10.json").exists()
    assert (tmp_path / "denis" / "recovery_2026-01-28.json").exists()
    assert (tmp_path / "denis" / "invalid_name.json").exists()
