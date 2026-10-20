# AGENTS.md — vision-ai

Instructions for any coding agent working in this workspace (`libs/vision-ai` in the VSS repo).
The repository-wide rules in the root `AGENTS.md` apply as well.

## Layout

- `libs/vision-ai/` — `nvidia-vision-ai` (`nvidia_vision_ai`). Pydantic in, pydantic out. It must not read
  config files, environment variables or `~/.vision-ai`, and must not import the CLI.
- `apps/cli/` — `nvidia-vision-ai-cli` (`nvidia_vision_ai_cli`). The `vision-ai` command, the Runtime
  (settings precedence, `~/.vision-ai` layout), the job store wiring and the plugin contract (`tool.py`).
- New tools implement `nvidia_vision_ai_cli.Tool` and register under the `vision_ai.tools` entry point.
  First-party tools follow the same path.

## Commands

Run from `libs/vision-ai`:

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest -q
```

CI runs all four in the `Test (vision-ai)` job.

## Rules

- Every source file starts with the NVIDIA SPDX header (`SPDX-FileCopyrightText`, `SPDX-License-Identifier: Apache-2.0`).
- Commits are signed off (`git commit -s`). Branches are named `<type>/<name>`.
- Exit codes in `apps/cli/src/nvidia_vision_ai_cli/exits.py` are a closed set that harnesses branch on.
  Add a new code only with a reason a caller would act on differently.
- Secrets (`SecretStr` settings) never become flags and never reach job records or stdout.
- Keep the base install light: the library depends on httpx, pydantic and tenacity; the CLI adds click.
  Knowledge backends sit behind optional extras and import them where they are used.
