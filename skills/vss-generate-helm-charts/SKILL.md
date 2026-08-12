---
name: vss-generate-helm-charts
description: Synchronizes runnable VSS Helm service and profile charts from the authoritative Docker Compose files, environment layers, launcher behavior (including dev-profile.sh), configs, scripts, deployment comments, and relevant Compose deployment-skill guidance. Use this skill when the user asks to "generate Helm charts from Compose", "sync deploy/helm after Docker changes", "port a Compose service to Kubernetes", or "update Helm for a VSS profile". Do not use it to install, upgrade, operate, or troubleshoot a live Kubernetes deployment.
---

# Generate VSS Helm Charts

Synchronize `deploy/helm` with `deploy/docker` while preserving Kubernetes semantics and the repository's existing chart hierarchy. Produce authoring changes only unless a separate request explicitly authorizes deployment.

## Non-negotiable rules

1. Treat `deploy/docker` as the behavioral source of truth. Inspect Compose files, every active env layer, `deploy/docker/scripts/dev-profile.sh`, referenced configs/scripts/assets, and relevant comments.
2. Treat an explicit `helm-sync` directive as higher priority than literal Compose translation. Treat an unstructured comment mentioning Helm, Kubernetes, K8s, or Compose-only behavior as a requirement that must be resolved, not discarded.
3. Preserve sound existing Helm architecture and public values. Update it for parity; do not replace a mature chart with a generic converter's output.
4. Never edit `deploy/docker` during synchronization. Never discard unrelated or pre-existing changes under `deploy/helm`.
5. Never run `docker compose up/down`, `helm install/upgrade/uninstall/rollback`, `kubectl apply/delete/patch/scale`, or any command that creates, changes, or removes runtime resources.
6. Permit only read-only discovery and offline authoring checks. `docker compose config`, `helm lint`, `helm template`, and schema validation are non-deploying, but run them only when their binaries and required inputs already exist.
7. Never expose real credentials. Convert sensitive inputs to Kubernetes Secret references or existing-secret values; never commit plaintext keys or tokens.
8. Do not claim full parity or renderability while a ledger row, directive, profile consumer, or required static check remains unresolved.

## Select the synchronization scope

Prefer the narrowest complete scope:

- Use explicit paths when the request names a service or profile.
- Use a supplied base ref for committed branch or PR changes.
- Use current staged, unstaged, and untracked Docker changes when no base ref is supplied.
- Use a full-tree pass only when explicitly requested; split it by service/profile family to keep each pass reviewable.

Run the bundled read-only inventory tool from the repository root:

```bash
# Current worktree changes
python3 skills/vss-generate-helm-charts/scripts/compose_helm_context.py --repo-root .

# Committed changes since a caller-supplied base, plus local worktree changes
python3 skills/vss-generate-helm-charts/scripts/compose_helm_context.py \
  --repo-root . --changed-from BASE_REF

# Explicit component
python3 skills/vss-generate-helm-charts/scripts/compose_helm_context.py \
  --repo-root . --path deploy/docker/services/agent
```

Both helpers require an existing Python 3 + PyYAML authoring environment. Do not install missing tools implicitly. The inventory returns `0` on success, `2` for malformed directives or Compose parse failures, `3` when no source scope exists, and `4` for repository/Git/tooling errors. Treat every nonzero result as a blocker. Use its service-field and environment-layer inventory to seed the ledger, but still read each selected file in full.

If no Docker source change or explicit path is found, stop and request a base ref or component. Do not infer scope from stale Helm files alone.

## Synchronization workflow

### 1. Resolve source and targets

Read [references/repository-map.md](references/repository-map.md) before selecting chart targets. Use the inventory's candidate chart roots, Docker profile consumers, corresponding or missing Helm profiles, and transitive consumer charts as a starting point, then verify them against `Chart.yaml` local dependencies and profile values. A Docker profile reported without a Helm target is a generation requirement or an explicit blocker, never an ignorable warning.

Inspect each selected source file in full. For a Compose change, also inspect its include parents, `env_file` inputs, profile `.env`/override layers, bind-mounted configs/scripts, and any referenced inventory/version files. For any developer-profile consumer, read `deploy/docker/scripts/dev-profile.sh` in full and trace the branches that derive its effective profile, mode, hardware, model placement, endpoint, `COMPOSE_PROFILES`, and generated environment. Do not translate a fragment without its effective context.

Read [references/deployment-sources.md](references/deployment-sources.md) before finalizing the source graph. Inspect `skills/vss-deploy-profile/SKILL.md` for developer-profile behavior and search the current `skills/*/SKILL.md` tree for Compose-oriented deployment skills that name the selected service, profile, or source path. Follow each matching skill's directly linked deployment/configuration references only as needed. Treat those skills as intent and operational-constraint evidence, never as higher priority than the checked-in Docker source. Record a conflict or behavior found only in a deployment skill in the ledger and resolve it explicitly; do not silently invent Helm behavior from stale operational documentation.

### 2. Resolve every deployment comment

Read [references/directives.md](references/directives.md) whenever the inventory reports a directive or deployment-related comment. Stop and report malformed directives without editing `deploy/docker`; resume only after the source owner corrects them. Record every valid directive and unstructured deployment comment in the parity ledger.

Apply this precedence:

