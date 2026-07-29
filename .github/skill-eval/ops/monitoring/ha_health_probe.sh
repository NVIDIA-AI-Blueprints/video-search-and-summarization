#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

readonly INVALID_AGE_SECONDS=999999999
readonly MAX_FUTURE_SKEW_SECONDS=60

utc_marker_epoch() {
    local value="$1"
    [[ "$value" =~ ^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$ ]] ||
        return 1
    date -u \
        --date="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]} ${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:${BASH_REMATCH[6]} UTC" \
        +%s
}

unit_metric() {
    local unit="$1"
    local tag="$2"
    local load_state
    local active_state
    local result
    local configured
    local active
    local result_success
    load_state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || true)"
    active_state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
    result="$(systemctl show "$unit" --property=Result --value 2>/dev/null || true)"
    [[ "$load_state" == "loaded" ]] && configured=1 || configured=0
    [[ "$active_state" == "active" ]] && active=1 || active=0
    if [[ "$configured" -eq 1 && "$result" == "success" ]]; then
        result_success=1
    else
        result_success=0
    fi
    printf 'vss_ha_unit,unit=%s configured=%di,active=%di,result_success=%di\n' \
        "$tag" \
        "$configured" \
        "$active" \
        "$result_success"
}

last_exit_age() {
    local unit="$1"
    local tag="$2"
    local timestamp
    local epoch
    local age
    local valid
    local now
    now="$(date +%s)"
    timestamp="$(
        systemctl show "$unit" \
            --property=ExecMainExitTimestamp \
            --value 2>/dev/null || true
    )"
    if [[ -n "$timestamp" ]] &&
       epoch="$(date --date="$timestamp" +%s 2>/dev/null)"; then
        age="$((now - epoch))"
        if ((age < -MAX_FUTURE_SKEW_SECONDS)); then
            age="$INVALID_AGE_SECONDS"
            valid=0
        else
            ((age < 0)) && age=0
            valid=1
        fi
    else
        age="$INVALID_AGE_SECONDS"
        valid=0
    fi
    printf 'vss_ha_last_run,unit=%s age_seconds=%di,valid=%di\n' \
        "$tag" "$age" "$valid"
}

marker_age() {
    local path="$1"
    local tag="$2"
    local value
    local epoch
    local age
    local valid
    local now
    now="$(date +%s)"
    if [[ -s "$path" ]]; then
        value="$(<"$path")"
        if epoch="$(utc_marker_epoch "$value" 2>/dev/null)"; then
            age="$((now - epoch))"
            if ((age < -MAX_FUTURE_SKEW_SECONDS)); then
                age="$INVALID_AGE_SECONDS"
                valid=0
            else
                ((age < 0)) && age=0
                valid=1
            fi
        else
            age="$INVALID_AGE_SECONDS"
            valid=0
        fi
    else
        age="$INVALID_AGE_SECONDS"
        valid=0
    fi
    printf 'vss_ha_evidence,unit=%s age_seconds=%di,valid=%di\n' \
        "$tag" "$age" "$valid"
}

patroni_cluster_metric() {
    local output
    if [[ ! -s /etc/vss-postgres-ha/patroni.yml ]] ||
       ! output="$(runuser -u postgres -- \
            patronictl -c /etc/vss-postgres-ha/patroni.yml \
            list --format json 2>/dev/null)"; then
        echo 'vss_ha_cluster leaders=0i,sync_standbys=0i,members=0i,healthy=0i'
        return
    fi
    python3 -c '
import json, sys
members = json.load(sys.stdin)
leaders = sum(member.get("Role") == "Leader" for member in members)
sync = sum(member.get("Role") == "Sync Standby" for member in members)
states_ok = all(
    member.get("State") in {"running", "streaming"} for member in members
)
healthy = int(len(members) == 3 and leaders == 1 and sync >= 1 and states_ok)
print(
    "vss_ha_cluster "
    f"leaders={leaders}i,sync_standbys={sync}i,"
    f"members={len(members)}i,healthy={healthy}i"
)
' <<<"$output"
}

etcd_quorum_metric() {
    local output
    local healthy
    if [[ ! -s /etc/vss-postgres-ha/patroni-etcd-client.key ]] ||
       ! output="$(
            ETCDCTL_API=3 etcdctl \
                --endpoints=https://10.203.142.1:2379,https://10.203.142.2:2379,https://10.203.142.3:2379 \
                --cacert=/etc/vss-postgres-ha/ca.crt \
                --cert=/etc/vss-postgres-ha/patroni-etcd-client.crt \
                --key=/etc/vss-postgres-ha/patroni-etcd-client.key \
                endpoint health --cluster 2>&1
        )"; then
        echo 'vss_etcd_quorum healthy_endpoints=0i,healthy=0i'
        return
    fi
    healthy="$(grep -c ' is healthy' <<<"$output" || true)"
    if [[ "$healthy" -eq 3 ]]; then
        printf 'vss_etcd_quorum healthy_endpoints=%di,healthy=1i\n' "$healthy"
    else
        printf 'vss_etcd_quorum healthy_endpoints=%di,healthy=0i\n' "$healthy"
    fi
}

unit_metric vss-postgres-ha.service patroni
unit_metric etcd.service etcd
unit_metric vss-postgres-ha-backup.timer backup_timer
unit_metric vss-postgres-ha-restore-test.timer restore_test_timer
unit_metric vss-postgres-ha-backup.service backup
unit_metric vss-postgres-ha-restore-test.service restore_test
last_exit_age vss-postgres-ha-backup.service backup
last_exit_age vss-postgres-ha-restore-test.service restore_test
marker_age /var/backups/vss-postgres-ha/logical/last-success backup
marker_age /var/backups/vss-postgres-ha/logical/last-restore-test restore_test
patroni_cluster_metric
etcd_quorum_metric
echo 'vss_ha_probe heartbeat=1i'
