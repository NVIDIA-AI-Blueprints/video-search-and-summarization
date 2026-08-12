# Compose-to-Helm translation rules

Use these rules to preserve behavior while replacing Docker runtime mechanics with Kubernetes resources. Existing VSS chart conventions take precedence when they already implement the same behavior safely.

## Contents

- [Start from effective behavior](#start-from-effective-behavior)
- [Choose the workload](#choose-the-workload)
- [Translate container configuration](#translate-container-configuration)
- [Translate networking](#translate-networking)
- [Translate storage and files](#translate-storage-and-files)
- [Translate health, ordering, and lifecycle](#translate-health-ordering-and-lifecycle)
- [Translate resources and GPUs](#translate-resources-and-gpus)
- [Translate identity and security](#translate-identity-and-security)
- [Translate remaining runtime metadata](#translate-remaining-runtime-metadata)
- [Translate profiles into values](#translate-profiles-into-values)
- [Design Helm values and templates](#design-helm-values-and-templates)
- [Handle additions, changes, and removals](#handle-additions-changes-and-removals)
- [Parity checklist](#parity-checklist)

## Start from effective behavior

Inspect all Compose includes, `extends.file`/`extends.service` bases, anchors/merges, interpolation defaults, `env_file` layers, selected profiles, and companion files. Apply Compose merge precedence before recording parity: a profile service that uses `extends` is the inherited base plus its overlay, not only the short overlay visible in that file. When available, `docker compose config` is a non-deploying way to inspect an effective configuration, but it removes comments; always pair it with source scanning.

Do not assume every service in the source graph runs together. Compose profiles often encode alternatives such as local versus shared GPU, model choice, or application mode. Map alternatives to `enabled` conditions and profile/hardware values rather than deploying duplicates.

For each service, capture image, command, entrypoint, environment, ports, volumes, configs/secrets, healthcheck, dependencies, restart/lifecycle, resources, GPU/device settings, security options, user, network behavior, and profile membership before editing Helm.

## Choose the workload

| Compose intent | Kubernetes representation |
|---|---|
| Long-running stateless process | `Deployment` |
| Stable identity, ordered rollout, or per-replica persistent state | `StatefulSet` plus headless Service when discovery needs it |
| One pod per eligible node | `DaemonSet` |
| Run-to-completion initializer/migration/download | `Job`, Helm hook Job, or init container based on lifecycle ownership |
| Scheduled task | `CronJob` |
| Sidecar tightly coupled to one service | Additional container in the same Pod only when lifecycle, scaling, and storage are truly shared |

Do not turn every Compose service into a Deployment. Preserve one-shot semantics and failure/retry behavior. Set `restartPolicy` to `Never` or `OnFailure` for Jobs; controllers for long-running pods normally use `Always`.

Keep selectors stable and compatible with upgrades. Never make an immutable selector depend on image tags, chart versions, or mutable configuration.

## Translate container configuration

### Image and build

- Split a concrete image into values for repository, tag or digest, and pull policy following the sibling chart.
- Translate Compose `pull_policy` to Kubernetes `imagePullPolicy`; preserve `platform` intent through supported image architecture plus node affinity/selectors rather than a Docker platform flag.
- Preserve the full interpolation/default chain in profile values where it represents supported choices.
- Prefer a digest when the Compose source is digest-pinned; do not invent one.
- Wire private registries through `imagePullSecrets`/existing secret contracts.
- Never translate `build:` into an in-cluster build. Select the published image produced by the build pipeline or report a blocker.
- Keep chart `appVersion` and image tag semantics distinct; follow repository release policy.

### Entrypoint and command

Compose `entrypoint` maps to Kubernetes `command`; Compose `command` maps to Kubernetes `args`. Preserve exec-form argument boundaries and shell expansion semantics. If Compose uses shell form, use an explicit shell only when required and retain signal handling.

Preserve working directory, user, stdin/TTY requirements, init process behavior, and stop signal implications. Interactive TTY settings are normally Compose-only for production workloads; require a directive or clear operational need before setting them.

### Environment

- Expand list and map forms without losing empty values.
- Quote YAML booleans, integers, ports, and durations when the container expects strings.
- Put stable non-sensitive configuration in values or ConfigMaps.
- Put credentials, tokens, passwords, private keys, and authorization headers in Secrets. Prefer `existingSecret` plus key-name values; never commit the secret value.
- Preserve optional-versus-required behavior. An empty optional token is not the same as a missing required Secret key.
- Convert peer URLs to Service DNS and target Service ports.
- Keep public URLs configurable through established `global.external*`, Ingress, or service values.
- Avoid duplicating the same env list in leaf and profile values. If an existing profile replaces the list, update every copy or refactor compatibly.
- Use `tpl` only where the existing chart deliberately supports templated values. Do not pass untrusted arbitrary templates into `tpl` casually.

`env_file` itself has no Kubernetes analogue. Translate the resulting variables and precedence, not the host file mechanism.

## Translate networking

### Ports and Services

- Compose `HOST:CONTAINER` publishing becomes a container port plus a Service `port`/`targetPort` when other pods need access.
- Default internal services to `ClusterIP`.
- Use Ingress for supported HTTP/public routes and the repository's established host/path scheme.
- Use `NodePort`, `LoadBalancer`, `hostPort`, or `hostNetwork` only when explicitly required; make them opt-in values.
- `expose` documents internal container ports; create a Service only when discovery/access is required.
- Preserve protocols and named ports. Named ports must be unique in a Pod and valid DNS labels.

### Discovery and addressing

- Replace Compose service names/network aliases with stable Kubernetes Service names.
- Ignore `container_name` as a workload identity; preserve compatibility through a Service name only when callers depend on it.
- Replace peer `localhost` and host-IP URLs with Service DNS. `localhost` is valid only for containers in the same Pod.
- Do not carry Docker bridge subnets, static container IPs, or `links` into Kubernetes.
- Map `extra_hosts` to `hostAliases` only for a real external host requirement; prefer DNS or a Service.
- Map custom DNS/search settings to `dnsPolicy`/`dnsConfig` only when necessary.
- Convert network exposure policy to NetworkPolicy when required by comments or security expectations.

## Translate storage and files

Classify every mount before translating it:

| Compose source | Kubernetes representation |
|---|---|
| Small checked-in non-secret config | ConfigMap, often built with `.Files.Get`/`Glob` |
| Checked-in script | ConfigMap with executable `defaultMode`, chart `files/`, or an image-baked script |
| Sensitive config | Secret or existing Secret reference |
| Durable application/model/database data | PVC with configurable size, storage class, access mode, and optional existing claim |
| Cache/scratch/runtime temp | `emptyDir`; use `medium: Memory` and `sizeLimit` for tmpfs/shm intent |
| Host development source mount | Usually `compose-only`; production image should contain code |
| Required node-local path/device | `hostPath` only with explicit justification, node scheduling, type, and security review |

Preserve target path, read-only state, file mode, `subPath`, ownership, and sharing expectations. A Docker named volume with bind driver options is still a host path; do not mistake it for portable persistence.

For files too large for ConfigMaps/Secrets, use an image, PVC population Job/init container, object store, NGC resource download, or CSI mechanism consistent with neighboring charts. Account for Kubernetes object size limits.

Model whether PVCs are created, retained, reused, or supplied through `existingClaim`. Avoid deleting user data on uninstall by default. Check access modes against replica count and multi-pod sharing.

Translate Compose `tmpfs` exactly like `shm_size`: use a memory-backed `emptyDir` at the same target path with a bounded `sizeLimit`. Preserve anonymous-volume intent explicitly; do not let an image-declared `VOLUME` silently become unbounded ephemeral storage.

## Translate health, ordering, and lifecycle

### Healthcheck

Map health intent rather than copying one probe everywhere:

- `startupProbe` protects slow-starting services and corresponds to Compose `start_period` plus retry budget.
- `readinessProbe` controls traffic and dependency availability.
- `livenessProbe` restarts a stuck process; use it only when restart is a safe recovery.

Translate command/HTTP/TCP checks, interval, timeout, retries, and initial delay. Use named ports when stable. A probe running inside a container must address its local container port, not a Service's public port.

### Dependencies

Kubernetes does not guarantee Compose startup order. Replace `depends_on` according to the actual need:

- Readiness and application retry for ordinary service dependencies
- Bounded init-container wait for a hard startup prerequisite
- Hook or ordinary Job for migrations/bootstrap work
- Parent chart dependency only for packaging/enabling, never as a readiness guarantee

Avoid infinite wait loops. Set retry count, interval, timeout, and failure visibility.

### Restart and shutdown

- `restart: always`/`unless-stopped` is normally controller-managed `restartPolicy: Always`.
- One-shot services require Job semantics rather than a perpetually restarted Deployment.
- Translate `stop_grace_period` to `terminationGracePeriodSeconds` and use lifecycle hooks only when the application needs them.
- Preserve signal handling; shell wrappers must `exec` the application when appropriate.
- Map update/rollback behavior to Deployment/StatefulSet strategy and disruption controls when required.

## Translate resources and GPUs

- Translate CPU/memory reservations to `requests` and limits to `limits` when source intent is known.
- Translate NVIDIA GPU reservations to the cluster's configured extended resource, normally `nvidia.com/gpu`, following existing VSS chart conventions.
- Preserve local versus shared-GPU alternatives as mutually exclusive values/profile modes. Do not request a full GPU for a mode intended to share one without verifying the cluster mechanism.
- Carry existing node selectors, affinity, tolerations, runtime class, and GPU-type override values through every profile.
- Translate `shm_size` to a memory-backed `emptyDir` mounted at `/dev/shm` with an appropriate `sizeLimit`.
- Do not convert Docker device paths blindly. Use a device plugin when available; otherwise require an explicit, schedulable host-device design.
- Treat ulimits and host sysctls as node/platform prerequisites unless a safe pod-level equivalent exists. Document unresolved node requirements.
- Translate legacy Compose `cpus`, `cpu_count`, `mem_limit`, `mem_reservation`, and PID/OOM controls into resource requests/limits and supported security-context fields. Record controls with no pod-level equivalent as platform requirements.

## Translate identity and security

- Map Compose `user`/group behavior to pod/container `securityContext` (`runAsUser`, `runAsGroup`, `fsGroup`) when numeric identity is stable.
- Translate read-only root filesystems and writable temp locations explicitly.
- Map capabilities with `capabilities.add/drop`; default to dropping unnecessary capabilities.
- Use `privileged`, host PID/IPC/network, host devices, and host paths only when indispensable and explicitly documented.
- Create a dedicated ServiceAccount and least-privilege RBAC for Kubernetes API access. Never mount broad credentials or grant cluster-admin as a shortcut.
- Honor seccomp/AppArmor conventions and Pod Security restrictions used by the repository/target cluster.
- Add pod/container security contexts as configurable maps only when the chart safely consumes them.

Docker socket mounts and Docker-aware workload discovery usually require a Kubernetes controller/API redesign. Use a `replace` directive, ServiceAccount, and scoped RBAC; never mount a nonexistent Docker socket into a Pod silently.

## Translate remaining runtime metadata

- Convert meaningful Compose labels into Kubernetes labels or annotations; omit Docker/Compose implementation labels with ledger reasoning. Validate label syntax and length.
- Keep application logs on stdout/stderr for the cluster logging pipeline. Translate a Compose `logging` driver only when the target platform has an explicit collector/sidecar contract; preserve rotation/retention intent outside the container when possible.
- Treat `init: true` as a PID 1 and signal-reaping requirement. Verify the image entrypoint supplies an init or add an intentional wrapper; do not confuse it with a Kubernetes init container.
- Avoid fixed `hostname`/`domainname` identity for ordinary Deployments. Use a Service for discovery or StatefulSet pod identity when stable hostnames are required.
- Treat `stdin_open`, `tty`, `attach`, and `develop`/watch settings as Compose development behavior unless an explicit operational requirement says otherwise.
- Map pod-compatible `sysctls`, supplemental groups, `/etc/hosts` entries, DNS policy, IPC/PID namespace, and termination settings deliberately. Report unsupported `isolation`, storage-driver, cgroup, or device-cgroup behavior rather than dropping it.
- Preserve top-level Compose `configs`, `secrets`, `volumes`, and network intent even when a selected service references them indirectly.

## Translate profiles into values

Compose `profiles` become explicit Helm enablement and selection:

- Give each reusable child an `enabled` value.
- Wire umbrella `Chart.yaml` conditions to the exact parent values path.
- Represent mutually exclusive implementations with a validated mode/model slug, not several enabled workloads.
- Put reusable defaults in the leaf chart and workflow choices in developer/industry profile values.
- Check every `values-*.yaml`, `override-values*.yaml`, node-port, endpoint, ingress, mode, and hardware file in an affected profile.
- Preserve global values propagation used by current charts (`global.imagePullSecrets`, endpoint and storage settings, release-name prefix behavior, etc.).

Remember Helm coalescing: mappings merge recursively, but sequences replace. Profile lists can hide new leaf defaults.

## Design Helm values and templates

- Follow neighboring key names and helper conventions; avoid a parallel values API for the same concept.
- Expose operational choices, not every manifest field mechanically.
- Keep safe defaults. Optional resources must render nothing when disabled and must not reference absent ConfigMap/Secret keys.
- Use `required`/`fail` for values whose absence would create a broken workload, but avoid blocking disabled branches.
- Quote string values and use `toYaml`/`nindent` for structured maps/lists.
- Scope variables carefully inside `with`/`range`; use `$` for root context when needed.
- Use deterministic resource names and standard app labels. Keep Service selectors identical to pod selector labels.
- Add checksum annotations for ConfigMap/Secret-driven rollouts when the existing chart expects automatic restart on config changes.
- Do not embed credentials, machine-specific paths/IPs, or release-specific names in templates.
- Preserve backward-compatible public values unless the task explicitly authorizes a breaking change.

When creating a chart, integrate it into the existing service umbrella first, then into profiles. Keep `Chart.yaml` child versions, parent dependency versions, and lock files synchronized.

## Handle additions, changes, and removals

### Addition

Create all runtime resources, values, helpers, assets, enablement wiring, parent dependency metadata, and at least one intended profile configuration. Confirm peer endpoints and public ingress paths.

### Change

Update the leaf source of truth and every profile override. Search by old and new image, env name, port, service/container name, config filename, and values key to catch aliases and copies.

### Removal

Determine whether the service is removed globally or only from selected profiles. Remove obsolete resources, values, dependencies, endpoints, ingress paths, secrets, configs, jobs, locks, and documentation references in scope. Preserve data/PVC migration requirements and avoid destructive cleanup.

### Rename

Treat a rename as an API/discovery migration. Review immutable selectors, Service DNS, PVC/resource names, config references, and upgrade compatibility. Do not rename persistent resources casually.

## Parity checklist

For every service, answer all rows explicitly in the ledger:

- Workload kind, replicas, enablement, and update strategy
- Image repository/tag/digest/pull policy and pull secret
- Entrypoint, command, args, working directory, user
- Every environment variable and secret source
- Container ports, Services, Ingress, public/internal endpoints
- Every config, script, mount, volume, PVC, cache, and file mode
- Startup, readiness, liveness, dependency waits, Jobs, and hooks
- CPU, memory, GPU, devices, shared memory, node scheduling
- Security context, capabilities, service account, and RBAC
- Shutdown grace, lifecycle hooks, and persistence/upgrade behavior
- Compose profile to Helm values/profile mapping
- Parent dependency, version, condition, lock, and all profile overrides
- Every formal directive and ordinary deployment-related comment
