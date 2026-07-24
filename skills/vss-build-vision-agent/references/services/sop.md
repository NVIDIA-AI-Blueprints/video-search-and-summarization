# SOP Capability Owner

> Reference bundle for the SOP microservices, **consumed by build-vision-agent**.
> Per PR review, the whole SOP family (condensed contract + detailed contracts of
> record + net-new assets) lives under this `references/services/sop/` folder —
> unlike the flat `services/<svc>.md` files, because SOP ships **net-new assets**
> (the DS-SOP compose block and the `get_sop_*` video-analytics patch) that other
> service owners materialize from the upstream Compose tree.

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| SOP step detection + compliance (DDM-Net + Cosmos-Reason VLM + step checker) | `ds-sop` |
| SOP compliance report tools (`get_sop_status/report/as_incidents/as_incident` over Elasticsearch) | `vss-va-mcp` (+ the SOP `video_analytics` patch) |

SOP compliance **reports** are rendered by the `vss-generate-video-report` skill
(Mode C) from `get_sop_report`. There is **no** report service, VSS agent, web UI,
or report LLM in this integration.

## Required peers

- **ELK** — Elasticsearch (`get_sop_*` reads `mdx-vlm-captions-*`), Logstash (the SOP
  JSON pipeline `sop/sop-vlm-captions-json-logstash.conf` — a mandatory deploy-time step,
  the stock `mdx-lvs` pipeline decodes this topic as protobuf), Kibana. See `services/elk.md`.
- **Kafka** — DS-SOP publishes per-chunk SOP JSON on `DEFAULT_TOPIC=mdx-vlm-captions`.
- **VIOS** — records DS-SOP's annotated `:8554/ds-out` RTSP output. See `services/vios.md`.
- DS-SOP occupies the perception slot — **do not also select RT-VLM** (both target
  `mdx-vlm-captions` and the GPU).
- The report tools **reuse the existing `vss-va-mcp`** service (self-named key
  `vss-va-mcp`); they add only the `get_sop_*` patch mount, no new service.

## Delta composition

SOP is not a Foundation; compose it as a **delta** off the closest Foundation —
`alerts` in `2d_vlm` mode (it already carries `vss-va-mcp`, Elasticsearch, Kafka,
Redis, VIOS, and a VLM-perception slot). Against alerts `COMPOSE_PROFILES_VLM`:

- **add** `ds-sop` — a genuinely new service → emit `patches/ds-sop.yml` from the
  DS-SOP Compose block in `sop/deploy-ds-sop.md § Example Compose Snippet`; and
  `sop-kibana-init` (SOP data-view + dashboard one-shot).
- **remove** `rtvi-vlm` (DS-SOP takes the perception slot), and the parts SOP does
  not use: `vss-agent`, `vss-ui`, `alert-bridge`, `vss-video-analytics-api-alerts`,
  `vss-behavior-analytics-alerts`, `perception-alerts`, `kibana-init-container-alerts`,
  and the `llm_${LLM_MODE}_${LLM_NAME_SLUG}` token (SOP reports are rendered by the
  `vss-generate-video-report` skill — no report LLM / agent / UI).
- **keep** `vss-va-mcp`, ELK (`elasticsearch`(+init), `logstash`, `kibana`,
  `kafka`(+init), `redis`, `broker-health-check`), the VIOS set (`vst-ingress`,
  `sensor-ms`, `streamprocessing-ms`, `sdr-controller` + its `init-dirs` /
  `render-config` / `wdm-env-from-config` / `wait-for-redis` /
  `wait-for-docker-workloads` helpers, `centralizedb`), `nvstreamer-alerts`
  (validation-harness source), `vss-haproxy-ingress`, `phoenix`.
- **change** the `vss-va-mcp` definition to mount the SOP `video_analytics` patch
  (`patches/vss-va-mcp.yml` — an added bind mount env interpolation cannot express,
  per `composition.md § Artifact contract`).

Two deploy-time steps the Compose patch cannot cover — the SOP JSON Logstash
pipeline and the DS-SOP → VIOS recording wiring — are in `sop/deploy-ds-sop.md`.

### Example `_builds/<name>/override.env`

```bash
FOUNDATION=alerts

# Effective service set = alerts 2d_vlm − rtvi-vlm/agent/ui/alert-bridge/report-LLM/
# alerts-extras + ds-sop + sop-kibana-init. Confirm each key against the resolved
# root graph (composition.md § Validate).
COMPOSE_PROFILES=ds-sop,sop-kibana-init,vss-va-mcp,nvstreamer-alerts,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,phoenix

# --- DS-SOP knobs (see deploy-ds-sop.md) ---
DS_SOP_IMAGE=ds-sop:1.0.0
ENABLE_MESSAGING=1
DEFAULT_TOPIC=mdx-vlm-captions
SOP_MESSAGING_SCHEMA=JSON
ENABLE_RTSP_OUTPUT=true
RTSP_PORT=8554
SW_ENCODER=true
VLLM_GPU_MEMORY_UTILIZATION=0.6            # 0.3 is fine on ≥80 GB GPUs

# --- SOP report tools (vss-va-mcp patch, see § Patch specifics) ---
# VSS_VA_MCP_CONFIG_FILE → the SOP va_mcp config staged into the container (selects
#   get_sop_*). VSS_AGENT_SITE_PACKAGES → mount root; verify vs the ghcr.io image.
VSS_VA_MCP_CONFIG_FILE=<staged: adapted from the downloaded SOP release VA-MCP config — see § Patch specifics>
VSS_AGENT_SITE_PACKAGES=/vss-agent/.venv/lib/python3.13/site-packages
```

