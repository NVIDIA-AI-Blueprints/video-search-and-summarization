# Agent Harness

- [Model](#model)
- [`nemoclaw` is never a service key](#nemoclaw-is-never-a-service-key)
- [Replacing the in-stack agent](#replacing-the-in-stack-agent)
- [Ordering](#ordering)
- [Prerequisites](#prerequisites)
- [Default provider](#default-provider)
- [Provisioning](#provisioning)
- [Verification](#verification)
- [Teardown](#teardown)
- [Sources](#sources)

## Model

A **harness** is the agent runtime that drives a build. Two exist, they are
**mutually exclusive**, and **at most one agent runtime** is deployed.
`vss-agent` is removed unless the request names it, so a build carries the
NemoClaw sandbox, the in-stack agent, or no harness at all — never two agent
runtimes. A single external runtime may still have two presentation surfaces:
its own Agent UI and VSS UI through `agent-gateway`.

| Harness | Where it runs | Reached by | Selected by |
|---|---|---|---|
| `nemoclaw` *(default)* | a sandbox on the host, outside the Compose project | its Agent UI and VSS UI through `agent-gateway`, with the VSS skills installed into the sandbox | this file |
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
when the request explicitly names `vss-agent`, the legacy in-stack agent, or one
of its REST routes such as `/generate`; asking for the Web UI alone does not
select it. Such a request skips Q3 the way any named harness does. Honour it; do
not steer it to NemoClaw. Everything below about removing `vss-agent` therefore
applies to a `no` as much as to a `yes`; only the
[Prerequisites](#prerequisites), [ingress](#ingress-is-still-required), and
provisioning sections are NemoClaw's alone.

A build that reaches no interactive surface still has **no harness at all** — the
correct outcome for one that only ingests, indexes, or serves an API. Defaulting
to NemoClaw never means adding a harness to a headless build.

## `nemoclaw` is never a service key

`nemoclaw` is **not** a Compose service and **must not** enter
`COMPOSE_PROFILES`, appear in `compose.yml`, or receive a file under
`patches/`. It has no image in the root Compose graph and resolution would fail
on the invented key. Treat it exactly as `<name>` is treated in the artifact
contract: a label outside the Compose model.

Selecting NemoClaw never adds a `nemoclaw` key. When VSS UI is retained, it does
add the separate `agent-gateway` service key so that the UI can reach the
host-side runtime. Provision the host runtime before resolving that Compose
graph, because the protected gateway configuration contains its live endpoint
and credentials.

## Replacing the in-stack agent

Applies to **both** Q3 answers — a `yes` and a `no` alike — and not to a build
whose request named the in-stack agent.

**Remove `vss-agent` from the Foundation's `COMPOSE_PROFILES`.** Keep `vss-ui`,
`phoenix`, and the `llm_*` peer at this decision point; pruning them is a
capability decision, not a harness one. A `yes` adds `agent-gateway`; a `no`
adds no replacement service.

`vss-ui`'s dependencies on both possible agent services ship as
`required: false` so the filtered project resolves, and
`scripts/normalize_resolved_yml.py` drops dangling entries. Never re-add a hard
`depends_on` in a build override — Compose rejects a project whose enabled
service hard-depends on a filtered one, and Step 8 fails with no `resolved.yml`.

`vss-ui` is worth keeping with no agent: its Alerts, Dashboard, and Video
Management tabs address Alert Bridge, Kibana, and VST directly. An explicit
"headless" request drops it as well — honour that, and report the loss of those
three tabs.

### Surface behavior

Report the applicable behavior explicitly:

| Surface | NemoClaw (`yes`) | No harness (`no`) |
|---|---|---|
| VSS UI chat sidebar and Chat tab | functional through same-origin `/api/agent` → `agent-gateway` → the sandbox | unavailable because no agent runtime exists |
| VSS UI Search and Alerts *Generate Report* | functional through the same chat path; Search and incident skills publish versioned artifacts that update the owning tab | conversational actions are unavailable; direct incident list/rule CRUD remain |
| NemoClaw Agent UI | functional against the same sandbox and VSS skills | absent |
| Dashboard and Video Management | unchanged; they address Kibana and VST directly | unchanged |
| Legacy ingress `/api`, `/chat`, `/websocket` routes | unavailable because they specifically target `vss-agent`; `/api/agent` is the replacement UI route | unavailable |
| Summarization from the UI | available by asking the external harness to run `vss-summarize-video` | unavailable from the UI; use `vss summarize` on the host |
| `vss-generate-video-report-rag` | unavailable because it drives the old agent API; use `vss-generate-video-report` through the harness | unavailable; use `vss-generate-video-report` from the host |

No `vss` CLI command group requires `vss-agent`: `configure` probes each route
independently, while `summarize`, `search`, `vlm`, `vios`, and `memory` address
their service owners directly. The external harness executes those same skills
and commands; `agent-gateway` only transports and normalizes its run events.

### Source lifecycle without `vss-agent`

Use the direct service-owner recipe in `vss-manage-video-io-storage`
[`provision-vios-source.md`](../../vss-manage-video-io-storage/references/provision-vios-source.md)
to fan a source into RT-CV and RT-Embed. On a yes, the external harness can run
that skill from either chat surface. On a no, run it from the host. Alert rules
stay with `vss-manage-alerts`, which addresses Alert Bridge directly.

### Ingress is still required

The sandbox reaches the build only over host-published HTTP, and the host-CLI
read path has no ingress-less form (see
[`deployment_resolution.md`](deployment_resolution.md) and
[`services/ingress.md`](services/ingress.md)). `vss-haproxy-ingress` must be in
the effective service set, carrying the operate route-set. A request that pairs
NemoClaw with "no ingress" is a **capability contradiction** — take it to the
clarification gate; do not resolve it by dropping either side.

Shipping the ingress is necessary but not sufficient. HAProxy 404s any `Host`
header outside its `known_host` allowlist. Keep the shipped
`HOST_INTERNAL_ALIAS=host.openshell.internal` value on the HAProxy service so
the sandbox's Compose origin is admitted; see
[`services/ingress.md`](services/ingress.md). Do not repoint `EXTERNAL_IP` to
the sandbox alias: Alert Bridge uses that value to rewrite clip URLs, and doing
so makes evidence links unusable outside the sandbox. A custom HAProxy file
must preserve the `HOST_INTERNAL_ALIAS` allowlist and main-host routing rules.

### Either answer makes it a delta build

Removing `vss-agent` is a capability delta, so a named profile that reaches Q3
is a **delta build** on a `no` as much as a `yes`; a yes also adds
`agent-gateway`. Create `_builds/<name>/` and follow Delta mode from Step 2.
Only a request that explicitly names the in-stack agent, and so never reaches
Q3, can stay a stock deploy.

### Cost to report, not to optimize away

Two things the user should hear up front rather than discover:

- **GPU and memory budget.** `vss-agent` reserves no GPU, so its removal frees
  memory rather than a device, and the `llm_*` peer stays resident. Budget the
  build against [`sizing.md`](sizing.md) plus the harness's own model provider —
  and note that a NemoClaw-managed local model claims every visible GPU unless
  pinned (see [Prerequisites](#prerequisites)).
- **One agent runtime, potentially two chat surfaces.** On a `yes`, both the
  sandbox Agent UI and VSS UI address the same NemoClaw runtime; report **both
  as markdown links** and name NemoClaw as the driver. On a `no`, report the
  build UI alone, state that its conversational controls have no backend, and
  name the `vss` CLI as the driver. The build's
  origin is `VSS_PUBLIC_HOST`; on Brev that is the FQDN the context file
  publishes for the ingress port, resolved rather than constructed
  ([`brev.md`](brev.md)). Never use `EXTERNAL_IP` as the sandbox origin; it is
  part of public/evidence URL rewriting, while the sandbox uses
  `HOST_INTERNAL_ALIAS`.

## Ordering

For a gateway-enabled Compose build, the planned origin is known from the
selected host and HAProxy port before containers start. The gateway env cannot
be created afterward: it contains the live harness endpoint, credentials, and
commit-bound capability receipt consumed by `docker compose config`. Use this
order:

| # | Step | Why here |
|---|---|---|
| 1 | Write `override.env` and `compose.yml`; determine the planned `http://host.openshell.internal:<HAPROXY_HOST_PORT>` origin | the sandbox needs a stable target even though it is not live yet |
| 2 | Create the dedicated sandbox with `deploy_nemoclaw.ipynb`, or select the existing BYO sandbox | the harness API and operator credential must exist first |
| 3 | Run `attach_vss_agent.py` with the planned origin and protected output paths | binds the exact source commit, installs the full capability plane, probes the harness API, and writes `agent-capabilities.json` plus `agent-gateway.env` |
| 4 | Generate and validate `resolved.yml`, with `agent-gateway.env` last | bakes the verified endpoint and credentials into the standalone model without exposing them at `up` time |
| 5 | Deploy and pass the Compose readiness gate | proves the VSS services and gateway are live |
| 6 | Run the sandbox-to-VSS and VSS-UI-to-sandbox end-to-end checks | distinguishes transport health from actual skill and artifact behavior |

For harness-only attachment to a running deployment, its origin is already
known and Step 1 reduces to resolving that origin. Connecting VSS UI still
requires `agent-gateway` to be present in the deployed graph; attachment alone
cannot retrofit a missing service.

## Prerequisites

Beyond everything in [`prerequisites.md`](prerequisites.md) and
[`credentials.md`](credentials.md), the harness step needs the following.

**Check them at Q3, not during provisioning.** NemoClaw is the default, so a build can
reach these requirements without anyone having asked for them. Any one missing is
a **blocker at harness selection**: name it, ask whether to supply it, proceed
with no harness, or name the in-stack agent instead, and deploy nothing until
that is answered. Discovering it after artifacts are resolved would leave a
gateway graph bound to a harness that was never made ready.

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
| (a) self-hosted endpoint, or one on a private address | `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_MODEL` | a key — 3.1 sends the `EMPTY` placeholder for a blank one and the server ignores it |
| (b) NemoClaw-managed local model | `NEMOCLAW_PROVIDER` (`install-vllm`, `ollama`, `nim-local`, …) | any API key; `HF_TOKEN` only for a gated `install-vllm` model |
| (c) build.nvidia.com hosted model | `NVIDIA_API_KEY` | `COMPATIBLE_API_KEY`, `NEMOCLAW_ENDPOINT_URL` |

The notebook's shipped policy covers the standard Compose HAProxy origin on
port `7777`. The required `attach_vss_agent.py` pass adds the exact planned
origin, including a non-default `HAPROXY_HOST_PORT`, through its generated
`vss-agent-origin` policy. Do not hand-edit the shared policy per build. A
post-deployment `CONNECT tunnel failed, response 403` means the attachment did
not install the exact origin that `resolved.yml` published.

## Default provider

**Default to notebook option (a) — the remote OpenAI-compatible endpoint —
serving Claude Opus.** Do not ask which provider to use, and do not fall through
to the notebook's own (c) build.nvidia.com path:

| Variable | Default | Note |
|---|---|---|
| `NEMOCLAW_PROVIDER` | `custom` | option (a); the only provider that consumes `NEMOCLAW_ENDPOINT_URL` |
| `NEMOCLAW_MODEL` | `claude-opus-4-8` | selected default; override for a different Opus revision, or for a router **route id** when the endpoint is a model router |
| `NEMOCLAW_ENDPOINT_URL` | `https://inference-api.nvidia.com/v1` | any OpenAI-compatible base URL; point it at an internal gateway or router instead when one is in use |
| `COMPATIBLE_API_KEY` | **no default** | a real bearer token is required for a public endpoint. Take it from the environment or the platform secret store — never a literal in a command, a file, or skill output |

Override the default **only on an explicit request** for a local or different
model. "Use a local model", "air-gapped", "use Nemotron", or a named endpoint of
their own each move the build to (b) or (c) per section 1.2 — the choice is the
user's, so carry it through rather than reasoning about which is better.

Two failure modes to handle rather than paper over:

- **No API key available for a provider that needs one.** Report it as a blocker
  and stop. Never silently substitute (c) build.nvidia.com or a local model: the
  harness would come up on a different LLM than the one reported, and nothing
  downstream could tell. Switching providers is the user's call to make on that
  report, not a fallback to take for them.
- **A private or self-hosted endpoint.** Section 3.1 picks the transport from the
  endpoint's address, not from the provider name, and a host resolving to a
  private address gets the bundled proxy with a blank key sent as `EMPTY`. A
  loopback-only server must be rebound to `0.0.0.0`, because the sandbox reaches
  this host as `host.openshell.internal`.

These four variables are exactly the parameter contract
`run_setup_notebook.py` already carries for this notebook, so exporting them is
sufficient — the injection re-reads them after the section 1.2 cells have run,
which is what lets the export win over whichever provider cell executed last.

## Provisioning

Do not reimplement any of this. `deploy_nemoclaw.ipynb` is the single source of
host-side harness logic — Docker pinning, sandbox onboarding, policy, skill
install, workspace docs, webhooks, UI link — and
`deploy/docker/scripts/run_setup_notebook.py` executes it non-interactively.

For a new dedicated sandbox, set the environment and run the notebook before
Compose resolution:

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
set -o pipefail   # report the notebook's status, not `tee`'s

REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<build-name>"

export VSS_REPO_DIR="$REPO"
export NEMOCLAW_SANDBOX_NAME="<build-name>"
export NEMOCLAW_RECREATE_SANDBOX=0
export AGENT_RUNTIME="${AGENT_RUNTIME:-openclaw}"
mkdir -p "$BUILD_DIR"

# Default harness LLM: notebook option (a), remote Claude Opus.
export NEMOCLAW_PROVIDER=custom
export NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-claude-opus-4-8}"
export NEMOCLAW_ENDPOINT_URL="${NEMOCLAW_ENDPOINT_URL:-https://inference-api.nvidia.com/v1}"
# From the environment or the secret store; never a literal here.
: "${COMPATIBLE_API_KEY:?bearer token for NEMOCLAW_ENDPOINT_URL is required}"
export COMPATIBLE_API_KEY

if ! uv run --isolated --no-project --python 3.12 \
    --with nbformat --with nbclient --with ipykernel -- \
    python "$REPO/deploy/docker/scripts/run_setup_notebook.py" \
      --notebook "$REPO/deploy/docker/scripts/deploy_nemoclaw.ipynb" \
      --require-output "Sandbox '${NEMOCLAW_SANDBOX_NAME}' ready." \
    2>&1 | tee "$BUILD_DIR/nemoclaw-setup.log"; then
  echo "NemoClaw provisioning failed; see $BUILD_DIR/nemoclaw-setup.log" >&2
  exit 1
fi

# Bind the dedicated sandbox to this build's exact planned Compose origin and
# generate the protected resolution overlay. Use the same command directly,
# without the notebook above, for an existing BYO sandbox.
python3 "$REPO/deploy/docker/scripts/attach_vss_agent.py" \
  --runtime "${AGENT_RUNTIME:-openclaw}" \
  --sandbox "$NEMOCLAW_SANDBOX_NAME" \
  --vss-origin "http://host.openshell.internal:<planned-haproxy-port>" \
  --receipt-output "$BUILD_DIR/agent-capabilities.json" \
  --gateway-env-output "$BUILD_DIR/agent-gateway.env"
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

From the notebook pair, run **only** `deploy_nemoclaw.ipynb`, followed by the
checked-in attachment script above. Its companion,
`deploy_vss_orchestrator.ipynb`, exists so the sandbox can deploy and manage VSS
itself; this skill owns that lifecycle. Add it as a second `--notebook` only
when the user explicitly wants the agent to own deployment too. That companion
already generates its own gateway-enabled graph, so do not also resolve and
deploy the build artifacts through the normal Steps.

Because the notebook installs every `SKILL.md` under `skills/`, the sandbox receives
this skill as well, and can compose further builds from chat. It operates the
build it was given; it is not expected to manage the `_builds/` tree this run
produced.

## Verification

The notebook runs with errors fatal, and the attachment then verifies the live
harness API and writes the commit-bound receipt and gateway overlay. After the
Compose readiness gate, confirm what those pre-deployment checks cannot cover:

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
2. **The sandbox can reach the build.** From the sandbox, make one call against
   the origin recorded in its capability receipt and runtime environment. A
   `403 CONNECT tunnel failed` is the egress
   policy (see Prerequisites), not a deployment fault — the distinction matters
   because the two have opposite fixes.
3. **VSS UI reaches the same sandbox.** Send one harmless turn through the VSS
   UI, confirm an intentional tool call appears under Intermediate Steps, then
   run a non-destructive Search or Alerts skill and confirm its structured
   artifact updates the owning UI surface.

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
