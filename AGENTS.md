# AGENTS.md

How to drive a VSS deployment from this repository, VSS CLI is the recommended approach to interact with a deployed VSS blueprint.

**Looking for a capability rather than the CLI?** [`skills/`](skills/) holds the
build, deploy and operational skills — deploy a profile, build a vision stack, search the archive,
ask about a video, manage alerts, generate a report. [`skills/README.md`](skills/README.md)
lists them; each `SKILL.md` says when to use it and when not to.

Human contributor guidance — licensing, DCO, file headers — is in
[CONTRIBUTING.md](CONTRIBUTING.md). What the blueprint *is* is in
[README.md](README.md). Neither is repeated here.

## The `vss` CLI

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
or `kubectl exec`.

### No deployment yet?

The CLI talks to a **running** stack; it does not stand one up. If there is
nothing to configure against:

[`/vss-build-vision-ai`](skills/vss-build-vision-ai/SKILL.md) takes the
capabilities you name — dense captioning, detection, search, alerting,
summarization — and composes, configures and deploys a stack for them, stock or
a custom combination. OR an industry profile, such as warehouse.

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

`configure` probes the origin's ingress routes — `/api`, `/vst`,
`/elasticsearch`, `/rtvi-embed`, `/rtvi-cv`, `/rtvi-vlm`, `/lvs`, `/va-mcp` —
and writes `~/.vss/config.json` (0600, no credentials). Re-run it after any
deployment change.

**Use the origin the host can reach, never the in-network gateway name.**
`VSS_PUBLIC_URL` is it: `http://<host>:7777` locally, or the platform's
`https://…` URL where TLS terminates in front of the deployment (a Brev secure
link forwards plain HTTP inward). Services *inside* the deployment address the
same HAProxy front door as `http://vss.local:7777` — that is `VSS_GATEWAY_ORIGIN`,
and it is correct for them and useless here: it is a container-network alias, so
configuring it fails every probe with a connection error and makes a healthy
deployment look broken. One front door, one path contract, two origins.

**Never construct an endpoint.** No `kubectl port-forward`, no Service DNS, no
NodePort, no reading `HOST_IP` or `VST_INTERNAL_URL` out of a container. The CLI
reads no process env for endpoints by design, so the same input behaves the same
way on any host. A command that exits 4 saying a service is missing is fixed by
`vss configure`, not by a flag.

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
8. `run` blocks until the job finishes — synchronous by design. Background it
   for a long summarization: the record is written before the model call, so
   `vss summarize status --job-id <id>` answers while the run is still in
   flight, and `get` returns the summary once it lands. `vss summarize list`
   finds the id, which `run` itself only prints when it exits.

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
| Video Analytics API | working on the analytics service | [`services/analytics/video-analytics-api/AGENTS.md`](services/analytics/video-analytics-api/AGENTS.md) |

