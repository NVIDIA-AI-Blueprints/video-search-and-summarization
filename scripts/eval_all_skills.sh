#!/usr/bin/env bash
# ==========================================================================
# eval_all_skills.sh — Generate and run Harbor evaluations for all VSS skills
#
# Uses the Brev environment provider to connect to a GPU instance and
# evaluate each skill via Claude Code.
#
# Required env vars (load from .env or export before running):
#   ANTHROPIC_API_KEY       — API key for Claude (or NVIDIA inference proxy)
#   ANTHROPIC_BASE_URL      — API base URL (e.g. https://inference-api.nvidia.com)
#                             Omit for direct Anthropic API.
#   ANTHROPIC_MODEL         — Model to use (e.g. us/aws/anthropic/bedrock-claude-opus-4-6)
#                             Omit for default.
#
# Optional env vars:
#   BREV_INSTANCE           — Name of existing Brev instance (skips creation)
#   BREV_INSTANCE_TYPE      — Instance type for creation (default: l40s-48gb.1x)
#   NGC_CLI_API_KEY         — NGC key for NIM container pulls
#   NVIDIA_API_KEY          — NVIDIA API key for remote LLM/VLM
#
# Usage:
#   ./scripts/eval_all_skills.sh                  # generate + run all
#   ./scripts/eval_all_skills.sh --generate-only  # generate datasets only
#   ./scripts/eval_all_skills.sh --skill deploy   # single skill
#   ./scripts/eval_all_skills.sh --dry-run        # print commands, don't run
#   ./scripts/eval_all_skills.sh --parallel 3     # run 3 skills at a time
#
# Env file:
#   Place a .env file at the repo root or pass --env-file <path>.
#   Format: KEY=VALUE, one per line, # comments allowed.
# ==========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Paths
DEPLOY_GENERATOR="$REPO_ROOT/tools/eval/harbor/adapters/deploy/generate.py"
DATASETS_DIR="$REPO_ROOT/tools/eval/harbor/datasets"
DEPLOY_DATASETS_DIR="$DATASETS_DIR/deploy"
BREV_ENV_MODULE="tools.eval.harbor.envs.brev_env:BrevEnvironment"
SKILLS_DIR="$REPO_ROOT/skills"
RESULTS_DIR="$REPO_ROOT/tools/eval/harbor/results"

# Defaults
GENERATE_ONLY=false
DRY_RUN=false
SKILL_FILTER=""
PARALLEL=1
AGENT="claude-code"
ENV_FILE=""
BREV_INSTANCE_TYPE="${BREV_INSTANCE_TYPE:-l40s-48gb.1x}"
SKIP_INSTANCE_SETUP=false

# Skills with a generator adapter (only 'deploy' for now).
ALL_SKILLS=(deploy)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Generate Harbor evaluation datasets for all VSS skills and run them.

Options:
  --generate-only          Generate task datasets without running harbor
  --skill SKILL            Evaluate a single skill (default: all)
  --task PROFILE/TASK      Run a single deploy task, e.g. base/thor-shared
                           Implies --skill deploy.
  --dry-run                Print harbor commands without executing
  --parallel N             Run N evaluations concurrently (default: 1)
  --agent AGENT            Agent to use (default: claude-code)
  --env-file PATH          Load env vars from file (default: \$REPO_ROOT/.env)
  --instance NAME          Brev instance name (default: \$BREV_INSTANCE or auto-create)
  --instance-type TYPE     Brev instance type for creation (default: $BREV_INSTANCE_TYPE)
  --skip-instance-setup    Skip instance provisioning/configuration
  --results-dir DIR        Output directory for results (default: $RESULTS_DIR)
  --help                   Show this help message

