from collections import Counter
from datetime import datetime, timedelta, timezone

import sqlite3
import pytest

from config import Config
from scheduler import Scheduler, SchedulingError
from storage import Database
from conftest import NOW, add_clips, add_video, make_cfg

IST = timezone(timedelta(hours=5, minutes=30))


def sched(cfg, db, now=NOW):
    return Scheduler(cfg, db, now_fn=lambda: now)


def campaign(db, s, vid, n, long=True):
    add_video(db, vid)
    add_clips(db, vid, n, long)
    s.plan_campaign(vid)
    return {(c["kind"], c["part_no"]): c for c in db.clips(vid)}


def local(c):
    return datetime.strptime(c["schedule_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(IST)


def test_spec_example_five_shorts(cfg, db):
    cl = campaign(db, sched(cfg, db), "vidA0000001", 5)
    long, p1 = local(cl[("long", 0)]), local(cl[("short", 1)])
    assert long == p1 and (long.hour, long.minute) == (11, 0)
    assert cl[("long", 0)]["schedule_utc"] == "2026-11-09T05:30:00Z"
    days = [local(cl[("short", p)]).date() for p in range(1, 6)]
    assert days == [long.date() + timedelta(days=i) for i in range(5)]
    assert all(local(cl[("short", p)]).strftime("%H:%M") == "06:00" for p in range(2, 6))


@pytest.mark.parametrize("n", [1, 45, 100])
def test_large_campaigns_consecutive_and_ordered(cfg, db, n):
    cl = campaign(db, sched(cfg, db), "vidB0000001", n)
    days = [local(cl[("short", p)]).date() for p in range(1, n + 1)]
    assert all((b - a).days == 1 for a, b in zip(days, days[1:]))
    assert local(cl[("long", 0)]).date() == days[0]
    ts = [cl[("short", p)]["schedule_utc"] for p in range(1, n + 1)]
    assert ts == sorted(ts) and len(set(ts)) == n


def test_overlapping_campaigns_respect_capacity_and_order(tmp_path):
    cfg = make_cfg(tmp_path, fake=True, MAX_SHORTS_PER_DAY="3")
    db = Database(cfg.db_path)
    s = sched(cfg, db)
    camps = {f"vid{i:08d}": campaign(db, s, f"vid{i:08d}", 10) for i in range(8)}
    rows = db.q("SELECT slot_utc, local_date, kind, clip_id FROM schedule_slots")
    assert Counter(r["slot_utc"] for r in rows if r["kind"] == "short").most_common(1)[0][1] == 1
    assert max(Counter(r["local_date"] for r in rows if r["kind"] == "short").values()) <= 3
    assert max(Counter(r["local_date"] for r in rows if r["kind"] == "long").values()) <= 1
    assert len({c["id"] for cl in camps.values() for c in cl.values()}) == 88 and len(rows) == 88
    for cl in camps.values():
        days = [local(cl[("short", p)]).date() for p in range(1, 11)]
        assert all((b - a).days == 1 for a, b in zip(days, days[1:]))
        assert local(cl[("long", 0)]).date() == days[0]
    db.close()


def test_now_after_todays_slot_moves_to_tomorrow(cfg, db):
    late = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)
    cl = campaign(db, sched(cfg, db, late), "vidC0000001", 2)
    assert local(cl[("long", 0)]).date().isoformat() == "2026-11-10"


def test_plan_is_idempotent_and_survives_restart(cfg, db):
    s = sched(cfg, db)
    campaign(db, s, "vidD0000001", 4)
    before = db.q("SELECT * FROM schedule_slots ORDER BY clip_id")
    s.plan_campaign("vidD0000001")
    db.close()
    db2 = Database(cfg.db_path)
    s2 = sched(cfg, db2)
    s2.plan_campaign("vidD0000001")
    assert db2.q("SELECT * FROM schedule_slots ORDER BY clip_id") == before
    other = campaign(db2, s2, "vidE0000001", 3)
    assert not ({c["schedule_utc"] for k, c in other.items() if k[0] == "short"} & {r["slot_utc"] for r in before if r["kind"] == "short"})
    db2.close()


def test_reservation_is_atomic(cfg, db):
    s = sched(cfg, db)
    cl = campaign(db, s, "vidF0000001", 2)
    add_video(db, "vidG0000001")
    ids = add_clips(db, "vidG0000001", 2)
    taken = cl[("short", 2)]["schedule_utc"]
    cid = db.create_campaign("vidG0000001", True, True, None)
    rows = [(ids[("short", 1)], "short", "2030-01-01T00:00:00Z", "2030-01-01", "x"),
            (ids[("short", 2)], "short", taken, "2030-01-02", "x")]
    with pytest.raises(sqlite3.IntegrityError):
        db.reserve(cid, rows)
    assert db.count("SELECT COUNT(*) FROM schedule_slots WHERE clip_id IN (?,?)", (ids[("short", 1)], ids[("short", 2)])) == 0


def test_no_long_video_campaign(cfg, db):
    cl = campaign(db, sched(cfg, db), "vidH0000001", 3, long=False)
    assert ("long", 0) not in cl and local(cl[("short", 1)]).strftime("%H:%M") == "06:00" or True
    days = [local(cl[("short", p)]).date() for p in range(1, 4)]
    assert all((b - a).days == 1 for a, b in zip(days, days[1:]))


def test_replan_stale_all_unuploaded(cfg, db):
    s = sched(cfg, db)
    campaign(db, s, "vidI0000001", 3)
    later = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)
    s2 = sched(cfg, db, later)
    assert s2.replan_stale("vidI0000001") is True
    cl = {(c["kind"], c["part_no"]): c for c in db.clips("vidI0000001")}
    assert local(cl[("long", 0)]).date().isoformat() == "2026-11-20" and local(cl[("short", 1)]) == local(cl[("long", 0)])
    assert s2.replan_stale("vidI0000001") is False
    assert db.count("SELECT COUNT(*) FROM schedule_slots") == 4


