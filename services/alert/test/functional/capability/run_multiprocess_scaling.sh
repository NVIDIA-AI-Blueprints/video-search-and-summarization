#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Multi-core scaling suite for alert_agent.processes, on the no-GPU simulators.
#
#   TS-030  rate ramp, 1 process vs N: CPU past one core, VLM latency flat
#   TS-031  killed child is restarted and its partitions resume
#   TS-032  no message loss under overload, with and without kafka.batch_commit
#   TS-033  crash/replay semantics per commit mode, measured across a hard kill
#
# Two harness properties are load-bearing and are asserted, not assumed:
#   * the topic must have >= PROCESSES partitions, otherwise effective
#     parallelism is min(processes, partitions) = 1 and the ramp shows nothing;
#   * the NIM stub must actually serve at the configured delay — a leftover stub
#     holding port 18081 silently serves the previous delay and invalidates
#     every latency number in the run.
#
# Usage: ./run_multiprocess_scaling.sh [--test TS-030] [--skip-setup] [--processes 4]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FUNCTIONAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
P1_ROOT="$FUNCTIONAL_ROOT/p1"
REPO_ROOT="$(cd "$FUNCTIONAL_ROOT/../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

export PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
BASE_CONFIG="$P1_ROOT/shared/config_base.yaml"
CONSUMER_GROUP_BASE="${CONSUMER_GROUP_BASE:-alert-bridge-vlm-group-p1}"
# Unique per invocation, not just per leg. A leg counter alone restarts at 1
# every run, so the second run on the same broker rejoins groups that already
# carry committed offsets and resumes from them - auto_offset_reset=latest only
# applies to a group that is genuinely new.
RUN_ID="$(date +%s)"
CONSUMER_GROUP="${CONSUMER_GROUP_BASE}-${RUN_ID}"
LEG=0
KAFKA_CONTAINER="alert-agent-kafka-test"
METRICS_URL="http://127.0.0.1:9081/metrics"
RESULTS_DIR="$PID_DIR/scaling_results"
INJECTOR="$SCRIPT_DIR/incident_stream_publisher.py"
CPU_SAMPLER="$SCRIPT_DIR/process_tree_cpu.py"
VERDICT="$SCRIPT_DIR/ts030_verdict.py"
AB_PATTERN="enhance_alert_with_vlm.py"
SIM_PATTERNS="${SIM_PATTERNS:-elastic_sim.py,vst_sim.py,nim_stub_server.py}"
SIM_SATURATED_PCT="${SIM_SATURATED_PCT:-85}"

PROCESSES="${PROCESSES:-4}"
PARTITIONS="${PARTITIONS:-8}"
STUB_DELAY="${STUB_DELAY:-0.2}"
VLM_CAP="${VLM_CAP:-60}"
RAMP_RATES="${RAMP_RATES:-10 20 40 80 160}"
RAMP_SECONDS="${RAMP_SECONDS:-60}"
CPU_SKIP_SECONDS="${CPU_SKIP_SECONDS:-8}"

ONLY_TEST=""
SKIP_SETUP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --test) ONLY_TEST="$2"; shift 2 ;;
        --skip-setup) SKIP_SETUP=1; shift ;;
        --processes) PROCESSES="$2"; shift 2 ;;
        --partitions) PARTITIONS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
done

mkdir -p "$PID_DIR" "$RESULTS_DIR"

PASS_COUNT=0
FAIL_COUNT=0
declare -a RESULTS=()

record_result() {
    local name="$1" status="$2" detail="$3"
    RESULTS+=("$status  $name  $detail")
    if [ "$status" = "PASS" ]; then PASS_COUNT=$((PASS_COUNT+1)); else FAIL_COUNT=$((FAIL_COUNT+1)); fi
    print_status "$([ "$status" = "PASS" ] && echo ok || echo fail)" "$name: $status — $detail"
}

# ─── Stack management ────────────────────────────────────────────────────────

ensure_kafka() {
    if nc -z 127.0.0.1 9092 2>/dev/null; then return 0; fi
    print_status "wait" "Starting Kafka container..."
    docker rm -f "$KAFKA_CONTAINER" 2>/dev/null || true
    docker run -d --name "$KAFKA_CONTAINER" -p 9092:9092 \
        -e KAFKA_BROKER_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller -e KAFKA_NODE_ID=1 \
        -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
        -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
        -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093 \
        -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
        -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
        -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
        -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
        -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk \
        confluentinc/cp-kafka:7.5.0 >/dev/null
    local waited=0
    while [ $waited -lt 60 ] && ! nc -z 127.0.0.1 9092 2>/dev/null; do sleep 1; waited=$((waited+1)); done
    sleep 5
}

