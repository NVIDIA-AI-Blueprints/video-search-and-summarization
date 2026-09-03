# NemoClaw + VSS (canonical CLI flow)

VSS creates and configures its NemoClaw/OpenClaw sandbox using **only canonical
upstream NemoClaw, OpenShell, and OpenClaw commands** — there is no VSS-specific
install/patch script and no hand-editing of `openclaw.json`. The flow is driven
from [`deploy_nemoclaw.ipynb`](../deploy_nemoclaw.ipynb) (section 3); this
document is the equivalent command reference for running it by hand.


## Prerequisites

- A recent NemoClaw release pinned via `NEMOCLAW_INSTALL_REF` (this repo pins
  `v0.0.80+`) that ships the sandbox-first grammar:
  `nemoclaw <sandbox> {policy-add, skill install, mcp, config set, upload, gateway-token}`.
- `docker`, `node`/`npm`, `nemoclaw`, and `openshell` on `PATH`.
- Provider credentials in the environment (`NVIDIA_API_KEY`, or
  `NEMOCLAW_ENDPOINT_URL` + `COMPATIBLE_API_KEY` for a custom OpenAI-compatible
  endpoint).
- This repo checked out so the policy, skills, and workspace docs are available.

## Canonical flow

```bash
SB="${NEMOCLAW_SANDBOX_NAME:-demo}"
RUNTIME="${AGENT_RUNTIME:-openclaw}"          # openclaw (default) or hermes
REPO="$(git rev-parse --show-toplevel)"

# 1. Install NemoClaw (pinned)
curl -fsSL "https://raw.githubusercontent.com/NVIDIA/NemoClaw/${NEMOCLAW_INSTALL_REF}/install.sh" | bash

# 2. Create the sandbox (provider/model come from the environment)
#    NEMOCLAW_PROVIDER=build|custom, NEMOCLAW_MODEL, NEMOCLAW_ENDPOINT_URL, COMPATIBLE_API_KEY / NVIDIA_API_KEY
# CHAT_UI_URL bakes gateway.controlUi.allowedOrigins (gateway.* cannot be
# edited afterwards) — set it to the dashboard origin before onboarding.
# <brev-link-domain>: apps.run.brev.nvidia.com on Skybridge instances,
# brevlab.com on legacy ones (see orchestrator_mcp_helper.detect_brev_link_domain).
CHAT_UI_URL="https://18789-${BREV_ENV_ID}.<brev-link-domain>" \
  nemoclaw onboard --non-interactive --agent "$RUNTIME"

# 3. Apply the VSS sandbox policy (merges into the base OpenShell policy)
nemoclaw "$SB" policy-add --from-file "$REPO/assets/vss_nemoclaw_policy.yaml" --yes

# 4. Install VSS skills (one validated SKILL.md directory at a time)
# `skills/*/` is one level deep and only matches `skills/vss-build-vision-ai/SKILL.md`; the
# other 18 live at `skills/<category>/<skill>/SKILL.md`. Use find so all 19 install.
find "$REPO/skills" -name SKILL.md -printf '%h\n' | sort -u | while read -r skill; do
  nemoclaw "$SB" skill install "$skill"
done

# 5. Push workspace bootstrap docs (base, then the _nemoclaw overlay)
# NOTE: the destination is a DIRECTORY (OpenShell mkdir + tar-extracts into it)
for md in "$REPO"/.openclaw/workspace/*.md ; do
  nemoclaw "$SB" upload "$md" /sandbox/.openclaw/workspace/
done
for md in "$REPO"/.openclaw/workspace/_nemoclaw/*.md ; do
  nemoclaw "$SB" upload "$md" /sandbox/.openclaw/workspace/
done

# 6. Orchestrator MCP registration — only for HTTPS.
#    Default path: leave this out. deploy_vss_orchestrator.ipynb starts the
#    host-side HTTP MCP at http://host.openshell.internal:9988/mcp; the agent
#    reaches it without a sandbox `mcp add`.
#    HTTPS only: set ORCHESTRATOR_ENABLE_HTTPS=true in both notebooks, then:
# nemoclaw "$SB" mcp add vss_orchestrator --url https://host.openshell.internal:9988/mcp

# 7. Sandbox config: only the optional webhooks need config set.
#    gateway.* (incl. controlUi.allowedOrigins) is rejected — it comes from
#    CHAT_UI_URL at onboard; agents.defaults.workspace already defaults to
#    ~/.openclaw/workspace (= /sandbox/.openclaw/workspace in the sandbox).
nemoclaw "$SB" config set --key hooks.enabled \
  --value true --config-accept-new-path --restart

# 8. Forward the dashboard + read the UI token
openshell forward start --background 18789 "$SB"
nemoclaw "$SB" gateway-token
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
`deploy_nemoclaw.ipynb` then `deploy_vss_orchestrator.ipynb` on the trial box.
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
