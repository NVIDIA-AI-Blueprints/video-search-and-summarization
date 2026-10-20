# Real-time Alerts: Chunking or Streaming VLM

Read for Docker `FOUNDATION=alerts`, `MODE=2d_vlm`, including custom builds on
that Foundation. Do not apply to `2d_cv`, Helm, warehouse, or standalone RTVI.
The user-facing setup is `docs/agent-workflow-rt-alert.mdx`, **Enable Streaming
VLM (Optional)**. Read that section and the current checked-in config before
materializing settings; keep the documentation and generated build aligned.

## Intake before generation

Apply `SKILL.md`'s **Q2a-inference** before preflight, placement, or artifact
generation, irrespective of entry mode:

1. Ask **Chunking (default)** or **Streaming** when the user has not selected
   either. Explain that chunking waits for a complete chunk and streaming
   processes sampled frames incrementally, generally reducing alert latency.
2. If Streaming is selected without a backend, ask **Native inside RTVI VLM**
   or **Separate streaming NIM**. Wait for the answer.

Reuse explicit answers; do not re-ask them. "Real-time" alone is not a streaming
selection. Neither silence nor an autonomous request supplies a missing mode
or backend; an explicit request to use defaults selects Chunking. Ask no
streaming-variant question when Chunking was selected.

After selection, collect only missing placement/model values. Native uses the
integrated model selected from `services/rt-vlm.md`; separate NIM needs an
accessible `STREAMING_NIM_IMAGE`, its served model ID (`VLM_NAME`), and
`STREAMING_NIM_DEVICE_ID`. The checked-in overlay selects Cosmos3 Nano Reasoner
BF16. Do not replace a requested model with that model silently. Confirm the
image supports the persistent streaming-session API, not only chat completions.
Use registry/model credentials through the existing credential gate, including
`ngc` for the separate local NIM; never ask for credentials pasted into chat.

## Build-local rules and environment

Generate under `_builds/<name>/` only. Never edit the Foundation's
`overrides.env`, `realtime-config.yml`, or service Compose files. Keep
`MODE=2d_vlm`, `ALERT_AGENT_ALWAYS_ON=true`, the two mode-selected UI flags,
and `RTVI_VLM_MESSAGE_BUS=kafka` / `RTVI_VLM_KAFKA_ENABLED=true` for all three
choices. Follow `composition.md` for derived path and host values.

### Chunking (default)

- Keep `RTVI_STREAMING_VLM_ENABLED=false`, the integrated
  `RTVI_VLM_MODEL_TO_USE=cosmos-reason3`, and no separate-NIM overlay.
- Inherit the checked-in `realtime-config.yml` with `inference_mode: chunked`.
  Its omitted chunk/sampling fields retain their existing service defaults;
  do not copy the streaming example's one-second chunk into this branch.
- Leave native-streaming-only tuning, eager execution, and warmup overrides at
  their Foundation defaults. Do not create a streaming rule payload or start
  either NIM service.

For an existing streaming build being switched back, remove its streaming
overrides and overlay before re-resolving; restore the inherited rule source
and the integrated checkpoint's matching `VLM_NAME`.
Preserve unrelated user customizations. Use the normal project teardown or
`--remove-orphans` handling for the old NIM containers.

### Shared rule configuration for either streaming variant

Copy the Foundation's always-on rules to
`_builds/<name>/patches/realtime-config.yml` and modify the selected rules there.
Retain each rule's identity and requested detection task. Keep
`model: "${VLM_NAME}"` literal for Alert Bridge's existing startup substitution.
Apply the shared `always_on_params` example in the docs:

```yaml
inference_mode: streaming_vlm
streaming_frame_policy: ordered
streaming_question_on_decode: false
streaming_window_frames: 8
chunk_duration: 1
chunk_overlap_duration: 0
num_frames_per_second_or_fixed_frames_chunk: 1
max_tokens: 16
response_format:
  type: choice
  choices: ["yes", "no"]
```

Put the complete detection condition and yes/no instruction in `system_prompt`,
and the corresponding question in `prompt`, as shown in the docs. Do not leave
the inherited generic system prompt when the task was only in `prompt`.
Rules sharing a camera must agree on chunk/sampling settings. This config
creates always-on rules; it does not rewrite previously persisted REST rules.

Set the mount source in the build's `override.env` to an absolute path:

```ini
VLM_AS_VERIFIER_CONFIG_FILE_REALTIME=${BUILD_DIR}/patches/realtime-config.yml
```

