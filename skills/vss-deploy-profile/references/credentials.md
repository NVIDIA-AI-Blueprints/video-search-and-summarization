# Credential Gate

Run this before mutating `generated.env` or starting any image pull. Validate credentials early: a bad key should fail in seconds, not after a cold NIM start.

## Required By Mode

- `NGC_CLI_API_KEY` or `NGC_API_KEY`: required for any local NIM image pull
  (`LLM_MODE` or `VLM_MODE` set to `local` / `local_shared`). These are the
  same underlying NGC personal API key with different consumer conventions:
  the NGC CLI and VSS generated env use `NGC_CLI_API_KEY`; NIM / RT-VLM
  containers receive the key as `NGC_API_KEY`.
- `NVIDIA_API_KEY`: required for remote NIM endpoints.
- `HF_TOKEN`: required only on edge targets that use the standalone
  small-model vLLM alternative (see [`edge.md`](edge.md)); the in-tree edge
  LLM does not need it.
- Customer LLM/VLM endpoint URL + model name: required for any selected
  remote endpoint. This includes build.nvidia.com / NVIDIA API catalog
  endpoints because their `/v1/models` response can list many models.

## Discovery

Surface discovered credentials to the user; do not auto-source them without confirmation.

- If either `$NGC_CLI_API_KEY` or `$NGC_API_KEY` is set, normalize both names
  to the same resolved NGC key before probes and before writing
  `generated.env`.
- If both `$NGC_CLI_API_KEY` and `$NGC_API_KEY` are set and differ, stop and
  ask which NGC personal API key to use. Do not silently choose one.
- If neither NGC env var is set but `~/.ngc/config` exists, extract the
  account metadata and ask: `Use NGC account <org>/<team> for the deploy?`
- If `$HF_TOKEN` is unset but `~/.cache/huggingface/token` exists, ask before exporting it.

## Probes

Run the credential-probe script. It validates each key that is set (`ok` /
`invalid`), prints `skip` for unset keys, resolves `NGC_CLI_API_KEY` /
`NGC_API_KEY` to one key, and reports a conflict when both are set and differ.
Compare each result with the chosen deployment mode before continuing.

```bash
bash skills/vss-deploy-profile/scripts/check_credentials.sh
```

After the NGC key validates, set **both** `NGC_CLI_API_KEY` and `NGC_API_KEY` to
that one resolved key in `generated.env` — the NGC CLI and VSS env read
`NGC_CLI_API_KEY`; NIM / RT-VLM containers read `NGC_API_KEY`. Do not leave only
one set.

This token probe is not sufficient for local NIM / RT-VLM deployments. It
proves the key authenticates, but it does not prove that the key's org/team can
access the selected `nvcr.io/...` images or `ngc:...` model repositories. After
`resolved.yml` exists, run the artifact probes below before starting Compose.

## Artifact Entitlement Probes

Build the artifact list from the selected deployment:

- `resolved.yml`: every `image:` under `nvcr.io/...` that Compose will pull.
- `generated.env`: NGC-backed model/resource paths such as
  `RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final`. Skip
  `none`, `git:...`, local paths, and remote endpoint URLs.
- Profile staging instructions: NGC model/resource downloads such as
  alerts/search perception models.

Probe each artifact with the normalized NGC key:

- Container images: after `docker login nvcr.io`, run `docker manifest inspect
  <nvcr.io/...>`, or use `ngc registry image info ...` when it maps cleanly to
  an NGC image path. A `401` or `403` from a gated repository proves the key
  lacks the required org/team entitlement.
- NGC models/resources: run `ngc registry model info ...` or `ngc registry
  resource info ...` for the exact repository and tag. Do not use `docker
  manifest inspect` for non-OCI models or a raw `Authorization: Bearer <key>`
  REST call; their expected failures are false entitlement signals. If the NGC
  CLI is unavailable, use the selected gated container-image probe as the
  org/team entitlement signal.
- Profile-staged TAO/perception models: run the corresponding NGC model or
  resource probe before downloading them.

On `401`, `403`, permission, membership, or missing repository errors, stop
and request an NGC key entitled to those artifacts. Do not defer this failure
to image pull, model download, or NIM cold start.

## Remote Endpoint Probes

For every selected remote LLM/VLM endpoint, probe the endpoint before writing
it into `generated.env`. Do this even when the endpoint is on localhost; it
catches wrong ports, stale tunnels, missing auth, and model-name mismatches
before the deploy flow spends time generating compose or warming containers.

Use the base URL without a trailing `/v1`; the script strips `/v1` and
`/v1/models` if the user supplied them. If the endpoint requires auth, set
`REMOTE_API_KEY` to the key that the agent will use for that endpoint.

Aggregate endpoints such as `https://integrate.api.nvidia.com` can advertise
many LLM and VLM models. Do not auto-select the first returned model from such
endpoints. If the endpoint lists multiple models and the user has not selected
an exact model id, stop and ask which model to use.

Run the skill script:

```bash
REMOTE_API_KEY="$NVIDIA_API_KEY" \
  skills/vss-deploy-profile/scripts/probe_remote_models.sh "$LLM_BASE_URL" "$LLM_NAME"

skills/vss-deploy-profile/scripts/probe_remote_models.sh \
  "http://localhost:30081" "nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8"
```

If `/v1/models` fails or does not advertise the selected model, stop and ask
the user for the correct endpoint/model before mutating `generated.env`.

## Decision Rule

A key reported `invalid` that the chosen mode needs, a `skip` for a key the
mode requires, conflicting `NGC_CLI_API_KEY` / `NGC_API_KEY` values, selected
NGC artifact access failure, or a selected remote endpoint that fails
`/v1/models` is a blocker. Prompt the user, re-probe, and do not proceed to env
mutation until it resolves.

A `skip` for a key the mode does not use is fine.
