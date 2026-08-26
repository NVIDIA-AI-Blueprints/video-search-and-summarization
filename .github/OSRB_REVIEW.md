# OSRB Scan and OSRB Review

Dependency-related pull requests receive two related GitHub checks:

1. **OSRB Scan (dependency inventory)** creates the public package-change
   overview and complete CSV.
2. **OSRB Review** evaluates changes that may require approval and publishes the
   final result.

Both checks run automatically. Do not manually edit the generated PR comment.

> **Transitional on `develop`.** The OSRB Scan still **fails** when the diff is
> non-empty, and that failure is still a real signal — get OSRB approval for the
> touched lockfile/manifest paths from
> `@NVIDIA-AI-Blueprints/VSS_OSRB_Approvers`, as before. OSRB Review runs
> alongside it and posts its verdict, but it does not yet replace the scan as
> the gate. Expect both signals until that changes. On `main`, the scan has
> already been downgraded to a notice and OSRB Review is the gate.

## The three kinds of row

Every row in the CSV and the overview is one of three classes. They are not
interchangeable — two of them block, and the fix is different for each.

| Class | Blocks? | What it means | What you do |
|---|---|---|---|
| Package change (`added` / `removed` / `updated`) | Blocks when it needs OSRB attention | A dependency entered, left, or changed version or license in a manifest or lockfile the scanner reads. | Get OSRB approval for a new dependency, a license change, or a license the scanner could not resolve. Same-license bumps and removals are recorded but need nothing. |
| `UNCOVERED_SOURCE` | **Blocks** | Your PR touches a file that carries third-party software, and the scanner has no parser for it. Nothing in that file was inventoried, so the report is incomplete and nobody can see what is missing. | **Do not ask OSRB to approve this — there is nothing to approve yet.** Teach the scanner: extend `is_dependency_file` in `.github/scripts/osrb_scan.py`, add or extend the parser, and cover it in `.github/scripts/test_osrb_scan.py`. If the file genuinely carries no third-party dependency, exclude it there with a comment saying why. Ask a maintainer if it is not your area. |
| `USED_UNDECLARED` | Never | Source code imports something that no manifest in the owning module declares. | Nothing is required to merge. Usually the package is reaching you transitively, the import name differs from the distribution name, or the code is vendored. Declare it when the gap is real and yours to fix. |

### The use-side pass is report-only

`USED_UNDECLARED` rows come from reading `import` statements, not from reading a
declaration, so they can be wrong. By design they are counted separately, never
added to the OSRB review total, and never fail the check. Treat them as a
to-do list, not a gate. If one is a false positive, say so in the PR — it is a
scanner bug worth fixing, not a merge blocker.

## What developers should do

### The OSRB Scan reports no reviewable changes

No OSRB action is required. Continue with the normal code and CODEOWNERS review.
Same-license version updates and dependency removals remain in the complete CSV
for traceability, but do not require OSRB re-engagement.

### OSRB Review is running

Wait for it to finish. The protected reviewer is checking the public diff
against existing approval evidence. Do not merge while this check is running.

### OSRB Review passes

No further OSRB action is required for the reported dependency changes.
Normal required checks, CODEOWNERS approval, and maintainer review still apply.

### OSRB Review passes with notes

The check is green and you can merge. A note means something worth recording
was found that does not affect approval, most often a package listed in an
attribution file that no code imports, or a changed container base image whose
own OSRB record should be confirmed. Fix the attribution gap in this PR if it
is yours to fix; otherwise raise it with the OSRB owner separately.

### The review says its verdict is provisional

The reviewer ran a pre-release build of itself, which happens during rollout. Do
not treat the verdict as final and do not merge on the strength of it. Tell a
repository maintainer, who will confirm what the check ran against.

### OSRB Review fails or is inconclusive

1. Read the automated review on the PR.
2. Open the linked **OSRB Scan** Actions run.
3. Inspect the short overview. Download `license-diff.csv` only when more detail
   is needed.
4. Address the reported issue:
   - correct or remove an unintended dependency;
   - provide a resolvable public license or component URL;
   - preserve or add the required third-party notice and license text;
   - work with the OSRB owner when a new component, license change, or changed
     use needs approval;
   - ask a repository maintainer to retry an infrastructure failure.
5. Push the correction. If no source change was needed, a maintainer can rerun
   the OSRB Scan workflow.

Do not paste private ticket comments, approval sheets, credentials, or internal
links into the public PR. The protected reviewer reads that evidence privately
and publishes only the decision.

## What triggers review

The overview requests OSRB attention for:

- a new dependency;
- a confirmed license change;
- an old or new license that cannot be resolved.

The complete CSV also retains same-license version updates and removals, but
those rows do not require OSRB re-engagement by themselves.

Container images, Compose files, Helm charts, and build files that add
dependencies without editing a language manifest are in scope as well. A file
of that kind that the scanner cannot yet read shows up as `UNCOVERED_SOURCE`
rather than being skipped in silence — that silence is the failure this scan
exists to prevent.

Changes in how an approved component is used can still require review even when
its package version and license are unchanged. Examples include static instead
of dynamic linking, vendoring source, or moving a build-only dependency into a
distributed runtime.

## Release-target pull requests

Pull requests targeting `dev/*` or `release/*` are marked not applicable because
OSRB review is performed on the earlier pull request into the development
branch.
