# PostgreSQL HA for distributed skill evaluation

This directory deploys the lease database on the eight existing Brev
coordinators. It does not provision additional machines.

## Topology

- `vss-skill-validator-distributed-{1,2,3}` run PostgreSQL 16 under Patroni
  and form the three-member etcd quorum.
- All eight coordinators join the `10.203.142.0/24` WireGuard overlay and can
  still host their four GitHub runners.
- GPU workers receive unique overlay addresses from
  `10.203.142.100` through `10.203.142.254`.
- PostgreSQL (`5432`), Patroni (`8008`), and etcd (`2379`/`2380`) listen only
  on the overlay. Firewall rules expose PostgreSQL to overlay clients but
  restrict etcd and Patroni control ports to the three database nodes. Only
  WireGuard UDP `51821` is exposed publicly. Installation refuses inactive
  UFW or a firewall without default-deny incoming policy. It masks the
  WireGuard service before remediation and unmasks it only after an exact
  node-role firewall audit passes. Role-specific overlay allows precede an
  ordered terminal `wg-vss` deny, so generic host rules cannot widen overlay
  access.
- TLS is mandatory for PostgreSQL, Patroni, and etcd. Lease and fence clients
  use separate least-privilege roles.

Patroni runs with strict synchronous replication. An acknowledged lease write
is present on the leader and a synchronous standby. The Linux watchdog fences
an unhealthy primary. Clients use all three database hostnames with
`target_session_attrs=read-write`, so they rediscover the writable leader.
These Brev hosts expose Linux `softdog`, not a hardware watchdog. It protects
against Patroni/userspace failure but is not equivalent to out-of-band power
fencing for a kernel or hypervisor failure; failover drills must retain the
split-brain checks described below.

## Secure operator state

The deployer creates a CA, client certificates, passwords, generated node
payloads, and DSNs under:

```text
${POSTGRES_HA_STATE_DIR:-$HOME/.config/vss-skill-eval/postgres-ha}
```

This directory is mode 0700 and is intentionally outside the repository.
Back it up to an encrypted operator-controlled secret store. Never attach it
to a workflow artifact or commit it.

## Deploy

Run the read-only checks first. The apply operation fences and captures the
retired lease database, verifies a PostgreSQL dump before replacing any local
cluster, and requires separate confirmations for PostgreSQL, etcd, and the
legacy scheduling state:

```bash
.github/skill-eval/ops/postgres-ha/deploy-postgres-ha.sh

.github/skill-eval/ops/postgres-ha/deploy-postgres-ha.sh \
  --apply \
  --confirm-reset-local-postgres \
  --confirm-reset-local-etcd \
  --confirm-legacy-drained
```

Use `--confirm-no-legacy-database` instead of `--confirm-legacy-drained` only
for a verified fresh installation. The drained path commits a connection
fence before capture, rejects every live lease, records a snapshot hash, and
never falls back to an older local snapshot after an error.

If infrastructure configuration is interrupted, rerun the deployer with the
same confirmations plus `--resume`. Resume requires the hash-verified capture
marker and every original CA/password secret; it never invents replacement
credentials for an existing cluster. If the Patroni cluster is already healthy
and only database finalization was interrupted, run:

```bash
.github/skill-eval/ops/postgres-ha/finalize-postgres-ha.sh \
  --publish-github-secret
```

The publish option writes only the lease-role DSN to the repository
`GPU_LEASE_DATABASE_URL` Actions secret.

Schema finalization records `legacy-inventory-v1` in
`skill_eval_migrations`. A retry with the same snapshot is a no-op for worker
inventory and leases. A different snapshot, a missing marker in a non-empty
database, or any potentially live lease fails closed.

Private keys, PostgreSQL passwords, and bootstrap SQL are streamed into
root-owned directories under `/run` on remote hosts and removed on exit. They
are never staged in runner-user-owned remote files.

## Enroll a GPU worker

Choose and record a unique overlay address. Enrollment rejects an address or
WireGuard public key already assigned to another named worker. Use
`--registered` for a worker reachable by direct SSH; omit it for a
Brev-managed instance:

```bash
.github/skill-eval/ops/postgres-ha/enroll-postgres-client.sh \
  --worker vss-eval-example \
  --address 101 \
  --registered

.github/skill-eval/ops/postgres-ha/enroll-postgres-client.sh \
  --worker vss-eval-example \
  --address 101 \
  --registered \
  --apply
```

Key rotation is explicit:

```bash
.github/skill-eval/ops/postgres-ha/enroll-postgres-client.sh \
  --worker vss-eval-example \
  --address 101 \
  --registered \
  --apply \
  --rotate-key
```

Enrollment establishes network and TLS trust only. Do not enable the worker in
`gpu_workers` until `vss-gpu-fence` is installed, healthy, and a stale-work
takeover test has passed.

