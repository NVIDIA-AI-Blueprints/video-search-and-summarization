# `vss-cli search run` invocation reference

Use this reference after completing the mandatory source-resolution step in
`SKILL.md`. The CLI is environment-free: resolve the running deployment's
non-secret configuration first, then pass it explicitly. Do not use
`POST /generate` for search.

## Kubernetes / Helm

Use the same wrapper shown in `SKILL.md`, replacing its Docker prefix with the
pod equivalent:

```bash
kubectl exec -i deploy/vss-agent -- sh -lc '<the validated wrapper from SKILL.md>'
```

If the deployment is release-prefixed, resolve its name first with:

```bash
kubectl get deploy -l app.kubernetes.io/name=vss-agent -o name
```

The wrapper must run inside the pod so its mounted config and service DNS match
the deployed runtime. `HOST_IP` remains required before deriving any endpoint;
never replace an unresolved host with `localhost`.

## Credential boundary

Pass only non-secret values through `--config-env`. Never print API-key
environment variables, include them in command arguments, or add
`--vlm-api-key` to a shared shell recipe. If the remote VLM requires an API
key, stop this workflow and use the approved secret-managed operator procedure.

## Query examples

Append explicit query controls to the final `vss-cli search run` call:

```bash
# Action search
--query "show me people running" --source-type video_file --top-k 10

# Time-bounded search
--query "person at the entrance" --source-type video_file \
  --timestamp-start "2025-01-01T14:00:00" --timestamp-end "2025-01-01T15:00:00"

# Live stream search
--query "find all instances of forklifts" --source-type rtsp
```

## Control reference

| Flag | Use |
|---|---|
| `--config` + non-secret `--config-env KEY=VALUE` | Preserve the deployed NAT profile without CLI env fallback. |
| `--video-source` | Restrict to the source resolved before the search. |
| `--source-type` | Select `video_file` or `rtsp`. |
| `--top-k`, `--min-cosine-similarity` | Control result count and precision. |
| `--attribute`, `--has-action` | Run attribute-only or fused action-and-appearance search. |
| `--description`, `--timestamp-start`, `--timestamp-end` | Filter by metadata and time. |
| `--decomposed-json`, `--object-id` | Supply host decomposition or re-search tracked objects. |
| `--use-critic` / `--no-use-critic` | Require or skip critic verification when the runtime is configured for it. |
| `--vlm-media-mode`, `--vst-clip-enable-audio` | Match the deployed unauthenticated VLM transport. |

When `critic_result` is null, report critic verification as skipped and offer
the screenshot Verification Step. See [discovery_modes.md](discovery_modes.md)
for search strategies and [deployment_resolution.md](deployment_resolution.md)
for runtime discovery.
