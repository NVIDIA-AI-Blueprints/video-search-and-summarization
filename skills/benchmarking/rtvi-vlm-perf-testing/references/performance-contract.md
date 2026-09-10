# RTVI VLM Performance Contract

Create a JSON plan before executing a benchmark. The plan must freeze:

- code commit, clean source identity, image digest, model revision, hardware, and precision;
- a `fresh_per_run` runtime, declared cache policy, and distinct output, scratch, and mutable-cache paths;
- media/prompt identity, token shape, counted load unit, source-identity policy, and session reuse;
- warmups, repetitions, metrics, scenarios, and the exact benchmark command.

Use `independent_live_stream` only when every counted stream has a distinct runtime source identity.
Use `shared_stream_subscriber` with `shared_across_requests` and `session_reuse=true` when the
count represents subscribers on one reused stream. Use `file_request` or `batch_request` with
`not_applicable` for non-live loads. Never compare capacity values across different load units.

For `measurement.claim=capacity_ceiling`, also freeze the highest-stable/first-unstable boundary,
stability window, minimum success rate, maximum p95 latency, zero cross-scenario residue,
admission controller and limits source, fatal markers, and observations from client, server,
engine, GPU, and cleanup.

## Validity gates

Mark each gate `pass`, `fail`, or `unknown`:

- identity: code, image, model, configuration, data, and hardware match;
- workload: media, prompt, token shape, rate/concurrency, and session policy match;
- stream semantics: counted units and source cardinality match the claim;
- measurement: warmup, windows, repetitions, metrics, and failure handling match;
- environment: competing load, runtime settings, and GPU state are controlled;
- freshness: the runtime was created from the frozen identity with isolated mutable paths;
- boundary: highest stable and first unstable points were observed for a ceiling claim;
- cleanup: owned services, children, telemetry, assets, and GPU allocations were closed.

Failed or unknown gates limit the conclusion. A prior result with a different contract is a
search seed, not a baseline.

Declare `workload.source_identity_count` for independent live streams. It must cover the initial
max-stream load or the largest requested concurrency level. For each scenario, use either
`concurrency_levels` or the max-stream fields (`initial_stream_count`, or `reference_maximum` plus
`offset`, and `add_stream_count`). The validator renders only the flags belonging to that mode.

Before a rerun, inspect and force-remove stale containers only through `scripts/container_guard.py`.
The destructive `--execute` mode requires the applicable launch/cleanup authorization and removes
only containers carrying the exact Compose-project or harness-run label. An exact-name container
without matching ownership is a conflict, not cleanup permission. Always allocate a new run ID and
new mutable paths after stale-container cleanup.

## One-command independent-stream canary

`scripts/canary_executor.py launch MANIFEST --execute` owns the deterministic remote sequence:
staging, preflight, fresh runtime, benchmark execution for the manifest stream count, evidence
finalization, and owned cleanup.
Its manifest embeds the validated plan above and additionally pins:

- `run_id`, SSH `host`, `expected_hostname`, `repo`, full `repo_commit`, `benchmark_python`,
  benchmark `config`, and `scenario`;
- service, MediaMTX, and FFmpeg image tags plus exact `sha256:` image IDs;
- `compose_env`, read-only `model_cache`, input `video` and `video_sha256`, `output_root`, and
  RTSP `public_host`;
- `gpu_index`, `gpu_uuid`, `ports` (`backend`, `rtsp`, `dcgm`, `node`), and positive `timeouts`
  (`ready`, `benchmark`).

The embedded plan must describe one, two, four, eight, or sixteen independent sources—or thirty-two with checksum-pinned object media—one scenario, and a single
`concurrency_levels` value equal to the source count. The executor starts a distinct publisher and
RTSP path for each source and rejects any iteration with source reuse, pool exhaustion, startup
errors, missing streams, or no fresh measurements for every stream. Its code/image identities and
exact run-owned output, scratch, and mutable cache paths must match the executor fields. Every rerun
needs a new `run_id`; the executor refuses an existing run directory and never restarts an old
container. The manifest contains no credentials.

Set top-level `"semantic_isolation": true` only with two, four, eight, sixteen, or object-backed thirty-two sources to add the cross-stream fidelity
gate. It uses pinned solid colors through eight sources and color plus SOLID/BORDER signatures at sixteen, starts
all caption requests together, requires three source-correct responses per stream, records the ID-to-source mapping and raw
synthetic captions in `evidence/semantic-isolation.json`, and verifies probe-stream deletion before
the normal performance measurement. The fixed prompt accepts a response only when it contains the
expected color token and does not contain any other expected color token.
For real object sources, supply `semantic_media` as a mapping from each safe source label to
`{"path": "<absolute-image-path>", "sha256": "<64-hex-digest>"}`. Its entry count must equal the
frozen source count. The executor verifies every digest, publishes each still image as an independent
RTSP stream, and accepts only the corresponding forced-choice object token. Set top-level
`"qualification_only": true` only with `semantic_media` to produce a fresh qualification evidence
bundle without running the benchmark; use a new run ID and the identical mapping with `false` for the
capacity run.
Thirty-two sources require `semantic_media`; the executor rejects synthetic or otherwise unqualified
32-source manifests before launch.
The launcher uses a short SSH launch followed by a separate terminal-status connection and returns
when terminal JSON is received, without depending on SSH EOF. It requires local `ssh` and `scp`; the target requires Python 3.9+, `tmux`, Docker Compose,
the NVIDIA CLI, `curl`, `timeout`, and the manifest's benchmark environment with pandas, requests,
and PyYAML. No additional package is introduced by the executor itself.

## Minimal plan shape

```json
{
  "schema_version": 1,
  "name": "rtvi-capacity-example",
  "benchmark_command": ["python3", "perf/benchmark/rtvi_perf_benchmark.py", "--config", "config.yaml"],
  "environment": {
    "code_commit": "<sha>",
    "container_digest": "sha256:<digest>",
    "runtime_policy": "fresh_per_run",
    "clean_source_identity": "<sha-or-bundle-hash>",
    "cache_policy": "empty",
    "model_revision": "<revision>",
    "hardware": "<gpu>",
    "precision": "<dtype>"
  },
  "paths": {"output": "<unique>", "scratch": "<unique>", "mutable_cache": "<unique>"},
  "workload": {
    "media": "<manifest-or-hash>",
    "prompt": "<prompt-hash>",
    "input_tokens": 2048,
    "output_tokens": 1,
    "load_unit": "independent_live_stream",
    "source_identity_policy": "unique_per_stream",
    "source_identity_count": 1,
    "session_reuse": false
  },
  "measurement": {"claim": "throughput", "warmup_runs": 1, "repetitions": 3, "metrics": ["p95_latency_ms"]},
  "scenarios": [{"name": "scenario-name", "initial_stream_count": 1, "add_stream_count": 1}]
}
```
