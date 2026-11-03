# Evaluation V2: sensitivity judge result (Mistral Large 4), 2026-11-03

**Primary judge:** gpt-oss-20b via OpenRouter.

**Sensitivity judge:** Mistral Large 4 (`openrouter-mistral-large-4-v1`).
- Sent 206 pairs: 80 calibration and 126 stratified.
- Answered 89. The other 117 returned no verdict: 110 spent the pre-registered 1,500 output tokens on reasoning, and 7 were cut off mid-JSON.

**On the 89 pairs both judges scored** (pass threshold 0.5):
- Agreement 0.944.
- Cohen's κ 0.888, 95% CI [0.778, 0.977] (pair bootstrap).

**The missing pairs are not missing at random:**
- The primary judge passes 49.4% of the 89 answered pairs but only 31.6% of the 117 missing ones.
- The gap is +0.18, 95% CI [0.04, 0.31].
- So κ 0.888 most likely overstates agreement on the full 206 pairs. Full-pair agreement cannot be computed.
- The gap is largest for `entities.description` (39% vs 19%) and `instruments.name` (80% vs 25%, with n = 5 and 4).

**Approved fix, and why it failed:**
- The owner approved up to $0.40 to fix the 117 failures by disabling reasoning (`openrouter-mistral-large-4-noreason-v1`).
- A 3-pair test returned HTTP 400 from Mistral: "reasoning_effort none is not supported for this model" (only low, medium or high).
- $0.00 was billed.
- As the approval required, the run stopped.
- The 89 v1 verdicts are unchanged, and no second configuration exists to mix with them.
- The registry entry is kept, marked FAILED, and must not be used.

**Untried option (needs approval):** `reasoning: {effort: "low"}` with a larger output cap. Its verdicts would be a separate configuration, reported beside the v1 verdicts and never pooled with them.

**Human calibration:** it will not happen (owner decision, 2026-11-03). See [v2-ai-only-reference-review.md](v2-ai-only-reference-review.md) for the AI-consensus replacement, which does not meet the human κ ≥ 0.60 threshold.
