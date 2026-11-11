# Initial planner prompt

```text
Create the minimal option-blind visual evidence plan for this video question.

question_id: {{question_id}}
question_text: {{question_text}}
asset_id: {{asset_id}}
allowed_modalities: {{allowed_modalities}}
task_constraints: {{task_constraints}}

Start with one specific, independently verifiable fact or relationship. Split into two claims when one necessary part could be established while another remains uncertain and needs a different visual test. Never return more than two claims; keep further prerequisites explicit in the tests.

Use already grounded entities and event anchors as qualifiers. Do not combine an unverified prerequisite with its dependent conclusion: an uncertain reference frame or identity bridge may need an independent test. Each requirement is one sentence naming subject, fact and temporal scope. Do not manufacture claims for mere object existence.

For each claim return only:
- claim_id: stable claim-<slug> identifier;
- requirement: one neutral, answer-relevant visible requirement including necessary entity, location, and temporal qualifiers;
- evidence_type: attribute, object, count, action, state_change, order, duration, trajectory, identity, spatial, cause, prediction, counterfactual, or negative. Use identity only for sameness across separated observations; use object for a vehicle, sign, or object's category/type;
- coverage_requirement: local_window, before_after, repeated_observation, or whole_video;
- support_test: visible outcome that satisfies the requirement;
- falsification_test: visible incompatible outcome.

Use whole_video for unscoped absence, counts accumulated across time, never, only, all, first, or last. A static scene count may use local_window when one complete view contains the full counting scope. Use before_after or stronger for state changes and causes. Use repeated_observation or stronger for identity, duration, trajectory, and cross-event order.

The falsification_test must describe visible counterevidence. Do not use missing visibility, ambiguity, occlusion, or insufficient coverage as falsification; those make the later sufficiency result unresolved.

Do not use answer choices, a proposed answer, memory, evidence IDs, timestamps, or prior conclusions. Do not decide what happened. Return only JSON conforming to evidence-plan.schema.json with plan_version "2.0" and mode "initial".
```