Required env vars:
  ANTHROPIC_API_KEY        API key for Claude Code
  ANTHROPIC_BASE_URL       API base URL (optional, for proxies like NVIDIA inference)
  ANTHROPIC_MODEL          Model override (optional)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --generate-only)       GENERATE_ONLY=true; shift ;;
        --skill)               SKILL_FILTER="$2"; shift 2 ;;
        --dry-run)             DRY_RUN=true; shift ;;
        --parallel)            PARALLEL="$2"; shift 2 ;;
        --agent)               AGENT="$2"; shift 2 ;;
        --env-file)            ENV_FILE="$2"; shift 2 ;;
        --instance)            BREV_INSTANCE="$2"; shift 2 ;;
        --instance-type)       BREV_INSTANCE_TYPE="$2"; shift 2 ;;
        --skip-instance-setup) SKIP_INSTANCE_SETUP=true; shift ;;
        --results-dir)         RESULTS_DIR="$2"; shift 2 ;;
        --help|-h)             usage ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Load env file
# ---------------------------------------------------------------------------

load_env_file() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        return 1
    fi
    echo "  Loading env from: $f"
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip comments and blank lines
        line="${line%%#*}"
        line="$(echo "$line" | xargs)"
        [[ -z "$line" ]] && continue
        # Only export if not already set in environment
        local key="${line%%=*}"
        if [[ -z "${!key:-}" ]]; then
            export "$line"
        fi
    done < "$f"
}

# Try explicit --env-file, then repo root .env
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "ERROR: env file not found: $ENV_FILE" >&2
        exit 1
    fi
    load_env_file "$ENV_FILE"
elif [[ -f "$REPO_ROOT/.env" ]]; then
    load_env_file "$REPO_ROOT/.env"
fi

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

echo "=== VSS Skills Evaluation ==="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found" >&2
    exit 1
fi

# harbor and brev are only needed when actually running evaluations
if ! $GENERATE_ONLY; then
    if ! command -v uvx &>/dev/null && ! command -v harbor &>/dev/null; then
        echo "ERROR: neither 'uvx' nor 'harbor' CLI found" >&2
        exit 1
    fi
    if ! command -v brev &>/dev/null; then
        echo "ERROR: brev CLI not found. Install from https://docs.brev.dev/" >&2
        exit 1
    fi
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "ERROR: ANTHROPIC_API_KEY not set" >&2
        echo "  Set it in your environment, .env file, or pass --env-file" >&2
        exit 1
    fi
fi

# Validate skill filter
if [[ -n "$SKILL_FILTER" ]]; then
    valid=false
    for s in "${ALL_SKILLS[@]}"; do
        [[ "$s" == "$SKILL_FILTER" ]] && valid=true && break
    done
    if ! $valid; then
        echo "ERROR: unknown skill '$SKILL_FILTER'" >&2
        echo "Available skills: ${ALL_SKILLS[*]}" >&2
        exit 1
    fi
fi

# Print config
echo "  Config:"
echo "    ANTHROPIC_API_KEY:  ${ANTHROPIC_API_KEY:+set (${#ANTHROPIC_API_KEY} chars)}"
echo "    ANTHROPIC_BASE_URL: ${ANTHROPIC_BASE_URL:-<default>}"
echo "    ANTHROPIC_MODEL:    ${ANTHROPIC_MODEL:-<default>}"
echo "    BREV_INSTANCE:      ${BREV_INSTANCE:-<auto-create>}"
echo "    BREV_INSTANCE_TYPE: $BREV_INSTANCE_TYPE"
[[ -n "${NGC_CLI_API_KEY:-}" ]] && echo "    NGC_CLI_API_KEY:    set" || echo "    NGC_CLI_API_KEY:    (not set)"
[[ -n "${NVIDIA_API_KEY:-}" ]] && echo "    NVIDIA_API_KEY:     set" || echo "    NVIDIA_API_KEY:     (not set)"
[[ -n "${LLM_REMOTE_URL:-}" ]] && echo "    LLM_REMOTE_URL:     ${LLM_REMOTE_URL} (model: ${LLM_REMOTE_MODEL:-?})" || echo "    LLM_REMOTE_URL:     (not set — remote-* modes disabled for LLM)"
[[ -n "${VLM_REMOTE_URL:-}" ]] && echo "    VLM_REMOTE_URL:     ${VLM_REMOTE_URL} (model: ${VLM_REMOTE_MODEL:-?})" || echo "    VLM_REMOTE_URL:     (not set — remote-* modes disabled for VLM)"
echo ""

# ---------------------------------------------------------------------------
# Step 0: Provision & configure Brev instance
# ---------------------------------------------------------------------------

