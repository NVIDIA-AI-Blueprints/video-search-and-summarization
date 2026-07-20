# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""QA drop release-note generation.

Collects PRs merged between two weekly drop tags (dev-YY.MM.N[-h]), extracts
JIRA (VIA-<n>) and NVBugs references, classifies them with an agent,
renders release notes, and cross-posts to JIRA / NVBugs.

Runs downstream on GitLab (ci-vss-oss) where JIRA/NVBugs are reachable;
see docs in the workflow files and `python -m release_notes --help`.
"""
