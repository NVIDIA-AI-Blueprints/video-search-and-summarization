# Livestream demo engineering QA

This branch contains the VSS source plus the Search, upload-retention, UI, and Filling extension changes used in the September 28–29, 2026 demo. It is an engineering QA handoff for a new checkout and a separately provisioned deployment. The media, model weights, credentials, original deployment state, and browser evidence are not bundled.

The prepared branch is `feat/livestream-filling-analysis`, based on `nightly-20260922` (`1f1a0b904df824fbcdcd81287547881de47d4c31`). Obtain that QA branch from the maintainer or apply its patch to this base before following these instructions. This handoff does not assume the branch has been published or that a fork exists.

Start with [assets](ASSETS.md), then the [build/deployment recipe](../../extensions/filling-analysis/deployment/qa/README.md), and run the [test plan](TEST-PLAN.md). [Prompt flow](PROMPTS.md) keeps the two demo stages distinct. [TEST-RESULTS.md](TEST-RESULTS.md) records checks actually run against this QA checkout.

## Deployment prerequisites

- Use the repository's [prerequisites and deployment guidance](../../README.md) and [VSS build workflow](../../skills/vss-build-vision-ai/SKILL.md). Obtain the required registry/model access and credentials through your own approved channels.
- The demonstrated host was x86_64 with four H100 GPUs; all 22 observed images were amd64. GPU assignments, memory requirements, and model endpoints must be chosen for the new host.
- DGX Spark ARM64 and a single GPU are **not validated by this handoff**. Image support/builds, model fit, GPU mapping, and end-to-end performance need separate porting and QA.
- Provision a native NemoClaw/OpenClaw harness and its model endpoint using the repository workflow. Configure the checkout CLI for this deployment. Do not copy another machine's private resolved Compose, harness configuration, tokens, or installed runtime.
- Supply the two videos and exact RF-DETR checkpoints described in [ASSETS.md](ASSETS.md). The custom liquid checkpoint requires a manual authorized handoff; a public download is not established. Use the sanitized Sammy v3 calibration template supplied by the QA recipe, replacing source identity and stream URL placeholders with values from your own deployment.

The portable extension recipe documents the build inputs and environment settings. Read its asset checks before building. Start with Search and realtime VLM Alerts; enable the supplied Filling extension only for stage 2. Enabling this extension does not generate a new model or new application.

## Stages and expected behavior

| Stage | Input | Behavior to verify |
| --- | --- | --- |
| Search | Uploaded, indexed original factory recording | Plain chat prompt runs a current archive Search job and returns playable timestamped cards. Manual **+ Chat** selection is optional. |
| Realtime Alerts | RTSP replay of the original factory footage | A native realtime VLM rule detects visible escaping liquid while frames arrive. Replay restart and decoder freshness require explicit QA. |
| Filling extension | Separate live RTSP replay of Sammy v3 | RF-DETR bottle/liquid masks drive visible-height measurements; a separate calibrated exterior-color signal supports overflow evidence. |

A file present on disk is not an uploaded, retained, indexed archive. Register/upload it through Video Management and wait for ingest before Search. An RTSP registration is a separate source and does not establish an archive is searchable.

## What is in the source

| Area | Entry point |
| --- | --- |
| Fresh Search intent, current tool receipts, and explicit failure on tool-less completion | [archiveSearch.ts](../../services/ui/apps/nv-metropolis-bp-vss-ui/utils/server/agentAdapter/archiveSearch.ts), [openClaw.ts](../../services/ui/apps/nv-metropolis-bp-vss-ui/utils/server/agentAdapter/connectors/openClaw.ts) |
| Search card parsing and source-filter reconciliation | [artifacts.ts](../../services/ui/apps/nv-metropolis-bp-vss-ui/utils/server/agentAdapter/artifacts.ts), [searchFilters.ts](../../services/ui/packages/nv-metropolis-bp-vss-ui/search/lib-src/utils/searchFilters.ts), [SearchComponent.tsx](../../services/ui/packages/nv-metropolis-bp-vss-ui/search/lib-src/SearchComponent.tsx) |
| Upload retention protection | [uploadProtection.ts](../../services/ui/packages/common/lib-src/utils/uploadProtection.ts), [chunkedUpload.ts](../../services/ui/packages/common/lib-src/utils/chunkedUpload.ts) |
| Removed-source tombstones and archive discovery | [VIOS client](../../libs/vss/core/src/vss_core/vios/client.py) |
| Search/critic/model-response handling | [Search CLI](../../libs/vss/cli/src/vss_cli/search/group.py), [critic](../../libs/vss/core/src/vss_core/critic/critic.py), [VLM client](../../libs/vss/core/src/vss_core/vlm/openai.py) |
| Supplied Filling API, live worker, calibration and tests | [extension](../../extensions/filling-analysis/README.md), [streaming worker](../../extensions/filling-analysis/streaming/README.md), [QA deployment](../../extensions/filling-analysis/deployment/qa/README.md) |

## Established observations and remaining limits

On September 29 the deployed Search v3 UI handled “Show me liquid leaking from bottles” and “Find liquid leaking from bottles” as distinct current Search jobs in the same conversation. Both showed a confirmed 65–70-second original-video clip first, with rejected alternatives; the first clip played and sought successfully. These focused checks do not establish broad model accuracy or consistent latency. Earlier checks showed the critic rejecting a real spill, so result labels still need visual review.

The persisted RTSP sidebar selection previously hid returned archive cards; the source-filter repair was checked against that starting state. The per-turn Search repair prevents old conversation prose from being presented as a completed fresh Search without a current successful tool receipt.

A realtime Alerts replay stopped working after NvStreamer end-of-stream left its decoder stale. Manual repair restored it on September 29. **Automatic recovery is not implemented**; track this as an open defect, not a passing replay-recovery test.

Filling uses camera-specific calibration and synthetic looping footage. It measures visible liquid height, not volume. Repeated loop cycles are not independent accuracy trials; decoded or inference frames may be dropped, and verified event playback depends on recording/timeline alignment. A full fresh-host rehearsal and Spark validation remain to be performed.
