<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vss-agent configs (dev-profile-base)

This directory holds the agent's NAT config files for the `base` profile.

## Files

| File | When loaded | Purpose |
|---|---|---|
| `config.yml` | **default** | Stock base profile — no knowledge retrieval, agent has only the video tools |
| `config_frag.yml` | **opt-in** | Full base config + the optional knowledge retrieval layer (search tool) |
| `config_edge.yml` | (unused today) | Reserved; legacy edge-mode template |

NAT loads exactly one config file at startup (`nat serve --config_file <path>`).
Switching which file the agent uses is the entire opt-in mechanism — there is
no merge step.

## Enabling knowledge retrieval (the `frag` layer)

Knowledge retrieval is **off by default**. To enable it for this profile:

### 1. Point the agent at `config_frag.yml`

Edit `deploy/docker/developer-profiles/dev-profile-base/.env` and change:

```bash
# Before (default — no knowledge retrieval)
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml

# After (knowledge retrieval enabled)
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_frag.yml
```

### 2. Configure the backend

`config_frag.yml` defaults to **`backend: frag_api`** — talks to a deployed
NVIDIA RAG Blueprint over HTTP. Set the env vars the agent will read at boot:

```bash
# In your shell or in the profile .env:
RAG_SERVER_URL=http://<your-rag-server>:8081/v1
KNOWLEDGE_COLLECTION=<your-collection-name>      # e.g. "warehouse_safety"
RAG_API_KEY=<optional bearer token>              # if your rag-server requires auth
```

### 3. Deploy as usual

```bash
./deploy/docker/scripts/dev-profile.sh up -p base -H <hardware>
```

The agent will register the `knowledge_retrieval` tool. To verify:

```bash
docker logs vss-agent 2>&1 | grep "knowledge_retrieval ready"
# Expected: knowledge_retrieval ready: backend=frag_api, default_collection=...
```

## Switching backends

The same `config_frag.yml` supports two backends; the differences live in the
`functions.knowledge_retrieval` section.

### `frag_api` — HTTP to a deployed rag-server (default)

```yaml
functions:
  knowledge_retrieval:
    _type: knowledge_retrieval
    backend: frag_api
    rag_url: ${RAG_SERVER_URL:-http://rag-server:8081/v1}
    api_key: ${RAG_API_KEY:-}
    verify_ssl: true
    timeout: 300
```

No extra Python deps required. Operators must deploy the
[NVIDIA RAG Blueprint](https://github.com/NVIDIA-AI-Blueprints/rag) separately
and provide the URL.

### `frag_lib` — in-process via the `nvidia-rag` Python package

```yaml
functions:
  knowledge_retrieval:
    _type: knowledge_retrieval
    backend: frag_lib
    llm: ${LLM_MODEL_TYPE:-nim}_llm        # references llms.<name>
    embedder: nim_embedder                  # references embedders.nim_embedder
    milvus_uri: ${MILVUS_URI:-http://milvus:19530}
    reranker_top_k: 10
    enable_citations: true
    enable_guardrails: false
```

Requires installing the optional extra in the agent image:

```bash
pip install vss_agents[nvidia_rag]
```

This pulls in `nvidia-rag>=2.4.0` plus its dependency tree (protobuf, pyarrow,
…). Verify there are no conflicts with other extras in your build before
enabling.

## Reverting to default

To turn knowledge retrieval back off, revert `VSS_AGENT_CONFIG_FILE` to the
original value (or `git checkout` the `.env` file) and redeploy.

## Drift policy

`config_frag.yml` is a full copy of `config.yml` with the knowledge layer
added. When `config.yml` changes upstream (a tool gets renamed, the prompt
updated, etc.), mirror the same edits into `config_frag.yml`. The localized
additions are:

- the `embedders:` section (new top-level key)
- the `knowledge_retrieval` entry under `functions:`
- a trailing `knowledge_retrieval` in `workflow.tool_names`
- a trailing block in `workflow.prompt` (after a blank line)
