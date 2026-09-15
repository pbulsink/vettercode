"""Command line interface: `vettercode`."""

from __future__ import annotations

import os
import sys

from . import __version__
from .auth import AuthError
from .config import AGENT_MODES, load_config, override
from .logsetup import get_logger, purge_old_logs, setup_logging
from .runner import run_once


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="vettercode",
        description="Nightly GitHub issue agent: triages @vettercode issues with LM Studio + mini-swe-agent.",
    )
    parser.add_argument("--version", action="version", version=f"vettercode {__version__}")
    parser.add_argument(
        "--config-home",
        default=None,
        help=(
            "override the config/state directory (default: $VETTERCODE_HOME, else "
            "~/.config/vettercode on macOS/Linux or %%APPDATA%%\\vettercode on Windows)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=list(AGENT_MODES),
        default=None,
        help="override agent_mode from config for this run",
    )
    parser.add_argument("--repo", metavar="OWNER/NAME", default=None, help="process only this repo")
    parser.add_argument("--dry-run", action="store_true", help="fetch & filter only; do not invoke the agent")
    parser.add_argument("--ignore-window", action="store_true", help="run even outside the configured time window")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    if args.config_home:
        os.environ["VETTERCODE_HOME"] = args.config_home

    cfg = load_config()
    if args.mode:
        cfg = override(cfg, agent_mode=args.mode)

    setup_logging(cfg.log_dir, verbose=args.verbose, tz=cfg.timezone)
    purge_old_logs(cfg.log_dir, tz=cfg.timezone)
    log = get_logger()

    try:
        result = run_once(
            cfg,
            ignore_window=args.ignore_window,
            dry_run=args.dry_run,
            only_repo=args.repo,
        )
    except AuthError as e:
        log.error("authentication failed: %s", e)
        if e.hint:
            log.error("hint: %s", e.hint)
        return 2

    if result.out_of_window:
        log.info("exit: outside run window (use --ignore-window to force)")
        return 0

    for outcome in result.outcomes:
        log.info(
            "  %s/%s#%s mode=%s status=%s%s",
            outcome.owner, outcome.name, outcome.number, outcome.mode, outcome.status,
            f" pr={outcome.pr_url}" if outcome.pr_url else "",
        )
    for error in result.errors:
        log.error("  ! %s", error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
