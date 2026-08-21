# AGENTS.md

How to drive a VSS deployment from this repository, and where the per-area
guides are. **This is the single place the CLI bootstrap is written down** — a
skill that needs VSS should link here rather than restate it. Instructions that
live in one skill are invisible to the next and drift the moment the CLI moves.

**Looking for a capability rather than the CLI?** [`skills/`](skills/) holds the
operational skills — deploy a profile, build a vision stack, search the archive,
ask about a video, manage alerts, generate a report. [`skills/README.md`](skills/README.md)
lists them; each `SKILL.md` says when to use it and when not to.

Human contributor guidance — licensing, DCO, file headers — is in
[CONTRIBUTING.md](CONTRIBUTING.md). What the blueprint *is* is in
[README.md](README.md). Neither is repeated here.

## The `vss` CLI

The host-side entry point to a **deployed** VSS stack. It runs beside the
deployment, not inside it: no NAT, no torch, no GPU, no agent framework. One
process per call — JSON on stdout, diagnostics on stderr, a typed exit code.
That is the whole contract; there is no SDK, server, or session to manage.

### Setup

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
vss() { uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss "$@"; }
vss --version
```

A function rather than an alias — aliases are not expanded in non-interactive
shells. **`--extra cli` is required**: without it the CLI is not installed and
there is no `vss` to run. **`--no-dev` matters too**: it is what keeps the
environment to the CLI's runtime — 256 MB with no `nvidia-nat` — where the
default group pulls the agent stack and 630 MB you have no use for.

Use that checkout's `vss` — not one from `PATH`, and not through `docker exec`
or `kubectl exec`. It is a client that reaches the deployment over the ingress,
and a binary of unknown provenance cannot be attributed to the code under test.
The skill evals reject a globally installed one outright.

### No deployment yet?

The CLI talks to a **running** stack; it does not stand one up. If there is
nothing to configure against:

[`/vss-build-vision-agent`](skills/vss-build-vision-agent/SKILL.md) takes the
capabilities you name — dense captioning, detection, search, alerting,
summarization — and composes, configures and deploys a stack for them, stock or
a custom combination.

Then `vss configure --base-url <origin>` against what came up.

A partial deployment is normal and fine: `vss configure check` reports which
command groups it can serve, and the rest fail with exit 4 naming what is
missing rather than misbehaving.

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

Listed in [`skills/README.md`](skills/README.md). A skill that talks to a
running deployment should drive `vss` and link here for the bootstrap, rather
than carrying its own copy.

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
