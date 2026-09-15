<h2>NVIDIA AI Blueprint: Video Search and Summarization (VSS)</h2>

**Build GPU-accelerated video AI agents that search, analyze, summarize, and reason over live or recorded video using natural language.**

NVIDIA AI Blueprint for Video Search and Summarization (VSS) combines vision-language models, RAG, and NVIDIA NIM microservices to deliver real-time video analytics, visual Q&A, alert verification, clip retrieval, and long-video summarization.

- Search video streams or archives using natural language queries
- Summarize hours of video
- Ask visual questions and automatically generate reports
- Detect and verify real-time alerts with VLMs

**[🚀 Try the Demo](https://build.nvidia.com/nvidia/video-search-and-summarization)** · **[⚡ Quickstart](#quickstart-guide)** · **[📚 Documentation](https://docs.nvidia.com/vss/latest/index.html)** · **[🏗️ Architecture](#software-components)** · **[📦 Latest Release](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/releases/latest)**

### Table of Contents
- [Overview](#overview)
- [Use Case / Problem Description](#use-case--problem-description)
- [Agent Workflows](#agent-workflows)
- [Software Components](#software-components)
- [Target Audience](#target-audience)
- [Repository Structure Overview](#repository-structure-overview)
- [Documentation](#documentation)
- [Prerequisites](#prerequisites)
- [Hardware Requirements](#hardware-requirements)
- [Quickstart Guide](#quickstart-guide)
- [Contributing](#contributing)
- [License](#license)

## Overview

The [NVIDIA Blueprint for Video Search and Summarization (VSS)](https://docs.nvidia.com/vss/latest/index.html) provides a suite of reference architectures for building vision agents and AI-powered video analytics applications. Those architectures bring together accelerated vision microservices, vision language models (VLMs), and large language models (LLMs) so you can use them in existing applications, as standalone microservices, or as part of a larger vision agent.

VSS is organized into three areas of processing and analysis: **real-time video intelligence** (feature extraction, embeddings, and stream understanding with results published to a message broker), **downstream analytics** (enrichment of metadata into trajectories, incidents, and verified alerts), and **agentic and offline processing** (orchestrated tools for search, Q&A, summarization, and clip retrieval, including via the Model Context Protocol).

This repository implements the blueprint and powers the [NVIDIA build experience](https://build.nvidia.com/nvidia/video-search-and-summarization) for natural-language video agents—search, summarization, visual Q&A, and related workflows—backed by generative AI, VLMs and LLMs, and [NVIDIA NIM](https://build.nvidia.com/) microservices as configured in the stacks below.

## Use Case / Problem Description

The NVIDIA AI Blueprint for Video Search and Summarization addresses the challenge of deploying visual agents capable of interacting with large volumes of video data, both stored and streamed. This can be used to create vision AI agents, that can be applied to a multitude of use cases such as monitoring smart spaces, warehouse automation, and SOP validation. This is important where quick and accurate video analysis can lead to better decision-making and enhanced operational efficiency.

## Agent Workflows
We provide multiple reference [Agent Workflows](https://docs.nvidia.com/vss/latest/agent-workflows.html) which demonstrate how the individual components can be leveraged by an agent:

| Workflow | Description |
|----------|-------------|
| [Q&A and Report Generation (Quickstart)](https://docs.nvidia.com/vss/latest/quickstart.html) | Video retrieval, VLM-based Q&A, and report generation on short video clips |
| [Alert Verification](https://docs.nvidia.com/vss/latest/agent-workflow-alert-verification.html) | Realtime processing of videos using perception (object detection, tracking) and behavior analytics to generate alerts, which are subsequently verified with VLM to reduce false positives |
| [Real-Time Alerts](https://docs.nvidia.com/vss/latest/agent-workflow-rt-alert.html) | Continuous processing of video streams through VLM for anomaly detection |
| [Video Search](https://docs.nvidia.com/vss/latest/agent-workflow-search.html) | Natural language search across video archives using video embeddings (alpha) |
| [Long Video Summarization](https://docs.nvidia.com/vss/latest/agent-workflow-lvs.html) | Analysis and summarization of extended video recordings through chunking and aggregation of dense captions |

## Software Components
<div align="center">
  <img src="https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/raw/main/assets/vss-architecture.png" width="800">
</div>

1. **NIM microservices**: Here are models used in this blueprint:

    - [Cosmos3 Nano Reasoner](https://build.nvidia.com/nvidia/cosmos3-nano-reasoner)
    - [NVIDIA Nemotron 3.5 Lightning 30B A3B](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b)

2. **Real-time video intelligence**: The Real-Time Video Intelligence layer extracts rich visual features, semantic embeddings, and contextual understanding from video data in real-time, publishing results to a message broker for downstream analytics and agentic workflows. It provides three core microservices for processing video streams.

3. **Downstream analytics**: The Downstream Analytics layer processes and enriches the metadata streams generated by real-time video intelligence microservices, transforming raw detections into actionable insights and verified alerts.

4. **Agent and offline processing**: The top-level agent leverages the Model Context Protocol (MCP) to access video analytics data, incident records, and vision processing capabilities through a unified tool interface. It integrates multiple vision-based tools including video understanding with Vision Language Models (VLMs), semantic video search using embeddings, long video summarization for extended footage analysis, and video snapshot/clip retrieval.

## Target Audience
This blueprint is designed for ease of setup with extensive configuration options, requiring technical expertise. It is intended for:

1. **Video Analysts and IT Engineers:** Professionals focused on analyzing video data and ensuring efficient processing and summarization. The blueprint offers 1-click deployment steps, easy-to-manage configurations, and plug-and-play models, making it accessible for early developers.

2. **GenAI Developers / Machine Learning Engineers:** Experts who need to customize the blueprint for specific use cases. This includes modifying the pipelines for unique datasets and fine-tuning LLMs as needed. For advanced users, the blueprint provides detailed configuration options and custom deployment possibilities, enabling extensive customization and optimization.

## Repository Structure Overview

| Directory | Description |
|-----------|-------------|
| `services/agent/` | Video search and summarization agent (Python). Contains `src/agent/` (tools, agents, APIs, embeddings, evaluators, video analytics), `tests/`, `stubs/`, `docker/`, and `3rdparty/`. See [services/agent/README.md](services/agent/README.md). |
| `services/ui/` | Frontend monorepo (Next.js, Turbo): `apps/` (nv-metropolis-bp-vss-ui, vss-chat) and shared `packages/`. See [services/ui/README.md](services/ui/README.md). |
| `services/analytics/` | Downstream analytics services for processing real-time video intelligence metadata. Contains behavior analytics stream processing and REST APIs for querying analytics results. |
| `services/analytics/behavior-analytics/` | Python streaming pipeline for spatial AI analytics, incident detection, Smart City, warehouse, playback, and other behavior analytics applications. Includes app entry points, configs, Docker support, tests, and detailed guides. See [services/analytics/behavior-analytics/README.md](services/analytics/behavior-analytics/README.md). |
| `services/analytics/video-analytics-api/` | Node.js and Express REST API service for VSS Video Analytics data. Exposes metrics, tracker, frames, behavior, clustering, events, sensor, config, alerts, and incidents endpoints backed by Elasticsearch. See [services/analytics/video-analytics-api/README.md](services/analytics/video-analytics-api/README.md). |
| `deploy/` | Deployment configs, Docker Compose, and Helm charts: NIM model configs, developer profiles (dev-profile-base, dev-profile-search, dev-profile-alerts, dev-profile-lvs), foundational services, LVS, RTVI, VLM-as-verifier, VST, and root `compose.yml`. Also contains `deploy/docker/scripts/` — the Brev launchable notebook and dev-profile / patch scripts. |
| `tools/message-broker-consumers/` | Multiprocessing Redis and Kafka consumers that decode VSS protobuf messages from streams/topics and export them as JSON Lines files for inspection, debugging, or offline processing. See [tools/message-broker-consumers/README.md](tools/message-broker-consumers/README.md). |
| `tools/sdg-postprocessing/` | Dataset post-processing utilities for synthetic data generation workflows: semantic labeling helpers, raw data sanity checks, RGB/depth/video conversion, and ground-truth conversion for MTMC-compatible datasets. See [tools/sdg-postprocessing/README.md](tools/sdg-postprocessing/README.md). |
| `tools/rtvi-cv-mv3dt-utils/` | Offline utilities for generating MV3DT RTVI-CV configuration artifacts, including per-camera `camInfo` projection configs and MQTT publish/subscribe topology files for warehouse MV3DT deployments. See [tools/rtvi-cv-mv3dt-utils/README.md](tools/rtvi-cv-mv3dt-utils/README.md). |
| `tools/logstash-plugins/` | Custom Logstash plugins for the ELK ingest pipeline. Includes `input/redis-stream/` (`logstash-input-redis_stream`), a Java input plugin that reads from Redis Streams via consumer groups (`XREADGROUP`) with optional Protocol Buffer decoding. See [tools/logstash-plugins/input/redis-stream/README.md](tools/logstash-plugins/input/redis-stream/README.md). |
| `skills/` | [agentskills.io](https://agentskills.io/specification)-compatible agent skills for VSS: self-contained skill directories with `SKILL.md` frontmatter, grouped by category (`deployment/`, `operations/`, `tools/`, `benchmarking/`) plus the top-level `vss-build-vision-ai`. Covers deploy and usage of search, summarization, alerts, VIOS, RT-VLM, LVS, and other related workflows—see the catalog and install notes in [skills/README.md](skills/README.md). |
| `libs/analytics/spatialai-data-utils/` | Spatial AI Data Utils (SDU): NVSchema / ground-truth / calibration / Sparse4D loaders, camera calibration + grouping (BEV group-origin / per-group fan-out), 3D&#x2194;2D geometry, multi-cam 3D-bbox visualization, detection (mAP) + tracking (HOTA, CLEAR, identity, count) evaluators, NVSchema result converters, and video&#x2194;frame utilities. See [libs/analytics/spatialai-data-utils/README.md](libs/analytics/spatialai-data-utils/README.md). |

## Documentation

For detailed instructions and additional information about this blueprint, please refer to the [official documentation](https://docs.nvidia.com/vss/latest/index.html).

## Prerequisites

### Obtain API Key

- NVIDIA AI Enterprise developer licence required to local host NVIDIA NIM.
- API catalog keys:
   - NVIDIA [API catalog](https://build.nvidia.com/) or [NGC](https://org.ngc.nvidia.com/setup/api-keys) ([steps to generate key](https://docs.nvidia.com/ngc/gpu-cloud/ngc-user-guide/index.html#generating-api-key))

### Container Image Availability

> **Development Container Notice:** Images tagged `develop-*` or `nightly-*` are pre-release artifacts intended for development and testing only. They may change without notice and are provided “AS IS.” Official release images are published through NVIDIA NGC. For production deployments, use only a versioned release image from NGC that has completed NVIDIA’s release review.

## Hardware Requirements

The platform requirement can vary depending on the configuration and deployment topology used for VSS and dependencies like VLM, LLM, etc. For a list of validated GPU topologies and what configuration to use, see the [GPU requirements](https://docs.nvidia.com/vss/latest/prerequisites.html#development-profile-gpu-requirements).

## Quickstart Guide

### Launchable Deployment

**Ideal for:** Quickly getting started with your own videos without worrying about hardware and software requirements.

Follow the steps from the [documentation](https://docs.nvidia.com/vss/latest/cloud-brev.html) and notebook in [deploy/docker/scripts](deploy/docker/scripts/) directory to complete all pre-requisites and deploy the blueprint using Brev Launchable in a 2xRTX PRO 6000 SE AWS instance.
- [deploy/docker/scripts/deploy_vss_launchable.ipynb](deploy/docker/scripts/deploy_vss_launchable.ipynb): This notebook is tailored specifically for the AWS CSP which uses Ephemeral storage.

### Docker Compose Deployment

**Ideal for:** Deploying a VSS agent on your own hardware or bare metal cloud instance.

#### System Requirements

- OS:
    - x86 hosts: Ubuntu 22.04 or Ubuntu 24.04
    - DGX-SPARK: DGX OS 7.4.0
    - IGX-THOR: Jetson Linux BSP (Rel 38.5)
    - AGX-THOR: Jetson Linux BSP (Rel 38.4)
- NVIDIA Driver:
    - 580.105.08 (x86 hosts with Ubuntu 24.04)
    - 580.65.06 (x86 hosts with Ubuntu 22.04)
    - 580.95.05 (DGX-SPARK)
    - 580.00 (IGX-THOR and AGX-THOR)
- NVIDIA Container Toolkit: 1.17.8+
- Docker Engine: 28.3.3 <= Docker Engine < 29.5.0
- Docker Compose: v2.39.1+
- NGC CLI: 4.10.0+

> **Docker upper bound:** Docker Engine 29.5.0+ may fail pulling NGC-hosted images. Use Docker Engine 28.3.3 or another supported version below 29.5.0.

Please refer to [Prerequisites section here for installation details](https://docs.nvidia.com/vss/latest/prerequisites.html).


## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, branch naming convention, and PR guidelines.


## License

The repository license is [LICENSE](LICENSE): Apache-2.0 for all code except
`services/ui/` (MIT) and the file-level exceptions LICENSE lists, with SSPL/AGPL
appendices for the Elasticsearch, Kibana and Redis configuration files.

All license and notice files retained in this repository:

| Path | Covers |
|---|---|
| [LICENSE](LICENSE) | Repository license (Apache-2.0 + MIT appendix + SSPL/AGPL appendices, file-level exceptions) |
| [LICENSE-3rd-party.txt](LICENSE-3rd-party.txt) | Root third-party attributions for the source distribution |
| [LICENSE.DATA](LICENSE.DATA) | NVIDIA Asset License for sample media assets (covered paths listed in LICENSE) |
| [deploy/LICENSE-3rd-party.txt](deploy/LICENSE-3rd-party.txt) | Third-party attributions for deploy tooling |
| [deploy/docker/NOTICE.md](deploy/docker/NOTICE.md) | Notices for the docker deploy profile |
| [deploy/docker/scripts/LICENSE-3rd-party-dev-profile.txt](deploy/docker/scripts/LICENSE-3rd-party-dev-profile.txt) | Third-party attributions for the developer profile scripts |
| [deploy/docker/scripts/nemoclaw/LICENSE-3rd-party.txt](deploy/docker/scripts/nemoclaw/LICENSE-3rd-party.txt) | Third-party attributions for nemoclaw scripts |
| [libs/analytics/spatialai-data-utils/NOTICE](libs/analytics/spatialai-data-utils/NOTICE) | Notices for spatialai-data-utils |
| [libs/analytics/spatialai-data-utils/release/NOTICE](libs/analytics/spatialai-data-utils/release/NOTICE) | Notices bundled with the spatialai-data-utils release artifact |
| [services/agent/LICENSE.md](services/agent/LICENSE.md) | Component-scoped copy of the Apache-2.0 license (source tree only; retained so the license accompanies the directory wherever it is consumed) |
| [services/agent/LICENSE-3rd-party.txt](services/agent/LICENSE-3rd-party.txt) | Third-party attributions for the agent service |
| [services/alert/LICENSE-3rd-party.txt](services/alert/LICENSE-3rd-party.txt) | Third-party attributions for the alert service |
| [services/rtvi/rt-cv/LICENSE.3rdparty](services/rtvi/rt-cv/LICENSE.3rdparty) | Third-party attributions for rt-cv |
| [services/rtvi/rt-cv/NOTICE.txt](services/rtvi/rt-cv/NOTICE.txt) | Notices for rt-cv |
| [services/rtvi/rt-embed/LICENSE-3rd-party.txt](services/rtvi/rt-embed/LICENSE-3rd-party.txt) | Third-party attributions for rt-embed (Python deps) |
| [services/rtvi/rt-embed/LICENSE.3rdparty](services/rtvi/rt-embed/LICENSE.3rdparty) | Third-party attributions for rt-embed (container) |
| [services/rtvi/rt-vlm/LICENSE.3rdparty](services/rtvi/rt-vlm/LICENSE.3rdparty) | Third-party attributions for rt-vlm |
| [services/ui/LICENSE](services/ui/LICENSE) | MIT license for the UI (upstream-derived code) |
| [services/ui/LICENSE-3rd-party.txt](services/ui/LICENSE-3rd-party.txt) | Third-party attributions for the UI |
| [services/video-summarization/LICENSE](services/video-summarization/LICENSE) | Component-scoped copy of the Apache-2.0 license (source tree only; retained so the license accompanies the directory wherever it is consumed) |
| [services/video-summarization/LICENSE.3rdparty](services/video-summarization/LICENSE.3rdparty) | Third-party attributions for video-summarization |
| [services/vios/LICENSE.md](services/vios/LICENSE.md) | Component-scoped copy of the Apache-2.0 license (source tree only; retained so the license accompanies the directory wherever it is consumed) |
| [services/vios/LICENSE.3rdparty](services/vios/LICENSE.3rdparty) | Third-party attributions for vios, part 1 of 2 |
| [services/vios/LICENSE.3rdparty.part2](services/vios/LICENSE.3rdparty.part2) | Third-party attributions for vios, part 2 of 2 |
| [services/vios/LICENSE_libnvjpeg](services/vios/LICENSE_libnvjpeg) | NVIDIA license for the bundled libnvjpeg |
| [deploy/docker/services/infra/3rdParty_Licenses](deploy/docker/services/infra/3rdParty_Licenses) | Third-party attributions for docker infra services |
| [deploy/helm/services/monitoring/3rdParty_Licenses.md](deploy/helm/services/monitoring/3rdParty_Licenses.md) | Third-party attributions for the monitoring helm chart |
| [libs/analytics/spatialai-data-utils/3rdParty_Licenses.md](libs/analytics/spatialai-data-utils/3rdParty_Licenses.md) | Third-party attributions for spatialai-data-utils |
| [libs/analytics/spatialai-data-utils/release/3rdParty_Licenses.md](libs/analytics/spatialai-data-utils/release/3rdParty_Licenses.md) | Third-party attributions for the spatialai-data-utils release artifact |
| [services/analytics/behavior-analytics/3rdParty_Licenses.md](services/analytics/behavior-analytics/3rdParty_Licenses.md) | Third-party attributions for behavior-analytics |
| [services/analytics/video-analytics-api/3rdParty_Licenses.md](services/analytics/video-analytics-api/3rdParty_Licenses.md) | Third-party attributions for video-analytics-api |
| [services/configurators/vss-configurator/3rdParty_Licenses.md](services/configurators/vss-configurator/3rdParty_Licenses.md) | Third-party attributions for vss-configurator |
| [services/configurators/vss-rt-config-adaptor/3rdParty_Licenses.md](services/configurators/vss-rt-config-adaptor/3rdParty_Licenses.md) | Third-party attributions for vss-rt-config-adaptor |
| [services/rtvi/rt-cv/3rdParty_Licenses.md](services/rtvi/rt-cv/3rdParty_Licenses.md) | Third-party attributions for rt-cv (Python deps) |
| [services/rtvi/rt-cv-3d/rt-cv-bev-fusion/3rdParty_Licenses.md](services/rtvi/rt-cv-3d/rt-cv-bev-fusion/3rdParty_Licenses.md) | Third-party attributions for rt-cv-bev-fusion |
| [services/rtvi/rt-cv-3d/rt-cv-config-init/3rdParty_Licenses.md](services/rtvi/rt-cv-3d/rt-cv-config-init/3rdParty_Licenses.md) | Third-party attributions for rt-cv-config-init |
| [services/rtvi/rt-cv-3d/rt-cv-mv3dt/3rdParty_Licenses.md](services/rtvi/rt-cv-3d/rt-cv-mv3dt/3rdParty_Licenses.md) | Third-party attributions for rt-cv-mv3dt |
| [services/sdrc/3rdParty_Licenses.md](services/sdrc/3rdParty_Licenses.md) | Third-party attributions for sdrc |
| [services/vios/ui/vios-ui/oss-licenses.txt](services/vios/ui/vios-ui/oss-licenses.txt) | Third-party attributions for the vios UI |
| [tools/logstash-plugins/input/redis-stream/THIRD_PARTY_LICENSES.md](tools/logstash-plugins/input/redis-stream/THIRD_PARTY_LICENSES.md) | Third-party attributions for the redis-stream logstash plugin |
| [tools/message-broker-consumers/3rdParty_Licenses.md](tools/message-broker-consumers/3rdParty_Licenses.md) | Third-party attributions for message-broker-consumers |
| [tools/sdg-postprocessing/3rdParty_Licenses.md](tools/sdg-postprocessing/3rdParty_Licenses.md) | Third-party attributions for sdg-postprocessing |

The component-scoped Apache-2.0 copies are retained deliberately so each service
directory carries its license wherever the directory is consumed on its own;
removing them is an OSRB call, not a cleanup.