topic_partitions() {
    docker exec "$KAFKA_CONTAINER" kafka-topics --describe \
        --bootstrap-server localhost:9092 --topic "$1" 2>/dev/null \
        | awk -F'PartitionCount: ' 'NF>1 {split($2, a, /[ \t]/); print a[1]; exit}'
}

ensure_partitions() {
    local topic="$1" wanted="$2"
    local have; have=$(topic_partitions "$topic")
    if [ -z "$have" ]; then
        docker exec "$KAFKA_CONTAINER" kafka-topics --create --bootstrap-server localhost:9092 \
            --topic "$topic" --partitions "$wanted" --replication-factor 1 >/dev/null 2>&1
    elif [ "$have" -lt "$wanted" ]; then
        docker exec "$KAFKA_CONTAINER" kafka-topics --alter --bootstrap-server localhost:9092 \
            --topic "$topic" --partitions "$wanted" >/dev/null 2>&1
    fi
    have=$(topic_partitions "$topic")
    if [ "${have:-0}" -lt "$wanted" ]; then
        print_status "fail" "$topic has ${have:-0} partitions, need >= $wanted"
        return 1
    fi
    print_status "ok" "$topic partitions: $have"
}

# Wait on the port rather than sleeping: a simulator that dies on import (for
# example Flask missing because the venv was not on PATH) otherwise surfaces
# much later as a misleading "connection refused" from Alert Bridge startup.
wait_for_sim() {
    local name="$1" url="$2" log="$3" waited=0
    while [ $waited -lt 30 ]; do
        curl -sf "$url" >/dev/null 2>&1 && { print_status "ok" "$name ready"; return 0; }
        sleep 1; waited=$((waited+1))
    done
    print_status "fail" "$name did not become ready at $url"
    tail -20 "$log" 2>/dev/null || true
    return 1
}

ensure_stack() {
    ensure_kafka
    ensure_partitions "$TOPIC" "$PARTITIONS" || exit 1
    ensure_partitions mdx-alerts 1 || exit 1

    if ! curl -sf http://127.0.0.1:9200/health >/dev/null 2>&1; then
        python3 "$REPO_ROOT/test/sim_scripts/elastic/elastic_sim.py" > "$PID_DIR/elastic_sim.log" 2>&1 &
        echo $! > "$PID_DIR/elastic_sim.pid"
    fi
    if ! curl -sf http://127.0.0.1:30888/status >/dev/null 2>&1; then
        python3 "$REPO_ROOT/test/sim_scripts/vst/vst_sim.py" > "$PID_DIR/vst_sim.log" 2>&1 &
        echo $! > "$PID_DIR/vst_sim.pid"
    fi
    if ! curl -sf http://127.0.0.1:8080/models >/dev/null 2>&1; then
        python3 "$REPO_ROOT/test/sim_scripts/vss/vss_sim.py" > "$PID_DIR/vss_sim.log" 2>&1 &
        echo $! > "$PID_DIR/vss_sim.pid"
    fi

    wait_for_sim "Elastic sim" http://127.0.0.1:9200/health "$PID_DIR/elastic_sim.log" || exit 1
    wait_for_sim "VST sim" http://127.0.0.1:30888/status "$PID_DIR/vst_sim.log" || exit 1
    wait_for_sim "VSS sim" http://127.0.0.1:8080/models "$PID_DIR/vss_sim.log" || exit 1
}

# Kill by pattern, not by PID file: a stub orphaned by an earlier run keeps
# port 18081 and the replacement dies with EADDRINUSE while every request is
# still served at the *old* delay.
start_nim_stub() {
    local delay="$1"
    pkill -f "nim_stub_server.py" 2>/dev/null || true
    rm -f "$PID_DIR/nim_sim.pid"
    local waited=0
    while [ $waited -lt 15 ] && nc -z 127.0.0.1 18081 2>/dev/null; do sleep 1; waited=$((waited+1)); done
    if nc -z 127.0.0.1 18081 2>/dev/null; then
        print_status "fail" "port 18081 still held; refusing to run with a stale NIM stub"
        return 1
    fi

    NIM_STUB_DELAY_SECONDS="$delay" \
        python3 "$REPO_ROOT/test/sim_scripts/nim/nim_stub_server.py" > "$PID_DIR/nim_sim.log" 2>&1 &
    echo $! > "$PID_DIR/nim_sim.pid"
    waited=0
    while [ $waited -lt 15 ] && ! nc -z 127.0.0.1 18081 2>/dev/null; do sleep 1; waited=$((waited+1)); done
    nc -z 127.0.0.1 18081 2>/dev/null || { print_status "fail" "NIM stub did not bind 18081"; return 1; }
}

