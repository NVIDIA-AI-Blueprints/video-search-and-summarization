# Initial planner prompt

```text
Create the minimal option-blind visual evidence plan for this video question.

question_id: {{question_id}}
question_text: {{question_text}}
asset_id: {{asset_id}}
allowed_modalities: {{allowed_modalities}}
task_constraints: {{task_constraints}}

Return a minimum of two claims. After a sufficiency check identifies a missing independent requirement, add one new claim at a time in a later expansion; do not add more than one new claim in that expansion. Each claim must express one specific visual fact or relationship that can be assessed separately. Keep the requirement to one sentence naming the subject, the fact to establish, and the relevant temporal scope. Split a claim when an answer-critical prerequisite and the conclusion it supports require different evidence tests and could have different resolution states. Record their dependency explicitly by naming the prerequisite claim_id in the dependent claim's requirement; do not resolve the dependent conclusion while its prerequisite remains uncertain. Do not create separate claims for incidental details that cannot affect the answer.

Do not create separate claims for referenced objects, locations, or event anchors that only identify the subject. Those stay inside the requirement sentence.

For each claim return only:
- claim_id: stable claim-<slug> identifier;
- requirement: one sentence naming the subject, the fact to establish, and the relevant temporal scope;
- evidence_type: attribute, object, count, action, state_change, order, duration, trajectory, identity, spatial, cause, prediction, counterfactual, or negative. Use identity only for sameness across separated observations; use object for a vehicle, sign, or object's category/type;
- coverage_requirement: local_window, before_after, repeated_observation, or whole_video;
- support_test: visible outcome that satisfies the requirement;
- falsification_test: visible incompatible outcome.

Use whole_video for unscoped absence, counts accumulated across time, never, only, all, first, or last. A static scene count may use local_window when one complete view contains the full counting scope. Use before_after or stronger for state changes and causes. Use repeated_observation or stronger for identity, duration, trajectory, and cross-event order.

The falsification_test must describe visible counterevidence. Do not use missing visibility, ambiguity, occlusion, or insufficient coverage as falsification; those make the later sufficiency result unresolved.

Do not use answer choices, a proposed answer, memory, evidence IDs, timestamps, or prior conclusions. Do not decide what happened. Return only JSON conforming to evidence-plan.schema.json with plan_version "2.0" and mode "initial".
```
