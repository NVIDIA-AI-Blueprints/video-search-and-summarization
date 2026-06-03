---
name: "vios-git"
description: "VIOS git conventions: branch names, commit messages, and merge-request descriptions. Use before creating any branch, commit, or MR."
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - git
    - commit
    - merge-request
    - branch
    - convention
  domain: workflow
---

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

# VIOS Git Conventions

Authoritative source for branch naming, commit messages, and MR descriptions on this project. Apply these to **every** branch, commit, and merge request — including ones the assistant produces.

Default base branch is `v2.1` (see `CLAUDE.md` git status). Use `rebase` skill to bring a feature branch up to `v2.1`.

---

## 0. Required workflow

Two behaviors are **mandatory**, not optional:

### A. Auto-create a branch before touching code

When the user asks for a bug fix, feature, refactor, CI/CD change, doc update, or any other code change, **create a topic branch before making the first edit** — do not commit straight onto `v2.1` (or whatever the current base branch is).

Steps:

1. Check the current branch. If it is `v2.1` / `main` / any protected base branch, you **must** branch off before editing.
2. Pick the right prefix from the table in §1 based on the nature of the request (`fix/` for a bug, `feat/` for a new capability, `refactor/`, `cicd/`, `test/`, `docs/`, `release/`, `hotfix/`).
3. Resolve the username. Prefer the value in the user's memory; otherwise infer from `git config user.email` (e.g. `prakhars@nvidia.com` → `prakhar`). Confirm with the user only if ambiguous.
4. Ask the user for a Jira / GitLab ticket ID if one exists. If they don't have one, omit it — the minimum form `<type>/<username>/<short-description>` is acceptable.
5. Compose a 2–5 word description that reflects the actual change, not the symptom.
6. Run `git checkout -b <branch>` from the latest base, then start editing.

You may skip the auto-branch step **only** if:

- the user explicitly says to keep editing the current branch, or
- the user is already on a non-base topic branch that fits the change.

If the user is on an unrelated topic branch (e.g. they're on `feat/prakhar/foo` and now asks for an unrelated bug fix), point this out and offer to branch off the base instead — don't silently pile unrelated commits.

### B. Confirm with the user before committing

**Never run `git commit` autonomously after finishing a change.** When the implementation looks done:

1. Run `git status` and `git diff --stat` (or `git diff`) so the user can see exactly what would be committed.
2. Show the proposed commit title and bullets (formatted per §2).
3. Ask the user: *"Ready to commit, or do you want more changes first?"* — wait for an explicit *yes* / *commit* / *go ahead* before running `git commit`.
4. If the user wants changes, apply them and repeat from step 1.

The same rule applies to pushing and to opening an MR — those are also explicit user-approved actions, per the harness's "executing actions with care" guideline.

If the user has previously said *"just commit when done"* in this session or via a saved feedback memory, skip the confirmation for the remainder of that session — but never adopt that as a permanent default without the user saying so.

### C. Hand the user a ready-to-paste MR description after every commit

Immediately after a successful `git commit`, **always** print a complete MR description that the user can copy straight into GitLab. Do this whether or not the user has asked to open an MR — the goal is that they always have it ready.

Steps:

1. Confirm the commit landed (e.g. `git log -1 --oneline` shows the new commit) and report it back.
2. Build the MR description using the exact shape in §3 (Summary, Changes, Testing, Risk, Bug, Jira). Reuse the commit title for `## Summary`, reuse the commit bullets for `## Changes`, drop Bug / Jira trailer lines when the user did not supply them.
3. Render the description inside a fenced code block so the user can copy it verbatim — no extra prose inside the block, no AI-attribution lines.
4. Below the block, in plain text, remind the user of the next manual step (push the branch, open the MR via GitLab UI or `glab mr create --description "$(cat <<'EOF' … EOF)"`). **Do not push or open the MR autonomously** — those remain explicit user-approved actions per §0.B.

If the user has not supplied a **Testing** detail, ask for it (one short sentence about how the change was verified) before printing the description rather than inventing one. If the user has not supplied a **Risk** assessment, default to `Low` and let them adjust.

---

## 1. Branch naming

Pattern:

```
<type>/<username>/<ticket-id>-<short-description>
```

- **Username is mandatory.** Ticket ID is optional. Minimum form: `<type>/<username>/<short-description>`.
- Lowercase for type prefix, username, and description. Ticket IDs keep their original case.
- Hyphens between words. **Never** use underscores as separators.
- Description: 2–5 words.
- Include the Jira/GitLab ticket ID when one exists.

| Prefix | When to use |
|---|---|
| `feat/` | New feature or capability |
| `fix/` | Bug fix |
| `refactor/` | Code restructuring, no behavior change |
| `cicd/` | CI/CD, build system, deployment changes |
| `test/` | Test-only changes |
| `docs/` | Documentation only |
| `release/` | Release preparation |
| `hotfix/` | Urgent production fix |

**Good:**

- `feat/prakhar/VIOS-1234-rtsp-latency-optimization`
- `fix/prakhar/sonarqube-violations`
- `cicd/prakhar/build-optimization`

**Bad:**

- `feat/no-username` (missing username)
- `latency_optimization` (no prefix, no username, underscores)
- `dev_mmj` (vague, no prefix, underscore)

Before creating a branch, confirm the username with the user if you do not already know it.

---

## 2. Commit messages

Every commit follows this exact shape:

```
<title>

* Point 1
* Point 2
* Point 3
* Point 4
* Point 5

Bug <number1>, <number2>
Jira <Id1>, <Id2>
```

Rules:

- **Title** — one line, imperative mood, ≤ 72 characters. Mirrors the change, not the symptom (e.g. `Fix sensor count cache drift and ghost files`, not `bug 12345 fix`).
- **Bullets** — at most 5. Each bullet is one concise sentence describing one change. **Do not pad.** A 2-line change deserves 2 bullets, not 5.
- **Tone** — professional prose. Do not use shorthand connectors like `+`, `&`, `->`, or `/` between concepts — write `and` or `or` instead. Slashes are fine inside identifiers (file paths, URLs, code references) but never as a substitute for natural language.
- **No sensitive data** — never include IP addresses, hostnames, ports, tokens, credentials, API keys, internal URLs, customer identifiers, or any other sensitive value in commit messages, MR descriptions, branch names, or trailer lines. Refer to environments generically: *"verified on the live deployment"*, *"verified against the staging cluster"*. Not *"verified on 10.24.218.241:30888"*.
- **Bug / Jira trailers** — append `Bug <number1>, <number2>` and `Jira <Id1>, <Id2>` at the end. **Ask the user** for the NVBugs IDs and Jira IDs before committing. If the user does not provide them, **omit the corresponding line entirely** — no `Bug TBD`, no `Bug N/A`, no placeholder of any kind.
- **No AI attribution** — do not include any `Co-Authored-By: Claude …`, `🤖 Generated with …`, or "Anthropic"/"AI assistant" lines anywhere. This applies to commit bodies and trailer blocks.

Use a HEREDOC when committing to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
<title>

* Point 1
* Point 2

Bug <number>
Jira <Id>
EOF
)"
```

Example, matching the style of recent commits on `v2.1` (`a9969faf0`, `d10d8af93`, `623515882`):

```
Fix sensor count cache drift and ghost files

