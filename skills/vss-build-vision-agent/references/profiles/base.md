# Base Developer Profile

## Capabilities and routing cues

- General VSS agent, UI, video ingest/storage, LLM, and integrated RT-VLM.
- Choose for "deploy VSS", general video understanding, or the smallest
  developer foundation.
- Prefer as the Foundation when the request needs VIOS + inference but no
  ELK-backed alerts, search, or long-video summarization.
- The Agent and UI layer is **optional in a delta** — see
  [Optional Agent/UI layer](#optional-agentui-layer) before assuming a request
  for "Q&A" requires it.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-base/overrides.env`.

```text
phoenix,redis,vss-haproxy-ingress,vss-ui,vss-agent,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG},rtvi-vlm
```

## Capability owners present

| Owner | Service profile keys | Required in a delta? |
|---|---|---|
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` | **Optional** — only when the user wants to chat through the VSS UI or needs Agent orchestration |
| VIOS | `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` | Required for video ingest, storage, and retrieval |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` | **Optional** — consumed by `vss-agent`; drop it whenever the Agent is dropped |
| RT-VLM | `rtvi-vlm` | Required for captioning and VLM inference |

`redis` is a shared peer used by this profile graph.

## Optional Agent/UI layer

`vss-agent`, `vss-ui`, `vss-haproxy-ingress`, and `phoenix` exist to provide the
**VSS web experience**: a browser UI for uploading and streaming video, and an
Agent that orchestrates multi-step reasoning across the LLM, RT-VLM, and VIOS.
Keep them only when the request actually asks for that experience.

Do **not** assume a request for "VLM Q&A" or "ask questions about video" needs
them. RT-VLM serves the full capability surface itself on `RTVI_VLM_PORT`
(default 8018):

| Capability | RT-VLM endpoint |
|---|---|
| VLM Q&A | `POST /v1/chat/completions` |
| Dense captioning (VOD) | `POST /v1/files` then `POST /v1/generate_captions` |
| Dense captioning (stream) | `POST /v1/streams/add` then `POST /v1/generate_captions` |

Nothing in `deploy/docker/` declares a `depends_on` for `vss-agent`, so removing
the Agent layer does not orphan VIOS, RT-VLM, or any broker/ELK service.

**Keep the Agent layer when** the user asks for the VSS UI, a browser/chat
experience, Agent-orchestrated reasoning that spans several services, or
Phoenix tracing of Agent calls.

**Drop all four when** the deployment is API-only — a microservice or pipeline
delta driven by direct REST calls to RT-VLM and VIOS. Drop the LLM NIM at the
same time: `vss-agent` is its only consumer in this profile, so retaining it
reserves a GPU for a service nothing calls.

When the request is ambiguous between the two, ask which the user wants rather
than silently retaining the heavier set.

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `HARDWARE_PROFILE` | Select current hardware defaults. |
| `LLM_MODE`, `LLM_NAME`, `LLM_NAME_SLUG`, `LLM_DEVICE_ID`, `LLM_BASE_URL` | Select and place the LLM. |
| `VLM_MODE`, `VLM_NAME`, `RT_VLM_DEVICE_ID`, `VLM_BASE_URL` | Select and place integrated or remote VLM inference. |
| `RTVI_VLM_KAFKA_ENABLED` | Keep `false` unless a broker is added. |
| `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, `EXTERNAL_IP` | Resolve repository, data, and network paths. |
| `HAPROXY_HOST_PORT`, `VSS_PUBLIC_*`, `VSS_*_HOST_PORT` | Change public ingress or host port bindings. |

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:8000/health"                        # vss-agent
curl -sf "http://${HOST_IP}:8018/v1/health/ready"               # rtvi-vlm
curl -sf "http://${HOST_IP}:${LLM_PORT:-30081}/v1/health/ready" # LLM NIM
curl -sf "http://${HOST_IP}:3000/"                              # vss-ui
```

In a delta that drops the Agent layer, skip the `:8000` and `:3000` probes and
the LLM NIM probe — those services are absent by design, and probing them
reports a false failure. `:8018` is the only readiness check that applies to
every base-derived delta.

For remote LLM/VLM mode, probe the selected remote `/v1/models` endpoint
instead of the absent local service.

## Sources

- `deploy/docker/developer-profiles/dev-profile-base/.env`
- `deploy/docker/developer-profiles/dev-profile-base/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-base/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `deploy/docker/services/vios/compose.yml`
