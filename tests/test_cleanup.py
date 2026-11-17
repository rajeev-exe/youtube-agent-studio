from pathlib import Path

from cleanup import cleanup_video
from storage import Database
from tests.conftest import add_video, make_cfg


def test_cleanup_only_removes_registered_generated_short_clips(tmp_path):
    cfg = make_cfg(tmp_path, fake=True, DELETE_AFTER_VERIFICATION="true")
    db = Database(cfg.db_path)
    video_id = "vidCLEAN001"
    add_video(db, video_id)
    work_dir = tmp_path / "output" / "work"
    work_dir.mkdir(parents=True)
    source = tmp_path / "my_source.mp4"
    generated_short = work_dir / "part_001.mp4"
    registered_original = work_dir / "original.mp4"
    unrelated_user_file = work_dir / "notes.txt"
    for path in (source, generated_short, registered_original, unrelated_user_file):
        path.write_text("preserve")

    db.update_video(video_id, status="verified", work_dir=str(work_dir), source_path=str(source))
    db.add_clip(video_id, "long", 0, path=str(registered_original), cut_status="done")
    db.add_clip(video_id, "short", 1, path=str(generated_short), cut_status="done")

    assert cleanup_video(cfg, db, video_id) == 1
    assert source.exists()
    assert registered_original.exists()
    assert unrelated_user_file.exists()
    assert not generated_short.exists()
    db.close()
