<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vss-agent configs (dev-profile-lvs)

This directory holds the agent's NAT config files for the `lvs` profile.

## Files

| File | When loaded | Purpose |
|---|---|---|
| `config.yml` | **default** | Stock lvs profile — no knowledge retrieval, agent has only the video / lvs tools |
| `config_frag.yml` | **opt-in** | Full lvs config + the optional knowledge retrieval layer (search tool) |

NAT loads exactly one config file at startup (`nat serve --config_file <path>`).
Switching which file the agent uses is the entire opt-in mechanism — there is
no merge step.

## Enabling knowledge retrieval (the `frag` layer)

Knowledge retrieval is **off by default**. To enable it for this profile:

### 1. Point the agent at `config_frag.yml`

Edit `deploy/docker/developer-profiles/dev-profile-lvs/.env` and change:

```bash
# Before (default — no knowledge retrieval)
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config.yml

# After (knowledge retrieval enabled)
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_frag.yml
```

### 2. Configure the backend

`config_frag.yml` defaults to **`backend: frag_api`** — talks to a deployed
NVIDIA RAG Blueprint over HTTP. Set the env vars the agent will read at boot:

```bash
RAG_SERVER_URL=http://<your-rag-server>:8081/v1
KNOWLEDGE_COLLECTION=<your-collection-name>
RAG_API_KEY=<optional bearer token>
```

### 3. Deploy as usual

```bash
./deploy/docker/scripts/dev-profile.sh up -p lvs -H <hardware>
```

Verify the tool registered:

```bash
docker logs vss-agent 2>&1 | grep "knowledge_retrieval ready"
```

## Switching backends

Two supported backends; edit `functions.knowledge_retrieval` in `config_frag.yml`:

### `frag_api` — HTTP to a deployed rag-server (default)

```yaml
backend: frag_api
rag_url: ${RAG_SERVER_URL:-http://rag-server:8081/v1}
api_key: ${RAG_API_KEY:-}
verify_ssl: true
timeout: 300
```

### `frag_lib` — in-process via `nvidia-rag>=2.4.0`

```yaml
backend: frag_lib
llm: ${LLM_MODEL_TYPE:-nim}_llm
embedder: nim_embedder
milvus_uri: ${MILVUS_URI:-http://milvus:19530}
reranker_top_k: 10
enable_citations: true
enable_guardrails: false
```

**Lazy runtime install**: `nvidia-rag>=2.4.0` is **not pre-installed** in the
agent image. The first time the `frag_lib` adapter is constructed (i.e., the
first agent boot with `backend: frag_lib`), it installs the package via
`pip install nvidia-rag>=2.4.0` from inside the running container. This takes
2-5 minutes and requires outbound network access to the NVIDIA pypi index.
The install is in the container's writable layer and lost on
`docker compose down` — it re-installs on the next `up`.

If the agent's healthcheck `start_period` (default 240s) is too short for
the install, bump it to `600s` when running `frag_lib`. For hermetic builds
with no runtime install, move `nvidia-rag>=2.4.0` from
`[project.optional-dependencies]` to main `dependencies` in `pyproject.toml`
and rebuild the image.

## LVS-specific routing notes

The `prompt` in `config_frag.yml` includes routing rules telling the agent to
prefer `knowledge_retrieval` for questions about ingested documents (SOPs,
manuals, policies) and to keep using `lvs_video_understanding` for video Q&A.
A future ticket may swap or augment the backend with an
`es_captions`-style adapter that reads dense captions out of Elasticsearch
for video-content Q&A — that's separate work and not in this fragment.

## Drift policy

`config_frag.yml` is a full copy of `config.yml` with the knowledge layer
added. When `config.yml` changes upstream, mirror the same edits into
`config_frag.yml`. The localized additions are:

- the `embedders:` section (new top-level key)
- the `knowledge_retrieval` entry under `functions:`
- a trailing `knowledge_retrieval` in `workflow.tool_names`
- a trailing block in `workflow.prompt` (after a blank line)
