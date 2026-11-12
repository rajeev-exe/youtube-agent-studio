"""Status/health reporting for the pipeline."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from config import AUTHORIZED_RIGHTS, Config
from storage import Database

AUTH_SQL = ",".join(f"'{r}'" for r in AUTHORIZED_RIGHTS)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def collect(cfg: Config, db: Database) -> dict:
    c = db.count
    free = shutil.disk_usage(cfg.base_dir).free
    return {
        "current_stage": db.get_state("current_stage", "idle"),
        "videos_discovered": c("SELECT COUNT(*) FROM videos"),
        "authorized_videos": c(f"SELECT COUNT(*) FROM videos WHERE rights_status IN ({AUTH_SQL})"),
        "awaiting_permission": c("SELECT COUNT(*) FROM videos WHERE rights_status='pending'"),
        "downloads_completed": c("SELECT COUNT(*) FROM videos WHERE file_size IS NOT NULL"),
        "shorts_generated": c("SELECT COUNT(*) FROM clips WHERE kind='short' AND cut_status='done'"),
        "shorts_uploaded": c("SELECT COUNT(*) FROM clips WHERE kind='short' AND upload_status='uploaded'"),
        "clips_verified": c("SELECT COUNT(*) FROM clips WHERE verified=1"),
        "videos_scheduled": c("SELECT COUNT(*) FROM clips WHERE schedule_utc IS NOT NULL"),
        "failed_uploads": c("SELECT COUNT(*) FROM clips WHERE upload_status!='uploaded' AND retries>0"),
        "remaining_queue": c("SELECT COUNT(*) FROM videos WHERE halted=0 AND rights_status IN ('owned','licensed','permission_confirmed')"),
        "storage_output_mb": round(dir_size(cfg.output_dir) / 1e6, 1),
        "disk_free_gb": round(free / 1e9, 1),
        "quota_used_today": db.quota_used("today") if db else 0,
        "quota_budget": cfg.youtube_daily_quota,
    }


def render(stats: dict) -> str:
    labels = [
        ("current_stage", "Current stage"),
        ("videos_discovered", "Videos discovered"),
        ("authorized_videos", "Authorized videos"),
        ("awaiting_permission", "Awaiting permission"),
        ("downloads_completed", "Downloads completed"),
        ("shorts_generated", "Shorts generated"),
        ("shorts_uploaded", "Shorts uploaded"),
        ("clips_verified", "Clips verified"),
        ("videos_scheduled", "Videos scheduled"),
        ("failed_uploads", "Failed uploads"),
        ("remaining_queue", "Remaining queue"),
        ("storage_output_mb", "Storage output (MB)"),
        ("disk_free_gb", "Disk free (GB)"),
    ]
    width = max(len(name) for _, name in labels)
    lines = ["=" * (width + 18)]
    for key, name in labels:
        lines.append(f"{name:<{width}} : {stats.get(key, 0)}")
    lines.append("=" * (width + 18))
    return "\n".join(lines)


def to_json(stats: dict) -> str:
    return json.dumps(stats, indent=2)
