# Patch Reference: Behavior Analytics (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold **Behavior Analytics** (image `vss-behavior-analytics`; compose base service-key `vss-behavior-analytics-base`; profile override service-keys such as `vss-search-analytics` / `vss-behavior-analytics`) into a generated deployment: the `component_services:` block, the `ba_path` mode/entrypoint variant, the Step 6.5 patch specifics (invented-flag append to the `extends`-based profile service, base-compose + config-JSON materialization), and the config knobs the skill folds into `.env`. It is NOT a microservice contract.

> **Complete BA reference — intentional overlap with `patch-alerts.md`.** This file documents **all three** BA deployment paths so it is self-complete: **search-embedding**, **base 2D/3D analytics**, and the **alerts-incident** path (BA as the candidate-incident producer feeding `alert-bridge`). The alerts-incident path is **also** declared in `references/patch-alerts.md` (its `alert_source=cv-verification` case), which stays **authoritative** for alerts generations. The overlap is intentional and duplicated on purpose — both files agree on the same `(key, file)` pair (`vss-behavior-analytics-alerts` in `developer-profiles/dev-profile-alerts/compose.yml`). Because Step 4 unions `component_services` by **unique `(key, file)`**, a key declared in both files collapses to a single allow-list entry — the duplication never double-applies a patch. Keep the two declarations in sync; if the alerts entry changes in one, mirror it in the other.

For the underlying Behavior Analytics integration contract — entrypoints/apps, processors and the `numWorkersFor*` gating, broker topics, config schema, env vars, and known constraints — read the skill-neutral pair files in the Behavior Analytics skill:

- `skills/vss-setup-behavior-analytics/references/integrate-behavior-analytics-service.md` — integration contract: entrypoints/processing modes, required peers, broker inputs/outputs, config knobs, known constraints.
- `skills/vss-setup-behavior-analytics/references/deploy-behavior-analytics-service.md` — standalone deployment contract: image, config-source, startup, verify, tear-down.
- Runtime config detail: `configuration.md`, `dynamic-config.md`, `dynamic-calibration.md` (same skill folder).

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 1** tag-matches the user's capability description ("spatial analytics", "behavior / trajectory tracking", "ROI / tripwire events", "proximity / restricted-area incidents", "candidate incidents for alert verification", "video-embedding downsampling for search") against the catalog tags for this microservice.
- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn the upstream compose service-keys Behavior Analytics owns and the `ba_path` variant (alerts-incident vs search-embedding vs base analytics-2d). Step 4 unions this block with the other selected microservices' patch files — by unique `(key, file)`, so the alerts-incident entry shared with `patch-alerts.md` collapses to one — and writes the flat allow-list to `allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies the patches in the "Patch specifics" section below to the patched copies under the build directory's `patched/` tree.

> **Relationship to `patch-alerts.md`.** For the alerts pipeline, BA is pulled in by the Alert Microservice's `alert_source=cv-verification` case. That case in `patch-alerts.md` stays **authoritative** for alerts generations and also declares `vss-behavior-analytics-alerts` + materializes its config (its own `§ Patch 3`); this file mirrors the same entry for completeness (see the intro note — union by unique `(key, file)` makes the overlap harmless). The RT-CV perception producer that feeds BA on the raw topic is owned separately (`skills/vss-deploy-detection-tracking-2d/`; its patch machinery, including the Patch 0 model-staging/host-prep, currently lives in `patch-alerts.md` pending a dedicated `patch-rt-cv.md`).

## component_services block

Behavior Analytics **owns** the `vss-behavior-analytics-base` service (the thin, `extends`-only base) and exactly one profile override service-key per generation, selected by the `ba_path` variant. The base is never brought up on its own — a profile service `extends:` it and overrides `command`, `container_name`, `profiles`, and the config volume — so the base compose file MUST be copied into the patched tree for `extends:` to resolve (Patch 3 below).

