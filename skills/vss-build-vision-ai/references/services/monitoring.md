# Monitoring Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys | Foundation |
|---|---|---|
| GPU, host and container metrics | `dcgm-exporter`, `prometheus`, `grafana`, `node-exporter`, `cadvisor` | `warehouse` |

Present in `COMPOSE_PROFILES_WH_2D` and in every **extended** Kafka/Redis list
for both in-scope modes. Absent from every `…_MINIMAL` list.

## Required peers

- None. Monitoring is observational — it requires nothing beyond the Docker
  daemon and the NVIDIA runtime, and nothing requires it. That makes dropping
  all five keys the **safest** warehouse delta.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `GRAFANA_HOST_PORT` | Grafana (default `35000` → container `3000`). No HAProxy route. |

Fixed published ports: `dcgm-exporter` `9400`, `prometheus` `9090`,
`node-exporter` `19100` → `9100`, `cadvisor` `18080` → `8080`.

> `node-exporter` and `cadvisor` set **no `container_name`**. In `docker ps` they
> appear as `<COMPOSE_PROJECT_NAME>-node-exporter-1` and `-cadvisor-1`, not as
> bare names. A readiness check matching exact container names will miss them.

## Sources

- `deploy/docker/services/monitoring/`
- `deploy/docker/industry-profiles/warehouse-operations/overrides.env`
