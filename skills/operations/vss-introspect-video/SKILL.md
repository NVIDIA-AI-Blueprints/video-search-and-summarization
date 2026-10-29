---
name: vss-introspect-video
description: Run the skill-owned evidence loop for a video question delegated by vss-ask-video. Plan option-blind claims before retrieval, bind memory, dispatch bounded one-claim inspections, merge deterministically, and return a traceable answer or explicit unresolved gaps.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  vss-requires: "vlm"
---

# Introspect video evidence

Use this skill only after `vss-ask-video` delegates a direct video question
whose answer needs an introspection loop. Direct “answer this video question”
requests still enter through `vss-ask-video`. `vss-generate-evidence-plan` is a
planner used by this workflow; it is never an answering workflow.

The top-level agent owns planning, the canonical ledger, memory binding, gap
assessment, task dispatch, result collection, deterministic merge, stop
decisions, final synthesis, and run artifacts. VSS owns only ordinary
evidence-producing commands:

- `vss memory get` and `vss memory query`;
- `vss vios list`, `vss vios timeline`, and other media lookup;
- `vss vlm run`;
- normal persistence of each VSS job when configured.

Do not use `vss memory introspect`, the VSS Agent `/generate` endpoint, raw
service HTTP, direct Elasticsearch, or a hand-built model request. Never add or
expect a VSS introspection job. Claims, ledgers, gaps, subagents, and rounds
remain invisible to the VSS CLI and libraries.

## Contracts and configuration

`config/ledger-budgets.json` is the only numeric budget source; never copy its
values into prompts or other configuration. Never exceed any maximum loaded
from that file. The initialized ledger records every effective value in
`budgets`. Task allocation and stopping use those configured limits. Failed
inspections and retries count toward `vlm_calls_used`.

All objects are strict: every listed field is required unless marked optional,
and unknown fields are rejected.

### Evidence plan

The evidence plan fields are:

- `plan_version`: `"2.0"`;
- `mode`: `"initial"` or `"expansion"`;
- `question_id`, `question_text`: non-empty strings;
- `asset_id`: string or null;
- `claims`: one or two initial claims, or exactly one expansion claim.

Each claim has `claim_id`, `requirement`, `evidence_type`,
`coverage_requirement`, `support_test`, and `falsification_test`.
`claim_id` is a stable `claim-<descriptive-slug>`. Allowed evidence types are
`attribute`, `object`, `count`, `action`, `state_change`, `order`, `duration`,
`trajectory`, `identity`, `spatial`, `cause`, `prediction`, `counterfactual`,
and `negative`. Allowed coverage is `local_window`, `before_after`,
`repeated_observation`, or `whole_video`.

### Observations and provenance

Every observation has exactly `observation_id`, `claim_id`, `relation`, `text`,
and `source`. Relation is `supports`, `contradicts`, or `context`.
`observation_id` is the utility-generated stable `obs-<24 hex>` content ID.

A memory source has `type: "memory"`, non-empty `record_id`, and optional
`job_id`, `sensor_id`, `start`, and `end`. Start and end must appear together.

A VLM source has `type: "vlm"`, non-empty `job_id`, and exactly one provenance
selector matching the inspected media:

- sensor: `sensor_id`, `start`, and `end`;
- URL: `media_url`;
- file: `path`.

Sensor times are timezone-aware ISO-8601 values with `start < end`. A media URL
is absolute HTTP(S). Never fabricate sensor or time fields for URL or file
inspection.

### Ledger

The ledger has exactly `ledger_version`, `revision`, `plan`, `claims`,
`observations`, `round`, `expansions_used`, `vlm_calls_used`, `budgets`,
`status`, and `stop_reason`. `budgets` is the effective snapshot of
`config/ledger-budgets.json`. `ledger_version` is `"1.0"`. Status is `in_progress`, `answered`,
or `unresolved`; stop reason is null, `resolved`, `no_progress`,
`budget_exhausted`, or `tool_failure`.

Each claim state has `claim_id`, `status`, `coverage`, `observation_ids`, and
`gap`. Claim status is `supported`, `contradicted`, or `unresolved`; coverage
is `none`, `partial`, or `sufficient`. The original plan claims are immutable,
accepted observations are append-only, and terminal ledgers cannot be merged
or expanded.

### Inspection task and result

Each task has exactly `task_id`, `base_revision`, `claim`, `gap`,
`existing_observations`, `asset_id`, `media_scope`, and `max_vlm_calls`.
`media_scope` is exactly one of:

- `{"type":"sensor","sensor_id":"...","start":"...","end":"..."}`;
- `{"type":"media_url","media_url":"https://..."}`;
- `{"type":"file","path":"..."}`.

