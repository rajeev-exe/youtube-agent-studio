"""Markdown incident and candidate reports for read-only discovery runs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dashboard_status import summarize_discovery


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_discovery_report(
    result: dict,
    completed_at: str | None = None,
    note: str | None = None,
) -> str:
    summary = summarize_discovery(result)
    ai_failures = int(result.get("ai_failures", summary["ai_failures"]))
    needs_review = int(result.get("needs_review", summary["needs_review"]))
    completed_at = completed_at or datetime.now(timezone.utc).isoformat()
    rows = result.get("results") or []

    lines = [
        "# YouTube discovery review report",
        "",
        f"Completed: {completed_at}",
        "Mode: read-only dry-run",
        "",
        "## Run summary",
        "",
        f"- Unique candidates collected: {summary['collected']}",
        f"- Candidates passing filters: {summary['kept']}",
        f"- AI inference failures/unavailable: {ai_failures}",
        f"- Candidates requiring human review: {needs_review}",
        f"- Candidates written to the database: {int(result.get('new', 0))}",
        "- Downloads, processing, scheduling, deletion, and uploads: not run",
        "- Search results and AI output are not evidence of reuse rights.",
        "",
    ]

    if note:
        lines.extend(["## Incident notes", "", note, ""])

    lines.extend(["## Source warnings", ""])
    if summary["warnings"]:
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    else:
        lines.append("- None")
    if summary["category_27_skipped"]:
        lines.extend([
            "",
            "Category 27 was skipped after its 404. This is nonfatal; discovery continued with the other categories and configured searches.",
        ])

    lines.extend([
        "",
        "## Candidate-by-candidate review",
        "",
    ])
    if rows:
        lines.extend([
            "| Video ID | Title | Channel | Views | Score | Candidate status | AI status | AI failure/review detail |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ])
        for row in rows:
            lines.append(
                "| " + " | ".join(_cell(row.get(key, "")) for key in (
                    "video_id", "title", "channel", "views", "score", "status", "ai_status", "ai_error"
                )) + " |"
            )
    else:
        lines.append(
            "Candidate IDs and per-video failure details were not present in the supplied dashboard summary. "
            "The earlier run did not persist candidates because it was a read-only dry-run. New dashboard runs include this table."
        )

    lines.extend([
        "",
        "## Readiness estimate",
        "",
        "Read-only manual discovery is working now. Based on the observed chat timeouts, allow roughly 1–2 engineering days to diagnose the Ollama host/model latency and verify stable AI-assisted discovery after that environment is available; this is an estimate, not a completion guarantee.",
        "",
        "There is no defensible end-to-end real-time publishing ETA: the uploader is intentionally unimplemented, and this report does not validate upload, scheduling, or production automation. A periodic read-only discovery cadence can be estimated separately once its required interval and quota budget are specified.",
        "",
    ])
    return "\n".join(lines)


def write_discovery_report(
    report_dir: str | Path,
    result: dict,
    completed_at: str | None = None,
    note: str | None = None,
) -> tuple[Path, str]:
    timestamp = datetime.now(timezone.utc)
    if completed_at:
        try:
            timestamp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"discovery-review-{stamp}.md"
    content = render_discovery_report(result, completed_at, note)
    path.write_text(content, encoding="utf-8")
    return path, content
