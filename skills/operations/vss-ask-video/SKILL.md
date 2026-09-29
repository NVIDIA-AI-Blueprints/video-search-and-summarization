---
name: vss-ask-video
description: Answer one question about one grounded VSS video by making exactly one direct VLM call. Use this skill when a task asks one grounded video question, requires exactly one direct VLM call, or forbids memory, introspection, retries, and follow-up calls.
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

1. Use the sensor supplied by the task, normally `$VSS_SENSOR_ID`.
2. Inspect the complete sensor recording. Do not add start or end bounds unless
   the task explicitly supplies them.
3. Pass the benchmark question to exactly one `vss vlm run` call.
4. Use the first VLM response to select the answer.
5. Write the answer in the format required by the task.
6. Finish immediately.

The call should have this form:

```bash
vss vlm run \
  --sensor "$VSS_SENSOR_ID" \
  --prompt "<exact benchmark question>"
```

Use the exact question from the task as the prompt. Do not expand it into a
checklist, add hints, or include the answer choices unless they are part of the
question supplied to the model.

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

If the single `vss vlm run` call fails:

- Do not retry.
- Do not use another route to inspect the video.
- Do not guess from the question or answer choices.
- Stop without writing `/output/answer.json`.
