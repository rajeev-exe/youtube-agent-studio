"""Export project data to CSV and import rights/priority edits safely."""
from __future__ import annotations

import csv
import os
import re
import tempfile
from pathlib import Path

from config import AUTHORIZED_RIGHTS, RIGHTS_VALUES, Config
from storage import Database

COLUMNS = [
    "video_id",
    "url",
    "channel",
    "title",
    "views",
    "published_at",
    "duration",
    "category",
    "rights_status",
    "status",
    "priority",
    "discovered_at",
]

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id_from_url(value: str) -> str | None:
    value = (value or "").strip()
    if VIDEO_ID_PATTERN.fullmatch(value):
        return value

    match = re.search(
        r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})",
        value,
    )
    return match.group(1) if match else None


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(file)
        ]


def write_rows(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _csv_row(video: dict) -> dict:
    return {
        "video_id": video["video_id"],
        "url": video.get("url") or "",
        "channel": video.get("channel") or "",
        "title": video.get("title") or "",
        "views": video.get("views") or 0,
        "published_at": video.get("published_at") or "",
        "duration": video.get("duration") or "",
        "category": video.get("category") or "",
        "rights_status": video.get("rights_status") or "pending",
        "status": video.get("status") or "discovered",
        "priority": video.get("priority") or 0,
        "discovered_at": video.get("discovered_at") or "",
    }


def export(db: Database, cfg: Config) -> None:
    """Write all known videos to trending.csv and authorized/manual videos to links.csv."""
    videos = db.videos()
    videos.sort(key=lambda video: (-(video.get("score") or 0), video["video_id"]))
    write_rows(cfg.trending_csv, COLUMNS, [_csv_row(video) for video in videos])

    links = [
        video for video in videos
        if video.get("rights_status") in AUTHORIZED_RIGHTS or video.get("manual")
    ]
    links.sort(key=lambda video: (-(video.get("priority") or 0), -(video.get("score") or 0)))
    write_rows(cfg.links_csv, COLUMNS, [_csv_row(video) for video in links])


def sync(db: Database, cfg: Config) -> dict:
    """Import rights and priority edits; new video rows may be added from links.csv."""
    summary = {"added": 0, "rights_updated": 0, "priority_updated": 0, "ignored": []}

    for path in (cfg.trending_csv, cfg.links_csv):
        if not path.exists():
            continue

        is_links = path == cfg.links_csv
        for row in read_rows(path):
            video_id = row.get("video_id") or video_id_from_url(row.get("url", "")) or ""
            if not video_id:
                if row.get("url"):
                    summary["ignored"].append(f"{path.name}: could not identify video")
                continue

            video = db.video(video_id)
            if video is None:
                # Only links.csv is the user-managed input for adding videos.
                if not is_links:
                    continue
                url = row.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                db.upsert_candidate({
                    "video_id": video_id,
                    "url": url,
                    "title": row.get("title") or "",
                    "channel": row.get("channel") or "",
                    "source": "manual",
                    "manual": 1,
                    "status": "discovered",
                    "priority": _priority(row.get("priority", "")),
                })
                db.update_video(
                    video_id,
                    manual=1,
                    priority=_priority(row.get("priority", "")),
                )
                summary["added"] += 1
                video = db.video(video_id)

            rights = row.get("rights_status", "").lower()
            if rights and rights != video["rights_status"]:
                if rights in RIGHTS_VALUES:
                    db.set_rights(video_id, rights, note=f"set via {path.name}")
                    summary["rights_updated"] += 1
                else:
                    summary["ignored"].append(
                        f"{path.name}: invalid rights_status {rights!r} for {video_id}"
                    )

            priority_text = row.get("priority", "")
            if priority_text.lstrip("-").isdigit():
                priority = int(priority_text)
                current = db.video(video_id)
                if current and priority != (current.get("priority") or 0):
                    db.update_video(video_id, priority=priority)
                    summary["priority_updated"] += 1

    return summary


def _priority(value: str) -> int:
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() else 0
