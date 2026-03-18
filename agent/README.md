<!--
  SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->

# NVIDIA VSS Agent

AI-powered video search, summarization, and incident analysis agent built on
[NVIDIA AIQ Toolkit](https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html).

For deployment instructions (Docker Compose, Helm, cloud), refer to the
[repository root](../README.md) and [`deployments/`](../deployments/).

## Overview

VSS Agent provides composable tools and agents for video understanding:

- **Video Search & Summarization** — natural language search across video streams
- **Incident Analysis** — automated investigation and report generation
- **Video Understanding** — frame-level analysis with Vision Language Models
- **Video Analytics** — metadata, behavior, and event queries

## Project Structure

| Path | Description |
|------|-------------|
| `src/vss_agents/` | Core package: tools, agents, APIs, embeddings, evaluators |
| `tests/unit_test/` | Unit tests (mirrors source tree) |
| `stubs/` | Mypy type stubs for third-party libraries |
| `docker/` | Dockerfile and build scripts |
| `3rdparty/` | Third-party source (FFmpeg, included for LGPL compliance) |

## Prerequisites

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

Install system libraries required for PDF generation:

```bash
sudo apt-get install libcairo2-dev pkg-config python3-dev
```

Install `uv` and create the virtual environment. If Python 3.13 is not present on the system,
`uv` downloads it automatically:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.13
uv sync
source .venv/bin/activate
```

### Docker

```bash
cd .. # Be at repo root level
docker buildx build --platform linux/amd64 -f agent/docker/Dockerfile -t vss-agent:latest --load .
```

## Quick Start

The instructions below use the **dev-profile-base** profile as an example.
The same pattern applies to other profiles (search, alerts, LVS) — substitute the
corresponding `.env` and `config.yml` from
[`deployments/developer-workflow/`](../deployments/developer-workflow/).
See [Configuration](#configuration) for the full list of profiles.

### 1. Set Environment Variables

Create a `.env_file` that points to the profile's `.env` so the agent auto-loads
environment variables on startup (one-time per profile):

```bash
echo "../deployments/developer-workflow/dev-profile-base/.env" > .env_file
```

Then source the same `.env` in your shell and override the placeholders.
`set -a` auto-exports every variable so child processes inherit them.
Because `HOST_IP` and `LLM/VLM_BASE_URL` are set **after** sourcing, every
variable the `.env` derived from them (VST URLs, Phoenix, reports URL, …)
must be re-evaluated — that is what the remaining lines do.

```bash
set -a
source ../deployments/developer-workflow/dev-profile-base/.env

HOST_IP=<YOUR_HOST_IP>                 # placeholder in .env
LLM_BASE_URL=http://${HOST_IP}:${LLM_PORT}   # empty in .env
VLM_BASE_URL=http://${HOST_IP}:${VLM_PORT}   # empty in .env
EXTERNAL_IP=${HOST_IP}                 # not in .env, used by config
INTERNAL_IP=${HOST_IP}                 # not in .env, used by config

# re-evaluate vars that were derived from the placeholder HOST_IP / empty URLs
EXTERNALLY_ACCESSIBLE_IP=${HOST_IP}
VST_INTERNAL_URL=http://${HOST_IP}:${VST_PORT}
VST_EXTERNAL_URL=http://${EXTERNALLY_ACCESSIBLE_IP}:${VST_PORT}
VSS_AGENT_REPORTS_BASE_URL=http://${EXTERNALLY_ACCESSIBLE_IP}:${VSS_AGENT_PORT}/static/
PHOENIX_ENDPOINT=http://${HOST_IP}:6006
EVAL_LLM_JUDGE_BASE_URL=${LLM_BASE_URL}
set +a
```

### 2. Start the Agent

```bash
nat serve \
  --config_file ../deployments/developer-workflow/dev-profile-base/vss-agent/configs/config.yml \
  --host 0.0.0.0 --port 8000
