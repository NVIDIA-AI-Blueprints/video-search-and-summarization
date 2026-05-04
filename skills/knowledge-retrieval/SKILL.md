---
name: knowledge-retrieval
description: Enable the VSS agent's pluggable `knowledge_retrieval` tool. One YAML field selects the backend — `frag_api` (RAG Blueprint documents) or `es_caption` (LVS dense captions). Use to ground agent answers, swap backends, or wire knowledge_retrieval into a profile.
version: "3.2.0"
license: "Apache License 2.0"
---

# Knowledge Retrieval

Opt-in tool that adds `knowledge_retrieval` to the VSS agent. Backend-pluggable — a `backend:` field in `config_rag.yml` selects the retrieval implementation at boot, and each backend's filter conventions ride in the tool description so the LLM picks them up automatically.

## When to Use

- "Ground the agent in indexed sources (documents or video captions)"
- "Enable follow-up Q&A on a video we summarized with LVS without re-running the VLM"
- "Plug `knowledge_retrieval` into the `<profile>` profile"

---

## Backends shipped today

| `backend:` | Retrieves | Mainly for |
|---|---|---|
| `frag_api` | Cited excerpts from a deployed NVIDIA RAG Blueprint rag-server | SOPs, manuals, ingested PDFs — document grounding |
| `es_caption` | LVS dense captions (per-stream summaries + per-chunk events) from Elasticsearch | LVS Q&A — answer questions about already-summarized videos |

---

## How to enable (any backend, any profile)

Five steps regardless of which backend you pick:

