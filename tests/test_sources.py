from datetime import datetime, timezone

from sources import Discovery, item_to_candidate, parse_iso_duration, score_video
from youtube_api import ApiError


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



def _discovery_item(video_id="abc123def45"):
    return {
        "id": video_id,
        "snippet": {
            "channelTitle": "Test Channel",
            "channelId": "channel-1",
            "title": "Sample family animation",
            "description": "A sample video.",
            "publishedAt": "2026-10-01T00:00:00Z",
            "categoryId": "24",
            "liveBroadcastContent": "none",
        },
        "contentDetails": {"duration": "PT3M20S"},
        "statistics": {"viewCount": "4000000", "likeCount": "120000"},
        "status": {"privacyStatus": "public"},
    }


def test_collect_category_27_404_is_non_fatal_and_search_terms_continue(cfg):
    cfg.search_keywords = ["animation", "education"]

    class FakeAPI:
        def __init__(self):
            self.categories = []
            self.queries = []

        def most_popular(self, region, category_id, limit):
            self.categories.append(category_id)
            if category_id == "27":
                raise ApiError(404, "notFound", "category unavailable")
            return [_discovery_item()] if category_id == "24" else []

        def search(self, q, region, language, published_after, max_results):
            self.queries.append(q)
            return []

        def videos_list(self, ids):
            raise AssertionError("no video detail lookup expected")

    api = FakeAPI()
    discovery = Discovery(cfg, None, api)
    collected = discovery.collect()

    assert api.categories == ["24", "1", "27"]
    assert api.queries == ["animation", "education"]
    assert "abc123def45" in collected


def test_ai_classification_exception_safely_marks_candidate_for_review(cfg, db):
    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            if category_id == "24":
                return [_discovery_item()]
            if category_id == "27":
                raise ApiError(404, "notFound", "category unavailable")
            return []

        def search(self, q, region, language, published_after, max_results):
            return []

        def videos_list(self, ids):
            return []

    class BrokenAI:
        def chat_json(self, *args, **kwargs):
            raise TimeoutError("classifier timed out")

    result = Discovery(cfg, db, FakeAPI(), BrokenAI()).run()

    assert result["kept"] == 1
    saved = db.video("abc123def45")
    assert saved["status"] == "needs_review"
    assert saved["ai_status"] == "needs_review"
    assert saved["ai_family_suitable"] == "unsure"


def test_discovery_search_uses_q_parameter(cfg):
    cfg.search_keywords = ["animation"]

    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            return []

        def search(self, **kwargs):
            assert kwargs["q"] == "animation"
            assert "query" not in kwargs
            return []

        def videos_list(self, ids):
            return []

    Discovery(cfg, None, FakeAPI()).collect()


def test_dry_run_deduplicates_and_does_not_require_database(cfg):
    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            return [_discovery_item(), _discovery_item()] if category_id == "24" else []

        def search(self, **kwargs):
            return []

        def videos_list(self, ids):
            return []

    discovery = Discovery(cfg, None, FakeAPI())
    result = discovery.run(dry_run=True, classify=False)
    assert result["collected"] == 1
    assert result["kept"] == 1
    assert result["new"] == 0
    assert result["results"][0]["status"] == "needs_review"


def test_discovery_caps_candidates_before_ai_and_dry_run_never_writes(cfg):
    cfg.discovery_limit = 1

    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            if category_id == "24":
                return [_discovery_item("candidate0001"), _discovery_item("candidate0002")]
            return []

        def search(self, **kwargs):
            return []

        def videos_list(self, ids):
            raise AssertionError("no search detail lookup expected")

    class NoWriteDB:
        def upsert_candidate(self, candidate):
            raise AssertionError("dry-run attempted a database write")

    class CountingAI:
        calls = 0

        def chat_json(self, *args, **kwargs):
            self.calls += 1
            return ({
                "relevance": 0.9,
                "family_suitable": "yes",
                "directed_to_children": "yes",
                "content_type": "animation",
                "reason": "mock response",
            }, "mock")

    ai = CountingAI()
    result = Discovery(cfg, NoWriteDB(), FakeAPI(), ai).run(dry_run=True)

    assert result["collected"] == 2
    assert result["kept"] == 1
    assert ai.calls == 1
    assert result["new"] == 0


def test_discovery_ai_budget_exhaustion_marks_remaining_candidates_for_review(cfg, monkeypatch):
    cfg.ai_discovery_budget_seconds = 1
    clock = [100.0]
    monkeypatch.setattr("sources.time.monotonic", lambda: clock[0])

    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            if category_id == "24":
                return [_discovery_item("candidate0001"), _discovery_item("candidate0002")]
            return []

        def search(self, **kwargs):
            return []

        def videos_list(self, ids):
            return []

    class SlowAI:
        calls = 0

        def chat_json(self, *args, **kwargs):
            self.calls += 1
            clock[0] += 2
            return None

    ai = SlowAI()
    result = Discovery(cfg, None, FakeAPI(), ai).run(dry_run=True)

    assert ai.calls == 1
    assert result["kept"] == 2
    assert [row["status"] for row in result["results"]] == ["needs_review", "needs_review"]


def test_discovery_stops_ai_calls_after_first_unavailable_result(cfg):
    class FakeAPI:
        def most_popular(self, region, category_id, limit):
            if category_id == "24":
                return [_discovery_item("candidate0001"), _discovery_item("candidate0002")]
            return []

        def search(self, **kwargs):
            return []

        def videos_list(self, ids):
            return []

    class UnavailableAI:
        calls = 0

        def chat_json(self, *args, **kwargs):
            self.calls += 1
            return None

    ai = UnavailableAI()
    result = Discovery(cfg, None, FakeAPI(), ai).run(dry_run=True)

    assert ai.calls == 1
    assert result["kept"] == 2
    assert [row["ai_status"] for row in result["results"]] == [
        "needs_review", "needs_review"
    ]


def test_search_ids_are_deduplicated_across_queries(cfg):
    cfg.search_keywords = ["first", "second"]

    class FakeAPI:
        detail_calls = []

        def most_popular(self, region, category_id, limit):
            return []

        def search(self, **kwargs):
            return [{"id": {"videoId": "sharedVideo01"}}]

        def videos_list(self, ids):
            self.detail_calls.append(ids)
            return [_discovery_item(ids[0])]

    api = FakeAPI()
    Discovery(cfg, None, api).collect()

    assert api.detail_calls == [["sharedVideo01"]]
