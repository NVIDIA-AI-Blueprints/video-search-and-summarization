---
name: vss-ask-video
description: Ask one visual question about a user-supplied local video file or pre-resolved video URL by sending the video and question to the configured vLLM backend through `vss vlm run`. Use when the exact video and question are already available. Not for archive search, stored-memory lookup, sensor discovery, summarization, or reports.
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

Send them to the configured vLLM backend with one `vss vlm run`, then return
the model's answer. Preserve the question verbatim. Do not add instructions,
turn it into a checklist, combine it with another question, or answer from
conversation context instead of inspecting the supplied video.

## Requirements

This skill requires a configured VSS CLI whose VLM policy uses the `vllm`
backend. Configuration is an operator task performed before the question:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)

"${VSS[@]}" configure vlm
```

The displayed policy must report `"backend": "vllm"`. If it does not, report
the configuration mismatch. Do not silently change the deployment or VLM
policy while answering a video question.

When the OpenClaw harness exposes the `vss_cli` tool, use it instead of a shell
command. Pass the arguments after `vss` as its `args` array. In a source
checkout, use the project-local invocation shown above. Bootstrap and exit-code
rules are defined in [AGENTS.md](../../../AGENTS.md).

## Run the request

Use only the video path or URL supplied by the user or by an explicit bounded
handoff. Do not search for a replacement video. Resolve a relative file path
against the current working directory and require a readable regular file.

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
vLLM performs decoding and samples it at 2 FPS. Do not decode the video or
extract frames in the agent.

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

`--media-url` sends the URL as the video input and vLLM fetches it. The URL
must already identify the exact video or bounded clip to inspect.

Use `--no-persist` because this skill performs direct visual Q&A and does not
depend on unified memory. Do not run `vss memory get`, `vss memory query`, or
`vss memory introspect`. Do not use VIOS unless another workflow has already
resolved a clip and handed this skill its URL.

## Multiple questions

Run one `vss vlm run` per question, in the user's order. Pass the same video to
each call and preserve each question verbatim. Never combine multiple questions
into one prompt.

## Return the result

On exit code 0, return the answer from the command output. On a nonzero exit,
report the exit code and diagnostic and stop for that question. Do not retry,
switch videos, inspect frames yourself, query memory, or call an
OpenAI-compatible endpoint with raw HTTP.

## Boundaries

- Archive-wide retrieval belongs to `/vss-search-archive`.
- Long-form summarization belongs to `/vss-summarize-video`.
- Structured reports belong to `/vss-generate-video-report`.
- Stored job or record lookup is outside this skill.
- Sensor registration, discovery, timelines, and clip creation belong to
  `/vss-manage-video-io-storage`.
