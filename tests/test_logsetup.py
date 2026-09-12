import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from vettercode.logsetup import purge_old_logs, setup_logging


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
