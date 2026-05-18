# Task-List Setup (Step 0 detail)

The deploy skill uses `TodoWrite` as its **single source of truth** for the plan and per-step progress. This file holds the JSON templates and the rules.

## Core principle

**`TodoWrite` is the plan. Do NOT print your own text rendering of it.** The user's client renders the todo list as a live widget that updates every time the skill calls `TodoWrite merge:true`. A competing plain-text list (`Deployment plan:` + checkbox rows) would:

- duplicate what the widget already shows,
- get stale the instant a todo updates,
- waste terminal scroll,
- clash with the widget's own glyphs when the client truncates.

The skill only prints progress narration (`→` step start, `✔` step result, `?` user input, `⚠` warning, `✖` error — see `ux-conventions.md`). The widget shows the plan.

## Two actions at startup, in strict order, before any other tool

1. **`TodoWrite` (merge: false) with all 10 tasks** — JSON template below. Labels ≤ 50 chars so the widget reads well when the client truncates.
2. **`TodoWrite` (merge: true) to pre-complete inferred tasks** — encode the inferred value inside the `content` field of each pre-completed todo (e.g. `"Identify use case → warehouse-2d"`). The widget will render it inline. No separate text print.

Do NOT run any bash, file read, `AskQuestion`, or other tool between 1 and 2. No platform detection, no NGC config check, no docker inspect — those belong to later steps.

## After startup — update-on-transition pattern

On every Step boundary, make a `TodoWrite merge:true` call that:

- marks the just-finished todo `completed`, updating its `content` to include the resolved value (e.g. `"Detect target platform → x86-dgpu (RTX 3050)"`),
- marks the next todo `in_progress`,
- leaves the rest untouched.

The widget re-renders with the new state. The skill then prints at most a single `✔ <result>` line (for the just-finished step) + a single `→ <next step>` line. **No full-list re-prints.**

## Label rule — short and stable

Every todo `content` field is a **short canonical label** (≤ 30 chars) set once at startup. It must NEVER change during the deploy — no embedded resolved values, no dynamic suffixes. Keeping content short is what makes the client render all 10 rows in the Todo widget instead of collapsing to "+N completed". Resolved values live in the scrollback `✔` narration (e.g. `✔ Platform: x86-dgpu (RTX 3050)`), not in the widget.

| ❌ Long (triggers widget truncation)                                                | ✅ Short (all 5 rows stay visible)        |
|------------------------------------------------------------------------------------|-------------------------------------------|
| `Prepare deploy: usecase + platform + container + model + videos + fetch`          | `Prepare deploy (targets + fetch)`        |
| `Prepare deploy → smartcity-gdino, default container, default model, downloaded`  | `Prepare deploy (targets + fetch)`        |
| `Finalize pipeline settings (batch=4, dynamic, filesrc, eglsink)`                  | `Finalize pipeline settings`              |

## Initial `TodoWrite` call (exact content — copy verbatim)

```json
{
  "merge": false,
  "todos": [
    {"id": "prepare",   "content": "1/5. Prepare deploy (targets + fetch)", "status": "in_progress"},
    {"id": "pipeline",  "content": "2/5. Finalize pipeline settings",       "status": "pending"},
    {"id": "launch",    "content": "3/5. Launch RTVI-CV container",         "status": "pending"},
    {"id": "config",    "content": "4/5. Apply configuration",              "status": "pending"},
    {"id": "start_app", "content": "5/5. Start perception app",             "status": "pending"}
  ]
}
```

> **Numbered prefix rule** (`N/5.`) — every `content` field starts with its
> task number and the total count. This ensures the user sees their
> position in the plan even when the client collapses completed rows
> ("+2 completed"). The number is PART of the content string and must be
> copied verbatim on every `TodoWrite merge:true` call so the client
> doesn't re-render the row on each merge (changed content = flicker).
>
> **Changes from v1.3.0:**
> - 6 todos → **5 todos**. The `targets` and `fetch` todos collapsed
>   into a single `prepare` todo. SKILL.md Step 1 now drives end-to-end:
>   use case detect → platform → load `deploy-defaults.yml` →
>   3-question AskUserQuestion (Container / Model / Videos with YAML
>   defaults) → resolve answers → fetch resources (one
>   `fetch_resources.sh` call: NGC creds gate + download/extract OR local
>   copy into `$HOME/rtvicv-storage/resources/local-<role>/`) → summary.
> - Step → todo mapping: `prepare` → SKILL.md Step 1, `pipeline` →
>   Step 2, `launch` → Step 3, `config` → Step 4, `start_app` →
>   Step 5. Step 6 (next steps) is post-deploy and has no todo.
>
> **Carry-overs:**
> - `ngc_creds` is NOT a top-level todo — credential setup runs as a
>   silent gate inside `prepare` (Step 1.g via `fetch_resources.sh`)
>   that no-ops when creds are cached OR `NEEDS_NGC=0`.
> - Local model and video paths are copied (`cp` / `cp -r`, never
>   symlinked) into `$HOME/rtvicv-storage/resources/local-<role>/` so
>   the `~/rtvicv-storage:/opt/storage` bind mount exposes them at
>   `/opt/storage/resources/local-<role>/` inside the container.

## Pre-complete tasks the user already answered (run IMMEDIATELY after the initial list)

Example — user says: *"deploy warehouse-3d, 4 streams, display, image `nvcr.io/X/Y:tag`, resource `org/team/res:ver`"* (all targets slots resolved + pipeline known; fetch will still happen but is part of `prepare`):

```json
{
  "merge": true,
  "todos": [
    {"id": "pipeline", "status": "completed"},
    {"id": "prepare",  "status": "in_progress"}
  ]
}
```

Example — user says: *"deploy smartcity-rtdetr, model at /data/model.onnx, RTSP cameras rtsp://..."* (all-local, no NGC):

```json
{
  "merge": true,
  "todos": [
    {"id": "pipeline", "status": "completed"},
    {"id": "prepare",  "status": "in_progress"}
  ]
}
```

In the all-local case, the credential gate inside `prepare` is a silent
no-op when `NEEDS_NGC=0` (determined by the resource plan computed in
SKILL.md Step 1.f). The user never sees an NGC credential prompt — and
the local model + videos paths get copied into
`$HOME/rtvicv-storage/resources/local-<role>/` so the bind mount picks
them up.

> **Only update `status`.** Never touch `content` — the labels set at startup must stay identical for the life of the deploy. If the client doesn't see the exact same content string across merges it may re-render the row, causing flicker.

## Progressive updates at every step boundary

On finishing each step, one `merge: true` update that:

1. flips the just-finished todo's `status` to `"completed"`,
2. flips the next pending todo's `status` to `"in_progress"`.

```json
{
  "merge": true,
  "todos": [
    {"id": "prepare",  "status": "completed"},
    {"id": "pipeline", "status": "in_progress"}
  ]
}
```

No `content` mutation. No re-stating the full list in text. The widget and the single `✔ <result>` + `→ <next>` pair in the scrollback are the only things the user sees per transition.

## Workflow rules

- Only **one task** is `in_progress` at a time.
- On entering each Step, flip its todo to `in_progress`.
- On exit, flip it to `completed` and promote the next pending todo.
- **If a step is trivially answered from the user's initial query**, mark it `completed` in the second `TodoWrite` call at startup (Action B) BEFORE starting work — don't leave it pending.
- Within each step, use only the `→ / ✔ / ? / ⚠ / ✖` glyph lines from `ux-conventions.md`. No full-list text prints.
