# Warehouse Infrastructure Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys | Foundation |
|---|---|---|
| Warehouse stream plumbing, config rendering, and WebRTC relay | see the floor list below | `warehouse` |

This owner exists because every warehouse service list carries services that no
user-facing capability names and nothing boots without. Without a contract that
declares them, the forward-closure prune in
[`../composition.md`](../composition.md) removes them: the build then resolves,
normalizes and validates cleanly and fails at bring-up — after the user has been
told it validated.

**The floor:**

```text
init-dirs, render-config, wdm-env-from-config, wait-for-redis,
wait-for-docker-workloads, sdr-controller, centralizedb, vst-ingress,
sensor-bp-wait-bp-configurator, turnserver-init, turnserver, redis
```

`vios-apt-cache-init` also resolves into every warehouse build. It carries no
`profiles:` gate and is a `depends_on` of `streamprocessing-ms-*`, so it is
never selected or pruned by `COMPOSE_PROFILES` and needs no entry above.

## Required peers

- **Every warehouse capability owner requires this entire floor**, unconditionally.
  It is not decomposable — do not prune a subset because a particular capability
  appears not to reach it.
- `redis` is in the floor even when `STREAM_TYPE=kafka`: it backs `sdr-controller`
  state in *every* warehouse variant, independently of the CV message broker.
- `turnserver` and `turnserver-init` are in every warehouse list; VST playback
  needs the relay.
- `sensor-bp-wait-bp-configurator` gates VIOS sensor registration on the
  configurator becoming healthy. Removing it produces a partial stream
  registration — every container healthy, short `Active sources` count.
- The `sdrc-*` one-shots (`init-dirs`, `render-config`, `wdm-env-from-config`,
  `wait-for-redis`, `wait-for-docker-workloads`) render and gate the
  `sdr-controller` config. They exit `0` on success; that is not a failure.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `SDR_CONTROLLER_CONFIG_PATH` | Warehouse SDRC config directory. Embeds `warehouse-${MODE}-app`, so it must be re-materialized whenever `MODE` or `VSS_APPS_DIR` changes — see the closure table in [`../composition.md`](../composition.md). |
| `SDRC_PROXY_HOST_PORT` | SDR controller proxy (default `10000`). |
| `TURN_HOST_PORT`, `TURN_PORT`, `TURN_MIN_RELAY_HOST_PORT`, `TURN_MAX_RELAY_HOST_PORT`, `TURN_EXTERNAL_IP` | TURN listener and relay range. `TURN_EXTERNAL_IP` derives from `HOST_IP`. |
| `VST_INGRESS_HOST_PORT` | VST UI (default `30888`, served under `/vst/`). |

Every warehouse build must include
`deploy/docker/services/infra/compose-no-turn-tcp-relay.yml` in its
`compose.yml` include path list; it overrides the `turnserver` published port
set. See the compose entrypoint section of [`../composition.md`](../composition.md).

## Sources

- `deploy/docker/services/infra/compose.yml`
- `deploy/docker/services/infra/compose-no-turn-tcp-relay.yml`
- `deploy/docker/services/infra/sdrc/`
- `deploy/docker/industry-profiles/warehouse-operations/overrides.env`
