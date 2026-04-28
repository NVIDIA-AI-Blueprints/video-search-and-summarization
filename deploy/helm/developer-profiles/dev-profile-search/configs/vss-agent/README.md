<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

-->

# vss-agent config (dev-profile-search)

Search-profile agent files packaged with this chart (`templates/vss-agent-configmap.yaml`):

- **config.yml** — required; passed through **`tpl`** with the chart root as context.
- Any extra key named in **`vss-agent.template.name`** — optional file at **`configs/vss-agent/<name>`** when present.

Video analytics MCP is not bundled in **vss-agent**; deploy the standalone **vss-va-mcp** chart (see **dev-profile-alerts**) and set **`vss-agent.videoAnalysisMcpUrl`** / **`VIDEO_ANALYSIS_MCP_URL`** as needed.
