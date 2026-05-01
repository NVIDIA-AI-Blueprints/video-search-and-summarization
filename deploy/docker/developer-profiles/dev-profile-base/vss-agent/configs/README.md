<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vss-agent configs (dev-profile-base)

| File | Purpose |
|---|---|
| `config.yml` | Default — no knowledge retrieval |
| `config_frag.yml` | Adds the `knowledge_retrieval` tool |

## Enable knowledge retrieval

**1.** In `dev-profile-base/.env`:

```bash
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_frag.yml
```

**2.** Configure the backend (see below).

**3.** Deploy:

```bash
./deploy/docker/scripts/dev-profile.sh up -p base -H <hardware>
```

Verify:

```bash
docker logs vss-agent | grep "knowledge_retrieval ready"
```

## Backend: `frag_api` — HTTP to a deployed rag-server

Set in `.env`:

```bash
RAG_SERVER_URL=http://<rag-server>:8081/v1
KNOWLEDGE_COLLECTION=<collection-name>
RAG_API_KEY=<optional bearer token>
```

Add to the `environment:` block of
[`services/agent/vss-agent-docker-compose.yml`](../../../../services/agent/vss-agent-docker-compose.yml):

```yaml
RAG_SERVER_URL: ${RAG_SERVER_URL:-}
KNOWLEDGE_COLLECTION: ${KNOWLEDGE_COLLECTION:-}
RAG_API_KEY: ${RAG_API_KEY:-}
```

Deploy the
[NVIDIA RAG Blueprint](https://github.com/NVIDIA-AI-Blueprints/rag)
separately and point `RAG_SERVER_URL` at it.

## Revert

Set `VSS_AGENT_CONFIG_FILE` back to `config.yml` and redeploy.
