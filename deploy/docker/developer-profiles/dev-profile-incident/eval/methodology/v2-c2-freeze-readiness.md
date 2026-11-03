# Evaluation V2: C2 freeze-readiness report (2026-11-03) — FOR AN INFORMED FREEZE DECISION

**What this report is.** C2 is **ready for a freeze decision**: every technical blocker is resolved or turned into an explicit scope exclusion or limitation. Four decisions remain for the owner (§1, part D). The report does not freeze C2, run C3 or declare every evaluation task complete.

**Constraints observed:**
- No paid call was made for this report.
- No held-out clip, frame, ground-truth text or reference answer was sent to any model.
- Frozen ground truth is unchanged.

Pins: [pre-registration (proposed)](evaluation-v2-preregistration.md).

## 1. Blocker matrix

### A. Resolved

| Item | How |
|---|---|
| Severity-word handling (#476) | Merged (`04270a3f2`) after the dev evaluation. The gain is not significant and one request regressed; both are disclosed |
| Held-out dashboard references | `dashboard-references-test-v1` (§2): 75 deterministic records, with expected specs, refusal decisions and recount figures computed by code. No model and no external exposure |
| Pre-registration | Completed draft with every pin, metric, scoring rule, exclusion, failure rule, statistic, the R8 rule and disclosures. **Approval pending (D1)** |
| Track B drift | Audited (§3). C3 uses the **historical C1b profile, labelled as such**, verified reproducible offline. Current production is **not** silently mixed in |
| Chat judging (proposal B) | Done: 221 calls, $0.0117 of the $0.08 cap ([results](v2-chat-judged-results.md)) |
| AI-judged metrics | Kept as secondary, PROVISIONAL AI-judged; never described as human-calibrated |
| Reference disputes | 10 unresolved, kept (8 judge-calibration, 2 dev chat); the biased adjudicator is not authoritative |

### B. Accepted scope exclusions (owner, 2026-11-03)

| Exclusion | Effect |
|---|---|
| **Evidence-box localisation (dimension C)** is out of the C3 critical path | No bounding-box IoU or crop accuracy is claimed. RA1 continues separately from its checkpoint (detector proposals 37/80 clips, all 25 dev clips done). Its generator and validator passes are **not started** (unapproved). Proposal A waits on them |
| Chat holder identification | Excluded as not interpretable (an instrument artefact) |
| Mistral sensitivity judge | Paused; none in C3 |
| Adjudicator experiments (proposal C) | Not run |
| More severity words (major, extreme, catastrophic) | Not added |

### C. Limitations carried into every C3 report

**The data and the instruments:**
- The test clips are held out but not unseen (§4).
- Ground truth was AI-assisted in adjudication.
- No reference is human-verified.
- Judged metrics have no human calibration (AI-consensus κ 0.63 [0.43, 0.80]; the judge is lenient).
- The sensitivity judge covered 89/206 dev pairs, missing not at random.

**The configurations:**
- The dev and test dashboard sets differ in wording (Claude-written vs templates), so they are not comparable.
- Track B is the historical profile, not current production.
- The MDEs are larger than the dev Nano–Super differences (a null result is likely).
- Temperature-0 serving is not bit-deterministic.
- The provider decides frame sampling.

### D. Decisions still requiring owner approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | Approve the pre-registration, including its amendments to methodology v2.0: dimension A primary metric (severity MAE and type accuracy, no composite), C excluded, D primary metric moved off judged fact recall, and the proposed R8 rule | Approve, or edit and approve |
| D2 | **Test set: option A (fresh 20 clips) or option B (the 55 with the exposure statement)** (§4) | **B now**, with A as a pre-registered optional addendum if four labellers become available. A is the scientifically stronger design but is not feasible without human labelling time |
| D3 | The Track B label: accept "historical C1b profile" for C3, or approve a VM-based current-production configuration (§3), which needs shared-infrastructure use and a new dev check | Accept historical for C3; treat current production as a separate, later version |
| D4 | The C2 freeze, then the C3 budget: ≈ 3,300 calls, ≈ 12 M tokens, expected ≈ $0.70, proposed cap $2.00 (§5) | Freeze after D1–D3 |

## 2. Held-out dashboard reference set (`dashboard-references-test-v1`)

**Specs:**
- Planned with RB2's own deterministic planner (`rb2.spec_plan`, seed 20261104). Every spec the dev set uses is excluded.
- Quotas: 4 per filterable dimension and 2 per analysis dimension, giving 60 specs over all 16 dimensions and all 5 periods (35 with a severity filter, 40 with a type filter).
- The two analysis dimensions have one spec per period. Their non-dev specs are enumerated and taken in a seed-determined order.

**Requests:**
- Rendered from fixed templates (`dashboard-test-templates-v1`). Each expected spec is therefore true by construction.
- Two of three severity-filtered requests use band words; one uses a canonical `chart-severity-words-v2` word (11 requests change under the normaliser).
- 15 must-refuse requests are fixed sentences, one per unsupported kind.

**Answers:**
- The figures are a deterministic recount on the RB2 corpus snapshot (`1c5b2927…`, clock 2026-11-01T12:00Z): 54 charts with rows, 6 empty, 15 refusals.
- The scorer recomputes them with the SQL harness at score time.

**Separation from model outputs:**
- Built offline by code, with no model call.
- Stored privately (`records.jsonl` `53782844…`) and registered by hash only.
- Never shown to a reviewer or a subject; subjects see only the request text in C3.

**Limitation:** the template wording is plainer than the Claude-worded dev set. Dashboard test scores are reported on their own, never compared with dev.

## 3. Track B configuration audit

| Aspect | C1b (dev pilot, 2026-11-02) | Current production (`native-services.sh` defaults, `main`) |
|---|---|---|
| Agent code | `acbf892ba` (experiment agent, its own checkout) | `04270a3f2` |
| Person tracking | off | **on** (`INCIDENT_TRACKING_ENABLED` default true; DeepStream stack on the VM) |
| Object tracking | off | **on** (`INCIDENT_OBJECT_TRACKING_ENABLED` default 1; tracker models on the VM) |
| Caption pass | on (inline, at most 30 calls) | on (`INCIDENT_CHUNKING_ENABLED` 1) |
| Privacy blur | off | removed (#453) |
| Pipeline-relevant agent changes since `acbf892ba` | — | #452 (an on-screen person is not the entity; person tracks confirm boxes), #463 (evidence image is the first second a track sees it), #469 (track timing), #454 (object-tracking detector timeout). All of them act when tracking is on and change evidence crops, which the zoom pass reads |

**Options:**
- **(H) Historical, recommended for C3.** The experiment agent at `acbf892ba` with the C1b flags, labelled "Track B (C1b profile)".
  - Verified offline: the launcher's dry run (`EXPERIMENT_AGENT_DRY_RUN=1`) starts the code at `acbf892ba` from `/home/hengz/vss-wt-pilot`, with `env -i`, experiments on and no tracking variables. That reproduces C1b's pinned flags.
  - The adapter refuses any reply whose pinned agent SHA or flags differ.
  - Dev and test then share one configuration.
- **(P) Verified current production.** This needs the VM's person-tracking and object-tracking services, which is shared infrastructure and needs owner approval. It would also need a new dev pilot to verify the configuration and recalibrate caps before C3. It cannot be verified on the laptop.
- **Not allowed:** current code with the historical flags, or historical dev results with current-production test results. Either would silently combine two configurations.

**Incident during this audit:**
- One launcher invocation used a non-existent `--dry-run` flag and briefly started the local loopback experiment agent.
- It was stopped within the minute.
- It received no request, so no model call was made. No process remains, and port 8765 is closed.

## 4. Test set: options A and B

**The existing 55 test clips (exposure history):**
- The V1 2026-10-02 benchmark ran three models on all 55.
- Animal003, Assault018 and Burglary005 shaped earlier prompt wording.
- The AI-assisted GT correction viewed frames of 38 clips from both halves.
- The GT adjudicator and the V2 reference generator are both Claude.
- **V2 itself used no test clip:** every probe, pilot, recovery, scoring choice and model choice ran on the 25 dev clips.

| | Option A: fresh test set | Option B: the 55 held-out clips |
|---|---|---|
| Validity | **Stronger.** About 20 clips no one in the project has seen, with GT made without AI assistance, so the set is independent of the Claude-family references | Held out from V2 tuning but **not unseen**. Absolute scores may be optimistic, especially for the V1-era prompt; paired model comparisons are less affected. A 52-clip sensitivity analysis drops the three prompt-shaping clips |
| Power | Low (20 clips: direction and ranking only) | MDE 0.33 severity levels and about 13 type-accuracy points (§5 of the pre-registration) |
| Cost and time | Sourcing and exclusion checks 3–4 h; labelling 15–25 person-hours (4 labellers); adjudication about 5 h, so **25–35 person-hours**. Inference < $0.25 | No labelling. Inference ≈ $0.70 (§5) |
| Feasibility now | **Not feasible without human labelling time.** The owner has no reviewer time, and AI-only labelling would destroy the independence that motivates A | Feasible immediately after D1 and the freeze |
| Reporting | Beside the 55, never pooled | With the exposure statement on every table |

**Recommendation (the decision is the owner's):**
- **B for C3 now**, with the exposure statement and the 52-clip sensitivity analysis.
- Pre-register **A as an optional addendum** that runs once, with the frozen C3 configuration, if four labellers become available.
- A is the stronger design, but only with human labelling. Without it, B with full disclosure is the stronger *feasible* choice.

## 5. Estimated C3 workload

The estimates use dev pilot unit costs scaled by video seconds (test half 3,408 s, mean 62.0 s). Wall-clock is sequential within a subject.

| Run | Calls | Tokens | Wall-clock | Paid USD |
|---|---|---|---|---|
| Track A Nano ×2 repeats | 110 | 1.25 M | ≈ 15 min (p50 7.5 s, p95 53 s) | 0 (Switchyard) |
| Track A Super ×2 | 110 (≈ 141 attempts) | 0.97 M | ≈ 2.3 h (p50 74 s, p95 226 s) | 0 |
| Track A Omni ×2 | 110 | 2.00 M | ≈ 35 min | 0 |
| Track A Qwen3.5-9B ×2 | 110 | 1.56 M | ≈ 2.3 h (p95 295 s) | ≈ 0.25 |
| Track A Gemma 4 31B ×2 | 110 | 0.72 M | ≈ 22 min | ≈ 0.12 |
| Track B Nano (historical profile) | 55 runs | 1.83 M | ≈ 1 h | 0 |
| Track B Super (historical profile) | 55 runs | 1.63 M | ≈ 1.5 h | 0 |
| Primary judge, incident fields | ≈ 1,800 | ≈ 0.8 M | ≈ 1.5 h | ≈ 0.06 |
| Chat (Ultra): 119 test questions × 2 corpora | ≈ 270 | ≈ 0.27 M | ≈ 30 min | 0 |
| Chat judge | ≈ 375 | ≈ 0.5 M | ≈ 2.5 h (measured pace ≈ 2.5 per min) | ≈ 0.02 |
| Dashboard test (Ultra; optionally Nano and Super) | 75 (225) | 0.07 M (0.21 M) | ≈ 5 min | 0 |
| **Total** | **≈ 3,300** | **≈ 12 M** | **≈ 12 h sequential; ≈ 5 h with subjects in parallel** | **≈ $0.70; proposed cap $2.00** |

## 6. What can be reported without human verification

| Metric | C3 status |
|---|---|
| Type accuracy, severity MAE, timestamps, object matching F1 | **Reportable** against frozen GT (human-labelled; AI-assisted adjudication disclosed) |
| Validity, reliability, tokens, latency, cost; caption and zoom effects on automated fields | **Reportable** (measured or deterministic) |
| Dashboard exact-spec accuracy, refusals, figure accuracy on the test set | **Reportable**: references are deterministic by construction. The template wording limitation applies |
| Chat exact answers, citations, retrieval | **Reportable as PROVISIONAL**: answers are code-computed, wording is AI, and the test references are not yet AI-reviewed |
| Judged description and attribute pass rates; chat fact recall, unsupported-claim and contradiction rates | **Secondary, PROVISIONAL AI-judged only** |
| Evidence-box and crop localisation | **Not reported** (out of scope) |
