#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy a three-node PostgreSQL/Patroni/etcd quorum on the first three of the
# eight Brev coordinator machines. All eight machines join a WireGuard overlay
# and retain their four dormant GitHub runners.
set -euo pipefail
umask 077

repository="${GITHUB_REPOSITORY:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
state_dir="${POSTGRES_HA_STATE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/vss-skill-eval/postgres-ha}"
apply=false
confirm_reset=false
publish_github_secret=false

usage() {
    cat >&2 <<'EOF'
Usage:
  deploy-postgres-ha.sh
  deploy-postgres-ha.sh --apply --confirm-reset-local-postgres [--publish-github-secret]

The default is a read-only preflight. Generated CA material, passwords, and
DSNs are stored outside the repository under POSTGRES_HA_STATE_DIR.
EOF
}

while (($#)); do
    case "$1" in
        --apply) apply=true; shift ;;
        --confirm-reset-local-postgres) confirm_reset=true; shift ;;
        --publish-github-secret) publish_github_secret=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
    echo "Invalid GITHUB_REPOSITORY: $repository" >&2
    exit 2
}
if [[ "$apply" != true && ("$confirm_reset" == true || "$publish_github_secret" == true) ]]; then
    echo "Mutation flags require --apply" >&2
    exit 2
fi
if [[ "$apply" == true && "$confirm_reset" != true ]]; then
    echo "--confirm-reset-local-postgres is required with --apply" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
wireguard_key_script="$script_dir/prepare-wireguard-key.sh"
wireguard_install_script="$script_dir/install-wireguard-node.sh"
postgres_install_script="$script_dir/install-postgres-ha-node.sh"
patroni_unit="$script_dir/vss-postgres-ha.service"
lease_schema="$repo_root/.github/skill-eval/postgres-gpu-leases.sql"
for required in \
    "$wireguard_key_script" \
    "$wireguard_install_script" \
    "$postgres_install_script" \
    "$patroni_unit" \
    "$lease_schema"; do
    [[ -f "$required" ]] || { echo "Missing deployment input: $required" >&2; exit 1; }
done

for command in curl git openssl scp ssh; do
    command -v "$command" >/dev/null || {
        echo "Missing local prerequisite: $command" >&2
        exit 1
    }
done
if [[ "$publish_github_secret" == true ]]; then
    command -v gh >/dev/null
    gh auth status >/dev/null
fi

coordinators=()
for index in $(seq 1 8); do
    coordinators+=("vss-skill-validator-distributed-${index}")
done

echo "Preflighting eight existing Brev coordinators..."
for index in "${!coordinators[@]}"; do
    host="${coordinators[$index]}"
    expected_index="$((index + 1))"
    ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" \
        "test \$(nproc) -ge 8 && test \$(awk '/MemTotal/ {print int(\$2/1024/1024)}' /proc/meminfo) -ge 30 && test \$(df -B1 --output=avail / | awk 'NR==2 {print \$1}') -ge 107374182400"
    echo "READY: $host (node $expected_index)"
done

existing_summary="$(
    ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo -u postgres psql -d skill_eval_leases -Atqc 'select count(*) from public.gpu_workers' 2>/dev/null || echo 0"
)"
[[ "$existing_summary" =~ ^[0-9]+$ ]] || {
    echo "Could not safely inventory the existing local lease database" >&2
    exit 1
}
echo "Existing non-HA lease database contains ${existing_summary} worker row(s); it will be backed up before reset."

if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply --confirm-reset-local-postgres."
    exit 0
fi

configured_nodes=0
for index in $(seq 1 3); do
    if ssh -o BatchMode=yes "${coordinators[$((index - 1))]}" \
        "sudo test -f /etc/vss-postgres-ha/.node-configured"; then
        configured_nodes="$((configured_nodes + 1))"
    fi
done
if [[ "$configured_nodes" -ne 0 ]]; then
    echo "Refusing to reset an existing/partial HA cluster (${configured_nodes}/3 nodes configured)." >&2
    echo "Verify cluster health, then use finalize-postgres-ha.sh to complete a partial deployment." >&2
    exit 2
