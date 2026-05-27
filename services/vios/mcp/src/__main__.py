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

"""Main entry point for the MCP Gateway server."""

import argparse
import sys
from .server import run_server, run_http_server
from .config import settings


def main():
    """Main function to start the MCP Gateway server."""
    parser = argparse.ArgumentParser(
        description="MCP Gateway server for connecting to C++ applications via REST API"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol to use (default: stdio)"
    )
    parser.add_argument(
        "--host",
        default=settings.server_host,
        help=f"Host address for HTTP transport (default: {settings.server_host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.server_port,
        help=f"Port for HTTP transport (default: {settings.server_port})"
    )
    
    args = parser.parse_args()
    
    try:
        if args.transport == "http":
            print(f"Starting MCP Gateway for HTTP integration on {args.host}:{args.port}...")
            print(f"MCP endpoint will be available at: http://{args.host}:{args.port}/mcp")
            run_http_server(host=args.host, port=args.port)
        else:
            print("Starting MCP Gateway for Cursor integration...")
            run_server()
            
    except KeyboardInterrupt:
        print("\nShutting down MCP Gateway server...")
    except Exception as e:
        print(f"Error starting server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 