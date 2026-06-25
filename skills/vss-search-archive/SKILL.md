---
name: vss-search-archive
description: Use this skill to run top-level VSS fusion search on archived video, or to ingest video files / RTSP streams for search. Do NOT use for ad-hoc visual Q&A (use vss-ask-video), live captioning (use vss-deploy-dense-captioning), or video summarization and reports (use vss-summarize-video).
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Purpose

Run the top-level VSS fusion search across archived video, ingest new clips / RTSP streams for search, and delete search-ingested sources.

## Prerequisites

- Active VSS deployment reachable on `$HOST_IP` (see `vss-deploy-profile` and `references/`).
- `vss-manage-video-io-storage` skill installed (used to list and manage video sources before search).
- NGC credentials in `$NGC_CLI_API_KEY` and `$NVIDIA_API_KEY` for any image pulls.
- `curl`, `jq`, and Docker or Kubernetes exec access available on the caller.
- `search-archive` available inside the running `vss-agent` container / pod.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/`.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow command blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

## Limitations

- Requires the matching VSS profile / microservice to be deployed and reachable from the caller.
- NGC-hosted models and NIMs may be subject to rate-limits, GPU memory requirements, and license restrictions.
- Concurrency, GPU memory, and storage limits depend on the host hardware and the profile's compose file.

## Troubleshooting

- **Error**: REST call returns connection refused. **Cause**: target microservice not running. **Solution**: probe `/docs` or `/health`; redeploy via `vss-deploy-profile` or the matching `vss-deploy-*` skill.
- **Error**: HTTP 401/403 from NGC pulls. **Cause**: missing/expired `NGC_CLI_API_KEY`. **Solution**: `docker login nvcr.io` and re-export the key before retrying.
- **Error**: container OOM or model fails to load. **Cause**: insufficient GPU memory for the selected profile. **Solution**: switch to a smaller variant or free GPUs via `docker compose down`.

# Video Search Workflows

> **Alpha Feature** — not recommended for production use.

Search video archives by natural language using Cosmos Embed1 embeddings. Requires the search profile — deploy with the `vss-deploy-profile` skill (`-p search`). These videos sources can be ingested files or RTSP streams.

## When to Use

- "Find all instances of forklifts"
- "When did someone enter the restricted area?"
- "Show me people near the loading dock"
- "Search for vehicles between 8am and noon"
- Any natural-language search across video archives
- "Ingest `<file>` for search" / "upload this video for search"
- "Add this RTSP stream for search" / "register `<rtsp_url>` for search"
- "Delete `<file>` from search" / "remove this video and embeddings"

---

## Deployment prerequisite

This skill requires the VSS **search** profile running on the host at `$HOST_IP`. Before any request:

1. Probe the stack:
   ```bash
   curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null \
     && curl -sf --max-time 5 "http://${HOST_IP}:9200/" >/dev/null
   ```
   (The second check confirms Elasticsearch is up — unique to the search profile.)

2. **If the probe fails**, ask the user:
   > *"The VSS `search` profile isn't running on `$HOST_IP`. Shall I deploy it now using the `/vss-deploy-profile` skill with `-p search`?"*

   - If yes → hand off to the `/vss-deploy-profile` skill. Return here once it succeeds.
   - If no → stop. Do not run this skill against a missing or wrong-profile stack.

   (If your caller has granted explicit pre-authorization to deploy
   autonomously — e.g. the request says "pre-authorized to deploy
   prerequisites", or you are running in a non-interactive evaluation
   harness with that permission — skip the confirmation and invoke
   `/vss-deploy-profile` directly.)

3. If the probe passes, proceed.

---

## Ingestion prerequisite (required before any search)

For a source to be searchable it must be ingested **through the VSS agent backend**, not through VIOS alone. The agent's ingest routes own the VIOS upload + RTVI-CV register + RTVI-embed pipeline as one transaction; a bare VIOS PUT only stores the bytes and never wires them into Elasticsearch.

Confirm the source exists in VIOS first (Mandatory workflow Step 2). If it is missing, ingest it with one of the recipes below before running `search-archive`. After ingest succeeds, the source appears in `sensor/list` under the name you provided and can be passed to the CLI with `--video-source`.

### File upload — universal three-step flow

Use the timestamped upload form below. The VSS agent/search profile uses
`2025-01-01T00:00:00.000Z` as the uploaded `video_file` base timestamp;
VIOS storage and embeddings must share that timeline, otherwise
screenshot URLs and critic frame fetches can fail.

```bash
FILENAME="<filename.mp4>"
FILE_PATH="/path/to/${FILENAME}"

