---
name: vss-ask-video
description: Use this skill when answering a question about previously analyzed or freshly scoped VSS video. Route through hot context, agent Markdown memory, structured VSS memory, bounded introspection, or an exact-window vss vlm run. Not for retrieval or metadata-answerable questions.
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
> - Query Elasticsearch directly. `vss memory` owns structured recall.
> - Build VIOS clip URLs by hand (e.g. `/vst/api/v1/storage/file/<id>/url`).
>   `--sensor` resolves the sensor, recorded window and clip URL internally.
> - `POST` to `http://<host>:8000/generate` or `/v1/summarize`.
>
> If a CLI operation fails, report the exit code. Do not retry by hand-rolling
> the request or by using a globally installed `vss`. Do not separately inspect
> media or call another verifier after the CLI returns.

## Prerequisites

Run `vss configure` once per deployment. Bootstrap, exit codes, and common CLI
rules live in [AGENTS.md](../../../AGENTS.md).

Direct VLM requires:
- A configured VSS deployment.
- Reachable RT-VLM.
- VIOS when using sensor-based media.

Introspection requires:
- Memory enabled and Elasticsearch reachable.
- Existing VSS memory records.
- Introspection configured and enabled.
- The judge endpoint reachable from the CLI execution environment.
- Its configured credential environment variable available, when one is named.
- RT-VLM only when a bounded visual follow-up is required.

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)

"${VSS[@]}" configure check
"${VSS[@]}" configure memory show
"${VSS[@]}" configure memory check
```

These checks show endpoint names and credential environment-variable names, not
secret values. A judge URL on `127.0.0.1` works only when the OpenClaw Gateway
and the VSS CLI process share a network namespace. Otherwise an operator must
configure a private Gateway URL reachable from the CLI execution environment.

## Memory layers

- **Hot conversation context** is evidence already present in this conversation.
- **Agent Markdown memory** is searched with the harness-native memory tools.
  Markdown search is not a `vss` command.
- **Structured VSS memory** is authoritative data in Elasticsearch, accessed
  only through `vss memory get` and `vss memory query`.
- **Introspection** performs its own structured retrieval, judge call, and
  bounded visual follow-ups through `vss memory introspect`.

The agent decides whether Markdown evidence already answers the question.
Never send raw Markdown documents to the VSS judge.

## Route the request

For a general question about previously analyzed video, use this exact order:

1. Use hot conversation context if it already answers the question.
2. Search agent Markdown memory using the harness-native memory search.
3. If Markdown contains enough evidence, answer directly.
4. If Markdown contains a VSS job/record pointer, retain that pointer as
   grounded scope.
5. Check the configured introspection state if it is not already known in the
   current session.
6. If introspection is enabled, call `vss memory introspect`.
7. If introspection is disabled or unconfigured, retrieve structured VSS memory
   with `vss memory get` or `vss memory query`, but do not introspect.
8. If the available memory still cannot answer, clearly report the missing
   information.

Do not force Markdown search when:
- Hot context already answers.
- The user requests a specific known `job_id` or complete child identity.
- The user explicitly requests a fresh visual inspection of a grounded
  sensor/time window.
- A search skill supplies a pre-resolved bounded `VIDEO_URL`.

Those exact routes remain:
- Exact stored parent -> `vss memory get` or a group-specific `get`.
- Exact fresh sensor/window -> `vss vlm run`.
- Pre-resolved bounded media URL -> `vss vlm run --media-url`.
- Local file with configured VSS -> `vss vlm run --file`.

## Invoke the project-local CLI

OpenClaw may execute every tool call in a fresh shell. Never depend on a shell
function or array defined in an earlier call. Define and invoke the complete
project-local command in the same shell call.

For a stored parent:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)

"${VSS[@]}" memory get --job-id "${JOB_ID}"
```

For a known child, pass the complete identity:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)

"${VSS[@]}" memory get \
  --job-id "${JOB_ID}" \
  --record-type "${RECORD_TYPE}" \
  --record-id "${RECORD_ID}"
