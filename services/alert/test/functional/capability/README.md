# Event-loop capability / load suite (no-GPU sim harness)

Sustained-stream capability checks for the `event_loop` pipeline mode, run
entirely against the functional-test simulators (Elastic/NIM/VST/VSS) plus a
dockerized Kafka — no GPU required. Simulated VLM latency comes from the
threaded NIM stub (`NIM_STUB_DELAY_SECONDS`); load comes from
`incident_stream_publisher.py` in `--unique` mode, where every message is a
fresh cohort so the survivor rate equals the injection rate exactly.

```bash
./run_capability.sh                 # full suite (sets up Kafka + sims if absent)
./run_capability.sh --test TS-004   # single check
```

| Check | Setup | Pass criteria |
|---|---|---|
| TS-001 | event_loop, `num_workers=2`, `max_vlm_concurrent=20`, VLM 3s, 5 msg/s | pipeline VLM in-flight gauge exceeds the worker count and never the cap; consumer lag flat |
| TS-002 | thread_bridge, `async_dispatch_workers=2`, same stream | VLM concurrency capped at the thread count and lag grows (the ceiling event_loop removes) |
| TS-003 | event_loop, cap 10, VLM 2s, ramp 1→3→5 msg/s | wait-excluded `vlm_duration` stays within ±20% of the low-concurrency baseline |
| TS-004 | event_loop, cap 5, 10 msg/s overload, 1s sampling | every `event_loop_vlm_in_flight` sample ≤ cap (zero tolerance), max == cap |
| TS-005 | event_loop, `max_vst_concurrent=4`, delayed VST sim | every `event_loop_vst_in_flight` sample ≤ cap, calls overlap |
| TS-006 | event_loop, cap 5, sustained overload then drain | `dispatch_in_flight` never exceeds the global bound; produced == after_dedup == events_total == ES docs (zero loss) |
| TS-011 | restart sweep sync→thread_bridge→event_loop→thread_bridge→sync | each restart lands in the right mode, processes one incident end-to-end, no tracebacks |
| TS-014 | event_loop, 15s VLM, hard-kill while the call is in flight, restart same group | offset committed at consume (lag 0 mid-flight); killed message NOT reprocessed after restart (at-most-once) |
| TS-020 | 20 byte-identical messages burst across a 10-worker pool (`max_poll_records=1`) | exactly 1 survivor: after_dedup == 1, dropped == 19, ES docs == 1 |

Notes:
- Consumer-group offsets are reset to latest between checks (committed offsets
  survive Alert Bridge restarts; overload leftovers would contaminate the next
  window).
- The runner waits for the startup VLM warmup to drain before zeroing the NIM
  stub counters. Cap assertions use the Alert Bridge gauges (pipeline-scoped);
  the stub's raw connection count also sees transport artifacts (client
  retries, warmup) and is reported as a diagnostic only.
- Start the injector only after Alert Bridge is up: the consumer joins with
  `auto_offset_reset=latest` and skips earlier messages.
