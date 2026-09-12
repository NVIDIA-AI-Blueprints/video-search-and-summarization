# AMC Calibration Parameters

Use this reference only when choosing an evidence-driven change during explicit tuning. It covers core tracklet-based AMC controls with implementation-backed behavior. It intentionally excludes ancillary settings that do not help diagnose AMC calibration.

## Runtime Contract

Treat the running service as authoritative for field availability and defaults:

1. Fetch `GET /v1/config/defaults` before attempt 1 and read its `config_params` object.
2. Confirm field names and types in `/openapi.yaml`, falling back to `/openapi.json`.
3. Tune only a field that is both returned by the running service and documented below. Leave an unknown field unchanged and report it rather than guessing.
4. Build every request from the complete runtime-default `config_params` object plus frozen dataset inputs and the current intentional changes.

The example defaults below are for orientation only. Do not hard-code them over values returned by the running service.

## Core Parameter Map

| Field | Example default | What it controls | More permissive or more data |
|---|---:|---|---|
| `iterations` | `2000` | Maximum Hyperopt evaluations for single-view camera-parameter search | Increase to search more candidates at greater runtime. It does not directly repair multi-view matching. |
| `min_length` | `90` | Minimum tracklet length in frames | Lower to retain shorter tracks. |
| `min_moving_dist` | `2.0` | Minimum cumulative movement required to retain a tracklet | Lower to retain tracks with less total movement. |
| `min_mean_moving_dist` | `0.01` | Minimum average movement per tracked frame | Lower to retain slower or nearly stationary tracks. Do not reduce to zero by default. |
| `min_matching_points` | `100` | Minimum matched samples required before scoring a candidate tracklet pair | Lower to allow candidates with shorter temporal overlap. |
| `min_dtw_score_threshold` | `25.0` | Maximum accepted dynamic-time-warping distance, despite the `min_` name | **Increase** to loosen. The implementation rejects a candidate when `dtw_score` is greater than this value. |
| `min_distance_score_threshold` | `1.2` | Maximum distance score used when counting supporting candidate matches, despite the `min_` name | **Increase** to loosen. A candidate contributes when its distance score is below this value. |
| `max_distance_score_hungarian_threshold` | `0.8` | Maximum distance allowed after Hungarian assignment | Increase to retain more assigned pairs. |
| `min_matched_tracklets` | `6` | Minimum number of supporting tracklets required for an initial cross-camera candidate | Lower to admit sparse candidates, but never below `3`. |
| `max_matched_tracklets_dist` | `2.0` | Maximum distance allowed when retaining matched tracklets for multi-view calibration | Increase to retain slightly farther matches. |
| `min_tracklet_dist` | `3.0` | Pixel-displacement gate between retained samples in a matched tracklet pair | Lower to retain denser samples. A sample is skipped only when both cameras moved less than this many pixels since the last retained sample. |
| `min_matched_points` | `100` | Minimum matched 2D points required by robust two-view sampling | Lower when otherwise plausible samples repeatedly fail the point-count gate. |
| `inlier_threshold` | `10.0` | Per-camera reprojection-error cutoff, in pixels, for classifying an inlier | Increase to admit noisier points. |
| `min_inliers` | `0.6` | Minimum inlier ratio required for the robust two-view solution | Lower to accept a noisier solution; it must remain greater than `0` and at most `1`. |
| `reproj_err_threshold` | `25.0` | Maximum summed mean reprojection error across the two cameras for retaining a matched tracklet | Increase to retain noisier tracklet matches. |
| `skip_frame` | `10` | Bundle-adjustment frame stride; every Nth frame is used | Lower to use more observations at greater runtime and memory cost. |

Every permissive change can also admit false matches. Require improved weakest-pair coverage without an unacceptable increase in outliers, reprojection spread, maximum error, or bundle-adjustment cost.

## Choose by Failing Stage

Do not treat all parameters as interchangeable.

| Log stage or evidence | First adjustment to consider |
|---|---|
| Tracklets removed before cross-camera matching | Decrease the relevant `min_length`, `min_moving_dist`, or `min_mean_moving_dist` gate. |
| Candidate pairs lack enough overlapping samples | Decrease `min_matching_points`; decrease `min_tracklet_dist` only when slow-motion sampling removes points. |
| DTW or distance-scored candidates are rejected | Increase the specific maximum gate: `min_dtw_score_threshold`, `min_distance_score_threshold`, `max_distance_score_hungarian_threshold`, or `max_matched_tracklets_dist`. |
| Too few otherwise plausible cross-camera tracklets survive | Decrease `min_matched_tracklets`, never below `3`. |
| Robust two-view point, inlier, or reprojection gate fails | Decrease `min_matched_points`, increase `inlier_threshold` or `reproj_err_threshold`, or decrease `min_inliers`, according to the failing gate. |
| Bundle adjustment lacks observations | Decrease `skip_frame`. |
| Single-view camera-parameter search needs a larger evaluation budget | Increase `iterations`. |

