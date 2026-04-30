#!/usr/bin/env python3
"""
Incrementally archive yesterday's The Mia Meta creative assets.

This is the daily cron entrypoint. It appends missing assets into the durable
latest_90d Drive folder and relies on the archiver's Drive-level dedupe.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_FOLDER_NAME = "latest_90d"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive yesterday's The Mia Meta assets into latest_90d without replacing existing Drive files."
    )
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--folder-name", default=DEFAULT_FOLDER_NAME)
    parser.add_argument("--parent-folder-id", default=os.environ.get("THE_MIA_DRIVE_PARENT_FOLDER_ID", ""))
    parser.add_argument("--max-insight-pages", type=int, default=20)
    parser.add_argument("--max-ads", type=int, default=0, help="Limit ads for testing. 0 means no limit.")
    parser.add_argument("--output-dir", default="tmp/the-mia-meta-assets")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    archiver = root / "scripts" / "the_mia_archive_meta_assets_to_drive.py"
    target_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today() - dt.timedelta(days=1)

    if not args.parent_folder_id and not args.dry_run:
        print("Missing --parent-folder-id or THE_MIA_DRIVE_PARENT_FOLDER_ID.", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-u",
        str(archiver),
        "--since",
        target_date.isoformat(),
        "--until",
        target_date.isoformat(),
        "--folder-name",
        args.folder_name,
        "--max-insight-pages",
        str(args.max_insight_pages),
        "--output-dir",
        args.output_dir,
        "--include-all-assets",
        "--share-anyone",
    ]
    if args.parent_folder_id:
        command.extend(["--parent-folder-id", args.parent_folder_id])
    if args.max_ads:
        command.extend(["--max-ads", str(args.max_ads)])
    if args.dry_run:
        command.append("--dry-run")

    print(f"Incremental The Mia Meta asset archive for {target_date.isoformat()} -> {args.folder_name}", flush=True)
    return subprocess.run(command, cwd=str(root), env=os.environ.copy()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
