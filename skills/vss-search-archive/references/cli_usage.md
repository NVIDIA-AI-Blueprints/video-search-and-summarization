# `vss-cli search run` reference

Run the `vss-cli` console executable from the independently distributed
`nvidia-vss-cli` project in the checkout:

```bash
uv run --project libs/vss-cli vss-cli search run [options]
```

Do not invoke it through `docker exec`, `kubectl exec`, a pod shell, or an
agent runtime endpoint.

## Deployment selectors

```bash
# Docker: generated.env plus checked-out profile config
--deployment docker --profile search

# Kubernetes: live Deployment + ConfigMaps, with managed port-forwards
--deployment kubernetes --namespace <namespace> --release <release>
--kube-context <context>  # optional
```

Kubernetes `VST_EXTERNAL_URL` must be a host-reachable ingress URL or an
operator-managed localhost forward that stays alive while result media links
are used. The CLI rejects an in-cluster Service URL for this field; its managed
backend forwards close when the command exits.

Explicit backend flags override values discovered through either selector. If
no selector is used, all required backend values must be supplied explicitly
or through `--config` and explicit non-secret `--config-env KEY=VALUE` pairs.
The CLI does not read host process endpoint variables.

Always append exactly one critic choice: `--no-use-critic` for ordinary host
search, or `--use-critic` only when operator-supported VLM wiring is available.
Do not rely on an omitted flag because omission inherits the deployment default.

## Query controls

```bash
# Embed-only
--query "red forklift" --source-type video_file --top-k 10

# Time-bounded named-source search
--query "person at entrance" --video-source entrance-camera \
--timestamp-start "2025-01-01T14:00:00" --timestamp-end "2025-01-01T15:00:00"

# Fusion search
--query "person in white jacket running" --attribute "white jacket" --has-action true
```

`--video-source` is validated against the selected deployment's VST source
listing. An unavailable or ambiguous source stops the command before search.

## Capability controls

`ELASTIC_SEARCH_INDEX` is preferred; the fallback is
`mdx-embed-filtered-2025-01-01`. Model IDs are verified through `/v1/models`.
RTVI-CV text embedding is preflighted for attribute/fusion search. Use
`--allow-embed-only-fallback` only to explicitly accept a result with the
attribute portion removed.

Never provide secrets through CLI flags. Kubernetes Secret values are not read
by this command.

`vss-cli search run` is read-only. File upload, RTSP registration, and source
deletion must use `scripts/manage_search_source.sh` as described in
`source_lifecycle.md`; do not replace them with bare VIOS mutations.