```yaml
component_services:
  # Base service — always copied so the profile override's `extends:` resolves. It carries
  # no `profiles:` of its own and is not brought up directly; it exists to be extended.
  - key: vss-behavior-analytics-base
    file: services/analytics/behavior-analytics/compose.yml
    role: Behavior Analytics base (image + host-network + default config mount + default 2D command); extended by the profile override, never run standalone.
    required: true

  # Profile override — exactly one case per generation, chosen in Step 4. Each case is an
  # `extends`-based service that overrides command (entrypoint/app), config volume, and
  # container_name. Mode within an app (incident vs behavior vs embed) is a config decision
  # (`numWorkersFor*`), NOT a compose service swap — see integrate-behavior-analytics-service.md.
  - variants:
      key: ba_path
      cases:
        # Alerts pipeline — the candidate-incident producer feeding alert-bridge. Runs
        # SearchAndAlertsApp with incident generation on; emits mdx-incidents / mdx-frames.
        # MIRROR of patch-alerts.md's `alert_source=cv-verification` case (authoritative there);
        # duplicated here for completeness. Union is by unique (key, file) → collapses to one.
        alerts-incident:
          - key: vss-behavior-analytics-alerts
            file: developer-profiles/dev-profile-alerts/compose.yml
            role: Behavior Analytics (incident path) — turns CV metadata on mdx-raw into candidate incidents/alerts (mdx-incidents / mdx-alerts) with a `category` that maps to alert_type_config.json.
        # Search pipeline — video-embedding downsampling producing mdx-embed-filtered.
        # No CV incident path; consumes chunked embeddings (mdx-embed) from the embedding producer.
        search-embedding:
          - key: vss-search-analytics
            file: developer-profiles/dev-profile-search/compose.yml
            role: Behavior Analytics (search path) — downsamples/filters chunked video embeddings (mdx-embed → mdx-embed-filtered) for the archive-search index.
        # Base 2D/3D analytics without the alerts or search profiles (behaviors + frame enhancement).
        analytics-2d:
          - key: vss-behavior-analytics
            file: services/analytics/behavior-analytics/compose.yml
            role: Behavior Analytics (base 2D analytics) — main_analytics_2d_app; behaviors + frame enhancement / incidents on 2D world-plane coordinates.

  # Peers Behavior Analytics needs but that are owned by OTHER component sets — NOT relisted here
  # (each is carried by its own microservice's block when that microservice is selected):
  #   - kafka, redis, elasticsearch, kafka-topic-init-container, logstash  -> ELK (integrate-elk.md)
  #   - the upstream CV/perception producer on mdx-raw (RT-CV / perception-alerts)
  #                                                       -> RT-CV (patch-alerts.md cv-verification, pending patch-rt-cv.md)
  #   - the video-embedding producer on mdx-embed (search path only)       -> video-embedding (integrate-vss-deploy-video-embedding.md)
  #   - the downstream VLM-as-verifier consumer of mdx-incidents            -> Alert Microservice (patch-alerts.md)
```

> **`ba_path` case vocabulary.** `alerts-incident` reproduces the alerts profile (incident generation on; `numWorkersForIncidentGeneration>0`, behavior/embed zeroed), `search-embedding` reproduces the search profile (`numWorkersForEmbedFiltering>0`, incident/behavior zeroed), `analytics-2d` is the base analytics app. The three are mutually exclusive per generation because the profile overrides share `container_name: vss-behavior-analytics`. Within `SearchAndAlertsApp` the incident/behavior/embed split is a **config** decision (`numWorkersFor*`), not a compose swap — do not model it as a variant here (see `integrate-behavior-analytics-service.md § Configuration & Modes`).

## Patch specifics (Step 6.5)

Applied to patched copies under `<BUILD_DIR>/patched/`; the upstream tree is never modified.

### Patch 1 — invented flag

The profile override service (`vss-behavior-analytics-alerts` / `vss-search-analytics`) gates on the upstream profile flags (e.g. `bp_developer_alerts_2d_cv`, `bp_wh_2d`), so `docker compose up` without `--profile` starts nothing. Step 6.5 appends the per-generation invented flag (e.g. `bp_developer_at_1`) to the chosen override service's `profiles:` list in the patched copy (additive — existing upstream flags stay). The base `vss-behavior-analytics-base` carries no `profiles:` and is never flagged; it is present only to satisfy `extends:`.

