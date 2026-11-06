#!/usr/bin/env bash
#
# run_eval.sh - Deploy blueprint and run evaluation pipeline
#
# Usage: ./run_eval.sh [OPTIONS]
#
# Configuration: Set deployment-related vars in the blueprint profile .env first
# (e.g. deployments/developer-profiles/dev-profile-alerts/.env). In eval/.env set only
# eval-specific vars and exceptions: BLUEPRINT (which profile), NUM_SENSORS, EVAL_*, etc.
# CLI options override blueprint .env when provided.
#
# Options:
#   -h, --help                  Show this help message
#   -e, --env-file FILE         Eval .env file(s); may be repeated (default: .env). Later files override earlier.
#   -p, --profile NAME          Blueprint profile name (default: from BLUEPRINT in eval .env)
#   -t, --eval-type TYPE        Eval type: accuracy or latency (default: from EVAL_TYPE)
#   --deployment-mode MODE      Deployment mode: compose or helm (default: compose)
#   -s, --skip-deploy           Skip blueprint deployment
#   -d, --down                  Tear down services instead of starting
#   -v, --verbose               Enable verbose output
#   --skip-dataset-download     Skip dataset download
#   --e2e-eval                  Run e2e evaluation (keeps vss-behavior-analytics running)
#   --synthetic-alerts-injection Run alerts verification with evaluator-seeded synthetic alerts
#   --synthetic-alerts-per-minute N Global synthetic alert submission rate for latency eval
#   --synthetic-alert-event-duration-ms N Duration of each synthetic event in milliseconds
#   --alert-category CATEGORY    Alert category used by eval and synthetic alerts injection
#   --alert-bridge-incident-category CATEGORY Deprecated alias for --alert-category
#   --alert-bridge-num-workers N Alert Bridge async worker count for latency profile config
#   --hardware-profile TYPE     Override hardware profile (default: from blueprint .env)
#   --host-ip IP                Override host IP (default: from blueprint .env HOST_IP)
#   --external-ip IP            Externally reachable IP (passed to dev-profile.sh --external-ip; e.g. AWS public IP)
#   --llm-url URL               Override LLM base URL when using remote LLM
#   --vlm-url URL               Override VLM base URL when using remote VLM
#   --llm-device-id ID          Override LLM device ID (passed to dev-profile.sh --llm-device-id)
#   --vlm-device-id ID          Override VLM device ID (passed to dev-profile.sh --vlm-device-id)
#   --use-remote-llm            Override: use remote LLM
#   --use-remote-vlm            Override: use remote VLM
#   --alerts-mode MODE          Override for profile=alerts only: verification or real-time (ignored for search/base)
#   --dataset-name NAME         Dataset name to download
#   --dataset-path PATH         Override EVAL_DATASET_PATH (dataset root)
#   --videos-path PATH          Override EVAL_VIDEOS_PATH (directory containing eval videos)
#   --ground-truth-path PATH    Override EVAL_GROUND_TRUTH_PATH (ground-truth JSON)
#   --tenant-id ID              NVDataset tenant ID
#   --venv-dir DIR              Virtual environment directory for dataset download
#   --blueprints-dir DIR        Deployment checkout/VSS repo root for latency profile config
#   --bp-profile-config-file FILE Override latency bp profile config YAML (env: BP_PROFILE_CONFIG_FILE)
#   --num-sensors N             Override NUM_SENSORS and Helm RT-CV sensor count
#   --latency-duration D        Override LATENCY_DURATION for latency eval (ES time notation: 30m, 10m, 1h, 2d; default: from .env or 10m)
#   --latency-source-video FILE Override LATENCY_SOURCE_VIDEO; filename under EVAL_VIDEOS_PATH used for latency replication
#   --skip-latency-config-update (Latency only) Skip runner-local profile config; use when configs were updated manually
#   --no-remove-synthetic-videos  On --down, keep synthetically generated videos (default: remove them)
#   --skip-synthetic-videos     Skip synthetic video generation even if video count < NUM_SENSORS
#   --helm-release NAME         Helm parent release name (helm mode)
#   --helm-namespace NAME       Helm namespace (helm mode)
#   --kubeconfig FILE           Kubeconfig path (helm mode)
#   --helm-parent-charts-dir DIR Directory containing dev-profile-* Helm charts
#   --helm-values-file FILE     Parent chart values file; may be repeated
#   --helm-set KEY=VALUE        Parent chart --set override; may be repeated
#   --helm-test-set KEY=VALUE   Eval test chart --set override; may be repeated
#   --helm-external-host HOST   External ingress host for Helm deployment
#   --helm-storage-class NAME   Storage class for Helm chart PVCs
#   --helm-install-timeout D    Helm parent install timeout
#   --helm-test-timeout-seconds N Helm test timeout in seconds
#   --helm-dataset-root DIR     Local dataset root to upload into Helm test PVC instead of pod download
#   --helm-dataset-pvc-size SIZE Dataset PVC size for --helm-dataset-root (default: run_helm_test.py default)
#   --helm-dataset-mount-path PATH Dataset PVC mount path inside Helm test pod (env: HELM_DATASET_MOUNT_PATH)
#   --helm-dataset-storage-class NAME Dataset PVC storageClassName for --helm-dataset-root
#   --helm-test-image-repository REPO Helm test image repository
#   --helm-test-image-tag TAG   Helm test image tag
#   --helm-test-image-pull-secret NAME Helm test image pull secret
#
# Eval .env: BLUEPRINT, NUM_SENSORS, EVAL_*, NVDATASET_* (and other eval-only vars).
# Deployment params (HARDWARE_PROFILE, HOST_IP, MODE, LLM/VLM, NGC_CLI_API_KEY, etc.) in blueprint .env.
#

set -euo pipefail

# -----------------------------------------------------------------------------
# Constants and Defaults
# -----------------------------------------------------------------------------
readonly SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

resolve_blueprint_deployments_dir() {
    local deployments_dir="${SCRIPT_DIR}/../video-search-and-summarization/deploy/docker"

    if [[ ! -d "${deployments_dir}" ]]; then
        echo "ERROR: Missing VSS deploy directory: ${deployments_dir}" >&2
        echo "Make sure the video-search-and-summarization submodule is checked out." >&2
        return 1
    fi

    realpath "${deployments_dir}"
}

BLUEPRINT_DEPLOYMENTS_DIR="$(resolve_blueprint_deployments_dir)"
readonly BLUEPRINT_DEPLOYMENTS_DIR

# Absolute path to deployments/developer-profiles/dev-profile-<profile>/.env
# Optional first arg overrides $BLUEPRINT (needed before export BLUEPRINT in main()).
default_blueprint_profile_env_file() {
    local profile="${1:-${BLUEPRINT:-alerts}}"
    echo "${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}/.env"
}
# Absolute path to deployments/developer-profiles/dev-profile-<profile>/overrides.env
default_blueprint_profile_overrides_env_file() {
    local profile="${1:-${BLUEPRINT:-alerts}}"
    echo "${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}/overrides.env"
}
# Absolute path to deployments/developer-profiles/dev-profile-<profile>/generated.env
default_blueprint_profile_generated_env_file() {
    local profile="${1:-${BLUEPRINT:-alerts}}"
    echo "${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}/generated.env"
}
# Absolute path to eval/.env.defaults
default_eval_env_file() {
    echo "${SCRIPT_DIR}/.env.defaults"
}


# Eval defaults (eval/.env overrides these). Deployment params (hardware, host IP, mode, LLM/VLM, etc.)
# come from the blueprint profile .env (e.g. developer-profiles/dev-profile-alerts/.env); see usage.
# Per-blueprint dataset mappings are documented in eval/configs/agent-eval-config.yml.
EVAL_ENV_FILE="${EVAL_ENV_FILE:-.env}"
# Default env files loaded before any --env-file args. Uses $BLUEPRINT (resolved later) at load time.
# Blueprint profile .env first, then (compose mode only) profile
# overrides/generated layers, then eval-level defaults; --env-file layers on top.
# EVAL_DEFAULT_ENV_FILES="${EVAL_DEFAULT_ENV_FILES:-__BP_ENV__ .env.defaults}"
BLUEPRINT="${BLUEPRINT:-alerts}"
EVAL_TYPE="${EVAL_TYPE:-accuracy}"
EVAL_DATASET_NAME="${EVAL_DATASET_NAME:-vss-devx-base}"
NVDATASET_TENANTID="${NVDATASET_TENANTID:-0573334707593577}"
NUM_SENSORS="${NUM_SENSORS:-1}"
ALERT_BRIDGE_NUM_WORKERS="${ALERT_BRIDGE_NUM_WORKERS:-30}"
# ES time notation (30m, 1h, 600s, 2d); used for evaluator query window and converted to seconds for trigger delay
LATENCY_DURATION="${LATENCY_DURATION:-10m}"
readonly HELM_LATENCY_TEST_TIMEOUT_BUFFER_SECONDS=300

VENV_DIR="${SCRIPT_DIR}/.venv"
PROFILE_CONFIG_RUNNER_PYTHON=""

# Verbose logging flag — set to "true" by --verbose; read by log_debug()
VERBOSE="${VERBOSE:-false}"

# Synthetic video generation (duplicate videos to reach NUM_SENSORS)
readonly SYNTHETIC_VIDEO_EXTENSIONS="mp4 mkv webm avi mov"
readonly SYNTHETIC_PREFIX="synthetic_"
readonly SYNTHETIC_MANIFEST="${SCRIPT_DIR}/.synthetic_videos_manifest"
readonly LATENCY_SOURCE_VIDEO_STAGE_MANIFEST="${SCRIPT_DIR}/.latency_source_video_stage_manifest"
LATENCY_STAGED_VIDEOS_DIR=""
HELM_STAGED_DATASET_ROOT=""
HELM_DATASET_UPLOAD_ROOT=""
readonly DEFAULT_HELM_ALERTS_VIDEOS_SUBPATH="vss-devx-alert/videos"
readonly DEFAULT_HELM_ALERTS_GROUND_TRUTH_SUBPATH="vss-devx-alert/events.json"

# -----------------------------------------------------------------------------
# Color & Style Output
# -----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    BOLD=''
    DIM=''
    NC=''
fi

# -----------------------------------------------------------------------------
# Logging Functions
# -----------------------------------------------------------------------------

# HH:MM:SS prefix used by all log lines
_ts() { date +"%H:%M:%S"; }

# Verbose-only debug line (shown only when VERBOSE=true / --verbose)
log_debug() {
    [[ "${VERBOSE:-false}" != "true" ]] && return 0
    echo -e "${DIM}$(_ts) · [debug] $*${NC}"
}

log_info() {
    echo -e "${BLUE}$(_ts)${NC}  ${CYAN}→${NC}  $*"
}

log_success() {
    echo -e "${GREEN}$(_ts)${NC}  ${BOLD}${GREEN}✓${NC}  $*"
}

log_warn() {
    echo -e "${YELLOW}$(_ts)${NC}  ${BOLD}${YELLOW}⚠${NC}  $*" >&2
}

log_error() {
    echo -e "${RED}$(_ts)${NC}  ${BOLD}${RED}✗${NC}  $*" >&2
}

# Bold section header that visually separates major phases
log_section() {
    local title
    title="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
    local sep='──────────────────────────────────────────────────────────────'
    printf "\n${BOLD}${CYAN}  %s${NC}\n" "${sep}"
    printf "${BOLD}${CYAN}  ▶  %s${NC}\n" "${title}"
    printf "${BOLD}${CYAN}  %s${NC}\n\n" "${sep}"
}

# Indented step label within a section
log_step() {
    echo -e "  ${CYAN}·${NC}  $*"
}

# Aligned key → value pair — used for config / summary blocks
log_kv() {
    local key="$1"
    local val="${2:-—}"
    printf "  ${DIM}%-38s${NC}${BOLD}%s${NC}\n" "${key}" "${val}"
}


duration_to_seconds() {
    local value="${1:-}"
    value="${value,,}"

    if [[ ! "${value}" =~ ^([0-9]+)([smhd]?)$ ]]; then
        log_error "Invalid duration ${1:-<empty>}. Use seconds or <N>s, <N>m, <N>h, <N>d."
        return 1
    fi

    local amount="${BASH_REMATCH[1]}"
    local unit="${BASH_REMATCH[2]:-s}"

    case "${unit}" in
        s|"") echo "${amount}" ;;
        m) echo "$((amount * 60))" ;;
        h) echo "$((amount * 3600))" ;;
        d) echo "$((amount * 86400))" ;;
        *)
            log_error "Unsupported duration unit ${unit} in ${1}."
            return 1
            ;;
    esac
}


