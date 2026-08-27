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

## Known limitations — read this before trusting a verdict

Adversarial review found these against the real tree. They are recorded here rather than
quietly fixed, because each one changes how a number should be read.

### The state comparison reports; it does not gate

A ratchet was built here and removed. It was unsound in four independent ways: a cross-job
`steps.ctx` reference that resolved to `""` (and `git show ":path"` with an empty rev reads
the *index* rather than failing, so base counts equalled head counts); a baseline computed
from the pull request's own `approved.csv`; count-netting, where removing one finding and
adding another let a brand-new GPL-3.0 dependency merge green; and an ungated catch-all
bucket for modules in neither `MODULE_MAP` nor `UNSUBMITTED`.

Gating needs, in order: identity-based comparison against the merge base rather than counts,
CODEOWNERS on the baseline (**done** — `approved.csv` and `module_map.py` now route to
`@NVIDIA-AI-Blueprints/VSS_OSRB_Approvers`), a real verdict for unknown modules, and the
`services/ui` submission below.

The **delta** gate in the `osrb-scan` job is unaffected and still blocks: a dependency change
a pull request actually introduces is caught there.

### `services/ui` has no OSRB submission (fixed in reporting, open in substance)

Verified against the source sheet, not assumed. `react`, `next`, `chart.js`, `tailwindcss`,
`@radix-ui/*` and `@datadog/browser-rum` appear **nowhere** in the 3877-row baseline, under
any module. All 16 upstream sheet tabs are accounted for and none is a UI dependency review
(`oss_licenses_v2_1`, the plausible candidate, is VIOS apt packages). The only two
`AGENT_UI_GITHUB` rows are `@img/sharp-freebsd-wasm32` and `@img/sharp-webcontainers-wasm32`,
both added inline in a bug comment.

Three numbers that should agree:

| source | packages |
|---|---|
| `services/ui/package-lock.json` resolves | 2157 |
| `services/ui/LICENSE-3rd-party.txt` lists | 86 |
| OSRB approved baseline holds | 2 |

`approved.csv` therefore carries a `provenance` column (`submission` or `inline-addition`,
derived from the upstream tab and carrying no ticket or name). A module whose approvals are
**all** inline additions is reported once as `MODULE_UNSUBMITTED` rather than as one
`NOT_APPROVED` per package — `AGENT_UI_GITHUB` is the only such module in the baseline. That
took `NOT_APPROVED` from 1717 to 431 and stopped `services/ui` burying every other module.

Filing the submission is a human step. `--submissions DIR` writes a ready-to-attach CSV per
unsubmitted module so it is an attachment rather than a research task; CI publishes them in
the `osrb-compliance` artifact. 14 packs today, largest first: `services/ui` 1354,
`services/vios` (unsubmitted subtrees) 706, `services/sdrc` 96,
`services/configurators/vss-configurator` 67, `services/alert` 29, `.github` 27,
`services/rtvi/rt-cv` 26, `deploy` 19.

Roughly 50 remaining `NOT_APPROVED` rows are extraction artifacts that can never match: URL
hostnames (`install.python-poetry.org`), unexpanded shell variables (`${NGINX_IMAGE}`),
archive filenames (`node-v22.23.2-linux-x64.tar.gz`).

### `USAGE_DRIFT` is 0 because it is near-unreachable, not because usage is clean

3507 of 3904 inventory rows resolve to the single evidence token `declared`, which appears in
no conflict rule. The `static-link` / `dynamic-link` tokens are defined and used in two rules
but the inventory never emits them — so the largest non-blank `usage_method` in the sheet
(`Dynamic Linking`, 1422 rows) has nothing to compare against.

The approved sheet is the other half of the problem: 1519 `usage_method` cells are blank, 698
say "Other", and 3219 `vendored` cells are blank — including every package where this repo
holds hard vendoring evidence (`abseil-cpp`, `boringssl`, `jsoncpp`, `libyuv` under
`services/vios`). Filling `vendored` and `usage_method` in the next OSRB submission is what
switches this check on.

