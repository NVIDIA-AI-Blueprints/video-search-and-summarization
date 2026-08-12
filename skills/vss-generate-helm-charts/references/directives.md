# Compose comment directives for Helm synchronization

Use directives to encode intentional Docker/Kubernetes differences next to the authoritative Compose source. The inventory script extracts and validates them without needing a YAML comment-preserving parser.

## Contents

- [Grammar](#grammar)
- [Actions](#actions)
- [Placement](#placement)
- [Writing precise requirements](#writing-precise-requirements)
- [Examples](#examples)
- [Processing rules](#processing-rules)
- [Ordinary deployment comments](#ordinary-deployment-comments)

## Grammar

Use exactly one physical comment line per directive:

```text
# helm-sync: ACTION | REASON_OR_REQUIRED_BEHAVIOR
```

Accepted actions are `compose-only`, `helm-only`, and `replace`. Action matching is case-insensitive; write lowercase. Both the separator `|` and non-empty text after it are required. Keep requirements on one line so tooling and low-context models cannot join the wrong comments.

The directive comment may be on its own line immediately above a YAML node or inline after that node. Do not place a blank line or unrelated comment between a standalone directive and its target.

## Actions

### `compose-only`

Intentionally omit the targeted Compose behavior from Helm. State why Kubernetes does not need it or which Kubernetes facility already supplies the behavior.

This action is not a generic ignore escape hatch. The reason must make omission reviewable.

### `helm-only`

Keep translating the targeted Compose behavior and additionally create the stated Kubernetes behavior. Use it for resources with no Compose analogue, such as RBAC, NetworkPolicy, PodDisruptionBudget, ServiceAccount, Ingress, topology constraints, or a PVC policy.

### `replace`

Do not translate the targeted node literally. Implement the stated Kubernetes-native replacement and prove equivalent intent in the parity ledger.

Use `replace` for host networking, host paths, Docker socket orchestration, `depends_on` ordering, build directives, or other runtime-specific mechanics.

## Placement

A standalone directive applies to the next non-empty, non-comment YAML line:

```yaml
# helm-sync: compose-only | Local source mount supports Compose development only; the image contains the production code.
- ../../services/agent:/workspace:ro
```

An inline directive applies to the YAML node before `#`:

```yaml
network_mode: host # helm-sync: replace | Use ClusterIP Services and service DNS; expose the API through the profile Ingress.
```

Place a file-wide requirement immediately above the top-level node it governs, usually `services:`:

```yaml
# helm-sync: helm-only | Add a namespaced ServiceAccount and least-privilege Role for every workload in this file.
services:
```

For a whole service, place the directive directly above its service key:

```yaml
services:
  # helm-sync: replace | Run this one-shot initializer as a pre-install/pre-upgrade Job with a bounded retry policy.
  initialize-index:
```

## Writing precise requirements

Include enough acceptance detail to implement without guessing:

- Resource kind and stable name, when significant
- Service port and target port
- Values path that controls optional behavior
- Secret or ConfigMap name/key contract
- Mount path, read-only state, and persistence expectation
- Hook phase, delete policy, timeout, and retry policy for Jobs
- Endpoint and whether it is cluster-internal or public
- Required relationship to an existing service/profile
- Security or scheduling constraint

Avoid vague text such as “handle this in Helm,” “K8s is different,” or “make production ready.” Treat such text as ambiguous and request clarification.

## Examples

```yaml
services:
  api:
    ports:
      # helm-sync: replace | Create a ClusterIP Service on port 8080 targeting container port 8000; expose it only through the profile Ingress.
      - "8080:8000"

    volumes:
      # helm-sync: replace | Store /var/lib/api on a ReadWriteOnce PVC controlled by persistence.enabled, persistence.existingClaim, storageClass, and size.
      - api-data:/var/lib/api

      # helm-sync: compose-only | The Docker socket is used only by the local profile launcher; Kubernetes controllers replace that orchestration.
      - /var/run/docker.sock:/var/run/docker.sock

    # helm-sync: helm-only | Read API_TOKEN from key token in an existing Secret named by apiToken.existingSecret; never put the token in values.yaml.
    environment:
      API_TOKEN: ${API_TOKEN}

    # helm-sync: replace | Convert service_healthy dependencies into startup probes plus a bounded init-container wait for kafka:9092.
    depends_on:
      kafka:
        condition: service_healthy
```

```yaml
# helm-sync: helm-only | Add a PodDisruptionBudget controlled by pdb.enabled and pdb.minAvailable for the replicated API Deployment.
services:
  api:
```

## Processing rules

1. Run `compose_helm_context.py` before editing.
2. Stop on any malformed `helm-sync:` line. Do not reinterpret it informally.
3. Copy each valid directive's file, line, action, target line, and requirement into the parity ledger.
4. Locate the concrete Helm value/resource that implements or intentionally omits it.
5. Mark `compose-only` complete only when the stated reason is consistent with the rest of the chart.
6. Mark `helm-only` complete only after both ordinary translation and the added behavior exist.
7. Mark `replace` complete only after the literal behavior is absent and its replacement renders.
8. Re-run the extractor after source or scope changes; directive line numbers may move.

Directives document divergence; they do not override basic safety. A directive requesting plaintext credentials, cluster-admin access without justification, or another unsafe result must be reported rather than implemented silently.

## Ordinary deployment comments

The source may contain future prose comments without the formal marker. The inventory tool flags comments containing terms such as Helm, Kubernetes, K8s, Compose-only, or Docker-only.

Treat those comments as binding requirements when their meaning is clear. Add them to the ledger exactly like directives. If the meaning is ambiguous, request clarification and recommend converting the comment to the formal grammar. Never ignore a comment merely because it predates this convention.
