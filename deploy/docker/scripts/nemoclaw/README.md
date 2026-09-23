# NemoClaw + VSS (canonical CLI flow)

VSS creates and configures its NemoClaw/OpenClaw sandbox using **only canonical
upstream NemoClaw, OpenShell, and OpenClaw commands** — there is no VSS-specific
install/patch script and no hand-editing of `openclaw.json`. The flow is driven
from [`deploy_nemoclaw.ipynb`](../deploy_nemoclaw.ipynb) (section 3); this
document is the equivalent command reference for running it by hand.


## Prerequisites

- A recent NemoClaw release pinned via `NEMOCLAW_INSTALL_REF` (this repo pins
  `v0.0.80+`) that ships the sandbox-first grammar:
  `nemoclaw onboard --from` and `nemoclaw <sandbox> {policy-add, mcp, config set, upload, gateway-token}`.
- `docker`, `node`/`npm`, `nemoclaw`, and `openshell` on `PATH`.
- Provider credentials in the environment (`NVIDIA_API_KEY`, or
  `NEMOCLAW_ENDPOINT_URL` + `NEMOCLAW_MODEL` + `COMPATIBLE_API_KEY` for a custom
  OpenAI-compatible endpoint). The default agent model is **Claude Opus 5
  through the NVIDIA Inference Hub**, which is reachable from NVIDIA
  infrastructure and issues its own token:

  ```bash
  export NEMOCLAW_ENDPOINT_URL="https://inference-api.nvidia.com/v1"
  export NEMOCLAW_MODEL="aws/anthropic/bedrock-claude-opus-5"
  export COMPATIBLE_API_KEY="<token for that endpoint>"
  ```

  Create a Hub key at <https://inference.nvidia.com/key-management?action=new-key>;
  the model ids it serves are listed at <https://inference.nvidia.com/?new=0>.

  Any other endpoint replaces all three — your own gateway or router with the id
  it serves the model under, a self-hosted server, or a provider's public API —
  and its own documentation is where that key and those model ids come from.
  `NVIDIA_API_KEY` (`nvapi-…`, from <https://build.nvidia.com>) is the
  build.nvidia.com path instead, and a NemoClaw-managed local model needs no key
  at all. Notebook section 1.2 stays the authority on which variables each
  provider reads.
- This repo checked out so the policy, the harness image definition for the
  runtime you pick (`.openclaw/` or `.hermes/`), skills, and workspace docs are
  available.

## Canonical flow

```bash
SB="${NEMOCLAW_SANDBOX_NAME:-demo}"
RUNTIME="${AGENT_RUNTIME:-openclaw}"          # openclaw (default) or hermes
REPO="$(git rev-parse --show-toplevel)"

# 1. Install NemoClaw (pinned). The installer auto-onboards a sandbox from the
#    environment when NEMOCLAW_NON_INTERACTIVE=1 / NEMOCLAW_PROVIDER are exported,
#    and non-interactive onboard takes the image from NEMOCLAW_FROM_DOCKERFILE
#    alone - export it first, or the installer creates a stock sandbox under $SB
#    that step 2 then finds and keeps. The notebook's 3.1 probes a reused
#    sandbox for the `vss` CLI and refuses one without it.
export NEMOCLAW_SANDBOX_NAME="$SB" NEMOCLAW_FROM_DOCKERFILE="$REPO/.$RUNTIME/Dockerfile"
curl -fsSL "https://raw.githubusercontent.com/NVIDIA/NemoClaw/${NEMOCLAW_INSTALL_REF}/install.sh" | bash

# 2. Create the sandbox (provider/model come from the environment)
#    NEMOCLAW_PROVIDER=build|custom, NEMOCLAW_MODEL, NEMOCLAW_ENDPOINT_URL, COMPATIBLE_API_KEY / NVIDIA_API_KEY
# CHAT_UI_URL bakes gateway.controlUi.allowedOrigins (gateway.* cannot be
# edited afterwards) — set it to the dashboard origin before onboarding. That
# origin is the *relay* port (18790, step 7), not NemoClaw's own forward port
# 18789, which stays loopback-only.
# On Brev, read that origin from the environment context file instead of
# assembling it: the secure-link domain varies per instance (gobrev.dev,
# brevlab.com, ...), and a wrong one is baked in for the sandbox's lifetime.
# The sandbox image is built from the repo's own harness Dockerfile (NemoClaw's
# custom-image workflow, `--from`; the Dockerfile's directory is the build
# context). .openclaw extends NemoClaw's managed OpenClaw runtime
# with the VSS OpenClaw plugin (the `vss` CLI as a tool, the operation skills,
# the workspace docs); .hermes extends the managed Hermes runtime
# with the same skills, docs and CLI. Nothing is installed into the sandbox
# afterwards except the policy and, for Kubernetes, a rendered ENV.md.
BREV_CTX="${BREV_ENVIRONMENT_CONTEXT_PATH:-/etc/brev/environment-context.json}"
# /etc/brev is 0700 root:root, hence the sudo read. Resolve the fqdn in its own
# command and check it before onboarding: as a `VAR=$(...) cmd` prefix, a failed
# substitution still runs the command, so a missing link would bake
# "https://null" in as the allowed origin for the sandbox's lifetime.
CHAT_UI_FQDN="$(sudo -n cat "$BREV_CTX" | jq -er '[.ports[]?|select(.destination_port==18790)|.fqdn][0]')"
if [ -z "$CHAT_UI_FQDN" ] || [ "$CHAT_UI_FQDN" = null ]; then
  echo "No secure link published for destination port 18790 (the relay port). Create it in the Brev console (Secure Links -> + HTTP port -> 18790) and re-run, or drop CHAT_UI_URL to onboard without a remote origin."
else
  CHAT_UI_URL="https://$CHAT_UI_FQDN" \
    nemoclaw onboard --non-interactive --agent "$RUNTIME" --name "$SB" \
      --from "$REPO/.$RUNTIME/Dockerfile"
fi

# 3. Apply the VSS sandbox policy (merges into the base OpenShell policy)
nemoclaw "$SB" policy-add --from-file "$REPO/assets/vss_nemoclaw_policy.yaml" --yes

# 3b. Record the deployment the agent operates. One contract for Compose and
#     Kubernetes: `vss configure` probes the path routes behind a single origin;
#     only the origin differs (haproxy on this host, or a cluster's Ingress).
#     The notebook also uploads it as VSS_PUBLIC_URL in ENV.md so the agent
#     re-runs the same call at session start.
VSS_PUBLIC_URL="${VSS_PUBLIC_URL:-http://host.openshell.internal:7777}"
openshell sandbox exec -n "$SB" -- vss configure --base-url "$VSS_PUBLIC_URL"
openshell sandbox exec -n "$SB" -- vss configure check
openshell sandbox exec -n "$SB" -- vss-openclaw-sync    # vss-hermes-sync on Hermes

# 4. Deployment origin (Kubernetes only): render VSS_PUBLIC_URL into ENV.md and
#    upload it over the image's copy. Compose deployments leave it as shipped.
# NOTE: the destination is a DIRECTORY (OpenShell mkdir + tar-extracts into it)
# sed "s|^export VSS_PUBLIC_URL=.*|export VSS_PUBLIC_URL=\"$VSS_PUBLIC_URL\"|" \
#   "$REPO/.openclaw/workspace/_nemoclaw/ENV.md" > /tmp/ENV.md
# nemoclaw "$SB" upload /tmp/ENV.md /sandbox/.openclaw/workspace/   # hermes: /sandbox/

# The checked-in NemoClaw ENV.md defaults HITL_ENABLED=false. In this mode the
# agent asks required questions in its normal response, ends the turn, and
# continues after the user's next chat message. Do not enable structured HITL
# unless both the active harness protocol and its UI support response events.

# 5. Orchestrator MCP registration — only for HTTPS.
#    Default path: leave this out. deploy_vss_orchestrator.ipynb starts the
#    host-side HTTP MCP at http://host.openshell.internal:9988/mcp; the agent
#    reaches it without a sandbox `mcp add`.
#    HTTPS only: set ORCHESTRATOR_ENABLE_HTTPS=true in both notebooks, then:
# nemoclaw "$SB" mcp add vss_orchestrator --url https://host.openshell.internal:9988/mcp

# 6. Sandbox config: only the optional webhooks need config set.
#    gateway.* (incl. controlUi.allowedOrigins) is rejected — it comes from
#    CHAT_UI_URL at onboard; agents.defaults.workspace already defaults to
#    ~/.openclaw/workspace (= /sandbox/.openclaw/workspace in the sandbox).
nemoclaw "$SB" config set --key hooks.enabled \
  --value true --config-accept-new-path --restart

# 7. Forward the dashboard, relay it off-loopback, read the UI token.
#    The forward stays on 127.0.0.1: `nemoclaw $SB connect|recover|start`
#    re-creates it there and retires a 0.0.0.0 one as stale (a --from image
#    cannot use NemoClaw's remote-bind contract), and it refuses to act when
#    any second listener shares the port. Off-loopback clients — the
#    vss-agent-ui container via host.docker.internal, the Brev secure link —
#    use the relay on its own port instead. The notebook resolves the bind
#    addresses at run time; by hand, bind Docker's default-bridge gateway
#    (what host.docker.internal resolves to) or 0.0.0.0 on Brev.
openshell forward start --background 18789 "$SB"
if [ -n "${BREV_ENV_ID:-}" ]; then BIND=0.0.0.0;   # the secure-link edge arrives off-loopback
else BIND=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'); fi
setsid -f python3 "$REPO/deploy/docker/scripts/nemoclaw/dashboard-relay.py" \
  --sandbox "$SB" --listen "$BIND" --port 18790 --upstream 127.0.0.1:18789
nemoclaw "$SB" gateway-token
#    vss-agent-ui then reaches the gateway at ws://host.docker.internal:18790.
```

## Non-interactive execution

Automation runs the notebook itself rather than a copy of the steps above.
[`run_setup_notebook.py`](../run_setup_notebook.py) — "the runner" below — reads
a checked-in notebook with `nbformat` and executes every cell with `nbclient`
(`allow_errors=False`, so the first failing cell aborts the run), which keeps
the notebook as the single source of setup logic:

```bash
uv run --isolated --no-project --python 3.12 \
  --with nbformat --with nbclient --with ipykernel -- \
  python deploy/docker/scripts/run_setup_notebook.py \
    --notebook deploy/docker/scripts/deploy_nemoclaw.ipynb
```

Two things invoke it. Skill-eval CI is the usual one:
[`notebook_setup_adapter.py`](../../../../.github/skill-eval/nemoclaw/notebook_setup_adapter.py)
loads the runner by file path (`.github` is not an importable package), maps the
eval's provider contract onto the notebook-native variables below, and runs
`deploy_nemoclaw.ipynb`, `deploy_nemo_relay.ipynb`, then
`deploy_vss_orchestrator.ipynb` on the trial box.
The other is anyone running the command above by hand or from their own
automation. Everything CI-specific — the provider mapping, the scoped MCP
cleanup, the runtime env file — stays in the adapter; the runner has no
knowledge of it.

Settings come from the environment. Most are picked up unaided, because the
notebook's own advanced-settings block reads them from its `SHELL_ENV` snapshot.
The provider variables in section 1.2 are the exception: cells (a), (b) and (c)
are mutually exclusive choices a human picks between, each assigning
`NEMOCLAW_PROVIDER` and friends as plain Python literals, so executing the
notebook top to bottom runs all three and the last one wins no matter what the
caller asked for. The runner therefore injects
`NAME = os.environ.get("NAME", NAME)` immediately before the derived-settings
marker — the first point where every provider literal is in scope — giving the
environment the last word. `NOTEBOOK_PARAMETERS` in the runner lists which
variables get that treatment per notebook; a variable the notebook already
reads from `SHELL_ENV` does not belong there. The injection touches only the
in-memory copy and executed notebooks are never written back, so a run cannot
persist credentials into the checkout.

## Why canonical

Keeping the flow on first-class NemoClaw commands means VSS and NemoClaw stay
decoupled: NemoClaw version upgrades, new agent runtimes (e.g. Hermes via
`--agent hermes`), and new features are picked up without VSS having to
patch, post-edit, or re-implement installer/onboard behaviour.
