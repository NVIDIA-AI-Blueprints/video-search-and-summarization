# Docker `vss search run` reference

This CLI reference applies to Docker Compose deployments. Kubernetes operate
skills use `${VSS_PUBLIC_URL}/generate` as documented in `SKILL.md`; they do
not invoke the CLI's Kubernetes selector or create port-forwards.

Run the `vss` console executable from the `vss` project in the checkout
(`--no-dev` keeps the sync runtime-only — no NAT or dev tooling):

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}
cd "${VSS_REPO_ROOT}" &&
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss search run [options]
```

The executable is provided by that project and need not exist globally. Do not
use `which vss`; verify the supported entry point directly:

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss search run --help
```

If this Docker preflight fails, report its error and stop. Do not manually call
Elasticsearch, embedding, or search endpoints.

Do not invoke it through `docker exec`, `kubectl exec`, or a pod shell.

## Deployment selector

```bash
# Docker: generated.env plus checked-out profile config
--deployment docker --profile search
```

Explicit backend flags override values discovered through the Docker selector.
If no selector is used, all required backend values must be supplied explicitly
or through `--config` and explicit non-secret `--config-env KEY=VALUE` pairs.
The CLI does not read host process endpoint variables.

Search is retrieval-only. The CLI has no critic or VLM flags. When visual
verification is requested or authorized, inspect the returned screenshots as a
separate, explicit workflow.

## Query controls

```bash
# Embed-only
--query "red forklift" --source-type video_file --top-k 10

# Time-bounded named-source search
--query "person at entrance" --video-source entrance-camera \
--timestamp-start "2025-01-01T14:00:00" --timestamp-end "2025-01-01T15:00:00"

# Fusion search
--query "person in white jacket running" --search-mode fusion --attribute "white jacket"
```

`--video-source` is validated against the selected deployment's VST source
listing. An unavailable or ambiguous source stops the command before search.

## Capability controls

`ELASTIC_SEARCH_INDEX` names only the video embedding index and is preferred
for that field; its fallback is `mdx-embed-filtered-2025-01-01`. It must not be
reused as the behavior or raw index. Those are separate values resolved from
the interpolated deployment config. Model IDs are verified through `/v1/models`.
RTVI-CV text embedding is preflighted for attribute/fusion search. Use
`--allow-embed-only-fallback` only to explicitly accept a result with the
attribute portion removed.

Never provide secrets through CLI flags. Kubernetes Secret values are not read
by this command.

`vss search run` is read-only. For upload, registration, deletion, or
repair, use the agent-backed mutation workflows in the parent skill.
