# `.env` File Enumeration (Step 0)

VSS spreads its environment configuration across **multiple `.env` files** by concern. A skill that only reads the per-profile `dev-profile-*/.env` will miss component-internal variables and produce a `.env` that fails dry-run with errors like `invalid spec: :/home/vst/vst_release/streamer_videos: empty section between colons` (caused by an unset `${CLIP_STORAGE_PATH}` collapsing the host portion of a volume mount).

Run a recursive `.env` discovery against the source repo and record every file found:

```bash
find <repo>/deploy -type f -name '.env' -not -path '*/_builds/*' -not -path '*/build-output/*' | sort
```

## Core `.env` files (10 total)

For the current upstream, the canonical set is **10 core `.env` files** (4 developer profiles, 1 industry profile, 5 service-internal) plus a NIM hardware-tier set selected per host architecture (see below).

| File | Owns variables for |
|---|---|
| `deploy/docker/developer-profiles/dev-profile-base/.env` | base profile (deployment shape, hardware, NIM placement, paths) |
| `deploy/docker/developer-profiles/dev-profile-lvs/.env` | LVS profile additions (`VLM_PORT`, `LVS_IMAGE`, `LVS_BACKEND_URL`, etc.) |
| `deploy/docker/developer-profiles/dev-profile-search/.env` | search profile additions (Kafka + ELK on, embedding service, video-analytics, etc.) |
| `deploy/docker/developer-profiles/dev-profile-alerts/.env` | alerts profile additions (alert verification, vlm-as-verifier, behavior analytics) |
| `deploy/docker/services/vios/vst.env` | **VIOS-internal vars** — `CLIP_STORAGE_PATH`, `VST_TEMP_FILES_PATH`, `SDR_IMAGE`, `ENVOY_PROXY_IMAGE`, `VST_STREAM_PROCESSOR_IMAGE`, `KAFKA_BOOTSTRAP_URL`, `REDIS_HOSTADDR`, `REDIS_PORT`, `REDIS_MSG_KEY`, `STREAM_PROCESSOR_HTTP_PORT`, `RTSP_SERVER_PORT`, `SENSOR_MODULE_ENDPOINT`, `VST_INGRESS_ENDPOINT`, `VST_INSTALL_ADDITIONAL_PACKAGES`, `MCP_GATEWAY_*` (the canonical image names live here too — `vss-vios-sensor`, not the historical `vss-vst-*`) |
| `deploy/docker/services/vios/compose-defaults.env` | Compose defaults — empty/placeholder values for variables referenced across all composes included by `deploy/docker/compose.yml`, suppressing `docker compose` warnings when a profile does not use a particular service. Override per-variable in the active profile `.env`. |
| `deploy/docker/services/video-summarization/.env` | LVS-component-internal vars (LVS image tag, backend port wiring) |
| `deploy/docker/services/rtvi/rtvi-vlm/.env` | RT-VLM-component-internal defaults |
| `deploy/docker/services/rtvi/rtvi-embed/.env` | RT-Embedding-component-internal defaults |
| `deploy/docker/industry-profiles/warehouse-operations/.env` | Industry-profile variant — `warehouse-operations` stack additions and overrides (separate from the developer-profile category) |

## NIM hardware-tier `.env` files (new structural category)

Beyond the 10 core files above, the NIM service tree carries a **per-hardware-tier `.env` set**: one file per (model × hardware) combination, plus `-shared` variants for shared-GPU mode. Selection is by `HW_PROFILE` (set in `dev-profile-base/.env` or equivalent), not by enumeration.

Layout: `deploy/docker/services/nim/<model>/hw-<HW_PROFILE>.env` (standalone) and `hw-<HW_PROFILE>-shared.env` (shared-GPU). Plus `deploy/docker/services/nim/fallback-override.env` for cross-model overrides.

Models present upstream (as of this writing): `cosmos-reason1-7b`, `cosmos-reason2-8b`, `qwen3-vl-8b-instruct`, `gpt-oss-20b`, `llama-3.3-nemotron-super-49b-v1.5`, `nemotron-3-nano`, `nvidia-nemotron-nano-9b-v2`, `nvidia-nemotron-nano-9b-v2-fp8`.

