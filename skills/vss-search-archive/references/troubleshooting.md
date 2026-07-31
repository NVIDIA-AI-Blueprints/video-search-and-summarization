# Search troubleshooting

- **Docker host CLI entry point fails:** run
  `uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run --help`
  after validating `VSS_REPO_ROOT`, and preserve the error. The
  executable is project-local, so `which vss` is not a valid preflight.
  Stop instead of calling Docker backends manually.
- **Exit 4 `no deployment configured`:** run `vss configure --base-url "${VSS_ORIGIN}"`.
- **Exit 4 `config ... has no 'base_url'`:** `~/.vss/config.json` was written by
  something other than `vss configure`; re-run configure to rewrite it.
- **Exit 4 `<path> needs <service>`:** the origin does not route it. Check the
  `routed`/`absent` lines from `vss configure`.
- **Exit 3 from `vss configure check`:** a recorded route stopped answering.
- **Public route fails:** verify the origin, DNS, TLS, and the
  `/api/v1/videos` and `/vst/api/v1/sensor/version` routes. Do not
  use `kubectl`, Service DNS, NodePorts, or port-forwards as a fallback.
- **Named source missing or ambiguous:** stop and ask the user to select or
  ingest a source. Never run an unconstrained substitute query.
- **Zero search results:** report the empty result explicitly and keep the
  selected source constraint. Offer a concrete query simplification or
  similarity-threshold adjustment; do not broaden the search without consent.
- **Index missing (exit 5):** wait for agent-backed ingestion, then re-run
  `vss configure` and read the names from `vss configure show`. `vss configure
  check` probes routes only, so it passes even when a recorded index is gone.
- **Embed model missing:** re-run `vss configure`; the model id is read from
  RT-Embed's own model list.
- **RTVI-CV absent:** repair RT-CV, or use `run embed`, which does not need it.
- **RT-VLM unavailable (Docker diagnostics) or critic results are `unverified`:** resolve the
  deployment's RT-VLM endpoint and verify `/v1/models`, then inspect
  `vss-rtvi-vlm` logs. In remote mode, RT-VLM remains local as the media proxy;
  confirm it uses `openai-compat`, points `RTVI_VLM_ENDPOINT` at the remote
  endpoint's `/v1`, and advertises the exact configured model name.
- **Authenticated visual/media route:** use the operator-managed secret workflow; do not pass
  API keys through `vss` or copy a Secret to the host.
