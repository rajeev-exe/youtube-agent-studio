"""Publishing calendar and scheduling logic."""
from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable

from config import Config
from storage import Database, iso

log = logging.getLogger("yta.schedule")
MAX_SEARCH_DAYS = 3650


class SchedulingError(Exception):
    pass


class Scheduler:
    def __init__(self, cfg: Config, db: Database, now_fn: Callable[[], datetime] | None = None):
        self.cfg = cfg
        self.db = db
        self.tz = cfg.tz
        self.now = now_fn or (lambda: datetime.now(timezone.utc))

    @property
    def short_cap(self) -> int:
        return min(self.cfg.max_shorts_per_day, len(self.cfg.shorts_slots))

    def earliest(self) -> datetime:
        return self.now() + timedelta(minutes=self.cfg.schedule_min_lead_minutes)

    def _dt(self, day: date, t: dtime) -> datetime:
        if isinstance(t, str):
            t = datetime.strptime(t, "%H:%M").time()
        return datetime.combine(day, t, tzinfo=self.tz)

    @staticmethod
    def _utc(dt: datetime) -> str:
        return iso(dt)

    def _pick_short(self, occ: dict, day: date, earliest: datetime, prefer: dtime | None = None) -> datetime | None:
        if occ["short_by_day"].get(day.isoformat(), 0) >= self.short_cap:
            return None
        order = list(self.cfg.shorts_slots)
        if prefer is not None and prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)
        for t in order:
            dt = self._dt(day, t)
            if dt.astimezone(timezone.utc) > earliest and self._utc(dt) not in occ["short_slots"]:
                return dt
        return None

    def _try(self, occ: dict, day: date, parts: list[int], has_long: bool, earliest: datetime):
        rows: list[tuple[str, int, datetime]] = []

        if has_long:
            if occ["long_by_day"].get(day.isoformat(), 0) >= self.cfg.max_long_per_day:
                return None
            ldt = self._dt(day, self.cfg.long_video_slot)
            if ldt.astimezone(timezone.utc) <= earliest:
                return None
            rows.append(("long", 0, ldt))

        first = self._pick_short(
            occ, day, earliest, self.cfg.long_video_slot if has_long else None)
        if first is None:
            return None
        rows.append(("short", parts[0], first))

        for idx, part in enumerate(parts[1:], start=1):
            dt = self._pick_short(occ, day + timedelta(days=idx), earliest)
            if dt is None:
                return None
            rows.append(("short", part, dt))
        return rows

    def find_plan(self, occ: dict, parts: list[int], has_long: bool, earliest: datetime, min_day: date | None = None):
        if not parts:
            raise SchedulingError("no clips to schedule")

        day = earliest.astimezone(self.tz).date()
        if min_day and min_day > day:
            day = min_day

        for _ in range(MAX_SEARCH_DAYS):
            plan = self._try(occ, day, parts, has_long, earliest)
            if plan:
                return plan
            day += timedelta(days=1)
        raise SchedulingError("no feasible plan found")

    def preview(self, n_shorts: int, has_long: bool) -> list[tuple[str, int, str]]:
        occ = self.db.occupancy()
        plan = self.find_plan(
            occ, list(range(1, n_shorts + 1)), has_long, self.earliest())
        return [(kind, part, dt.isoformat()) for kind, part, dt in plan]

    def plan_campaign(self, video_id: str) -> dict:
        with self.db.tx():
            camp = self.db.campaign(video_id)
            if camp:
                return camp

            clips = self.db.clips(video_id)
            shorts = [c for c in clips if c["kind"] == "short"]
            long = [c for c in clips if c["kind"] == "long"]

            if not shorts:
                raise SchedulingError(
                    f"{video_id}: no short clips to schedule")

            occ = self.db.occupancy()
            plan = self.find_plan(
                occ,
                [c["part_no"] for c in shorts],
                bool(long),
                self.earliest(),
            )

            cid = self.db.create_campaign(video_id, bool(
                long), True, plan[0][2].date().isoformat())
            rows = [
                (next(c["id"] for c in clips if c["kind"] == kind and c["part_no"] ==
                 part), kind, self._utc(dt), dt.date().isoformat(), dt.isoformat())
                for kind, part, dt in plan
            ]
            self.db.reserve(cid, rows)
            return self.db.campaign(video_id)

    def replan_stale(self, video_id: str) -> bool:
        with self.db.tx():
            campaign = self.db.campaign(video_id)
            if not campaign:
                return False

            clips = self.db.clips(video_id)
            pending = [
                clip for clip in clips if clip["upload_status"] != "uploaded"]
            earliest = self.earliest()
            stale = any(
                not clip.get("schedule_utc")
                or datetime.strptime(clip["schedule_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= earliest
                for clip in pending
            )
            if not pending or not stale:
                return False

            self.db.release(clip["id"] for clip in pending)
            uploaded = [
                clip for clip in clips if clip["upload_status"] == "uploaded"]
            min_day = None
            if uploaded:
                last_date = max(
                    datetime.fromisoformat(clip["schedule_local"]).date()
                    for clip in uploaded if clip.get("schedule_local")
                )
                min_day = last_date + timedelta(days=1)

            shorts = sorted(
                (clip for clip in pending if clip["kind"] == "short"),
                key=lambda clip: clip["part_no"],
            )
            long = [clip for clip in pending if clip["kind"] == "long"]
            if not shorts:
                return False

            plan = self.find_plan(
                self.db.occupancy(),
                [clip["part_no"] for clip in shorts],
                bool(long),
                earliest,
                min_day,
            )
            clip_by_key = {(clip["kind"], clip["part_no"])
                            : clip for clip in pending}
            rows = [
                (
                    clip_by_key[(kind, part)]["id"],
                    kind,
                    self._utc(dt),
                    dt.date().isoformat(),
                    dt.isoformat(),
                )
                for kind, part, dt in plan
            ]
            self.db.reserve(campaign["id"], rows)
            return True
