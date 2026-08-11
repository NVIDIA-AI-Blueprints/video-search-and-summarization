# NemoClaw + VSS (canonical CLI flow)

VSS creates and configures its NemoClaw/OpenClaw sandbox using **only canonical
upstream NemoClaw, OpenShell, and OpenClaw commands** — there is no VSS-specific
install/patch script and no hand-editing of `openclaw.json`. The flow is driven
from [`deploy_nemoclaw_vss.ipynb`](../deploy_nemoclaw_vss.ipynb) (section 3); this
document is the equivalent command reference for running it by hand.

> Removed in favour of this flow: `init_nemoclaw.sh` and `update_openclaw_config.py`.
> The VSS OpenClaw *plugin* (`.openclaw/{index.ts,package.json,openclaw.plugin.json}`)
> is also gone — skills are installed with `nemoclaw sandbox skill install` and the
> workspace docs under `.openclaw/workspace/` are pushed with `nemoclaw sandbox upload`.

## Prerequisites

- A recent NemoClaw release pinned via `NEMOCLAW_INSTALL_REF` that ships the
  `nemoclaw sandbox {policy add, skill install, mcp add, config set, upload}`
  subcommands.
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
nemoclaw sandbox policy add "$SB" --from-file "$REPO/assets/vss_nemoclaw_policy.yaml" --yes

# 4. Install VSS skills (one validated SKILL.md directory at a time)
for skill in "$REPO"/skills/*/ ; do
  [ -f "$skill/SKILL.md" ] && nemoclaw sandbox skill install "$SB" "$skill"
done

# 5. Push workspace bootstrap docs (base, then the _nemoclaw overlay)
# NOTE: the destination is a DIRECTORY (OpenShell mkdir + tar-extracts into it)
for md in "$REPO"/.openclaw/workspace/*.md ; do
  nemoclaw sandbox upload "$SB" "$md" /sandbox/.openclaw/workspace/
done
for md in "$REPO"/.openclaw/workspace/_nemoclaw/*.md ; do
  nemoclaw sandbox upload "$SB" "$md" /sandbox/.openclaw/workspace/
done

# 6. Orchestrator MCP registration — only for HTTPS.
#    Default path: leave this out. deploy_vss_orchestrator.ipynb starts the
#    host-side HTTP MCP at http://host.openshell.internal:9988/mcp; the agent
#    reaches it without a sandbox `mcp add`.
#    HTTPS only: set ORCHESTRATOR_ENABLE_HTTPS=true in both notebooks, then:
# nemoclaw sandbox mcp "$SB" add vss_orchestrator --url https://host.openshell.internal:9988/mcp

# 7. Sandbox config: only the optional webhooks need config set.
#    gateway.* (incl. controlUi.allowedOrigins) is rejected — it comes from
#    CHAT_UI_URL at onboard; agents.defaults.workspace already defaults to
#    ~/.openclaw/workspace (= /sandbox/.openclaw/workspace in the sandbox).
nemoclaw sandbox config set "$SB" --key hooks.enabled \
  --value true --config-accept-new-path --restart

# 8. Forward the dashboard + read the UI token
openshell forward start --background 18789 "$SB"
nemoclaw sandbox gateway token "$SB"
```

## Why canonical

Keeping the flow on first-class NemoClaw commands means VSS and NemoClaw stay
decoupled: NemoClaw version upgrades, new agent runtimes (e.g. Hermes via
`--agent hermes`), and new features are picked up without VSS having to
patch, post-edit, or re-implement installer/onboard behaviour.
