---
name: vss-event-verify
description: EXPERIMENTAL (draft, not merge ready). Use this skill when asked an Event Verification yes/no question about a video (the question text under `## Question`, with a `${VIDEO_URL}`), to answer it through the `ev-verify` tool instead of a single `vss vlm run`.
license: Apache-2.0
metadata:
  version: "0.0.0-experimental"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint experimental"
  # The OpenClaw harness image ships and activates skills by this.
  vss-requires: "vlm"
---

# Event Verification checks (`ev-verify`)

> **Experimental.** Draft for a Fleet Event Verification test. Not for merge; removed after the test.

## When to Use

- A yes/no Event Verification question about one video (the text under `## Question`, with a `${VIDEO_URL}`).

Not for open questions, search, or summaries; use `vss-ask-video` for those.

## How it works

The deployment ships a fixed set of simple yes/no checks and per-subset cut-offs for these questions. `ev-verify` asks each check
and the question itself, through `vss vlm run`, averages the probabilities and answers against the subset's cut-off.

## Steps

1. Write the question text exactly as given, unchanged, to `./ev/question.txt` with the write tool. Do not shorten, reword, add or drop anything.
2. Run it once, in one working directory (OpenClaw may start a fresh shell per call):

```bash
mkdir -p ev && cd ev && ev-verify --question-file question.txt --media-url "${VIDEO_URL}"; echo "ev_exit_code=$?"
```

3. Read the JSON it prints. `answer` (yes or no) is the final answer. Report `score`, `cutoff` and the check probabilities with it.

## Exit codes

- **0:** answered. Use `answer`.
- **2:** no checks exist for this question text. Fall back to the normal path: follow `vss-ask-video` for one `vss vlm run --media-url` on the whole clip.
- **3:** a check failed or returned no probability. Report the error text and the exit code. Do not guess, do not re-run, and do not
  answer from your own reading of the video.

## Rules

- **One run per question.** Run `ev-verify` once. Each check, and the question itself, is one `vss vlm run` made by `ev-verify`; do not add a separate `vss vlm run` of your own.
- **Never decode or sample frames.** `ev-verify` never lets you see frames, and you must not look at any either. Every look at the video is a `vss vlm run`.
- **Use the `vss` CLI only.** Do not call a VLM endpoint directly.
