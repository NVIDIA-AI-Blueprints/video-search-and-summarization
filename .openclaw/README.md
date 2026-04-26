# VSS Claw — OpenClaw Plugin

NVIDIA Video Search & Summarization agent for [OpenClaw](https://github.com/openclaw/openclaw). Provides 6 skills covering the full VSS lifecycle: NGC setup, prerequisites, base deployment, live video streams, semantic search, and alerts.

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
openclaw skills list | grep -E "ngc|vss"
```

Expected output:

```
ngc               Install, configure, or verify NVIDIA NGC CLI and API key access
vss-prerequisites Check and install VSS system requirements
vss-base          Deploy and manage VSS base profile
vss-lvs           Deploy and manage VSS live video stream profile
vss-search        Run semantic video search queries
vss-alerts        Configure and manage VSS alert rules
```

---

## 4. First Run

Start a new OpenClaw session. The BOOTSTRAP flow runs automatically and the agent will introduce itself and walk through initial VSS configuration.

---

## Skills Reference

| Skill | Trigger phrases |
|---|---|
| `ngc` | "set up NGC", "configure NGC key", "NGC not found" |
| `vss-prerequisites` | "check prerequisites", "install requirements" |
| `vss-base` | "deploy VSS", "start VSS base" |
| `vss-lvs` | "deploy live stream", "start LVS profile" |
| `vss-search` | "search videos", "find footage of …" |
| `vss-alerts` | "create alert", "configure alert rules" |