fi

install -d -m 0700 "$state_dir"
bundle_dir="$state_dir/bundle"
secret_dir="$state_dir/secrets"
inventory_dir="$state_dir/inventory"
install -d -m 0700 "$bundle_dir" "$secret_dir" "$inventory_dir"

legacy_inventory_file="$bundle_dir/legacy-inventory.json"
legacy_capture_file="$bundle_dir/.legacy-inventory.capture"
if ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
    "sudo -u postgres psql -d skill_eval_leases -Atqc \"SELECT COALESCE(jsonb_agg(jsonb_build_object('gpu_id', w.gpu_id, 'enabled', w.enabled, 'metadata', w.metadata, 'generation', l.generation, 'live', l.owner_id IS NOT NULL AND l.lease_expires_at > statement_timestamp()) ORDER BY w.gpu_id), '[]'::jsonb) FROM public.gpu_workers AS w JOIN public.gpu_leases AS l USING (gpu_id)\"" \
    >"$legacy_capture_file" 2>/dev/null; then
    mv "$legacy_capture_file" "$legacy_inventory_file"
elif [[ -s "$legacy_inventory_file" ]]; then
    rm -f "$legacy_capture_file"
    echo "Reusing the secured legacy inventory captured before the local database reset."
else
    rm -f "$legacy_capture_file"
    echo "No live legacy database or secured migration inventory is available" >&2
    exit 1
fi
chmod 0600 "$legacy_inventory_file"
python3 - "$legacy_inventory_file" <<'PY'
import json
import sys

inventory = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(inventory, list):
    raise SystemExit("legacy lease inventory is not a JSON list")
for worker in inventory:
    if worker.get("live"):
        raise SystemExit(
            f"refusing migration with a live lease on {worker.get('gpu_id')}"
        )
    if not isinstance(worker.get("generation"), int):
        raise SystemExit("legacy lease generation is missing or invalid")
print(f"Captured {len(inventory)} inactive worker record(s) for migration.")
PY

secret_file() {
    local name="$1"
    local path="$secret_dir/$name"
    if [[ ! -s "$path" ]]; then
        openssl rand -hex 32 >"$path"
        chmod 0600 "$path"
    fi
    printf '%s' "$path"
}

ca_key="$secret_dir/ca.key"
ca_cert="$secret_dir/ca.crt"
if [[ ! -s "$ca_key" || ! -s "$ca_cert" ]]; then
    rm -f "$ca_key" "$ca_cert"
    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "$ca_key"
    openssl req \
        -x509 \
        -new \
        -sha256 \
        -days 3650 \
        -key "$ca_key" \
        -subj "/CN=VSS Skill Eval PostgreSQL HA CA/O=NVIDIA" \
        -out "$ca_cert"
    chmod 0600 "$ca_key"
    chmod 0644 "$ca_cert"
fi

cluster_token_file="$(secret_file cluster-token)"
postgres_password_file="$(secret_file postgres-password)"
replication_password_file="$(secret_file replication-password)"
rest_password_file="$(secret_file patroni-rest-password)"
lease_password_file="$(secret_file lease-role-password)"
fence_password_file="$(secret_file fence-role-password)"

cluster_token="$(<"$cluster_token_file")"
postgres_password="$(<"$postgres_password_file")"
replication_password="$(<"$replication_password_file")"
rest_password="$(<"$rest_password_file")"
lease_password="$(<"$lease_password_file")"
fence_password="$(<"$fence_password_file")"

generate_certificate() {
    local name="$1"
    local subject="$2"
    local extensions="$3"
    local key="$bundle_dir/${name}.key"
    local csr="$bundle_dir/${name}.csr"
    local cert="$bundle_dir/${name}.crt"
    local extension_file="$bundle_dir/${name}.extensions"
    if [[ -s "$key" && -s "$cert" ]]; then
        return
    fi
    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "$key"
    openssl req -new -sha256 -key "$key" -subj "$subject" -out "$csr"
    printf '%s\n' "$extensions" >"$extension_file"
    openssl x509 \
        -req \
        -sha256 \
        -days 825 \
        -in "$csr" \
        -CA "$ca_cert" \
        -CAkey "$ca_key" \
        -CAcreateserial \
        -extfile "$extension_file" \
        -out "$cert" >/dev/null 2>&1
    rm -f "$csr" "$extension_file"
    chmod 0600 "$key"
    chmod 0644 "$cert"
}

