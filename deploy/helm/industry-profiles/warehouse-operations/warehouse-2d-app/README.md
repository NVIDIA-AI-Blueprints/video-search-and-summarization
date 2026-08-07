# Warehouse 2D App Helm Chart

This profile chart wraps `deploy/helm/services/rtvi` and enables `vss-rtvi-cv.profileMode=standalone-2d`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
helm template warehouse-2d deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
```

Override `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` and `vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion` when using a different NGC warehouse app-data resource.

## Web UIs

`global.vssIngress.enabled` (off by default) renders one `Ingress` routing every UI
under a single host, matching the `vss-haproxy-ingress` service in the compose
profiles. The top-level `vssIngress.*` block holds config only (host, ports,
ingressClassName); it is not the enable gate.

`global.externalHost` drives all browser-reachable URLs (VST endpoint, analytics
address, incident links). `vssIngress.host` controls only the Ingress
`spec.rules[].host` for Kubernetes routing. Set both if they differ; omit
`vssIngress.host` to match any hostname.

### Prerequisite: HAProxy ingress controller

The controller is not in this repo and the chart does not install it. Install it
once per cluster:

```bash
helm repo add haproxytech https://haproxytech.github.io/helm-charts
helm repo update

helm install haproxy-ingress haproxytech/kubernetes-ingress --version 1.49.0 \
  -n haproxy-controller --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.daemonset.useHostNetwork=true \
  --set controller.service.enabled=false \
  --set controller.ingressClass=haproxy
```

`useHostNetwork=true` binds node ports 8080 (HTTP) and 8443 (HTTPS), hence the
`:8080` in the URLs below. A stock install creates a LoadBalancer Service, which
stays `Pending` on bare metal. Check with:

```bash
kubectl get ingressclass          # expect: haproxy
```

### Install

```bash
helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app

helm install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app \
  -n <namespace> --create-namespace \
  --set global.vssIngress.enabled=true \
  --set global.externalHost=<NODE_IP> \
  --set global.externalPort=8080 \
  --set monitoring.grafana.rootUrl=http://<NODE_IP>:8080/grafana \
  --set infra.kibana.kibanaPublicUrl=http://<NODE_IP>:8080/kibana
```

Both `--set` values are host specific. Grafana and Kibana build absolute links, so
without them Grafana points at `localhost` and Kibana at its in-cluster Service
name. The rest works off the defaults.

The namespace also needs the `ngc-api` and `ngc-docker-reg-secret` secrets.

### URLs

With `<NODE_IP>` being any cluster node:

| UI | URL |
| --- | --- |
| VST | `http://<NODE_IP>:8080/vst/` |
| Kibana | `http://<NODE_IP>:8080/kibana/` |
| NVStreamer | `http://<NODE_IP>:8080/streamer/` |
| Grafana | `http://<NODE_IP>:8080/grafana/` |
| Prometheus | `http://<NODE_IP>:8080/prometheus/` |

`/storage/`, `/video-analytics-api/` and `/behavior-analytics/` are routed too.

Kibana, Grafana and Prometheus run under a path prefix set by
`infra.kibana.basePath`, `monitoring.grafana.rootUrl` and
`monitoring.prometheus.routePrefix`. Change an ingress path and the matching value
has to change too, or the app 404s after its first redirect.

### No ingress controller: NodePort

The bundled override puts the same UIs on node ports and skips the Ingress:

```bash
helm install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app \
  -n <namespace> --create-namespace \
  -f deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app/values-nodeport.yaml
```

| UI | URL |
| --- | --- |
| VST | `http://<NODE_IP>:30888/vst/` |
| NVStreamer | `http://<NODE_IP>:30900/` |
| Kibana | `http://<NODE_IP>:31560/` |
| Grafana | `http://<NODE_IP>:30300/` |
| Prometheus | `http://<NODE_IP>:30909/` |

It sets `global.vssIngress.enabled` to false and clears
the path prefixes, since each app then owns the root of its own port.

## Alerts

The alerts stack is off by default. Four components must be enabled together:

| Component | Flag | Notes |
| --- | --- | --- |
| Alert bridge | `vss-alert-bridge.enabled=true` | Core orchestrator |
| Agent | `agent.enabled=true` | Enables `vss-agent` + `vss-va-mcp` (MCP already on in this profile) |
| Agent UI | `vss-agent-ui.enabled=true` | Separate subchart; not enabled by `agent.enabled` |
| Real-time VLM | `rtvi.vss-rtvi-vlm.enabled=true` | In-cluster VLM endpoint; omit only if supplying an external `vlmBaseUrl` |

Required values to supply:

| Value | Description |
| --- | --- |
| `vss-alert-bridge.kafkaBootstrapServers` | Kafka broker address |
| `vss-alert-bridge.elasticHosts` | Elasticsearch host(s) |
| `vss-alert-bridge.vstBaseUrl` | Base URL of the VST service |

```bash
helm install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app \
  -n <namespace> --create-namespace \
  --set vss-alert-bridge.enabled=true \
  --set vss-alert-bridge.kafkaBootstrapServers=<KAFKA_HOST>:9092 \
  --set vss-alert-bridge.elasticHosts=<ELASTIC_HOST>:9200 \
  --set vss-alert-bridge.vstBaseUrl=http://<VST_HOST>:<PORT> \
  --set agent.enabled=true \
  --set vss-agent-ui.enabled=true \
  --set rtvi.vss-rtvi-vlm.enabled=true
```

`vss-alert-bridge.vlmName` defaults to `nim_nvidia_cosmos3-nano-reasoner_bf16-final`.
If you have an external VLM, skip `rtvi.vss-rtvi-vlm.enabled` and set
`vss-alert-bridge.vlmBaseUrl` to the external endpoint instead.

## Monitoring

Prometheus and Grafana come with the profile (`monitoring.enabled`, on by
default). Prometheus scrapes pods in the release namespace that carry
`prometheus.io/scrape`, container metrics from the kubelet, node metrics from the
node-exporter DaemonSet, and GPU metrics from the GPU operator's
`nvidia-dcgm-exporter`. Three dashboards are provisioned: containers, node, and
GPU.

`dcgmExporter` stays off because the GPU operator already runs one. Set
`monitoring.enabled=false` to skip the stack.

To reach a service directly:

```bash
kubectl port-forward -n <namespace> svc/grafana 3000:3000
```

