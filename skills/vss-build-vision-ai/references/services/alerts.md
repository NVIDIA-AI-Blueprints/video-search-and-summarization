# Alerts Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alert verification and real-time bridge | `alert-bridge` |
| Alerts analytics API | `vss-video-analytics-api` |

`vss-video-analytics-api` is a common Compose service with one profile key and one
container name across all Foundations. Include that key when the build needs the
REST query surface; never add a Foundation-specific alias or a second API
instance.

Host-CLI and NemoClaw analytics do not require `vss-va-mcp`; they use
`vss analytics` through `vss-video-analytics-api`. Stock Alerts still ships
`vss-agent`, whose `config.yml` points `incident_report_agent` and
`rtvi_vlm_alert` at `video_analytics_mcp`, so keep `vss-va-mcp` beside
`vss-agent` until those tools move to the REST API. That MCP surface is owned
by `agent.md`; the SOP-report-specific patch is owned by `sop.md`.

## Required peers

- `alert-bridge` requires Kafka, Elasticsearch, topic initialization, Redis, and
  **no service-definition patch**: the stock definition carries the `alert-bridge`
  profile gate, reads `VLM_BASE_URL`/`VLM_NAME` from env, and mounts its verifier
  configs from env-interpolated sources (`VLM_AS_VERIFIER_CONFIG_FILE*`). Wire it in
  `override.env` — add `alert-bridge` to `COMPOSE_PROFILES` and point those
  mount-source vars at the checked-in alerts verifier configs (not inherited on a
  non-`alerts` Foundation); do **not** author an `alert-bridge.yml` patch.
  **One exception:** a custom VLM response parser needs a bind mount the stock
  definition does not have — see
  [Custom VLM response parser](#custom-vlm-response-parser-alert-enhancement).
- **CV verification** (`MODE=2d_cv`): RT-CV (`perception-alerts`) feeds Behavior
  Analytics (`vss-behavior-analytics-alerts`), which emits candidate incidents;
  `alert-bridge` verifies clips with a VLM. Requires RT-CV + Behavior Analytics
  with incident generation enabled. `rtvi-vlm` still runs (verification backend).
- **VLM real-time** (`MODE=2d_vlm`): `alert-bridge` realtime / always-on rules
  drive `rtvi-vlm` over live media. No Behavior Analytics or CV candidate stage.
- Alerts VIOS is **direct** (no `sdr-controller`): stream add/remove reaches RT-CV
  or Alert Bridge via MODE-selected VIOS webhook configs under
  `developer-profiles/dev-profile-alerts/vios/configs/notification_config_${MODE}.json`
  (`VST_NOTIFICATION_CONFIG_PATH`).
- When Behavior Analytics also serves another capability on one shared instance
  (a combined build), it runs as **one** shared instance, not two — converge its
  single mounted JSON config per [`behavior-analytics.md`](behavior-analytics.md);
  its `numWorkersFor*` gates are not env-expressible.
- An explicitly selected `vss-va-mcp` requires its matching legacy config and
  reachable VST/ELK endpoints. Those requirements do not apply to Alerts itself.

## Stream lifecycle (VIOS webhooks)

| `MODE` | Webhook config | `camera_streaming` / `camera_remove` target |
|---|---|---|
| `2d_cv` | `notification_config_2d_cv.json` | RT-CV `POST …/api/v1/stream/add` / `…/stream/remove` (`:9010`) |
| `2d_vlm` | `notification_config_2d_vlm.json` | Alert Bridge `POST …/api/v1/realtime/always-on` (`:9080`) |

Always-on is gated by `ALERT_AGENT_ALWAYS_ON` (substituted into
`alert_agent.always_on` in the verifier config): **true** for real-time /
`2d_vlm`, **false** for verification / `2d_cv`. When enabled, Alert Bridge must
render a valid always-on rules file at boot.

## Write-path topic flow

A build resolves exactly one alerts mode; the two flows are mutually exclusive.
Surface the resolved flow in the architecture preview (SKILL.md step 6 requires
principal data flows and topics).

- **CV verification** (`perception-alerts` + `vss-behavior-analytics-alerts` +
  `alert-bridge` + `rtvi-vlm`): `perception-alerts -> mdx-raw ->
  vss-behavior-analytics-alerts -> mdx-incidents` (candidate incidents) `->
  alert-bridge` (retrieves the clip and runs VLM inference on `rtvi-vlm`) `->
  mdx-vlm-incidents` (verified). Alert Bridge writes the verified record with its
  `verdict` **directly to Elasticsearch** `mdx-vlm-incidents-*` and
  `mdx-vlm-alerts-*` (its `vlm_enhanced_sink`; optionally also to Kafka
  `mdx-vlm-incidents`). Requires RT-CV, Behavior Analytics with incident generation
  enabled, and `rtvi-vlm`.
- **VLM real-time** (`alert-bridge` realtime rules + `rtvi-vlm`, no Behavior
  Analytics): an `alert-bridge` realtime rule drives `rtvi-vlm` over the live stream;
  `rtvi-vlm -> mdx-vlm-incidents` (`RTVI_VLM_KAFKA_INCIDENT_TOPIC`) `-> Logstash ->
  Elasticsearch mdx-vlm-incidents-*`. RT-VLM produces the incident (confirmed at
  source); Alert Bridge orchestrates the rule but does **not** write Elasticsearch.
  This path has no `mdx-raw`/`mdx-incidents` candidate stage. Always-on rules start
  from the VIOS webhook path above when `ALERT_AGENT_ALWAYS_ON=true`.

The modes are exclusive: do not enable Behavior Analytics incident generation for
real-time alerts, and do not route CV verification through the real-time rule path.

## Alert Bridge config rendering

`alert-bridge` entrypoint (`env-substitute.py`) renders the required main verifier
YAML and, when present, the optional realtime / always-on YAML into `/app/runtime`
(including `${VLM_NAME}` in always-on rules). Keep `model: "${VLM_NAME}"` in
`realtime-config.yml` — do not hardcode the model id. `ALWAYS_ON_RULES_CONFIG`
points at the **rendered** file under `/app/runtime` when always-on is enabled.

## Custom VLM response parser (alert enhancement)

"Alert enhancement" swaps Alert Bridge's Yes/No verdict parsing for a generated
class that turns the verification VLM's reply into structured fields. Alert
Bridge loads it once at startup, so it is wired here, at build time. The
contract, the generation steps, a worked example (parser and prompt) and the
verifier settings are in
[`vss-manage-alerts/references/response-parser.md`](../../../operations/vss-manage-alerts/references/response-parser.md);
read it before writing the parser.

**Check before composing:**

- **CV verification only** (`MODE=2d_cv`, with `alert-bridge`). The real-time
  path (`2d_vlm`) never calls a parser: say so and offer a CV build instead of
  building a parser that would never run.
- **Host coding agent only.** The build writes host files and recreates a
  container; a NemoClaw sandbox can do neither, so hand the build to the user or
  a host agent.
- **Global.** One parser serves every alert type. It replaces the Yes/No verdict
  for all of them, not only the one the request named: each verified alert gets
  `info.verdict: ""` and its result in `info.vlm_response`, and none becomes
  `confirmed` or `rejected`. The parser therefore has to accept the reply of
  every alert type the deployment verifies.

Removing a parser is a rebuild as well; response-parser.md's *Remove a parser*
lists what to take out and the prompts to put back.

**Artifacts.** A new bind mount is a service-definition change that env
interpolation cannot express, so this is the one `alert-bridge.yml` patch. Write
only under `_builds/<name>/` (never `deploy/docker/**` or
`dev-profile-alerts/vlm-as-verifier/`):

| File | Content |
|---|---|
| `patches/alert-bridge.yml` | Only `services.alert-bridge.volumes`, with one entry: `${BUILD_DIR:?set BUILD_DIR in override.env}/patches/parsers:/app/parsers:ro`. Compose merges volumes by target, so the stock config mounts stay. |
| `patches/parsers/__init__.py` | Empty. |
| `patches/parsers/<module>.py` | The generated parser. |
| `patches/vlm-as-verifier/config.yml` | A copy of the file the Foundation's `VLM_AS_VERIFIER_CONFIG_FILE` resolves to (it honours `VLM_AS_VERIFIER_CONFIG_FILE_PREFIX`), with the `vlm:` keys from response-parser.md's *Verifier settings* set — `response_parser: "parsers.<module>.<Class>"` among them. |
| `patches/vlm-as-verifier/alert_type_config.json` | A copy of the Foundation's `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` with JSON prompts for every alert type the parser must accept. |

`override.env` sets `BUILD_DIR`, then points `VLM_AS_VERIFIER_CONFIG_FILE` and
`VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` at the two copies with absolute
`${BUILD_DIR}/patches/vlm-as-verifier/` sources. `compose.yml` lists
`./patches/alert-bridge.yml` after the root Compose file. The container runs as
uid `65532`, so make the payloads world-readable:
`chmod -R a+rX "$BUILD_DIR"/patches/parsers* "$BUILD_DIR/patches/vlm-as-verifier"`.

These are the first parser's paths. A revision goes to a new
`patches/parsers-<n>/` and `patches/vlm-as-verifier/config-<n>.yml` (see
*Apply and verify*); every step below acts on the paths the patch and
`override.env` point at.

A parser build is a **Delta build** even when its service set is stock: it
changes the `alert-bridge` service definition.

**Test before approval.** Step 6 comes before any build artifact is written,
so draft the parser and its prompts in a temporary directory and run
response-parser.md's *Test before deploying* there first; a non-zero exit is a
blocker. The Step 6 approval then shows that tested `.py`, and the same file is
written to the build's parser directory. If anything changes the `.py` after
the approval, show it again and get a fresh approval before deploying. After
resolving, confirm `resolved.yml` mounts that parser directory read-only at
`/app/parsers` and that the `/app/configs/config.yml` and
`/app/alert_type_config.json` sources are the build's current copies.

**Approval (SKILL.md step 6).** Show the generated `.py` in full — it is code
that runs inside Alert Bridge — and say that it replaces the verdict for every
alert type, with what follows from that: no verified alert is `confirmed` or
`rejected` any more, so no new incident is marked confirmed and
confirmed-verdict protection (which skips re-verifying an incident already
confirmed) covers only markers set before the parser, until they expire; and the `confirmed` / `rejected`
verdict filters (such as the Video Analytics API's `vlmVerdict`) match none of
the successful results.

**Confirmation — one rule for both skills.** On a stack that is already
running, confirm that global effect with the user first — before any other
build question (harness, models, intake) and before deploying — under
autonomous execution too and whichever skill the request came through: the
request usually names one alert type, and the parser changes the result of
every alert type the user already relies on. For a new build, an autonomous
instruction answers the Step 6 approval; the `.py` and the warning still go in
the conversation and the final summary.

**Apply and verify.** On a stack that is already running, first save the
reply of `curl -sf http://localhost:9080/api/v1/verification/config` (the
Alert Bridge still running) to
`patches/vlm-as-verifier/stored-configs.before.json` — only if that file does
not exist yet, so a later revision never overwrites it; a fresh host has
nothing to save. Then deploy `resolved.yml` per
[`deployment.md`](../deployment.md), as for any build; `docker compose restart`
does not pick up a new mount. Compose recreates Alert Bridge only when its
service definition changes, and a mounted file's contents are not part of it:
a changed parser or config copy written over the same path leaves the old
parser running. Write each revision to a new path — `patches/parsers-<n>/`,
`patches/vlm-as-verifier/config-<n>.yml` — and point the patch and
`override.env` at it. Then run response-parser.md's *Verify after deploying*: the
`Pluggable response parser active: '<dotted path>'` log line, a JSON prompt
stored for every alert type (prompts already in Elasticsearch win over the
build's file), and one incident submitted through the pipeline whose
`info.vlm_response` decodes to the schema. On-demand verification never runs
the parser, so it cannot stand in for that last check.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `MODE` | Select `2d_cv` (verification) or `2d_vlm` (real-time); keep `COMPOSE_PROFILES` aligned. |
| `NEXT_PUBLIC_APP_SUBTITLE` | Derive from the final service set using [`composition.md`](../composition.md#ui-subtitle). |
| `ALERT_AGENT_ALWAYS_ON` | Gate always-on (`true` for `2d_vlm`, `false` for `2d_cv`). |
| `VST_NOTIFICATION_CONFIG_PATH` | MODE-selected VIOS webhook config (`notification_config_${MODE}.json`). |
| `ALERT_BRIDGE_HOST_PORT`, `ALERT_BRIDGE_PORT` | Publish and bind the alert API. |
| `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODE` | Configure the verification VLM (`VLM_NAME` must match RT-VLM `/v1/models`). |
| `RTVI_VLM_BASE_URL`, `RTVI_VLM_MODEL_TO_USE` | Configure real-time VLM alerts. |
| `VLM_AS_VERIFIER_CONFIG_FILE`, `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`, `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | Select mounted verifier/rule configs. |
| `HOST_IP`, `EXTERNAL_IP`, `VST_INTERNAL_URL` | Configure media URL routing. |
| `VSS_VA_MCP_HOST_PORT`, `VSS_VA_MCP_PORT`, `VSS_VA_MCP_CONFIG_FILE` | Configure an explicitly selected legacy video-analytics MCP. |
| `VIDEO_ANALYTICS_API_HOST_PORT`, `VSS_VIDEO_ANALYTICS_API_IMAGE`, `VSS_VIDEO_ANALYTICS_API_TAG` | Configure the alerts analytics API. |

## Sources

- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/alert/scripts/env-substitute.py`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/analytics/video-analytics-api/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/vios/configs/notification_config_*.json`
- `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/`
- `services/alert/src/schemas/base_response_parser.py`
- `services/alert/src/schemas/pluggable_parser_runtime.py`
- `skills/vss-build-vision-ai/references/profiles/alerts.md`
- `skills/operations/vss-manage-alerts/references/integrate-alerts.md`
- `skills/operations/vss-manage-alerts/references/deploy-alerts.md`
- `skills/operations/vss-manage-alerts/references/response-parser.md`
