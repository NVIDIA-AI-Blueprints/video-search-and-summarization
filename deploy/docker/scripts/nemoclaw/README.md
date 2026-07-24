# NemoClaw + VSS setup flow

VSS creates and configures its NemoClaw/OpenClaw sandbox with upstream NemoClaw,
OpenShell, OpenClaw, and the `mcporter` adapter bundled in the pinned OpenClaw
image. There is no VSS-specific install/patch script and no hand-editing of
`openclaw.json`. The flow is driven from
[`deploy_nemoclaw.ipynb`](../deploy_nemoclaw.ipynb) (section 3); this document is
the equivalent command reference for running it by hand.

> Removed in favour of this flow: `init_nemoclaw.sh` and `update_openclaw_config.py`.
> The VSS OpenClaw *plugin* (`.openclaw/{index.ts,package.json,openclaw.plugin.json}`)
> is also gone — skills are installed with `nemoclaw sandbox skill install` and the
> workspace docs under `.openclaw/workspace/` are pushed with `nemoclaw sandbox upload`.

## Prerequisites

- A recent NemoClaw release pinned via `NEMOCLAW_INSTALL_REF` that ships the
  `nemoclaw sandbox {policy add, skill install, exec, config set, upload}`
  subcommands and an OpenClaw image with `mcporter`.
- `docker`, `node`/`npm`, `nemoclaw`, and `openshell` on `PATH`.
- Provider credentials in the environment (`NVIDIA_API_KEY`, or
  `NEMOCLAW_ENDPOINT_URL` + `COMPATIBLE_API_KEY` for a custom OpenAI-compatible
  endpoint).
- This repo checked out so the policy, skills, and workspace docs are available.

## Setup flow

This private host-side MCP command reference uses the OpenClaw runtime. Hermes
requires an authenticated public HTTPS MCP endpoint registered through
NemoClaw's managed MCP command.

```bash
SB="${NEMOCLAW_SANDBOX_NAME:-demo}"
RUNTIME="openclaw"
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

# 6. Register the host-side VSS Orchestrator MCP in OpenClaw.
#    The custom VSS OpenShell policy controls access to this private route.
nemoclaw sandbox exec "$SB" -- \
  mcporter config add vss_orchestrator \
    --url http://host.openshell.internal:9988/mcp \
    --scope home
nemoclaw sandbox exec "$SB" -- \
  mcporter config get vss_orchestrator --json

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

## Why the local MCP uses `mcporter`

NemoClaw v0.0.80's managed MCP boundary requires an authenticated public HTTPS
endpoint. The VSS Orchestrator is intentionally host-local and unauthenticated;
it is reachable only through the route allowed by
`assets/vss_nemoclaw_policy.yaml`. The notebook therefore uses the same
`mcporter` OpenClaw adapter that NemoClaw v0.0.80 uses internally, but registers
the private URL directly. Other agent runtimes should use an authenticated
public HTTPS MCP endpoint with the managed `nemoclaw sandbox mcp add` command.
