# Evaluation V2: dataset integrity and how to describe the held-out benchmark

## 1. The split (unchanged)

- **Clips and split:** `dataset-manifest-v1` holds 80 clips. `split-v2` divides them into **25 development** clips (5 per incident type, chosen with `random.Random(42)`; they were the earlier few-shot clips) and **55 test** clips (Animal 10, Assault 10, Burglary 13, Explosion 10, Road Accident 12).
- **Ground truth:** `ground-truth-v1.0`, frozen 2026-10-28.
- **Verification:** all hashes verify through the dataset registry.
- **Policy:** the split and the ground truth stay as they are. Nothing here changes them.

## 2. Exposure of the 55 test clips (they are held out from V2 tuning, not unseen)

| Exposure | When | Source |
|---|---|---|
| The 2026-10-02 benchmark ran three models on all 55 test clips | V1, before the split was frozen | `docs/experiments/stage3-results.md` (D15) |
| Three test clips (Animal003, Assault018, Burglary005) shaped earlier prompt pilots and wording | V1 | Stage 3 §3; Stage 3 reports a 52-clip sensitivity analysis excluding them |
| The Claude-assisted GT correction step viewed frames of 38 clips from both halves | GT v1.0 adjudication | `ground_truth/` review and freeze records |
| The GT adjudicator, the reference generator and the reference validator are the same model family (Claude) | GT and V2 references | methodology v2.0 §3 |
| Evaluation V2 used **no** test clip: every probe, pilot, recovery, scoring choice and model choice used the 25 dev clips only | V2 | run manifests (`half: dev`); executor refuses a test clip in a pilot |

## 3. How to describe the benchmark (proposed wording)

> "Results on 55 held-out clips. They were not used for any Evaluation V2 tuning or model selection. They were exposed during earlier (V1) prompt development and in a 2026-10-02 benchmark, so they are **not unseen**: absolute scores may be optimistic, especially for the production prompt. Model comparisons on the same clips are less affected than absolute levels. A 52-clip sensitivity analysis excludes the three clips that shaped earlier prompts."

Every C3 table and slide that shows held-out numbers should carry a one-line version of this.

## 4. Rules from now on

- No further tuning, prompt change, diagnostic experiment, probe or model selection may use any of the 55 test clips. The executor already refuses test clips in pilots; probes accept only dev clips.
- Before C3, the C2 freeze package pins the split hash and records this statement in the pre-registration (§9 Disclosures).

## 5. Is an additional independent test set feasible?

**Option A, recommended if time allows: a small fresh test set ("test-fresh").**
- **Clips:** about 20 new clips, 4 per incident type, from the same public sources. Duplicates, near-duplicates and any clip already in the 80 are excluded by file hash and visual check.
- **Labelling:** the same protocol as `ground-truth-v1.0`.
  - Four labellers, then the same adjudication.
  - The AI-assisted correction is **not** used, so the set stays independent of the model family that made the V2 references.
  - Effort: about 1–1.5 h per clip-labeller pair, so roughly 20–30 person-hours.
- **Freezing and use:** freeze it as `ground-truth-fresh-v1` with its own split file and hashes, before any model sees it.
- **Inference:** run once, in C3, with the frozen configuration. Track A for five models × 2 repeats is 200 runs, about 2.5 M tokens (estimated from dev token use); Track B for Nano and Super is 40 runs. Paid inference (OpenRouter, Gemma and Qwen only) would be under $0.20; Switchyard is not charged to the project.
- **Reporting:** a robustness check next to the 55-clip results. With 20 clips, its intervals are wide, so it supports "the ranking holds on unseen clips" rather than precise levels.

**Option B: no new set.** Report the 55-clip results with the statement in §3 and the 52-clip sensitivity analysis.

**Decision needed from the owner:** A or B. If A, someone needs to source the clips and four labellers are needed. A does not delay C3 on the 55 clips; the fresh set can run after it.
