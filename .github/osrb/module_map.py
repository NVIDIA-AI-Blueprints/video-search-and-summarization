#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OSRB module names <-> repo module paths, and the modules OSRB has never seen.

The approved baseline (``approved.csv``) names its modules the way the OSRB
spreadsheet does: submission-shaped labels like
``RTVI_VLM+RTVI_EMBED (container 3.2 GA)``, which record *what was submitted
for review*, not where the code lives. The scanner names modules the way the
tree does (``services/rtvi/rt-vlm``). Comparing an inventory against the
baseline needs both sides in one vocabulary, and that translation is the only
thing this file does.

Two properties of the mapping are load-bearing:

* It is many-to-many. One submission can cover several repo modules (the RTVI
  VLM and EMBED containers were reviewed together), and one repo module can
  appear in several submissions (rt-vlm has a source-scope submission, a GHCR
  container submission and a PR-delta submission). So a repo module's approved
  set is the UNION of every OSRB submission that maps onto it, and a package
  approved under any one of them is approved for that module.

* ``UNSUBMITTED`` is not the complement of ``MODULE_MAP``. It is an explicit
  list, because "no approved row" has two completely different meanings and
  the report has to tell them apart. A package missing from a *submitted*
  module skipped review -- someone chases the existing OSRB bug. A package in
  a module OSRB never received at all is not rejected, it was never asked
  about -- someone files a new bug. Collapsing the two into one NOT_APPROVED
  bucket sends every reader down the wrong path, and for the unsubmitted
  modules that is most of the report.

