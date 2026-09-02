---
name: vss-deploy-profile
description: Use only as a deprecated compatibility redirect when a legacy request explicitly invokes vss-deploy-profile or asks for the old VSS profile deployment skill. Redirect base, search, lvs, and alerts developer-profile requests to vss-build-vision-ai; block warehouse and edge until replacement coverage is complete.
license: Apache-2.0
metadata:
  version: "3.2.1"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint deployment deprecated redirect"
---

# VSS Deploy Profile (Deprecated Redirect)

## Status

`vss-deploy-profile` is superseded by [`vss-build-vision-ai`](../vss-build-vision-ai/SKILL.md) for current VSS developer-profile deployments.

Do not start new `base`, `search`, `lvs`, or `alerts` deployments from this skill. Redirect those requests to `vss-build-vision-ai`.

## When To Use

Use this skill only when the user or an older eval explicitly names
`vss-deploy-profile`, `/vss-deploy-profile`, or the deprecated VSS deploy-profile
skill. Its only job is to redirect supported profile requests and report the
temporary blocker for unsupported legacy profile coverage.

## Do Not Use This Skill For

- New VSS profile deployments. Use `vss-build-vision-ai` for `base`, `search`,
  `lvs`, and `alerts`.
- Custom profile composition, delta overlays, generated `_builds/` artifacts, or
  resolved-Compose deployment lifecycle. Use `vss-build-vision-ai`.
- Warehouse or edge profile deployment. Stop with the blocker message below
  until replacement coverage lands.
- Standalone microservice deployment or operation of a running stack. Use the
  matching service or operations skill.

## Redirect Map

| Legacy profile request | Redirect target |
|---|---|
| `base` / quickstart | `vss-build-vision-ai` stock **Base** workflow |
| `search` / video search | `vss-build-vision-ai` stock **Search** workflow |
| `lvs` / video summarization | `vss-build-vision-ai` stock **Video Summarization** workflow |
| `alerts -m verification` | `vss-build-vision-ai` stock **Alerts** workflow, verification mode |
| `alerts -m real-time` | `vss-build-vision-ai` stock **Alerts** workflow, real-time mode |

If the request includes existing generated artifacts, env overrides, deployment names, teardown requirements, endpoint discovery, or readiness checks, carry that context into the `vss-build-vision-ai` handoff.

## Warehouse And Edge

Warehouse and edge profile requests are not redirected yet. `vss-build-vision-ai` currently covers developer examples only, so full removal is blocked until it covers the warehouse and edge profiles this skill previously owned.

For warehouse or edge requests, stop and tell the user:

> `vss-deploy-profile` is deprecated, but `vss-build-vision-ai` does not yet cover warehouse or edge profiles. This request is blocked until that coverage lands; use the final-removal child task to complete the migration.

## Standalone Services

Do not use this skill for standalone microservice deployments. Use the matching service skill instead:

| Request | Skill |
|---|---|
| Dense captioning / RT-VLM only | [`vss-deploy-dense-captioning`](../vss-deploy-dense-captioning/SKILL.md) |
| Video embeddings only | [`vss-deploy-video-embedding`](../vss-deploy-video-embedding/SKILL.md) |
| Detection and tracking 2D only | [`vss-deploy-detection-tracking-2d`](../vss-deploy-detection-tracking-2d/SKILL.md) |
| Detection and tracking 3D / MV3DT only | [`vss-deploy-detection-tracking-3d`](../vss-deploy-detection-tracking-3d/SKILL.md) |
| VIOS only | [`vss-manage-video-io-storage`](../vss-manage-video-io-storage/SKILL.md) |
| Behavior analytics only | [`vss-setup-behavior-analytics`](../vss-setup-behavior-analytics/SKILL.md) |
| Video analytics API only | [`vss-setup-video-analytics-api`](../vss-setup-video-analytics-api/SKILL.md) |
