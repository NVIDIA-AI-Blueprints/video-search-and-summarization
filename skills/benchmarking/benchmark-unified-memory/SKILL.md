---
name: benchmark-unified-memory
description: Run the dataset-backed unified-memory video benchmark setup: deploy LVS, download and ingest its videos, and initialize frozen authoritative memories plus OpenClaw Markdown projections. Use only from the skill-eval benchmark harness.
---

# Unified-memory benchmark

Follow the setup query exactly. This skill supplies deterministic setup scripts; it does not answer benchmark questions.

## Video setup

1. Download and extract the pinned NGC video fixture into `$TMPDIR/videos`:

```bash
VIDEO_DIR="${TMPDIR:?}/videos"
mkdir -p "${VIDEO_DIR}"
cd "${VIDEO_DIR}"

ngc registry resource download-version \
  nvidia/vss-developer/dev-profile-sample-data:3.2.0 \
  --org nvidia \
  --team vss-developer

tar -xzf \
  dev-profile-sample-data_v3.2.0/dev-profile-sample-data.tar.gz \
  --strip-components=1
```

This produces:

```text
$TMPDIR/videos/warehouse_sample.mp4
$TMPDIR/videos/sample-sim-traffic.mp4
```

2. For every required video, use its filename stem as the VIOS sensor name and ingest it with the project-local `vss vios add` CLI.

Notes:
- Do not derive a video manifest from the question Parquet.

## Memory setup

Initialize every parent summary and child event from the configured frozen-memory directory with the official VSS CLI:

```bash
set -euo pipefail

VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(
  uv run
  --project "$VSS_REPO_ROOT/services/agent"
  --no-dev
  --extra cli
  vss
)

"${VSS[@]}" configure memory \
  --enable \
  --backend elasticsearch \
  --index vss-memory
"${VSS[@]}" configure memory check

SOURCE_DIR="$HOME/.openclaw/skills/benchmark-unified-memory/datasets/physical-ai-video-mme-v2/memory"
STATE_DIR="${TMPDIR:?}/memory-initialization"
MARKDOWN_ROOT="$HOME/.openclaw/workspace/memory/vss"

rm -rf "$STATE_DIR"
mkdir -p "$STATE_DIR/upserts"
: > "$STATE_DIR/summary-ids.txt"
: > "$STATE_DIR/events.tsv"

shopt -s nullglob
summaries=("$SOURCE_DIR"/*_summary.json)
events=("$SOURCE_DIR"/*_event-*.json)

if ((${#summaries[@]} == 0)); then
  echo "No frozen summary records found under $SOURCE_DIR" >&2
  exit 1
fi

for source in "${summaries[@]}"; do
  persisted="$STATE_DIR/upserts/$(basename "$source")"
  "${VSS[@]}" memory upsert < "$source" > "$persisted"
  jq -er 'select(.job.record_id == null) | .job.job_id' "$persisted" \
    >> "$STATE_DIR/summary-ids.txt"
done

for source in "${events[@]}"; do
  persisted="$STATE_DIR/upserts/$(basename "$source")"
  "${VSS[@]}" memory upsert < "$source" > "$persisted"
  jq -er \
    'select(.job.record_type == "event") | [.job.job_id, .job.record_id] | @tsv' \
    "$persisted" >> "$STATE_DIR/events.tsv"
done

total_summaries=$(wc -l < "$STATE_DIR/summary-ids.txt")
unique_summaries=$(sort -u "$STATE_DIR/summary-ids.txt" | wc -l)
total_events=$(wc -l < "$STATE_DIR/events.tsv")
unique_events=$(sort -u "$STATE_DIR/events.tsv" | wc -l)

if [ "$total_summaries" -ne "$unique_summaries" ] || \
   [ "$total_events" -ne "$unique_events" ]; then
  echo "Frozen memories produced duplicate authoritative identities" >&2
  exit 1
fi

while IFS= read -r job_id; do
  "${VSS[@]}" memory get --job-id "$job_id" |
    jq -e --arg expected "$job_id" \
      '.job.job_id == $expected and (.job.record_id == null)' \
      >/dev/null
done < "$STATE_DIR/summary-ids.txt"

while IFS=$'\t' read -r job_id event_id; do
  "${VSS[@]}" memory get \
    --job-id "$job_id" \
    --record-type event \
    --record-id "$event_id" |
    jq -e \
      --arg expected_job "$job_id" \
      --arg expected_event "$event_id" \
      '.job.job_id == $expected_job and
       .job.record_type == "event" and
       .job.record_id == $expected_event' \
      >/dev/null
done < "$STATE_DIR/events.tsv"

for persisted in "$STATE_DIR"/upserts/*_summary.json; do
  uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev --extra cli \
    python "$HOME/.openclaw/skills/benchmark-unified-memory/scripts/write_job_markdown.py" \
    --input "$persisted" \
    --markdown-root "$MARKDOWN_ROOT"
done

printf \
  'Initialized and verified %d summaries and %d events.\n' \
  "$total_summaries" \
  "$total_events"
```

Parent summaries are always persisted before their child events. Markdown generation starts only after every authoritative parent and child passes exact-identity readback. Upserts are idempotent, so the setup is safe to retry.
