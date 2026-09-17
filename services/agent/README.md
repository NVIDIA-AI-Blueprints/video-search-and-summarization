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
[repository root](../../README.md) and [`deploy/docker/`](../../deploy/docker/).

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
| `3rdparty/` | Third-party source retained in the repository; not copied into the container image |

## Prerequisites

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

Commands in **Installation**, **Quick Start**, **Testing**, and **Contributing** assume your shell is in `services/agent/` (this directory). From the repository root:

```bash
cd services/agent
```

Install system libraries required for PDF generation:

```bash
sudo apt-get install libcairo2-dev pkg-config python3-dev
```

Install `uv` and create the virtual environment. If Python 3.13 is not present on the system,
`uv` downloads it automatically:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.13
uv sync --extra agent
source .venv/bin/activate
```

The project ships three install profiles, smallest to largest: `nvidia-vss`
(the NAT-free `lib` libraries), `nvidia-vss[cli]` (adds the `nvidia-vss-cli`
distribution, which declares the `vss` console script), and
`nvidia-vss[agent]` (the full NAT-based agent application). The `cli` extra
gives the NAT-free environment used by the host CLI; it is required, because
the base distribution depends only on `nvidia-vss-core` and so provides no
`vss` executable:

```bash
uv run --no-dev --extra cli vss --help
```

### Docker

```bash
cd .. # Be at services/ (Docker build context; Dockerfile COPY paths use agent/)
docker buildx build --platform linux/amd64 -f agent/docker/Dockerfile -t vss-agent:latest --load .
cd agent  # back to services/agent/ for Quick Start and local development below
```

## Quick Start

The instructions below use the **dev-profile-base** profile as an example.
The same pattern applies to other profiles (search, alerts, LVS) — substitute the
corresponding `.env` and `config.yml` from
[`deploy/docker/developer-profiles/`](../../deploy/docker/developer-profiles/).
See [Configuration](#configuration) for the full list of profiles.

### 1. Set Environment Variables

Create a `.env_file` that points to the profile's `.env` so the agent auto-loads
environment variables on startup (one-time per profile):

```bash
echo "../../deploy/docker/developer-profiles/dev-profile-base/.env" > .env_file
```

Then source the same `.env` in your shell and override the placeholders.
`set -a` auto-exports every variable so child processes inherit them.
Because `HOST_IP` and `LLM/VLM_BASE_URL` are set **after** sourcing, every
variable the `.env` derived from them (VST URLs, Phoenix, reports URL, …)
must be re-evaluated — that is what the remaining lines do.

```bash
set -a
source ../../deploy/docker/developer-profiles/dev-profile-base/.env

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
  --config_file ../../deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml \
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
[`deploy/docker/developer-profiles/`](../../deploy/docker/developer-profiles/):

| Profile | Path | Description |
|---------|------|-------------|
| Base | [`dev-profile-base/.../config.yml`](../../deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml) | Video understanding and report generation |
| Search | [`dev-profile-search/.../config.yml`](../../deploy/docker/developer-profiles/dev-profile-search/vss-agent/configs/config.yml) | Search and RAG workflow |
| LVS | [`dev-profile-lvs/.../config.yml`](../../deploy/docker/developer-profiles/dev-profile-lvs/vss-agent/configs/config.yml) | LVS video understanding |
| Alerts | [`dev-profile-alerts/.../config.yml`](../../deploy/docker/developer-profiles/dev-profile-alerts/vss-agent/configs/config.yml) | Incident analysis and alerting |

Each profile has a companion `.env` file in the same directory with all deployment variables
pre-configured.

### Deployment version API

`GET /api/v1/version` exposes the version of the deployed VSS release for
automated compatibility checks. It is served by the agent, so it is reachable
wherever the agent is — directly at the agent service, and through the
deployment origin on profiles whose ingress routes `/api` to the agent. Some
profiles do not: the warehouse Helm ingress
(`deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app/templates/vss-ingress.yaml`)
declares no agent backend at all, so on that profile point the check at the
agent service rather than the deployment origin.

```console
$ curl -sS http://localhost:8000/api/v1/version
{"service":"vss","version":"3.3.0"}
```

