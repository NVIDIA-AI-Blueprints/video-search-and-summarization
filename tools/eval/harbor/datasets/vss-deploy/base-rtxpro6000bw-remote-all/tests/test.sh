#!/bin/bash
# Verifier: check that VSS containers are running and endpoints respond.
# Exit 0 = pass, exit 1 = fail.
set -euo pipefail

PASS=0
FAIL=0

check_container() {
    local name=$1
    if docker ps --format '{{.Names}}' | grep -q "$name"; then
        echo "PASS: container $name is running"
        ((PASS++))
    else
        echo "FAIL: container $name not found"
        ((FAIL++))
    fi
}

check_endpoint() {
    local port=$1 path=$2 name=$3
    if curl -sf -o /dev/null --max-time 10 "http://localhost:${port}${path}"; then
        echo "PASS: $name (port $port) responds"
        ((PASS++))
    else
        echo "FAIL: $name (port $port) not responding"
        ((FAIL++))
    fi
}

echo "=== Checking containers ==="
check_container "mdx-vss-agent"
check_container "mdx-vss-ui"
check_container "mdx-elasticsearch"
check_container "mdx-kafka"
check_container "mdx-redis"

echo ""
echo "=== Checking endpoints ==="
check_endpoint 8000 "/docs" "Agent API"
check_endpoint 3000 "/" "Agent UI"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

# Teardown
echo ""
echo "=== Tearing down ==="
REPO=/home/ubuntu/video-search-and-summarization
if [ -f "$REPO/deployments/resolved.yml" ]; then
    cd "$REPO/deployments"
    docker compose -f resolved.yml down --timeout 30 2>/dev/null || true
fi

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
