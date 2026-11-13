---
name: vss-ask-video
description: Answer one question about a video at a task-supplied URL by making exactly one direct VLM call. Use this skill for URL-based video questions requiring a single call without memory, introspection, retries, or follow-up calls.
license: Apache-2.0
metadata:
  version: "3.3.0-single-call"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational evaluation"
  vss-requires: "vlm"
---

# Ask one question about a video

Use the configured `vss` CLI to answer the supplied question from the specified
video.

## Required behavior

1. Use the exact video URL supplied by the task with `--media-url`. If no URL is
   supplied, stop without calling the VLM or writing `/output/answer.json`.
   Do not resolve a sensor or derive a URL from a video ID.
2. Inspect the complete supplied video. Do not add start or
   end bounds unless the task explicitly supplies them.
3. Pass the original question AND every supplied labeled answer choice,
   unchanged and in their original order, to exactly one `vss vlm run` call.
   Include choices even when the task lists them separately from the question.
   For a multiple-choice task, also tell the VLM to return only the selected
   option letter, without an explanation.
4. Use the first VLM response to select the answer.
5. Write the answer in the format required by the task.
6. Finish immediately.

## Inference prompt

For a multiple-choice task, assemble the prompt before making the single call:

```bash
VLM_PROMPT=$(cat <<'PROMPT'
<exact benchmark question>

<every original labeled answer choice, in its original order>

Answer with only the selected option letter. Do not include an explanation.
PROMPT
)
```

Replace the placeholders with the task's actual question and all of its labeled
choices. Preserve the labels and wording; do not add hints, a proposed answer,
or a reasoning checklist. For a task without choices, use its exact question
and requested response format instead; do not invent options or require a letter.

## Video source

When the task provides a video URL, set `VIDEO_URL` to that exact URL.
This includes a supplied HTTP(S) RustFS/S3 object URL; do not reconstruct it
from a video ID, bucket name, or endpoint, and do not substitute an `s3://` path.

```bash
vss vlm run \
  --media-url "${VIDEO_URL:?The task must supply a video URL}" \
  --prompt "$VLM_PROMPT"
```

The media URL identifies the video, not the VSS deployment. Keep using the
configured backend at `$VSS_GATEWAY_ORIGIN`; do not reconfigure VSS to the
object-store URL. The VLM backend fetches the supplied URL, so it must be
reachable from that backend. An internal hostname such as
`rustfs.media.svc.cluster.local` requires backend access to Kubernetes DNS and
networking; a supplied URL alone does not establish that access. Do not download,
upload, ingest, or convert the video yourself, or switch sources after a failure.

## Hard constraints

- Make exactly one `vss vlm run` call.
- Do not make a second VLM call for verification.
- Do not retry a failed, vague, incomplete, or uncertain response.
- Do not inspect smaller time windows or crop the video.
- Do not search memory or Markdown notes.
- Do not call `vss memory query`, `vss memory get`, or
  `vss memory introspect`.
- Do not summarize the video before answering.
- Do not use search, retrieval, frame extraction, ffmpeg, OpenCV, or another
  vision model.
- Do not call the VLM through raw HTTP or an OpenAI-compatible endpoint.
- Do not run readiness checks or reconfigure the deployment.
- Do not repair, restart, or modify backend services.

## Success

When `vss vlm run` succeeds:

1. Read its first response.
2. Select the answer requested by the task.
3. Write the required output file.
4. Stop immediately.

For the VideoMME evaluation, write:

```json
{"answer": "<selected option letter>"}
```

to:

```text
/output/answer.json
```

## Failure

If the task supplies no video URL, or the single `vss vlm run` call fails:

- Do not retry.
- Do not use another route to inspect the video.
- Do not guess from the question or answer choices.
- Stop without writing `/output/answer.json`.