# 1. Ask the agent for the chunked-upload URL
UPLOAD_URL=$(curl -s -X POST "http://${HOST_IP}:8000/api/v1/videos" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"${FILENAME}\"}" | jq -r .url)

# 2. Chunked POST the file to that VST URL (nvstreamer protocol).
#    The final-chunk response carries sensorId.
IDENTIFIER=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
UPLOAD_RESPONSE=$(curl -s -X POST "${UPLOAD_URL}" \
  -H "nvstreamer-chunk-number: 1" \
  -H "nvstreamer-total-chunks: 1" \
  -H "nvstreamer-is-last-chunk: true" \
  -H "nvstreamer-identifier: ${IDENTIFIER}" \
  -H "nvstreamer-file-name: ${FILENAME}" \
  -F "mediaFile=@${FILE_PATH};filename=${FILENAME}" \
  -F "filename=${FILENAME}" \
  -F 'metadata={"timestamp":"2025-01-01T00:00:00"}')

# 3. Tell the agent the upload finished — this fans out to RTVI-CV + RTVI-embed
SENSOR=$(printf '%s' "${UPLOAD_RESPONSE}" | jq -r .sensorId)
[ -z "${SENSOR}" ] || [ "${SENSOR}" = "null" ] \
  && { echo "Upload failed: no sensorId in response: ${UPLOAD_RESPONSE}"; exit 1; }
printf '%s' "${UPLOAD_RESPONSE}" \
  | jq --arg filename "${FILENAME}" '. + {filename: $filename}' \
  | curl -s -X POST "http://${HOST_IP}:8000/api/v1/videos/${SENSOR}/complete" \
      -H "Content-Type: application/json" \
      -d @- | jq .
```

Wait for the `/complete` response (it returns `chunks_processed > 0` once embeddings land). Only then is the video searchable.

> The deprecated `PUT /api/v1/videos-for-search/{filename}` route is also wired in for legacy callers (single-shot, agent-driven), but its OpenAPI entry is flagged `deprecated`. Prefer the three-step flow above for new work.

### RTSP stream — single endpoint

```bash
curl -s -X POST "http://${HOST_IP}:8000/api/v1/rtsp-streams/add" \
  -H "Content-Type: application/json" \
  -d '{
    "sensorUrl": "rtsp://<host>:<port>/<path>",
    "name": "<sensor-name>",
    "username": "",
    "password": "",
    "location": "",
    "tags": ""
  }' | jq .
```

The response shape is `{status, message, error}` — no `sensorId` (the agent keys the stream by the `name` you provided). On any step's failure earlier steps roll back. The `start_embedding_generation` step is fire-and-verify: a 2xx confirms the request was accepted and the embedding pipeline is running in the background, **not** that the stream is searchable yet. Search hits will start appearing only after enough chunks land in Elasticsearch — poll with a low-`top_k` query a few seconds in if you need a readiness signal.

### Delete source — agent-backed cleanup

Delete through the agent backend, not bare VIOS, so VIOS storage and search embeddings are cleaned up together.

```bash
# For video files: video_id is the VIOS sensor/video UUID
curl -s -X DELETE "http://${HOST_IP}:8000/api/v1/videos/<video_id>" | jq .

