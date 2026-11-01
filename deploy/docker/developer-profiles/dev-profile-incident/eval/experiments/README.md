# Prompt experiments (PR 6A runner)

`experiment_runner.py` and `scripts/eval_experiment.py` run one **configuration** per config file in `configs/`. A configuration is an arm (`direct-vlm` or `vss`), a registry prompt, a model, a split half (`dev` or `test` of `split-v2`), the frozen ground truth, repeats and a P1 call budget. Plan: the local planning document `.docs/mvp2/ground-truth-and-prompt-experiments-plan.md`, not tracked in Git §F.

```bash
uv run python scripts/eval_experiment.py plan configs/…json            # the plan and every guard; no calls
uv run python scripts/eval_experiment.py run  configs/…json [configs/…json …] --confirm-paid   # one stage, interleaved; only after its budget is approved
uv run python scripts/eval_experiment.py rescore <results.json> --confirm-paid  # judge calls only, no P1
uv run python scripts/eval_experiment.py compare C1=… C2=… C3=… C4=…    # the four paired comparisons, offline
uv run python scripts/eval_experiment.py report --title "…" C1=… C2=…   # Markdown report from saved results
uv run python scripts/eval_experiment.py verify <results dir>           # reproducibility check of one run
uv run python scripts/eval_experiment.py budget configs/*.json [--measured measured.json]   # requests, tokens, provisional cost
uv run python scripts/eval_experiment.py measure <results.json> …       # pilot figures for budget --measured
uv run python scripts/eval_experiment.py caps configs/…json [--write]  # judge-request caps derived from the ground truth
uv run python scripts/eval_experiment_plumbing.py                       # Stage 0 step 1: offline C1–C4, scripted model
```

Readiness, the budget statement and the approval gates: [`docs/experiments/readiness.md`](../docs/experiments/readiness.md).

## Fairness

- **Same request:** both arms send the same P1 request, with the same prompt text, `inference.json` settings and signed URL.
- **Same policy and scoring:** both run under the same rate-limit policy. Both are re-validated by `contract.evaluate_policy` and scored by the same scorer against the same frozen ground truth.
- **The VSS arm** calls the agent's `/analyze` (experimental settings documented in [`vm-api-contracts.md`](../../.docs/vm-api-contracts.md#experimental-settings-prompt-experiments-pr-6b); the agent must run with `INCIDENT_EXPERIMENTS_ENABLED=1` and without Supabase or R2 settings) with `skip_rp1: true`, `skip_crops: true` and, for a non-baseline prompt, `experiment_prompt_id`.
  - A 200 response, or a 422 with a `raw_output`, is a model output. Anything else is a request failure (eval.md §4).
  - A non-baseline prompt counts only when the agent echoes the prompt id and SHA-256 it used. Otherwise the attempt fails, so a VSS run can never silently use the production prompt.

## Guards

Every guard runs before any call:

