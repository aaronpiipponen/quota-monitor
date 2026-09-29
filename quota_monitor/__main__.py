"""Command-line entry point.

Usage:
    python -m quota_monitor poll          Poll all enabled providers once and store results.
    python -m quota_monitor status        Print the latest stored status per provider.
    python -m quota_monitor tray          Run the tray application (requires PySide6).
    python -m quota_monitor tray --demo   Run the tray application with fake data.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import load_config
from .dotenv import DotenvError, load_dotenv
from .poller import poll_all
from .storage import Storage


def _default_config_path() -> Path:
    return Path(os.environ.get("QUOTA_MONITOR_CONFIG", "config.toml"))


def _format_meter(meter: dict) -> str:
    unit = meter["unit"]
    value = f"{meter['value']:.1f}%" if unit == "%" else f"{meter['value']:.2f} {unit}"
    resets = f" (resets {meter['resets_at']})" if meter.get("resets_at") else ""
    return f"    {meter['label'] or meter['meter']:<24} {value}{resets}"


def cmd_poll(storage: Storage, config) -> int:
    results = poll_all(config)
    if not results:
        print("No providers are enabled in the config.")
        return 0
    for result in results:
        storage.save(result)
        marker = "OK  " if result.ok else "FAIL"
        detail = f"{len(result.meters)} meter(s)" if result.ok else result.error
        print(f"[{marker}] {result.provider}: {detail}")
    # A non-zero exit code lets schedulers and scripts detect partial failure.
    return 0 if all(r.ok for r in results) else 1


def cmd_status(storage: Storage) -> int:
    rows = storage.latest_status()
    if not rows:
        print("No data yet. Run 'python -m quota_monitor poll' first.")
        return 0
    for row in rows:
        state = "OK" if row["ok"] else f"FAILING: {row['error']}"
        print(f"{row['provider']}  (last poll {row['last_polled_at']}, {state})")
        if not row["ok"] and row["last_ok_at"]:
            print(f"    showing values from last success at {row['last_ok_at']}")
        for meter in row["meters"]:
            print(_format_meter(meter))
    return 0


def _run_tray(config, demo: bool) -> int:
    # Imported here so the CLI commands keep working on machines without PySide6.
    try:
        from .ui.app import run_tray
    except ImportError as exc:
        raise SystemExit(f"The tray app needs PySide6 (pip install -r requirements.txt): {exc}")
    return run_tray(config, demo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quota_monitor")
    parser.add_argument("--config", type=Path, default=_default_config_path(),
                        help="Path to config.toml (default: $QUOTA_MONITOR_CONFIG or ./config.toml)")
    parser.add_argument("command", choices=["poll", "status", "tray"])
    parser.add_argument("--demo", action="store_true",
                        help="tray only: show fake data; no config or credentials needed")
    args = parser.parse_args(argv)

    if args.command == "tray" and args.demo:
        return _run_tray(None, demo=True)

    # Secrets live in a .env file next to the config, so both files can be
    # located with a single --config path.
    try:
        load_dotenv(args.config.parent / ".env")
    except DotenvError as exc:
        raise SystemExit(str(exc))

    config = load_config(args.config)
    if args.command == "tray":
        return _run_tray(config, demo=False)

    storage = Storage(config.db_path)
    try:
        if args.command == "poll":
            return cmd_poll(storage, config)
        return cmd_status(storage)
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
