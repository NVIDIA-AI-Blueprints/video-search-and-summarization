# NemoClaw VSS Installer

`init_nemoclaw.sh` bootstraps a NemoClaw sandbox on a Brev instance and installs the Video Search and Summarization OpenClaw plugin into it.

It currently uses a remote NVIDIA-hosted model via `NVIDIA_API_KEY`.

## What It Does

When you run `init_nemoclaw.sh`, it:

1. Runs NemoClaw onboarding if `nemoclaw` is already available, or falls back to `/home/ubuntu/NemoClaw/install.sh`.
2. Configures the OpenShell inference provider to use the remote NVIDIA-hosted model API.
3. Applies the VSS sandbox policy from `assets/vss_nemoclaw_policy.yaml`.
4. Packages and installs the VSS OpenClaw plugin from `.openclaw/` and `skills/`.
5. Updates OpenClaw's allowed origins and prints the final OpenClaw UI URL when available.

## Expected Environment

This script is meant to run on a NemoClaw-ready Ubuntu machine, typically a Brev instance, with this repository already checked out.

The following repo content is expected to exist:

- `.openclaw/`
- `skills/`
- `assets/vss_nemoclaw_policy.yaml`
- `deployments/scripts/nemoclaw/update_openclaw_config.py`

The following host tools or resources are also expected:

- `python3`
- `docker`
- `sudo`
- a working NemoClaw install source at `/home/ubuntu/NemoClaw/install.sh`, unless `nemoclaw` is already in `PATH`

## Usage

Run from the repo checkout on the Brev instance:

```bash
bash deployments/scripts/nemoclaw/init_nemoclaw.sh
```

You can also pass the sandbox name and model positionally:

```bash
bash deployments/scripts/nemoclaw/init_nemoclaw.sh demo nvidia/nvidia-nemotron-nano-9b-v2
```

Or use explicit flags:

```bash
bash deployments/scripts/nemoclaw/init_nemoclaw.sh \
  --sandbox-name demo \
  --model nvidia/nvidia-nemotron-nano-9b-v2 \
  --nvidia-api-key "$NVIDIA_API_KEY"
```

To start it in the background on a Brev instance:

```bash
nohup bash /home/ubuntu/video-search-and-summarization/deployments/scripts/nemoclaw/init_nemoclaw.sh \
  > /tmp/nemoclaw_install.log 2>&1 &
```

## Options

| Option | Description | Default |
|---|---|---|
| `--sandbox-name NAME` | Target sandbox name | `demo` |
| `--model NAME` | NemoClaw inference model | `nvidia/nvidia-nemotron-nano-9b-v2` |
| `--remote-base-url URL` | OpenAI-compatible base URL for remote provider | `https://integrate.api.nvidia.com/v1` |
| `--nvidia-api-key KEY` | API key for remote provider | `NVIDIA_API_KEY` env fallback |
| `--openclaw-config-script PATH` | Path to `update_openclaw_config.py` | `deployments/scripts/nemoclaw/update_openclaw_config.py` |
| `--policy-file PATH` | Custom sandbox policy file | `assets/vss_nemoclaw_policy.yaml` |
| `--help` | Show usage help | n/a |

## Environment Variables

The script also honors these environment variables:

- `VSS_REPO_DIR`: repo root used to resolve plugin assets and the default policy file
- `NEMOCLAW_SANDBOX_NAME`
- `NEMOCLAW_MODEL`
- `NEMOCLAW_REMOTE_BASE_URL`
- `NEMOCLAW_REMOTE_API_KEY`
- `NVIDIA_API_KEY`
- `OPENCLAW_CONFIG_UPDATE_SCRIPT`
- `NEMOCLAW_POLICY_FILE`
- `VSS_CONTAINER_NAME`: explicit OpenShell gateway container name, if autodetection is not sufficient
- `VSS_NAMESPACE`: Kubernetes namespace for the sandbox pod, default `openshell`

## Expected Output

Successful runs usually include log lines like:

```text
[run_nemoclaw_install] Using remote NVIDIA-hosted model provider
[run_nemoclaw_install] Start installing/onboarding NemoClaw
[run_nemoclaw_install] Finished installing/onboarding NemoClaw
[run_nemoclaw_install] Applying custom policy to sandbox demo
[run_nemoclaw_install] VSS OpenClaw plugin installed
[run_nemoclaw_install] Updating OpenClaw config for sandbox demo
OpenClaw UI at https://openclaw0-<brev-id>.brevlab.com/#token=<token>
```

If the config update succeeds, the helper also prints:

- `Updated /sandbox/.openclaw/openclaw.json` or `No JSON change needed ...`
- `Brev instance ID: ...`
- `Origin allowed in OpenClaw: https://openclaw0-<brev-id>.brevlab.com`
- `Dashboard token: ...`

## Troubleshooting

- Verify `NVIDIA_API_KEY` is set before running the installer.
- If NemoClaw onboarding fails, verify `nemoclaw` is resolvable or that `/home/ubuntu/NemoClaw/install.sh` exists and is executable.
- If the custom policy is skipped, confirm `assets/vss_nemoclaw_policy.yaml` exists or pass `--policy-file`.
- If plugin installation is skipped, verify the repo checkout includes both `.openclaw/` and `skills/`.
- If the plugin install cannot find a gateway container, set `VSS_CONTAINER_NAME` explicitly.
- If the OpenClaw origin update fails, run `python3 deployments/scripts/nemoclaw/update_openclaw_config.py demo` directly to inspect the underlying error.
