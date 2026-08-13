# AGENTS.md

Behavioral rules for any coding agent working in this repository — Claude Code, Codex,
Cursor, or otherwise. Read this before writing code; it outranks your defaults.

Order of work: **grill → plan → publish the plan → build → verify.**

**Tradeoff:** these rules bias toward caution over speed. For genuinely trivial work —
typo fixes, version bumps, mechanical renames, regenerating a lockfile — use judgment and
skip to §5.

---

## 1. Grill before you build

Do not start from your first interpretation of the request. Interview the requester until
you reach a shared understanding.

Map the work as a **design tree**: every decision branches into the decisions that hang
off it. Work the tree in **rounds**.

- The **frontier** is every decision whose prerequisites are already settled — the
  questions you can ask *now* without guessing at answers you haven't heard yet.
- Ask the whole frontier in one round. Number each question, give your recommended
  answer, then stop and wait.
- Each round of answers reshapes the tree: settled decisions push the frontier outward
  and unblock questions that depended on them. Recompute the frontier, ask the next round.
- A question whose answer depends on another question still open in this round belongs to
  a *later* round, not this one.

Format every question like this:

```
❓ **Q1** — **<question title>**: <question body; may run several paragraphs, and may
offer multiple choices>

➡️ <your recommended answer>
```

**Finding facts is your job, never the requester's.** When a frontier question needs a
fact from the environment — a config default, which profile ships a service, whether a
dependency already does this — go find it. Dispatch a sub-agent if it is slow. Don't block
on it: a running exploration is an unsettled prerequisite, so only the questions
downstream of it wait. Ask the rest of the frontier now.

**The decisions are the requester's.** Put each one to them and wait.

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — never pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what is confusing. Ask.

The session is done when the frontier is empty: every branch visited, nothing left
silently assumed. **Do not write code until the requester confirms you have reached a
shared understanding.**

## 2. Publish the plan on the pull request

The grilling record is what turns a diff into a reviewable decision. **Every PR you open
carries it in the PR description.**

The repo template gives you `## Description` and `## Checklist`. Insert `## Plan` between
them:

```markdown
## Plan

**Goal:** <one sentence — what is true after this PR that was not true before>

**Decisions** (from grilling — every question that was put to the requester)

| # | Question | I recommended | Decided |
|---|----------|---------------|---------|
| Q1 | <title> | <your recommendation> | <the requester's actual answer> |
| Q2 | … | … | … |

**Steps**

1. <step> → verify: <check>
2. <step> → verify: <check>

**Out of scope:** <what was raised and deliberately left out, and why>
```

Rules for the record:

- Record the requester's **actual** answer, not the one you preferred. A row where they
  overrode your recommendation is the most useful row in the table.
- Keep questions answered "your call" — that documents delegated authority.
- Facts you looked up yourself do not belong here. Only decisions.
- It lives in the PR description, not in a committed file. The plan is review context, not
  repo content, and a reviewer must see it without checking out the branch.
- Grilled across several sessions? The description carries the merged final state, not a
  transcript.
- Scope changed mid-PR? Update the section. A stale plan is worse than no plan.

Reviewers read `## Plan` first. A diff that contradicts its plan gets sent back.

## 3. Simplicity first

Minimum code that solves the stated problem. Nothing speculative.

- No features beyond what was asked. No abstraction for single-use code.
- No "flexibility" or configurability nobody requested, and no indirection you can't
  justify in one sentence.
- No error handling for impossible scenarios.
- If you wrote 200 lines and it could be 50, rewrite it.

Ask: *would a senior engineer say this is overcomplicated?* If yes, simplify.

**Grow the system in layers.** Start from the smallest version that works end to end, then
add each new capability on top of a product that already works. Never trade a working
product for unfinished complexity.

Keep components modular and concerns clearly separated. Simple is not the same as tangled.

## 4. Reach for what already exists

Before writing an implementation, check whether one is already in front of you.

- **Lean on the dependencies already in the project.** Do not assume a library lacks a
  capability without checking its documentation and types.
- Prefer established, well-maintained libraries when they reduce overall complexity or
  improve reliability. Do not reimplement common functionality without a clear reason.
