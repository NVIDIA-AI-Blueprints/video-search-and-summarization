# VSS Orchestrator MCP (`tools.py`)

This module exposes a NAT MCP function group named `vss_orchestrator` for generating Docker Compose artifacts, running deployments, and inspecting runtime state.

### Required environment-specific configuration

The MCP server reads its runtime configuration from:

- `src/vss_agents/orchestrator/vss_orchestrator_mcp_config.yml`

Before starting the server, update these paths in that file for your environment:

- `mdx_data_dir`: base writable data directory used by orchestrator runtime.
- `output_dir`: writable directory where `docker_generate` stores generated artifacts.

If these are left as invalid/non-writable paths, tool execution will fail early during startup or artifact generation.

## Start the server

From `agent/`:

```bash
uv run nat mcp serve --config_file src/vss_agents/orchestrator/vss_orchestrator_mcp_config.yml --port 9902
```

## Key IDs returned by tools

- `docker_generate` returns `docker_compose_id` (used by `docker_read`, `docker_up`, `docker_down`)
- `docker_up` / `docker_down` return `docker_compose_ops_id` (used by `docker_status`)

## Tool summary

- `profiles`: List all supported deployment profiles.
- `prereqs`: Run Docker/GPU prerequisite checks.
- `docker_generate`: Generate resolved Docker Compose YAML and `.env` artifacts.
- `docker_read`: Fetch generated env and resolved compose YAML content by `docker_compose_id`.
- `docker_list`: List Docker container names.
- `docker_logs`: Fetch Docker logs by container name.
- `docker_up`: Start Docker Compose services using previously generated artifacts.
- `docker_status`: Poll status and recent logs for a background `docker_up` or `docker_down` operation.
- `docker_down`: Stop and remove Docker Compose services.

## Available tools and payloads

- `profiles`: list supported deployment profiles.
  - Payload: none (use `{}`).
  - Example response:
    ```json
    {
      "status": "success",
      "profiles": ["alerts", "base", "lvs", "search"]
    }
    ```
- `prereqs`: run prerequisite checks (GPU, Docker, NVIDIA container toolkit, disk).
  - Payload: none (use `{}`).
  - Example response:
    ```json
    {
      "status": "success",
      "message": "Prerequisite checks passed.",
      "details": {
        "gpus": [
          {
            "index": 0,
            "name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "driver_version": "580.126.09",
            "memory_total_mib": 97887,
            "memory_total": "97887 MiB"
          },
          {
            "index": 1,
            "name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "driver_version": "580.126.09",
            "memory_total_mib": 97887,
            "memory_total": "97887 MiB"
          }
        ],
        "gpu_count": 2,
        "driver_version": "580.126.09",
        "docker_version": "Docker version 29.4.1, build 055a478",
        "compose_version": "Docker Compose version v5.1.3",
        "container_toolkit_ok": true,
        "disk_free_gib": 295.3,
        "disk_total_gib": 484.4
      }
    }
    ```
- `docker_generate`: validate profile/env and generate resolved env + compose artifacts.
  - Payload:
    ```json
    {
      "profile": "search",
      "env_overrides": [
        "HARDWARE_PROFILE=H100",
        "HOST_IP=10.0.0.10"
      ],
      "ngc_cli_api_key": null,
      "nvidia_api_key": null
    }
    ```
  - Example response:
    ```json
    {
      "status": "success",
      "docker_compose_id": "search-abc12345",
      "hardware_profile": "H100",
      "host_ip": "10.0.0.10",
      "external_ip": "10.0.0.10",
      "llm_mode": "local",
      "llm_name": "nvidia/nvidia-nemotron-nano-9b-v2",
      "vlm_mode": "local",
      "vlm_name": "nvidia/cosmos-reason2-8b",
      "compose_profiles": "search_local,...",
      "message": "Artifacts generated. Use docker_compose_id with docker_up/docker_down."
    }
    ```
- `docker_read`: fetch generated env/compose artifact contents by `docker_compose_id`.
  - Payload:
    ```json
    {
      "docker_compose_id": "search-abc12345"
    }
    ```
  - Example response:
    ```json
    {
      "status": "success",
      "docker_compose_id": "search-abc12345",
      "profile": "search",
      "env_content": "...",
      "compose_yaml_content": "..."
    }
    ```
- `docker_list`: list Docker container names.
  - Payload:
    ```json
    {
      "all_containers": true
    }
    ```
  - Example response:
    ```json
    {
      "status": "success",
      "container_names": ["vss-agent", "vst", "redis"]
    }
    ```
- `docker_logs`: fetch logs for one container.
  - Payload:
    ```json
    {
      "container_name": "vss-agent",
      "tail": 200
    }
    ```
  - Example response:
    ```json
    {
      "status": "success",
      "container_name": "vss-agent",
      "tail": 200,
      "logs": "..."
    }
    ```
- `docker_up`: start deployment for a generated compose id (background operation).
  - Note: `docker_up` currently runs `docker compose up -d --build --quiet-pull`, so every invocation rebuilds images for services with a `build:` section. This is convenient for local dev iteration, but repeated agent retries can be slower than users expect.
  - Payload:
    ```json
    {
      "docker_compose_id": "search-abc12345"
    }
    ```
  - Example response:
    ```json
    {
      "status": "started",
      "docker_compose_ops_id": "up-search-abc12345",
      "docker_compose_id": "search-abc12345",
      "action": "up",
      "command": "docker compose up -d --build --quiet-pull",
      "poll_tool": "docker_status",
      "status_hint": "Poll docker_status with docker_compose_ops_id for progress/completion.",
      "recommended_poll_interval_s": 5,
      "pid": -1
    }
    ```
- `docker_status`: poll a running `docker_up`/`docker_down` operation.
  - Payload:
    ```json
    {
      "docker_compose_ops_id": "up-search-abc12345",
      "tail_lines": 80
    }
    ```
  - Example response:
    ```json
    {
      "status": "running",
      "docker_compose_ops_id": "up-search-abc12345",
      "docker_compose_id": "search-abc12345",
      "action": "up",
      "pid": 12345,
      "running": true,
      "exit_code": null,
      "command": "docker compose up -d --build --quiet-pull",
      "tail_lines": 80,
      "log_excerpt": "..."
    }
    ```
- `docker_down`: stop / remove deployment for a generated compose id (background operation).
  - Payload:
    ```json
    {
      "docker_compose_id": "search-abc12345"
    }
    ```
  - Example response:
    ```json
    {
      "status": "started",
      "docker_compose_ops_id": "down-search-abc12345",
      "docker_compose_id": "search-abc12345",
      "action": "down",
      "command": "docker compose down -v --remove-orphans",
      "poll_tool": "docker_status",
      "status_hint": "Poll docker_status with docker_compose_ops_id for progress/completion.",
      "recommended_poll_interval_s": 5,
      "pid": -1
    }
    ```


