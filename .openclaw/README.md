# VSS Claw — OpenClaw Plugin

NVIDIA Video Search & Summarization agent for [OpenClaw](https://github.com/openclaw/openclaw). Provides 7 skills covering the full VSS lifecycle: deployment, sensor management, alerts, video search, video summarization, video analytics, and incident reports.

---

## Prerequisites

The following must be in place before VSS can deploy containers. The agent will check and guide you through each one via the `vss-prerequisites` skill — this is just a quick reference.

| Requirement | Min version | Install guide |
|---|---|---|
| NVIDIA GPU driver | 580+ | [nvidia.com/drivers](https://www.nvidia.com/en-us/drivers/) — reboot after install |
| Docker Engine | 27.2.0 | [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/) |
| Docker Compose | v2.29.0 | Included with Docker Desktop / Engine |
| NVIDIA Container Toolkit | latest | [docs.nvidia.com/datacenter/cloud-native/container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| NGC API key | — | [ngc.nvidia.com](https://ngc.nvidia.com) → Setup → API Keys |

**Post-Docker install:** add your user to the docker group so containers run without sudo:

```bash
sudo usermod -aG docker $USER && newgrp docker
```

Once OpenClaw is running, ask the agent: _"check prerequisites"_ to run a full automated check.

---

## 1. Install OpenClaw

```bash
npm install -g openclaw
```

Verify the install:

```bash
openclaw --version
```

---

## 2. Install the VSS Claw Plugin

**From the cloned VSS repo:**

```bash
openclaw plugins install ./video-search-and-summarization/.openclaw/
```

**From npm (after publishing):**

```bash
openclaw plugins install @nvidia/openclaw-vss
```

On first gateway start after install, the plugin automatically copies workspace templates (`BOOTSTRAP.md`, `IDENTITY.md`, `SOUL.md`, `AGENTS.md`, `TOOLS.md`) to `~/.openclaw/workspace/` and patches the gateway service for Docker group access.

---

## 3. Verify

```bash
openclaw skills list | grep vss
```

Expected output:

```
deploy               Deploy or tear down any VSS profile (base, alerts, lvs, search)
sensor-ops           Manage cameras, RTSP streams, recordings, and snapshots via VIOS
alerts               Manage and monitor VSS alerts, submit for VLM verification
video-search         Search video archives using natural language (Cosmos Embed1)
video-summarization  Summarize long videos, generate shift reports and daily summaries
video-analytics      Query video analytics — incidents, alerts, object counts, metrics
incident-report      Generate and query incident reports from Elasticsearch
```

---

## 4. First Run

Start a new OpenClaw session. The BOOTSTRAP flow runs automatically and the agent will introduce itself and walk through initial VSS configuration.

---

## Skills Reference

| Skill | Trigger phrases |
|---|---|
| `deploy` | "deploy VSS", "start VSS base", "deploy alerts profile", "tear down VSS" |
| `sensor-ops` | "add a camera", "list sensors", "start recording", "get a snapshot" |
| `alerts` | "check alerts", "submit alert for verification", "customize alert prompts" |
| `video-search` | "find forklifts", "search for vehicles between 8am and noon" |
| `video-summarization` | "summarize this video", "generate a shift summary", "daily activity report" |
| `video-analytics` | "show me alerts", "how many PPE violations?", "any incidents today?" |
| `incident-report` | "generate an incident report", "what happened at the loading dock?" |
