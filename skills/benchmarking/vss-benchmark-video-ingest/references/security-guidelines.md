# Authentication, artifacts, and cleanup

Run only local permitted video files. The subprocess uses an argument list, never
shell=True; paths and names are not shell code. Bootstrap CLI dependencies before
measurement. Keep the CLI config stable during concurrent execution; use a dedicated
VSS_CONFIG_HOME for CI or multiple deployments.

VSS_AUTH_TOKEN supplies ES-read Authorization only; version-API requests do not use it. The CLI upload library does not
inherit that as an authentication option. Do not claim an authenticated upload setup
without verifying the CLI/deployment supports it. Never put credentials in YAML,
command arguments, or URLs. Metadata stores token presence, not the value.

Artifacts contain names, paths, counts, timestamps, returned upload identity/recorded
range, and bounded error diagnostics; no video frames. Error messages may contain
server-provided data. Review raw diagnostics before sharing them outside the team.
Results are local; nothing is published or exported. The final artifact validator
scans for the current `VSS_AUTH_TOKEN` value (including its JSON-escaped form) without
printing it. That check does not identify unknown secrets or establish that every
server diagnostic is safe to share; review the recorded diagnostics.

Cleanup defaults to `always` and deletes returned run-owned handles after each
point, including failed/unconfirmed uploads. `on-success` retains failed uploads;
`never` retains all uploads. With cleanup enabled, a failed deletion or missing
handle stops subsequent sweep points and records the reason in metadata and the
summary. Collected point artifacts are still written. These options do not delete
unrelated VIOS media.
An unknown handle is never invented. List sensors before manual cleanup:

```bash
vss vios list --type video
vss vios delete --type video --sensor RETURNED_ID
```

VIOS removal does not prove asynchronous ES cleanup. No raw ES deletion fallback.
Keep results/corpora out of commits; honor the printed transfer projection and the
configured confirmation ceiling for large workloads.

## Interrupted-run recovery

The runner writes `raw/upload_ledger.jsonl` immediately after each CLI response
returns a sensor ID, before waiting for ES. Each append is flushed and synced to
disk, so identities remain available when the process is killed during readiness.
This small local write is included in end-to-end latency, after CLI timing ends.
The ledger contains the run ID, public VIOS URL, generated filename and returned
sensor ID; it is ownership evidence, not a completed benchmark result.

Stop the interrupted run before recovery. Use the same CLI executable and CLI
configuration as that run, then preview:

```bash
python scripts/recover_cleanup.py --ledger /path/to/results/raw/upload_ledger.jsonl \
  --cli-executable /path/to/vss --cli-config-home /path/to/cli-config
```

After reviewing the exact targets, add `--apply` to delete them. The helper checks
the original VIOS URL and requires an exact sensor ID, generated name and video
type match in the current public VIOS listing before it deletes anything. Missing
IDs are reported as `already_absent`; unrelated sensors are untouched. A malformed
ledger or mismatched identity stops all deletions. The helper does not retry a
failed deletion, inspect internal services, or delete ES records.

Keep this trusted local ledger with the run's other artifacts. An interruption
before the CLI returns an ID, or before its ledger append completes, can leave an
unrecorded asset; never guess its ownership or bulk-delete by prefix. Ask the
operator to reconcile that case against upload evidence. A partial/corrupt ledger
requires inspection, not automatic recovery. Recovery confirms only VIOS deletion;
CV/Embed/ES cleanup remains asynchronous. Use a fresh results directory for the
next attempt and managed background execution to avoid foreground tool deadlines.
