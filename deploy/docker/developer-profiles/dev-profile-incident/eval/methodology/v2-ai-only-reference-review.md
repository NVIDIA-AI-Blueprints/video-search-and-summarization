# Evaluation V2: AI-only independent reference review (owner decision 2026-11-03)

**Status (2026-11-03):**
- **Approved by the owner**, for **development-split records only**, with hard caps of 440 calls, 3.4 M tokens and **$1.55**.
- **Six-call probe passed 6/6.** It checked strict JSON, the pinned provider and model actually served, the cost reported by OpenRouter, and reasoning text kept where the provider returns it.
- **Review run:**
  - judge-calibration: 80 records;
  - dashboard: 30 records (they carry no ground truth);
  - chat: the 23 dev records.
- **Deferred until after C3:** the 41 chat records that quote held-out ground truth.
- Results: [`v2-ai-review-results.md`](v2-ai-review-results.md).
- The tooling and outputs are private under `evalv2-data/v2/ai-review/`, because the packets quote ground-truth text.
- This document states the method and its limits.

## 1. Why

The planned human reviews will not happen, because the owner has no reviewer time. That covers:
- the 80 judge-calibration pairs;
- RA2 chat (64 records) and RB2 dashboard (30 records);
- evidence boxes after RA1.

An AI-only review replaces them. It is **weaker evidence** than human review, and every record stays **Human-unverified**.

## 2. What changes in the methodology

| Methodology v2.0 rule | Now |
|---|---|
| Primary metrics use `human_verified` records only (§3) | **No record will be `human_verified`.** Primary metrics use `ai_validated` records, annotated with the AI-review status. Every table is labelled PROVISIONAL, and AI-consensus-only results are reported beside the all-records results |
| Human QA of about 20% plus every flagged record (§3) | Two independent AI reviewers on the same samples, plus a separate AI adjudication of disputed records |
| Judge–human κ on about 100 items (§7); pre-registered threshold κ ≥ 0.60 | **Not done, and the human threshold is not claimed.** It is replaced by an explicitly labelled **AI-consensus agreement analysis**: the judge's verdicts compared with the records where both AI reviewers agree |
| Tier `human_verified` | Unused. The tier stays `ai_validated`; the AI review is an extra annotation, never a tier upgrade |

## 3. Terminology (used in every table, chart and slide)

| Term | Meaning |
|---|---|
| **AI-reviewed** | Both AI reviewers returned a valid verdict |
| **AI-consensus** | They independently gave the same verdict, and neither flagged ambiguity or unsupported claims. **Not proof of correctness** |
| **AI-disputed** | They disagreed, or one flagged an issue. The record is adjudicated by a third model; the original opinions are kept |
| **Human-unverified** | Every record in V2. Nothing is ever labelled human-verified |

## 4. Design

- **Reviewers:**
  - A is DeepSeek V4.1 Flash, served by Nebius.
  - B is GLM-5.3 Flash (Zhipu), served by Reka.
  - The adjudicator is MiniMax M3.1 Flash, served by InferenceNet.
  - None of these families is a subject (NVIDIA, Google, Alibaba), a judge (OpenAI, Mistral) or the reference generator (Anthropic).
  - Each provider is pinned with no fallback, and none serves a subject or a judge.
- **Blindness:**
  - Packets hold only the material under review: the two texts; the question, reference and ground-truth evidence; or the request, the production chart rules and the reference.
  - They never hold a judge verdict, a subject, a generator or validator output, or the other reviewer's opinion. A self-test asserts this.
- **Settings:**
  - Fixed, versioned prompts (`air-*-v1`, hashes pinned in the approval marker).
  - Temperature 0, fixed seed, strict JSON schema, `data_collection: deny`.
  - The reviewer writes its own answer before judging the reference, to reduce anchoring.
- **Records:**
  - Raw responses, any reasoning text, verdicts and confidence are stored unmodified.
  - Truncated or unparseable replies are recorded as failures, never repaired.
- **Agreement:** raw agreement and Cohen's κ between A and B per task, with a 95% CI from 10,000 bootstrap resamples over records.
- **Adjudication:**
  - Only for AI-disputed records.
  - The adjudicator re-examines the original material, and sees both opinions in seeded random order with identities hidden.
  - It is told not to count votes.
  - The originals are never overwritten.
- **Deterministic parts stay in code:**
  - Chat counts, dashboard figures (SQL harness, Node replay, Python recount) and box geometry are computed, not reviewed by a model.
  - For boxes, reviewers see actual frames.
- **Versioned outputs:**
  - The original reference sets are unchanged.
  - The AI-reviewed sets are new versions (for example `chat-references-v1+air-v1`). They hold the per-record review block and any *recommended* change, which is not applied.

## 5. Circularity audit (summary; the full audit is private)

- **Clear:**
  - shared family with subjects, judges or the generator;
  - shared providers;
  - shared prompts;
  - reviewers seeing verdicts or each other.
- **Residual:**
  - GT was adjudicated with Claude help, and the references are checked *against* GT. So AI review cannot catch GT errors.
  - LLMs share biases.
  - Training-data overlap is unknowable.
- **Watch:** the dashboard's severity-synonym mapping (minor→low and similar). It drives most of Ultra's false refusals, so reviewers are asked whether a refusal is also acceptable, and the result is reported both ways.

## 6. Cost (estimate; calls need approval)

- **Volume:** 418 calls: 2 × 174 reviewer calls, plus adjudication of up to 40% of records, plus 6 probe calls.
- **Tokens:** about 1.7 M input tokens.
- **Cost:** expected about **$0.70** on OpenRouter; worst case **$1.55**, with every reply at its 4,000-token cap.
- **Proposed hard caps:** 440 calls, 3.4 M tokens, **$1.60**.
- **Box review:** priced separately when RA1 finishes.

**Disclosure decision (owner, 2026-11-03):** development records only.
- Every record's source clips are audited before sending (a private `split-audit.jsonl`).
- No held-out ground truth is sent.
- The 41 held-out chat records are deferred until after C3.

## 7. What the result can claim

- **Can claim:** "two independent non-Claude AI reviewers agree (or disagree) that the reference is supported by the ground truth or the rules".
- **Cannot claim:**
  - human verification;
  - the human calibration threshold;
  - correctness where the GT itself is wrong.