# For RTSP streams: name is the registered source name
curl -s -X DELETE "http://${HOST_IP}:8000/api/v1/rtsp-streams/delete/<name>" | jq .
```

---

## How Search Works

1. **Ingest** — Files come in through the agent's three-step universal flow; RTSP streams through `/api/v1/rtsp-streams/add`. Both routes hand the source to RTVI-CV (attribute detection) and RTVI-Embed (Cosmos Embed1) which generates vector embeddings for video segments.
2. **Index** — Embeddings are stored in Elasticsearch via the Kafka pipeline.
3. **Query** — The host agent decomposes the request, then `search-archive` invokes `lib.search_core` directly with explicit fields. It never calls the VSS agent `/generate` API for search.
4. **Verify** — If requested and the deployed VLM is configured, the NAT-free critic calls the same OpenAI-compatible VLM service with VST clips or sampled frames.
5. **Results** — Timestamped video segments ranked by relevance, with clip playback links and optional critic criteria.

This search orchestrated by `lib.search_core` can lead to 3 behaviors:
- Attribute-only: when the caller passes appearance attributes and `--has-action false` (e.g. "person wearing red jacket")
- Embed-only: when the caller passes only `--query` with no attributes (e.g. "show me forklifts")
- Fusion: when the caller passes both `--query`, `--attribute`, and `--has-action true` (e.g. "person in red jacket running"), it runs embed search first, then reranks using attribute search

---

## Mandatory workflow

When using this skill, ALWAYS follow this high-level workflow:
1. **Resolve deployment inputs.** See § Input resolution below and read
   [deployment_resolution.md](references/deployment_resolution.md). Prefer the
   live `vss-agent` container/pod env plus mounted NAT config. For Docker
   dev profiles, also use `deploy/docker/developer-profiles/dev-profile-search/generated.env`
   when present; for Helm, use the rendered pod env/configmap. HARD STOP only
   if neither user instructions nor deployment artifacts provide a usable VSS
   agent endpoint/runtime.
2. **Resolve the source — HARD STOP before any `search-archive` call.**
   If the user query references a specific video / sensor name
   (e.g. "the airport video", "warehouse_cam_3", "sample warehouse"),
   verify it's actually registered in VIOS **before** running
   `search-archive`. List sources via the `vss-manage-video-io-storage` skill.

   Then:
   - **If the named source (or a clearly substring-matching name) IS in the list** → proceed to step 3. Pass the resolved source name with `--video-source`; do not rely on NAT query decomposition.
   - **If the named source is NOT in the list** → STOP. Do NOT run `search-archive` as a probe. Respond to the user with the registered source names and ask whether they meant one of those, want to ingest the missing source (point them at *Ingestion prerequisite* and run the matching file or RTSP recipe through the **agent backend**, not bare VIOS), or want to abandon the query. Wait for clarification.
   - **If the query names no specific source** ("find forklifts in the ingested videos", "search across all sources") → skip the substring check, but `sensor/list` must still return non-empty (otherwise no sources are ingested → HARD STOP).
3. Read [deployment_resolution.md](references/deployment_resolution.md) and [query_decomposition.md](references/query_decomposition.md), decompose the user request, then run `search-archive` with the explicit CLI flags in *Search via CLI*. Prefer `--decomposed-json` when the request includes attributes, action binding, object IDs, source selection, time filters, or critic intent.
4. Present the results to the user query. Format response as a professional inspection report but name it `Video Search Results`:
   — Use clear section headers
   - Organize findings individually with supporting detail, and close with a summary
   - Use tables where comparisons help. Write like a technical report, not a chat message.
   - If criteria results are non-null, then in addition to a column "Critic result" ("confirmed" | "rejected" | "skipped"), include a column "Criteria" with all the criteria for this search result ({criteria_n}: ✓ | ✗)
5. CRITICAL: Verify the results and explain this to the user concisely.
   If search fails, or returns unexpected results (i.e. videos that do not appear to match user query, zero matches, zero videos returned, error etc.), STOP. Do not proceed without reading [troubleshooting.md](references/troubleshooting.md) to iterate with feedback loops until proper results are found and presented like a professional inspection report.
6. Final verifications:
   - ALWAYS inform user that final and further verifications can be run. Present this as a `Verification Step`
   - ONLY IF user agrees, download screenshots using the `screenshot_url` of the best candidates (highest similarity scores) from the search hits (JSON results) to `/tmp`. Read them and verify if they correspond to the user query

## Input resolution

Infer these inputs from the conversation, the live deployment, or deployment artifacts. If some cannot be inferred, ask the user immediately:
- `$HOST_IP` / VSS agent endpoint: user-provided, live Docker/Helm env, or Docker `generated.env`
- Search runtime args: derive from the deployed NAT config plus resolved Docker/Helm env; do not invent localhost defaults

---

## Gotchas

- ALWAYS step into the troubleshooting step of the workflow immediately if anything unexpected happens, read [troubleshooting.md](references/troubleshooting.md)
- Queries work best with **concrete visual descriptions** (objects, actions, locations). Augment user queries if needed to enhance the quality of the questions, expanding potential details
- The skill assumes video sources are **already ingested through the agent backend** (see *Ingestion prerequisite*). It MAY run the agent-backed ingest recipes when the user explicitly asks ("ingest `<file>` for search", "add `<rtsp_url>` for search"); it does NOT search the local filesystem for files the user didn't name, and it does NOT use the bare-VIOS PUT path (no embeddings get generated). Workflow step 2 still makes confirming "this source exists in VIOS" a hard precondition before `search-archive`.
- Use `vss-query-analytics` skill to cross-reference search results with incident/alert data

---

## Search via CLI

Default to this CLI approach. Do not call the VSS agent `/generate` API for search.

Run `search-archive` inside the `vss-agent` container / pod, and pass every backend/runtime value as an explicit CLI flag. Prefer the deployed `$VSS_AGENT_CONFIG_FILE` when present, with each interpolation value passed through `--config-env KEY=VALUE`; the CLI itself never reads `$VSS_AGENT_CONFIG_FILE` or endpoint env vars. For Docker, use `generated.env` plus the live container env to resolve those values; for Helm, use the rendered pod env/configmap. This preserves the configured search profile, ES, RTVI, VST, VLM, critic, and media behavior without invoking NAT.

```bash
docker exec -i vss-agent sh -lc '
set -eu
EMBED_ENDPOINT="${COSMOS_EMBED_ENDPOINT:-${RTVI_EMBED_BASE_URL:-http://${HOST_IP:-localhost}:${RTVI_EMBED_PORT:-8017}}}"
CV_ENDPOINT="${RTVI_CV_BASE_URL:-http://${HOST_IP:-localhost}:${RTVI_CV_PORT:-9000}}"
VLM_BASE="${VLM_BASE_URL:-}"
if [ -n "$VLM_BASE" ]; then
  case "$VLM_BASE" in */v1|*/v1/) VLM_BASE="${VLM_BASE%/}" ;; *) VLM_BASE="${VLM_BASE%/}/v1" ;; esac
