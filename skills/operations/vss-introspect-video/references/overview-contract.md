# Experiment D: fixed-count whole-video overview

The normal plan, memory preparation, targeted prompts, closure rules and final
check stay unchanged. After memory binding, run:

```bash
python3 "$WORKFLOW" overview --ledger "$RUN_DIR/ledger.json" --sensor "$SENSOR"
```

Code resolves the complete contiguous recording from VIOS, not an agent-selected
window. It requests 48 uniform frames in ONE VLM call. This consumes one of the
eight normal calls and no targeted round. It runs even when memory appears
sufficient. No quality retry or model fallback is permitted.

Read `overview.json`. Its fixed prompt requests an approximate chronological
event map, not an answer. Use it to locate the requested occurrence and select
later inspection windows. Sparse frames cannot establish exhaustive counts or
prove that an event never occurred.

Create `overview-assessments.json`, one assessment per planned claim, using the
same compact assessment schema as a normal batch. Task IDs are
`inspect-<claim-id>-r1`, e.g. `inspect-claim-direction-r1`; they are stored in
overview artifacts, NOT normal round artifacts. `attempt_index: 0` refers to the
single overview reply. Quotes must be verbatim. Assess alongside already-bound
memory; preserve missing prerequisites, ambiguity and incomplete temporal coverage.
If the call failed, use no observations, false sufficiency flags and explicit gaps.

```bash
python3 "$WORKFLOW" merge-overview --ledger "$RUN_DIR/ledger.json" \
  --assessments "$RUN_DIR/overview-assessments.json"
```

This writes a canonical revision without consuming another call or round. The
usual two targeted batches may then use at most seven remaining normal calls.
If sufficient, skip targeted batches; always perform the reserved final check.
Repeated overview/merge commands reuse receipts. An interrupted call is charged
once and not silently repeated. Do not manually edit the canonical ledger.

## Sampling preflight and interpretation

- The CLI route requests `--max-frames 48 --chunk-duration 0` with NO `--fps`;
  backend chunking must not turn the overview into multiple model calls. A locked
  nonzero chunk duration is rejected before inference. Supported fixed-count
  routes are RT-VLM and Cosmos NIM. This does not change the configured model.
- A locked FPS policy would defeat that request; code rejects it before inference.
  Prepare a common policy allowing explicit per-call sampling for ALL arms before
  the cohort. Do not relax locks or switch endpoints mid-run.
- Other CLI backends that cannot guarantee this fixed-count request fail preflight.
  Do not reinterpret a plain OpenAI video URL request as 48 submitted images.
- If the existing remote-image route is already configured, code extracts the full
  clip, measures its container duration, samples uniformly across it and sends at
  most 48 images. It retains the images and their hashes in `overview-media/`.
- CLI receipts retain `frames_sent: null` because actual images are not exposed.
  Remote receipts record image count and a nominal sampling grid, NOT measured
  source PTS. Neither path claims exact source event timing without that telemetry.
- This is a 48-frame architectural test, not an exact reproduction of the historical
  64-frame baseline. The normal FPS-driven inspections retain their existing
  backend-cap limitations; this arm does not silently fix or alter those calls.