`service` is always `vss`. `version` is the configured deployment version and
must be strict [Semantic Versioning 2.0.0](https://semver.org/):
`MAJOR.MINOR.PATCH`, optionally followed by a prerelease suffix and build
metadata (for example, `3.3.0-rc.1+build.42`). The official SemVer grammar is
enforced, so `03.3.0`, `3.3.0-01`, `3.3.0-.` and `v1.0.0` are all rejected.

**Which variable supplies it.** Two are consulted, in order:

| Order | Variable | Notes |
|-------|----------|-------|
| 1 | `VSS_DEPLOYMENT_VERSION` | Dedicated to the reported version. Feeds nothing else. |
| 2 | `VSS_AGENT_VERSION` | Legacy fallback. Also used for telemetry project naming and as the fallback container image tag. |

The first of the two that is set to a non-empty value wins. If that value is not
valid SemVer the endpoint returns HTTP 503 rather than falling through to the
other variable, so a misconfigured deployment reports a problem instead of
silently serving a different variable's value.

`VSS_DEPLOYMENT_VERSION` exists because `VSS_AGENT_VERSION` also resolves the
agent's container image tag
(`${VSS_CONTAINER_TAG:-${VSS_AGENT_VERSION:-develop-latest}}` in
[`compose.yml`](../../deploy/docker/services/agent/compose.yml)). Giving that
variable a default would change which image a deployment pulls;
`VSS_DEPLOYMENT_VERSION` appears in no `image:` line, so defaulting it cannot.

Both deployment paths default it, so a stock deployment answers 200 with no
operator action. An operator override can take that away — the 503 rules below
still apply to whatever the override sets:

- **Docker Compose** — [`containers.env`](../../deploy/docker/containers.env)
  and the inline default in
  [`compose.yml`](../../deploy/docker/services/agent/compose.yml) set `3.3.0`.
- **Helm** — the agent chart sets
  `vssDeploymentVersion | default vssAgentVersion | default .Chart.Version`, and
  the stock `vssAgentVersion` is `3.3.0-65576357eb80`, which is valid SemVer. A
  chart that pins `vssAgentVersion` keeps reporting that value **only when the
  pinned value is itself strict SemVer**. `vssAgentVersion` is also the fallback
  container image tag, so an image-tag-shaped value such as `develop-latest` is
  routine there and makes the endpoint answer 503. Whenever `vssAgentVersion`
  carries anything that is not a version, set `vssDeploymentVersion` explicitly.

**404 versus 503.** Both are failures to report a version, but they mean
different things and call for different operator actions. Reading 503 as the
only failure mode hides the case where the deployment is simply too old, or the
check is aimed at an origin that does not route `/api` to the agent.

| Status | Means | Operator action |
|--------|-------|-----------------|
| `404` | The deployment predates this endpoint and cannot report a version at all, **or** this origin's ingress does not route `/api` to the agent (the warehouse Helm ingress does not). | Upgrade the deployment, or point the check at the agent's own origin. |
| `503` | The deployment is new enough to serve the endpoint but is misconfigured: neither variable is set to a non-empty value (for example a bare `nat serve` with no deployment environment), or the winning value is not strict SemVer. | Set `VSS_DEPLOYMENT_VERSION` (`vssDeploymentVersion` on Helm) to a strict SemVer value. |

**Checking it.** [`scripts/check_vss_version.py`](scripts/check_vss_version.py)
checks the endpoint on any deployment — standard library only, so it can be
copied to a machine that has nothing but `python3`:

```console
$ python3 scripts/check_vss_version.py <base_url> [--timeout N] [--skill <path/to/SKILL.md> | --require '<range>']
```

`--skill` and `--require` are mutually exclusive. `--skill` is the preferred
form: the range is read out of the skill's own metadata, so the value a run
enforces cannot drift from the value the skill publishes. `--require` is for
ad-hoc checks. With neither, the script only prints the deployed version.

Exit codes — `0` is the only success:

| Code | Meaning | Detail |
|------|---------|--------|
| `0` | compatible | The deployment reported a version, and it satisfies the range when one was given. The version is printed on stdout. |
| `1` | indeterminate | The deployed version or the required range could not be determined: unreachable or timed out, HTTP 404, HTTP 503, any other HTTP error, non-JSON body, wrong JSON shape, a version that is not strict SemVer, an unreadable or front-matter-less `SKILL.md`, a `SKILL.md` with no `requires-vss` field, or a malformed or empty range. Reason on stderr, prefixed `error: cannot determine compatibility: `. |
| `2` | usage | Bad command line (argparse's own exit code). |
| `3` | incompatible | The deployed version is valid SemVer but outside the required range. Reason on stderr, prefixed `error: incompatible: `. |

A version-only check, and a range check that passes:

```console
$ python3 scripts/check_vss_version.py http://localhost:8000
3.3.0
$ python3 scripts/check_vss_version.py http://localhost:8000 \
    --skill ../../skills/benchmarking/benchmark-video-summarization/SKILL.md
3.3.0
$ echo $?
0
```

A range check that fails closed, here against a deployment reporting `3.2.1`:

```console
$ python3 scripts/check_vss_version.py http://localhost:8000 \
    --skill ../../skills/benchmarking/benchmark-vlm-qa/SKILL.md
error: incompatible: deployed VSS 3.2.1 is outside the range >=3.3.0,<4.0.0 required by this skill (skill version 3.3.0). Do not benchmark this deployment: the results would not be comparable. Either deploy a VSS release inside that range, or use a revision of the skill whose declared range covers the deployment. A prerelease of X.Y.Z counts as X.Y.Z, so the suffix is not what excluded it.
$ echo $?
3
```

A check whose answer is unknowable, here against a deployment origin that does
not route `/api` to the agent:

```console
$ python3 scripts/check_vss_version.py http://localhost:30080 --require '>=3.2.0,<4.0.0'
error: cannot determine compatibility: http://localhost:30080/api/v1/version returned 404: this deployment predates the version endpoint and cannot report a version, so its compatibility cannot be determined. Upgrade the deployment, or check the agent's own origin if the deployment ingress does not route /api to the agent.
$ echo $?
1
```

**How the benchmark skills gate a run.** This endpoint and the checker above are
the mechanism the VSS benchmarking skills use to decide whether a deployment may
be benchmarked at all. Each declares the deployment range it supports in a
`requires-vss` field under `metadata:` in its `SKILL.md` front matter.
`benchmark-video-summarization` enforces it in
[`preflight.sh`](../../skills/benchmarking/benchmark-video-summarization/scripts/preflight.sh),
which runs the check before every benchmark run and hard-fails the run on exit
`3`: benchmarking a deployment outside the range produces numbers that are not
comparable to the skill's own baselines.

| Skill | Declared `requires-vss` |
|-------|-------------------------|
| [`benchmark-video-summarization`](../../skills/benchmarking/benchmark-video-summarization/SKILL.md) | `>=3.2.0,<4.0.0` |
| [`benchmark-vlm-qa`](../../skills/benchmarking/benchmark-vlm-qa/SKILL.md) | `>=3.3.0,<4.0.0` |
| [`vss-evaluate-caption-accuracy`](../../skills/benchmarking/vss-evaluate-caption-accuracy/SKILL.md) | `>=3.2.0,<4.0.0` |

A range is a comma-separated list of comparators, **all** of which must hold —
`>=`, `>`, `<`, `<=` and `==`, each followed by a bare `MAJOR.MINOR.PATCH` bound
with no prerelease or build metadata (`>=3.2.0,<4.0.0`). Comparison uses those
three numbers only: **prerelease and build metadata are ignored, so a prerelease
of X.Y.Z counts as X.Y.Z.** That is deliberate. Helm defaults the deployment to
`3.3.0-65576357eb80` and Compose to a bare `3.3.0`, and under semver.org
precedence a prerelease precedes its release, so `>=3.3.0` would otherwise
reject the stock Helm deployment. The same skill must behave identically on
Compose and Helm, so the suffix is not considered.

**Who bumps a range.** The skill's own owner — the VSS benchmarking skill owner
named in the `author` field of its front matter (currently "NVIDIA Video Search
and Summarization Team") — owns widening `requires-vss` when a deployment ships
a new minor. A VSS release does not silently widen any skill's range, because
whether the skill's workflow and baselines still hold on that release is a
question only the skill's owner can answer. The check failing closed with exit
`3` is the intended signal that somebody must go and answer it.

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
| `LLM_NAME` | yes | — | LLM model name (e.g. `nvidia/nemotron-3.5-lightning-30b-a3b`) |
| `VLM_NAME` | yes | — | VLM model name (e.g. `nvidia/cosmos-reason2-8b`) |
| `LLM_MODEL_TYPE` | no | `nim` | LLM backend type: `nim`, `openai` |
| `VLM_MODEL_TYPE` | no | `nim` | VLM backend type: `nim`, `openai`, `vllm`, `rtvi` |
| `VLM_MODE` | no | `local_shared` | VLM deployment mode: `local_shared`, `local`, `remote` |
| `VST_INTERNAL_URL` | yes | — | VST internal URL (e.g. `http://HOST:30888`) |
| `VST_EXTERNAL_URL` | yes | — | VST external URL (e.g. `http://HOST:30888`) |
| `VSS_AGENT_PORT` | no | `8000` | Agent HTTP port |
| `VSS_AGENT_OBJECT_STORE_TYPE` | no | `local_object_store` | Object store: `local_object_store` (in-memory) or `s3` |
| `VSS_AGENT_REPORTS_BASE_URL` | no | — | Base URL for generated report assets |
| `VSS_DEPLOYMENT_VERSION` | no | `3.3.0` (Compose), `3.3.0-65576357eb80` (Helm, via `vssAgentVersion`) | Strict SemVer version reported by `GET /api/v1/version`; feeds no image tag |
| `VSS_AGENT_VERSION` | no | — | Telemetry project naming and fallback container image tag; also the fallback for `GET /api/v1/version` |
| `PHOENIX_ENDPOINT` | no | — | Phoenix tracing endpoint (e.g. `http://HOST:6006`) |
| `EVAL_LLM_JUDGE_NAME` | no | same as `LLM_NAME` | Model used for evaluation judge |
| `EVAL_LLM_JUDGE_BASE_URL` | no | same as `LLM_BASE_URL` | Endpoint for evaluation judge |
| `NGC_CLI_API_KEY` | cond. | — | Required when `LLM_MODE` / `VLM_MODE` is `local` or `local_shared` (Docker Compose) |
| `NVIDIA_API_KEY` | cond. | — | Required for build.nvidia.com remote endpoints |
| `INSTALL_PROPRIETARY_CODECS` | no | `true` in the Compose/Helm deployments (the bare image installs nothing when the variable is unset) | Install OpenCV/FFmpeg at container startup to enable video decoding (see [Proprietary multimedia codecs](#proprietary-multimedia-codecs)) |

## Proprietary multimedia codecs

The pre-built VSS Agent container image **does not bundle `opencv-python-headless`, any
FFmpeg binary, or any FFmpeg source archive**. The OpenCV wheel ships FFmpeg libraries
that contain **patent-encumbered codecs** (H.264, H.265, and variants), which NVIDIA
cannot redistribute. Following the VST team's approach, **all FFmpeg/codec libraries are
removed while building the container** (`libav*`, `libswscale`, `libswresample`,
`libpostproc`, `libx264/5`, ...), and the repository's FFmpeg source archive is not copied
into any image stage. An installation script reinstalls OpenCV and its bundled libraries
at runtime only when the operator opts in. A build-time guard in the Dockerfile and a CI
job (`.github/scripts/check_no_patented_codecs.py`) fail the build if any such library
leaks into the image. Tools that decode video (video understanding/captioning, frame
timestamp, S3 picture URL) therefore fail with a clear error in the default image and
require opting in to the proprietary codecs.

Video decoding is **enabled by default** in the Docker Compose and Helm deployments
(`INSTALL_PROPRIETARY_CODECS=true`). At container startup the agent downloads
`opencv-python-headless` **from PyPI onto your own machine** (never from an NVIDIA source) and
adds it to the runtime path. By leaving this enabled you are obtaining and using
patent-encumbered codecs and are responsible for any associated licensing. Set
`INSTALL_PROPRIETARY_CODECS=false` to keep them off (the bare image, with the variable unset,
also installs nothing).

```bash
# Docker Compose — opt out of the codec download
INSTALL_PROPRIETARY_CODECS=false docker compose ... up
```

Notes:

- The image itself never bundles anything patent-encumbered; the Compose/Helm deployments
  default `INSTALL_PROPRIETARY_CODECS=true`, so codecs are downloaded at startup unless you set
  it to `false`.
- The download (~45–90 MB) happens once per container and is cached under `/vss-agent/.codecs`
  (override with `VSS_PROPRIETARY_CODECS_DIR`). A `.installed` marker skips re-download on restart.
- **Air-gapped deployments:** pre-download the matching wheel and point
  `VSS_PROPRIETARY_CODECS_WHEEL` at it to install without network access.
- If the install fails (e.g. no network), the agent still starts; only video-decoding
  features are unavailable.
- On GPU deployments, hardware decode via PyNvVideoCodec/NVDEC is the codec-royalty-covered
  alternative and does not require this opt-in.

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
2. Install dev dependencies: `uv sync --group dev --extra agent`
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

This module is governed by **two separate licenses**, depending on what you use:

- **The source code in this directory and its subdirectories is licensed under the Apache License,
  Version 2.0.** The full license text is at the repository root: [`LICENSE`](../../LICENSE). If you
  clone, build, modify, or redistribute the source, Apache 2.0 terms apply.

- **The pre-built VSS Agent container images distributed by NVIDIA via NGC**
  (`nvcr.io/nvidia/blueprint/vss-agent` and related tags) **are licensed under the NVIDIA Software
  License Agreement.** If you pull and use NVIDIA's pre-built container
  images, the NVIDIA Software License Agreement governs your use; the agreement is conveyed by the
  distribution channel those images ship through.

Third-party open-source components bundled in the container image are attributed in
[`LICENSE-3rd-party.txt`](./LICENSE-3rd-party.txt).

The container image carries `LICENSE-3rd-party.txt` and `NVIDIA-Software-License-Agreement.pdf`
under `/vss-agent`. The agreement is **not** vendored in this source tree — the Dockerfile's `ADD` instruction
fetches it from `nvidia.com` at build time with a pinned SHA-256, which keeps the repository free
of a proprietary EULA and needs no HTTP client in any build stage. Note that `LICENSE.md` is source-only and is not copied
into the image.
