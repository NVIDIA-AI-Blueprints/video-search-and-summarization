---
name: vss-introspect-video
description: Run a single-coordinator evidence loop using fixed-format parallel VLM tool calls, compact claim assessments, and a mandatory reserved final visual check. Delegate here from vss-ask-video for evidence-intensive questions.
license: Apache-2.0
metadata:
  version: "4.0.0-experimental"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  vss-requires: "vlm"
---

# Batched video introspection — experimental workflow

Use after `vss-ask-video` delegates an introspection question. This branch
replaces parallel inspector agents with **parallel tool calls**. One coordinator
plans, assesses evidence, and synthesizes. Do not spawn inspection/planning
subagents, construct free-form VLM prompts, poll worker processes, or write the
canonical ledger/final-result yourself.

`scripts/inspection_workflow.py` owns prompt rendering, concurrent tool calls,
batch artifacts, accounting and merge. It reuses `vlm_inspection.py` and the
configured backend; do not change routing or construct endpoints. Use ordinary
`vss memory get/query`, `vss vios list/timeline` and the guarded helper, never
a VSS introspection service or `/generate`.

## Limits and compatibility

Read `config/batch-policy.json` for experiment limits and sampling presets.
`config/ledger-budgets.json` remains the legacy schema's upper bounds; the runner
applies tighter experiment limits. There is an initial batch, at most one
materially different refinement, and a **separately reserved final check**.
Historical ledger keys named `max_parallel_subagents` and
`max_vlm_calls_per_subagent` now describe tool-task slots and allocations, not
permission to spawn agents.

No claim expansion in this experiment. No quality/syntax retries: preserve useful
imperfect captions. Existing backend transport retries remain helper-owned.
Failures consume calls. A task interrupted with unknown consumption is charged
its full allocation and not silently resubmitted. Repeated commands reuse saved
artifacts. Never delete a receipt to obtain more calls.

Only grounded **sensor windows** are supported. Discover them through VIOS; do
not invent sensor IDs for files/URLs. Report unsupported input rather than falling
back to the legacy workflow. Keep all artifacts in a fresh per-attempt RUN_DIR.

## 1. Plan atomic requirements before memory retrieval

Retain question ID, original question, choices, asset identity and allowed
modalities. Use the `vss-generate-evidence-plan` schema and classification rubric
to write the plan yourself before retrieval. Normal inspection/planning is
option-blind: no choices, proposed answers or prior conclusions.

Each claim is one sentence naming the subject, one independently verifiable
fact/relationship, and temporal scope. Split when one necessary part could be
established while another needs a different evidence test. Do not combine an
unverified prerequisite with a dependent conclusion. Use one or two claims; do
not manufacture a second claim for an atomic question. Preserve other required
prerequisites in tests and missing-evidence fields.

Plan fields: `plan_version: "2.0"`, `mode: "initial"`, `question_id`,
`question_text` (option-blind), `asset_id` (string/null), `claims`.
Each claim has `claim_id: "claim-<slug>"`, `requirement`, `evidence_type`,
`coverage_requirement`, `support_test`, `falsification_test`. Use the planner's
existing enums. Falsification requires visible incompatible evidence, not a
retrieval miss or uncertainty. Counts/absence need complete scoped coverage;
motion, identity and order need linked observations.

```bash
# Set SKILL_DIR to the absolute directory containing THIS loaded SKILL.md.
# Installed harness skills are flattened; do not assume a repository checkout.
LEDGER_TOOL="$SKILL_DIR/scripts/evidence_ledger.py"
WORKFLOW="$SKILL_DIR/scripts/inspection_workflow.py"
python3 "$LEDGER_TOOL" init --plan "$RUN_DIR/plan.json" --output "$RUN_DIR/ledger.json"
# Explicit option-selection question: also pass --requires-choice and
# --original-question "$ORIGINAL_QUESTION" to init.
```

## 2. Retrieve and bind memory

After planning, use available Markdown memory and configured VSS memory.
Preserve record IDs/jobs/timestamps. No matches are not negative evidence.
Write a compact array of memory updates: `claim_id`, `observations`,
`coverage` (`none/partial/sufficient`), `gap` (string/null).
Observation fields: `observation_id` (helper canonicalizes), `claim_id`,
`relation` (`supports/contradicts/context`), `text`, `source`.
Memory source: `type: "memory"`, `record_id`, optional `job_id`,
`sensor_id`, and paired `start/end`.

Check prerequisites and compatible subject, time and state before accepting
sufficiency. Individually supported observations do not support their combination.
Preserve uncertainty in `gap`; a non-null gap blocks code-computed sufficiency.

```bash
python3 "$LEDGER_TOOL" merge-memory --ledger "$RUN_DIR/ledger.json" \
  --updates "$RUN_DIR/memory-updates.json" --output "$RUN_DIR/ledger.json"
python3 "$LEDGER_TOOL" assess --ledger "$RUN_DIR/ledger.json"
```

If memory resolves the ledger, skip normal batches **but not the final check**.

## 3. Submit a structured batch

Write `inspection-specs.json`, a JSON array with exactly these fields per task:

```json
[{
  "claim_id": "claim-direction",
  "subject": "The vehicle identified by the question",
  "fact_to_establish": "Motion relative to its independently visible heading",
  "required_state": "During the approach referenced by the question",
  "reference_frame": "Vehicle front/rear; report unknown if not established",
  "scope": {
    "type": "sensor", "sensor_id": "<listed sensor>",
    "start": "<grounded ISO-8601 start>", "end": "<grounded ISO-8601 end>"
  },
  "sampling": "locate"
}]
```

