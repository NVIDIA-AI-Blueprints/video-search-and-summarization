# Agent Harness

- [Model](#model)
- [`nemoclaw` is never a service key](#nemoclaw-is-never-a-service-key)
- [What choosing NemoClaw implies](#what-choosing-nemoclaw-implies)
- [Ordering](#ordering)
- [Prerequisites](#prerequisites)
- [Default provider](#default-provider)
- [Bring-up](#bring-up)
- [Verification](#verification)
- [Teardown](#teardown)
- [Sources](#sources)

## Model

A **harness** is what a person or another agent talks to in order to drive a
build. Two are supported, and the choice is **orthogonal to the service set**: it
never adds, removes, or reconfigures a service, and never changes how the delta
is computed. The two are not mutually exclusive — a NemoClaw build keeps
`vss-agent` whenever the capabilities put it there.

| Harness | Where it runs | Reached by | Selected by |
|---|---|---|---|
| `vss-agent` | inside the Compose project | the agent REST API (`/generate`), Web UI | the Agent owner ([`services/agent.md`](services/agent.md)), like any other capability |
| `nemoclaw` | a sandbox on the host, outside the Compose project | its chat UI, with the VSS skills installed into it | this file |

`vss-agent` is in-stack: it is a container, it is reached through the build's own
origin, and forward closure retains it whenever agentic orchestration is
requested or another owner declares it as a peer. NemoClaw is host-side: an
OpenShell sandbox running an agent harness (OpenClaw or Hermes) with the
repository's skills installed, driving the deployment from outside over the same
public routes an operator would use.

NemoClaw is added only on an explicit request. Absent one, the harness follows
from the capabilities: a build that reaches the Agent owner has `vss-agent`, and
one that does not is headless with no harness at all — the correct outcome for a
build that only ingests, indexes, or serves an API.

## `nemoclaw` is never a service key

`nemoclaw` is **not** a Compose service and **must not** enter
`COMPOSE_PROFILES`, appear in `compose.yml`, or receive a file under
`patches/`. It has no image in the root Compose graph and resolution would fail
on the invented key. Treat it exactly as `<name>` is treated in the artifact
contract: a label outside the Compose model.

A request to "deploy NemoClaw" therefore changes nothing in the service set. All
it adds is a host-side step after deployment (below).

## What choosing NemoClaw implies

**NemoClaw changes no service key, and the agent tier is always present.** Those
two statements are compatible because **every Foundation already ships the agent
tier**: `vss-agent`, `vss-ui`, `phoenix`, and an `llm_*` peer are in the
authoritative `COMPOSE_PROFILES` of all four developer profiles
([`base.md`](profiles/base.md), [`alerts.md`](profiles/alerts.md),
[`lvs.md`](profiles/lvs.md), [`search.md`](profiles/search.md)). So "keep the
agent" never means adding a service — it means **a delta on a NemoClaw build
never prunes the agent tier**.

This is a deliberate choice of functionality over minimality. NemoClaw replaces
the agent as the **conversational layer** — the thing a person talks to — but
`vss-agent` is also a **capability provider** that the skills call as a backend,
and those two roles do not come apart cleanly. Keeping it means no capability
silently loses its backend when someone drives the build from a sandbox instead
of the Web UI, at the price of some redundancy on a query-only build.

**Never prune or hand-remove the agent tier on a NemoClaw build**, and do not
treat "nothing I requested reaches the Agent owner" as licence to. **A NemoClaw
build is never headless.** A request for both "headless" and NemoClaw is
therefore a contradiction, like NemoClaw with no ingress — take it to the
clarification gate instead of dropping either side.

### What the retained agent still provides

Redundant for a caller who only queries, and load-bearing for these:

| Capability | What breaks without the agent |
|---|---|
| Video summarization (`lvs-server`) | a declared `Required peer` ([`services/lvs.md`](services/lvs.md)) — the service will not work at all |
| Search **ingestion and deletion** | `vss-search-archive` mutates only through the agent lifecycle (`/api/v1/videos` + `/complete`) and forbids direct-REST mutation of Elasticsearch, RT-CV, RT-Embed, storage-ms, or VIOS |
| Alert-rule management | `vss-manage-alerts` drives agent-owned routes |
| Agent REST API, Web UI, tracing, agentic natural-language decomposition (`/api/v1/search`) | the Agent owner's own surfaces |

Search **querying** is deliberately absent: `vss configure` plus `vss search run`
read through Elasticsearch, RT-Embed, and RT-CV over the ingress, so that path
never needed the agent. Dense-caption Q&A (`vss-ask-video`) and report generation
(`vss-generate-video-report`) likewise never call the agent's `/generate`. Those
are the cases where the retained agent is the redundancy this contract accepts.

### Provisioning stays on the agent-owned path

Because the agent tier is present, source provisioning is **agent-owned**:
`vss-search-archive` for search ingestion, `vss-manage-alerts` for alert rules.

Do **not** use `vss-manage-video-io-storage`
[`provision-vios-source.md`](../../vss-manage-video-io-storage/references/provision-vios-source.md)
on a NemoClaw build. That recipe is headless-only and stops when it detects an
agent route, because a direct-REST fan-out would double-provision what the agent
already owns.

### Ingress is still required

The sandbox reaches the build only over host-published HTTP, and the host-CLI
read path has no ingress-less form (see
[`deployment_resolution.md`](deployment_resolution.md) and
[`services/ingress.md`](services/ingress.md)). `vss-haproxy-ingress` must be in
the effective service set, carrying the operate route-set. A request that pairs
NemoClaw with "no ingress" is a **capability contradiction** — take it to the
clarification gate; do not resolve it by dropping either side.

### Stock stays stock

Since the harness changes no service key, a named profile plus NemoClaw is a
**stock deploy** with the harness step appended — the profile's authoritative
service set, unchanged. Only an actual capability delta makes it a delta build,
and even then the agent tier survives the delta.

### Cost to report, not to optimize away

Two things follow from keeping the agent, and the user should hear both up front
rather than discover them:

- **GPU and memory budget.** The agent tier and the LLM peer it requires stay
  resident, on top of the harness's own model provider. Budget all of it against
  [`sizing.md`](sizing.md) — and note that a NemoClaw-managed local model claims
  every visible GPU unless pinned (see [Prerequisites](#prerequisites)).
- **Two conversational surfaces.** The build's own Web UI and the sandbox chat UI
  both answer, and they do not share session state. Name NemoClaw as the intended
  driver when reporting the deployment so nobody splits work across both and
  wonders why context is missing.

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
[`credentials.md`](credentials.md), the harness step needs:

- **Python 3.11+ on the host**, plus `docker`, `python3`, and `curl`. The
  notebook's own preflight (section 2) checks these and reports what is missing.
- **An agent model provider.** This is the harness's *own* LLM, unrelated to the
  build's `LLM_*` and `VLM_*` knobs. The notebook offers three — (a) an
  OpenAI-compatible endpoint, (b) a NemoClaw-managed local model, (c) a
  build.nvidia.com hosted model — and **this skill defaults to (a) with a remote
  Claude Opus** (see [Default provider](#default-provider) below). Section 1.2 of
  the notebook remains the authority on which variables each provider needs; do
  not infer them.
- **A GPU budget that accounts for the harness.** The default remote provider
  costs no GPU, which is what makes the retained agent tier affordable. This
  applies only when the user overrides to the local provider: (b)
  `install-vllm` takes every visible GPU unless `NEMOCLAW_VLLM_GPU_DEVICE` pins
  it, which will strand the build's own models. Reconcile that against
  [`sizing.md`](sizing.md) before choosing it, not after.
- **The checkout's own assets**: `assets/vss_nemoclaw_policy.yaml`, `skills/`,
  and `.openclaw/workspace/`. The notebook resolves all three from
  `VSS_REPO_DIR`.

Egress from the sandbox to the build is already allowed: the shipped policy's
`vss-backend` entries cover the HAProxy origin (`7777`) along with each
backend's own host port. **A build that moves `HAPROXY_HOST_PORT` off `7777`
has no matching policy entry**, so every call from the sandbox returns
`CONNECT tunnel failed, response 403`. Report that as a blocker naming the port;
the policy is a checked-in asset, not something to rewrite per build.

## Default provider

**Default to notebook option (a) — the remote OpenAI-compatible endpoint —
serving Claude Opus.** Do not ask which provider to use, and do not fall through
to the notebook's own (c) build.nvidia.com path:

| Variable | Default | Note |
|---|---|---|
| `NEMOCLAW_PROVIDER` | `custom` | option (a); the only provider that consumes `NEMOCLAW_ENDPOINT_URL` |
| `NEMOCLAW_MODEL` | `claude-opus-4-6` | matches the notebook's own (a) example; override for a different Opus revision, or for a router **route id** when the endpoint is a model router |
| `NEMOCLAW_ENDPOINT_URL` | `https://api.anthropic.com/v1/` | any OpenAI-compatible base URL; point it at an internal gateway or router instead when one is in use |
| `COMPATIBLE_API_KEY` | **no default** | a real bearer token is required for a public endpoint. Take it from the environment or the platform secret store — never a literal in a command, a file, or skill output |

Override the default **only on an explicit request** for a local or different
model. "Use a local model", "air-gapped", "use Nemotron", or a named endpoint of
their own each move the build to (b) or (c) per section 1.2 — the choice is the
user's, so carry it through rather than reasoning about which is better.

Two failure modes to handle rather than paper over:

- **No API key available.** Report it as a blocker and stop. Never silently
  substitute (c) build.nvidia.com or a local model: the harness would come up on
  a different LLM than the one reported, and nothing downstream could tell.
- **A private or self-hosted endpoint.** Section 3.1 picks the transport from the
  endpoint's address, not from the provider name, and a host resolving to a
  private address gets the bundled proxy with a blank key sent as `EMPTY`. A
  loopback-only server must be rebound to `0.0.0.0`, because the sandbox reaches
  this host as `host.openshell.internal`.

These four variables are exactly the parameter contract
`run_setup_notebook.py` already carries for this notebook, so exporting them is
sufficient — the injection re-reads them after the section 1.2 cells have run,
which is what lets the export win over whichever provider cell executed last.

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
| `NEMOCLAW_PROVIDER`, `NEMOCLAW_MODEL`, `NEMOCLAW_ENDPOINT_URL`, `COMPATIBLE_API_KEY` | per [Default provider](#default-provider) | remote Claude Opus unless the user asked for local or another model |
| `ORCHESTRATOR_ENABLE_HTTPS` | `false` | leave at the default; the HTTPS MCP path is a separate opt-in |

```bash
REPO="$(git rev-parse --show-toplevel)"

export VSS_REPO_DIR="$REPO"
export NEMOCLAW_SANDBOX_NAME="<build-name>"
export NEMOCLAW_RECREATE_SANDBOX=0

# Default harness LLM: notebook option (a), remote Claude Opus.
export NEMOCLAW_PROVIDER=custom
export NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-claude-opus-4-6}"
export NEMOCLAW_ENDPOINT_URL="${NEMOCLAW_ENDPOINT_URL:-https://api.anthropic.com/v1/}"
# From the environment or the secret store; never a literal here.
: "${COMPATIBLE_API_KEY:?bearer token for NEMOCLAW_ENDPOINT_URL is required}"
export COMPATIBLE_API_KEY

uv run --isolated --no-project --python 3.12 \
  --with nbformat --with nbclient --with ipykernel -- \
  python "$REPO/deploy/docker/scripts/run_setup_notebook.py" \
    --notebook "$REPO/deploy/docker/scripts/deploy_nemoclaw.ipynb" \
    --require-output "Sandbox '${NEMOCLAW_SANDBOX_NAME}' ready."
```

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

Because the notebook installs every `skills/*/SKILL.md`, the sandbox receives
this skill as well, and can compose further builds from chat. It operates the
build it was given; it is not expected to manage the `_builds/` tree this run
produced.

## Verification

The notebook runs with errors fatal and asserts each step itself, so a clean
exit already means onboarding, policy, skills, and workspace docs all landed.
Confirm the two things that exit code cannot cover:

1. **The harness is reachable.** Section 3.7 prints the Agent UI link. Report it
   verbatim — on Brev it is the secure-link FQDN, and a `127.0.0.1` URL needs the
   SSH tunnel the same section prints.
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
