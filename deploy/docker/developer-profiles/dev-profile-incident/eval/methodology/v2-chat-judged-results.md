# Evaluation V2: C1c chat results with judged checks (development split, 2026-11-03)

**Status:** development split only: 70 dev questions, 140 answers from Nemotron 3 Ultra. **PROVISIONAL AI-judged.** The judge is not human-calibrated, the references are AI-generated, and no record is human-verified.

**The two corpora stay separate:**
- **oracle:** the frozen ground-truth reports;
- **system:** the pipeline-generated C1b Super final reports.

## Run (owner-approved proposal B)

| Item | Value |
|---|---|
| Judge | `openrouter-gpt-oss-20b-v1` (gpt-oss-20b via OpenRouter → DeepInfra or DekaLLM, no fallback, no retention; temperature 0, `max_tokens` 1,500) |
| Prompts | `judge-fact-v1` `7a9363c9…`, `judge-unsupported-v1` `10c7b1f3…`, `judge-contradiction-v1` `81cec67b…` |
| Calls | **221** of the 245 cap (79 unsupported claim, 93 contradiction, 49 fact), deduplicated by the judge cache. 0 failed |
| Tokens | 250,057 of the 1.25 M cap |
| **Cost** | **$0.0117** measured on the key (usage $0.885577 → $0.897251), against the $0.08 cap |
| Evidence | The unsupported-claim check compares the answer with the reports the chatbot retrieved. The contradiction check compares it with the ground-truth reports the expected answer rests on |
| Records | Raw verdicts are kept in the judge cache (`raw`, `score`). The original code-only chat report is unchanged; the judged report is a separate file |

## Results (95% CIs: cluster bootstrap by incident, by record for library questions; seed 20261027, 10,000 resamples)

| Metric | Oracle (GT reports) | System (pipeline reports) | Status |
|---|---|---|---|
| Exact-answer accuracy (code) | 0.78 (n = 50) [0.65, 0.89] | 0.24 (n = 49) [0.14, 0.35] | PROVISIONAL references |
| Citation precision / recall (code) | 1.00 (n = 40) / 0.64 (n = 60) [0.51, 0.76] | 0.95 (n = 27) / 0.40 (n = 59) [0.26, 0.54] | PROVISIONAL |
| Retrieval R@5 (code; library retrieval) | 0.74 (n = 20) [0.55, 0.91] | 0.42 (n = 20) [0.23, 0.61] | PROVISIONAL |
| **Unsupported-claim rate** (judged) | **2/46 = 0.04** [0.00, 0.11] | **1/33 = 0.03** [0.00, 0.11] | PROVISIONAL AI-judged |
| **Contradiction with ground truth** (judged) | **1/55 = 0.02** [0.00, 0.06] | **28/42 = 0.67** [0.52, 0.80] | PROVISIONAL AI-judged |
| **Fact recall** (judged; incident "what happened" questions) | 0.70 (n = 5 answers) [0.54, 0.87] | 0.13 (n = 5) [0.00, 0.29] | PROVISIONAL AI-judged; n = 5 |
| Holder identified (judged) | 0/5 | 0/5 | **Not interpretable**: see below |

**Denominators:**
- Refusals are excluded from the unsupported-claim and contradiction denominators (oracle 15 refusals, system 27).
- In each corpus, 9 library count answers had no retrieved context: they come from database facets. The unsupported-claim check does not apply to them, and they are counted as not applicable, not as missing.

## Reading the results

- **The chatbot rarely adds unsupported claims** to what it retrieved: about 3–4% in both corpora.
- **On pipeline reports, two-thirds of answers contradict the ground truth.** The answers are faithful to the reports, so this mostly measures errors in the pipeline-generated reports propagating into the chat, not chatbot unfaithfulness. The same contrast shows in exact answers: 0.78 on oracle against 0.24 on system.
- **Fact recall rests on 5 answers per corpus** and is indicative only.
- **Holder identification is not interpretable.** The scorer's holder fact is "<full ground-truth entity description> held the <instrument>". The description includes actions ("…who was beating up E1 using a stick"), and the judge requires the actions as well as the holder. A scorer fix would be a new scorer version decided before C2. It is excluded from the C3 pre-registration instead.

## Limitations

- The judge is not human-calibrated: AI-consensus κ 0.63 on incident fields, and the judge is lenient.
- There is no sensitivity judge.
- Development split only; the test half has 119 references that are not yet AI-reviewed.
- These are not human-calibrated semantic accuracy figures.
