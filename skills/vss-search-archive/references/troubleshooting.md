# Search troubleshooting

- **Docker host CLI entry point fails:** run
  `uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run --help`
  after validating `VSS_REPO_ROOT`, and preserve the error. The
  executable is project-local, so `which vss` is not a valid preflight.
  Stop instead of calling Docker backends manually.
- **Docker profile environment missing:** `.env` and runtime `generated.env`
  are both required. Start the selected profile with
  `deploy/docker/scripts/dev-profile.sh` when the generated overlay is absent;
  `.env` alone is not initialized runtime state.
- **Kubernetes public route fails:** verify `VSS_PUBLIC_URL`, DNS, TLS, and the
  public `/openapi.json`, `/generate`, and `/vst/api/v1/sensor/version` routes. Do not
  use `kubectl`, Service DNS, NodePorts, or port-forwards as a fallback.
- **Named source missing or ambiguous:** stop and ask the user to select or
  ingest a source. Never run an unconstrained substitute query.
- **Zero search results:** report the empty result explicitly and keep the
  selected source constraint. Offer a concrete query simplification or
  similarity-threshold adjustment; do not broaden the search without consent.
- **Index missing (Docker CLI):** wait for agent-backed ingestion and inspect the reported
  MDX indexes. The fallback index is `mdx-embed-filtered-2025-01-01`.
- **Embed model missing (Docker CLI):** review the `/v1/models` IDs printed by the command
  and set `--cosmos-embed-model` explicitly.
- **RTVI-CV text embedding absent (Docker CLI):** repair RTVI-CV, or use
  `--allow-embed-only-fallback` only when dropping all attributes is intended.
- **RT-VLM unavailable (Docker diagnostics) or critic results are `unverified`:** resolve the
  deployment's RT-VLM endpoint and verify `/v1/models`, then inspect
  `vss-rtvi-vlm` logs. In remote mode, RT-VLM remains local as the media proxy;
  confirm it uses `openai-compat`, points `RTVI_VLM_ENDPOINT` at the remote
  endpoint's `/v1`, and advertises the exact configured model name.
- **Authenticated visual/media route:** use the operator-managed secret workflow; do not pass
  API keys through `vss` or copy a Secret to the host.
