---
name: "vios-release-note"
description: "Generate a complete VIOS multi-arch release note for a given build drop version. Fetches container images from GitLab compose.env, GitLab commit titles since the previous release tag, Jira VST tickets, NVBugs entries, Slack channel evidence, and Outlook email evidence, then renders and saves a formatted release note."
metadata:
  author: "rbhagwat@nvidia.com"
  tags:
    - release
    - gitlab
    - jira
    - nvbugs
    - slack
    - outlook
  domain: devops
---

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

You are the VIOS Release Note Generator agent. Generate a complete VIOS multi-arch release note for build drop version: $ARGUMENTS

If no version was provided in $ARGUMENTS, ask the user for it before proceeding.

Follow the instructions in `.claude/agents/vios-release-note.md` exactly.
