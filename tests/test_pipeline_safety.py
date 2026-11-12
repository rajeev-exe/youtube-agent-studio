from types import SimpleNamespace
from pathlib import Path

from pipeline import Pipeline
from splitter import Splitter
from storage import Database
from tests.conftest import add_video, make_cfg
from uploader import Uploader


def test_disabled_pipeline_stages_leave_queued_video_untouched(tmp_path):
    cfg = make_cfg(tmp_path, fake=True)
    db = Database(cfg.db_path)
    add_video(db, "vidP0000001")
    pipeline = Pipeline(cfg, db, ai=object(), api=object())

    pipeline.process("vidP0000001")

    assert db.video("vidP0000001")["status"] == "queued"
    db.close()


def test_unimplemented_upload_never_records_fake_success(tmp_path):
    cfg = make_cfg(tmp_path, fake=True, AUTO_UPLOAD="true", DRY_RUN="false")
    db = Database(cfg.db_path)
    video_id = "vidP0000002"
    add_video(db, video_id)
    db.update_video(video_id, status="scheduled")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"not a real video")
    db.add_clip(video_id, "short", 1, path=str(
        clip_path), duration=30, cut_status="done")

    result = Uploader(cfg, db, api=object()).upload_campaign(video_id)

    clip = db.clips(video_id)[0]
    assert result == "error"
    assert clip["upload_status"] == "pending"
    assert clip["yt_video_id"] is None
    assert db.video(video_id)["status"] == "scheduled"
    db.close()


def test_splitter_registers_original_as_long_clip(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, fake=True, UPLOAD_LONG_VIDEO="true")
    cfg.short_min, cfg.short_max = 1, 15
    db = Database(cfg.db_path)
    video_id = "vidP0000003"
    video = add_video(db, video_id)
    work_dir = tmp_path / "video"
    work_dir.mkdir()
    original = work_dir / "original.mp4"
    original.write_bytes(b"test input")
    db.update_video(video_id, work_dir=str(work_dir))
    video["work_dir"] = str(work_dir)

    def fake_probe(path):
        if Path(path) == original:
            duration = 12.0
        else:
            duration = 10.0 if "part_001" in Path(path).name else 2.0
        return {
            "duration": duration,
            "width": cfg.short_width,
            "height": cfg.short_height,
            "has_audio": True,
        }

    def fake_ffmpeg(command, capture_output, text):
        Path(command[-1]).write_bytes(b"test output")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("splitter.probe", fake_probe)
    monkeypatch.setattr("splitter.subprocess.run", fake_ffmpeg)

    clips = Splitter(cfg, db).split(video)

    assert len(clips) == 3
    assert any(clip["kind"] == "long" and clip["path"] == str(original) for clip in clips)
    assert sum(clip["kind"] == "short" for clip in clips) == 2
    assert all(clip["cut_status"] == "done" for clip in clips)
    db.close()
