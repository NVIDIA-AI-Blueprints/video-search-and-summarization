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
(Mode C) from `get_sop_report` — which returns an aggregated window (message count,
current/completed cycle, compliance status, all violations, unique actions, and a
`formatted_report` markdown). `get_sop_status` / `get_sop_as_incident(s)` are the
recent-activity and incident-adapter variants. There is **no** report service, VSS
agent, web UI, or report LLM in this integration.

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
  DS-SOP Compose block in `sop/integrate-ds-sop.md § Example Compose Snippet`; and
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
# SOP_STAGE_DIR → absolute BUILD-LOCAL dir the adapted get_sop_* patch + VA-MCP config stage into
#   (_builds/<name>/sop-report) — NEVER deploy/docker. VSS_AGENT_SITE_PACKAGES/PKG → mount root.
SOP_STAGE_DIR=<abs repo path>/_builds/sop-1/sop-report
VSS_VA_MCP_CONFIG_FILE=${SOP_STAGE_DIR}/configs/va_mcp_server_config.yml
VSS_AGENT_SITE_PACKAGES=/vss-agent/.venv/lib/python3.13/site-packages
VSS_AGENT_PKG=vss_agents            # develop ships video_analytics under vss_agents; resolve from the image — see § Patch specifics
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
| `SOP_STAGE_DIR` | Absolute **build-local** dir (`_builds/<name>/sop-report`) the adapted `get_sop_*` patch + VA-MCP config stage into — never `deploy/docker/`. |
| `VSS_VA_MCP_CONFIG_FILE` | SOP VA-MCP config (`${SOP_STAGE_DIR}/configs/va_mcp_server_config.yml`; adapted from the downloaded SOP release config; selects the `get_sop_*` tools). |
| `VSS_AGENT_SITE_PACKAGES` | In-container site-packages root the SOP patch mounts over. |
| `VSS_AGENT_PKG` | Package that holds `video_analytics` — on `develop` it is `vss_agents` (`services/agent/packages/vss_agents`). Resolve it from the running image by probing `<pkg>.video_analytics` (§ Patch specifics); never hardcode (a published image may differ). |

## Patch specifics — download the SOP release patch, then adapt it

The `get_sop_*` tools are **not shipped in this repo**. They live in NVIDIA's **public**
`sop-monitoring-blueprints` repo (Apache-2.0); the agent **downloads** them at build time
and adapts them to the running `vss-va-mcp` image. Because they come straight from NVIDIA's
own public repo, **no license grant or OSRB is needed** — nothing third-party is vendored here.

**Source — download at build time:**
- Repo: `https://github.com/NVIDIA/sop-monitoring-blueprints` (public, branch `main`, pin `0dd472f`)
- Path: `agentic/vss-sop-skills/vss-sop-build/references/deployments/sop/vss-agent/`
  - `patches/tools.py` — the release's **3.1 monolith** `video_analytics/tools.py` (stock
    tools + the SOP additions). `patches/utils.py` is stock (the image ships it).
    `patches/es_client.py` is **NOT stock** — the release adds the SOP index to the whitelist
    (`vision_llm_messages → mdx-vlm-captions-*` + `NO_PREFIX_INDEXES`); without it `get_sop_*`
    raises `ValueError` before reaching ES. Handle it per § caveats (register the index from
    `sop_tools.py`, or mount the release `es_client.py` as a third file).
  - `configs/va_mcp_server_config.yml` — release VA-MCP config (selects `get_sop_*`).

`sop/build-ds-sop.md` § Step 0 already clones this same repo — reuse that clone, or fetch
just the two files:
```bash
REPO=https://raw.githubusercontent.com/NVIDIA/sop-monitoring-blueprints/0dd472f
BASE=agentic/vss-sop-skills/vss-sop-build/references/deployments/sop/vss-agent
curl -fsSL --max-time 60 $REPO/$BASE/patches/tools.py                 -o /tmp/sop-release-tools.py
curl -fsSL --max-time 60 $REPO/$BASE/configs/va_mcp_server_config.yml -o /tmp/sop-release-va-mcp.yml
```

