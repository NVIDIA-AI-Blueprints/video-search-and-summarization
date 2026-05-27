---
name: "setup-maas-mcp"
description: "Install MaaS MCP servers into Claude Code settings for GitLab, Outlook, Jira, Confluence, and other NVIDIA internal services"
metadata:
  author: "Prakhar Shukla <prakhars@nvidia.com>"
  tags:
    - mcp
    - maas
    - setup
    - gitlab
    - jira
    - confluence
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

Help the user install MaaS MCP servers. Run the commands below using the Bash tool.

## Available MaaS MCP Servers

| Name | Purpose | Command |
|---|---|---|
| MaaS-GitLab | NVIDIA internal GitLab | `claude mcp add --transport http "MaaS-GitLab" https://maas.prd.astra.nvidia.com/maas/gitlab/mcp` |
| MaaS-Outlook | Email (requires ECI auth) | `claude mcp add --transport http --client-id eci-prd-pub-9ab40d21-b129-4075-8e82-842df4cb5045 "MaaS-Outlook" https://maas.prd.astra.nvidia.com/maas/outlook/mcp` |
| MaaS-P4 | Perforce | `claude mcp add --transport http "MaaS-P4" https://maas.prd.astra.nvidia.com/maas/p4/mcp` |
| MaaS-Jira | Jira | `claude mcp add --transport http "MaaS-Jira" https://maas.prd.astra.nvidia.com/maas/jira/mcp` |
| MaaS-NVBugs | NVBugs | `claude mcp add --transport http "MaaS-NVBugs" https://maas.prd.astra.nvidia.com/maas/nvbugs/mcp` |
| MaaS-Confluence | Confluence | `claude mcp add --transport http "MaaS-Confluence" https://maas.prd.astra.nvidia.com/maas/confluence/mcp` |
| MaaS-GDrive | Google Drive | `claude mcp add --transport http "MaaS-GDrive" https://maas.prd.astra.nvidia.com/maas/gdrive/mcp` |
| MaaS-SharePoint | SharePoint | `claude mcp add --transport http "MaaS-SharePoint" https://maas.prd.astra.nvidia.com/maas/sharepoint/mcp` |
| MaaS-OneDrive | OneDrive | `claude mcp add --transport http "MaaS-OneDrive" https://maas.prd.astra.nvidia.com/maas/onedrive/mcp` |
| MaaS-Colossus-MySQL | Colossus MySQL DB | `claude mcp add --transport http "MaaS-Colossus-MySQL" https://maas.prd.astra.nvidia.com/maas/colossus_mysql/mcp` |
| MaaS-Jama-Cache | Jama Cache | `claude mcp add --transport http "MaaS-Jama-Cache" https://maas.prd.astra.nvidia.com/maas/jama_cache/mcp` |
| MaaS-SonarQube | SonarQube | `claude mcp add --transport http "MaaS-SonarQube" https://maas.prd.astra.nvidia.com/maas/sonarqube/mcp` |

## Steps

1. If the user specified particular servers, install only those. Otherwise install all of them.
2. Run each `claude mcp add` command via Bash.
3. Note: MaaS-Outlook requires ECI authentication — the `--client-id` flag handles this automatically, but the user must be logged in via their NVIDIA SSO browser session.
4. After installing, remind the user to restart Claude Code (or start a new session) for the new MCP servers to take effect.

## Notes

- These are project-scoped by default. To install globally add `--scope user` to each command.
- All servers use HTTP transport — no local process required.
- If a server already exists, `claude mcp add` will error; safe to ignore or remove with `claude mcp remove <name>` first.

No emojis.
