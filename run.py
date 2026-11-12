#!/usr/bin/env python3
"""Command-line entry point for YouTube AI Studio."""
from __future__ import annotations

import argparse
import logging
import sys
import time

import monitor
from config import Config, RIGHTS_VALUES
from pipeline import Pipeline
from storage import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube AI Studio")
    parser.add_argument("--status", action="store_true",
                        help="show current project status")
    parser.add_argument("--json", action="store_true",
                        help="print status as JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="enable dry-run mode")
    parser.add_argument("--loop", action="store_true",
                        help="repeat runs using LOOP_INTERVAL_MINUTES")
    parser.add_argument("--set-rights", nargs=2, metavar=("VIDEO_ID", "RIGHTS"),
                        help=f"set rights status; allowed: {', '.join(RIGHTS_VALUES)}")
    parser.add_argument("--retry", metavar="VIDEO_ID",
                        help="clear a video's halted state")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        cfg = Config.load()
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        cfg.dry_run = True

    db = Database(cfg.db_path)
    try:
        if args.status:
            stats = monitor.collect(cfg, db)
            print(monitor.to_json(stats) if args.json else monitor.render(stats))
            return 0

        if args.set_rights:
            video_id, rights = args.set_rights
            if rights not in RIGHTS_VALUES:
                print(
                    f"Invalid rights status. Choose: {', '.join(RIGHTS_VALUES)}", file=sys.stderr)
                return 2
            if not db.video(video_id):
                print(f"Video not found: {video_id}", file=sys.stderr)
                return 2
            db.set_rights(video_id, rights)
            print(f"{video_id}: rights_status={rights}")
            return 0

        if args.retry:
            if not db.video(args.retry):
                print(f"Video not found: {args.retry}", file=sys.stderr)
                return 2
            db.reset_video(args.retry)
            print(f"{args.retry}: retry enabled")
            return 0

        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        pipeline = Pipeline.build(cfg, db)
        exit_code = 0

        while True:
            stats = pipeline.run_once()
            if isinstance(stats, dict):
                print(monitor.render(monitor.collect(cfg, db)))
                print(
                    f"Run status: {stats.get('status', 'unknown')} (failures: {stats.get('failures', 0)})")
                if stats.get("status") != "ok":
                    exit_code = 1
            if not args.loop:
                break
            interval = max(1, int(getattr(cfg, "loop_interval_minutes", 360)))
            logging.info("Next run in %d minutes", interval)
            time.sleep(interval)

        return exit_code
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