* Refresh the cached sensor count from Postgres on every list request.
* Skip orphaned recording files when the originating sensor has been removed.
* Add a regression test covering the post-delete list path.

Bug 5123456
Jira VIOS-2104
```

---

## 3. Merge-request descriptions

GitLab auto-loads `.gitlab/merge_request_templates/Default.md` into the MR description box for new MRs. The template shape mirrors the commit message: summary, up to 5 bullets under **Changes**, a **Testing** section, a **Risk** note, then `Bug` / `Jira` trailers.

When creating an MR programmatically (via `glab`, `gh`, or `git push -o merge_request.description=…`), emit this exact shape:

```
## Summary
<one-line summary matching the commit title>

## Changes
* Point 1
* …  (≤ 5 bullets, no filler)

## Testing
* <how it was verified — be specific about which tests ran or how the change was exercised>

## Risk
<area + mitigation, or "Low">

Bug <number1>, <number2>
Jira <Id1>, <Id2>
```

Same trailer rule as commits: ask the user for Bug / Jira IDs; if not provided, drop the corresponding line entirely. Same data-sensitivity rule: no hostnames, IPs, ports, credentials. No AI-attribution lines anywhere.

If creating via `glab`:

```bash
glab mr create \
  --target-branch v2.1 \
  --title "<commit title>" \
  --description "$(cat <<'EOF'
## Summary
...

## Changes
* ...

## Testing
* ...

## Risk
Low

Bug <number>
Jira <Id>
EOF
)"
```

---

## 4. Pre-commit / pre-push checks

Before staging:

- Run `/security-review` to scan the diff for Checkmarx / nspect issues.
- Run the relevant unit tests (`test/gtests/`) and BDD tests (`test/bdd_tests/`) for the touched modules.
- Never bypass hooks (`--no-verify`, `--no-gpg-sign`, `-c commit.gpgsign=false`) unless the user explicitly requests it. If a hook fails, fix the underlying cause and create a *new* commit — do not `--amend` after a hook rejection.
- `git add` only the specific paths you intend to ship. Avoid `git add -A` / `git add .` — they pick up `.env`, build artifacts, or secrets.

---

## 5. How to apply this skill

The full lifecycle, in order:

1. **Before editing** — derive the username, pick the type prefix, and run `git checkout -b <branch>` per §0.A. Do not start editing on a protected base branch.
2. Make the change. Re-verify against the change description; run unit / BDD tests for the touched modules.
3. Run `/security-review` against the diff (per §4).
4. **Before committing** — show `git status` + the proposed commit message to the user and explicitly ask if they want to commit or keep editing, per §0.B. Wait for a clear yes.
5. Ask the user for **Bug** and **Jira** IDs. Save the answer in memory if they expect to reuse it across commits in the same effort.
6. Run `git commit` with a HEREDOC body (template in §2). `git add` only the specific paths.
7. Ask before pushing. Ask again before opening an MR. When opening, use the shape in §3.

Never invent ticket IDs, never use placeholders, never add AI-attribution lines, never embed hostnames or other sensitive values.
