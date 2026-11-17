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

    source_path = Path(video["source_path"]).resolve() if video and video.get("source_path") else None
    managed_paths = {
        Path(clip["path"]).resolve()
        for clip in db.clips(video_id)
        if clip.get("path") and clip.get("kind") == "short"
    }
    deleted = 0
    work_root = work_dir.resolve()
    for path in sorted(managed_paths):
        # Only remove generated short clips recorded by this application. Keep
        # source videos, the registered long/original clip, and unrelated files.
        try:
            path.relative_to(work_root)
        except ValueError:
            continue
        if path == source_path or path.name.startswith("original."):
            continue
        if path.is_file():
            path.unlink()
            deleted += 1

    db.update_video(video_id, files_deleted=deleted)
    return deleted