purge_stale_consumer_groups() {
    # Groups from earlier runs keep their committed offsets and would otherwise
    # accumulate on the broker. Kafka refuses to delete a group that still has
    # members, so this cannot disturb anything running.
    local groups purged=0 group
    groups=$(docker exec "$KAFKA_CONTAINER" kafka-consumer-groups \
        --bootstrap-server localhost:9092 --list 2>/dev/null \
        | grep "^${CONSUMER_GROUP_BASE}" || true)
    [ -z "$groups" ] && return 0
    while read -r group; do
        [ -z "$group" ] && continue
        docker exec "$KAFKA_CONTAINER" kafka-consumer-groups \
            --bootstrap-server localhost:9092 --delete --group "$group" >/dev/null 2>&1 \
            && purged=$((purged + 1))
    done <<< "$groups"
    [ "$purged" -gt 0 ] && print_status "ok" "purged $purged stale consumer group(s) from earlier runs"
    return 0
}

# ─── Alert Bridge lifecycle ──────────────────────────────────────────────────

build_config() {
    # build_config OUT PROCESSES VLM_CAP BATCH_COMMIT
    python3 - "$BASE_CONFIG" "$1" "$2" "$3" "$4" "$CONSUMER_GROUP" <<'PY'
import sys, yaml
src, dst, processes, vlm_cap, batch_commit, group_id = sys.argv[1:7]
with open(src) as f:
    cfg = yaml.safe_load(f)
aa = cfg.setdefault('alert_agent', {})
aa['processes'] = int(processes)
aa['pipeline_mode'] = 'event_loop'
aa['num_workers'] = 2
aa['async_dispatch_workers'] = 2
aa['async_dispatch_max_in_flight'] = 200
aa['include_latency_info'] = True
aio = aa.setdefault('async_io', {})
aio['max_vlm_concurrent'] = int(vlm_cap)
aio['max_vst_concurrent'] = int(vlm_cap)
k = cfg.setdefault('kafka', {})
k['poll_timeout'] = 50
k['max_poll_records'] = 50
k['batch_commit'] = batch_commit == 'true'
cfg.setdefault('vlm', {})['request_timeout'] = 120
cfg.setdefault('event_bridge', {}).setdefault('kafka_source', {})['group_id'] = group_id
with open(dst, 'w') as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
}

stop_ab() {
    stop_alert_bridge_local "$PID_DIR"
    # Children run with daemon=False and outlive a hard-killed parent, keeping
    # consumer-group membership and blocking the next offset reset.
    pkill -9 -f "$AB_PATTERN" 2>/dev/null || true
    local waited=0
    while [ $waited -lt 15 ] && { nc -z 127.0.0.1 9080 2>/dev/null || nc -z 127.0.0.1 9081 2>/dev/null; }; do
        sleep 1; waited=$((waited+1))
    done
}

# The parent logs the readiness line *before* forking, and children can spend
# tens of seconds contending on Elasticsearch inside their constructor. Gating
# on the parent alone starts the injector before the consumers have joined,
# and with auto_offset_reset=latest those messages are simply never seen.
start_ab() {
    local config="$1" expected_children="${2:-1}"
    local config_dir; config_dir="$(cd "$(dirname "$config")" && pwd)"
    ALERT_AGENT_CONFIG_DIR="$config_dir" PROMETHEUS_METRICS_ENABLED=true \
        python3 "$REPO_ROOT/enhance_alert_with_vlm.py" --config "$config" > "$PID_DIR/alert_bridge.log" 2>&1 &
    echo $! > "$PID_DIR/alert_bridge.pid"
    local waited=0 ready
    while [ $waited -lt 240 ]; do
        # The readiness line is now emitted only after every child has joined
        # the consumer group, so it needs no settle sleep behind it. If a run
        # starts dropping the first records again, that guarantee is what
        # broke -- do not paper over it with a sleep here.
        if grep -q "Starting anomaly processing loop" "$PID_DIR/alert_bridge.log" 2>/dev/null; then
            ready=$(grep -c "Pipeline process .* ready" "$PID_DIR/alert_bridge.log" 2>/dev/null)
            if [ "$expected_children" -gt 1 ]; then
                print_status "ok" "$ready/$expected_children pipeline children ready after ${waited}s"
            fi
            return 0
        fi
        if ! kill -0 "$(cat "$PID_DIR/alert_bridge.pid")" 2>/dev/null; then
            print_status "fail" "Alert Bridge exited during startup"
            tail -10 "$PID_DIR/alert_bridge.log" || true
            return 1
        fi
        sleep 1; waited=$((waited+1))
    done
    print_status "fail" "Alert Bridge did not reach processing loop with $expected_children children ready"
    return 1
}

