---
name: vss-search-archive
description: Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. Do not use it for visual Q&A, live captioning, or video summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.4.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

## Purpose

Run NAT-free archive search from the host machine with `vss-cli`, and retain the
agent-backed source ingestion and deletion lifecycle. The `nvidia-vss-cli`
distribution exports `lib.*` for Python callers and provides the `vss-cli`
console executable. Search never calls the VSS agent `/generate` route and never
performs NAT query decomposition.

## Prerequisites

- A running VSS search deployment and a checkout containing `services/agent/vss-cli`.
- Host `uv`, plus Docker access for Docker deployments or `kubectl` access to
  Deployments, ConfigMaps, Services, Endpoints, Ingresses, and port-forwards for Kubernetes.
- The `vss-manage-video-io-storage` skill for source listing and inspection.
- `curl`, `jq`, and Docker available for agent-backed source ingestion or deletion.

Do not execute `vss-cli` inside a distroless VSS container or a pod. Do not
wrap it with `docker exec`, `kubectl exec`, or `sh -lc`.

## Deployment prerequisite

This skill requires the VSS **search** profile running on the host at `$HOST_IP`.
Before an agent-backed source mutation:

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

   (If the caller has granted explicit pre-authorization to deploy autonomously,
   skip confirmation and invoke `/vss-deploy-profile` directly.)

3. If the probe passes, proceed.

---

## Ingestion prerequisite

For a source to be searchable it must be ingested **through the VSS agent
backend**, not through VIOS alone. The agent's ingest routes own the VIOS upload
RTVI-CV registration + RTVI-Embed pipeline as one transaction; a bare VIOS
PUT only stores the bytes and never wires them into Elasticsearch.

Confirm the source exists in VIOS first (Mandatory workflow step 2). If it is
missing, ingest it with one of the recipes below before running `vss-cli search
run`. After ingestion succeeds, the source appears in `sensor/list` under the
name you provided and can be selected with `--video-source`.

### File upload — universal three-step flow

Use the timestamped upload form below. The VSS agent/search profile uses
`2025-01-01T00:00:00.000Z` as the uploaded `video_file` base timestamp; VIOS
storage and embeddings must share that timeline, otherwise screenshot URLs and
critic frame fetches can fail.

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

# 3. Tell the agent the upload finished — this fans out to RTVI-CV + RTVI-Embed
SENSOR=$(printf '%s' "${UPLOAD_RESPONSE}" | jq -r .sensorId)
[ -z "${SENSOR}" ] || [ "${SENSOR}" = "null" ] \
  && { echo "Upload failed: no sensorId in response: ${UPLOAD_RESPONSE}"; exit 1; }
printf '%s' "${UPLOAD_RESPONSE}" \
  | jq --arg filename "${FILENAME}" '. + {filename: $filename}' \
  | curl -s -X POST "http://${HOST_IP}:8000/api/v1/videos/${SENSOR}/complete" \
      -H "Content-Type: application/json" \
      -d @- | jq .
```

Wait for the `/complete` response (it returns `chunks_processed > 0` once
embeddings land). Only then is the video searchable.

> The deprecated `PUT /api/v1/videos-for-search/{filename}` route is also wired
> in for legacy callers (single-shot, agent-driven), but its OpenAPI entry is
> flagged `deprecated`. Prefer the three-step flow above for new work.

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

The response shape is `{status, message, error}` — no `sensorId` (the agent
keys the stream by the `name` you provided). On any step's failure earlier steps
roll back. The `start_embedding_generation` step is fire-and-verify: a 2xx
confirms the request was accepted and the embedding pipeline is running in the
background, **not** that the stream is searchable yet. Search hits start
appearing only after enough chunks land in Elasticsearch.

### Delete source — agent-backed cleanup

Delete through the agent backend, not bare VIOS, so VIOS storage and search
embeddings are cleaned up together.

```bash
# For video files: video_id is the VIOS sensor/video UUID
curl -s -X DELETE "http://${HOST_IP}:8000/api/v1/videos/<video_id>" | jq .