### Matching gaps that produce findings in both directions

- 28 approved rows carry prose where a package identifier belongs — `minio-cpp (client)`,
  `libpaho-mqttpp and libpaho-mqtt`, `Google Guava`. The vendored `minio` C tree in
  `services/vios` is reported `NOT_APPROVED` even though the sheet holds a row for it, and
  that is the single row in the tree where `USAGE_DRIFT` could have engaged.
- Versions containing `~` (78 approved rows, ordinary Ubuntu/Debian versions like
  `14.2.0-4ubuntu2~24.04.1`) are treated as unresolved ranges and bypass the version check —
  those rows are judged on package and licence only.
- Package names fold `-`, `_` and `.` together across every ecosystem, because `approved.csv`
  has no language column. Nothing collides in the current baseline, but an npm `uuid` and a
  Debian `uuid` inside one module would.

### `APPROVED_NOT_PRESENT` is 2325 rows and is informational

Dominated by `rt-vlm` (1129) and `rt-embed` (579). Some are genuinely stale approvals; many
are apt packages a container inherits from a base image that this inventory does not expand.
Do not read it as "2325 approvals to withdraw" until the base-image side is enumerated.

## Files

| file | what it is |
|---|---|
| `approved.csv` | 3877 rows from the consolidated OSRB sheet + 62 recovered from bug comments. `provenance` says which; `evidence` cites the comment. |
| `conditions.csv` | 25 rows OSRB **refused** (2) or approved **conditionally** (23). Not approvals — kept separate so they cannot be read as one. |
| `inventory.csv` | the repo's own dependency state, regenerated deterministically |
| `module_map.py` | OSRB module labels → repo paths, plus path aliases and the submitted-but-no-package-list set |

### Why the bug comments had to be merged in

The consolidated sheet is not the complete approval record. It was built from 14 upstream
sheets, and approvals granted only in a bug comment never reached it. Comment #1 requests
approval for seven packages in the Alerts Microservice and comment #2 grants it — yet
`services/alert` had no row in the sheet at all. Reading all 117 comments recovered 62
approvals, 23 conditions and 2 refusals.

Three states now exist where there used to be two, because collapsing them misleads:

- **never submitted** (`UNSUBMITTED`) — file a new OSRB bug
- **submitted, package list not recovered** (`SUBMITTED_NO_PACKAGE_LIST`) — recover the list;
  do **not** file a new bug
- **submitted under a different path** (`SUBMITTED_PATH_ALIASES`) — nothing to do; the tree
  moved after submission. This alone was 895 false rows.

### The permissive green-gate

Most of this tree is MIT/Apache/BSD/ISC, and reporting those as "unapproved" buries the rows
that carry real risk. A package whose licence matches the repo's **own** permissive list —
`PERMISSIVE_LICENSE_PATTERNS` in `.github/scripts/check_python_licenses.py`, the same list the
`check_python_licenses.sh` pre-commit hook enforces — is reported `PERMISSIVE_AUTOCLEARED` and
kept out of the review comment.

Deliberate limits:

- it **only** downgrades verdicts meaning "no approval row found". It cannot touch
  `VERSION_DRIFT` or `LICENSE_DRIFT`: those are disagreements with an approval that exists,
  and a licence label does not answer them.
- composites (`MIT AND GPL-2.0-or-later`) and `UNKNOWN` are refused. Exact match only.
- `license_denylist.txt` wins — those wheels misrepresent their own metadata, so clearing them
  on that metadata is the failure the denylist exists to stop.
- if the import fails it clears **nothing**, so a broken import makes the report noisier, never
  quieter.

Effect: rows needing human review went from 2878 to 616, with zero permissive rows remaining.
546 of the 616 are `UNKNOWN` licence — a resolution gap, not a risk finding, and now the
largest remaining lever.
