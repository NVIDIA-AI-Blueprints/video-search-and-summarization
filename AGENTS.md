# AGENTS.md

How to drive a VSS deployment from this repository, and where the per-area
guides are. **This is the single place the CLI bootstrap is written down** — a
skill that needs VSS should link here rather than restate it. Instructions that
live in one skill are invisible to the next and drift the moment the CLI moves.

Human contributor guidance — licensing, DCO, file headers — is in
[CONTRIBUTING.md](CONTRIBUTING.md). What the blueprint *is* is in
[README.md](README.md). Neither is repeated here.

## The `vss` CLI

The host-side entry point to a **deployed** VSS stack. It runs beside the
deployment, not inside it: no NAT, no torch, no GPU, no agent framework. One
process per call — JSON on stdout, diagnostics on stderr, a typed exit code.
That is the whole contract; there is no SDK, server, or session to manage.

### Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python ≥3.13,<3.15.

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT" >&2; exit 1; }

# A function, not an alias: bash does not expand aliases in non-interactive
# shells, and neither survives into a separate command invocation.
vss() { uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss "$@"; }

vss --version
```
**What matters is provenance, not the form.** Run the `vss` that belongs to the
checkout you are testing. These are the same file — `uv run --project X` execs
`X/.venv/bin/vss` — so either is fine:

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss …
"${VSS_REPO_ROOT}/services/agent/.venv/bin/vss" …        # after a sync
```

The difference is that `uv run` syncs first, so the environment is guaranteed to
match the lockfile; calling the path directly assumes someone already synced it
with the right extras. If `vss --version` reports a build you did not expect,
that is why.

What is *not* fine is a `vss` from `PATH` — `~/.local/bin/vss`, pipx, `uv tool
install`. It can come from any checkout and nothing in the trace says which, so
a result cannot be attributed to the code under test. The skill evals reject one
outright: *"a globally installed `vss` is not an acceptable substitute"*. Those
checks also match the `uv run --project` string literally, so use that exact
form in an eval run.

Do not run it through `docker exec`, `kubectl exec`, or a pod shell either: it
is a client, and it talks to the deployment over the ingress like any other
client.

Working on the CLI itself, or wanting `vss` on `PATH` for a session:

```bash
cd "${VSS_REPO_ROOT}/services/agent"
uv sync --frozen --no-dev --group cli-dev --extra cli   # runtime + test tooling
source .venv/bin/activate
```

Sync once with that superset and then pass `--no-sync` to later `uv run` calls.
A bare `uv run --project … --no-dev --extra cli` re-resolves the environment to
exactly the runtime spec and **removes pytest**, which turns the next test run
into a wall of collection errors.

### Point it at a deployment

Once per deployment. Nothing after this takes a host, port, or service URL.

```bash
vss configure --base-url "${VSS_PUBLIC_URL}"   # e.g. http://localhost:7777
vss configure show                              # what was recorded
vss configure check                             # re-probe + what each group can serve
```

`configure` probes the origin's ingress routes and writes `~/.vss/config.json`
(0600, no credentials). Re-run it after any deployment change.

**Never construct an endpoint.** No `kubectl port-forward`, no Service DNS, no
NodePort, no reading `HOST_IP` or `VST_INTERNAL_URL` out of a container. The CLI
reads no process env for endpoints by design, so the same input behaves the same
way on any host. A command that exits 4 saying a service is missing is fixed by
`vss configure`, not by a flag.

### What is available here

A deployment rarely runs everything. `vss configure check` reports which groups
it can actually serve, so you learn it before you try rather than from a failed
run:

```
commands:
  search         unavailable  needs elasticsearch, rt_embed, rtvi_cv
  vios           available    vst
```

