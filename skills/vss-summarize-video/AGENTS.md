# AGENTS.md

## Scope

Applies to `vss-summarize-video`, the Long Video Summarization skill.

## Rules

- Use this skill for recorded long-video summaries. Use `vss-ask-video` for a
  one-off VLM question and `vss-search-archive` for retrieval.
- Verify the LVS profile or LVS API is reachable before submitting work.
- Keep user-provided video source, time range, and summarization request intact.
- Submit one summarize request per user request unless the spec explicitly
  authorizes retries.
- Poll only according to the documented API cadence and stop on terminal
  status.
- Report the final summary plus job/status evidence.

## Eval Behavior

- In CI, deploy the LVS prerequisite only when the trial prompt says setup is
  pre-authorized.
- Do not hide deploy or polling failures; report the exact failing endpoint or
  job status.
