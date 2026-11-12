"""Main pipeline orchestration for the project."""
from __future__ import annotations

import logging
import time
from typing import Callable

from ai import AIClient
from cleanup import cleanup_video
from config import Config
from downloader import DownloadError, Downloader, PermanentDownloadError, RightsError
from metadata import MetadataGenerator
from scheduler import Scheduler
from sources import Discovery
from splitter import SplitError, Splitter
from storage import Database
from uploader import Uploader
from youtube_api import ApiError, AuthError, QuotaExceeded, YouTubeAPI

log = logging.getLogger("yta.pipeline")


class Pipeline:
    def __init__(self, cfg: Config, db: Database, ai: AIClient, api: YouTubeAPI):
        self.cfg = cfg
        self.db = db
        self.ai = ai
        self.api = api
        self.scheduler = Scheduler(cfg, db)
        self.downloader = Downloader(cfg, db)
        self.splitter = Splitter(cfg, db)
        self.metagen = MetadataGenerator(cfg, db, ai)
        self.uploader = Uploader(cfg, db, api)
        self.discovery = Discovery(cfg, db, api, ai)
        self.stop = False

    @classmethod
    def build(cls, cfg: Config, db: Database) -> "Pipeline":
        return cls(cfg, db, AIClient(cfg, db), YouTubeAPI(cfg, db))

    def stage(self, name: str) -> None:
        self.db.set_state("current_stage", name)
        log.info("== %s", name)

    def process(self, video_id: str) -> None:
        video = self.db.video(video_id)
        if not video or video.get("halted"):
            return

        status = video.get("status")
        if status == "queued":
            if not self.cfg.auto_download:
                return
            try:
                self.downloader.download(video)
                self.db.advance(video_id, "downloaded")
            except PermanentDownloadError as exc:
                self.db.fail_video(video_id, "download", str(
                    exc), self.cfg.max_retries, permanent=True)
                return
            except (DownloadError, RightsError) as exc:
                self.db.fail_video(video_id, "download",
                                   str(exc), self.cfg.max_retries)
                return
        elif status == "downloaded":
            if not self.cfg.auto_split:
                return
            try:
                self.splitter.split(video)
                self.db.advance(video_id, "split")
            except SplitError as exc:
                self.db.fail_video(video_id, "split", str(
                    exc), self.cfg.max_retries)
                return
        elif status == "split":
            if self.cfg.auto_metadata:
                try:
                    self.metagen.generate_all(video)
                    self.db.advance(video_id, "ready")
                except Exception as exc:
                    self.db.fail_video(video_id, "metadata",
                                       str(exc), self.cfg.max_retries)
        elif status == "ready":
            if not self.cfg.auto_schedule:
                return
            try:
                self.scheduler.plan_campaign(video_id)
                self.db.advance(video_id, "scheduled")
            except Exception as exc:
                self.db.fail_video(video_id, "schedule",
                                   str(exc), self.cfg.max_retries)
                return
        elif status == "scheduled":
            if not self.cfg.auto_upload or self.cfg.dry_run:
                return
            try:
                result = self.uploader.upload_campaign(video_id)
                if result != "done":
                    self.db.fail_video(
                        video_id, "upload", f"upload did not complete: {result}", self.cfg.max_retries)
                    return
                self.db.advance(video_id, "uploaded")
            except Exception as exc:
                self.db.fail_video(video_id, "upload",
                                   str(exc), self.cfg.max_retries)
                return
        elif status == "uploaded":
            result = self.uploader.verify_video(video_id)
            if result == "verified":
                self.db.advance(video_id, "verified")
                if self.cfg.delete_after_verification:
                    cleanup_video(self.cfg, self.db, video_id)
            elif result == "failed":
                self.db.fail_video(video_id, "verify",
                                   "verification failed", self.cfg.max_retries)

    def run_once(self) -> dict:
        errors_before = self.db.count("SELECT COUNT(*) FROM errors")
        discovery_failed = False
        if self.cfg.auto_discover:
            self.stage("discover")
            try:
                self.discovery.run()
            except (ApiError, AuthError, QuotaExceeded) as exc:
                log.warning(
                    "Discovery skipped due to API availability: %s", exc)
                discovery_failed = True

        self.stage("process queue")
        for video in self.db.work_queue():
            if self.stop:
                break
            self.process(video["video_id"])

        self.stage("idle")
        failures = self.db.count("SELECT COUNT(*) FROM errors") - errors_before
        return {"status": "error" if failures or discovery_failed else "ok", "failures": failures}
