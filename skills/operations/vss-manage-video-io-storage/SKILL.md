---
name: vss-manage-video-io-storage
description: Use to drive `vss vios` for sensor list, timelines, clips, snapshots, and add/delete of video or stream sources. Use the bundled REST references only for what that CLI does not cover (WebRTC session control, the RTSP proxy, recorder configuration, network scan, device settings, and the NvStreamer API). Also provisions a source into a headless (no-agent) build, which the deployment fans out to the perception consumers (RT-CV/RT-Embed/RT-VLM). Not for VLM inference, semantic search, or agent-backed ingestion.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  # What a live deployment must expose for this skill to be usable, as the vss CLI
  # names it: a command group (search, summarize, vlm, vios, memory), "alerts"
  # (Alert Bridge), or "always" for a skill every VSS deployment gets. The
  # OpenClaw harness image ships and activates skills by it.
  vss-requires: "always"
---
## Purpose

Manage VIOS video input/output and storage with `vss vios`: sensors, streams,
uploads, snapshots, clips, timelines, and recording status. NvStreamer stays a
separate REST API, used only for an explicit synthetic RTSP request.

## When to Use

- Drive `vss vios` — add or delete a video file or RTSP stream, list sensors, show configured sensors, get a snapshot, download a clip, upload a video file, or manage video storage
- Serve test/sample videos as synthetic RTSP via NvStreamer, or drive the NvStreamer → `vss vios add --type stream` handoff
- Provision a source into a headless (no-agent) build so the deployment fans it out to RT-CV / RT-Embed / RT-VLM

Not for VLM inference or ad-hoc visual Q&A (`vss-ask-video`), semantic search (`vss-search-archive`), agent-backed search ingestion (`vss-search-archive`), narrative summaries (`vss-summarize-video`), or reading analytics/incidents (`vss-query-analytics`).

## Prerequisites

- The `vss` CLI on `PATH`. The OpenClaw and Hermes harness images ship it; anywhere else, install it from the same checkout as this skill so the CLI and the skill match: `uv tool install <checkout>/libs/vss/cli`.
- A deployment already recorded by `vss configure`. Confirm with `vss configure show`. `vss configure --base-url` is the only place an endpoint is supplied, and only the ingress origin the operator already has (`VSS_PUBLIC_URL`). If nothing is recorded and `VSS_PUBLIC_URL` is unset, stop and ask for the ingress origin. Never default a host or port. Exit codes, empty results, and pipe rules live in the repository root [`AGENTS.md`](../../../AGENTS.md).
- NGC credentials in `$NGC_CLI_API_KEY` and `$NVIDIA_API_KEY` for any image pulls.
- `curl` and `jq` only for the REST surface `vss vios` does not cover, and for reading CLI JSON. Use `set -o pipefail`, or capture stdout before piping. Docker is needed only for Compose deployment diagnostics.

## Instructions

# VIOS Operations

Use `vss vios` for list, add, delete, timeline, clip, and snapshot. Address media by sensor name. Do not build a sensorId from a name. `--type` is required on add and delete (`video` for a file-backed sensor, `stream` for RTSP) and optional on list. Do not hand-build a clip window when `vss vios clip --sensor NAME` can resolve it. Do not navigate the UI.

`curl` is not the path for those operations. If a `vss vios` command fails, report the failure — do not fall back to raw REST. `curl` remains only for what the CLI does not cover: WebRTC session control, the RTSP proxy, recorder configuration, network scan, device settings, and the NvStreamer REST API. Those stay in [`references/api-reference.md`](references/api-reference.md) and [`references/nvstreamer-api-reference.md`](references/nvstreamer-api-reference.md).

```bash
vss vios list     [--type video|stream] [--sensor NAME]
vss vios timeline --sensor NAME
vss vios clip     --sensor NAME [--start-time T --end-time T]   # -> media_url
vss vios snapshot --sensor NAME [--at T]                        # -> media_url
vss vios add      --type video|stream SOURCE [--name NAME]
vss vios delete   --type video|stream --sensor NAME
```

**Upload routing rule:**

