# AGENTS.md

## Scope

Applies to `vss-ask-video`, the skill for one-off visual questions about a
specific clip or recorded video.

## Rules

- Use this skill for ad-hoc VLM understanding of one clip, not for archive
  search, long-video summarization, incident reports, or alert management.
- Resolve whether the input is a VIOS clip URL, local file, or provided remote
  video URL before calling a VLM path.
- If VSS base is needed and absent, deploy only when the user or eval prompt
  pre-authorizes deployment.
- Remote VLM endpoints generally cannot fetch private localhost URLs; rewrite
  or choose a reachable path before calling them.
- Return the direct answer to the visual question and include the evidence path
  used, without fabricating observations.

## Eval Behavior

- Follow the spec's single request exactly. Do not turn Q&A into a report or
  summary workflow.
