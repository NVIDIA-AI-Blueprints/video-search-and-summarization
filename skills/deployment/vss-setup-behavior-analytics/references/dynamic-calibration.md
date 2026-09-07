> See [`../SKILL.md`](../SKILL.md) for the project overview.

# Dynamic Calibration

behavior-analytics supports replacing the live calibration (sensors, ROIs, tripwires, homographies) at runtime via messages on the `mdx-notification` Kafka topic. This document is the **contract** between the producer (video analytics api) and the consumer (the worker's `CalibrationBase` instance). For end-user docs (HTTP API, request shapes) see the `video-analytics-api` repo.

---

## Quick mental model

```
video analytics api  -- upsert/upsert-all/delete -->  mdx-notification
                                                            |
                                                            v
                                              CalibrationListener
                                              (consumer thread, main process)
                                                            |
                                              schema-validate (pre-write gate)
                                                            |
                                                            v
                                              atomic write -> /tmp/checkpoint/calibration/
                                              <action>-calibration-<iso>.json
                                                            |
                                                            v
                                              CalibrationFileMonitor.on_moved
                                              (watchdog, per-worker)
                                                            |
                                                            v
                                              CalibrationBase.reload_data
                                              (per-worker)
                                                            |
                                              schema-validate (defense-in-depth) -> update_calibration_info -> _load_data
```

Three event types:

| Event | Semantics | Worker action |
|---|---|---|
| `upsert-all` | Full snapshot replacement | Replace entire `calibration_info` with the payload; rebuild sensors / ROIs / tripwires from scratch |
| `upsert` | Per-sensor merge (add / replace) | Merge sensors in the payload into the existing `calibration_info["sensors"]` map by `id` |
| `delete` | Per-sensor removal | Drop sensors whose `id` appears in the payload's `sensors[]` |

Unlike dynamic config, there's no request/reply bootstrap. The calibration's **initial state** is loaded from the file at `--calibration <path>` (CLI flag) at startup; runtime updates are additive deltas on top.

---

## Atomic-write contract

Writes into `CALIBRATION_DIR` (`/tmp/checkpoint/calibration` by default) **must be atomic-rename**. `CalibrationListener._atomic_write` stages a hidden `.<name>.tmp` and `os.rename`s it into place. The watchdog only listens for `on_moved`; `on_created` is intentionally not handled because a non-atomic direct write fires `on_created` while the file is still partial and would race the read.

Any debug / operator workflow that drops a file in must `mv` from outside `CALIBRATION_DIR`, not `cp` — same rule as dynamic config.

Filename convention:

```
<action>-calibration-<iso8601-with-z>.json
e.g. upsert-all-calibration-2026-05-15T10:34:21.000Z.json
```

The action prefix is parsed by `reload_data` (`os.path.basename(file_path).split("-calibration-")[0]`) and drives both the merge logic in `update_calibration_info` and the per-action schema check in `calibration_validator.validate`.

---

## Component map

Under `services/analytics/behavior-analytics/`:

```
src/mdx/analytics/core/transform/calibration/
├── calibration_listener.py    # Main-process consumer thread: drain mdx-notification
│                              # -> filter by timestamp -> atomic-write file
├── calibration_validator.py   # Per-action JSON Schema gate
├── calibration_base.py        # CalibrationBase + watchdog (on_moved -> reload_data
│                              # -> _read_config -> validate -> update_calibration_info)
├── calibration_g.py           # Geo (lat/lng) calibration
├── calibration_e.py           # Cartesian calibration
├── calibration_i.py           # Image-plane calibration
├── calibration_dynamic.py     # Wrapper that one-time-switches from
│                              # no-file to a typed calibration when the
│                              # first event lands
└── schemas/calibration.schema.json  # Vendored from
                                     # services/analytics/video-analytics-api/src/web-api-core/schemas/ajv/calibration.json
```

The split mirrors dynamic config. The **main process** runs a single `CalibrationListener` (wired up in `services/analytics/behavior-analytics/src/mdx/analytics/core/app/app_runner.py`): it drains `mdx-notification` and atomic-writes files into `CALIBRATION_DIR`. **Each worker** — spawned, not forked (`mp.get_context("spawn")`), so it starts a fresh interpreter with no inherited parent state — builds its own `CalibrationBase`-derived instance in `BaseApp.__init__` and calls `start_listen()`, which runs a **per-worker** watchdog `Observer` on `CALIBRATION_DIR`. Workers race each other to apply the same file in their own process (`on_moved -> reload_data`), so every worker independently reloads the new sensor map. There is no shared parent calibration object and nothing is pickled across the process boundary.

---

## Per-action validation policy (schema gate)

`calibration_validator.validate(payload, action)` dispatches on the parsed action:

| Action | Schema | Why |
|---|---|---|
| `upsert-all` | Full vendored schema (`schemas/calibration.schema.json`) | This is a full snapshot — same constraints video analytics api enforces pre-publish. Validation here catches schema drift between video analytics api and the worker, or a non-video-analytics-api producer |
| `upsert` | Full schema | video-analytics-api enforces the same schema on the input before publishing. Worker-side validation is defense-in-depth |
| `delete` | Minimal inline schema (sensors is non-empty array of `{id: <non-empty string>}`) | video analytics api builds the delete payload from already-stored sensor records; those may legitimately omit fields the strict full schema requires (legacy data, hand-edited entries). A full check would falsely reject legitimate deletes |

### Two-layer enforcement

The same validator runs at two boundaries:

1. **Listener (pre-write gate)** — `CalibrationListener.process_notifications`
   parses the Kafka message and calls `validate(payload, action)` before
   the atomic write. A schema violation or non-JSON body is logged
   (`rejecting invalid calibration payload at listener: ...`) and the
   notification is skipped — **no file lands in `CALIBRATION_DIR`** and
   `last_insert_timestamp` is NOT advanced (so a corrected republish
   under a new timestamp gets a clean retry). This keeps the directory
   clean and avoids waking the watcher for known-bad payloads.
2. **Watcher (defense-in-depth)** — `CalibrationBase.reload_data`
   re-validates after reading the file. This covers any file that
   bypasses the listener: out-of-band `mv` drops (debug / operator
   workflows), startup `--calibration <path>` load, future tooling.
   On failure, the watcher's `on_moved` wraps `reload_data` in a
   `try/except Exception`, so a bad payload is **logged with every
   violation listed** and the previously-good calibration **stays
   loaded** — no crash, no partial state.

Both layers raise `CalibrationValidationError`; the listener catches
it locally to drop the notification, the watcher relies on the outer
`try/except` to keep the worker running.

## DynamicCalibration: the one-time switch

`DynamicCalibration` is a thin wrapper used when the app starts with **no** `--calibration` argument. It begins as a `CalibrationI` (image-plane) placeholder and, on the first calibration event, switches to the typed subclass (`Calibration` / `CalibrationE` / `CalibrationI`) inferred from the payload's `calibrationType` field.

```
DynamicCalibration(config, calibration_path=None)
                 |
                 v
       _calibrator: CalibrationI  ─── until first reload ───┐
                                                            │
                                                            v
                                            reload_data() runs
                                            schema-validate (upsert-all)
                                            inspect calibrationType
                                            _create_typed_calibration()
                                            -> new _calibrator: CalibrationE
                                            -> _started_with_file = True
                                                            │
                                                            v
                                        subsequent reloads -> _calibrator.reload_data
```

After the one-time switch, the inherited `CalibrationBase` watcher continues to drive `reload_data`, which now delegates to the typed `_calibrator`. The switch is guarded by `_switch_lock` so a burst of file events can't double-switch.

See `services/analytics/behavior-analytics/src/mdx/analytics/core/transform/calibration/calibration_dynamic.py` and the unit tests in `services/analytics/behavior-analytics/tests/unit/mdx/analytics/core/transform/calibration/test_calibration_dynamic.py` for the contract.

---

## Known limitations and gotchas

1. **Validation is strict on `upsert-all` / `upsert`, lenient on `delete`.** If video-analytics-api's stored data has historically-acceptable-but-now-schema-violating sensors, a `delete` referencing those sensors still works. An `upsert-all` carrying those sensors would be rejected — the operator must fix the stored data first.
2. **Reload is per-worker (redundant by design).** The main process only listens and writes files; each spawned worker runs its own watchdog on `CALIBRATION_DIR` and reloads independently, so every worker applies each update on its own (same split as `CONFIG_DIR`). Because workers are spawned (not forked), there is no shared calibration object — a bug in one worker's reload can't corrupt another's, but N workers each re-parse the same file.
3. **Stale-timestamp filter is monotone.** `CalibrationListener` rejects any notification whose `timestamp` is `<= last_insert_timestamp`. After a Kafka offset reset (or replay from offset 0), old notifications are silently skipped. This is intentional — out-of-order deliveries would otherwise corrupt the in-memory map.
4. **`globalROIs` is not read.** Legacy test fixtures use `globalROIs` (CamelCase). Production code reads `rois` (lowercase). The vendored schema follows `rois`. Migration of legacy data is operator-owned.
5. **No ACK back to video analytics api.** The dynamic-config flow publishes `ack` after applying; the calibration flow does not. A worker-side validation failure is observable only via container logs (`calibration schema violation (...)`).

---

