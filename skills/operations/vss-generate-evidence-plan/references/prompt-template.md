# Initial planner prompt

```text
Create the minimal option-blind visual evidence plan for this video question.

question_id: {{question_id}}
question_stem: {{question_stem}}
asset_id: {{asset_id}}
allowed_modalities: {{allowed_modalities}}
task_constraints: {{task_constraints}}

Start with exactly one claim. Add a second claim only when the question explicitly asks for two independently reportable outcomes that require different visual tests or coverage. Never return more than two claims.

Do not create setup claims for referenced objects, locations, target identity, event anchors, or parts of an action. Include those as qualifiers in the requirement and tests. Examples: color of the car that stops before a line is one attribute claim; cause of a vehicle stopping is one cause claim; “how many and which” may need separate count and identity claims.

For each claim return only:
- claim_id: stable claim-<slug> identifier;
- requirement: one neutral, answer-relevant visible requirement including necessary entity, location, and temporal qualifiers;
- evidence_type: attribute, object, count, action, state_change, order, duration, trajectory, identity, spatial, cause, prediction, counterfactual, or negative. Use identity only for sameness across separated observations; use object for a vehicle, sign, or object's category/type;
- coverage_requirement: local_window, before_after, repeated_observation, or whole_video;
- support_test: visible outcome that satisfies the requirement;
- falsification_test: visible incompatible outcome.

Use whole_video for unscoped absence, counts accumulated across time, never, only, all, first, or last. A static scene count may use local_window when one complete view contains the full counting scope. Use before_after or stronger for state changes and causes. Use repeated_observation or stronger for identity, duration, trajectory, and cross-event order.

The falsification_test must describe visible counterevidence. Do not use missing visibility, ambiguity, occlusion, or insufficient coverage as falsification; those make the later sufficiency result unresolved.

The question_stem has been sanitized before this prompt. It must contain only the question being asked, never answer choices, answer labels, or a proposed answer. If it is not sanitized, return a schema-valid rejection with reason_code "unsanitized_question" instead of planning.

Do not use answer choices, a proposed answer, memory, evidence IDs, timestamps, or prior conclusions. Do not decide what happened. Return only JSON conforming to evidence-plan.schema.json with plan_version "2.1" and mode "initial".
```
