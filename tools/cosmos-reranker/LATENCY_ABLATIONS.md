# CR3 reranker latency ablations

Measured, incremental optimizations for the Cosmos 3 Nano rerank step, plus the
runbook for ablating them against a real stage-1 run. All numbers: fine-tuned
CR3 (merged fp16 vision + language LoRA), vLLM 0.23, 1 FPS, 32-frame cap,
20 candidates/query, 80-clip physicalAI subset (1080p, 5 s clips),
L40S + 4-vCPU host. Every optimization below preserves the exact bytes the
model consumes (or was rejected for changing them).

## Where the time went (measured, per query)

| Stage | Upstream | Cause |
| --- | --- | --- |
| Frame decode (cv2) | ~5.2 s | every `CAP_PROP_POS_FRAMES` seek re-decodes from the previous keyframe (~1 s per candidate at 1080p) |
| JPEG q95 encode + base64 | ~2.2 s | 315 KB/frame payloads, parallel pool |
| vLLM frontend: serial PIL re-decode of our JPEGs | ~0.6 s (erratic to 6 s) | vLLM decodes every `video/jpeg` base64 frame one at a time |
| Engine: fp16 prefill + 1-token readout | ~0.3–0.8 s | 2–4 engine calls per query when sampled fps differs |
| HF video processor resize (frontend, CPU) | ~1.5 s | resizes 80 x 1080p frames; already multi-core via torch intra-op |

## Variants (same seeded candidates throughout)

| Variant | Change | Mean/query | Accuracy gate |
| --- | --- | --- | --- |
| V0 upstream | seek-decode → JPEG → base64 → vLLM | 9.6–10.2 s | reference |
| V1 sequential decode | one grab/retrieve pass, bit-exact frames | 6.8 s | identical |
| V3 pre-decode handoff | PIL-decode in our pool, hand vLLM identical arrays | 5.9 s | engine noise floor |
| V4 chunked CPU/GPU overlap | prep next chunk while engine scores this one | 5.3 s | noise floor |
| V6 warm frame store | frames extracted once, served from RAM (320 frames, 22 s build) | 2.36 s | noise floor |
| V7 parallel chat preprocessing | threads inside vLLM's frontend | 2.5 s | no gain: resize already saturates cores |
| rejected: raw frames (JPEG removal) | skip JPEG entirely | — | rankings flip (max Δ0.48): needs mAP eval |

Accuracy gate = max |Δ score| vs the same-path rerun noise floor. vLLM fp16
batching is nondeterministic: identical reruns differ by |Δ| ≤ ~0.03 and
reorder near-ties, so "no degradation" means "within this floor", not bitwise.
The one variant that exceeded it (raw frames) is rejected without a full mAP
evaluation.

## Runbook: ablate against a real stage-1 run

One completed `run_raw_clip_eval.py` run dir; one model load per arm; the
same candidates everywhere. Each arm writes `summary.json` (with
`rerank_latency`) and `comparison.json` (mAP per variant) under
`<run-dir>/rerank/<name>/fps-1_frames-32/`.

```bash
RERANK_PY=tools/cosmos-reranker/.venv-eval/bin/python
SCRIPTS=skills/benchmarking/benchmark-video-search/scripts
COMMON="--run-dir $WORK/results --dataset-root $DATASET_ROOT \
        --ingestion-manifest $WORK/ingestion.json --rerank-depth 20 \
        --fps 1 --max-frames 32"

# A0 stock path (baseline latency + mAP)
"$RERANK_PY" "$SCRIPTS/rerank_raw_clip_eval.py" $COMMON --output-name a0-stock

# A1 sequential decode is now the package default (bit-exact); V1 == A0 pipeline
#    with the decode algorithm swapped. To measure it in isolation, run A0 from
#    the parent commit and A1 from this branch over the same run dir.

# A2 + pre-decode handoff (removes vLLM's serial PIL re-decode)
"$RERANK_PY" "$SCRIPTS/rerank_raw_clip_eval.py" $COMMON --output-name a2-handoff \
    --predecode-handoff

# A3 + warm frame store (write-through: misses populate it; rerun = warm)
"$RERANK_PY" "$SCRIPTS/rerank_raw_clip_eval.py" $COMMON --output-name a3-store \
    --predecode-handoff --frame-store $WORK/frame-store
# A3-repeat: rerun with the same output dir name removed first; identical args.
#   This is the noise-floor control for both latency and mAP.

# Optional pre-population (deployment shape: ingest-time extraction)
tools/cosmos-reranker/.venv-eval/bin/python -m cosmos_reranker.reranking.frame_store build \
    --store $WORK/frame-store --videos $DATASET_ROOT/clips --fps 1 --max-frames 32 \
    --windows $WORK/windows.jsonl
```

Read `rerank_latency` (mean/median/p95) and mAP for `reranked` vs
`stage1-pool` from each arm's outputs. Pass criteria:

1. latency drops monotonically A0 → A2 → A3 (warm) as measured here;
2. mAP deltas of A2/A3 stay within the A3-repeat band;
3. report frame-store build cost separately (amortized, or ingest-time).

Keep the environment identical across arms (GPU, `gpu_memory_utilization`,
`VLLM_USE_FLASHINFER_SAMPLER=0` on hosts without nvcc). Use >= 50–100 queries
for stable latency numbers.

## Novel ablations still worth running

Ordered by expected value; the first two need an accuracy gate (they change
model inputs or weights).

1. **FP8 checkpoint** (ModelOpt, like the search profile's RT-VLM build):
   halves the ~0.8 s engine time; gate with the same mAP ablation.
2. **Processor-output cache**: cache the HF processor's resized output per
   (clip, window); eliminates the ~1.5 s frontend resize on repeat windows.
   Pixels identical only if the resize is replicated exactly — verify against
   the rerun noise floor like V3.
3. **Uniform-window single engine call**: real ES chunk hits are uniform, so
   all 20 candidates share one sampled fps and the engine sees one call
   instead of 2–4 (the 5 s test clips already show this).
4. **Chunked CPU/GPU overlap** (V4): worth ~10–20% once media is cheap; the
   probes in this directory contain a working implementation.
5. **Cross-query batching** (`rerank_batch`): does not reduce single-query
   latency, but roughly doubles throughput when multiple queries can be
   scored together — measure it as a throughput ablation, not a latency one.
6. **NVFP4 on Blackwell (RTX PRO 6000)**: the search profile already runs
   CR3 Super NVFP4 there; a quantized reranker checkpoint is the natural
   follow-up after FP8 passes the accuracy gate.

## Probe scripts kept in this directory

`cr3_opt_probe.py` (V0/V1/V3/V4 matrix + parity gates), `cr3_v6_probe.py`
(frame store + uniform windows + V7), `cr3_phase_probe.py` /
`cr3_segment_probe.py` / `cr3_profile_probe.py` (stage attribution),
`cr3_latency_probe.py` (branch-style single-number latency probe),
`cr3_stream_merge.py` + `cr3_export_vlm.py` (checkpoint rebuild utilities).
All are standalone and safe to delete once the flags above cover the ground.
