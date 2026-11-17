"""Small, testable status summaries used by the Streamlit dashboard."""
from __future__ import annotations


def summarize_discovery(result: dict) -> dict:
    """Separate discovery/source success from AI inference and review state."""
    rows = result.get("results") or []
    errors = result.get("errors") or []
    return {
        "collected": int(result.get("collected", 0)),
        "kept": int(result.get("kept", len(rows))),
        "warnings": list(errors),
        "category_27_skipped": any(
            str(error).startswith("Popular chart category 27:") for error in errors
        ),
        "ai_failures": sum(row.get("ai_status") == "needs_review" for row in rows),
        "needs_review": sum(row.get("status") == "needs_review" for row in rows),
    }
