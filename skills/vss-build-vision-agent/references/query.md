# Headless query (read path)

Querying a deployed headless build is a **runtime, read-only** step: bring-up
registers no source and runs no query. A `_builds/<name>` deployment has no
agent, so the query runs from the host CLI (`vss search run`) against the
deployed backends. Source registration and provisioning are the separate,
agent-free write path — see `references/stream-provisioning.md`, not this file.

## Resolve endpoints from the build, not from a profile

A `_builds/<name>` build is not a stock developer profile — no `dev-profile.sh`
`generated.env`, no agent `config.yml` — so the CLI's `--deployment docker
--profile` discovery does not apply. Use the **explicit-endpoint mode** (no
`--deployment` selector) and source every value from the build's own
`resolved.yml`, so the URLs match its actual exposed ports. The CLI reads no
endpoint env vars and no agent config; the flags below are the whole resolution.

| CLI flag | Source in `resolved.yml` | Host form |
|---|---|---|
| `--es-endpoint` | Elasticsearch published port | `http://localhost:<port>` |
| `--cosmos-embed-endpoint` | RT-Embed published port | `http://localhost:<port>` |
| `--rtvi-cv-endpoint` | RT-CV published port (attribute/fusion only) | `http://localhost:<port>` |
| `--vst-internal-url` | VST published port | `http://localhost:<port>` |
| `--vst-external-url` | host-reachable VST origin | `${HOST_IP}:<port>` |
| `--video-embed-index` / `--behavior-index` / `--frames-index` | index families the build writes | `ELASTIC_SEARCH_INDEX`, `mdx-behavior-*`, `mdx-raw-*` |

Run the CLI **on the deploy host**: the build publishes these ports
loopback-only (querying from another host would need them published beyond
loopback), so only `--vst-external-url` — which builds the screenshot/clip links
a result opens — must be host-reachable. Confirm the exact required set,
including the `--behavior-index-wildcard` / `--frames-index-wildcard` variants
when the build distinguishes them, with `vss search run --help`.

## Run

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev \
  vss search run \
    --es-endpoint "http://localhost:<es-port>" \
    --cosmos-embed-endpoint "http://localhost:<embed-port>" \
    --rtvi-cv-endpoint "http://localhost:<rtvi-cv-port>" \
    --vst-internal-url "http://localhost:<vst-port>" \
    --vst-external-url "<host-reachable-vst-origin>" \
    --video-embed-index "<embed-index>" \
    --behavior-index "mdx-behavior-*" \
    --frames-index "mdx-raw-*" \
    [query flags]
```

With an installed `vss`, drop the `uv run --project … --no-dev` prefix.

## Defer the query itself to `vss-search-archive`

Endpoint resolution above is the only build-specific part; follow that skill for
everything else:

- **Decompose** the NL request (`query_decomposition.md`) — the CLI does not
  decompose; pass `--query`/`--decomposed-json`/`--attribute` and time bounds.
  Bypassing the conversational agent does **not** remove this step: the driving
  agent performs decomposition itself. Never pass a raw NL sentence to `--query`
  — keep the action/object in `query`, route appearance terms via
  `search_mode=fusion` + `attributes`, and set the mode explicitly.
- **Pick the mode** (`cli_usage.md`, `discovery_modes.md`): embed, attribute, or
  fusion, with `--top-k`, thresholds, and time windows.
- **Name the source**: `--video-source` is validated by the CLI against the
  deployed VST listing (list via `vss-manage-video-io-storage`).
- **Validate and verify**: follow that skill's media-URL validation and
  verification opt-in. Search is retrieval-only.

One substitution: where that skill's Docker workflow derives
`EXPECTED_VST_EXTERNAL_URL` via `discover_docker`, reuse the `--vst-external-url`
value passed above.

## Sources

- `skills/vss-search-archive/` — `query_decomposition.md`, `cli_usage.md`,
  `discovery_modes.md`, `deployment_resolution.md`
- `services/agent/packages/vss_cli/` — the `vss search run` CLI
