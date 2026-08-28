# OSRB Scan migration — operator note

The "License Diff" workflow is now "OSRB Scan". Everything inside this repository was renamed
in one commit. Two couplings live **outside** it and both fail quietly — no workflow turns red,
no comment is posted, the compliance gate simply stops happening. Those are items 2 and 3.

Item 1 records what is *not* a risk here, because the obvious assumption is wrong and acting on
it wastes an admin's time.

Work this list when the PR merges, not after.

## 1. The check rename is NOT a branch-protection action

Verified against the live repository on 2026-08-26. This repo uses **rulesets**, not legacy
branch protection, so `…/branches/develop/protection` returns `404 Branch not protected` and
tells you nothing. Query the ruleset instead:

```bash
gh api repos/NVIDIA-AI-Blueprints/video-search-and-summarization/rulesets/15323753 \
  --jq '.name, (.rules[] | select(.type=="required_status_checks")
        | .parameters.required_status_checks[].context)'
```

At the time of writing, ruleset 15323753 ("Protect develop") requires:

    DCO Sign-off · Check folder structure · Copyright Headers ·
    Security Scan (detect-secrets) · SonarQube Scan (agent) · (ui) · (skills)

**Neither `License Diff (OSRB CSV)` nor `OSRB Review` is in that list**, and ruleset 15323750
("Protect main") sets no required status checks at all. The OSRB check has always been
advisory: a red License Diff never blocked a merge, and a red OSRB Scan will not either.
There is therefore nothing to rename, and no fail-open window is introduced by this PR.

### What actually enforces OSRB today

The same ruleset sets `require_code_owner_review: true` with
`required_approving_review_count: 1`, `required_review_thread_resolution: true` and
`dismiss_stale_reviews_on_push: true`. So the real gate is `.github/CODEOWNERS` routing
dependency paths to `@NVIDIA-AI-Blueprints/VSS_OSRB_Approvers` — a human approval that
genuinely blocks — and the scan exists to tell that human *what changed*.

That split is worth stating plainly, because it decides where the remaining risk lives:

| | analysed by the scan | blocked by CODEOWNERS |
|---|---|---|
| lockfiles, pyproject, requirements | yes | yes |
| package.json, go.mod, Cargo.toml, Gemfile, setup.py, Dockerfile | **now yes** (this PR) | yes |
| pom.xml, build.gradle*, *.gemspec, conanfile*, vcpkg.json, environment.yml, Chart.yaml, compose, CMakeLists.txt | **now yes** (this PR) | **no** |

The bottom row is the gap this PR does not close. Widening `CODEOWNERS` to cover those paths
is a separate, small, reviewable change and should follow this one. Until then those files are
*reported* but not *blocked*.

## 2. Get the rename onto the default branch

`workflow_run` matches the **name** of the upstream workflow, not its filename,
and GitHub loads `osrb-review.yml` from the **default branch** (`main`) — never
from the pull request. So during any window where `main` still carries
`workflows: ["License Diff"]` while pull requests are already producing a
workflow named `OSRB Scan`:

- OSRB Review never triggers;
- no check run is created at all;
- `workflow_run` jobs are not listed in a pull request, so nothing on the PR
  says the review is missing.

Both files change together in this PR, so a merge to `develop` is safe only in
the sense that the scan keeps working. **The OSRB Review gate is absent — not
passing — until `develop` is promoted to `main`.** Promote promptly, and treat
PRs merged in that window as un-reviewed for OSRB purposes.

## 3. Do not rename the artifact, the CSV, or the pipeline variables

These are read by the private GitLab OSRB pipeline, which cannot be seen or
tested from this repository:

| Name | Where |
|---|---|
| artifact `license-diff` | upload step in `osrb-scan.yml` |
| file `license-diff.csv` | `--output` / `--input` of the scan and summary scripts |
| `GITHUB_LICENSE_RUN_ID`, `GITHUB_LICENSE_RUN_URL` | `DOWNSTREAM_EXTRA_VARIABLES_JSON` in `osrb-review.yml` |

They were deliberately **not** renamed to match the new wording. If a later
tidy-up renames one, nothing here fails — the downstream job downloads nothing,
reviews an empty dependency list, and returns a green OSRB verdict for an
unreviewed change. `test_osrb_dispatch.py` now asserts the artifact and CSV
names, and the `workflow_run` name coupling, so at least the in-repo half of
this is enforced pre-merge.

## 4. Expect stale signals on in-flight pull requests

- A pull request opened before the merge keeps its old `License Diff (OSRB CSV)`
  status on its current head commit. It refreshes on the next push; nothing
  back-fills it.
- The PR comment marker `<!-- license-diff-osrb -->` is unchanged on purpose, so
  existing comments are updated in place rather than duplicated.

## 5. Fix links that point at the old filename

`.github/workflows/license-diff.yml` is now `.github/workflows/osrb-scan.yml`.
Any bookmark, runbook, or private-side job that links
`…/actions/workflows/license-diff.yml` now 404s. The OSRB runbook on the private
side is the one that matters.

## What changed inside the repository

| Before | After |
|---|---|
| workflow `License Diff` | `OSRB Scan` |
| check `License Diff (OSRB CSV)` | `OSRB Scan (dependency inventory)` |
| `.github/scripts/license_diff_csv.py` | `.github/osrb/osrb_scan.py` |
| `.github/scripts/license_diff_summary.py` | `.github/osrb/osrb_summary.py` |
| fails on `review_rows` | fails on `review_rows` **or** `uncovered_rows` |
| OSRB scripts under `.github/scripts` | all OSRB tooling under `.github/osrb` |
| (delta pipeline only) | plus a state pipeline: `osrb_inventory.py` → `osrb_compare.py` vs `approved.csv` |

