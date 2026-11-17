from dashboard_status import summarize_discovery


def test_discovery_summary_separates_source_warning_from_ai_failure():
    summary = summarize_discovery({
        "collected": 124,
        "kept": 2,
        "errors": ["Popular chart category 27: YouTube API error 404 notFound"],
        "results": [
            {"status": "needs_review", "ai_status": "needs_review"},
            {"status": "needs_review", "ai_status": "ai:ollama"},
        ],
    })

    assert summary["collected"] == 124
    assert summary["kept"] == 2
    assert summary["category_27_skipped"] is True
    assert summary["ai_failures"] == 1
    assert summary["needs_review"] == 2


def test_discovery_summary_reports_success_without_warnings():
    summary = summarize_discovery({
        "collected": 8,
        "kept": 1,
        "errors": [],
        "results": [{"status": "discovered", "ai_status": "ai:ollama"}],
    })

    assert summary["warnings"] == []
    assert summary["category_27_skipped"] is False
    assert summary["ai_failures"] == 0
