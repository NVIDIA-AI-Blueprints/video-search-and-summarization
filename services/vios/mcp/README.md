# MCP Gateway

A Python MCP (Model Context Protocol) server gateway that connects to VST services via REST API calls.

## Overview

The gateway translates MCP requests into REST API calls to connect to VST REST APIs and acts as MCP gateway.

## Installation

### Prerequisites

- Python 3.10 or higher
- Poetry (for dependency management)
- A backend service with REST API endpoints

### Setup

1. **Clone or create the project** (if you haven't already)

2. **Install dependencies:**
   ```bash
   cd mcp-gateway
   poetry install
   ```

3. **Install development dependencies (optional):**
   ```bash
   poetry install --with dev
   ```

## Configuration

The gateway can be configured using environment variables. Create a `.env` file in the project root:

```bash
# Backend API Configuration
MCP_GATEWAY_CPP_API_BASE_URL=http://localhost:8080
MCP_GATEWAY_CPP_API_TIMEOUT=30

# MCP Server Configuration
MCP_GATEWAY_SERVER_NAME=cpp-gateway
MCP_GATEWAY_SERVER_VERSION=1.0.0
MCP_GATEWAY_SERVER_HOST=0.0.0.0
MCP_GATEWAY_SERVER_PORT=8000

# Logging Configuration
MCP_GATEWAY_LOG_LEVEL=INFO
MCP_GATEWAY_ENABLE_JSONRPC_LOGGING=false

# HTTP Security Configuration (for HTTP transport mode)
# Host validation is disabled by default for ease of use
# For production, enable host validation:
# MCP_GATEWAY_DISABLE_HOST_CHECK=false
# MCP_GATEWAY_ALLOWED_HOSTS=10.41.26.58,192.168.1.100
```

### Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_GATEWAY_CPP_API_BASE_URL` | Base URL of your backend API | `http://localhost:8080` |
| `MCP_GATEWAY_CPP_API_TIMEOUT` | Timeout for API calls (seconds) | `30` |
| `MCP_GATEWAY_SERVER_NAME` | Name of the MCP server | `cpp-gateway` |
| `MCP_GATEWAY_SERVER_VERSION` | Version of the MCP server | `1.0.0` |
| `MCP_GATEWAY_SERVER_HOST` | Host address for MCP server (HTTP mode) | `0.0.0.0` |
| `MCP_GATEWAY_SERVER_PORT` | Port for MCP server (HTTP mode) | `8000` |
| `MCP_GATEWAY_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `MCP_GATEWAY_ENABLE_JSONRPC_LOGGING` | Enable detailed JSON RPC message logging | `false` |
| `MCP_GATEWAY_ALLOWED_HOSTS` | Comma-separated list of allowed IP addresses/hostnames for HTTP transport (e.g., `10.41.26.58,192.168.1.100`). Only used when `DISABLE_HOST_CHECK=false`. | `None` |
| `MCP_GATEWAY_DISABLE_HOST_CHECK` | Disable Host header validation. Set to `false` and configure `ALLOWED_HOSTS` for production. | `true` |
| `MCP_GATEWAY_SENSOR_LIST_FORCE_REFRESH` | Add `forceRefresh=true` to sensor list API calls | `true` |
| `MCP_GATEWAY_VIDEO_URL_DISABLE_AUDIO` | Add `disableAudio=true` to video URL API calls | `true` |

## Usage

### Running the Server

Two transports are supported:

- stdio (default) — local clients spawn the server
- http — streamable HTTP at `/mcp`

```bash
# stdio (default)
poetry run mcp-gateway

# http (uses .env host/port or CLI overrides)
poetry run mcp-gateway --transport http
poetry run mcp-gateway --transport http --host 127.0.0.1 --port 8080
```

#### Remote Access
- Set `MCP_GATEWAY_SERVER_HOST` and `MCP_GATEWAY_SERVER_PORT` in `.env`
- Open the port in your firewall/security group
- Clients connect to `http://<host>:<port>/mcp` (note: **no trailing slash**)

> **Important**: When connecting to the HTTP endpoint, use `/mcp` without a trailing slash. Using `/mcp/` will result in a 307 redirect which may cause connection issues with some MCP clients.

### Docker

Build and run with Docker:

```bash
docker build -t mcp-gateway:latest /home/ubuntu/mcp/mcp-gateway
docker run --rm -p 8000:8000 --env-file /home/ubuntu/mcp/mcp-gateway/.env mcp-gateway:latest
```

## Testing

```bash
# stdio
./start_mcp.sh
mcp-inspector stdio -- poetry run mcp-gateway --transport stdio

# http
./start_mcp.sh http
mcp-inspector http://localhost:8000/mcp
```

cURL (optional):
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}'
```

## Development

### Debugging and Logging

#### JSON RPC Message Logging

To debug MCP protocol issues or inspect the JSON RPC messages being sent and received, you can enable detailed JSON RPC logging:

```bash
# Enable JSON RPC logging via environment variable
export MCP_GATEWAY_ENABLE_JSONRPC_LOGGING=true

# Or add to your .env file
MCP_GATEWAY_ENABLE_JSONRPC_LOGGING=true
```

