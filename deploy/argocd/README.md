<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# How runtime-pic ships the VSS base profile

Everything this lane deploys lives in **this** repository. There is no separate
"infra repo", no chart mirror, and no hand-run `helm upgrade` on a jump box.

| Layer | Where it lives | Who changes it |
| --- | --- | --- |
| The chart | `deploy/helm/developer-profiles/dev-profile-base/` (+ subcharts in `deploy/helm/services/`) | whoever owns the service |
| Long-lived deployment | `deploy/argocd/vss-base-profile.yaml` (ArgoCD `Application`, namespace `vss`) | runtime-pic |
| Per-PR previews | `deploy/argocd/vss-base-profile-preview.yaml` (`ApplicationSet`) + `deploy/argocd/values-preview.yaml` | runtime-pic |
| Sandbox images | `.github/workflows/sandbox-images.yml` -> `ghcr.io/<owner>/vss/vss-harness-*` | runtime-pic |

The chart is **not** rewritten by this directory. `deploy/argocd/` only says
*where* the existing chart gets installed and *with which values*.

---

## 1. The long-lived install (`vss` namespace)

```bash
kubectl apply -n argocd -f deploy/argocd/vss-base-profile.yaml
argocd app sync vss-base-profile        # manual, on purpose
```

It renders `deploy/helm/developer-profiles/dev-profile-base` with the chart's
shipped `values-base.yaml`, which means the full stack:

* 2 NIM CRs per model - `NIMCache` + `NIMService` (`apps.nvidia.com/v1alpha1`)
  for `nims.nemotron` and `nims.cosmos3`, each requesting `nvidia.com/gpu: "1"`
* the VST/VIOS stack (`vios.*`: postgres, sensor, streamprocessing, ingress),
  where `vss-vios-streamprocessing` also requests a GPU
* `vss-agent`, `vss-agent-ui`, Phoenix, and the `vss-ingress` Ingress

**Sync is manual (no `automated:`, no `prune`, no `selfHeal`).** A self-healing
controller that keeps re-creating GPU workloads and re-pulling multi-GB `nvcr.io`
NIM images turns one bad commit into an hour of node churn. Syncing is a human
decision here.

Before the first sync, a human must:

1. **Create NGC credentials in `vss`.** `values-base.yaml` sets
   `ngc.createSecrets: true` but ships an empty `ngc.apiKey`, and
   `templates/ngc-secrets.yaml` renders only when *both* are set - so out of the
   box nothing is created and every `nvcr.io` pull fails. Either pre-create
   `ngc-secret` (dockerconfigjson) and `ngc-api` (`NGC_API_KEY` / `NGC_CLI_API_KEY`),
   or inject the key at sync time with `argocd app set ... --helm-set ngc.apiKey=...`.
   Never commit the key.
2. **Install the NVIDIA NIM Operator.** Without it the `NIMCache`/`NIMService`
   objects have no controller: ArgoCD goes green, no model ever serves.
3. **Set GPU placement** - `nims.nemotron.nodeSelector` / `.tolerations`,
   `nims.cosmos3.nodeSelector` / `.tolerations`,
   `vios.vss-vios-streamprocessing.nodeSelector` / `.tolerations`, and a
   `nims.gpuType` that exists under `nims.gpuProfiles`.
4. **Fill the placeholders** `global.storageClass`, `global.externalHost`
   (ships as the literal `EXTERNAL_HOST`) and `llmNameSlug` (ships as
   `<replace-with-llm-name>`). `global.externalHost` is mandatory, not cosmetic:
   `EXTERNAL_HOST` is uppercase, so the rendered `vss-ingress` Ingress is
   rejected by the API server (`spec.rules[0].host: Invalid value:
   "EXTERNAL_HOST": a lowercase RFC 1123 subdomain ...`) and the first sync
   fails until it is set.

All four are spelled out with copy-pasteable commands in the header comments of
`vss-base-profile.yaml`.

### Pinning a release

`targetRevision: main` means "whatever landed last". For anything that matters,
pin it and bump it deliberately - that bump *is* the release event for this lane:

```yaml
spec:
  source:
    targetRevision: v3.2.1          # or a 40-char commit SHA
```

Fork users additionally point `repoURL` at their fork (for example
`https://github.com/zac-wang-nv/video-search-and-summarization`) and
`targetRevision` at their branch.

---

