# `vss search run` reference

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

If this preflight fails, report its error and stop. Do not substitute an agent
runtime route or manually call Elasticsearch, embedding, or search endpoints.

Do not invoke it through `docker exec`, `kubectl exec`, a pod shell, or an
agent runtime endpoint.

## Deployment resolution (single-origin)

A deployed Docker profile exposes one haproxy ingress; pass it as
`--base-url` and every service endpoint derives from its path routes
(`/elasticsearch` read-only, `/cosmos-embed`, `/rtvi-cv`, pass-through
`/api` and `/vst`):

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run \
  --base-url "${VSS_BASE_URL:-http://127.0.0.1:7777}" ...
```

Kubernetes single-origin support is deferred; use explicit non-secret
`--*-endpoint` flags for host-reachable services there. `VST_EXTERNAL_URL`
must remain host-reachable while result media links are used.

Explicit backend flags override values discovered through either selector. If
no selector is used, all required backend values must be supplied explicitly
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