Each result has exactly `task_id`, `base_revision`, `observations`, `coverage`,
`gap`, `vlm_calls_used`, and `error`. Result observations must be VLM
observations for the assigned claim and media scope. Sensor observations may
use a subwindow within the assigned window; URL and file provenance must match
the assigned selector exactly. Errors are strings or null and never evidence.

### Count inventory

A result for a `count` claim may add an optional `count_state` with exactly
`candidates`, `open_gaps`, and `resolved_gaps`. Report one for every count
inspection. It is how a count claim records what was seen, what was not, and
whether the total is exact. Other claim types must omit it.

Each candidate has `candidate_id` (`evt-<slug>`), `status`, `counted`,
`description`, and `justification`, plus optional `observation_ids`, `spans`
(`[{"start","end"}]` ISO-8601), and `duplicate_of`. Status is:

- `observed`: the qualifying event is visible; cite its observation IDs;
- `inferred`: not directly visible but implied by visible before and after
  states; say why in `justification`;
- `unresolved`: something happened that may or may not qualify;
- `duplicate`: the same event seen again, for example in an overlapping
  window; set `duplicate_of` to the original candidate;
- `excluded`: seen, but it does not meet the claim's definition.

Keep the inventory compact: one sentence each for `description` and
`justification`, citing observation IDs and spans instead of restating VLM
text. Report activity that does not qualify as a few grouped `excluded`
candidates, not one per moment.

Only `observed` and `inferred` candidates may be `counted`. A disappearance,
retrieval, or other visible change is not counted merely because it occurred;
count it only when the visible evidence meets the claim's definition.

Each open gap has `gap_id` (`gap-<slug>`), `kind`, `description`, and
`affects_answer`, plus an optional `start`/`end`. Kinds are
`unobserved_interval`, `obscured_interval`, `temporal_boundary`,
`identity_ambiguity`, and `qualification_ambiguity`. Set `affects_answer`
true only when the gap could change the total, for example an occluded
interval where a qualifying event could occur. An occlusion that cannot hide a
qualifying event is not answer-affecting. Resolve a prior gap only with new
evidence, listing `{"gap_id","resolution"}` in `resolved_gaps`.

The merge keeps candidates by ID, with the newest report replacing an earlier
one, and carries every unresolved prior gap forward. Tasks for that claim
include the canonical `count_state`. While an answer-affecting gap or an
unresolved candidate remains, the claim cannot be `sufficient`: a reported
`sufficient` is lowered to `partial` and the claim records `coverage_cap`.
`assess` and the final result report `count_assessment`: the counted total,
how many were observed or inferred, and `count_kind` of `exact` or
`lower_bound`. A lower bound is not an exact total.

### Final result

The final result keeps decision and evidence state separate. It includes
`status`, `evidence_status`, `answer`, `decision_source`, `evidence`,
`evidence_details`, `unresolved_gaps`, `revision`, and `artifact_dir`.
`answer` is the complete text supplied by the top-level agent, or null when
abstaining. The utility does not add a letter, infer options, or join a label
to an explanation.

A resolved ledger with an answer has `evidence_status: resolved` and
`decision_source: introspection`. A terminal unresolved ledger with a supplied
answer has `status: answered`, `evidence_status: unresolved`, and
`decision_source: best_available_answer`, and it keeps the unresolved gaps.
That includes a choice question answered with no accepted observations.
An abstention omits the answer: `status` and `evidence_status` stay
`unresolved`, `answer` is null, and `decision_source` is `abstention`. Use
abstention only when the question does not ask for one of its choices. A
ledger with `requires_choice: true` cannot abstain: `final-result` without
`--answer` is rejected.
Every gap has `claim_id`, `gap`, and one reason: `insufficient_coverage`,
`not_visible`, `tool_failure`, or `budget_exhausted`.

Use the utility for canonical state transitions:

