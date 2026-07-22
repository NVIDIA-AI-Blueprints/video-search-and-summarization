# Deployment resolution for `vss`

Deployment resolution is a single origin. A deployed Docker profile publishes
one haproxy ingress (`VSS_BASE_URL`, host port `HAPROXY_HOST_PORT`, default
7777) that path-routes every service the CLI needs; `vss search run
--base-url <origin>` derives all endpoints from it. The CLI never reads
configuration out of a deployed VSS.

## Route map

| Path | Service | Notes |
|---|---|---|
| `/api` | VSS agent | served natively (ingestion three-step, deletion) |
| `/vst` | VST ingress | served natively (sensor list, storage/media links) |
| `/elasticsearch` | Elasticsearch | path-stripped; **read-only at the edge** — mutating methods and admin/cluster paths are denied |
| `/cosmos-embed` | RTVI-Embed | path-stripped |
| `/rtvi-cv` | RTVI-CV | path-stripped |

`VST_EXTERNAL_URL` equals the base origin: media links in results ride the
same ingress and outlive the CLI process.

## Precedence

Explicit `--*-endpoint` flags override `--base-url`-derived values. Index
names default to the profile's pinned values
(`mdx-embed-filtered-2025-01-01`, `mdx-behavior-2025-01-01`,
`mdx-raw-2025-01-01`); pass explicit index flags for non-default deployments.

## Kubernetes

Deferred. When the Helm deployment exposes an equivalent host-reachable
single-origin ingress, the same `--base-url` contract applies; until then use
explicit non-secret endpoint flags. Secret-backed values are never read or
passed.
