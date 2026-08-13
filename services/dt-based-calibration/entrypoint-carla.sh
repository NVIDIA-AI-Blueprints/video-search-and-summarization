#!/bin/bash

set -e

echo "=========================================="
echo "Starting CARLA Server"
echo "Port: ${CARLA_PORT:-2000}"
echo "=========================================="

# Start CARLA server
exec /workspace/CarlaUE4.sh \
    -carla-rpc-port=${CARLA_PORT:-2000} \
    -carla-server \
    -RenderOffScreen

