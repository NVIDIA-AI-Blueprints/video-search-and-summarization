# Claim-expansion prompt

Use only after a sufficiency check identifies a missing independent requirement.

```text
Decide whether this evidence plan requires one additional claim.

question_id: {{question_id}}
question_text: {{question_text}}
current_plan: {{current_plan_json}}
claim_ledger: {{claim_ledger_json}}
identified_insufficiency: {{identified_insufficiency}}

Add a claim only when the current plan cannot express a necessary independently assessable answer requirement. Missing, ambiguous, occluded, or contradictory evidence for an existing claim is an evidence-gathering gap, not a new claim.

If expansion is justified, return exactly one new claim with a new stable claim_id, requirement, evidence_type, coverage_requirement, support_test, and falsification_test. Do not rewrite existing claims. Return a JSON object conforming to evidence-plan.schema.json with plan_version "2.0" and mode "expansion".

If expansion is not justified, return {"decision":"gather_evidence","claim_id":"<existing claim>","missing_visible_fact":"<specific gap>"}.
```
