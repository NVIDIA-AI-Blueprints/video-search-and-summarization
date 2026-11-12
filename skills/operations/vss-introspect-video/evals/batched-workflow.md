# Batched introspection experiment

This is a combined experimental change, not an accuracy claim or a set of
isolated ablations. Base: `529858777` on `feat/agent-introspection-loop`.

## What changes

- One coordinator; Python threads execute parallel VLM **tools**, not inspector
  agents. The coordinator still plans, evaluates evidence and answers.
- Fixed versioned prompt plus four bounded focus fields; code selects the
  configured FPS preset and splits at a 48-frame cap for 50-image backends.
- Up to two normal rounds, two concurrent tasks, four calls per task and eight
  normal calls total. A first empty-evidence round may refine once; identical
  requests are rejected. Tool failure proceeds to final checking.
- Compact assessments reference exact VLM output quotes. Code owns provenance
  and closure constraints; gaps or false applicability/coverage flags block
  closure. Semantic correctness is still a model judgment.
- One fresh reserved final VLM inspection after any normal stop, independent
  of the normal budget. One logical inspection can still have helper-owned
  HTTP transport retries. No automatic quality retry or reinspection loop.
- Finalization requires a ledger-bound check receipt. A negative, failed or
  inconclusive check, or a revised answer, downgrades the final evidence status.
  Choice questions still choose an option; open-ended questions may abstain.
- Exact prompt, nominal timing, actual image count for the remote path, provider
  usage when available, per-call/task latency, raw outputs and revisions persist.
- Restart uses completed receipts; interrupted unknown calls conservatively
  consume their allocation. Never reuse a run directory across trials.

## Runtime setup

Build the harness image with `VSS_REF` pinned to this branch's committed SHA.
Both OpenClaw and Hermes copy the entire skill directory; no new manual COPY is
required for these scripts/config files. Verify the **running** image contains
`scripts/inspection_workflow.py` and skill version `4.0.0-experimental`.

Configure the ordinary VSS CLI and model routing as for the control run. Do not
change the VLM/LLM models, reasoning settings, dataset, memory or seed prompts
while comparing this experiment. This branch does not choose a model or change
backend reasoning settings. Installed `vss` on PATH is the default. Optional
`--vss-project` is only for a local source checkout. Resolve helper paths from
the loaded skill's directory, not the current working directory.

Run one UI smoke question before the overnight benchmark. Confirm batch artifacts,
the final-check receipt and a non-provisional terminal marker exist, then inspect
the final normal/check call counts. An old image/skill override would not exercise
this workflow. Do not interpret helper unit tests as live-model accuracy evidence.

## Suggested report columns

Question ID; dataset snapshot; selected answer; GT; correctness; original ledger
status/stop; final evidence status; final-check verdict; answer changed;
normal/final call counts; actual images where available; input/output token usage;
wall latency; missing usage fields; infrastructure failures; repeated/interrupted
work. Preserve each effective and actually submitted VLM prompt.

Report accuracy, unsupported closure and latency separately. Count failed checks
explicitly. Do not silently exclude missing answers/provider failures from the
denominator. Separate completed/scored accuracy from operational success rate.

## Limitations

- Sensor scopes only. URL/file inputs are rejected, not silently routed elsewhere.
- Final check is one bounded clip (at most 96/48/24 seconds at the configured
  skim/locate/detail presets). It cannot certify an exhaustive long-video count.
- Frame timestamps on the remote path are nominal ffmpeg sampling-grid offsets,
  not measured source presentation timestamps. Sparse samples can miss events.
- A second VLM check can share the first model's errors and is not ground truth.
- The code cannot prove a quote's semantic applicability. Manual ledger labels
  remain useful to measure false closure.
- No live VLM call, deployment or benchmark was performed during implementation.

## Validation and independent review

48-frame compatibility fix: **150 tests passed**. Additional tests cover the
workflow's three FPS presets, final-check acceptance at 48 frames and rejection
above the limit, remote sampling, and invalidation of receipts from a different
frame-cap configuration. This changes partition sizes, not normal call budgets;
long scopes can therefore leave more uncovered windows. No live lease retest
was performed during this fix.

Run `python -m pytest skills/operations/vss-introspect-video/scripts/tests -q`.
New coverage includes parallel execution, exact-quote provenance, failed calls,
one refinement after empty evidence, mandatory final checking after resolution
and exhaustion, no retries on resume, stale receipts, final-check downgrades,
choice enforcement, open-ended abstention and installed-CLI mode.

Local validation: **142 tests passed**, independently rerun by the reviewer;
targeted Ruff checks for the new workflow/ledger/tests and `git diff --check`
passed. The legacy VLM helper has pre-existing full-Ruff findings; these were not
an excuse to claim the entire repository's lint/CI passed.

An independent read-only agent reviewed the implementation. Findings addressed:
packaged-runtime/source-path assumptions; missing per-window timing metadata;
failed batches incorrectly refining when memory existed; and inability to abstain
after a contradictory final check; and ignoring a malformed/retracted final
verdict. No outstanding code-review blockers were reported. Final runtime
smoke/accuracy remain untested.