Hardware tiers: `H100`, `RTXPRO6000BW`, `L40S`, `DGX-SPARK`, `AGX-THOR`, `IGX-THOR`, `OTHER` (not every model has every tier — `cosmos-reason1-7b` skips DGX-SPARK / Thor tiers, etc.).

**Step 0 must determine `HW_PROFILE` first**, then pick the matching NIM env file(s) for every NIM model the profile uses. Do not blindly fold all hardware-tier files into one `.env` — they contain mutually-exclusive `MODEL_PROFILE` / `LIMITS_*` values per tier and would clobber each other.

## Variable folding rule

When generating the output `.env` in Step 6, **fold in every variable referenced by any selected service's compose** — even if it lives outside the per-profile `.env`. Cross-reference each candidate's `integrate-<microservice>.md § Environment Variables` for the authoritative per-service list, and walk the actual compose YAML for `${VAR}` substitutions to catch any the reference file missed.

### Drop variables for services NOT in the allow-list

Fold only the variables consumed by **allow-listed** services. The per-profile `.env` files carry vars for the *whole* profile (UI, agent, every NIM tier), most of which a narrowed generation does not deploy. In particular:

- **`NEXT_PUBLIC_*` are UI-only.** They are consumed exclusively by `vss-ui`. When `vss-ui` is not in the allow-list (e.g. a headless verification/captioning deployment), **drop the entire `NEXT_PUBLIC_*` set.** They are dead weight and one of them is actively harmful (see the parser footgun below).
- Apply the same "drop if the owning service was dropped" rule to any other service-scoped block (e.g. LVS/search/embedding vars when those services aren't selected).

### Do NOT absolutize container-relative path vars

Some path vars are **relative paths that resolve inside the container** against a bind mount + the container's workdir — they are NOT host paths. Pass them through **verbatim**; never prefix `${VSS_APPS_DIR}` or otherwise rewrite them to host-absolute, or the service looks for a path that does not exist in the container and fails to boot.

The canonical case (Finding F-I, 2026-06-16): `vss-agent` / `vss-va-mcp` mount `${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro` with workdir `/vss-agent`, so these stay container-relative (leading `./deploy/docker/...`): `VSS_AGENT_CONFIG_FILE`, `VSS_VA_MCP_CONFIG_FILE`, `VSS_AGENT_TEMPLATE_PATH`. Heuristic: a value that already starts with `./` and names a path *under* a directory that is a bind-mount **target** is container-scoped — leave it alone. (This is also why a flat/absolute-resolving env emitter is risky for the agent layer: it can wrongly expand these.)

### Quote complex values — the `NEXT_PUBLIC_..._JSON` parser footgun

> **Live finding (AT-1, 2026-06-15).** `dev-profile-alerts/.env` ships a single ~319-char unquoted JSON value, `NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_API_CUSTOM_AGENT_PARAMS_JSON={"params":[...]}`. A line-based `--env-file` parser (and several naive env-folding parsers) **mis-handles it and silently drops every variable after it**, including the host-override block (`HOST_IP`, `VSS_APPS_DIR`, `VSS_DATA_DIR`, `BUILD_DIR`). The deploy then fails with unset-path / `empty section between colons` volume errors that look unrelated to the UI.

Two defenses, apply both:

1. **Exclude `NEXT_PUBLIC_*` entirely** when `vss-ui` is not deployed (above) — this removes the offending line at the source.
2. **Single-quote any folded value that contains `{`, `[`, `"`, `#`, a space, or `=`** when emitting the output `.env`, so the value stays on one logical line and cannot truncate the file. Order the emitted `.env` so the **host-override / credentials block comes first**, never after an unquoted complex value, as belt-and-suspenders against any downstream parser.

After folding, the Step 7 dry-run (`docker compose --env-file .env -f compose.yml config`) must show zero **real** unexpanded `${...}` tokens — a missing `HOST_IP` / `VSS_APPS_DIR` in the resolved output is the tell-tale signature of this truncation bug.
