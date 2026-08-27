<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# `.github/osrb` — OSRB compliance tooling

Two pipelines run against every pull request, and they answer different questions.

| | question | blind to |
|---|---|---|
| **Delta** (`osrb_scan.py` → `osrb_summary.py`) | *What does this pull request change?* | anything already wrong in the tree |
| **State** (`osrb_inventory.py` → `osrb_compare.py`) | *Does this repository, as it stands, match what OSRB approved?* | who changed it, or when |

The delta pipeline is what the private OSRB reviewer reads. The state pipeline is the
drift gate: it re-derives the whole inventory from the tree and holds it against the
approved baseline, so a dependency that slipped out of approval three merges ago is a
finding on every pull request until someone clears it.

Both run from `.github/workflows/osrb-scan.yml`, as two independent jobs.

## Files

| File | What it is |
|---|---|
| `osrb_scan.py` | Delta pipeline. Diffs two git refs and emits `license-diff.csv`. Owns `make_row` / `ROW_FIELDS` / `license_risk` — the row shape everything else writes. |
| `osrb_sources.py` | Source-side parsers for files that carry dependencies without a language manifest: Dockerfiles, Compose, Helm charts, CI, build files. |
| `osrb_usage.py` | Use-side pass. Reads `import` statements and reports packages no manifest declares. Advisory — it infers rather than reads, so it never blocks. |
| `osrb_summary.py` | Renders the human overview and the counts the workflow gates on. |
| `osrb_inventory.py` | State pipeline, part 1. Walks the whole tree and writes the full inventory. |
| `osrb_compare.py` | State pipeline, part 2. Joins the inventory to `approved.csv` and assigns each row a verdict. |
| `module_map.py` | Translates OSRB submission labels (`RTVI_VLM+RTVI_EMBED (container 3.2 GA)`) to repo module paths (`services/rtvi/rt-vlm`), and lists the modules OSRB has never received. |
| `osrb_check.py` | Publishes the **OSRB Review** check run from the private pipeline's verdict. |
| `approved.csv` | The OSRB-approved baseline. See below. |
| `inventory.csv` | The committed, generated inventory of what this tree actually contains. |
| `OSRB_REVIEW.md` | Contributor-facing guide to both checks. |
| `MIGRATION.md` | Operator note for the License Diff → OSRB Scan rename. |
| `test_*.py` | `unittest`, standalone-runnable, stdlib only. CI runs each as a plain script. |

Everything here is **stdlib only**. CI runs plain `python` with no `pip install`, so a
new import is a pipeline that fails closed on every pull request. `tomllib` is available;
PyYAML is not, which is why `osrb_sources.py` carries its own narrow YAML reader.

## `approved.csv`

Derived from the OSRB uber-components sheet — the record of what the Open Source Review
Board approved, for which module, in which form.

Three columns from that sheet are **removed** here: `source_sheet`, `source_url` and
`source_sheet_tab`. They carried an NVBug id and the names of the employees who filed and
reviewed each submission. This repository is public and anything committed to it is public
immediately, so the internal provenance stays internal. Nothing in the comparison needs it:
the join is on package, version, licence, module, and usage.

One column is **added**: `repo_modules`, the `module` label translated through
`module_map.py` into the repo paths it covers. It is derived, not authoritative —
`module_map.check_repo_modules_column()` fails when the two have drifted apart.

**This file is a point-in-time copy.** The authoritative OSRB record is the private one.
A green state check means "matches the baseline as of the last refresh", not "approved" —
if a row here disagrees with the OSRB record, the OSRB record wins and this file is what
needs fixing. Do not paste private approval evidence into a public pull request to argue
otherwise; take it to the OSRB owner.

## Refreshing `inventory.csv`

`inventory.csv` is committed, and CI fails when it does not match what the tree produces.
That is deliberate: the comparison reads the committed file, so a stale one would let a
dependency change through by simply not appearing in it.

If the **OSRB Scan (approved-state comparison)** job says the inventory is stale:

```bash
python3 .github/osrb/osrb_inventory.py \
  --ref HEAD \
  --previous .github/osrb/inventory.csv \
  --output .github/osrb/inventory.csv
git add .github/osrb/inventory.csv
```

`--previous` points at the file you are about to overwrite, and it is not
optional in practice. `osrb_inventory.py` makes no network calls, so a licence it
cannot read out of the tree or out of an attribution file is carried forward from
the last inventory. Regenerate without it and every such row comes back `UNKNOWN`,
which is a large, meaningless diff. It cannot hide real drift either: carry-forward
is the last resort, after the parser and after every attribution file, and it is
keyed on `(package, version)` so a bump never inherits the old release's licence.

Commit that alongside the dependency change that caused it. Review the diff before you
commit it — it is the list of what your change actually pulled in, and it is usually
shorter than you expect.

## Verdicts

`osrb_compare.py` gives every inventory row one verdict.

### These fail the check

| Verdict | What it means | What you do |
|---|---|---|
| `NOT_APPROVED` | The module has been through OSRB, but no approved row covers this package at all. | It is a new dependency. Get it approved by `@NVIDIA-AI-Blueprints/VSS_OSRB_Approvers` before merging, or drop it. |
| `VERSION_DRIFT` | Approved, but at a different version than the tree ships. | Pin back to the approved version, or take the new version to OSRB. A bump is a re-review, not a formality — licences change between releases. |
| `LICENSE_DRIFT` | The licence recorded in the tree is not the licence OSRB approved. | Stop. Either the scanner read it wrong (fix the parser, add a test) or the component relicensed, which needs OSRB before it merges. |
| `USAGE_DRIFT` | Package, version and licence all match — the **use** does not. | OSRB approves a component *for a use*. The same library dynamically linked, statically linked, or vendored and modified are three different approvals. Either restore the approved `distribution_method` / `usage_method`, or get the new use approved. |

`USAGE_DRIFT` is the one that surprises people: nothing about the dependency changed, and
the check is still red. That is working as intended. Vendoring an approved MIT library and
patching it is a different legal position from importing it from PyPI, and the baseline
records which one was reviewed.

### These are reported, not enforced

| Verdict | What it means | What you do |
|---|---|---|
| `MODULE_UNSUBMITTED` | The package sits in a module OSRB has never received. It was not rejected — it was never asked about. | Nothing, to merge. This is a gap in the OSRB record that a maintainer has to close by submitting the module. Chasing individual packages here is wasted effort. |
| `APPROVED_NOT_PRESENT` | An approved row that nothing in the tree matches any more. | Nothing, to merge. Usually a dependency that was removed and whose approval was never retired. Worth reporting so the baseline can be trimmed at the next refresh. |

Neither fails the job, on purpose: both describe the state of the OSRB record rather than
anything a pull request did, and a gate that is red for reasons its author cannot clear is
a gate that gets ignored.

## Running the tests

Each test file is standalone and runs the way CI runs it:

```bash
python3 .github/osrb/test_osrb_scan.py
python3 .github/osrb/test_osrb_summary.py
python3 .github/osrb/test_osrb_sources.py
python3 .github/osrb/test_osrb_usage.py
python3 .github/osrb/test_osrb_inventory.py
python3 .github/osrb/test_osrb_compare.py
python3 .github/osrb/test_osrb_dispatch.py
```

`test_osrb_dispatch.py` also enumerates this directory and fails when a `test_*.py` here is
not run by `ci.yml` — a test nobody runs reads as coverage. It reads
`.github/workflows/*.yml` and asserts on them too, including
the artifact and CSV names the private GitLab pipeline fetches. If you rename something in
those workflows and this file goes red, read the assertion before you change it — several
of them exist because the failure they prevent is silent.