```bash
LEDGER_TOOL="skills/operations/vss-introspect-video/scripts/evidence_ledger.py"

python3 "${LEDGER_TOOL}" init \
  --plan "${RUN_DIR}/plan.json" \
  --output "${RUN_DIR}/ledger.json"

# When the caller listed choices or asked for a letter, mark the ledger so
# abstention is refused. Pass the original question if the plan text is
# option-blind.
python3 "${LEDGER_TOOL}" init \
  --plan "${RUN_DIR}/plan.json" \
  --requires-choice \
  --original-question "${ORIGINAL_QUESTION}" \
  --output "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" merge-memory \
  --ledger "${RUN_DIR}/ledger.json" \
  --updates "${RUN_DIR}/memory-updates.json" \
  --output "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" create-tasks \
  --ledger "${RUN_DIR}/ledger.json" \
  --media-scopes "${ROUND_DIR}/media-scopes.json" \
  --output "${ROUND_DIR}/tasks.json"

python3 "${LEDGER_TOOL}" merge-round \
  --ledger "${RUN_DIR}/ledger.json" \
  --base-ledger "${ROUND_DIR}/base-ledger.json" \
  --tasks "${ROUND_DIR}/tasks.json" \
  --results-dir "${ROUND_DIR}" \
  --output "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" assess --ledger "${RUN_DIR}/ledger.json"

# Answered result. Pass the complete answer text and the observation IDs the
# synthesis actually used. Do not expand an unset variable into an empty flag.
python3 "${LEDGER_TOOL}" final-result \
  --ledger "${RUN_DIR}/ledger.json" \
  --artifact-dir "${RUN_DIR}" \
  --answer "The accepted observations support the yellow vest." \
  --evidence-id "${OBSERVATION_ID}" \
  --output "${RUN_DIR}/final-result.json"

# Choice question with incomplete evidence: still pass --answer. Omit
# --evidence-id only when no accepted observation exists.
python3 "${LEDGER_TOOL}" final-result \
  --ledger "${RUN_DIR}/ledger.json" \
  --artifact-dir "${RUN_DIR}" \
  --answer "C. yellow" \
  --output "${RUN_DIR}/final-result.json"

# Open-ended abstention only. Omit --answer entirely. Do not pass an empty
# string. Do not use this path when the question asks for one of its choices.
python3 "${LEDGER_TOOL}" final-result \
  --ledger "${RUN_DIR}/ledger.json" \
  --artifact-dir "${RUN_DIR}" \
  --output "${RUN_DIR}/final-result.json"
```

Only the top-level agent invokes a command that writes `ledger.json`.
Subagents write only their assigned result artifact.

## Run artifacts

Use the caller-provided per-run artifact directory when it provides one.
Otherwise use:

```text
${VSS_WORKSPACE:-$HOME/.vss}/runs/vss-introspection/<question-id>/
├── plan.json
├── ledger.json
├── final-result.json
├── terminal-commit.json
└── rounds/
    ├── round-1/
    │   ├── base-ledger.json
    │   ├── media-scopes.json
    │   ├── tasks.json
    │   ├── task-<claim-id>.json
    │   ├── results.json
    │   └── result-<claim-id>.json
    ├── round-2/
    └── round-<n>/
```

`round-<n>` continues through the configured `max_inspection_rounds`. The tree
does not cap the run at three rounds.

Freeze `base-ledger.json` before dispatch. Atomically replace `ledger.json` and
`final-result.json`; publish `terminal-commit.json` last with the committed
revision and hashes. Never write the ledger to `MEMORY.md`, VSS memory,
Elasticsearch, or Git.

## Workflow

### 1. Freeze the question and grounded media scope

Retain the verbatim question, stable question ID, optional asset ID, allowed
visual modalities, and grounded media selectors. Keep answer choices out of
planning, retrieval, subagent prompts, and VLM prompts. The top-level agent may use them only during final synthesis.

Do not invent timestamps from “recently”, “this morning”, or similar relative
phrases. A named sensor remains a sensor. For a local file, resolve the
user-named relative path and require the real path to remain beneath the
working directory.

### 2. Plan first and option-blind

Invoke `vss-generate-evidence-plan` before reading Markdown memory, querying VSS
memory, looking at a VIOS timeline, selecting timestamps, or inspecting video.
Planner input may contain only:

- question ID and verbatim question text;
- optional asset ID;
- allowed visual modalities;
- task constraints.

Never send answer choices, labels, proposed answers, prior conclusions, memory
records, evidence, or selected timestamps. Reject claims requiring unavailable
audio, speech, ASR, transcripts, or external subtitles. OCR is allowed only
when visible text is an explicitly allowed modality.
The evidence planner never answers the question.

Apply these claim-classification rules:

- classify the smallest visible answer-bearing outcome, not every locating fact;
- use exactly one strict evidence type and one strict coverage requirement;
- default to one initial claim;
- allow a second initial claim only for two independently reportable outcomes
  requiring independent tests or coverage;
- keep entity, location, event, and temporal grounding inside the requirement
  and tests;
- treat missing or ambiguous evidence as a gathering gap, not a new claim;
- require visible productive evidence for `cause`, not chronology;
- use `negative` for absence and `whole_video` for unscoped absence,
  exhaustive counts, `never`, `only`, `all`, `first`, or `last`;
- use `before_after` or stronger for state changes and causes;
- use `repeated_observation` or stronger for identity, duration, trajectory,
  and cross-event order.

Choose the evidence type by the answer-bearing test:

