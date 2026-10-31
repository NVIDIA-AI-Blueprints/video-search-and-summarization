# Worked example: Warehouse ladder Question 4

**Question:** “What was the last person who climbed the ladder wearing?”

**Question ID:** `vss-sample-warehouse-ladder-combined-g1-4`

The initial plan uses exactly two claims. Which completed climb is last, and what that person is wearing, need different evidence tests and can resolve separately: the last climber can be known while their clothing is not, or the reverse. Because `last` requires ruling out later qualifying climbs, both claims need whole-video coverage. The clothing claim names the climb claim as its prerequisite.

```json
{
  "plan_version": "2.0",
  "mode": "initial",
  "question_id": "vss-sample-warehouse-ladder-combined-g1-4",
  "question_text": "What was the last person who climbed the ladder wearing?",
  "asset_id": "vss-sample-warehouse-ladder-combined",
  "claims": [
    {
      "claim_id": "claim-last-completed-ladder-climb",
      "requirement": "Identify the person whose completed ladder climb is the last one in the video.",
      "evidence_type": "order",
      "coverage_requirement": "whole_video",
      "support_test": "Complete video coverage identifies and deduplicates every completed ladder-climb event and shows which one is chronologically last.",
      "falsification_test": "Complete video coverage shows a different person completing a ladder climb later than the candidate."
    },
    {
      "claim_id": "claim-last-ladder-climber-clothing",
      "requirement": "Determine the visible clothing of the person in claim-last-completed-ladder-climb during that last climb in the video.",
      "evidence_type": "attribute",
      "coverage_requirement": "whole_video",
      "support_test": "The last climber established by claim-last-completed-ladder-climb is visible closely enough to name their clothing.",
      "falsification_test": "That same climber is visible wearing clothing different from the candidate description."
    }
  ]
}
```

Do not create separate initial claims for the ladder, each person, or other incidental details that cannot affect the answer. Those facts stay inside the requirement sentence. Do not resolve the clothing claim while claim-last-completed-ladder-climb remains uncertain.

## Sufficiency-loop decision

Suppose memory shows a ladder climb and describes a climber's clothing, but does not establish that the memory covers every qualifying climb. That is missing evidence for `claim-last-completed-ladder-climb`, not a reason to add a third claim. The clothing claim stays unresolved while that prerequisite is uncertain. The expansion decision should request gathering against the prerequisite:

```json
{
  "decision": "gather_evidence",
  "claim_id": "claim-last-completed-ladder-climb",
  "missing_visible_fact": "Complete video coverage is needed to enumerate the ladder-climb events and establish which completed climb is last."
}
```

Likewise, if the last climb is established but the climber's clothing is occluded, create a gathering gap on `claim-last-ladder-climber-clothing`. Do not add another claim merely because one of the two requirements remains unsupported.

An expansion would be justified only if the question independently requested another reportable outcome—for example, both what the last climber wore and how many people climbed the ladder—and that second outcome had been omitted from the initial plan.