```

For structured discovery, use only relevant filters:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)

"${VSS[@]}" memory query \
  --query "${USER_QUESTION}" \
  --sensor-id "${SENSOR_NAME}" \
  --limit 20
```

Valid introspection scope is established by one of:
- `--sensor`
- `--job-id`
- Both `--start-time` and `--end-time`
- Complete child identity: `--job-id`, `--record-type`, and `--record-id`

Never pass `--record-id` alone. `--record-type` and `--group` may refine valid
scope but do not establish it independently.

## Choose visual sampling density

For every introspection or direct VLM call, choose `VLM_FPS` from the visual
task. RT-VLM samples at that rate across the requested window:

- **Skim (`0.5`)**: locate whether or roughly when a sustained event occurred.
- **Locate (`1`)**: default event and action questions.
- **Inspect (`2`)**: fine details such as labels, clothing, object state, or
  precise spatial relationships. Prefer a shorter grounded window before
  increasing density.

RT-VLM keeps the requested FPS only while `fps × clip_seconds` is at most 60 frames
(the same cap as video-understanding). Longer windows are sampled as 60 evenly
spaced frames so the vision token budget is not spent on many tiny images.
Prefer a shorter window before raising FPS.

Do not use fixed `--num-frames` unless the user explicitly requests a fixed
frame budget or a reproducibility workflow requires it. Never combine
`--num-frames` and `--fps`.

## When introspection is enabled

For a general memory-aware question that Markdown does not fully answer:
- Preserve the user's question verbatim.
- Pass only grounded selectors.
- Prefer a known `job_id` from the Markdown pointer.
- Otherwise use a grounded sensor or complete time range.
- Do not run `vss memory query` immediately before introspection merely to
  duplicate its internal retrieval.
- Do not run `vss vlm run` after a completed or partial result. Introspection
  owns bounded VLM follow-ups.

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)
VLM_FPS=1 # choose 0.5 (skim), 1 (locate), or 2 (inspect)

RC=0
RESULT=$("${VSS[@]}" memory introspect \
  --query "${USER_QUESTION}" \
  --sensor "${SENSOR_NAME}" \
  --fps "${VLM_FPS}") || RC=$?

if [ -n "${RESULT}" ]; then
  printf '%s\n' "${RESULT}"
fi
printf 'vss_exit_code=%s\n' "${RC}" >&2
```

Capture stdout and the exit code separately. Useful JSON can precede a nonzero
timeout or backend exit; parse it when present while still respecting the exit
code. Never pipe the CLI directly to `jq`, which would hide the VSS exit code.

Handle the result fields `status`, `sufficient_from_memory`, `answer`,
`memory_evidence`, `sufficiency`, `vlm_evidence`, and `unresolved_gaps`:
- **`completed`**: return `.answer`; when useful say whether memory alone or
  memory plus VLM supplied it, and cite available job/record handles.
- **`partial` with an answer**: return the answer with its limitations and
  relevant `unresolved_gaps`; do not present it as fully confirmed.
- **`partial` without an answer**: explain the failure or unresolved gaps; do
  not invent an answer or repeat internal VLM calls.
- **`no_memory`**: treat it as expected not-found output. Only one direct VLM
  fallback is allowed, and only when an exact sensor plus exact UTC start/end
  range were grounded before introspection. Otherwise request the missing scope.

## When introspection is disabled or unconfigured

Do not call `vss memory introspect` while answering an ordinary video question,
and do not enable it or rewrite static configuration automatically. Users and
the agent may still configure and enable introspection when the user explicitly
asks. If Markdown supplies a `job_id`, use `vss memory get`; otherwise use
`vss memory query` with relevant text, sensor, and time filters. Answer from
the returned records when sufficient. If insufficient, report what is known and
what is missing.

Do not simulate introspection by selecting a sensor/window and automatically
calling VLM. Direct VLM is still allowed only for an explicit fresh-verification
request, an exact grounded sensor/window, or a trusted bounded media handoff.
If the user explicitly asks to enable or configure introspection, explain the
current state and run the project-local configure command. `--enable` alone
fails when introspection was never configured; include the judge endpoint on
first setup:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)

"${VSS[@]}" configure memory introspection \
  --enable \
  --judge-endpoint "${JUDGE_ENDPOINT}"
```

