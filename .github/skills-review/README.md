<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Skills Review (multi-paradigm, advisory)

A CI pass that reviews **changed skills** with six independent paradigms in
parallel, consolidates their findings by **cross-paradigm agreement + severity**,
and posts a **sticky PR comment** + uploads a full **artifact**. It is the CI
realization of the manual 6-paradigm review. It is **advisory** — it never fails
the merge and is not a required status check.

Complements, does not replace: `skills-nv-base.yml` (schema/secrets/PII lint) and
`skills-eval.yml` (GPU behavioral eval). This is static, LLM-only review.

## Flow

```
plan  (plan_review_matrix.py: changed skills × 6 paradigms → matrix)
  → review  (matrix, fail-fast:false, max-parallel 12; one Agent-SDK leg per (skill,paradigm))
       each leg → review-<skill>__<paradigm>.json   (canonical findings schema)
  → report  (consolidate.py → sticky comment via post_review_comment.py + consolidated.{json,md})
```

Parallelism is the **Actions matrix**, not in-agent subagents — the Agent-SDK
substrate disables Task/background tools (one serial agent per leg), exactly as
`skills-eval` does.

## Paradigms

| Paradigm | Engine | Scope |
|---|---|---|
| `review` | Claude Agent SDK + `prompts/review.md` | diff: VSS correctness (stale refs, dead container names, ghost paths) |
| `gstack-review` | Claude Agent SDK + `prompts/gstack-review.md` | diff: dangerous/non-portable shell, side effects, proxy gates |
| `codex` | `codex exec` + `prompts/codex.md` | diff: adversarial second opinion, authz-vs-authn, fabricated facts |
| `ce-code-review` | installed `ce-code-review` skill (`mode:agent`) | diff: multi-persona code review (native JSON) |
| `ce-doc-review` | installed `ce-doc-review` skill (`mode:headless`) | whole `SKILL.md`: document quality |
| `best-practices` | Claude Agent SDK + `prompts/best-practices.md` | whole skill: Anthropic authoring rubric |

All legs normalize to one schema (`findings-schema.json`): `{skill, paradigm,
file, line, title, severity, category, rationale, suggested_fix, confidence}`.

## Runner prerequisites (`vss-brev-runner`)

Runs on `[self-hosted, vss-brev-runner]` — the **original `vss-skill-validator`**
host (shared CPU brev-CI pool), **not** `vss-skill-validator-v2`
(`vss-skill-eval-runner`, reserved for GPU eval). Review is LLM-only; no GPU,
Brev box, or Harbor.

That host must have:
1. **Anthropic coordinator `.env`** at `/home/ubuntu/eval-coordinator/.env` with
   `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`. The review legs
   source it and set `CLAUDE_CODE_DISABLE_THINKING=1` (the NVIDIA Anthropic proxy
   rejects the `context_management` field otherwise). **Confirmed present on
   `vss-skill-validator`.**
2. **`claude` CLI** — the Claude Agent SDK *spawns* the `claude` binary, so it is
   required for **every agentic leg**: `review` / `gstack-review` /
   `best-practices` (via the SDK) and `ce-code-review` / `ce-doc-review` (which
   call it directly). The two `ce-*` legs additionally need the
   **compound-engineering plugin** installed. Missing `claude` → SDK legs record
   `status: failed`, CE legs `status: skipped`.
3. **`codex` CLI + auth** (`CODEX_HOME`) for the `codex` leg. If absent, that leg
   degrades to `status: skipped` — net-new on this pool; install only if the
   cross-model lens is wanted.
4. `python3` 3.12 (provided by `actions/setup-python`); `claude-agent-sdk` is
   `pip install`ed on demand by `review_agent.py` (it still needs the `claude`
   CLI from item 2 at runtime).

> **Provisioning status (checked via `brev exec vss-skill-validator`):** the
> `.env` + `ANTHROPIC_*` keys are present, but `claude`, the CE plugin, and
> `codex` are **not installed** on the pool yet. Until `claude` is installed the
> report is empty/skipped — the workflow still runs green (advisory). The eval
> host `vss-skill-validator-v2` already has `claude`, but it is reserved for GPU
> eval; install `claude` (+ CE plugin, codex) on `vss-skill-validator` to enable
> the legs.

## Consolidation rules (`review_findings.py`)

- **agreement** = count of **distinct** paradigms that raised a finding (two
  checks from one paradigm are not corroboration).
- **severity** = **max** across members (worst-case wins).
- **needs_verification** = `severity == critical` **or** `agreement == 1` (the
  "verify criticals and single-lens outliers" rule — e.g. the DGX-Spark trap).
- dedupe key = `(skill, file, ~title)`; titles merge on token-overlap ≥ 0.6 **or**
  `SequenceMatcher` ≥ 0.82.
- ranking = `(severity, -agreement, skill, file)`.

The sticky comment shows critical/high + corroborated (agreement ≥ 2) rows and a
critical/high **verify** list; the low-confidence long tail stays in the artifact.

## Secret hygiene

Only the JSON **findings** are uploaded — never agent trajectory dirs (the
`skills-eval` PR #516 lesson, where `NGC_CLI_API_KEY` leaked via trajectories).

## Triggers

- **PR** (push to `pull-request/<N>` mirror): reviews only the **changed** skills
  (`FETCH_HEAD...HEAD` cumulative diff).
- **Manual** (`workflow_dispatch` `skills` input): `*` (all), a comma list, or a
  JSON array of skill dirs.

## Promoting to a gate (later)

Kept advisory until the false-positive rate is understood. To make it blocking,
add `Skills Review / report` as a required status check and change the leg/report
exit-code policy — today everything exits 0 by design.

## Cost

A full manual sweep is ~`(#skills) × 6` LLM legs (~100); PR runs review only the
changed skills. `max-parallel` bounds concurrency against the Anthropic proxy
rate limit.

## Tests

```
python3 .github/skills-review/tests/test_plan_review_matrix.py
python3 .github/skills-review/tests/test_review_findings.py
python3 .github/skills-review/tests/test_consolidate.py
python3 .github/skills-review/tests/test_review_agent.py
python3 .github/skills-review/tests/test_post_review_comment.py
```