declare -a public_ips
declare -a wireguard_keys
for index in "${!coordinators[@]}"; do
    host="${coordinators[$index]}"
    node_index="$((index + 1))"
    public_ip="$(
        ssh -o BatchMode=yes "$host" \
            "curl -fsS --max-time 10 https://api.ipify.org"
    )"
    [[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || {
        echo "Invalid public IPv4 returned by $host" >&2
        exit 1
    }
    public_ips[$node_index]="$public_ip"

    remote_key_script="/tmp/vss-prepare-wireguard-key.sh"
    scp -q "$wireguard_key_script" "${host}:${remote_key_script}"
    public_key="$(
        ssh -o BatchMode=yes "$host" \
            "trap 'rm -f \"$remote_key_script\"' EXIT; chmod 700 '$remote_key_script' && '$remote_key_script'"
    )"
    public_key="$(awk 'NF {value=$0} END {print value}' <<<"$public_key")"
    [[ "$public_key" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
        echo "Invalid WireGuard public key returned by $host" >&2
        exit 1
    }
    wireguard_keys[$node_index]="$public_key"
    printf '%s\t%s\t%s\t%s\n' \
        "$node_index" "$host" "$public_ip" "$public_key" \
        >"$inventory_dir/node-${node_index}.tsv"
done

hosts_entries="$bundle_dir/hosts.entries"
: >"$hosts_entries"
for index in $(seq 1 8); do
    printf '10.203.142.%s vss-pg-%s %s\n' \
        "$index" "$index" "${coordinators[$((index - 1))]}" \
        >>"$hosts_entries"
done

for index in $(seq 1 8); do
    payload="$bundle_dir/wireguard-${index}"
    rm -rf "$payload"
    install -d -m 0700 "$payload"
    install -m 0644 "$ca_cert" "$payload/ca.crt"
    install -m 0644 "$hosts_entries" "$payload/hosts.entries"
    printf '10.203.142.%s/32\n' "$index" >"$payload/wireguard-address"
    : >"$payload/wireguard-peers.conf"
    for peer_index in $(seq 1 8); do
        [[ "$peer_index" -eq "$index" ]] && continue
        cat >>"$payload/wireguard-peers.conf" <<EOF
# ${coordinators[$((peer_index - 1))]}
[Peer]
PublicKey = ${wireguard_keys[$peer_index]}
AllowedIPs = 10.203.142.${peer_index}/32
Endpoint = ${public_ips[$peer_index]}:51821
PersistentKeepalive = 25

EOF
    done
done

echo "Installing encrypted coordinator overlay..."
for index in $(seq 1 8); do
    host="${coordinators[$((index - 1))]}"
    remote_dir="/tmp/vss-postgres-ha-wireguard"
    ssh -o BatchMode=yes "$host" \
        "rm -rf '$remote_dir' && mkdir -m 700 '$remote_dir'"
    scp -qr "$bundle_dir/wireguard-${index}/." "${host}:${remote_dir}/"
    scp -q \
        "$wireguard_install_script" \
        "${host}:${remote_dir}/install-wireguard-node.sh"
    ssh -o BatchMode=yes "$host" \
        "trap 'rm -rf \"$remote_dir\"' EXIT; chmod 700 '$remote_dir/install-wireguard-node.sh' && '$remote_dir/install-wireguard-node.sh' --payload-dir '$remote_dir' --node-index '$index' --coordinator-name '$host'"
done

for index in $(seq 1 8); do
    host="${coordinators[$((index - 1))]}"
    ssh -o BatchMode=yes "$host" \
        "for peer in 10.203.142.1 10.203.142.2 10.203.142.3; do ping -c1 -W3 \"\$peer\" >/dev/null; done"
done
echo "Verified encrypted paths from all coordinators to all database nodes."

initial_cluster=""
for index in $(seq 1 3); do
    [[ -z "$initial_cluster" ]] || initial_cluster+=","
    initial_cluster+="vss-pg-${index}=https://10.203.142.${index}:2380"

    server_extensions="$(
        cat <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:vss-pg-${index},DNS:${coordinators[$((index - 1))]},IP:10.203.142.${index}
EOF
    )"
    postgres_extensions="$(
        cat <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:vss-pg-${index},DNS:${coordinators[$((index - 1))]},IP:10.203.142.${index}
EOF
    )"
    client_extensions="$(
        cat <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth
