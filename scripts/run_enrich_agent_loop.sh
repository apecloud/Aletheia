#!/usr/bin/env zsh
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

LOG_PATH="${ALETHEIA_ENRICH_LOOP_LOG:-logs/enrich-agent-loop.log}"
PID_PATH="${ALETHEIA_ENRICH_LOOP_PID:-logs/enrich-agent-loop.pid}"
LOCK_DIR="${ALETHEIA_ENRICH_LOOP_LOCK:-logs/enrich-agent-loop.lock}"
SLEEP_SECONDS="${ALETHEIA_ENRICH_LOOP_SLEEP_SECONDS:-45}"
TENANT="${ALETHEIA_TENANT:-default}"
OBJECTIVE="${ALETHEIA_ENRICH_OBJECTIVE:-Deep research the selected tenant topic, extract evidence-backed findings, metrics, situations, claims, and reviewable graph proposals}"

mkdir -p "$(dirname "$LOG_PATH")"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf "[%s] enrich_loop_already_running lock=%s\n" "$(date -Iseconds)" "$LOCK_DIR" >> "$LOG_PATH"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM
echo $$ > "$PID_PATH"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

while true; do
  printf "\n[%s] enrich_loop_start\n" "$(date -Iseconds)" >> "$LOG_PATH"
  RUN_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/aletheia-enrich-run.XXXXXX")"
  .venv/bin/python agents/iterative_graph_enrichment_agent.py \
    --tenant "$TENANT" \
    --objective "$OBJECTIVE" \
    --research-provider "${ALETHEIA_RESEARCH_PROVIDER:-gpt_researcher}" \
    --max-iterations "${ALETHEIA_ENRICH_MAX_ITERATIONS:-1}" \
    --max-frontier "${ALETHEIA_ENRICH_MAX_FRONTIER:-2}" \
    --max-results-per-query "${ALETHEIA_ENRICH_MAX_RESULTS_PER_QUERY:-2}" \
    --gpt-researcher-max-report-chars "${ALETHEIA_GPT_RESEARCHER_MAX_REPORT_CHARS:-24000}" \
    > "$RUN_OUTPUT" 2>&1
  code=$?
  cat "$RUN_OUTPUT" >> "$LOG_PATH"
  RUN_KEY="$(grep -Eo 'run=iterative-graph-run:[^[:space:]]+' "$RUN_OUTPUT" | tail -1 | sed 's/^run=//')"
  if [ -n "$RUN_KEY" ]; then
    printf "[%s] enrich_post_run_review_start run=%s\n" "$(date -Iseconds)" "$RUN_KEY" >> "$LOG_PATH"
    .venv/bin/python scripts/review_enrich_run_outputs.py \
      --tenant "$TENANT" \
      --run-key "$RUN_KEY" \
      >> "$LOG_PATH" 2>&1
    printf "[%s] enrich_post_run_review_done run=%s\n" "$(date -Iseconds)" "$RUN_KEY" >> "$LOG_PATH"
  else
    printf "[%s] enrich_post_run_review_skipped reason=run_key_not_found\n" "$(date -Iseconds)" >> "$LOG_PATH"
  fi
  rm -f "$RUN_OUTPUT"
  printf "[%s] enrich_loop_exit code=%s\n" "$(date -Iseconds)" "$code" >> "$LOG_PATH"
  sleep "$SLEEP_SECONDS"
done
