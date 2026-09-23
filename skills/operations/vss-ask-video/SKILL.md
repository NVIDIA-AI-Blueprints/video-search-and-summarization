---
name: vss-ask-video
description: Answer a visual question when the task supplies an exact local video file or video URL. Send the complete supplied video and the question directly to the configured vLLM backend with `vss vlm run`. Use for benchmark and multiple-choice video questions, including prompts that also provide a time reference.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  vss-requires: "vlm"
---

# Ask vLLM about a video

Take two logical inputs:

- `VIDEO`: one local video file or one pre-resolved HTTP/HTTPS video URL.
- `QUESTION`: the user's visual question.

Send them to the configured vLLM backend with exactly one `vss vlm run`, then
return the model's answer. Preserve the question verbatim, including answer
choices. A time reference is metadata: do not append it to the question and do
not use it to clip or seek the video.

Do not inspect, download, decode, transcode, segment, or extract frames from the
video. Do not call a generic image/video tool, use `ffmpeg`, issue an exploratory
VLM prompt, search the web, or answer from context. The supplied complete video
and the supplied question are the only inputs to inference.

## Requirements

This skill requires `VSS_GATEWAY_ORIGIN` and `VSS_VLM_BACKEND=vllm`. In the
OpenClaw harness, initialize the VSS CLI once when its configuration file is
missing:

```bash
test -n "${VSS_GATEWAY_ORIGIN:?}"
test "${VSS_VLM_BACKEND:?}" = "vllm"
test -f "${HOME}/.vss/config.json" || \
  vss configure --base-url "${VSS_GATEWAY_ORIGIN}"
```

This initialization only records the operator-provided origin. Do not probe
ports, discover another endpoint, or deploy or modify a backend. If either
required environment value is absent or different, report the configuration
mismatch and stop.

When the OpenClaw harness exposes `vss_cli`, use it for both initialization and
the request. Pass the arguments after `vss` as its `args` array. Do not use a
shell command when `vss_cli` is available. In a source checkout, use the
project-local invocation defined in [AGENTS.md](../../../AGENTS.md).

## Run the request

Use only the exact video path or URL supplied by the task. Do not search for a
replacement video. Resolve a relative file path against the current working
directory and require a readable regular file.

For an OpenClaw URL request, make this single inference call after the one-time
initialization above:

```json
{
  "args": [
    "vlm", "run",
    "--media-url", "<supplied-video-url>",
    "--prompt", "<verbatim-question-and-choices>",
    "--fps", "2",
    "--no-persist"
  ]
}
```

Do not add `--num-frames`, `--max-tokens`, a time range, or any other inference
override. The deployed vLLM policy controls those settings.

For a local file:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)
VIDEO_FILE="${VIDEO_FILE:?user-supplied video file}"
USER_QUESTION="${USER_QUESTION:?user-supplied question}"

[ -f "${VIDEO_FILE}" ] && [ -r "${VIDEO_FILE}" ] || exit 2
RC=0
RESULT=$("${VSS[@]}" vlm run \
  --file "${VIDEO_FILE}" \
  --prompt "${USER_QUESTION}" \
  --fps 2 \
  --no-persist) || RC=$?
printf '%s\n' "${RESULT}"
printf 'vss_exit_code=%s\n' "${RC}" >&2
exit "${RC}"
```

`--file` streams the complete video file to vLLM as a base64 video payload.
vLLM performs decoding and samples it at 2 FPS.

For a pre-resolved URL:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)
VIDEO_URL="${VIDEO_URL:?user-supplied video URL}"
USER_QUESTION="${USER_QUESTION:?user-supplied question}"

RC=0
RESULT=$("${VSS[@]}" vlm run \
  --media-url "${VIDEO_URL}" \
  --prompt "${USER_QUESTION}" \
  --fps 2 \
  --no-persist) || RC=$?
printf '%s\n' "${RESULT}"
printf 'vss_exit_code=%s\n' "${RC}" >&2
exit "${RC}"
```

`--media-url` sends the supplied URL unchanged and vLLM fetches the complete
video.

Use `--no-persist` because this skill performs direct visual Q&A and does not
depend on unified memory. Do not run `vss memory get`, `vss memory query`, or
`vss memory introspect`. Do not use VIOS unless another workflow has already
resolved a clip and handed this skill its URL.

## Multiple questions

Run one `vss vlm run` per question, in the user's order. Pass the same complete
video to each call and preserve each question verbatim. Never combine multiple
questions into one prompt.

## Return the result

On exit code 0, return the answer from the command output. On a nonzero exit,
report the exit code and diagnostic and stop for that question. Do not retry,
fall back to another tool, change the prompt, switch videos, inspect frames,
query memory, or call an OpenAI-compatible endpoint with raw HTTP.

## Boundaries

- Archive-wide retrieval belongs to `/vss-search-archive`.
- Long-form summarization belongs to `/vss-summarize-video`.
- Structured reports belong to `/vss-generate-video-report`.
- Stored job or record lookup is outside this skill.
- Sensor registration, discovery, timelines, and clip creation belong to
  `/vss-manage-video-io-storage`.
