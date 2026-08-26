---
name: benchmark-unified-memory
description: Run the dataset-backed unified-memory video benchmark setup: deploy LVS, download and ingest its videos, and initialize frozen authoritative memories plus OpenClaw Markdown projections. Use only from the skill-eval benchmark harness.
---

# Unified-memory benchmark

Follow the setup query exactly. This skill supplies deterministic setup scripts; it does not answer benchmark questions.

## Video setup

1. Download the named NVIDIA dataset with `nvdataset` into `$TMPDIR/videos`. Preserve each video filename. For example, if named `vss-devx-base`:
```bash
nvdataset download \
  vss-devx-base \
  $TMPDIR/videos/vss-devx-base-data \
  --snapshot-name base-eval-v26.04.3 \
  --yes
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