- `attribute`: visible property of a qualified entity;
- `object`: presence or category of an object, vehicle, sign, or scene element;
- `count`: number of distinct qualifying instances;
- `action`: defining activity or completed act;
- `state_change`: transition between visible states;
- `order`: relative event chronology;
- `duration`: elapsed interval or relative length;
- `trajectory`: path or direction through space;
- `identity`: sameness across separated observations;
- `spatial`: relative position or location;
- `cause`: visible productive link between precursor and outcome;
- `prediction`: visually grounded immediate continuation;
- `counterfactual`: visually grounded alternative under a changed condition;
- `negative`: absence or non-occurrence within a declared scope.

When types overlap, prefer `negative`; then `counterfactual` or `prediction`;
`cause` over mere `order`; `identity` only when sameness is answer-bearing;
`state_change` over `action` when transition matters; `count` or `duration`
when the number or interval is the answer; then `spatial`, `attribute`, or
`object`. Type/category questions are `object`, not `identity`.

Coverage means:

- `local_window`: one bounded view can run the test;
- `before_after`: both sides of a transition are required;
- `repeated_observation`: linked observations over time are required;
- `whole_video`: complete video or defensibly complete scoped interval.

Support tests name visible satisfying outcomes. Falsification tests name
visible incompatible outcomes. Occlusion, blur, incomplete coverage,
ambiguity, VLM uncertainty, timeout, and tool failure do not falsify a claim.
Avoid circular tests. Counts require complete scoped coverage and
deduplication; identity requires continuity or distinguishing features; cause
requires a visible precursor or contact leading to the outcome.

Validate the plan, save it as `plan.json`, and initialize `ledger.json`.

### 3. Retrieve and bind memory

Only after planning, search:

1. local Markdown memory, when available;
2. structured VSS memory, when configured and needed.

Use exact identity for a known record and the complete project-local CLI
invocation:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)

"${VSS[@]}" memory get \
  --job-id "${JOB_ID}" \
  --record-type "${RECORD_TYPE}" \
  --record-id "${RECORD_ID}"
```

Use `vss memory query` for grounded discovery. Branch on the CLI exit code; an
empty result at exit zero is valid. Do not retry or fall back to a backend.

Convert only useful visible facts to observations. Preserve memory `job_id`,
`record_id`, sensor, and ISO-8601 window when available. Observation text
describes the visible fact, never an answer choice or unsupported conclusion.
Retrieval misses, absent fields, ambiguity, and command failures are gaps, not
contradicting observations.

For each claim, the top-level agent interprets accepted facts against its
support test, falsification test, and coverage requirement, then supplies one
memory update with observations, coverage, and the current gap. Merge all
memory updates in one canonical revision and reassess. If the deterministic
gate passes, skip visual inspection. Never expose answer choices to an
inspection subagent or VLM prompt.

### 4. Select bounded inspection work

Create one utility-generated task per claim that still needs evidence. A claim
still needs evidence when it is `unresolved`, or when it is `supported` or
`contradicted` but its coverage is not `sufficient`. Derived support is not
completion. While round or VLM-call budget remains, keep that claim eligible
and inspect the uncovered interval. Every task:

- targets one existing claim;
- carries the complete evidence-plan claim unchanged;
- carries the current gap and accepted observations;
- uses the same frozen `base_revision` from the canonical ledger;
- embeds one strict immutable media scope: sensor plus bounded ISO-8601 window,
  media URL, or local file;
- receives an allocation from the remaining canonical budget.

Use `vss vios list` and `vss vios timeline --sensor NAME` only after planning
to ground sensor identity and useful ISO-8601 windows. Window selection may be
agent-driven. Write the chosen strict selectors to `media-scopes.json` before
creating the task batch. The file must map each selected claim ID to
exactly one scope, so parallel claims may use different media or windows. Do
not alter a task's embedded scope after dispatch. For `whole_video`, inspect
useful non-overlapping bounded windows; do not issue one arbitrarily large VLM
request. Report partial coverage unless the accepted windows defensibly cover
the complete grounded interval, and keep uncovered intervals as explicit gaps.
These are ordinary `vss vios` lookups, not an orchestration service.

Choose `VLM_FPS` from the inspection round. Do not use one fixed density for
every round:

- **First visual round — skim (`0.5`)**: cover the still-uncovered interval
  with the widest useful bounded windows. Skim only to locate whether, or
  roughly when, the missing visible fact occurs. Do not start at inspect
  density.
- **Later rounds — shorter window, higher FPS**: do not re-skim the same
  broad interval. Narrow to shorter windows around remaining gaps, candidate
  event times, or occluded regions from the skim. Raise FPS to **locate
  (`1`)** for event and action confirmation, then **inspect (`2`)** for
  labels, clothing, object state, or precise spatial relationships. Prefer a
  shorter grounded window before increasing density.

RT-VLM keeps the requested FPS only while `fps × clip_seconds` is at most 60
frames. The helper splits longer assigned scopes at that cap. A later round
at a higher FPS or a shorter window is a new inspection, not a duplicate of
the skim.

Before dispatching a sensor inspection, use the deterministic window planner:

```bash
VLM_HELPER="skills/operations/vss-introspect-video/scripts/vlm_inspection.py"
VLM_FPS=0.5 # first visual round: skim. Later rounds: 1 then 2 on a shorter window