| Guard | Rule |
|---|---|
| Test split | Only for `final: true` with a `selection_record` that is an existing `### ` heading in `.docs/decisions.md` |
| Frozen baseline | A `frozen_baseline` config with a completed run is never rerun. Re-score its saved outputs instead |
| Budget | Videos × repeats must not exceed `budget.max_p1_calls` |
| Paid calls | `run` and `rescore` send nothing without `--confirm-paid` |
| Ground truth | Must be frozen. A candidate needs `ground_truth.allow_candidate` (plumbing only), and every result is then marked `reportable: false` |
| Accuracy runs | `skip_rp1` and `skip_crops` must be true. Cropping belongs only to the separate end-to-end profile |
| End-to-end profile (`purpose: profile`) | VSS only, `pipeline.profile: end-to-end`, cropping on with `pipeline.max_crop_calls_per_request` (sent as the agent's `max_crop_calls`) and `pipeline.request_timeout_s`; `scoring: none` (no judge or embedding call; `budget.max_judge_calls` 0); its own `budget.max_crop_calls` and `budget.max_crop_tokens`, reserved before each video-run for a worst case of the cap per attempt; ids start `e2e-`; never final, run once, never reportable. Configs `configs/e2e-*.json`, pinned by `e2e-profile/manifest.json`; checks and report: `scripts/eval_e2e_profile.py` ([plan](../docs/experiments/e2e-profile-plan.md)) |
| Hard caps (D11) | `budget.max_p1_calls`, `max_p1_tokens` and `max_judge_calls` are required. A plan that cannot fit is refused.<br>**Requests:** every HTTP attempt counts, and 429 retries use only the attempts left after one per remaining video.<br>**Tokens:** each call must leave room for a 35,000-token worst case, and unmetered non-429 attempts count at that ceiling.<br>**Judge:** each video must leave room for its worst case of judged fields × 5 attempts.<br>The stage also stops if more than 25% of attempts are rate-limited. Partial results are kept as incomplete. Judge tokens are measured; their per-call length is not capped by the pinned judge settings |
| Scoring | `scoring: full` for experiments (D9 needs item F1). `incident_only` is allowed for pilots only |
| Order (D10) | Configurations given together run interleaved per video, the first rotating, in a seeded order per repeat (`schedule.json`) |
| Pilot | `purpose: pilot` is dev-only, may use candidate ground truth, is never reportable, never feeds selection, and its report has no accuracy tables |

The local experiment agent (`experiments/agent/start-experiment-agent.sh`) always runs the code of the checkout it is launched from (it sets `PYTHONPATH` to that checkout's `services/agent/src`, whatever an editable install in the venv points at), so the commit in its run provenance is the code that ran. It finds ffmpeg and ffprobe for the caption pass in the checkout's gitignored `.tools/ffmpeg/bin` (`.scripts/media-tools.sh install`), else in `/usr/bin`; `EXPERIMENT_AGENT_DRY_RUN=1` prints the command instead of starting it.

Every call is logged to `eval_data/results/experiments/stages/<time>-<id>/cost-log.jsonl` with tokens, judge calls and the provisional cost (`pricing.json`, provisional until Switchyard pricing is confirmed). The VSS arm uses the [local experiment agent](agent/README.md).

Results are written to `eval_data/results/experiments/<id>/<UTC time>/` as `results.json` (every §F.4 field) and `prompt.txt`. They are immutable and gitignored.

## The committed configs

| Config | Stage | What |
|---|---|---|
| `pilot-direct-vlm`, `pilot-vss` | 0 (paid pilot) | `purpose: pilot`, `p1-v4-base`, 1 dev clip per category (`videos`), repeats 1, candidate ground truth allowed. Run together (interleaved) |
| `direct-vlm-baseline` | 1 | C1 on dev, frozen after its first complete run |
| `vss-baseline` | 1 | C2 on dev, local agent per D14 (b) |

`videos` (optional) narrows a config to a subset of its split half and refuses clips outside it. `repeats` and budgets follow plan D10/D11 and are **placeholders until those decisions and each stage's budget are approved**. C3/C4 configs are written only after the Stage 2 selection is recorded in decisions.md. Stage 3 configs: `configs/s3-*.json` (final, test, 1 repeat, run once), pinned by `stage3/manifest.json`; check them with `scripts/eval_stage3.py preflight` and report with `scripts/eval_stage3.py report`; run, recover, report and audit with `scripts/eval_stage3.py supervise` ([plan](../docs/experiments/stage3-plan.md), [results](../docs/experiments/stage3-results.md)). Stage 3 is done and must not be rerun.

## Retries, recovery and host availability (approved 2026-10-28)

- **`pipeline.retry_policy`:** `retry-v1` (the default; Stages 0–1: 429 only) or `retry-v2` (also timeout, transport error and `host_suspended`; 60 s then 180 s). The same policy applies to every arm ([details](../docs/experiments/infrastructure-and-recovery.md)).
- **Host suspend:** every attempt compares wall-clock and monotonic time. A failed attempt that spans a suspend is `host_suspended`.
- **Keep-awake:** run paid stages under `experiments/keep-awake.sh -- <command>` (WSL), with the lid open and on AC power.
- **Recovery:** `scripts/eval_experiment.py recover <results.json>...` is a dry run by default. With `--confirm-paid`, it re-attempts only infrastructure-failed video-runs as a separate `<experiment>-recovery` result linked to the original, and writes `merged-original+recovery-v1.json`. Originals are never changed. D9 G3 refuses mixed protocols or methodology versions.
- **Checkpoint and resume:** every video-run updates the stage's `checkpoint.json`. `run <same configs> --confirm-paid --resume <stage dir>` continues an interrupted stage in the same order and writes its results once at the end.
- **Pauses (approved 2026-10-28):** a stage pauses, keeping its checkpoint and writing no result, in three cases:
  - after 4 consecutive video-runs with no model output (`OUTAGE_PAUSE_RUNS`);
  - when the session's rate-limit share exceeds 25%;
  - at `--session-hours` (checked between video-runs).

  Exit code 4 means paused; continue with `--resume`. A cap still stops the stage (`complete: false`).
- **Wake guard:** on WSL a paid `run` or `recover` refuses unless it runs under `keep-awake.sh` (`--allow-sleep` overrides).
- **Recovery limit:** at most 15 recovered video-runs per config (`--max-runs`).
- **Offline re-matching:** `scripts/eval_experiment.py rematch <results.json> --methodology evaluation-methodology-v1.2 [--threshold t]` writes a new `rematched-*.json`. It makes no model or judge call.

## Reports and selection

- **`render`:** a report over any result on candidate ground truth carries a NOT REPORTABLE banner. Inference latency and end-to-end latency are separate tables.
- **Paired comparisons:** the C3−C1 comparison is shown as the sum (C3−C4) + (C4−C1), with the plan's interpretation limits and held-out exposure disclosure.
- **`select`:** applies a selection rule only to dev, reportable records, and only when the rule is an existing decisions.md heading. Test results can never feed selection.
- **D9 v2** (`experiment_selection.py`, proposed, see the [decision record](../../.docs/mvp2/experiment-decision-record.md)): the primary metric is the equal-weight mean of incident fields, entity F1 and object F1; the secondary is the eight-score average. It applies the +0.02 minimum, the bootstrap CIs, guardrails G1–G3 and the documented tie-breakers. `select_d9` refuses test, pilot, incomplete or unfrozen records and a rule that is not pre-registered.
