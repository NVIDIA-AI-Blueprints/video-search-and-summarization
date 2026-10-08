# Worked example: Warehouse ladder Question 4

**Question:** “What was the last person who climbed the ladder wearing?”

**Question ID:** `vss-sample-warehouse-ladder-combined-g1-4`

The initial plan uses one claim. The requested outcome is a visible attribute: the clothing worn by a qualified person. “Last” and “climbed the ladder” identify which person must be assessed; they are temporal and action qualifiers, not separate requested outcomes. Because `last` requires ruling out later qualifying climbs, the claim needs whole-video coverage.

```json
{
  "plan_version": "2.1",
  "mode": "initial",
  "question_id": "vss-sample-warehouse-ladder-combined-g1-4",
  "question_stem": "What was the last person who climbed the ladder wearing?",
  "asset_id": "vss-sample-warehouse-ladder-combined",
  "claims": [
    {
      "claim_id": "claim-last-ladder-climber-clothing",
      "requirement": "Determine the visible clothing worn by the person whose completed ladder climb occurs last in the video.",
      "evidence_type": "attribute",
      "coverage_requirement": "whole_video",
      "support_test": "Complete video coverage identifies and deduplicates every completed ladder-climb event, establishes the chronologically last climber, and shows that person's clothing attributes.",
      "falsification_test": "Complete video coverage shows that another person completes a ladder climb later, or that the last climber visibly wears clothing with different attributes."
    }
  ]
}
```

Do not create separate initial claims for the ladder, each person, the climbing action, or the event order. Those facts are grounding requirements inside the one answer-relevant attribute claim.

## Sufficiency-loop decision

Suppose memory shows a ladder climb and describes a climber's clothing, but does not establish that the memory covers every qualifying climb. That is missing evidence for the existing claim, not a missing independent answer requirement. The expansion decision should request gathering against the same claim:

```json
{
  "plan_version": "2.1",
  "decision": "gather_evidence",
  "question_id": "vss-sample-warehouse-ladder-combined-g1-4",
  "claim_id": "claim-last-ladder-climber-clothing",
  "missing_visible_fact": "Complete video coverage is needed to enumerate the ladder-climb events, establish which completed climb is last, and inspect that climber's clothing."
}
```

Likewise, if the last climb is established but the climber's clothing is occluded, create a claim-specific gathering gap. Do not add an `order`, `action`, or `identity` claim merely because one part of the existing test remains unsupported.

An expansion would be justified only if the question independently requested another reportable outcome—for example, both what the last climber wore and how many people climbed the ladder—and that second outcome had been omitted from the initial plan.