EOF
    )"
    generate_certificate \
        "etcd-node-${index}" \
        "/CN=vss-pg-${index}/O=NVIDIA/OU=VSS Skill Eval etcd" \
        "$server_extensions"
    generate_certificate \
        "postgres-server-${index}" \
        "/CN=vss-pg-${index}/O=NVIDIA/OU=VSS Skill Eval PostgreSQL" \
        "$postgres_extensions"
    # The etcd v3 gRPC gateway rejects client certificates that carry a
    # CommonName. Deliberately use only organization attributes here.
    generate_certificate \
        "patroni-client-${index}" \
        "/O=NVIDIA/OU=VSS Skill Eval Patroni node ${index}" \
        "$client_extensions"
done

for index in $(seq 1 3); do
    payload="$bundle_dir/postgres-${index}"
    rm -rf "$payload"
    install -d -m 0700 "$payload"
    install -m 0644 "$ca_cert" "$payload/ca.crt"
    install -m 0644 \
        "$bundle_dir/etcd-node-${index}.crt" \
        "$payload/etcd-node.crt"
    install -m 0600 \
        "$bundle_dir/etcd-node-${index}.key" \
        "$payload/etcd-node.key"
    install -m 0644 \
        "$bundle_dir/patroni-client-${index}.crt" \
        "$payload/patroni-etcd-client.crt"
    install -m 0600 \
        "$bundle_dir/patroni-client-${index}.key" \
        "$payload/patroni-etcd-client.key"
    install -m 0644 \
        "$bundle_dir/postgres-server-${index}.crt" \
        "$payload/postgres-server.crt"
    install -m 0600 \
        "$bundle_dir/postgres-server-${index}.key" \
        "$payload/postgres-server.key"
    install -m 0644 "$patroni_unit" "$payload/vss-postgres-ha.service"

    cat >"$payload/etcd.env" <<EOF
ETCD_NAME="vss-pg-${index}"
ETCD_DATA_DIR="/var/lib/etcd/vss-postgres-ha"
ETCD_LISTEN_CLIENT_URLS="https://10.203.142.${index}:2379"
ETCD_ADVERTISE_CLIENT_URLS="https://10.203.142.${index}:2379"
ETCD_LISTEN_PEER_URLS="https://10.203.142.${index}:2380"
ETCD_INITIAL_ADVERTISE_PEER_URLS="https://10.203.142.${index}:2380"
ETCD_INITIAL_CLUSTER="${initial_cluster}"
ETCD_INITIAL_CLUSTER_TOKEN="${cluster_token}"
ETCD_INITIAL_CLUSTER_STATE="new"
ETCD_CERT_FILE="/etc/vss-postgres-ha/etcd/node.crt"
ETCD_KEY_FILE="/etc/vss-postgres-ha/etcd/node.key"
ETCD_CLIENT_CERT_AUTH="true"
ETCD_TRUSTED_CA_FILE="/etc/vss-postgres-ha/etcd/ca.crt"
ETCD_PEER_CERT_FILE="/etc/vss-postgres-ha/etcd/node.crt"
ETCD_PEER_KEY_FILE="/etc/vss-postgres-ha/etcd/node.key"
ETCD_PEER_CLIENT_CERT_AUTH="true"
ETCD_PEER_TRUSTED_CA_FILE="/etc/vss-postgres-ha/etcd/ca.crt"
ETCD_AUTO_COMPACTION_MODE="periodic"
ETCD_AUTO_COMPACTION_RETENTION="1h"
ETCD_QUOTA_BACKEND_BYTES="2147483648"
ETCD_HEARTBEAT_INTERVAL="500"
ETCD_ELECTION_TIMEOUT="5000"
ETCD_ENABLE_V2="false"
EOF
    chmod 0600 "$payload/etcd.env"

    cat >"$payload/patroni.yml" <<EOF
