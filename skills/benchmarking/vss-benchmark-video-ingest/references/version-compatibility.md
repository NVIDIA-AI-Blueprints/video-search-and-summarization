# Version compatibility

`metadata.yml` is the source of the skill version and its deployed-VSS compatibility
policy. The current skill version is **v3.3.0**, with `requires-vss: "==3.3.0"`.
The checker evaluates this rule; it does not assume that matching skill and VSS
version strings always imply compatibility.

## Required pre-upload gate

After the one-time CLI and dependency setup, the agent runs
`scripts/check_compatibility.py` first for each benchmark, before invoking
`validate.py` or `run.py`. It reads that metadata and makes one public
`GET /api/v1/version` request to the configured deployment. The expected response is:

```json
{"service": "vss", "version": "3.3.0"}
```

The API returns the deployed blueprint version. `vss --version` reports the local
CLI package version and is never used as a fallback. A missing route, unavailable
endpoint, malformed response, invalid Semantic Versioning value, or incompatible
version stops the benchmark before any upload. There is no bypass flag or automatic
retry. `run.py` and `validate.py` invoke this gate even if the standalone check was
already run; each new benchmark must check its current deployment.

```bash
python scripts/check_compatibility.py
# Read-only check against an explicitly supplied deployment origin:
python scripts/check_compatibility.py --base-url "$VSS_PUBLIC_URL"
```

Exit codes are `0` for compatible and `2` for failure. The gate uses the same config
file selection and CLI overrides as the runner. `--no-health-check` on validation
only skips the VIOS probe; it does not skip compatibility.

## Routing and comparison

The default endpoint is `configure show`'s `base_url` plus `/api/v1/version`.
`compatibility.version_url` / `--version-url` may specify another public route to
the same deployment. `compatibility.request_timeout_sec` / `--version-timeout`
defaults to 10 seconds. The URL must not contain credentials, query parameters or
a fragment. `VSS_AUTH_TOKEN` is reserved for ES reads and is not sent to this API.

The `requires-vss` rule accepts comma-separated comparisons (`==`, `>=`, `>`, `<=`,
`<`) against three-part versions. Following VSS's release-version convention, the
checker compares **major.minor.patch** and ignores valid prerelease/build suffixes.
For example, the current rule evaluates `3.3.0-dev+build` as release `3.3.0`.
This is the stated policy, not proof that every build or deployment configuration
has been benchmarked. Extend the rule only after validating another release.

A successful result is stored as `version_compatibility` in `run-metadata.json`,
including skill version, deployed VSS version, requirement, API URL, comparison
convention and check time. Summaries read this evidence back from metadata.

## Checks that still apply

A version match cannot prove working CLI commands, webhooks, models or indexing.
The benchmark still checks the CLI/VIOS route, measures the corpus, checks ES and
runs smoke against a new deployment. Required ingestion capabilities remain:
`vios add --type video PATH --name NAME`, JSON `added/type/sensor_id`, `configure show`,
`vios list`, `vios delete`, working VIOS streaming/removal webhooks, and the ES
raw/embed identity/count schema.

The current endpoint is registered in
`services/agent/packages/vss_agents/src/vss_agents/api/version.py`; version resolution
is in `libs/vss/core/src/vss_core/version.py`. Its hosting component does not change
the upload path: ingestion still goes directly from CLI to VIOS and its webhooks.
A deployment whose public version endpoint is absent must be fixed by its operator;
the skill does not change the cluster or fall back to an Agent ingestion request.

Rebaseline when the CLI, notifier, model configuration, ES schema or measurement
contract changes. See [harness comparability](harness-comparability.md).
