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

## Multi-core scaling suite

`run_multiprocess_scaling.sh` covers `alert_agent.processes` — whether one
instance uses more than the ~1 core a single GIL-bound process can reach.

```bash
./run_multiprocess_scaling.sh                    # full suite
./run_multiprocess_scaling.sh --test TS-031      # single check
./run_multiprocess_scaling.sh --processes 8 --partitions 16
```

| Check | Setup | Pass criteria |
|---|---|---|
| TS-030 | rate ramp (10/20/40/80/160 msg/s), NIM stub 0.2s, `max_vlm_concurrent=60`, 1 process vs N. Each step reports `completed=N/s`, the throughput actually achieved — not the injected rate, which stops being a result once a configuration saturates | at the first rate where the single process inflates, N processes are still flat and faster; and somewhere in the ramp N processes exceed one core |
| TS-031 | N processes, `SIGKILL` one child | the supervisor logs the exit and stops the instance: every remaining child is reaped and the parent exits, leaving no orphan holding a consumer-group slot |
| TS-032 | N processes, 60 msg/s overload, `batch_commit` off then on | no shortfall against the produced count in either mode (batched commit may add duplicates, never losses) |
| TS-033 | N processes, `SIGKILL` one child mid-flight, `batch_commit` off then on | `batch_commit: false` never replays, and nothing is persisted that was never admitted. Loss counts are reported, not gated — `alert_bridge_events_after_dedup_total` counts admission to dispatch, not completion, and one dead child now stops the instance, so in-flight work in *every* child is lost rather than only the dead one's |
| TS-036 | N processes under load, a second instance joins the same group with more processes, then leaves | the drain runs, the assignment gauge and readiness both drop, `/health` and `/ready` both report 503 while the fleet is short, and all of them recover. Needs more group members than partitions on a topic, or every worker keeps one and nothing drops |
| TS-035 | N processes, at rest | the fleet gauges agree with reality: configured, alive and ready all equal the process count, and the partitions held across the instance equal the partitions that exist |
| TS-034 | N processes, clean start | the prompt store is seeded exactly once and before the first child starts; every child is given an assignment; the readiness line trails the last child rather than the fork |

Sizing the run matters more than in the suite above:

- **Partitions ≥ processes.** Effective parallelism is
  `min(processes, partition_count)`; the runner grows `mdx-incidents` to
  `--partitions` (default 8) and aborts if the topic cannot reach it. At one
  partition the ramp shows nothing at all.
- **The offered rate has to reach the CPU ceiling.** Keep the stub delay low
  and the cap high so `max_vlm_concurrent / VLM_latency` sits far above every
  rate tested (default `60 / 0.2 = 300/s`); otherwise the semaphore binds
  first and single- and multi-process results are identical.
- **CPU comes from `/proc` deltas** (`process_tree_cpu.py`), not `ps %cpu`,
  which reports a lifetime average. 100% = one fully busy core. Linux only.
  The first `CPU_SKIP_SECONDS` (default 8) of samples are discarded — without
  that, interpreter and child startup produces a peak at the *lowest* offered
  rate that has nothing to do with steady state. Gates use `cpu_avg`;
  `cpu_max` is a diagnostic.
- **TS-030 checks its two halves at different rates, on purpose.** "Stays
  flat" only holds below the knee; "uses more than one core" only appears as
  the knee is approached. Gating both at the top rate made the test
  unsatisfiable at *every* rate — measured: at 80 msg/s four processes were
  still under one core (94.7%), and by 160 msg/s where they reached 182.6%
  their latency had already left the flat band. The gate now finds the first
  rate at which the single process inflates, requires N processes to be flat
  and faster *there*, and separately requires them to exceed one core somewhere
  in the ramp. It never gates on a CPU multiple: N processes can do the same
  work for less total CPU than a saturated single process.
- **Pipeline children are identified from the supervisor's log**, not
  `pgrep -f`: `fork` leaves `argv` unchanged, so the parent and the FastAPI
  child match the same pattern, and picking a victim by position could kill
  FastAPI instead of a pipeline child.
- **Readiness counts `Pipeline process N ready`, one per child.** The parent
  prints `Starting anomaly processing loop...` before it forks, and children
  contend on Elasticsearch inside their constructor — measured at 20–40 s for
  three children against the sim. Gating on the parent alone starts the
  injector before the consumers have joined, and with
  `auto_offset_reset=latest` those messages are never seen, which shows up as
  a bogus message-loss failure rather than a timing bug.
