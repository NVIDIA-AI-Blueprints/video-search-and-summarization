# Dynamic config E2E

Smaller-scope end-to-end driver for the dynamic-config flow. Acts as a "fake
video analytics api" against the live `mdx-notification` topic while the
integration-test docker-compose stack is up.

## Scenarios covered

| # | Name | Input | Expected ack | File written | Workers apply |
|---|---|---|---|---|---|
| 1 | bootstrap | full config (incl. `kafka`) | none (silent) | yes (filtered) | all |
| 2 | upsert-success-app | `{"app": [valid]}` | `success` | yes | all |
| 3 | upsert-success-sensors | `{"sensors": [{id, configs}]}` | `success` | yes | all |
| 4 | upsert-success-mixed-types | one valid item per value-validator helper kind (bool / int / float / enum / datetime / JSON-list / Pydantic-JSON / non-empty string) | `success` | yes | all |
| 5 | upsert-empty-patch | `{}` | `success` (no-op, `error=null`) | **no** | n/a |
| 6 | upsert-non-dict | `[1,2,3]` (config field) | `failure` | **no** | n/a |
| 7 | upsert-pure-forbidden | `{"kafka": {...}}` only | `failure` | **no** | n/a |
| 8 | upsert-pure-bad-items | `{"app": [{name: "", value: ...}]}` | `failure` | **no** | n/a |
| 9 | upsert-sensor-with-empty-configs | `{"sensors": [{"id": "cam1", "configs": []}]}` | `failure` ("empty sensor configs not allowed" — ambiguous shape) | **no** | n/a |
| 10 | upsert-pure-non-allowlisted | `{"app": [{numProcesses, "8"}]}` (restart-required key) | `failure` ("not allowlisted") | **no** | n/a |
| 11 | upsert-pure-invalid-value | `{"app": [{behaviorMaxPoints, "0"}]}` (below min) | `failure` ("invalid") | **no** | n/a |
| 12 | upsert-partial-with-forbidden | `{"kafka", "app": [good]}` | `partial-success` | yes (only `app`) | all |
| 13 | upsert-partial-with-bad-item | `{"app": [good, bad]}` | `partial-success` | yes (only good) | all |
| 14 | upsert-partial-with-non-allowlisted | `{"app": [allowlisted, numProcesses]}` | `partial-success` | yes (only allowlisted) | all |
| 15 | upsert-partial-with-invalid-value | `{"app": [valid, trajDirectionMode="5"]}` | `partial-success` | yes (only valid) | all |
| 16 | upsert-kafka-source-type | `reference-id="kafka"` + valid `app` patch (sourceType==kafka) | `success` (ack `reference-id` rewritten to `kafka-<broker_ts_ms>`) | yes | all |
| 17 | upsert-missing-status | `{"config": {}, "error": null}` (body omits `status`) | `success` (no-op; `ConfigMessage.status` defaults to `null`) | **no** | n/a |
| 18 | upsert-missing-config | `{"status": null, "error": null}` (body omits `config`) | `failure` ("no config to update") | **no** | n/a |
| 19 | upsert-null-config | `{"status": null, "config": null, "error": null}` | `failure` ("no config to update") | **no** | n/a |
| 20 | upsert-config-as-list | `{"status": null, "config": [], "error": null}` | `failure` ("payload is not a JSON object") | **no** | n/a |
| 21 | upsert-error-as-object | `{"status": null, "config": {}, "error": {"message": "bad"}}` | `failure` ("invalid envelope shape") | **no** | n/a |
| 22 | upsert-invalid-broker-timestamp | Kafka record timestamp set to `10**18` (out of range) | **none** (listener drops at wire layer) | **no** | n/a |
| 23 | upsert-extra-envelope-field | `{"status": null, "config": {}, "error": null, "extra": true}` | `failure` ("unrecognized envelope keys") | **no** | n/a |
| 24 | bootstrap-empty-reply-in-window¹ | (restart) → reply `{status: failure, config: null}` within 15s | none | no | n/a |

Scenarios 16–23 exercise the envelope-level gates added to
`deserialize_config_message` (key filter, broker-timestamp guard,
reference-id prefix-vs-source-type) and `validate_envelope` (extras
check, null-config-for-upsert check, pydantic typing on
`status`/`error`).

