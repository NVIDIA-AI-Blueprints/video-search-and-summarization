---
name: vss-deploy-video-embedding
description: >
  Use this skill when deploying, operating, integrating, or customizing the
  VSS RT-Embed Video Embedding microservice. Covers standalone
  Docker Compose deployment, the `/v1` REST API for text/video embeddings
  and live streams, Redis/Kafka/OTel integration, troubleshooting, and
  bring-your-own-model (BYOM) custom embedding backends, with VideoPrism as an example.
  Do not use for RT-CV, RT-VLM, VSS Agent, or general VSS deployment work
  that does not include RT-Embed.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment byom rtvi-embed videoprism"
---

# VSS Video Embedding (RT-Embed)

Use this skill for the RT-Embed video embedding microservice, including the
standard Cosmos-Embed1 deployment path and custom/BYOM embedding model work.

**Trigger phrases:** `vss-deploy-video-embedding`, `RT-Embed`, `rtvi-embed`,
`video embedding service`, `Cosmos-Embed1`, `embed live stream`, `embed video
file`, `generate video embeddings`, `text embedding for video search`,
`RT-Embed BYOM`, `VideoPrism embed`, `custom embed model`,
`MODEL_IMPLEMENTATION_PATH`, `MODEL_REPOSITORY_SCRIPT_PATH`, `bring your own
embedding model`.

**Do not use this skill** for RT-CV, RT-VLM, VSS Agent, or general VSS
deployment work unless the request deploys, operates, integrates, or customizes
RT-Embed.

## Service Snapshot

- **Skill:** `vss-deploy-video-embedding`.
- **Legacy 3.1 name:** RT-Embed.
- **Compose service:** `rtvi-embed`.
- **Container name:** `vss-rtvi-embed`.
- **Image:** `ghcr.io/nvidia-ai-blueprints/vss/vss-rt-embed` (override with `VSS_RT_EMBED_IMAGE`).
- **Default tag:** `develop-latest` (override with `VSS_RT_EMBED_TAG`; use `develop-latest-sbsa` for an SBSA/DGX Spark host).
- **Profile:** `rtvi-embed`.
- **Container port:** `8000` (host-side `${RTVI_EMBED_PORT}`).
- **Default model:** `cosmos-embed1-448p` from `nvidia/Cosmos-Embed1-448p`.
- **BYOM loader variables:** `MODEL_PATH`, `MODEL_IMPLEMENTATION_PATH`, `MODEL_REPOSITORY_SCRIPT_PATH`.
- **Health endpoint:** `GET /v1/ready`.
- **Healthcheck startup grace:** `1200s` (20 minutes) on first boot.

## Route First

Choose one primary path before acting. Load the linked reference and follow it;
do not duplicate full workflows from this top-level file.

| User intent | Use this path |
|---|---|
| Deploy, size, upgrade, roll back, or tear down standalone RT-Embed with the default Cosmos-Embed1 model | [`references/deploy-vss-deploy-video-embedding.md`](references/deploy-vss-deploy-video-embedding.md) |
| Call RT-Embed APIs for files, text/video embeddings, live streams, model listing, health, metrics, metadata, or manifests | [`references/rest-api.md`](references/rest-api.md) |
| Wire RT-Embed into another service or deployment with Redis, Kafka, OpenTelemetry, auth, storage, or env var mapping | [`references/integrate-vss-deploy-video-embedding.md`](references/integrate-vss-deploy-video-embedding.md) and [`references/environment.md`](references/environment.md) |
| Add, wire, or validate a custom/BYOM embedding backend, with VideoPrism as an example | [`references/byom-custom-model.md`](references/byom-custom-model.md) |
| Debug readiness, model/cache startup, permissions, Redis/Kafka reachability, API failures, or observability | [`references/troubleshooting.md`](references/troubleshooting.md) |

Selection rules:

