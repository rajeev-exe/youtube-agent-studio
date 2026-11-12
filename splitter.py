"""Simple video splitting into Shorts using FFmpeg and optional transcript hints."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from config import Config
from downloader import probe
from storage import Database

log = logging.getLogger("yta.split")


class SplitError(Exception):
    pass


def boundary_points(duration: float) -> list[float]:
    # This minimal implementation uses evenly spaced cut points.
    # Real transcript/silence-aware splitting is left for expansion.
    return [x for x in [i for i in range(10, int(duration), 30)] if x < duration]


def plan_segments(duration: float, min_dur: float, max_dur: float) -> list[tuple[float, float]]:
    points = [0.0] + boundary_points(duration) + [float(duration)]
    segments = []
    for start, end in zip(points, points[1:]):
        diff = end - start
        if diff <= 0:
            continue
        if diff < min_dur:
            if segments:
                segments[-1] = (segments[-1][0], end)
            else:
                segments.append((start, end))
            continue
        if diff > max_dur:
            chunk_count = max(1, int(diff / max_dur))
            step = diff / chunk_count
            current = start
            for _ in range(chunk_count - 1):
                current += step
                segments.append((start, current))
                start = current
        segments.append((start, end))
    return [(round(a, 3), round(b, 3)) for a, b in segments]


@lru_cache(maxsize=1)
def ffmpeg_has_subtitles() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=30)
        return "subtitles" in r.stdout.lower()
    except Exception:
        return False


def validate_clip(path: Path, expected: float, cfg: Config) -> None:
    info = probe(path)
    if abs(info["duration"] - expected) > 1.5:
        raise SplitError(
            f"{path.name}: duration mismatch ({info['duration']} vs {expected})")
    if (info["width"], info["height"]) != (cfg.short_width, cfg.short_height):
        raise SplitError(
            f"{path.name}: size {info['width']}x{info['height']} not {cfg.short_width}x{cfg.short_height}")
    if not info["has_audio"]:
        raise SplitError(f"{path.name}: missing audio track")


def filter_graph(cfg: Config) -> str:
    if cfg.short_fit == "crop":
        return f"[0:v]scale={cfg.short_width}:{cfg.short_height}:force_original_aspect_ratio=increase,crop={cfg.short_width}:{cfg.short_height},setsar=1[v]"
    return (
        f"[0:v]split[bg][fg];"
        f"[bg]scale={cfg.short_width}:{cfg.short_height}:force_original_aspect_ratio=increase,"
        f"crop={cfg.short_width}:{cfg.short_height},boxblur=20:10[bg];"
        f"[fg]scale={cfg.short_width}:{cfg.short_height}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )


class Splitter:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db

    def split(self, video: dict) -> list[dict]:
        work_dir = Path(video["work_dir"]) if video.get(
            "work_dir") else Path(self.cfg.output_dir / video["video_id"])
        source = next((p for p in sorted(work_dir.glob("original.*"))
                      if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
        if source is None:
            raise SplitError(
                f"original video file not found for {video['video_id']}")

        info = probe(source)
        segments = plan_segments(
            info["duration"], self.cfg.short_min, self.cfg.short_max)

        if self.cfg.upload_long_video:
            self.db.add_clip(
                video["video_id"],
                "long",
                0,
                start_sec=0,
                end_sec=info["duration"],
                duration=info["duration"],
                path=str(source),
                cut_status="done",
            )

        if not any(clip["kind"] == "short" for clip in self.db.clips(video["video_id"])):
            for idx, (start, end) in enumerate(segments, start=1):
                self.db.add_clip(video["video_id"], "short", idx, start_sec=start, end_sec=end,
                                 duration=end - start, path=str(work_dir / f"part_{idx:03d}.mp4"), cut_status="pending")

        for clip in self.db.clips(video["video_id"]):
            if clip["kind"] != "short" or clip.get("cut_status") == "done":
                continue

            out = Path(clip["path"]) if clip.get(
                "path") else work_dir / f"part_{clip['part_no']:03d}.mp4"
            tmp = out.with_suffix(".tmp.mp4")

            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{clip['start_sec']:.3f}",
                "-i", str(source.resolve()),
                "-t", f"{clip['duration']:.3f}",
                "-filter_complex", filter_graph(self.cfg),
                "-map", "[v]",
                "-map", "0:a:0?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(tmp),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise SplitError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")

            validate_clip(tmp, float(clip["duration"]), self.cfg)
            tmp.replace(out)
            self.db.update_clip(clip["id"], cut_status="done", path=str(out))
            log.info("Cut %s", out.name)

        return self.db.clips(video["video_id"])