1. **Pick the config file.** Either reuse one of the pre-baked `config_rag.yml` files for `base` / `lvs` (see [Examples](#examples) below), or copy the profile's `config.yml` to `config_rag.yml` and apply the three patches in [Adapt to any other profile](#adapt-to-any-other-profile).
2. **Set the `backend:` field** in the `knowledge_retrieval` block — one of the values in the table above.
3. **Set backend-specific fields** in the same block (see [Backend reference](#backend-reference)).
4. **Point the agent at the file**: in the profile's `.env`,
   ```bash
   VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-<profile>/vss-agent/configs/config_rag.yml
   ```
5. **Deploy and verify**:
   ```bash
   ./deploy/docker/scripts/dev-profile.sh up -p <profile> -H <hardware>
   docker logs vss-agent 2>&1 | grep "knowledge_retrieval ready"
   # knowledge_retrieval ready: backend=<chosen>, default_collection=…, default_top_k=5, …
   ```

To switch backends later, edit the `knowledge_retrieval` block to a different `backend:` value (and its fields) and redeploy. No other parts of `config_rag.yml` change.

---

## Backend reference

### `frag_api`

For document grounding via a deployed [NVIDIA RAG Blueprint](https://github.com/NVIDIA-AI-Blueprints/rag).

**Config fields** (set inline in the `knowledge_retrieval` block):

| Field | Purpose | Default |
|---|---|---|
| `rag_url` | rag-server `/v1` endpoint | env `RAG_SERVER_URL`, then `http://localhost:8081/v1` |
| `api_key` | Bearer token (optional) | env `RAG_API_KEY`, then unset |
| `verify_ssl`, `timeout` | HTTP client tuning | `true`, `300` |

**Per-query `filters`:** pass `filter_expr` only when the user names an exact filename:

```python
filters = {"filter_expr": 'content_metadata["filename"] == "<exact name>"'}
```

### `es_caption`

For LVS Q&A. Reads the same Elasticsearch instance the LVS pipeline writes to (Kafka → Logstash → ES). One ES index per stream (`default_<uuid_with_underscores>`); the adapter searches across all by default.

**Config fields:**

| Field | Purpose | Default |
|---|---|---|
| `elasticsearch_url` | ES base URL | env `ELASTIC_SEARCH_ENDPOINT`, then `http://elasticsearch:9200` |
| `index` | Index pattern to search | `default_*` |
| `default_doc_type` | Which caption shape to retrieve when callers don't override | `aggregated_summary` |
| `api_key` | ES API key (optional) | unset |
| `verify_ssl`, `timeout` | HTTP client tuning | `true`, `30` |

**`default_doc_type` values:**

| Value | Per video | Content | Use it for |
|---|---|---|---|
| `aggregated_summary` *(default)* | 1 doc | One chronological narrative — timestamps embedded in the prose | General Q&A and time-windowed Q&A; the LLM reads the prose |
| `structured_events` | 1+ docs (batched) | JSON list of merged events with `start_time`/`end_time` | Machine-readable event listings |
| `raw_events` | N docs (one per VLM chunk) | Per-chunk events JSON with NTP-float bounds at the metadata level | Exact time-window-bounded retrieval |

**Per-query `filters`:** all keys optional.

| Key | Type | Effect |
|---|---|---|
| `doc_type` | one of the values above | Override deployment default |
| `time_range` | `{"start": <s>, "end": <s>}` (either bound optional) | Range filter on `start_ntp_float` / `end_ntp_float` (overlap semantics). Only meaningful for `raw_events` |
| `camera_id` | string | Term filter on `camera_id` |
| any other key | string / number | Term filter on `content_metadata.<key>` (generic equality) |
| `es_query` | full ES query body | Escape hatch — replaces the constructed query |

**`time_range` units:** values are passed straight to ES — no auto-conversion.

| Stream type | Format | "First 1 minute" example |
|---|---|---|
| Uploaded video | Clip-relative seconds | `{"start": 0, "end": 60}` |
| RTSP stream | Unix-epoch seconds | `{"start": 1777572638, "end": 1777572698}` |

**`collection_name`:** pass the per-query stream uuid (resolved upstream via `vst_video_list`); empty searches across all streams.

---

## Examples

The repo ships pre-baked, deploy-ready configs for `base` and `lvs` (both currently using `frag_api`). Use them as a reference for what a fully-assembled `config_rag.yml` looks like — function block + workflow tool entry + routing rule, all in one file:

- [`deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_rag.yml`](../../deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_rag.yml)
- [`deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml`](../../deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml)

To switch either of those to `es_caption`, replace just the `knowledge_retrieval` function block with the `es_caption` shape (see [Backend reference](#es_caption)) and redeploy. Everything else in the file stays.

---

## Adapt to any other profile

For profiles without a pre-baked `config_rag.yml` (e.g. `search`, `alerts`, …), copy that profile's `config.yml` to `config_rag.yml` in the same directory and apply three patches.

### Patch 1 — register the tool under `functions:`

Add a `knowledge_retrieval` entry in the existing `functions:` map, with the `backend:` and backend-specific fields from [Backend reference](#backend-reference).

### Patch 2 — expose it to the workflow

Append `knowledge_retrieval` to the workflow's `tool_names:` list:

```yaml
workflow:
  ...
  tool_names:
    - <existing tools>
    - knowledge_retrieval        # add this
```

### Patch 3 — teach the routing agent when to call it

Add a routing rule inside the workflow's `prompt:` block (under `## Routing Rules:`, before `## Context:`). Phrasing depends on the chosen backend:

- **`frag_api`** — for document grounding: *"Call FIRST for compliance/rule/procedure questions; pass `filters` only when the user names an exact filename."*
- **`es_caption`** — for LVS follow-up Q&A: *"Resolve named videos to their uuid via `vst_video_list` and pass as `collection`. Default returns the timestamped narrative — answers most general and time-windowed questions directly. For per-chunk JSON in a window pass `filters={\"doc_type\": \"raw_events\", \"time_range\": {…}}`. If `chunks=[]`, fall back to `lvs_video_understanding` or `video_understanding`."*

Backend-specific filter shapes are baked into the tool description, so you don't have to repeat them all in the prompt.

Save as `config_rag.yml` next to that profile's `config.yml`, then run the [How to enable](#how-to-enable-any-backend-any-profile) steps with `-p <profile>`.

---

## Revert

In the profile's `.env`, set `VSS_AGENT_CONFIG_FILE` back to the profile's `config.yml` and redeploy. The tool is opt-in — `config.yml` does not register it.