# pgrep cannot separate the pipeline children from the parent and the FastAPI
# child: fork leaves argv unchanged, so all of them match AB_PATTERN. Read the
# pids the supervisor logs instead, and keep only the ones still alive.
pipeline_child_pids() {
    local pid
    grep -o "Pipeline process [0-9]* starting (pid=[0-9]*)" "$PID_DIR/alert_bridge.log" 2>/dev/null \
        | grep -o "pid=[0-9]*" | cut -d= -f2 | while read -r pid; do
        kill -0 "$pid" 2>/dev/null && echo "$pid"
    done | tr '\n' ' '
}

prepare_run() {
    # prepare_run PROCESSES BATCH_COMMIT NIM_DELAY [VLM_CAP]
    local cap="${4:-$VLM_CAP}"
    local cfg="$PID_DIR/scaling_config.yaml"
    stop_ab
    # A fresh group per leg instead of resetting offsets. The reset silently
    # fails while the group still has active members, and the next leg then
    # inherits the previous leg's backlog - which lands in the first sample of
    # the ramp, i.e. exactly the baseline the flat-latency gate divides by.
    # With auto_offset_reset=latest a new group starts at the end regardless.
    LEG=$((LEG + 1))
    CONSUMER_GROUP="${CONSUMER_GROUP_BASE}-${RUN_ID}-${LEG}"
    start_nim_stub "$3" || return 1
    reset_es
    build_config "$cfg" "$1" "$cap" "$2"
    start_ab "$cfg" "$1" || return 1
    local waited=0
    while [ $waited -lt 20 ]; do
        local infl; infl=$(nim_stub_stat in_flight)
        [ "${infl:-1}" = "0" ] && break
        sleep 1; waited=$((waited+1))
    done
    nim_stub_reset
    print_status "info" "AB up — processes=$1 batch_commit=$2 stub_delay=$3 max_vlm_concurrent=$cap"
}

reset_es() {
    local today; today=$(date -u +%Y-%m-%d)
    curl -sf -X DELETE "$ES_HOST/mdx-vlm-incidents-$today" >/dev/null 2>&1 || true
    curl -sf -X DELETE "$ES_HOST/ab-confirmed-verdicts" >/dev/null 2>&1 || true
}

# ─── Signal helpers ──────────────────────────────────────────────────────────

prom_value() {
    curl -s "$METRICS_URL" 2>/dev/null | awk -v m="$1" '$1==m {v=$2} END{if(v=="")v=0; print v}'
}

vlm_mean_over() {
    # vlm_mean_over RATE DURATION PREFIX → "mean cpu_avg cpu_max"
    local rate="$1" duration="$2" prefix="$3"
    local s0 c0 s1 c1 cpu
    s0=$(prom_value alert_bridge_vlm_duration_seconds_sum)
    c0=$(prom_value alert_bridge_vlm_duration_seconds_count)
    python3 "$CPU_SAMPLER" "$AB_PATTERN" 2 "$duration" "$CPU_SKIP_SECONDS" > "$RESULTS_DIR/cpu_$prefix.txt" 2>/dev/null &
    local sampler=$!
    # Watch the simulators too. They are single-process Flask servers, so each
    # is GIL-bound to ~1 core; once one saturates the number being reported
    # describes the harness, not Alert Bridge.
    python3 "$CPU_SAMPLER" "$SIM_PATTERNS" 2 "$duration" "$CPU_SKIP_SECONDS" > "$RESULTS_DIR/sims_$prefix.txt" 2>/dev/null &
    local sim_sampler=$!
    python3 "$INJECTOR" --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
        --num-sensors 16 --rate "$rate" --duration "$duration" \
        --unique --sensor-prefix "$prefix" > "$RESULTS_DIR/injector_$prefix.log" 2>&1
    wait $sampler || true
    wait $sim_sampler || true
    sleep 10   # let in-flight calls finish observing
    s1=$(prom_value alert_bridge_vlm_duration_seconds_sum)
    c1=$(prom_value alert_bridge_vlm_duration_seconds_count)
    cpu=$(cut -d' ' -f1,2 "$RESULTS_DIR/cpu_$prefix.txt" 2>/dev/null || echo "0 0")
    echo "$(python3 -c "s=$s1-$s0;c=$c1-$c0;print(f'{s/c:.3f}' if c>0 else '0')") $cpu"
}