fi
ENABLE_AUDIO_LC=$(printf "%s" "${ENABLE_AUDIO:-false}" | tr "[:upper:]" "[:lower:]")
VLM_MODE_LC=$(printf "%s" "${VLM_MODE:-local}" | tr "[:upper:]" "[:lower:]")
VLM_MODEL_LC=$(printf "%s" "${VLM_NAME:-}" | tr "[:upper:]" "[:lower:]")
MEDIA_MODE=video-url
if [ "$VLM_MODE_LC" = "remote" ]; then
  case "$ENABLE_AUDIO_LC:$VLM_MODEL_LC" in
    true:*omni*) MEDIA_MODE=video-base64 ;;
    *) MEDIA_MODE=frame-base64 ;;
  esac
fi
CONFIG_FILE="${VSS_AGENT_CONFIG_FILE:-}"
if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
  set -- \
    --config "$CONFIG_FILE" \
    --config-env "HOST_IP=${HOST_IP:-localhost}" \
    --config-env "ELASTIC_SEARCH_ENDPOINT=${ELASTIC_SEARCH_ENDPOINT:-}" \
    --config-env "BEHAVIOR_ES_ENDPOINT=${BEHAVIOR_ES_ENDPOINT:-}" \
    --config-env "COSMOS_EMBED_ENDPOINT=${COSMOS_EMBED_ENDPOINT:-}" \
    --config-env "RTVI_EMBED_BASE_URL=${RTVI_EMBED_BASE_URL:-}" \
    --config-env "RTVI_EMBED_PORT=${RTVI_EMBED_PORT:-}" \
    --config-env "RTVI_EMBED_MODEL=${RTVI_EMBED_MODEL:-cosmos-embed1-448p}" \
    --config-env "RTVI_CV_BASE_URL=${RTVI_CV_BASE_URL:-}" \
    --config-env "RTVI_CV_PORT=${RTVI_CV_PORT:-}" \
    --config-env "VST_INTERNAL_URL=${VST_INTERNAL_URL:-}" \
    --config-env "VST_EXTERNAL_URL=${VST_EXTERNAL_URL:-}" \
    --config-env "ELASTIC_SEARCH_INDEX=${ELASTIC_SEARCH_INDEX:-video_embeddings}" \
    --config-env "ELASTIC_SEARCH_INDEX_WILDCARD=${ELASTIC_SEARCH_INDEX_WILDCARD:-mdx-embed-filtered-*}" \
    --config-env "BEHAVIOR_INDEX=${BEHAVIOR_INDEX:-mdx-behavior-2025-01-01}" \
    --config-env "BEHAVIOR_INDEX_WILDCARD=${BEHAVIOR_INDEX_WILDCARD:-mdx-behavior-*}" \
    --config-env "FRAMES_INDEX=${FRAMES_INDEX:-mdx-raw-2025-01-01}" \
    --config-env "FRAMES_INDEX_WILDCARD=${FRAMES_INDEX_WILDCARD:-mdx-raw-*}" \
    --config-env "ENABLE_CRITIC=${ENABLE_CRITIC:-true}" \
    --config-env "ENABLE_AUDIO=${ENABLE_AUDIO:-false}" \
    --config-env "VLM_BASE_URL=${VLM_BASE_URL:-}" \
    --config-env "VLM_NAME=${VLM_NAME:-}" \
    --config-env "VLM_MODE=${VLM_MODE:-}" \
    --config-env "VLM_MODEL_TYPE=${VLM_MODEL_TYPE:-nim}" \
    --config-env "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
    --config-env "NVIDIA_API_KEY=${NVIDIA_API_KEY:-}" \
    --config-env "CRITIC_TIME_FORMAT=${CRITIC_TIME_FORMAT:-offset}" \
    --config-env "CRITIC_EVALUATION_COUNT=${CRITIC_EVALUATION_COUNT:-5}" \
    --config-env "VLM_MAX_FRAMES=${VLM_MAX_FRAMES:-60}" \
    --config-env "VLM_MAX_FPS=${VLM_MAX_FPS:-2}" \
    --vlm-media-mode "$MEDIA_MODE" \
    --vlm-video-url-scope internal \
    --log-level ERROR \
    "$@"
