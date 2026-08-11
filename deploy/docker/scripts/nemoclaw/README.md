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
for skill in "$REPO"/skills/*/ ; do
  [ -f "$skill/SKILL.md" ] && nemoclaw "$SB" skill install "$skill"
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

## Why canonical

Keeping the flow on first-class NemoClaw commands means VSS and NemoClaw stay
decoupled: NemoClaw version upgrades, new agent runtimes (e.g. Hermes via
`--agent hermes`), and new features are picked up without VSS having to
patch, post-edit, or re-implement installer/onboard behaviour.
