# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Review Comment Resolver — Master Guide

Fetch all review comments on a GitLab merge request, verify each one against the actual codebase, apply fixes for valid issues, and post a reply to every actionable comment. Replies must attribute the action to the `/review-mr` skill.

**Project root:** resolve with `git rev-parse --show-toplevel`.

---

## Step 0 — Authenticate

Resolve a GitLab personal access token (scope: `api`) in this priority order. Stop at the first that works:

1. `echo $GITLAB_TOKEN` — environment variable set by the user
2. `cat ~/.config/glab-cli/config.yml 2>/dev/null` — glab config
3. `git config --get gitlab.token 2>/dev/null` — git config
4. Ask the user once: "Please set `GITLAB_TOKEN` with an `api`-scoped personal access token from `<gitlab-host>/-/user_settings/personal_access_tokens`."

Verify the token works:
```python
import gitlab
gl = gitlab.Gitlab('<GITLAB_HOST>', private_token='<TOKEN>')
gl.auth()
```

If `python-gitlab` is not installed: `pip3 install python-gitlab --quiet`.

> **Why not the MaaS GitLab MCP?** The MaaS MCP is read-only (only `get`/`list` tools). Posting replies and resolving discussions require write operations (`PUT`/`POST`) that the MCP does not expose.

---

## Step 1 — Parse the MR reference

Accept any of these argument forms:

| Argument form | Example |
|---|---|
| Full URL | `https://gitlab-master.nvidia.com/L4TMM/vms_shim/-/merge_requests/2416` |
| MR IID only | `2416` |
| (none) | Auto-detect from current branch (see below) |

**Parsing rules:**

- Full URL → extract host, `namespace/project`, and IID from the URL path.
- IID only → use the host and project from `git remote get-url origin`.
- No argument → query GitLab for open MRs whose source branch matches `git branch --show-current`; if exactly one matches, use it; if multiple, show a numbered list and ask once.

**Derive host and project path from git remote:**
```bash
git remote get-url origin
# e.g. https://gitlab-master.nvidia.com/L4TMM/vms_shim.git
# → host: gitlab-master.nvidia.com
# → project_path: L4TMM/vms_shim
```

---

## Step 2 — Fetch all comments

```python
import gitlab, json

gl = gitlab.Gitlab('https://<GITLAB_HOST>', private_token='<TOKEN>')
project = gl.projects.get('<PROJECT_PATH>')
mr = project.mergerequests.get(<MR_IID>)

discussions = mr.discussions.list(get_all=True)

# Build a flat list of actionable notes
notes = []
for d in discussions:
    for note in d.attributes['notes']:
        if note.get('system', False):
            continue   # skip system events (branch push, approval, etc.)
        notes.append({
            'discussion_id': d.id,
            'note_id':       note['id'],
            'author':        note['author']['username'],
            'author_name':   note['author']['name'],
            'body':          note['body'],
            'position':      note.get('position'),   # inline position if any
            'resolved':      note.get('resolved', False),
            'created_at':    note['created_at'],
        })

print(json.dumps(notes, indent=2))
```

**Skip entirely (no reply needed):**
- System notes (push events, approvals, label changes)
- Notes whose `author.username` is a CI bot that only reports pass/fail (e.g. `SonarQube` reporting "Quality Gate passed", pipeline status bots)
- Notes that are already replies to a thread started by this skill (body contains `<!-- review-mr-skill -->`)

---

## Step 3 — Classify each comment

Assign one disposition per note. Work through every note; do not skip any.

| Disposition | Meaning |
|---|---|
| `FIX` | Valid issue — apply a code or doc change |
| `FALSE_POSITIVE` | The reviewer's claim is factually wrong (file exists, logic is correct, etc.) |
| `INTENTIONAL` | The pattern is correct by design; explain the rationale |
| `ALREADY_HANDLED` | Addressed elsewhere in the codebase or earlier in this MR |
| `FIXED_THIS_SESSION` | Was valid; fixed by this skill run |
| `INFO` | No action needed (CI pass, informational note from a human) |

