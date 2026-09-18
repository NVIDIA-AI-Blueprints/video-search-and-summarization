# Performance Bubble Playbook

Use this reference only after a reproducible fixed-load signal exists. Metric and
module names vary by vLLM revision; inspect the checked-out source and enumerate the
live `/metrics` endpoint instead of assuming remembered names.

## Evidence Matrix

| Bubble class | Correlated evidence | Shortest discriminating experiment | Typical fix area |
|---|---|---|---|
| Offered-load gap | GPU idle, scheduler queue empty, no eligible requests | Replay the same shape at higher fixed concurrency | Benchmark/load generator |
| Frontend starvation | Eligible requests wait before engine submission; CPU/process pool saturated | Preprocess recorded inputs ahead of time or raise an existing worker limit once | Processor workers, CPU affinity, media pipeline |
| Media/decode starvation | Chunk readiness gaps precede an empty engine queue; NVDEC or network cadence is limiting | Feed cached decoded tensors with identical timestamps and shapes | RTSP/NVDEC/frame handoff |
| Host-device transfer | Long copies or synchronization intervals precede vision kernels | Compare the existing pinned/asynchronous tensor path against the pageable/copying path | Tensor IPC, pinned buffers, streams |
| Serialized vision | Queue is nonempty but one request's vision encoder runs at a time; visual batch size stays one | Replay the same fixed visual shapes and request set with batched preprocessing or encoding enabled; require output and multimodal token-position parity | Multimodal processor/model runner |
| Dynamic shape or graph gap | CPU launch gaps and repeated graph capture or compilation surround shape changes | Bucket to one existing supported shape and compare graph reuse | Shape policy, CUDA graph configuration |
| Scheduler underbatching | Queue is nonempty while active sequences, scheduled tokens, or batch size remain below limits | Raise one existing scheduler limit within memory headroom | Scheduler/configuration |
| Prefill monopolization | Long multimodal prefill delays decode steps and ITL spikes | Compare chunked or interleaved prefill at the same request set | Chunked prefill/scheduler |
| Decode underbatching | Many short decode launches, low sequences per step, queue nonempty | Fix OSL and EOS policy, then compare active sequences and tokens per step | Continuous batching/sampler/scheduler |
| KV pressure | High KV occupancy, preemption, recompute, or eviction with latency sawtooth | Reduce one load or sequence dimension, or increase the existing cache budget | KV sizing, admission, prefix policy |
| CPU sampler/output stall | GPU kernels finish but the next step waits on sampling, detokenization, serialization, or client backpressure | Disable only the suspected output feature in a controlled canary | Sampler/output path |
| Distributed bubble | Rank gaps align with collectives or stragglers | Compare rank timelines and collective duration at the same batch | Parallel topology/collectives |
| Kernel inefficiency | GPU remains busy but occupancy, achieved bandwidth, or tensor-core use is poor | Profile one stable shape after launch, scheduling, and transfer gaps are ruled out | Kernel selection/fusion/quantization |

## Minimal Capture Set

Preserve:

- exact launch command and runtime configuration;
- a fixed request list with arrival times and expected output tokens;
- vLLM metrics for queue, running requests, KV usage, preemption, and available
  iteration or batch behavior;
- client and server phase timestamps with request identifiers;
- scenario-scoped GPU telemetry;
- a short Nsight Systems trace with CUDA, NVTX, OS runtime, and relevant library
  traces;
- output, error, and cleanup accounting.

Prefer a 10-30 second steady-state trace around the known knee. Start with existing
NVTX ranges and add only the boundary ranges needed to distinguish hypotheses.

## Derived Measurements

Report these when the underlying evidence supports them:

- `eligible_work_bubble_ratio = GPU idle time while eligible work is queued / steady-state time`;
- GPU-busy time split across vision, prefill, decode, copies, collectives, and other kernels;
- p50/p95/p99 idle-gap duration;
- batch-size and scheduled-token distributions by prefill and decode;
- queued, running, completed, rejected, cancelled, and failed requests;
- KV occupancy and preemption or recompute counts;
- visual items or visual tokens per encoder invocation;
- TTFT and ITL distributions.

Do not estimate phase shares from `nvidia-smi` utilization alone. Sampling can miss
bursty kernels, and high utilization can still hide poor kernel efficiency.

## Comparison Rules

- Compare fixed concurrency before maximum capacity. Capacity-boundary latency values
  occur at different loads and are not direct latency comparisons.
- Keep output budgets fixed and verify actual generated token counts. `max_tokens=100`
  with natural EOS is not an OSL-100 workload.
- Keep visual tensors, frame ordering, resolution, temporal metadata, and processor
  behavior matched across runtimes.
- Separate vision/prefill and decode conclusions. OSL-1 is usually more sensitive to
  media and prefill; the incremental OSL-100 gap can help isolate decode, but only a
  phase trace proves it.
- Treat a fallback kernel as a hypothesis until the trace shows that it occupies
  recoverable critical-path time.

## Fix Acceptance

Accept a fix only when:

1. the correlated target gap shrinks in the unprofiled A/B run;
2. successful work and output parity remain unchanged, including multimodal visual-token count, ordering, and position assignments;
3. TTFT, ITL, or throughput improves beyond the declared noise band;
4. memory, preemption, errors, and cleanup do not regress beyond thresholds;
5. the capacity boundary improves when capacity was the stated goal.
