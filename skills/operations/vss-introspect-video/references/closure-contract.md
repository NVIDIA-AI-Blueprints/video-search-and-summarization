# Experiment B: frozen requirements and evidence bindings

Before any memory retrieval, create the normal option-blind plan and a separate
`closure-plan.json` keyed by its claim IDs. Do not change the contract after init.
Declare 1..6 independently testable necessary parts per claim, not answer options.
Retain one or two claims; requirements are not extra inspection tasks.

```json
{
  "claim-direction": {
    "depends_on": [],
    "requirements": [
      {
        "requirement_id": "heading",
        "test": "Independently visible features establish the vehicle heading.",
        "applicability": {
          "subject": "The target vehicle",
          "event": "The approach requested in the question",
          "state": "Before the turn",
          "reference_frame": "Vehicle front/rear"
        }
      }
    ]
  }
}
```

This illustrates one prerequisite; add separate requirements for other necessary
facts, such as motion relative to that heading. `depends_on` names other planned
claims that must be settled, or is empty. Cycles and unknown claims are rejected.
Applicability labels identify the required subject/event/state/reference frame
from the question, not guessed observations. Use "Not applicable" where appropriate.
Code stores this contract in the canonical ledger at initialization.

For BOTH a memory update and a batch assessment, add `closure_evidence`:

```json
[
  {
    "requirement_id": "heading",
    "evidence": [
      {
        "observation_index": 0,
        "applicability": {
          "subject": "The target vehicle",
          "event": "The approach requested in the question",
          "state": "Before the turn",
          "reference_frame": "Vehicle front/rear"
        }
      }
    ],
    "coverage_sufficient": true
  }
]
```

`observation_index` indexes this update's `observations`, NOT raw attempt indices.
Alternatively use `observation_id` for an already accepted observation of this
claim. Describe the observation's ACTUAL applicability, not the desired one.
Do not copy contract labels onto an observation of a different event or state.
Code compares these labels; it cannot detect a false description of the frames.

Re-submit the complete current certificate for that claim on every update,
including still-useful prior observation IDs. Missing requirements stay unresolved.
Each requirement needs answer-bearing evidence, compatible applicability and
`coverage_sufficient: true`; context-only references are insufficient. Set false
when counts, absence, order or required temporal coverage are incomplete. Preserve
limitations in the existing `missing_evidence`/`gap` fields. Contradictions cannot
be removed by omitting their citations: all accepted observations remain in the ledger.

`contract --ledger` and `assess` expose computed `closure_blockers`. Do not edit
the ledger or certificates by hand. A blocked dependent becomes eligible once its
prerequisite and its own evidence satisfy their contracts. Failure to establish
a requirement never prevents a best-available multiple-choice answer at the end.

This adds no semantic judge and no VLM calls. Legacy ledger library functions are
retained for compatibility tests, but this build's init/publication CLI requires
the closure contract. Installed skills from another image must not override it.