### Patch 3 — materialize base compose + profile config (so `extends:` resolves)

The profile override uses compose `extends:` against the base file. Step 6.5 MUST copy the extended base and the profile config JSON into the patched tree, or `extends:` fails to resolve at project-load:

- `services/analytics/behavior-analytics/compose.yml` (the `vss-behavior-analytics-base` definition the override `extends:`) → patched tree.
- The profile config JSON the override bind-mounts to `/resources/vss-behavior-analytics-config.json`:
  - `alerts-incident` → `developer-profiles/dev-profile-alerts/vss-behavior-analytics/configs/vss-behavior-analytics-config.json`
  - `search-embedding` → `developer-profiles/dev-profile-search/vss-search-analytics/configs/vss-search-analytics-${STREAM_TYPE}-config.json`
  - `analytics-2d` → `services/analytics/behavior-analytics/configs/vss-behavior-analytics-config.json`

(The **alerts-incident** config is also materialized by `patch-alerts.md § Patch 3` — the same host file. Whichever patch file the alerts generation runs, the copy target is identical; the duplication is intentional so this file stays self-complete. Keep the two in sync.)

### Patch 4 — neutralize nested `include:`

The `developer-profiles/dev-profile-*/compose.yml` files and the `extends:` chains pull in siblings via relative paths. Strip/neutralize any nested `include:` in copied composes and let the build's top-level `compose.yml` be the single include orchestrator. Record dropped includes in `PATCHES.md`. (Same rule as `patch-alerts.md § Patch 4` / `standalone-compose-patches.md`.)

## Config knobs the skill folds into the deployment

Behavior Analytics is broker- and config-driven — broker endpoints, topics, mode selection, and all tuning live in the **mounted config JSON**, not env (see `integrate-behavior-analytics-service.md § Environment Variables`). The skill's Step 6 generation MUST ensure:

- **Mode matches the `ba_path`.** The config's `numWorkersFor*` keys select which processors run: `alerts-incident` enables `numWorkersForIncidentGeneration` (behavior/embed zeroed); `search-embedding` enables `numWorkersForEmbedFiltering` (incident/behavior zeroed). Omitting a `numWorkersFor*` key defaults to `0` (opt-in) — the config must explicitly enable the wanted paths.
- **Every enabled processor's sink topic is mapped in `kafka.topics`.** A processor with worker count `> 0` writes to its sink every batch; an unmapped topic raises `Could not find a kafka topic with key: <name>` at first batch. Map exactly the topics the enabled processors touch (alerts: `raw`/`incidents`/`frames`; search: `embed`/`embedFiltered`).
- **Broker URLs resolve on host network.** The config JSON's `kafka.brokers` / `redisStream.host` / `mqtt.host` reference `${HOST_IP}` (BA runs `network_mode: host`); pre-resolve `${HOST_IP}` during env-folding so dry-run (Step 7) has zero unexpanded tokens.
- **`category` ⇄ `alert_type_config.json` (alerts path).** An emitted incident whose `category` has no `alert_type` entry is never VLM-verified downstream — keep BA's emitted categories in sync with the verifier config the Alert Microservice mounts (`patch-alerts.md § Patch 3`).
- **Object-type casing.** Incident object-type knobs (e.g. `fovCountViolationIncidentObjectType`) compare with exact `==` against the detector's emitted label — match the RT-DETR/GDINO lowercase (`person`), or no incidents fire.
- **Calibration.** With no `--calibration` file BA uses `CalibrationI` (image-plane, no perspective); supply a typed calibration matching the deployed sensors, or push one at runtime via `mdx-notification`. See `dynamic-calibration.md`.

## Emitted shape

The patched profile-override block (plus the copied base for `extends:`) is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. See the `## Example Compose Snippet` in `integrate-behavior-analytics-service.md` for the full upstream base + `extends`-override block this is patched from, and `references/standalone-compose-patches.md` for the generalized Patch 0–4 pseudocode.