- If the user asks to "upload `<file>.mp4` to VIOS", "upload a video file", or otherwise means storing a local video as a VIOS file-backed sensor, use `vss vios add --type video <file>`. The filename stem is the sensor name. Uploaded filenames must have no whitespace.
- Use NvStreamer only when the user explicitly needs a live/synthetic RTSP camera feed, asks for NvStreamer, or asks to retrieve an RTSP URL.
- Do not substitute the NvStreamer upload -> RTSP URL -> `vss vios add --type stream` handoff for a plain VIOS MP4 upload request.

**Provisioning + fan-out routing rule:**

- To register a source into a build with **no agent tier** — e.g. a `vss-build-vision-ai` headless `_builds/<name>` deployment — follow [`references/provision-vios-source.md`](references/provision-vios-source.md) (register one VIOS source; the deployment's mounted notification config fans it out to RT-CV / RT-Embed / RT-VLM). RT-VLM carries **two independent legs** — dense captioning (free-form prompt) and **VLM tagging** (a controlled JSON-tag prompt that feeds BM25 tag search via `mdx-vlm-captions` → Logstash → `default_<streamId>`). Either can be driven by hand where no receiver runs it; tagging is independent of the Alert-Bridge carve-out that governs the dense-captioning leg.
- If an agent `/api` tier **is** present, provisioning is agent-owned: defer to `vss-search-archive` (search ingestion) or `vss-manage-alerts` (alert rules), not this recipe.

**Do NOT use this skill for:**

- VLM inference or ad-hoc visual Q&A about a clip — use `vss-ask-video`.
- Semantic search across the archive — use `vss-search-archive`.
- **Agent-backed** ingestion for search (full-stack, `/api` agent tier present) — use `vss-search-archive`. (Headless, no-agent provisioning *is* this skill — see the routing rule above.)
- Narrative summaries of a recorded clip — use `vss-summarize-video`.
- Incident-range or alert-window reports — use `vss-generate-video-report` Mode B.
- Reading analytics metrics, incidents, or alerts — use `vss-query-analytics`.

## Reference contracts shipped with this skill

This skill bundles five reference files under `references/`. Read whichever applies to the task in front of you:

| File | Purpose | Audience |
| --- | --- | --- |
| [`references/api-reference.md`](references/api-reference.md) | The full VIOS REST API reference (the runtime contract) — sensor management, storage, snapshots, clip extraction, WebRTC live/replay, RTSP proxy, recorder, service configuration, service discovery. **Read this for what `vss vios` does not cover: WebRTC session control, the RTSP proxy, recorder configuration, network scan, and device settings.** Do not use it in place of `vss vios list`, `add`, `delete`, `timeline`, `clip`, or `snapshot`. | Operational users + this skill itself |
| [`references/provision-vios-source.md`](references/provision-vios-source.md) | The **headless (no-agent) write path** — register one VIOS source and stop; the mounted notification config fans it out. Carries the upload `creation_time` rule, the shared-id rule, idempotency, teardown, the two RT-VLM legs a caller must drive by hand where no receiver runs them, and what to read when a consumer never got the source. **Read this when provisioning a source into a headless build.** | Runtime operators, `vss-build-vision-ai` callers |
| [`references/nvstreamer-api-reference.md`](references/nvstreamer-api-reference.md) | The **NvStreamer REST API reference** — version, sensor list/info/status/streams, the three upload methods (PUT v2 / PUT v1 / POST multipart) with the `nvstreamer-*` custom headers, delete, snapshots (frame-indexed live, timestamp-indexed storage), storage info, filesystem scan. NvStreamer (`vss-vios-nvstreamer`, the streamer-adaptor variant of `launch_vst`) is **brought up by the same profiles that bring VIOS up** — `dev-profile-alerts`, `dev-profile-lvs`, `dev-profile-search`, all warehouse profiles. See `integrate-vios-service.md § Topology B` for the deployment side. **Read this when serving test / sample videos as synthetic RTSP, retrieving the RTSP URL NvStreamer generated for a file, or driving the canonical NvStreamer → VIOS handoff** (upload to NvStreamer → read RTSP URL → register that URL with VIOS via `vss vios add --type stream`). | Operational users + skill authors composing the upload → RTSP URL → VIOS handoff |
| [`references/integrate-vios-service.md`](references/integrate-vios-service.md) | The **integration contract** — how VIOS plugs into other VSS microservices. Documents required peer services (RT-VLM, ELK, Kafka, Redis; `sdr-controller` / SDRC **when `VST_USE_SDRC=true`**), the structured `component_services:` block consumed by the `vss-build-vision-ai` skill's Step 4, integration inputs/outputs (Kafka topics, REST endpoints, file paths), environment variables, network requirements, and known integration constraints (e.g. the `/url`-variant double-`http://` bug, the VIOS + SDRC co-enablement rule for SDRC-routed profiles). **Read this when authoring a skill that talks to VIOS as a peer, when composing a new VSS deployment, or when debugging caption-pipeline wiring.** | Skill authors, deployment composers, pair-file maintainers |
| [`references/deploy-vios-service.md`](references/deploy-vios-service.md) | The **deployment contract** — what it takes to bring VIOS up. Documents container images and tags (VIOS core under `nvcr.io/nvidia/vss-core/vss-vios-*`; **SDRC `sdr-mw-l` from [`sdrc/docker-compose.yaml`](../../../deploy/docker/services/infra/sdrc/docker-compose.yaml) `SDR_MW_L_IMAGE` — resolve there before pull/deploy**), GPU / CPU / memory / storage requirements, startup behavior + healthcheck tuning, required environment variables (notably `VST_INSTALL_ADDITIONAL_PACKAGES=true` for the libav apt-install step that gates uploads), known deployment issues (volume drift, libav missing, 502 from leftover containers), prerequisites, dry-run, verify-deployment, and tear-down commands. **Read this when VIOS isn't running and you (or your caller) need to deploy it standalone, when debugging container-startup failures, or when authoring a deploy skill that wraps VIOS.** | Operators, deploy-skill authors |

## Deployment prerequisite — VIOS MUST be running

This skill operates a deployment `vss configure` already recorded. It does not construct a VIOS URL, and it does not deploy VIOS itself. When the vios group is missing, coordinate a deploy using the bundled deployment runbook ([`references/deploy-vios-service.md`](references/deploy-vios-service.md)) or hand off to `/vss-build-vision-ai`.

For Kubernetes, do not use `kubectl port-forward`, an in-cluster Service name, a NodePort, or a guessed Helm release name. Before doing any work:

1. **Confirm the recorded deployment:**

   ```bash
   vss configure show
   vss configure check
   vss vios list
   ```

   `vss configure show` prints the origin already recorded. If nothing is recorded, the only setup is:

   ```bash
   vss configure --base-url "${VSS_PUBLIC_URL}"
   ```

   and only when the operator's ingress origin is already in `VSS_PUBLIC_URL`. If `VSS_PUBLIC_URL` is unset, stop and ask for the ingress origin. Never default a host or port.

2. **`vss vios list` exits 4 when no deployment is recorded, or the recorded deployment does not expose `vst`.** `vss configure check` does not use that code: it exits 3 when a previously recorded route is unreachable, and it prints which command groups are available. On exit 4 from `vss vios`, re-run `vss configure --base-url` with the operator's ingress origin, or hand off to deploy. Do not curl a constructed URL to decide that. Offer the standalone path:

   > *"The vios group is not configured (exit 4) — no deployment is currently serving VIOS.*
   > *(a) Bring up VIOS standalone using this skill's bundled [`references/deploy-vios-service.md`](references/deploy-vios-service.md) runbook — image tags, env vars (notably `VST_INSTALL_ADDITIONAL_PACKAGES=true`), host directories, NGC login, bring-up command, healthcheck loop, and known deployment issues are all documented there. This is the right path if you only need VIOS itself (no RT-VLM / ELK / etc.) or if you're composing a custom profile.*
   > *(b) Deploy a full VSS profile that includes VIOS via the `/vss-build-vision-ai` skill — `base` (recommended), `lvs`, `search`, or `alerts` all bring VIOS up alongside other components. This is the right path if you want a complete VSS stack.*
   > *Which would you like?"*

   - If the user picks (a) → walk them through `references/deploy-vios-service.md` step by step. Pay particular attention to its `§ Environment Variables — Required for Upload-to-Caption Path` and `§ Known Deployment Issues` sections — the libav-missing failure (`VST_INSTALL_ADDITIONAL_PACKAGES=true`) and the volume-drift hang (`docker compose up --yes` or `docker volume rm` first) are the two most common bring-up blockers. After deploy succeeds, record the operator's ingress origin with `vss configure --base-url "${VSS_PUBLIC_URL}"` (stop and ask if that variable is unset) and confirm `vss vios list` exits 0, then return here.
   - If the user picks (b) → hand off to the matching `/vss-build-vision-ai` stock workflow (default `base`). Return here once it succeeds and `vss configure` records the new origin.
   - If the user declines both → **stop**. VIOS operations require the vios group to be configured; do not attempt to fabricate responses or proceed with a degraded mode.

   *Pre-authorized autonomous mode:* if your caller has granted explicit pre-authorization to deploy prerequisites (e.g. the request says "pre-authorized to deploy prerequisites", or you are running in a non-interactive evaluation harness with that permission), skip the confirmation and prefer path (a) — bring up VIOS standalone via this skill's bundled `references/deploy-vios-service.md` — unless the request explicitly asks for a full VSS profile, in which case invoke the `/vss-build-vision-ai` stock Base workflow.

3. **If `vss vios list` exits 0, proceed.** An empty sensor list at exit 0 means the deployment has no sensors; it is not a missing service. Do not retry it.

---

## Known limitation — leftover containers from prior deploys

`vss vios list` can surface **HTTP 502 Bad Gateway** or stale results when leftover `*-smc` VST containers from an earlier deploy survive teardown and win the host `network_mode: host` port-bind race. **Remediation: re-run `/vss-build-vision-ai`** — its Step 0 teardown grep clears the full `sensor-ms-*` / `vst-ingress-*` / `sdr-*` / `sdrc-*` / `rtspserver-ms-*` set. `vss vios add`, `vss vios snapshot`, and `vss vios clip` are unaffected. Full failure-mode catalogue, remediation, and the current routing contract (direct vs SDRC; SDR/Envoy removed in PR #711) live in `references/deploy-vios-service.md § Known Deployment Issues` and [issue #151](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues/151).

---

## Setup

**Recorded origin:** `vss configure show`. CLI examples take no endpoint.

**If nothing is recorded:** the only endpoint input is `vss configure --base-url "${VSS_PUBLIC_URL}"`, using the ingress origin the operator already has. If `VSS_PUBLIC_URL` is unset, stop and ask. Never default a host or port. Do not discover or guess Kubernetes Service names, NodePorts, release names, or node IPs, and do not start a port-forward.

**Availability check:**

- Before any `vss vios` call, run `vss configure check`, then `vss vios list`.
- Exit 4 from `vss vios list` means no deployment is recorded, or it does not expose `vst`. See the **Deployment prerequisite** section. Do not curl a constructed URL to decide that.
- Exit 3 from `vss configure check` means a previously recorded route is unreachable. Report that failure. Do not fall back to REST.
- Any other non-zero exit is the failure to report. Do not fall back to REST for a covered operation.

**Run the commands yourself** — `vss vios` for list, add, delete, timeline, clip, and snapshot. `curl` only for the uncovered REST surface named above. Never instruct the user to run commands manually.

**Auth:** Covered operations go through the CLI. On the uncovered REST surface, most deployments run without auth. If a `401` is returned, retry with `-H "Authorization: Bearer <token>"` and ask the user for the token.

**Start/end time handling:**

- If the user provides a window, pass `--start-time` and `--end-time` to `vss vios clip`, or `--at` to `vss vios snapshot`.
- If the user does not, `vss vios clip --sensor NAME` resolves the recorded window itself and returns that window with the `media_url`. Do not read a timeline and invent bounds.
- `vss vios timeline --sensor NAME` is how you inspect the recorded range. Never fabricate timestamps.

**Resolving a sensor:** Address media by the sensor name. Confirm it with `vss vios list` (`--type video|stream` and `--sensor NAME` optional). Do not build a sensorId from a name. If an id is required, read it from that listing.

---

## Service Map

| Capability | How | Authoritative reference |
| --- | --- | --- |
| List sensors | `vss vios list` | [`../../../AGENTS.md`](../../../AGENTS.md) |
| Add / delete a video or stream | `vss vios add --type video` or `--type stream`; `vss vios delete` with the same `--type` and `--sensor NAME` | [`../../../AGENTS.md`](../../../AGENTS.md) |
| Recording timeline | `vss vios timeline --sensor NAME` | [`../../../AGENTS.md`](../../../AGENTS.md) |
| Clip | `vss vios clip --sensor NAME` → `media_url` | CLI. Binary `/url` defects are a service bug in `references/integrate-vios-service.md § Known Integration Constraints`, not a reason to bypass the CLI |
| Snapshot | `vss vios snapshot --sensor NAME` → `media_url` | CLI. The same `/url` defect note applies |
| File upload | `vss vios add --type video <file>` | libav upload failures are a deploy bug in `references/deploy-vios-service.md § Known Deployment Issues`, not a reason to bypass the CLI |
| Network scan, device settings, WebRTC, RTSP proxy, recorder | REST surface the CLI does not cover | `references/api-reference.md` |
| **NvStreamer** (synthetic RTSP only) | Direct API only when the caller already supplied `VSS_STREAMER_URL`. If it is absent, stop and ask. Do not derive a hostname or port. | `references/nvstreamer-api-reference.md` (separate from VIOS; no `/vst` prefix; `type: "streamer"` on `/version`) |

---

## Operations

Supported operations are the `vss vios` verbs in the service map. Read [`references/api-reference.md`](references/api-reference.md) only for WebRTC session control, the RTSP proxy, recorder configuration, network scan, and device settings.

NvStreamer is outside `vss`. When the user explicitly asks for a synthetic RTSP feed, the direct API is allowed only when the caller already supplied `VSS_STREAMER_URL`. Do not derive a hostname or port from `VSS_PUBLIC_URL`. If `VSS_STREAMER_URL` is absent, stop and ask. Follow [`references/nvstreamer-api-reference.md`](references/nvstreamer-api-reference.md) for that API, then register the RTSP URL it returns with `vss vios add --type stream`. NvStreamer comes up automatically with any VIOS-using profile that ships it; do not deploy it separately.

For integration- and deployment-time questions about how VIOS interacts with other microservices or how it's brought up, defer to [`references/integrate-vios-service.md`](references/integrate-vios-service.md) and [`references/deploy-vios-service.md`](references/deploy-vios-service.md) respectively (see the **Reference contracts** table above for what each covers).

---

## Workflow: sensor name/IP -> clip or snapshot

When the user has a sensor name or IP but needs a clip or snapshot:

0. Confirm the vios group (see Setup — Availability check): `vss configure check`, then `vss vios list`. Exit 4 from `vss vios list` follows the deployment prerequisite. Do not curl a constructed URL.

1. Confirm the sensor by name:

   ```bash
   SENSORS=$(vss vios list) || exit $?
   printf '%s\n' "${SENSORS}" | jq .
   ```

   Pass `--sensor NAME` when the user named one. Do not build a sensorId from that name.

2. Inspect the recorded range:

   ```bash
   vss vios timeline --sensor NAME
   ```

3. Clip or snapshot. Pass a window only when the user supplied one. Otherwise let the CLI resolve it:

   ```bash
   vss vios clip --sensor NAME
   vss vios snapshot --sensor NAME
   ```

   Use the returned `media_url`. Binary `/url` defects and libav upload failures are service bugs documented in the references, not a reason to bypass the CLI.

---

## Responses

Covered operations return CLI JSON on stdout and a typed exit code. Branch on the exit code ([`AGENTS.md`](../../../AGENTS.md)). An empty sensor list at exit 0 is an answer. Do not retry it, and do not fall back to REST.

The uncovered REST surface still uses its own shapes. **Success with data:** JSON object or array. **Success with no data:** `null` — the call succeeded but there is nothing to return (for example a scan found nothing). It is not an error. **Error:** JSON object with `error_code` and `error_message`:

```json
{
  "error_code": "VMSInternalError",
  "error_message": "VMS internal processing error"
}
```

Common codes: `VMSInternalError`, `VMSNotFound`, `VMSInvalidParameter`.

If `vss vios add` reports `InvalidParameterError: Failed to get media information`, this is the libav-missing failure mode — VIOS was deployed without `VST_INSTALL_ADDITIONAL_PACKAGES=true`. See `references/deploy-vios-service.md § Known Deployment Issues` Finding 9 for the fix. That is a service bug, not a reason to bypass the CLI.

If a returned `media_url` contains a double-`http://` prefix, that is Finding 8 in `references/integrate-vios-service.md § Known Integration Constraints`. Report it. Do not switch to a hand-built binary endpoint.

---

## Examples

Example operation prompts:

- "List the active VIOS sensors and show their stream status."
- "Upload this sample video to VIOS and return the generated stream id."
- "Download a two-second clip from this sensor's recording timeline."
- "Use NvStreamer to upload a file and retrieve its generated RTSP URL."

## Limitations

- VIOS operations require the vios group recorded by `vss configure`. Exit 4 means reconfigure with the operator's ingress origin or deploy; do not curl a constructed URL.
- Most deployments do not require auth, but a deployment can add an external auth layer on the uncovered REST surface.
- Container-side paths in the references use `${VST_CONTAINER_ROOT}` as a neutral placeholder for the VST install root inside the container. Resolve it from the active deployment before using path examples.
- Do not print API keys, bearer tokens, or generated credentials in logs or final responses.

## Troubleshooting

- **Error**: `vss vios list` exits 4. **Cause**: no deployment is recorded, or the recorded deployment does not expose `vst`. **Solution**: `vss configure --base-url "${VSS_PUBLIC_URL}"` when that origin is already set; if it is unset, stop and ask. Or follow the deployment prerequisite.
- **Error**: `vss configure check` exits 3. **Cause**: a previously recorded route is unreachable. **Solution**: report the failure. Do not curl a constructed URL.
- **Error**: uploads fail with `Failed to get media information`. **Cause**: libav packages were not installed in the VIOS container. **Solution**: set `VST_INSTALL_ADDITIONAL_PACKAGES=true` and redeploy. Do not bypass the CLI.
- **Error**: a `media_url` contains `http://http://...`. **Cause**: known URL construction defect in the service. **Solution**: report it; the references document Finding 8. Do not bypass the CLI.
- **Error**: a consumer (RT-CV, RT-Embed, RT-VLM) does not list a source just added to VIOS. **Cause**: the deployment fans a new source out by webhook, asynchronously and with retries, so consumer state trails registration. **Solution**: allow for the delay — the slowest shipped receivers retry for ~30 minutes before giving up. Never call a consumer directly to compensate; [`references/provision-vios-source.md`](references/provision-vios-source.md) has the logs to read if it genuinely never arrives.

---

## Tips

- **jq:** Capture CLI stdout before piping to `jq`, or use `set -o pipefail`. `jq`'s exit code is not the CLI's. See [`AGENTS.md`](../../../AGENTS.md).
- **Time format:** Always ISO 8601 UTC, e.g. `2026-04-10T10:30:00Z` or `2026-04-10T10:30:00.000Z`.
- **streamId header:** Only on the uncovered live/replay/recorder REST surface. Those endpoints require `streamId` as both a path parameter and a request header — include both. Covered clip and snapshot calls are `vss vios clip` and `vss vios snapshot`.
- **Clips:** `vss vios clip` returns `media_url`. The `/url` double-`http://` defect is a service bug documented in the references, not a reason to bypass the CLI.
- **Sensor name:** Address media by sensor name. `--type video` is a file-backed sensor; `--type stream` is RTSP. Read the type from `vss vios list`. Delete with `vss vios delete --type video|stream --sensor NAME`.
- **Recorded timeline after upload:** `vss vios add --type video` records the file. Read the anchored range with `vss vios timeline --sensor NAME` before a later clip or snapshot. Do not hand-build an upload request.
- **Endpoints:** CLI examples take no endpoint. The origin is whatever `vss configure` recorded.
