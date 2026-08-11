# DGX Spark Deployment (Experimental)

Helm values overlays for running the NVIDIA VSS search profile on NVIDIA DGX
Spark (GB10 Grace Blackwell, arm64) hardware.

> **Experimental:** Validated on real DGX Spark hardware but not part of
> upstream VSS CI. Maintained as a feature branch for partners who need VSS
> on edge GPU hardware.

## What the overlays do

The `values-dgx-spark*.yaml` files cover **platform compatibility only**:

- arm64 (`-sbsa`) image variants where the default tag has no arm64 build
- `runtimeClassName: nvidia` for CDI-based GPU driver injection
- `podSecurityContext.fsGroup` so the non-root DeepStream process can read
  models from a PVC
- `downloadModelsFromNgc: false`, because the upstream model-download Job
  installs the amd64 NGC CLI
- remote model serving (`nims.enabled=false` plus shared `global.llmBaseUrl` /
  `global.vlmBaseUrl`), since the LLM and VLM run as standalone Deployments

They deliberately set **no node placement, no resource limits, and no service
exposure**. A single-node Spark needs none of that. If you are running a
multi-node cluster, see [Reference 3-node layout](#reference-3-node-layout-optional)
below for the tested arrangement, shipped as a separate overlay you stack with
`-f` (the same pattern as `values-nodeport.yaml`).

### GB10 NIM parameters

The DGX Spark has 128 GiB of unified LPDDR5x shared between CPU and GPU.
NIM parameters are sourced from upstream's official Docker Compose profiles
to stay aligned with the VSS team's validated values:

- **VLM (Cosmos3):** `deploy/docker/services/nim/cosmos3-reasoner/hw-DGX-SPARK.env` — pins the BF16 model profile, sets `NIM_GPU_MEMORY_UTILIZATION=0.7`, `NIM_MAX_MODEL_LEN=32768`, disables CUDA graph and the MM preprocessor cache.
- **LLM (Nemotron):** uses the DGX Spark NIM image (`nvidia-nemotron-nano-9b-v2-dgx-spark:1.0.0-variant`), which has built-in GB10 optimizations and needs no extra env vars. Per `skills/vss-deploy-profile/references/edge.md`, do **not** use the standard `nvidia-nemotron-nano-9b-v2:1` image on DGX Spark.
- **GB10 gpuProfile** in `deploy/helm/services/nims/values.yaml` mirrors the same env vars for future NIM Operator integration.

## Prerequisites

1. **DGX Spark system(s)** with the NVIDIA driver and CUDA toolkit installed.
2. **k3s cluster** (single node, or one server plus agents).
3. **GPU operator** with `driver.enabled=false`, `toolkit.enabled=false`,
   `cdi.enabled=true`.
4. **NGC secrets** in namespace `vss`, using the chart's default secret names:
   ```sh
   kubectl create namespace vss
   kubectl create secret docker-registry ngc-docker-reg-secret -n vss \
     --docker-server=nvcr.io --docker-username='$oauthtoken' \
     --docker-password='<NGC_API_KEY>'
   kubectl create secret generic ngc-api-key-secret -n vss \
     --from-literal=NGC_API_KEY='<NGC_API_KEY>'
   ```
5. **GPU time-slicing**, if several GPU pods share one GPU:
   ```sh
   kubectl apply -f deploy/helm/scripts/dgx-spark/manifests/gpu-time-slicing.yaml
   kubectl label node <node> nvidia.com/device-plugin.config=vss-shared
   ```

## Deployment

All commands run from the repo root. Replace `<SC>` with your StorageClass
(e.g. `local-path`) and `<IP>` with the externally reachable Spark IP.

### 1. Deploy the LLM and VLM NIMs

```sh
kubectl apply -f deploy/helm/scripts/dgx-spark/manifests/llm-nim.yaml
kubectl apply -f deploy/helm/scripts/dgx-spark/manifests/vlm-nim.yaml

kubectl wait -n vss pod/llm-nim-0 --for=condition=ready --timeout=20m
kubectl wait -n vss pod/vlm-nim-0 --for=condition=ready --timeout=20m
```

### 2. Helm install the VSS stack (5 releases)

```sh
helm dep up deploy/helm/developer-profiles/dev-profile-search
helm upgrade --install vss-search deploy/helm/developer-profiles/dev-profile-search \
  -f deploy/helm/developer-profiles/dev-profile-search/values-dgx-spark.yaml \
  --set global.storageClass=<SC> \
  -n vss --create-namespace --timeout 30m

helm dep up deploy/helm/services/rtvi
helm upgrade --install vss-rtvi-embed deploy/helm/services/rtvi \
  -f deploy/helm/services/rtvi/values-dgx-spark-embed.yaml \
  --set global.storageClass=<SC> \
  -n vss --timeout 30m

helm upgrade --install vss-rtvi-vlm deploy/helm/services/rtvi \
  -f deploy/helm/services/rtvi/values-dgx-spark-vlm.yaml \
  --set global.storageClass=<SC> \
  -n vss --timeout 30m

helm dep up deploy/helm/services/alert
helm upgrade --install vss-alert deploy/helm/services/alert \
  -f deploy/helm/services/alert/values-dgx-spark.yaml \
  -n vss --timeout 10m

helm dep up deploy/helm/services/video-summarization
helm upgrade --install vss-summarization deploy/helm/services/video-summarization \
  -f deploy/helm/services/video-summarization/values-dgx-spark.yaml \
  -n vss --timeout 10m
```

### 3. Post-install steps

**RTVI-CV model staging** — the upstream model-download Job uses the amd64
NGC CLI, which fails on arm64. Pre-stage models on the PVC with the arm64
NGC CLI:
```sh
kubectl -n vss exec vss-rtvi-cv-0 -- /opt/nvidia/nvidia_ngc/arm64/ngc \
  config set API_KEY <NGC_API_KEY>
kubectl -n vss exec vss-rtvi-cv-0 -- /opt/nvidia/nvidia_ngc/arm64/ngc \
  model download <model-path> --dest <pvc-mount>
```

**Local-path PVC permissions** — the k3s local-path provisioner creates PV
directories as `root:root`; the non-root DeepStream process needs read access:
```sh
kubectl -n vss exec vss-rtvi-cv-0 -- chown -R 1000:1000 /opt/storage
kubectl -n vss exec vss-rtvi-vlm-0 -- chown -R 1000:1000 /opt/storage
```

**Tokenizer file permissions** — the model staging process leaves SigLIP
tokenizer files at chmod 600. The non-root text-embedder process cannot
read them, breaking the `attribute_search` endpoint. Fix after staging:
```sh
kubectl -n vss exec vss-rtvi-cv-0 -- sh -c \
  'chmod 644 /opt/storage/*.onnx /opt/storage/*.bin && chmod 755 /opt/storage/*_tokenizer/ && chmod 644 /opt/storage/*_tokenizer/*'
```

**SigLIP `.plan` timestamp** — if you re-stage the `.onnx` after the encoder
has first built the `.plan`, `kubectl cp` resets the `.onnx` mtime ahead of
the `.plan`, triggering a rebuild that fails on arm64 FP16. After staging:
```sh
kubectl -n vss exec vss-rtvi-cv-0 -- touch \
  /opt/storage/siglip_v2_v1.1.onnx_batch16.plan \
  /opt/storage/siglip_v2_v1.1.onnx_batch16.plan.meta
```

**video_embeddings ES alias** — the upstream ES init Job creates the
`video_embeddings` alias for the current day's index only. Uploaded videos
use a fixed `2025-01-01` timestamp, so their embeddings land in a different
index that is NOT in the alias. Without this fix, `embed_search` returns 0
results for uploaded videos. Add all `mdx-embed-filtered-*` indices to the
alias:
```sh
# Create an index template so future daily indices auto-join the alias:
curl -s -X POST "http://elasticsearch:9200/_index_template/video_embeddings_alias" \
  -H "Content-Type: application/json" \
  -d '{"index_patterns":["mdx-embed-filtered-*"],"template":{"aliases":{"video_embeddings":{}}}}'

# Add any existing indices to the alias:
curl -s -X POST "http://elasticsearch:9200/_aliases" \
  -H "Content-Type: application/json" \
  -d '{"actions":[{"add":{"index":"mdx-embed-filtered-*","alias":"video_embeddings"}}]}'
```
Run these from a pod in the `vss` namespace (e.g. `kubectl exec` into the
agent pod) or via `kubectl port-forward svc/elasticsearch 9200:9200 -n vss`.

**SSE embed keepalive** — the search profile's continuous embedding path
uses a long-lived SSE connection to the RTVI-Embed service. A keepalive pod
holds this connection open so embeddings are not dropped between queries.
Deploy a simple keepalive Deployment:
```sh
cat <<'EOF' | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: embed-gen-keepalive
  namespace: vss
spec:
  replicas: 1
  selector:
    matchLabels:
      app: embed-gen-keepalive
  template:
    metadata:
      labels:
        app: embed-gen-keepalive
    spec:
      containers:
        - name: keepalive
          image: curlimages/curl:8.10.1
          command: ["sh", "-c"]
          args:
            - |
              while true; do
                curl -s -N http://vss-rt-embed:8000/v1/generate_embeddings \
                  -H "Content-Type: application/json" \
                  -d '{"model":"cosmos-embed1-448p-anomaly-detection","input":"keepalive"}' \
                  > /dev/null 2>&1
                sleep 60
              done
      restartPolicy: Always
EOF
```

### 4. Expose the UI and backend APIs

No ingress controller is assumed. Expose the UI, backend APIs, and Kibana
with NodePorts. Replace `<IP>` with the externally reachable Spark IP.

> **Security note:** NodePorts expose the agent, video-analytics,
> alert-bridge, VST, and Kibana APIs without authentication or TLS. This
> matches the existing `values-nodeport.yaml` overlay pattern and is
> appropriate for a DGX Spark on a trusted LAN. If the Spark is reachable
> from an untrusted network, add a firewall rule or an auth proxy (e.g.
> oauth2-proxy + nginx ingress) before exposing the NodePorts.

**Patch existing services to NodePort:**
```sh
kubectl -n vss patch svc vss-agent-ui \
  -p '{"spec":{"type":"NodePort","ports":[{"port":3000,"nodePort":30777}]}}'
kubectl -n vss patch svc vss-vios-ingress \
  -p '{"spec":{"type":"NodePort","ports":[{"port":30888,"nodePort":30888}]}}'
```

**Create NodePort services for backend APIs** (the chart exposes these as
ClusterIP by default; the UI needs to reach them from the browser):
```sh
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: vss-agent-nodeport
  namespace: vss
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: vss-agent
  ports:
    - name: http
      port: 8000
      targetPort: 8000
      nodePort: 30780
---
apiVersion: v1
kind: Service
metadata:
  name: vss-video-analytics-api-nodeport
  namespace: vss
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: vss-video-analytics-api
  ports:
    - name: http
      port: 8081
      targetPort: 8081
      nodePort: 30781
---
apiVersion: v1
kind: Service
metadata:
  name: vss-alert-bridge-nodeport
  namespace: vss
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: vss-alert-bridge
  ports:
    - name: http
      port: 9080
      targetPort: 9080
      nodePort: 30783
EOF
```

Kibana is already exposed as NodePort 30784 by `values-dgx-spark-3node.yaml`.

**Set UI environment variables** — the Next.js UI reads `NEXT_PUBLIC_*` env
vars (not `EXTERNAL_HOST`) to locate its backend APIs. Set all of them so
the Chat, Search, Alerts, and Dashboard tabs can reach their backends:
```sh
IP=<IP>
kubectl set env deployment/vss-agent-ui -n vss \
  NEXT_PUBLIC_ENABLE_CHAT_TAB=true \
  NEXT_PUBLIC_ENABLE_SEARCH_TAB=true \
  NEXT_PUBLIC_ENABLE_ALERTS_TAB=true \
  NEXT_PUBLIC_ENABLE_DASHBOARD_TAB=true \
  NEXT_PUBLIC_VIDEO_MANAGEMENT_TAB_ADD_RTSP_ENABLE=true \
  NEXT_PUBLIC_AGENT_API_URL_BASE="http://${IP}:30780/api/v1" \
  NEXT_PUBLIC_SIDEBAR_CHAT_AGENT_API_URL_BASE="http://${IP}:30780/api/v1" \
  NEXT_PUBLIC_VST_API_URL="http://${IP}:30888/vst/api" \
  NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL="http://${IP}:30780/chat/stream" \
  NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL="http://${IP}:30780/chat/stream" \
  NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL="ws://${IP}:30780/websocket" \
  NEXT_PUBLIC_SIDEBAR_CHAT_WEBSOCKET_CHAT_COMPLETION_URL="ws://${IP}:30780/websocket" \
  NEXT_PUBLIC_ALERTS_API_URL="http://${IP}:30783/api/v1" \
  NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL="http://${IP}:30784" \
  NEXT_PUBLIC_MDX_WEB_API_URL="http://${IP}:30781" \
  NEXT_PUBLIC_SEARCH_TAB_CHAT_AGENT_API_URL_BASE="http://${IP}:30780/api/v1" \
  NEXT_PUBLIC_SEARCH_TAB_CHAT_HTTP_CHAT_COMPLETION_URL="http://${IP}:30780/generate" \
  NEXT_PUBLIC_SEARCH_TAB_CHAT_WEBSOCKET_CHAT_COMPLETION_URL="ws://${IP}:30780/websocket"
```

**Set agent environment variables** — the agent needs the external VST URL
for file upload URL rewriting:
```sh
kubectl set env deployment/vss-agent -n vss \
  VST_EXTERNAL_URL="http://${IP}:30888" \
  VSS_AGENT_EXTERNAL_URL="http://${IP}:30780" \
  VSS_AGENT_REPORTS_BASE_URL="http://${IP}:30780/static/" \
  LVS_BACKEND_URL="http://vss-summarization:38111" \
  RTVI_VLM_BASE_URL="http://vss-rtvi-vlm:8000"
kubectl rollout restart deployment/vss-agent-ui deployment/vss-agent -n vss
```

**Fix VST CORS** — the VST ingress nginx ConfigMap ships with
`__EXTERNAL_IP__` / `__INTERNAL_IP__` placeholders. Replace them with your
Spark IP so browser cross-origin requests to the VST API succeed:
```sh
kubectl get cm vss-vios-ingress-nginx -n vss -o json | python3 -c "
import json, sys
cm = json.load(sys.stdin)
cfg = cm['data']['nginx.conf']
cfg = cfg.replace('__EXTERNAL_IP__', '${IP}')
cfg = cfg.replace('__INTERNAL_IP__', '${IP}')
cm['data']['nginx.conf'] = cfg
json.dump(cm, sys.stdout)
" | kubectl apply -f -
kubectl rollout restart deploy vss-vios-ingress -n vss
```

The UI is then reachable at `http://<IP>:30777`.

| Service | URL | NodePort |
|---------|-----|----------|
| VSS UI | `http://<IP>:30777` | 30777 |
| Agent API (Chat/Search) | `http://<IP>:30780` | 30780 |
| Video Analytics API | `http://<IP>:30781` | 30781 |
| Alert-Bridge API | `http://<IP>:30783` | 30783 |
| Kibana (Dashboard) | `http://<IP>:30784` | 30784 |
| VST API | `http://<IP>:30888` | 30888 |

> **Re-patch invariant:** `helm upgrade` resets the env vars and CORS config
> above. Re-run steps 3 and 4 after any `helm upgrade` of `vss-search`. The
> NodePort Services survive `helm upgrade` (they are standalone, not
> helm-managed).

## Reference 3-node layout (optional)

This is the arrangement the overlays were validated against. Nothing below is
required: a single-node Spark works without any of it. Apply it only if you
are spreading VSS across three Sparks.

Exactly one LLM, one VLM, and one embedding instance exist cluster-wide.

| Node    | Role | Model tier                                     | Infra                                     |
|---------|------|------------------------------------------------|-------------------------------------------|
| spark-1 | LLM  | nemotron-nano-9b-v2-dgx-spark (arm64)          | —                                         |
| spark-2 | VLM  | cosmos3-reasoner (arm64 native)                | Kafka, Elasticsearch, Redis, Logstash     |
| spark-3 | VSS  | vss-rt-embed + vss-rt-vlm + vss-rt-cv (sbsa)   | Phoenix, Postgres, VST, agent, UI, Kibana |

Label the nodes:
```sh
kubectl label node <spark-1> vss-role=llm
kubectl label node <spark-2> vss-role=vlm vss-infra=infra2
kubectl label node <spark-3> vss-role=vss
```

Uncomment the `nodeSelector` block in `manifests/llm-nim.yaml` and
`manifests/vlm-nim.yaml` so each NIM lands on its own node.

Then stack `values-dgx-spark-3node.yaml` on the `vss-search` install in step 2.
It carries the placement for every component in the profile, plus a Kibana
NodePort since the cluster has no ingress controller:
```sh
helm upgrade --install vss-search deploy/helm/developer-profiles/dev-profile-search \
  -f deploy/helm/developer-profiles/dev-profile-search/values-dgx-spark.yaml \
  -f deploy/helm/developer-profiles/dev-profile-search/values-dgx-spark-3node.yaml \
  --set global.storageClass=<SC> \
  -n vss --create-namespace --timeout 30m
```

The four service releases each take a single flag:
```sh
helm upgrade --install vss-rtvi-embed ... --set vss-rtvi-embed.nodeSelector.vss-role=vss
helm upgrade --install vss-rtvi-vlm   ... --set vss-rtvi-vlm.nodeSelector.vss-role=vss
helm upgrade --install vss-alert      ... --set nodeSelector.vss-role=vss
helm upgrade --install vss-summarization ... --set nodeSelector.vss-role=vss
```

## Files

| File | Description |
|------|-------------|
| `developer-profiles/dev-profile-search/values-dgx-spark.yaml` | Search profile platform overlay |
| `developer-profiles/dev-profile-search/values-dgx-spark-3node.yaml` | Optional 3-node placement, stacked with `-f` |
| `services/rtvi/values-dgx-spark-embed.yaml` | Embedding microservice overlay |
| `services/rtvi/values-dgx-spark-vlm.yaml` | RTVI-VLM proxy overlay |
| `services/alert/values-dgx-spark.yaml` | Alert-bridge overlay |
| `services/video-summarization/values-dgx-spark.yaml` | Summarization overlay |
| `services/nims/override-values-GB10.yaml` | GB10 GPU selector for NIM Operator |
| `scripts/dgx-spark/manifests/llm-nim.yaml` | LLM NIM standalone Deployment |
| `scripts/dgx-spark/manifests/vlm-nim.yaml` | VLM NIM standalone Deployment |
| `scripts/dgx-spark/manifests/gpu-time-slicing.yaml` | GPU time-slicing ConfigMap |

## Known limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| `vss-rt-cv` has no arm64 tag on the default channel | Model-download Job and SigLIP engine build fail on arm64 | Pre-stage models with the arm64 NGC CLI; overlay pins `3.2.1-sbsa`, the newest public arm64 tag |
| `vss-vios-sensor` / `vss-vios-streamprocessing` arm64 tags lag the multi-arch ones | Overlay pins `3.1.0-sbsa` rather than the current release | Promote newer `-sbsa` builds to the public catalog |
| File-based streams carry epoch-0 timestamps | behavior-analytics creates 0 behaviors from file input | Use live RTSP streams via `/api/v1/stream/add` |
| LLM/VLM NIMs use plain Deployments | Bypasses the NIM Operator convention | Switch to the NIM Operator once GB10 support is validated |

While `develop` is mid-cycle, several charts default to `nvcr.io/nvstaging/...`
images that external users cannot pull. That affects every architecture
equally and is resolved by upstream's release sweep to the public catalog, so
these overlays deliberately do not pin around it.

## Open upstream ask

**Add `linux/arm64` to `vss-rt-cv` in `deploy/docker/container-inventory.json`,
and promote the 3.3.0 `-sbsa` builds to the public NGC catalog.** With an
arm64 `vss-rt-cv` build, the `downloadModelsFromNgc: false` override, the
image pins, and the manual post-install permission fixes can all be dropped.
