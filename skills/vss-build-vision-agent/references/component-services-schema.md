# Component Services Schema

This document defines the structured `component_services:` block that every `integrate-<microservice>.md` carries inside its `## Required Peer Services` section, and the per-generation `build-output/allow-list.yml` sidecar that `vss-build-vision-agent` synthesizes from those blocks.

## Why this exists

A microservice's "Required Peer Services" prose tells a human reader which peers a service expects to find on the network. But the `vss-build-vision-agent` skill needs a machine-readable list of **upstream compose service-keys** to drive Step 6.5 (Patch 1 — flag insertion, Patch 2 — depends_on strip). The structured block sits alongside the prose and gives the skill that list without re-parsing English.

The skill consumes the blocks at **Step 4** (architecture proposal), unions them across the candidate services for the user's profile, resolves any `variants:` branches against the user's chosen deployment shape, and writes the resulting flat list to `build-output/allow-list.yml`. Step 6.5 then reads only this sidecar — never the catalog, never the prose.

This decouples profile composition from the skill's source files: a new profile shape never requires editing `microservice-catalog.md` or `SKILL.md`. It only requires that the participating `integrate-*.md` files declare the right component_services entries and variant keys.

## Schema — `component_services:` block

The block lives at the bottom of `## Required Peer Services`, as a fenced ` ```yaml ` block. It enumerates every upstream compose service-key the microservice itself owns, plus the peers it requires from other microservices' integrate docs.

```yaml
component_services:
  # Top-level entries: services this integrate doc OWNS (defined in this microservice's
  # upstream compose tree). The skill adds these to the allow-list whenever this
  # microservice is selected.
  - key: <upstream-compose-service-key>
    file: services/<...>/docker-compose.yaml
    role: <short-noun-phrase>            # human-readable purpose; not parsed
    required: true                        # default true; false means user MAY opt out
  # Variant branches: when a microservice has sibling services that share container_name
  # (e.g. sensor-ms vs sensor-ms-2d/-3d/-mv3dt all use container_name vss-vios-sensor),
  # exactly one variant is chosen per generation based on the deployment_shape selected
  # in Step 4. Each variant lists its own service-keys.
  - variants:
      key: <selector-name>                 # e.g. sensor_topology, streamprocessing_topology
      cases:
        <case-name>:                       # e.g. rtsp-and-uploaded, warehouse-2d, warehouse-3d
          - key: <service-key>
            file: services/<...>/docker-compose.yaml
            role: <short-noun-phrase>
        <other-case-name>:
          - key: <service-key>
            file: services/<...>/docker-compose.yaml
```

### Field semantics

| Field | Required | Meaning |
|---|---|---|
| `key` | yes | Exact upstream compose service-key as it appears in the upstream YAML (the dict-key under `services:`). Must match the literal upstream string — Step 6.5 string-matches against compose service-keys when applying Patch 1. |
| `file` | yes | Repo-root-relative path to the upstream compose file that defines this service. Used by Step 6.5 to scope the patch to one file. |
| `role` | recommended | One-line description for human readers. Not parsed. |
| `required` | optional (default `true`) | When `false`, Step 6.5 of the skill MAY drop the service from the allow-list if Step 4 architecture choices exclude it. Use sparingly. |

### Variant branches

Use `variants:` when one microservice has sibling compose services that **cannot coexist** in the same project (most commonly because they share a `container_name`). The selector key (e.g. `sensor_topology`) is also the question the skill asks the user in Step 4 ("Which sensor topology — RTSP-and-uploaded video, warehouse-2d, warehouse-3d, or warehouse-mv3dt?"). The chosen case's services land in the allow-list; the other cases' services are excluded.

Variant cases are not arbitrary strings — they should match the `deployment_shape` vocabulary the skill uses when posing the question to the user in Step 4. The skill validates that the chosen `deployment_shape` resolves to at least one case in every `variants:` block it encounters during Step 4 union.

## Sidecar — `build-output/allow-list.yml`

Written by the skill at Step 4 immediately after the user confirms the architecture proposal. Read by Step 6.5.

```yaml
flag: bp_developer_<profile-id-snake>     # the fresh per-generation flag invented in Step 6
deployment_shape: <shape-name>            # the variant-case selector from Step 4 (e.g. streaming-and-uploaded-dense-captioning)
services:
  - key: <service-key>
    file: services/<...>/docker-compose.yaml
  - key: <service-key>
    file: services/<...>/docker-compose.yaml
  # ...flat list, one entry per allow-listed service
```

The sidecar is the **single source of truth** for which services get patched in Step 6.5. It is generated, committed nowhere, and overwritten on every generation.

## Patcher behavior (Step 6.5)

The patcher reads `build-output/allow-list.yml` and applies two patches to each compose file under `build-output/patched/`:

**Patch 1 — flag insertion.** For each `(key, file)` pair in `services:`, locate the compose service-key in the patched copy of `file` and append the sidecar's `flag` to that service's `profiles:` list. Handle both inline (`profiles: [a, b]`) and block-style (`profiles:\n  - a\n  - b`) YAML.

**Patch 2 — depends_on strip.** For each allow-listed service, walk its `depends_on:` block and apply this rule per peer:
- Peer is **defined** in the patched compose tree (anywhere) — keep the entry, regardless of `required:` value.
- Peer is **undefined** AND carries `required: false` — strip the entry.
- Peer is **undefined** AND does NOT carry `required: false` — patcher errors and reports the allow-list as inconsistent (the user must either add the peer to a different microservice's component_services, or set `required: false` upstream).

**`container_name` collisions are impossible by construction** as long as each `variants:` block resolves to at most one service-key per `container_name`. The patcher does not deduplicate.

## Discovery

This schema is referenced from:
- `references/microservice-catalog.md` (the "How the skill uses this file" section points readers here)
- Every `integrate-<microservice>.md` § Required Peer Services (the YAML block lives there)
- `SKILL.md` Step 4 (the skill emits the sidecar based on these blocks)
- `SKILL.md` Step 6.5 (the patcher reads the sidecar)
