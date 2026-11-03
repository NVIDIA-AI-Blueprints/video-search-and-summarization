# Incident Profile Skills

Operational skills for `dev-profile-incident`, the incident search and reporting capstone built on the VSS blueprint. Each subdirectory is a self-contained skill in the [agentskills.io](https://agentskills.io/specification) format, modelled on NVIDIA's [`skills/`](../../../../../skills/README.md).

Like NVIDIA's skills, these are for **deploying and operating** the profile from natural language (start the stack, analyse a clip, apply a migration, run the eval). They are not coding guidance. For how to change code in this profile, read [`../AGENTS.md`](../AGENTS.md).

> **New here?** Read [Orientation](#orientation) and then [Which skill do I need?](#which-skill-do-i-need). Background facts live in [`../.docs/`](../.docs/). Skills link to those files instead of copying them.

---

## Orientation

```
  LAPTOP                                    kwanz-ws (shared, GPU 0)
  ┌───────────────────────────┐ SSH tunnel  ┌──────────────────────────────┐
  │ incident-console-v2 :3200 │────8000────▶│ vss-agent :8000 (native)     │
  │ vlm-gateway :8600 (mock)  │             │ phoenix :6006 (Docker)       │
  │ mock mode only:           │             │ tracking stack (default)     │
  │   mock-backend  :7777     │             └──────────────┬───────────────┘
  │                           │                            │
  └─────────────┬─────────────┘                            │
                ▼                                          ▼
  CLOUD:  Brev Switchyard (LLM/VLM) · Supabase (PostgREST + RPC) · Cloudflare R2 (videos)
```

Two ways to run it, both through [`start.sh`](../start.sh):

| Mode | Backend | Analysis path (`ANALYSIS_MODE`) | Needs |
|---|---|---|---|
| `--mode mock` | `mock-backend` + `vlm-gateway` on the laptop | `gateway`: console calls the gateway, the console writes Supabase | `.env.local`, no GPU, no SSH |
| `--mode vm` (default) | Native `vss-agent` + Docker Phoenix on `kwanz-ws` | `agent`: console calls `POST /api/v1/incidents/{id}/analyze` (`skip_rp1: true`); the agent inserts the AI revision and returns `revision_id`, the console writes `model_runs` | Tailscale, `Host kwanz-ws` SSH config, `.env.local` |

---

## Which skill do I need?

| I want to… | Use this skill |
|---|---|
| Set up secrets and launch the console, locally or against the VM | [`incident-start`](incident-start/SKILL.md) |
| Deploy, restart, inspect or fix services on `kwanz-ws` | [`incident-operate-vm`](incident-operate-vm/SKILL.md) |
| Upload a clip and generate, re-run, version or mark official an incident report | [`incident-analyze-video`](incident-analyze-video/SKILL.md) |
| Search incident evidence, ask grounded follow-up questions, or refresh evidence crops | [`incident-search`](incident-search/SKILL.md) |
| Change or inspect the Supabase schema, test or apply a migration, query incident data, or find a video in R2 | [`incident-manage-database`](incident-manage-database/SKILL.md) |
| Ingest ground truth, run the P1/RP1 VLM benchmark, aggregate results | [`incident-run-eval`](incident-run-eval/SKILL.md) |
| Build, style or migrate UI components with shadcn/ui and Tailwind | [`shadcn`](shadcn/SKILL.md) (UI work in `incident-console-v2` should read this first) |
| Natural-language video search & chat (MVP2) | Use [`incident-search`](incident-search/SKILL.md). Background facts and contracts are in [`.docs/mvp2/README.md`](../.docs/mvp2/README.md), [`.docs/status.md`](../.docs/status.md), `search-contract-v1`, and `chat-contract-v1`. Upstream reference: [`vss-search-archive`](../../../../../skills/vss-search-archive/SKILL.md) |

**Easy to confuse:**

- `incident-start` (laptop, daily launch) vs. `incident-operate-vm` (the backend on `kwanz-ws`: native services, Compose, env). `start.sh --mode vm` self-heals the VM, so reach for `incident-operate-vm` only when that fails or you need to change something.
- `incident-analyze-video` (produce one report and check it) vs. `incident-run-eval` (batch-score many predictions against ground truth).
- `incident-manage-database` (schema, migrations, storage layout) vs. `incident-analyze-video` (rows written by one analysis).
- These skills vs. NVIDIA's `vss-deploy-profile`: that skill drives `dev-profile.sh`, which does **not** know the `incident` profile. Never use it to deploy or tear down this profile.

---

## Catalogue

| Skill | Description |
|---|---|
| [incident-start](incident-start/SKILL.md) | Prepare `.env.local` and symlinks, then launch via `start.sh --mode mock\|vm`. Covers mode choice, SSH preflight, and health checks. |
| [incident-operate-vm](incident-operate-vm/SKILL.md) | Operate `kwanz-ws`: the native-vs-Docker split, `native-services.sh`, Compose appliances, `generated.env.remote` rules, the agent editable reinstall, and the troubleshooting order. Includes the never-run list. |
| [incident-analyze-video](incident-analyze-video/SKILL.md) | Upload → analyse → persist → version flow in both modes. Covers agent and console API contracts, error codes, where each row comes from, and verification. |
| [incident-search](incident-search/SKILL.md) | Search and chat requests, critic verdicts, evidence crops, and the local/VM verification path. |
| [incident-manage-database](incident-manage-database/SKILL.md) | Finals schema (baseline, revision schema, RPCs), the SQL harness and security check, `supabase db push` with dry-run first, PostgREST vs direct-Postgres access, and the R2 key layout. Never writes to the frozen midterm project. |
| [incident-run-eval](incident-run-eval/SKILL.md) | Standalone `eval/` pipeline: embedding server, GT ingest, seeded split, batch run, aggregation, and hermetic tests. |
| [shadcn](shadcn/SKILL.md) | Component library, styling, rules, and design system patterns for UI work in `incident-console-v2` (read this first for console UI changes). |

---

## Install

Claude Code needs no install step. [`/.claude/skills/`](../../../../../.claude/skills/) at the repo root holds symlinks to each skill folder here, so sessions started in this repo load them automatically. Antigravity and other universal agentskills.io hosts discover the shadcn skill via [`.agents/skills/`](../../../../../.agents/skills/).

For other hosts (Codex, Cursor, any agentskills.io host), use NVIDIA's install prompt with this directory: symlink each `skills/incident-*/` folder into the host's skills directory.

## Maintaining these skills

- **Source of truth.** This directory is canonical. Add a skill here, then add a matching symlink under the repo-root `.claude/skills/`: `ln -s ../../deploy/docker/developer-profiles/dev-profile-incident/skills/<name> .claude/skills/<name>`. Cross-harness skills like `shadcn` are additionally symlinked under `.agents/skills/`.
- **Frontmatter.** `name` must match the folder. `description` says when to use the skill and when not to. Keep `license` and `metadata`.
- **No duplicated facts.** Put facts in `.docs/` and link to them. A skill owns only the procedure: steps, commands, gates and verification.
- **Same PR.** When a command, port, script or contract changes, update the affected skill in the same PR as the `.docs/` update, and remove or amend any `decisions.md` entry the change supersedes (see [`../AGENTS.md`](../AGENTS.md)).
- **No evals.** NVIDIA's Skills Eval CI only covers root `skills/**`, so these skills have none.
