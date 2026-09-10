---
name: vss-ask-video
description: Routes VSS video questions through hot conversation context, stored memory, bounded introspection, or an exact-window vss vlm run CLI job, including a user-confirmed vss-search-archive handoff with a pre-resolved bounded VIDEO_URL. Not for retrieval or metadata-answerable questions.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Ask a VSS video question

Answer from the cheapest grounded source that can satisfy the question. For a
running VSS deployment, use the project-local `vss` CLI. Do not call an
OpenAI-compatible `/chat/completions` endpoint directly or fall back to raw REST
when a CLI command fails.

This skill does not call `POST /generate` on the VSS agent. It requires a
**deployed VSS with `vss configure` already run**.

> **Hard rule — never substitute a hand-built HTTP call for the CLI.**
> Specifically, do **not**:
> - `POST` to `/v1/chat/completions` yourself. `vss vlm run` owns that call.
> - Build VIOS clip URLs by hand (e.g. `/vst/api/v1/storage/file/<id>/url`).
>   `--sensor` resolves the sensor, recorded window and clip URL internally.
> - `POST` to `http://<host>:8000/generate` or `/v1/summarize`.
>
> If `vss vlm run` fails, report the exit code. Do not retry the question by
> hand-rolling the request.

## Prerequisites

A deployed VSS stack with **`rt_vlm` reachable through the configured origin**.
Run `vss configure` once per deployment. Bootstrap, exit codes, and common CLI
rules live in [AGENTS.md](../../../AGENTS.md).

```bash
vss configure check
# Expected: rt_vlm   ok   http://<origin>/rtvi-vlm   HTTP 200
```

## Instructions

### Bootstrap the CLI once

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
vss() { uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss "$@"; }
vss --version
```

Never construct an endpoint or replace a failed CLI call with raw HTTP.

### Choose exactly one initial route

Apply these routes in order:

1. **Hot conversation context -> answer directly.** If current messages or
   current-turn tool output already contain the answer, answer from that
   evidence. Do not query memory or run a model.
2. **Explicit stored summary/result -> `vss memory get`, `vss summarize get`,
   or `vss memory query`.** For a known `job_id`, read the stored parent with
   `vss memory get` or, for a summarize job, `vss summarize get`. To find or
   list stored results by text, sensor, type, status, or time, use `query`.
3. **General video question where past memory may exist -> `vss memory
   introspect`.** Use this for a substantive question about prior video analysis
   when hot context does not answer it and the user did not request one exact
   stored record or fresh visual verification.
4. **Exact sensor/time or explicit fresh visual verification -> `vss vlm
   run`.** Bypass introspection when the user supplies a grounded VIOS sensor
   plus exact ISO-8601 UTC start/end times, or explicitly asks to watch, inspect,
   re-check, or freshly verify that exact window. A user-confirmed
   vss-search-archive handoff with a pre-resolved bounded `VIDEO_URL` uses this
   route as Path A; do not rerun search or resolve a different interval.
5. **`introspect` returns `no_memory` -> conditional `vss vlm run`.** Run the
   VLM only when a grounded sensor and exact ISO-8601 UTC start/end window are
   already available from the request, hot context, or trusted tool output.
   Otherwise explain that no matching memory was found and that an exact
   recorded sensor/window is needed. Never invent or broaden a window.

Do not call `memory query`, `memory introspect`, and `vlm run` speculatively or
in parallel. The only escalation is the specified `no_memory` fallback.

For a confirmed search-result handoff, use only the caller-supplied `VIDEO_URL`
and visual question. Treat that URL as Path A; do not rerun search or
resolve a different interval. Do not consume similarity scores, filenames,
object IDs, or other retrieval metadata as visual evidence, and do not rerun
search, resolve a sensor, broaden the clip, or choose another interval. The
caller owns verdict validation and any fallback after this skill returns.

### Run the selected command

For an explicit stored parent:

```bash
vss memory get --job-id '<job-id>'
# summarize jobs may also use:
vss summarize get --job-id '<job-id>'
```

For a known child, add both `--record-type event|search_hit|incident` and
`--record-id '<record-id>'`. For discovery, apply only relevant filters:

```bash
vss memory query --query '<search text>' --sensor-id '<sensor-name>' --limit 20
```

For bounded introspection, preserve the user's question verbatim:

```bash
vss memory introspect --query '<user question>' --sensor '<sensor-name>'
```

`introspect` requires grounded, useful scope: `--sensor`, `--job-id`,
`--record-id`, or a complete UTC time range. Add only grounded selectors. A time range requires
both `--start-time` and `--end-time`. `--record-type` and `--group` refine scope
but do not establish it. The command may perform its own bounded VLM follow-ups;
do not duplicate them manually. `no_memory` returns JSON with status
`"no_memory"` and exit code 5; this is an expected not-found result, not a
general command or backend failure. Do not automatically run a VLM afterward
unless an exact sensor and exact ISO-8601 UTC start/end window were already
grounded before introspection returned.

For one fresh inspection, use **`vss vlm run`** — never a hand-built VLM request.

**Path A — URL or local file** (default when the user or search handoff provides
media directly; skip the sensor check):

```bash
VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" --no-dev --extra cli vss)
# Exit 6 means the answer was produced but could not be written to memory.
check_rc() { [ "$1" -eq 0 ] || [ "$1" -eq 6 ] || { echo "vss vlm run failed (exit $1)" >&2; exit "$1"; }; }

RC=0
RESULT=$("${VSS[@]}" vlm run --prompt "${USER_QUESTION}" --media-url "${VIDEO_URL}") || RC=$?
check_rc "${RC}"

