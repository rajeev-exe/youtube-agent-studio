"""Read-only Streamlit dashboard for YouTube Agent Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import monitor
from ai import AIClient
from config import Config
from dashboard_status import summarize_discovery
from discovery_report import write_discovery_report
from sources import Discovery
from storage import Database
from youtube_api import ApiError, QuotaExceeded, YouTubeAPI

st.set_page_config(page_title="YouTube Agent Studio", layout="wide")
st.title("YouTube Agent Studio")
st.caption(
    "Dashboard server is running. Discovery is separate and runs only when requested. "
    "Downloads, processing, scheduling, deletion, and publishing are unavailable here."
)

try:
    cfg = Config.load()
except Exception as exc:
    st.error(f"Configuration error: {exc}")
    st.stop()

# Keep dashboard safety independent of local automation settings.
cfg.auto_discover = False
cfg.auto_download = False
cfg.auto_split = False
cfg.auto_metadata = False
cfg.auto_schedule = False
cfg.auto_upload = False
cfg.delete_after_verification = False
cfg.dry_run = True

db = Database(cfg.db_path)
try:
    stats = monitor.collect(cfg, db)
    st.subheader("Application status")
    st.write(f"Pipeline stage recorded locally: **{stats['current_stage']}**")
    col1, col2, col3 = st.columns(3)
    col1.metric("Saved candidates", stats["videos_discovered"])
    col2.metric("Awaiting permission", stats["awaiting_permission"])
    col3.metric("Authorized queue", stats["remaining_queue"])

    st.subheader("Service health")
    st.caption(
        "Checks are on demand and their results remain visible across dashboard reruns. "
        "Model listing, chat inference, and YouTube API access are reported separately."
    )
    if st.button("Check service health"):
        health = {"checked_at": datetime.now(timezone.utc).isoformat()}
        ai = AIClient(cfg)
        ai_health = ai.check(timeout=10)
        health["ollama_model"] = ai_health["detail"].get("ollama", "not checked")
        health["ollama_model_ready"] = ai_health["providers"].get("ollama", False)
        health["ollama_chat"] = "not run"
        health["ollama_chat_provider"] = None
        if health["ollama_model_ready"]:
            # This is an explicit, small smoke inference, not an automatic
            # dashboard-rerun request or a candidate classification.
            ai.usable["openrouter"] = False
            response = ai.chat_json(
                "Return only the requested JSON object.",
                'Return {"ok": true}.',
                lambda data: data if data.get("ok") is True else (_ for _ in ()).throw(
                    ValueError("invalid smoke-test response")
                ),
                "dashboard-health-check",
                timeout=cfg.ai_timeout_seconds,
            )
            if response is None:
                health["ollama_chat"] = "failed; discovery classifications require human review"
            else:
                _, health["ollama_chat_provider"] = response
                health["ollama_chat"] = "passed"
        else:
            health["ollama_chat"] = "not run; configured model is not listed as ready"

        health["youtube_key_configured"] = bool(cfg.youtube_api_key)
        health["youtube_api"] = "not checked"
        if cfg.youtube_api_key:
            try:
                YouTubeAPI(cfg).most_popular(cfg.region_code, "24", 1)
                health["youtube_api"] = "reachable (read-only popular chart request)"
            except (ApiError, QuotaExceeded) as exc:
                health["youtube_api"] = f"failed ({type(exc).__name__})"
            except Exception as exc:
                health["youtube_api"] = f"failed ({type(exc).__name__})"
        st.session_state["service_health"] = health

    health = st.session_state.get("service_health")
    if health:
        st.caption(f"Last checked: {health['checked_at']}")
        st.write(f"Ollama model listed/reachable (/api/tags): **{health['ollama_model']}**")
        st.write(f"Ollama chat inference (/api/chat): **{health['ollama_chat']}**")
        if health["ollama_chat_provider"]:
            st.write(f"Inference provider: {health['ollama_chat_provider']}")
        if health["youtube_key_configured"]:
            st.write(f"YouTube API: **{health['youtube_api']}** (key value hidden)")
        else:
            st.write("YouTube API: key not configured (key value hidden)")
    else:
        st.info("Service checks have not been run in this dashboard session.")

    st.subheader("Read-only discovery")
    st.caption(
        "Discovery is a separate user-triggered action. It uses read-only YouTube endpoints, "
        "writes no database rows, and never downloads or processes videos. AI failures remain "
        "needs_review."
    )
    if st.button("Run discovery dry-run", type="primary"):
        try:
            result = Discovery(cfg, None, YouTubeAPI(cfg), AIClient(cfg)).run(dry_run=True)
        except Exception as exc:
            st.session_state["discovery_error"] = f"{type(exc).__name__}: {exc}"
        else:
            completed_at = datetime.now(timezone.utc).isoformat()
            st.session_state["discovery_result"] = result
            st.session_state["discovery_completed_at"] = completed_at
            st.session_state.pop("discovery_error", None)
            st.session_state.pop("discovery_report_error", None)
            st.session_state.pop("discovery_report_path", None)
            st.session_state.pop("discovery_report_text", None)
            try:
                report_path, report_text = write_discovery_report(
                    cfg.base_dir / "logs", result, completed_at
                )
            except OSError as exc:
                st.session_state["discovery_report_error"] = type(exc).__name__
            else:
                st.session_state["discovery_report_path"] = str(report_path.relative_to(cfg.base_dir))
                st.session_state["discovery_report_text"] = report_text

    if st.session_state.get("discovery_error"):
        st.error(f"Discovery did not complete: {st.session_state['discovery_error']}")

    # A Streamlit session can already hold a completed result from before the
    # report feature was added. Generate its detailed report on the next rerun
    # when those candidate rows are still available in session state.
    if (
        st.session_state.get("discovery_result")
        and not st.session_state.get("discovery_report_path")
        and not st.session_state.get("discovery_report_error")
    ):
        try:
            completed_at = st.session_state.get("discovery_completed_at")
            report_path, report_text = write_discovery_report(
                cfg.base_dir / "logs",
                st.session_state["discovery_result"],
                completed_at,
            )
        except OSError as exc:
            st.session_state["discovery_report_error"] = type(exc).__name__
        else:
            st.session_state["discovery_report_path"] = str(report_path.relative_to(cfg.base_dir))
            st.session_state["discovery_report_text"] = report_text

    if st.session_state.get("discovery_result"):
        result = st.session_state["discovery_result"]
        summary = summarize_discovery(result)
        st.caption(f"Last discovery completed: {st.session_state['discovery_completed_at']}")
        if summary["warnings"]:
            st.warning(
                "Discovery completed with source warnings. "
                f"{summary['collected']} unique candidates were collected; "
                f"{summary['kept']} passed filters."
            )
        else:
            st.success(
                f"Discovery completed: {summary['collected']} unique candidates collected; "
                f"{summary['kept']} passed filters."
            )
        if summary["category_27_skipped"]:
            st.info(
                "Popular chart category 27 was skipped after its 404. "
                "The other chart categories and configured searches continued."
            )
        if summary["ai_failures"]:
            st.warning(
                f"AI inference failed for {summary['ai_failures']} candidate(s). "
                "Those candidates are marked needs_review and require human review."
            )
        st.write(
            f"Needs human review: {summary['needs_review']} · "
            f"AI unavailable/timed out: {summary['ai_failures']}"
        )
        if result["results"]:
            st.dataframe(result["results"], width="stretch", hide_index=True)
        for error in result["errors"]:
            if not error.startswith("Popular chart category 27:"):
                st.warning(error)
        if st.session_state.get("discovery_report_error"):
            st.warning(
                "Discovery completed, but the local review report could not be saved "
                f"({st.session_state['discovery_report_error']})."
            )
        if st.session_state.get("discovery_report_path"):
            st.success(f"Detailed review report saved to `{st.session_state['discovery_report_path']}`.")
            st.download_button(
                "Download detailed review report",
                data=st.session_state["discovery_report_text"],
                file_name=Path(st.session_state["discovery_report_path"]).name,
                mime="text/markdown",
            )

    st.subheader("Recent saved candidates")
    rows = db.q(
        "SELECT video_id,title,channel,views,score,status,ai_status "
        "FROM videos ORDER BY discovered_at DESC LIMIT 100"
    )
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info(
            "No candidates are saved in the database. Discovery dry-runs intentionally do not "
            "persist candidate rows; use the detailed report for this run's results."
        )

    st.info(
        "YouTube uploads are not implemented or verified. No result in this dashboard "
        "represents a scheduled or published video."
    )
finally:
    db.close()
