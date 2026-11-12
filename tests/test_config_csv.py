from pathlib import Path

from config import Config
from csvio import export, sync
from storage import Database


def test_config_defaults(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "TIMEZONE=Asia/Kolkata",
            "SHORTS_SLOTS=06:00,11:00",
            "LONG_VIDEO_SLOT=11:00",
            "MAX_SHORTS_PER_DAY=5",
            "MAX_LONG_VIDEOS_PER_DAY=1",
            "AI_PROVIDER=ollama",
            "AUTO_DOWNLOAD=false",
            "AUTO_SPLIT=false",
            "AUTO_SCHEDULE=false",
            "AUTO_UPLOAD=false",
            "DRY_RUN=false",
        ]) + "\n",
        encoding="utf-8",
    )

    cfg = Config.load(env)
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.shorts_slots == ["06:00", "11:00"]
    assert cfg.short_min == 30
    assert cfg.max_shorts_per_day == 5


def test_config_load_does_not_leak_between_files(tmp_path):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("MAX_SHORTS_PER_DAY=6\n", encoding="utf-8")
    second.write_text("MAX_SHORTS_PER_DAY=3\n", encoding="utf-8")

    assert Config.load(first).max_shorts_per_day == 6
    assert Config.load(second).max_shorts_per_day == 3


def test_csv_round_trip(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "TIMEZONE=Asia/Kolkata",
            "SHORTS_SLOTS=06:00,11:00",
            "LONG_VIDEO_SLOT=11:00",
            "AI_PROVIDER=ollama",
            "AUTO_DOWNLOAD=false",
            "AUTO_SPLIT=false",
            "AUTO_SCHEDULE=false",
            "AUTO_UPLOAD=false",
            "DRY_RUN=false",
        ]) + "\n",
        encoding="utf-8",
    )
    cfg = Config.load(env)
    db = Database(cfg.db_path)

    db.upsert_candidate({
        "video_id": "abc123def45",
        "url": "https://www.youtube.com/watch?v=abc123def45",
        "title": "Demo",
        "channel": "Publisher",
        "status": "discovered",
        "rights_status": "pending",
        "manual": 1,
        "score": 42.0,
        "source": "manual",
    })

    export(db, cfg)
    assert cfg.trending_csv.exists()
    assert cfg.links_csv.exists()

    # Simulate user edit
    lines = cfg.links_csv.read_text(encoding="utf-8").splitlines()
    updated = "\n".join(
        lines[:1] + [lines[1].replace("pending", "owned")]) + "\n"
    cfg.links_csv.write_text(updated, encoding="utf-8")

    summary = sync(db, cfg)
    assert summary["rights_updated"] >= 1
    assert db.video("abc123def45")["rights_status"] == "owned"

    db.close()
