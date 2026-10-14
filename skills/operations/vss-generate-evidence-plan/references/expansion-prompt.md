# Claim-expansion prompt

Use only after a sufficiency check identifies a missing independent requirement.

```text
Decide whether this evidence plan requires one additional claim.

question_id: {{question_id}}
question_stem: {{question_stem}}
current_plan: {{current_plan_json}}
claim_ledger: {{claim_ledger_json}}
identified_insufficiency: {{identified_insufficiency}}

Add a claim only when the current plan cannot express a necessary independently assessable answer requirement. Missing, ambiguous, occluded, or contradictory evidence for an existing claim is an evidence-gathering gap, not a new claim.

The question_stem has been sanitized before this prompt and must not contain answer choices, answer labels, or a proposed answer.

If expansion is justified, return exactly one new claim with a new stable claim_id, requirement, evidence_type, coverage_requirement, support_test, and falsification_test. Do not rewrite existing claims. Return a JSON object conforming to evidence-plan.schema.json with plan_version "2.1" and mode "expansion".

If expansion is not justified, return {"plan_version":"2.1","decision":"gather_evidence","question_id":"<question id>","claim_id":"<existing claim>","missing_visible_fact":"<specific gap>"}. This object must also conform to evidence-plan.schema.json.
```
