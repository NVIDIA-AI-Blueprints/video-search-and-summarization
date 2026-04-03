---
name: nemoclaw-vss-plugin-brev-install
description: Install NemoClaw + VSS OpenClaw plugin on a Brev instance. Use when the user wants to set up NemoClaw, run init_vss_nemoclaw.sh, or install the VSS OpenClaw plugin on a remote Brev GPU instance. Trigger keywords - nemoclaw, openclaw, vss install, init nemoclaw, nemoclaw setup, install nemoclaw and vss skills, vss skills brev.
allowed-tools: Bash, Read
argument-hint: "[brev-instance-name]"
---

# NemoClaw + VSS OpenClaw plugin Brev Install

Install NemoClaw, Ollama, and the VSS OpenClaw plugin on a Brev instance using `scripts/nemoclaw/init_vss_nemoclaw.sh`.

## What This Does

Runs `init_vss_nemoclaw.sh` on the Brev instance, which:
1. Installs and starts Ollama
2. Pulls the model (default: `qwen3.5`)
3. Installs/onboards NemoClaw (installs Node.js via nvm, builds CLI, creates sandbox)
4. Configures the OpenShell inference provider
5. Applies the VSS sandbox policy
6. Installs the VSS OpenClaw plugin with 7 skills
7. Updates the OpenClaw allowed origin with the Brev instance URL

The full install takes **5–10 minutes** (model pull + Docker image build + sandbox startup).

## Workflow

The VSS repo is cloned on the instance at `/home/ubuntu/video-search-and-summarization` (branch `feat/skills`), so `scripts/nemoclaw/` is already present — no file copy needed.

### 1. Find the Brev instance
```bash
brev ls
```
- If only one instance is RUNNING, use it.
- If multiple instances are found, ask the user which one to use before proceeding.
- If none are RUNNING, ask the user to start one.

**Verify the instance was launched from the correct launchable.**
Instances from the VSS NemoClaw launchable (`https://brev.nvidia.com/launchable/deploy/now?launchableID=env-3BgcwbtTMrB4IXdnaeDwaq5ULti`) are named `nemoclaw---vss-*`. If the selected instance name does not match this pattern, warn the user and ask them to confirm before continuing.

**IMPORTANT:** Always use single quotes around the nohup command to prevent local shell expansion.

### 2. Run installer in background
```bash
brev exec <instance> 'nohup bash /home/ubuntu/video-search-and-summarization/scripts/nemoclaw/init_vss_nemoclaw.sh > /tmp/nemoclaw_install.log 2>&1 & echo "PID: $!"'
```

Note the PID printed. Do NOT run this twice — check the log first if unsure whether it's already running.

### 4. Monitor progress
Poll the log every 60 seconds:
```bash
brev exec <instance> 'tail -50 /tmp/nemoclaw_install.log'
```

Expected milestones (in order):
- `Ollama already installed` / `Installing Ollama`
- `Ollama is ready`
- `pulling manifest ... 100%` — model downloaded
- `Start installing/onboarding NemoClaw`
- `Running NemoClaw installer` or `Running nemoclaw onboard`
- `Building image openshell/sandbox-from:...` — sandbox Docker build (~2–3 min)
- `Uploading image into OpenShell gateway...`
- `Waiting for NemoClaw dashboard to become ready...`
- `VSS OpenClaw plugin installed`
- `OpenClaw UI at https://...`

### 5. Verify success
Installation is successful when the log contains **all** of:
```
[run_nemoclaw_install] VSS OpenClaw plugin installed
[run_nemoclaw_install] Updating OpenClaw config for sandbox demo
OpenClaw UI at https://openclaw0-<instance-id>.brevlab.com/#token=...
```

And the skills table shows 7 rows with `✓ ready`:
- alerts, deploy, incident-report, sensor-ops, video-analytics, video-search, video-summarization

Report the OpenClaw UI URL and token to the user.

## Troubleshooting

**"Another NemoClaw onboarding run is already in progress" / lock error:**
Check if the lock-holding PID is still running:
```bash
brev exec <instance> 'ps aux | grep nemoclaw | grep -v grep'
```
If the process is running, wait for it. If it's dead (stale lock), remove the lock:
```bash
brev exec <instance> 'rm -f /home/ubuntu/.nemoclaw/onboard.lock'
```
Then re-run the installer.

**Multiple installer processes running:**
Do NOT run the installer again. Check the existing log — the install may have already succeeded. Only one process should run at a time.

**Installer exits before OpenClaw UI line:**
Check for errors:
```bash
brev exec <instance> 'grep -i "error\|fail\|exit" /tmp/nemoclaw_install.log | tail -20'
```

**Ollama model pull hangs:**
Check Ollama log:
```bash
brev exec <instance> 'cat /tmp/ollama.log | tail -20'
```

**`/home/ubuntu/NemoClaw/install.sh` not found:**
NemoClaw source must be pre-installed on the instance at `/home/ubuntu/NemoClaw/`. This is expected to be present on nemoclaw Brev instances.

## Expected Final Output

```
[run_nemoclaw_install] VSS OpenClaw plugin installed
[run_nemoclaw_install] Updating OpenClaw config for sandbox demo
Updated /sandbox/.openclaw/openclaw.json
Brev instance ID: <id>
Origin allowed in OpenClaw: https://openclaw0-<id>.brevlab.com
Dashboard token: <token>

OpenClaw UI at https://openclaw0-<id>.brevlab.com/#token=<token>

[run_nemoclaw_install] To use nemoclaw in your current shell, run:

  . "/home/ubuntu/.nvm/nvm.sh"
```
