---
name: benchmark-unified-memory
description: "Run the dataset-backed unified-memory video benchmark setup: deploy LVS, ingest its videos, and generate authoritative memory through production VSS summarization. Use only from the skill-eval benchmark harness."
---

# Unified-memory benchmark

Follow the setup query and supplied task inputs exactly. This skill defines the benchmark setup procedure; it does not answer benchmark questions.

## Deployment and CLI setup

Use `vss-deploy-profile` to deploy the LVS profile with Elasticsearch, VIOS,
and the VLM. Wait until every required service is healthy before configuring
the CLI: `vss configure` probes the running ingress and records its discovered
service routes for the later benchmark steps.

After the deployment is healthy, prepare and configure the project-local CLI:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
AGENT_PROJECT="$VSS_REPO_ROOT/services/agent"

# A cancelled warm-worker task can leave an incomplete environment.
if [ -d "$AGENT_PROJECT/.venv" ] && \
   [ ! -x "$AGENT_PROJECT/.venv/bin/python" ]; then
  rm -rf "$AGENT_PROJECT/.venv"
fi

VSS=(
  uv run
  --project "$AGENT_PROJECT"
  --no-dev
  --extra cli
  vss
)

"${VSS[@]}" --help
"${VSS[@]}" configure --base-url http://localhost:7777
"${VSS[@]}" configure check
```

Do not run `vss configure` before deployment. The resulting
`~/.vss/config.json` is shared with the video-ingestion, unified-memory
summarization, and benchmark-question tasks in the same evaluation leg.

## Video setup

1. Verify that the dataset-service credentials are available. Do not print
   their values:

```bash
: "${NGC_API_KEY:?NGC_API_KEY is required}"
: "${NVDATASET_TENANTID:?NVDATASET_TENANTID is required}"
: "${NVDATASET_GROUPID:?NVDATASET_GROUPID is required}"
```

2. Install the SDK, which also provides the `nvdataset` CLI, if the command is
   not already available on the Brev worker:

```bash
if ! command -v nvdataset >/dev/null 2>&1; then
  python3 -m pip install \
    --extra-index-url https://artifactory.pdx.nvidia.com/artifactory/api/pypi/sw-ngc-data-platform-pypi-local/simple \
    nvdataset
fi

nvdataset --version
nvdataset --help >/dev/null
```

3. Download only the benchmark Parquet and the two videos exercised by this
   evaluation from the pinned dataset snapshot:

```bash
DEST="${TMPDIR:?}/videos/physical-ai-video-mme-v2"
mkdir -p "$DEST"

for key in \
  "questions.parquet" \
  "videos/warehouse-monitoring/warehouse_sample.mp4" \
  "videos/traffic-simulation/sample-sim-traffic.mp4"
do
  nvdataset download \
    physical-ai-video-mme-v2 \
    "$DEST" \
    --snapshot-name initial-v1 \
    --filter "key == '$key'"
done
```

The resulting task-private subset is:

```text
physical-ai-video-mme-v2/
├── questions.parquet
└── videos/
    ├── traffic-simulation/sample-sim-traffic.mp4
    └── warehouse-monitoring/warehouse_sample.mp4
```

4. For every supplied `dataset_video_id`:

   1. Search recursively under `$DEST/videos` and find exactly one downloaded
      video whose filename stem equals the ID.
   2. Fail if no file or more than one file matches.
   3. Ingest the matching file with the project-local `vss vios add` CLI.
   4. Use the exact filename stem as the VIOS sensor name.

5. Run `vss vios list --type video` and confirm that every supplied ID exists as a VIOS video sensor before completing the task.

Notes:
- Do not open or parse the question Parquet. Use only the safe video IDs
  supplied in the task prompt.

## Unified-memory summarization

Configure the production persistence policy before starting any summary:

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
OPENCLAW_WORKSPACE="$HOME/.openclaw/workspace"
mkdir -p "$OPENCLAW_WORKSPACE"

"${VSS[@]}" configure memory \
  --enable \
  --backend elasticsearch \
  --index vss-memory \
  --persist-by-default \
  --markdown \
  --harness openclaw \
  --workspace "$OPENCLAW_WORKSPACE" \
  --write-notes-by-default
"${VSS[@]}" configure memory show
"${VSS[@]}" configure memory check
```

For every supplied `dataset_video_id`:

1. Confirm that the same value exists as a VIOS sensor.
2. Resolve that sensor's full current timeline and mint a fresh VIOS clip URL.
3. Verify that the clip URL is byte-fetchable from the `vss-lvs` container.
4. Run exactly one summary, substituting the supplied `summarization_config` values:

```bash
"${VSS[@]}" summarize run \
  --url '<fresh VIOS clip URL>' \
  --video-id '<dataset_video_id>' \
  --scenario '<summarization_config.scenario>' \
  --event '<summarization_config.events item>' \
  --creation-time '<summarization_config.creation_time>'
```

Repeat `--event` once for every configured event. Run the videos sequentially.
Do not pass a sensor name through `--id`; LVS reserves that field for its
UUID-shaped request identity. The stable benchmark sensor belongs in
`--video-id` while the media is supplied through `--url`. Pass the supplied
`dataset_video_id` itself through `--video-id`; do not substitute the internal
VIOS sensor UUID returned by the sensor-list or clip APIs. The stable ID is
what the persisted record and OpenClaw note must expose for later recall.

Do not pass persistence flags, call `vss memory upsert`, or write Markdown manually. The configured defaults make `vss summarize run` persist its parent and child records to Elasticsearch and write its standard parent block under `~/.openclaw/workspace/memory/YYYY-MM-DD-vss.md` after authoritative persistence succeeds.

A completed summarize run is terminal. Do not rerun it because its content looks incomplete. Fail the setup task if a run fails, reports `persisted: false`, or does not report successful Markdown-note creation. Before completing, confirm that the daily VSS note contains the resulting summary job block for every supplied video and that its `Sensor:` context is the exact supplied `dataset_video_id`, not a VIOS UUID.
