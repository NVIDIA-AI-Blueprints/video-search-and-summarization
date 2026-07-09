# Deploy VSS on Red Hat OpenShift AI (RHOAI)

Use the following documentation to deploy the [NVIDIA Video Search and Summarization Blueprint](../../../README.md) on a Red Hat OpenShift cluster with Helm.

Four developer profiles are available. Each profile targets a different use case; choose the one that matches your workflow:

| Profile | Directory | Description |
|---------|-----------|-------------|
| **base** | `dev-profile-base/` | Full stack — all services, both NIMs |
| **alerts** | `dev-profile-alerts/` | Real-time alert/verification pipeline with RTVI-CV |
| **lvs** | `dev-profile-lvs/` | Long-video summarization with RTVI-VLM |
| **search** | `dev-profile-search/` | Video search with RTVI-CV + RTVI-Embed |

## Prerequisites

1. [Get an NGC API Key](https://org.ngc.nvidia.com/setup/api-keys).

2. Verify that you meet the hardware requirements. GPU counts depend on the profile — see the GPU table in each profile's deploy section under [step 3](#3-deploy-the-application).

3. Verify that you have **OpenShift 4.14 or later** with cluster-admin access, and the `oc` CLI configured.

4. Verify that you have **Helm 3** installed. To install Helm 3, follow the official [Helm installation instructions](https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3).

5. Verify that you have the **NVIDIA GPU Operator** installed and functional. For details, see [GPU Operator documentation](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Pin the driver version via the GPU Operator's driver settings: `580.105.08` (Ubuntu 24.04 nodes) or `580.65.06` (Ubuntu 22.04 nodes).

6. Verify that you have the **NVIDIA NIM Operator** installed (`apps.nvidia.com/v1alpha1` API available). Required when `nims` subcharts are enabled (`NIMCache`/`NIMService`) — skip this if you're using remote LLM/VLM only (see each profile's remote install under [step 3](#3-deploy-the-application)). If not installed, install it:

   ```bash
   helm repo add nvidia https://helm.ngc.nvidia.com/nvidia \
     --username='$oauthtoken' \
     --password=$NGC_CLI_API_KEY
   helm repo update
   helm install nim-operator nvidia/k8s-nim-operator \
     -n nim-operator --create-namespace
   ```

   For details, see the [NIM Operator installation guide](https://docs.nvidia.com/nim-operator/latest/install.html).

7. Verify that a **default StorageClass** with dynamic provisioning is available (e.g., `gp3-csi` on AWS):

   ```bash
   oc get storageclass
   ```

8. Check GPU node taints. GPU nodes on OpenShift clusters typically have taints that prevent non-GPU workloads from scheduling on them. You need the taint key(s) to fill in the tolerations below:

   ```bash
   oc get nodes -l nvidia.com/gpu.present=true \
     -o custom-columns=\
   "NODE:.metadata.name,TAINTS:.spec.taints[*].key"
   ```

   The `values-openshift.yaml` overlay ships with the `tolerations` block for every GPU-scheduled workload (NIMs, `vios-sensor`, `vios-streamprocessing`, and the profile's GPU-using RTVI component(s)) commented out — uncomment and set the `key` to match your cluster's taint (e.g. `nvidia.com/gpu`, or a cloud-specific key like `g6-gpu` on AWS G6e node groups) before installing. If your GPU nodes are untainted, leave the tolerations commented out.

## 1. Login to OpenShift cluster

```bash
oc login --token=$OPENSHIFT_TOKEN \
  --server=$OPENSHIFT_CLUSTER_URL
```

## 2. Export your API key and cluster domain

```bash
export NGC_CLI_API_KEY='<your NGC API key>'
export APPS_DOMAIN=$(oc get \
  ingress.config.openshift.io/cluster \
  -o jsonpath='{.spec.domain}')
```

## 3. Deploy the application

Pick **one** of the profiles below.

> `values-openshift.yaml` sets `global.openshift.enabled: true`, which turns on OpenShift Routes (replacing the Kubernetes Ingress), grants the `anyuid` SCC to the VIOS root services (`vss-vios-nvstreamer`, `-sensor`, `-streamprocessing`), adds a dedicated SCC for the NIM model-cache ServiceAccount (avoids a PVC SELinux relabel timeout on 200Gi+ model caches), nulls out security contexts that conflict with `restricted-v2`, and ships commented-out GPU tolerations for every GPU-scheduled workload (see [step 8](#prerequisites)).

---

### Base profile

Same install flow as `dev-profile-base/README.md`, with the OpenShift overlay (`-f values-openshift.yaml`) and OpenShift host/exposure settings.

   | Workload | GPU |
   |----------|-----|
   | `nvidia-cosmos-reason2-8b` (NIM) | 1 |
   | `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
   | `vss-vios-streamprocessing` | 1 |
   | **Total** | **3** |

```bash
cd deploy/helm/developer-profiles
helm dependency build ./dev-profile-base
```

**In-cluster NIMs** (default):

```bash
helm upgrade --install vss-base ./dev-profile-base \
  -f ./dev-profile-base/values-base.yaml \
  -f ./dev-profile-base/values-openshift.yaml \
  -n vss-base --create-namespace \
  --set llmNameSlug=nvidia-nemotron-nano-9b-v2 \
  --set vlmNameSlug=nvidia-cosmos-reason2-8b \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.${APPS_DOMAIN}
```

**Remote LLM/VLM** (no NIM subcharts); URLs must be reachable from `vss-agent` pods:

```bash
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-base ./dev-profile-base \
  -f ./dev-profile-base/values-base.yaml \
  -f ./dev-profile-base/values-openshift.yaml \
  -n vss-base --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.${APPS_DOMAIN} \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

**Switching the VLM model:** The default deployment uses Cosmos-Reason2-8B. To switch to Cosmos3, uncomment the two lines already stubbed under `global:` in `values-openshift.yaml`:

```yaml
global:
  vlmName: "nvidia/cosmos3-nano-reasoner"
  vlmBaseUrl: "http://nvidia-cosmos3-reasoner:8000"
```

Then flip the NIM toggle via `--set` and re-run the upgrade:

```bash
helm upgrade vss-base ./dev-profile-base \
  -f ./dev-profile-base/values-base.yaml \
  -f ./dev-profile-base/values-openshift.yaml \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss.${APPS_DOMAIN}" \
  --set nims.cosmos.enabled=false \
  --set nims.cosmos3.enabled=true \
  -n vss-base
```

---

### Alerts profile

Same install flow as `dev-profile-alerts/README.md` (verification mode), with the OpenShift overlay (`-f values-openshift.yaml`) and OpenShift host/exposure settings.

**Alert verification** (`values-verification.yaml`):

   | Workload | GPU |
   |----------|-----|
   | `vss-rtvi-cv` | 1 |
   | `vss-rtvi-vlm` | 1 |
   | `vss-vios-streamprocessing` | 1 |
   | `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
   | **Total** | **4** |

**Alert real-time** (`values-realtime.yaml`):

   | Workload | GPU |
   |----------|-----|
   | `vss-vios-streamprocessing` | 1 |
   | `vss-rtvi-vlm` | 1 |
   | `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
   | **Total** | **3** |

```bash
cd deploy/helm/developer-profiles
helm dependency build ./dev-profile-alerts
```

**Verification — in-cluster NIMs** (default):

```bash
helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-verification.yaml \
  -f ./dev-profile-alerts/values-openshift.yaml \
  -n vss-alerts --create-namespace \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-alerts.${APPS_DOMAIN}
```

**Verification — remote LLM/VLM** (no in-cluster NIMs):

```bash
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-verification.yaml \
  -f ./dev-profile-alerts/values-openshift.yaml \
  -n vss-alerts --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-alerts.${APPS_DOMAIN} \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

**Real-time — in-cluster NIMs:**

```bash
helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-realtime.yaml \
  -f ./dev-profile-alerts/values-openshift.yaml \
  -n vss-alerts --create-namespace \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-alerts.${APPS_DOMAIN}
```

**Real-time — remote LLM/VLM** (no in-cluster NIMs):

```bash
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-realtime.yaml \
  -f ./dev-profile-alerts/values-openshift.yaml \
  -n vss-alerts --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-alerts.${APPS_DOMAIN} \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

---

### LVS profile

Same install flow as `dev-profile-lvs/README.md`, with the OpenShift overlay (`-f values-openshift.yaml`) and OpenShift host/exposure settings.

   | Workload | GPU |
   |----------|-----|
   | `vss-summarization` | 1 |
   | `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
   | `vss-vios-streamprocessing` | 1 |
   | `vss-rtvi-vlm` (integrated Cosmos checkpoint) | 1 |
   | **Total** | **4** |

```bash
cd deploy/helm/developer-profiles
helm dependency build ./dev-profile-lvs
```

**In-cluster NIMs** (default):

```bash
helm upgrade --install vss-lvs ./dev-profile-lvs \
  -f ./dev-profile-lvs/values-lvs.yaml \
  -f ./dev-profile-lvs/values-openshift.yaml \
  -n vss-lvs --create-namespace \
  --set llmNameSlug=nvidia-nemotron-nano-9b-v2 \
  --set vlmNameSlug=none \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-lvs.${APPS_DOMAIN}
```

**Remote LLM/VLM** (no NIM subcharts); URLs must be reachable from `vss-agent` and `vss-summarization` pods (service root, no trailing `/v1`):

```bash
export LLM_BASE_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_BASE_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

helm upgrade --install vss-lvs ./dev-profile-lvs \
  -f ./dev-profile-lvs/values-lvs.yaml \
  -f ./dev-profile-lvs/values-openshift.yaml \
  -n vss-lvs --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-lvs.${APPS_DOMAIN} \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nim_nvidia_cosmos-reason2-8b_hf-1208" \
  --set rtvi.vss-rtvi-vlm.useSharedNim=true
```

---

### Search profile

Same install flow as `dev-profile-search/README.md`, with the OpenShift overlay (`-f values-openshift.yaml`) and OpenShift host/exposure settings.

   | Workload | GPU |
   |----------|-----|
   | `nvidia-cosmos-reason2-8b` (NIM) | 1 |
   | `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
   | `vss-vios-streamprocessing` | 1 |
   | `vss-rtvi-cv` | 1 |
   | `vss-rtvi-embed` | 1 |
   | **Total** | **5** |

```bash
cd deploy/helm/developer-profiles
helm dependency build ./dev-profile-search
```

**In-cluster NIMs** (default):

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -f ./dev-profile-search/values-openshift.yaml \
  -n vss-search --create-namespace \
  --set-string global.ngcApiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss-search.${APPS_DOMAIN}
```

**Remote LLM/VLM** (custom remote NIM; no in-cluster NIMs). Uses `values-build-endpoint.yaml` and `agent.vss-agent.*` keys — see `dev-profile-search/README.md`:

```bash
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-search ./dev-profile-search \
  -f ./dev-profile-search/values-build-endpoint.yaml \
  -f ./dev-profile-search/values-openshift.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.${APPS_DOMAIN} \
  --set-string global.ngcApiKey="$NGC_CLI_API_KEY" \
  --set-string agent.vss-agent.apiKeys.nvidia="$NGC_CLI_API_KEY" \
  --set nims.enabled=false \
  --set agent.vss-agent.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set agent.vss-agent.vlmName="nvidia/cosmos-reason2-8b" \
  --set agent.vss-agent.llmBaseUrl="$LLM_BASE_URL" \
  --set agent.vss-agent.vlmBaseUrl="$VLM_BASE_URL" \
  --wait=false
```

> Scaling `vss-rtvi-cv` / `vss-rtvi-embed` past 1 replica each requires consistent-hash routing on the stream ID so retries land on the same replica (`global.rtviInternalIngress`, disabled by default). That path is unrelated to the Routes above — it needs the HAProxy Kubernetes Ingress controller installed separately (see the profile's own README, "Install Ingress Controller"), which works the same on OpenShift as elsewhere. Leave it disabled at the default 1 replica.

## 4. Creating NGC secrets manually (optional)

Applies to **base**, **alerts**, and **lvs**. By default those charts create `ngc-api` and `ngc-secret` when `ngc.createSecrets=true` and `ngc.apiKey` is set. If `ngc.createSecrets=false`, create both secrets yourself in the release namespace before installing, then keep `global.ngcApiSecret` and `global.imagePullSecrets` aligned with the names and keys you chose.

```bash
export NAMESPACE='<NAMESPACE>'
export NGC_CLI_API_KEY='<your NGC API key>'

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic ngc-api \
  -n "$NAMESPACE" \
  --from-literal=NGC_API_KEY="$NGC_CLI_API_KEY" \
  --from-literal=NGC_CLI_API_KEY="$NGC_CLI_API_KEY"

kubectl create secret docker-registry ngc-secret \
  -n "$NAMESPACE" \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password="$NGC_CLI_API_KEY"
```

> **Search** uses `global.ngcApiKey` instead and creates its own secrets from that value — see `dev-profile-search/README.md`.

## 5. Setting the GPU type (optional)

Chart defaults for `nims.gpuType` (for example `RTXPRO6000BW` in `values-base.yaml`) may not match your cluster. On the in-cluster NIM path, add this flag to the `helm install` command for your chosen profile in [step 3](#3-deploy-the-application):

```
--set nims.gpuType=H100
```

Use `L40S` or another supported value if that matches your cluster's GPU. This selects NIM tuning presets; an unrecognized value fails the install.

## 6. Setting the StorageClass (optional)

If the cluster's default StorageClass isn't suitable, add this flag to the `helm install` command for your chosen profile in [step 3](#3-deploy-the-application):

```
--set global.storageClass="<Storage Class Name>"
```

Use a StorageClass that exists on the cluster (for example `gp3-csi` on AWS). List available classes with `oc get storageclass`.

## 7. Verify the deployment

Replace `vss` below with your release name and namespace (`vss-alerts`, `vss-lvs`, or `vss-search` if using a non-base profile).

```bash
# All pods should be Running
oc get pods -n vss

# Get the UI URL
echo "https://vss.${APPS_DOMAIN}/"

# API health check
oc exec -n vss deploy/vss-agent -- \
  curl -s localhost:8000/health

# OpenShift Routes
oc get routes -n vss

# NIM model cache / serving status
oc get nimcache -n vss
oc get nimservice -n vss
```

> **Wait until everything is Ready before using the application in the browser.** With in-cluster NIM enabled (`nims.enabled: true`, the usual default), NIM model workloads need extra time (image pull, `NIMCache`/`NIMService`, model download and warm-up). Opening `vss-agent-ui` while NIM or other backends are still starting can produce transient errors (failed API calls, timeouts, empty screens).
>
> A pod's `READY` column (e.g. `1/1`) only reflects its own readiness probe — for the NIM Operator-managed pods, don't stop there. Check the `NIMService`'s own `.status.state` instead, since that's the field the Operator itself uses to report whether the model is actually loaded and serving:
>
> ```bash
> oc get nimservice -n vss -w
> ```
>
> Wait until every `NIMService` shows `Ready` (not just `Pending`/`NotReady`), and confirm the rest of the pods in `oc get pods -n vss` are `Running`. With remote LLM/VLM only (`nims.enabled: false`), there's no `NIMService` to watch — just confirm all pods are ready.

## 8. Uninstall

Replace `vss` with your release name and namespace.

```bash
helm uninstall vss -n vss
oc delete scc -l app.kubernetes.io/instance=vss
oc delete nimcache --all -n vss
oc delete pvc --all -n vss
oc delete project vss
```