if ! $GENERATE_ONLY && ! $SKIP_INSTANCE_SETUP; then
    echo "--- Step 0: Brev instance setup ---"
    echo ""

    # Create or reuse instance
    if [[ -z "${BREV_INSTANCE:-}" ]]; then
        BREV_INSTANCE="vss-eval-gpu"
        echo "  Checking for existing instance '$BREV_INSTANCE'..."

        # Check if instance already exists and is running
        INSTANCE_STATUS=$(echo "" | timeout 15 brev ls 2>/dev/null | grep "$BREV_INSTANCE" | awk '{print $2}' || true)

        if [[ "$INSTANCE_STATUS" == "RUNNING" ]]; then
            echo "  Instance '$BREV_INSTANCE' is already RUNNING"
        else
            echo "  Creating instance '$BREV_INSTANCE' (type: $BREV_INSTANCE_TYPE)..."
            if $DRY_RUN; then
                echo "  [dry-run] brev create $BREV_INSTANCE"
            else
                echo "$BREV_INSTANCE_TYPE" | timeout 120 brev create "$BREV_INSTANCE" --detached 2>&1 | tail -3
                echo ""
                echo "  Waiting for instance to be ready..."
                for attempt in $(seq 1 80); do
                    STATUS=$(echo "" | timeout 15 brev ls 2>/dev/null | grep "$BREV_INSTANCE" || true)
                    if echo "$STATUS" | grep -q "RUNNING.*READY"; then
                        echo "  Instance '$BREV_INSTANCE' is READY (${attempt}x15s)"
                        break
                    fi
                    if echo "$STATUS" | grep -q "FAILURE"; then
                        echo "  ERROR: Instance creation failed" >&2
                        echo "$STATUS" >&2
                        exit 1
                    fi
                    sleep 15
                done
            fi
        fi
    else
        echo "  Using existing instance: $BREV_INSTANCE"
    fi

    export BREV_INSTANCE

    # Configure API credentials on the instance
    if ! $DRY_RUN; then
        echo ""
        echo "  Configuring API credentials on '$BREV_INSTANCE'..."

        # Build the env block to write to the instance
        ENV_BLOCK="export ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}'"
        [[ -n "${ANTHROPIC_BASE_URL:-}" ]] && ENV_BLOCK+="\nexport ANTHROPIC_BASE_URL='${ANTHROPIC_BASE_URL}'"
        [[ -n "${ANTHROPIC_MODEL:-}" ]]    && ENV_BLOCK+="\nexport ANTHROPIC_MODEL='${ANTHROPIC_MODEL}'"
        [[ -n "${NGC_CLI_API_KEY:-}" ]]    && ENV_BLOCK+="\nexport NGC_CLI_API_KEY='${NGC_CLI_API_KEY}'"
        [[ -n "${NVIDIA_API_KEY:-}" ]]     && ENV_BLOCK+="\nexport NVIDIA_API_KEY='${NVIDIA_API_KEY}'"
        # Disable beta flags for third-party Anthropic-compatible endpoints
        [[ -n "${ANTHROPIC_BASE_URL:-}" ]] && ENV_BLOCK+="\nexport CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1"
        [[ -n "${ANTHROPIC_BASE_URL:-}" ]] && ENV_BLOCK+="\nexport DISABLE_PROMPT_CACHING=1"

        echo "" | timeout 30 brev exec "$BREV_INSTANCE" "
            echo -e '$ENV_BLOCK' > ~/.eval_env
            grep -q 'source ~/.eval_env' ~/.profile 2>/dev/null || echo 'source ~/.eval_env 2>/dev/null' >> ~/.profile
            echo configured
        " 2>&1 | tail -1

        # Fix ownership of /logs in case previous runs created it as root
        echo "" | timeout 15 brev exec "$BREV_INSTANCE" "
            sudo mkdir -p /logs/agent
            sudo chown -R \$(whoami):\$(id -gn) /logs
        " 2>&1 | tail -1

        # Verify connectivity
        echo "  Verifying instance connectivity..."
        VERIFY=$(echo "" | timeout 30 brev exec "$BREV_INSTANCE" "source ~/.profile && echo ok" 2>&1 | grep -c "ok" || true)
        if [[ "$VERIFY" -lt 1 ]]; then
            echo "  ERROR: Cannot execute commands on '$BREV_INSTANCE'" >&2
            exit 1
        fi
        echo "  Instance '$BREV_INSTANCE' is configured and reachable"
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Step 1: Generate datasets
# ---------------------------------------------------------------------------

