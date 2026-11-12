from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import Config
from storage import Database

NOW = datetime(2026, 11, 9, 0, 30, tzinfo=timezone.utc)
CONFIG_ENV_KEYS = (
    "TIMEZONE", "SHORTS_SLOTS", "LONG_VIDEO_SLOT", "MAX_SHORTS_PER_DAY",
    "MAX_LONG_VIDEOS_PER_DAY", "SCHEDULE_MIN_LEAD_MINUTES", "YOUTUBE_API_KEY",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL", "LOOP_INTERVAL_MINUTES",
    "AI_PROVIDER", "REGION_CODE", "SEARCH_KEYWORDS", "MINIMUM_VIEWS",
    "DISCOVERY_LIMIT", "SHORT_MIN_DURATION", "SHORT_MAX_DURATION", "SHORT_WIDTH",
    "SHORT_HEIGHT", "SHORT_FIT", "UPLOAD_LONG_VIDEO", "AUTO_DISCOVER",
    "AUTO_DOWNLOAD", "AUTO_SPLIT", "AUTO_METADATA", "AUTO_UPLOAD", "AUTO_SCHEDULE",
    "DRY_RUN", "DELETE_AFTER_VERIFICATION", "MAX_RETRIES", "YOUTUBE_DAILY_QUOTA",
)


@pytest.fixture(autouse=True)
def isolate_config_environment(monkeypatch):
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def make_cfg(tmp_path: Path, fake, **overrides):
    defaults = {
        "TIMEZONE": "Asia/Kolkata",
        "SHORTS_SLOTS": "06:00,11:00,15:00,17:00,21:00,00:00",
        "LONG_VIDEO_SLOT": "11:00",
        "MAX_SHORTS_PER_DAY": "6",
        "MAX_LONG_VIDEOS_PER_DAY": "1",
        "AI_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://fake",
        "OLLAMA_MODEL": "fake-model",
        "AUTO_DISCOVER": "false",
        "AUTO_DOWNLOAD": "false",
        "AUTO_SPLIT": "false",
        "AUTO_METADATA": "false",
        "AUTO_UPLOAD": "false",
        "AUTO_SCHEDULE": "false",
        "DRY_RUN": "false",
    }
    defaults.update(overrides)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(f"{k}={v}" for k, v in defaults.items()) + "\n", encoding="utf-8")
    return Config.load(env)


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path, fake=False)


@pytest.fixture
def db(cfg):
    db = Database(cfg.db_path)
    yield db
    db.close()


def add_video(db, video_id):
    db.upsert_candidate({
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "title": f"Test {video_id}",
        "channel": "x",
        "status": "queued",
        "rights_status": "owned",
        "priority": 1,
        "source": "manual",
    })
    return db.video(video_id)


def add_clips(db, video_id, n, long=True):
    by_kind = {}
    if long:
        db.add_clip(video_id, "long", 0, path=f"/tmp/{video_id}-long.mp4",
                    start_sec=0, end_sec=60, duration=60, cut_status="done")
        by_kind[("long", 0)] = db.one(
            "SELECT id FROM clips WHERE video_id=? AND kind='long' AND part_no=0", (video_id,))["id"]
    for part in range(1, n + 1):
        db.add_clip(video_id, "short", part,
                    path=f"/tmp/{video_id}-short-{part}.mp4", start_sec=0, end_sec=30, duration=30, cut_status="done")
        by_kind[("short", part)] = db.one(
            "SELECT id FROM clips WHERE video_id=? AND kind='short' AND part_no=?", (video_id, part))["id"]
    return by_kind