**Adapt to the running image** — do NOT mount the 3.1 `tools.py` verbatim (it would regress
the image's other `video_analytics` tools and targets the old package layout):

1. **Resolve which package holds `video_analytics` + the site-packages path** on the image —
   **probe the filesystem, NOT `import`.** `import`-based probing is unreliable here: a hardcoded or
   wrongly-guessed package name (e.g. `agent`) can mount under a directory that does not exist on
   disk, and Docker then creates an empty `agent/` that shadows the real package, so `vss-va-mcp`
   crash-loops with `Unknown included functions`. Pick the real on-disk directory (has
   `video_analytics/`, not a symlink) instead:
   ```bash
   docker run --rm --entrypoint /vss-agent/.venv/bin/python3 \
     ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest -c '
   import os, sys, sysconfig
   # check purelib AND platlib (the package may live in either); exit non-zero if neither resolves
   for sp in (sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]):
       for pkg in ("vss_agents", "agent"):
           d = os.path.join(sp, pkg)
           if os.path.isdir(os.path.join(d, "video_analytics")) and not os.path.islink(d):
               print(pkg, sp); raise SystemExit(0)
   sys.stderr.write("no on-disk video_analytics package (vss_agents/ or agent/) under purelib/platlib\n")
   raise SystemExit(1)'
   ```
   Set `VSS_AGENT_PKG` (`vss_agents` on `develop-latest` — there is no on-disk `agent/` package) and
   `VSS_AGENT_SITE_PACKAGES` from the output. The probe exits non-zero (empty stdout) if neither
   resolves — treat that as a hard error, never set `VSS_AGENT_PKG` empty.
2. **Locate the SOP additions** in the downloaded `tools.py` — a self-contained block of the
   four SOP tool implementations plus their four include-gated registrations. Diffing it
   against the image's own `<pkg>/video_analytics/tools.py` makes them obvious.
3. **Produce two files** into `${SOP_STAGE_DIR}/video_analytics/` — an absolute path under
   `_builds/<name>/sop-report/` (**build-local**; never under `deploy/docker/`; the exact path
   `sop-report-override.yml` binds from), keeping the diff to the product file minimal:
   - `tools.py` = the image's own `video_analytics/tools.py` with **only** the four SOP tool
     registrations grafted in (imported from the sibling module below).
   - `sop_tools.py` = the SOP tool implementations lifted into a self-contained sibling module;
     its relative imports resolve against the image's `video_analytics` package.
4. **Adapt the downloaded VA-MCP config** and stage it at
   `${SOP_STAGE_DIR}/configs/va_mcp_server_config.yml` (where `VSS_VA_MCP_CONFIG_FILE` points;
   build-local under `_builds/<name>/`): repoint endpoints from the release's `localhost` to compose
   service names (`elasticsearch:9200`, `vst-ingress:30888`), and keep the `video_analytics`
   group with the four `get_sop_*` tools in its `include` list.
5. **Mount** the adapted `{tools,sop_tools}.py` over
   `${VSS_AGENT_SITE_PACKAGES}/${VSS_AGENT_PKG}/video_analytics/`. The composed build emits this as
   the generated `_builds/<name>/patches/vss-va-mcp.yml` overlay (what the profile eval checks for);
   `sop/sop-report/sop-report-override.yml` is the equivalent shipped template for a direct one-off
   apply as an extra `-f` — same two bind mounts, both sourced from `${SOP_STAGE_DIR}`. Either way,
   bring up just the patched service (`docker compose … up -d vss-va-mcp`; leaves DS-SOP/ELK untouched).
6. **Verify:** `docker exec vss-va-mcp /vss-agent/.venv/bin/python3 -c "import ${VSS_AGENT_PKG}.video_analytics.sop_tools"`
   exits 0, and MCP `tools/list` on `:9901` lists the four `get_sop_*` (full runtime checks are
   in the profile eval). Empty/error `get_sop_report` → `mdx-vlm-captions-*` empty or the SOP
   Logstash pipeline not registered (`deploy-ds-sop.md`).

The image is the Foundation's stock `vss-va-mcp`
(`ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest`); verify `VSS_AGENT_SITE_PACKAGES`
(python minor) against it. The adaptation is mostly a module move + graft, but a few caveats
need handling:

- **Register the SOP index in the `es_client` whitelist.** The stock `es_client` on
  `develop-latest` doesn't know the SOP captions index, so `get_sop_*` raises `ValueError` on the
  index whitelist (`get_index`) *before* reaching ES. The release `es_client.py` adds it
  (`vision_llm_messages → mdx-vlm-captions-*` + `NO_PREFIX_INDEXES`). Fix by **either** registering
  the SOP index from within `sop_tools.py` (idempotent, prefix-aware — keeps 2 mounts, verified
  working) **or** downloading + mounting the release `es_client.py` as a third file.
- **Do not add `from __future__ import annotations`** to the adapted modules. NAT evaluates
  each tool's annotations at registration time; postponed annotations raise a `NameError` and
  the `get_sop_*` tools never register.
- **`get_sop_report` caps at 1000 ES docs** (~2.75 h at 10 s chunks); beyond that, message
  count and compliance status can be wrong (dropped-tail violations are not aggregated) with no
  warning. For long windows, report over a narrower time range — or flag the cap to the user.

## Sources

- **Detailed contracts of record** (this folder): `sop/integrate-ds-sop.md`,
  `sop/deploy-ds-sop.md`, `sop/build-ds-sop.md`. (The SOP report layer is small — its full
  contract is § Patch specifics above, so it has no separate integrate/deploy files.)
- **Assets** (this folder): `sop/sop-report/sop-report-override.yml` (the mount override),
  `sop/sop-vlm-captions-json-logstash.conf`. The `get_sop_*` patch + VA-MCP config are
  **downloaded at build time** from the public SOP repo (see § Patch specifics) — **not shipped
  in this repo**.
- **Profile evals** (split per build-vision-agent convention): `eval/profile_sop_1_compliance_monitoring.json`
  (build + Compose validate — no deploy, no ds-sop image needed) and
  `eval/profile_sop_1_compliance_monitoring_runtime_harbor.json` (build + deploy + runtime detection/report —
  requires a provisioned host with `ds-sop:1.0.0` built + SOP models staged).
- **Report rendering**: `skills/vss-generate-video-report/` (Mode C; `assets/sop-compliance-report.md`).
- **Upstream**: `deploy/docker/services/agent/compose.yml` (`vss-va-mcp`), ELK / Kafka / VIOS Compose.
- **SOP source**: `NVIDIA/sop-monitoring-blueprints` (public, branch `main`, `0dd472f`) —
  DS-SOP image + Kibana dashboard + the VA-MCP report patch (`…/vss-agent/patches/`,
  **downloaded at build time**, see § Patch specifics).