**Reviewer-specific heuristics:**

### CodeRabbit / Greptile (AI reviewers)
These bots run shell scripts against a specific commit and may not see files added later in the same MR. Treat their findings as hypotheses, not facts.

- **File-existence claims** (`"file does not exist"`, `"missing reference"`, `"broken link"`): verify using `git ls-files -- <path>` (not filesystem `ls`). A file may exist on disk but be untracked, in which case the reviewer's claim is correct — the file is absent from the git tree and will not be in the MR unless staged. If `git ls-files` returns the path → `FALSE_POSITIVE`; if empty → `FIX` (the file must be staged as part of this MR).
- **Behavioral claims** (e.g. "this function returns X"): read the relevant lines and verify. If the code does what the comment claims → `FIX`; if not → `FALSE_POSITIVE`.
- **Style/convention claims**: check the rest of the codebase for the dominant pattern. If the claim contradicts the established convention → `INTENTIONAL`.
- **Severity labels** (`🔴 Critical`, `🟠 Major`, `🟡 Minor`): use as a signal, not as ground truth — a "Critical" false positive is still a false positive.

### Human reviewers
- Read carefully. Assume good faith and technical accuracy unless you can verify otherwise.
- If a human raises a concern you cannot fully verify → `FIX` (conservative approach) or note it as requiring human judgement in the reply.
- Do not dismiss human comments as false positives without reading the relevant code.

### SonarQube / CI bots
- Parse for pass/fail status.
- If the gate passed → `INFO` (skip reply).
- If the gate failed → treat as `FIX` and investigate the linked issues.

---

## Step 4 — Verify each claim before acting

Never accept a reviewer's claim at face value. For every non-`INFO` note:

### File-existence checks
```bash
# Is the file tracked by git (committed or staged)?
git ls-files -- <path>
# Non-empty output → file is in the git tree (committed or staged) → claim is false positive
# Empty output    → file is absent from git tree → claim is valid even if the file exists on disk
```

Never use filesystem `ls` alone — a file can exist locally as an untracked file while being genuinely absent from the branch's committed/staged state. The reviewer scans the git tree, not the working directory.

### Code behavior checks
- Read the file at the referenced line(s).
- Trace the relevant code path (function signatures, defaults, dispatch logic).
- Confirm or refute the claim with evidence.

### Convention checks
- Grep the codebase for the pattern in question.
- Count occurrences of both forms (e.g. `<PLACEHOLDER>` vs `${PLACEHOLDER}`).
- If the project uses one form overwhelmingly → defend it as `INTENTIONAL`.

### Logic/documentation consistency
- Read both the documentation and the code it documents.
- If they disagree → one of them is wrong; determine which and classify accordingly.

---

## Step 5 — Apply fixes (FIX and FIXED_THIS_SESSION)

- Apply each fix before posting its reply.
- Prefer the smallest correct change — do not refactor surrounding code.
- If a fix touches a public interface or has non-obvious side effects, note this in the reply.
- After all fixes are applied, **ask the user to commit and push before posting any replies**. AI reviewers (CodeRabbit, Greptile) re-scan the repo when they receive a reply — if the fix is not yet pushed, they will contest the reply, creating extra noise. Wait for user confirmation that the push is complete before proceeding to Step 6.

---

## Step 6 — Post replies

**Pre-condition: the user must have committed and pushed all fixes from Step 5 before any reply is posted.** Never reply "Fixed" to a thread before the fix is on the remote branch.

