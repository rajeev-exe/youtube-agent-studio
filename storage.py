"""SQLite storage layer."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ACTIVE_STATUSES = ("queued", "downloaded", "split", "ready", "scheduled", "uploaded", "verified")
STATUS_ORDER = [
    "discovered", "needs_review", "rejected", "queued", "downloaded", "split", "ready",
    "scheduled", "uploaded", "verified", "cleaned", "complete"
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
  video_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  channel TEXT,
  channel_id TEXT,
  title TEXT,
  description TEXT,
  views INTEGER DEFAULT 0,
  likes INTEGER,
  published_at TEXT,
  duration TEXT,
  duration_sec INTEGER,
  category TEXT,
  license TEXT,
  default_language TEXT,
  source TEXT,
  score REAL DEFAULT 0,
  score_detail TEXT,
  ai_relevance REAL,
  ai_family_suitable TEXT,
  ai_content_type TEXT,
  ai_directed_to_children TEXT,
  ai_status TEXT,
  rights_status TEXT NOT NULL DEFAULT 'pending',
  rights_note TEXT,
  made_for_kids INTEGER,
  status TEXT NOT NULL DEFAULT 'discovered',
  priority INTEGER DEFAULT 0,
  discovered_at TEXT,
  updated_at TEXT,
  manual INTEGER DEFAULT 0,
  local_source INTEGER DEFAULT 0,
  work_dir TEXT,
  source_path TEXT,
  file_size INTEGER,
  file_duration REAL,
  halted INTEGER DEFAULT 0,
  retries INTEGER DEFAULT 0,
  last_error TEXT,
  files_deleted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT NOT NULL REFERENCES videos(video_id),
  kind TEXT NOT NULL CHECK(kind IN ('long', 'short')),
  part_no INTEGER NOT NULL,
  path TEXT,
  start_sec REAL,
  end_sec REAL,
  duration REAL,
  transcript TEXT,
  label TEXT,
  cut_status TEXT DEFAULT 'pending',
  meta_json TEXT,
  meta_status TEXT DEFAULT 'pending',
  meta_source TEXT,
  campaign_id INTEGER,
  schedule_utc TEXT,
  schedule_local TEXT,
  upload_status TEXT DEFAULT 'pending',
  upload_session TEXT,
  upload_started_at TEXT,
  yt_video_id TEXT,
  yt_url TEXT,
  privacy TEXT,
  expected_privacy TEXT,
  processing_status TEXT,
  verified INTEGER DEFAULT 0,
  verified_at TEXT,
  retries INTEGER DEFAULT 0,
  last_error TEXT,
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(video_id, kind, part_no)
);

CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT UNIQUE NOT NULL,
  status TEXT DEFAULT 'planned',
  has_long INTEGER DEFAULT 0,
  scheduled INTEGER DEFAULT 1,
  start_date TEXT,
  created_at TEXT,
  verified_at TEXT,
  cleanup_status TEXT DEFAULT 'pending',
  files_deleted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_slots(
  slot_utc TEXT NOT NULL,
  local_date TEXT NOT NULL,
  kind TEXT NOT NULL,
  clip_id INTEGER NOT NULL UNIQUE,
  campaign_id INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_short_slot ON schedule_slots(slot_utc) WHERE kind='short';
CREATE TABLE IF NOT EXISTS ai_cache(
  key TEXT PRIMARY KEY,
  response TEXT,
  provider TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  stage TEXT,
  video_id TEXT,
  clip_id INTEGER,
  message TEXT
);
CREATE TABLE IF NOT EXISTS state(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS quota(
  day TEXT PRIMARY KEY,
  units INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_clips_video ON clips(video_id);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(utcnow())


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._depth = 0

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self):
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return

        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    def q(self, sql: str, params: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params))]

    def one(self, sql: str, params: Iterable = ()) -> dict | None:
        r = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    def count(self, sql: str, params: Iterable = ()) -> int:
        r = self.conn.execute(sql, tuple(params)).fetchone()
        return int(r[0] or 0) if r else 0

    def video(self, video_id: str) -> dict | None:
        return self.one("SELECT * FROM videos WHERE video_id=?", (video_id,))

    def videos(self, where: str = "1=1", params: Iterable = ()) -> list[dict]:
        return self.q(f"SELECT * FROM videos WHERE {where}", params)

    def update_video(self, video_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE videos SET {assignments}, updated_at=? WHERE video_id=?",
            (*fields.values(), now_iso(), video_id),
        )

    def upsert_candidate(self, c: dict) -> bool:
        cur = self.video(c["video_id"])
        if cur is None:
            row = {k: v for k, v in c.items() if k in {"video_id", "url", "channel", "channel_id", "title", "description",
                                                     "views", "likes", "published_at", "duration", "duration_sec", "category",
                                                     "license", "default_language", "source", "score", "score_detail",
                                                     "ai_relevance", "ai_family_suitable", "ai_content_type",
                                                     "ai_directed_to_children", "ai_status", "rights_status", "rights_note",
                                                     "made_for_kids", "status", "priority", "discovered_at", "updated_at",
                                                     "manual", "local_source", "work_dir", "source_path", "file_size",
                                                     "file_duration", "halted", "retries", "last_error", "files_deleted"}}
            row.setdefault("status", "discovered")
            row.setdefault("rights_status", "pending")
            row["updated_at"] = now_iso()
            if "discovered_at" not in row:
                row["discovered_at"] = now_iso()
            cols = ",".join(row)
            self.conn.execute(f"INSERT INTO videos({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
            return True

        upd = {
            k: c[k]
            for k in ("title", "channel", "channel_id", "description", "views", "likes", "published_at",
                      "duration", "duration_sec", "category", "license", "default_language", "score",
                      "score_detail", "source", "ai_relevance", "ai_family_suitable", "ai_content_type",
                      "ai_directed_to_children", "ai_status")
            if c.get(k) is not None
        }
        if c.get("status") and cur["status"] in ("discovered", "needs_review"):
            upd["status"] = c["status"]
        if upd:
            self.update_video(c["video_id"], **upd)
        return False

    def set_rights(self, video_id: str, rights: str, note: str | None = None) -> None:
        if rights not in ("pending", "owned", "licensed", "permission_confirmed", "not_authorized"):
            raise ValueError(f"invalid rights_status {rights!r}")

        with self.tx():
            v = self.video(video_id)
            if not v:
                raise KeyError(video_id)
            fields = {"rights_status": rights}
            if note is not None:
                fields["rights_note"] = note
            if rights in ("owned", "licensed", "permission_confirmed") and v["status"] in ("discovered", "needs_review", "rejected"):
                fields["status"] = "queued"
            elif rights not in ("owned", "licensed", "permission_confirmed") and v["status"] == "queued":
                fields["status"] = "discovered"
            self.update_video(video_id, **fields)

    def advance(self, video_id: str, status: str, **extra: Any) -> None:
        self.update_video(video_id, status=status, retries=0, last_error=None, **extra)

    def fail_video(self, video_id: str, stage: str, err: Any, max_retries: int, permanent: bool = False) -> bool:
        err = str(err)[:2000]
        with self.tx():
            self.log_error(stage, err, video_id)
            n = (self.video(video_id) or {}).get("retries", 0) + 1
            halted = 1 if (permanent or n >= max_retries) else 0
            self.update_video(video_id, retries=n, last_error=f"[{stage}] {err}", halted=halted)
        return bool(halted)

    def reset_video(self, video_id: str) -> None:
        self.update_video(video_id, halted=0, retries=0, last_error=None)

    def work_queue(self) -> list[dict]:
        rows = self.q(
            "SELECT * FROM videos WHERE halted=0 AND rights_status IN ('owned','licensed','permission_confirmed') "
            "AND status IN ('queued','downloaded','split','ready','scheduled','uploaded','verified')",
        )
        rows.sort(key=lambda r: (-STATUS_ORDER.index(r["status"]), -(r["priority"] or 0), -(r["score"] or 0)))
        return rows

    def clips(self, video_id: str) -> list[dict]:
        return self.q(
            "SELECT * FROM clips WHERE video_id=? ORDER BY CASE kind WHEN 'long' THEN 0 ELSE 1 END, part_no",
            (video_id,),
        )

    def clip(self, clip_id: int) -> dict | None:
        return self.one("SELECT * FROM clips WHERE id=?", (clip_id,))

    def add_clip(self, video_id: str, kind: str, part_no: int, **fields: Any) -> int:
        row = self.one("SELECT id FROM clips WHERE video_id=? AND kind=? AND part_no=?", (video_id, kind, part_no))
        if row:
            return row["id"]

        cols = {"video_id": video_id, "kind": kind, "part_no": part_no, "created_at": now_iso(), "updated_at": now_iso(), **fields}
        cur = self.conn.execute(f"INSERT INTO clips({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values()))
        return int(cur.lastrowid)

    def update_clip(self, clip_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE clips SET {assignments}, updated_at=? WHERE id=?",
            (*fields.values(), now_iso(), clip_id),
        )

    def campaign(self, video_id: str) -> dict | None:
        return self.one("SELECT * FROM campaigns WHERE video_id=?", (video_id,))

    def create_campaign(self, video_id: str, has_long: bool, scheduled: bool, start_date: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO campaigns(video_id, has_long, scheduled, start_date, created_at) VALUES(?,?,?,?,?)",
            (video_id, int(has_long), int(scheduled), start_date, now_iso()),
        )
        return int(cur.lastrowid)

    def update_campaign(self, campaign_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE campaigns SET {assignments}, updated_at=? WHERE id=?",
            (*fields.values(), now_iso(), campaign_id),
        )

    def occupancy(self) -> dict:
        rows = self.q("SELECT slot_utc, local_date, kind, clip_id FROM schedule_slots")
        return {
            "short_slots": {r["slot_utc"] for r in rows if r["kind"] == "short"},
            "short_by_day": {k: v for k, v in __import__("collections").Counter(r["local_date"] for r in rows if r["kind"] == "short").items()},
            "long_by_day": {k: v for k, v in __import__("collections").Counter(r["local_date"] for r in rows if r["kind"] == "long").items()},
        }

    def reserve(self, campaign_id: int, rows: list[tuple]) -> None:
        with self.tx():
            for clip_id, kind, utc, ldate, liso in rows:
                self.conn.execute("DELETE FROM schedule_slots WHERE clip_id=?", (clip_id,))
                self.conn.execute(
                    "INSERT INTO schedule_slots(slot_utc, local_date, kind, clip_id, campaign_id) VALUES(?,?,?,?,?)",
                    (utc, ldate, kind, clip_id, campaign_id),
                )
                self.update_clip(clip_id, schedule_utc=utc, schedule_local=liso, campaign_id=campaign_id)

    def release(self, clip_ids: Iterable[int]) -> None:
        with self.tx():
            for cid in clip_ids:
                self.conn.execute("DELETE FROM schedule_slots WHERE clip_id=?", (cid,))
                self.update_clip(cid, schedule_utc=None, schedule_local=None)

    def cache_get(self, key: str) -> tuple[str, str] | None:
        r = self.one("SELECT response, provider FROM ai_cache WHERE key=?", (key,))
        return (r["response"], r["provider"]) if r else None

    def cache_put(self, key: str, response: str, provider: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO ai_cache(key, response, provider, created_at) VALUES(?,?,?,?)",
            (key, response, provider, now_iso()),
        )

    def log_error(self, stage: str, msg: str, video_id: str | None = None, clip_id: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO errors(ts, stage, video_id, clip_id, message) VALUES(?,?,?,?,?)",
            (now_iso(), stage, video_id, clip_id, str(msg)[:2000]),
        )

    def get_state(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM state WHERE key=?", (key,))
        return r["value"] if r else default

    def set_state(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM state WHERE key=?", (key,))
        else:
            self.conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?,?)", (key, value))

    def add_quota(self, day: str, units: int) -> None:
        self.conn.execute(
            "INSERT INTO quota(day, units) VALUES(?, ?) ON CONFLICT(day) DO UPDATE SET units = units + ?",
            (day, units, units),
        )

    def quota_used(self, day: str) -> int:
        return self.count("SELECT units FROM quota WHERE day=?", (day,))
