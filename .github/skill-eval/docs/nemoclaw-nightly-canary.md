# NemoClaw nightly canary working agreement

Status: proof of concept  
Task: VSS-1009  
First target: `vss-manage-alerts / cv_mode_gate / L40S`

## Objective

Prove the complete nightly lifecycle for a cloud-hosted NemoClaw operational agent on one skill before changing the scheduled full sweep. The canary must use the normal operator-managed Brev pool, produce the existing Harbor result structure, and return the selected worker cleanly to the pool.

## Root cause

Skills Eval Daily historically exposes one agent selector and assigns that harness to both setup and operational work. NemoClaw is an operational harness: it needs Codex or Claude Code to deploy VSS, create the isolated NemoClaw client, configure its model route, and install the skill during `expects[0]`. NemoClaw then runs `expects[1..N]`.

The shared evaluator already implements that route split. The missing design seam is a nightly entry point that can select one exact matrix leg and pass independent coding and operational routes.

## Workflow comparison

```text
PR/manual Skills Eval                 Scheduled Skills Eval Daily
─────────────────────                 ───────────────────────────
PR diff or manual filters             Resolve nightly tag/develop
             │                                      │
             ▼                                      ▼
 skill × spec × platform              full catalog matrix
             │                                      │
             ▼                                      ▼
 split coding/operational routes      one harness for both routes
             │                                      │
             └──────────────┬───────────────────────┘
                            ▼
                 adapter → Harbor dataset
                            │
                            ▼
              run_leg scans vss-eval-* pool
              and flocks one eligible worker
                            │
                            ▼
                Harbor run + verification
                            │
                            ▼
              redacted per-leg result artifact
```

The canary does not reuse the PR trigger, branch mutation, comment, or artifact contract. It calls a reusable seam on Skills Eval Daily so the nightly implementation remains the owner of planning, execution, result collection, and cleanup.

## Canary flow

```text
Manual Skills Eval NemoClaw Canary dispatch
                    │
                    ▼
  Guard: fail closed if Daily is queued/running
                    │
                    ▼
 Join Daily's default-branch concurrency group
 (canary cannot cancel; Daily remains authoritative)
                    │
                    ▼
  Plan exactly vss-manage-alerts × cv_mode_gate × L40S
                    │
                    ▼
 run_leg scans the normal pool and flocks one L40S worker
                    │
                    ▼
 Codex: deploy VSS and bootstrap the isolated NemoClaw client
                    │
                    ▼
 NemoClaw: run the operational incident query
                    │
                    ▼
 Harbor: verify, recover logs, and publish trace/result files
                    │
                    ▼
 While flock is held: destroy sandbox, stop its relay/watchdog,
 wipe VSS containers/volumes/networks and trial bind-mount data
                    │
                    ▼
 Record worker-cleanup.txt, release flock, redact and upload
 skills-eval-nemoclaw-canary-results-* artifact
```

There is no dedicated worker and no `BREV_INSTANCE` override. The Brev instance stays running, and cached container images remain. A per-machine flock prevents two legs from changing the same worker. The preflight avoids an already-active Daily run; shared concurrency closes the race around that check. If Daily starts after the canary, Daily may cancel the canary, but the canary cannot cancel Daily.

## POC configuration

| Setting | Value |
| --- | --- |
| Skill | `vss-manage-alerts` |
| Spec | `cv_mode_gate` |
| Platform | `L40S` |
| Setup route | Codex, hosted inference |
| Operational route | NemoClaw, hosted inference |
| Matrix parallelism | `1` |
| Worker selection | normal `vss-eval-*` pool scan and flock |
| Artifact prefix | `skills-eval-nemoclaw-canary-results` |
| Worker cleanup | enabled for the canary only |

`cv_mode_gate` has two ordered steps. Step 1 deploys the Alerts CV profile, onboards its sample, verifies services, and prepares the NemoClaw sandbox. Step 2 asks for today's alerts and verifies that NemoClaw queries Alert Bridge directly and grounds its answer in the returned incidents.

## Non-disruption contract

- The production schedule, full-matrix defaults, Daily artifact prefix, and dashboard ingestion contract do not change.
- The canary has its own workflow identity and artifact prefix, so the dashboard cannot mistake it for a nightly trend point.
- The guard fails closed if the GitHub API cannot prove Daily is idle.
- A reusable caller joins Daily's default-branch concurrency group with cancellation disabled.
- Scheduled Daily remains authoritative and may cancel a canary.
- The worker stays locked through final cleanup.
- A cleanup failure fails the canary and is recorded in `worker-cleanup.txt`.

## Running the proof of concept

The workflow must exist on the repository's default branch before GitHub exposes its manual dispatch button. After merge:

1. Open Actions → **Skills Eval NemoClaw Canary**.
2. Select the ref to evaluate.
3. Optionally provide a hosted operational-model override; leave it empty for the configured NemoClaw default.
4. Run the workflow outside the normal Daily window.
5. Confirm the plan contains exactly `vss-manage-alerts__cv_mode_gate__L40S`.
6. Inspect the Harbor result, trace links, and `worker-cleanup.txt` in the canary artifact.

## Promotion path

1. Pass this one-leg canary repeatedly.
2. Expand the same callable nightly seam to a small operational-skills canary.
3. Change the scheduled Daily defaults to Codex setup plus NemoClaw operations.
4. Keep the existing `skills-eval-daily-results-*` contract for scheduled runs.
5. Verify the established downstream ingestion publishes the latest full-nightly result.

The owner and trigger of that downstream dashboard-ingestion step remain a follow-up before full promotion.