Post one reply per actionable discussion thread (not per note within a thread — reply once to the root note's discussion).

**Exception — contested follow-ups:** If a reviewer posts new, distinct claims *after* the skill's reply and those claims are false positives or require rebuttal to close the thread, a second reply is permitted. Keep it short and evidence-based. This is the only circumstance where posting twice in one thread is allowed.

**Skip posting a reply if:**
- Disposition is `INFO`
- The discussion is already resolved and the thread already has a reply from this skill (unless the exception above applies)

**Python snippet to post a reply:**
```python
discussion = note_to_discussion[note_id]
discussion.notes.create({'body': reply_body})
```

### Reply format

Every reply must begin with the attribution marker and end with the HTML comment so this skill can detect its own replies on a re-run:

```
> Handled by the `/review-mr` skill.

<disposition-specific body — see templates below>

<!-- review-mr-skill -->
```

---

## Reply Templates

### FIX applied
```
> Handled by the `/review-mr` skill.

Fixed. <one-sentence description of what was changed and where (file:line)>.
<!-- review-mr-skill -->
```

### FALSE_POSITIVE
```
> Handled by the `/review-mr` skill — dismissed as false positive.

<Specific factual rebuttal. Include the evidence: file path that exists, line number that shows the correct behavior, grep output, etc. Do not just say "this is wrong" — show why.>
<!-- review-mr-skill -->
```

### INTENTIONAL
```
> Handled by the `/review-mr` skill — no change, intentional by design.

<Explain the design rationale and point to where the convention or decision is documented or established in the codebase.>
<!-- review-mr-skill -->
```

### ALREADY_HANDLED
```
> Handled by the `/review-mr` skill — already addressed.

<State where and how it is handled: file, line, or earlier commit.>
<!-- review-mr-skill -->
```

---

## Step 7 — Wait for AI reviewer responses, then triage and close

### 7a — Report initial actions

Output a concise table of what was done in Steps 3–6:

```
## MR !<IID> — Review addressed

| # | Discussion | Author | Severity | Disposition | Action |
|---|------------|--------|----------|-------------|--------|
| 1 | acb53eaf   | CodeRabbit | Critical | FIX | Fixed build-containers.md:39; replied |
| 2 | f019dd1e   | CodeRabbit | Minor    | FALSE_POSITIVE | Replied |
| 3 | 12347      | humanuser  | —        | FIX | Fixed src/foo.cpp:42; replied |
...

Fixes applied: N  |  False positives dismissed: N  |  Intentional/no-change: N
```

### 7b — Wait ~2 minutes for AI reviewer re-scans

Before waiting, tell the user:

> Waiting 2 minutes for CodeRabbit/Greptile to re-scan and post follow-up replies…

AI reviewers re-scan the branch when they receive a reply and typically respond within 1–2 minutes. Then wait and re-fetch:

```python
import time
time.sleep(120)
discussions = mr.discussions.list(get_all=True)
```

### 7c — Triage follow-up replies and act

For each discussion that received a skill reply in Step 6, check for notes posted **after** the skill's reply by the original reviewer. Classify and act immediately:

| Follow-up content | Action |
|---|---|
| Acknowledgement ("confirmed", "LGTM", "thanks", "✅", thumbs-up, "resolved") | **Auto-resolve** — proceed to Step 8 for this discussion, no user input needed |
| No reply | **Auto-resolve** — absence of objection = uncontested |
| New valid issue raised | Apply fix, commit+push, post a follow-up reply (contested follow-up exception from Step 6), then **auto-resolve** |
| New false positive or claim already rebutted | Post a follow-up rebuttal reply, then **auto-resolve** |
| Genuine human reviewer contest that cannot be automatically resolved | Surface to user — do not resolve |

**Detecting acknowledgement vs. disagreement:** words like "still", "not fixed", "incorrect", "disagree", "wrong", "should be", question marks, or any non-acknowledgement phrasing = contested. Anything else = acknowledgement.

### 7d — Report outcome

After resolving, report a summary table. Only ask the user if there are threads that genuinely could not be resolved:

```
## MR !<IID> — Follow-up triage

| Discussion | Author | Follow-up | Action |
|------------|--------|-----------|--------|
| acb53eaf   | CodeRabbit | "Confirmed ✅" | Auto-resolved |
| f019dd1e   | CodeRabbit | (no reply) | Auto-resolved |
| 7640b533   | CodeRabbit | "Still wrong…" → false positive, rebutted | Auto-resolved |
| 12347      | humanuser  | "This approach breaks X" | Needs your input |

Resolved: N  |  Needs attention: N
```

If any threads need user attention, describe what the reviewer said and ask the user how to proceed.

---

## Step 8 — Resolve a discussion

Called automatically from Step 7c — no user confirmation gate.

### Fetch the latest discussion state

Re-fetch all discussions — do not reuse the data from Step 2, as reviewers may have replied since then:

```python
discussions = mr.discussions.list(get_all=True)
```

### Determine which discussions to resolve

For each discussion that received a `<!-- review-mr-skill -->` reply in Step 6:

1. Collect all notes in the thread **after** the skill's reply (i.e. notes with a `created_at` timestamp later than the skill reply note).
2. A discussion is **contested** if any of those later notes:
   - Are authored by the original reviewer (same `author.username` as the root note)
   - AND do not themselves contain `<!-- review-mr-skill -->` (i.e. not our own follow-up)
   - AND contain substantive disagreement — look for words like "still", "not fixed", "incorrect", "disagree", "wrong", "this is", "should be", question marks, or any non-acknowledgement phrasing
3. A discussion is **uncontested** if there are no later notes from the reviewer, or their only later note is an acknowledgement (e.g. "thanks", "LGTM", "looks good", thumbs-up).

### Resolve uncontested discussions

```python
import datetime

for d in discussions:
    notes = d.attributes['notes']
    # Find our skill reply in this thread
    skill_reply = next(
        (n for n in notes if '<!-- review-mr-skill -->' in n.get('body', '')),
        None
    )
    if not skill_reply:
        continue  # this thread was not touched by this skill

    skill_reply_time = datetime.datetime.fromisoformat(skill_reply['created_at'].replace('Z', '+00:00'))
    root_author = notes[0]['author']['username']

    # Notes from the reviewer after the skill replied
    later_reviewer_notes = [
        n for n in notes
        if n['author']['username'] == root_author
        and '<!-- review-mr-skill -->' not in n.get('body', '')
        and datetime.datetime.fromisoformat(n['created_at'].replace('Z', '+00:00')) > skill_reply_time
    ]

    if later_reviewer_notes:
        print(f"Discussion {d.id} CONTESTED — leaving open")
    else:
        d.notes.create({'body': 'Resolved by the `/review-mr` skill — no contesting reply from the reviewer.\n\n<!-- review-mr-skill -->'})
        # Mark resolved via the GitLab API
        import requests, os
        resp = requests.put(
            f"https://<GITLAB_HOST>/api/v4/projects/<PROJECT_ID>/merge_requests/<MR_IID>/discussions/{d.id}",
            headers={"PRIVATE-TOKEN": "<TOKEN>"},
            json={"resolved": True}
        )
        if resp.ok:
            print(f"Discussion {d.id} resolved")
        else:
            print(f"Discussion {d.id} resolve failed: {resp.status_code} {resp.text}")
```

> **Note:** `python-gitlab`'s discussion object does not expose a `save(resolved=True)` shortcut reliably across versions — use the raw `requests.put` call above for the resolve step.

### Report resolved vs contested

```
## MR !<IID> — Discussions resolved

| Discussion | Root note author | Status |
|------------|-----------------|--------|
| <id>       | CodeRabbit      | Resolved |
| <id>       | greptile        | Contested — left open |
...

Resolved: N  |  Contested (left open): N
```

---

## Constraints

- Never commit or push changes — leave git operations to the user.
- Never dismiss a human reviewer's comment as a false positive without reading and verifying the referenced code.
- Never post more than one reply per discussion thread.
- Do not re-reply to threads that already contain a `<!-- review-mr-skill -->` marker.
- Do not reply to `INFO` notes (CI pass reports, system events).
- Do not modify files outside the project root.
- If `GITLAB_TOKEN` is not available after checking all sources, ask the user once and then stop — do not proceed without authentication.
