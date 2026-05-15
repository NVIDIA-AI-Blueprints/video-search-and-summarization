# sdrc Helm Chart

This chart runs the combined WDM SDRC controller and Envoy router image built
from `wdm/envoy/Dockerfile.wdm-router`.

Override `image.repository`, `image.tag`, and `image.pullPolicy` when deploying
a different SDRC image.

Runtime config is mounted at `/config.yml` by default from a ConfigMap created
by the parent profile chart:

- `/config.yml`

## Compose Parity

The Docker Compose SDRC service is generic: it mounts profile-owned
`config.yml` data from `SDR_CONTROLLER_CONFIG_PATH`. This chart follows the same
shape by mounting a profile-owned ConfigMap into the SDRC container. Workload
definitions should live beside the profile that owns them.

When the profile-owned ConfigMap content changes, bump `config.rolloutVersion`
or restart the `sdrc` Deployment so the container remounts the updated
`config.yml`.

- the chart owns the `sdrc` Deployment;
- the chart owns one Service per WDM/Envoy endpoint;
- the profile chart owns the ConfigMap containing `config.yml`;
- the chart owns the same read-only Kubernetes API RBAC shape previously used by
  the VIOS SDR chart;
- the umbrella/profile values provide the ConfigMap name, key, and mount path.

Docker Compose mounts `/var/run/docker.sock` for `WDM_CLUSTER_TYPE: docker`.
This Helm chart does not expose that host socket. Kubernetes deployments should
use `WDM_CLUSTER_TYPE: k8s-headless` or `WDM_CLUSTER_TYPE: k8s`; Docker-backed
workloads should stay on the Docker Compose path.

## Umbrella Chart Example

```yaml
dependencies:
  - name: sdrc
    version: 26.04.2
    repository: "file://./charts/sdrc"
    condition: sdrc.enabled
```

```yaml
sdrc:
  enabled: true
  image:
    repository: nvcr.io/nv-metropolis-dev/metropolis-analytic/sdr-mw-l
    tag: 3.0.0-rc.13
  imagePullSecrets:
    - name: ngc-docker-reg-secret
  service:
    controller:
      type: ClusterIP
      port: 5002
    sdrcDirectListener:
      type: NodePort
      port: 8011
      nodePort: 30001
    envoyAdmin:
      type: LoadBalancer
      port: 9902
  config:
    configMapName: sdrc-config
    key: config.yml
    mountPath: /config.yml
    rolloutVersion: ""
  runtimeEnv:
    WDM_WL_REDIS_SERVER: redis
    WDM_WL_REDIS_PORT: "6379"
    OTEL_SDK_DISABLED: "true"
    KUBERNETES_HOST: kubernetes.default.svc
    KUBERNETES_PORT: "443"
    WDM_CONTROLLER_HOST: "127.0.0.1"
```

The `sdrc` container discovers workloads from `/config.yml`, which is the
default path used by the WDM router entrypoint and `sdr-mw` binary.
`KUBERNETES_HOST` and `KUBERNETES_PORT` are set explicitly so WDM targets the
standard in-cluster Kubernetes API service instead of deriving a namespace-local
service name such as `kubernetes.<namespace>.svc`.
The controller, SDRC direct listener, and Envoy admin endpoints are configured
under `service`. The chart renders one Kubernetes Service per endpoint, so each
endpoint can choose its own Service `type`, `port`, and optional `nodePort`. The
same port values are passed to the container as `WDM_CONTROLLER_PORT`,
`WDM_SDRC_DIRECT_LISTENER_PORT`, `ENVOY_ADMIN_PORT`, and `ROUTER_PORT`.
Workload Envoy listener ports such as `WDM_MS_LISTENER_PORT` are defined in
`config.yml`; this Service intentionally exposes only controller, SDRC direct,
and Envoy admin ports.
