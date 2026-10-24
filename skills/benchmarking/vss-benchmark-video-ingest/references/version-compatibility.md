# Version compatibility

The skill declares `metadata.requires-vss` in `SKILL.md`. Its `metadata.version`
is maintained by the repository's shared version-stamping workflow, so there is
no separate skill-version file to update.

## Required gate

After CLI setup, run this read-only check before the benchmark:

```bash
python scripts/check_compatibility.py --config config.local.yml
```

The wrapper finds `services/agent/scripts/check_vss_version.py` in the checkout
selected by `--vss-repo`, `cli.repo` or `VSS_REPO_ROOT`, using the same checkout
resolution as the runner. Installed copies of the skill still need that prepared
checkout. The wrapper delegates requirement parsing and version comparison to the
shared checker. It preserves CLI-discovered endpoint selection and writes JSON
provenance. `validate.py` and `run.py` repeat the gate before any upload.

The endpoint defaults to the recorded deployment origin plus `/api/v1/version`.
`--version-url` or `compatibility.version_url` may select another public route to
the same deployment. The expected response includes `service: "vss"` and its
SemVer `version`. `--version-timeout` defaults to 10 seconds. Endpoint URLs cannot
include credentials, query parameters or fragments; the ES auth token is not sent.

The current requirement accepts the 3.3 release line, including patch releases,
and excludes 3.4. Shared-checker comparison follows the repository's release
convention for prerelease/build suffixes. A version match does not prove working
webhooks, models or indexing; run the remaining preflight and smoke checks.

| Exit | Meaning |
|---|---|
| `0` | Compatible |
| `1` | Indeterminate: missing checker/configuration, failed lookup or invalid response |
| `3` | Incompatible deployed version |
| `2` | Command-line usage error |

Stop on any nonzero result. Local `vss --version` is not a deployment version and
is never a fallback. `--no-health-check` skips only VIOS probing, not this gate.

Successful `version_compatibility` metadata records the skill/version requirement,
deployed version, public endpoint, check time, skill file and shared checker file.
Reports use that recorded evidence. Change compatibility policy only with evidence
for the newly admitted release; a permissive range is not a validation result.
