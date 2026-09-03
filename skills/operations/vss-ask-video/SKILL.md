---
name: vss-ask-video
description: Use this skill to ask a fresh visual question about a recorded video clip using the vss vlm run CLI, including a user-confirmed vss-search-archive handoff with a pre-resolved bounded VIDEO_URL. Not for retrieval or metadata-answerable questions.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Video QnA using `vss vlm run`

Answer a fresh visual question about a video by running **`vss vlm run`** — the VSS CLI command
that resolves the VLM endpoint from the recorded deployment config, sends the user's question with
the video, persists the answer to memory, and returns the result. **This skill does not call**
`POST /generate` on the VSS agent. It requires a **deployed VSS with `vss configure` already run**
and a reachable `rt_vlm` service.

> **Hard rule — every answer comes from `vss vlm run`.** Every question, including
> **temporal / timing ones** ("at what timestamp did X happen", "how long", "when does Y
> start"), is answered by a single **`vss vlm run`** call. A timing question is not a
> reason to reach for another tool: put it in `--prompt` and read the timing out of the
> answer.
>
> Never substitute a hand-built HTTP call for the CLI. Specifically, do **not**:
> - `POST` to `/v1/chat/completions` yourself. `vss vlm run` owns that call and sends the
>   frame-sampling parameter with it; a hand-rolled request omits it, the model sees only
>   the opening frame, and timing questions become unanswerable.
> - Build VIOS clip URLs by hand (e.g. `/vst/api/v1/storage/file/<id>/url`). `--sensor`
>   resolves the sensor, the recorded window and the clip URL internally.
> - `POST` to `http://<host>:8000/generate` (the agent's summarize pipeline) or
>   `/v1/summarize` under any circumstances.
>
> If `vss vlm run` fails, report the exit code (see *Error Handling*). Do not retry the
> question by hand-rolling the request.

---

## Agent harness

**Harness-agnostic** — whatever runs it (Claude Code, Codex, Cursor, or the NAT VSS Agent)
calls `vss vlm run` via the CLI. A running `vss-agent` is optional; what is required is
`vss configure` having been run once against the deployed VSS so the rt_vlm service URL is
recorded.

---

## When to Use

- The user asks **what happens in the video**, what **objects / people / actions** appear,
  **colors**, **timing**, **safety**, or other **visual facts** that require watching the clip.
- The user asks for **details** that **cannot be answered** from existing messages, summaries,
  Elasticsearch/MCP results, or filenames alone—you need **model inference on the video**.
- Follow-up questions about **content details** after a coarse summary or after report generation.
- `vss-search-archive` has already displayed only `unverified` results, the user
  explicitly confirmed visual verification, and the caller supplies one exact
  bounded clip as `VIDEO_URL`. Treat that URL as Path A; do not rerun search or
  resolve a different interval.

---

## Negative Triggers

Do **not** use this skill when the request is one of the following:

- A **database / MCP / prior tool output** already answers the question, unless
  the user explicitly wants fresh visual verification. The confirmed bounded
  `vss-search-archive` handoff above is the only search-result exception; use
  `/vss-query-analytics` for analytics-result verification.
- Archive/semantic similarity retrieval ("find forklifts", "search all videos for tailgating")
  → use `/vss-search-archive`. This skill may inspect only the pre-resolved
  bounded clip that search hands off after confirmation; it never performs the
  retrieval itself.
- A request for a **formatted/structured report** ("generate a report", "analysis report")
  → use `/vss-generate-video-report`.
- Summarizing a long recording → use `/vss-summarize-video`.
- Deploy/teardown/profile changes → use `/vss-deploy-profile`.

---

## Prerequisites

A deployed VSS stack with **`rt_vlm` service reachable through the configured origin**. Run
`vss configure` once per deployment — all subsequent `vss vlm run` calls read the recorded URL.

### Setup

Set up the CLI and run `vss configure` per [AGENTS.md](../../AGENTS.md).

Verify `rt_vlm` was discovered:

```bash
vss configure check
# Expected: rt_vlm   ok   http://<origin>/rtvi-vlm   HTTP 200
```

Bootstrap detail, exit codes and the rules that go with them are in
[AGENTS.md](../../AGENTS.md).

---

## Instructions

1. **Run the numbered steps** — *Step 1* (obtain the video source — directly from the user, or
   optionally resolve a clip URL from VST/VIOS) → *Step 2* (`vss vlm run` — one command) →
   *Step 3* (return the answer).
2. **Return only the final answer text** to the user.

For a confirmed search-result handoff, use only the caller-supplied `VIDEO_URL`
and visual question. Do not consume similarity scores, filenames, object IDs,
or other retrieval metadata as visual evidence, and do not rerun search,
resolve a sensor, broaden the clip, or choose another interval. The caller owns
verdict validation and any fallback after this skill returns.

---

## Sensor check (only when sourcing the clip from VST/VIOS)

**This section applies only on Step 1, Path B — when you are sourcing the video from VST/VIOS.**
If the user provided the video directly (a file path or URL), **skip this entirely** and use
Step 1, Path A.

When using VST/VIOS, **you MUST list VST sensors before resolving a clip URL.** This is required
even when the user names the sensor explicitly, even when the user asserts the video is already
uploaded, and even when a previous turn appeared to use the same video. Do not skip this step.

> **Running the CLI.** Bootstrap, `vss configure`, exit codes and the sensor-resolution
> rules live in [AGENTS.md](../../AGENTS.md) at the repo root — read it once rather than per skill.

1. List sensors (capture first — a bare pipe into `jq` would hide a failed
   `vss` behind `jq`'s exit code and read as "no sensors"):
   ```bash
   # Each block is its own shell; define what it uses.
   VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" --no-dev --extra cli vss)
   set -o pipefail
   SENSORS=$("${VSS[@]}" vios list --type video) || { echo "vss vios list failed" >&2; exit 1; }
   printf '%s' "${SENSORS}" | jq -r '.sensors[].name'
   ```

2. Compare the returned `name` values against the user-supplied `<sensor-id>` (or **filename stem**,
   e.g. `warehouse_safety_0001`).

   A row may carry an `error` field — VIOS listed the sensor but could not describe it (no
   streams, or no id). It is still listed on purpose: the name **exists**, so treating it as
   absent and uploading would create a duplicate and a 409.

3. **If a matching sensor is present** → proceed to Step 1. If its row carries an `error`,
   report that rather than uploading over it.

4. **If no matching sensor is present** — upload the video first, then re-list to confirm the new
   sensor appears:
   ```bash
   # Each block is its own shell; define what it uses.
   VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" --no-dev --extra cli vss)
   "${VSS[@]}" vios add /path/to/<filename>
   ```
   See `/vss-manage-video-io-storage` for the REST-level upload semantics (v1 vs v2, conflict
   handling, delete flow). In interactive runs, confirm with the user before uploading. **Never**
   upload without first running the sensor-list check above.

---

## Step 1 — Identify the video source

Determine whether the video comes from the user directly (Path A) or from a named VST/VIOS
sensor (Path B). This decides which `vss vlm run` flag to use in Step 2.

### Path A — provided directly by the user (default; no VST/VIOS)

If the user hands you a file path or a URL, use Path A in Step 2:

- **URL** → pass as `--media-url <url>`. RT-VLM fetches the video directly.
- **Local file** → pass as `--file /path/to/clip.mp4`. The CLI reads the file and sends it
  inline as a base64-encoded `data:video/mp4;base64,…` URI.

A user-confirmed search-result handoff with a pre-resolved bounded `VIDEO_URL` uses this
same path. Do not discard that URL and enter Path B merely because the caller also retains
a sensor ID or timestamps for reporting.

Then go straight to Step 2 — **skip the Sensor check**.

### Path B — resolve from VST/VIOS (optional)

> **Hard rule — a question that names a sensor is Path B and MUST use `--sensor`.**
> When the question references a VIOS sensor (e.g. `warehouse_safety_0001`), pass
> `--sensor <name>` to `vss vlm run`. The CLI resolves the sensor by name, reads the recorded
> range, mints a normalised and warmed clip URL, and sends it to RT-VLM — all in one call.
> Do **not** hand-build a `/storage/file/<streamId>/url` call or inline a local copy as base64.
>
> **A follow-up that names no video stays on the sensor already in play.** Re-use the same
> `--sensor <name>` (and optionally `--start-time` / `--end-time`) from the prior turn.

When the clip lives on a named sensor: confirm the sensor exists (the *Sensor check* above
— required on this path), then use `--sensor <name>` in Step 2.

To restrict the clip to a known time window, add `--start-time` / `--end-time` (ISO 8601):

```bash
# Optional window. Both or neither — not one alone.
START_TIME='2025-01-01T00:00:00Z'
END_TIME='2025-01-01T00:00:30Z'
```

---

## Step 2 — Ask the VLM

One call, one attempt. `vss vlm run` reads the `rt_vlm` endpoint from the recorded config,
sends the prompt with the video, and prints the result as JSON.

**Path A — URL or local file:**

```bash
# Each block is its own shell; define what it uses.
VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" --no-dev --extra cli vss)
# Exit 6 means the answer was produced but could not be written to memory.
# The answer is still valid, so keep it; only persistence needs retrying.
check_rc() { [ "$1" -eq 0 ] || [ "$1" -eq 6 ] || { echo "vss vlm run failed (exit $1)" >&2; exit "$1"; }; }

# URL (RT-VLM fetches it):
RC=0
RESULT=$("${VSS[@]}" vlm run \
  --prompt "${USER_QUESTION}" \
  --media-url "${VIDEO_URL}") || RC=$?
check_rc "${RC}"

# Local file (inlined as base64):
# RC=0
# RESULT=$("${VSS[@]}" vlm run \
#   --prompt "${USER_QUESTION}" \
#   --file "${VIDEO_FILE}") || RC=$?
# check_rc "${RC}"
```

**Path B — named sensor (with optional time window):**

```bash
# Each block is its own shell; define what it uses.
VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" --no-dev --extra cli vss)
# Exit 6 means the answer was produced but could not be written to memory.
# The answer is still valid, so keep it; only persistence needs retrying.
check_rc() { [ "$1" -eq 0 ] || [ "$1" -eq 6 ] || { echo "vss vlm run failed (exit $1)" >&2; exit "$1"; }; }

# Without a time window (full recorded clip):
RC=0
RESULT=$("${VSS[@]}" vlm run \
  --prompt "${USER_QUESTION}" \
  --sensor "${SENSOR_NAME}") || RC=$?
check_rc "${RC}"

# With a time window:
# RC=0
# RESULT=$("${VSS[@]}" vlm run \
#   --prompt "${USER_QUESTION}" \
#   --sensor "${SENSOR_NAME}" \
#   --start-time "${START_TIME}" \
#   --end-time "${END_TIME}") || RC=$?
# check_rc "${RC}"
```

**Optional flags:**

| Flag | Default | When to use |
|---|---|---|
| `--intent <critic\|report\|qa\|introspection>` | `qa` | Set when the question has a known intent for memory tagging. |
| `--no-persist` | persist | Pass when you do not want the answer written to the memory index. |
| `--model <id>` | first model from rt_vlm | Override when the deployment serves multiple models. |
| `--timeout <sec>` | 30 | Raise for long clips or slow inference. |
| `--num-frames <N>` | 8 | Fixed-frame budget sent to RT-VLM as `num_frames_per_second_or_fixed_frames_chunk`. Raise for long clips where the default 8 frames misses key moments. |

---

## Step 3 — Return the answer

Extract the answer from the JSON result and return it to the user:

```bash
# The CLI emits a body JSON line then a completion-marker JSON line.
# Print the full output so the trajectory contains the status and answer.
printf '%s\n' "${RESULT}"
# Select the line that carries .answer so the marker line is silently skipped.
ANSWER=$(printf '%s' "${RESULT}" | jq -rR 'try (fromjson | objects | select(has("answer")) | .answer)')
```

Return only `$ANSWER` to the user — plain text, light markdown is fine. Do not wrap it in a
report template.

---

## Examples

- "What's happening in this clip? `/home/me/forklift.mp4`" → Path A (`--file /home/me/forklift.mp4`).
- "Is the worker wearing PPE? `https://example.com/clip.mp4`" → Path A (`--media-url https://example.com/clip.mp4`).
- "Is the worker in `warehouse_safety_0001` wearing PPE?" → sensor check → Path B (`--sensor warehouse_safety_0001`).
- "At what timestamp did the worker climb the ladder?" → same Path B; `vss vlm run` handles the clip.
- "What color is the truck at 00:12 in `dock_cam`?" → Path B with `--start-time` / `--end-time`.

---

## Error Handling

- If `vss vlm run` exits non-zero, stop and report the error. The code says what to do next:
  - `2` — invalid input: conflicting or missing media source, a time window without `--sensor`, an unreadable `--file`, or a URL RT-VLM rejected (e.g. SSRF-blocked). Fix the request; do not retry it unchanged.
  - `3` — backend unreachable: rt_vlm returned 5xx, refused the connection, or replied with an unusable or empty answer. Infrastructure rather than the request — retrying is reasonable.
  - `4` — required service missing from the recorded config: `rt_vlm` for every request, or `vst` when using `--sensor`. Re-run `vss configure` against an origin that exposes every required service.
  - `5` — the sensor name is not in VIOS. Do not retry as-is: list the sensors (see *Sensor check*) and confirm the intended name with the user.
  - `6` — the answer was produced but could not be written to memory. The answer on stdout is valid; only the memory write failed.
  - `7` — timeout (raise `--timeout`).
- If **no video is available** (neither a URL/file nor a sensor), stop and ask the user for one.
- If `rt_vlm` is not listed as `ok` in `vss configure check`, the deployment does not serve it.
  Stop and report that; standing the service up is a deployment task (see *Cross-Reference*).

---

## Cross-Reference

- **`/vss-manage-video-io-storage`** — *optional* (Step 1, Path B): REST-level upload semantics.
- **`/vss-deploy-dense-captioning`** — *optional*: stand up a standalone RT-VLM if rt_vlm is
  not deployed. **Do not re-run `vss configure` against the standalone RT-VLM URL** if you also
  use Path B (`--sensor` / `vss vios`): doing so replaces the deployment config with one that
  lacks VIOS routes, breaking the sensor path. Instead, expose RT-VLM through the same origin
  as VIOS and re-run `vss configure` against that shared origin.
- **`/vss-generate-video-report`** — timestamped **reports** via the same VLM path; this skill
  returns an ad-hoc **answer**, not a report.
- **`/vss-query-analytics`** — read already-computed incidents/metrics (no live VLM inference).
