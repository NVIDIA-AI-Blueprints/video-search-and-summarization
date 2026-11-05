# Authentication, artifacts and cleanup

Use permitted local video files. Commands use argument arrays rather than shell
interpolation. Keep the installed CLI and its configuration stable during the
run; `VSS_CONFIG_HOME` can isolate a deployment from other CLI users.

`VSS_AUTH_TOKEN` supplies ES-read authorization only. It does not authenticate
CLI uploads or version requests. Never put credentials in URLs, YAML or arguments.
Metadata records token presence, not its value. Artifacts include local paths,
returned identities and bounded server diagnostics; review them before sharing.
The artifact validator scans for the current token, but cannot identify every
unknown secret.

## Cleanup ownership and verification

`always` deletes all run-owned uploads, including non-confirmed uploads.
`on-success` retains non-confirmed uploads; `never` retains everything. A missing
returned ID is resolved only from one public listing with a unique exact generated
UUID name and valid sensor ID. Prefix matches never establish ownership.

The runner requires the CLI response to confirm stored-recording deletion. It
then polls the selected raw/Embed indices for those owned identities until both
counts are absent across the configured settle period. Deletion and verification
run outside measured latency/throughput. Failed deletion, unresolved ownership,
read error or timeout stops the next point. No raw ES deletion fallback exists.

An observed zero count proves only visible document absence. Concurrent webhook
legs may still be withdrawing consumers, and in-flight producers may write after
the observation period. This bounded check does not prove released capacity or
all backend work stopping. Retained media can also affect subsequent load.

## Interrupted-run recovery

Before launching an upload, the runner writes its generated filename, camera name,
run ID and public VIOS URL as an intent in `raw/upload_ledger.jsonl`. Returned sensor
IDs are appended before readiness polling. Each append is flushed and fsynced;
a failed intent write prevents upload, and a failed identity write retains media.
This ledger preserves ownership if the CLI times out before returning its JSON or
the benchmark process is killed. It does not establish successful ingestion.

Intent persistence precedes latency timing. Saving the returned ID occurs after
CLI timing, within end-to-end latency. Missing-ID resolution and deletion happen
outside the point's measurement window. Finding a cleanup identity never changes
a failed upload into a confirmed one.

Stop the interrupted run before recovery. Use its same CLI configuration and
preview the current ledger's exact targets:

```bash
python scripts/recover_cleanup.py --ledger /path/to/results/raw/upload_ledger.jsonl \
  --cli-executable /path/to/vss --cli-config-home /path/to/cli-config
```

After reviewing targets, add `--apply`. Recovery checks the original VIOS URL and
validates all targets before deleting. A recorded ID must match the listed name
and video type; an absent recorded ID is `already_absent` and is never replaced
with a same-name object. A pending intent requires a unique exact-name video
match. Resolved IDs are persisted before deletion; preview writes nothing.

Unresolved intents, ambiguous names or malformed ledgers require investigation;
recovery cannot guess ownership. It does not retry uploads or failed deletions,
inspect internal services or write to ES. A partially written ledger must be
reviewed before recovery.

The standalone helper confirms CLI deletion only and does not run the runner's
ES settling check. Verify downstream removal before another benchmark after
recovery. Preserve prior results and use a fresh results directory for each run.