python3 "${VLM_HELPER}" plan-windows \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --fps "${VLM_FPS}" \
  --output "${ROUND_DIR}/vlm-windows-${CLAIM_ID}.json"
```

The helper splits the assigned scope so every request remains within RT-VLM's
frame-delivery cap. If the allocated call budget cannot cover every planned
window, coverage remains partial and uninspected windows stay in the gap.

### 5. Dispatch bounded parallel subagents

Spawn no more subagents than the loaded parallelism limit. Give each one task
containing one claim ID, its immutable `media_scope`, and a result path. Instruct
it to:

- read only its claim, gap, existing observations, asset ID, media scope, and
  coverage;
- inspect only the missing visible fact in the assigned media scope;
- use targeted `vss vlm run` calls within its allocation; never search memory,
  summarize broadly, include answer choices, or spawn agents; for sensor work,
  invoke VLM only through the guarded helper below, never directly;
- report visible facts against the support and falsification tests, leaving
  missing visibility, ambiguity, occlusion, failure, and incomplete coverage
  unresolved; an occlusion, an ambiguous view, or an unobservable requested
  attribute is a limitation, not proof that the attribute or event was absent;
- preserve VSS job ID and the exact assigned provenance selector: sensor plus
  ISO-8601 window, media URL, or file path;
- for a count claim, return `count_state` as described under Count inventory:
  start from the task's `count_state` when present, list each candidate event
  and each interval where a qualifying event could be hidden or cut off, and
  resolve a prior gap only with new visible evidence;
- put tool failures in `error`, never in `observations`; exit 6 may retain
  usable evidence while recording the persistence limitation;
- return exactly one contract-valid result without changing claims, canonical
  state, or the final answer;
- write the result file directly from the inspection output, without pasting
  raw VLM answers or the whole result into a message; end with the result path
  and a few lines of summary.

For sensor work, use the helper instead of one unbounded VLM invocation.
Pass the round's `VLM_FPS` chosen above:

```bash
python3 "${VLM_HELPER}" inspect \
  --vss-project "${VSS_REPO_ROOT}/libs/vss" \
  --task "${ROUND_DIR}/tasks.json" \
  --task-id "${TASK_ID}" \
  --sensor "${SENSOR_NAME}" \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --fps "${VLM_FPS}" \
  --prompt "${OPTION_BLIND_CLAIM_PROMPT}" \
  --output-mode "${VLM_OUTPUT_MODE:-legacy}" \
  --calls-budget "${TASK_MAX_VLM_CALLS}" \
  --history "${PRIOR_INSPECTION_PATH}" \
  --recording-start "${RECORDING_START}" \
  --recording-end "${RECORDING_END}" \
  --output "${ROUND_DIR}/inspection-${CLAIM_ID}.json"
