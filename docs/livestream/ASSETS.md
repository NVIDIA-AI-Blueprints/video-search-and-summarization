# External assets

The repository contains source, calibration and recipes, not the media or model weights. Obtain authorized copies from the demo asset owner. No public download for the custom liquid model or these exact demo videos is established.

Set paths using the [QA deployment environment example](../../extensions/filling-analysis/deployment/qa/env.example), then run its asset checker for the original factory video and model pair. Also verify Sammy v3 against the table below before registering its replay. Keep media and checkpoint directories outside Git; do not commit registry credentials, provider tokens, private configuration, or generated deployment state.

## Required inputs

| Asset | Purpose | SHA-256 |
| --- | --- | --- |
| Original factory video, supplied as `orangejuiceneww.mp4` (also stored as `orangejuice.mp4`) | Archive Search and separate realtime VLM Alerts replay | `21739924f607755906107affa28842df85a4c468e1a8a3a9f28afcb83f346d61` |
| `orange_juice_5min_v3.mp4` | Sammy live Filling replay | `061005c045b18b0f555cc1ad1ac7a037d80b3cf4c0fa0fb3b5ca3357bcba5818` |
| `rf-detr-seg-xxlarge.pt` | RF-DETR bottle segmentation | `f6e06ac5ebafd3f9f6f01c55a7f53b8785a1697037063a6adeb737c4c6ce4838` |
| `liquid-rfdetr-seg-nano-weak-v1.pth` | Custom RF-DETR liquid segmentation | `85af581d8a0e5fc619052a893de805338124523bf65c5620912b8daac6c5e135` |

The original factory video is 80,807,146 bytes and about 107.6 seconds. Sammy v3 is 84,027,277 bytes, H.264, 1280×720, 24 fps, 300 seconds and 7,200 frames. The custom liquid checkpoint is 133,292,127 bytes. Check hashes rather than trusting filenames.

The demonstrated NvStreamer conversion of Sammy v3 removed B-frames: 82,257,111 bytes, SHA-256 `365d93c2241313d160a3ab447be61443cc3be98005ccaf5ff68523de92596519`, with the same duration, frame rate and frame count. This is a documented derivative, not the original source. Preserve the original. A fresh converter may produce different bytes; inspect media identity and calibration compatibility rather than assuming a matching name is sufficient.

## Register each input for its own purpose

1. Upload the original factory recording through Video Management, retain/protect it, and wait for ingest/indexing. Verify that the recording appears in inventory and Search can retrieve it. Merely copying its bytes onto the host does not register or index it.
2. Create a separate RTSP replay of that factory video for realtime VLM Alerts. Confirm advancing frames before enabling its rule.
3. Create a separate RTSP replay of Sammy v3 for Filling. Use the sanitized v3 calibration template in the QA recipe, replacing its source identity and stream URL placeholders with the newly registered source and reachable replay URL. No live stream auto-starts. Do not reuse another deployment's UUIDs or session state.

Search uses archive ingestion; realtime Alerts and Filling consume arriving frames. They do not substitute for one another.

## Calibration and model provenance

The model pair performs bottle and liquid segmentation. Runtime loads the supplied checkpoints locally; it does not train or download replacement models. See the [bottle](../../extensions/filling-analysis/segmentation/bottle/README.md) and [liquid](../../extensions/filling-analysis/segmentation/liquid/README.md) provenance notes. The liquid model uses source-specific weak supervision; access to its checkpoint does not establish a redistribution license for its training data.

For the current Sammy v3 demo, the profile is `sammy-v3-single-station`, reference visible height is 0.71648 and tolerance is 0.05 (five percentage points of detected bottle height). Its separate exterior-color calibration uses a fixed camera region and a persistence gate; it is not an RF-DETR spill classifier. The QA recipe supplies the reviewed v3 exterior regions and remaining per-camera settings without the original deployment identities. This is provenance for the supplied footage, not a fresh-host validation result. Calibration for other footage must not silently authorize v3 or a new camera.

A different video, camera position, crop, resolution, or checkpoint requires an explicit calibration/provenance review. Do not transfer the Sammy single-station calibration to the factory recording's multiple camera angles.