scope: vss-skill-eval
namespace: /vss-skill-eval/
name: vss-pg-${index}

restapi:
  listen: 10.203.142.${index}:8008
  connect_address: 10.203.142.${index}:8008
  certfile: /etc/vss-postgres-ha/postgres-server.crt
  keyfile: /etc/vss-postgres-ha/postgres-server.key
  cafile: /etc/vss-postgres-ha/ca.crt
  verify_client: required
  authentication:
    username: patroni
    password: ${rest_password}
  https_extra_headers:
    Strict-Transport-Security: max-age=31536000
    X-Content-Type-Options: nosniff

ctl:
  insecure: false
  cacert: /etc/vss-postgres-ha/ca.crt
  certfile: /etc/vss-postgres-ha/patroni-etcd-client.crt
  keyfile: /etc/vss-postgres-ha/patroni-etcd-client.key
  authentication:
    username: patroni
    password: ${rest_password}

etcd3:
  hosts:
    - 10.203.142.1:2379
    - 10.203.142.2:2379
    - 10.203.142.3:2379
  protocol: https
  cacert: /etc/vss-postgres-ha/ca.crt
  cert: /etc/vss-postgres-ha/patroni-etcd-client.crt
  key: /etc/vss-postgres-ha/patroni-etcd-client.key

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 0
    maximum_lag_on_syncnode: 0
    primary_start_timeout: 0
    check_timeline: true
    failsafe_mode: true
    synchronous_mode: true
    synchronous_mode_strict: true
    synchronous_node_count: 1
    postgresql:
      use_pg_rewind: true
      use_slots: true
      remove_data_directory_on_rewind_failure: true
      remove_data_directory_on_diverged_timelines: true
      parameters:
        max_connections: 100
        shared_buffers: 1GB
        effective_cache_size: 4GB
        work_mem: 8MB
        maintenance_work_mem: 256MB
        wal_level: replica
        wal_compression: "on"
        wal_keep_size: 1GB
        max_wal_senders: 10
        max_replication_slots: 10
        hot_standby: "on"
        wal_log_hints: "on"
        synchronous_commit: "on"
        password_encryption: scram-sha-256
        ssl: "on"
        ssl_cert_file: /etc/vss-postgres-ha/postgres-server.crt
        ssl_key_file: /etc/vss-postgres-ha/postgres-server.key
        ssl_ca_file: /etc/vss-postgres-ha/ca.crt
        ssl_min_protocol_version: TLSv1.2
        log_connections: "on"
        log_disconnections: "on"
        log_lock_waits: "on"
        log_min_duration_statement: 1000
        log_line_prefix: "%m [%p] %q%u@%d "
  initdb:
    - encoding: UTF8
    - data-checksums
  pg_hba:
    - local all all peer
    - hostssl replication replicator 10.203.142.0/24 scram-sha-256
    - hostssl all all 10.203.142.0/24 scram-sha-256
    - hostnossl all all 0.0.0.0/0 reject

postgresql:
  listen: 10.203.142.${index}:5432
  connect_address: 10.203.142.${index}:5432
  data_dir: /var/lib/postgresql/vss-ha
  bin_dir: /usr/lib/postgresql/16/bin
  pgpass: /var/lib/postgresql/.pgpass-vss-ha
  use_unix_socket: true
  authentication:
    superuser:
      username: postgres
      password: ${postgres_password}
      sslmode: verify-full
      sslrootcert: /etc/vss-postgres-ha/ca.crt
    replication:
      username: replicator
      password: ${replication_password}
      sslmode: verify-full
      sslrootcert: /etc/vss-postgres-ha/ca.crt

