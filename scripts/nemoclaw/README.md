# NemoClaw VSS Installer

`init_vss_nemoclaw.sh` bootstraps a NemoClaw sandbox on a Brev instance and installs the Video Search and Summarization OpenClaw plugin into it.

## What It Does

When you run `init_vss_nemoclaw.sh`, it:

1. Installs Ollama if needed.
2. Starts `ollama serve` with the requested GPU selection.
3. Pulls the requested Ollama model.
4. Runs NemoClaw onboarding if `nemoclaw` is already available, or falls back to `/home/ubuntu/NemoClaw/install.sh`.
5. Configures the OpenShell inference provider to talk to Ollama through `host.openshell.internal`.
6. Applies the VSS sandbox policy from `assets/vss_nemoclaw_policy.yaml`.
7. Packages and installs the VSS OpenClaw plugin from `.openclaw/` and `skills/`.
8. Updates OpenClaw's allowed origins and prints the final OpenClaw UI URL when available.

## Expected Environment

This script is meant to run on a NemoClaw-ready Ubuntu machine, typically a Brev instance, with this repository already checked out.

The following repo content is expected to exist:

- `.openclaw/`
- `skills/`
- `assets/vss_nemoclaw_policy.yaml`
- `scripts/nemoclaw/update_openclaw_config.py`

The following host tools or resources are also expected:

- `python3`
- `docker`
- `sudo`
- a working NemoClaw install source at `/home/ubuntu/NemoClaw/install.sh`, unless `nemoclaw` is already in `PATH`

## Usage

Run from the repo checkout on the Brev instance:

```bash
bash scripts/nemoclaw/init_vss_nemoclaw.sh
```

You can also pass the sandbox name and model positionally:

```bash
bash scripts/nemoclaw/init_vss_nemoclaw.sh demo qwen3.5
```

Or use explicit flags:

```bash
bash scripts/nemoclaw/init_vss_nemoclaw.sh \
  --sandbox-name demo \
  --model qwen3.5 \
  --cuda-visible-devices 1
```

To start it in the background on a Brev instance:

```bash
nohup bash /home/ubuntu/video-search-and-summarization/scripts/nemoclaw/init_vss_nemoclaw.sh \
  > /tmp/nemoclaw_install.log 2>&1 &
```

## Options

| Option | Description | Default |
|---|---|---|
| `--sandbox-name NAME` | Target sandbox name | `demo` |
| `--model NAME` | NemoClaw model and default Ollama model | `qwen3.5` |
| `--ollama-model NAME` | Override the Ollama model name only | same as `--model` |
| `--ollama-host HOST:PORT` | Ollama bind address | `0.0.0.0:11434` |
| `--ollama-base-url URL` | OpenShell-facing Ollama endpoint | `http://host.openshell.internal:11434/v1` |
| `--cuda-visible-devices IDS` | GPU selection for `ollama serve` | `1` |
| `--openclaw-config-script PATH` | Path to `update_openclaw_config.py` | `scripts/nemoclaw/update_openclaw_config.py` |
| `--policy-file PATH` | Custom sandbox policy file | `assets/vss_nemoclaw_policy.yaml` |
| `--help` | Show usage help | n/a |

## Environment Variables

The script also honors these environment variables:

- `VSS_REPO_DIR`: repo root used to resolve plugin assets and the default policy file
- `NEMOCLAW_SANDBOX_NAME`
- `NEMOCLAW_MODEL`
- `OLLAMA_MODEL`
- `OLLAMA_HOST`
- `OLLAMA_BASE_URL`
- `CUDA_VISIBLE_DEVICES`
- `OPENCLAW_CONFIG_UPDATE_SCRIPT`
- `NEMOCLAW_POLICY_FILE`
- `VSS_CONTAINER_NAME`: explicit OpenShell gateway container name, if autodetection is not sufficient
- `VSS_NAMESPACE`: Kubernetes namespace for the sandbox pod, default `openshell`

## Expected Output

Successful runs usually include log lines like:

```text
[run_nemoclaw_install] Ollama is ready
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

- If the script stops after the Ollama step, inspect `/tmp/ollama.log`.
- If NemoClaw onboarding fails, verify `nemoclaw` is resolvable or that `/home/ubuntu/NemoClaw/install.sh` exists and is executable.
- If the custom policy is skipped, confirm `assets/vss_nemoclaw_policy.yaml` exists or pass `--policy-file`.
- If plugin installation is skipped, verify the repo checkout includes both `.openclaw/` and `skills/`.
- If the plugin install cannot find a gateway container, set `VSS_CONTAINER_NAME` explicitly.
- If the OpenClaw origin update fails, run `python3 scripts/nemoclaw/update_openclaw_config.py demo` directly to inspect the underlying error.