# Local file (complete MP4 data URI, not extracted JPEG/image blocks):
# RESULT=$("${VSS[@]}" vlm run --prompt "${USER_QUESTION}" --file "${VIDEO_FILE}") || RC=$?
```

**Path B — named VIOS sensor** (optional window). List sensors first even when
the user names the sensor. Then:

```bash
RC=0
RESULT=$("${VSS[@]}" vlm run \
  --prompt "${USER_QUESTION}" \
  --sensor "${SENSOR_NAME}" \
  --start-time "${START_TIME}" \
  --end-time "${END_TIME}") || RC=$?
check_rc "${RC}"
```

A question that names a sensor is Path B and MUST use `--sensor`. Do not
hand-build a `/storage/file/<streamId>/url` call. The window must be fully
recorded and start before end. Cite the returned `job_id`, sensor, and window.
Do not substitute `vss vios clip`, direct VLM HTTP, or a local copy for a
failed `vss vlm run`.

### Return a grounded answer

State whether the answer came from hot context, stored memory, introspection, or
a fresh VLM job when that distinction matters. Preserve uncertainty and cite
available handles. Extract `.answer` from the CLI JSON. On CLI failure, report
the diagnostic and a useful recovery step; do not fabricate an answer.

If `vss vlm run` exits non-zero, stop and report the error:
- `2` — invalid input. Fix the request; do not retry it unchanged.
- `3` — backend unreachable. Retrying is reasonable.
- `4` — required service missing from the recorded config. Re-run `vss configure`.
- `5` — the sensor name is not in VIOS. List sensors and confirm the name.
- `6` — the answer was produced but could not be written to memory. Keep the answer.
- `7` — timeout (raise `--timeout`).

## Examples

- **Hot conversation:** The previous turn says, "A forklift crossed the loading
  aisle at 10:14 UTC." User: "When did the forklift cross?" -> answer `10:14
  UTC` directly; run no command.
- **Explicit stored parent:** "Show me the summary from job `sum-01JXYZ`." ->
  `vss memory get --job-id sum-01JXYZ` or `vss summarize get --job-id
  sum-01JXYZ`.
- **Stored-result discovery:** "Find stored search results about forklifts on
  `dock_cam`." -> `vss memory query --query 'forklifts' --sensor-id dock_cam`.
- **General memory-aware question:** "Was anyone missing PPE on
  `warehouse_safety_0001`?" -> `vss memory introspect --query ... --sensor
  warehouse_safety_0001`.
- **Exact fresh verification:** "Freshly verify whether the worker wore a hard
  hat on `dock_cam` from `2026-08-13T20:00:00Z` to
  `2026-08-13T20:00:30Z`." -> `vss vlm run` with that exact sensor/window.
- **Search handoff:** confirmed unverified hit with bounded `VIDEO_URL` -> Path A
  `--media-url`.
- **No-memory with scope:** Introspection returns `no_memory`, while trusted
  context provides `dock_cam` and `2026-08-13T20:00:00Z` through
  `2026-08-13T20:00:30Z` -> run one `vss vlm run` for exactly that interval.
- **No-memory without scope:** "Did a forklift enter the loading area last
  week?" returns `no_memory`, with no exact sensor/window -> explain no matching
  memory/window exists and ask for the sensor and exact UTC window; do not run
  the VLM.

## Explicit non-VSS local-file fallback

This separate fallback applies only when the user explicitly asks about a
standalone local file/base64 video and no VSS deployment or VIOS sensor is in
scope. It may use a caller-provided OpenAI-compatible VLM according to that
service's documented media format. Label the result as non-VSS.

For an MP4, send the **native MP4 bytes as one video input**. Read the file
directly, base64-encode the complete byte sequence, and construct exactly:

```text
data:video/mp4;base64,<base64 of the complete MP4 file>
```

Pass that URI in one OpenAI-compatible `video_url` content part:

```json
{"type":"video_url","video_url":{"url":"data:video/mp4;base64,<complete MP4 base64>"}}
```

Do not run `ffmpeg`, OpenCV, or any frame extractor. Do not convert the video
to JPEG/PNG images or send an `image_url` array: extracted frames are not the
requested native video input and can discard motion, timing, and audio. If the
caller-provided VLM does not support a native MP4 `video_url` data URI, report
that incompatibility instead of silently changing the media format.

Never enter this fallback after `vss memory introspect`, use it for a named VIOS
sensor or stored VSS result, or combine local/base64 media with introspection.
If the request could refer to VSS memory, ask the user to choose the standalone
file or the VSS sensor. If a VSS deployment is configured, use Path A
`vss vlm run --file` / `--media-url` instead.

## Negative triggers

- Archive/semantic similarity retrieval ("find videos of ...") -> `/vss-search-archive`.
  This skill may inspect only the pre-resolved bounded clip that search hands
  off after confirmation; it never performs the retrieval itself.
- Long-form summarization -> `/vss-summarize-video`.
- Structured reports -> `/vss-generate-video-report`.
- Existing analytics incidents or metrics -> `/vss-query-analytics`.
- Deployment/profile changes -> `/vss-deploy-profile`.

## Cross-Reference

- **`/vss-manage-video-io-storage`** — optional Path B upload semantics.
- **`/vss-deploy-dense-captioning`** — optional standalone RT-VLM. Do not
  re-run `vss configure` against a standalone RT-VLM URL if you also need VIOS.
- **`/vss-generate-video-report`** — timestamped reports; this skill returns an
  ad-hoc answer.
- **`/vss-query-analytics`** — already-computed incidents/metrics.