Do not silently substitute ordinary VLM inspection.

## Direct fresh inspection

For a trusted bounded URL or local file:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)
VLM_FPS=1 # choose 0.5 (skim), 1 (locate), or 2 (inspect)

RC=0
RESULT=$("${VSS[@]}" vlm run \
  --prompt "${USER_QUESTION}" \
  --media-url "${VIDEO_URL}" \
  --fps "${VLM_FPS}") || RC=$?
[ "${RC}" -eq 0 ] || [ "${RC}" -eq 6 ] || exit "${RC}"
if [ -n "${RESULT}" ]; then
  printf '%s\n' "${RESULT}"
fi
printf 'vss_exit_code=%s\n' "${RC}" >&2

# A configured VSS local-file request uses:
# RESULT=$("${VSS[@]}" vlm run --prompt "${USER_QUESTION}" --file "${VIDEO_FILE}") || RC=$?
```

For an exact named VIOS sensor/window:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run \
  --project "${VSS_REPO_ROOT}/services/agent" \
  --no-dev \
  --extra cli \
  vss)
VLM_FPS=1 # choose 0.5 (skim), 1 (locate), or 2 (inspect)

RC=0
RESULT=$("${VSS[@]}" vlm run \
  --prompt "${USER_QUESTION}" \
  --sensor "${SENSOR_NAME}" \
  --start-time "${START_TIME}" \
  --end-time "${END_TIME}" \
  --fps "${VLM_FPS}") || RC=$?
[ "${RC}" -eq 0 ] || [ "${RC}" -eq 6 ] || exit "${RC}"
if [ -n "${RESULT}" ]; then
  printf '%s\n' "${RESULT}"
fi
printf 'vss_exit_code=%s\n' "${RC}" >&2
```

For a confirmed search handoff, use only the supplied bounded `VIDEO_URL` and
visual question. Do not rerun search, resolve another sensor/window, or treat
retrieval metadata as visual evidence. A sensor route must use `--sensor`; do
not substitute `vss vios clip` or raw HTTP. Cite the returned `job_id`, sensor,
and window. Exit 6 means the answer exists but persistence failed; retain the
answer and report that limitation.

## Examples

- **Hot conversation:** The previous turn says, "A forklift crossed the loading
  aisle at 10:14 UTC." Answer `10:14 UTC` directly; search nothing.
- **Markdown sufficient:** Native agent Markdown memory search finds a note that
  directly answers the question -> answer from it; call no VSS command.
- **Markdown incomplete:** Retain its `job_id`, inspect known/configured state,
  then introspect by that job when enabled.
- **Explicit stored parent:** "Show me the summary from job `sum-01JXYZ`." ->
  `vss memory get --job-id sum-01JXYZ`.
- **Disabled introspection:** Search Markdown, then structured memory. Do not
  introspect, enable it, or escalate automatically to VLM.
- **Exact fresh verification:** "Freshly verify whether the worker wore a hard
  hat on `dock_cam` from `2026-08-13T20:00:00Z` to
  `2026-08-13T20:00:30Z`." -> `vss vlm run` with that exact sensor/window.
- **Search handoff:** a user-confirmed vss-search-archive handoff with a pre-resolved bounded VIDEO_URL -> Path A `--media-url`.
- **No memory with scope:** Introspection returns `no_memory`, while trusted
  context provides `dock_cam` and `2026-08-13T20:00:00Z` through
  `2026-08-13T20:00:30Z` -> run one `vss vlm run` for exactly that interval.
- **No memory without scope:** "Did a forklift enter the loading area last
  week?" returns `no_memory`, with no exact sensor/window -> explain no matching
  memory/window exists and ask for the sensor and exact UTC window; do not run
  the VLM.

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
- **`/vss-generate-video-report`** — timestamped reports; this skill returns an
  ad-hoc answer.
- **`/vss-query-analytics`** — already-computed incidents/metrics.
