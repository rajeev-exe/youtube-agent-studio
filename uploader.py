"""Upload and verification logic for YouTube campaigns."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from config import Config
from scheduler import Scheduler
from storage import Database

log = logging.getLogger("yta.upload")


class UploadHeld(Exception):
    pass


class Uploader:
    def __init__(self, cfg: Config, db: Database, api, scheduler: Scheduler | None = None, sleep=time.sleep):
        self.cfg = cfg
        self.db = db
        self.api = api
        self.scheduler = scheduler or Scheduler(cfg, db)
        self.sleep = sleep

    def _clip_path(self, vid: str, kind: str, part_no: int) -> Path | None:
        clips = self.db.clips(vid)
        for c in clips:
            if c["kind"] == kind and c["part_no"] == part_no:
                p = c.get("path")
                return Path(p) if p else None
        return None

    def upload_campaign(self, video_id: str) -> str:
        """Return 'done' when the whole campaign is uploaded, 'quota' on quota limit, else 'error'."""
        if not self.cfg.auto_upload or self.cfg.dry_run:
            return "held"
        clips = self.db.clips(video_id)
        if not clips:
            return "error"

        for clip in clips:
            if clip["kind"] == "long" and clip["upload_status"] == "pending":
                path = Path(clip["path"]) if clip.get("path") else None
                if path and path.exists():
                    try:
                        self._upload_clip(clip)
                    except Exception as exc:
                        log.warning("Upload error for %s: %s", video_id, exc)
                        return "error"
                else:
                    return "error"

            if clip["kind"] == "short" and clip["upload_status"] == "pending":
                path = Path(clip["path"]) if clip.get("path") else None
                if path and path.exists():
                    try:
                        self._upload_clip(clip)
                    except Exception as exc:
                        log.warning("Upload error for %s: %s", video_id, exc)
                        return "error"
                else:
                    return "error"

        return "done"

    def _upload_clip(self, clip: dict) -> None:
        raise UploadHeld(
            "YouTube upload is not implemented; refusing to mark the clip uploaded")

    def verify_video(self, video_id: str, wait: bool = False) -> str:
        clips = self.db.clips(video_id)
        if not clips:
            return "failed"

        ok = all(c["upload_status"] == "uploaded" for c in clips)
        if ok:
            for c in clips:
                self.db.update_clip(c["id"], verified=1, processing_status="succeeded",
                                    verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            self.db.update_video(video_id, status="verified")
            return "verified"
        return "incomplete"
