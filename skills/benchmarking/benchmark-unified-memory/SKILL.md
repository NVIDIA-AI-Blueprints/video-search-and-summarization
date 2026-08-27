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

Initialize every JSON record from the configured frozen-memory directory with:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev --extra cli \
  python ~/.openclaw/skills/benchmark-unified-memory/scripts/initialize_memories.py \
  --source-dir <memory-directory> \
  --markdown-root ~/.openclaw/workspace/memory/vss \
  --es-endpoint <deployed-memory-elasticsearch-endpoint> \
  --memory-index <deployed-memory-index>
```

The initializer validates all inputs first, persists all authoritative records, verifies every readback by actual record ID, and only then writes job-named Markdown projections. It is safe to retry because authoritative IDs are assigned before persistence.
