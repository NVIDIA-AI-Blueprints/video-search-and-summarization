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
  on the overlay. Only WireGuard UDP `51821` is exposed publicly.
- TLS is mandatory for PostgreSQL, Patroni, and etcd. Lease and fence clients
  use separate least-privilege roles.

Patroni runs with strict synchronous replication. An acknowledged lease write
is present on the leader and a synchronous standby. The Linux watchdog fences
an unhealthy primary. Clients use all three database hostnames with
`target_session_attrs=read-write`, so they rediscover the writable leader.

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

Run the read-only checks first. The apply operation backs up an existing local
PostgreSQL cluster before replacing it and requires an explicit destructive
confirmation:

```bash
.github/skill-eval/ops/postgres-ha/deploy-postgres-ha.sh

.github/skill-eval/ops/postgres-ha/deploy-postgres-ha.sh \
  --apply \
  --confirm-reset-local-postgres
```

If infrastructure bootstrap succeeds but schema initialization is interrupted,
resume without resetting the cluster:

```bash
.github/skill-eval/ops/postgres-ha/finalize-postgres-ha.sh \
  --publish-github-secret
```

The publish option writes only the lease-role DSN to the repository
`GPU_LEASE_DATABASE_URL` Actions secret.

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
  leases expire and worker-side deadlines terminate admitted work.

HA is not a backup. Before broad production cutover, configure and test an
encrypted off-cluster backup target with retention and restore drills. The
pre-deployment dumps under `/var/backups/vss-postgres-ha` only protect the
initial migration and do not replace recurring backups.
