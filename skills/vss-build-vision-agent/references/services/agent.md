# Agent Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Agent orchestration and REST API | `vss-agent` |
| Video-analytics MCP | `vss-va-mcp` |
| Web UI | `vss-ui` |
| Public ingress | `vss-haproxy-ingress` |
| Tracing UI | `phoenix` |

## Required peers

- `vss-agent` requires one reachable LLM and the VIOS endpoints used by its
  selected config.
- Add a VLM owner only when the config enables video understanding, critique,
  alerts, or summarization that calls it.
- Add `vss-va-mcp` only for agent configurations that use video-analytics MCP.
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
| `ENABLE_CRITIC`, `ENABLE_AUDIO` | Toggle critique and audio-aware flows. |
| `VSS_AGENT_REPORTS_BASE_URL`, `VSS_AGENT_EXTERNAL_URL` | Generate externally reachable links. |
| `VSS_UI_HOST_PORT`, `HAPROXY_HOST_PORT`, `VSS_PUBLIC_*` | Configure UI and public ingress. |

## Sources

- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/ui/compose.yml`
- `deploy/docker/services/infra/haproxy/compose.yml`
- `services/agent/README.md`
