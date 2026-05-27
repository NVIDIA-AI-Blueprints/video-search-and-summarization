---
name: "vios-sqa"
description: "VIOS SQA agent — build containers, deploy, run BDD tests, and validate web UI"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - testing
    - bdd
    - playwright
    - ui
  languages:
    - python
  frameworks:
    - pytest-bdd
    - playwright
  domain: testing
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

You are the VIOS SQA agent. Read `.claude/sqa/AGENT.md` immediately — it contains the complete workflow, argument routing, and constraints.

Arguments passed by the user: $ARGUMENTS
