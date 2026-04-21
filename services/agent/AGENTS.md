# AGENTS.md

## Project Overview

NVIDIA VSS Agent — video search, summarization, and incident analysis built on
[NVIDIA AIQ Toolkit](https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html).

**Tech stack:** Python 3.13, NAT framework (nvidia-nat), LangChain/LangGraph, FastAPI,
Pydantic v2, OpenCV, xhtml2pdf. Package manager: `uv`. Linter/formatter: Ruff. Type checker: Mypy.

## Commands

```bash
# Setup
uv venv --python 3.13 && uv sync --group dev && source .venv/bin/activate
sudo apt-get install libcairo2-dev pkg-config python3-dev   # PDF generation deps
pre-commit install

# Test
uv run pytest tests/unit_test/ -v                           # all tests
uv run pytest tests/unit_test/tools/test_video_clip.py -v   # single file

# Lint & type-check (run all three after every change)
uv run ruff check src/                                      # lint
uv run ruff check src/vss_agents/tools/video_clip.py        # lint single file
uv run ruff format --check src/                             # format check
uv run mypy src/vss_agents/                                 # type check

# Run the agent (dev-profile-base example; see README.md Quick Start)
nat serve --config_file ../../deploy/developer-workflow/dev-profile-base/vss-agent/configs/config.yml \
  --host 0.0.0.0 --port 8000
```

## Project Structure

```
src/vss_agents/
├── agents/            # Orchestration agents (top_agent, report_agent, multi_report_agent)
│   └── postprocessing/  # Response validation (URL validator, etc.)
├── api/               # FastAPI endpoints, custom workers, RTSP/video ingest routes
├── data_models/       # Pydantic models shared across modules
├── embed/             # Embedding and vector-search utilities
├── evaluators/        # LLM-judge evaluators (trajectory, QA, report quality)
├── tools/             # NAT tools: video_understanding, report_gen, geolocation, …
│   ├── vst/           # Video Storage Toolkit tools (clip, snapshot, video_list)
│   └── code_executor/ # Sandboxed code execution (Docker backend)
├── utils/             # Shared helpers
└── video_analytics/   # Video Analytics MCP server and ES client
tests/unit_test/       # Mirrors src/ tree — every module has a matching test dir
stubs/                 # Mypy stubs for NAT framework (nat.data_models)
```

## Code Style

- **Line length**: 120 chars. **Quotes**: double. **Trailing commas**: yes.
- **Imports**: one per line, isort-sorted, `force-single-line = true`.
- **Type hints**: required on all function signatures. No `Any` without justification.
- **Dependencies**: sorted in `pyproject.toml`, `~=` with 2-digit precision (e.g. `~=1.2`).

```python
# ✅ Good
async def fetch_video_clip(sensor_id: str, start: float, end: float) -> VideoClipResult:
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    return await self._vst_client.get_clip(sensor_id, start, end)

# ❌ Bad — missing types, vague name, no validation
async def get(id, s, e):
    return await self._vst_client.get_clip(id, s, e)
```

**License header** (required on every Python file):

```python
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
```

## Architecture Patterns

- **Tools** subclass `FunctionBaseConfig` (NAT framework). Each tool has a `register.py`
  entry point listed under `[project.entry-points.'nat.components']` in `pyproject.toml`.
- **Agents** are LangGraph state machines (`top_agent.py` routes to tools and sub-agents).
- **Config** is YAML with `${ENV_VAR}` substitution. Profiles live in
  `../../deploy/developer-workflow/<profile>/vss-agent/configs/config.yml`.
- **Stubs**: `stubs/` has Mypy stubs for NAT. When subclassing a NAT base config,
  verify `uv run mypy src/vss_agents/` passes — extend the stub if needed.

## Testing

- Unit tests mirror `src/` under `tests/unit_test/`. Adding a new module? Add a matching test file.
- Use `pytest-asyncio` for async tests. Mark slow tests with `@pytest.mark.slow`.
- Mocking: mock external services (VST, LLM, VLM, Elasticsearch) — never call real endpoints in unit tests.

## Git Workflow

- Create a feature branch from `main`. Keep commits focused.
- Pre-commit hooks run `ruff`, `gitleaks` (secret scanning), and format checks automatically.
- Run `uv run pytest tests/unit_test/ -v` before pushing.

## Boundaries

- **Always**: add type hints, add the license header, run `ruff check` + `ruff format --check` + `mypy` after changes, write or update unit tests for new code.
- **Ask first**: adding new dependencies to `pyproject.toml`, modifying agent orchestration in `top_agent.py`, changing YAML config schema.
- **Never**: commit secrets or API keys, modify files under `3rdparty/`, remove or skip failing tests, hardcode IPs/URLs (use `${ENV_VAR}` in configs).
