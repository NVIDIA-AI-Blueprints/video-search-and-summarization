# Warehouse 2D App Helm Chart

This profile chart wraps `deploy/helm/services/rtvi` and enables `vss-rtvi-cv.profileMode=standalone-2d`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
helm template warehouse-2d deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app
```

Override `rtvi.vss-rtvi-cv.ngcAppDataResourceVersion` and `vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion` when using a different NGC warehouse app-data resource.

## Web UIs

`vssIngress.enabled` (on by default) renders one `Ingress` routing every UI under
a single host, matching the `vss-haproxy-ingress` service in the compose profiles.

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

It sets `global.vssIngress.enabled` and `vssIngress.enabled` to false and clears
the path prefixes, since each app then owns the root of its own port.

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

