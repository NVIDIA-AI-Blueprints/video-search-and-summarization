# VSS Docker-to-Helm repository map

Use this reference to select the full source context, leaf chart, and downstream consumer charts for a synchronization pass.

## Contents

- [Source hierarchy](#source-hierarchy)
- [Service-family mapping](#service-family-mapping)
- [Profile mapping](#profile-mapping)
- [Change fan-out](#change-fan-out)
- [Target-selection procedure](#target-selection-procedure)
- [Files that belong to source behavior](#files-that-belong-to-source-behavior)

## Source hierarchy

`deploy/docker/compose.yml` includes three families:

1. `deploy/docker/services/compose.yml` — reusable services
2. `deploy/docker/developer-profiles/compose.yml` — assembled developer workflows
3. `deploy/docker/industry-profiles/compose.yml` — assembled industry workflows

Includes and `env_file` entries form the source graph. Profile `.env` and override files select Compose profiles and replace defaults. The same service key may have local/shared-GPU or mode variants that are mutually exclusive rather than separate Kubernetes workloads.

`deploy/helm` uses the inverse hierarchy:

1. `deploy/helm/services/*` — reusable leaf or service umbrella charts
2. `deploy/helm/developer-profiles/*` — profile umbrellas and profile-only resources
3. `deploy/helm/industry-profiles/*` — industry umbrellas and profile-only resources

Most Helm parents use `file://` dependencies with `condition: NAME.enabled`. A leaf edit therefore fans out through dependency versions, parent values, profile overrides, templates, and `Chart.lock` files.

## Service-family mapping

Use this table as routing, not as proof of complete parity. Inspect the actual Compose services and Helm dependencies on every run.

| Docker source | Primary Helm target | Existing structure and notes |
|---|---|---|
| `services/agent` | `services/agent` | Umbrella with `charts/agent` and `charts/va-mcp` |
| `services/alert` | `services/alert` | Alert Bridge leaf chart |
| `services/analytics` | `services/analytics` | Umbrella with behavior analytics, video analytics API, and analytics UI children |
| `services/auto-calibration` | `services/calibration-toolkit`; sometimes `services/calibration-import` | Not one-to-one. Route API/UI/runtime behavior deliberately and scaffold missing Kubernetes behavior when required. |
| `services/configurators/vss-configurator` | `services/bp-configurator` | Compose name and Helm name differ |
| `services/infra` | `services/infra` | Umbrella for Elasticsearch, Kafka, Redis, Kibana, Logstash, Mosquitto, Phoenix, SDRC, and broker health checks |
| `services/monitoring` | `services/monitoring` | Multi-resource chart rather than one child per Compose service |
| `services/nim` | `services/nims` | Singular Docker directory, plural Helm umbrella; model variants and hardware overrides require explicit routing |
| `services/rtvi` | `services/rtvi` | Umbrella with RT-CV, RT-Embed, and RT-VLM children |
| `services/ui` | `services/ui` | Chart name is `vss-agent-ui` |
| `services/video-summarization` | `services/video-summarization` | Chart name is `vss-summarization` |
| `services/vios` | `services/vios` | Umbrella with ingress, PostgreSQL, sensor, stream processing, and NvStreamer children |

For a new Docker family with no row, prefer `deploy/helm/services/<family>` only after checking whether it belongs inside an existing umbrella. A missing chart is not permission to force every Compose service into a standalone chart.

## Profile mapping

| Docker profile | Helm profile |
|---|---|
| `developer-profiles/dev-profile-base` | `developer-profiles/dev-profile-base` |
| `developer-profiles/dev-profile-alerts` | `developer-profiles/dev-profile-alerts` |
| `developer-profiles/dev-profile-lvs` | `developer-profiles/dev-profile-lvs` |
| `developer-profiles/dev-profile-search` | `developer-profiles/dev-profile-search` |
| `industry-profiles/warehouse-operations/warehouse-2d-app` | `industry-profiles/warehouse-operations/warehouse-2d-app` |
| `industry-profiles/warehouse-operations/warehouse-3d-app` | `industry-profiles/warehouse-operations/warehouse-3d-app` |
| `industry-profiles/warehouse-operations/warehouse-mv3dt-app` | `industry-profiles/warehouse-operations/warehouse-mv3dt-app` |

Some Docker profiles, including Smart Cities in the current tree, may not yet have a Helm counterpart. Treat that as a new-profile generation task: identify reusable service charts first, create only the missing profile umbrella/resources, and report any source behavior with no Kubernetes-safe representation.

## Change fan-out

Apply these rules to every selected Docker change:

| Source change | Required Helm review |
|---|---|
| Service image/tag/build input | Leaf image values, workload image, pull secrets, hardware/mode overrides, every profile override |
| Command/entrypoint/env | Leaf workload and values; ConfigMaps/Secrets; every profile that replaces `env`/`extraEnv` |
| Port/endpoint/network alias | Container port, Service, Ingress, NetworkPolicy, peer URLs, public URL values |
| Volume/config/script | ConfigMap/Secret/chart file, PVC/emptyDir, mounts/subPath/mode, init container or Job |
| Healthcheck/dependency/restart | Startup/readiness/liveness probes, init/wait behavior, controller kind and restart policy |
| GPU/device/runtime/security | Resources, runtime class, node selector, tolerations, security contexts, profile hardware files |
| Compose profile membership | Child `enabled`, parent dependency condition, profile values and mutually exclusive variants |
| Service addition/removal | Leaf chart/resource, parent dependency and values, all profiles, locks, endpoints, ingress, docs/config assets |
| Profile `.env` or overrides | Corresponding Helm profile values plus all named mode/hardware/endpoint values files |
| Shared env/inventory/release metadata | Every chart consuming the variable/image/version; potentially all profiles |

Do not stop at the first parent. Resolve local dependency edges transitively until no new chart consumes a changed child.

## Target-selection procedure

1. Run `scripts/compose_helm_context.py` for the requested scope.
2. Confirm every selected path is under `deploy/docker` and inspect deleted paths through `git diff`.
3. Inspect the reported Docker profile consumers. The helper resolves literal and interpolated `COMPOSE_PROFILES*` entries; verify unusual launcher-generated combinations manually.
4. Treat every missing corresponding Helm profile as a required new target or an explicit blocker.
5. Identify the logical service/profile family from the tables above.
6. Find every `Chart.yaml` below the primary Helm target; distinguish the leaf chart from its service umbrella.
7. Resolve every `repository: file://...` dependency pointing to the changed chart and repeat for each parent.
8. Search all consumer `values*.yaml`, override files, templates, configs, and files for the dependency key, service name, container name, image variable, ports, and env names.
9. Search profile charts that configure the service without a direct local dependency, including profile-only ConfigMaps, Secrets, PVCs, Jobs, and Ingress rules.
10. Add all discovered consumers to the parity ledger before editing.

The inventory tool computes candidate and transitive dependency consumers without parsing Helm templates. Verify its result because a template may integrate a service through values or resource names without declaring a chart dependency.

## Files that belong to source behavior

Inspect more than files named `compose`:

- Root and nested Compose include files
- `env_file` targets, checked-in `.env`, `overrides.env`, defaults, and hardware env files
- `container-inventory.json`, release-set metadata, and image/tag declarations
- Bind-mounted JSON/YAML/conf/text configurations
- Entrypoint, init, download, migration, and readiness scripts
- Dockerfiles when they determine runtime command, user, ports, files, or dependencies
- Model/download manifests and profile-specific assets
- Comments adjacent to services or fields, especially `helm-sync` or Helm/Kubernetes/Compose-only language

Do not copy host-specific deployment mechanics blindly. First extract the behavior they provide, then implement that behavior with a Kubernetes-native resource.