Set `BUILD_DIR` before this assignment. No `alert-bridge.yml` patch is needed:
the existing Compose service already mounts and renders this file.

### Native inside RTVI VLM

Use the native settings listed in the docs in the build's `override.env`:
`RTVI_STREAMING_VLM_ENABLED=true`, `RTVI_VLM_MODEL_TO_USE=cosmos-reason3`,
warmup skipped, frame timestamps off,
batch/session capacity 8, window capacity 8, total segment capacity 64, and
session token budget 14000, with generated-text retention, exact compaction,
and raw-key shadow disabled. Copy the exact variable names/values from the
native option, then adjust capacity only for the selected workload and GPU
memory. Keep one VLM worker and its stream history on the selected VLM GPU.
Use the integrated checkpoint and its matching advertised model ID.

Keep the native backend and the shared rule payload in the same build. Do not
include `compose.streaming-nim.yml`, start a NIM peer, or build a custom RTVI
image. This is a rule/environment delta, and uses the shipped RTVI container.

### Separate streaming NIM

Use the separate-NIM settings listed in the docs in the build's `override.env`:
native streaming disabled, `RTVI_VLM_MODEL_TO_USE=openai-compat`, the selected
streaming NIM image and device, and `VLM_NAME` equal to NIM's served model ID
(the documented Nano BF16 example is `nvidia/cosmos3-nano-reasoner`). Keep
`VLM_NAME_SLUG=none`; do not activate an ordinary `vlm_*` NIM as a second peer.
Set RTVI's decoding GPU and visible device consistently and plan the NIM GPU
separately from the LLM. The documented defaults are eight NIM sessions, 0.90
NIM memory utilization, RTVI batch size 1, and ten forwarding workers. Size NIM
with `NIM_STREAMING_MAX_SESSIONS`, not native `RTVI_STREAMING_VLM_MAX_*`.

Merge the checked-in overlay in the **same ordered include path list** as
the root model, before any other build-specific service patches:

```yaml
include:
  - path:
      - ../../deploy/docker/compose.yml
      - ../../deploy/docker/developer-profiles/dev-profile-alerts/compose.streaming-nim.yml
```

Do not use a second independent `include` entry, which would import conflicting
`rtvi-vlm` resources. Do not copy the overlay or edit the shared RTVI service.
The overlay's NIM and cache initializer share the existing `rtvi-vlm` profile
gate: keep that canonical key in `COMPOSE_PROFILES`; do not invent new profile
keys. Report the two added **services**, `streaming-nim` and
`streaming-nim-cache-init`, in the delta architecture even though the profile
key set does not need an addition. This is a streaming configuration delta,
not a harness-only customization; preserve the other selected capabilities.

The overlay sets RTVI to `openai-compat`, enables
`VIA_VLM_STREAMING_NIM_ENABLED`, disables the native backend, and uses
`http://streaming-nim:8000/v1` internally. Alert Bridge and the Agent still
address RTVI, not NIM. Do not change their VLM URLs to the NIM container.
The overlay also prepares the cache, waits for NIM readiness, and enables
live-stream EOS session cleanup. It needs no NIM host port or host-network
Kafka/Redis overrides from the standalone performance harness.

## Resolve, review, deploy, and verify

Follow the existing ordered env layers, normalization, validation, and
`resolved.yml` lifecycle from `composition.md` and `deployment.md`. Both the
checked-in overlay and the rule mount must be baked into that resolved file;
do not add an overlay or env flags only at `up` time. Apply the same build to
later readiness and teardown commands. In the architecture review and final
summary name Chunking, Streaming/native, or Streaming/separate NIM and the
chosen model/GPU placement. Do not apply either streaming branch to `2d_cv`.

Before deployment, check the resolved model and the mounted rule payload:

| Choice | Required result |
| --- | --- |
| Chunking | Native disabled, no NIM forwarding, inherited chunked rules, no NIM peer. |
| Streaming/native | Native enabled, integrated backend, generated streaming rules, no NIM peer. |
| Streaming/separate NIM | Native disabled, NIM forwarding enabled, `openai-compat`, generated streaming rules with the served NIM model ID, both NIM services present, RTVI waiting for healthy NIM. |

Require the normal Alerts/RTVI readiness checks. For separate NIM also require
the cache initializer to have exited successfully and NIM to be healthy;
RTVI readiness alone is insufficient. Never register a stream or create a
REST rule just to prove readiness; do so only when requested, via the operation
skills. With an authorized stream, verify verdicts reach Alert Bridge/UI.