```

Take `RECORDING_START` and `RECORDING_END` from `vss vios timeline` for the
sensor. Pass both or neither. With both, the helper tells the VLM where each
subwindow sits in the recording and how long the recording lasts, so it does
not treat a subwindow as the whole video. This is the grounded media interval,
never a human-verified event time or count.

`OPTION_BLIND_CLAIM_PROMPT` is the agent-written inspection focus for this claim.
It is not a fixed dataset prompt. Customize it from the assigned claim and the
current evidence gap: which entities must be distinguished, which observable
action, attribute, transition, or relationship is missing, what must be visible
before, during, and after that event, and which uncertainty still needs
inspection. Keep it neutral and option-blind. Do not assert that an event
happened merely because the question asks about it. Use the claim's evidence
type only as generic guidance:

- count: enumerate distinct qualifying events and distinguish incomplete events;
- order: describe completed events and their source and destination states;
- identity: preserve distinguishing features and visible continuity;
- spatial or trajectory: name the reference frame and the relevant heading;
- attribute: require a clear view of the correct entity and the relevant region;
- cause: describe visible physical links, not merely temporal succession.

`--output-mode` selects how the helper asks the VLM to report. `legacy` is the
default and the control: the helper sends the inspection focus unchanged.
`timestamped-caption` is the experiment. Set `VLM_OUTPUT_MODE=timestamped-caption`
only for that arm. The helper keeps the inspection focus and appends caption
reporting instructions separately to each submitted subwindow, using that
subwindow's duration. The requested report shape is
`[start_seconds:end_seconds] caption`, with offsets from the start of that
submitted subwindow. One caption line per visible event or unchanged state;
do not put a sequence of events inside one interval, and do not copy the
window duration as `[0:duration]` unless that fact is unchanged for the whole
span. The inspection artifact records `output_mode`, the inspection focus, the
exact effective prompt and its hash, the raw answer, normalized text, the
request window, FPS, frame limit, job ID, attempt number, exit status, and
call accounting. The helper does not validate caption syntax after the VLM
returns. In timestamped mode it adds `caption_annotations`, counting caption
lines, uncaptioned lines, spans outside the subwindow, and reversed spans.
These timestamps are `model_generated`; the annotation never changes
`usable` and is not a reason to discard output. Well-formatted captions are not visual correctness,
exhaustive coverage, or claim sufficiency. Absence from the sampled captions
is not evidence that an event did not occur. When a caption visibly supports
or contradicts the claim, put the timestamped line in the existing observation
`text` field. Do not add caption-metadata fields to ledger observations or
task results.

Each attempt separates what was requested from what was verified.
`requested_sampling` holds the requested window, FPS, and frame count.
`input_manifest` holds the measured input when the helper sampled the frames
itself: clip hash and probed duration, the actual frame count, and each frame's
hash, size, dimensions, and decoder timestamp in clip seconds. Measured frame
timestamps are separate from the model's caption timestamps. When a service
samples frames internally, `input_manifest.basis` is `unavailable`; do not
infer actual frames from the request. `request_metadata` and
`response_metadata` record the requested and returned model, response ID,
status, token usage, and the returned reasoning configuration. Reasoning
content is never stored; only a hash of the raw response is kept. A remote VLM
configuration with `"retain_frames": true` also copies the sampled frames
beside the inspection artifact for audit.

Timestamped mode is supported on the guarded sensor helper above. A direct
`vss vlm run` with `--media-url` or `--file` does not attach the caption
contract. Do not use that direct command to bypass timestamped mode. URL and
file scopes are unsupported for this experiment; leave the claim gap explicit
instead of inventing a sensor ID or an absolute source time.

Omit `--history` when no prior inspection artifact exists; repeat it for every
prior round that targeted the claim. The helper rejects an exact duplicate
sensor/window/FPS/effective-prompt/claim call before inference. The effective
prompt includes the caption contract and any repair instructions, so a legacy
answer is not timestamped-mode evidence for the same window and claim. A usable prior attempt
for that same claim is returned in `reused_evidence` with its original
observation ID, job ID, and provenance; it counts as inspected and does not
spend a new VLM call. Do not insert that evidence again. A failed or unusable
prior attempt does not establish coverage. A different sensor, claim, sampling
rate, or prompt is not coverage. A higher-density refinement or materially
different evidence prompt is not a duplicate.

The helper records every attempted subwindow, detects empty, reasoning-only,
repetitive-template, and reverse-chronology output. It does not check whether
timestamped captions followed the requested `[start:end]` shape. It attempts
each affordable uncovered window once before spending remaining allocation on
at most one quality retry per unusable window. This prevents a degenerate
early window from starving later windows. Every new inference attempt,
including a quality retry, counts toward `vlm_calls_used`. Reused evidence
does not. The retry uses an explicit repair prompt, so it is not rejected as a
duplicate. Unusable output is retained for diagnostics but never becomes an
observation. `window_inspection` reports only whether planned windows produced
usable text. `claim_sufficiency` is always null: returned text, including a
report that the subject is occluded, does not make the claim sufficient.
Convert a usable attempt into an observation only when it visibly supports or
contradicts the claim. When the requested fact is not visible, keep the claim
unresolved and record the limitation in the gap; do not treat occlusion
language itself as unusable text or as proof of absence.

For a sensor scope, `--task` is mandatory. Before any inference call, the
helper loads that one task and rejects a missing or ambiguous task ID, a
non-sensor scope, a mismatched sensor, a requested interval that is reversed
or not entirely inside the task's immutable window, and a call allowance
above the task allocation. It requests `vss vlm run --raw` and still accepts
a pretty-printed answer body followed by the compact completion marker; it
does not treat the marker as the answer. When the caller supplies
`.vss/introspection-attempt.json`, the helper also requires both the task
sensor and requested sensor to match that attempt's video ID or sensor UUID.
An internally consistent task for a different video is therefore rejected
before inference.
Every returned observation must also use the assigned sensor and remain within
the assigned window; merge validation rejects mismatches.

The direct command below illustrates the subprocess shape used by the helper;
do not invoke it directly for sensor tasks:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)

VLM_FPS=0.5 # first visual round: skim. Later rounds: 1 then 2 on a shorter window
"${VSS[@]}" vlm run \
  --prompt "${OPTION_BLIND_CLAIM_PROMPT}" \
  --sensor "${SENSOR_NAME}" \
  --start-time "${START_TIME}" \
  --end-time "${END_TIME}" \
  --fps "${VLM_FPS}"
```