# For RTSP streams: name is the registered source name
curl -s -X DELETE "http://${HOST_IP}:8000/api/v1/rtsp-streams/delete/<name>" | jq .
```

---

## Mandatory workflow

1. Confirm this is the **search** profile. If it is unavailable, ask whether
   the user wants it deployed; do not target an unrelated profile.
2. If the user names a file, camera, or sensor, list registered sources using
   `vss-manage-video-io-storage` before searching. Accept an exact source,
   stream ID, or one unambiguous normalized substring match only.

   - If there is no match, stop. Report the registered names and ask whether
     the user meant one of them, wants to ingest the named source using the
     agent-backed workflow above, or wants an unrestricted search.
   - If several sources match, stop and ask the user to choose.
   - Never remove a requested source constraint, substitute a different video,
     or run a broad search as a probe.

3. Decompose the request into explicit fields using
   [Query decomposition](references/query_decomposition.md). The CLI does not
   decompose natural language. Preserve the requested object/action and use
   `--query`, `--attribute`, `--has-action`, `--video-source`, time bounds, and
   `--use-critic` as appropriate.
4. Run the host command for the selected deployment. It validates named
   sources again against that deployment's VST listing before querying ES.
5. Present an inspection report titled `Video Search Results`, not raw JSON.
   For every hit, copy the selected source, start/end timestamps, similarity,
   and screenshot/clip URL verbatim from CLI output. Never invent or normalize
   evidence. Show critic status and every returned criterion (`✓`/`✗`); mark a
   null critic result as skipped.
6. End with a `Verification Step`. Offer to download and visually inspect the
   returned screenshots, but do not download them without user opt-in or prior
   authorization. When authorized, inspect the pixels and report a grounded
   confirmed/rejected/uncertain verdict for each inspected hit.
7. If the result set is empty, say that no matches were found. Keep all source
   constraints, explain that the object may be absent or the query too narrow,
   and offer a specific query or similarity-threshold refinement. Never broaden
   the search silently or fabricate a result.

8. For source ingestion or deletion, use the agent-backed flows above. Never
   substitute a bare VIOS or Elasticsearch mutation.

## Host CLI

Always invoke the checked-out `services/agent/vss-cli` project with `uv run`:

```bash
uv run --project services/agent/vss-cli vss-cli search run [deployment options] [query options]
```

Direct low-level invocation remains environment-free. Use explicit runtime
flags or `--config` with explicit `--config-env KEY=VALUE` values only when a
deployment selector is not appropriate. The CLI never reads endpoint variables
from the host process.

### Docker

Docker requires a deployed profile's generated file, not its checked-in `.env`.
The command reads `deploy/docker/developer-profiles/dev-profile-<profile>/generated.env`
and that profile's checked-out agent config. It translates Compose-only service
DNS to the loopback ports published for Elasticsearch, RTVI-Embed, RTVI-CV,
and VST.

```bash
uv run --project services/agent/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "find all instances of forklifts" \
  --source-type video_file --top-k 10 --no-use-critic
```

Before running this, start the profile with `dev-profile.sh` so `generated.env`
exists. Do not use a checked-in `.env` as a substitute. Private service ports
are loopback-only; do not expose them to a LAN simply to run a search.

### Kubernetes / Helm

Kubernetes has no `generated.env`. The command obtains non-secret state from:

1. the live vss-agent Deployment, to find its config mount and literal or
   ConfigMap-backed environment values;
2. the mounted ConfigMap's `config.yml`;
3. referenced ConfigMaps for the runtime-key allowlist only.

It never reads `secretKeyRef` values, Secrets, checked-in `values.yaml`, or the
agent runtime endpoint. It rewrites private backend Service URLs to managed
localhost `kubectl port-forward` connections and closes every managed forward
on success, failure, or interruption. `VST_EXTERNAL_URL` is the exception: it
is returned in screenshots and media links that must outlive the CLI process,
so it must already be host-reachable or use an operator-managed localhost
forward whose lifetime extends through result consumption. The CLI rejects an
in-cluster Service URL in that external field instead of returning dead links.

```bash
uv run --project services/agent/vss-cli vss-cli search run \
  --deployment kubernetes --namespace <namespace> --release <release> \
  --kube-context <optional-context> \
  --query "person in a white jacket climbing a ladder" \
  --attribute "white jacket" --has-action true \
  --video-source <resolved-source> --top-k 10 --no-use-critic
