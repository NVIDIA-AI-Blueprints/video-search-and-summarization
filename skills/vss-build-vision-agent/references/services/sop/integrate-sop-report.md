# Integration Reference: SOP Report Tools (VA-MCP)

## Overview

SOP Report Tools adds the SOP query tools to a **`vss-va-mcp`** (`:9901`)
Video-Analytics MCP server so that the **`vss-generate-video-report`** skill
(Mode C) can render an **SOP compliance report** (cycle status, missing /
mis-ordered steps, actions, verdict) from the DS-SOP captions already in
Elasticsearch (`mdx-vlm-captions-*`).

The deployed unit is **just `vss-va-mcp` plus a bind-mounted `video_analytics`
patch** that adds four tools — `get_sop_status`, `get_sop_report`,
`get_sop_as_incidents`, `get_sop_as_incident`. Report generation itself is done by
the `vss-generate-video-report` skill (it queries these tools via
`/vss-query-analytics` and fills the SOP template) — there is **no report LLM, no
web UI, and no separate report service** in this layer.

## Required Peer Services

- **DS-SOP** (`sop-detection`) — data producer: its captions land in ES
  `mdx-vlm-captions-*` via the SOP JSON Logstash pipeline (`integrate-ds-sop.md`).
  No captions → empty reports.
- **ELK** (`caption-storage`) — `vss-va-mcp` reads `http://elasticsearch:9200`
  (index prefix `mdx-`).
- **Kafka** — required transitively (DS-SOP → ELK path).

`vss-va-mcp` runs standalone (`mcp serve`, no `depends_on`) — it does **not**
require `vss-agent` or `vss-ui`.

> DS-SOP's build-vision-agent composition (a delta, no new service) is in
> § build-vision-agent composition below. `vss-va-mcp` lives in
> `services/agent/compose.yml` (upstream); this integration reuses it and layers
> the SOP VA-MCP config + a tool patch.

## Integration Interfaces

**Inputs:** ES `mdx-vlm-captions-*` — the SOP tools query it for a sensor + range
(docs carry `response`, `sensor_id`, `@timestamp`, `req_id`,
`checker_result{cycle_completed, missing_detected, misordered_detected}`; see
`integrate-ds-sop.md § Outputs`).

**Outputs (MCP tools on `:9901`, called by `/vss-query-analytics`):**
- `video_analytics__get_sop_report` — aggregated window: total messages, current /
  completed cycle, compliance status, all violations, unique actions, plus a
  `formatted_report` markdown string. (Primary input for report Mode C.)
- `video_analytics__get_sop_status` — recent SOP messages / current activity.
- `video_analytics__get_sop_as_incidents` / `__get_sop_as_incident` — incident-shaped
  adapters (whole-window aggregate) for generic incident consumers.

The `vss-generate-video-report` skill (Mode C) consumes `get_sop_report` and renders
`assets/sop-compliance-report.md` → markdown returned to the user.

## Environment Variables

Full set in `../sop.md` § Delta composition (example `override.env`). Load-bearing:

| Variable | Default | Notes |
|---|---|---|
| `VSS_AGENT_VERSION` | `3.2.1-26.07.1-977d13d3a55c` | `vss-va-mcp` image tag; the patch is adapted/version-matched to it. |
| `VSS_VA_MCP_CONFIG_FILE` | adapted from the downloaded SOP release config (`../sop.md` § Patch specifics) | Selects the SOP tools + reads `mdx-vlm-captions-*`. |
| `VSS_AGENT_SITE_PACKAGES` | `/vss-agent/.venv/lib/python3.13/site-packages` | Patch mount target — verify python minor vs image. |
| `VSS_VA_MCP_PORT` | `9901` | Host port (free on the SOP host). |
| `VSS_ES_HOST` / `VSS_ES_PORT` | `elasticsearch` / `9200` | ES read endpoint. |

No LLM, no `NVIDIA_API_KEY`, no UI env — none are used by this layer.

## Network Requirements

`vss-va-mcp` joins the SOP stack's bridge network (same `COMPOSE_PROJECT_NAME=mdx`)
— reaches `elasticsearch:9200` by name. Host port `9901` (no collision with DS-SOP
`8300`, VIOS `30888`, ES `9200`). No GPU.

## Known Integration Constraints

- **SOP tools are a mounted patch, not in the image.** The stock `video_analytics`
  group has `get_incidents`, not `get_sop_*`. The patch is **downloaded from NVIDIA's
  public SOP repo at build time** (not shipped in this repo) and **adapted** to the running
  image, then bind-mounted over `<pkg>/video_analytics/` at deploy time
  (`sop-report-override.yml`) — full steps in `../sop.md § Patch specifics`. The adapted
  `tools.py` = the image's own `video_analytics/tools.py` + four include-gated `get_sop_*`
  registrations; `sop_tools.py` is the extracted SOP logic. **Adapt/version-match to
  `VSS_AGENT_VERSION`.**
- **VA-MCP config must list the SOP tools** in `video_analytics.include` (shipped
  config does) or they never register.
- **Read-only** — reads the ES the DS-SOP path writes; never writes/seeds ES.
- **Same project as DS-SOP** — do not start a second ES/Kafka.

## Scope notes

SOP-scoped extension of the stock `vss-va-mcp` service. Pairs with
`integrate-ds-sop.md`: compose `sop-detection` + `sop-report-generation` for the
full blueprint (detection + on-demand compliance reports via the report skill).

## build-vision-agent composition

The SOP report layer introduces **no new service** — it **reuses the stock
`vss-va-mcp`** (self-named key `vss-va-mcp`, already in the `alerts` Foundation). The
delta just:

- keeps `vss-va-mcp` in `COMPOSE_PROFILES`;
- **changes** its definition to mount the SOP `video_analytics` patch →
  `patches/vss-va-mcp.yml` (an added bind mount env interpolation cannot express — see
  `../sop.md § Patch specifics`);
- points `VSS_VA_MCP_CONFIG_FILE` at the SOP va_mcp config (selects `get_sop_*`).

`override.env` keys: `../sop.md § Delta composition`. Two deploy-time steps the Compose
patch cannot cover — **download** the SOP release patch + VA-MCP config from NVIDIA's
public repo and **adapt** them (per `../sop.md § Patch specifics`), staging the adapted
output under the build, and apply `sop-report-override.yml` for the site-packages mount —
are in `deploy-sop-report.md`.
build-vision-agent also **bundles the `vss-generate-video-report` skill** (the Mode C
report renderer) into the build's `skills/`.
