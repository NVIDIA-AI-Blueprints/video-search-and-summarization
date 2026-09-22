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

Before starting, read:

- `../vss-generate-evidence-plan/references/claim-classification.md` in full;
- `config/ledger-budgets.json`, the only authoritative POC budget source;
- `references/evidence-ledger.schema.json`;
- `references/inspection-task.schema.json`;
- `references/inspection-result.schema.json`;
- `scripts/evidence_ledger.py`.

Do not copy the numeric budgets into prompts, scripts, or other configuration.
Load them from `config/ledger-budgets.json`. The utility enforces them when
initializing, expanding, creating tasks, validating results, and merging.
Never exceed any maximum loaded from that file.

The ledger contains the original evidence plan, one small runtime state per
claim, append-only accepted observations, the completed round count, and the
global VLM-call count. Runtime state never edits the claim objects. Unknown
fields are rejected.

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
  --output "${ROUND_DIR}/tasks.json"

python3 "${LEDGER_TOOL}" merge-round \
  --ledger "${RUN_DIR}/ledger.json" \
  --tasks "${ROUND_DIR}/tasks.json" \
  --results "${ROUND_DIR}/results.json" \
  --output "${RUN_DIR}/ledger.json"

python3 "${LEDGER_TOOL}" assess --ledger "${RUN_DIR}/ledger.json"
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
    │   ├── tasks.json
    │   ├── task-<claim-id>.json
    │   ├── results.json
    │   └── result-<claim-id>.json
    └── round-2/
```

Create `base-ledger.json` from the frozen canonical revision before dispatch.
Use atomic replacement for `ledger.json` and `final-result.json`. Do not write
the ledger to `MEMORY.md`, VSS memory, or Elasticsearch. Runtime artifacts must
not be added to Git.

## Workflow

### 1. Freeze the question and grounded media scope

Retain the verbatim question, a stable question ID, optional asset ID, allowed
visual modalities, and grounded media selectors. Keep answer choices separate
for final synthesis only.

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

Support tests name visible satisfying outcomes. Falsification tests name
visible incompatible outcomes. Occlusion, blur, incomplete coverage,
ambiguity, VLM uncertainty, timeout, and tool failure do not falsify a claim.

Validate the plan, save it as `plan.json`, and initialize `ledger.json`.

### 3. Retrieve and bind memory

Only after planning, search:

1. OpenClaw or harness-native Markdown memory, when available;
2. structured VSS memory, when configured and needed.

Use exact identity for a known record:

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

For each selected unresolved claim, create one task through the utility. Every
task:

- targets one existing claim;
- carries the complete PR #2322 claim unchanged;
- carries the current gap and accepted observations;
- uses the same frozen `base_revision` from the canonical ledger;
- receives an allocation from the remaining canonical budget.

Use `vss vios list` and `vss vios timeline --sensor NAME` only after planning
to ground sensor identity and useful ISO-8601 windows. Window selection may be
agent-driven. For `whole_video`, inspect useful non-overlapping bounded windows;
do not issue one arbitrarily large VLM request. Report partial coverage unless
the accepted windows defensibly cover the complete grounded interval, and keep
uncovered intervals as explicit gaps. These are ordinary `vss vios` lookups,
not an orchestration service.

### 5. Dispatch bounded parallel subagents

Spawn no more subagents than the loaded parallelism limit. Each subagent gets
one task containing one claim ID, immutable media selectors, a result path, and
this instruction:

> Read only the assigned claim, gap, existing observations, asset ID, and
> coverage requirement. Inspect only the missing visible fact. Use existing
> memory timestamps, VIOS timeline information, or question context to choose
> useful assigned media. Issue targeted `vss vlm run` queries; do not broadly
> summarize the video and do not include answer choices. Describe observations
> only as visible facts and compare them to the support and falsification
> tests. Missing visibility, ambiguity, occlusion, tool failure, and incomplete
> coverage remain unresolved. Return exactly one schema-valid inspection
> result. Do not create or modify claims, spawn agents, edit the canonical
> ledger, or answer the user.

The subagent may use only its assigned VLM-call allocation. It must not search
memory. Capture the returned VSS job ID, sensor, and exact ISO-8601 window in
every accepted VLM observation. A tool failure goes in `error`, never in
`observations`. Exit 6 may retain usable evidence while recording its
persistence limitation; other nonzero exits produce an error and no invented
observation.

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

### 6. Collect and merge one complete round

Wait for every assigned subagent or the harness timeout. If a subagent does not
return, write a result for its assigned task with no observations, retained
coverage/gap, zero consumed calls when known, and a timeout error. Preserve
successful sibling results.

Validate every result against its task. To batch-merge safely, merge the complete result set
once. The utility rejects stale, unknown, duplicated, cross-claim, over-budget,
or malformed results; deduplicates observations by claim, relation, normalized
text, source type and identifiers, and window; updates only assigned claim
states; preserves supporting and contradicting evidence; increments the global
call count; and increments `round` and `revision` exactly once.

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
job, record, sensor, and time provenance.

## POC boundary

This POC has no probability score, confidence threshold, semantic-threshold
sufficiency, historical learning, long-term ledger persistence, NeMo Fabric,
NeMo Relay, NAT, LangGraph, new runtime service, or new VSS job type. Do not
describe any of these as implemented.