For non-sensor input, the illustration would replace the sensor and time flags
with exactly one of `--media-url "${MEDIA_URL}"` or `--file "${FILE_PATH}"`.
That direct path is not a timestamped-caption inspection. Use the guarded
sensor helper when the experiment is enabled.

### 6. Collect and merge one complete round

Wait for every assigned subagent or the caller timeout. If a subagent does not
return, write a result for its assigned task with no observations, retained
coverage/gap, zero consumed calls when known, and a timeout error. Preserve
successful sibling results.

Each subagent persists `result-<claim-id>.json` independently. On restart,
rediscover those files, resume only missing tasks, and create explicit timeout
results for tasks that remain missing at the round deadline. Validate every
result against its task, then batch-merge the complete result set from the
frozen `base-ledger.json`. Re-running the same complete batch is idempotent and
returns the already committed revision without incrementing round or call
counts. The utility rejects stale, unknown, duplicate, cross-claim,
over-budget, or malformed results; deterministically deduplicates observations;
updates only assigned claim states; preserves support and contradiction; and
increments the call count, `round`, and `revision` exactly once.

Never merge arbitrary ledger copies from subagents and never merge results in
completion order.

### 7. Reassess, optionally expand, and stop

The deterministic sufficiency gate passes only when every claim:

- is `supported` or `contradicted`;
- has `sufficient` coverage;
- cites at least one accepted observation;
- does not contain both supporting and contradicting observations;
- for a count inventory, has no answer-affecting open gap or unresolved
  candidate.

If the gate passes, the ledger becomes `answered` with stop reason `resolved`.
If a round adds no observation and improves no coverage, it stops unresolved
with `no_progress`. Exhausted round or global-call budget stops with
`budget_exhausted`, including when a claim is only partially covered. If every
attempted inspection fails and no useful evidence exists, it stops with
`tool_failure`.

A supported or contradicted claim whose coverage is still `partial` or `none`
is not finished. If budget remains, create another task for it using a shorter
window and higher FPS targeted at the remaining gap, not another full-interval
skim. If no budget remains, stop. Do not answer from a note while the ledger
is `in_progress`.

#### One bounded refinement for an open count gap

When a count claim still has an answer-affecting open gap, you may attach one
refinement to that claim's next task. Do this only when another view could
plausibly reveal the missing evidence. An interval that stays fully occluded
from this camera cannot be revealed by more frames; leave it as a gap. Write
`refinement.json`:

```json
{
  "claim_id": "claim-...",
  "gap_ids": ["gap-..."],
  "missing_evidence": "What visible fact is still missing.",
  "rationale": "Why a different view could show it.",
  "strategy": "narrower_denser",
  "fps": 2,
  "prior_request": {"start": "...", "end": "...", "fps": 0.5}
}
```

Then pass it to `create-tasks --refinement refinement.json`, with that claim's new
window in `media-scopes.json`. `prior_request` is the earlier window and FPS
that left the gap open. Strategies:

- `narrower_denser`: a shorter window inside the prior one, at a higher FPS;
- `adjacent_context`: time outside the prior window, such as just before or
  after an occlusion, to see before and after states;
- `boundary_check`: a shorter window that contains a gap's start or end, to
  settle whether an event falls inside the asked interval.

The utility rejects an identical or immaterial retry, a window that does not
reach a targeted gap, a gap that is not open and answer-affecting, a request
whose frames exceed the task's call allocation, and a second refinement. It
also rejects a refinement when less than five minutes remain before the
deadline given with `--deadline` or set by the runtime policy. The task records
the claim and gaps, the missing evidence, the rationale, what changed from the
prior request, and the remaining call, round, frame, and time budget. Inspect
that task at exactly the refinement's `fps`; the helper refuses another rate.
The merged ledger keeps the record and its outcome in `refinements`.

A round that adds no observation and no coverage normally stops with
`no_progress`. If an answer-affecting count gap is still open, budget remains,
and no refinement has been used, the ledger instead stays `in_progress` once
and records `no_progress_continuation`. `assess` then reports
`refinement_only`. The next batch must be exactly one refinement task. If no
materially different view is plausible, do not invent one: call
`final-result`, which records the `no_progress` stop. This continuation is
granted at most once and is never reset.

