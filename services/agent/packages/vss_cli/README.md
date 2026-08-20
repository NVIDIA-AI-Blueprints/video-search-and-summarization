# `vss` — the VSS command-line interface

The host-side entry point to a deployed VSS stack. Runs beside a deployment, not
inside it: no NAT, no torch, no GPU, no agent framework. One process per call,
JSON on stdout, typed exit codes.

Driving this from an agent or a skill? Read [AGENTS.md](AGENTS.md) — it is the
single source for the workflow, so individual skills do not restate it.

## Run it

**Agents and skills use the project-local form.** It pins the CLI to a known
checkout, which is why the skill evals require it and reject a globally
installed `vss` outright:

```bash
uv run --project services/agent --no-dev --extra cli vss --help
```

`--extra cli` is required: the base meta-package does not pull the
`nvidia-vss-cli` distribution that provides the `vss` executable.

## Install (human development)

For working on the CLI itself, where a `vss` on `PATH` is convenient:

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e "services/agent/packages/vss_core" -e "services/agent/packages/vss_cli"
vss --version
```

Do not use this form in a skill or an eval run — see [AGENTS.md](AGENTS.md#bootstrap).

## Point it at a deployment

Once per deployment. Everything after this takes no host, port, or endpoint.

```bash
vss configure --base-url https://vss.example.nvidia.com
vss configure show     # what was recorded
vss configure check    # re-probe; exit 3 if a route disappeared
```

`configure` probes the origin's ingress routes (`/api`, `/vst`, `/elasticsearch`,
`/cosmos-embed`, `/rtvi-cv`, `/rtvi-vlm`) and writes `~/.vss/config.json` (mode
0600, no credentials). Re-run it after any deployment change.

## The surface

| Group | What it is | Verbs |
|-------|-----------|-------|
| `vss search` | Fused archive search over ES + the embedding NIM | `run`, `status`, `get`, `list` |
| `vss summarize` | VLM summarization of stored video | `run`, `status`, `get`, `list` |
| `vss vios` | Media plane: sensors, timelines, clip and snapshot URLs | `list`, `timeline`, `clip`, `snapshot`, `add`, `delete` |
| `vss configure` | Resolve and record a deployment | `show`, `check` |

`search` and `summarize` are **job groups**: every run mints a `job_id`, and the
result stays retrievable by that id. `vios` is **not** — it resolves handles and
mints URLs, so it has no job verbs. See [AGENTS.md](AGENTS.md#the-two-shapes).

## Extending it

Groups are discovered from the `vss.commands` entry point, so a third party adds
one without touching this package:

```toml
[project.entry-points."vss.commands"]
acme = "acme_vss.entrypoint:GROUP"

[project.entry-points."vss.command_summaries"]
acme = "Acme video operations"
```

The object needs `api_version`, `name`, `summary`, and `cli() -> click.Command`.
Summaries are read as raw strings, so `vss --help` lists every installed group
without importing any of them.

## Develop

```bash
cd services/agent
uv sync --frozen --no-dev --group cli-dev --extra cli

uv run pytest packages/vss_cli/tests packages/vss_core/tests -q
uv run ruff check packages/vss_cli packages/vss_core
uv run mypy packages/vss_cli/src/vss_cli packages/vss_core/src/vss_core/vios
```
