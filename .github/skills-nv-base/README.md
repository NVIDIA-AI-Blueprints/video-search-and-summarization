# Skills NV-BASE CI

Three-step Tier-1 skill gate that runs on every PR touching `skills/` or
this harness. Fails the check if Step 1 or Step 2 reports a blocking
finding. Step 3 posts a sticky PR comment summarising all findings (kept
in sync across pushes by a hidden marker).

## Files

| File | Role |
|---|---|
| [`../workflows/skills-nv-base.yml`](../workflows/skills-nv-base.yml) | GitHub Actions workflow definition |
| [`run_check.py`](run_check.py) | Step 1 — driver for `nv-base validate` (parses JSON report, emits annotations, dumps findings to `$NVBASE_FINDINGS_JSON`) |
| [`skill_compliance_check.py`](skill_compliance_check.py) | Step 2 — vendored playbook compliance checker (NAM / FM / STR / SEC). `--json-out` dumps findings for the comment poster |
| [`post_comment.py`](post_comment.py) | Step 3 — composes a markdown summary from both JSON inputs and posts/updates a sticky PR comment via REST API |
| `README.md` | this file |

## What each step covers

**Step 1 — `nv-base validate --type skill --external --checks schema,secrets,pii,unicode --report json`:**
- SCHEMA: frontmatter validity, folder hierarchy, naming convention, recommended sections
- SECRETS / PII / UNICODE: hardcoded credentials, PII, Trojan-Source smuggling
- Profile `external` (silently demotes `author_missing` HIGH → MEDIUM via bundled `external.yaml` policy)
- Gate: any `critical` or `high` finding → `::error` + exit 1

**Step 2 — `skill_compliance_check.py`** (vendored from `agent_skills_playbook` with modifications):
- **NAM-001..007**: kebab-case, generic-name guard, approved verbs / team prefixes, token/char limits, reserved bare names, cross-skill collision
- **FM-001..011**: frontmatter required (only `name` + `description` per [agentskills.io spec](https://agentskills.io/specification); `version`/`reviewed`/`data_classification`/etc. checked only if present), description-quality heuristics (length, trigger phrase, ≥3 user-phrasing scenarios, no implementation-led lead)
- **STR-001..003**: `SKILL.md` required, `SKILL.md` ≤500 lines (matches Anthropic best-practices), **`evals/` directory presence (WARN only; no filename restriction)**
- **SEC-001..003**: redundant with Step 1 but independent — kept for defense-in-depth and so the gate keeps working if nv-base is unavailable
- Gate: any ERROR-level finding → exit 1

## Modifications vs upstream playbook script

Documented in the script's docstring; summary:

| Upstream rule | Action | Reason |
|---|---|---|
| `STR-003` evals/evals.json required (ERROR) | Softened to WARN, no filename restriction | `evals/` is not part of [agentskills.io spec](https://agentskills.io/specification); the `evals.json` shape is one community runner's convention, not a standard; none of [Anthropic's reference skills](https://github.com/anthropics/skills) ship `evals/evals.json` |
| `STR-004` references/README.md required (WARN) | Dropped | Not in spec; not used by Anthropic's reference skills |
| `EVAL-001..005` (eval coverage / negative cases / assertions) | Dropped entire family | Tied to STR-003 shape; the repo's existing `skills-eval` workflow runs real evals as Tier-3 |
| `REQUIRED_FM_FIELDS = [name, description, owner, service, version, reviewed]` | Trimmed to `[name, description]` | Match [agentskills.io spec](https://agentskills.io/specification); upstream would mass-false-fail any skill following the spec (which all skills in this repo do) |

`APPROVED_TEAM_PREFIXES` and `APPROVED_VERBS` carried over as-is (already
VSS-flavored). Adjust by PR review when the playbook updates.

## Where it runs

Self-hosted runner labelled **`nv-base`**. NV-BASE is not publicly
distributed, so Step 1 needs the binary pre-installed; Step 2 is
stdlib-only and would also run on `ubuntu-latest`, but is kept on the
same runner to share the checkout.

## Runner bootstrap (one-time, by operator)

1. Provision a host with network access to the internal NV-BASE
   distribution channel and to `api.github.com`.
2. Install nv-base into a dedicated venv:

   ```bash
   sudo python3 -m venv /opt/nvbase-venv
   sudo /opt/nvbase-venv/bin/pip install --upgrade nv-base
   /opt/nvbase-venv/bin/nv-base --version
   ```

   The pip command needs the internal NV-BASE index URL; check the
   NV-BASE distribution docs for the current location. Operators with
   access should pin a version (`nv-base==X.Y.Z`) for reproducibility.

3. Register the host as a GitHub Actions self-hosted runner on this
   repository with the **`nv-base`** label
   (Settings → Actions → Runners → New self-hosted runner).
4. Confirm the workflow can resolve the binary — `${{ env.NVBASE_BIN }}`
   in [`../workflows/skills-nv-base.yml`](../workflows/skills-nv-base.yml)
   defaults to `/opt/nvbase-venv/bin/nv-base`; adjust if your install
   path differs.

To refresh nv-base later, SSH to the runner and re-run the
`pip install --upgrade` line above. No workflow change is needed.

## Tuning the gate

**Step 1 (`nv-base validate`):** to override severity defaults, write a
`--policy <yaml>` overlay file rather than maintaining an env-var
allow-list. Example: keep `author_missing` at HIGH for new skills.

```yaml
# .github/skills-nv-base/policy-strict.yaml
severity_overrides:
  SCHEMA.author_missing: high
```

then in the workflow:

```yaml
env:
  NVBASE_BIN: /opt/nvbase-venv/bin/nv-base
run: |
  /opt/nvbase-venv/bin/nv-base validate skills/ --type skill --external \
    --policy .github/skills-nv-base/policy-strict.yaml ...
```

**Step 2 (`skill_compliance_check.py`):** edit the constants at the top
of the script (`APPROVED_VERBS`, `APPROVED_TEAM_PREFIXES`,
`RESERVED_BARE_NAMES`, etc.) via PR review when the playbook changes.
Pass `--strict` to promote WARN to ERROR.

## Required status check

For exit-1 to actually block merging, add `Skills NV-BASE / skills-check`
as a required status check on `develop` (and `main`, once synced) under
Settings → Branches / Rulesets. Without that, a failing finding shows a
red X but doesn't prevent merge.

## PR comment poster (Step 3)

`post_comment.py` walks the two JSON inputs from Steps 1 and 2, builds a
markdown summary, and posts a sticky comment on the PR. Subsequent runs
update the same comment in place (matched by the hidden HTML marker
`<!-- skills-nv-base-bot:v1 -->`). The comment has two sections (one per
step), each with a count summary and a table of findings (capped at 30
rows per table; the full list remains in the job annotations + log).

Failures inside the poster (network errors, missing token, etc.) surface
as `::warning` annotations and the script exits 0 — the comment is best-
effort and should never block the gate. Requires `pull-requests: write`
in the workflow permissions; the GHA-issued `${{ github.token }}` is
enough, no PAT.

## What's NOT in v1

- **No Tier-2/3 nv-base checks** (`quality`, `inter-skill`, `lint`, dedup, agent-eval). Those need an Anthropic / inference-api credential on the runner and are a separate decision. The existing `skills-eval` workflow handles agent-eval.
- **No gitleaks / bandit / pip-audit** (the playbook's GitLab pipeline runs those separately). Step 1's `secrets` check covers the credential-scan surface.
