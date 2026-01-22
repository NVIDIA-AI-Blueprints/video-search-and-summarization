######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

# Docker Compose project name - can be overridden to run multiple isolated instances
# Usage: make shell PROJECT_NAME=eval-test1
PROJECT_NAME ?= eval
COMPOSE_CMD = docker compose -p $(PROJECT_NAME)

.PHONY: up down byov logs shell clean status help ps clean-all shell1 shell2 shell3

# Start all dependency services (without byov container)
up:
	@echo "Starting dependency services with project: $(PROJECT_NAME)..."
	$(COMPOSE_CMD) up -d graph-db milvus-standalone arango-db minio
	@echo "Waiting for services to initialize..."
	@sleep 5
	@echo "Services started. Use 'make status PROJECT_NAME=$(PROJECT_NAME)' to check health."

# Stop all services
down:
	@echo "Stopping all services for project: $(PROJECT_NAME)..."
	$(COMPOSE_CMD) down

# Run byov service with default command (pip install)
byov: up
	@echo "Running byov container for project: $(PROJECT_NAME)..."
	$(COMPOSE_CMD) run --rm -it byov

# View logs from all services
logs:
	$(COMPOSE_CMD) logs -f

# Start an interactive shell in byov container (with dependencies)
shell:
	@# Check if a byov container for this project is already running
	@existing_container=$$(docker ps --format '{{.Names}}' | grep "^$(PROJECT_NAME)-byov-run" | head -n 1); \
	if [ -n "$$existing_container" ]; then \
		echo "ℹ️  Found existing byov container for project '$(PROJECT_NAME)': $$existing_container"; \
		echo "ℹ️  Attaching to existing container instead of creating a new one..."; \
		echo ""; \
		docker exec -it $$existing_container bash; \
	else \
		echo "Starting dependency services for project: $(PROJECT_NAME)..."; \
		$(COMPOSE_CMD) up -d graph-db milvus-standalone arango-db minio; \
		echo "Waiting for milvus health check..."; \
		timeout 120 sh -c 'until $(COMPOSE_CMD) ps milvus-standalone | grep -q "healthy"; do echo "Waiting for milvus..."; sleep 3; done' || echo "Milvus health check timeout (continuing anyway)"; \
		echo "Starting new byov shell for project: $(PROJECT_NAME)..."; \
		$(COMPOSE_CMD) run --rm -it --entrypoint bash byov -c "pip install --no-cache-dir -r /opt/nvidia/via/via-engine/eval/byov/requirements_byov.txt && bash"; \
	fi

# Check status of all services
status:
	@echo "=== Services Status for Project: $(PROJECT_NAME) ==="
	@$(COMPOSE_CMD) ps

# List all containers (across all projects)
ps:
	@echo "=== All Docker Containers ==="
	@docker ps -a --filter "label=com.docker.compose.project" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Clean up volumes and containers
clean:
	@echo "Cleaning up all containers and volumes for project: $(PROJECT_NAME)..."
	$(COMPOSE_CMD) down -v
	@echo "Cleanup complete."

# Clean up ALL projects (use with caution!)
clean-all:
	@echo "⚠️  WARNING: This will stop and remove ALL eval-related containers and volumes!"
	@echo "Press Ctrl+C within 5 seconds to cancel..."
	@sleep 5
	@echo "Cleaning up all eval projects..."
	@docker ps -a --filter "label=com.docker.compose.project" --format "{{.Names}}" | grep "^eval" | xargs -r docker stop || true
	@docker ps -a --filter "label=com.docker.compose.project" --format "{{.Names}}" | grep "^eval" | xargs -r docker rm || true
	@docker volume ls --filter "label=com.docker.compose.project" --format "{{.Name}}" | grep "^eval" | xargs -r docker volume rm || true
	@echo "All eval projects cleaned up."

# Convenience targets for running multiple instances
shell1:
	@$(MAKE) shell PROJECT_NAME=eval-instance1

shell2:
	@$(MAKE) shell PROJECT_NAME=eval-instance2

shell3:
	@$(MAKE) shell PROJECT_NAME=eval-instance3

# Show available make targets
help:
	@echo "╔════════════════════════════════════════════════════════════════════╗"
	@echo "║            VSS Evaluation Docker Environment Manager              ║"
	@echo "╚════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "📦 Basic usage (single instance):"
	@echo "  make shell                    - Start interactive shell"
	@echo "  make up                       - Start dependency services only"
	@echo "  make down                     - Stop all services"
	@echo "  make status                   - Check status of services"
	@echo "  make logs                     - View logs from all services"
	@echo "  make clean                    - Stop services and remove volumes"
	@echo ""
	@echo "🔄 Multi-instance usage (run multiple tests in parallel):"
	@echo "  Method 1 - Convenience shortcuts:"
	@echo "    Terminal 1: make shell1      # Uses project: eval-instance1"
	@echo "    Terminal 2: make shell2      # Uses project: eval-instance2"
	@echo "    Terminal 3: make shell3      # Uses project: eval-instance3"
	@echo ""
	@echo "  Method 2 - Custom project names:"
	@echo "    Terminal 1: make shell PROJECT_NAME=eval-qwen"
	@echo "    Terminal 2: make shell PROJECT_NAME=eval-cosmos"
	@echo ""
	@echo "  💡 Note: Running the same command from multiple terminals will attach"
	@echo "           to the existing container (shared session). Use different"
	@echo "           project names for truly parallel evaluations."
	@echo ""
	@echo "🔍 Monitoring:"
	@echo "  make ps                       - List ALL containers (across all projects)"
	@echo "  make status PROJECT_NAME=X    - Check specific project status"
	@echo ""
	@echo "🧹 Cleanup:"
	@echo "  make clean                    - Clean current project ($(PROJECT_NAME))"
	@echo "  make clean PROJECT_NAME=X     - Clean specific project"
	@echo "  make clean-all                - ⚠️  Clean ALL eval projects (with warning)"
	@echo ""
	@echo "ℹ️  Current project: $(PROJECT_NAME)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
