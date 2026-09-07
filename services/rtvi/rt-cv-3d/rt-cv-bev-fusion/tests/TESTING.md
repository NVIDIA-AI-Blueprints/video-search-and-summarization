# MV3DT BEV Fusion — Automated Testing

Automated tests for the **`vss-rt-cv-mv3dt-bev-fusion`** container (the measurement-fusion
service). The service has no web API: on startup it writes `/tmp/fusion_ready`, then
subscribes to the Kafka topic **`mdx-raw`**, fuses the per-camera 3D measurements,
and publishes the fused result to **`mdx-bev`**. (The service also supports Redis, but
the tests exercise Kafka — the broker the warehouse deployment uses.)

The tests validate exactly that contract — the fusion math, and that the real container
consumes `mdx-raw` and produces correct `mdx-bev` output over Kafka.

## Tiers

| Tier | Marker | Needs | What it covers |
|------|--------|-------|----------------|
| Unit | `unit` | python only | Pure fusion logic. Fast, isolated. |
| Integration | `integration` | docker | Runs the **built image** + Kafka; injects frames into `mdx-raw`, asserts fused `mdx-bev`. |
| E2E | `e2e` | GPU + NGC assets | Full `warehouse-3d-app-mv3dt` deploy on the 4-cam sample. **Local/manual only — not run in CI.** |

## Running

```bash
pip install -r tests/requirements.txt
cd tests

pytest -m unit -v                                    # no docker
pytest -m integration -v --image-ref=<image>          # needs docker (Kafka)
pytest -m e2e -v --deploy-root <deployments-dir>      # GPU host, local only
```

CI can run unit and integration tests by installing `tests/requirements.txt` and
passing the image under test with `--image-ref` or `IMAGE_REF`.

## All tests

| Test | Tier | What it does (input → output) |
|------|------|-------------------------------|
| `test_fused_timestamp_is_mean_of_sensors` | unit | Per-sensor frames → fused frame timestamp = arithmetic mean of sensor timestamps |
| `test_bbox3d_coordinates_are_elementwise_mean` | unit | Same object across sensors → fused `bbox3d.coordinates` = element-wise mean (12 values) |
| `test_confidence_is_averaged` | unit | → fused `confidence` (and `bbox3d.confidence`) = mean across sensors |
| `test_object_type_majority_vote` | unit | Disagreeing types → fused type = majority vote |
| `test_object_type_tie_broken_by_confidence` | unit | Tied vote → broken by higher total confidence |
| `test_objects_aggregated_by_id` | unit | Two object ids seen by all sensors → two fused objects, merged by id |
| `test_fused_sensor_id_and_info_map` | unit | → fused `sensorId == "bev-sensor-1"`, `id == bucket key`, `info` = per-sensor RFC3339 timestamps |
| `test_ts_bucket_key_groups_same_instant_across_sensors` | unit | Same-instant frames share a timestamp bucket; a full 30-FPS frame apart separates |
| `test_element_wise_mean_empty_is_empty` | unit | Empty input → empty result (guard) |
| `test_element_wise_mean_zero_weights_fall_back_to_plain_mean` | unit | All-zero weights (no 2D boxes in payload) → plain mean, no division by zero |
| `test_default_fusion_method_is_gated_weighted` | unit | `FUSION_METHOD` defaults to `gated_weighted`; `average` and `gated_weighted` are the supported values |
| `test_unknown_fusion_method_is_rejected` | unit | `FUSION_METHOD=by-visibility` (typo) → `ValueError` at import, no silent fallback |
| `test_average_ignores_visibility_entirely` | unit | `average` + views at 0.2/0.9 → plain mean of both, gate not applied |
| `test_average_publishes_objects_no_view_sees_well` | unit | `average` + a single 0.0-visibility view → object still published |
| `test_average_does_not_weight_by_bbox_area` | unit | `average` + 10×10 and 20×20 boxes → plain mean, not weighted 1:4 |
| `test_low_visibility_view_is_refused` | unit | Views at 0.2 and 0.9 visibility → fused position comes from the 0.9 view alone |
| `test_object_dropped_when_no_view_is_visible_enough` | unit | Every view under `VISIBILITY_MIN` → object absent from the fused frame |
| `test_zero_visibility_is_refused_not_read_as_missing` | unit | Visibility exactly `0.0` → refused (regression: `0.0` is falsy and must not read as unreported) |
| `test_threshold_is_inclusive` | unit | Visibility exactly at `VISIBILITY_MIN` → admitted |
| `test_unreported_visibility_admits_the_view` | unit | Absent, empty or unparseable visibility → admitted, and no `visibility` on the fused object |
| `test_coordinates_weighted_by_bbox_area` | unit | 10×10 and 20×20 detection boxes → coordinates weighted 1:4 by area |
| `test_type_vote_and_confidence_ignore_refused_views` | unit | Two refused Forklift views + one admitted Person → type `Person`, confidence from the admitted view only |
| `test_fused_visibility_is_mean_over_admitted_views` | unit | Views at 0.6/1.0/0.2 → fused `info["visibility"]` = 0.8 (mean over admitted only) |
| `test_fusion_service_fuses_raw_to_bev[warehouse-sample]` | integration | Real image + Kafka: inject 60×4 per-sensor frames (real sample sensor ids) to `mdx-raw` → assert fused `mdx-bev` (count, `bev-sensor-1`, 4-sensor `info`, averaged coords, majority type, no dup ids) |
| `test_warehouse_pipeline_e2e` | e2e (local) | Deploy full stack on 4-cam sample → assert perception FPS, fusion `healthy`, and `mdx-mv3dt-raw`/`mdx-bev` topics growing |
