"""Safe cleanup of local files after verified uploads."""
from __future__ import annotations

import logging
from pathlib import Path

from config import Config
from storage import Database

log = logging.getLogger("yta.cleanup")


def eligibility(db: Database, video_id: str) -> tuple[bool, str]:
    video = db.video(video_id)
    if not video:
        return False, "video not found"
    if video.get("status") != "verified":
        return False, "video not verified"
    return True, "ok"


def cleanup_video(cfg: Config, db: Database, video_id: str) -> int | None:
    if cfg.dry_run:
        return None
    ok, reason = eligibility(db, video_id)
    if not ok:
        log.info("Skip cleanup for %s: %s", video_id, reason)
        return None
    video = db.video(video_id)
    work_dir = Path(video["work_dir"]) if video and video.get("work_dir") else None
    if not work_dir or not work_dir.exists():
        return None

    deleted = 0
    for path in sorted(work_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".tmp", ".part", ".srt"}:
            path.unlink()
            deleted += 1

    db.update_video(video_id, files_deleted=deleted)
    return deleted
