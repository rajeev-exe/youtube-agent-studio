"""Trend discovery and basic ranking."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ai import AIClient
from config import Config
from storage import Database
from youtube_api import ApiError, QuotaExceeded, YouTubeAPI

log = logging.getLogger("yta.discovery")


def parse_iso_duration(value: str | None) -> int:
    """Convert YouTube ISO-8601 durations such as PT2M30S to seconds."""
    if not value or not value.startswith("P"):
        return 0
    value = value[1:]
    days = hours = minutes = seconds = 0

    if "T" in value:
        date_part, time_part = value.split("T", 1)
    else:
        date_part, time_part = value, ""

    number = ""
    for char in date_part:
        if char.isdigit():
            number += char
        elif char == "D" and number:
            days = int(number)
            number = ""

    number = ""
    for char in time_part:
        if char.isdigit():
            number += char
        elif char == "H" and number:
            hours = int(number)
            number = ""
        elif char == "M" and number:
            minutes = int(number)
            number = ""
        elif char == "S" and number:
            seconds = int(number)
            number = ""

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def score_video(views: int, likes: int | None, published_at: str, now: datetime) -> tuple[float, dict]:
    """Return a simple, explainable 0–100 popularity score and its components."""
    age_days = 365.0
    if published_at:
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            age_days = max((now - published.astimezone(timezone.utc)).total_seconds() / 86400, 1.0)
        except ValueError:
            pass

    views_per_day = max(views, 0) / age_days
    velocity = 40 * min(math.log10(views_per_day + 1) / 6, 1)
    reach = 25 * min(math.log10(max(views, 0) + 1) / 8, 1)
    like_ratio = likes / views if likes is not None and views > 0 else 0.0
    engagement = 20 * min(like_ratio / 0.05, 1)
    score = round(velocity + reach + engagement, 2)

    return score, {
        "views_per_day": round(views_per_day, 1),
        "velocity": round(velocity, 2),
        "reach": round(reach, 2),
        "engagement": round(engagement, 2),
        "like_ratio": round(like_ratio, 4),
        "age_days": round(age_days, 1),
    }


def item_to_candidate(item: dict) -> dict:
    snippet = item.get("snippet", {})
    details = item.get("contentDetails", {})
    statistics = item.get("statistics", {})
    raw_id = item.get("id")
    video_id = raw_id if isinstance(raw_id, str) else (raw_id or {}).get("videoId")

    if not video_id:
        raise ValueError("YouTube result does not contain a video ID")

    duration = details.get("duration", "")
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "views": int(statistics.get("viewCount", 0)),
        "likes": int(statistics["likeCount"]) if "likeCount" in statistics else None,
        "published_at": snippet.get("publishedAt", ""),
        "duration": duration,
        "duration_sec": parse_iso_duration(duration),
        "category": snippet.get("categoryId", ""),
        "_privacy": item.get("status", {}).get("privacyStatus", "public"),
        "_live": snippet.get("liveBroadcastContent", "none"),
    }


def _classification_validator(data: dict) -> dict:
    relevance = float(data["relevance"])
    if not 0 <= relevance <= 1:
        raise ValueError("relevance must be between 0 and 1")

    suitable = str(data.get("family_suitable", "unsure")).lower()
    directed = str(data.get("directed_to_children", "unsure")).lower()
    if suitable not in {"yes", "no", "unsure"}:
        raise ValueError("family_suitable must be yes, no, or unsure")
    if directed not in {"yes", "no", "unsure"}:
        raise ValueError("directed_to_children must be yes, no, or unsure")

    return {
        "relevance": relevance,
        "family_suitable": suitable,
        "directed_to_children": directed,
        "content_type": str(data.get("content_type", "other"))[:40].lower(),
        "reason": str(data.get("reason", ""))[:300],
    }


class Discovery:
    def __init__(
        self,
        cfg: Config,
        db: Database,
        api: YouTubeAPI,
        ai: AIClient | None = None,
        now: datetime | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.api = api
        self.ai = ai
        self.now = now or datetime.now(timezone.utc)

    def collect(self) -> dict[str, dict]:
        """Collect videos from YouTube's popular chart and configured search terms."""
        items: dict[str, dict] = {}

        for category_id in ("24", "1", "27"):
            try:
                for item in self.api.most_popular(
                    self.cfg.region_code, category_id, self.cfg.discovery_limit
                ):
                    candidate = item_to_candidate(item)
                    items[candidate["video_id"]] = item
            except QuotaExceeded:
                raise
            except ApiError as exc:
                log.warning("Popular chart category %s unavailable: %s", category_id, exc)

        for query in self.cfg.search_keywords:
            try:
                results = self.api.search(
                    q=query,
                    region=self.cfg.region_code,
                    language="en",
                    published_after=None,
                    max_results=25,
                )
                ids = [
                    result.get("id", {}).get("videoId")
                    for result in results
                    if result.get("id", {}).get("videoId")
                ]
                if ids:
                    for item in self.api.videos_list(ids):
                        items[item["id"]] = item
            except QuotaExceeded:
                log.warning("Search quota reached; keeping collected results")
                break
            except ApiError as exc:
                log.warning("Search for %r failed: %s", query, exc)

        return items

    def classify(self, candidate: dict) -> dict:
        if self.ai is not None:
            prompt = (
                f"Title: {candidate['title']}\n"
                f"Channel: {candidate['channel']}\n"
                f"Description: {candidate['description'][:500]}\n"
                "Return JSON with relevance (0 to 1), family_suitable "
                "(yes/no/unsure), directed_to_children (yes/no/unsure), "
                "content_type, and reason."
            )
            result = self.ai.chat_json(
                "Classify video metadata cautiously. Do not assume a video is safe.",
                prompt,
                _classification_validator,
                f"classify:{candidate['video_id']}",
            )
            if result:
                data, provider = result
                return {
                    "ai_relevance": data["relevance"],
                    "ai_family_suitable": data["family_suitable"],
                    "ai_content_type": data["content_type"],
                    "ai_directed_to_children": data["directed_to_children"],
                    "ai_status": f"ai:{provider}",
                }

        return {
            "ai_relevance": 0.0,
            "ai_family_suitable": "unsure",
            "ai_content_type": "other",
            "ai_directed_to_children": "unsure",
            "ai_status": "needs_review",
        }

    @staticmethod
    def status_for(classification: dict) -> str:
        if classification["ai_status"] == "needs_review":
            return "needs_review"
        if classification["ai_relevance"] < 0.5 or classification["ai_family_suitable"] == "no":
            return "rejected"
        if classification["ai_family_suitable"] == "yes":
            return "discovered"
        return "needs_review"

    def run(self) -> dict:
        items = self.collect()
        kept = []
        dropped = 0

        for item in items.values():
            try:
                candidate = item_to_candidate(item)
            except (KeyError, TypeError, ValueError):
                dropped += 1
                continue

            if (
                candidate["_privacy"] != "public"
                or candidate["_live"] != "none"
                or candidate["duration_sec"] <= 0
                or candidate["views"] < self.cfg.minimum_views
            ):
                dropped += 1
                continue

            score, detail = score_video(
                candidate["views"],
                candidate["likes"],
                candidate["published_at"],
                self.now,
            )
            candidate["score"] = score
            candidate["score_detail"] = json.dumps(detail)
            candidate["source"] = "youtube_discovery"

            classification = self.classify(candidate)
            candidate.update(classification)
            candidate["status"] = self.status_for(classification)
            candidate.pop("_privacy", None)
            candidate.pop("_live", None)
            kept.append(candidate)

        kept.sort(key=lambda video: video["score"], reverse=True)
        kept = kept[: self.cfg.discovery_limit]

        new_count = sum(1 for candidate in kept if self.db.upsert_candidate(candidate))
        return {
            "collected": len(items),
            "dropped": dropped,
            "kept": len(kept),
            "new": new_count,
        }