| Group | For | Verbs |
|-------|-----|-------|
| `vss search` | fused archive search over ES + the embedding NIM | `run`, `status`, `get`, `list` |
| `vss summarize` | VLM summarization of stored video | `run`, `status`, `get`, `list` |
| `vss vios` | media plane: sensors, timelines, clip and snapshot URLs | `list`, `timeline`, `clip`, `snapshot`, `add`, `delete` |
| `vss configure` | resolve and record a deployment | `show`, `check` |

`search` and `summarize` are **job groups**: `run` mints a `job_id` and the
result stays retrievable by it. `vios` is not — it resolves handles and mints
URLs, so it has no job verbs and its `list` lists *sensors*, not jobs.

### Exit codes — branch on these, not on stdout

| Code | Meaning | What to do |
|------|---------|-----------|
| 0 | Success | Parse stdout |
| 1 | Unexpected error | Report it; do not retry blindly |
| 2 | Invalid input | You asked for something impossible — fix the arguments |
| 3 | Backend unreachable | VSS or one of its services is down |
| 4 | Configuration | Run `vss configure --base-url <origin>` |
| 5 | Not found | The handle does not exist |
| 6 | Partial | Some results are missing; the payload says which |
| 7 | Timeout | Bounded wait expired; a `job_id` may be resumable |

**A non-zero exit always writes a diagnostic to stderr.** A non-zero exit with
no message is a bug worth reporting — not a reason to improvise a substitute
query. Improvising around a silent failure is how agents answer from data they
invented.

**An empty result is not a failure.** `{"count": 0}` at exit 0 means the
deployment genuinely has nothing matching; a backend problem exits 3. Never
treat the two as the same.

**Pipe carefully.** `vss … | jq` hides the CLI's exit code behind `jq`'s, so a
failed command with empty stdout reads as an empty answer. Use `set -o
pipefail`, or capture and check before piping.

### Rules

1. Configure once; never pass or construct an endpoint.
2. Branch on the exit code, not on parsing stdout for the word "error".
3. An empty result is an answer. Do not retry it as a failure.
4. Never fall back to raw REST when a command fails — report the failure. A
   hand-built query that returns *something* is worse than a clean failure,
   because nothing downstream can tell it was improvised.
5. Read identifiers from listings; never assemble one from a name.
6. Do not wrap commands in your own retry or timeout loops. Bounded waits are
   the CLI's job; a second layer hides which one gave up.
7. Cite the handle you were given — `media_url`, `job_id` — not one you rebuilt.

Per-command detail — sensor addressing, `--type`, window rules, what `vios`
covers and what it does not — is in
[`services/agent/packages/vss_cli/AGENTS.md`](services/agent/packages/vss_cli/AGENTS.md).

## Skills

`skills/` holds the operational skills — deploy a profile, search the archive,
ask about a video, manage alerts. Each carries a `SKILL.md` saying when to use
it and when not to; `skills/README.md` lists them. A skill that talks to a
deployment should drive `vss` and link here for the bootstrap.

## Other guides

| Area | Read when you are… | Guide |
|------|--------------------|-------|
| `vss` CLI internals | changing the CLI or its library | [`services/agent/packages/vss_cli/AGENTS.md`](services/agent/packages/vss_cli/AGENTS.md) |
| VSS Agent service | working on the agent: tools, workflows, the NAT stack | [`services/agent/AGENTS.md`](services/agent/AGENTS.md) |
| Video Analytics API | working on the analytics service | [`services/analytics/video-analytics-api/AGENTS.md`](services/analytics/video-analytics-api/AGENTS.md) |
| Skill evaluation | writing or debugging a skill eval | [`.github/skill-eval/AGENTS.md`](.github/skill-eval/AGENTS.md) |
| Helm sync | changing the Helm chart mirror | [`.github/helm-sync/AGENTS.md`](.github/helm-sync/AGENTS.md) |

## Two things that apply everywhere

- **Sign your commits.** `git commit -s`; DCO is enforced and unsigned commits
  are rejected.
- **Branch as `<type>/<name>`** matching your commit's conventional-commit type
  (`feat/`, `fix/`, `docs/`, `refactor/`, `test/`).