Do not pin the `vss-va-mcp` image here — it comes from the Foundation (stock
`ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest`). The `patches/` files
(`ds-sop.yml`, `vss-va-mcp.yml`) are generated into `_builds/<name>/`, gitignored.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `DS_SOP_IMAGE` | DS-SOP image (`ds-sop:1.0.0`, built locally — see `sop/build-ds-sop.md`; must be built before deploy). |
| `ENABLE_MESSAGING`, `DEFAULT_TOPIC`, `SOP_MESSAGING_SCHEMA` | Kafka publication (`1` / `mdx-vlm-captions` / `JSON`). |
| `ENABLE_RTSP_OUTPUT`, `RTSP_PORT`, `SW_ENCODER` | Annotated RTSP output that VIOS records. |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.6` on ≤48 GB GPUs (the `0.3` default is H100-tuned). |
| `VSS_VA_MCP_CONFIG_FILE` | SOP VA-MCP config (adapted from the downloaded SOP release config — see § Patch specifics; selects the `get_sop_*` tools). |
| `VSS_AGENT_SITE_PACKAGES` | In-container site-packages root the SOP patch mounts over. |

## Patch specifics — download the SOP release patch, then adapt it

The `get_sop_*` tools are **not shipped in this repo**. They live in NVIDIA's **public**
`sop-monitoring-blueprints` repo (Apache-2.0); the agent **downloads** them at build time
and adapts them to the running `vss-va-mcp` image. Because they come straight from NVIDIA's
own public repo, **no license grant or OSRB is needed** — nothing third-party is vendored here.

**Source — download at build time:**
- Repo: `https://github.com/NVIDIA/sop-monitoring-blueprints` (public, branch `main`, pin `0dd472f`)
- Path: `agentic/vss-sop-skills/vss-sop-build/references/deployments/sop/vss-agent/`
  - `patches/tools.py` — the release's **3.1 monolith** `video_analytics/tools.py` (stock
    tools + the SOP additions). `patches/{es_client.py,utils.py}` are stock — the image
    already ships them, so no download needed.
  - `configs/va_mcp_server_config.yml` — release VA-MCP config (selects `get_sop_*`).

`sop/build-ds-sop.md` § Step 0 already clones this same repo — reuse that clone, or fetch
just the two files:
```bash
REPO=https://raw.githubusercontent.com/NVIDIA/sop-monitoring-blueprints/0dd472f
BASE=agentic/vss-sop-skills/vss-sop-build/references/deployments/sop/vss-agent
curl -fsSL $REPO/$BASE/patches/tools.py                 -o /tmp/sop-release-tools.py
curl -fsSL $REPO/$BASE/configs/va_mcp_server_config.yml -o /tmp/sop-release-va-mcp.yml
```

**Adapt to the running image** — do NOT mount the 3.1 `tools.py` verbatim (it would regress
the image's other `video_analytics` tools and targets the old package layout):

1. **Resolve the package + site-packages path** on the image (see `sop/deploy-sop-report.md`
   § Deploy Steps). The current base uses `agent/video_analytics/` (an earlier layout used
   `vss_agents/`).
2. **Locate the SOP additions** in the downloaded `tools.py` — a self-contained block of the
   four SOP tool implementations plus their four include-gated registrations. Diffing it
   against the image's own `<pkg>/video_analytics/tools.py` makes them obvious.
3. **Produce two files** into the build staging dir, keeping the diff to the product file minimal:
   - `tools.py` = the image's own `video_analytics/tools.py` with **only** the four SOP tool
     registrations grafted in (imported from the sibling module below).
   - `sop_tools.py` = the SOP tool implementations lifted into a self-contained sibling module;
     its relative imports resolve against the image's `video_analytics` package.
4. **Adapt the downloaded VA-MCP config**: repoint endpoints from the release's `localhost` to
   compose service names (`elasticsearch:9200`, `vst-ingress:30888`), and keep the
   `video_analytics` group with the four `get_sop_*` tools in its `include` list.
5. **Mount** the adapted `{tools,sop_tools}.py` over
   `${VSS_AGENT_SITE_PACKAGES}/<pkg>/video_analytics/` via `sop/sop-report/sop-report-override.yml`.

The image is the Foundation's stock `vss-va-mcp`
(`ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest`); verify `VSS_AGENT_SITE_PACKAGES`
(python minor) against it. The adaptation is a pure module move + graft — **no logic change**
to the SOP tools.

## Sources

- **Detailed contracts of record** (this folder): `sop/integrate-ds-sop.md`,
  `sop/deploy-ds-sop.md`, `sop/integrate-sop-report.md`, `sop/deploy-sop-report.md`, `sop/build-ds-sop.md`.
- **Assets** (this folder): `sop/sop-report/sop-report-override.yml` (the mount override),
  `sop/sop-vlm-captions-json-logstash.conf`, `sop/evals/sop_deployment.json`. The `get_sop_*`
  patch + VA-MCP config are **downloaded at build time** from the public SOP repo (see
  § Patch specifics) — **not shipped in this repo**.
- **Report rendering**: `skills/vss-generate-video-report/` (Mode C; `assets/sop-compliance-report.md`).
- **Upstream**: `deploy/docker/services/agent/compose.yml` (`vss-va-mcp`), ELK / Kafka / VIOS Compose.
- **SOP source**: `NVIDIA/sop-monitoring-blueprints` (public, branch `main`, `0dd472f`) —
  DS-SOP image + Kibana dashboard + the VA-MCP report patch (`…/vss-agent/patches/`,
  **downloaded at build time**, see § Patch specifics).