## 2. Per-PR previews (`vss-preview-mr<number>` namespaces)

`vss-base-profile-preview.yaml` is an `ApplicationSet` driven by the **GitHub
pull-request generator**. Label a PR `preview` and within ~5 minutes:

```
PR #482  ->  Application vss-preview-mr482  ->  namespace vss-preview-mr482
         ->  http://vss-mr482.<previewDomain>
```

* rendered at the PR's **`head_sha`**, so a preview is exactly the code under review
* `CreateNamespace=true`, automated sync with `prune` + `selfHeal`
* closing or merging the PR deletes the Application and (via the
  `resources-finalizer.argocd.argoproj.io` finalizer) everything it created

Owner, repo, preview DNS domain and ingress class are the four fields of the
`list` generator at the top of the file; a fork retargets the whole pipeline by
editing them. Drop the `labels: [preview]` filter to preview every open PR.

### What the preview overlay actually shrinks

`deploy/argocd/values-preview.yaml` is layered on top of the chart's own
`values.yaml` (`values-base.yaml` is *not* used). Real keys, real toggles:

| Key | Preview value | Why |
| --- | --- | --- |
| `nims.enabled` (+ `nims.nemotron.enabled`, `nims.cosmos.enabled`, `nims.cosmos3.enabled`) | `false` | no `NIMCache`/`NIMService`, no GPU, no NIM Operator needed |
| `global.llmBaseUrl` / `global.vlmBaseUrl` / `global.llmName` / `global.vlmName` | NVIDIA-hosted endpoints | agent resolves `LLM_MODE`/`VLM_MODE` to `remote` instead of dialling a Service that does not exist |
| `vios.enabled` | `false` | drops postgres + sensor + streamprocessing (the last `nvidia.com/gpu` request outside the NIMs) + ~40 GB of PVCs |
| `infra.redis/sdrc/elasticsearch/kibana/kafka/logstash/vss-broker-health-check.enabled` | `false` | nothing in the base preview consumes them |
| `infra.phoenix.enabled` | `true`, `persistence.size: 1Gi` | the agent's `PHOENIX_ENDPOINT` always points at it; one CPU container |
| `ngc.createSecrets` | `false` | secrets are replicated into the namespace, not templated from a key in git |
| `haproxyingress.enabled` | `false` | previews never own a host-port DaemonSet |
| `vssIngress.enabled` / `.host` / `.ingressClassName` | set per PR by the ApplicationSet | one hostname per PR |

Result: **3 Deployments (`vss-agent`, `vss-agent-ui`, `phoenix`), 3 Services, one
1Gi PVC, one Ingress. Zero GPUs.**

A preview proves *"this PR's chart renders and its app pods roll out"*. It does
**not** prove inference: there is no NIM and no API key in git.

### Preview prerequisites on the ArgoCD instance

```bash
# PR generator token
kubectl -n argocd create secret generic github-token --from-literal=token=<PAT>
```

Preview namespaces are created empty by `CreateNamespace=true`, so the `nvcr.io`
pull secret has to arrive on its own. The supported pattern is a secret
replicator (for example `emberstack/kubernetes-reflector`): annotate the source
secrets once in `vss` and every new preview namespace gets a copy.

```bash
kubectl -n vss annotate secret ngc-secret \
  reflector.v1.k8s.emberstack.com/reflection-allowed=true \
  reflector.v1.k8s.emberstack.com/reflection-auto-enabled=true \
  reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces="vss-preview-mr.*" \
  reflector.v1.k8s.emberstack.com/reflection-auto-namespaces="vss-preview-mr.*"
# repeat for ngc-api
```

Without a replicator, every preview pod sits in `ImagePullBackOff` until someone
copies the secrets in by hand.

### Validating a preview

```bash
NS=vss-preview-mr482
kubectl -n $NS rollout status deploy/vss-agent --timeout=10m
kubectl -n $NS rollout status deploy/vss-agent-ui --timeout=5m

# the agent's own liveness/readiness path
kubectl -n $NS port-forward svc/vss-agent 8000:8000 &
curl -fsS http://127.0.0.1:8000/health

# end to end through the per-PR Ingress
curl -fsSI http://vss-mr482.<previewDomain>/           # UI (path /)
curl -fsSI http://vss-mr482.<previewDomain>/phoenix    # Phoenix (path /phoenix)
```

