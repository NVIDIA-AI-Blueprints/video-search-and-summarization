# Evaluation V2: AI-only reference review results (ai-review-v1.1, 2026-11-03)

**Status:**
- Development-split records only. **No record is human-verified.**
- The review packets, raw responses, prompts and per-record verdicts are private (`evalv2-data/v2/ai-review/`), because they quote ground truth.
- The method is in [v2-ai-only-reference-review.md](v2-ai-only-reference-review.md).

## 1. Run, cost and validity

| Item | Value |
|---|---|
| Calls | 278: the 6-call probe, 266 reviewer calls (2 × 133) and 10 adjudications (6 probe + 272 full run) |
| Invalid or truncated replies | 0 |
| Served by the pinned provider | 278/278 (Nebius, Reka, InferenceNet; no fallback) |
| Tokens | 544,660 |
| Cost | **$0.1205**, as reported by OpenRouter (`usage.cost`) on every call. Approved cap: $1.55 |
| Reasoning text kept | Reviewer A only. Reviewer B and the adjudicator return none, so their `rationale` field is the only record |

## 2. Coverage (development split only)

| Set | Reviewed | Not reviewed |
|---|---|---|
| Judge calibration pairs | 80 / 80 (all from dev clips) | — |
| Dashboard references (RB2) | 30 / 75 (the review sample; no ground truth sent) | 45 outside the sample |
| Chat references (RA2 review sample) | 23 / 64 (dev clips only) | **41 deferred until after C3** (held-out GT) |
| Chat references (all of RA2) | 23 / 189 | 166 |
| Evidence boxes (RA1) | 0 | RA1 is still generating |

## 3. Agreement between the two AI reviewers

| Task | n | Raw agreement | Cohen's κ (95% CI) | AI-consensus | AI-disputed |
|---|---|---|---|---|---|
| Judge calibration (PASS / FAIL / UNSURE) | 80 | 0.90 | **0.79** (0.63–0.92) | 72 | 8 |
| Chat (correct / partly / incorrect) | 23 | 0.91 | −0.02 (−0.06–0.00) | 21 | 2 |
| Dashboard (correct / alternative / incorrect) | 30 | 1.00 | undefined (both reviewers said "correct" for all 30) | 30 | 0 |

**Reading the κ values:**
- For chat and the dashboard, κ is not informative. Almost every verdict falls in one category, so κ collapses even though raw agreement is high (the prevalence paradox).
- An anchoring check covers the dashboard: each reviewer first wrote its own spec, and both matched the reference on 30/30.

## 4. Disagreement and adjudication

- **Disputed:** 10 records went to the adjudicator: 8 judge-calibration pairs and 2 chat records.
- **Position bias:** the adjudicator supported the opinion shown first in 9 of 10 cases (two-sided binomial p = 0.02), although the order was randomised (A first in 6). Its rulings are therefore treated as **low reliability**, and those records stay **AI-disputed**.
- **Recommended reference changes:** recorded in the private AI-reviewed sets but **not applied**:
  - one chat library question, where the reference list may miss an incident;
  - one judge-calibration GT wording, which cannot change because GT v1.0 is frozen.

## 5. Judge calibration: AI-consensus agreement analysis (not human calibration)

This analysis compares the primary judge (gpt-oss-20b) with the AI-consensus verdicts, counting a judge score ≥ 0.5 as a pass. It **does not satisfy** the pre-registered human criterion (judge–human κ ≥ 0.60).

| Comparison | n | Agreement | κ (95% CI) | Judge pass rate | AI pass rate |
|---|---|---|---|---|---|
| Primary vs AI-consensus | 72 | 0.82 | 0.63 (0.43–0.80) | 0.44 | 0.35 |
| Primary vs all 80 (with low-reliability adjudications) | 80 | 0.79 | 0.56 (0.37–0.73) | 0.45 | 0.34 |
| Mistral (sensitivity) vs AI-consensus | 36 | 0.86 | 0.73 (0.51–0.94) | 0.56 | 0.42 |

**What this shows:**
- **The primary judge is more lenient than the AI reviewers.** On the consensus set it passes 10 pairs they fail, and fails only 3 they pass.
- **Agreement is lowest on `incident.description`** (0.63, n = 19).
- **Judged pass rates should be read as possibly optimistic,** and stay PROVISIONAL.

## 6. Dashboard references and severity words

- **Reviewers accepted the synonym mapping.** Both reviewers called the references' severity-synonym mapping (minor→low, moderate→medium, severe→high) "reasonable", and neither said a refusal would also be acceptable for any of the 4 sampled requests Ultra refused.
- **The refusal flags are not about synonyms.** The 12 "refusal also acceptable" flags fall on the 6 references that are themselves refusals.
- **The reviewers are not a project definition.** Their view of "minor" is an opinion only; the project defines no level for "minor" (see the dashboard severity-word change).

## 7. What this does not establish

- The review does not establish human verification.
- It does not meet the human calibration threshold.
- It cannot find errors in the ground truth itself. GT v1.0 was adjudicated with Claude's help, and the references are checked against it.
- Two LLMs agreeing is weaker evidence than two people agreeing.
