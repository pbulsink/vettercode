import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from vettercode.logsetup import purge_old_logs, resolve_tz, setup_logging


@pytest.fixture(autouse=True)
def _no_tz_env(monkeypatch):
    """The host's TZ must not leak into zone-resolution tests."""
    monkeypatch.delenv("TZ", raising=False)


def test_setup_logging_creates_file_and_stream_handlers(tmp_path):
    log_dir = tmp_path / "logs"
    logger = setup_logging(log_dir)
    assert len(logger.handlers) == 2
    logger.info("hello-log")
    files = list(log_dir.glob("vettercode-*.log"))
    assert len(files) == 1
    assert "hello-log" in files[0].read_text()


def test_setup_logging_is_idempotent(tmp_path):
    log_dir = tmp_path / "logs"
    setup_logging(log_dir)
    logger = setup_logging(log_dir, verbose=True)
    assert len(logger.handlers) == 2


def test_daily_filename(tmp_path):
    from vettercode.logsetup import log_filename

    now = datetime(2026, 9, 11, 12, 0, tzinfo=ZoneInfo("America/Toronto"))
    assert log_filename(tmp_path, now).name == "vettercode-2026-09-11.log"


def test_purge_old_logs_deletes_only_old(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tz = ZoneInfo("America/Toronto")
    now = datetime.now(tz)

    old = log_dir / "vettercode-2020-01-01.log"
    old.write_text("old")
    old_ts = (now - timedelta(days=400)).timestamp()
    os.utime(old, (old_ts, old_ts))

    fresh = log_dir / "vettercode-2026-09-10.log"
    fresh.write_text("new")

    deleted = purge_old_logs(log_dir, days=30, now=now)
    assert old in deleted
    assert not old.exists()
    assert fresh.exists()


def test_purge_missing_dir_returns_empty(tmp_path):
    assert purge_old_logs(tmp_path / "does-not-exist") == []


def test_resolve_tz_prefers_tz_env(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert resolve_tz("America/Toronto") == ZoneInfo("Europe/Berlin")


def test_resolve_tz_uses_configured_zone_when_tz_unset():
    """The Windows path: no TZ env var, so config.yaml's timezone wins."""
    assert resolve_tz("Asia/Tokyo") == ZoneInfo("Asia/Tokyo")


def test_resolve_tz_accepts_zoneinfo_instance():
    zone = ZoneInfo("Asia/Tokyo")
    assert resolve_tz(zone) is zone


def test_resolve_tz_falls_back_without_config():
    assert resolve_tz() == ZoneInfo("America/Toronto")


def test_resolve_tz_bad_env_falls_through_to_config(monkeypatch):
    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")
    assert resolve_tz("Asia/Tokyo") == ZoneInfo("Asia/Tokyo")


def test_resolve_tz_bad_config_falls_through_to_default():
    assert resolve_tz("Mars/Olympus_Mons") == ZoneInfo("America/Toronto")


def test_log_filename_uses_configured_zone(tmp_path):
    """Same instant, two zones that straddle midnight -> different log files."""
    from vettercode.logsetup import log_filename

    instant = datetime(2026, 9, 11, 3, 30, tzinfo=timezone.utc)
    toronto = log_filename(tmp_path, instant.astimezone(ZoneInfo("America/Toronto")))
    tokyo = log_filename(tmp_path, instant.astimezone(ZoneInfo("Asia/Tokyo")))
    assert toronto.name == "vettercode-2026-09-10.log"
    assert tokyo.name == "vettercode-2026-09-11.log"


def test_setup_logging_names_file_by_configured_zone(tmp_path):
    log_dir = tmp_path / "logs"
    setup_logging(log_dir, tz="Asia/Tokyo")
    expected = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
    assert (log_dir / f"vettercode-{expected}.log").exists()


def test_purge_old_logs_accepts_configured_zone(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old = log_dir / "vettercode-2020-01-01.log"
    old.write_text("old")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
    os.utime(old, (old_ts, old_ts))

    assert purge_old_logs(log_dir, days=30, tz="Asia/Tokyo") == [old]
