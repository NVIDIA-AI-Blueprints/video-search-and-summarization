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

Cleanup defaults to `always` and deletes run-owned handles after each point,
including failed/unconfirmed uploads. When the CLI returns no sensor ID, cleanup
reads the public VIOS listing once and resolves the exact generated UUID name
from the persisted upload intent. Only one matching video with a valid, unique
sensor ID can be deleted; matching a prefix is never sufficient. `on-success` retains failed uploads;
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

The runner writes a pending intent to `raw/upload_ledger.jsonl` **before** starting
each CLI upload, including warmups. It records the exact generated UUID filename,
camera name, run ID and public VIOS URL. An intent write failure prevents that
upload. Returned sensor IDs are appended before waiting for ES. Each append is
flushed and synced to disk, so a timeout or interruption before CLI JSON output
still leaves a recoverable intent. The v2 ledger supports both intents and resolved
identities; recovery also accepts older v1 ledgers containing returned IDs.

The intent write precedes upload latency timing. Saving a returned ID remains
included in end-to-end latency after CLI timing ends. Missing-ID lookup and
cleanup occur outside the measured sweep window. Finding a cleanup handle never
changes a failed/timed-out upload into a confirmed ingestion.
The ledger is ownership evidence, not a completed benchmark result.

Stop the interrupted run before recovery. Use the same CLI executable and CLI
configuration as that run, then preview:

```bash
python scripts/recover_cleanup.py --ledger /path/to/results/raw/upload_ledger.jsonl \
  --cli-executable /path/to/vss --cli-config-home /path/to/cli-config
```

After reviewing the exact targets, add `--apply` to delete them. The helper checks
the original VIOS URL and validates all targets before deletion. A recorded ID
requires an exact ID, generated name and video type match. An ID absent from the
listing is `already_absent`; recovery never substitutes a same-name replacement.
A pending intent requires one exact generated-name match with a valid, unique
video ID. The helper durably saves all newly resolved IDs before deleting, so
subsequent recovery uses those IDs. Preview mode makes no ledger changes.

An intent with no current match is `unresolved`, and recovery exits nonzero;
media may become visible later. Ambiguous names, invalid target identities or a
malformed ledger stop all deletions. Unrelated diagnostic rows in the VIOS listing
do not establish ownership and are left alone. The helper does not retry an
upload or failed deletion, inspect internal services, or delete ES records.

Keep this trusted local ledger with the run's other artifacts. If a returned-ID
append fails, normal cleanup retains the media instead of deleting while the
ledger might still contain only an intent. A partial/corrupt ledger requires
inspection, not automatic recovery. Old v1 runs interrupted before recording an ID
still require operator reconciliation; never guess ownership or bulk-delete by
prefix. Recovery confirms only VIOS deletion;
CV/Embed/ES cleanup remains asynchronous. Use a fresh results directory for the
next attempt and managed background execution to avoid foreground tool deadlines.