resolve_helm_test_timeout_seconds() {
    local explicit_timeout="${1:-}"
    local eval_type="${2:-${EVAL_TYPE:-accuracy}}"
    local latency_duration="${3:-${LATENCY_DURATION:-10m}}"
    local synthetic_injection="${4:-false}"

    if [[ "${eval_type}" != "latency" ]]; then
        echo "${explicit_timeout}"
        return 0
    fi

    local latency_seconds
    if ! latency_seconds="$(duration_to_seconds "${latency_duration}")"; then
        return 1
    fi
    local drain_seconds=0
    if [[ "${synthetic_injection}" == true ]]; then
        local configured_drain="${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS:-${PROMETHEUS_SUMMARY_BUFFER_SECONDS:-90}}"
        if [[ ! "${configured_drain}" =~ ^[0-9]+$ ]]; then
            log_error "--post-injection-drain-seconds must be a nonnegative integer"
            return 1
        fi
        drain_seconds=$((10#${configured_drain}))
    fi
    local minimum_timeout_seconds=$((latency_seconds + drain_seconds))


    if [[ -n "${explicit_timeout}" ]]; then
        if [[ ! "${explicit_timeout}" =~ ^[0-9]+$ ]]; then
            log_error "--helm-test-timeout-seconds must be a positive integer"
            return 1
        fi
        local explicit_timeout_seconds=$((10#${explicit_timeout}))
        if (( explicit_timeout_seconds <= 0 )); then
            log_error "--helm-test-timeout-seconds must be a positive integer"
            return 1
        fi
        if (( explicit_timeout_seconds <= minimum_timeout_seconds )); then
            log_error "--helm-test-timeout-seconds must exceed the latency duration plus post-injection drain for Helm latency eval"
            log_error "Required more than ${minimum_timeout_seconds}s (duration=${latency_seconds}s, drain=${drain_seconds}s); got ${explicit_timeout_seconds}s"
            return 1
        fi
        echo "${explicit_timeout_seconds}"
        return 0
    fi

    local derived_timeout_seconds=$((latency_seconds + HELM_LATENCY_TEST_TIMEOUT_BUFFER_SECONDS))
    if [[ "${synthetic_injection}" == true ]]; then
        local vst_ready_timeout="${VST_SENSOR_READY_TIMEOUT_SECONDS:-900}"
        if [[ ! "${vst_ready_timeout}" =~ ^[0-9]+$ ]]; then
            log_error "VST_SENSOR_READY_TIMEOUT_SECONDS must be a positive integer for Helm synthetic latency eval"
            return 1
        fi
        local vst_ready_timeout_seconds=$((10#${vst_ready_timeout}))
        if (( vst_ready_timeout_seconds <= 0 )); then
            log_error "VST_SENSOR_READY_TIMEOUT_SECONDS must be a positive integer for Helm synthetic latency eval"
            return 1
        fi
        derived_timeout_seconds=$((latency_seconds + drain_seconds + vst_ready_timeout_seconds + HELM_LATENCY_TEST_TIMEOUT_BUFFER_SECONDS))
    fi

    echo "${derived_timeout_seconds}"
}

# Startup banner shown once at the top of every run
log_banner() {
    echo ""
    echo -e "${BOLD}${CYAN}  ┌─────────────────────────────────────────────────────────┐${NC}"
    printf  "${BOLD}${CYAN}  │  %-55s│${NC}\n" "VSS Blueprint Evaluator"
    printf  "${BOLD}${CYAN}  │  %-55s│${NC}\n" "$(basename "$0")  ·  $(date +'%Y-%m-%d')"
    echo -e "${BOLD}${CYAN}  └─────────────────────────────────────────────────────────┘${NC}"
    echo ""
}

# Phase timer — call _phase_start before a block, _phase_elapsed after it
_PHASE_START=0
_phase_start() { _PHASE_START="${SECONDS}"; }
_phase_elapsed() {
    local elapsed=$(( SECONDS - _PHASE_START ))
    local m=$(( elapsed / 60 )) s=$(( elapsed % 60 ))
    [[ ${m} -gt 0 ]] && printf "%dm %02ds" "${m}" "${s}" || printf "%ds" "${s}"
}

# Spinner — braille-dot animation for quiet waits; only runs on interactive TTYs
_SPINNER_PID=""
spinner_start() {
    [[ ! -t 1 ]] && return 0
    local msg="$1"
    (
        local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
        while true; do
            printf "\r  ${CYAN}%s${NC}  %s" "${frames:$i:1}" "${msg}"
            i=$(( (i + 1) % ${#frames} ))
            sleep 0.08
        done
    ) &
    _SPINNER_PID=$!
    disown "${_SPINNER_PID}" 2>/dev/null || true
}

spinner_stop() {
    [[ -z "${_SPINNER_PID:-}" ]] && return 0
    kill "${_SPINNER_PID}" 2>/dev/null || true
    wait "${_SPINNER_PID}" 2>/dev/null || true
    _SPINNER_PID=""
    printf "\r\033[2K"   # erase spinner line
}

# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Deploy blueprint and run evaluation pipeline.

Options:
    -h, --help                  Show this help message and exit
    -e, --env-file FILE         Eval .env file(s); may be repeated (default: ${EVAL_ENV_FILE}). Later override earlier.
    -p, --profile NAME          Blueprint profile name (default: ${BLUEPRINT})
    -t, --eval-type TYPE        Eval type: accuracy or latency (default: ${EVAL_TYPE})
    --deployment-mode MODE      Deployment mode: compose or helm (default: compose)
    -s, --skip-deploy           Skip blueprint deployment
    -d, --down                  Tear down services instead of starting
    -v, --verbose               Enable verbose output
    --skip-dataset-download     Skip dataset download
    --video-upload-only         Start only bp-configurator (video upload to VST/nvstreamer) without triggering eval-orchestrator
    --skip-nat-eval             (deprecated: use --video-upload-only) Skip NAT eval REST API polling only
    --e2e-eval                  Run end-to-end evaluation (keeps vss-behavior-analytics running)
    --synthetic-alerts-injection
                                Alerts verification only: use evaluator-seeded synthetic alerts and disable behavior analytics
    --synthetic-alerts-per-minute N
                                Synthetic latency only: global alert submission rate (default: NUM_SENSORS alerts/min)
    --synthetic-alert-event-duration-ms N
                                Synthetic latency only: event duration in milliseconds (default: evaluator media window)
    --synthetic-alert-submission-mode MODE
                                Synthetic latency only: message_bus (default) or ondemand_api
    --alert-category CATEGORY    Alert category used by eval and synthetic alerts injection
    --alert-bridge-incident-category CATEGORY
                                Deprecated alias for --alert-category
    --post-injection-drain-seconds N
                                Synthetic latency only: wait after injection before reporting (default: 90)
    --alert-bridge-num-workers N
                                Alert Bridge async worker count for latency profile config (env: ALERT_BRIDGE_NUM_WORKERS; default: 30)
    --hardware-profile TYPE     Override hardware profile (default: from blueprint profile .env)
    --host-ip IP                Override host IP (default: from blueprint profile .env HOST_IP)
    --external-ip IP            Externally reachable IP for UIs/APIs (passed to dev-profile.sh --external-ip)
    --llm-url URL               Override LLM base URL for remote LLM
    --vlm-url URL               Override VLM base URL for remote VLM
    --llm-device-id ID          Override LLM device ID (passed to dev-profile.sh --llm-device-id)
    --vlm-device-id ID          Override VLM device ID (passed to dev-profile.sh --vlm-device-id)
    --use-remote-llm            Override: use remote LLM (default: from blueprint .env LLM_MODE)
    --use-remote-vlm            Override: use remote VLM (default: from blueprint .env VLM_MODE)
    --alerts-mode MODE          Override for profile=alerts only: verification or real-time (ignored for search/base)
    --dataset-name NAME        Dataset name to download (default: ${EVAL_DATASET_NAME})
    --dataset-path PATH        Override EVAL_DATASET_PATH (dataset root; overrides eval .env)
    --videos-path PATH         Override EVAL_VIDEOS_PATH (directory containing eval videos; overrides eval .env)
    --ground-truth-path PATH   Override EVAL_GROUND_TRUTH_PATH (ground-truth JSON; overrides eval .env)
    --tenant-id ID              NVDataset tenant ID (default: ${NVDATASET_TENANTID})
    --venv-dir DIR              Virtual environment dir for dataset download (default: <script_dir>/.venv)
    --blueprints-dir DIR        Deployment checkout/VSS repo root for latency profile config
    --bp-profile-config-file FILE Override latency bp profile config YAML (env: BP_PROFILE_CONFIG_FILE; default: eval/configs/bp_config_latency*.yaml)
    --num-sensors N             Override NUM_SENSORS for synthetic video count and Helm RT-CV deployment (positive integer, default: 1)
    --latency-duration D        Override LATENCY_DURATION for latency eval (ES notation: 30m, 10m, 1h, 2d; default: from .env or 10m)
    --latency-source-video FILE Override LATENCY_SOURCE_VIDEO; filename under EVAL_VIDEOS_PATH used for latency replication
    --skip-latency-config-update  (Latency only) Skip runner-local profile config; use when latency config was applied manually
    --no-remove-synthetic-videos  On --down, do not delete synthetically generated videos (default: remove them)
    --skip-synthetic-videos     Skip synthetic video generation even if video count < NUM_SENSORS

Helm mode options:
    --helm-release NAME         Parent Helm release (default: HELM_PARENT_RELEASE/HELM_RELEASE/vss-<profile>)
    --helm-namespace NAME       Kubernetes namespace (default: HELM_NAMESPACE/NAMESPACE/release)
    --kubeconfig FILE           Kubeconfig path (default: KUBECONFIG/helm default)
    --helm-parent-charts-dir DIR Directory containing dev-profile-* parent charts
    --helm-values-file FILE     Parent chart values file; may be repeated
    --helm-set KEY=VALUE        Parent chart --set override; may be repeated
    --helm-test-set KEY=VALUE   Eval test chart --set override; may be repeated
    --helm-external-host HOST   External ingress host (default: HELM_EXTERNAL_HOST or vss-<profile>.<ip>.nip.io)
    --helm-storage-class NAME   Set global.storageClass for parent chart PVCs
    --helm-install-timeout D    Parent install timeout passed to deploy_helm.py
    --helm-test-timeout-seconds N Helm test timeout passed to run_helm_test.py
    --helm-dataset-root DIR     Local eval dataset root to upload into the test PVC instead of pod-native download
    --helm-dataset-pvc-size SIZE Dataset PVC size for --helm-dataset-root (env: HELM_DATASET_PVC_SIZE)
    --helm-dataset-mount-path PATH Dataset PVC mount path inside Helm test pod (env: HELM_DATASET_MOUNT_PATH)
    --helm-dataset-storage-class NAME Dataset PVC storageClassName for --helm-dataset-root
    --helm-test-image-repository REPO Test pod image repository override
    --helm-test-image-tag TAG   Test pod image tag override
    --helm-test-image-pull-secret NAME Test pod imagePullSecret override

Eval .env (set only eval-specific and exceptions): BLUEPRINT, NUM_SENSORS, EVAL_TYPE, LATENCY_DURATION,
    EVAL_DATASET_PATH, EVAL_DATASET_NAME, EVAL_VIDEOS_PATH, EVAL_GROUND_TRUTH_PATH,
    LATENCY_SOURCE_VIDEO, ALERT_BRIDGE_NUM_WORKERS, NVDATASET_TENANTID, NGC_API_KEY,
    HELM_DATASET_MOUNT_PATH, REMOVE_SYNTHETIC_VIDEOS_ON_DOWN. Deployment params (HARDWARE_PROFILE, HOST_IP, MODE,
    LLM_MODE, VLM_MODE, LLM_BASE_URL, VLM_BASE_URL, NGC_CLI_API_KEY, etc.) in blueprint profile .env.
    CLI options override blueprint .env when provided.

Examples:
    # Run full evaluation pipeline with default profile (alerts)
    ${SCRIPT_NAME}

    # Run with a specific profile (alerts, search, or base)
    ${SCRIPT_NAME} --profile alerts
    ${SCRIPT_NAME} --profile search
    ${SCRIPT_NAME} --profile base

    # Skip deployment, only run evaluation
    ${SCRIPT_NAME} --skip-deploy

    # Skip dataset download (useful if dataset already exists)
    ${SCRIPT_NAME} --skip-dataset-download

    # Tear down all services for a specific profile
    ${SCRIPT_NAME} --profile alerts --down

    # Use custom env file(s); later files override earlier
    ${SCRIPT_NAME} --env-file .env --env-file ./secrets.env --profile alerts

    # Alerts with verification mode and custom hardware
    ${SCRIPT_NAME} --profile alerts --alerts-mode verification --hardware-profile L40S

    # Remote LLM and VLM with custom URLs
    ${SCRIPT_NAME} --use-remote-llm --use-remote-vlm --llm-url http://host:30081 --vlm-url http://host:30082

    # Run latency evaluation (default is accuracy)
    ${SCRIPT_NAME} --eval-type latency

    # Latency eval with custom time window (ES notation: 5 min window)
    ${SCRIPT_NAME} --eval-type latency --latency-duration 5m

    # Tear down but keep synthetically generated videos
    ${SCRIPT_NAME} --profile alerts --down --no-remove-synthetic-videos

EOF
}

# -----------------------------------------------------------------------------
# Cleanup Handler
# -----------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    spinner_stop   # ensure spinner is cleared before any final output
    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Script failed with exit code: ${exit_code}"
    fi
    exit ${exit_code}
}

trap cleanup EXIT

# -----------------------------------------------------------------------------
# Validation Functions
# -----------------------------------------------------------------------------
check_prerequisites() {
    local deployment_mode="${1:-compose}"
    local missing_deps=()

    case "${deployment_mode}" in
        compose)
            if ! command -v docker &>/dev/null; then
                missing_deps+=("docker")
            fi
            ;;
        helm)
            for dep in python3 helm kubectl; do
                if ! command -v "${dep}" &>/dev/null; then
                    missing_deps+=("${dep}")
                fi
            done
            ;;
        *)
            log_error "Invalid deployment mode: ${deployment_mode}. Must be: compose or helm"
            exit 1
            ;;
    esac

    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        exit 1
    fi
}

validate_env_file() {
    local env_file="$1"
    if [[ ! -f "${env_file}" ]]; then
        log_error "Environment file not found: ${env_file}"
        exit 1
    fi
}

# Read VAR=value from a file (strips optional quotes). Used to read from blueprint profile .env.
get_env_value() {
    local env_file="$1"
    local var_name="$2"
    if [[ -f "${env_file}" ]]; then
        local val
        val="$(grep "^${var_name}=" "${env_file}" 2>/dev/null | cut -d'=' -f2- | head -1)"
        val="${val#[\'\"]}"
        val="${val%[\'\"]}"
        echo "${val}"
    fi
}


resolve_run_eval_env_path() {
    local env_file="$1"
    if [[ -f "${SCRIPT_DIR}/${env_file}" ]]; then
        echo "${SCRIPT_DIR}/${env_file}"
    elif [[ -f "${env_file}" ]]; then
        echo "$(cd "$(dirname "${env_file}")" && pwd)/$(basename "${env_file}")"
    fi
}

extract_env_file_keys() {
    local env_file="$1"
    [[ -f "${env_file}" ]] || return 0
    sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "${env_file}"
}

write_env_assignment() {
    local key="$1"
    local value="$2"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    printf '%s="%s"\n' "${key}" "${value}"
}


add_env_key_once() {
    local key="$1"
    local -n keys_ref="$2"
    local -n seen_ref="$3"
    if [[ -z "${seen_ref[${key}]:-}" ]]; then
        seen_ref[${key}]=1
        keys_ref+=("${key}")
    fi
}

is_placeholder_deploy_path() {
    local value="${1:-}"
    [[ -z "${value}" || "${value}" == /path/to/* || "${value}" == *"/path/to/"* || "${value}" == \<* || "${value}" == *"PATH_TO_"* ]]
}

normalize_alerts_dev_profile_paths() {
    local blueprint_env_file="$1"
    [[ "${blueprint_env_file}" == */dev-profile-alerts/.env ]] || return 0

    local vss_apps_dir="${VSS_APPS_DIR:-}"
    if is_placeholder_deploy_path "${vss_apps_dir}"; then
        vss_apps_dir="${BLUEPRINT_DEPLOYMENTS_DIR}"
    fi
    export VSS_APPS_DIR="${vss_apps_dir}"

    local alerts_mode_lc="${ALERTS_MODE:-verification}"
    alerts_mode_lc="${alerts_mode_lc,,}"
    local sdrc_mode="2d_cv"
    [[ "${alerts_mode_lc}" == "real-time" ]] && sdrc_mode="2d_vlm"

    local config_prefix="${VLM_AS_VERIFIER_CONFIG_FILE_PREFIX:-}"
    local default_vlm_config="${vss_apps_dir}/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/${config_prefix}config.yml"
    local default_alert_type_config="${vss_apps_dir}/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json"
    local default_realtime_config="${vss_apps_dir}/industry-profiles/warehouse-operations/vlm-as-verifier/realtime-config.yml"
    local default_sdrc_config_path="${vss_apps_dir}/developer-profiles/dev-profile-alerts/sdrc/${sdrc_mode}"

    if is_placeholder_deploy_path "${VLM_AS_VERIFIER_CONFIG_FILE:-}"; then
        export VLM_AS_VERIFIER_CONFIG_FILE="${default_vlm_config}"
    fi
    if is_placeholder_deploy_path "${VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE:-}"; then
        export VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE="${default_alert_type_config}"
    fi
    if is_placeholder_deploy_path "${VLM_AS_VERIFIER_CONFIG_FILE_REALTIME:-}"; then
        export VLM_AS_VERIFIER_CONFIG_FILE_REALTIME="${default_realtime_config}"
    fi
    if is_placeholder_deploy_path "${SDR_CONTROLLER_CONFIG_PATH:-}"; then
        export SDR_CONTROLLER_CONFIG_PATH="${default_sdrc_config_path}"
    fi
}

ensure_prometheus_config_readable() {
    local config_file="${SCRIPT_DIR}/configs/prometheus_alert_agent.yml"
    [[ -f "${config_file}" ]] || return 0

    local mode=""
    mode="$(stat -c "%a" "${config_file}" 2>/dev/null || true)"
    if [[ "${mode}" =~ ^[0-7]+$ ]] && (( (8#${mode} & 0044) == 0044 )); then
        return 0
    fi

    if chmod a+r "${config_file}"; then
        log_info "Updated Prometheus config permissions for container read access: ${config_file}"
        return 0
    fi

    log_error "Prometheus config is not readable by the container user: ${config_file}"
    return 1
}

write_merged_dev_profile_env() {
    local output_file="$1"
    local blueprint_env_file="$2"
    shift 2
    local -a user_env_files=("$@")
    local defaults_env_file="${SCRIPT_DIR}/.env.defaults"
    local -a all_env_files=("${blueprint_env_file}")
    [[ -f "${defaults_env_file}" ]] && all_env_files+=("${defaults_env_file}")
    all_env_files+=("${user_env_files[@]}")

    local -a merged_keys=()
    local -A seen_keys=()
    local env_file key
    for env_file in "${all_env_files[@]}"; do
        [[ -f "${env_file}" ]] || continue
        while IFS= read -r key; do
            [[ -n "${key}" ]] || continue
            add_env_key_once "${key}" merged_keys seen_keys
        done < <(extract_env_file_keys "${env_file}")
    done
    if [[ "${blueprint_env_file}" == */dev-profile-alerts/.env ]]; then
        add_env_key_once "VSS_APPS_DIR" merged_keys seen_keys
        add_env_key_once "SDR_CONTROLLER_CONFIG_PATH" merged_keys seen_keys
        add_env_key_once "VLM_AS_VERIFIER_CONFIG_FILE" merged_keys seen_keys
        add_env_key_once "VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE" merged_keys seen_keys
        add_env_key_once "VLM_AS_VERIFIER_CONFIG_FILE_REALTIME" merged_keys seen_keys
    fi

    (
        set -a
        [[ -f "${blueprint_env_file}" ]] && source "${blueprint_env_file}"

        if [[ -f "${defaults_env_file}" ]]; then
            local -a default_keys=()
            while IFS= read -r key; do
                [[ -n "${key}" ]] && default_keys+=("${key}")
            done < <(extract_env_file_keys "${defaults_env_file}")

            local -A previous_values=()
            local -A previous_set=()
            for key in "${default_keys[@]}"; do
                if [[ -v ${key} ]]; then
                    previous_set[${key}]=1
                    previous_values[${key}]="${!key}"
                fi
                unset "${key}"
            done

            source "${defaults_env_file}"

            for key in "${default_keys[@]}"; do
                if [[ -v ${key} ]]; then
                    if [[ -z "${!key}" && -n "${previous_set[${key}]:-}" ]]; then
                        printf -v "${key}" "%s" "${previous_values[${key}]}"
                        export "${key}"
                    fi
                elif [[ -n "${previous_set[${key}]:-}" ]]; then
                    printf -v "${key}" "%s" "${previous_values[${key}]}"
                    export "${key}"
                fi
            done
        fi

        for env_file in "${user_env_files[@]}"; do
            [[ -f "${env_file}" ]] && source "${env_file}"
        done
        normalize_alerts_dev_profile_paths "${blueprint_env_file}"
        set +a

        for key in "${merged_keys[@]}"; do
            if [[ -v ${key} ]]; then
                write_env_assignment "${key}" "${!key}"
            fi
        done
    ) > "${output_file}"
}

write_resolved_eval_defaults_env() {
    local output_file="$1"
    local defaults_env_file="${SCRIPT_DIR}/.env.defaults"
    [[ -f "${defaults_env_file}" ]] || return 1

    local -a merged_keys=()
    local -A seen_keys=()
    local key
    while IFS= read -r key; do
        [[ -n "${key}" ]] || continue
        if [[ -z "${seen_keys[${key}]:-}" ]]; then
            seen_keys[${key}]=1
            merged_keys+=("${key}")
        fi
    done < <(extract_env_file_keys "${defaults_env_file}")

    (
        set -a
        local -A previous_values=()
        local -A previous_set=()
        for key in "${merged_keys[@]}"; do
            if [[ -v ${key} ]]; then
                previous_set[${key}]=1
                previous_values[${key}]="${!key}"
            fi
            unset "${key}"
        done

        source "${defaults_env_file}"

        for key in "${merged_keys[@]}"; do
            if [[ -v ${key} ]]; then
                if [[ -z "${!key}" && -n "${previous_set[${key}]:-}" ]]; then
                    printf -v "${key}" "%s" "${previous_values[${key}]}"
                    export "${key}"
                fi
            elif [[ -n "${previous_set[${key}]:-}" ]]; then
                printf -v "${key}" "%s" "${previous_values[${key}]}"
                export "${key}"
            fi
        done
        set +a

        for key in "${merged_keys[@]}"; do
            if [[ -v ${key} ]]; then
                write_env_assignment "${key}" "${!key}"
            fi
        done
    ) > "${output_file}"
}

is_unresolved_external_ip() {
    local value="${1:-}"
    case "${value}" in
        ""|"<HOST_IP>"|"<REPLACE_ME>"|'${HOST_IP}'|'$HOST_IP'|*"<HOST_IP>"*|*"<REPLACE_ME>"*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Map MODE from profile .env (2d_cv | 2d_vlm) to dev-profile -m value (verification | real-time)
mode_env_to_alerts_mode() {
    local mode="$1"
    case "${mode}" in
        2d_cv) echo "verification" ;;
        2d_vlm) echo "real-time" ;;
        *) echo "" ;;
    esac
}

validate_required_vars() {
    # NGC_CLI_API_KEY is required for deploy but is read from blueprint profile .env in deploy_blueprint();
    # no need to require it here from eval .env.
    :
}

validate_eval_type() {
    local eval_type="$1"
    case "${eval_type}" in
        accuracy|latency) return 0 ;;
        *)
            log_error "Invalid EVAL_TYPE: '${eval_type}'. Must be: accuracy or latency"
            exit 1
            ;;
    esac
}

# -----------------------------------------------------------------------------
# Helm Deployment Helpers
# -----------------------------------------------------------------------------
is_ipv4_address() {
    local value="${1:-}"
    [[ "${value}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

strip_url_to_host() {
    local value="${1:-}"
    value="${value#http://}"
    value="${value#https://}"
    value="${value%%/*}"
    echo "${value}"
}

resolve_helm_parent_charts_dir() {
    local explicit="${1:-}"
    if [[ -n "${explicit}" ]]; then
        echo "${explicit}"
    elif [[ -n "${HELM_PARENT_CHARTS_DIR:-}" ]]; then
        echo "${HELM_PARENT_CHARTS_DIR}"
    elif [[ -n "${VSS_PARENT_CHARTS_DIR:-}" ]]; then
        echo "${VSS_PARENT_CHARTS_DIR}"
    else
        echo "${SCRIPT_DIR}/../video-search-and-summarization/deploy/helm/developer-profiles"
    fi
}

resolve_helm_release() {
    local profile="$1"
    local explicit="${2:-}"
    echo "${explicit:-${HELM_PARENT_RELEASE:-${HELM_RELEASE:-vss-${profile}}}}"
}

resolve_helm_namespace() {
    local release="$1"
    local explicit="${2:-}"
    echo "${explicit:-${HELM_NAMESPACE:-${NAMESPACE:-${release}}}}"
}

resolve_helm_external_host() {
    local profile="$1"
    local explicit="${2:-}"
    local host="${explicit:-${HELM_EXTERNAL_HOST:-}}"
    if [[ -n "${host}" ]]; then
        strip_url_to_host "${host}"
        return 0
    fi

    local candidate="${EXTERNAL_IP:-${HOST_IP:-}}"
    if is_unresolved_external_ip "${candidate}"; then
        candidate=""
    fi
    candidate="$(strip_url_to_host "${candidate}")"
    if [[ -z "${candidate}" ]]; then
        return 0
    fi
    if is_ipv4_address "${candidate}"; then
        echo "vss-${profile}.${candidate}.nip.io"
    else
        echo "${candidate}"
    fi
}

resolve_alerts_evaluator_media_base_url() {
    local external_host="${1:-}"
    local explicit="${ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL:-${MEDIA_BASE_URL:-}}"
    if [[ -n "${explicit}" ]]; then
        echo "${explicit%/}"
        return 0
    fi

    external_host="$(strip_url_to_host "${external_host}")"
    if [[ -z "${external_host}" ]]; then
        return 0
    fi
    echo "http://${external_host}/vst/sim/media"
}

resolve_compose_alerts_evaluator_host() {
    local use_remote_vlm="${USE_REMOTE_VLM:-false}"
    [[ "${VLM_MODE:-}" == "remote" || "${NIM:-}" == "remote" ]] && use_remote_vlm="true"

    local host=""
    if [[ "${use_remote_vlm}" == "true" ]]; then
        host="$(strip_url_to_host "${HOST_IP:-}")"
        if is_unresolved_external_ip "${host}" || [[ "${host}" == "0.0.0.0" ]]; then
            host=""
        fi
        if [[ -z "${host}" ]]; then
            host="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' | head -1 || true)"
        fi
    fi

    echo "${host:-127.0.0.1}"
}

resolve_compose_alerts_evaluator_media_base_url() {
    local explicit="${ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL:-${MEDIA_BASE_URL:-}}"
    if [[ -n "${explicit}" ]]; then
        echo "${explicit%/}"
        return 0
    fi

    echo "http://$(resolve_compose_alerts_evaluator_host):30888/vst/sim/media"
}

resolve_compose_alerts_evaluator_vst_base_url() {
    local explicit="${ALERT_BRIDGE_EVALUATOR_VST_BASE_URL:-}"
    if [[ -n "${explicit}" ]]; then
        echo "${explicit%/}"
        return 0
    fi

    echo "http://$(resolve_compose_alerts_evaluator_host):30888/vst"
}


resolve_helm_ngc_key() {
    for var_name in NGC_PERSONAL_API_KEY NGC_CLI_API_KEY NGC_API_KEY; do
        local value="${!var_name:-}"
        if [[ -n "${value}" ]]; then
            echo "${value}"
            return 0
        fi
    done
}

normalize_helm_gpu_type() {
    local hardware_profile="${1:-}"
    [[ "${hardware_profile}" == "RTX6000PROBW" ]] && hardware_profile="RTXPRO6000BW"
    echo "${hardware_profile}"
}

append_arg_if_set() {
    local -n target="$1"
    local flag="$2"
    local value="${3:-}"
    if [[ -n "${value}" ]]; then
        target+=("${flag}" "${value}")
    fi
    return 0
}

append_helm_set_if_set() {
    local -n target="$1"
    local key="$2"
    local value="${3:-}"
    if [[ -n "${value}" ]]; then
        target+=(--set "${key}=${value}")
    fi
    return 0
}

append_env_list_values() {
    local -n target="$1"
    local value="${2:-}"
    local separator="${3:-:}"
    [[ -z "${value}" ]] && return 0
    local old_ifs="${IFS}"
    IFS="${separator}"
    # shellcheck disable=SC2206
    local parts=( ${value} )
    IFS="${old_ifs}"
    local part
    for part in "${parts[@]}"; do
        [[ -n "${part}" ]] && target+=("${part}")
    done
}

resolve_helm_values_files() {
    local profile="$1"
    local alerts_mode="${2:-}"
    local parent_charts_dir="$3"
    local -n explicit_values_ref="$4"
    local -n out_values_ref="$5"

    if [[ ${#explicit_values_ref[@]} -gt 0 ]]; then
        out_values_ref+=("${explicit_values_ref[@]}")
        return 0
    fi
    if [[ -n "${HELM_VALUES_FILE:-}" ]]; then
        out_values_ref+=("${HELM_VALUES_FILE}")
        return 0
    fi
    if [[ -n "${HELM_VALUES_FILES:-}" ]]; then
        append_env_list_values out_values_ref "${HELM_VALUES_FILES}" ":"
        return 0
    fi

    local values_file="values-${profile}.yaml"
    case "${profile}" in
        alerts)
            if [[ "${alerts_mode}" == "verification" ]]; then
                values_file="values-verification.yaml"
            else
                values_file="values-realtime.yaml"
            fi
            ;;
        base) values_file="values-base.yaml" ;;
        lvs) values_file="values-lvs.yaml" ;;
        search) values_file="values-build-endpoint.yaml" ;;
    esac
    out_values_ref+=("${parent_charts_dir}/dev-profile-${profile}/${values_file}")
}

resolve_helm_set_args() {
    local -n explicit_sets_ref="$1"
    local -n out_sets_ref="$2"

    if [[ ${#explicit_sets_ref[@]} -gt 0 ]]; then
        out_sets_ref+=("${explicit_sets_ref[@]}")
        return 0
    fi
    if [[ -n "${HELM_SET:-}" ]]; then
        out_sets_ref+=("${HELM_SET}")
    fi
    if [[ -n "${HELM_SET_ARGS:-}" ]]; then
        append_env_list_values out_sets_ref "${HELM_SET_ARGS}" ";"
    fi
}

resolve_alerts_evaluator_prometheus_host() {
    local mode="${1:-compose}"
    local host="${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_HOST:-${PROMETHEUS_HOST:-}}"

    if [[ "${mode}" == "helm" ]]; then
        case "${host}" in
            ""|localhost|127.0.0.1)
                echo "127.0.0.1"
                return 0
                ;;
        esac
    fi

    echo "${host:-localhost}"
}

resolve_synthetic_alert_submission_mode() {
    local configured="${1:-}"
    local normalized
    normalized="$(printf '%s' "${configured:-message_bus}" | tr '[:upper:]-' '[:lower:]_')"
    case "${normalized}" in
        message_bus|messagebus|bus|kafka)
            echo "message_bus"
            ;;
        incident_api|http_incident_api|http_incident)
            echo "incident_api"
            ;;
        ondemand_api|ondemand|on_demand|api)
            echo "ondemand_api"
            ;;
        *)
            log_error "--synthetic-alert-submission-mode must be one of: message_bus, incident_api, ondemand_api"
            return 1
            ;;
    esac
}

resolve_alerts_evaluator_metric_profile() {
    local mode="${1:-compose}"
    local profile_name="${2:-}"
    local alerts_mode="${3:-}"
    local eval_type="${4:-}"
    local synthetic_enabled="${5:-false}"
    local synthetic_submission_mode="${6:-message_bus}"
    local configured="${ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE:-${PROMETHEUS_METRIC_PROFILE:-}}"
    local normalized

    normalized="$(printf '%s' "${configured:-auto}" | tr '[:upper:]' '[:lower:]')"
    case "${normalized}" in
        ""|auto)
            if [[ "${profile_name}" == "alerts" && "${alerts_mode}" == "verification" && "${eval_type}" == "latency" && "${synthetic_enabled}" == true && "${synthetic_submission_mode}" == "ondemand_api" ]]; then
                echo "ondemand"
            else
                echo "realtime"
            fi
            ;;
        realtime|ondemand)
            echo "${normalized}"
            ;;
        *)
            log_error "ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE must be one of: auto, realtime, ondemand"
            return 1
            ;;
    esac
}