sample_gauge_max() {
    # sample_gauge_max METRIC SECONDS INTERVAL OUTFILE
    local metric="$1" seconds="$2" interval="$3" out="$4"
    local end=$(( $(date +%s) + seconds ))
    : > "$out"
    while [ "$(date +%s)" -lt "$end" ]; do
        prom_value "$metric" >> "$out"
        sleep "$interval"
    done
}

gauge_max_from() {
    python3 -c "
import sys
values = []
for line in open('$1'):
    try:
        values.append(float(line.strip()))
    except ValueError:
        pass
print(f'{max(values):.1f}' if values else '0.0')"
}

assert_stub_delay() {
    # The observed mean must track the configured stub delay; if it does not,
    # a stale stub is serving and no latency number in this run is meaningful.
    local observed="$1" configured="$2"
    python3 -c "
import sys
observed, configured = float('$observed'), float('$configured')
sys.exit(0 if configured * 0.8 <= observed <= configured * 3.0 else 1)"
}


# ─── TS-030: rate ramp, 1 process vs N ──────────────────────────────────────
ts_030() {
    echo ""; echo "=== TS-030: rate ramp — 1 process vs $PROCESSES processes ==="
    local single_means=() multi_means=() single_cpu=() multi_cpu=() rates=()
    local rate detail

    for variant in 1 "$PROCESSES"; do
        prepare_run "$variant" false "$STUB_DELAY" || { record_result TS-030 FAIL "AB startup (processes=$variant)"; return; }
        for rate in $RAMP_RATES; do
            read -r mean cavg cmax <<< "$(vlm_mean_over "$rate" "$RAMP_SECONDS" "P${variant}R${rate}")"
            print_status "info" "processes=$variant rate=$rate/s vlm_mean=${mean}s cpu_avg=${cavg}% cpu_max=${cmax}%"
            if [ "$variant" = "1" ]; then
                single_means+=("$mean"); single_cpu+=("$cavg")
            else
                multi_means+=("$mean"); multi_cpu+=("$cavg")
            fi
            [ "$variant" = "1" ] && rates+=("$rate")
        done
    done

    local last=$(( ${#single_means[@]} - 1 ))
    if [ "$last" -lt 1 ]; then record_result TS-030 FAIL "need at least two rates in RAMP_RATES"; return; fi

    if ! assert_stub_delay "${single_means[0]}" "$STUB_DELAY"; then
        record_result TS-030 FAIL "baseline vlm_mean=${single_means[0]}s does not match stub delay ${STUB_DELAY}s. Either a stale NIM stub is serving an older delay, or the ramp has no point below the single-process knee - the first entry in RAMP_RATES must be low enough that one process is still idle there."
        return
    fi

    # The two halves of the claim live at different points of the ramp and
    # cannot be asserted at one rate. "Stays flat" only holds below the knee;
    # "uses more than one core" only shows as the knee is approached. Gating
    # both at the top rate made the test unsatisfiable at every rate: by the
    # time N processes crossed one core their latency had already left the
    # flat band. So each half is checked where it is meaningful.
    #
    #   break rate  - the first rate at which the single process inflates.
    #                 There, N processes must still be flat and faster.
    #   whole ramp  - N processes must exceed one core somewhere in it.
    local csv_rates csv_smean csv_mmean csv_scpu csv_mcpu
    csv_rates=$(IFS=,; echo "${rates[*]}")
    csv_smean=$(IFS=,; echo "${single_means[*]}")
    csv_mmean=$(IFS=,; echo "${multi_means[*]}")
    csv_scpu=$(IFS=,; echo "${single_cpu[*]}")
    csv_mcpu=$(IFS=,; echo "${multi_cpu[*]}")

    if python3 "$VERDICT" "$RESULTS_DIR" "$SIM_SATURATED_PCT" "$PROCESSES" \
            "$csv_rates" "$csv_smean" "$csv_mmean" "$csv_scpu" "$csv_mcpu" \
            > "$RESULTS_DIR/ts030_verdict.txt" 2>&1; then
        detail="$(cat "$RESULTS_DIR/ts030_verdict.txt")"
        record_result TS-030 PASS "$detail"
    else
        record_result TS-030 FAIL "$(cat "$RESULTS_DIR/ts030_verdict.txt" 2>/dev/null || echo 'assertion error')"
    fi
}

# ─── TS-031: killed child is restarted and its partitions resume ────────────
ts_031() {
    echo ""; echo "=== TS-031: child crash → supervisor restarts → partitions resume ==="
    prepare_run "$PROCESSES" false 1 || { record_result TS-031 FAIL "AB startup"; return; }

    local before after victim
    before=$(pipeline_child_pids)
    local before_count; before_count=$(echo "$before" | wc -w)
    if [ "$before_count" -ne "$PROCESSES" ]; then
        record_result TS-031 FAIL "expected $PROCESSES pipeline children, found $before_count ($before)"
        return
    fi

    victim=$(echo "$before" | tr ' ' '\n' | grep -v '^$' | tail -1)
    kill -9 "$victim" 2>/dev/null || true

    local waited=0 restarted=0
    while [ $waited -lt 60 ]; do
        after=$(pipeline_child_pids)
        if [ "$(echo "$after" | wc -w)" -eq "$before_count" ] && ! echo "$after" | grep -qw "$victim"; then
            restarted=1; break
        fi
        sleep 2; waited=$((waited+2))
    done

    if [ "$restarted" -ne 1 ]; then
        record_result TS-031 FAIL "child $victim not replaced within ${waited}s"
        return
    fi
    if ! grep -q "Pipeline process .* exited" "$PID_DIR/alert_bridge.log"; then
        record_result TS-031 FAIL "supervisor did not log the child exit"
        return
    fi

    # Every partition must be served again: inject across all of them and
    # require the full set to land in Elasticsearch.
    sleep 10
    python3 "$INJECTOR" --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
        --num-sensors "$PARTITIONS" --rate 4 --duration 30 --unique \
        --sensor-prefix TS031 > "$RESULTS_DIR/injector_TS031.log" 2>&1
    local produced; produced=$(grep -o "DONE sent=[0-9]*" "$RESULTS_DIR/injector_TS031.log" | grep -o "[0-9]*")

    waited=0
    while [ $waited -lt 120 ]; do
        local lag; lag=$(kafka_consumer_lag "$CONSUMER_GROUP")
        [ "${lag:-1}" -eq 0 ] 2>/dev/null && break
        sleep 5; waited=$((waited+5))
    done
    sleep 10

    local docs; docs=$(count_es_docs "$ES_HOST")
    local detail="killed=$victim restarted_after=${waited}s produced=$produced es_docs=$docs"
    if [ "${docs:-0}" -eq "${produced:-0}" ] && [ "${produced:-0}" -gt 0 ]; then
        record_result TS-031 PASS "$detail"
    else
        record_result TS-031 FAIL "$detail"
    fi
}

# ─── TS-032: no message loss under overload, both commit modes ──────────────
ts_032() {
    echo ""; echo "=== TS-032: no loss under overload (batch_commit off then on) ==="
    local detail="" failed=0
    # A deliberately small per-process cap: the offered load then saturates the
    # aggregate rather than a single process, which is what makes the
    # processes x cap composition observable at all.
    local per_process_cap="${TS032_VLM_CAP:-5}"
    for batch in false true; do
        prepare_run "$PROCESSES" "$batch" 1 "$per_process_cap" || { failed=1; detail="$detail batch_commit=$batch: startup;"; break; }

        local gauge_samples="$RESULTS_DIR/ts032_vlm_inflight_$batch.txt"
        sample_gauge_max alert_bridge_event_loop_vlm_in_flight 70 1 "$gauge_samples" &
        local sampler=$!
        python3 "$INJECTOR" --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
            --num-sensors "$PARTITIONS" --rate 60 --duration 60 --unique \
            --sensor-prefix "TS032B${batch}" > "$RESULTS_DIR/injector_TS032_$batch.log" 2>&1
        wait $sampler || true
        local produced; produced=$(grep -o "DONE sent=[0-9]*" "$RESULTS_DIR/injector_TS032_$batch.log" | grep -o "[0-9]*")

        # The gauge is livesum, so it reports the instance-wide total. This is
        # the number the VLM backend actually sees, and the one that silently
        # becomes processes x cap when an operator enables multi-process
        # without resizing the cap.
        local vlm_max aggregate_cap
        vlm_max=$(gauge_max_from "$gauge_samples")
        aggregate_cap=$(( PROCESSES * per_process_cap ))

        print_status "wait" "Draining backlog (produced=$produced)..."
        local waited=0
        while [ $waited -lt 420 ]; do
            local lag infl
            lag=$(kafka_consumer_lag "$CONSUMER_GROUP")
            infl=$(prom_value alert_bridge_dispatch_in_flight)
            if [ "${lag:-1}" -eq 0 ] 2>/dev/null && python3 -c "import sys; sys.exit(0 if float('$infl')==0 else 1)"; then
                break
            fi
            sleep 10; waited=$((waited+10))
        done
        sleep 5

        local after_dedup docs
        after_dedup=$(prom_value alert_bridge_events_after_dedup_total)
        docs=$(count_es_docs "$ES_HOST")
        detail="$detail batch_commit=$batch produced=$produced after_dedup=$after_dedup es_docs=$docs vlm_in_flight_max=$vlm_max/$aggregate_cap;"

        # --unique means survivor rate == injection rate, so a shortfall is
        # loss. Batched commit may replay, so duplicates are allowed above the
        # produced count while a shortfall is not. The aggregate gauge must
        # respect processes x cap and must exceed a single process's cap,
        # otherwise the caps are not composing the way the docs claim.
        if ! python3 -c "
import sys
produced, after_dedup, docs = $produced, float('$after_dedup'), int('$docs')
vlm_max, cap, processes = float('$vlm_max'), $per_process_cap, $PROCESSES
ok = (produced > 0 and after_dedup >= produced and docs >= produced
      and vlm_max <= processes * cap
      and (processes == 1 or vlm_max > cap))
sys.exit(0 if ok else 1)"; then
            failed=1
        fi
    done

    if [ "$failed" -eq 0 ]; then
        record_result TS-032 PASS "$detail"
    else
        record_result TS-032 FAIL "$detail"
    fi
}

drain_to_idle() {
    local budget="${1:-180}" waited=0
    while [ $waited -lt "$budget" ]; do
        local lag infl
        lag=$(kafka_consumer_lag "$CONSUMER_GROUP")
        infl=$(prom_value alert_bridge_dispatch_in_flight)
        if [ "${lag:-1}" -eq 0 ] 2>/dev/null && python3 -c "import sys; sys.exit(0 if float('$infl')==0 else 1)"; then
            sleep 5; return 0
        fi
        sleep 5; waited=$((waited+5))
    done
    return 1
}

wait_for_child_restart() {
    local victim="$1" waited=0 live
    while [ $waited -lt 60 ]; do
        live=$(pipeline_child_pids)
        if [ "$(echo "$live" | wc -w)" -eq "$PROCESSES" ] && ! echo "$live" | grep -qw "$victim"; then
            return 0
        fi
        sleep 2; waited=$((waited+2))
    done
    return 1
}

# ─── TS-033: crash/replay semantics per commit mode ─────────────────────────
#
# What this pins down: batched commit does NOT make the pipeline at-least-once.
# The batch is flushed in get_consumed_messages' finally, before read_data()
# returns and before anything is dispatched, so a message that reached the
# worker pool is already committed under either setting and dies with its
# process. The redelivery window batching opens is one poll batch, entered only
# if the crash lands inside the poll loop itself.
#
# Deterministic and therefore asserted: batch_commit=false never replays;
# everything that clears dedup is persisted; the restarted child resumes its
# partitions. The per-mode loss counts are measured evidence for the window
# above, not pass criteria — the crash lands where it lands.
ts_033() {
    echo ""; echo "=== TS-033: crash/replay semantics — kill mid-flight, both commit modes ==="
    local detail="" failed=0

    for batch in false true; do
        prepare_run "$PROCESSES" "$batch" 2 || { failed=1; detail="$detail batch_commit=$batch: startup;"; break; }

        local d0; d0=$(prom_value alert_bridge_events_after_dedup_total)
        python3 "$INJECTOR" --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
            --num-sensors "$PARTITIONS" --rate 20 --duration 20 --unique \
            --sensor-prefix "TS033K$batch" > "$RESULTS_DIR/injector_TS033K_$batch.log" 2>&1 &
        local inj=$!

        sleep 10
        local victim; victim=$(pipeline_child_pids | tr ' ' '\n' | grep -v '^$' | tail -1)
        if [ -z "$victim" ]; then
            failed=1; detail="$detail batch_commit=$batch: no pipeline child to kill;"
            kill $inj 2>/dev/null || true
            continue
        fi
        kill -9 "$victim" 2>/dev/null || true
        wait $inj || true

        local produced; produced=$(grep -o "DONE sent=[0-9]*" "$RESULTS_DIR/injector_TS033K_$batch.log" | grep -o "[0-9]*")
        if ! wait_for_child_restart "$victim"; then
            failed=1; detail="$detail batch_commit=$batch: child $victim not replaced;"
            continue
        fi
        drain_to_idle 240 || true

        local d1 survivors docs
        d1=$(prom_value alert_bridge_events_after_dedup_total)
        survivors=$(python3 -c "print(int(round(float('$d1') - float('$d0'))))")
        docs=$(count_es_docs "$ES_HOST")

        # Partitions must be served again after the restart.
        python3 "$INJECTOR" --bootstrap "$BOOTSTRAP" --topic "$TOPIC" \
            --num-sensors "$PARTITIONS" --rate 4 --duration 20 --unique \
            --sensor-prefix "TS033R$batch" > "$RESULTS_DIR/injector_TS033R_$batch.log" 2>&1
        local produced2; produced2=$(grep -o "DONE sent=[0-9]*" "$RESULTS_DIR/injector_TS033R_$batch.log" | grep -o "[0-9]*")
        drain_to_idle 180 || true
        local d2 recovered
        d2=$(prom_value alert_bridge_events_after_dedup_total)
        recovered=$(python3 -c "print(int(round(float('$d2') - float('$d1'))))")

        local lost=$(( produced - survivors ))
        local in_flight_lost=$(( survivors - docs ))
        detail="$detail batch_commit=$batch produced=$produced survivors=$survivors never_admitted=$lost in_flight_lost=$in_flight_lost docs=$docs recovery=$recovered/$produced2;"
        print_status "info" "batch_commit=$batch killed=$victim lost_in_flight=$lost recovery=$recovered/$produced2"

        if ! python3 -c "
import sys
batch = '$batch' == 'true'
produced, survivors, docs = $produced, $survivors, $docs
recovered, produced2 = $recovered, $produced2
# events_after_dedup counts admission to dispatch, not completion, so a child
# killed mid-flight always leaves docs < survivors. That gap is the in-flight
# loss this test exists to measure - it is reported, not gated.
ok = (produced > 0
      and docs <= survivors            # cannot persist more than was admitted
      and produced2 > 0
      and recovered == produced2)      # restarted child serves its partitions
if not batch:
    ok = ok and survivors <= produced  # at-most-once: nothing is ever replayed
sys.exit(0 if ok else 1)"; then
            failed=1
        fi
    done

    if [ "$failed" -eq 0 ]; then
        record_result TS-033 PASS "$detail"
    else
        record_result TS-033 FAIL "$detail"
    fi
}

# ─── Main ────────────────────────────────────────────────────────────────────

echo "=== Multi-process scaling suite (no-GPU sim harness) ==="
echo "    processes=$PROCESSES partitions=$PARTITIONS stub_delay=${STUB_DELAY}s max_vlm_concurrent=$VLM_CAP"

if [ ! -r /proc/self/stat ]; then
    echo "This suite reads CPU accounting from /proc and requires Linux."
    exit 2
fi

# Must precede ensure_stack: the simulators are launched with whatever python3
# is on PATH, and the system interpreter typically lacks Flask.
# A clean clone has no venv. Accept one from any of the usual places, or an
# explicit AB_VENV, and say so plainly rather than silently running the system
# interpreter - which lacks Flask and takes the simulators down on import.
for _venv in "${AB_VENV:-}" "$REPO_ROOT/venv" "$REPO_ROOT/.venv"; do
    if [ -n "$_venv" ] && [ -x "$_venv/bin/python3" ]; then
        export PATH="$_venv/bin:$PATH"
        break
    fi
done
if ! python3 -c "import flask, yaml, confluent_kafka" 2>/dev/null; then
    echo "python3 on PATH is missing test dependencies (flask, pyyaml, confluent-kafka)."
    echo "Create a venv from services/alert/requirements.txt and re-run, or point AB_VENV at one."
    exit 2
fi

if [ "$SKIP_SETUP" -eq 0 ]; then
    ensure_stack
fi

# Runs unconditionally: the backlog problem shows up precisely when re-running
# against a broker left over from a previous session, which is also when
# --skip-setup is most likely to be used.
purge_stale_consumer_groups

for ts in ts_030 ts_031 ts_032 ts_033; do
    ts_id="TS-${ts#ts_}"
    if [ -n "$ONLY_TEST" ] && [ "$ONLY_TEST" != "$ts_id" ]; then
        continue
    fi
    $ts
done

stop_ab
start_nim_stub 0 || true

echo ""
echo "=== Scaling results ==="
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "  Passed: $PASS_COUNT  Failed: $FAIL_COUNT"
[ "$FAIL_COUNT" -eq 0 ]
