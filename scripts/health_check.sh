#!/usr/bin/env bash
set -u

HEALTH_BUDGET_SECONDS=90
HEALTH_STARTED_AT=$SECONDS
remaining_budget() {
  local remaining=$((HEALTH_BUDGET_SECONDS - (SECONDS - HEALTH_STARTED_AT)))
  (( remaining > 0 )) && printf '%s' "$remaining" || printf '0'
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2
mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="logs/health-${STAMP}.log"
exec > >(tee "$REPORT") 2>&1

PASS=0
FAIL=0
check() {
  local label="$1"; shift
  printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$label"
  if "$@"; then
    printf 'PASS: %s\n' "$label"; PASS=$((PASS + 1)); return 0
  else
    printf 'FAIL: %s\n' "$label"; FAIL=$((FAIL + 1)); return 1
  fi
}

printf 'YouTube Agent Studio health check\nReport: %s\n' "$REPORT"
printf 'Progress: [1/4] Checking runtime and dependencies\n'
check 'Python 3.10+' .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || CRITICAL=1
check 'Virtual environment interpreter' test -x .venv/bin/python || CRITICAL=1
check 'Bounded command timeout utility' command -v timeout || CRITICAL=1
check 'Required Python modules' .venv/bin/python -c 'import dotenv, requests, yt_dlp, streamlit' || CRITICAL=1
check 'FFmpeg and ffprobe available' bash -c 'command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null' || true
check 'Ollama CLI available' command -v ollama || true

if [[ "${CRITICAL:-0}" == 1 ]]; then
  printf '\nCritical runtime failure; stopping before project or network checks.\n'
else
  printf '\nProgress: [2/4] Checking safe configuration\n'
  check 'Safe mode configuration (uploads disabled, dry-run enabled)' .venv/bin/python -c 'from config import Config; c=Config.load(); raise SystemExit(0 if not c.auto_upload and c.dry_run else 1)' || true
  check 'YouTube API key configured (value hidden)' .venv/bin/python -c 'from config import Config; raise SystemExit(0 if Config.load().youtube_api_key else 1)' || true
  printf '\nProgress: [3/4] Running unit tests\n'
  TEST_BUDGET="$(remaining_budget)"
  if (( TEST_BUDGET > 30 )); then TEST_BUDGET=30; fi
  if (( TEST_BUDGET > 0 )); then
    check "Project tests (bounded to ${TEST_BUDGET} seconds)" timeout "${TEST_BUDGET}s" .venv/bin/python -m pytest -q || true
  else
    check 'Project tests (health-check time budget exhausted)' false || true
  fi
  printf '\nProgress: [4/4] Checking Ollama and read-only YouTube discovery\n'
  DIAGNOSTIC_BUDGET="$(remaining_budget)"
  if (( DIAGNOSTIC_BUDGET > 0 )); then
    check "Ollama and YouTube discovery dry-run (bounded to ${DIAGNOSTIC_BUDGET} seconds)" timeout "${DIAGNOSTIC_BUDGET}s" .venv/bin/python scripts/diagnostics.py || true
  else
    check 'Ollama and YouTube discovery dry-run (health-check time budget exhausted)' false || true
  fi
fi

printf '\nFinal summary\n'
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
printf 'Report saved: %s\n' "$REPORT"
if (( FAIL > 0 )); then exit 1; fi
exit 0
