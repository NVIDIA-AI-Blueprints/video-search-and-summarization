---
name: vss-ask-video
description: Answer a visual question when the task supplies an exact local video file or video URL. Send the complete supplied video and the question directly to the configured vLLM backend with `vss vlm run`. Use for benchmark and multiple-choice video questions, including prompts that also provide a time reference.
license: Apache-2.0
metadata:
  version: "3.4.0"
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
choices. For a four-choice multiple-choice question, append this exact response
instruction after the question and choices: `Answer with only the option letter:
A, B, C, or D. Do not explain your answer and do not repeat the question or
choices.` A time reference is metadata: do not append it to the question and
do not use it to clip or seek the video.

Do not inspect, download, decode, transcode, segment, or extract frames from the
video. Do not call a generic image/video tool, use `ffmpeg`, issue an exploratory
VLM prompt, search the web, or answer from context. The supplied complete video
and the supplied question are the only inputs to inference.

## Requirements

The OpenClaw harness must prepare the direct-vLLM CLI configuration from
`VSS_GATEWAY_ORIGIN` and `VSS_VLM_BACKEND=vllm` before the agent starts. Do not
spend an agent tool call checking the environment or configuration file. Make
the one inference call; if it reports no configured deployment, report the
bootstrap failure and stop. Do not run `vss configure` against a bare vLLM
origin, discover another endpoint, or deploy or modify a backend.

When the OpenClaw harness exposes `vss_cli`, use it for the request. Pass the
arguments after `vss` as its `args` array. Do not use a shell command when
`vss_cli` is available. In a source checkout, use the project-local invocation
defined in [AGENTS.md](../../../AGENTS.md).

## Run the request

Use only the exact video path or URL supplied by the task. Do not search for a
replacement video. Resolve a relative file path against the current working
directory and require a readable regular file.

The examples below are for four-choice questions. For other question types,
pass the question verbatim without the option-letter instruction.

For an OpenClaw URL request, make this single inference call:

```json
{
  "args": [
    "vlm", "run",
    "--media-url", "<supplied-video-url>",
    "--prompt", "<verbatim-question-and-choices>\n\nAnswer with only the option letter: A, B, C, or D. Do not explain your answer and do not repeat the question or choices.",
    "--server-video-sampling",
    "--no-persist"
  ]
}
```

Do not add `--fps`, `--num-frames`, `--max-tokens`, a time range, or any other
inference override. The deployed vLLM policy controls video decoding, sampling,
processor size, generation, and thinking. `--server-video-sampling` tells the CLI
to leave those video controls out of the request.

For a local file:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)
VIDEO_FILE="${VIDEO_FILE:?user-supplied video file}"
USER_QUESTION="${USER_QUESTION:?user-supplied question}"
MODEL_PROMPT="${USER_QUESTION}"$'\n\n'"Answer with only the option letter: A, B, C, or D. Do not explain your answer and do not repeat the question or choices."

[ -f "${VIDEO_FILE}" ] && [ -r "${VIDEO_FILE}" ] || exit 2
RC=0
RESULT=$("${VSS[@]}" vlm run \
  --file "${VIDEO_FILE}" \
  --prompt "${MODEL_PROMPT}" \
  --server-video-sampling \
  --no-persist) || RC=$?
printf '%s\n' "${RESULT}"
printf 'vss_exit_code=%s\n' "${RC}" >&2
exit "${RC}"
```

`--file` streams the complete video file to vLLM as a base64 video payload.
The deployed vLLM video policy decides how to decode and sample it.

For a pre-resolved URL:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)
VIDEO_URL="${VIDEO_URL:?user-supplied video URL}"
USER_QUESTION="${USER_QUESTION:?user-supplied question}"
MODEL_PROMPT="${USER_QUESTION}"$'\n\n'"Answer with only the option letter: A, B, C, or D. Do not explain your answer and do not repeat the question or choices."

RC=0
RESULT=$("${VSS[@]}" vlm run \
  --media-url "${VIDEO_URL}" \
  --prompt "${MODEL_PROMPT}" \
  --server-video-sampling \
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
video to each call and preserve each question verbatim before the response
instruction. Never combine multiple questions into one prompt.

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