else
  set -- \
    --es-endpoint "$ELASTIC_SEARCH_ENDPOINT" \
    --behavior-es-endpoint "${BEHAVIOR_ES_ENDPOINT:-$ELASTIC_SEARCH_ENDPOINT}" \
    --cosmos-embed-endpoint "$EMBED_ENDPOINT" \
    --rtvi-cv-endpoint "$CV_ENDPOINT" \
    --vst-internal-url "$VST_INTERNAL_URL" \
    --vst-external-url "$VST_EXTERNAL_URL" \
    --cosmos-embed-model "${RTVI_EMBED_MODEL:-cosmos-embed1-448p}" \
    --video-embed-index "${ELASTIC_SEARCH_INDEX:-video_embeddings}" \
    --video-embed-index-wildcard "${ELASTIC_SEARCH_INDEX_WILDCARD:-mdx-embed-filtered-*}" \
    --behavior-index "${BEHAVIOR_INDEX:-mdx-behavior-2025-01-01}" \
    --behavior-index-wildcard "${BEHAVIOR_INDEX_WILDCARD:-mdx-behavior-*}" \
    --frames-index "${FRAMES_INDEX:-mdx-raw-2025-01-01}" \
    --frames-index-wildcard "${FRAMES_INDEX_WILDCARD:-mdx-raw-*}" \
    --no-enable-frame-lookup \
    --default-max-results "${SEARCH_DEFAULT_MAX_RESULTS:-10}" \
    --embed-default-max-results "${EMBED_DEFAULT_MAX_RESULTS:-100}" \
    --search-max-iterations "${SEARCH_MAX_ITERATIONS:-1}" \
    --embed-confidence-threshold "${EMBED_CONFIDENCE_THRESHOLD:-0.1}" \
    --fusion-method "${FUSION_METHOD:-rrf}" \
    --critic-time-format "${CRITIC_TIME_FORMAT:-offset}" \
    --critic-evaluation-count "${CRITIC_EVALUATION_COUNT:-5}" \
    --vlm-max-frames "${VLM_MAX_FRAMES:-60}" \
    --vlm-max-fps "${VLM_MAX_FPS:-2}" \
    --log-level ERROR \
    "$@"
  if [ "$ENABLE_AUDIO_LC" = "true" ]; then
    set -- --vst-clip-enable-audio "$@"
  else
    set -- --no-vst-clip-enable-audio "$@"
  fi
  ENABLE_CRITIC_LC=$(printf "%s" "${ENABLE_CRITIC:-true}" | tr "[:upper:]" "[:lower:]")
  if [ "$ENABLE_CRITIC_LC" = "false" ] || [ -z "$VLM_BASE" ] || [ -z "${VLM_NAME:-}" ]; then
    set -- --no-enable-critic "$@"
  else
    set -- --enable-critic \
      --vlm-base-url "$VLM_BASE" \
      --vlm-model "$VLM_NAME" \
      --vlm-media-mode "$MEDIA_MODE" \
      --vlm-video-url-scope internal \
      "$@"
    if [ "${VLM_MODEL_TYPE:-nim}" = "openai" ] && [ -n "${OPENAI_API_KEY:-}" ]; then
      set -- --vlm-api-key "$OPENAI_API_KEY" "$@"
    elif [ -n "${NVIDIA_API_KEY:-}" ]; then
      set -- --vlm-api-key "$NVIDIA_API_KEY" "$@"
    fi
  fi