watchdog:
  mode: required
  device: /dev/watchdog
  safety_margin: 5

tags:
  nofailover: false
  noloadbalance: false
  clonefrom: false
  nosync: false
EOF
    chmod 0600 "$payload/patroni.yml"
done

echo "Installing PostgreSQL HA packages and configuration..."
for index in $(seq 1 3); do
    host="${coordinators[$((index - 1))]}"
    remote_dir="/tmp/vss-postgres-ha-install"
    ssh -o BatchMode=yes "$host" \
        "rm -rf '$remote_dir' && mkdir -m 700 '$remote_dir'"
    scp -qr "$bundle_dir/postgres-${index}/." "${host}:${remote_dir}/"
    scp -q \
        "$postgres_install_script" \
        "${host}:${remote_dir}/install-postgres-ha-node.sh"
    if ssh -o BatchMode=yes "$host" \
        "chmod 700 '$remote_dir/install-postgres-ha-node.sh' && '$remote_dir/install-postgres-ha-node.sh' --payload-dir '$remote_dir' --node-index '$index' --coordinator-name '$host' --confirm-reset-local-postgres"; then
        ssh -o BatchMode=yes "$host" "rm -rf '$remote_dir'"
    else
        ssh -o BatchMode=yes "$host" "rm -rf '$remote_dir'" || true
        echo "PostgreSQL HA installation failed on $host" >&2
        exit 1
    fi
done

echo "Starting three-member etcd quorum..."
for index in $(seq 1 3); do
    ssh -o BatchMode=yes "${coordinators[$((index - 1))]}" \
        "sudo systemctl restart --no-block etcd.service"
done

etcd_healthy=false
for _ in $(seq 1 30); do
    if ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo env ETCDCTL_API=3 etcdctl --endpoints=https://10.203.142.1:2379,https://10.203.142.2:2379,https://10.203.142.3:2379 --cacert=/etc/vss-postgres-ha/ca.crt --cert=/etc/vss-postgres-ha/patroni-etcd-client.crt --key=/etc/vss-postgres-ha/patroni-etcd-client.key endpoint health --cluster" \
        >/dev/null 2>&1; then
        etcd_healthy=true
        break
    fi
    sleep 2
done
if [[ "$etcd_healthy" != true ]]; then
    for index in $(seq 1 3); do
        ssh -o BatchMode=yes "${coordinators[$((index - 1))]}" \
            "sudo systemctl status --no-pager etcd.service; sudo journalctl -u etcd.service -n 40 --no-pager" >&2 || true
    done
    echo "etcd quorum did not become healthy" >&2
    exit 1
fi

echo "Bootstrapping Patroni primary, then synchronous replicas..."
ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
    "sudo systemctl restart vss-postgres-ha.service"
leader_ready=false
for _ in $(seq 1 60); do
    if ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo -u postgres patronictl -c /etc/vss-postgres-ha/patroni.yml list --format json" \
        >"$bundle_dir/patroni-initial.json" 2>/dev/null &&
       python3 - "$bundle_dir/patroni-initial.json" <<'PY'
import json
import sys

members = json.load(open(sys.argv[1], encoding="utf-8"))
if not any(member.get("Role") == "Leader" for member in members):
    raise SystemExit(1)
PY
    then
        leader_ready=true
        break
    fi
    sleep 2
done
if [[ "$leader_ready" != true ]]; then
    ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo systemctl status --no-pager vss-postgres-ha.service; sudo journalctl -u vss-postgres-ha.service -n 80 --no-pager" >&2 || true
    echo "Patroni primary did not bootstrap" >&2
    exit 1
fi

for index in 2 3; do
    ssh -o BatchMode=yes "${coordinators[$((index - 1))]}" \
        "sudo systemctl restart vss-postgres-ha.service"
done

cluster_ready=false
for _ in $(seq 1 90); do
    if ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo -u postgres patronictl -c /etc/vss-postgres-ha/patroni.yml list --format json" \
        >"$bundle_dir/patroni-cluster.json" 2>/dev/null &&
       python3 - "$bundle_dir/patroni-cluster.json" <<'PY'
