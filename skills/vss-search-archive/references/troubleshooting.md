# Search troubleshooting

- **Host CLI entry point fails:** run
  `uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run --help`
  after validating `VSS_REPO_ROOT`, and preserve the error. The
  executable is project-local, so `which vss` is not a valid preflight.
  Stop instead of switching search interfaces or calling backends manually.
- **Docker profile environment missing:** `.env` and runtime `generated.env`
  are both required. Start the selected profile with
  `deploy/docker/scripts/dev-profile.sh` when the generated overlay is absent;
  `.env` alone is not initialized runtime state.
- **Kubernetes discovery fails:** check namespace/release spelling and RBAC for
  Deployments, ConfigMaps, Services, and port-forwards. Do not replace the host
  command with `kubectl exec`.
- **Kubernetes rejects `VST_EXTERNAL_URL`:** replace the Service-backed value
  with a host-reachable ingress URL or an operator-managed localhost forward
  that remains alive after `vss` exits. Managed backend forwards are too
  short-lived for result media links.
- **Named source missing or ambiguous:** stop and ask the user to select or
  ingest a source. Never run an unconstrained substitute query.
- **Zero search results:** report the empty result explicitly and keep the
  selected source constraint. Offer a concrete query simplification or
  similarity-threshold adjustment; do not broaden the search without consent.
- **Index missing:** wait for agent-backed ingestion and inspect the reported
  MDX indexes. The fallback index is `mdx-embed-filtered-2025-01-01`.
- **Embed model missing:** review the `/v1/models` IDs printed by the command
  and set `--cosmos-embed-model` explicitly.
- **RTVI-CV text embedding absent:** repair RTVI-CV, or use
  `--allow-embed-only-fallback` only when dropping all attributes is intended.
- **RT-VLM unavailable or critic results are `unverified`:** resolve the
  deployment's RT-VLM endpoint and verify `/v1/models`, then inspect
  `vss-rtvi-vlm` logs. In remote mode, RT-VLM remains local as the media proxy;
  confirm it uses `openai-compat`, points `RTVI_VLM_ENDPOINT` at the remote
  endpoint's `/v1`, and advertises the exact configured model name.
- **Authenticated visual/media route:** use the operator-managed secret workflow; do not pass
  API keys through `vss` or copy a Secret to the host.
