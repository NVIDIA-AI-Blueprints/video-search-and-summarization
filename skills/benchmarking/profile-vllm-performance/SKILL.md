---
name: profile-vllm-performance
description: Profile vLLM and RT-VLM inference to identify and remove GPU-idle gaps, underfilled batches, transfer stalls, serialized multimodal work, scheduler gaps, or KV pressure. Use this skill when GPU utilization is unexpectedly low or bursty, capacity trails another runtime, TTFT or ITL regresses, or multimodal prefill and decode appear serialized. Not for running a first benchmark without a reproducible fixed-load signal.
license: Apache-2.0
metadata:
  version: "1.0.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia rt-vlm vllm performance profiling gpu"
---

# Profile vLLM Performance

Find the dominant loss mechanism, prove it with a correlated timeline, make the
smallest causal change, and validate the result with a matched A/B run. A capacity
number or average GPU-utilization sample is not bubble attribution.

## Freeze The Comparison

Record the exact code, container, model revision, precision, hardware, scheduler
settings, workload shape, media, prompt, token budget, sampling, cache state,
concurrency, and success criteria. Prove semantic correctness before optimizing.
Do not compare runtimes when any of these dimensions differ unless that dimension is
the declared variable.

## Establish The Signal

1. Verify actual output-token counts, successful work, fresh-stream coverage,
   dropped chunks, cancellations, and cleanup. Early EOS or silent failure is not
   throughput.
2. Reproduce at fixed load below the knee, near the highest stable point, and just
   beyond it when practical. Keep capacity search separate from profiling.
3. Record TTFT, inter-token latency, throughput, queue delay, batch size, scheduled
   tokens, active sequences, preemptions, KV occupancy, GPU memory, and phase
   durations available in the checked-out version.
4. Use scenario-scoped DCGM or `nvidia-smi dmon` for the resource envelope and a
   short Nsight Systems or repository profiler capture for attribution. Profilers
   perturb timing; never use a profiled run as the capacity result.

Define a performance bubble as GPU-idle or low-occupancy time while eligible work is
queued. Idle time caused by insufficient offered load is not a runtime bubble.

## Correlate The Timeline

Use stable request, sequence, stream, and chunk identifiers across:

- media arrival and decode;
- CPU frame selection, resize, normalization, tokenization, and processor work;
- host-to-device transfer and synchronization;
- vision encoder and projector;
- text and visual prefill;
- scheduler admission, batch formation, KV allocation, preemption, and dispatch;
- decode, sampling, detokenization, and output delivery.

Capture only enough NVTX or structured timing to align these phases with CUDA
kernels, memory copies, CPU wakeups, and scheduler state. Avoid high-volume logging
in the measured path.

Read [references/bubble-playbook.md](references/bubble-playbook.md) for the evidence
matrix and the shortest discriminating experiment for each bubble class.

## Prove The Dominant Bubble

For each candidate, state the precise idle or under-occupancy interval, whether
eligible work was queued, the event immediately preceding it, its frequency and
share of measured time, and the observation that rules out the nearest competing
explanation. Rank candidates by recoverable critical-path time, not visual prominence
in one trace.

Do not infer a decode bottleneck from an OSL capacity gap alone. Compare matched
fixed-concurrency prefill and decode timelines plus tokens per scheduler step.

## Fix And Validate

Stop at the first rung that removes the proven bubble:

1. Correct benchmark or configuration defects such as early EOS, mismatched shapes,
   cache policy, scheduler limits, or insufficient offered load.
2. Tune an existing runtime knob supported by the checked-out version.
3. Reuse an existing batching, preprocessing, cache, CUDA-graph, tensor-IPC, or
   scheduler path that is configured incorrectly or bypassed.
4. Fix an integration boundary that serializes or copies work unnecessarily.
5. Change scheduling or model-runner code only when traces prove the existing path
   cannot express the required overlap.
6. Change a kernel only after ruling out launch, scheduling, shape, and data movement.

Change one causal variable at a time. Repeat the short trace, then run an unprofiled
matched A/B or A/B/B/A comparison at fixed concurrency. Rerun the capacity boundary
only when capacity is the claim. Preserve output parity, ordering, cancellation, KV
lifetime, and multimodal token and position invariants.

Report `confirmed`, `improved`, `inconclusive`, or `invalid comparison`, followed by
the dominant bubble, recoverable-time estimate, evidence, ruled-out alternatives,
fix, before/after distributions, artifact paths, remaining bottleneck, and smallest
next experiment.

Do not install profilers, launch costly GPU work, push changes, or mutate a shared
machine without authorization.
