# Agent Harness

- [Model](#model)
- [`nemoclaw` is never a service key](#nemoclaw-is-never-a-service-key)
- [What removing the agent implies](#what-removing-the-agent-implies)
- [Ordering](#ordering)
- [Prerequisites](#prerequisites)
- [Default provider](#default-provider)
- [Bring-up](#bring-up)
- [Verification](#verification)
- [Teardown](#teardown)
- [Sources](#sources)

## Model

A **harness** is what a person or another agent talks to in order to drive a
build. Two exist, they are **mutually exclusive**, and **at most one** is
deployed. `vss-agent` is removed unless the request names it, so a build carries
the NemoClaw sandbox, the in-stack agent, or no harness at all — never two.

| Harness | Where it runs | Reached by | Selected by |
|---|---|---|---|
| `nemoclaw` *(default)* | a sandbox on the host, outside the Compose project | its chat UI, with the VSS skills installed into it | this file |
| `vss-agent` | inside the Compose project | the agent REST API (`/generate`), Web UI | the Agent owner ([`services/agent.md`](services/agent.md)), like any other capability |

`vss-agent` is in-stack: it is a container, it is reached through the build's own
origin, and forward closure retains it whenever agentic orchestration is
requested or another owner declares it as a peer — unless NemoClaw is selected,
which removes it. NemoClaw is host-side: an OpenShell sandbox running an agent
harness (OpenClaw or Hermes) with the repository's skills installed, driving the
deployment from outside over the same public routes an operator would use.

**NemoClaw is the default harness.** When a build has an interactive surface and
the request names no harness, Q3 asks one yes/no question — deploy the NemoClaw
sandbox as the harness? — and **yes** is the default, including for an
unanswered Q3. A **no** means no harness: the build is driven by the `vss` CLI
from the host. Never turn that into a menu of harnesses; the only question is
whether NemoClaw is deployed.

**`vss-agent` is removed on either answer.** The in-stack agent is deployed only
when the request names it — the chat agent, the Web UI, the agent REST API — and
such a request skips Q3 the way any named harness does. Honour it; do not steer
it to NemoClaw. Everything below about the removal therefore applies to a `no`
as much as to a `yes`; only the [Prerequisites](#prerequisites),
[ingress](#ingress-is-still-required), and bring-up sections are NemoClaw's
alone.

A build that reaches no interactive surface still has **no harness at all** — the
correct outcome for one that only ingests, indexes, or serves an API. Defaulting
to NemoClaw never means adding a harness to a headless build.

## `nemoclaw` is never a service key

`nemoclaw` is **not** a Compose service and **must not** enter
`COMPOSE_PROFILES`, appear in `compose.yml`, or receive a file under
`patches/`. It has no image in the root Compose graph and resolution would fail
on the invented key. Treat it exactly as `<name>` is treated in the artifact
contract: a label outside the Compose model.

Selecting NemoClaw therefore never *adds* a key. All it adds is a host-side step
after deployment (below).

## What removing the agent implies

Applies to **both** Q3 answers — a `yes` and a `no` alike — and not to a build
whose request named the in-stack agent.

**Remove `vss-agent` from the Foundation's `COMPOSE_PROFILES`, and change no
other key.** `vss-ui`, `phoenix`, and the `llm_*` peer all stay. Pruning them is
a capability decision, not a harness one: leave them and report `phoenix` as
idle, since it collects the agent's traces and has no other client.

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
| Web UI chat sidebar, Chat tab, Search tab | stop answering — they address `/chat/stream`, `/websocket`, and `/api/v1/search`. The Alerts, Dashboard, and Video Management tabs keep working, because they address Alert Bridge, Kibana, and VST directly — including Video Management's upload and delete, which never went through the agent |
| Alerts tab, *Generate Report* | goes with the sidebar it drives. The incident list and rule CRUD stay, on `video-analytics-api` and Alert Bridge |
| Web UI summarization on `lvs` | gone: the UI ships no LVS client and reaches summarization only through the agent's chat. On a build with no harness, the capability is `vss summarize` from the host and the UI is a dashboard |
| Ingress `/api`, `/chat`, `/websocket` | `503`. HAProxy still starts — `bk_vss_agent` is declared `init-addr none` — and the origin's root still serves the UI |
| Search **ingestion and deletion** | no `vss` verb covers the RT-CV/RT-Embed fan-out the agent's `/complete` performs. Use the headless recipe below |
| `vss-generate-video-report-rag` | unavailable: it drives the agent's `/v1/chat` and `/executions`. Route reports through `vss-generate-video-report`, which never calls the agent |

Nothing else in the operate set needs it. No `vss` command group declares the
agent a requirement: `configure` probes each route independently and simply omits
`/api`, while `summarize`, `search` (query), `vlm`, `vios`, and `memory` address
LVS, Elasticsearch, RT-Embed, RT-VLM, and VIOS directly. `vss-ui` holds the only
`depends_on` in the whole graph, and it is optional; no other service in any
profile calls the agent, so alerting, analytics, ingest, and summarization are
unaffected.

### Provisioning moves to the headless path

With no agent route, source provisioning is the direct-REST recipe:
`vss-manage-video-io-storage`
[`provision-vios-source.md`](../../vss-manage-video-io-storage/references/provision-vios-source.md).
Its own gate — stop when an agent route answers — passes on any build with the
agent removed, and it is the only path that fans a source into RT-CV and
RT-Embed. Alert rules stay with `vss-manage-alerts`, which addresses Alert Bridge.

### Ingress is still required

The sandbox reaches the build only over host-published HTTP, and the host-CLI
read path has no ingress-less form (see
[`deployment_resolution.md`](deployment_resolution.md) and
[`services/ingress.md`](services/ingress.md)). `vss-haproxy-ingress` must be in
the effective service set, carrying the operate route-set. A request that pairs
NemoClaw with "no ingress" is a **capability contradiction** — take it to the
clarification gate; do not resolve it by dropping either side.

Shipping the ingress is necessary but not sufficient. HAProxy 404s any `Host`
header outside its `known_host` allowlist, which admits only `VSS_PUBLIC_HOST`,
`EXTERNAL_IP`, `HOST_IP`, and localhost ([`services/ingress.md`](services/ingress.md)).
The sandbox reaches the host as `host.openshell.internal`, so every Compose
NemoClaw build 404s on its own origin until that name holds one of those slots.
Set `EXTERNAL_IP=host.openshell.internal` in `_builds/<name>/override.env` before
resolving, and comment the line. `EXTERNAL_IP` is the slot to spend: `HOST_IP`
must stay bridge-reachable, and the URLs users follow resolve from
`VSS_PUBLIC_HOST`. Do not ask the user — it is a mechanical consequence of
choosing NemoClaw.

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
  memory rather than a device, and the `llm_*` peer stays resident. Budget the
  build against [`sizing.md`](sizing.md) plus the harness's own model provider —
  and note that a NemoClaw-managed local model claims every visible GPU unless
  pinned (see [Prerequisites](#prerequisites)).
- **At most one chat surface, plus a dashboard.** The build's Web UI remains
  either way, with its chat and Search tabs dead and the rest live. On a `yes`
  the sandbox chat UI is the only conversational surface: report **both as
  markdown links** — the build's browse origin next to the Agent UI — and name
  NemoClaw as the driver. On a `no` report the browse origin alone, and name the
  `vss` CLI as the driver. The build's
  origin is `VSS_PUBLIC_HOST`; on Brev that is the FQDN the context file
  publishes for the ingress port, resolved rather than constructed
  ([`brev.md`](brev.md)). Never `EXTERNAL_IP`, which on a NemoClaw build holds
  `host.openshell.internal` and resolves only inside the sandbox.

## Ordering

The harness needs the build's origin, and that origin does not exist until the
deployment answers. Run the steps in this order and no other:

| # | Step | Why here |
|---|---|---|
| 1 | Deploy `resolved.yml` | nothing to point a harness at yet |
| 2 | Readiness gate ([`readiness.md`](readiness.md)) | a harness pointed at a half-warm stack reports failures that are not its own |
| 3 | Resolve the origin | `http://$HOST_IP:$HAPROXY_HOST_PORT` from the deployed build |
| 4 | Bring up the harness | consumes that origin |

Skipping the readiness gate is the common failure: onboarding succeeds, the
first call from the sandbox fails, and the cause looks like the harness.

## Prerequisites

Beyond everything in [`prerequisites.md`](prerequisites.md) and
[`credentials.md`](credentials.md), the harness step needs the following.

**Check them at Q3, not at bring-up.** NemoClaw is the default, so a build can
reach these requirements without anyone having asked for them. Any one missing is
a **blocker at harness selection**: name it, ask whether to supply it, proceed
with no harness, or name the in-stack agent instead, and deploy nothing until
that is answered. Discovering it after the readiness gate means a deployed build
with no way to drive it.

- **Python 3.11+ to run the notebook**, plus `docker`, `python3`, and `curl` on
  `PATH`, and outbound reach to the installer. **Do not require the NemoClaw CLI
  here**: section 3.1 installs it at the pinned `NEMOCLAW_INSTALL_REF` whenever
  that ref is not already present, so a fresh host is a supported starting point
  and preflighting the post-install CLI would reject one. The notebook's own
  preflight (section 2) re-checks the host commands, but it runs too late to
  inform the harness choice.
- **An agent model provider**, and only the credential that provider needs. This
  is the harness's *own* LLM, unrelated to the build's `LLM_*` and `VLM_*` knobs.
  The notebook offers three — (a) an OpenAI-compatible endpoint, (b) a
  NemoClaw-managed local model, (c) a build.nvidia.com hosted model — and **this
  skill defaults to (a) with a remote Claude Opus** (see [Default
  provider](#default-provider) below). Section 1.2 of the notebook remains the
  authority on which variables each provider needs; do not infer them, and do not
  preflight a variable a different provider would have used.
- **A GPU budget that accounts for the harness.** The default remote provider
  costs no GPU. This applies only when the user overrides to the local
  provider: (b)
  `install-vllm` takes every visible GPU unless `NEMOCLAW_VLLM_GPU_DEVICE` pins
  it, which will strand the build's own models. Reconcile that against
  [`sizing.md`](sizing.md) before choosing it, not after.
- **The checkout's own assets**: `assets/vss_nemoclaw_policy.yaml`, `skills/`,
  and `.openclaw/workspace/`. The notebook resolves all three from
  `VSS_REPO_DIR`.

Preflight the selected provider's row below and no other — a credential check
that fires for every build rejects the supported paths that need no key:

| Provider | Required at Q3 | Not required |
|---|---|---|
| (a) public OpenAI-compatible endpoint — the skill default | `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_MODEL`, `COMPATIBLE_API_KEY` | `NVIDIA_API_KEY` |
| (a) self-hosted endpoint, or one on a private address — including the build's own LLM NIM | `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_MODEL`, `COMPATIBLE_API_KEY=EMPTY`, `NEMOCLAW_INFERENCE_PROXY=0` | a real bearer token — the server ignores the value |
| (b) NemoClaw-managed local model | `NEMOCLAW_PROVIDER` (`install-vllm`, `ollama`, `nim-local`, …) | any API key; `HF_TOKEN` only for a gated `install-vllm` model |
| (c) build.nvidia.com hosted model | `NVIDIA_API_KEY` | `COMPATIBLE_API_KEY`, `NEMOCLAW_ENDPOINT_URL` |

`NEMOCLAW_INFERENCE_PROXY` follows the upstream's transport rather than the row
above. Leave the default `1` when the endpoint is HTTPS on 443: it is inert for a
public address, and it is what rescues a public name that corp or DGX DNS
resolves to a private one — the case the proxy was built for. Set `0` when the
upstream is plain HTTP or on another port, which the proxy cannot represent.

Egress from the sandbox to the build is already allowed: the shipped policy's
`vss-backend` entries cover the HAProxy origin (`7777`) along with each
backend's own host port. **A build that moves `HAPROXY_HOST_PORT` off `7777`
has no matching policy entry**, so every call from the sandbox returns
`CONNECT tunnel failed, response 403`. Report that as a blocker naming the port;
the policy is a checked-in asset, not something to rewrite per build.

The LLM NIM's published port is in the same position. `vss-backend` allowlists
literal `host` + `port` pairs and interpolates nothing, and neither NIM port is
among them — `30081`, which `LLM_PORT` resolves to, nor `30082` for a VLM NIM.
A harness pointed at the build's NIM therefore relies on NemoClaw authorizing
the inference endpoint it was onboarded with. A `403 CONNECT tunnel failed` on
the harness's first turn is that missing entry rather than a deployment fault —
report it naming the port, and do not fall back to a remote provider to get a
working chat.

## Default provider

**Default to notebook option (a) — the remote OpenAI-compatible endpoint —
serving Claude Opus.** Do not ask which provider to use, and do not fall through
to the notebook's own (c) build.nvidia.com path:

| Variable | Default | Note |
|---|---|---|
| `NEMOCLAW_PROVIDER` | `custom` | option (a); the only provider that consumes `NEMOCLAW_ENDPOINT_URL` |
| `NEMOCLAW_MODEL` | `claude-opus-4-8` | matches the notebook's own (a) example; override for a different Opus revision, or for a router **route id** when the endpoint is a model router |
| `NEMOCLAW_ENDPOINT_URL` | `https://inference-api.nvidia.com/v1` | any OpenAI-compatible base URL; point it at an internal gateway or router instead when one is in use |
| `COMPATIBLE_API_KEY` | **no default** | a real bearer token is required for a public endpoint. Take it from the environment or the platform secret store — never a literal in a command, a file, or skill output |

Override the default **only on an explicit request** for a local or different
model. "Use a local model", "air-gapped", "use Nemotron", or a named endpoint of
their own each move the build off the remote default — the choice is the user's,
so carry it through rather than reasoning about which is better. A local request
has **two** destinations, not one:

| Request | Route | Where the model runs |
|---|---|---|
| "reuse the deployment's LLM", "one model for both", "point it at the LLM NIM" | (a) `custom` against the build's own NIM | the build's `llm_local*` container — no additional GPU |
| "use a local model", "air-gapped", with no server named | (b) a NemoClaw-managed server (`install-vllm`, `ollama`, `nim-local`) | a second server NemoClaw starts and owns, on its own GPU |
| a named endpoint of their own | (a) `custom` against that endpoint | wherever they run it |

**(a) against the build's own LLM NIM** is the cheapest local harness and the
least obvious route, so it is spelled out here. It applies only to a build whose
`LLM_MODE` resolved to `local` or `local_shared`; one resolved to a remote LLM
has no local NIM to point at.

| Variable | Value |
|---|---|
| `NEMOCLAW_PROVIDER` | `custom` |
| `NEMOCLAW_ENDPOINT_URL` | `http://host.openshell.internal:<LLM_PORT>/v1`, with `LLM_PORT` read from the build's `resolved.yml` (`30081` on every current profile). Use that hostname rather than `HOST_IP` — the egress policy's entries are keyed on it |
| `NEMOCLAW_MODEL` | the NIM's `NIM_SERVED_MODEL_NAME` from `resolved.yml`, e.g. `nvidia/nemotron-3.5-lightning-30b-a3b`; never assume the slug |
| `COMPATIBLE_API_KEY` | `EMPTY` — set it explicitly, per the self-hosted failure mode below |
| `NEMOCLAW_INFERENCE_PROXY` | `0` — required, per that same failure mode |

Say what the route costs **before** taking it, so the user can pick (b) or the
remote default instead: the harness adds no GPU but shares one NIM instance with
the build's own LLM work, at `NIM_KVCACHE_PERCENT=0.3` and
`NIM_MAX_MODEL_LEN=65536` on the shared-GPU profiles. On a build whose
capabilities drive that NIM — `lvs` summarization, `search` critique — agent
turns and the build's own requests contend for KV cache.

[Ordering](#ordering) already covers the timing, and it matters more here: the
NIM is the slowest service in the build on a cold cache, and it has to answer on
that port before the notebook runs. Onboard is the only step that applies the
endpoint, so changing it afterwards needs the sandbox recreated.

Two failure modes to handle rather than paper over:

- **No API key available for a provider that needs one.** Report it as a blocker
  and stop. Never silently substitute (c) build.nvidia.com or a local model: the
  harness would come up on a different LLM than the one reported, and nothing
  downstream could tell. Switching providers is the user's call to make on that
  report, not a fallback to take for them.
- **A private or self-hosted endpoint.** Section 3.1 picks the transport from the
  endpoint's address, not from the provider name: a host resolving to a private
  address gets the bundled proxy, and a blank key is sent as `EMPTY`. That proxy
  reaches its upstream as `https://<host>` on 443 and drops the port, so it is
  **wrong for an endpoint served over plain HTTP or on another port** — a local
  NIM, vLLM, or Ollama. Export `NEMOCLAW_INFERENCE_PROXY=0` for those, and pass
  `COMPATIBLE_API_KEY=EMPTY` yourself, since the placeholder is applied only on
  the branch that export turns off. Disabling the proxy leaves NemoClaw's own
  SSRF guard to accept the endpoint; an onboard that rejects it is the case the
  proxy exists for — report it rather than re-aiming the harness at another
  provider. A loopback-only server must be rebound to `0.0.0.0`, because the
  sandbox reaches this host as `host.openshell.internal`.

These four variables are exactly the parameter contract
`run_setup_notebook.py` already carries for this notebook, so exporting them is
sufficient — the injection re-reads them after the section 1.2 cells have run,
which is what lets the export win over whichever provider cell executed last.
`NEMOCLAW_INFERENCE_PROXY` is not in that table and must not be added to it:
the injection assigns the raw environment string and section 3.1 accepts only a
real bool. Section 1.3 reads it from the environment itself, so exporting `0`
works the same way.

## Bring-up

Do not reimplement any of this. `deploy_nemoclaw.ipynb` is the single source of
host-side harness logic — Docker pinning, sandbox onboarding, policy, skill
install, workspace docs, webhooks, UI link — and
`deploy/docker/scripts/run_setup_notebook.py` executes it non-interactively.

Set the environment, then run the notebook:

| Variable | Value for a build | Why |
|---|---|---|
| `VSS_REPO_DIR` | the checkout root | resolves the policy, skills, and workspace docs |
| `VSS_PUBLIC_URL` | **leave unset** for a Compose build | Kubernetes-only, and setting it breaks a Compose build — see [Leave `VSS_PUBLIC_URL` unset](#leave-vss_public_url-unset-on-a-compose-build) below |
| `NEMOCLAW_SANDBOX_NAME` | one name per build | the default is `demo`; a second build under the same name reuses the first build's sandbox |
| `NEMOCLAW_RECREATE_SANDBOX` | `0` | **the notebook default is `1`, which discards the sandbox and every agent session in it.** Pass `0` unless the user asked to rebuild the harness |
| `AGENT_RUNTIME` | `openclaw` (default) or `hermes` | selects the harness profile; a change needs a fresh onboard |
| `NEMOCLAW_PROVIDER`, `NEMOCLAW_MODEL`, `NEMOCLAW_ENDPOINT_URL`, `COMPATIBLE_API_KEY` | per [Default provider](#default-provider) | remote Claude Opus unless the user asked for local or another model. The block below spells out that remote route alone; every other route **replaces** these values rather than defaulting through them |
| `NEMOCLAW_INFERENCE_PROXY` | unset, or `0` against a local endpoint | `0` is required when (a) points at the build's own LLM NIM, or at any plain-HTTP server: the default rewrites such an endpoint to an `https` upstream on 443 |
| `ORCHESTRATOR_ENABLE_HTTPS` | `false` | leave at the default; the HTTPS MCP path is a separate opt-in |

```bash
set -o pipefail   # report the notebook's status, not `tee`'s

REPO="$(git rev-parse --show-toplevel)"

export VSS_REPO_DIR="$REPO"
export NEMOCLAW_SANDBOX_NAME="<build-name>"
export NEMOCLAW_RECREATE_SANDBOX=0

# Default harness LLM: notebook option (a), remote Claude Opus. The `:-` form
# lets an already-exported value win — right for a caller-supplied remote
# endpoint, and exactly why these four lines must not be copied as-is for the
# NIM route, where a stale remote endpoint and model would survive.
export NEMOCLAW_PROVIDER=custom
export NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-claude-opus-4-6}"
export NEMOCLAW_ENDPOINT_URL="${NEMOCLAW_ENDPOINT_URL:-https://api.anthropic.com/v1/}"
# From the environment or the secret store; never a literal here.
: "${COMPATIBLE_API_KEY:?bearer token for NEMOCLAW_ENDPOINT_URL is required}"
export COMPATIBLE_API_KEY

# Against the build's own LLM NIM, replace all four outright — plain
# assignment, and never an inherited bearer token:
#   export NEMOCLAW_ENDPOINT_URL="http://host.openshell.internal:<LLM_PORT>/v1"
#   export NEMOCLAW_MODEL="<NIM_SERVED_MODEL_NAME from resolved.yml>"
#   export COMPATIBLE_API_KEY=EMPTY
#   export NEMOCLAW_INFERENCE_PROXY=0

uv run --isolated --no-project --python 3.12 \
  --with nbformat --with nbclient --with ipykernel -- \
  python "$REPO/deploy/docker/scripts/run_setup_notebook.py" \
    --notebook "$REPO/deploy/docker/scripts/deploy_nemoclaw.ipynb" \
    --require-output "Sandbox '${NEMOCLAW_SANDBOX_NAME}' ready." \
  2>&1 | tee "$REPO/_builds/${NEMOCLAW_SANDBOX_NAME}/nemoclaw-setup.log"
```

**Take the status from the notebook, not from `tee`.** Keep `pipefail` set, or
read `${PIPESTATUS[0]}` on the line right after the pipeline. Non-zero is a
blocker: report it with the log path and stop, rather than going on to the UI
link.

**Keep the whole run.** `run_setup_notebook.py` does not persist cell outputs, so
`| tail`, `| head`, or a dropped stream loses section 3.7's `Agent UI:` line and
the `WARNING:` that cell prints — without failing — when the dashboard forward
does not come up. A clean exit does not mean the link is usable. Read the link
from the `tee`d log.

Do not reconstruct the URL from the notebook source. Its origin branches on
whether the Brev context file publishes a secure link for the dashboard port.
Outside the notebook, resolve that FQDN from the context file
([`brev.md`](brev.md) → *Resolving a secure link*) rather than assembling a
hostname.

### Leave `VSS_PUBLIC_URL` unset on a Compose build

This skill deploys Docker Compose, and **`VSS_PUBLIC_URL` is a Kubernetes
setting**. Leaving it empty is not an omission — it is the value that means
"Compose". The sandbox's `ENV.md` already ships
`export HOST_IP=host.openshell.internal` for exactly this case, because a Compose
build publishes each service on a host port, and `vss-backend-readwrite`
allowlists those ports. Kubernetes publishes nothing on host ports, which is why
it alone needs an Ingress origin.

Setting it to the build's own origin does not merely add a redundant entry, it
**fails the harness step**. The notebook renders that value into the
`vss-k8s-ingress` policy entry, so `http://host.openshell.internal:7777`
duplicates the `host.openshell.internal:7777` that `vss-backend-readwrite`
already carries — with different metadata — and the gateway rejects the whole
policy update:

```text
network endpoint ambiguity validation failed: network policies
'vss-backend-readwrite' endpoint[8] (host.openshell.internal:7777) and
'vss-k8s-ingress' endpoint[0] (host.openshell.internal:7777) overlap on
port(s) 7777 with conflicting metadata
```

The sandbox is left onboarded but with **no VSS egress at all**, since the add is
atomic — the failure looks like a harness problem and is really this variable.
Set it only for a Kubernetes deployment, to that cluster's Ingress origin.

Run **only** `deploy_nemoclaw.ipynb`. Its companion,
`deploy_vss_orchestrator.ipynb`, exists so the sandbox can deploy and manage VSS
itself — work this skill has already done by the time the harness comes up.
Add it as a second `--notebook` only when the user explicitly wants the agent to
own the deployment lifecycle too.

Because the notebook installs every `SKILL.md` under `skills/`, the sandbox receives
this skill as well, and can compose further builds from chat. It operates the
build it was given; it is not expected to manage the `_builds/` tree this run
produced.

## Verification

The notebook runs with errors fatal and asserts each step itself, so a clean
exit already means onboarding, policy, skills, and workspace docs all landed.
Confirm the two things that exit code cannot cover:

1. **The harness is reachable.** Section 3.7 prints `Agent UI: <url>`. Put it in
   the final summary as a **markdown link** — `[Open the NemoClaw Agent UI](<url>)`
   — not as a bare URL in prose, so the user can click straight through to the
   harness they just deployed. The target is the printed URL character for
   character: do not shorten it, re-host it, or drop the fragment, because the
   OpenClaw URL carries the gateway token in `#token=` and a link without it
   lands on an unauthenticated page. On Brev the host is the secure-link FQDN. A
   `127.0.0.1` URL only resolves on the deployment host, so pair the link with
   the SSH tunnel the same section prints rather than offering it alone.

   Confirm the forward behind it is bound as that origin requires:

   ```bash
   openshell forward list   # BIND column for the dashboard port
   ```

   On Brev it must read `0.0.0.0`; a `127.0.0.1` bind answers a local health
   probe and still `503`s behind the secure link. On loopback, re-run the
   notebook and take the new link — do not publish the localhost URL instead.
2. **The sandbox can reach the build.** From the sandbox, one call against the
   origin recorded in `ENV.md`. A `403 CONNECT tunnel failed` is the egress
   policy (see Prerequisites), not a deployment fault — the distinction matters
   because the two have opposite fixes.

Section 3.8 is an optional deeper pass over the live sandbox, active policy
metadata, webhooks, and the installed workspace docs. Run it when onboarding
behaved unexpectedly.

## Teardown

The harness and the build are independent lifecycles. Tearing down one never
tears down the other, and [`teardown.md`](teardown.md) covers only the Compose
project.

```bash
nemoclaw "<build-name>" destroy --yes --cleanup-gateway
```

Destroy the sandbox **before** the Compose project when doing both, so the
harness is not left pointed at an origin that has stopped answering. Removing
the build's containers alone leaves a healthy sandbox with a dead origin, which
reports as a skill failure rather than a missing deployment.

## Sources

- `deploy/docker/scripts/deploy_nemoclaw.ipynb`
- `deploy/docker/scripts/run_setup_notebook.py`
- `deploy/docker/scripts/nemoclaw/README.md`
- `assets/vss_nemoclaw_policy.yaml`
- `.openclaw/workspace/` (and its `_nemoclaw` overlay)
