---
name: knowledge-retrieval
description: Enable the VSS agent's `knowledge_retrieval` tool — a retrieval layer that grounds agent answers in indexed knowledge sources (SOPs, manuals, ingested documents) by fetching cited excerpts from a deployed NVIDIA RAG Blueprint rag-server. Two pre-baked configs ship for the `base` and `lvs` dev profiles; for any other profile, extend that profile's `config.yml` with the three blocks shown below and save as `config_rag.yml`. Use when asked to ground agent responses in indexed documents, point VSS at a RAG server, or enable knowledge retrieval on a profile that doesn't ship a pre-baked config.
version: "3.2.0"
license: "Apache License 2.0"
---

# Knowledge Retrieval

Opt-in tool that adds `knowledge_retrieval` to the VSS agent. Retrieves cited excerpts from a deployed NVIDIA RAG Blueprint rag-server and lets the agent ground compliance/SOP/policy answers in source material.

## When to Use

- "Ground the agent in our SOPs / manuals / ingested documents"
- "Point VSS at our deployed RAG server"
- "Enable knowledge retrieval on the `<profile>` profile"

---

## Quick start (`base` or `lvs` profile)

A pre-baked `config_rag.yml` ships for both:

- [`deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_rag.yml`](../../deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config_rag.yml)
- [`deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml`](../../deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config_rag.yml)

**1.** In the profile's `.env`, point the agent at the RAG config:

```bash
VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-<base|lvs>/vss-agent/configs/config_rag.yml
```

**2.** Set rag-server connection vars in the same `.env`:

```bash
RAG_SERVER_URL=http://<rag-server-host>:8081/v1
KNOWLEDGE_COLLECTION=<collection-name>
RAG_API_KEY=<optional bearer token>      # leave empty if the server is open
```

(Deploy the [NVIDIA RAG Blueprint](https://github.com/NVIDIA-AI-Blueprints/rag) separately and point `RAG_SERVER_URL` at it.)

**3.** Deploy:

```bash
./deploy/docker/scripts/dev-profile.sh up -p <base|lvs> -H <hardware>
```

**4.** Verify the tool registered:

```bash
docker logs vss-agent 2>&1 | grep "knowledge_retrieval ready"
# knowledge_retrieval ready: backend=frag_api, default_collection=…, default_top_k=5, …
```

Then ask the agent a knowledge-base question:

```bash
curl -s -X POST "http://${HOST_IP}:8000/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_message": "What does our SOP say about lockout/tagout?"}' | jq .
```

---

## Adapt to any other profile

For profiles without a pre-baked `config_rag.yml` (e.g. `search`, `alerts`, …), copy that profile's `config.yml` to `config_rag.yml` in the same directory and apply these three patches. Then follow the Quick start steps with the new file.

### Patch 1 — register the tool under `functions:`

Add the block to the existing `functions:` map (location: anywhere inside `functions:`, e.g. just before `llms:`):

```yaml
  knowledge_retrieval:
    _type: knowledge_retrieval
    backend: frag_api
    collection_name: ${KNOWLEDGE_COLLECTION:-default}
    top_k: 5
    # Backend-specific (validated against FragApiConfig at boot):
    rag_url: ${RAG_SERVER_URL:-http://rag-server:8081/v1}
    api_key: ${RAG_API_KEY:-}
    verify_ssl: true
    timeout: 300
```

### Patch 2 — expose it to the workflow

Append `knowledge_retrieval` to the workflow's `tool_names:` list:

```yaml
workflow:
  _type: top_agent
  ...
  tool_names:
  - video_understanding
  - vst_video_clip
  - vst_snapshot
  - vst_video_list
  - knowledge_retrieval        # add this
```

### Patch 3 — teach the routing agent when to call it

Add a routing rule inside the workflow's `prompt:` block (under `## Routing Rules:`, before `## Context:`):

```yaml
      **knowledge_retrieval** - For questions about ingested documents (SOPs, manuals, policies, safety rules).
      - Call FIRST for compliance/rule/procedure questions, then chain into a video tool if needed.
      - Skip for operational queries (list/show videos, snapshots, reports).
      - Pass `filters` only when the user names an exact filename; never invent one.
```

Save as `config_rag.yml` next to that profile's `config.yml`, then run the Quick start steps with `-p <profile>`.

---

## Revert

In the profile's `.env`, set `VSS_AGENT_CONFIG_FILE` back to the profile's `config.yml` and redeploy. The tool is opt-in — `config.yml` does not register it.