¹ Scenario 24 restarts the `mdx-analytics` container so the listener emits a
fresh `request-config`, then races to deliver an empty `upsert-all` reply
within the listener's 15s bootstrap window. Verifies the listener logs
`video analytics api had no config to send` (and NOT `bootstrap timed out`), no file
lands, no apply happens. Runs last because the restart invalidates the
running app's state.

Every scenario that lands a file also asserts that **all watchdogs** (main
process + each worker process) ran `apply` on it, by counting `applied
config from <name>` lines in the container's docker logs. Default
expected count is 5 (1 main + 4 workers, matching
`warehouse_2d`/`kafka`); override with
`--expected-worker-applies`.

Bootstrap will `SKIP` (not fail) if the listener has already timed out
its `request-config` window by the time the driver runs (>15s after app
startup) — the request-config message can still be replayed from offset
0 since `auto.offset.reset=earliest`, but the bootstrap reply only
applies if the listener is still alive and has the matching
`bootstrap_ref_id` registered.

## What it does NOT cover

- Hot refresh of a `self.config.X` value under live frame processing (would need a probe
  processor in the app — see the larger E2E plan).
- Multi-replica fan-out across pods.
- Worker respawn / late-spawn replay.
- Real network partition / broker outage.
- Disk-full / write failure (already covered by unit tests with mocked
  `os.rename`).

## Prerequisites

Start the integration-test stack first:

```bash
cd tests/integration && ./test.sh warehouse_2d kafka dev
# wait until "Process count verified" appears, then in another shell:
```

Required tools:

- `docker` (used to inspect the app container's `CONFIG_DIR`)
- The `mdx-analytics` container running (default name; override with
  `--container`)
- `confluent-kafka` Python package — already in `Pipfile`

## Run

```bash
pipenv run python3 tests/integration/dynamic_config/dynamic_config_e2e.py
```

With overrides:

```bash
pipenv run python3 tests/integration/dynamic_config/dynamic_config_e2e.py \
  --bootstrap-servers localhost:9092 \
  --container mdx-analytics \
  --config-dir /tmp/checkpoint/config \
  --bootstrap-window-sec 5 \
  --ack-timeout-sec 10 \
  --verbose
```

## Output

```
============================================================
Dynamic config E2E summary
============================================================
  ✓ bootstrap: PASS
  ✓ upsert-success-app: PASS
  ✓ upsert-success-sensors: PASS
  ✓ upsert-success-mixed-types: PASS
  ✓ upsert-empty-patch: PASS
  ✓ upsert-non-dict: PASS
  ✓ upsert-pure-forbidden: PASS
  ✓ upsert-pure-bad-items: PASS
  ✓ upsert-sensor-with-empty-configs: PASS
  ✓ upsert-pure-non-allowlisted: PASS
  ✓ upsert-pure-invalid-value: PASS
  ✓ upsert-partial-with-forbidden: PASS
  ✓ upsert-partial-with-bad-item: PASS
  ✓ upsert-partial-with-non-allowlisted: PASS
  ✓ upsert-partial-with-invalid-value: PASS
  ✓ upsert-kafka-source-type: PASS
  ✓ upsert-missing-status: PASS
  ✓ upsert-missing-config: PASS
  ✓ upsert-null-config: PASS
  ✓ upsert-config-as-list: PASS
  ✓ upsert-error-as-object: PASS
  ✓ upsert-invalid-broker-timestamp: PASS
  ✓ upsert-extra-envelope-field: PASS
  ✓ bootstrap-empty-reply-in-window: PASS
============================================================
```

Exit code is `0` if no scenario failed (a `SKIP` on `bootstrap` does not
fail the run), `1` on any `FAIL`.

## Implementation notes

- Each scenario builds its own `Consumer` with a fresh group id and
  `enable.auto.commit=false` so there's no offset bleed between runs.
- Bootstrap uses `auto.offset.reset=earliest` to replay the listener's
  startup `request-config`. The other scenarios use `latest` to skip past
  any historical traffic.
- File landing is observed via `docker exec <container> ls
  /tmp/checkpoint/config/`. No bind-mount needed.
- The driver does **not** restart the app, so a re-run during the same
  app lifetime should still work — the success/forbidden scenarios send
  unique `reference-id`s per invocation; bootstrap uses whatever ref-id
  the listener emitted at startup (read from the topic).
