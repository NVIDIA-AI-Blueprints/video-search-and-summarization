---
name: vss-generate-evidence-plan
description: Generate a minimal, option-blind evidence plan for a video question, starting with one or two visible claims and expanding only when the agent loop proves another claim is necessary. Use this skill before memory retrieval or visual inspection, or to add one claim after a sufficiency failure. Do not use it to bind evidence, answer the question, or pre-plan every possible supporting fact.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  vss-requires: "vlm"
---

# Generate a VSS evidence plan

Create the smallest auditable evidence plan that could answer a video question. The initial plan contains one claim by default and never more than two. Plan from the question alone; do not assess whether claims are true.

## Required inputs

Require `question_id` and `question_text`. Accept an optional `asset_id`, allowed visual modalities, and task constraints. If the caller supplies answer choices, proposed answers, memory records, labels, or prior conclusions, exclude them from planning.

Reject claims that require audio, speech, ASR, transcripts, external subtitles, or other unavailable modalities. Natural OCR may be used only when visible text is an allowed modality.

## Initial plan

1. Read [the claim-classification rubric](references/claim-classification.md) in full.
2. Substitute the inputs into [the planner prompt](references/prompt-template.md). Preserve its option-blind boundary and output contract.
3. Start with one answer-relevant claim. Put grounding details such as entity, location, and time inside its requirement and tests rather than creating setup claims.
4. Add a second claim only when the question explicitly asks for two independently assessable outcomes that require different evidence tests or coverage. Never emit three or more initial claims.
5. For each claim, choose one primary `evidence_type` and `coverage_requirement`, then write observable support and falsification tests.
6. Return one JSON object conforming to [the evidence-plan schema](references/evidence-plan.schema.json) with `mode="initial"`.

## Loop expansion

Do not anticipate every supporting fact. Bind memory, check sufficiency, and gather evidence against the current claims first.

Create one additional claim only when the sufficiency result shows that an independently assessable requirement is missing from the plan. Missing evidence for an existing claim is a gathering gap, not a reason to add a claim. Use [the expansion prompt](references/expansion-prompt.md), return `mode="expansion"`, and add exactly one claim per loop iteration. Preserve all prior claim IDs in the ledger.

## Planning invariants

- Remain option-blind. Never expose or infer answer choices in planning.
- State requirements neutrally; do not presuppose that an event occurred.
- Use visible and independently assessable claims only.
- Prefer one compound-but-testable answer claim over separate setup claims. Split only when the tests or coverage genuinely differ.
- Treat scene objects, target identification, event anchors, and spatial qualifiers as parts of the requirement unless the question independently asks about them.
- Keep one primary evidence type per claim.
- Do not cite evidence, observations, events, timestamps, or memory IDs at this stage.
- Use the strongest coverage required to run both the support and falsification tests.
- Keep `claim_id` values unique, stable, and descriptive using `claim-<slug>`.
- Preserve uncertainty. Planning describes what must be observed, not what the video contains.

## Semantic validation

After schema validation, verify that:

- an initial plan contains one or two claims and an expansion contains exactly one;
- each claim directly contributes to answering the question;
- no claim merely establishes a noun, landmark, target, or event already usable as a qualifier in another claim;
- support and falsification tests name observable and incompatible outcomes;
- a falsification test describes counterevidence, not missing visibility, ambiguity, or occlusion; those are sufficiency outcomes;
- `whole_video` is used for unscoped `never`, `only`, `all`, `first`, `last`, absence, and counts accumulated across time;
- a static scene count may use `local_window` when one complete view contains the full counting scope;
- `before_after` or stronger coverage is used for state changes and causal claims;
- identity means sameness across separated observations; object category or vehicle type alone is `object`;
- identity, duration, and cross-event order have repeated observations or stronger coverage;
- no claim contains an answer option, answer letter, evidence ID, or unobservable narrative assumption.

Use [the worked example](references/worked-example.md) to calibrate output shape and granularity. Do not copy its claims into unrelated questions.