import json
import sys

members = json.load(open(sys.argv[1], encoding="utf-8"))
if len(members) != 3:
    raise SystemExit(1)
if any(
    member.get("State") not in {"running", "streaming"}
    for member in members
):
    raise SystemExit(1)
roles = [member.get("Role") for member in members]
if roles.count("Leader") != 1 or "Sync Standby" not in roles:
    raise SystemExit(1)
PY
    then
        cluster_ready=true
        break
    fi
    sleep 2
done
if [[ "$cluster_ready" != true ]]; then
    for index in $(seq 1 3); do
        ssh -o BatchMode=yes "${coordinators[$((index - 1))]}" \
            "sudo systemctl status --no-pager vss-postgres-ha.service; sudo journalctl -u vss-postgres-ha.service -n 60 --no-pager" >&2 || true
    done
    echo "Patroni cluster did not reach synchronous three-node health" >&2
    exit 1
fi

legacy_migration_sql="$bundle_dir/legacy-inventory.sql"
python3 - "$legacy_inventory_file" "$legacy_migration_sql" <<'PY'
import json
import pathlib
import re
import sys

inventory = json.load(open(sys.argv[1], encoding="utf-8"))


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


statements = [
    "-- Preserve monotonic fencing generations from the inactive local canary DB."
]
for worker in inventory:
    gpu_id = worker["gpu_id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", gpu_id):
        raise SystemExit(f"invalid legacy gpu_id: {gpu_id!r}")
    metadata = json.dumps(worker.get("metadata", {}), separators=(",", ":"))
    enabled = "true" if worker.get("enabled") else "false"
    generation = int(worker["generation"])
    if generation < 0:
        raise SystemExit(f"invalid legacy generation for {gpu_id}")
    statements.extend(
        [
            (
                "INSERT INTO public.gpu_workers (gpu_id, enabled, metadata) "
                f"VALUES ({quote(gpu_id)}, {enabled}, {quote(metadata)}::jsonb) "
                "ON CONFLICT (gpu_id) DO UPDATE SET "
                "enabled = EXCLUDED.enabled, metadata = EXCLUDED.metadata, "
                "updated_at = statement_timestamp();"
            ),
            (
                "UPDATE public.gpu_leases SET "
                f"generation = GREATEST(generation, {generation}), "
                "owner_id = NULL, lease_token = NULL, acquired_at = NULL, "
                "renewed_at = statement_timestamp(), "
                "lease_expires_at = statement_timestamp() "
                f"WHERE gpu_id = {quote(gpu_id)};"
            ),
        ]
    )
pathlib.Path(sys.argv[2]).write_text("\n".join(statements) + "\n", encoding="utf-8")
PY
chmod 0600 "$legacy_migration_sql"

bootstrap_sql="$bundle_dir/bootstrap-lease-database.sql"
cat >"$bootstrap_sql" <<EOF
\set ON_ERROR_STOP on
DO \$bootstrap\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_owner') THEN
        CREATE ROLE skill_eval_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_lease') THEN
        CREATE ROLE skill_eval_lease LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_fence') THEN
        CREATE ROLE skill_eval_fence LOGIN;
    END IF;
    EXECUTE format(
        'ALTER ROLE skill_eval_lease LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
        '${lease_password}'
    );
    EXECUTE format(
        'ALTER ROLE skill_eval_fence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
        '${fence_password}'
    );
END
\$bootstrap\$;
SELECT 'CREATE DATABASE eval OWNER skill_eval_owner'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'eval'
)\gexec
\connect eval
REVOKE CONNECT ON DATABASE eval FROM PUBLIC;
GRANT CONNECT ON DATABASE eval TO skill_eval_lease, skill_eval_fence;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE skill_eval_owner;
\ir postgres-gpu-leases.sql
\ir legacy-inventory.sql
RESET ROLE;
EOF
chmod 0600 "$bootstrap_sql"

remote_bootstrap="/tmp/vss-postgres-ha-bootstrap"
ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
    "rm -rf '$remote_bootstrap' && mkdir -m 700 '$remote_bootstrap'"