The directory move is in-repo only and nothing outside reads those paths, but it does
reach three places a grep for `osrb` misses: `osrb_sources.py` imports
`compose_image_golden` from `.github/scripts` (which did **not** move, because the
container-image workflows share it), `test_osrb_dispatch.py` loads the downstream
pipeline helpers from the same place, and `osrb_check.py` publishes a link to
`OSRB_REVIEW.md` by path. All three are covered by tests.

`review_rows` keeps exactly the meaning it had before, so any consumer of it — including the
private OSRB pipeline — sees no change in behaviour. `uncovered_rows` is new and reports a
scanner gap — a dependency-bearing file the scanner cannot parse — which needs a
change to `osrb_scan.is_dependency_file`, not an OSRB approval.

## 6. Follow-up: widen CODEOWNERS

This PR widens the *scanner*. It does not widen the *gate*. Two gaps remain, both closed by
small edits to `.github/CODEOWNERS`, which is what actually blocks a merge
(`require_code_owner_review: true` on the `Protect develop` ruleset):

**Dependency manifests now scanned but still unowned** — `pom.xml`, `build.gradle`,
`build.gradle.kts`, `*.gemspec`, `conanfile.*`, `vcpkg.json`, `environment.yml`,
`Chart.yaml`, `docker-compose*.y*ml`, `compose*.y*ml`, `CMakeLists.txt`. A change to any of
these is now reported to OSRB, but nothing requires an OSRB approver to look.

**Attribution files** — CODEOWNERS routes `LICENSE-3rd-party.txt`, which covers 6 of the 33
attribution files in the tree. The other 27 — `LICENSE.3rdparty`, `3rdParty_Licenses*`,
`THIRD_PARTY_LICENSES.md`, `oss-licenses.txt`, `NOTICE*` — are routed nowhere. Since the scan
treats an added attribution file as advisory rather than blocking (nothing can parse prose, so
there is no action a failure could ask for), CODEOWNERS is the only place this can be caught.

Kept out of this PR deliberately: CODEOWNERS changes who must approve every future PR touching
those paths, which is an ownership decision for the maintainers, not a side effect of a scanner
change.

## 7. Follow-up: retire the internal OSRB reviewer

The `triage` job in `osrb-scan.yml` (comment marker `<!-- osrb-triage -->`, agent in
`.github/osrb/osrb_agent.py`) supersedes the **comment** the private GitLab reviewer posts
on the same pull requests — the "Hinton" bot in `ci-vss-oss/ci/osrb_review/review.py`,
marker `<!-- hinton-osrb-review -->`. It does **not** supersede the gate: the OSRB Review
check that `osrb-review.yml` publishes from the private pipeline's verdict remains the
compliance gate until the private side is retired.

**The private pipeline keeps running until its trigger is removed in ci-vss-oss, and this
repository cannot do that.** The reviewer's code is loaded from that repository's standing
`osrb-review-trigger` branch; the dispatch in `osrb-review.yml` here is only the doorbell.
Deleting `osrb-review.yml` from this repo would stop *ringing* it, but that removes the
OSRB Review check entirely — a fail-open, not a retirement. So the OSRB Review check
remains until ci-vss-oss retires its half, **and that is fine**: the two reviewers coexist
by design. Their comment markers differ, so neither updates, strands, or overwrites the
other's comment; a pull request in the transition window simply carries both, and the
triage comment says explicitly that the OSRB Review check is still the gate.

Retirement steps, in order — 1 and 2 are private-side and cannot be done from here:

1. **Parity period.** Let both run on real pull requests and compare: every package the
   private reviewer blocks should appear in the triage comment's "OSRB review required"
   section (the reverse need not hold — the triage agent reads the state comparison too,
   which the private reviewer never had). Divergences are evidence bugs in `approved.csv`
   / `conditions.csv` and are worth fixing *before* the private record stops being
   consulted.
2. **Remove the trigger in ci-vss-oss** (the `osrb-review-trigger` standing branch / the
   root-job gate in its `.gitlab-ci.yml`). This is the actual retirement, and it happens
   in a repository this one cannot see or test.
3. **In the same window, in this repo:** remove `osrb-review.yml` and `osrb_check.py`.
   Order matters — once step 2 lands, every dispatch from here fails and the check
   completes as `failure` on every pull request, so step 3 must follow promptly. Removing
   them *before* step 2 is the silent fail-open described in § 2.
4. **Clean up the couplings that existed only for the private consumer**, in this order
   and only after step 3: the `license-diff` artifact/CSV names in `osrb-scan.yml` may
   then be renamed (today § 3 forbids it), and the assertions pinning them in
   `test_osrb_dispatch.py` retired alongside. Update the "supersedes" footer wording in
   the triage comment, and the OSRB Review references in `OSRB_REVIEW.md` and
   `README.md`.

Until step 2 happens, treat a red OSRB Review exactly as before — it is the gate; the
triage comment is the public, checkable explanation of what it is looking at.

## Verify after merge

```bash
# the scan runs under its new name
gh run list --repo NVIDIA-AI-Blueprints/video-search-and-summarization \
  --workflow "OSRB Scan" --limit 5

# and an OSRB Review check appears on a fresh pull request
gh pr checks <pr> --repo NVIDIA-AI-Blueprints/video-search-and-summarization
# expect both "OSRB Scan (dependency inventory)" and "OSRB Review"
```

If the second command shows the scan but no OSRB Review, stop: that is item 2
above, and the gate is off.
