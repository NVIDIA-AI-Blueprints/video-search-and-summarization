# Agent Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Agent orchestration and REST API | `vss-agent` |
| Video-analytics MCP | `vss-va-mcp` |
| Web UI | `vss-ui` |
| Tracing UI | `phoenix` |

## Access role

This owner backs the interactive surface: conversational orchestration and REST
API, video-analytics MCP, Web UI, and tracing. Its public front door is a
separate owner (`ingress.md`), so browse ingress can be requested without this
tier. None of these keys sit on the ingest, detection, embedding, indexing, or
service-native (Kibana/REST) browse path, and no write-path service depends on
them. A headless, ingestion-only, or service-native-browse request reaches none
of them, so the whole owner (and the LLM peer only `vss-agent` required) is
pruned as unreachable.

## Required peers

- `vss-agent` requires one reachable LLM and the VIOS endpoints used by its
  selected config.
- Add a VLM owner only when the config enables video understanding, critique,
  alerts, or summarization that calls it.
- Add `vss-va-mcp` only for agent configurations that use video-analytics MCP.
- When the interactive surface must be reachable through a single public origin,
  add the Ingress owner (`vss-haproxy-ingress`, `ingress.md`) as the front door.
- ELK, Alerts, Search, and LVS peers are capability-dependent, not universal
  Agent dependencies.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_AGENT_CONFIG_FILE` | Select the checked-in agent config for the Foundation. |
| `VSS_AGENT_HOST_PORT`, `VSS_AGENT_PORT` | Publish and bind the agent API. |
| `VSS_VA_MCP_HOST_PORT`, `VSS_VA_MCP_PORT`, `VSS_VA_MCP_CONFIG_FILE` | Configure the optional analytics MCP. |
| `LLM_MODE`, `LLM_MODEL_TYPE`, `LLM_NAME`, `LLM_BASE_URL` | Configure the agent's LLM. |
| `VLM_MODE`, `VLM_MODEL_TYPE`, `VLM_NAME`, `VLM_BASE_URL`, `RTVI_VLM_BASE_URL` | Configure the agent's VLM. |
| `VST_BASE_URL`, `VST_INTERNAL_URL`, `VST_EXTERNAL_URL`, `VST_MCP_URL` | Configure video storage access. |
| `LVS_BACKEND_URL`, `COSMOS_EMBED_ENDPOINT`, `ELASTIC_SEARCH_ENDPOINT`, `ALERT_BRIDGE_URL` | Enable capability-specific tools. |
| `ENABLE_AUDIO` | Enable audio-aware flows. (Result critique has no build-time flag; it is chosen per request via the agent's `use_critic` option.) |
| `VSS_AGENT_REPORTS_BASE_URL`, `VSS_AGENT_EXTERNAL_URL` | Generate externally reachable links. |
| `VSS_UI_HOST_PORT` | Publish the Web UI (public-ingress knobs live with the Ingress owner, `ingress.md`). |

## Sources

- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/ui/compose.yml`
- `services/agent/README.md`
