# Evaluation V2 pre-registration record

> **Status: skeleton. Not yet pre-registered.**
> - Complete and approve this record **before any run on the test half** ([methodology v2.0](evaluation-methodology-v2.0.md) §8).
> - Every field names its source of truth, such as a manifest hash or a decision entry. Values are not copied in by hand.
> - Once approved, the record is append-only: a correction is a new, dated section, never an edit.

## 1. System under test
- **`main` SHA:**
- **Contract version** and the SHA-256 of `contracts/VERSION`, the prompt, the schema and `inference.json`:
- **Pipeline profiles** (agent SHA and flags):
  - Track A: P1 only.
  - Track B: production profile. Caption pass, grounding and zoom on; set-of-marks, object tracking and person tracking off.
  - Optional crop condition with tracking on, if approved.
- **Migration names and hashes** (dashboard snapshot):

## 2. Subjects and stage models
| Subject | Track | P1 model | Caption model | Grounding / zoom model | Endpoint | Capabilities verified on |
|---|---|---|---|---|---|---|
| | | | | | | |

**Unsupported (subject, task) pairs** (reported as unsupported, never scored):

## 3. Data
- **Clips:** `dataset-manifest-v1` (`clips_sha256`), `split-v2` (`test_sha256`).
- **Ground truth:** `ground-truth-v1.0` (`records_sha256`, `manifest_sha256`). The human-verified subset and its version, if present.
- **New references** (boxes, chat, dashboard):
  - dataset version and manifest hash;
  - counts per tier;
  - the generator and validator, with model id and prompt SHA-256;
  - reviewers;
  - κ on the double-reviewed share.
- **Exclusions,** with the reason for each (decided before any test run):

## 4. Primary metric per dimension and the comparisons
| Dimension | Primary metric | Comparisons (subject vs reference) | Family for multiplicity |
|---|---|---|---|
| A | Severity: **MAE** (`severity-v2-mae`, lower is better), proposed for the C2 freeze. Type: accuracy (proposed). Other A metrics are secondary. | Nano vs Super (Track A and Track B final stage), paired by clip, 95% cluster bootstrap on the MAE difference | A |
| B | | | |
| C | | | |
| D | | | |
| E | | | |

- **Secondary metrics:** all exploratory; listed by name.
- **Effects:** caption effect and zoom effect (within one model), with how each is computed.

## 5. Statistics
- **Repeats** on the test half, and the interleaving seed:
- **Bootstrap:** resamples, seed, cluster unit.
- **Multiplicity:** Holm within each dimension's primary family.
- **Minimum detectable effect,** with how it was computed:

## 6. Judges and instruments
- **Judge model, prompt version and SHA-256, settings:**
- **Judge–human κ** on the calibration set, and the date:
- **Matcher:** embedder, τ, matching validation set version, and measured matcher precision and recall:

## 7. Budgets and stop rules
- **Caps per ledger:** inference, caption pass, grounding, zoom, embeddings, judge, generation.
- **Stop rules:** outages, rate limits, session limits.
- **Recovery policy:** infrastructure failures only.

## 8. Default-model recommendation rule (R8)
The rule fixed now and applied after the test runs to recommend whether the console default (Cosmos 3 Nano) and the agent default (Cosmos 3 Super) should be aligned. It weighs Track B results, reliability, latency and cost. There is no hidden threshold.

- **Severity in the rule (proposed with `severity-v2-mae`):** severity enters as its own criterion, the paired MAE difference (Super − Nano) with its 95% interval; a negative difference favours Super. It is never added to, averaged with or re-weighted against accuracy-type metrics, and no composite incident score exists. Severity coverage (`n / attempted`) is reported beside it, and invalid outputs count against reliability, not against severity.
- **Dependencies found (2 Nov):** no V2 code computes a composite incident score or applies a selection rule, and this rule has no weights yet, so nothing used severity accuracy. The only use was descriptive: pilot summaries quoted V1's field accuracy, now kept as a continuity metric.

## 9. Disclosures
- The test clips are not unseen.
- `ground-truth-v1.0` and τ are AI-assisted.
- Claude-made references are correlated.
- The provider decides frame sampling.

## 10. Approval
- **Approved by:**
- **Approved at:**
- **Freeze manifest SHA-256:**
