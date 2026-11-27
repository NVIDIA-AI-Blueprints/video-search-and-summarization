# Agent Harness

- [Model](#model)
- [Connecting the Web UI to the sandbox](#connecting-the-web-ui-to-the-sandbox)
- [The harness is never a service key](#the-harness-is-never-a-service-key)
- [What removing the agent implies](#what-removing-the-agent-implies)
- [Ordering](#ordering)
- [Prerequisites](#prerequisites)
- [Harness model and provider](#harness-model-and-provider)
- [Bring-up](#bring-up)
- [Verification](#verification)
- [Teardown](#teardown)
- [Sources](#sources)

## Model

A **harness** is what a person or another agent talks to in order to drive a
build. Two exist, they are **mutually exclusive**, and **at most one** is
deployed. `vss-agent` is removed unless the request names it, so a build carries
the sandbox, the in-stack agent, or no harness at all — never two.

**Q3 is yes/no:** **yes** *(default)* deploys the host-side OpenShell sandbox;
**no** leaves host `vss` as the driver. **`vss-agent`** is a separate explicit
name — skip Q3 and read [`services/agent.md`](services/agent.md). The sandbox is
the default Hermes image this repo builds; [`services/openshell/README.md`](../../../services/openshell/README.md)
owns gateway, policy, and bring-up. Headless builds get **no harness**.

**`vss-agent` is removed on either Q3 answer** unless the request named it.
Everything below about removal applies to yes and no alike; prerequisites,
ingress, and bring-up apply only on yes.

## Connecting the Web UI to the sandbox

When a sandbox build includes `vss-ui`, connect its chat sidebar and Chat tab to
the harness API server through the embedded adapter, per
[`services/openshell/README.md`](../../../services/openshell/README.md#optional-connect-the-vss-web-ui-chat).
Do it **after** the sandbox exists: the bearer is `API_SERVER_KEY`, minted per
sandbox inside `/sandbox/.hermes/.env`, and a sandbox recreated later invalidates
both it and the forward.

Nothing moves on the browser's side — it still enters through the build's
ingress, and `vss-ui` stays on its Compose network. Only the UI's own outbound
hop is new, and it cannot be the gateway-managed URL: the gateway binds the
host's loopback, which a bridged container has no route to. The forward
publishes the sandbox's `8642` on Docker's default-bridge gateway address, which
is what `host.docker.internal` resolves to inside the container.

The adapter settings go in a `ui-hermes.override.yml` beside the build's
`resolved.yml`, and `vss-ui` is recreated against it — `resolved.yml` already
carries the resolver's values, so never regenerate it for this:

| Variable | Value |
|---|---|
| `AGENT_ADAPTER_ENABLED`, `NEXT_PUBLIC_AGENT_ADAPTER_ENABLED` | `true` |
| `AGENT_BACKEND_PROTOCOL` | `responses` — Hermes' API server implements the stateful Responses semantics the connector already sends |
| `AGENT_BACKEND_URL` | `http://host.docker.internal:8642` |
| `AGENT_BACKEND_MODEL` | `hermes-agent` |
| `AGENT_BACKEND_TOKEN` | the sandbox's `API_SERVER_KEY` |
| `NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT`, `NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR`, `NEXT_PUBLIC_ENABLE_CHAT_TAB` | `true` |

Leave `AGENT_BACKEND_PATH` unset; `/v1/responses` is the `responses` default.

Once the overlay exists it is part of the build's deployment model: every later
command that creates or recreates a service passes
`-f resolved.yml -f ui-hermes.override.yml`, because `resolved.yml` alone brings
`vss-ui` back with the adapter off ([`deployment.md`](deployment.md#review-and-deploy)).

This connects the chat sidebar and Chat tab. It does not restore the Search tab
or the ingress `/api`, `/chat`, `/websocket` routes, which address the in-stack
agent directly.

## The harness is never a service key

The harness is **not** a Compose service of the build and **must not** enter
`COMPOSE_PROFILES`, appear in `compose.yml`, or receive a file under
`patches/`. It has no image in the root Compose graph and resolution would fail
on the invented key. Treat it exactly as `<name>` is treated in the artifact
contract: a label outside the Compose model.

The OpenShell **gateway** is a Compose service of the `vss` project
([`deploy/docker/services/openshell/compose.yml`](../../../deploy/docker/services/openshell/compose.yml),
behind the `openshell` profile), started by the bring-up rather than by the
build's `COMPOSE_PROFILES`. It is one per host, and a teardown of the `vss`
project removes it and its `openshell-data` volume, so teardown stops while
another sandbox remains on it ([Shared gateway](teardown.md#shared-gateway--before-compose)).

Selecting the sandbox therefore never *adds* a key to the build. All it adds is
a host-side step after deployment (below).

## What removing the agent implies

Applies to **both** Q3 answers — a `yes` and a `no` alike — and not to a build
whose request named the in-stack agent.

**Remove `vss-agent`, its `llm_*` peer, and `phoenix` together** from
`COMPOSE_PROFILES` unless `lvs-server` or a harness-local NIM still needs the
LLM NIM (`scripts/resolve_service_graph.py`). Keep `vss-ui` unless the request
is headless; its agent `depends_on` is optional and `normalize_resolved_yml.py`
drops dangling edges — never re-add a hard dependency in overrides.

### What the removal costs, and what it does not

Report what the build loses: Web UI chat (restore via the sandbox adapter when
wired), Search tab and ingress `/api` routes, LVS summarization in the UI, and
`vss-generate-video-report-rag`. Alerts, dashboards, VIOS ingest, and host
`vss` groups keep working — see `configure check` for what remains.

Source provisioning follows
[`provision-vios-source.md`](../../operations/vss-manage-video-io-storage/references/provision-vios-source.md)
on every deployment (`vss vios add`; no agent gate).

### Ingress is still required

The sandbox needs `vss-haproxy-ingress` and a grant for `host.openshell.internal:7777`
([`services/ingress.md`](services/ingress.md)). Set
`EXTERNAL_IP=host.openshell.internal` in `override.env` before resolve on most
profiles; on `alerts`, extend `haproxy.cfg` instead of repointing `EXTERNAL_IP`
(alert-bridge clip URLs). Removal makes any Q3 path a **delta build** unless the
request named `vss-agent`. Report GPU freed by dropping `llm_*` and, on yes,
both UI chat and harness API entry points ([`brev.md`](brev.md) for origin links).

## Ordering

The harness needs the build's origin, and that origin does not exist until the
deployment answers. Run the steps in this order and no other:

| # | Step | Why here |
|---|---|---|
| 1 | Deploy `resolved.yml` | nothing to point a harness at yet |
| 2 | Readiness gate ([`readiness.md`](readiness.md)) | a harness pointed at a half-warm stack reports failures that are not its own |
| 3 | Resolve the origin | `http://$HOST_IP:$HAPROXY_HOST_PORT` from the deployed build |
| 4 | Bring up the harness | consumes that origin |

Skipping the readiness gate is the common failure: the sandbox comes up, the
first call from it fails, and the cause looks like the harness.

The CLI install, the gateway and the harness image depend on none of this and
can be done at any point; only `sandbox create` consumes the origin.

## Prerequisites

Beyond everything in [`prerequisites.md`](prerequisites.md) and
[`credentials.md`](credentials.md), the harness step needs the following.

**Check them at Q3, not at bring-up.** The sandbox is the default, so a build can
reach these requirements without anyone having asked for them. Any one missing is
a **blocker at harness selection**: name it, ask whether to supply it, proceed
with no harness, or name the in-stack agent instead, and deploy nothing until
that is answered. Discovering it after the readiness gate means a deployed build
with no way to drive it.

- **`docker`, `curl`, `tar` and `sha256sum` on `PATH`**, and outbound reach to
  GitHub releases. **Do not require the `openshell` CLI here**: a fresh host is
  a supported starting point, and Quickstart step 0 installs `openshell` and
  `openshell-prover` under `~/.local/bin` at the gateway's release.
- **An agent model provider**, and only the credential that provider needs. This
  is the harness's *own* LLM, unrelated to the build's `LLM_*` and `VLM_*` knobs.
  See [Harness model and provider](#harness-model-and-provider); the credential
  is **collected from the user** at Q3a, which is where a missing one surfaces.
- **`127.0.0.1:8080` free, or held by an OpenShell gateway.** The gateway is one
  per host, not per build or per sandbox, and re-running its Compose service
  leaves a live one alone — so a gateway already there is reused, not torn down.
  Anything else on that port is a blocker whose remedy is the user's.
- **`8642` free on Docker's default-bridge gateway address**, and only when the
  build wires the Web UI: that is what the forward publishes. A sandbox with no
  UI adapter needs no host port at all, because `--expose` routes through the
  gateway's own listener.
- **An ingress on `7777`.** The baked policy's `vss-backend` grant allowlists
  the literal pair `host.openshell.internal:7777` and interpolates nothing, so
  **a build that moves `HAPROXY_HOST_PORT` off `7777` has no matching entry**
  and every call from the sandbox returns `CONNECT tunnel failed, response 403`.
  Report that as a blocker naming the port; the policy is a checked-in asset
  proven against a boundary, not something to rewrite per build.

The LLM NIM's published port is in the same position: neither `30081` (`LLM_PORT`)
nor `30082` (a VLM NIM) is in any grant. A harness pointed at the build's own NIM
therefore needs the host-local provider profile, which declares the ports it may
reach — see the provider table below.

### Sandbox names on a shared host

`openshell sandbox list` shows every name on the shared gateway; `create`
replaces an existing holder and discards its sessions. Record the name in
`_builds/<name>/sandbox` **before** bring-up. At Q3, ask before replacing any
name another build or a hand-run Quickstart holds — `hermes` included — unless
this build's own `_builds/*/sandbox` record already claims it.

## Harness model and provider

**Default to the remote OpenAI-compatible endpoint — Claude Opus 5 through the
NVIDIA Inference Hub**, which is what
[`openshell.env`](../../../deploy/docker/services/openshell/openshell.env) ships.
First ask the user which model the sandbox runs on, per [Harness model —
Q3a](../SKILL.md#harness-model--q3a). Another model on this same endpoint
replaces `HARNESS_MODEL` alone and keeps every other value below. Only a
request for a different *endpoint* changes the provider profile.

| Variable | Default | Note |
|---|---|---|
| `HARNESS_MODEL` | `aws/anthropic/bedrock-claude-opus-5` | Claude Opus 5 as the Inference Hub routes it. The id is **the endpoint's, not the model's**: `azure/anthropic/claude-opus-5` is the same model over another route, a provider's public API serves the bare `claude-opus-5`, and a model router takes a **route id**. Replace it whenever the endpoint changes |
| `HARNESS_BASE_URL` | `https://inference-api.nvidia.com/v1` | the NVIDIA Inference Hub gateway, reachable from NVIDIA infrastructure. Any OpenAI-compatible base URL replaces it: another gateway, a model router, a self-hosted server, or a provider's public API |
| `HARNESS_PROVIDER` | `agent-inference` | matches `openshell.env`; the profile below that authorizes `HARNESS_BASE_URL`'s host, and the name of the provider created from it |
| `HARNESS_API_KEY` | **no default** | the one credential name every provider profile declares; a real bearer token is required for a public endpoint. Take it from the environment or the platform secret store — never a literal in a command, a file, or skill output. Pass it **unquoted**: quotes are sealed into the credential |

**The profile is what authorizes the endpoint's host**, so an endpoint outside
the imported profile is unreachable however correct the URL is. One file each
covers the three choices, and only one is imported:

| Endpoint | `HARNESS_PROVIDER` (profile under `services/openshell/providers/`) | `HARNESS_API_KEY` |
|---|---|---|
| (a) OpenAI-compatible endpoint — the skill default | `agent-inference` | the endpoint's key |
| (b) a model served on this host, including the build's own LLM NIM | `agent-inference-local` | optional |
| (c) build.nvidia.com | `agent-inference-build` | an `nvapi-…` key, the `NVIDIA_API_KEY` value when the build has one |

Within a family the model id is free. Profile (b) declares `host.openshell.internal`
on `8000` (vLLM, NIM) and `11434` (Ollama) only, so a server on any other port —
the build's NIM on `30081` included — is unreachable until an entry is added to
a copy of that profile. Say so before taking the route rather than after the
first turn fails.

The agent never holds the key: the supervisor injects the real credential at
egress, which is why the image's `api_key` stays an `${env:HARNESS_API_KEY}`
placeholder and only its presence matters.

Keys for the default endpoint come from <https://inference.nvidia.com/key-management>.
Other rows in the provider table name their own sources. Read exported
`HARNESS_*` values and autonomous-mode env first ([`SKILL.md`](../SKILL.md#exception--autonomous-mode));
a missing required key is a blocker — never substitute another provider.

Profile (b) can reuse the build's LLM NIM when `LLM_MODE` is `local` or
`local_shared`; add the NIM port to the local profile and expect KV contention
with build traffic. Profile (c) needs an `nvapi-…` key from build.nvidia.com.

## Bring-up

Do not reimplement any of this.
[`services/openshell/README.md`](../../../services/openshell/README.md#quickstart) is the
single source of host-side harness logic — CLI install, gateway, image,
provider, sandbox, skill activation. From the checkout root, run the Quickstart
through step 4, then
[verify and point the sandbox at VSS](../../../services/openshell/README.md#5-verify-and-point-the-sandbox-at-vss).
This section says only what a *build* changes about them.

**OpenShell v0.1.6 idempotency:** `profile import` or `provider create` may
report already-exists on a shared host — run `openshell provider update
"$HARNESS_PROVIDER" --credential HARNESS_API_KEY --wait`. Before `sandbox
create` on an approved replacement name, `openshell sandbox delete` first.

**Operational Harbor evaluations:** when a caller sets
`SKILLS_EVAL_OPERATIONAL_HARNESS=nemoclaw`, attach NemoClaw through
`deploy/docker/scripts/deploy_nemoclaw.ipynb` and `.openclaw/`; do not
substitute the OpenShell `hermes` sandbox.

Write build overrides into gitignored `harness.env` (or export them only after
sourcing `openshell.env`) so step 1 never clobbers build values. The commands
source `openshell.env`, then `harness.env` when it exists, then apply shell
overrides:

| Value | For a build | Why |
|---|---|---|
| `VSS_PUBLIC_URL` | `http://host.openshell.internal:7777` — the `openshell.env` default, unchanged for a Compose build | the origin as **the sandbox** sees it, not `HOST_IP`. Set it only for a Kubernetes deployment, to that cluster's Ingress origin, which then also needs a policy entry |
| `HARNESS_BASE_URL`, `HARNESS_MODEL`, `HARNESS_PROVIDER` | the Q3a answers | the model values and the profile that authorizes them; a model change is a re-create, not a rebuild |
| `HARNESS_API_KEY` | from the environment or the secret store | the selected provider's credential, unquoted. Never into `openshell.env`, `override.env`, a transcript, or a summary |
| sandbox name | `hermes`, the README's own default, unless this host already holds that name | the handle `sandbox create`, `sandbox delete` and `sandbox exec` take; settled at Q3, per [Sandbox names on a shared host](#sandbox-names-on-a-shared-host) |

Record the name before the run, not on success — `create` is not the last step,
and a failure in a later one still leaves a live sandbox that teardown reaches
only through this file:

```bash
REPO="$(git rev-parse --show-toplevel)"
printf '%s\n' "$SANDBOX_NAME" > "$REPO/_builds/<name>/sandbox"
```

Capture the run: `… 2>&1 | tee "$REPO/_builds/<name>/harness-setup.log"`, with
`set -o pipefail` so the status is the command's and not `tee`'s, and `umask 077`
because the log echoes the settings. A non-zero exit is a blocker: report it with
the log path and stop, rather than going on to the UI link. Say that the build
may hold a live sandbox and name it — the claim is on disk, so
[Teardown](#teardown) removes it like any other build's.

### Harness source ref

Only when SKILL.md's [Harness source ref](../SKILL.md#harness-source-ref)
selected one. The build reads its context, so the ref is checked out into a
worktree and the build runs from there. A ref that does not resolve is a
blocker to report rather than a reason to fall back to the checkout:

```bash
REPO="$(git rev-parse --show-toplevel)"
SRC="$REPO/_builds/<name>/harness-src"
git -C "$REPO" fetch -q origin "$REF" && git -C "$REPO" worktree add -q --detach "$SRC" FETCH_HEAD
grep -q '^COPY \.hermes/plugin ' "$SRC/.hermes/Dockerfile" 2>/dev/null || {
  echo "$REF predates the OpenShell harness image; no sandbox can be built from it" >&2
  git -C "$REPO" worktree remove "$SRC"; false; }
VERSION="$(cd "$SRC/libs/vss/cli" && uvx --with hatch-vcs hatch version)"
docker build -f "$SRC/.hermes/Dockerfile" \
  ${HERMES_REF:+--build-arg HERMES_REF="$HERMES_REF"} \
  --build-arg VSS_PACKAGE_VERSION="$VERSION" \
  -t "openshell-hermes-vss:${VERSION//+/-}-from-source" "$SRC"
git -C "$REPO" worktree remove "$SRC"
```

Both the Dockerfile and the context come from the worktree, so the ref builds
its own image definition. A ref whose `.hermes/Dockerfile` does not build from
the repository root predates this image, and is a blocker to report, never a
build to retry another way. The version names the ref's own commit, so two refs
coexist as two images.
Quote the ref and the version `vss --version` reports inside the sandbox in the
summary.

The sandbox image ships only the **operation** skills — the ones whose `SKILL.md`
declares `vss-requires` — and `vss-hermes-sync` activates those the recorded
deployment can serve after `vss configure`. It does **not** receive this skill:
the sandbox operates the build it was given and is not expected to compose
further builds or manage the `_builds/` tree this run produced.

## Verification

The checks are in
[`services/openshell/README.md`](../../../services/openshell/README.md#5-verify-and-point-the-sandbox-at-vss) —
prover, sandbox state and effective policy, the exposed route, an end-to-end
agent turn, and the two that need a deployment: the CLI and its grant. Run
them, then confirm the two things a build adds:

1. **The build's own origin answers from inside the sandbox.** The Quickstart's
   last step already runs `vss configure --base-url "$VSS_PUBLIC_URL"`; read
   what it prints. A partial deployment is normal, and the inactive skills name
   the service they wanted. A `403 CONNECT tunnel failed` is the egress policy
   (see [Prerequisites](#prerequisites)), not a deployment fault — the two have
   opposite fixes.
2. **The Web UI reaches the sandbox**, when the adapter was wired: verify
   through the ingress rather than the container, so the check covers the path
   the browser takes (`/api/agent/connection` → `{"state":"connected"}`, per
   the README's UI option).

### Name the sandbox in the summary

Report the sandbox name next to the harness link. It is the handle every later
command takes — `openshell sandbox exec -n <name>`, `openshell policy get
<name>`, and the [Teardown](#teardown) delete. The sandbox lives outside the
Compose project, so nothing that lists the build reveals it, and a second build
under the same name replaces it. Say with it whether the bring-up **replaced** an
existing sandbox of that name, discarding its agent sessions; nothing else in the
run tells the user that happened.

The harness API server is authenticated with the per-sandbox `API_SERVER_KEY`,
so report its URL and hand over the recipe rather than the value:
`openshell sandbox exec -n <name> --no-tty -- sh -c '. /sandbox/.hermes/.env;
printf %s "$API_SERVER_KEY"'`. Never copy the key itself into the response or
into `override.env`.

## Teardown

The harness and the build are independent lifecycles: nothing in Compose
reaches the sandbox. Tearing down a build therefore starts with
[`teardown.md`](teardown.md#harness-sandbox--before-compose), which stops the
UI forward and deletes the sandbox.

Delete the sandbox **before** the Compose project when doing both, so the
harness is not left pointed at an origin that has stopped answering. Removing
the build's containers alone leaves a healthy sandbox with a dead origin, which
reports as a skill failure rather than a missing deployment.

The gateway, the image and the provider survive a sandbox delete by design, and
a later build reuses all three. A teardown of the `vss` project removes the
gateway and its `openshell-data` volume, provider included, once no other
sandbox remains on it, so the next bring-up starts both again.

## Sources

- [`services/openshell/README.md`](../../../services/openshell/README.md) — the harness itself: architecture, Quickstart, steps, verification
- [`deploy/docker/services/openshell/`](../../../deploy/docker/services/openshell/), [`.hermes/`](../../../.hermes/), [`services/openshell/providers/`](../../../services/openshell/providers/)
- `deploy/docker/services/ui/compose.yml`
- `services/ui/DOCKER-README.md`