fi
search-archive "$@"
' search-archive \
  --query "find all instances of forklifts" \
  --source-type video_file | jq .
```

### More Examples

If the deployment is Kubernetes/Helm rather than Docker, use the equivalent pod exec form:

```bash
kubectl exec -i deploy/vss-agent -- sh -lc '
set -eu
EMBED_ENDPOINT="${COSMOS_EMBED_ENDPOINT:-${RTVI_EMBED_BASE_URL:-http://${HOST_IP:-localhost}:${RTVI_EMBED_PORT:-8017}}}"
CV_ENDPOINT="${RTVI_CV_BASE_URL:-http://${HOST_IP:-localhost}:${RTVI_CV_PORT:-9000}}"
VLM_BASE="${VLM_BASE_URL:-}"
if [ -n "$VLM_BASE" ]; then
  case "$VLM_BASE" in */v1|*/v1/) VLM_BASE="${VLM_BASE%/}" ;; *) VLM_BASE="${VLM_BASE%/}/v1" ;; esac
fi
ENABLE_AUDIO_LC=$(printf "%s" "${ENABLE_AUDIO:-false}" | tr "[:upper:]" "[:lower:]")
VLM_MODE_LC=$(printf "%s" "${VLM_MODE:-local}" | tr "[:upper:]" "[:lower:]")
VLM_MODEL_LC=$(printf "%s" "${VLM_NAME:-}" | tr "[:upper:]" "[:lower:]")
MEDIA_MODE=video-url
if [ "$VLM_MODE_LC" = "remote" ]; then
  case "$ENABLE_AUDIO_LC:$VLM_MODEL_LC" in
    true:*omni*) MEDIA_MODE=video-base64 ;;
    *) MEDIA_MODE=frame-base64 ;;
  esac
