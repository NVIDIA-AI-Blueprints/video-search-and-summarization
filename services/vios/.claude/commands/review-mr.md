---
name: "review-mr"
description: "Fetch all review comments on a GitLab MR, verify each one against the codebase, apply fixes for valid issues, and post a reply to every comment."
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - gitlab
    - code-review
    - automation
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

You are the review comment resolver agent. Read `.claude/sqa/REVIEW_COMMENT_RESOLVER.md` immediately — it contains the complete workflow, verification strategy, fix guidelines, and reply templates.

Arguments passed by the user: $ARGUMENTS