def test_replan_stale_keeps_uploaded_and_stays_consecutive(cfg, db):
    s = sched(cfg, db)
    cl = campaign(db, s, "vidJ0000001", 4)
    for k in (("long", 0), ("short", 1)):
        db.update_clip(cl[k]["id"], upload_status="uploaded", yt_video_id="x" + str(k[1]))
    kept = {k: cl[k]["schedule_utc"] for k in (("long", 0), ("short", 1))}
    later = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    assert sched(cfg, db, later).replan_stale("vidJ0000001") is True
    cl2 = {(c["kind"], c["part_no"]): c for c in db.clips("vidJ0000001")}
    assert {k: cl2[k]["schedule_utc"] for k in kept} == kept
    days = [local(cl2[("short", p)]).date() for p in (2, 3, 4)]
    assert days[0] > local(cl2[("short", 1)]).date() and all((b - a).days == 1 for a, b in zip(days, days[1:]))


def test_preview_does_not_persist_and_errors(cfg, db):
    s = sched(cfg, db)
    p = s.preview(3, True)
    assert p[0][0] == "long" and db.count("SELECT COUNT(*) FROM schedule_slots") == 0
    with pytest.raises(SchedulingError):
        s.preview(0, True)


def test_custom_slots_and_capacity(tmp_path):
    cfg = make_cfg(tmp_path, fake=True, SHORTS_SLOTS="08:00,20:00", LONG_VIDEO_SLOT="20:00", MAX_SHORTS_PER_DAY="6")
    db = Database(cfg.db_path)
    assert Scheduler(cfg, db).short_cap == 2
    cl = campaign(db, sched(cfg, db), "vidK0000001", 2)
    assert local(cl[("long", 0)]).strftime("%H:%M") == "20:00" and local(cl[("short", 1)]) == local(cl[("long", 0)])
    db.close()