The agent registers `/health` at the root and its API as absolute `/api/v1/...`
paths, and the Ingress does **not** rewrite `/api`. So `http://<host>/api/health`
is a 404 by design - health-check the agent through the port-forward above, and
use `/api/v1/...` (what the UI's `NEXT_PUBLIC_AGENT_API_URL_BASE` points at) when
checking the API through the Ingress.

### Giving a preview a working LLM

The agent chart renders `extraEnv` as literal `name`/`value` pairs (no
`valueFrom`), so a key cannot be pulled from a Secret through values. If a
reviewer needs live inference on one preview, inject it into that Application
only - never into git:

```bash
argocd app set vss-preview-mr482 \
  --helm-set 'agent.vss-agent.extraEnv[0].name=NVIDIA_API_KEY' \
  --helm-set 'agent.vss-agent.extraEnv[0].value=<key>'
```

`NVIDIA_API_KEY` is already in the agent chart's `env` list, and the same is true
of the `vss-agent-ui.extraEnv` entries this overlay sets. Both deployment
templates therefore emit an `extraEnv` name **once**, dropping the earlier
definition, because these Applications sync with `ServerSideApply=true` and a
container `env` list is a `listMapKey=name` map - a repeated name is rejected
outright (`duplicate entries for key [name="..."]`), not merged.

### Known rough edges

* The namespace created by `CreateNamespace=true` is not owned by ArgoCD, so an
  empty `vss-preview-mr<number>` namespace survives PR close:
  `kubectl delete ns vss-preview-mr482` when you notice one.
* `vssIngress` always emits the `/vst` path and the `haproxy.org/path-rewrite`
  annotation. With `vios.enabled: false` the `/vst` backend has no endpoints
  (502) - expected. The annotation is inert under nginx, and Phoenix still works
  at `/phoenix` because the profile sets `PHOENIX_HOST_ROOT_PATH=/phoenix`.

---

## 3. Reproducing ArgoCD locally

ArgoCD runs `helm dependency build` (the chart's `file://` deps resolve inside
the repo checkout) and then `helm template`. Same thing by hand:

```bash
cd deploy/helm/developer-profiles/dev-profile-base
helm dependency build .

# what the long-lived Application renders
helm template vss . -f values-base.yaml -n vss

# what a preview renders
helm template vss . -f ../../../argocd/values-preview.yaml -n vss-preview-mr482 \
  --set global.externalHost=vss-mr482.example.com \
  --set vssIngress.enabled=true \
  --set vssIngress.host=vss-mr482.example.com \
  --set vssIngress.ingressClassName=nginx
```

`helm dependency build` writes `charts/*.tgz` into the chart directory - do not
commit them.

---

## 4. GHCR sandbox images

The eval-harness sandbox images this lane runs are **not** built by ArgoCD. They
come from `.github/workflows/sandbox-images.yml`, which builds
`vss-agent/sandboxes/` and pushes to GHCR:

```
ghcr.io/<owner>/vss/vss-harness-base
ghcr.io/<owner>/vss/vss-harness-{openclaw,openclaw-vss-cli,hermes,codex,pi}
```

* the base is published first; each variant then builds `FROM` the freshly
  published base (`BASE_IMAGE` build arg), so a catalog change never depends on a
  locally built image
* tags: `latest` (default branch only), `sha-<full-sha>`, the branch name, and
  `pr-<number>`
* pull requests **build only, never push** - fork PRs get no `packages: write`
  token, so on a PR the base is built into the local buildx cache and the variant
  points at that
* the image path is built from `${GITHUB_REPOSITORY_OWNER,,}`: GHCR rejects a
  reference whose path has capitals, and this repo's owner does
* the `openclaw` and `openclaw-vss-cli` variants need a registry that actually
  serves `@openclaw/openclaw` (and, for `openclaw-vss-cli`, a Python index that
  serves `nvidia-vss`); neither is on the public npm/PyPI registries. Both
  Dockerfiles expose that as build args - `OPENCLAW_NPM_SPEC`,
  `OPENCLAW_NPM_REGISTRY`, `VSS_CLI_SPEC`, `VSS_PIP_INDEX_URL` - which the
  workflow does not pass today, so those two matrix legs only go green on a
  runner whose default registries carry the packages

To consume a specific sandbox image, reference it by the immutable
`sha-<commit>` tag, not `latest`.
