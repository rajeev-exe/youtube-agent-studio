"""Downloader with authorization gate, retries, and ffprobe validation."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

from config import AUTHORIZED_RIGHTS, Config
from storage import Database

PERMANENT_MARKERS = (
    "private video", "sign in to confirm", "members-only", "drm", "video unavailable",
    "has been removed", "copyright", "age-restricted", "not available in your country"
)


class DownloadError(Exception):
    pass


class PermanentDownloadError(DownloadError):
    pass


class RightsError(Exception):
    pass


class DependencyMissing(Exception):
    pass


def slugify(s: str, n: int = 50) -> str:
    s = unicodedata.normalize("NFKD", (s or "video")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return (s[:n].strip("_") or "video")


def work_dir_for(cfg: Config, v: dict) -> Path:
    return cfg.output_dir / f"{slugify(v.get('title') or '')}_{slugify(v['video_id'], 20)}"


def probe(path: Path | str) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise DependencyMissing("ffprobe not found") from exc
    if r.returncode != 0:
        raise DownloadError(f"ffprobe failed: {r.stderr.strip()[-300:]}")
    j = json.loads(r.stdout or "{}")
    streams = j.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not vstream:
        raise DownloadError("no video stream found")
    duration = float(j.get("format", {}).get("duration") or vstream.get("duration") or 0)
    size = int(j.get("format", {}).get("size") or os.path.getsize(path))
    return {
        "duration": duration,
        "width": int(vstream.get("width", 0)),
        "height": int(vstream.get("height", 0)),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "size": size,
    }


def ytdlp_command() -> list[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise DependencyMissing("yt-dlp not installed")


def _run(cmd: list[str], timeout: int = 7200) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class Downloader:
    def __init__(self, cfg: Config, db: Database, runner: Callable[[list[str]], tuple[int, str]] = _run):
        self.cfg = cfg
        self.db = db
        self.runner = runner
        self._sem = threading.BoundedSemaphore(cfg.max_retries if cfg.max_retries else 1)

    def require_authorized(self, v: dict) -> None:
        cur = self.db.video(v["video_id"]) or v
        if cur.get("rights_status") not in AUTHORIZED_RIGHTS:
            raise RightsError(f"{v['video_id']}: rights_status={cur.get('rights_status')!r}")

    def download(self, v: dict) -> Path:
        self.require_authorized(v)
        wd = work_dir_for(self.cfg, v)
        wd.mkdir(parents=True, exist_ok=True)

        if v.get("local_source"):
            src = Path(v["source_path"] or v["url"])
            if not src.is_file():
                raise DownloadError(f"local source missing: {src}")
            dst = wd / f"original{src.suffix.lower() or '.mp4'}"
            if not dst.exists():
                shutil.copy2(src, dst)
            info = probe(dst)
            self.db.update_video(v["video_id"], work_dir=str(wd), file_size=info["size"], file_duration=info["duration"])
            return dst

        existing = next((p for p in sorted(wd.glob("original.*")) if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
        if existing:
            return existing

        cmd = [
            *ytdlp_command(),
            "--no-playlist",
            "--no-warnings",
            "--continue",
            "--retries", "5",
            "--fragment-retries", "5",
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "--write-info-json",
            "-o", str(wd / "original.%(ext)s"),
            v["url"],
        ]

        for attempt in range(1, self.cfg.max_retries + 1):
            code, out = self.runner(cmd)
            if code == 0:
                match = next((p for p in sorted(wd.glob("original.*")) if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
                if match:
                    info = probe(match)
                    self.db.update_video(v["video_id"], work_dir=str(wd), file_size=info["size"], file_duration=info["duration"])
                    return match
            low = (out or "").lower()
            if any(marker in low for marker in PERMANENT_MARKERS):
                raise PermanentDownloadError(out.strip()[:300])
            if attempt < self.cfg.max_retries:
                time.sleep(min(2 ** attempt, 30))
        raise DownloadError(f"download failed: {out.strip()[:300]}")