```

On success you will see:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### 3. Verify

```bash
curl http://localhost:8000/health
```

## Usage

Start the agent server:

```bash
nat serve --config_file <config>.yaml --host 0.0.0.0 --port 8000
```

### Configuration

Agent behavior is defined in YAML config files with four top-level sections:

| Section | Purpose |
|---------|---------|
| `general` | Front-end type (FastAPI), CORS, telemetry, object stores |
| `functions` | Tool and sub-agent definitions (video understanding, VST, reports, …) |
| `llms` | LLM / VLM connection profiles (NIM, OpenAI, vLLM, …) |
| `workflow` | Orchestration — which LLM drives the agent, which tools are available, system prompt |

Config values support `${ENV_VAR}` substitution with optional defaults (`${VAR:-default}`).

Ready-to-use configurations are provided under
[`deployments/developer-workflow/`](../deployments/developer-workflow/):

| Profile | Path | Description |
|---------|------|-------------|
| Base | [`dev-profile-base/.../config.yml`](../deployments/developer-workflow/dev-profile-base/vss-agent/configs/config.yml) | Video understanding and report generation |
| Search | [`dev-profile-search/.../config.yml`](../deployments/developer-workflow/dev-profile-search/vss-agent/configs/config.yml) | Search and RAG workflow |
| LVS | [`dev-profile-lvs/.../config.yml`](../deployments/developer-workflow/dev-profile-lvs/vss-agent/configs/config.yml) | LVS video understanding |
| Alerts | [`dev-profile-alerts/.../config.yml`](../deployments/developer-workflow/dev-profile-alerts/vss-agent/configs/config.yml) | Incident analysis and alerting |

Each profile has a companion `.env` file in the same directory with all deployment variables
pre-configured.

### Environment Variables

The table below lists every variable referenced by the agent config files.
Variables marked **required** must be set before `nat serve`; the rest have sensible defaults
or are only needed for specific features.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOST_IP` | yes | — | IP of the host running backing services |
| `EXTERNAL_IP` | yes | — | Externally reachable IP (usually same as `HOST_IP`) |
| `INTERNAL_IP` | yes | — | Internal IP (usually same as `HOST_IP`) |
| `LLM_BASE_URL` | yes | — | LLM endpoint (e.g. `http://HOST:30081`) |
| `VLM_BASE_URL` | yes | — | VLM endpoint (e.g. `http://HOST:30082`) |
| `LLM_NAME` | yes | — | LLM model name (e.g. `nvidia/nvidia-nemotron-nano-9b-v2`) |
| `VLM_NAME` | yes | — | VLM model name (e.g. `nvidia/cosmos-reason2-8b`) |
| `LLM_MODEL_TYPE` | no | `nim` | LLM backend type: `nim`, `openai` |
| `VLM_MODEL_TYPE` | no | `nim` | VLM backend type: `nim`, `openai`, `vllm`, `rtvi` |
| `VLM_MODE` | no | `local_shared` | VLM deployment mode: `local_shared`, `local`, `remote` |
| `VST_INTERNAL_URL` | yes | — | VST internal URL (e.g. `http://HOST:30888`) |
| `VST_EXTERNAL_URL` | yes | — | VST external URL (e.g. `http://HOST:30888`) |
| `VSS_AGENT_PORT` | no | `8000` | Agent HTTP port |
| `VSS_AGENT_OBJECT_STORE_TYPE` | no | `local_object_store` | Object store: `local_object_store` (in-memory) or `s3` |
| `VSS_AGENT_REPORTS_BASE_URL` | no | — | Base URL for generated report assets |
| `VSS_AGENT_VERSION` | no | — | Version tag (used in telemetry project name) |
| `PHOENIX_ENDPOINT` | no | — | Phoenix tracing endpoint (e.g. `http://HOST:6006`) |
| `EVAL_LLM_JUDGE_NAME` | no | same as `LLM_NAME` | Model used for evaluation judge |
| `EVAL_LLM_JUDGE_BASE_URL` | no | same as `LLM_BASE_URL` | Endpoint for evaluation judge |
| `NGC_CLI_API_KEY` | cond. | — | Required when `LLM_MODE` / `VLM_MODE` is `local` or `local_shared` (Docker Compose) |
| `NVIDIA_API_KEY` | cond. | — | Required for build.nvidia.com remote endpoints |

## Testing

```bash
uv run pytest tests/unit_test/ -v
```

With coverage:

```bash
uv run pytest tests/unit_test/ --cov=src/vss_agents --cov-report=term-missing -v
```

## Contributing

1. Fork the repository and create a feature branch.
2. Install dev dependencies: `uv sync --group dev`
3. Install pre-commit hooks: `pre-commit install`
   Hooks include [gitleaks](https://github.com/gitleaks/gitleaks) for secret scanning,
   installed automatically as a Go binary via the pre-commit framework.
4. Run checks:

```bash
uv run pytest tests/unit_test/ -v
uv run ruff check src/
uv run ruff format --check src/
uv run mypy src/vss_agents/
```

5. Submit a pull request.

## License

[Apache-2.0](LICENSE.md). Third-party licenses: [LICENSE-3rd-party.txt](LICENSE-3rd-party.txt).

