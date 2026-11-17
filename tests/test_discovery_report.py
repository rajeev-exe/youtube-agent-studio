from discovery_report import render_discovery_report, write_discovery_report


def test_report_includes_per_candidate_ai_failure_details():
    report = render_discovery_report({
        "collected": 1,
        "kept": 1,
        "new": 0,
        "errors": [],
        "results": [{
            "video_id": "video123",
            "title": "Example | title",
            "channel": "Channel",
            "views": 1000,
            "score": 12.5,
            "status": "needs_review",
            "ai_status": "needs_review",
            "ai_error": "ReadTimeout",
        }],
    }, completed_at="2026-11-12T18:00:00+00:00")

    assert "video123" in report
    assert "Example \\| title" in report
    assert "ReadTimeout" in report
    assert "Candidates written to the database: 0" in report
    assert "not evidence of reuse rights" in report


def test_incident_summary_does_not_invent_missing_candidate_ids():
    report = render_discovery_report({
        "collected": 125,
        "kept": 50,
        "ai_failures": 47,
        "needs_review": 48,
        "new": 0,
        "errors": ["Popular chart category 27: YouTube API error 404"],
        "results": [],
    }, completed_at="2026-11-12T18:21:57.279460+00:00")

    assert "AI inference failures/unavailable: 47" in report
    assert "Candidates requiring human review: 48" in report
    assert "Category 27 was skipped" in report
    assert "Candidate IDs and per-video failure details were not present" in report


def test_write_discovery_report_creates_timestamped_markdown_file(tmp_path):
    path, content = write_discovery_report(
        tmp_path,
        {"collected": 0, "kept": 0, "new": 0, "errors": [], "results": []},
        completed_at="2026-11-12T18:00:00+00:00",
    )

    assert path.name == "discovery-review-20261112T180000000000Z.md"
    assert path.read_text(encoding="utf-8") == content