Enrollment preflights address and key uniqueness on all three database nodes
before changing any node. A fleet-wide operation lock spans all three nodes,
reserving every client address and key until commit or rollback; per-node file
locks also serialize peer updates and full node reinstalls.
Operation-scoped helper, staging, and rollback paths prevent a losing concurrent
request from deleting the winner's state. Every mutation proves live operation
ownership and compares the current peer with the exact per-node preimage before
writing. Phase-boundary renewals, bounded SSH/package operations, and a
root-owned client operation marker prevent an expired coordinator from
overwriting a successor. Rollback restores those server preimages, while a
root-only client snapshot covers the key, address, hosts, CA, and prior service
state. Failed
enrollment or key rotation restores both server peers and the complete prior
client network state before releasing the fleet lock. Client installation also
requires active, default-deny UFW and places a terminal `wg-vss` deny before
generic host allows.

## Backups and restore proof

Finalization installs an hourly encrypted logical backup on
`vss-skill-validator-distributed-4`, outside the three database nodes. For
production, deploy the same service independently on coordinator 5 so one
backup-host loss does not remove every recovery copy. Backups are retained for
seven days and checksummed. A weekly timer starts a clean, local PostgreSQL
cluster on the backup host, bootstraps the protected role names, restores the
newest dump, checks the lease safety tables and fence gate, then removes the
cluster. Restore testing never creates a scratch database on the production
HA cluster.

The encryption passphrase remains mode 0600 in secure operator state and is
installed as root-owned, group-readable mode 0640 only for the dedicated
`vss-pg-backup` service account. No plaintext dump remains on the backup host.
The remote dump uses a dedicated read-only `skill_eval_backup` role, not the
PostgreSQL superuser. If operator state loses the encryption passphrase while
any backup host is configured, deployment fails instead of silently generating
a key that cannot decrypt retained backups. Every deployment compares the local
passphrase digest with every configured recovery host from coordinators 4–8 and
fails if any replica has drifted. The first valid digest is also registered
atomically in PostgreSQL through the first reachable database coordinator among
nodes 1–3; all later deployments must match it. Recovery hosts use
compare-and-create installation and never overwrite an existing key.
Verify:

```bash
ssh vss-skill-validator-distributed-4 \
  'sudo systemctl status vss-postgres-ha-backup.timer \
    vss-postgres-ha-restore-test.timer'

ssh vss-skill-validator-distributed-4 \
  'sudo systemctl show vss-postgres-ha-backup.service \
    vss-postgres-ha-restore-test.service \
    --property=Id,Result,ExecMainStatus'
```

Reinstall and immediately prove backup plus restore with:

```bash
POSTGRES_HA_STATE_DIR="$HOME/.config/vss-skill-eval/postgres-ha" \
  .github/skill-eval/ops/postgres-ha/deploy-postgres-ha-backup.sh --apply

POSTGRES_HA_BACKUP_HOST=vss-skill-validator-distributed-5 \
POSTGRES_HA_STATE_DIR="$HOME/.config/vss-skill-eval/postgres-ha" \
  .github/skill-eval/ops/postgres-ha/deploy-postgres-ha-backup.sh --apply
```

## Health checks

```bash
ssh vss-skill-validator-distributed-1 \
  'sudo patronictl -c /etc/vss-postgres-ha/patroni.yml list'

ssh vss-skill-validator-distributed-1 \
  'sudo env ETCDCTL_API=3 etcdctl \
    --endpoints=https://10.203.142.1:2379,https://10.203.142.2:2379,https://10.203.142.3:2379 \
    --cacert=/etc/vss-postgres-ha/ca.crt \
    --cert=/etc/vss-postgres-ha/patroni-etcd-client.crt \
    --key=/etc/vss-postgres-ha/patroni-etcd-client.key \
    endpoint health --cluster'
```

Require exactly one leader, at least one synchronous standby, all replicas
streaming with zero or bounded lag, three healthy etcd endpoints, and a
postgres-writable `/dev/watchdog` on every database node.

## Failure and recovery

- One database-node failure: Patroni can fail over and clients reconnect.
- Loss of etcd quorum: existing PostgreSQL service may remain readable, but
  Patroni will not make an unsafe leadership decision.
- Loss of synchronous standby: strict mode stops new lease writes instead of
  acknowledging data that could be lost.
- Loss of all three database nodes: coordinators and GPU fences fail closed;
  leases expire and worker-side deadlines terminate admitted work. Rebuild
  the quorum, bootstrap the protected roles, and restore the latest verified
  encrypted dump from coordinator 4 or 5.

HA and backups address different failures. Pre-deployment dumps on each
database node protect only the initial conversion; recurring encrypted
backups and restore-test evidence on coordinators 4 and 5 provide the
disaster-recovery path. Copy at least one encrypted backup to a separately
administered durable store when one becomes available; two Brev hosts do not
protect against a provider- or account-wide loss.