echo "--- Step 1a: Generating deploy skill datasets (per profile) ---"
echo ""

DEPLOY_GEN_ARGS=(
    --output-dir "$DEPLOY_DATASETS_DIR"
    --skill-dir "$SKILLS_DIR/deploy"
)

# Plumb remote LLM/VLM endpoints from env into the generator.
# If either pair is set, the generator will emit remote-* tasks for it.
if [[ -n "${LLM_REMOTE_URL:-}" ]] && [[ -n "${LLM_REMOTE_MODEL:-}" ]]; then
    DEPLOY_GEN_ARGS+=(--llm-remote-url "$LLM_REMOTE_URL" --llm-remote-model "$LLM_REMOTE_MODEL")
fi
if [[ -n "${VLM_REMOTE_URL:-}" ]] && [[ -n "${VLM_REMOTE_MODEL:-}" ]]; then
    DEPLOY_GEN_ARGS+=(--vlm-remote-url "$VLM_REMOTE_URL" --vlm-remote-model "$VLM_REMOTE_MODEL")
fi

if [[ -n "$SKILL_FILTER" ]] && [[ "$SKILL_FILTER" == "deploy" ]]; then
    :
elif [[ -n "$SKILL_FILTER" ]] && [[ "$SKILL_FILTER" != "deploy" ]]; then
    echo "  (skipping deploy — filtered to $SKILL_FILTER)"
    DEPLOY_GEN_ARGS=()
fi