- Adding a dependency is an **ask-first** action here: new packages pass a license
  denylist/allowlist gate (`.github/scripts/check_python_licenses.sh`,
  `check_ui_licenses.sh`). Raise it during grilling, not in the diff.

## 5. Surgical changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor what isn't broken.
- Match existing style, even if you'd do it differently.
- Notice unrelated dead code? Mention it — don't delete it.

When your changes create orphans:

- Remove imports, variables, and functions that **your** change made unused.
- Don't remove pre-existing dead code unless asked.

The test: **every changed line traces directly to the request.**

## 6. Delete obsolete paths — inside the boundary

When your change makes an internal code path obsolete, remove it. Don't bolt on a
compatibility layer, a fallback branch, or a migration shim to keep the old path breathing
beside the new one. Two ways to do one thing is a bug that hasn't been filed yet.

That rule stops at this repository's published surface. VSS is a released blueprint with
users, so the following are not yours to break silently — they change through deprecation,
and only after being raised during grilling:

- REST/OpenAPI surfaces of any service
- `config.yml` schema and environment variable names under
  `deploy/docker/developer-profiles/`
- Helm chart values, and published GHCR image tags and aliases
- CLI flags, and anything the documentation tells a user to type

Behind those boundaries: no back-compat, delete freely. Across them: propose, deprecate,
then delete.

## 7. Decide architecture for the long term

Make architectural decisions you would still make in a year. Do not accept a stopgap that
only works for now and is meant to be replaced later — the replacement PR rarely gets
written.

This is not a license to build speculatively. The two rules divide cleanly:

| | Decide for the long term | Build for today |
|---|---|---|
| Applies to | boundaries, interfaces, names, data models, where a thing lives | the implementation behind the boundary |
| Ask | "is this still right when the next three features land?" | "what is the least code that satisfies today's requirement?" |

Get the seam right and keep what sits behind it small. A simple implementation behind a
well-placed boundary is cheap to replace; a clever one behind a wrong boundary is not.

## 8. Verify, then claim

Define success criteria before you start and loop until they are met.

Turn tasks into verifiable goals:

- "Add validation" → "write tests for the invalid inputs, then make them pass"
- "Fix the bug" → "write a test that reproduces it, then make it pass"
- "Refactor X" → "tests pass before and after"

Every step in your `## Plan` carries its own `verify:` check. Weak criteria ("make it
work") force constant clarification; strong ones let you loop on your own.

Before you claim done:

- Run the checks. Pre-commit hooks (ruff, mypy, SPDX headers, secret scan, DCO) mirror CI,
  so passing locally means passing remotely — see
  [CONTRIBUTING.md § Local development and testing](CONTRIBUTING.md#local-development-and-testing).
- Report failures honestly, with the output. A skipped step is a reported step. Never
  describe unrun tests as passing.

## Repository conventions

| | |
|---|---|
| Base branch | `develop` |
| Branch naming | `<type>/<name>` — `feat`, `fix`, `docs`, `refactor`, `test`; dashes, not spaces ([CONTRIBUTING.md § Branch naming](CONTRIBUTING.md#branch-naming)) |
| Commits | DCO sign-off required — `git commit -s`. Enforced at the `commit-msg` hook and in CI |
| PR body | Template `## Description` + `## Plan` (§2) + `## Checklist` |
| Per-service setup | The nearest `AGENTS.md` / `CLAUDE.md` owns commands, layout, and style: `services/agent/AGENTS.md`, `services/vios/CLAUDE.md`, `services/video-summarization/CLAUDE.md` |

Precedence: a service-level `AGENTS.md` or `CLAUDE.md` overrides this file on
project-specific matters. This file governs behavior and process everywhere.

## Sources

These rules are merged and reconciled from three public sources:

- [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
  — think before coding, simplicity first, surgical changes, goal-driven execution;
  derived from Andrej Karpathy's observations on LLM coding pitfalls (§§1, 3, 5, 8).
- [Marcos Hernanz's AGENTS.md](https://x.com/MarcosHernanz/status/2083954734487212511)
  — grow in layers, lean on existing dependencies, remove obsolete paths, decide for the
  long term (§§3, 4, 6, 7).
- [mattpocock/skills — `grilling`](https://github.com/mattpocock/skills/blob/main/skills/productivity/grilling/SKILL.md)
  — the design tree, the frontier, and the question format (§1).