``approved.csv`` already carries the translation in its derived
``repo_modules`` column, so the comparator does one lookup per row instead of
a string match against 27 labels. This file stays the source of truth that
generated that column: change the mapping here and regenerate, and
``check_repo_modules_column()`` fails loudly when the two have drifted apart.
"""

from __future__ import annotations

# OSRB module name -> repo module path(s). Pipe-separated OSRB names map to several.
MODULE_MAP = {
    "AGENT":                                  ["services/agent"],
    "AGENT_UI_GITHUB":                        ["services/ui"],
    "LVS":                                    ["services/video-summarization"],
    "RTVI_VLM_GHCR":                          ["services/rtvi/rt-vlm"],
    "RTVI_VLM (source scope 3.2 GA)":         ["services/rtvi/rt-vlm"],
    "RTVI_VLM_GHCR (PR #1730 delta)":         ["services/rtvi/rt-vlm"],
    "RTVI_EMBED_GHCR":                        ["services/rtvi/rt-embed"],
    "RTVI_VLM+RTVI_EMBED (container 3.2 GA)": ["services/rtvi/rt-vlm", "services/rtvi/rt-embed"],
    "RTVI-CV-3D Tracking":                    ["services/rtvi/rt-cv-3d/rt-cv-bev-fusion",
                                               "services/rtvi/rt-cv-3d/rt-cv-config-init",
                                               "services/rtvi/rt-cv-3d/rt-cv-mv3dt"],
    "Video Analytics API":                    ["services/analytics/video-analytics-api"],
    "Behavior Analytics":                     ["services/analytics/behavior-analytics"],
    "Spatial AI Data Utils":                  ["libs/analytics/spatialai-data-utils"],
    "Spatial AI Data Utils (Pytorch dependency)": ["libs/analytics/spatialai-data-utils"],
    "Spatial AI Data Utils | Behavior Analytics": ["libs/analytics/spatialai-data-utils",
                                               "services/analytics/behavior-analytics"],
    "VIOS_VST_V2.1 [dependent package]":      ["services/vios"],
    "VIOS_VST_V2.1 [VST_backend]":            ["services/vios"],
    "VIOS_VST_V2.1 [main package]":           ["services/vios"],
    "Logstash Redis Stream Input Plugin":     ["tools/logstash-plugins"],
    "Logstash Plugins":                       ["tools/logstash-plugins"],
    "DEPLOY":                                 ["deploy"],
    "DT_BASED_CALIBRATION":                   ["services/rtvi/rt-cv-3d/rt-cv-config-init"],
    "Utility Scripts: SDG Postprocessing":    ["tools/sdg-postprocessing"],
    "Utility Scripts: SDG Postprocessing | Behavior Analytics":
        ["tools/sdg-postprocessing", "services/analytics/behavior-analytics"],
    "Utility Scripts: SDG Postprocessing | Spatial AI Data Utils":
        ["tools/sdg-postprocessing", "libs/analytics/spatialai-data-utils"],
    "Utility Scripts: SDG Postprocessing | Spatial AI Data Utils | Behavior Analytics":
        ["tools/sdg-postprocessing", "libs/analytics/spatialai-data-utils",
         "services/analytics/behavior-analytics"],
    "Utility scripts for RTVI-CV-MV3DT":      ["tools/rtvi-cv-mv3dt-utils"],
    "Utility Scripts: Message Broker":        ["tools/message-broker-consumers"],
}

# Repo modules with NO OSRB approval record of any kind. Recorded explicitly so
# "not approved" for these reads as "never submitted", not "rejected".
# NOTE: entries here must NOT also be reachable via SUBMITTED_PATH_ALIASES.
# A module that OSRB reviewed under its old path is submitted, whatever the tree
# calls it now; listing it here would restore the 895-row false report.
UNSUBMITTED = [
    # Verified against all 117 comments of the OSRB bug: no submission of any
    # kind. "File a new OSRB bug" is the correct advice for these and only
    # these. Modules whose submission exists but whose package list could not
    # be recovered live in SUBMITTED_NO_PACKAGE_LIST instead, and modules that
    # moved after submission live in SUBMITTED_PATH_ALIASES -- conflating any
    # of the three sends someone to redo work OSRB already did.
    #
    # services/alert is deliberately absent: comments #1-2 approve seven
    # packages for it, and those rows are now in approved.csv.
    "skills/vss-manage-alerts",
    "skills/vss-build-vision-ai",
    "skills/vss-deploy-profile",
    ".github",
    "docs",
    "docs/smartcity-docs",
    "fern",
    "<root>",
    "deploy/helm/services/monitoring",
    "deploy/docker/services/infra",
]

# Separator inside the derived `repo_modules` column. A comma would collide
# with CSV quoting for no benefit; a semicolon never appears in a repo path.
REPO_MODULE_SEPARATOR = ";"

UNSUBMITTED_SET = frozenset(UNSUBMITTED)

# Every repo module that at least one OSRB submission covers.
SUBMITTED_MODULES = frozenset(path for paths in MODULE_MAP.values() for path in paths)


def repo_modules(osrb_module: str) -> list[str]:
    """Repo module paths covered by one OSRB submission label.

    Unknown labels return ``[]`` rather than raising: a submission added to the
    sheet upstream must not crash the comparator for every other module. The
    caller reports it instead (see ``unmapped_osrb_modules``), so the gap is
    visible rather than fatal.
    """
    return list(MODULE_MAP.get(osrb_module.strip(), []))


def repo_modules_cell(osrb_module: str) -> str:
    """The `repo_modules` cell approved.csv should carry for this label."""
    return REPO_MODULE_SEPARATOR.join(repo_modules(osrb_module))


def split_repo_modules(cell: str) -> list[str]:
    """Read a derived `repo_modules` cell back into paths, dropping blanks."""
    return [part.strip() for part in cell.split(REPO_MODULE_SEPARATOR) if part.strip()]


def is_unsubmitted(module: str) -> bool:
    """True when OSRB holds no record for this repo module at all."""
    return module in UNSUBMITTED_SET


def unmapped_osrb_modules(labels: list[str]) -> list[str]:
    """OSRB labels in the sheet that MODULE_MAP does not translate.

    An unmapped label means approved rows no repo module can ever match, which
    silently inflates NOT_APPROVED. Surfacing the label turns that into a
    one-line fix here.
    """
    return sorted({label for label in labels if label.strip() not in MODULE_MAP})


def check_repo_modules_column(rows: list[dict[str, str]]) -> list[str]:
    """Complaints where approved.csv's derived column disagrees with this map.

    An empty list means the CSV was generated from the mapping as it stands.
    A non-empty one means the two have drifted and the comparator is reading a
    stale translation -- the failure that would quietly move packages into
    another module's approved set.
    """
    complaints = []
    for row in rows:
        label = (row.get("module") or "").strip()
        expected = repo_modules(label)
        actual = split_repo_modules(row.get("repo_modules") or "")
        if expected != actual:
            complaints.append(
                f"{row.get('package', '?')} [{label}]: "
                f"repo_modules={actual} but MODULE_MAP says {expected}"
            )
    return complaints


# Paths OSRB was given, mapped to where the code actually landed.
#
# Three modules reported as "no OSRB record" purely because the repo moved after
# submission. That is 895 rows of false MODULE_UNSUBMITTED - the largest single
# source of noise in the comparison, and the most misleading, because it tells a
# reader to file a new OSRB bug for something OSRB has already reviewed.
#
# Keyed on the path as written in the OSRB submission, because that is the
# string the approval record is anchored to. Verified against the bug comments
# and the commit that moved each one.
SUBMITTED_PATH_ALIASES = {
    # comment #40 submitted PR #1045's requirements.txt under this name; commit
    # 6d04ddf7d landed the identical 10-package skill under skills/benchmarking/.
    "skills/vss-benchmark-lvs": ["skills/benchmarking"],
    # comment #41 gives the planned paths under services/analytics/; both
    # configurators shipped under services/configurators/ instead.
    "services/analytics/vss-configurator": ["services/configurators/vss-configurator"],
    "services/analytics/vss-rt-config-adaptor": [
        "services/configurators/vss-rt-config-adaptor"
    ],
    # comment #85 submits "VIOS UI ... services/vios/ui" as one component; the
    # tree splits it into two packages, and owning_module reports them apart.
    "services/vios/ui": ["services/vios/ui/vios-ui", "services/vios/ui/streaming-lib"],
}


def resolve_submitted_path(path: str) -> list[str]:
    """Repo modules covered by an OSRB submission recorded at `path`."""
    return SUBMITTED_PATH_ALIASES.get(path.rstrip("/"), [])


def aliased_modules() -> set[str]:
    """Every repo module reachable through an alias, for unsubmitted checks."""
    return {m for targets in SUBMITTED_PATH_ALIASES.values() for m in targets}


# Modules OSRB has a record for, where the package list could not be recovered
# in machine-readable form.
#
# This is a THIRD state and collapsing it either way misleads a reader.
# "Never submitted" tells them to file a new OSRB bug for work OSRB already did.
# "Approved" claims an assurance nobody has, because no package list was ever
# reduced to rows. What is true is narrower: a submission exists, and this
# baseline cannot say what it covered.
#
# Each entry cites the bug comment that carries the submission, so the record can
# be found without guessing. Move an entry out of here the moment its package
# list lands in approved.csv.
SUBMITTED_NO_PACKAGE_LIST = {
    "services/sdrc": "comment-58",
    "services/rtvi/rt-cv": "comment-15",
    # comment #103 submits this as Batch 2 and comment #95 attaches a live
    # condition: preserve Google's attribution for the bundled protobuf files in
    # a NOTICE, or do not bundle them. The repo bundles
    # libs/nvschema/protobuf/struct.proto WITH Google's copyright header and
    # ships no NOTICE file, so the condition is currently half met.
    "libs/nvschema": "comment-103 (condition in comment-95: protobuf NOTICE)",
}


def submission_reference(module: str) -> str:
    """The bug comment carrying this module's submission, or ""."""
    return SUBMITTED_NO_PACKAGE_LIST.get(module, "")
