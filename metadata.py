"""Generate metadata JSON for each clip/campaign."""
from __future__ import annotations

import json
from pathlib import Path

from ai import AIClient
from config import Config
from storage import Database

class MetadataGenerator:
    def __init__(self, cfg: Config, db: Database, ai: AIClient | None = None):
        self.cfg = cfg
        self.db = db
        self.ai = ai

    def generate_for_clip(self, clip: dict) -> dict:
        payload = {
            "video_id": clip["video_id"],
            "kind": clip["kind"],
            "part_no": clip["part_no"],
            "title": self.db.video(clip["video_id"])["title"],
            "duration": clip["duration"],
            "schedule_utc": clip.get("schedule_utc"),
            "label": clip.get("label", ""),
            "transcript": clip.get("transcript") or "",
        }
        clip_path = Path(clip["path"]) if clip.get("path") else None
        if clip_path and clip_path.exists():
            payload["path"] = str(clip_path)
        self.db.update_clip(clip["id"], meta_json=json.dumps(payload), meta_status="done")
        return payload

    def generate_all(self, video: dict) -> list[dict]:
        out = []
        for clip in self.db.clips(video["video_id"]):
            out.append(self.generate_for_clip(clip))
        return out