```

If a required runtime value is Secret-backed or absent from the Deployment and
its non-secret ConfigMaps, stop. Do not print or pass a secret. Use an explicit
non-secret endpoint override when valid, or route authenticated VLM work
through the operator-managed workflow.

## Search behavior and safeguards

- `ELASTIC_SEARCH_INDEX` wins whenever the deployment provides it. The only
  fallback is `mdx-embed-filtered-2025-01-01`, never `video_embeddings`.
  Missing indexes fail with nearby MDX index diagnostics; ingest video before
  retrying.
- The configured Cosmos/RTVI Embed model is verified through `/v1/models`.
  The CLI never guesses a replacement model ID. Choose one explicitly from the
  reported deployed IDs if the configured model is unavailable.
- Attribute/fusion search performs a short RTVI-CV text-embedding capability
  preflight. It fails by default rather than hanging or silently changing the
  search. `--allow-embed-only-fallback` is the only opt-in way to remove
  attributes and continue as embed-only search.
- Result object IDs that are missing or `unknown` are not merged together.
- Always make critic intent explicit. Use `--no-use-critic` for ordinary host
  searches. Use `--use-critic` only when the deployment exposes an
  operator-reachable, unauthenticated VLM or an approved operator-managed
  authenticated workflow has already been established. This skill never reads
  or copies an API key. Requested critic configuration errors are fatal; only
  transient backend failures degrade to unverified results.
- Deployment-discovered critic calls default to frame-base64 media so remote or
  containerized VLMs never receive a host-loopback VST URL. Use video-url only
  when the selected VLM can demonstrably reach the chosen VST URL.

## Query examples

```bash
# Embed-only search across all ingested files
uv run --project services/agent/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "red forklift near a loading bay" --source-type video_file \
  --no-use-critic

# Attribute-only search; source must have been resolved first
uv run --project services/agent/vss-cli vss-cli search run \
  --deployment kubernetes --namespace vss --release search \
  --query "person wearing a white jacket" \
  --attribute "white jacket" --has-action false \
  --video-source warehouse-camera-3 --no-use-critic

# Deliberate fallback when a deployment has no RTVI-CV text endpoint
uv run --project services/agent/vss-cli vss-cli search run \
  --deployment docker --profile search \
  --query "forklift near a loading bay" --attribute "yellow forklift" \
  --has-action true --allow-embed-only-fallback --no-use-critic
```

## Troubleshooting

- **`generated.env` missing**: start the selected Docker profile with
  `deploy/docker/scripts/dev-profile.sh`; do not fall back to `.env`.
- **Kubernetes ConfigMap/port-forward error**: verify read and port-forward
  RBAC in the selected namespace. Do not use a pod shell as a workaround.
- **Kubernetes `VST_EXTERNAL_URL` is Service-backed**: configure a durable
  external ingress URL or an operator-managed localhost forward. The CLI does
  not create a short-lived managed forward for result URLs.
- **Source unavailable or ambiguous**: stop and clarify; do not substitute.
- **Zero results**: report the empty outcome, retain the selected source, and
  offer an explicit query or similarity-threshold refinement. Run a broader
  search only after the user accepts it.
- **Missing index**: verify ingestion completion and the deployed
  `ELASTIC_SEARCH_INDEX` value.
- **Model preflight failure**: pass an explicit deployed model ID after
  reviewing the reported list.
- **RTVI-CV preflight failure**: repair the service or use the explicit
  `--allow-embed-only-fallback` option only when an embed-only result is
  acceptable.
- **Critic needs authentication**: stop and use the secret-managed operator
  path. Never copy API keys into CLI flags, generated files, logs, or skill
  output.
