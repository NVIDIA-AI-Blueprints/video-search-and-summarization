# Search troubleshooting

- **Docker `generated.env` missing:** start the selected profile with
  `deploy/docker/scripts/dev-profile.sh`; do not use `.env` instead.
- **Kubernetes discovery fails:** check namespace/release spelling and RBAC for
  Deployments, ConfigMaps, Services, and port-forwards. Do not replace the host
  command with `kubectl exec`.
- **Kubernetes rejects `VST_EXTERNAL_URL`:** replace the Service-backed value
  with a host-reachable ingress URL or an operator-managed localhost forward
  that remains alive after `vss-cli` exits. Managed backend forwards are too
  short-lived for result media links.
- **Named source missing or ambiguous:** stop and ask the user to select or
  ingest a source. Never run an unconstrained substitute query.
- **Zero search results:** report the empty result explicitly and keep the
  selected source constraint. Offer a concrete query simplification or
  similarity-threshold adjustment; do not broaden the search without consent.
- **Search ingest/delete fails:** use the agent-backed
  `scripts/manage_search_source.sh` recipe and report partial cleanup. Do not
  fall back to a bare VIOS
  upload/delete because that leaves the search services or indexes stale.
- **Index missing:** wait for agent-backed ingestion and inspect the reported
  MDX indexes. The fallback index is `mdx-embed-filtered-2025-01-01`.
- **Embed model missing:** review the `/v1/models` IDs printed by the command
  and set `--cosmos-embed-model` explicitly.
- **RTVI-CV text embedding absent:** repair RTVI-CV, or use
  `--allow-embed-only-fallback` only when dropping all attributes is intended.
- **Authenticated VLM:** use the operator-managed secret workflow; do not pass
  API keys through `vss-cli` or copy a Secret to the host.
