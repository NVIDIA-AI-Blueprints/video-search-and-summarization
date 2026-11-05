# Public version, CLI and ES surfaces

| Operation | Surface |
|---|---|
| Configure once | `vss configure --base-url ORIGIN` |
| Recorded routing | `vss configure show` |
| Preflight VIOS | `vss vios list --type video` |
| Deployed version gate | VSS `GET /api/v1/version` |
| Every worker upload | `vss vios add --type video PATH --name UNIQUE_FILENAME` |
| Cleanup returned identity | `vss vios delete --type video --sensor RETURNED_ID` |
| Readiness preflight | ES `GET /_cluster/health` |
| Per-upload completion | ES `POST /RAW_INDEX,EMBED_INDEX/_search` with document counts |
| Runner cleanup settling | Public ES searches for run-owned raw camera names and Embed sensor IDs; both counts must remain zero |

The ES URL comes from configure show, or an explicitly supplied public ES URL for
that same deployment. The version API defaults to the configured origin plus
`/api/v1/version`; an explicit override must also target that deployment.
The selected ES route must permit both `GET /_cluster/health` and the readiness
`POST /RAW_INDEX,EMBED_INDEX/_search` reads. Successful CLI discovery or index
catalog reads do not establish that an ingress allows the health route. If the
health request is denied, stop before upload. Use an explicitly operator-provided
public ES route to the same cluster, or ask the operator for one; do not disable
an ingress guard, skip readiness, or automatically try other routes.
Do not construct VIOS URLs or use an Agent upload fallback.

Source trace in a VSS checkout:

- `AGENTS.md` and `libs/vss/cli/README.md`: caller-side CLI installation and contract.
- `services/agent/packages/vss_agents/src/vss_agents/api/version.py`: deployed version API.
- `libs/vss/core/src/vss_core/version.py`: deployment version resolution.
- `libs/vss/cli/src/vss_cli/vios_group.py`, `_add`: upload and VIOS timeline wait.
- `libs/vss/core/src/vss_core/vios/client.py`, `upload_media`: streamed VIOS PUT.
- `deploy/docker/developer-profiles/dev-profile-search/vios/configs/notification_config.json`:
  camera_streaming fan-out and camera_remove cleanup.
- `deploy/docker/services/infra/elk/logstash/pipelines/kafka/mdx-logstash.conf`: indexing.

VSS still contains Agent source, including the version API; uploads bypass its
ingestion handlers. Choosing CLI
does not itself enable webhooks. Helm/Compose must deploy matching notifier, JSON,
consumer schemas, and indexing configuration before this skill runs.
