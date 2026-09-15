"""Logging setup: daily files under <home>/logs, console mirror, 30-day retention.

Timestamps and retention are evaluated in a local zone resolved by
:func:`resolve_tz`. Windows has no `TZ` environment variable convention, so
the zone configured in ``config.yaml`` is what actually takes effect there.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
ROOT_LOGGER_NAME = "vettercode"
FALLBACK_TZ = "America/Toronto"


def resolve_tz(tz: ZoneInfo | str | None = None) -> ZoneInfo:
    """Resolve the zone used for log filenames and retention.

    Precedence:

    1. ``TZ`` environment variable, the POSIX convention — honoured so a Unix
       user can override per-invocation without editing config.
    2. ``tz``, the zone from ``config.yaml`` (``cfg.timezone``). Windows does
       not use ``TZ``, so in practice this is the effective setting there.
    3. ``FALLBACK_TZ``, for callers that have no config loaded yet.

    An unparseable value at any level falls through to the next, since a bad
    timezone should never stop logging from working.
    """
    for candidate in (os.environ.get("TZ"), tz, FALLBACK_TZ):
        if not candidate:
            continue
        if isinstance(candidate, ZoneInfo):
            return candidate
        try:
            return ZoneInfo(candidate)
        except Exception:  # noqa: BLE001 - fall through to the next candidate
            continue
    return ZoneInfo(FALLBACK_TZ)


def log_filename(
    log_dir: Path, now: datetime | None = None, tz: ZoneInfo | str | None = None
) -> Path:
    now = now or datetime.now(resolve_tz(tz))
    return log_dir / f"vettercode-{now.strftime('%Y-%m-%d')}.log"


def setup_logging(
    log_dir: Path, verbose: bool = False, tz: ZoneInfo | str | None = None
) -> logging.Logger:
    """Configure the `vettercode` logger. Safe to call repeatedly."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_filename(log_dir, tz=tz), encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(ROOT_LOGGER_NAME)


def purge_old_logs(
    log_dir: Path,
    days: int = 30,
    now: datetime | None = None,
    tz: ZoneInfo | str | None = None,
) -> list[Path]:
    """Delete daily log files older than `days`. Returns deleted paths."""
    if not log_dir.is_dir():
        return []
    zone = resolve_tz(tz)
    now = now or datetime.now(zone)
    cutoff = now - timedelta(days=days)
    deleted: list[Path] = []
    for path in sorted(log_dir.glob("vettercode-*.log")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=zone)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                deleted.append(path)
            except OSError:
                pass
    return deleted

