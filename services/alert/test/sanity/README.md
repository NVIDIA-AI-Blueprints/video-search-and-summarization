# Alert Bridge Sanity Check

Minimal, bash-based sanity test framework for Alert Bridge CID pipelines.

## Quick Start

```bash
# HTTP-only mode (most common for CID)
ES_HOST=localhost ./run_sanity.sh

# With SSH access (enables additional checks)
ES_HOST=localhost SSH_HOST=localhost ./run_sanity.sh

# JSON output for CI/CD
ES_HOST=localhost ./run_sanity.sh --json
```

## Checks

| # | Check | Mode | Description |
|---|-------|------|-------------|
| 01 | Elasticsearch Connectivity | HTTP | Verify ES cluster is healthy |
| 02 | Count Parity | HTTP | Compare incidents vs alerts counts |
| 03 | Recent Data Flow | HTTP | Verify recent documents exist |
| 04 | Verification Codes | HTTP | Check VLM responses in documents |
| 05 | Pipeline Lag | HTTP | Verify timestamp consistency |
| 06 | Container Running | SSH | Check alert-bridge container (requires SSH) |
| 07 | Kafka Consumer Lag | SSH | Check Kafka lag (requires SSH) |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ES_HOST` | **Yes** | - | Elasticsearch host |
| `ES_PORT` | No | 9200 | Elasticsearch port |
| `SSH_HOST` | No | - | SSH host (enables SSH checks) |
| `SSH_USER` | No | ubuntu | SSH username |
| `SSH_KEY` | No | - | Path to SSH private key |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed (skips OK) |
| 1 | One or more checks failed |
| 2 | Configuration error |

## For Coding Agents

Execute sanity checks against a remote deployment:

```bash
# Step 1: Set target
export ES_HOST="localhost"
export ES_PORT="9200"

# Step 2: Run checks
cd ~/PROJECT/alert_agent/test/sanity
./run_sanity.sh

# Step 3: Interpret results
# Exit code 0 = success, 1 = failure
```

## Integration with Council

This sanity check can be run via the council `ab-sanity` task. See the council task documentation for details.