log_helm_command() {
    local label="$1"
    shift
    local redacted=()
    local next_is_set=false
    local arg key
    for arg in "$@"; do
        if [[ "${next_is_set}" == true ]]; then
            key="${arg%%=*}"
            case "${key,,}" in
                *apikey*|*api_key*|*token*|*secret*|*password*|*credential*|*dataset*)
                    redacted+=("${key}=<redacted>")
                    ;;
                *)
                    redacted+=("${arg}")
                    ;;
            esac
            next_is_set=false
            continue
        fi
        redacted+=("${arg}")
        [[ "${arg}" == "--set" || "${arg}" == "--set-string" ]] && next_is_set=true
    done
    log_debug "${label}: ${redacted[*]}"
}

validate_helm_alert_bridge_num_workers_resolved() {
    local parent_charts_dir="$1"
    local profile="$2"

    if [[ "${profile}" != "alerts" || "${EVAL_TYPE:-accuracy}" != "latency" || "${skip_deploy}" == true ]]; then
        return 0
    fi

    local alert_config_dir="${parent_charts_dir%/}/../services/alert/configs"
    local checked=false
    local file
    local -a config_files=(
        "${alert_config_dir}/config.yml"
        "${alert_config_dir}/EDGE-LOCAL-VLM-config.yml"
    )

    for file in "${config_files[@]}"; do
        [[ -f "${file}" ]] || continue
        checked=true
        if grep -Fq '${ALERT_BRIDGE_NUM_WORKERS}' "${file}"; then
            log_error "Helm Alert Bridge config still contains unresolved ALERT_BRIDGE_NUM_WORKERS placeholder: ${file}"
            log_error "Latency profile config must render alert_agent.num_workers as a number before Helm deploy."
            log_error "Run without --skip-latency-config-update, or fix ${SCRIPT_DIR}/configs/bp_config_latency_helm.yaml/custom bp profile config."
            return 1
        fi
        if ! grep -Eq '^[[:space:]]+num_workers:[[:space:]]+[0-9]+([[:space:]]*#.*)?$' "${file}"; then
            log_warn "Could not verify numeric alert_agent.num_workers in ${file}; continuing because no unresolved placeholder was found"
        fi
    done

    if [[ "${checked}" != true ]]; then
        log_warn "Could not find Alert Bridge Helm config files under ${alert_config_dir}; skipping num_workers placeholder validation"
    fi
}

run_helm_eval() {
    local profile="$1"
    local release namespace parent_charts_dir resolved_kubeconfig
    local external_host storage_class install_timeout test_timeout_seconds
    local ngc_key hardware_profile
    local dataset_root dataset_upload_root dataset_mount_path dataset_pvc_size dataset_storage_class
    local dataset_layout_root dataset_videos_subpath dataset_ground_truth_subpath
    local -a resolved_values=()
    local -a resolved_sets=()

    parent_charts_dir="$(resolve_helm_parent_charts_dir "${helm_parent_charts_dir}")"
    release="$(resolve_helm_release "${profile}" "${helm_release}")"
    namespace="$(resolve_helm_namespace "${release}" "${helm_namespace}")"
    resolved_kubeconfig="${kubeconfig:-${KUBECONFIG:-}}"
    external_host="$(resolve_helm_external_host "${profile}" "${helm_external_host}")"
    storage_class="${helm_storage_class:-${HELM_STORAGE_CLASS:-}}"
    install_timeout="${helm_install_timeout:-${HELM_INSTALL_TIMEOUT:-${HELM_PARENT_INSTALL_TIMEOUT:-}}}"
    local requested_test_timeout_seconds
    requested_test_timeout_seconds="${helm_test_timeout_seconds:-${HELM_TEST_TIMEOUT_SECONDS:-}}"
    if ! test_timeout_seconds="$(resolve_helm_test_timeout_seconds "${requested_test_timeout_seconds}" "${EVAL_TYPE:-accuracy}" "${LATENCY_DURATION:-10m}" "${synthetic_alerts_injection:-false}")"; then
        return 1
    fi
    ngc_key="$(resolve_helm_ngc_key)"
    hardware_profile="$(normalize_helm_gpu_type "${HARDWARE_PROFILE:-}")"
    dataset_root="${helm_dataset_root:-${HELM_DATASET_ROOT:-}}"
    if [[ -z "${dataset_root}" && "${skip_dataset_download}" == true ]] && ! is_placeholder_path_value "${EVAL_DATASET_PATH:-}"; then
        dataset_root="${EVAL_DATASET_PATH}"
    fi
    if [[ -n "${LATENCY_SOURCE_VIDEO:-}" && -z "${dataset_root}" ]]; then
        log_error "--latency-source-video requires --skip-dataset-download with --helm-dataset-root or EVAL_DATASET_PATH in Helm mode"
        return 1
    fi
    dataset_upload_root=""
    dataset_layout_root="${EVAL_DATASET_PATH:-}"
    if is_placeholder_path_value "${dataset_layout_root}"; then
        dataset_layout_root="${dataset_root}"
    fi
    dataset_videos_subpath=""
    dataset_ground_truth_subpath=""
    dataset_mount_path="${helm_dataset_mount_path:-${HELM_DATASET_MOUNT_PATH:-}}"
    dataset_pvc_size="${helm_dataset_pvc_size:-${HELM_DATASET_PVC_SIZE:-}}"
    dataset_storage_class="${helm_dataset_storage_class:-${HELM_DATASET_STORAGE_CLASS:-${storage_class}}}"

    if [[ -n "${dataset_root}" ]]; then
        if [[ ! -d "${dataset_root}" ]]; then
            log_error "Helm dataset root not found or not a directory: ${dataset_root}"
            return 1
        fi
        dataset_root="$(realpath "${dataset_root}")"
        dataset_upload_root="${dataset_root}"
        if ! dataset_videos_subpath="$(resolve_helm_dataset_relative_path "${dataset_root}" "${dataset_layout_root}" "${EVAL_VIDEOS_PATH:-}" "EVAL_VIDEOS_PATH" "dir")"; then
            return 1
        fi
        if [[ "${EVAL_TYPE:-accuracy}" != "latency" ]]; then
            if ! dataset_ground_truth_subpath="$(resolve_helm_dataset_relative_path "${dataset_root}" "${dataset_layout_root}" "${EVAL_GROUND_TRUTH_PATH:-}" "EVAL_GROUND_TRUTH_PATH" "file")"; then
                return 1
            fi
        elif ! is_placeholder_path_value "${EVAL_GROUND_TRUTH_PATH:-}"; then
            if dataset_ground_truth_subpath="$(resolve_helm_dataset_relative_path_if_exists "${dataset_root}" "${dataset_layout_root}" "${EVAL_GROUND_TRUTH_PATH:-}" "file")"; then
                :
            else
                log_warn "Skipping optional latency ground truth path for Helm local upload; local file not found under dataset root: ${EVAL_GROUND_TRUTH_PATH}"
            fi
        fi
    fi

    resolve_helm_values_files "${profile}" "${ALERTS_MODE:-}" "${parent_charts_dir}" helm_values_files resolved_values
    resolve_helm_set_args helm_set_args resolved_sets

    local chart_dir="${parent_charts_dir}/dev-profile-${profile}"
    if [[ ! -d "${chart_dir}" ]]; then
        log_error "Helm parent chart directory not found: ${chart_dir}"
        return 1
    fi
    local values_file
    for values_file in "${resolved_values[@]}"; do
        if [[ ! -f "${values_file}" ]]; then
            log_error "Helm values file not found: ${values_file}"
            return 1
        fi
    done

    if [[ -n "${ngc_key}" && -z "${NVDATASET_NGC_API_KEY:-}" ]]; then
        export NVDATASET_NGC_API_KEY="${ngc_key}"
    fi

    validate_helm_alert_bridge_num_workers_resolved "${parent_charts_dir}" "${profile}" || return 1

    log_section "Helm Deployment"
    log_kv "Profile" "${profile}"
    log_kv "Release" "${release}"
    log_kv "Namespace" "${namespace}"
    log_kv "Parent charts dir" "${parent_charts_dir}"
    log_kv "External host" "${external_host:-<not set>}"
    log_kv "Storage class" "${storage_class:-<not set>}"
    log_kv "Dataset root" "${dataset_root:-<pod download>}"
    log_kv "Dataset mount path" "${dataset_mount_path:-<module default>}"
    log_kv "Dataset PVC size" "${dataset_pvc_size:-<module default>}"
    log_kv "Dataset storage class" "${dataset_storage_class:-<cluster default>}"
    if [[ -n "${dataset_root}" ]]; then
        log_kv "Dataset videos subpath" "${dataset_videos_subpath:-<chart default>}"
        log_kv "Dataset ground truth subpath" "${dataset_ground_truth_subpath:-<chart default>}"
    fi
    if [[ "${profile}" == "alerts" && "${ALERTS_MODE:-}" == "verification" ]]; then
        log_kv "Alerts evaluator media URL" "$(resolve_alerts_evaluator_media_base_url "${external_host}")"
        log_kv "Alerts evaluator VST URL" "<test chart default>"
    fi
    log_kv "Install timeout" "${install_timeout:-<module default>}"
    log_kv "Test timeout" "${test_timeout_seconds:-<module default>}"
    echo ""

    local -a deploy_cmd=(
        python3 -m eval.kubernetes.deploy_helm
        --flavor "${profile}"
        --release "${release}"
        --namespace "${namespace}"
        --parent-charts-dir "${parent_charts_dir}"
    )
    append_arg_if_set deploy_cmd --kubeconfig "${resolved_kubeconfig}"
    append_arg_if_set deploy_cmd --install-timeout "${install_timeout}"
    append_arg_if_set deploy_cmd --llm-base-url "${LLM_BASE_URL:-${LLM_ENDPOINT_URL:-}}"
    append_arg_if_set deploy_cmd --vlm-base-url "${VLM_BASE_URL:-${VLM_ENDPOINT_URL:-}}"
    for values_file in "${resolved_values[@]}"; do
        deploy_cmd+=(-f "${values_file}")
    done
    append_helm_set_if_set deploy_cmd ngc.apiKey "${ngc_key}"
    append_helm_set_if_set deploy_cmd global.externalHost "${external_host}"
    append_helm_set_if_set deploy_cmd global.storageClass "${storage_class}"
    append_helm_set_if_set deploy_cmd nims.gpuType "${hardware_profile}"
    if [[ "${profile}" == "alerts" ]]; then
        append_helm_set_if_set deploy_cmd vssIngress.host "${external_host}"
    fi
    local set_arg
    for set_arg in "${resolved_sets[@]}"; do
        deploy_cmd+=(--set "${set_arg}")
    done
    if [[ "${profile}" == "alerts" && -n "${num_sensors_cli:-}" ]]; then
        append_helm_set_if_set \
            deploy_cmd \
            rtvi.vss-rtvi-cv.env.NUM_SENSORS \
            "$(normalize_num_sensors "${NUM_SENSORS:-1}")"
    fi
    if [[ "${synthetic_alerts_injection:-false}" == true && "${profile}" == "alerts" ]]; then
        deploy_cmd+=(--set "analytics.vss-behavior-analytics.enabled=false")
    fi

    if [[ "${skip_deploy}" == false ]]; then
        log_info "Deploying Helm parent release with --wait via deploy_helm.py..."
        log_helm_command "deploy_helm.py" "${deploy_cmd[@]}"
        "${deploy_cmd[@]}"
    else
        log_info "Skipping Helm deployment (--skip-deploy)"
        if [[ "${synthetic_alerts_injection:-false}" == true && "${profile}" == "alerts" ]]; then
            log_warn "Skipping Helm deployment; ensure the existing parent release has behavior analytics disabled for synthetic alerts injection"
        fi
    fi

    if [[ -n "${dataset_root}" ]]; then
        prepare_helm_dataset_upload_root "${dataset_root}" "${dataset_videos_subpath:-${DEFAULT_HELM_ALERTS_VIDEOS_SUBPATH}}" || return 1
        dataset_upload_root="${HELM_DATASET_UPLOAD_ROOT}"
    fi

    local -a test_cmd=(
        python3 -m eval.kubernetes.run_helm_test
        --flavor "${profile}"
        --release "${release}"
        --namespace "${namespace}"
    )
    append_arg_if_set test_cmd --kubeconfig "${resolved_kubeconfig}"
    append_arg_if_set test_cmd --timeout-seconds "${test_timeout_seconds}"
    append_arg_if_set test_cmd --image-repository "${helm_test_image_repository:-${HELM_TEST_IMAGE_REPOSITORY:-}}"
    append_arg_if_set test_cmd --image-tag "${helm_test_image_tag:-${HELM_TEST_IMAGE_TAG:-}}"
    append_arg_if_set test_cmd --image-pull-secret "${helm_test_image_pull_secret:-${HELM_TEST_IMAGE_PULL_SECRET:-}}"
    if [[ "${profile}" == "alerts" ]]; then
        append_helm_set_if_set test_cmd alertsVerification.hardwareProfile "${hardware_profile}"
    fi
    for set_arg in "${helm_test_set_args[@]}"; do
        test_cmd+=(--set "${set_arg}")
    done
    append_arg_if_set test_cmd --dataset-mount-path "${dataset_mount_path}"
    if [[ -n "${dataset_upload_root}" ]]; then
        test_cmd+=(--dataset-root "${dataset_upload_root}")
        append_arg_if_set test_cmd --dataset-videos-subpath "${dataset_videos_subpath}"
        append_arg_if_set test_cmd --dataset-ground-truth-subpath "${dataset_ground_truth_subpath}"
        append_arg_if_set test_cmd --dataset-pvc-size "${dataset_pvc_size}"
        append_arg_if_set test_cmd --dataset-storage-class "${dataset_storage_class}"
    elif [[ "${skip_dataset_download}" != true ]]; then
        test_cmd+=(--dataset-download)
    fi
    if [[ "${profile}" == "alerts" && "${ALERTS_MODE:-}" == "verification" ]]; then
        test_cmd+=(
            --alerts-verification
            --alerts-eval-type "${EVAL_TYPE:-accuracy}"
            --alerts-job-id "${JOB_ID:-${release}-alerts-eval}"
            --alerts-category "${ALERT_CATEGORY:-FOV Count Violation}"
            --alerts-num-sensors "$(normalize_num_sensors "${NUM_SENSORS:-1}")"
            --alerts-evaluator-data-source "${ALERT_BRIDGE_EVALUATOR_DATA_SOURCE:-${DATA_SOURCE:-prometheus}}"
            --alerts-evaluator-metric-profile "${ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE:-realtime}"
            --alerts-synthetic-alert-submission-mode "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-message_bus}"
            --alerts-evaluator-prometheus-host "$(resolve_alerts_evaluator_prometheus_host helm)"
            --alerts-evaluator-prometheus-port "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_PORT:-${PROMETHEUS_PORT:-9090}}"
            --alerts-evaluator-prometheus-timeout "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_TIMEOUT:-${PROMETHEUS_TIMEOUT:-30}}"
            --alerts-evaluator-prometheus-summary-buffer-seconds "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS:-${PROMETHEUS_SUMMARY_BUFFER_SECONDS:-0}}"
            --alerts-evaluator-synthetic-e2e-min-coverage-ratio "${SYNTHETIC_E2E_MIN_COVERAGE_RATIO:-0.99}"
        )
        append_arg_if_set test_cmd --alerts-evaluator-media-base-url "$(resolve_alerts_evaluator_media_base_url "${external_host}")"
        if [[ "${EVAL_TYPE:-accuracy}" == "latency" ]]; then
            test_cmd+=(--alerts-latency-duration "${LATENCY_DURATION:-10m}")
            append_arg_if_set test_cmd --alerts-latency-source-video "${LATENCY_SOURCE_VIDEO:-}"
            append_arg_if_set test_cmd --alerts-synthetic-alerts-per-minute "${SYNTHETIC_ALERTS_PER_MINUTE:-}"
            append_arg_if_set test_cmd --alerts-synthetic-alert-event-duration-ms "${SYNTHETIC_ALERT_EVENT_DURATION_MS:-}"
        fi
        if [[ "${synthetic_alerts_injection:-false}" == true ]]; then
            test_cmd+=(--alerts-synthetic-alerts-injection)
        fi
    fi
    if [[ "${profile}" == "search" ]]; then
        test_cmd+=(--search-fixture)
    fi

    log_section "Helm Tests"
    log_info "Running Helm test chart via run_helm_test.py..."
    log_helm_command "run_helm_test.py" "${test_cmd[@]}"
    local test_rc=0
    "${test_cmd[@]}" || test_rc=$?
    cleanup_helm_staged_dataset
    return "${test_rc}"
}