- For normal RT-Embed or Cosmos-Embed1 deployment, use the deployment reference.
  In the answer, explicitly say that this is the default RT-Embed deployment
  path. Also explicitly distinguish it from BYOM/custom model integration:
  BYOM is only for adding or validating non-default custom embedding backends
  such as VideoPrism, and is not needed for the default Cosmos-Embed1 model.
- For BYOM, custom embedding models, VideoPrism examples, or model implementation path questions,
  use the BYOM reference first, then deployment/API references only as needed.
- For direct endpoint calls, use the API reference and reuse deployment context
  only when the service is not already running.
- If the request mixes deployment and BYOM, load BYOM first to establish model
  path requirements, then use the deployment reference to run the service.

## Operating Rules

- Do not deploy a full VSS profile for standalone RT-Embed. Work from
  `deploy/docker/services/rtvi/rtvi-embed` unless the user explicitly asks for a
  profile deployment.
- Never let `sudo` prompt interactively. Prefer plain `docker`; otherwise use
  `sudo -n docker` and stop with the exact manual command if passwordless sudo is
  unavailable.
- Do not expose full values of `NGC_API_KEY`, `HF_TOKEN`, bearer tokens, or model
  repository credentials in prompts, logs, or final answers.
- Do not shorten the `start_period: 1200s` healthcheck during first boot. Cosmos
  model download and Triton model repository generation can take up to 20 minutes.
- In standalone mode, disable missing peers with `MESSAGE_BUS=`, `ERROR_BUS=`,
  and `ENABLE_REDIS_ERROR_MESSAGES=false` unless the corresponding Kafka or Redis
  service is started and reachable.
- For BYOM models that are video-only, require an explicit text endpoint decision:
  either a compatible text encoder in the same embedding space or a clear 4xx
  response for `/v1/generate_text_embeddings`.

## Quick Reference

- **Deployment details:** image, GPU, storage, startup, readiness, upgrade, and
  teardown live in [`references/deploy-vss-deploy-video-embedding.md`](references/deploy-vss-deploy-video-embedding.md).
- **API details:** file upload, text/video embeddings, live-stream control,
  models, health, metadata, and metrics live in [`references/rest-api.md`](references/rest-api.md).
- **Integration details:** inputs/outputs, Redis/Kafka/OTel, auth, networking,
  and Compose snippets live in [`references/integrate-vss-deploy-video-embedding.md`](references/integrate-vss-deploy-video-embedding.md).
- **Environment matrix:** host-to-container renames, optional volumes, and
  secret-sensitive variables live in [`references/environment.md`](references/environment.md).
- **BYOM details:** custom model contract, Docker/Helm overrides, model path
  variables, and VideoPrism example validation live in [`references/byom-custom-model.md`](references/byom-custom-model.md).
- **Troubleshooting details:** common startup, cache, permission, bus, and API
  failures live in [`references/troubleshooting.md`](references/troubleshooting.md).

## References

| File | When to read |
|---|---|
| [references/README.md](references/README.md) | Table of contents for all reference files. |
| [references/deploy-vss-deploy-video-embedding.md](references/deploy-vss-deploy-video-embedding.md) | Deployment reference: image, GPU, storage, startup, prerequisites, known issues. |
| [references/rest-api.md](references/rest-api.md) | Full REST endpoint catalog with worked `curl` examples for file uploads, video/text embeddings, live streams, and health/metrics. |
| [references/integrate-vss-deploy-video-embedding.md](references/integrate-vss-deploy-video-embedding.md) | Integration reference: peers, inputs/outputs, env vars, network, example Compose snippet. |
| [references/environment.md](references/environment.md) | Complete environment-variable matrix, including host-to-container renames and secret-sensitive variables. |
| [references/byom-custom-model.md](references/byom-custom-model.md) | BYOM reference: custom model contract, path overrides, Docker/Helm wiring, and VideoPrism example validation checklist. |
| [references/troubleshooting.md](references/troubleshooting.md) | Operational diagnostics for startup, model/cache, runtime, and observability issues. |
