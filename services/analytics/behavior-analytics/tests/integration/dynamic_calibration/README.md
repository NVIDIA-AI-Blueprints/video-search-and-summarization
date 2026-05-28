# Dynamic calibration E2E

Smaller-scope end-to-end driver for the dynamic-calibration flow. Acts as a
"fake video analytics api" against the live `mdx-notification` topic (with
`key="calibration"`) while the integration-test docker-compose stack is up.

For the architecture / contract docs, see `../../../readmes/dynamic-calibration.md`.

## Scenarios covered

| # | Name | Wire input | Expected outcome |
|---|---|---|---|
| 1 | upsert-all-valid | `event.type=upsert-all`, full snapshot (`version`/`osmURL`/`calibrationType`/`sensors[]`) | File lands in `CALIBRATION_DIR`; container logs `Reloading new calibration information...` and `upsert-all N sensors` |
| 2 | upsert-valid | `event.type=upsert`, one full sensor | File lands; logs `upsert 1 sensors: ...` |
| 3 | delete-valid | `event.type=delete`, `sensors=[{id}]` | File lands; logs `delete 1 sensors: ...` |
| 4 | upsert-all-schema-invalid | full snapshot missing `calibrationType` | Listener validates pre-write, logs `calibration schema violation` → **no file** written; previously-good calibration on worker side untouched |
| 5 | upsert-schema-invalid | `event.type=upsert`, payload missing `calibrationType` | Same path — full schema rejects pre-write, **no file** written |
| 6 | delete-schema-invalid | `event.type=delete`, sensor missing `id` | Minimal delete-schema rejects pre-write, **no file** written |
| 7 | stale-timestamp | `event.type=upsert`, valid body but timestamp `now - 1h` | Listener filters by `last_insert_timestamp` → **no file** written |

Negative scenarios (4, 5, 6) verify that the **listener** catches
schema violations **before** the file is written, so `CALIBRATION_DIR`
stays clean. The same validator runs again on the worker side
(`CalibrationBase.reload_data`) as defense-in-depth for any file that
bypasses the listener (out-of-band `mv` drop, startup `--calibration`
load), but the E2E driver doesn't exercise that path. 4 and 5 exercise
the full-schema path (`upsert-all`/`upsert`); 6 exercises the minimal
inline schema used for `delete`.

The stale-timestamp scenario (7) verifies the listener-side filter
(`CalibrationListener.last_insert_timestamp`), which silently drops
out-of-order deliveries without writing anything.

## What it does NOT cover

- Out-of-band atomic-write contract violations (debug `cp` into
  `CALIBRATION_DIR` instead of `mv`) — that's a per-watchdog concern
  already exercised by unit tests of `CalibrationFileMonitor`.
- `DynamicCalibration` one-time switch from no-file to typed
  calibration — covered by unit tests
  (`tests/unit/.../test_calibration_dynamic.py`).
- Multi-replica fan-out (calibration runs in the main process only;
  worker processes share the parent's reference via fork).
- Disk-full / write-failure on the listener side (unit tests with
  mocked `os.rename` cover this).
- Schema-drift between web-api's AJV file and the vendored copy
  (covered by intent of the validator commit + a future schema-sync
  check).

## Prerequisites

Start the integration-test stack first:

```bash
cd tests/integration && ./test.sh warehouse_2d kafka dev
# wait until "Process count verified" appears, then in another shell:
```

Required tools:

- `docker` (used to introspect the app container's `CALIBRATION_DIR` and
  scrape container logs)
- The `mdx-analytics` container running (default name; override with
  `--container`)
- `confluent-kafka` Python package — already in `Pipfile`

## Run

```bash
pipenv run python3 tests/integration/dynamic_calibration/dynamic_calibration_e2e.py
```

Useful flags:

```bash
--bootstrap-servers localhost:9092      # Kafka broker
--container mdx-analytics               # app container to inspect
--calibration-dir /tmp/checkpoint/calibration   # where files land
--reload-timeout-sec 10                 # per-scenario deadline for file + log
--verbose                               # DEBUG-level driver logging
```

Exit code is `0` if every scenario passes, `1` on the first hard
failure (driver continues running remaining scenarios so you see the
full picture).

## Ordering caveats

- **7 (stale-timestamp) must run after 1–6.** It relies on the
  listener's `last_insert_timestamp` already being advanced past the
  stale value (`now - 1h`).
- **3 (delete-valid) must run after 2 (upsert-valid)** so there's an
  actual sensor (`e2e-upsert-sensor`) in calibration state to delete.
  (The listener writes the file regardless, but the test reads more
  naturally as a sequence.)

The driver runs scenarios in the order they're declared in `SCENARIOS`
in `dynamic_calibration_e2e.py`, which respects both constraints.

## When to extend

Add a scenario function when:

- A new `event.type` is added to the calibration wire contract.
- The validator gains a new per-action rule worth pinning end-to-end.
- The listener's filename/atomic-write contract changes.
- A new sensor / ROI / tripwire field becomes load-bearing.

Each scenario should be self-contained and idempotent — assume the
container has been running for arbitrary scenarios before; never
hard-code an absolute starting state.
