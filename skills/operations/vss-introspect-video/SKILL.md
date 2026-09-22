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

This `SKILL.md` is the complete agent-facing workflow and contract. Do not load
separate reference files or infer fields beyond those listed here.
`scripts/evidence_ledger.py` is the deterministic implementation.
`config/ledger-budgets.json` is the only numeric budget source; never copy its
values into prompts or other configuration. Never exceed any maximum loaded
from that file.

All objects are strict: every listed field is required unless marked optional,
and unknown fields are rejected.

### Evidence plan

The PR #2322 plan fields are:

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
`observations`, `round`, `expansions_used`, `vlm_calls_used`, `status`, and
`stop_reason`. `ledger_version` is `"1.0"`. Status is `in_progress`, `answered`,
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

### Final result

The final result has exactly `status`, `answer`, `evidence`,
`evidence_details`, `unresolved_gaps`, `revision`, and `artifact_dir`.
`evidence_details` contains the complete accepted observations named by
`evidence`. An answered result has a non-empty answer and no unresolved gaps.
An unresolved result has a null answer and empty evidence arrays; every gap has
`claim_id`, `gap`, and one reason: `insufficient_coverage`, `not_visible`,
`tool_failure`, or `budget_exhausted`.

Use the utility for canonical state transitions:

```bash
LEDGER_TOOL="skills/operations/vss-introspect-video/scripts/evidence_ledger.py"

python3 "${LEDGER_TOOL}" init \
  --plan "${RUN_DIR}/plan.json" \
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
  --tasks "${ROUND_DIR}/tasks.json" \
  --results "${ROUND_DIR}/results.json" \
  --output "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" assess --ledger "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" final-result \
  --ledger "${RUN_DIR}/ledger.json" \
  --artifact-dir "${RUN_DIR}" \
  --answer "${ANSWER}" \
  --output "${RUN_DIR}/final-result.json"
```

Only the top-level agent invokes a command that writes `ledger.json`.
Subagents write only their assigned result artifact.

## Run artifacts

Use the harness per-run artifact directory when it provides one. Otherwise use:

```text
${VSS_WORKSPACE:-$HOME/.vss}/runs/vss-introspection/<question-id>/
├── plan.json
├── ledger.json
├── final-result.json
└── rounds/
    ├── round-1/
    │   ├── base-ledger.json
    │   ├── media-scopes.json
    │   ├── tasks.json
    │   ├── task-<claim-id>.json
    │   ├── results.json
    │   └── result-<claim-id>.json
    └── round-2/
```

Freeze `base-ledger.json` before dispatch. Atomically replace `ledger.json` and
`final-result.json`. Never write the ledger to `MEMORY.md`, VSS memory,
Elasticsearch, or Git.

## Workflow

### 1. Freeze the question and grounded media scope

Retain the verbatim question, stable question ID, optional asset ID, allowed
visual modalities, and grounded media selectors. Keep answer choices only for
final synthesis.

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

Apply the complete PR #2322 rubric. In particular:

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

1. OpenClaw or harness-native Markdown memory, when available;
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
gate passes, skip visual inspection.

### 4. Select bounded inspection work

Create one utility-generated task per selected unresolved claim. Every task:

- targets one existing claim;
- carries the complete PR #2322 claim unchanged;
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

### 5. Dispatch bounded parallel subagents

Spawn no more subagents than the loaded parallelism limit. Give each one task
containing one claim ID, its immutable `media_scope`, and a result path. Instruct
it to:

- read only its claim, gap, existing observations, asset ID, media scope, and
  coverage;
- inspect only the missing visible fact in the assigned media scope;
- use targeted `vss vlm run` calls within its allocation; never search memory,
  summarize broadly, include answer choices, or spawn agents;
- report visible facts against the support and falsification tests, leaving
  missing visibility, ambiguity, occlusion, failure, and incomplete coverage
  unresolved;
- preserve VSS job ID and the exact assigned provenance selector: sensor plus
  ISO-8601 window, media URL, or file path;
- put tool failures in `error`, never in `observations`; exit 6 may retain
  usable evidence while recording the persistence limitation;
- return exactly one contract-valid result without changing claims, canonical
  state, or the final answer.

For a sensor scope, every returned VLM observation must use the assigned sensor
and remain within the assigned window. The utility rejects mismatches.

Example command shape:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
VSS=(uv run --project "${VSS_REPO_ROOT}/libs/vss" vss)

"${VSS[@]}" vlm run \
  --prompt "${OPTION_BLIND_CLAIM_PROMPT}" \
  --sensor "${SENSOR_NAME}" \
  --start-time "${START_TIME}" \
  --end-time "${END_TIME}" \
  --fps "${VLM_FPS}"
```

For non-sensor input, replace the sensor and time flags with exactly one of
`--media-url "${MEDIA_URL}"` or `--file "${FILE_PATH}"`.

### 6. Collect and merge one complete round

Wait for every assigned subagent or the harness timeout. If a subagent does not
return, write a result for its assigned task with no observations, retained
coverage/gap, zero consumed calls when known, and a timeout error. Preserve
successful sibling results.

Validate every result against its task, then batch-merge the complete result
set once. The utility rejects stale, unknown, duplicate, cross-claim,
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
- does not contain both supporting and contradicting observations.

If the gate passes, the ledger becomes `answered` with stop reason `resolved`.
If a round adds no observation and improves no coverage, it stops unresolved
with `no_progress`. Exhausted round or global-call budget stops with
`budget_exhausted`. If every attempted inspection fails and no useful evidence
exists, it stops with `tool_failure`.

Invoke planner expansion mode only when an independently assessable answer
requirement is absent from the plan. Pass the prior plan and demonstrated
planning gap, still option-blind. An expansion adds exactly one new claim and
must preserve every existing claim ID and all evidence. Accept no more than the
loaded expansion and total-claim budgets. Bind memory and reassess the new
claim before inspecting it.

If the run remains in progress, perform the next bounded round. Otherwise stop;
do not infer through an unresolved gap.

### 8. Synthesize and write the final result

Only the top-level agent synthesizes the user answer. Answer choices may be
considered now, after sufficient option-blind evidence exists. Every
answer-bearing statement must cite accepted observation IDs.

Use `scripts/evidence_ledger.py final-result` to write the result shape. For an
unresolved run, set `answer` to null and report each claim gap using only:

- `insufficient_coverage`;
- `not_visible`;
- `tool_failure`;
- `budget_exhausted`.

Do not suppress gaps, fill them with inference, or present context observations
as answers. Report whether each cited source is memory or VLM and preserve VSS
job, record, and assigned media provenance. `final-result.json` must be
self-contained: include the final ledger revision, run artifact directory, and
full provenance for every cited observation.
