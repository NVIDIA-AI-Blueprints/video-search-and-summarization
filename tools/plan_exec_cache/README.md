# Plan–Execute–Learn with Procedural Memory

A small, generic package that lets tool-using agents reuse verified procedures.
It stores validated Markdown outside the repository under
`$PLAN_EXECUTE_CACHE_HOME` (default `~/.plan-execute-cache`):

```text
memories/<key>/{procedure.md,metadata.json}
```

## Workflow

Start Claude Code with procedural memory enabled:

```bash
PLAN_EXECUTE_CACHE=1 claude
```

The Claude Code `SessionStart` hook supplies the procedure inventory and one
progressive Plan–Execute–Learn workflow:

- Cache hit: recall the procedure, bind values from the current request,
  execute it, and verify the result.
- Cache miss: form a compact working plan in context, execute and revise it
  progressively, verify the real result, then remember only the successful
  reusable path.
- Recalled procedure failure: preserve completed work, repair the remaining
  procedure, verify it, then replace the stored procedure.

The working plan stays in Claude's context, so writing an unverified temporary
artifact does not add another tool turn. Claude calls the cache runtime only to
recall a verified procedure or remember the final successful one. There is no
second planner or executor session and no hidden response envelope.

The local evaluator never writes or repairs a procedure. Every arm is one fresh
Claude invocation followed by the existing Harbor judge. If cold Claude
remembers nothing, warm preflight fails. If it remembers only some operations,
the warm run exposes the remaining misses instead of repairing them.

## Commands

```bash
plan-exec-cache list
plan-exec-cache recall --key demo.inspect
plan-exec-cache forget --key demo.inspect

plan-exec-cache remember --key demo.inspect \
  --source /path/to/instructions \
  --procedure-file /path/to/procedure.md
```

`remember` stores the final procedure after the agent has executed and verified
the task. `recall` checks the key, procedure hash, metadata format, and
instruction-source hashes. Missing, edited, or stale memory returns `MISS` with
exit code 3.

## Tests and local evaluation

```bash
tools/plan_exec_cache/tests/run_tests.sh
```

### Evaluate one generated Harbor task locally

Use `local_eval.py` when VSS is already running on the current host. It sends
the generated task's exact instruction to local Claude Code, saves its stream,
runs the existing Harbor verifier, and reports reward, agent tokens, cost, and
latency. It does not use Brev, Git synchronization, or a second host.

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --mode direct \
  --output /tmp/local-upload-direct
```

Cold and warm runs share an explicit cache directory:

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --mode cold \
  --cache-home /tmp/local-procedure-cache \
  --output /tmp/local-upload-cold

python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --mode warm \
  --cache-home /tmp/local-procedure-cache \
  --output /tmp/local-upload-warm
```

Run one mode per command and restore task-specific system state between
comparison arms. The runner does not guess how to undo an arbitrary task.

To compare the post-deployment upload, readiness, video-QA, and report chain,
use the VSS reset fixture. It removes only the uploaded file sensor before each
arm; the base deployment remains running, and cold and warm share only the
procedure cache.

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --mode compare \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --task /tmp/skill-eval-ask/base/l40s/step-3 \
  --task /tmp/skill-eval-ask/base/l40s/step-4 \
  --task /tmp/skill-eval-report/base/l40s/step-4 \
  --reset-script .github/skill-eval/fixtures/reset_vios_upload.py \
  --output /tmp/local-postdeploy-comparison
```

`compare` runs all three arms by default. Select a subset with `--arms`; the
selected arms still share one automatically generated cache directory:

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --mode compare --arms cold warm \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --task /tmp/skill-eval-ask/base/l40s/step-3 \
  --task /tmp/skill-eval-ask/base/l40s/step-4 \
  --task /tmp/skill-eval-report/base/l40s/step-4 \
  --reset-script .github/skill-eval/fixtures/reset_vios_upload.py
```

Use `cold_true` in place of `cold` to measure true per-query cold starts. Every
`cold_true` task receives a separate empty procedure cache, so it cannot recall
a procedure learned by an earlier query. After each task, the runner collects
its learned procedures into the shared cache; `warm` can therefore reuse the
combined learned cache. Application state remains sequential within the arm
and is still restored only by `--reset-script` before each arm.

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --mode compare \
  --arms direct cold_true warm \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --task /tmp/skill-eval-ask/base/l40s/step-3 \
  --task /tmp/skill-eval-ask/base/l40s/step-4 \
  --task /tmp/skill-eval-report/base/l40s/step-4 \
  --reset-script .github/skill-eval/fixtures/reset_vios_upload.py
```

`cold` and `cold_true` cannot be selected in the same comparison because that
would make the source of the subsequent Warm cache ambiguous.

Repeat the complete comparison three times with `--runs 3`. Each run gets an
independent cold/warm procedure cache. Optional labels make the generated
tables use short workload names:

```bash
python3 tools/plan_exec_cache/integrations/harbor/local_eval.py \
  --mode compare \
  --arms direct cold warm \
  --runs 3 \
  --task /tmp/skill-eval-ask/base/l40s/step-2 \
  --task-label Upload \
  --task /tmp/skill-eval-ask/base/l40s/step-3 \
  --task-label Readiness \
  --task /tmp/skill-eval-ask/base/l40s/step-4 \
  --task-label "Video QA" \
  --task /tmp/skill-eval-report/base/l40s/step-4 \
  --task-label Report \
  --reset-script .github/skill-eval/fixtures/reset_vios_upload.py \
  --output /tmp/local-postdeploy-comparison-3-runs
```

The output contains `run-01/`, `run-02/`, and `run-03/`, plus a top-level
`summary.md` with one table per run and an average table. `result.json` keeps
the individual results under `runs` and the aggregated metrics under
`average`. Average tokens, cost, and agent latency are arithmetic means of the
three complete runs; changes versus Direct are calculated from those means.
Verifier latency remains available in `result.json` but is not included in the
table's Latency column. A result is shown as PASS in the average only when it
passed in every run.

When paths are omitted, the evaluator stores each run under
`local_eval/<timestamp>/`, with procedures in `cache/` and benchmark artifacts
in `results/`. The final report records the cache path. Pass `--cache-home`
only when separate cold and warm commands must share one specific cache.

The command records failed tasks without skipping later tasks or arms. Each arm
has its own task artifacts under the output directory, and the top-level
`result.json` contains the direct, cold, and warm aggregate reward, tokens,
cost, and latency. After each task, the evaluator also prints its input, agent
output, pass/reward, tokens, cost, and agent/verifier latency.

### Generate example tasks

First generate the unchanged ask-video and report Harbor datasets:

```bash
python3 .github/skill-eval/adapters/vss-ask-video/generate.py \
  --output-dir /tmp/skill-eval-ask \
  --skill-dir skills/vss-ask-video \
  --deploy-skill-dir skills/vss-deploy-profile \
  --video-io-skill-dir skills/vss-manage-video-io-storage \
  --platform L40S

python3 .github/skill-eval/adapters/vss-generate-video-report/generate.py \
  --output-dir /tmp/skill-eval-report \
  --skill-dir skills/vss-generate-video-report \
  --deploy-skill-dir skills/vss-deploy-profile \
  --video-io-skill-dir skills/vss-manage-video-io-storage \
  --platform L40S
```

Run any generated task with `local_eval.py` as shown above. The local shell
needs `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `ANTHROPIC_MODEL`, plus
credentials required by the task such as `NGC_CLI_API_KEY` or
`NVIDIA_API_KEY`. Skill-specific queries and checks remain in the generated
Harbor task and its existing verifier.
