# AGENTS.md

## Scope

Applies to `vss-manage-video-io-storage`, the skill for VIOS, VST, NvStreamer,
sensors, streams, uploads, clips, snapshots, timelines, and recordings.

## First Reads

- Read `SKILL.md`.
- Use `references/api-reference.md` for VIOS REST operations.
- Use the NvStreamer reference only when the task explicitly asks for
  NvStreamer, RTSP feed generation, or stream URL management.

## Rules

- For plain MP4/file upload requests, use the direct VIOS storage API. Do not
  substitute NvStreamer unless the user asked for a live RTSP feed.
- Do not use this skill for VLM Q&A, search, summaries, narrative reports, or
  analytics incidents. Route to the adjacent VSS skill instead.
- Resolve public Docker or Kubernetes endpoints before issuing curl commands.
- Prefer direct API probes over UI navigation.
- Preserve sensor ids, filenames, timestamps, and returned URLs exactly when
  reporting results.

## Eval Behavior

- In profile-less specs, stand VIOS up via the skill's standalone runbook when
  the API probe fails and the prompt pre-authorizes setup.
- Chain multi-step API tasks using previous step outputs rather than inventing
  ids.