1. Explicit user requirement
2. Explicit `helm-sync` directive
3. Effective Compose behavior and associated files
4. Existing Helm convention
5. Kubernetes-safe default

Stop for clarification when two higher-priority requirements conflict or a comment is not precise enough to implement safely.

### 3. Create the parity ledger before editing

Create a temporary ledger in the working response or `/tmp`, not a tracked repository file:

| ID | Source evidence | Required behavior | Helm target | Status |
|---|---|---|---|---|
| S001 | `path:line` | Image/command/env/port/storage/probe/etc. | chart + values/template | pending |
| D001 | `path:line` | Directive or deployment comment | chart + resource/value | pending |

Add rows for every changed source fact and every affected service, profile, config, script, launcher-derived variant, deployment-skill constraint, named volume, network-facing port, dependency, credential, health check, GPU/resource setting, and lifecycle rule. A source deletion needs explicit remove-or-retain reasoning. Mark a row complete only after locating the rendered Helm representation or recording an intentional, directive-backed divergence.

### 4. Update leaf charts first

Read [references/translation-rules.md](references/translation-rules.md) before editing templates or values. Implement the ledger in this order:

1. Leaf service chart defaults and bundled `configs/`, `files/`, or scripts
2. Workload, Service, ConfigMap/Secret, PVC, RBAC, and helper templates
3. Service umbrella dependency and values wiring
4. Every developer/industry profile that consumes or overrides the service
5. Profile templates, configs, Ingress, secrets, and mode/hardware override values

Use the nearest maintained sibling chart as the structural pattern for a new chart. Include `Chart.yaml`, `values.yaml`, `_helpers.tpl`, the correct workload kind, required supporting resources, SPDX headers consistent with neighboring deploy files, an `enabled` gate, stable labels/selectors, and parent dependency wiring. Do not add a README or NOTES file unless operational output genuinely requires it.

### 5. Check value shadowing and endpoint rewrites

Search all consumer values files for each changed leaf value. YAML maps merge, but lists replace; a profile-level `env`, `extraEnv`, ports, volume mounts, tolerations, or similar list can silently shadow updated child defaults. Update every replacing list or refactor to a single source without breaking the public values contract.

Translate inter-container endpoints to Kubernetes Service DNS. Never preserve `localhost`, host IPs, Compose aliases, published host ports, or `container_name` as peer-service discovery unless an explicit directive documents why Kubernetes needs that behavior.

### 6. Propagate chart metadata

Follow the repository's release/version policy rather than inventing a version. When a child chart version changes:

1. Update every direct parent's dependency version.
2. Continue transitively through all umbrella/profile charts.
3. Regenerate affected `Chart.lock` files with Helm when available.
4. Review all values files named in the affected profile, including mode, hardware, node-port, endpoint, and ingress overrides.

Do not hand-edit a lock digest. If Helm is unavailable, update author-owned metadata only when correct and report lock regeneration as a blocking validation gap.

### 7. Validate without deploying

Read [references/validation.md](references/validation.md) after edits. Start with the bundled offline structural validator:

```bash
python3 skills/vss-generate-helm-charts/scripts/validate_chart_structure.py \
  --repo-root . --chart deploy/helm/services/CHART --recursive
```

The validator returns `0` when no errors exist, `1` for chart errors (or warnings under `--strict-warnings`), and `2` for an invalid chart request. Treat warnings as review items even when the exit code is `0`.

Then run available offline render checks for every changed leaf and transitive consumer with every relevant values/override combination. Render to a temporary directory. Never connect to a cluster and never substitute a live install for static validation.

Re-run `compose_helm_context.py` against the same source scope and reconcile the final diff with the ledger. Inspect `git diff --check` and `git diff -- deploy/helm skills/vss-generate-helm-charts` before completion.

## Definition of done

Complete only when all of the following hold:

- Every source fact, directive, and deployment-related comment has a completed ledger row.
- Every affected service exists in the correct leaf chart and every intended profile enables/configures it.
- Images, commands, args, env, secrets, ports, endpoints, storage, configs, probes, resources/GPU settings, security context, lifecycle, and dependency behavior are represented with Kubernetes semantics.
- Child defaults and all profile overrides agree; no replacing list hides a required update.
- Local dependency versions and lock files are consistent, or an unavailable Helm binary is reported as an explicit blocker.
- All available structural, lint, template, and schema checks pass across relevant values combinations.
- No deployment or runtime mutation occurred.

## Error handling

- **Malformed directive:** stop before edits; report its file and line and the accepted grammar.
- **Ambiguous or missing chart target:** inspect dependency consumers and sibling layout; if still ambiguous, request a target decision instead of creating duplicate charts.
- **Unsupported Compose primitive or host dependency:** choose a Kubernetes-native replacement only when semantics are clear; otherwise record a blocker. Never silently drop it.
- **Missing env/config/input file:** report the missing path and do not guess its contents or defaults.
- **Secret in source:** preserve the variable/Secret contract without copying the value; flag any checked-in plaintext credential separately.
- **Static tool unavailable:** continue with remaining checks, list the exact skipped gate, and do not describe the chart as fully validated.
- **Render/lint/schema failure:** keep the failing command and concise error, fix the earliest root cause, and rerun all affected consumers.
- **Unrelated dirty Helm files:** preserve them and restrict edits. Stop if correct synchronization would overwrite an overlapping user change.
