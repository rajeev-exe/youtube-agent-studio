"""Bounded read-only live checks for health_check.sh."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import AIClient
from config import Config
from sources import Discovery
from youtube_api import YouTubeAPI


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded read-only service diagnostics")
    parser.add_argument(
        "--discovery-only",
        action="store_true",
        help="run only read-only YouTube discovery; do not call Ollama",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cfg = Config.load()
    # Permit a cold local model some startup time while leaving room under
    # health_check.sh's 90-second process deadline for YouTube discovery.
    cfg.ai_timeout_seconds = min(cfg.ai_timeout_seconds, 25)
    cfg.max_retries = 1
    cfg.auto_discover = cfg.auto_download = cfg.auto_split = False
    cfg.auto_metadata = cfg.auto_schedule = cfg.auto_upload = False
    cfg.delete_after_verification = False
    cfg.dry_run = True

    chat_ok = True
    if not args.discovery_only:
        ai = AIClient(cfg)
        health = ai.check(timeout=10)
        print(f"Ollama model listed (/api/tags): {'PASS' if health['providers']['ollama'] else 'FAIL'} ({health['detail']['ollama']})")
        chat_ok = False
        if health["providers"]["ollama"]:
            response = ai.chat_json(
                "Return only the requested JSON object.",
                'Return {"ok": true}.',
                lambda data: data if data.get("ok") is True else (_ for _ in ()).throw(ValueError("invalid test response")),
                "health-check",
            )
            chat_ok = response is not None
        print(f"Ollama chat inference (/api/chat): {'PASS' if chat_ok else 'FAIL'}")

    if not cfg.youtube_api_key:
        print("YouTube API key: FAIL (not configured)")
        return 1
    print("YouTube API key: PASS (configured; value hidden)")
    # Run the complete discovery path, but skip AI and persistence. This still
    # exercises filtering, deduplication, scoring, and review-safe defaults.
    discovery = Discovery(cfg, None, YouTubeAPI(cfg), None)
    try:
        result = discovery.run(dry_run=True, classify=False)
        print(
            "YouTube discovery dry-run: PASS "
            f"({result['collected']} unique candidates, {result['kept']} kept; "
            "no database writes or processing)"
        )
        for error in discovery.errors:
            print(f"Discovery warning: {error}")
        youtube_ok = True
    except Exception as exc:
        print(f"YouTube discovery dry-run: FAIL ({type(exc).__name__})")
        youtube_ok = False

    return 0 if chat_ok and youtube_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
