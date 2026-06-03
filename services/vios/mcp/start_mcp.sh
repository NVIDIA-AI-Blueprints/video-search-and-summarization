#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

cd /home/prakhar/mcp/mcp-gateway
export PYTHONPATH=/home/prakhar/mcp/mcp-gateway/src

# Default behavior - Stdio transport (for Cursor/Claude Desktop integration)


# Parse command line argument
TRANSPORT=${1:-stdio}

case $TRANSPORT in
    "stdio")
        exec /home/prakhar/.local/bin/poetry run python -m mcp_gateway --transport stdio
        ;;
    "http")
        HOST_ARG="$2"
        PORT_ARG="$3"
        if [[ -n "$HOST_ARG" || -n "$PORT_ARG" ]]; then
            HOST=${HOST_ARG:-0.0.0.0}
            PORT=${PORT_ARG:-8000}
            exec /home/prakhar/.local/bin/poetry run python -m mcp_gateway --transport http --host $HOST --port $PORT
        else
            exec /home/prakhar/.local/bin/poetry run python -m mcp_gateway --transport http
        fi
        ;;
    "help"|"-h"|"--help")
        echo "Usage: $0 [stdio|http] [host] [port]"
        echo "- stdio: local stdio transport (default)"
        echo "- http: streamable HTTP transport (endpoint: /mcp, no trailing slash)"
        echo "Examples:"
        echo "  $0                # stdio"
        echo "  $0 http           # http using .env host/port"
        echo "  $0 http 127.0.0.1 8080"
        ;;
    *)
        echo "Error: Unknown transport '$TRANSPORT'"
        echo "Run '$0 help' for usage information"
        exit 1
        ;;
esac 