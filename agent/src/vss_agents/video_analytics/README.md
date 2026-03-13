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
  See the License for the specific language governing permissions and
  limitations under the License.
-->

# Video Analytics Tools

NAT function group providing video analytics tools for querying incident, behavior, and metadata from Elasticsearch.

## Available Tools

- **get_incident**: Get a specific incident by ID
- **get_incidents**: Get incidents from a sensor or place/city
- **get_sensor_ids**: Get list of available sensor IDs (optionally filtered by place)
- **get_places**: Get list of available places
- **get_fov_histogram**: Get FOV histogram with object count statistics over time (people, vehicles, etc.)
- **get_average_speeds**: Get average speed metrics
- **analyze**: Perform analysis on video analytics data

### Source Type Options

When querying incidents with `get_incidents`, you can specify the `source_type` parameter:
- **sensor**: Query by specific sensor ID (exact match)
- **place**: Query by place name using wildcard matching. Works for both city names and intersection names.
  - Example: `source="Dubuque"` with `source_type="place"` matches all incidents in Dubuque
  - Example: `source="HWY_20_AND_LOCUST"` with `source_type="place"` matches that specific intersection

**Note:** The `vlm_verdict` parameter can only be used when `vlm_verified` is set to `true` in the configuration. Attempting to use it when `vlm_verified` is `false` will result in a validation error.

## Quick Start: Serve via MCP

### 1. Set up config file.

Use the `va_mcp_server_config.yml` as a guide. 

Example config file setup:

```yaml
functions:
  vst_sensor_list:
    _type: mcp_tool_wrapper
    url: http://localhost:8001/mcp
    mcp_tool_name: sensor_list

function_groups:
  video_analytics:
    _type: video_analytics
    es_url: "http://localhost:9200"
    index_prefix: "mdx-"
    vlm_verified: false
    embedding_model_name: "sentence-transformers/all-MiniLM-L6-v2"
    vst_sensor_list_tool: vst_sensor_list
    include:
      - get_incident
      - get_incidents
      - get_sensor_ids
      - get_places
      - get_fov_histogram
      - get_average_speeds
      - analyze
```

Note that a dummy workflow is required by NAT.

### 2. Start the MCP Server

Edit the config file variables and then run:

```bash
nat mcp serve --config_file deployments/warehouse/vss-agent/configs/va_mcp_server_config.yml
```

The server will start on `http://localhost:9901/mcp` by default.

### 3. Connect NAT Workflow as Client

You can now invoke these tools using your workflow's standard tool-calling interface. Make sure your NAT workflow is configured to connect to the same server URL where MCP is running.

```yaml
function_groups:
  video_analytics_mcp:
    _type: mcp_client
    server:
      transport: streamable-http
      url: "http://localhost:9901/mcp"

llms:
  nim_llm:
    _type: nim
    model_name: meta/llama-3.1-70b-instruct
    temperature: 0.0
    max_tokens: 1024

workflow:
  _type: react_agent
  tool_names: [video_analytics_mcp]
  llm_name: nim_llm
  verbose: true
  max_retries: 3
```