Invoke planner expansion mode only when an independently assessable answer
requirement is absent from the plan. Pass the prior plan and demonstrated
planning gap, still option-blind. An expansion adds exactly one new claim and
must preserve every existing claim ID and all evidence. Accept no more than the
loaded expansion and total-claim budgets. Bind memory and reassess the new
claim before inspecting it.

If the run remains in progress, perform the next bounded round on the narrower
unresolved interval at higher FPS. Otherwise stop. Do not turn an unresolved
gap into an observation.

### 8. Synthesize and write the final result

Only the top-level agent synthesizes the user answer. When an answer cites
accepted observations, pass those IDs with repeated `--evidence-id` flags.
Do not cite context observations or observations for other claims unless they
are the evidence the synthesis actually used. The utility rejects unknown IDs
and does not fill citations from the rest of the ledger. Never fabricate
citations.

Answer the original question using accepted evidence and follow the caller’s
requested response format.

When the question asks for one of its choices, choose one. Do not leave that
question unanswered. Mark the ledger with `--requires-choice` at `init`.
Compare the full meaning of each option against the evidence rather than
matching isolated words. Pass the complete option text with `--answer`. If the
evidence is incomplete, conflicting, not visible, the inspection budget is
exhausted, or every inspection failed, still select the choice best supported
by what is known. Cite `--evidence-id` for any accepted observations used in
that comparison. If no accepted observation exists, still pass `--answer` and
omit `--evidence-id`. The helper rejects a missing `--answer` on a choice
ledger. The result records `decision_source: best_available_answer`, keeps
`evidence_status: unresolved`, and keeps the unresolved gaps.

When the question does not ask for a choice, answer naturally without
inventing options or labels. Pass `--answer` only when accepted observations
support the text, and cite those IDs. If no answer is supported, omit
`--answer` and abstain. Never pass an empty string.

Only the top-level agent synthesizes the answer and performs any requested
option selection. Planning and inspection remain option-blind.

A mentioned list is not a choice question unless the question asks you to
select one of those items. Do not hardcode benchmark names or letter-only
output. Letter, numeric, and word options are all ordinary answer text. Pass
that text with `--answer`. A resolved ledger records
`decision_source: introspection`. A terminal unresolved ledger with a supplied
answer records `decision_source: best_available_answer`, keeps
`evidence_status: unresolved`, and keeps the gaps. Supplying an answer does
not resolve claims or the ledger. An omitted answer records
`decision_source: abstention` and `answer: null`. Omit `--answer` only for
questions that do not ask for a choice.

Write `final-result.json` with `scripts/evidence_ledger.py final-result` before
any user-facing answer. Do this for every outcome: resolved, partial coverage
stopped by `budget_exhausted`, `no_progress`, and `tool_failure`. The command
persists a budget stop when no inspection budget remains, then writes the
file. It refuses an in-progress ledger that still has budget; create the next
task instead of writing a synthesis note. `merge-round` also writes
`final-result.json` and a **provisional** `terminal-commit.json` when the
merged ledger is unresolved **and does not require a choice**. That pair is a
crash-safety fallback, not a finished choice. Choice ledgers never publish a
harvestable abstention; `merge-round` writes the ledger only, and you must
call `final-result` with `--answer` and the complete option text. That
overwrite publishes a non-provisional marker. Do not omit `--answer` for a
choice question; the helper refuses it. Consumers must not harvest a
provisional marker while the agent is still working. If the session goes idle
with no overwrite, harvest the provisional pair as an abstention only for
open-ended questions.

When the question does not ask for a choice, pass `--answer` only when
accepted observations support an answer. If no answer is supported, omit
`--answer` and abstain. Never pass an empty string. Every unresolved run
reports each claim gap using only:

- `insufficient_coverage`;
- `not_visible`;
- `tool_failure`;
- `budget_exhausted`.

Do not suppress gaps, present inference as observed evidence, or present
context observations as answers. Report whether each cited source is memory or VLM and preserve VSS
job, record, and assigned media provenance. `final-result.json` must be
self-contained: include the final ledger revision, run artifact directory, and
full provenance for every cited observation.

Terminal finalization commits the revision-matched `ledger.json` and
`final-result.json`, then publishes `terminal-commit.json` last. Consumers trust
a terminal result only when the marker's revision and hashes match both files.
A marker with `"provisional": true` is hash-valid but not done: wait for a
non-provisional overwrite or idle. If finalization is interrupted, rerun the
same command to repair the pair without advancing the ledger or consuming
additional calls.
