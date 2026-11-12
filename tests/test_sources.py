from datetime import datetime, timezone

from sources import item_to_candidate, parse_iso_duration, score_video


def test_parse_iso_duration():
    assert parse_iso_duration("PT3M20S") == 200
    assert parse_iso_duration("PT1H2M3S") == 3723
    assert parse_iso_duration("invalid") == 0


def test_score_video_returns_score_and_details():
    score, details = score_video(
        200_000,
        3_000,
        "2026-10-01T00:00:00Z",
        datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert 0 <= score <= 85
    assert details["views_per_day"] > 0
    assert "engagement" in details


def test_item_to_candidate():
    item = {
        "id": "abc123def45",
        "snippet": {
            "channelTitle": "Test Channel",
            "channelId": "channel-1",
            "title": "Sample title",
            "description": "A sample video.",
            "publishedAt": "2026-10-01T00:00:00Z",
            "categoryId": "24",
            "liveBroadcastContent": "none",
        },
        "contentDetails": {"duration": "PT3M20S"},
        "statistics": {"viewCount": "4000000", "likeCount": "120000"},
        "status": {"privacyStatus": "public"},
    }

    candidate = item_to_candidate(item)

    assert candidate["video_id"] == "abc123def45"
    assert candidate["duration_sec"] == 200
    assert candidate["views"] == 4_000_000
    assert candidate["_privacy"] == "public"
