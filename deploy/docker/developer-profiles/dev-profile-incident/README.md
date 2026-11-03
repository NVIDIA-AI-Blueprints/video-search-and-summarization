<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# dev-profile-incident

Human-facing overview for the incident search and reporting profile. Coding agents start at [`AGENTS.md`](AGENTS.md).

## Documentation

**For agents**
- [AGENTS.md](AGENTS.md): components, checks, architecture rules, boundaries, docs rule
- [Skills router](skills/README.md): operational skills (`incident-start`, `incident-operate-vm`, `incident-analyze-video`, `incident-search`, `incident-manage-database`, `incident-run-eval`, `shadcn`)

**Reference (`.docs/`)**
- [Current status & known issues](.docs/status.md)
- [Action plan & scope](.docs/action-plan.md)
- [System architecture & sequence flows](.docs/architecture.md)
- [Database schema & R2 storage](.docs/data.md)
- [Incident analysis schema & field mapping](.docs/analysis-schema.md)
- [Profile operations & runbook](.docs/incident-profile-operations.md)
- [VM API contracts & tunnel endpoints (frontend)](.docs/vm-api-contracts.md)
- [Decision log](.docs/decisions.md)
- [MVP2 natural-language search overview](.docs/mvp2/README.md)
- [MVP2 evaluation plan (retrieval, critic, detector, embeddings)](.docs/mvp2/eval.md)
- Ground-truth refinement and prompt-experiment plan (direct VLM vs VSS): local planning document `.docs/mvp2/ground-truth-and-prompt-experiments-plan.md`, not tracked in Git; audited against the work in the [plan audit](eval/docs/experiments/plan-audit.md)
- [Annotation guidelines v1.0 (approved)](eval/ground_truth/annotation-guidelines-v1.0.md)
- [Evaluation V2 methodology v2.0 (proposed)](eval/methodology/evaluation-methodology-v2.0.md) and its [pre-registration skeleton](eval/methodology/evaluation-v2-preregistration.md)
- [incident-console-v2 hexagonal layout](.docs/mvp2/console-hexagonal-architecture.md)

**Components**
- [Next.js console (v2, active)](incident-console-v2/README.md)
- [VLM gateway](vlm-gateway/README.md)
- [Mock backends](mock-backend/README.md)
- [Evaluation pipeline](eval/README.md)
- [Incident contract (prompt + schema)](contracts/README.md)
- [Experiment prompt registry (not the production contract)](contracts/experiments/README.md)
- [Prompt experiment runner and guards](eval/experiments/README.md); [readiness and budget statement](eval/docs/experiments/readiness.md); [experiment decision record (D9–D15, Stage 0)](.docs/mvp2/experiment-decision-record.md); [local experiment agent](eval/experiments/agent/README.md); [Stage 1 baseline results](eval/docs/experiments/stage1-baselines.md); [Stage 2 execution plan](eval/docs/experiments/stage2-plan.md); [Stage 2a results](eval/docs/experiments/stage2a-results.md); [Stage 2b results and final Stage 2 selection](eval/docs/experiments/stage2b-results.md); [Stage 3 execution plan](eval/docs/experiments/stage3-plan.md); [Stage 3 results](eval/docs/experiments/stage3-results.md); [consolidated final evaluation report](eval/docs/experiments/final-report.md); [evaluation plan audit and deviation record](eval/docs/experiments/plan-audit.md); [end-to-end VSS profile plan and budget](eval/docs/experiments/e2e-profile-plan.md) and [results](eval/docs/experiments/e2e-profile-results.md); [Stage 2 quarantine attestation](eval/docs/experiments/stage2-quarantine-attestation.md); [gateway failures and recovery protocol](eval/docs/experiments/infrastructure-and-recovery.md); [evaluation methodology v1.2](eval/methodology/evaluation-methodology-v1.2.md); [methodology v1.3 severity reporting extension](eval/methodology/evaluation-methodology-v1.3.md) and its [severity analysis](eval/docs/experiments/severity-v1.3.md) and its matching calibration ([v2, final](eval/methodology/calibration/matching-v1.2-calibration-v2.md); [v1](eval/methodology/calibration/matching-v1.2-calibration.md)); [Stage 2 registered prompts](eval/docs/experiments/stage2-prompts.md) ([drafts](eval/experiments/stage2-drafts/README.md))
- [Supabase schema, SQL harness and midterm-to-finals copy](supabase/README.md); [frozen midterm record](supabase/midterm/README.md)
- [Lifecycle scripts](.scripts/README.md)
- [VM dotfiles](.dotfiles/README.md)
- [Archived components](archived/README.md) (read-only)

## Steps to set up and deploy

Full procedure, checks and troubleshooting: [`incident-start`](skills/incident-start/SKILL.md).

`./start.sh` is THE single command to set up and launch everything from your laptop.

### 1. Secrets & environment files

1. Place `.env.local` under this directory (`deploy/docker/developer-profiles/dev-profile-incident/.env.local`).
2. Symlink `.env.local` to the sub-services:

```bash
ln -sfn ../.env.local incident-console-v2/.env.local
ln -sfn ../.env.local vlm-gateway/.env.local
ln -sfn ../.env.local mock-backend/.env.local
```

### 2. Launching via start.sh (The Canonical Entry Point)

Run `start.sh` with your chosen mode:

```bash
./start.sh --mode mock   # zero GPU: local mock backend and VLM gateway, no SSH (mock-backend tests only, never for UI or backend work)
./start.sh --mode vm     # kwanz-ws over an SSH tunnel (VSS uses GPU 0 only there)
```

What each mode starts and how to troubleshoot it: [`incident-start`](skills/incident-start/SKILL.md).