teardown_helm() {
    local profile="$1"
    local release namespace resolved_kubeconfig
    release="$(resolve_helm_release "${profile}" "${helm_release}")"
    namespace="$(resolve_helm_namespace "${release}" "${helm_namespace}")"
    resolved_kubeconfig="${kubeconfig:-${KUBECONFIG:-}}"

    log_section "Helm Teardown"
    log_kv "Profile" "${profile}"
    log_kv "Release" "${release}"
    log_kv "Namespace" "${namespace}"
    echo ""

    if [[ -n "${resolved_kubeconfig}" ]]; then
        export KUBECONFIG="${resolved_kubeconfig}"
    fi

    log_info "Uninstalling Helm release ${release} from namespace ${namespace}..."
    helm uninstall "${release}" -n "${namespace}" || log_warn "Helm uninstall failed or release was already absent"

    log_info "Cleaning up release-scoped SDR/SDRC cluster roles and bindings (best effort)..."
    local kind name
    local -a names=(
        "${release}-vss-vios-sdr"
        "${release}-sdrc"
        "${release}-vss-sdrc"
        "${release}-sdrc-controller"
    )
    for kind in clusterrole clusterrolebinding; do
        for name in "${names[@]}"; do
            kubectl delete "${kind}" "${name}" --ignore-not-found || true
        done
    done
    kubectl delete clusterrole,clusterrolebinding \
        -l "app.kubernetes.io/instance=${release}" \
        --ignore-not-found || true

    log_info "Deleting namespace ${namespace} (best effort)..."
    kubectl delete namespace "${namespace}" --ignore-not-found --wait=false || \
        log_warn "Namespace deletion failed for ${namespace}"

    log_success "Helm teardown complete"
}

# Set profile-based eval env vars for compose: trigger config file and video-upload flags.
# Init container waits for nvstreamer (31000) only if ENABLE_NVSTREAMER_VIDEO_UPLOAD=true,
# and for vst (30888) only if ENABLE_VMS_VIDEO_UPLOAD=true.
# - alerts/search: enable NVStreamer upload (and wait for it); base: no nvstreamer (no wait).
set_profile_eval_env() {
    local profile="$1"
    export ENABLE_VMS_VIDEO_UPLOAD="false"
    export ENABLE_NVSTREAMER_VIDEO_UPLOAD="false"
    export SEND_CONFIG_TO_SDR="false"
    export CALL_SENSOR_ADD_API="false"
    export ENABLE_SENSOR_CONFIGURATOR="true"
    export SENSOR_INFO_SOURCE="nvstreamer"
    case "${profile}" in
        alerts)
            export EVAL_TRIGGER_CONFIG="trigger_eval_config_alerts_${EVAL_TYPE}.yml"   # accuracy -> alerts_accuracy.yml, latency -> alerts_latency.yml
            export ENABLE_NVSTREAMER_VIDEO_UPLOAD="true"
            export SEND_CONFIG_TO_SDR="true"
            export CALL_SENSOR_ADD_API="true"
            ;;
        search)
            export EVAL_TRIGGER_CONFIG="trigger_eval_config_search.yml"
            export ENABLE_NVSTREAMER_VIDEO_UPLOAD="true"
            export CALL_SENSOR_ADD_API="true"
            ;;
        base|lvs)
            export EVAL_TRIGGER_CONFIG="trigger_eval_config_base.yml"
            export ENABLE_VMS_VIDEO_UPLOAD="true"
            export SENSOR_INFO_SOURCE="not_required"
            ;;
        *)
            # default to alerts profile with NVStreamer upload
            export EVAL_TRIGGER_CONFIG="trigger_eval_config_alerts_${EVAL_TYPE}.yml"
            export ENABLE_SENSOR_CONFIGURATOR="true"
            export ENABLE_NVSTREAMER_VIDEO_UPLOAD="true"
            export SEND_CONFIG_TO_SDR="true"
            export CALL_SENSOR_ADD_API="true"
            ;;
    esac
}

# -----------------------------------------------------------------------------
# Dataset Download Functions
# -----------------------------------------------------------------------------
ensure_uv_installed() {
    if ! command -v uv &>/dev/null; then
        log_info "Installing uv package manager..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="${HOME}/.local/bin:${PATH}"
    fi

    if ! command -v uv &>/dev/null; then
        log_error "uv package manager is required but could not be installed or found in PATH"
        return 1
    fi
}

ensure_uv_venv() {
    local venv_dir="$1"

    ensure_uv_installed || return 1

    if [[ ! -x "${venv_dir}/bin/python" ]]; then
        log_info "Creating virtual environment at ${venv_dir} using uv..."
        [[ -d "${venv_dir}" ]] && rm -rf "${venv_dir}"
        uv venv "${venv_dir}"
    fi


    if [[ ! -x "${venv_dir}/bin/python" ]]; then
        log_error "Virtual environment is missing python executable: ${venv_dir}/bin/python"
        return 1
    fi
}

uv_install_in_venv() {
    local venv_dir="$1"
    shift

    ensure_uv_venv "${venv_dir}" || return 1
    uv pip install --python "${venv_dir}/bin/python" "$@"
}

setup_venv() {
    ensure_uv_venv "${VENV_DIR}" || return 1

    log_info "Installing nvdataset using uv..."
    uv_install_in_venv \
        "${VENV_DIR}" \
        --extra-index-url https://artifactory.pdx.nvidia.com/artifactory/api/pypi/sw-ngc-data-platform-pypi-local/simple \
        nvdataset
}

download_dataset() {
    local dataset_path="${EVAL_DATASET_PATH:-}"
    local dataset_name="${EVAL_DATASET_NAME}"
    local tenant_id="${NVDATASET_TENANTID}"
    local api_key="${NGC_API_KEY:-${NGC_CLI_API_KEY:-}}"

    log_section "Downloading Dataset"

    # If no dataset path specified, use dataset name as default path
    if [[ -z "${dataset_path}" ]]; then
        dataset_path="${SCRIPT_DIR}/${dataset_name}"
        log_debug "EVAL_DATASET_PATH not set, using default: ${dataset_path}"
    fi

    # Check if dataset already exists
    if [[ -d "${dataset_path}" ]] && [[ -n "$(ls -A "${dataset_path}" 2>/dev/null)" ]]; then
        log_info "Dataset already present at '${dataset_path}', skipping download"
        return 0
    fi

    log_step "Dataset   ${BOLD}${dataset_name}${NC}"
    log_step "Target    ${dataset_path}"
    log_step "Tenant    ${tenant_id}"
    echo ""

    # Set the environment variables for nvdataset
    export NVDATASET_TENANTID="${tenant_id}"
    export NVDATASET_GROUPID="${NVDATASET_GROUPID:-vss-bp-team}"
    if [[ -n "${api_key}" ]]; then
        export NGC_API_KEY="${api_key}"
    fi

    # Setup virtual environment and install nvdataset
    setup_venv

    local nvdataset_bin="${VENV_DIR}/bin/nvdataset"
    if [[ ! -x "${nvdataset_bin}" ]]; then
        log_error "nvdataset was not installed in ${VENV_DIR}"
        return 1
    fi

    # Download the dataset
    _phase_start
    log_info "Fetching '${dataset_name}' via nvdataset..."
    "${nvdataset_bin}" download "${dataset_name}" "${dataset_path}"

    log_success "Dataset download complete ($(_phase_elapsed))"
}

# -----------------------------------------------------------------------------
# Synthetic Video Functions (duplicate videos to reach NUM_SENSORS)
# -----------------------------------------------------------------------------
ensure_synthetic_videos() {
    # Caller (pre-check in main) ensures: EVAL_VIDEOS_PATH set, is directory, 0 < video_count < num_sensors
    local videos_dir
    videos_dir="$(realpath "${EVAL_VIDEOS_PATH}")"
    local num_sensors="${NUM_SENSORS:-1}"

    if [[ -z "${videos_dir}" ]]; then
        log_info "EVAL_VIDEOS_PATH not set, skipping synthetic video generation"
        return 0
    fi

    # Validate NUM_SENSORS (positive integer; default 1)
    if ! [[ "${num_sensors}" =~ ^[0-9]+$ ]] || [[ "${num_sensors}" -lt 1 ]]; then
        log_warn "NUM_SENSORS invalid or non-positive ('${num_sensors}'), using 1"
        num_sensors=1
    fi

    # Resolve to absolute path and validate directory
    if [[ ! -d "${videos_dir}" ]]; then
        log_warn "EVAL_VIDEOS_PATH is not a directory or not readable: ${videos_dir}; skipping synthetic video generation"
        return 0
    fi
    
    if [[ ! -r "${videos_dir}" ]]; then
        log_warn "EVAL_VIDEOS_PATH not readable: ${videos_dir}; skipping synthetic video generation"
        return 0
    fi

    # Collect video files (regular files with allowed extensions, case-insensitive)
    local -a sources=()
    local f ext
    for f in "${videos_dir}"/*; do
        [[ -f "${f}" ]] || continue
        ext="${f##*.}"
        ext="${ext,,}"
        case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
            *" ${ext} "*) sources+=("${f}") ;;
        esac
    done
    # Sort for deterministic order
    if [[ ${#sources[@]} -gt 0 ]]; then
        readarray -t sources < <(printf '%s\n' "${sources[@]}" | sort)
    fi

    local current_count=${#sources[@]}

    if [[ ${current_count} -eq 0 ]]; then
        log_error "No video files in EVAL_VIDEOS_PATH ('${videos_dir}'); cannot duplicate. Add at least one video file (e.g. .mp4, .mkv, .webm, .avi, .mov)."
        exit 1
    fi

    if [[ ${current_count} -ge ${num_sensors} ]]; then
        log_info "Enough videos (${current_count} >= ${num_sensors}), skipping synthetic video generation"
        [[ -f "${SYNTHETIC_MANIFEST}" ]] && rm -f "${SYNTHETIC_MANIFEST}"
        return 0
    fi

    local need=$((num_sensors - current_count))
    log_info "Synthetic videos: ${current_count} present, ${num_sensors} required; creating ${need} duplicate(s)"

    local -a manifest_entries=()
    local i src base ext newname rel
    for (( i = 0; i < need; i++ )); do
        src="${sources[$(( i % current_count ))]}"
        base="${src##*/}"
        base="${base%.*}"
        ext="${src##*.}"
        newname="${videos_dir}/${SYNTHETIC_PREFIX}${base}_${i}.${ext}"
        rel="${SYNTHETIC_PREFIX}${base}_${i}.${ext}"
        cp -- "${src}" "${newname}"
        manifest_entries+=("${rel}")
    done

    # Write manifest: first line EVAL_VIDEOS_DIR=<abs>, then one relative path per line
    printf 'EVAL_VIDEOS_DIR=%s\n' "${videos_dir}" > "${SYNTHETIC_MANIFEST}"
    printf '%s\n' "${manifest_entries[@]}" >> "${SYNTHETIC_MANIFEST}"
    log_success "Created ${need} synthetic video(s); manifest at ${SYNTHETIC_MANIFEST}"
}

normalize_num_sensors() {
    local num_sensors="${1:-1}"
    if ! [[ "${num_sensors}" =~ ^[0-9]+$ ]] || [[ "${num_sensors}" -lt 1 ]]; then
        echo "1"
        return 0
    fi
    echo "${num_sensors}"
}

is_placeholder_path_value() {
    local value="${1:-}"
    [[ -z "${value}" || "${value}" == \<* || "${value}" == *"PATH_TO_"* ]]
}

validate_helm_dataset_local_equivalent() {
    local dataset_root="$1"
    local rel_path="$2"
    local label="$3"
    local expected_kind="${4:-path}"
    local local_path="${dataset_root}/${rel_path}"

    if [[ "${expected_kind}" == "dir" && ! -d "${local_path}" ]]; then
        log_error "${label} resolves to ${rel_path}, but local dataset upload path is missing directory: ${local_path}"
        return 1
    fi
    if [[ "${expected_kind}" == "file" && ! -f "${local_path}" ]]; then
        log_error "${label} resolves to ${rel_path}, but local dataset upload path is missing file: ${local_path}"
        return 1
    fi
}

resolve_helm_dataset_relative_path() {
    local dataset_root="$1"
    local dataset_layout_root="$2"
    local path_value="$3"
    local label="$4"
    local expected_kind="${5:-path}"

    if is_placeholder_path_value "${path_value}"; then
        return 0
    fi

    local abs_path rel_path layout_root_abs layout_path_abs
    if [[ -e "${path_value}" ]]; then
        abs_path="$(realpath "${path_value}")"
        case "${abs_path}/" in
            "${dataset_root}/"*)
                rel_path="$(realpath --relative-to="${dataset_root}" "${abs_path}")"
                validate_helm_dataset_local_equivalent "${dataset_root}" "${rel_path}" "${label}" "${expected_kind}" || return 1
                printf '%s\n' "${rel_path}"
                return 0
                ;;
        esac
    fi

    if ! is_placeholder_path_value "${dataset_layout_root}"; then
        layout_root_abs="$(realpath -m "${dataset_layout_root}")"
        layout_path_abs="$(realpath -m "${path_value}")"
        case "${layout_path_abs}/" in
            "${layout_root_abs}/"*)
                rel_path="$(realpath -m --relative-to="${layout_root_abs}" "${layout_path_abs}")"
                if [[ -z "${rel_path}" || "${rel_path}" == "." || "${rel_path}" == /* || "${rel_path}" == ..* ]]; then
                    log_error "Could not derive a safe relative path for ${label}: ${path_value} under ${dataset_layout_root}"
                    return 1
                fi
                validate_helm_dataset_local_equivalent "${dataset_root}" "${rel_path}" "${label}" "${expected_kind}" || return 1
                printf '%s\n' "${rel_path}"
                return 0
                ;;
        esac
    fi

    log_error "${label} must be inside the Helm dataset root (${dataset_root}) or EVAL_DATASET_PATH (${dataset_layout_root}) for local PVC upload: ${path_value}"
    return 1
}


resolve_helm_dataset_relative_path_if_exists() {
    local dataset_root="$1"
    local dataset_layout_root="$2"
    local path_value="$3"
    local expected_kind="${4:-path}"

    if is_placeholder_path_value "${path_value}"; then
        return 1
    fi

    local abs_path rel_path layout_root_abs layout_path_abs local_path
    if [[ -e "${path_value}" ]]; then
        abs_path="$(realpath "${path_value}")"
        case "${abs_path}/" in
            "${dataset_root}/"*)
                rel_path="$(realpath --relative-to="${dataset_root}" "${abs_path}")"
                local_path="${dataset_root}/${rel_path}"
                case "${expected_kind}" in
                    dir) [[ -d "${local_path}" ]] || return 1 ;;
                    file) [[ -f "${local_path}" ]] || return 1 ;;
                    *) [[ -e "${local_path}" ]] || return 1 ;;
                esac
                printf '%s\n' "${rel_path}"
                return 0
                ;;
        esac
    fi

    if ! is_placeholder_path_value "${dataset_layout_root}"; then
        layout_root_abs="$(realpath -m "${dataset_layout_root}")"
        layout_path_abs="$(realpath -m "${path_value}")"
        case "${layout_path_abs}/" in
            "${layout_root_abs}/"*)
                rel_path="$(realpath -m --relative-to="${layout_root_abs}" "${layout_path_abs}")"
                if [[ -z "${rel_path}" || "${rel_path}" == "." || "${rel_path}" == /* || "${rel_path}" == ..* ]]; then
                    return 1
                fi
                local_path="${dataset_root}/${rel_path}"
                case "${expected_kind}" in
                    dir) [[ -d "${local_path}" ]] || return 1 ;;
                    file) [[ -f "${local_path}" ]] || return 1 ;;
                    *) [[ -e "${local_path}" ]] || return 1 ;;
                esac
                printf '%s\n' "${rel_path}"
                return 0
                ;;
        esac
    fi

    return 1
}


validate_latency_source_video_name() {
    local source_name="${1:-}"

    [[ -z "${source_name}" ]] && return 0
    if [[ "${source_name}" == */* || "${source_name}" == *\\* || "${source_name}" == "." || "${source_name}" == ".." || "${source_name}" == *".."* ]]; then
        log_error "LATENCY_SOURCE_VIDEO must be a file name, not a path: ${source_name}"
        return 1
    fi

    local ext="${source_name##*.}"
    ext="${ext,,}"
    case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
        *" ${ext} "*) ;;
        *)
            log_error "LATENCY_SOURCE_VIDEO must use one of these extensions: ${SYNTHETIC_VIDEO_EXTENSIONS}"
            return 1
            ;;
    esac
}

resolve_latency_source_video_path() {
    local videos_dir="$1"
    local source_name="${LATENCY_SOURCE_VIDEO:-}"

    validate_latency_source_video_name "${source_name}" || return 1
    local source_path="${videos_dir}/${source_name}"
    if [[ ! -f "${source_path}" ]]; then
        log_error "LATENCY_SOURCE_VIDEO not found under ${videos_dir}: ${source_name}"
        return 1
    fi
    printf "%s\n" "${source_path}"
}

populate_latency_source_video_copies() {
    local source_path="$1"
    local dest_dir="$2"
    local prefix="$3"
    local num_sensors
    num_sensors="$(normalize_num_sensors "${NUM_SENSORS:-1}")"

    mkdir -p "${dest_dir}"
    local base ext i dest
    base="$(basename "${source_path}")"
    ext="${base##*.}"
    base="${base%.*}"
    for (( i = 0; i < num_sensors; i++ )); do
        dest="${dest_dir}/${prefix}${base}_${i}.${ext}"
        copy_video_for_helm_stage "${source_path}" "${dest}" || return 1
    done
}

stage_latency_source_videos_dir() {
    local videos_dir="$1"
    local source_path stage_parent stage_dir
    source_path="$(resolve_latency_source_video_path "${videos_dir}")" || return 1
    stage_parent="${TMPDIR:-/tmp}"
    stage_dir="$(mktemp -d "${stage_parent%/}/run-eval-latency-videos.XXXXXX")"
    if ! populate_latency_source_video_copies "${source_path}" "${stage_dir}" "${SYNTHETIC_PREFIX}latency_"; then
        rm -rf -- "${stage_dir}"
        return 1
    fi

    LATENCY_STAGED_VIDEOS_DIR="${stage_dir}"
    printf "LATENCY_STAGED_VIDEOS_DIR=%s\n" "${stage_dir}" > "${LATENCY_SOURCE_VIDEO_STAGE_MANIFEST}"
    export EVAL_VIDEOS_PATH="${stage_dir}"
    log_success "Staged latency source video $(basename "${source_path}") at ${stage_dir}"
}

remove_latency_source_video_stage() {
    [[ -f "${LATENCY_SOURCE_VIDEO_STAGE_MANIFEST}" ]] || return 0

    local line dir
    read -r line < "${LATENCY_SOURCE_VIDEO_STAGE_MANIFEST}"
    if [[ ! "${line}" =~ ^LATENCY_STAGED_VIDEOS_DIR=(.*)$ ]]; then
        log_warn "Latency source video manifest format invalid; skipping removal"
        return 0
    fi
    dir="${BASH_REMATCH[1]}"
    if [[ -n "${dir}" && -d "${dir}" && "$(basename "${dir}")" == run-eval-latency-videos.* ]]; then
        rm -rf -- "${dir}"
        log_success "Removed staged latency source videos: ${dir}"
    else
        log_warn "Latency source video stage path is missing or not recognized: ${dir}"
    fi
    rm -f "${LATENCY_SOURCE_VIDEO_STAGE_MANIFEST}"
}


