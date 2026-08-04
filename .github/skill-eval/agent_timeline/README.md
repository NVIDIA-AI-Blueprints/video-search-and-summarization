# Agent Hardware Timeline

This prototype correlates skill-eval agent steps and tool calls with the GPU
telemetry introduced by PR #1516 and aggregate CPU/RAM telemetry sampled over
the same Harbor invocation.

One sanitized canonical model produces four files under
`<results-root>/timeline/<invocation>/`:

- `timeline.perfetto.json` — Chrome Trace Event JSON, loadable in
  `https://ui.perfetto.dev`;
- `timeline.html` — a self-contained, dependency-free timeline for direct
  artifact viewing;
- `timeline.otlp.jsonl` — one OTLP trace export request and one OTLP metrics
  export request, each on its own line;
- `timeline.json` — the canonical model used to compare and refine the three
  representations.

The GPU, system, and agent clocks are normalized to Unix microseconds. GPU and
system samples are aligned to the coordinator's invocation start, avoiding
remote clock and timezone skew (`nvidia-smi` emits timezone-free wall-clock
values). Every hardware sample is annotated with the active agent step when one
spans that timestamp.

## Collection boundary

The system sampler reads fixed aggregate numeric counters from `/proc/stat`,
`/proc/meminfo`, and `/proc/loadavg`. It does not inspect processes, command
lines, environments, containers, network payloads, or files.

The full trajectory is an ephemeral input. After all sanitized outputs are
written, its per-trial `agent/` directory is removed before the trial is copied
to the persistent Harbor viewer. Published timeline artifacts use an allowlist:

- step timing and source (`agent`/`user`);
- tool name;
- for shell calls, known executable names only;
- aggregate numeric GPU, CPU, load, RAM, and swap metrics;
- non-secret run/spec/platform identity.

Messages, observations, tool arguments, raw commands, tool-call IDs, file
contents, and environment values are never copied. The workflow continues to
exclude each trial's `agent/` directory from public artifacts.

Both samplers are default-on, hard-bounded by the Harbor timeout, and
best-effort. Set `EVAL_GPU_TRACE=0` or `EVAL_SYSTEM_TRACE=0` to disable one.
Telemetry startup, collection, rendering, and write failures never alter the
evaluation result.