fi
CONFIG_FILE="${VSS_AGENT_CONFIG_FILE:-}"
if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
  set -- \
    --config "$CONFIG_FILE" \
    --config-env "HOST_IP=${HOST_IP:-localhost}" \
    --config-env "ELASTIC_SEARCH_ENDPOINT=${ELASTIC_SEARCH_ENDPOINT:-}" \
    --config-env "BEHAVIOR_ES_ENDPOINT=${BEHAVIOR_ES_ENDPOINT:-}" \
    --config-env "COSMOS_EMBED_ENDPOINT=${COSMOS_EMBED_ENDPOINT:-}" \
    --config-env "RTVI_EMBED_BASE_URL=${RTVI_EMBED_BASE_URL:-}" \
    --config-env "RTVI_EMBED_PORT=${RTVI_EMBED_PORT:-}" \
    --config-env "RTVI_EMBED_MODEL=${RTVI_EMBED_MODEL:-cosmos-embed1-448p}" \
    --config-env "RTVI_CV_BASE_URL=${RTVI_CV_BASE_URL:-}" \
    --config-env "RTVI_CV_PORT=${RTVI_CV_PORT:-}" \
    --config-env "VST_INTERNAL_URL=${VST_INTERNAL_URL:-}" \
    --config-env "VST_EXTERNAL_URL=${VST_EXTERNAL_URL:-}" \
    --config-env "ELASTIC_SEARCH_INDEX=${ELASTIC_SEARCH_INDEX:-video_embeddings}" \
    --config-env "ELASTIC_SEARCH_INDEX_WILDCARD=${ELASTIC_SEARCH_INDEX_WILDCARD:-mdx-embed-filtered-*}" \
    --config-env "BEHAVIOR_INDEX=${BEHAVIOR_INDEX:-mdx-behavior-2025-01-01}" \
    --config-env "BEHAVIOR_INDEX_WILDCARD=${BEHAVIOR_INDEX_WILDCARD:-mdx-behavior-*}" \
    --config-env "FRAMES_INDEX=${FRAMES_INDEX:-mdx-raw-2025-01-01}" \
    --config-env "FRAMES_INDEX_WILDCARD=${FRAMES_INDEX_WILDCARD:-mdx-raw-*}" \
    --config-env "ENABLE_CRITIC=${ENABLE_CRITIC:-true}" \
    --config-env "ENABLE_AUDIO=${ENABLE_AUDIO:-false}" \
    --config-env "VLM_BASE_URL=${VLM_BASE_URL:-}" \
    --config-env "VLM_NAME=${VLM_NAME:-}" \
    --config-env "VLM_MODE=${VLM_MODE:-}" \
    --config-env "VLM_MODEL_TYPE=${VLM_MODEL_TYPE:-nim}" \
    --config-env "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
    --config-env "NVIDIA_API_KEY=${NVIDIA_API_KEY:-}" \
    --config-env "CRITIC_TIME_FORMAT=${CRITIC_TIME_FORMAT:-offset}" \
    --config-env "CRITIC_EVALUATION_COUNT=${CRITIC_EVALUATION_COUNT:-5}" \
    --config-env "VLM_MAX_FRAMES=${VLM_MAX_FRAMES:-60}" \
    --config-env "VLM_MAX_FPS=${VLM_MAX_FPS:-2}" \
    --vlm-media-mode "$MEDIA_MODE" \
    --vlm-video-url-scope internal \
    --log-level ERROR \
    "$@"
else
  set -- \
    --es-endpoint "$ELASTIC_SEARCH_ENDPOINT" \
    --behavior-es-endpoint "${BEHAVIOR_ES_ENDPOINT:-$ELASTIC_SEARCH_ENDPOINT}" \
    --cosmos-embed-endpoint "$EMBED_ENDPOINT" \
    --rtvi-cv-endpoint "$CV_ENDPOINT" \
    --vst-internal-url "$VST_INTERNAL_URL" \
    --vst-external-url "$VST_EXTERNAL_URL" \
    --cosmos-embed-model "${RTVI_EMBED_MODEL:-cosmos-embed1-448p}" \
    --video-embed-index "${ELASTIC_SEARCH_INDEX:-video_embeddings}" \
    --video-embed-index-wildcard "${ELASTIC_SEARCH_INDEX_WILDCARD:-mdx-embed-filtered-*}" \
    --behavior-index "${BEHAVIOR_INDEX:-mdx-behavior-2025-01-01}" \
    --behavior-index-wildcard "${BEHAVIOR_INDEX_WILDCARD:-mdx-behavior-*}" \
    --frames-index "${FRAMES_INDEX:-mdx-raw-2025-01-01}" \
    --frames-index-wildcard "${FRAMES_INDEX_WILDCARD:-mdx-raw-*}" \
    --no-enable-frame-lookup \
    --default-max-results "${SEARCH_DEFAULT_MAX_RESULTS:-10}" \
    --embed-default-max-results "${EMBED_DEFAULT_MAX_RESULTS:-100}" \
    --search-max-iterations "${SEARCH_MAX_ITERATIONS:-1}" \
    --embed-confidence-threshold "${EMBED_CONFIDENCE_THRESHOLD:-0.1}" \
    --fusion-method "${FUSION_METHOD:-rrf}" \
    --critic-time-format "${CRITIC_TIME_FORMAT:-offset}" \
    --critic-evaluation-count "${CRITIC_EVALUATION_COUNT:-5}" \
    --vlm-max-frames "${VLM_MAX_FRAMES:-60}" \
    --vlm-max-fps "${VLM_MAX_FPS:-2}" \
    --log-level ERROR \
    "$@"
  if [ "$ENABLE_AUDIO_LC" = "true" ]; then
    set -- --vst-clip-enable-audio "$@"
  else
    set -- --no-vst-clip-enable-audio "$@"
  fi
  ENABLE_CRITIC_LC=$(printf "%s" "${ENABLE_CRITIC:-true}" | tr "[:upper:]" "[:lower:]")
  if [ "$ENABLE_CRITIC_LC" = "false" ] || [ -z "$VLM_BASE" ] || [ -z "${VLM_NAME:-}" ]; then
    set -- --no-enable-critic "$@"
  else
    set -- --enable-critic \
      --vlm-base-url "$VLM_BASE" \
      --vlm-model "$VLM_NAME" \
      --vlm-media-mode "$MEDIA_MODE" \
      --vlm-video-url-scope internal \
      "$@"
    if [ "${VLM_MODEL_TYPE:-nim}" = "openai" ] && [ -n "${OPENAI_API_KEY:-}" ]; then
      set -- --vlm-api-key "$OPENAI_API_KEY" "$@"
    elif [ -n "${NVIDIA_API_KEY:-}" ]; then
      set -- --vlm-api-key "$NVIDIA_API_KEY" "$@"
    fi
  fi
