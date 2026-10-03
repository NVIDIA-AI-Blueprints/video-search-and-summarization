# Local vision-pipeline evaluation

This runner executes the three `vss-build-vision-pipeline` scenarios on the local
GPU using Claude Code and the existing `generic_judge.py`. It does not use Brev
or its Docker reset. Results are local; nothing is published or committed.

Each run has a private working directory, skill snapshot, rendered specification,
agent trace, and judge evidence. The agent and judge run with the invoking user's
host/Docker access: **this is workspace separation, not an OS security sandbox**.
The runner performs no Docker cleanup itself. Workspace `CLAUDE.md` contains only shared-host resource boundaries, including
exact-ID cleanup of trial-owned resources and no global Docker cleanup. The user
query is passed verbatim, without a task preamble or grading instructions.
After an interrupted run, inspect resources labeled `vss.local-eval=<run-directory-name>`
before removing any; this launcher does not assume all containers are disposable.

## Setup

From the repository root:

```bash
uv venv /tmp/vss-local-eval-venv --python 3.12
uv pip install --python /tmp/vss-local-eval-venv/bin/python \
  -r .github/skill-eval/local/requirements.txt
```

Requires Docker with NVIDIA GPU access, `nvidia-smi`, and Claude Code on PATH.
Verify GPU access with a compatible official DeepStream image. Set
`ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` through the shell environment or your
secret manager. Set `ANTHROPIC_BASE_URL` only for a non-default provider/proxy.
For NVIDIA Inference Hub, an exported `NVIDIA_API_KEY` is also accepted. When
`ANTHROPIC_API_KEY` is absent, the runner maps the key in memory and defaults to
`https://inference-api.nvidia.com` and
`aws/anthropic/bedrock-claude-sonnet-4-6`, with proxy-compatible thinking disabled.
Explicit endpoint/model settings take precedence; select a model enabled for your
account. Existing `ANTHROPIC_API_KEY` authentication takes precedence over the alias.
The key is neither printed nor saved. It must be inherited by the runner process;
exporting it in a different terminal does not update an already-running agent.

The shared judge requires an API key; a Claude login alone is insufficient.
`JUDGE_MODEL` optionally selects a different judge model. Do not store secrets
in scripts, task specs, or command-line arguments.

## Prepare and run

```bash
/tmp/vss-local-eval-venv/bin/python .github/skill-eval/local/run.py \
  --spec yolo26_object_detection --prepare-only

# After configuring valid credentials, omit --prepare-only to execute:
/tmp/vss-local-eval-venv/bin/python .github/skill-eval/local/run.py \
  --spec yolo26_object_detection
```

Other scenarios:

- `peoplenet_transformer_four_streams`
- `rf_detr_instance_segmentation`

Run sequentially. The launcher locks out concurrent local runs started through
it; it does not reserve the GPU against other applications. The default agent
limit is 3600 seconds and each judge check has a 600-second limit. Override with
`--agent-timeout` and `--judge-timeout` if needed.

Artifacts default to `/tmp/vss-local-eval/results/`; use `--results-root` for
persistent storage. Exit 0 means preparation succeeded or all checks passed,
according to the selected mode; exit 1 is a failed judgment, and exit 2 denotes
an agent failure or CLI prerequisite error. An agent/API failure skips judging
and produces no success score. Exceptions are infrastructure failures, not passes.

`original-spec.json` preserves the CI spec. `manifest.json` records the actual
GPU and an explicit local hardware override; the L40S CI declaration is unchanged.
The user query is unchanged, and expected checks are reused; local hardware is
recorded separately in the manifest.
RTX 4090 results do not establish L40S compatibility or identical performance.
This is a local behavioral trial, not a Harbor/Brev lifecycle parity test.

Local logs have restrictive permissions and basic secret redaction. They have
not gone through CI's publication scanning gate; do not publish them blindly.