Focus fields are bounded short strings, not an extra instruction channel.
Name the missing discriminator, not a proposed answer. Required state anchors the
event; reference frame names the coordinates or says not applicable.
Select `skim/locate/detail` from the policy based on the missing fact, not always
the densest. Code splits scopes at the frame cap and records uncovered windows.
Sampling a scope does not guarantee observing every event in it.

The workflow uses the helper's 48-frame default to stay below 50-image backend
limits. A single call spans at most 96 seconds at skim, 48 at locate, or 24 at
detail. Longer normal scopes split and consume additional calls within the same
budget; inspect the recorded uncovered windows if the allocation runs out.

```bash
python3 "$WORKFLOW" batch --ledger "$RUN_DIR/ledger.json" \
  --spec "$RUN_DIR/inspection-specs.json"
```

The command is synchronous. If the hosting tool yields a session ID, use its
session-wait mechanism, not process polling or another agent.
It uses the installed configured `vss` CLI on PATH. Only for a deliberate local
source-checkout run, add `--vss-project "$VSS_REPO_ROOT/libs/vss"`.
Read `rounds/round-N/batch.json` and task receipts. The fixed prompt asks for
chronological `[start_seconds:end_seconds]` captions relative to each submitted
clip and limitations. Imperfect caption syntax does not trigger a retry.

## 4. Assess compactly and merge once

Write one assessment per returned task:

```json
[{
  "task_id": "inspect-claim-direction-r1",
  "observations": [
    {"attempt_index": 0, "quote": "<verbatim output substring>", "relation": "context"}
  ],
  "prerequisites_established": false,
  "compatible_evidence": true,
  "coverage_sufficient": false,
  "conflict_free": true,
  "missing_evidence": ["Vehicle front/rear is not independently visible"],
  "rationale": "Camera-relative motion cannot establish forward motion."
}]
```

`attempt_index` indexes that task's `inspection.attempts` followed by
`reused_evidence`. Quote useful lines only. Code checks exact quote membership,
usable output, scope and job provenance and creates observation IDs.
An empty observation list is valid. A limitation is not counterevidence.

Assess whether prerequisites are established, evidence combines compatible
subject/time/state, required coverage is sufficient, and conflicts are absent.
A false field, explicit gap, incomplete inspection, conflicting relations, or
missing evidence prevents closure. Code enforces these declared constraints;
it does **not** determine whether semantic assessments are true.

```bash
python3 "$WORKFLOW" merge --ledger "$RUN_DIR/ledger.json" \
  --batch "$RUN_DIR/rounds/round-1/batch.json" --assessments "$RUN_DIR/assessments.json"
```

Code merges the complete batch in deterministic order and writes one revision.
If still in progress, use the one refinement to change window, sampling or focus
based on the gap. A first pass with no useful observations can refine. No identical
retry, third batch or claim expansion. Terminal states proceed to final check.

## 5. Mandatory final check after EVERY terminal outcome

After resolution, no progress, budget exhaustion or tool failure, synthesize a
tentative answer. For explicit options choose the best available option even
when unresolved. For open-ended questions answer naturally or abstain; never
invent labels. Do not publish the answer yet.

Write `final-check-spec.json`:

```json
{
  "candidate_answer": "<complete tentative answer, or null for open-ended abstention>",
  "scope": {
    "type": "sensor", "sensor_id": "<listed sensor>",
    "start": "<ISO start>", "end": "<ISO end>"
  },
  "sampling": "detail"
}
```

Choose the most answer-discriminating grounded window. It **must fit one call**
at the preset; an overly long scope is rejected, not silently truncated.
Use the same 48-frame limit (96/48/24 seconds at skim/locate/detail).
The call is fresh, outside normal budget, with no quality retry. Normal inspections
remain option-blind; the final template intentionally receives the candidate as
a hypothesis to challenge. One local check may not establish a whole-video/count
answer; that should be inconclusive, not confirmation.

```bash
python3 "$WORKFLOW" final-check --ledger "$RUN_DIR/ledger.json" \
  --spec "$RUN_DIR/final-check-spec.json"
```

Read `final-check.json`, including raw output. The VLM returns visual evidence and
one `CHECK: supported/contradicted/inconclusive` line. Malformed verdicts remain
inconclusive; useful raw text is retained. Tool failure remains failed.
Do not loop after this call. You may revise the answer, but the revised answer
is explicitly not rechecked. Do not modify the ledger to make the check agree.

## 6. Publish answer and artifacts

```bash
python3 "$LEDGER_TOOL" final-result --ledger "$RUN_DIR/ledger.json" \
  --artifact-dir "$RUN_DIR" --answer "$ANSWER" \
  --evidence-id "$OBSERVATION_ID" --output "$RUN_DIR/final-result.json"
```

Cite only accepted IDs actually used; repeat the flag as needed. Omit citations
when none exist on an unresolved choice question. For open-ended abstention
omit `--answer`, never pass an empty string. Choice questions must answer.

Finalization refuses a missing or stale final-check receipt. It records normal
and final-check calls separately, the verdict/candidate, whether the answer
changed, and the check's hash. Failed/inconclusive/contradictory checks or revised
answers make overall `evidence_status: unresolved` even when the pre-check ledger
was resolved. An unresolved ledger stays unresolved even with a supported check.
Selecting an option never closes evidence gaps.

`terminal-commit.json` binds ledger and final-result hashes. No provisional
answer is published before the check. A process killed mid-call cannot guarantee
completion: it records interruption and consumes the reservation without claiming
confirmation. Re-running commands repairs saved artifacts without new inference.

Keep exact prompts, raw outputs, scopes, sampling, provider token usage (null
when unavailable), elapsed times, assessments and revisions in the run directory.
Do not persist ledgers in VSS memory or Git. Use a new directory for every trial.
