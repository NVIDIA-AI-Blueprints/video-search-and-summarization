# `vss` — the VSS command-line interface

The host-side entry point to a deployed VSS stack. Runs beside a deployment, not
inside it: no NAT, no torch, no GPU, no agent framework. One process per call,
JSON on stdout, typed exit codes.

Driving this from an agent or a skill? The bootstrap and the cross-cutting
contract are in [AGENTS.md at the repository root](../../../../AGENTS.md);
per-command detail is in [AGENTS.md](AGENTS.md) beside this file.

## Run it

```bash
cd services/agent
uv run --no-dev --extra cli vss --help
```

`uv run` syncs `services/agent/.venv` on first use. `--extra cli` is required —
the base meta-package does not pull the `nvidia-vss-cli` distribution that
provides the executable — and `--no-dev` keeps it to the CLI's runtime
(256 MB, no `nvidia-nat`) rather than the agent stack (630 MB).

Driving a deployment from a skill or an agent? Read
[AGENTS.md at the repository root](../../../../AGENTS.md) — it is the one place
the bootstrap, `vss configure` and the exit-code contract are written down.

## Develop

```bash
cd services/agent
uv sync --frozen --extra cli
uv run --no-sync pytest packages/vss_cli/tests packages/vss_core/tests -q
```

Development needs the test tooling, so this one keeps the default group.

Keep `--no-sync` after that first sync, and note it works the other way too:
the `--no-dev` run above re-resolves to the runtime spec and drops pytest. The
two specs share one `.venv`, so pick the one matching what you are doing and
stay on it.

CI additionally runs a NAT-free lane (`--no-dev --group cli-dev`) to prove the
CLI imports nothing from the agent stack.

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
| `vss vlm` | One VLM answer from a recorded sensor window | `run`, `status`, `get`, `list` |
| `vss memory` | Unified-memory access and bounded introspection | `upsert`, `get`, `query`, `events`, `introspect` |
| `vss vios` | Media plane: sensors, timelines, clip and snapshot URLs | `list`, `timeline`, `clip`, `snapshot`, `add`, `delete` |
| `vss configure` | Resolve and record a deployment | `show`, `check` |

`search`, `summarize`, and `vlm` are **job groups**: every run mints a `job_id`, and the
result stays retrievable by that id. `vios` is **not** — it resolves handles and
mints URLs, so it has no job verbs. See [AGENTS.md](AGENTS.md#the-two-shapes).

Memory introspection requires an explicitly configured OpenAI-compatible text
LLM for sufficiency judgment and answer synthesis. RT-VLM is reserved for
grounded visual follow-ups. See [the memory policy and configuration
guide](MEMORY.md).

## Memory recall

Static memory policy is set once with `vss configure memory`; reads and writes
live under `vss memory`. With `--embeddings` enabled, VSS derives text vectors
into a versioned companion Elasticsearch index and answers text queries with a
hybrid ranking — keyword and semantic results fused client-side. Elasticsearch
canonical memory stays authoritative; its records only gain
`output.embedding` references to the companion documents.

The CLI loads no model. It calls an OpenAI-compatible `/embeddings` endpoint,
so CPU or GPU execution is decided by that endpoint's deployment, not here. The
defaults (`nvidia/llama-nemotron-embed-300m-v2`, 768 dimensions), the exact
flags, and the backfill are in [MEMORY.md](MEMORY.md).

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