fi
search-archive "$@"
' search-archive \
  --query "find all instances of forklifts" \
  --source-type video_file | jq .
```

Use explicit flags instead of prose-only control hints:

#### Search by action
Append these query/control flags after the final `search-archive` argument in the wrapper above:

```bash
--query "show me people running" \
  --source-type video_file \
  --top-k 10
```

#### Search by time context
Append these query/control flags after the final `search-archive` argument in the wrapper above:

```bash
--query "person at the entrance" \
  --source-type video_file \
  --timestamp-start "2025-01-01T14:00:00" \
  --timestamp-end "2025-01-01T15:00:00"
```

#### Consider only RTSP sources i.e. live camera streams
Append these query/control flags after the final `search-archive` argument in the wrapper above:

```bash
--query "find all instances of forklifts" \
  --source-type rtsp
```

### Advanced control knobs

If the user query is ambiguous, user wants more guidance, or fine-grained control is needed, translate user intent into explicit CLI flags. Available control axes:

| Axes                 | Type      | Default | Description                                               |
|----------------------|-----------|---------|-----------------------------------------------------------|
| `--config` + `--config-env KEY=VALUE` | path + repeatable mapping | deployment-derived | Prefer inside deployed agent containers/pods to preserve the NAT search profile while keeping CLI env-free |
| `--video-source`     | repeatable string | null | Filter to specific cameras or sensor names                |
| `--source-type`      | `video_file` or `rtsp` | `video_file` | Select uploaded video files or RTSP stream embeddings |
| `--top-k`            | int       | profile default | Max results |
| `--min-cosine-similarity` | float | 0.0 | Min similarity threshold; raise (e.g. 0.3) to filter noise |
| `--attribute`        | repeatable string | [] | Appearance attributes for attribute/fusion search |
| `--has-action`       | bool | null | Use `true` for action + attributes fusion; `false` for attribute-only search |
| `--description`      | string    | null    | Filter by camera metadata (e.g. location, category) if metadata is available |
| `--timestamp-start` / `--timestamp-end` | ISO-8601 datetime | null | Restrict time range |
| `--decomposed-json`  | JSON object | null | Preferred handoff from host-agent query decomposition |
| `--object-id`        | repeatable int | null | Search for objects visually similar to tracked object IDs |
| `--use-critic` / `--no-use-critic` | bool | runtime default | Require VLM critic verification or explicitly skip it for latency |
| `--vlm-media-mode`   | `video-url`, `video-base64`, `frame-base64` | deployment-derived | Local VLMs usually use URLs; remote VLMs usually use frame base64 unless audio-capable |
| `--vst-clip-enable-audio` | bool | deployment-derived | Preserve audio for VLMs that can use MP4 audio |

Pick and choose these flags as needed for the user’s situation and query. If the output has `critic_result: null`, report critic verification as skipped and use the explicit screenshot `Verification Step` for visual confirmation.
For examples of discovery modes leveraging these, see [discovery_modes.md](references/discovery_modes.md).

---

## Search via Agent UI

Open `http://${HOST_IP}:3000/` and type natural-language queries:

```
find all instances of forklifts
show me people near the loading dock
when did a truck arrive at the gate?
find someone wearing a red jacket
```

Results include timestamped clips with similarity scores.

bump:2
