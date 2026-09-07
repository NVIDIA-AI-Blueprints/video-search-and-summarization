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

This section described a `workflow_run` ordering hazard: `osrb-review.yml`
matched the upstream workflow's **name**, was loaded from the **default
branch**, and so went silently dead in any window where `main` still listened
for `License Diff` while pull requests already produced `OSRB Scan`.

**That hazard is gone: `osrb-review.yml` has been removed.** OSRB Scan does the
review in this repository, and nothing triggers the private GitLab OSRB
pipeline any more. There is no default-branch listener left to keep in step
with a rename, and no window in which a missing review reports nothing.

What replaces it as the gate is the delta check inside `osrb-scan.yml`, which
runs on the pull request itself: it fails when the change introduces a
dependency needing OSRB approval, and when the scanner cannot parse a
dependency-bearing file the change touched.

## 3. Do not rename the artifact, the CSV, or the pipeline variables

These were read by the private GitLab OSRB pipeline. That pipeline is no longer
triggered from here, but the names are kept so any consumer still reading the
artifact keeps working:

| Name | Where |
|---|---|
| artifact `license-diff` | upload step in `osrb-scan.yml` |
| file `license-diff.csv` | `--output` / `--input` of the scan and summary scripts |

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

## 7. The internal OSRB reviewer has been retired from this side

The `triage` job in `osrb-scan.yml` (marker `<!-- osrb-triage -->`, agent in
`.github/osrb/osrb_agent.py`) replaces the comment the private GitLab reviewer posted —
the "Hinton" bot in `ci-vss-oss/ci/osrb_review/review.py`, marker
`<!-- hinton-osrb-review -->`. `osrb-review.yml`, the dispatch that rang that pipeline
and published the **OSRB Review** check, has been removed.

**Read this before assuming the gate moved cleanly.** An earlier revision of this
section argued the dispatch should not be deleted until `ci-vss-oss` retired its half,
because deleting it stops *ringing* the pipeline and removes the OSRB Review check
without anything taking its place — a fail-open rather than a retirement. That ordering
was not followed: this side went first, deliberately. So the state today is:

- Nothing here triggers the private OSRB pipeline. Its trigger in `ci-vss-oss` (the
  `osrb-review-trigger` standing branch / the root-job gate in its `.gitlab-ci.yml`) may
  still exist, but this repository no longer rings it.
- The **OSRB Review** check no longer appears on pull requests. It was verified not to be
  a required status check on `develop` — that ruleset requires DCO Sign-off, Check folder
  structure, Copyright Headers, Security Scan (detect-secrets) and SonarQube — so its
  absence blocks nothing, and reports nothing either.
- The replacement gate is the delta check in `osrb-scan.yml`: it fails when the change
  introduces a dependency needing OSRB approval, and when the scanner cannot parse a
  dependency-bearing file the change touched.

**The open item is that this gate is not yet required.** Until
`OSRB Scan (dependency inventory)` is added to the `Protect develop` ruleset, a pull
request can merge with it red. That is the fail-open the earlier revision warned about,
and adding the required check is what closes it. It cannot be added before this branch
reaches `develop`, because a required context that does not yet exist blocks every pull
request.

Still owed on the private side, and not doable from here:

1. **Parity check against history.** Compare what the private reviewer blocked on recent
   pull requests against what the triage comment reports. Divergences are evidence bugs
   in `approved.csv` / `conditions.csv` and are worth fixing while the private record is
   still consultable.
2. **Remove the trigger in `ci-vss-oss`**, so the private pipeline stops running for a
   doorbell nobody presses.

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