- **The NIM stub is killed by pattern and its port verified.** A stub left
  from an earlier run keeps port 18081, the replacement dies with
  `EADDRINUSE`, and every request is silently served at the *old* delay.
  TS-030 additionally asserts the observed baseline latency tracks the
  configured delay before trusting any number in the run.
- `stop_ab` kills by process name: children run with `daemon=False` and
  outlive a hard-killed parent, keeping consumer-group membership and blocking
  the next offset reset.
- **Treat the rates here as an upper bound.** The stubs return small payloads
  over loopback with no real protobuf decode, Elasticsearch round-trip or VST
  I/O, so the per-process CPU ceiling they show is higher than a real
  deployment's. Re-measure against real dependencies before sizing anything.
- **The simulators saturate before Alert Bridge does, and TS-030 now refuses
  to report a number when they have.** `elastic_sim` and `vst_sim` are single
  Flask processes, so each is GIL-bound to about one core — the very limit this
  feature removes from the product. Measured at 8 processes and a high offered
  rate: `elastic_sim` 108.3% while Alert Bridge sat at ~322% of the 1600%
  available on a 16-core host, and Alert Bridge CPU stayed flat at ~322% while
  the offered rate went from 320 to 1280 msg/s. Everything above that point
  describes the harness. The suite samples the simulators alongside Alert
  Bridge every ramp point. It fails only when a simulator is saturated at the
  *break rate*, where the latency comparison happens; saturation at higher
  rates is printed as `sims@top` but does not fail the check, because a
  throttled simulator makes Alert Bridge use less CPU and so cannot turn the
  "crosses one core" half into a false pass. The threshold is
  `SIM_SATURATED_PCT` (default 85). Raising the ceiling means
  giving the simulators more than one process each; until then the "≥4×
  throughput" acceptance criterion cannot be measured here.
- **Start the ramp below the single-process knee.** Both baseline checks assume
  the first rate is one where a single process is still idle. Starting at or
  above its knee produces two misleading verdicts — "never inflated", whose
  remedy is to *lower* the first rate rather than raise it, and a stub-delay
  mismatch that sends you hunting a stale NIM stub that is working fine. Keep a
  low anchor in `RAMP_RATES` when testing high process counts, for example
  `"10 80 160 320 640 1280"`.

Notes:
- **Each leg gets a consumer group unique to the run**, rather than an offset
  reset. The reset silently fails while the group still has active members, and
  the leg then inherits a backlog — which lands in the first sample of the
  ramp, the very baseline the flat-latency gate divides by. Note the group must
  be unique per *run*, not merely per leg: a leg counter restarts at 1 every
  invocation, so a second run on the same broker rejoins groups that already
  carry committed offsets and resumes from them. `auto_offset_reset=latest`
  only applies to a group that is genuinely new. Measured when this was wrong:
  the first ramp point read 0.641 s / 100.7% CPU instead of 0.206 s / 14%.
  Leftover groups are also purged at startup, which Kafka refuses to do for any
  group that still has members.
- The runner waits for the startup VLM warmup to drain before zeroing the NIM
  stub counters. Cap assertions use the Alert Bridge gauges (pipeline-scoped);
  the stub's raw connection count also sees transport artifacts (client
  retries, warmup) and is reported as a diagnostic only.
- Start the injector only after Alert Bridge is up: the consumer joins with
  `auto_offset_reset=latest` and skips earlier messages.
- The venv is put on `PATH` **before** the simulators are started, from
  `AB_VENV`, `venv/` or `.venv/`, and both runners now abort with a clear
  message when the interpreter on `PATH` lacks the test dependencies. They are
  launched with whatever `python3` is on `PATH`, and the system interpreter
  usually lacks Flask. A simulator that dies on import used to surface much
  later as a misleading "Elasticsearch connection refused" at Alert Bridge
  startup, so both runners now wait on ports 9200/30888/8080 and tail the
  simulator log on timeout instead of sleeping.
- The simulators are background jobs of the invoking shell and die with it —
  including when a `screen`/`tmux` session ends. Re-running with
  `--skip-setup` against a finished session finds every port closed; either
  keep the session alive or drop `--skip-setup`.
