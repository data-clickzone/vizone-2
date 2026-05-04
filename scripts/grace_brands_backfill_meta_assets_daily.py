#!/usr/bin/env python3
"""
Run the Grace Brands Meta creative asset archiver once per calendar day.

The Drive structure already uses date folders under the ad account folder. This
wrapper keeps that structure intact by refreshing one date folder at a time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh Grace Brands Meta creative assets in daily Drive folders."
    )
    parser.add_argument("--days", type=int, default=90, help="Number of calendar folders to refresh.")
    parser.add_argument("--since", help="YYYY-MM-DD. Overrides --days start date.")
    parser.add_argument("--until", help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--parent-folder-id", default=os.environ.get("GRACE_BRANDS_DRIVE_PARENT_FOLDER_ID", ""))
    parser.add_argument("--max-ads", type=int, default=0, help="Limit ads per day for testing. 0 means no limit.")
    parser.add_argument("--max-insight-pages", type=int, default=20)
    parser.add_argument("--output-dir", default="tmp/grace-brands-meta-assets")
    parser.add_argument("--include-all-assets", action="store_true")
    parser.add_argument("--share-anyone", action="store_true")
    parser.add_argument("--replace-folder-contents", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += dt.timedelta(days=1)
    return days


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    archiver = root / "scripts" / "grace_brands_archive_meta_assets_to_drive.py"

    until = dt.date.fromisoformat(args.until) if args.until else dt.date.today()
    since = dt.date.fromisoformat(args.since) if args.since else until - dt.timedelta(days=max(args.days, 1) - 1)

    if not args.parent_folder_id and not args.dry_run:
        print("Missing --parent-folder-id or GRACE_BRANDS_DRIVE_PARENT_FOLDER_ID.", file=sys.stderr)
        return 2

    failed: list[str] = []
    for day in date_range(since, until):
        day_key = day.isoformat()
        command = [
            sys.executable,
            str(archiver),
            "--since",
            day_key,
            "--until",
            day_key,
            "--folder-name",
            day_key,
            "--max-insight-pages",
            str(args.max_insight_pages),
            "--output-dir",
            args.output_dir,
        ]
        if args.parent_folder_id:
            command.extend(["--parent-folder-id", args.parent_folder_id])
        if args.max_ads:
            command.extend(["--max-ads", str(args.max_ads)])
        if args.include_all_assets:
            command.append("--include-all-assets")
        if args.share_anyone:
            command.append("--share-anyone")
        if args.replace_folder_contents:
            command.append("--replace-folder-contents")
        if args.dry_run:
            command.append("--dry-run")

        print(f"\n=== Refreshing {day_key} ===", flush=True)
        result = subprocess.run(command, cwd=str(root), env=os.environ.copy())
        if result.returncode != 0:
            failed.append(day_key)
            if not args.continue_on_error:
                break

    if failed:
        print(f"Failed date folders: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"Refreshed {len(date_range(since, until))} date folder(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
