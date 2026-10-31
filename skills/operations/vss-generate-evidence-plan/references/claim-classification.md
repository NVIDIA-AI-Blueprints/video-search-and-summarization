# Minimal claim-classification rubric

Classification is based on the smallest visible outcome needed to answer the question, not every fact that could help locate it.

## Claim-count rule

The initial plan contains a minimum of two claims. Do not start with one. After a sufficiency check identifies a missing independent requirement, add one new claim at a time.

Each claim must express one specific visual fact or relationship that can be assessed separately. Keep the requirement to one sentence naming the subject, the fact to establish, and the relevant temporal scope. Split a claim when an answer-critical prerequisite and the conclusion it supports require different evidence tests and could have different resolution states. Record their dependency explicitly by naming the prerequisite `claim_id` in the dependent claim's requirement; do not resolve the dependent conclusion while its prerequisite remains uncertain. Do not create separate claims for incidental details that cannot affect the answer.

Put entity, location, temporal, and scene qualifiers inside the claim requirement or tests. Do not create a separate claim merely to establish that a stop line, intersection, person, vehicle, or other referenced object exists.

Examples:

- “What color is the car that stops before the line?” is two claims: which car stops before the line, and the color of that car. The color claim names the stopping claim as its prerequisite. The line itself is not a claim.
- “How many and which vehicles collide?” is two claims: one `count` claim and one `identity` claim, because the outputs and tests differ.
- “What caused the vehicle to stop?” is two claims: that the vehicle stops, and the visible cause of that stop. The cause claim names the stopping claim as its prerequisite. The target vehicle is a qualifier, not another claim.

## Primary evidence type

Choose the type matching the answer-bearing test:

| Type | Use when the answer requires |
|---|---|
| `attribute` | a visible property of a qualified entity |
| `object` | presence or category/type of an object, vehicle, sign, or scene element |
| `count` | the number of distinct qualifying instances |
| `action` | a defining activity or completed act |
| `state_change` | a transition between visible states |
| `order` | relative chronology of events |
| `duration` | an elapsed interval or relative length |
| `trajectory` | a path or direction through space |
| `identity` | sameness of an entity across separated observations |
| `spatial` | relative position or location |
| `cause` | a visible productive link between precursor and outcome |
| `prediction` | a visually grounded immediate continuation |
| `counterfactual` | a visually grounded alternative under a changed condition |
| `negative` | absence or non-occurrence within a declared scope |

Use this precedence when types overlap: `negative`; `counterfactual` or `prediction`; `cause` over mere `order`; `identity` only when cross-observation sameness is answer-bearing; `state_change` over `action` when the transition matters; `count` or `duration` when the number or interval is the answer; `spatial`; then `attribute` or `object`. “What type of vehicle/sign/object?” is `object`, not `identity`, unless proving it is the same entity across views is itself required.

## Coverage

- `local_window`: one bounded view can run the test.
- `before_after`: both sides of a transition are required.
- `repeated_observation`: linked observations over time are required.
- `whole_video`: the complete video or a defensibly complete question-scoped interval is required.

Use the strongest coverage needed for both support and falsification. Unscoped absence, counts accumulated across time, and `never`, `only`, `all`, `first`, or `last` normally require `whole_video`. A static scene count may use `local_window` when one complete view contains the full counting scope. Identity, duration, trajectory, and cross-event order normally require `repeated_observation`. State changes and visible causes require `before_after` or stronger.

## Tests

The support test names the visible outcome that would satisfy the claim. The falsification test names a visible incompatible outcome. Missing visibility, ambiguity, occlusion, or insufficient coverage makes a claim unresolved; it does not falsify the claim. Include all grounding qualifiers needed to avoid inspecting the wrong entity, place, or interval.

Avoid circular wording such as “the evidence confirms the claim.” For counts, mention complete scoped coverage and deduplication. For identity, mention continuity or distinguishing features. For causes, require a visible precursor or contact leading to the outcome rather than chronology alone.

## Expansion gate

After a sufficiency check identifies a missing independent requirement, add one new claim at a time. Do not add a claim because evidence is absent, ambiguous, or occluded; create an evidence-gathering gap instead. Run another sufficiency check before adding a further claim.

Valid expansion reasons include:

- one current claim contains two predicates that received different evidence assessments;
- an identity bridge is independently necessary to connect otherwise sufficient observations;
- the question requires another independently reportable outcome omitted from the initial plan.

An expansion must add exactly one claim, use a new stable ID, and leave existing claims unchanged. Re-run binding and sufficiency for the new claim before considering another expansion.
