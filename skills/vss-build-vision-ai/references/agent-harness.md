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

**Q3 is binary — these are its only two outcomes:**

| Q3 answer | Outcome | Where it runs | Reached by |
|---|---|---|---|
| **yes** *(default)* | the sandbox | an OpenShell sandbox on the host, outside the Compose project | the Web UI chat, the harness API server, or `openshell sandbox exec` |
| **no** | no harness | — | the `vss` CLI from the host |

**Separate explicit-name path — not a Q3 option:** `vss-agent` runs inside the
Compose project and is reached through the agent REST API (`/generate`) and Web
UI. Select it through the Agent owner ([`services/agent.md`](services/agent.md))
only when the request names it; skip Q3 entirely.

`vss-agent` is in-stack: it is a container, it is reached through the build's own
origin, and forward closure retains it whenever agentic orchestration is
requested or another owner declares it as a peer — unless the sandbox is
selected, which removes it. The sandbox is host-side: an
[OpenShell](https://docs.nvidia.com/openshell/latest/) sandbox running the
Hermes harness with this repository's operation skills and the `vss` CLI baked
in, driving the deployment from outside over the same public routes an operator
would use. [`services/openshell/README.md`](../../../services/openshell/README.md) owns it end
to end — gateway, policy, image, provider, sandbox; this file says only what a
*build* changes about it.

**The sandbox is the default harness.** When a build has an interactive surface
and the request names no harness, Q3 asks one yes/no question — deploy the
sandbox as the harness? — and **yes** is the default, including for an
unanswered Q3. A **no** means no harness: the build is driven by the `vss` CLI
from the host. Never turn that into a menu of harnesses; the only question is
whether the sandbox is deployed. Hermes is the harness the checkout ships an
image for, so it is not a choice either.

**`vss-agent` is removed on either answer.** The in-stack agent is deployed only
when the request names it — the chat agent, the Web UI, the agent REST API — and
such a request skips Q3 the way any named harness does. Honour it; do not steer
it to the sandbox. Everything below about the removal therefore applies to a
`no` as much as to a `yes`; only the [Prerequisites](#prerequisites),
[ingress](#ingress-is-still-required), and bring-up sections are the sandbox's
alone.

A build that reaches no interactive surface still has **no harness at all** — the
correct outcome for one that only ingests, indexes, or serves an API. Defaulting
to the sandbox never means adding a harness to a headless build.

## Connecting the Web UI to the sandbox

When a sandbox build includes `vss-ui`, connect its chat sidebar and Chat tab to
the harness API server through the embedded adapter, per
[`services/openshell/README.md`](../../../services/openshell/README.md#optional--answer-from-the-vss-ui).
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

This connects the chat sidebar and Chat tab. It does not restore the Search tab
or the ingress `/api`, `/chat`, `/websocket` routes, which address the in-stack
agent directly.

## The harness is never a service key

The harness is **not** a Compose service of the build and **must not** enter
`COMPOSE_PROFILES`, appear in `compose.yml`, or receive a file under
`patches/`. It has no image in the root Compose graph and resolution would fail
on the invented key. Treat it exactly as `<name>` is treated in the artifact
contract: a label outside the Compose model.

The OpenShell **gateway** is a Compose service, but of its own pinned `openshell`
project ([`deploy/docker/services/openshell/compose.yml`](../../../deploy/docker/services/openshell/compose.yml),
behind the `openshell` profile), not of the build. It is one per host and survives
any number of builds, so it is brought up once and left alone — the build's own
`docker compose down` neither reaches it nor should.

Selecting the sandbox therefore never *adds* a key to the build. All it adds is
a host-side step after deployment (below).

## What removing the agent implies

Applies to **both** Q3 answers — a `yes` and a `no` alike — and not to a build
whose request named the in-stack agent.

**Remove `vss-agent` from the Foundation's `COMPOSE_PROFILES` together with the
two peers only it uses: the `llm_*` key and `phoenix`.** They go as one unit.
The `llm_*` peer is the in-stack agent's LLM: of the `base` services only
`vss-agent` calls the LLM (`services/agent/compose.yml`; the only other consumer
is `lvs-server`, outside `base` - `alert-bridge` never calls it: its compose
passes `LLM_MODE`, which nothing in the service reads, and its URL rewriting
keys on `VLM_MODE`), and the harness brings its own model provider - the NIM is
the build's largest GPU claim and image pull.
`phoenix` collects the agent's traces and has no other client; `haproxy` only
routes to it, and tolerates an absent backend exactly as it does the absent
agent. Keep `llm_*` only when an enabled service still consumes it (an
`lvs` or combined build keeps it for `lvs-server`) or
the harness LLM is pointed *at the build's own LLM NIM*, which needs the
NIM resident - name the key in `REQUESTED_PROFILES` so Step 5's validator keeps
it. This is the agent-owned removal Step 5's harness-only invariant applies,
and `scripts/resolve_service_graph.py` enforces. `vss-ui` stays: pruning it is
a capability decision, not a harness one.

`vss-ui`'s dependency on the agent ships as `required: false` so the filtered
project still resolves, and `scripts/normalize_resolved_yml.py` drops the
dangling entry. Never re-add a hard `depends_on` in a build override — Compose
rejects a project whose enabled service hard-depends on a filtered one, and
Step 8 fails with no `resolved.yml`.

`vss-ui` is worth keeping with no agent: its Alerts, Dashboard, and Video
Management tabs address Alert Bridge, Kibana, and VST directly. An explicit
"headless" request drops it as well — honour that, and report the loss of those
three tabs.

### What the removal costs, and what it does not

Report every one of these that the build has, whenever the agent is removed:

| Surface | Effect |
|---|---|
| Web UI chat sidebar, Chat tab, Search tab | the sidebar and Chat tab answer through the sandbox when the adapter is wired ([Connecting the Web UI to the sandbox](#connecting-the-web-ui-to-the-sandbox)); with no harness they stop. The Search tab stays dead either way because it addresses `/api/v1/search`, which the adapter does not replace. The Alerts, Dashboard, and Video Management tabs keep working, because they address Alert Bridge, Kibana, and VST directly — including Video Management's upload and delete, which never went through the agent |
| Alerts tab, *Generate Report* | goes with the sidebar it drives. The incident list and rule CRUD stay, on `video-analytics-api` and Alert Bridge |
| Web UI summarization on `lvs` | with no harness, gone: the UI ships no LVS client, so the capability is `vss summarize` from the host and the UI is a dashboard |
| Ingress `/api`, `/chat`, `/websocket` | `503`. HAProxy still starts — `bk_vss_agent` is declared `init-addr none` — and the origin's root still serves the UI |
| Search **ingestion and deletion** | use `vss vios add` / `delete` on Agent-backed and headless deployments; configured notification receivers perform fan-out and cleanup |
| `vss-generate-video-report-rag` | unavailable: it drives the agent's `/v1/chat` and `/executions`. Route reports through `vss-generate-video-report`, which never calls the agent |

Nothing else in the operate set needs it. No `vss` command group declares the
agent a requirement: `configure` probes each route independently and simply omits
`/api`, while `summarize`, `search` (query), `vlm`, `vios`, and `memory` address
LVS, Elasticsearch, RT-Embed, RT-VLM, and VIOS directly. `vss-ui` holds the only
`depends_on` in the whole graph, and it is optional; no other service in any
profile calls the agent, so alerting, analytics, ingest, and summarization are
unaffected.

### Source provisioning on every deployment

On Agent-backed and headless deployments, source provisioning follows
`vss-manage-video-io-storage`
[`provision-vios-source.md`](../../operations/vss-manage-video-io-storage/references/provision-vios-source.md):
register one source with `vss vios add`, which the mounted notification config
fans out to enabled receivers. There is no Agent-presence gate or `/complete`
prerequisite. Check the receivers for the requested capability before claiming
indexing readiness. Alert rules stay with `vss-manage-alerts`, which addresses
Alert Bridge.

### Ingress is still required

The sandbox reaches the build only over host-published HTTP, and the host-CLI
read path has no ingress-less form (see
[`deployment_resolution.md`](deployment_resolution.md) and
[`services/ingress.md`](services/ingress.md)). `vss-haproxy-ingress` must be in
the effective service set, carrying the operate route-set. A request that pairs
the sandbox with "no ingress" is a **capability contradiction** — take it to the
clarification gate; do not resolve it by dropping either side.

Shipping the ingress is necessary but not sufficient. HAProxy 404s any `Host`
header outside its `known_host` allowlist, which admits only `VSS_PUBLIC_HOST`,
`EXTERNAL_IP`, `HOST_IP`, and localhost ([`services/ingress.md`](services/ingress.md)).
The sandbox reaches the host as `host.openshell.internal`, so every Compose
harness build 404s on its own origin until that name holds one of those slots.
Set `EXTERNAL_IP=host.openshell.internal` in `_builds/<name>/override.env` before
resolving, and comment the line. `EXTERNAL_IP` is the slot to spend: `HOST_IP`
must stay bridge-reachable, and the URLs users follow resolve from
`VSS_PUBLIC_HOST`. Do not ask the user — it is a mechanical consequence of
deploying the sandbox.

Not on `alerts`: `alert-bridge` rewrites clip URLs from `INTERNAL_IP` to
`EXTERNAL_IP`, so repointing it makes alert evidence unopenable. There the
curated `haproxy.cfg` is **required** — admit `host.openshell.internal` per
[`services/ingress.md`](services/ingress.md) and leave `EXTERNAL_IP` alone.
Do not settle for the origin 404 on the grounds that alerts operate reaches
Alert Bridge and VA-MCP on their host ports: it costs the sandbox the ingress
origin every other skill resolves against.

### Either answer makes it a delta build

Removing `vss-agent` is a capability delta, so a named profile that reaches Q3 is
a **delta build** on a `no` as much as a `yes` — create `_builds/<name>/` and
follow Delta mode from Step 2. Only a request that names the in-stack agent, and
so never reaches Q3, can stay a stock deploy.

### Cost to report, not to optimize away

Two things the user should hear up front rather than discover:

- **GPU and memory budget.** `vss-agent` reserves no GPU, so its removal frees
  memory rather than a device; pruning its `llm_*` peer (above) is what frees
  GPU memory, and a build that keeps the NIM for `lvs-server` or
  for the harness must say so. Budget the build against [`sizing.md`](sizing.md)
  plus the harness's own model provider, which costs no GPU on the default
  remote endpoint and whatever its server already claims on a host-local one.
- **One harness, two entry points.** On a **yes**, the build UI chat and the
  harness API server both reach the sandbox. Report **both as markdown links**
  and name the sandbox as the driver. On a **no**, report the browse origin
  alone and name the `vss` CLI as the driver. The build's origin is
  `VSS_PUBLIC_HOST`; on Brev that is the FQDN the context file publishes for the
  ingress port, resolved rather than constructed ([`brev.md`](brev.md)). Never
  `EXTERNAL_IP`, which on a sandbox build holds `host.openshell.internal` and
  resolves only inside the sandbox.

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

`openshell sandbox list` is the whole picture: the gateway is shared, so every
sandbox on the host is visible, whoever created it. A `create` reuses a name by
replacing what holds it, discarding that sandbox's agent sessions.

Record the name in `_builds/<name>/sandbox` **before** bring-up, so a run that
fails midway still leaves teardown a handle. That record is also this build's
only claim on the name: the `vss-harness-hermes` default proves nothing, since a
hand-run Quickstart and every sibling build take it too.

So at Q3, list the sandboxes and compare against the name this build will take.
A holder no `_builds/*/sandbox` record names, or one a *sibling* build recorded,
is the case to **ask about before the deploy** rather than replace — say which
name is held and whose sessions a yes discards. Take a yes, or its owner's own
[Teardown](#teardown); a second name on the same gateway is also on offer here,
since nothing but the name collides. Only the name this build's own record
already holds is replaced without asking.

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
| `HARNESS_PROVIDER` | `agent-inference` | the profile below that authorizes `HARNESS_BASE_URL`'s host, and the name of the provider created from it |
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

### Where the user gets each value

What to tell a user who accepts the default endpoint but does not have a key.

| Value | Where it comes from |
|---|---|
| A key for the default endpoint | Create one at <https://inference.nvidia.com/key-management?action=new-key>; the model ids the Hub serves are listed at <https://inference.nvidia.com/?new=0>. Its gateway answers from NVIDIA infrastructure, so a host outside that network takes one of the rows below — settle that before the sandbox is created, not after the first agent turn fails |
| Another gateway or router the user runs | Whoever operates it, plus its own model list for the matching Opus id. Usually the endpoint their other tooling already points at, so ask about that first when the default does not apply. A router's route ids come from [`deploy_vss_switchyard.ipynb`](../../../deploy/docker/scripts/deploy_vss_switchyard.ipynb), and it forwards the caller's credential upstream, so the key stays the **upstream** provider's |
| A provider's public API key | That provider's own documentation, for both the key and the model ids it serves. Point the user there rather than describing a key format or a console layout this skill does not own |
| No key at all | The build's own LLM NIM through profile (b) on a build that resolved `LLM_MODE=local` or `local_shared`; or (c) with an `nvapi-…` key from <https://build.nvidia.com>, which replaces this route and serves Nemotron rather than Opus |

Two cases need no question at all. **A provider explicitly configured in the
environment with all of its required values is the answer**: values exported
by the caller, by CI, or by a platform secret store win over anything Q3a
collects, so read them first and confirm what was found instead of re-asking.
And in **autonomous mode**
([`SKILL.md`](../SKILL.md#exception--autonomous-mode)) the caller's instruction
plus that environment answer Q3a; a token missing from both is still a blocker
to report, never grounds to substitute a provider.

**Reusing the build's own LLM NIM** is the cheapest local harness and the least
obvious route, so it is spelled out. It applies only to a build whose `LLM_MODE`
resolved to `local` or `local_shared`; one resolved to a remote LLM has no local
NIM to point at. Take profile (b) with a `host.openshell.internal:<LLM_PORT>`
entry added, set `HARNESS_BASE_URL=http://host.openshell.internal:<LLM_PORT>/v1`
and `HARNESS_MODEL` to the NIM's `NIM_SERVED_MODEL_NAME` from `resolved.yml`
— never an assumed slug. Say what the route costs **before** taking it: the
harness adds no GPU but shares one NIM instance with the build's own LLM work,
at `NIM_KVCACHE_PERCENT=0.3` and `NIM_MAX_MODEL_LEN=65536` on the shared-GPU
profiles, so on a build whose capabilities drive that NIM — `lvs` summarization,
`search` critique — agent turns and the build's own requests contend for KV
cache. [Ordering](#ordering) matters more here too: the NIM is the slowest
service in the build on a cold cache and has to answer before the first turn.

**No API key available for a provider that needs one is a blocker.** Report it
and stop. Never silently substitute (c) build.nvidia.com or a local model: the
harness would come up on a different LLM than the one reported, and nothing
downstream could tell. Switching providers is the user's call to make on that
report, not a fallback to take for them.

## Bring-up

Do not reimplement any of this.
[`services/openshell/README.md`](../../../services/openshell/README.md#3-quickstart) §3 is the
single source of host-side harness logic — CLI install, gateway, policy, image,
provider, sandbox, skill activation — and §4 is the same steps with the
reasoning. From the checkout root, run the Quickstart's steps 0–3, then
[Optional — add VSS capabilities](../../../services/openshell/README.md#optional--add-vss-capabilities)
steps 1–3 in place of the Quickstart's step 4, so the one sandbox is created on
the VSS image and policy. This section says only what a *build* changes about
them.

The commands source `openshell.env`, whose values replace any of the same name
exported in the shell, then `harness.env` when it exists. Source them first,
then apply this build's values over them:

| Value | For a build | Why |
|---|---|---|
| `VSS_PUBLIC_URL` | `http://host.openshell.internal:7777` — the `openshell.env` default, unchanged for a Compose build | the origin as **the sandbox** sees it, not `HOST_IP`. Set it only for a Kubernetes deployment, to that cluster's Ingress origin, which then also needs a policy entry |
| `HARNESS_BASE_URL`, `HARNESS_MODEL`, `HARNESS_PROVIDER` | the Q3a answers | the model values and the profile that authorizes them; a model change is a re-create, not a rebuild |
| `HARNESS_API_KEY` | from the environment or the secret store | the selected provider's credential, unquoted. Never into `openshell.env`, `override.env`, a transcript, or a summary |
| sandbox name | `vss-harness-hermes` unless this host already holds that name | in place of the README's `hermes` on `sandbox create`, `sandbox delete` and `sandbox exec`; settled at Q3, per [Sandbox names on a shared host](#sandbox-names-on-a-shared-host) |

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
VERSION="$(cd "$SRC/libs/vss/cli" && uvx --with hatch-vcs hatch version)"
docker build -f "$SRC/services/openshell/vss/Dockerfile" \
  --build-arg HARNESS_IMAGE="openshell-hermes:$HERMES_REF" \
  --build-arg VSS_PACKAGE_VERSION="$VERSION" \
  -t "openshell-hermes-vss:${VERSION//+/-}-from-source" "$SRC"
git -C "$REPO" worktree remove "$SRC"
```

Both the Dockerfile and the context come from the worktree, so the ref builds
its own image definition. A ref from before that file existed cannot be built
this way, and is a blocker to report. The version names the ref's own commit,
so two refs coexist as two images.
Quote the ref and the version `vss --version` reports inside the sandbox in the
summary.

The sandbox image ships only the **operation** skills — the ones whose `SKILL.md`
declares `vss-requires` — and `vss-hermes-sync` activates those the recorded
deployment can serve after `vss configure`. It does **not** receive this skill:
the sandbox operates the build it was given and is not expected to compose
further builds or manage the `_builds/` tree this run produced.

## Verification

The checks are in
[`services/openshell/README.md`](../../../services/openshell/README.md#5-verification) §5 —
prover, sandbox state and effective policy, the exposed route, an end-to-end
agent turn, and the VSS layer's two: the CLI and its grant. Run them, then
confirm the two things a build adds:

1. **The build's own origin answers from inside the sandbox.** Step 3 of the
   VSS capabilities section already runs `vss configure --base-url "$VSS_PUBLIC_URL"`; read
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
a later build reuses all three. Bring the gateway down only when no sandbox is
left on the host, and keep its `openshell-data` volume unless every sandbox
record and the sealed provider credentials are meant to go with it.

## Sources

- [`services/openshell/README.md`](../../../services/openshell/README.md) — the harness itself: architecture, Quickstart, steps, verification
- [`deploy/docker/services/openshell/`](../../../deploy/docker/services/openshell/), [`.hermes/`](../../../.hermes/), [`services/openshell/vss/`](../../../services/openshell/vss/), [`services/openshell/providers/`](../../../services/openshell/providers/)
- `deploy/docker/services/ui/compose.yml`
- `services/ui/DOCKER-README.md`
