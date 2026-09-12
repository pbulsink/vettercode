"""Logging setup: daily files under <home>/logs, console mirror, 30-day retention."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
ROOT_LOGGER_NAME = "vettercode"


def log_filename(log_dir: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(ZoneInfo("America/Toronto"))
    return log_dir / f"vettercode-{now.strftime('%Y-%m-%d')}.log"


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    """Configure the `vettercode` logger. Safe to call repeatedly."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_filename(log_dir), encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(ROOT_LOGGER_NAME)


def purge_old_logs(log_dir: Path, days: int = 30, now: datetime | None = None) -> list[Path]:
    """Delete daily log files older than `days`. Returns deleted paths."""
    if not log_dir.is_dir():
        return []
    now = now or datetime.now(ZoneInfo("America/Toronto"))
    cutoff = now - timedelta(days=days)
    deleted: list[Path] = []
    for path in sorted(log_dir.glob("vettercode-*.log")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=path_tz())
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                deleted.append(path)
            except OSError:
                pass
    return deleted


def path_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "America/Toronto")) if os.environ.get("TZ") else ZoneInfo("America/Toronto")