Do not use a later-stage threshold to compensate for an earlier input or filtering failure. Prefer the alternate detector before loosening several matching stages at once.

## Choose the Adjustment Size

1. Prefer a rejected boundary explicitly reported in logs or artifacts. Do not infer it from accepted or post-filtered values. For a minimum gate, use the highest value that retains the nearest plausible rejected case. For an evidence-relative maximum gate, set the target to `nearest plausible rejected value * 1.10`; apply it only when it is at most `1.25 * baseline` and does not exceed a schema maximum. Otherwise leave that field unchanged. Do not target the worst outlier.
2. If no boundary is reported and the field has a skill-policy bound, move halfway from the current value toward that bound: `new = (current + bound) / 2`. Round an integer toward the bound and ensure it changes by at least `1`.
3. Once one value fails and another succeeds, bisect that bracket only when an attempt remains and a less-permissive working value would be useful. Select the winner from full metrics, not pass/fail alone.
4. Do not halve or double the raw value. For `iterations`, increase by at most 50% from the attempt-1 baseline, at most once automatically, and never exceed an upper bound supplied by the running schema.

The `min_matched_tracklets` floor is `3` because the downstream robust two-view solver samples three matched tracklet pairs. They can occur across the clips and do not require three people to be visible simultaneously. If fewer than three usable pairs survive, improve detection, coverage, or footage; lowering this threshold further cannot bypass the downstream solver.

## Skill Automation Guardrails

These are conservative skill-policy windows and limits for unattended retries, not AMC API validation ranges or implementation limits. Keep the running schema and explicit runtime validation authoritative. A fixed window is used when a meaningful dataset-independent operating band exists; dataset-dependent score and distance gates use evidence-relative limits instead.

| Field | Automation window or limit |
|---|---|
| `iterations` | Attempt-1 baseline to `min(schema maximum, 1.5 * baseline)`; at most one automatic increase. |
| `min_length` | `30-120` |
| `min_moving_dist` | `0.5-3.0` |
| `min_mean_moving_dist` | `0.001-0.02` |
| `min_matching_points` | `30-150` |
| `min_dtw_score_threshold` | Target `nearest plausible rejected DTW score * 1.10`; apply only within `1.25 * baseline` and the schema maximum. Require explicitly reported rejected-score evidence. |
| `min_distance_score_threshold` | Target `nearest plausible rejected distance score * 1.10`; apply only within `1.25 * baseline` and the schema maximum. Require explicitly reported rejected-score evidence. |
| `max_distance_score_hungarian_threshold` | Target `nearest plausible rejected assignment score * 1.10`; apply only within `1.25 * baseline` and the schema maximum. Require explicitly reported rejected-score evidence. |
| `min_matched_tracklets` | `3-8` |
| `max_matched_tracklets_dist` | Target `nearest plausible rejected tracklet distance * 1.10`; apply only within `1.25 * baseline` and the schema maximum. Require explicitly reported rejected-distance evidence. |
| `min_tracklet_dist` | `max(schema minimum, 0.75 * baseline)` to baseline; require evidence that slow-motion sampling removed otherwise plausible points. |
| `min_matched_points` | `30-150` |
| `inlier_threshold` | `5-25` |
| `min_inliers` | `0.35-0.75` |
| `reproj_err_threshold` | `10-60` |
| `skip_frame` | `1-15` |

Baseline means the runtime-default value used in attempt 1. Ignore a `schema minimum` or `schema maximum` term when the running schema does not supply it. If a dynamic policy's required rejected-value evidence is unavailable, do not change that field automatically. Never exceed a bound that the running schema does supply.

## Boundaries

- Never tune `layout_px_per_m`; it is a measured dataset input.
- Treat detector choice as a baseline comparison, not a numeric parameter change.
- Keep VGGT controls at their runtime defaults unless a separate VGGT-specific diagnosis is requested. Do not change them in response to AMC logs.
- Treat rectification model and distortion search settings as a separate media-preparation experiment. Changing image geometry requires a fresh project and revalidated alignment; do not mix it into an ordinary AMC parameter attempt.
- Never tune camera order, synchronization, source files, alignment points, or layout orientation.

## Adjustment Rule

For each follow-up attempt, record the failing stage, weakest camera pair, observed threshold/score evidence, selected field, old and new values, adjustment rule, expected effect, and false-match or runtime risk. Change one field or one tightly coupled group, then compare the complete result against the incumbent before continuing.