if [[ ${#DEPLOY_GEN_ARGS[@]} -gt 0 ]]; then
    if $DRY_RUN; then
        echo "[dry-run] python3 $DEPLOY_GENERATOR ${DEPLOY_GEN_ARGS[*]}"
    else
        python3 "$DEPLOY_GENERATOR" "${DEPLOY_GEN_ARGS[@]}"
    fi
fi

# Non-deploy skills don't have generators yet; they'll live under
# datasets/<skill>/ mirroring the skills/ folder structure.

if $GENERATE_ONLY; then
    echo ""
    echo "Datasets generated under: $DATASETS_DIR"
    echo "Done (generate-only mode)."
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 2: Run harbor evaluations
# ---------------------------------------------------------------------------

echo ""
echo "--- Step 2: Running Harbor evaluations ---"
echo ""

mkdir -p "$RESULTS_DIR"

# Determine harbor command (uvx or direct)
if command -v uvx &>/dev/null; then
    HARBOR_CMD="uvx harbor"
else
    HARBOR_CMD="harbor"
fi

# Build list of eval items: each is "dataset_dir:task_id:label"
# Deploy layout: datasets/deploy/<profile>/<platform>-<mode>/
#   dataset root = datasets/deploy/<profile>
#   task_id      = <platform>-<mode>
EVAL_ITEMS=()

enumerate_deploy_tasks() {
    # Populate EVAL_ITEMS with all deploy tasks found on disk.
    for profile_dir in "$DEPLOY_DATASETS_DIR"/*/; do
        [[ -d "$profile_dir" ]] || continue
        profile=$(basename "$profile_dir")
        for task_dir in "$profile_dir"*/; do
            [[ -f "$task_dir/task.toml" ]] || continue
            task_id=$(basename "$task_dir")
            EVAL_ITEMS+=("$profile_dir:$task_id:deploy/$profile/$task_id")
        done
    done
}

if [[ -n "$SKILL_FILTER" ]]; then
    if [[ "$SKILL_FILTER" == "deploy" ]]; then
        enumerate_deploy_tasks
    else
        echo "  skill '$SKILL_FILTER' has no generator adapter yet — nothing to run"
    fi
else
    enumerate_deploy_tasks
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
PIDS=()
ITEM_LABELS=()

run_harbor_eval() {
    local dataset_dir="$1"
    local task_id="$2"
    local label="$3"
    local result_dir="$RESULTS_DIR/$TIMESTAMP/$label"
    mkdir -p "$result_dir"

    # Build harbor command
    local harbor_args=(
        run
        --environment-import-path "$BREV_ENV_MODULE"
        -p "$dataset_dir"
        -i "$task_id"
        -a "$AGENT"
        -n 1
        -o "$result_dir"
        --timeout-multiplier 6
        --max-retries 2
    )

    # Pass model and API base if configured
    [[ -n "${ANTHROPIC_MODEL:-}" ]]    && harbor_args+=(--model "$ANTHROPIC_MODEL")
    [[ -n "${ANTHROPIC_BASE_URL:-}" ]] && harbor_args+=(--ak "api_base=${ANTHROPIC_BASE_URL}/v1")

    if $DRY_RUN; then
        echo "[dry-run] BREV_INSTANCE=$BREV_INSTANCE $HARBOR_CMD ${harbor_args[*]}"
        return 0
    fi

    echo "[$(date +%H:%M:%S)] Starting evaluation: $label"
    echo "  Results: $result_dir"
    echo ""

    BREV_INSTANCE="$BREV_INSTANCE" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    $HARBOR_CMD "${harbor_args[@]}" 2>&1 | tee "$result_dir/harbor.log"
    local rc=$?

    if [[ $rc -eq 0 ]]; then
        echo "[$(date +%H:%M:%S)] PASS: $label"
    else
        echo "[$(date +%H:%M:%S)] FAIL: $label (exit code $rc)"
    fi

    return $rc
}

# Run evaluations (sequential or parallel)
TOTAL=${#EVAL_ITEMS[@]}
PASSED=0
FAILED=0
FAILED_LABELS=()

if [[ $PARALLEL -le 1 ]]; then
    for item in "${EVAL_ITEMS[@]}"; do
        IFS=: read -r ds_dir task_id label <<< "$item"
        if run_harbor_eval "$ds_dir" "$task_id" "$label"; then
            ((PASSED++))
        else
            ((FAILED++))
            FAILED_LABELS+=("$label")
        fi
        echo ""
    done
else
    for item in "${EVAL_ITEMS[@]}"; do
        IFS=: read -r ds_dir task_id label <<< "$item"

        while [[ ${#PIDS[@]} -ge $PARALLEL ]]; do
            for i in "${!PIDS[@]}"; do
                if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
                    wait "${PIDS[$i]}" && ((PASSED++)) || {
                        ((FAILED++))
                        FAILED_LABELS+=("${ITEM_LABELS[$i]}")
                    }
                    unset 'PIDS[$i]'
                    unset 'ITEM_LABELS[$i]'
                    PIDS=("${PIDS[@]}")
                    ITEM_LABELS=("${ITEM_LABELS[@]}")
                    break
                fi
            done
            sleep 2
        done

        run_harbor_eval "$ds_dir" "$task_id" "$label" &
        PIDS+=($!)
        ITEM_LABELS+=("$label")
    done

    for i in "${!PIDS[@]}"; do
        wait "${PIDS[$i]}" && ((PASSED++)) || {
            ((FAILED++))
            FAILED_LABELS+=("${ITEM_LABELS[$i]}")
        }
    done
fi

# ---------------------------------------------------------------------------
# Step 3: Summary
# ---------------------------------------------------------------------------

echo ""
echo "==========================================="
echo "  Evaluation Summary"
echo "==========================================="
echo ""
echo "  Instance:        ${BREV_INSTANCE}"
echo "  Total evaluated: $TOTAL"
echo "  Passed:          $PASSED"
echo "  Failed:          $FAILED"

if [[ ${#FAILED_LABELS[@]} -gt 0 ]]; then
    echo ""
    echo "  Failed:"
    for s in "${FAILED_LABELS[@]}"; do
        echo "    - $s"
    done
fi

echo ""
echo "  Results directory: $RESULTS_DIR/$TIMESTAMP"
echo "==========================================="

if [[ $FAILED -gt 0 ]]; then
    exit 1
fi
exit 0