copy_video_for_helm_stage() {
    local src="$1"
    local dest="$2"

    if cp --reflink=auto -- "${src}" "${dest}" 2>/dev/null; then
        return 0
    fi
    cp -- "${src}" "${dest}"
}

prepare_helm_dataset_upload_root() {
    local dataset_root="$1"
    local videos_subpath="${2:-${DEFAULT_HELM_ALERTS_VIDEOS_SUBPATH}}"
    local num_sensors
    num_sensors="$(normalize_num_sensors "${NUM_SENSORS:-1}")"

    HELM_DATASET_UPLOAD_ROOT="${dataset_root}"
    HELM_STAGED_DATASET_ROOT=""

    if [[ "${EVAL_TYPE:-accuracy}" != "latency" ]]; then
        return 0
    fi
    if [[ "${skip_synthetic_videos:-false}" == true ]]; then
        log_info "Skipping Helm dataset video staging (--skip-synthetic-videos)"
        return 0
    fi

    local videos_dir="${dataset_root}/${videos_subpath}"
    if [[ ! -d "${videos_dir}" ]]; then
        log_warn "Helm dataset video directory not found: ${videos_dir}; skipping synthetic staging"
        return 0
    fi

    if [[ -n "${LATENCY_SOURCE_VIDEO:-}" ]]; then
        local selected_source stage_parent stage_dir stage_videos_dir old_video old_ext
        selected_source="$(resolve_latency_source_video_path "${videos_dir}")" || return 1
        stage_parent="${TMPDIR:-/tmp}"
        stage_dir="$(mktemp -d "${stage_parent%/}/run-eval-helm-dataset.XXXXXX")"
        if ! cp -a -- "${dataset_root}/." "${stage_dir}/"; then
            rm -rf -- "${stage_dir}"
            log_error "Failed to stage Helm dataset from ${dataset_root}"
            return 1
        fi
        stage_videos_dir="${stage_dir}/${videos_subpath}"
        for old_video in "${stage_videos_dir}"/*; do
            [[ -f "${old_video}" ]] || continue
            old_ext="${old_video##*.}"
            old_ext="${old_ext,,}"
            case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
                *" ${old_ext} "*) rm -f -- "${old_video}" ;;
            esac
        done
        if ! populate_latency_source_video_copies "${selected_source}" "${stage_videos_dir}" "${SYNTHETIC_PREFIX}helm_latency_"; then
            rm -rf -- "${stage_dir}"
            return 1
        fi
        HELM_STAGED_DATASET_ROOT="${stage_dir}"
        HELM_DATASET_UPLOAD_ROOT="${stage_dir}"
        log_success "Staged Helm latency source video $(basename "${selected_source}") at ${stage_dir}"
        return 0
    fi

    local -a sources=()
    local f ext
    for f in "${videos_dir}"/*; do
        [[ -f "${f}" ]] || continue
        ext="${f##*.}"
        ext="${ext,,}"
        case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
            *" ${ext} "*) sources+=("${f}") ;;
        esac
    done
    if [[ ${#sources[@]} -gt 0 ]]; then
        readarray -t sources < <(printf '%s\n' "${sources[@]}" | sort)
    fi

    local current_count=${#sources[@]}
    if [[ ${current_count} -eq 0 ]]; then
        log_warn "No videos found under ${videos_dir}; skipping synthetic staging"
        return 0
    fi
    if [[ ${current_count} -ge ${num_sensors} ]]; then
        log_info "Helm dataset has enough videos (${current_count} >= ${num_sensors}); no staging needed"
        return 0
    fi

    local stage_parent="${TMPDIR:-/tmp}"
    local stage_dir
    stage_dir="$(mktemp -d "${stage_parent%/}/run-eval-helm-dataset.XXXXXX")"
    if ! cp -a -- "${dataset_root}/." "${stage_dir}/"; then
        rm -rf -- "${stage_dir}"
        log_error "Failed to stage Helm dataset from ${dataset_root}"
        return 1
    fi

    local stage_videos_dir="${stage_dir}/${videos_subpath}"
    local need=$((num_sensors - current_count))
    local i src base ext newname
    log_info "Helm latency dataset staging: ${current_count} video(s), NUM_SENSORS=${num_sensors}; creating ${need} synthetic copy/copies"
    for (( i = 0; i < need; i++ )); do
        src="${sources[$(( i % current_count ))]}"
        base="${src##*/}"
        base="${base%.*}"
        ext="${src##*.}"
        newname="${stage_videos_dir}/${SYNTHETIC_PREFIX}helm_${base}_${i}.${ext}"
        if ! copy_video_for_helm_stage "${src}" "${newname}"; then
            rm -rf -- "${stage_dir}"
            log_error "Failed to create staged Helm synthetic video: ${newname}"
            return 1
        fi
    done

    HELM_STAGED_DATASET_ROOT="${stage_dir}"
    HELM_DATASET_UPLOAD_ROOT="${stage_dir}"
    log_success "Staged Helm dataset at ${stage_dir}"
}

cleanup_helm_staged_dataset() {
    [[ -n "${HELM_STAGED_DATASET_ROOT}" ]] || return 0
    if [[ -d "${HELM_STAGED_DATASET_ROOT}" ]]; then
        rm -rf -- "${HELM_STAGED_DATASET_ROOT}"
        log_info "Removed staged Helm dataset: ${HELM_STAGED_DATASET_ROOT}"
    fi
    HELM_STAGED_DATASET_ROOT=""
    HELM_DATASET_UPLOAD_ROOT=""
}

