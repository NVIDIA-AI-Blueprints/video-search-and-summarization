# RT-CV / RT-VLM IPC A/B Benchmark

Use this mode to compare direct RT-VLM decoding with one RT-CV decode feeding RT-VLM through
`nvunixfdsink`/`nvunixfdsrc`. The comparison is valid only when `decode_path` is the sole intended
difference.

## Frozen contract

Keep these identical across arms: code commit, pinned CV/VLM image digests, model revision,
hardware and GPU UUID, media and prompt hashes, cache settings, chunk/request duration, request
count, frame-selection policy, generation settings, warmup, and repetition count. Each arm and
repetition requires a fresh runtime and unique output directory.

The IPC arm must use:

- one RT-CV RTSP connection and one Unix socket per source;
- a bind-mounted socket directory, not a shared host IPC namespace;
- RT-VLM Docker `ipc: private`;
- explicit runtime CLI activation, normally through
  `RTVI_EXTRA_ARGS="--ipc-frame-copy --ipc-socket-dir /run/rtvi-ipc --ipc-socket-template nvds_ipc_{camera_id}.sock"`;
- zero RT-VLM connections to the RTSP source.

Environment variables named `RTVI_IPC_FRAME_COPY` or `RTVI_IPC_SOCKET_DIR` are not activation proof.
Capture the process command line and the `Using IPC decoded-frame source` log marker.

The direct arm must connect RT-VLM to the same RTSP source and prove at least one RT-VLM RTSP
connection. Do not run RT-CV in the direct arm unless it is needed for an unrelated invariant.

## Plan interface

```bash
python3 scripts/ipc_ab_benchmark.py validate ipc-ab-plan.json
python3 scripts/ipc_ab_benchmark.py render ipc-ab-plan.json
python3 scripts/ipc_ab_benchmark.py run ipc-ab-plan.json --execute
```

`render` and `run` without `--execute` are read-only. Commands may use `{arm}`, `{repetition}`, and
`{output_dir}` placeholders. The runner alternates arm order by repetition by default to reduce
thermal and temporal bias. Do not put credentials in the plan. Secret-like environment keys are
rejected; authenticate the host before launch and let the arm command inherit its environment.

Minimal plan:

```json
{
  "schema_version": 1,
  "benchmark_id": "cr3-h100-ipc-ab",
  "changed_variable": "decode_path",
  "common": {
    "code_commit": "<sha>",
    "cv_image_digest": "sha256:<digest>",
    "vlm_image_digest": "sha256:<digest>",
    "model_revision": "<model>",
    "hardware": "H100",
    "gpu_uuid": "GPU-...",
    "media_sha256": "<64 hex>",
    "prompt_sha256": "<64 hex>",
    "cache_policy": "mm-shm-1gb",
    "chunk_duration_seconds": 2,
    "request_duration_seconds": 120,
    "request_count": 4,
    "runtime_policy": "fresh_per_arm_repetition"
  },
  "execution": {
    "output_root": "/absolute/unique/output",
    "repetitions": 3,
    "arm_order": ["direct", "ipc"],
    "alternate_order": true
  },
  "arms": {
    "direct": {
      "decode_path": "direct_rtsp",
      "command": ["python3", "run_arm.py", "--arm", "{arm}", "--output", "{output_dir}"]
    },
    "ipc": {
      "decode_path": "rt_cv_nvunixfd",
      "docker_ipc_mode": "private",
      "socket_transport": "bind_mount",
      "command": ["python3", "run_arm.py", "--arm", "{arm}", "--output", "{output_dir}"]
    }
  }
}
```

## Arm result contract

Each command must write `{output_dir}/arm-result.json` with:

```json
{
  "schema_version": 1,
  "arm": "ipc",
  "runtime_fresh": true,
  "identity": {"<every common plan field>": "<exact value>"},
  "topology": {
    "decode_path": "rt_cv_nvunixfd",
    "cv_rtsp_connections": 1,
    "vlm_rtsp_connections": 0,
    "ipc_socket_count": 1,
    "ipc_cli_flags_present": true,
    "ipc_source_log_marker_count": 1
  },
  "outcomes": {
    "offered_requests": 4,
    "successful_requests": 4,
    "failed_requests": 0,
    "nonempty_chunks": 200,
    "empty_chunks": 0
  },
  "throughput_chunks_per_second": 1.7,
  "latency_samples_ms": {
    "chunk_latency_ms": [2500.0],
    "decode_latency_ms": [1800.0],
    "vlm_latency_ms": [650.0]
  },
  "resource_samples": {
    "gpu_util_pct": [95.0],
    "gpu_memory_mib": [64000.0],
    "decoder_util_pct": [2.0],
    "cpu_pct": [115.0],
    "container_memory_mib": [5800.0],
    "power_w": [650.0]
  },
  "cleanup": {
    "containers_remaining": 0,
    "sockets_remaining": 0,
    "host_shm_objects_remaining": 0
  },
  "fatal_markers": []
}
```

The runner rejects any identity mismatch, failed/empty work, IPC RTSP fallback, missing IPC socket,
missing launched CLI or runtime-source proof, fatal marker, or cleanup residue before calculating
deltas. Telemetry that was not collected remains blank in every report; it is never converted to a
measured zero.

## Reporting existing artifacts

```bash
python3 scripts/ipc_ab_benchmark.py report ipc-ab-plan.json \
  --result direct=/path/direct-rep-1/arm-result.json \
  --result ipc=/path/ipc-rep-1/arm-result.json \
  --output-dir /path/report
```

Supply one result per configured repetition for each arm. Outputs are:

- `ipc-ab-summary.json`
- `ipc-ab-comparison.tsv`
- `ipc-ab-comparison.md`
- `ipc-ab-comparison.xlsx`

Interpret percentage differences as `(IPC - direct) / direct * 100`. Higher throughput is better;
lower latency, memory, CPU, and power are better. Do not summarize an invalid arm as a regression or
improvement.
