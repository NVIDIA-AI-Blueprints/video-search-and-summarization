# Evaluation V2: proposal for an independent 20-video test set ("test-fresh") — NOT APPROVED

**Status:** proposal only. No clip has been collected, labelled or sent to any model. Nothing here changes `split-v2` (25 dev / 55 test) or `ground-truth-v1.0`.

**Why:** the 55 held-out clips are held out from all Evaluation V2 tuning, but they were exposed during V1 ([v2-dataset-integrity.md](v2-dataset-integrity.md)). A small set that no person or model in the project has seen gives an unbiased robustness check of the C3 ranking.

## 1. Size and composition

- **Clips:** 20, 4 per incident type (animal attack, assault, burglary, explosion, road accident).
- **Durations:** spread like the existing set (median about 40 s; at least 4 clips over 60 s, at least 4 of 30 s or less).
- **Out of scope:** no out-of-taxonomy clips in this round; that handling stays as it is.

## 2. Sourcing

**Pool:** the same two kinds of source as the existing 80 clips.
- The anomaly-video library already used for 56 clips (R2 folders `anomaly/<category>`, files named `*_x264`): its unused videos per category.
- New demo-style recordings, matching the 24 `demo` clips, for categories the library lacks enough of. Animal attacks are thin there.

**Exclusion:** before anyone watches a candidate, remove:
- every file already in `dataset-manifest-v1` (SHA-256 and R2 ETag);
- any file sharing a source video with an existing clip (same base name or event);
- near-duplicates (a perceptual hash on 1 fps frames, flagged for a person to decide).

**Selection:** a team member who did **not** write or tune prompts picks candidates by category and duration only, from file listings and metadata, before watching. Rejected candidates are logged with a reason.

**Licence:** only sources the existing set already uses, or recordings the team owns.

## 3. Labelling (four people)

- **Protocol:** the same as `ground-truth-v1.0`: the same schema, labelling workbook and validators (`eval_gt_schema`), and each clip labelled independently by **4 labellers**.
- **Adjudication:** by the team, as for v1.0.
- **No AI assistance:** the Claude-assisted correction step is **not** used, so the set stays independent of the model family that adjudicated v1.0 and generated the V2 references.

**Workload:**

| Item | Estimate |
|---|---|
| Labelling | About 45–75 min per clip per labeller (clips average about 1 min; longer clips take more). 20 clips × 4 labellers ≈ **15–25 person-hours** |
| Adjudication | About 15 min per clip ≈ **5 hours** (2 people) |
| Sourcing and exclusion checks | About **3–4 hours** |
| **Total** | **About 25–35 person-hours**, over about one week with 4 people working in parallel |

## 4. Isolation (how the set stays unseen)

- **Storage:** clips go under a new private prefix and a new manifest (`dataset-manifest-fresh-v1`), with a split file containing only `test-fresh`. They are not added to `split-v2`.
- **Registration and freeze:** the set is registered in the dataset registry with hashes and frozen as `ground-truth-fresh-v1` **before any model call touches it**.
- **Use:** no prompt, probe, pilot, diagnostic or reference generation may use these clips. The executor's existing rule (pilots run on dev only) is extended so a `test-fresh` clip can be run only by the frozen C3 configuration, under a C3 approval.
- **Labellers** do not run models on the clips and do not see model outputs until after C3.

## 5. Inference (run once, in C3, with the frozen configuration)

| Track | Runs | Tokens (estimated from development use) | Paid |
|---|---|---|---|
| Track A: 5 models × 2 repeats | 200 | about 2.5 M (Gemma about 6.7 k and Qwen about 15 k per call; NVIDIA models via Switchyard) | OpenRouter part (Gemma, Qwen: 80 runs) **< $0.20** |
| Track B: Nano, Super × 1 | 40 | about 2.1 M | Switchyard: no charge to the project |
| Judge (primary) | about 150 pairs | about 70 k | **< $0.02** |
| **Total** | 240 runs + judge | **about 4.7 M** | **< $0.25** |

## 6. Reporting

The fresh set is reported **beside** the 55-clip results, never pooled with them, as a robustness check: "the ranking and the main differences hold on clips no part of the project had seen". With 20 clips its intervals are wide, so it supports direction and ranking, not precise levels.

## 7. Decisions needed from the owner

1. Approve sourcing and labelling: four labellers, about 25–35 person-hours.
2. Name the person who selects clips, who must not have written or tuned prompts.
3. Approve the C3 inference above when the C2 package is approved.
