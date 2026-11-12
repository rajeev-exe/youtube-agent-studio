"""Project configuration loaded from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

AUTHORIZED_RIGHTS = ("owned", "licensed", "permission_confirmed")
RIGHTS_VALUES = ("pending",) + AUTHORIZED_RIGHTS + ("not_authorized",)


def _env(name: str, default: str, values: dict[str, str | None]) -> str:
    return os.getenv(name, values.get(name) or default).strip()


def _bool(name: str, default: bool, values: dict[str, str | None]) -> bool:
    value = os.getenv(name, values.get(name))
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(name: str, default: int, values: dict[str, str | None]) -> int:
    try:
        return int(os.getenv(name, values.get(name) or str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _csv(name: str, default: str, values: dict[str, str | None]) -> list[str]:
    value = os.getenv(name, values.get(name) or default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Config:
    base_dir: Path
    env_path: Path
    timezone: str
    shorts_slots: list[str]
    long_video_slot: str
    max_shorts_per_day: int
    max_long_per_day: int
    schedule_min_lead_minutes: int

    youtube_api_key: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    ollama_base_url: str
    ollama_model: str
    openrouter_api_key: str
    openrouter_base_url: str
    openrouter_model: str
    ai_provider: str
    ai_timeout_seconds: int
    loop_interval_minutes: int

    region_code: str
    search_keywords: list[str]
    minimum_views: int
    discovery_limit: int

    short_min: int
    short_max: int
    short_width: int
    short_height: int
    short_fit: str
    upload_long_video: bool

    auto_discover: bool
    auto_download: bool
    auto_split: bool
    auto_metadata: bool
    auto_upload: bool
    auto_schedule: bool
    dry_run: bool
    delete_after_verification: bool

    max_retries: int
    youtube_daily_quota: int

    @classmethod
    def load(cls, env_path: Path = ENV_PATH) -> "Config":
        env_path = Path(env_path)
        values = dotenv_values(
            env_path) if dotenv_values is not None and env_path.exists() else {}

        cfg = cls(
            base_dir=env_path.parent,
            env_path=env_path,
            timezone=_env("TIMEZONE", "Asia/Kolkata", values),
            shorts_slots=_csv(
                "SHORTS_SLOTS", "06:00,11:00,15:00,17:00,21:00", values),
            long_video_slot=_env("LONG_VIDEO_SLOT", "11:00", values),
            max_shorts_per_day=_int("MAX_SHORTS_PER_DAY", 5, values),
            max_long_per_day=_int("MAX_LONG_VIDEOS_PER_DAY", 1, values),
            schedule_min_lead_minutes=_int(
                "SCHEDULE_MIN_LEAD_MINUTES", 30, values),

            youtube_api_key=_env("YOUTUBE_API_KEY", "", values),
            google_client_id=_env("GOOGLE_CLIENT_ID", "", values),
            google_client_secret=_env("GOOGLE_CLIENT_SECRET", "", values),
            google_refresh_token=_env("GOOGLE_REFRESH_TOKEN", "", values),

            ollama_base_url=_env(
                "OLLAMA_BASE_URL", "http://localhost:11434", values).rstrip("/"),
            ollama_model=_env("OLLAMA_MODEL", "qwen2.5:3b", values),
            openrouter_api_key=_env("OPENROUTER_API_KEY", "", values),
            openrouter_base_url=_env(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1", values).rstrip("/"),
            openrouter_model=_env("OPENROUTER_MODEL",
                                  "openrouter/free", values),
            ai_provider=_env("AI_PROVIDER", "ollama", values).lower(),
            ai_timeout_seconds=_int("AI_TIMEOUT_SECONDS", 20, values),
            loop_interval_minutes=_int("LOOP_INTERVAL_MINUTES", 360, values),

            region_code=_env("REGION_CODE", "IN", values).upper(),
            search_keywords=_csv(
                "SEARCH_KEYWORDS", "family friendly animation,education", values),
            minimum_views=_int("MINIMUM_VIEWS", 10000, values),
            discovery_limit=_int("DISCOVERY_LIMIT", 50, values),

            short_min=_int("SHORT_MIN_DURATION", 30, values),
            short_max=_int("SHORT_MAX_DURATION", 60, values),
            short_width=_int("SHORT_WIDTH", 1080, values),
            short_height=_int("SHORT_HEIGHT", 1920, values),
            short_fit=_env("SHORT_FIT", "blur", values).lower(),
            upload_long_video=_bool("UPLOAD_LONG_VIDEO", True, values),

            auto_discover=_bool("AUTO_DISCOVER", False, values),
            auto_download=_bool("AUTO_DOWNLOAD", False, values),
            auto_split=_bool("AUTO_SPLIT", False, values),
            auto_metadata=_bool("AUTO_METADATA", False, values),
            auto_upload=_bool("AUTO_UPLOAD", False, values),
            auto_schedule=_bool("AUTO_SCHEDULE", False, values),
            dry_run=_bool("DRY_RUN", True, values),
            delete_after_verification=_bool(
                "DELETE_AFTER_VERIFICATION", False, values),

            max_retries=_int("MAX_RETRIES", 3, values),
            youtube_daily_quota=_int("YOUTUBE_DAILY_QUOTA", 10000, values),
        )
        cfg.validate()
        return cfg

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.base_dir / "app.db"

    @property
    def output_dir(self) -> Path:
        return self.base_dir / "output"

    @property
    def trending_csv(self) -> Path:
        return self.base_dir / "trending.csv"

    @property
    def links_csv(self) -> Path:
        return self.base_dir / "links.csv"

    @property
    def report_path(self) -> Path:
        return self.base_dir / "report.md"

    @property
    def has_oauth(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_refresh_token
        )

    def validate(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {self.timezone}") from exc

        if not self.shorts_slots:
            raise ValueError("SHORTS_SLOTS must contain at least one time")
        if len(set(self.shorts_slots)) != len(self.shorts_slots):
            raise ValueError("SHORTS_SLOTS must not contain duplicate times")

        for name, value in [("LONG_VIDEO_SLOT", self.long_video_slot), *[
            ("SHORTS_SLOTS", slot) for slot in self.shorts_slots
        ]]:
            try:
                hour, minute = map(int, value.split(":"))
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    f"{name} has invalid time {value!r}; use HH:MM") from exc

        if self.ai_provider not in {"ollama", "openrouter"}:
            raise ValueError("AI_PROVIDER must be ollama or openrouter")
        if self.short_min < 1 or self.short_min > self.short_max:
            raise ValueError("Short duration settings are invalid")
        if self.short_max > 180:
            raise ValueError("SHORT_MAX_DURATION cannot exceed 180 seconds")
        if self.short_fit not in {"blur", "crop"}:
            raise ValueError("SHORT_FIT must be blur or crop")
        if self.short_width < 2 or self.short_height < 2:
            raise ValueError(
                "Short video dimensions must be at least 2 pixels")
        if self.max_shorts_per_day < 1 or self.max_long_per_day < 1:
            raise ValueError("Daily publishing limits must be at least 1")
        if self.max_retries < 1:
            raise ValueError("MAX_RETRIES must be at least 1")
        if not 1 <= self.ai_timeout_seconds <= 120:
            raise ValueError("AI_TIMEOUT_SECONDS must be between 1 and 120")
        if self.loop_interval_minutes < 1:
            raise ValueError("LOOP_INTERVAL_MINUTES must be at least 1")