remove_synthetic_videos() {
    [[ -f "${SYNTHETIC_MANIFEST}" ]] || return 0

    local dir line
    read -r line < "${SYNTHETIC_MANIFEST}"
    if [[ ! "${line}" =~ ^EVAL_VIDEOS_DIR=(.*)$ ]]; then
        log_warn "Synthetic manifest format invalid (missing EVAL_VIDEOS_DIR); skipping removal"
        return 0
    fi
    dir="${BASH_REMATCH[1]}"
    if [[ -z "${dir}" ]] || [[ ! -d "${dir}" ]]; then
        log_warn "Synthetic manifest EVAL_VIDEOS_DIR missing or not a directory; skipping removal"
        return 0
    fi

    local count=0
    while read -r line; do
        [[ -n "${line}" ]] || continue
        # Reject path traversal or absolute paths
        [[ "${line}" == *".."* ]] && continue
        [[ "${line}" == /* ]] && continue
        local target="${dir}/${line}"
        if [[ -f "${target}" ]]; then
            rm -f -- "${target}"
            (( count++ )) || true
        else
            log_warn "Synthetic file no longer present: ${target}"
        fi
    done < <(tail -n +2 "${SYNTHETIC_MANIFEST}")

    rm -f "${SYNTHETIC_MANIFEST}"
    log_success "Removed ${count} synthetic video(s)"
}

# -----------------------------------------------------------------------------
# Core Functions
# -----------------------------------------------------------------------------
run_latency_init_config() {
    local user_env_files=("$@")
    local profile_dir="${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${BLUEPRINT}"
    local dev_env_file="${profile_dir}/.env"
    local dev_overrides_env_file="${profile_dir}/overrides.env"
    local dev_generated_env_file="${profile_dir}/generated.env"

    log_section "Latency Init Config"
    log_info "Running init-config-manager before blueprint deploy..."

    cd "${SCRIPT_DIR}"

    local compose_cmd="docker compose -p vss --profile latency-init"
    [[ -f "${dev_env_file}" ]] && compose_cmd+=" --env-file ${dev_env_file}"
    [[ -f "${dev_overrides_env_file}" ]] && compose_cmd+=" --env-file ${dev_overrides_env_file}"
    [[ -f "${dev_generated_env_file}" ]] && compose_cmd+=" --env-file ${dev_generated_env_file}"

    local _ef _path
    for _ef in "${user_env_files[@]}"; do
        if [[ -f "${SCRIPT_DIR}/${_ef}" ]]; then
            _path="${SCRIPT_DIR}/${_ef}"
        elif [[ -f "${_ef}" ]]; then
            _path="$(cd "$(dirname "${_ef}")" && pwd)/$(basename "${_ef}")"
        else
            _path=""
        fi
        if [[ -n "${_path}" ]]; then
            compose_cmd+=" --env-file ${_path}"
        fi
    done

    DOCKER_UID="$(id -u)"
    DOCKER_GID="$(id -g)"
    export DOCKER_UID DOCKER_GID
    log_debug "Running: ${compose_cmd} up init-config-manager"
    _phase_start
    ${compose_cmd} up init-config-manager
    local rc=$?
    if [[ ${rc} -ne 0 ]]; then
        log_error "init-config-manager failed (exit code ${rc})"
        return 1
    fi
    log_success "init-config-manager completed ($(_phase_elapsed))"
}


resolve_profile_config_blueprints_dir() {
    local explicit_dir="${1:-}"
    local deployment_mode_for_config="${2:-compose}"
    local -a required_subdirs=("deploy/docker" "deployments")
    local -a candidates=()

    if [[ "${deployment_mode_for_config}" == "helm" ]]; then
        required_subdirs=(
            "deploy/helm"
            "video-search-and-summarization/deploy/helm"
        )
    fi

    [[ -n "${explicit_dir}" ]] && candidates+=("${explicit_dir}")
    [[ -n "${BLUEPRINTS_DIR:-}" ]] && candidates+=("${BLUEPRINTS_DIR}")
    candidates+=(
        "${SCRIPT_DIR}/.."
        "${SCRIPT_DIR}/../video-search-and-summarization"
        "${SCRIPT_DIR}/../../met-blueprints"
    )

    local candidate required_subdir
    for candidate in "${candidates[@]}"; do
        [[ -n "${candidate}" ]] || continue
        for required_subdir in "${required_subdirs[@]}"; do
            if [[ -d "${candidate}/${required_subdir}" ]]; then
                BLUEPRINTS_DIR="$(realpath "${candidate}")"
                export BLUEPRINTS_DIR
                return 0
            fi
        done
    done

    log_error "BLUEPRINTS_DIR must point to a checkout containing one of: ${required_subdirs[*]} for ${deployment_mode_for_config} latency profile config. Pass --blueprints-dir DIR or set BLUEPRINTS_DIR."
    log_debug "Checked blueprint dir candidates: ${candidates[*]}"
    return 1
}

resolve_profile_config_python() {
    PROFILE_CONFIG_RUNNER_PYTHON=""

    if [[ -n "${PROFILE_CONFIG_PYTHON:-}" ]]; then
        if command -v "${PROFILE_CONFIG_PYTHON}" >/dev/null 2>&1 || [[ -x "${PROFILE_CONFIG_PYTHON}" ]]; then
            PROFILE_CONFIG_RUNNER_PYTHON="${PROFILE_CONFIG_PYTHON}"
            return 0
        fi
        log_error "PROFILE_CONFIG_PYTHON is set but not executable: ${PROFILE_CONFIG_PYTHON}"
        return 1
    fi

    local venv_dir="${VENV_DIR}"
    ensure_uv_venv "${venv_dir}" || return 1

    log_info "Installing profile-config dependency ruamel.yaml==0.18.15 using uv..."
    uv_install_in_venv "${venv_dir}" "ruamel.yaml==0.18.15" || return 1

    PROFILE_CONFIG_RUNNER_PYTHON="${venv_dir}/bin/python"
}

run_latency_profile_config() {
    local explicit_blueprints_dir="${1:-}"
    local deployment_mode_for_config="${2:-compose}"
    local config_name="bp_config_latency.yaml"
    local hardware_profile="${HARDWARE_PROFILE:-default}"

    if [[ "${deployment_mode_for_config}" == "helm" ]]; then
        config_name="bp_config_latency_helm.yaml"
    fi

    local config_file=""
    local custom_config_file="${bp_profile_config_file:-${BP_PROFILE_CONFIG_FILE:-}}"

    resolve_profile_config_blueprints_dir "${explicit_blueprints_dir}" "${deployment_mode_for_config}" || return 1

    if [[ -n "${custom_config_file}" ]]; then
        config_file="${custom_config_file}"
        if [[ ! -f "${config_file}" ]]; then
            log_error "BP profile config file not found: ${config_file}"
            return 1
        fi
        config_file="$(realpath "${config_file}")"
    else
        config_file="${SCRIPT_DIR}/configs/${config_name}"
        if [[ ! -f "${config_file}" ]]; then
            log_error "Latency profile config file not found: ${config_file}"
            return 1
        fi
    fi

    resolve_profile_config_python || return 1

    log_section "Latency Profile Config"
    log_info "Applying latency profile config before deployment..."
    log_kv "Config file" "${config_file}"
    log_kv "Blueprints dir" "${BLUEPRINTS_DIR}"
    log_kv "Hardware profile" "${hardware_profile}"

    local -a cmd=(
        "${PROFILE_CONFIG_RUNNER_PYTHON}"
        -m eval.scripts.deploy.apply_profile_config
        --config-file "${config_file}"
        --blueprints-dir "${BLUEPRINTS_DIR}"
        --hardware-profile "${hardware_profile}"
    )

    log_debug "Running: ${cmd[*]}"
    _phase_start
    (
        cd "${SCRIPT_DIR}/.."
        "${cmd[@]}"
    )
    local rc=$?
    if [[ ${rc} -ne 0 ]]; then
        log_error "Latency profile config failed (exit code ${rc})"
        return 1
    fi
    log_success "Latency profile config completed ($(_phase_elapsed))"
}

stop_alerts_behavior_analytics_for_synthetic_injection() {
    log_info "Stopping vss-behavior-analytics for synthetic alerts injection"
    if docker stop "vss-behavior-analytics" >/dev/null; then
        log_success "vss-behavior-analytics stopped"
        return 0
    fi
    log_error "Failed to stop vss-behavior-analytics for synthetic alerts injection"
    return 1
}


wait_for_alerts_prometheus_target_ready() {
    command -v curl >/dev/null 2>&1 || { log_error "wait_for_alerts_prometheus_target_ready requires curl"; return 1; }

    local port="${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_PORT:-${PROMETHEUS_PORT:-9090}}"
    local url="http://localhost:${port}/api/v1/query?query=up%7Bjob%3D%22alert-agent%22%7D"
    local attempts="${ALERTS_PROMETHEUS_READY_ATTEMPTS:-60}"
    local interval="${ALERTS_PROMETHEUS_READY_INTERVAL_SECONDS:-5}"
    local attempt response

    log_info "Waiting for Prometheus to scrape Alert Bridge metrics at ${url}"
    for (( attempt = 1; attempt <= attempts; attempt++ )); do
        response="$(curl -fsS "${url}" 2>/dev/null || true)"
        if printf '%s' "${response}" | grep -Eq '"value"[[:space:]]*:[[:space:]]*\[[^]]*,[[:space:]]*"1"\]'; then
            log_success "Prometheus Alert Bridge metrics target is ready"
            return 0
        fi
        log_debug "Prometheus Alert Bridge target not ready yet (attempt ${attempt}/${attempts})"
        sleep "${interval}"
    done

    log_error "Prometheus is not scraping the Alert Bridge metrics target. Redeploy the alerts profile so host port ${ALERT_BRIDGE_PROMETHEUS_HOST_PORT:-9081} is published, then retry."
    return 1
}

wait_for_compose_video_setup_ready() {
    command -v curl >/dev/null 2>&1 || { log_error "wait_for_compose_video_setup_ready requires curl"; return 1; }

    local bp_port="${BLUEPRINT_CONFIGURATOR_PORT:-5001}"
    local upload_url="http://localhost:${bp_port}/video-upload-status"
    local timelines_url="http://localhost:30888/vst/api/v1/storage/timelines"
    local attempts="${COMPOSE_SYNTHETIC_VIDEO_READY_ATTEMPTS:-120}"
    local interval="${COMPOSE_SYNTHETIC_VIDEO_READY_INTERVAL_SECONDS:-5}"
    local attempt upload_body timelines_body

    log_info "Waiting for blueprint configurator video upload at ${upload_url}"
    for (( attempt = 1; attempt <= attempts; attempt++ )); do
        upload_body="$(curl -fsS "${upload_url}" 2>/dev/null || true)"
        if echo "${upload_body}" | grep -Eq '"ready"[[:space:]]*:[[:space:]]*true'; then
            log_success "Blueprint configurator video upload is ready"
            break
        fi
        log_debug "Video upload not ready yet (attempt ${attempt}/${attempts})"
        sleep "${interval}"
    done
    if (( attempt > attempts )); then
        log_error "Blueprint configurator video upload did not become ready at ${upload_url}"
        return 1
    fi

    log_info "Waiting for VST storage timelines at ${timelines_url}"
    for (( attempt = 1; attempt <= attempts; attempt++ )); do
        timelines_body="$(curl -fsS "${timelines_url}" 2>/dev/null || true)"
        if [[ -n "${timelines_body//[[:space:]]/}" && "${timelines_body//[[:space:]]/}" != "{}" && "${timelines_body//[[:space:]]/}" != "[]" ]]; then
            log_success "VST storage timelines are ready"
            return 0
        fi
        log_debug "VST storage timelines not ready yet (attempt ${attempt}/${attempts})"
        sleep "${interval}"
    done

    log_error "VST storage timelines did not become ready at ${timelines_url}"
    return 1
}

compose_latency_sensor_ids_from_videos() {
    local videos_dir="${EVAL_VIDEOS_PATH:-}"
    local num_sensors
    num_sensors="$(normalize_num_sensors "${NUM_SENSORS:-1}")"

    if [[ -z "${videos_dir}" || ! -d "${videos_dir}" ]]; then
        log_error "EVAL_VIDEOS_PATH must point to the staged latency videos directory for synthetic alerts injection"
        return 1
    fi

    local -a videos=()
    local f ext
    for f in "${videos_dir}"/*; do
        [[ -f "${f}" ]] || continue
        ext="${f##*.}"
        ext="${ext,,}"
        case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
            *" ${ext} "*) videos+=("${f}") ;;
        esac
    done
    if [[ ${#videos[@]} -gt 0 ]]; then
        readarray -t videos < <(printf "%s\n" "${videos[@]}" | sort)
    fi

    if [[ ${#videos[@]} -lt "${num_sensors}" ]]; then
        log_error "Synthetic alerts injection needs ${num_sensors} staged video(s), found ${#videos[@]} in ${videos_dir}"
        return 1
    fi

    local i base
    for (( i = 0; i < num_sensors; i++ )); do
        base="$(basename "${videos[$i]}")"
        printf "%s\n" "${base%.*}"
    done
}


deploy_blueprint() {
    local profile="$1"
    local e2e_eval="$2"
    shift 2
    local user_env_files=("$@")
    local profile_env="${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}/.env"

    # Deployment params: CLI override (env set in main) wins; else read from blueprint profile .env
    local hardware_profile="${HARDWARE_PROFILE:-$(get_env_value "${profile_env}" "HARDWARE_PROFILE")}"
    [[ "${hardware_profile}" == "RTX6000PROBW" ]] && hardware_profile="RTXPRO6000BW"
    local host_ip="${HOST_IP:-$(get_env_value "${profile_env}" "HOST_IP")}"
    local external_ip="${EXTERNAL_IP:-$(get_env_value "${profile_env}" "EXTERNAL_IP")}"
    if is_unresolved_external_ip "${external_ip}"; then
        external_ip=""
    fi
    local ngc_cli_api_key="${NGC_CLI_API_KEY:-$(get_env_value "${profile_env}" "NGC_CLI_API_KEY")}"
    local _mode_from_bp
    _mode_from_bp="$(get_env_value "${profile_env}" "MODE")"
    local alerts_mode="${ALERTS_MODE:-$(mode_env_to_alerts_mode "${_mode_from_bp}")}"
    # Remote LLM/VLM: CLI (USE_REMOTE_LLM/VLM) or from profile .env LLM_MODE/VLM_MODE; backward compat NIM=remote
    local use_remote_llm="${USE_REMOTE_LLM:-false}"
    local use_remote_vlm="${USE_REMOTE_VLM:-false}"
    if [[ "${NIM:-}" == "remote" ]]; then
        use_remote_llm="true"
        use_remote_vlm="true"
    else
        [[ "$(get_env_value "${profile_env}" "LLM_MODE")" == "remote" ]] && use_remote_llm="true"
        [[ "$(get_env_value "${profile_env}" "VLM_MODE")" == "remote" ]] && use_remote_vlm="true"
    fi
    local llm_url="${LLM_ENDPOINT_URL:-${LLM_BASE_URL:-$(get_env_value "${profile_env}" "LLM_BASE_URL")}}"
    local vlm_url="${VLM_ENDPOINT_URL:-${VLM_BASE_URL:-$(get_env_value "${profile_env}" "VLM_BASE_URL")}}"
    local llm_device_id="${LLM_DEVICE_ID:-$(get_env_value "${profile_env}" "LLM_DEVICE_ID")}"
    local vlm_device_id="${VLM_DEVICE_ID:-$(get_env_value "${profile_env}" "VLM_DEVICE_ID")}"

    # Fallbacks when blueprint .env is missing or incomplete (e.g. first-time setup)
    hardware_profile="${hardware_profile:-RTXPRO6000BW}"
    export HARDWARE_PROFILE="${hardware_profile}"
    host_ip="${host_ip:-$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' || true)}"
    [[ -z "${host_ip}" ]] && host_ip="0.0.0.0"
    if [[ "${profile}" == "alerts" ]] && [[ -z "${alerts_mode}" ]]; then
        alerts_mode="verification"
    fi

    if [[ -z "${ngc_cli_api_key}" ]]; then
        log_error "NGC_CLI_API_KEY is required for deployment. Set it in the blueprint profile .env (${profile_env}) or export it."
        exit 1
    fi

    log_section "Deploying Blueprint"
    log_kv "Profile"            "${profile}"
    log_kv "Hardware profile"   "${hardware_profile}"
    log_kv "Host IP"            "${host_ip}"
    [[ -n "${external_ip}" ]] && log_kv "External IP" "${external_ip}"
    log_kv "Use remote LLM"     "${use_remote_llm}"
    log_kv "Use remote VLM"     "${use_remote_vlm}"
    if [[ "${use_remote_llm}" == "true" ]]; then
        log_kv "LLM endpoint"   "${llm_url}"
    else
        [[ -n "${llm_device_id}" ]] && log_kv "LLM device ID" "${llm_device_id}"
    fi
    if [[ "${use_remote_vlm}" == "true" ]]; then
        log_kv "VLM endpoint"   "${vlm_url}"
    else
        [[ -n "${vlm_device_id}" ]] && log_kv "VLM device ID" "${vlm_device_id}"
    fi
    echo ""

    export NGC_CLI_API_KEY="${ngc_cli_api_key}"
    if [[ -n "${NGC_CLI_API_KEY_NVAPI:-}" ]]; then
        export NGC_CLI_API_KEY_NVAPI
    fi
    if [[ "${use_remote_llm}" == "true" ]]; then
        export LLM_ENDPOINT_URL="${llm_url}"
    fi
    if [[ "${use_remote_vlm}" == "true" ]]; then
        export VLM_ENDPOINT_URL="${vlm_url}"
    fi
    # Allow eval to override NUM_SENSORS for deploy (dev-profile writes it to generated.env when set)
    [[ -n "${NUM_SENSORS:-}" ]] && export NUM_SENSORS="${NUM_SENSORS}"

    cd "${BLUEPRINT_DEPLOYMENTS_DIR}"

    local cmd_args=(
        -p "${profile}"
        -H "${hardware_profile}"
    )
    # Only pass -i when user explicitly provided --host-ip (let dev-profile.sh use its own default otherwise)
    if [[ -n "${HOST_IP_VIA_CLI:-}" ]]; then
        cmd_args+=(-i "${host_ip}")
    fi
    if [[ -n "${external_ip}" ]]; then
        cmd_args+=(--external-ip "${external_ip}")
    fi

    if [[ "${profile}" == "alerts" ]]; then
        cmd_args+=(-m "${alerts_mode}")
    fi

    [[ "${use_remote_llm}" == "true" ]] && cmd_args+=(--use-remote-llm)
    [[ "${use_remote_vlm}" == "true" ]] && cmd_args+=(--use-remote-vlm)
    # Edge hardware profiles (IGX-THOR, DGX-SPARK, AGX-THOR) do not support device-ID selection;
    # dev-profile.sh rejects --vlm-device-id and --llm-device-id for these profiles.
    local _is_edge_hw=false
    case "${hardware_profile}" in
        IGX-THOR|DGX-SPARK|AGX-THOR) _is_edge_hw=true ;;
    esac
    [[ -n "${llm_device_id}" ]] && [[ "${use_remote_llm}" != "true" ]] && [[ "${_is_edge_hw}" != "true" ]] && cmd_args+=(--llm-device-id "${llm_device_id}")
    [[ -n "${vlm_device_id}" ]] && [[ "${use_remote_vlm}" != "true" ]] && [[ "${_is_edge_hw}" != "true" ]] && cmd_args+=(--vlm-device-id "${vlm_device_id}")

    local -a resolved_user_env_files=()
    local _ef _path
    for _ef in "${user_env_files[@]}"; do
        _path="$(resolve_run_eval_env_path "${_ef}")"
        [[ -n "${_path}" ]] && resolved_user_env_files+=("${_path}")
    done

    local profile_env_backup=""
    local merged_profile_env=""
    if [[ -f "${profile_env}" ]]; then
        profile_env_backup="$(mktemp "${profile_env}.run-eval-backup.XXXXXX")"
        merged_profile_env="$(mktemp "${profile_env}.run-eval-merged.XXXXXX")"
        cp -p "${profile_env}" "${profile_env_backup}"
        write_merged_dev_profile_env "${merged_profile_env}" "${profile_env}" "${resolved_user_env_files[@]}"
        cp "${merged_profile_env}" "${profile_env}"
        log_info "Using merged blueprint env for dev-profile.sh (custom env > eval/.env.defaults > blueprint .env)"
    fi

    restore_profile_env() {
        if [[ -n "${profile_env_backup}" && -f "${profile_env_backup}" ]]; then
            cp "${profile_env_backup}" "${profile_env}"
        fi
        [[ -n "${profile_env_backup}" ]] && rm -f "${profile_env_backup}"
        [[ -n "${merged_profile_env}" ]] && rm -f "${merged_profile_env}"
    }

    _phase_start
    local _dev_profile_rc=0
    set +e
    ./scripts/dev-profile.sh "${cmd_args[@]}" up 2>&1 \
        | grep -v -E '^\s+[0-9a-f]{12}\s+(Pulling fs layer|Downloading|Download complete|Extracting|Pull complete|Waiting)\s'
    _dev_profile_rc=${PIPESTATUS[0]}
    set -e
    restore_profile_env
    if [[ ${_dev_profile_rc} -ne 0 ]]; then
        log_error "Blueprint deployment failed (exit code ${_dev_profile_rc})"
        return "${_dev_profile_rc}"
    fi

    log_success "Blueprint deployed successfully ($(_phase_elapsed))"

    # TODO: Check if this is needed
    # # Stop vss-behavior-analytics unless running e2e evaluation
    # if [[ "${e2e_eval}" == false ]]; then
    #     log_info "Stopping vss behaviour analytics"
    #     docker stop vss-behavior-analytics-${profile}
    #     log_success "VSS behaviour analytics stopped"
    # else
    #     log_info "Keeping vss-behavior-analytics running for e2e evaluation"
    # fi
}

write_deploy_failure_marker() {
    local stage="$1"
    local reason="$2"
    local timeout_sec="${3:-}"
    local marker="${SCRIPT_DIR}/.deploy_failure.json"
    if command -v python3 >/dev/null 2>&1; then
        DEPLOY_STAGE="${stage}" DEPLOY_REASON="${reason}" DEPLOY_TIMEOUT="${timeout_sec}" \
            python3 - <<'PY' > "${marker}" 2>/dev/null || true
import json, os
payload = {
    "stage": os.environ.get("DEPLOY_STAGE", ""),
    "reason": os.environ.get("DEPLOY_REASON", ""),
}
t = os.environ.get("DEPLOY_TIMEOUT", "").strip()
if t:
    try:
        payload["timeout_sec"] = int(t)
    except ValueError:
        payload["timeout_sec"] = t
print(json.dumps(payload))
PY
    else
        printf '{"stage":"%s","reason":"%s"}\n' "${stage}" "${reason}" > "${marker}" 2>/dev/null || true
    fi
    log_warn "Recorded deploy failure marker → ${marker}"
}

dump_eval_init_container_logs() {
    local c
    for c in bp-configurator-init bp-configurator; do
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "${c}"; then
            log_warn "---- logs: ${c} (last 40 lines) ----"
            docker logs --tail 40 "${c}" 2>&1 | sed 's/^/  /' || true
        fi
    done
}

run_evaluation() {
    local action="$1"
    local profile="$2"
    local services="$3"   # optional: space-separated service names to start (empty = all)
    shift 3
    local user_env_files=("$@")
    local profile_dir="${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}"
    local dev_env_file="${profile_dir}/.env"
    local dev_overrides_env_file="${profile_dir}/overrides.env"
    local dev_generated_env_file="${profile_dir}/generated.env"

    log_section "Evaluation Services  [${action^^}]"
    log_info "Action: ${action}  |  Profile: ${profile}"

    # Profile-based bp-configurator env (compose substitutes these when parsing)
    # if [[ "${profile}" == "alerts" ]]; then
    #     export SEND_CONFIG_TO_SDR=true
    # else
    #     export SEND_CONFIG_TO_SDR=false
    # fi

    cd "${SCRIPT_DIR}"

    # Resolve env file path (same logic as main: SCRIPT_DIR/rel or absolute/rel from cwd)
    resolve_env_path() {
        local ef="$1"
        if [[ -f "${SCRIPT_DIR}/${ef}" ]]; then
            echo "${SCRIPT_DIR}/${ef}"
        elif [[ -f "${ef}" ]]; then
            echo "$(cd "$(dirname "${ef}")" && pwd)/$(basename "${ef}")"
        fi
    }

    DOCKER_UID="$(id -u)"
    DOCKER_GID="$(id -g)"
    export DOCKER_UID DOCKER_GID
    local _resolved_hardware_profile="${HARDWARE_PROFILE:-$(get_env_value "${dev_env_file}" "HARDWARE_PROFILE")}"
    _resolved_hardware_profile="$(normalize_helm_gpu_type "${_resolved_hardware_profile}")"
    export HARDWARE_PROFILE="${_resolved_hardware_profile:-RTXPRO6000BW}"
    # Build docker compose --env-file list: deployment env -> resolved eval defaults -> user env.
    # eval/.env.defaults uses Bash syntax, so resolve it to a temporary plain env-file
    # before handing it to Docker Compose. Later files override earlier files.
    local _compose_env_paths=()
    local _ef _path _rp _seen_line _resolved_defaults_env=""
    _append_compose_env_if_exists() {
        local f="$1"
        [[ ! -f "${f}" ]] && return 0
        _rp="$(realpath "${f}" 2>/dev/null || echo "${f}")"
        for _seen_line in "${_compose_env_paths[@]}"; do
            [[ "${_seen_line}" == "${_rp}" ]] && return 0
        done
        _compose_env_paths+=("${_rp}")
    }

    _append_compose_env_if_exists "${dev_env_file}"
    _append_compose_env_if_exists "${dev_overrides_env_file}"
    _append_compose_env_if_exists "${dev_generated_env_file}"

    if [[ -f "${SCRIPT_DIR}/.env.defaults" ]]; then
        _resolved_defaults_env="$(mktemp "${TMPDIR:-/tmp}/run-eval-compose-defaults.XXXXXX")"
        write_resolved_eval_defaults_env "${_resolved_defaults_env}"
        _append_compose_env_if_exists "${_resolved_defaults_env}"
    fi

    if [[ ${#user_env_files[@]} -gt 0 ]]; then
        for _ef in "${user_env_files[@]}"; do
            _path="$(resolve_env_path "${_ef}")"
            if [[ -n "${_path}" ]]; then
                _append_compose_env_if_exists "${_path}"
            else
                case "${_ef##*/}" in
                    overrides.env|generated.env) log_debug "Optional profile env file not found: ${_ef}, skipping for compose" ;;
                    *) log_warn "Environment file not found: ${_ef}, skipping for compose" ;;
                esac
            fi
        done
    else
        # No --env-file passed to run_eval.sh: keep backward compatibility with eval/.env
        _append_compose_env_if_exists "${SCRIPT_DIR}/.env"
    fi

    # For alerts profile, pass --profile alerts (must be before subcommand: up/down).
    # Pin project name to vss (also set via `name:` in compose.yml) so CI filters
    # (--project vss) see bp-configurator / eval-orchestrator containers.
    # Alerts verification latency also needs local Prometheus for evaluator data-source mode.
    # On teardown, include the latency Prometheus profile unconditionally so containers
    # started by a previous latency run are removed even if the current env resolves
    # EVAL_TYPE back to accuracy.
    local compose_cmd="docker compose -p vss"
    local _uses_alerts_latency_prometheus=false
    if [[ "${profile}" == "alerts" ]]; then
        compose_cmd+=" --profile alerts"
        local _compose_eval_type_lc="${EVAL_TYPE:-accuracy}"
        local _compose_alerts_mode_lc="${ALERTS_MODE:-verification}"
        _compose_eval_type_lc="${_compose_eval_type_lc,,}"
        _compose_alerts_mode_lc="${_compose_alerts_mode_lc,,}"
        if [[ "${action}" == "down" || ( "${_compose_eval_type_lc}" == "latency" && "${_compose_alerts_mode_lc}" == "verification" ) ]]; then
            _uses_alerts_latency_prometheus=true
            compose_cmd+=" --profile alerts-verification-latency-prometheus"
            if [[ "${action}" == "down" ]]; then
                log_info "Including local Prometheus profile for alerts teardown"
            else
                log_info "Enabling local Prometheus profile for alerts verification latency eval"
            fi
        fi
    fi
    for _path in "${_compose_env_paths[@]}"; do
        compose_cmd+=" --env-file ${_path}"
    done

    if [[ "${action}" == "up" && "${_uses_alerts_latency_prometheus}" == true ]]; then
        ensure_prometheus_config_readable || return 1
    fi

    local _compose_status=0
    case "${action}" in
        up)
            # shellcheck disable=SC2086
            _phase_start
            local _compose_up_timeout="${COMPOSE_UP_TIMEOUT:-3600}"
            local _progress_pid=""
            export INIT_WAIT_TIMEOUT_SEC="${INIT_WAIT_TIMEOUT_SEC:-${_compose_up_timeout}}"
            log_info "Compose up timeout=${_compose_up_timeout}s (INIT_WAIT_TIMEOUT_SEC=${INIT_WAIT_TIMEOUT_SEC})"
            if command -v python3 >/dev/null 2>&1; then
                python3 -m eval.scripts.infra.compose_progress watch \
                    --project vss --interval 30 --eval-init-gates &
                _progress_pid=$!
            fi
            local _compose_rc=0
            if command -v timeout >/dev/null 2>&1; then
                timeout "${_compose_up_timeout}" ${compose_cmd} up -d --quiet-pull ${services} || _compose_rc=$?
            else
                ${compose_cmd} up -d --quiet-pull ${services} || _compose_rc=$?
            fi
            if [[ -n "${_progress_pid}" ]]; then
                kill "${_progress_pid}" 2>/dev/null || true
                wait "${_progress_pid}" 2>/dev/null || true
            fi
            if [[ ${_compose_rc} -ne 0 ]]; then
                local _reason="docker compose up failed or timed out after ${_compose_up_timeout}s"
                if [[ ${_compose_rc} -eq 124 ]]; then
                    _reason="docker compose up timed out after ${_compose_up_timeout}s (eval init chain likely blocked - see [compose-progress] / init container logs)"
                fi
                log_error "${_reason}"
                dump_eval_init_container_logs
                if command -v python3 >/dev/null 2>&1; then
                    python3 -m eval.scripts.infra.compose_progress once --project vss --eval-init-gates 2>/dev/null || true
                fi
                write_deploy_failure_marker "compose_up" "${_reason}" "${_compose_up_timeout}"
                exit 1
            fi
            log_success "Evaluation services started ($(_phase_elapsed))"
            ;;
        down)
            set +e
            ${compose_cmd} down
            _compose_status=$?
            set -e
            if [[ ${_compose_status} -eq 0 ]]; then
                log_success "Evaluation services stopped"
            fi
            ;;
        *)
            [[ -n "${_resolved_defaults_env}" ]] && rm -f -- "${_resolved_defaults_env}"
            log_error "Unknown action: ${action}"
            exit 1
            ;;
    esac

    [[ -n "${_resolved_defaults_env}" ]] && rm -f -- "${_resolved_defaults_env}"
    return "${_compose_status}"
}

# -----------------------------------------------------------------------------
# Poll eval status API until job is no longer submitted/running
# Requires: JOB_ID (from env or eval .env), curl, jq. Uses EVAL_STATUS_BASE_URL if set (default http://localhost:8000).
# Optional: EVAL_STATUS_POLL_INITIAL_WAIT (default 60) - seconds to wait before first poll (stack + job need time to be ready).
# Optional: EVAL_STATUS_POLL_MAX_ATTEMPTS (default 0 = unlimited) - stop after this many poll attempts.
# -----------------------------------------------------------------------------