scp -q "$bootstrap_sql" \
    "vss-skill-validator-distributed-1:${remote_bootstrap}/bootstrap.sql"
scp -q "$lease_schema" \
    "vss-skill-validator-distributed-1:${remote_bootstrap}/postgres-gpu-leases.sql"
scp -q "$legacy_migration_sql" \
    "vss-skill-validator-distributed-1:${remote_bootstrap}/legacy-inventory.sql"
if ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
    "sudo chown -R postgres:postgres '$remote_bootstrap' && sudo chmod 700 '$remote_bootstrap' && sudo chmod 600 '$remote_bootstrap/bootstrap.sql' '$remote_bootstrap/postgres-gpu-leases.sql' '$remote_bootstrap/legacy-inventory.sql' && sudo -u postgres psql --no-psqlrc -f '$remote_bootstrap/bootstrap.sql'"; then
    ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo rm -rf '$remote_bootstrap'"
else
    ssh -o BatchMode=yes vss-skill-validator-distributed-1 \
        "sudo rm -rf '$remote_bootstrap'" || true
    echo "Lease database bootstrap failed" >&2
    exit 1
fi

hosts_uri="vss-pg-1:5432,vss-pg-2:5432,vss-pg-3:5432"
common_query="sslmode=verify-full&sslrootcert=/etc/vss-postgres-ha/ca.crt&target_session_attrs=read-write&connect_timeout=5"
lease_dsn_file="$secret_dir/lease-dsn"
fence_dsn_file="$secret_dir/fence-dsn"
admin_dsn_file="$secret_dir/admin-dsn"
printf 'postgresql://skill_eval_lease:%s@%s/eval?%s\n' \
    "$lease_password" "$hosts_uri" "$common_query" >"$lease_dsn_file"
printf 'postgresql://skill_eval_fence:%s@%s/eval?%s\n' \
    "$fence_password" "$hosts_uri" "$common_query" >"$fence_dsn_file"
printf 'postgresql://postgres:%s@%s/eval?%s\n' \
    "$postgres_password" "$hosts_uri" "$common_query" >"$admin_dsn_file"
chmod 0600 "$lease_dsn_file" "$fence_dsn_file" "$admin_dsn_file"

echo "Validating read/write primary discovery from all eight coordinators..."
for host in "${coordinators[@]}"; do
    remote_dsn="/tmp/.vss-postgres-ha-validation-dsn"
    scp -q "$lease_dsn_file" "${host}:${remote_dsn}"
    if ssh -o BatchMode=yes "$host" \
        "chmod 600 '$remote_dsn' && /home/ubuntu/eval-coordinator/venv/bin/python - '$remote_dsn' <<'PY'
import pathlib
import sys

import psycopg

dsn = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').strip()
with psycopg.connect(dsn, autocommit=True) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT NOT pg_is_in_recovery()')
        if cursor.fetchone() != (True,):
            raise SystemExit('multi-host DSN did not select the writable primary')
PY
status=\$?
rm -f '$remote_dsn'
exit \$status"; then
        echo "DATABASE READY: $host"
    else
        ssh -o BatchMode=yes "$host" "rm -f '$remote_dsn'" || true
        echo "Database validation failed from $host" >&2
        exit 1
    fi
done

if [[ "$publish_github_secret" == true ]]; then
    gh secret set \
        GPU_LEASE_DATABASE_URL \
        --repo "$repository" \
        <"$lease_dsn_file"
    echo "Published GPU_LEASE_DATABASE_URL to $repository."
fi

python3 - "$bundle_dir/patroni-cluster.json" <<'PY'
import json
import sys

members = json.load(open(sys.argv[1], encoding="utf-8"))
for member in members:
    print(
        f"{member.get('Member')}: role={member.get('Role')} "
        f"state={member.get('State')} lag={member.get('Lag in MB', 0)}MB"
    )
PY
echo "PostgreSQL HA deployment complete."
echo "Coordinator DSN: $lease_dsn_file"
echo "GPU fence DSN: $fence_dsn_file"
echo "Administrator DSN: $admin_dsn_file"