teardown_all() {
    local profile="$1"
    local remove_synthetic="${2:-true}"
    shift 2
    local teardown_env_files=("$@")
    log_section "Teardown"
    log_kv "Profile"               "${profile}"
    log_kv "Remove synthetic videos" "${remove_synthetic}"
    echo ""

    # Teardown evaluation (same env files as up)
    run_evaluation "down" "${profile}" "" "${teardown_env_files[@]}"

    # Teardown init-config-manager (latency-init profile) if it was started
    if docker ps -a --format '{{.Names}}' | grep -q '^init-config-manager$'; then
        log_info "Tearing down init-config-manager (latency-init profile)..."
        cd "${SCRIPT_DIR}"
        docker rm -f init-config-manager 2>/dev/null || true
    fi

    # Optionally remove synthetically generated videos
    if [[ "${remove_synthetic}" == "true" ]]; then
        remove_synthetic_videos
        remove_latency_source_video_stage
    else
        log_info "Keeping synthetic videos (--no-remove-synthetic-videos)"
    fi

    # Remove downloaded eval dataset
    local dataset_path="${EVAL_DATASET_PATH:-${SCRIPT_DIR}/${EVAL_DATASET_NAME:-blueprint-eval}}"
    if [[ -d "${dataset_path}" ]]; then
        log_info "Removing eval dataset at ${dataset_path}..."
        rm -rf "${dataset_path}"
        log_success "Eval dataset removed"
    else
        log_info "No eval dataset found at ${dataset_path}, nothing to remove"
    fi

    # Teardown blueprint
    cd "${BLUEPRINT_DEPLOYMENTS_DIR}"
    if [[ -f "./scripts/dev-profile.sh" ]]; then
        log_info "Tearing down blueprint..."
        ./scripts/dev-profile.sh down || true
    fi

    log_success "All services torn down"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    local env_files=()
    local profile=""
    local profile_cli=""
    local deployment_mode="${DEPLOYMENT_MODE:-compose}"
    local deployment_mode_via_cli=false
    local skip_deploy=false
    local skip_dataset_download=false
    local teardown=false
    local verbose=false
    local alerts_eval_mode="e2e"
    local e2e_eval=true
    local e2e_eval_requested=false
    local synthetic_alerts_injection=false
    local synthetic_alerts_per_minute_cli=""
    local synthetic_alert_event_duration_ms_cli=""
    local synthetic_alert_submission_mode_cli=""
    local synthetic_alert_submission_mode=""
    local post_injection_drain_seconds_cli=""
    local hardware_profile=""
    local host_ip=""
    local host_ip_via_cli=false
    local external_ip=""
    local external_ip_via_cli=false
    local llm_url=""
    local vlm_url=""
    local llm_device_id=""
    local vlm_device_id=""
    local use_remote_llm=false
    local use_remote_vlm=false
    local alerts_mode=""
    local dataset_name=""
    local eval_dataset_path=""
    local eval_videos_path=""
    local eval_ground_truth_path=""
    local tenant_id=""
    local venv_dir=""
    local blueprints_dir=""
    local bp_profile_config_file=""
    local eval_type_cli=""
    local num_sensors_cli=""
    local latency_duration_cli=""
    local latency_source_video_cli=""
    local alert_category_cli=""
    local alert_bridge_incident_category_cli=""
    local alert_bridge_num_workers=""
    local skip_latency_config_update=false
    local no_remove_synthetic_videos=false
    local skip_synthetic_videos=false
    local skip_nat_eval=false
    local video_upload_only=false
    local helm_release=""
    local helm_namespace=""
    local kubeconfig=""
    local helm_parent_charts_dir=""
    local -a helm_values_files=()
    local -a helm_set_args=()
    local -a helm_test_set_args=()
    local helm_external_host=""
    local helm_storage_class=""
    local helm_install_timeout=""
    local helm_test_timeout_seconds=""
    local helm_dataset_root=""
    local helm_dataset_pvc_size=""
    local helm_dataset_mount_path=""
    local helm_dataset_storage_class=""
    local helm_test_image_repository=""
    local helm_test_image_tag=""
    local helm_test_image_pull_secret=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -e|--env-file)
                env_files+=("$2")
                shift 2
                ;;
            -p|--profile)
                profile_cli="$2"
                shift 2
                ;;
            -t|--eval-type)
                eval_type_cli="$2"
                shift 2
                ;;
            --deployment-mode)
                deployment_mode="$2"
                deployment_mode_via_cli=true
                shift 2
                ;;
            -s|--skip-deploy)
                skip_deploy=true
                shift
                ;;
            -d|--down)
                teardown=true
                shift
                ;;
            -v|--verbose)
                verbose=true
                VERBOSE=true
                set -x
                shift
                ;;
            --skip-dataset-download)
                skip_dataset_download=true
                shift
                ;;
            --e2e-eval)
                alerts_eval_mode="e2e"
                e2e_eval=true
                e2e_eval_requested=true
                shift
                ;;
            --synthetic-alerts-injection)
                alerts_eval_mode="synthetic-alerts-injection"
                e2e_eval=false
                synthetic_alerts_injection=true
                shift
                ;;
            --synthetic-alerts-per-minute)
                synthetic_alerts_per_minute_cli="$2"
                shift 2
                ;;
            --synthetic-alert-event-duration-ms)
                synthetic_alert_event_duration_ms_cli="$2"
                shift 2
                ;;
            --post-injection-drain-seconds)
                post_injection_drain_seconds_cli="$2"
                shift 2
                ;;
            --synthetic-alert-submission-mode)
                synthetic_alert_submission_mode_cli="$2"
                shift 2
                ;;
            --hardware-profile)
                hardware_profile="$2"
                shift 2
                ;;
            --host-ip)
                host_ip="$2"
                host_ip_via_cli=true
                shift 2
                ;;
            --external-ip)
                external_ip="$2"
                external_ip_via_cli=true
                shift 2
                ;;
            --llm-url)
                llm_url="$2"
                shift 2
                ;;
            --vlm-url)
                vlm_url="$2"
                shift 2
                ;;
            --llm-device-id)
                llm_device_id="$2"
                shift 2
                ;;
            --vlm-device-id)
                vlm_device_id="$2"
                shift 2
                ;;
            --use-remote-llm)
                use_remote_llm=true
                shift
                ;;
            --use-remote-vlm)
                use_remote_vlm=true
                shift
                ;;
            --alerts-mode)
                alerts_mode="$2"
                shift 2
                ;;
            --dataset-name)
                dataset_name="$2"
                shift 2
                ;;
            --dataset-path)
                eval_dataset_path="$2"
                shift 2
                ;;
            --videos-path)
                eval_videos_path="$2"
                shift 2
                ;;
            --ground-truth-path)
                eval_ground_truth_path="$2"
                shift 2
                ;;
            --tenant-id)
                tenant_id="$2"
                shift 2
                ;;
            --venv-dir)
                venv_dir="$2"
                shift 2
                ;;
            --blueprints-dir)
                blueprints_dir="$2"
                shift 2
                ;;
            --bp-profile-config-file)
                bp_profile_config_file="$2"
                shift 2
                ;;
            --num-sensors)
                num_sensors_cli="$2"
                shift 2
                ;;
            --latency-duration)
                latency_duration_cli="$2"
                shift 2
                ;;
            --latency-source-video)
                latency_source_video_cli="$2"
                shift 2
                ;;
            --alert-category)
                alert_category_cli="$2"
                shift 2
                ;;
            --alert-bridge-incident-category)
                alert_bridge_incident_category_cli="$2"
                shift 2
                ;;
            --alert-bridge-num-workers)
                alert_bridge_num_workers="$2"
                shift 2
                ;;
            --skip-latency-config-update)
                skip_latency_config_update=true
                shift
                ;;
            --no-remove-synthetic-videos)
                no_remove_synthetic_videos=true
                shift
                ;;
            --skip-synthetic-videos)
                skip_synthetic_videos=true
                shift
                ;;
            --video-upload-only)
                video_upload_only=true
                skip_nat_eval=true
                shift
                ;;
            --skip-nat-eval)
                skip_nat_eval=true
                shift
                ;;
            --helm-release)
                helm_release="$2"
                shift 2
                ;;
            --helm-namespace)
                helm_namespace="$2"
                shift 2
                ;;
            --kubeconfig)
                kubeconfig="$2"
                shift 2
                ;;
            --helm-parent-charts-dir)
                helm_parent_charts_dir="$2"
                shift 2
                ;;
            --helm-values-file)
                helm_values_files+=("$2")
                shift 2
                ;;
            --helm-set)
                helm_set_args+=("$2")
                shift 2
                ;;
            --helm-test-set)
                helm_test_set_args+=("$2")
                shift 2
                ;;
            --helm-external-host)
                helm_external_host="$2"
                shift 2
                ;;
            --helm-storage-class)
                helm_storage_class="$2"
                shift 2
                ;;
            --helm-install-timeout)
                helm_install_timeout="$2"
                shift 2
                ;;
            --helm-test-timeout-seconds)
                helm_test_timeout_seconds="$2"
                shift 2
                ;;
            --helm-dataset-root)
                helm_dataset_root="$2"
                shift 2
                ;;
            --helm-dataset-pvc-size)
                helm_dataset_pvc_size="$2"
                shift 2
                ;;
            --helm-dataset-mount-path)
                helm_dataset_mount_path="$2"
                shift 2
                ;;
            --helm-dataset-storage-class)
                helm_dataset_storage_class="$2"
                shift 2
                ;;
            --helm-test-image-repository)
                helm_test_image_repository="$2"
                shift 2
                ;;
            --helm-test-image-tag)
                helm_test_image_tag="$2"
                shift 2
                ;;
            --helm-test-image-pull-secret)
                helm_test_image_pull_secret="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    if [[ "${e2e_eval_requested}" == true && "${synthetic_alerts_injection}" == true ]]; then
        log_error "--e2e-eval and --synthetic-alerts-injection cannot be used together"
        usage
        exit 1
    fi


    case "${deployment_mode}" in
        compose|helm) ;;
        *)
            log_error "Invalid deployment mode: ${deployment_mode}. Must be: compose or helm"
            exit 1
            ;;
    esac

    # Enable verbose mode if requested
    if [[ "${verbose}" == true ]]; then
        set -x
    fi

    # Resolve eval type: CLI overrides env
    local eval_type="${eval_type_cli:-${EVAL_TYPE:-accuracy}}"
    validate_eval_type "${eval_type}"
    export EVAL_TYPE="${eval_type}"

    # Resolve latency duration (ES notation): CLI overrides env
    if [[ -n "${latency_duration_cli}" ]]; then
        export LATENCY_DURATION="${latency_duration_cli}"
    fi

    log_banner
    log_info "Starting ${SCRIPT_NAME}..."
    log_debug "Script directory: ${SCRIPT_DIR}"

    # Build the list of default env files. The profile overrides/generated
    # files belong to the Docker Compose deployment and contain in-network
    # service names such as http://rtvi-vlm:8000. Loading those in Helm mode
    # leaks invalid Compose DNS names into parent chart values.
    # Pass "${profile_cli:-...}" so --profile is honored before BLUEPRINT is exported below.
    local _bp_path _bp_overrides_path _bp_generated_path _eval_defaults_path
    _bp_path="$(default_blueprint_profile_env_file "${profile_cli:-${BLUEPRINT:-alerts}}")"
    _bp_overrides_path="$(default_blueprint_profile_overrides_env_file "${profile_cli:-${BLUEPRINT:-alerts}}")"
    _bp_generated_path="$(default_blueprint_profile_generated_env_file "${profile_cli:-${BLUEPRINT:-alerts}}")"
    _eval_defaults_path="$(default_eval_env_file)"

    local _defaults=()
    if [[ -f "${_bp_path}" ]]; then
        _defaults+=("${_bp_path}")
    fi
    if [[ "${deployment_mode}" != "helm" ]]; then
        if [[ -f "${_bp_overrides_path}" ]]; then
            _defaults+=("${_bp_overrides_path}")
        fi
        if [[ -f "${_bp_generated_path}" ]]; then
            _defaults+=("${_bp_generated_path}")
        fi
    fi
    if [[ -f "${_eval_defaults_path}" ]]; then
        _defaults+=("${_eval_defaults_path}")
    fi

    # Load default env files first, then any --env-file args on top.
    local _all_env_files=("${_defaults[@]}")
    if [[ ${#env_files[@]} -gt 0 ]]; then
        _all_env_files+=("${env_files[@]}")
    fi

    # Pool inventory / CI may set device IDs before run_eval; profile overrides.env
    # defaults (e.g. search uses 1/2 for H100) must not clobber them during sourcing.
    local _pool_llm_device_id="${LLM_DEVICE_ID:-}"
    local _pool_vlm_device_id="${VLM_DEVICE_ID:-}"

    for _ef in "${_all_env_files[@]}"; do
        local _path=""
        if [[ -f "${SCRIPT_DIR}/${_ef}" ]]; then
            _path="${SCRIPT_DIR}/${_ef}"
        elif [[ -f "${_ef}" ]]; then
            _path="${_ef}"
        fi
        if [[ -n "${_path}" ]]; then
            log_info "Loading environment from: ${_path}"
            # shellcheck source=/dev/null
            source "${_path}"
        else
            case "${_ef##*/}" in
                overrides.env|generated.env) log_debug "Optional profile env file not found: ${_ef}, skipping" ;;
                *) log_warn "Environment file not found: ${_ef}, skipping" ;;
            esac
        fi
    done

    # Resolve profile and deployment mode after env files are loaded.
    profile="${profile_cli:-${BLUEPRINT:-alerts}}"
    export BLUEPRINT="${profile}"
    if [[ "${deployment_mode_via_cli}" != true && -n "${DEPLOYMENT_MODE:-}" ]]; then
        deployment_mode="${DEPLOYMENT_MODE}"
    fi
    case "${deployment_mode}" in
        compose|helm) ;;
        *)
            log_error "Invalid deployment mode: ${deployment_mode}. Must be: compose or helm"
            exit 1
            ;;
    esac

    # Check prerequisites after env loading so DEPLOYMENT_MODE from env files is honored.
    check_prerequisites "${deployment_mode}"

    # CLI overrides (take precedence over env file)
    [[ -n "${eval_type_cli}" ]] && export EVAL_TYPE="${eval_type_cli}"
    [[ -n "${hardware_profile}" ]] && export HARDWARE_PROFILE="${hardware_profile}"
    if [[ -n "${host_ip}" ]]; then
        export HOST_IP="${host_ip}"
        export BLUEPRINT_IP="${host_ip}"
        [[ "${host_ip_via_cli}" == true ]] && export HOST_IP_VIA_CLI="true"
    fi
    if [[ "${external_ip_via_cli}" == true ]]; then
        export EXTERNAL_IP="${external_ip}"
    elif [[ "${host_ip_via_cli}" == true ]] && is_unresolved_external_ip "${EXTERNAL_IP:-}"; then
        export EXTERNAL_IP="${HOST_IP}"
    elif is_unresolved_external_ip "${EXTERNAL_IP:-}"; then
        unset EXTERNAL_IP
    fi
    [[ -n "${llm_url}" ]] && export LLM_ENDPOINT_URL="${llm_url}" && export LLM_BASE_URL="${llm_url}"
    [[ -n "${vlm_url}" ]] && export VLM_ENDPOINT_URL="${vlm_url}" && export VLM_BASE_URL="${vlm_url}"
    if [[ -n "${llm_device_id}" ]]; then
        export LLM_DEVICE_ID="${llm_device_id}"
    elif [[ -n "${_pool_llm_device_id}" ]]; then
        export LLM_DEVICE_ID="${_pool_llm_device_id}"
    fi
    if [[ -n "${vlm_device_id}" ]]; then
        export VLM_DEVICE_ID="${vlm_device_id}"
    elif [[ -n "${_pool_vlm_device_id}" ]]; then
        export VLM_DEVICE_ID="${_pool_vlm_device_id}"
    fi
    [[ "${use_remote_llm}" == true ]] && export USE_REMOTE_LLM="true"
    [[ "${use_remote_vlm}" == true ]] && export USE_REMOTE_VLM="true"
    [[ -n "${alerts_mode}" ]] && export ALERTS_MODE="${alerts_mode}"
    if [[ -n "${alerts_mode}" ]] && [[ "${profile}" != "alerts" ]]; then
        log_warn "Profile is '${profile}'; --alerts-mode is only used for profile=alerts and will be ignored."
    fi
    if [[ "${profile}" == "alerts" && -z "${ALERTS_MODE:-}" ]]; then
        local _alerts_mode_from_mode
        _alerts_mode_from_mode="$(mode_env_to_alerts_mode "${MODE:-}")"
        if [[ -n "${_alerts_mode_from_mode}" ]]; then
            export ALERTS_MODE="${_alerts_mode_from_mode}"
        else
            export ALERTS_MODE="verification"
        fi
    fi
    [[ -n "${dataset_name}" ]] && export EVAL_DATASET_NAME="${dataset_name}"
    [[ -n "${eval_dataset_path}" ]] && export EVAL_DATASET_PATH="${eval_dataset_path}"
    [[ -n "${eval_videos_path}" ]] && export EVAL_VIDEOS_PATH="${eval_videos_path}"
    [[ -n "${eval_ground_truth_path}" ]] && export EVAL_GROUND_TRUTH_PATH="${eval_ground_truth_path}"
    [[ -n "${tenant_id}" ]] && export NVDATASET_TENANTID="${tenant_id}"
    [[ -n "${venv_dir}" ]] && VENV_DIR="${venv_dir}"
    [[ -n "${blueprints_dir}" ]] && export BLUEPRINTS_DIR="${blueprints_dir}"
    if [[ -n "${num_sensors_cli}" ]]; then
        export NUM_SENSORS="${num_sensors_cli}"
        if [[ -z "${NUM_STREAMS:-}" ]]; then
            export NUM_STREAMS="${num_sensors_cli}"
        else
            export NUM_STREAMS="${NUM_STREAMS}"
        fi
    fi
    [[ -n "${latency_duration_cli}" ]] && export LATENCY_DURATION="${latency_duration_cli}"
    [[ -n "${latency_source_video_cli}" ]] && export LATENCY_SOURCE_VIDEO="${latency_source_video_cli}"
    [[ -n "${synthetic_alerts_per_minute_cli}" ]] && export SYNTHETIC_ALERTS_PER_MINUTE="${synthetic_alerts_per_minute_cli}"
    [[ -n "${synthetic_alert_event_duration_ms_cli}" ]] && export SYNTHETIC_ALERT_EVENT_DURATION_MS="${synthetic_alert_event_duration_ms_cli}"
    if [[ -n "${synthetic_alert_submission_mode_cli}" ]]; then
        if ! synthetic_alert_submission_mode="$(resolve_synthetic_alert_submission_mode "${synthetic_alert_submission_mode_cli}")"; then
            exit 1
        fi
        export ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE="${synthetic_alert_submission_mode}"
    elif [[ -n "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-${SYNTHETIC_ALERT_SUBMISSION_MODE:-}}" ]]; then
        if ! synthetic_alert_submission_mode="$(resolve_synthetic_alert_submission_mode "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-${SYNTHETIC_ALERT_SUBMISSION_MODE:-}}")"; then
            exit 1
        fi
        export ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE="${synthetic_alert_submission_mode}"
    elif [[ "${synthetic_alerts_injection}" == true ]]; then
        synthetic_alert_submission_mode="message_bus"
        export ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE="${synthetic_alert_submission_mode}"
    fi
    if [[ -n "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-}" ]]; then
        export SYNTHETIC_ALERT_SUBMISSION_MODE="${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE}"
    fi
    if [[ -n "${alert_bridge_num_workers}" ]]; then
        export ALERT_BRIDGE_NUM_WORKERS="${alert_bridge_num_workers}"
    fi
    export ALERT_BRIDGE_NUM_WORKERS="${ALERT_BRIDGE_NUM_WORKERS:-30}"
    if ! [[ "${ALERT_BRIDGE_NUM_WORKERS}" =~ ^[0-9]+$ ]] || [[ "${ALERT_BRIDGE_NUM_WORKERS}" -lt 1 ]]; then
        log_error "--alert-bridge-num-workers must be a positive integer"
        exit 1
    fi

    # Alerts latency (including E2E) and synthetic-alert evaluation do not use
    # dataset ground truth. Docker Compose creates a directory when the source
    # of a short bind mount does not exist; mounting that directory at
    # /app/gt.json makes the evaluator health endpoint fail with EISDIR.
    # Preserve a real, explicitly supplied GT file, but use /dev/null whenever
    # the optional path is absent or is not a regular file.
    if [[ "${deployment_mode}" == "compose" && "${profile}" == "alerts" ]] \
        && { [[ "${EVAL_TYPE:-accuracy}" == "latency" ]] || [[ "${synthetic_alerts_injection}" == true ]]; }; then
        if [[ "${EVAL_GROUND_TRUTH_PATH:-}" != "/dev/null" && ! -f "${EVAL_GROUND_TRUTH_PATH:-}" ]]; then
            if [[ -n "${EVAL_GROUND_TRUTH_PATH:-}" ]]; then
                log_warn "Ground truth is optional for alerts latency/synthetic eval; using /dev/null instead of non-file path: ${EVAL_GROUND_TRUTH_PATH}"
            else
                log_info "Ground truth is optional for alerts latency/synthetic eval; using /dev/null"
            fi
            export EVAL_GROUND_TRUTH_PATH="/dev/null"
        fi
    fi

    if [[ -z "${alert_category_cli}" && -n "${alert_bridge_incident_category_cli}" ]]; then
        alert_category_cli="${alert_bridge_incident_category_cli}"
    fi
    [[ -n "${alert_category_cli}" ]] && export ALERT_CATEGORY="${alert_category_cli}"
    if [[ -z "${ALERT_CATEGORY:-}" && -n "${ALERT_BRIDGE_INCIDENT_CATEGORY:-}" ]]; then
        export ALERT_CATEGORY="${ALERT_BRIDGE_INCIDENT_CATEGORY}"
    fi

    if [[ "${synthetic_alerts_injection}" == true ]]; then
        if [[ "${teardown}" != true ]]; then
            if [[ "${profile}" != "alerts" ]]; then
                log_error "--synthetic-alerts-injection is only supported with --profile alerts"
                exit 1
            fi
            if [[ "${ALERTS_MODE:-}" != "verification" ]]; then
                log_error "--synthetic-alerts-injection requires --alerts-mode verification"
                exit 1
            fi
            if [[ -z "${ALERT_CATEGORY:-}" ]] || [[ -z "${ALERT_CATEGORY//[[:space:]]/}" ]]; then
                log_error "ALERT_CATEGORY is required when using --synthetic-alerts-injection"
                log_error "Provide it via --alert-category \"<category>\" or set ALERT_CATEGORY in an env file."
                exit 1
            fi
            if [[ "${EVAL_TYPE:-accuracy}" == "latency" && -z "${LATENCY_SOURCE_VIDEO:-}" ]]; then
                log_error "--synthetic-alerts-injection requires --latency-source-video for latency eval"
                exit 1
            fi
            if [[ "${EVAL_TYPE:-accuracy}" == "latency" ]]; then
                export SYNTHETIC_ALERTS_PER_MINUTE="${SYNTHETIC_ALERTS_PER_MINUTE:-${NUM_SENSORS:-1}}"
                if ! [[ "${SYNTHETIC_ALERTS_PER_MINUTE}" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "${SYNTHETIC_ALERTS_PER_MINUTE}" =~ ^0+([.]0+)?$ ]]; then
                    log_error "--synthetic-alerts-per-minute must be a positive number"
                    exit 1
                fi
                if [[ -n "${SYNTHETIC_ALERT_EVENT_DURATION_MS:-}" ]] && { ! [[ "${SYNTHETIC_ALERT_EVENT_DURATION_MS}" =~ ^[0-9]+$ ]] || [[ "${SYNTHETIC_ALERT_EVENT_DURATION_MS}" -lt 1 ]]; }; then
                    log_error "--synthetic-alert-event-duration-ms must be a positive integer"
                    exit 1
                fi
                if [[ -n "${post_injection_drain_seconds_cli}" ]]; then
                    export ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS="${post_injection_drain_seconds_cli}"
                elif [[ -z "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS:-}" ]]; then
                    export ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS="${PROMETHEUS_SUMMARY_BUFFER_SECONDS:-90}"
                fi
                if ! [[ "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS}" =~ ^[0-9]+$ ]]; then
                    log_error "--post-injection-drain-seconds must be a nonnegative integer"
                    exit 1
                fi
                export PROMETHEUS_SUMMARY_BUFFER_SECONDS="${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS}"
            fi
        fi
        export AUTO_SEED_AB="true"
    else
        export AUTO_SEED_AB="false"
        export ALERT_CATEGORY="${ALERT_CATEGORY:-FOV Count Violation}"
    fi
    export ALERT_BRIDGE_INCIDENT_CATEGORY="${ALERT_CATEGORY:-}"
    export SYNTHETIC_ALERTS_INJECTION="${synthetic_alerts_injection}"
    if ! ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE="$(resolve_alerts_evaluator_metric_profile "${deployment_mode}" "${profile}" "${ALERTS_MODE:-}" "${EVAL_TYPE:-accuracy}" "${synthetic_alerts_injection}" "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-message_bus}")"; then
        exit 1
    fi
    export ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE

    if [[ "${deployment_mode}" == "compose" && "${profile}" == "alerts" && "${ALERTS_MODE:-}" == "verification" && "${EVAL_TYPE:-accuracy}" == "latency" && "${synthetic_alerts_injection}" == true ]]; then
        if [[ -z "${MEDIA_BASE_URL:-}" && -n "${ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL:-}" ]]; then
            export MEDIA_BASE_URL="${ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL}"
        fi
        if [[ -z "${MEDIA_BASE_URL:-}" ]]; then
            export MEDIA_BASE_URL="$(resolve_compose_alerts_evaluator_media_base_url)"
        fi
        export ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL="${ALERT_BRIDGE_EVALUATOR_MEDIA_BASE_URL:-${MEDIA_BASE_URL}}"
        if [[ -z "${ALERT_BRIDGE_EVALUATOR_VST_BASE_URL:-}" ]]; then
            export ALERT_BRIDGE_EVALUATOR_VST_BASE_URL="$(resolve_compose_alerts_evaluator_vst_base_url)"
        fi
    fi

    local resolved_helm_test_timeout_seconds=""
    if [[ "${deployment_mode}" == "helm" && "${teardown}" != true ]]; then
        if ! resolved_helm_test_timeout_seconds="$(resolve_helm_test_timeout_seconds "${helm_test_timeout_seconds:-${HELM_TEST_TIMEOUT_SECONDS:-}}" "${EVAL_TYPE:-accuracy}" "${LATENCY_DURATION:-10m}" "${synthetic_alerts_injection:-false}")"; then
            exit 1
        fi
    elif [[ "${deployment_mode}" == "helm" ]]; then
        resolved_helm_test_timeout_seconds="${helm_test_timeout_seconds:-${HELM_TEST_TIMEOUT_SECONDS:-}}"
    fi

    # Display effective configuration for this run
    log_section "Effective Configuration"
    log_kv "Profile (BLUEPRINT)"          "${profile}"
    log_kv "Deployment mode"              "${deployment_mode}"
    log_kv "Eval type (EVAL_TYPE)"        "${EVAL_TYPE:-accuracy}"
    log_kv "Alerts mode"                  "${ALERTS_MODE:-<not set>}"
    log_kv "Hardware profile"             "${HARDWARE_PROFILE:-<not set>}"
    log_kv "Host IP"                      "${HOST_IP:-<not set>}"
    log_kv "External IP"                  "${EXTERNAL_IP:-<not set>}"
    echo ""
    log_kv "Use remote LLM"              "${USE_REMOTE_LLM:-false}"
    log_kv "Use remote VLM"              "${USE_REMOTE_VLM:-false}"
    log_kv "LLM base URL"                "${LLM_BASE_URL:-<not set>}"
    log_kv "VLM base URL"                "${VLM_BASE_URL:-<not set>}"
    log_kv "LLM device ID"               "${LLM_DEVICE_ID:-<not set>}"
    log_kv "VLM device ID"               "${VLM_DEVICE_ID:-<not set>}"
    echo ""
    log_kv "Num sensors (NUM_SENSORS)"   "${NUM_SENSORS:-1}"
    log_kv "Latency duration"            "${LATENCY_DURATION:-10m}"
    log_kv "Dataset name"                "${EVAL_DATASET_NAME:-blueprint-eval}"
    log_kv "Latency source video"       "${LATENCY_SOURCE_VIDEO:-<not set>}"
    log_kv "Eval videos path"            "${EVAL_VIDEOS_PATH:-<not set>}"
    log_kv "Eval ground truth path"      "${EVAL_GROUND_TRUTH_PATH:-<not set>}"
    log_kv "Eval result dir"             "${EVAL_RESULT_DIR_PATH:-./eval_results}"
    log_kv "Job ID"                      "${JOB_ID}"
    log_kv "Configurator port"           "${BLUEPRINT_CONFIGURATOR_PORT:-5001}"
    log_kv "Evaluator port"              "${ALERT_BRIDGE_EVALUATOR_PORT:-5002}"
    log_kv "Alert Bridge num workers"   "${ALERT_BRIDGE_NUM_WORKERS:-30}"
    log_kv "Evaluator data source"       "${ALERT_BRIDGE_EVALUATOR_DATA_SOURCE:-${DATA_SOURCE:-prometheus}}"
    log_kv "Evaluator metric profile"    "${ALERT_BRIDGE_EVALUATOR_METRIC_PROFILE:-realtime}"
    log_kv "Evaluator Prometheus host"   "$(resolve_alerts_evaluator_prometheus_host "${deployment_mode}")"
    log_kv "Post-injection drain"       "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS:-0}s"
    log_kv "Evaluator Prometheus port"   "${ALERT_BRIDGE_EVALUATOR_PROMETHEUS_PORT:-${PROMETHEUS_PORT:-9090}}"
    log_kv "Prometheus metrics enabled" "${PROMETHEUS_METRICS_ENABLED:-true}"
    log_kv "Alerts eval mode"            "${alerts_eval_mode}"
    log_kv "Synthetic submission mode"   "${ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE:-<not set>}"
    log_kv "Synthetic alerts/min"       "${SYNTHETIC_ALERTS_PER_MINUTE:-<not set>}"
    log_kv "Synthetic event duration ms" "${SYNTHETIC_ALERT_EVENT_DURATION_MS:-<evaluator default>}"
    if [[ "${synthetic_alerts_injection}" == "true" && "${EVAL_TYPE:-accuracy}" == "latency" ]]; then
        log_kv "Synthetic distribution"   "round_robin"
    fi
    log_kv "Alert category"              "${ALERT_CATEGORY:-<not set>}"
    log_kv "Alert Bridge auto-seed"     "${AUTO_SEED_AB:-false}"
    if [[ "${deployment_mode}" == "compose" && "${profile}" == "alerts" && "${ALERTS_MODE:-}" == "verification" ]]; then
        log_kv "Evaluator media URL"       "${MEDIA_BASE_URL:-<compose default>}"
        log_kv "Evaluator VST URL"         "${ALERT_BRIDGE_EVALUATOR_VST_BASE_URL:-<not set>}"
    fi
    echo ""
    log_kv "Skip deploy"                 "${skip_deploy}"
    log_kv "Skip latency config update"  "${skip_latency_config_update}"
    log_kv "Skip dataset download"       "${skip_dataset_download}"
    log_kv "Skip synthetic videos"       "${skip_synthetic_videos}"
    log_kv "No-remove synthetic videos"  "${no_remove_synthetic_videos}"
    echo ""
    log_kv "NGC_CLI_API_KEY"             "$([[ -n "${NGC_CLI_API_KEY:-}" ]] && echo '<set>' || echo '<unset>')"
    log_kv "NGC_API_KEY"                 "$([[ -n "${NGC_API_KEY:-}" ]] && echo '<set>' || echo '<unset>')"
    log_kv "Env files"                   "${env_files[*]:-.env}"
    log_kv "Blueprints dir"               "${BLUEPRINTS_DIR:-<auto>}"
    log_kv "BP profile config file"      "${bp_profile_config_file:-${BP_PROFILE_CONFIG_FILE:-<auto>}}"
    if [[ "${deployment_mode}" == "helm" ]]; then
        local _helm_release_preview _helm_namespace_preview _helm_parent_preview _helm_external_host_preview
        local -a _helm_values_preview=()
        _helm_parent_preview="$(resolve_helm_parent_charts_dir "${helm_parent_charts_dir}")"
        _helm_release_preview="$(resolve_helm_release "${profile}" "${helm_release}")"
        _helm_namespace_preview="$(resolve_helm_namespace "${_helm_release_preview}" "${helm_namespace}")"
        _helm_external_host_preview="$(resolve_helm_external_host "${profile}" "${helm_external_host}")"
        resolve_helm_values_files "${profile}" "${ALERTS_MODE:-}" "${_helm_parent_preview}" helm_values_files _helm_values_preview
        echo ""
        log_kv "Helm release"             "${_helm_release_preview}"
        log_kv "Helm namespace"           "${_helm_namespace_preview}"
        log_kv "Helm parent charts dir"   "${_helm_parent_preview}"
        log_kv "Helm values files"        "${_helm_values_preview[*]}"
        log_kv "Helm external host"       "${_helm_external_host_preview:-<not set>}"
        log_kv "Helm storage class"       "${helm_storage_class:-${HELM_STORAGE_CLASS:-<not set>}}"
        log_kv "Helm test timeout"        "${resolved_helm_test_timeout_seconds:-<module default>}"
        local _helm_dataset_root_preview="${helm_dataset_root:-${HELM_DATASET_ROOT:-}}"
        if [[ -z "${_helm_dataset_root_preview}" && "${skip_dataset_download}" == true ]] && ! is_placeholder_path_value "${EVAL_DATASET_PATH:-}"; then
            _helm_dataset_root_preview="${EVAL_DATASET_PATH}"
        fi
        log_kv "Helm dataset root"        "${_helm_dataset_root_preview:-<pod download>}"
        log_kv "Helm dataset mount path"  "${helm_dataset_mount_path:-${HELM_DATASET_MOUNT_PATH:-<module default>}}"
        log_kv "Helm dataset PVC size"    "${helm_dataset_pvc_size:-${HELM_DATASET_PVC_SIZE:-<module default>}}"
        log_kv "Helm dataset storage class" "${helm_dataset_storage_class:-${HELM_DATASET_STORAGE_CLASS:-${helm_storage_class:-${HELM_STORAGE_CLASS:-<cluster default>}}}}"
    fi

    # Profile-based eval settings (trigger config, upload flags)
    set_profile_eval_env "${profile}"
    local _trigger_cfg="${EVAL_TRIGGER_CONFIG:-trigger_eval_config_${EVAL_TYPE:-accuracy}.yml}"
    log_kv "Trigger config file"          "${_trigger_cfg}"
    log_kv "VMS video upload"            "${ENABLE_VMS_VIDEO_UPLOAD:-false}"
    log_kv "NVStreamer video upload"      "${ENABLE_NVSTREAMER_VIDEO_UPLOAD:-false}"
    log_kv "Video upload delay"           "${VIDEO_UPLOAD_DELAY:-<not set>}"
    # Handle teardown
    if [[ "${teardown}" == true ]]; then
        local remove_synthetic="true"
        if [[ "${no_remove_synthetic_videos}" == true ]]; then
            remove_synthetic="false"
        elif [[ "${REMOVE_SYNTHETIC_VIDEOS_ON_DOWN:-true}" == "false" ]]; then
            remove_synthetic="false"
        fi
        if [[ "${deployment_mode}" == "helm" ]]; then
            teardown_helm "${profile}"
        else
            teardown_all "${profile}" "${remove_synthetic}" "${env_files[@]}"
        fi
        exit 0
    fi

    # Validate required variables for deployment
    if [[ "${skip_deploy}" == false ]]; then
        validate_required_vars
    fi

    if [[ "${skip_latency_config_update}" == true ]] && [[ "${EVAL_TYPE}" != "latency" ]]; then
        log_warn "--skip-latency-config-update has no effect unless --eval-type latency"
    fi

    if [[ -n "${LATENCY_SOURCE_VIDEO:-}" ]]; then
        if [[ "${EVAL_TYPE:-accuracy}" != "latency" ]]; then
            log_error "--latency-source-video requires --eval-type latency"
            exit 1
        fi
        if [[ "${skip_synthetic_videos}" == true ]]; then
            log_error "--latency-source-video cannot be used with --skip-synthetic-videos"
            exit 1
        fi
        validate_latency_source_video_name "${LATENCY_SOURCE_VIDEO}" || exit 1
    fi

    if [[ "${deployment_mode}" == "helm" ]]; then
        if [[ "${EVAL_TYPE}" == "latency" ]] && [[ "${skip_deploy}" == false ]] && [[ "${skip_latency_config_update}" != true ]]; then
            run_latency_profile_config "${blueprints_dir}" "helm"
        elif [[ "${EVAL_TYPE}" == "latency" ]] && [[ "${skip_deploy}" == false ]] && [[ "${skip_latency_config_update}" == true ]]; then
            log_info "Skipping latency profile config (--skip-latency-config-update)"
        fi
        if [[ "${skip_synthetic_videos}" != true ]]; then
            log_info "Helm mode uses pod-native dataset/video setup; skipping local synthetic video generation"
        fi
        run_helm_eval "${profile}"
        log_section "Done"
        log_success "Helm evaluation completed"
        log_step "Parent release left running → ${BOLD}${_helm_release_preview:-$(resolve_helm_release "${profile}" "${helm_release}")}${NC}"
        log_step "Teardown → ${BOLD}${SCRIPT_NAME} --deployment-mode helm --down --profile ${profile}${NC}"
        echo ""
        exit 0
    fi

    # Download dataset before deployment (NGC from blueprint .env if not set, so no duplicate in eval .env)
    if [[ "${skip_dataset_download}" == true ]]; then
        log_info "Skipping dataset download (--skip-dataset-download)"
    else
        if [[ -z "${NGC_CLI_API_KEY:-}" ]] && [[ -z "${NGC_API_KEY:-}" ]]; then
            local _pe="${BLUEPRINT_DEPLOYMENTS_DIR}/developer-profiles/dev-profile-${profile}/.env"
            if [[ -f "${_pe}" ]]; then
                NGC_CLI_API_KEY="$(get_env_value "${_pe}" "NGC_CLI_API_KEY")"
                [[ -n "${NGC_CLI_API_KEY}" ]] && export NGC_CLI_API_KEY
            fi
        fi
        download_dataset
    fi

    # Ensure enough videos for NUM_SENSORS (duplicate if needed). Only run when 0 < video_count < num_sensors.
    if [[ "${skip_synthetic_videos}" == true ]]; then
        log_info "Skipping synthetic video generation (--skip-synthetic-videos)"
    else
        local _videos_dir="${EVAL_VIDEOS_PATH:-}"
        local _num_sensors="${NUM_SENSORS:-1}"
        if ! [[ "${_num_sensors}" =~ ^[0-9]+$ ]] || [[ "${_num_sensors}" -lt 1 ]]; then
            _num_sensors=1
        fi
        if [[ -z "${_videos_dir}" ]] || [[ ! -d "${_videos_dir}" ]]; then
            log_info "EVAL_VIDEOS_PATH unset or not a directory, skipping synthetic video generation"
        else
            _videos_dir="$(realpath "${_videos_dir}")"
            if [[ -n "${LATENCY_SOURCE_VIDEO:-}" ]]; then
                log_section "Preparing Video Assets"
                log_kv "Latency source video" "${LATENCY_SOURCE_VIDEO}"
                log_kv "NUM_SENSORS" "${_num_sensors}"
                log_kv "Videos dir" "${_videos_dir}"
                stage_latency_source_videos_dir "${_videos_dir}"
            else
                local -a _sources=()
                local _f _ext
                for _f in "${_videos_dir}"/*; do
                    [[ -f "${_f}" ]] || continue
                    _ext="${_f##*.}"
                    _ext="${_ext,,}"
                    case " ${SYNTHETIC_VIDEO_EXTENSIONS} " in
                        *" ${_ext} "*) _sources+=("${_f}") ;;
                    esac
                done
                if [[ ${#_sources[@]} -gt 0 ]]; then
                    readarray -t _sources < <(printf "%s\n" "${_sources[@]}" | sort)
                fi
                local _current_count=${#_sources[@]}
                log_section "Preparing Video Assets"
                log_kv "Videos found"   "${_current_count}"
                log_kv "NUM_SENSORS"    "${_num_sensors}"
                log_kv "Videos dir"     "${_videos_dir}"
                if [[ ${_current_count} -gt 0 ]]; then
                    echo ""
                    for _f in "${_sources[@]}"; do
                        log_step "$(basename "${_f}")"
                    done
                fi
                echo ""
                if [[ ${_current_count} -eq 0 ]]; then
                    log_info "No video files found - skipping synthetic video generation"
                elif [[ ${_current_count} -ge ${_num_sensors} ]]; then
                    log_info "Sufficient videos (${_current_count} >= ${_num_sensors}) - no duplication needed"
                else
                    ensure_synthetic_videos
                fi
            fi
        fi
    fi

    # Run shared profile config before blueprint deploy (latency eval only)
    if [[ "${EVAL_TYPE}" == "latency" ]] && [[ "${skip_deploy}" == false ]] && [[ "${skip_latency_config_update}" != true ]]; then
        run_latency_profile_config "${blueprints_dir}" "compose"
    elif [[ "${EVAL_TYPE}" == "latency" ]] && [[ "${skip_deploy}" == false ]] && [[ "${skip_latency_config_update}" == true ]]; then
        log_info "Skipping latency profile config (--skip-latency-config-update)"
    fi

    # Deploy blueprint
    if [[ "${skip_deploy}" == false ]]; then
        deploy_blueprint "${profile}" "${e2e_eval}" "${env_files[@]}"
    else
        log_info "Skipping blueprint deployment (--skip-deploy)"
    fi

    if [[ "${synthetic_alerts_injection}" == true ]]; then
        stop_alerts_behavior_analytics_for_synthetic_injection
    fi

    # Evaluation runs in the locked host benchmark environment. Containers below
    # configure video streams and collect application metrics only.
    run_evaluation "up" "${profile}" "bp-configurator-init bp-configurator prometheus" "${env_files[@]}"
    wait_for_alerts_prometheus_target_ready
    wait_for_compose_video_setup_ready
    export SYNTHETIC_ALERTS_INJECTION="${synthetic_alerts_injection}"
    if [[ "${synthetic_alerts_injection}" == true ]]; then
        local sensor_ids_text
        sensor_ids_text="$(compose_latency_sensor_ids_from_videos)" || return 1
        PERF_SENSOR_IDS="$(printf '%s\n' "${sensor_ids_text}" | jq -R . | jq -s .)"
        export PERF_SENSOR_IDS
    fi
    "${PROFILE_CONFIG_PYTHON:?Locked benchmark Python is required}" "${PERF_LOCAL_ALERTS_RUNNER:?Local Alerts runner is required}"

    log_section "Done"
    log_success "Evaluation pipeline started successfully"
    log_step "Results  →  ${EVAL_RESULT_DIR_PATH:-./eval_results}"
    log_step "Teardown →  ${BOLD}${SCRIPT_NAME} --down${NC}"
    echo ""
}

main "$@"
