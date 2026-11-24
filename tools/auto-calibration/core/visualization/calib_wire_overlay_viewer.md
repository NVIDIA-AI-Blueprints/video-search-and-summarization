# Calibration Floor-Grid + Reconstructed-Object Viewer

`core/visualization/calib_wire_overlay_viewer.py`

A browser-based, tiled multi-camera viewer for **verifying multi-view (MV)
calibration** on an Auto-Magic-Calib (AMC) project output. It overlays the
MV-calibrated world floor grid on each camera's video and, for every 2D person
detection, reconstructs a **virtual 3D object** (pole, cylinder, or 3D box) by
back-projecting the detection into the world and re-projecting it. Miscalibration
shows up directly as objects that lean, float, or sink relative to the people in
the video.

---

## 1. Why this tool

**Original intent: MV calibration verification.**

After the AMC pipeline runs (SV calib → SV3DT → MV calib + bundle adjustment),
we get a per-camera world→pixel projection matrix `P` in a shared world frame
(`results_ba_scaled_world/camInfo_*.yaml`, key `projectionMatrix_3x4`). The
question is: **is that calibration actually correct across all cameras?**

Numbers (reprojection error, etc.) don't give an intuitive, per-camera,
per-region sense of whether the geometry is right. This tool makes it visual:

- If the MV calibration is good, a shared world floor grid projects onto the
  actual floor in every camera, and a virtual object reconstructed from a
  person's feet/head sits correctly on that person (vertical pole, upright
  cylinder, or fronto-parallel box).
- If a camera is miscalibrated (wrong tilt/roll/pan/height/position), its floor
  grid slides off the floor and its reconstructed objects lean or are mis-scaled —
  immediately visible against the video, and consistent across the tiled views.

It doubles as a **sensitivity tool**: you can inject a known pose error into any
camera (`--perturb`) and watch how much the overlay degrades, to build intuition
for how much calibration error is tolerable.

It also has an additive **non-interactive virtual-GT dump mode** for pipeline
post-processing. This mode requires no detections or body keypoints: it places a
1.75 m pole at every 1 m floor-grid intersection and writes one background +
grid + poles PNG per camera. The original browser viewer is unchanged.

---

## 2. What it shows

Per camera tile (all cameras tiled in one page, master-clock synced):

| Overlay | Color | Meaning |
|---------|-------|---------|
| Floor grid (z=0) | green | World floor grid projected via that camera's MV `P` (opacity adjustable) |
| Reconstructed pole | red | Vertical `(X,Y,0)–(X,Y,h)` per detection (see §4) |
| Cylinder / 3D box | magenta | Wireframe cylinder or box per detection (see §4.3–4.4) |
| 2D detection box | yellow (solid) | Raw detector bbox for that frame (opacity adjustable via bbox-opacity) |
| Recovered / expected 2D bbox | cyan (dashed) | Geometry-inferred full-height bbox when bottom is occluded (`center` snap + extend occluded; see §8). Shares bbox-opacity and line-width with the yellow box |
| Axis endpoints | object color | Filled dots at the reconstructed foot (`Z=0`) and head (`Z=h`) |
| Anchor marker | object color | Filled = keypoint-derived anchor, hollow = bbox fallback |

Each tile also prints the MV camera parameters decomposed from `P`: focal length,
tilt (rx), roll (rz), azimuth/pan, mounting height — and, if perturbed, the
applied Δ and the effective change.

---

## 3. How it works (architecture)

- **Server**: a small stdlib `http.server` (`discover_scene` → state → handler).
  It locates the MV camInfo dir, the `single_view_results/cam_XX/` dirs (videos +
  detections), builds the floor grid, projects it per camera, and serves:
  - `/config` — scene metadata + per-camera projected floor grid + `P_mv` (12 floats)
    + `ground_basis` (4 floats, for box orientation)
  - `/video?i=N` — range-served mp4 (`rectified.mp4` by default)
  - `/detections?i=N&min_conf=…` — per-frame detections (lazy, per camera)
  - `/overlays?grid_spacing=…&person_height=…` — recompute floor grid on the fly
- **Client** (single HTML page): plays the videos, and each animation frame draws
  the static floor grid + the per-frame reconstructed virtual objects + boxes on
  a canvas over the video. Object reconstruction is done **in the browser** from
  `P_mv` (cheap: one 3×3 inverse + a few mat-vecs per detection), so it responds
  instantly to snap/opacity/object-type controls and reflects any `--perturb`.
- **Optional expected-foot maps**: `generate_expected_foot_y_maps.py` tabulates
  `(head_u, head_v) → expected_foot_v` as one float32 TIFF per camera, plus a
  second 3-channel float32 TIFF for center-snap only:
  `(center_u, center_v) → (X, Y, 0)` (pole / cylinder / box bottom; stored as
  HxWx3 float32 `.npy`). With
  `--expected-foot-y-dir`, the browser uses bilinear map lookup for occlusion
  recovery and center-snap ground reconstruction, falling back to live geometry
  if a map is absent or incompatible.

**Design choices worth remembering**

- Floor grid is projected once (server) as 1 m unit segments so a partial grid
  survives when part of the plane is behind the camera; recomputed only when
  `grid-spacing`/`person-height` change.
- Poles are **not** a precomputed lattice (that was an earlier approach; it was
  too heavy at fine grid spacing and didn't reflect real people). Virtual objects
  are reconstructed per detection at runtime.
- Object type is selectable: **pole** (red), **cylinder** or **3D box** (magenta).
  All three share the same ground anchor `(X,Y)`; only the wireframe geometry
  differs.
- All overlays use the **MV** projection matrix. (An earlier single-view (SV)
  comparison overlay and a frame-difference "motion" highlight mode were removed
  to keep the tool focused on MV verification.)

---

## 4. The reconstruction math (per detection, per frame)

All three virtual objects share the same first step: recover a world ground
anchor `(X, Y)` from the detection, then build 3D wireframe geometry around
that point and project it through the camera's (possibly perturbed) `P`.

### 4.1 Ground anchor (shared by pole / cylinder / box)

Given a camera's `P = K[R|t]` (3×4) and an anchor pixel `(u,v)`:

1. **Back-project to a plane `Z = z0`.** For fixed `Z`, `P` reduces to a 3×3
   homography whose columns are `[P₀, P₁, P₂·z0 + P₃]`; invert it and map
   `[u,v,1] → (X,Y)`.

   | Snap (`--bbox-snap`) | Anchor pixel | Back-project plane |
   |------|-------------|--------------------|
   | `bottom` | foot | `Z = 0` |
   | `top` | head | `Z = h` |
   | `center` | bbox dead-center | `Z = h/2` |

   The snap only determines *where* the anchor ray hits the world (`center`
   back-projects the mid-box point to the mid-height plane, then derives
   `(X,Y,0)` and `(X,Y,h)` from it). The virtual object always spans the full
   height `Z = 0 … h` regardless of snap mode.

2. **Degenerate cases are skipped**: near-singular homography, intersection behind
   the camera (`depth ≤ 0`), non-finite `(X,Y)`, or intersection far outside the
   grid bounds (grazing rays).

Because back-projection and re-projection use the **same** `P`, a pose error in
`P` bends/shifts the reconstructed object — that deviation is the verification
signal.

### 4.2 Pole (default)

The simplest case: one vertical segment from the recovered ground point.

```
(X, Y, 0) ──► project ──► pixel p₀
(X, Y, h) ──► project ──► pixel p₁
draw line p₀–p₁
```

Drawn in **red**.

### 4.3 Cylinder

**Yes — a full 3D cylinder is built first, then projected as a wireframe.**

Given ground centre `(X, Y)`, height `h`, and radius `r` (`--cylinder-radius`,
default 0.3 m):

1. **Build the 3D cylinder** in MV world coordinates:
   - Bottom ring: `N = 32` points on a circle at `Z = 0`:
     `(X + r·cos θ, Y + r·sin θ, 0)` for `θ = 2πi/N`
   - Top ring: same circle at `Z = h`

2. **Project each 3D point** through `P` via standard pinhole projection
   (`projectWorld`). Points behind the camera (`depth ≤ 0`) are dropped.

3. **Connect projected neighbours** into 2D line segments:
   - Bottom ring: consecutive projected points → closed polygon
   - Top ring: same
   - Two **silhouette (outer) lines**: the vertical generators tangent to the
     tube, at the ground points offset by `±r` perpendicular to the
     camera→centre direction (`perp ⟂ (X−Cx, Y−Cy)`). No interior vertical seams
     are drawn — just the outline plus the two circles.

**Why sample the circle in 3D instead of drawing an analytic ellipse?**

A circle in 3D projects to a general conic (ellipse) in the image. Rather than
deriving the ellipse equation, we sample the circle in world space and project
each point. The correct ellipse falls out automatically from the pinhole model,
and partial occlusion (some arc behind the camera) is handled for free — only
segments whose endpoints both project successfully are drawn.

Drawn in **magenta**.

### 4.4 3D bounding box

Also built in 3D first, then projected as a 12-edge wireframe.

Given ground centre `(X, Y)`, height `h`, and half-footprint `r` (reuses
`--cylinder-radius`; full square side = `2r`):

1. **Orientation** — per camera, computed once at startup in Python from `P`
   (`_ground_basis_from_projection`):
   - Box local **x** = camera image-right vector (`Rᵀ · [1,0,0]`) projected onto
     the world floor (`z = 0`) and normalised
   - Box local **y** = 90° rotation of x on the floor (perpendicular)
   - This makes the box front face fronto-parallel to that camera's image plane
     ("x aligns with view x")

   Sent to the browser as `ground_basis = [ux, uy, vx, vy]`.

2. **8 corners** — four at `Z = 0`, four at `Z = h`:
   ```
   corner = (X + su·r·ux + sv·r·vx,  Y + su·r·uy + sv·r·vy,  Z)
   for su, sv ∈ {(+1,+1), (+1,−1), (−1,−1), (−1,+1)}
   ```

3. **12 edges** — bottom face (4), top face (4), verticals (4). Each edge is
   projected point-to-point; edges with a behind-camera endpoint are dropped.

Drawn in **magenta**.

### 4.4b Occlusion recovery for `center` snap (`--extend-occluded`)

When **extend occluded → foot** is enabled (checkbox or `--extend-occluded`),
`center` snap uses the **expected 2D bounding box** recovered from calibration
geometry instead of trusting a truncated detector box. Full derivation, visuals,
and caveats are in **§8**.

### 4.5 Anchor: keypoints override the bbox

Detections are sourced (preferring the richer file) from:

- `trajDump_Stream_0_3d.txt` — bbox **+ body keypoints** (pelvis, ankles, nose)
- else `Det-bboxes.log` / `Det-Stream_0.log` — bbox only

The keypoint foot/head logic is ported from AMC's
`convert_amc_dump_to_2d_detections.py`:

- **foot** (bottom snap) = intersection of the inter-ankle line and the
  nose→pelvis axis (or ankle midpoint), landing in the bbox lower region;
  `--foot-method {line_intersect, ankle_mean}`.
- **head** (top snap) = nose keypoint.
- **center** snap uses the 2D bbox dead-center only — no keypoint override
  (there is no single body joint at the box center), so it is robust on frames
  with no fresh pose.

**Keypoints are used by default whenever present**; the bbox center is only a
fallback (no keypoints in that row, joint below `--min-kpt-conf`, or the derived
point is out of region). No interpolation/fill-in is ever done — a frame without
keypoints simply uses the bbox.

> Note on pose refresh rate: AMC's `poseInferenceInterval` (DeepStream tracker
> `PoseEstimator`) controls how often BodyPose runs; `0` = every frame, `N` =
> every Nth, `-1` = only first frame. With a large interval, most frames have no
> fresh pose and fall back to bbox. `--null-stale-kpts` (off by default) drops
> held/repeated poses if you don't want frozen poses reused between refreshes.

---

## 5. Pose perturbation (sensitivity / what-if)

`--perturb CAM DX DY DZ DRX DRY DRZ` (repeatable) injects a pose error into one
camera's MV `P` only (floor grid + reconstructed objects for that tile change;
video and other cameras unchanged):

- `DX DY DZ` — camera-centre shift in MV-world metres.
- `DRX / DRY / DRZ` — rotation in **degrees, camera frame**: `DRX` = tilt/pitch,
  `DRY` = yaw/pan, `DRZ` = roll (about the optical axis). Applied as
  `R_new = R_delta · R`.
- Sign note: `+DRZ` yields roll of `−DRZ`.

The tile shows the applied Δ and the effective (tilt/roll/azimuth) change.

---

## 6. Usage

```bash
source ~/venv/amc/bin/activate
P=~/Downloads/auto-calib/projects/<project>/output

python3 ~/work/code/core/visualization/calib_wire_overlay_viewer.py "$P" --port 8010 \
  --virtual-object cylinder --cylinder-radius 0.3 \
  --bbox-snap bottom --grid-opacity 0.35 --grid-spacing 1.5 \
  --person-height 1.7 --foot-method ankle_mean
```

To precompute and use dense expected-foot lookup images:

```bash
python3 ~/work/code/core/visualization/generate_expected_foot_y_maps.py "$P" \
  --person-height 1.7 --output-dir "$P/expected_foot_y_maps"

python3 ~/work/code/core/visualization/calib_wire_overlay_viewer.py "$P" \
  --person-height 1.7 --expected-foot-y-dir "$P/expected_foot_y_maps"
```

### Static virtual-GT image dump (no detections required)

```bash
# AMC result
python3 core/visualization/calib_wire_overlay_viewer.py "$P" \
  --dump-virtual-gt \
  --virtual-gt-output-dir "$P/virtual_gt_overlays" \
  --person-height 1.75 --grid-spacing 1.0 --grid-opacity 0.5

# VGGT result; single-view backgrounds remain shared at output/single_view_results
python3 core/visualization/calib_wire_overlay_viewer.py "$P/vggt_results" \
  --sv-results-dir "$P/single_view_results" \
  --dump-virtual-gt \
  --virtual-gt-output-dir "$P/vggt_results/virtual_gt_overlays" \
  --person-height 1.75 --grid-spacing 1.0 --grid-opacity 0.5
```

The importable entry point is:

```python
from calib_wire_overlay_viewer import render_grid_pole_overlays
```

It uses `rectified.jpg` as the background, falling back to frame zero of the
staged video. It draws no camera parameters, detections, or keypoints. Shared
post-processing invokes this mode for both AMC and VGGT and writes:

```text
output/virtual_gt_overlays/cam_XX.png
output/vggt_results/virtual_gt_overlays/cam_XX.png
```

The generated manifest records image dimensions, person height, and each exact
projection matrix. A mismatched projection (including `--perturb`) disables the
map for that camera. Changing person height in the UI uses live geometry until
it matches the map height again.

Then open `http://127.0.0.1:8010/`. Most parameters are also live-adjustable in
the settings bar (grid-spacing, person-height, grid-opacity, line-width,
det-min-conf, bbox-snap, bbox-opacity, **object type**, **radius**, and the
**extend occluded → foot** checkbox in `center` snap).

**Save PNG** — the **⤓ Save PNG** button in the header dumps the current tiled
view (all cameras, current video frame + all overlays) as a single PNG download.
Each camera is scaled to **half** its native resolution; layout matches the
on-screen grid. Best used while paused on the frame you want. Filename:
`<project>_overlay_t<seconds>s.png`.

**Key options**

| Option | Meaning |
|--------|---------|
| `--virtual-object {pole,cylinder,box}` | per-detection wireframe shape (default: pole) |
| `--cylinder-radius` | cylinder radius / box half-footprint in metres (default: 0.3) |
| `--bbox-snap {center,bottom,top}` | bbox-center→Z=h/2 (default), foot→Z=0, or head→Z=h anchor |
| `--extend-occluded` | center-snap add-on: extend a bottom-occluded bbox down to the geometry-expected foot before taking the center (see §8) |
| `--kpt-gate-extend` | center-snap add-on (trajDump keypoints): gate geometry extension by pose evidence — extend only when the lowest trusted ankle sits well above the geometry-expected foot (occlusion/truncation); skip when the ankle already reaches it (see §7.8) |
| `--kpt-gate-tol` | gate tolerance as a fraction of bbox height (default 0.2; larger = extend less) |
| `--grid-spacing` / `--grid-opacity` | floor grid density / opacity |
| `--person-height` | object height `h` (m) |
| `--det-min-conf` / `--min-kpt-conf` | detection / per-joint confidence floors |
| `--foot-method {line_intersect,ankle_mean}` | keypoint foot construction |
| `--null-stale-kpts` | drop held/repeated poses (default: keep) |
| `--perturb CAM DX DY DZ DRX DRY DRZ` | inject pose error (repeatable) |
| `--video`, `--video-dir` | alternate video source (e.g. `peoplenet_3d.mp4`) |
| `--camparams-dir`, `--sv-results-dir` | override auto-discovered paths |
| `--expected-foot-y-dir` | directory of per-camera maps from `generate_expected_foot_y_maps.py` |
| `--x-min/--x-max/--y-min/--y-max`, `--margin` | floor grid bounds |
| `--port`, `--host`, `--no-open` | server options |

---

## 7. Expected 2D bounding box recovery

This section explains how the viewer infers a **full-height 2D bounding box**
when a person is only partially visible — typically the **lower body is occluded**
or cut off by the frame edge, so the detector box stops at the torso instead of
the feet.

### 7.1 The problem

A person detector outputs a 2D box `(bx, by, bw, bh)` in image pixels. For a
fully visible standing person, the box roughly spans head-to-foot. When the legs
are hidden:

- The **top** of the box still tracks the visible head/shoulders.
- The **bottom** of the box sits on the torso — *not* on the floor.
- Under `center` snap, the anchor is the box midpoint, which is too high.
- Back-projecting that midpoint to `Z = h/2` places the virtual object floating
  above the real person even when calibration is correct.

This is a **detection artefact**, not a calibration failure. The fix is to
**infer where the foot should be** using camera geometry and a fixed person
height `h` (`--person-height`).

### 7.2 Intuition: fixed height under a calibrated camera

For a given camera, mounting height and tilt (encoded in the MV projection
matrix `P`) determine how many **image pixels** correspond to one metre on the
ground and how that scale varies with image **y** (closer to the horizon → fewer
pixels per metre).

If we know:

1. A pixel on the **head** (top of the visible box), and
2. That the person is **h metres tall** standing on the floor (`Z = 0`),

then pinhole geometry gives a unique **expected foot pixel** directly below that
head in the world (same ground `(X, Y)`, different `Z`). No separate SV calib
file is needed — the same `P_mv` used for the floor grid and virtual objects
already contains focal length, tilt, and height.

### 7.3 Step-by-step algorithm

For each detection, with bbox `(bx, by, bw, bh)`, person height `h`, and camera
`P` (3×4 row-major):

| Step | What | Detail |
|------|------|--------|
| 1 | Head pixel | Top-edge center: `u_head = bx + bw/2`, `v_head = by` |
| 2 | Back-project to `Z = h` | Invert the plane homography for `Z = h` (columns `[P₀, P₁, P₂·h + P₃]`) and map `[u_head, v_head, 1] → (X, Y)` on the head plane |
| 3 | Foot in world | Vertical person: foot at `(X, Y, 0)` — same ground position, `Z = 0` |
| 4 | Re-project foot | Standard pinhole: `(X, Y, 0) → (u_foot, v_foot)` |
| 5 | Compare bottoms | Actual bbox bottom: `v_det = by + bh`. If `v_foot > v_det`, the detector box is **too short** (bottom occluded) |
| 6 | Build expected box | Keep top/width unchanged; extend bottom to `v_foot`. Expected box: `(bx, by, bw, v_foot − by)` |
| 7 | Anchor for reconstruction | Center of the **expected** box: `(bx + bw/2, (by + v_foot) / 2)`, then normal `center → Z = h/2` snap |

If `v_foot ≤ v_det`, the detector box already reaches at least as far down as
geometry predicts — **no extension** (the box is never shrunk).

Degenerate cases (singular homography, point behind camera, wild ground
intersection) are skipped silently for that detection.

> **Appendix A** derives an equivalent single homography for this pipeline and
> discusses when it collapses to a 1D map in image `y` only — including why
> that 1D shortcut can be too aggressive on real mounted cameras.

### 7.4 Visual layout

```
Image y ↑
        │
  by ───┼─── top edge (head)     ← trusted when upper body is visible
        │ ╔═══════════╗
        │ ║  detector ║  yellow solid — raw 2D det
        │ ║    box    ║
 by+bh ─┼─╚═══════════╝─── det bottom (torso — NOT the foot)
        │       :
        │       :  inferred extension
        │       :
 v_foot ┼─ - - - - - - - - - -   cyan dashed — recovered expected bottom
        │
```

Both boxes share the **same top-left and width**; only the **height** grows
downward when occlusion is detected.

### 7.5 UI controls and overlays

| Control | Effect |
|---------|--------|
| `bbox-snap = center` | Required — extension is an **add-on** to center snap only |
| **extend occluded → foot** checkbox | Enables steps 1–7 above (`--extend-occluded` from CLI) |
| **gate extension with keypoints** checkbox | trajDump-only add-on (`--kpt-gate-extend`): extend only when the lowest trusted ankle sits well above the geometry-expected foot; skip when the pose already reaches it. See §7.8. No effect without keypoints in the dataset |
| **gate-tol** slider | Gate tolerance (`--kpt-gate-tol`) as a fraction of bbox height; larger extends less |
| **bbox-opacity** slider | Controls opacity of **both** yellow (detector) and cyan (recovered) boxes |
| **line-width** slider | Same stroke width for both; cyan is **dashed** to distinguish it |

When extension applies, you see **two** boxes overlaid:

- **Yellow solid** — what the detector actually returned.
- **Cyan dashed** — the geometry-recovered full-height box used for anchoring.

The virtual object (pole / cylinder / box) is reconstructed from the cyan box
center, not the yellow one.

### 7.8 Keypoint-gated extension (`--kpt-gate-extend`)

Optional add-on to **extend occluded → foot** (center snap only). Visible in the UI
only when the dataset provides trajDump body keypoints (`has_kpts`).

**Why not a bbox-containment test?** BodyPose3DNet is a 3D-lifting model that
emits all 34 joints regardless of visibility: when the legs are occluded it
*hallucinates* the ankles/knees and places them **inside** the (truncated)
detector box, with normal confidence (~0.86 on the reference rig). Measured on
one camera, **93.5 %** of ankle keypoints land inside the bbox, so a "keypoints
inside the box → skip" rule suppresses ~89 % of genuinely needed extensions.
Raising `--min-kpt-conf` does **not** help — hallucinated joints keep high
confidence.

**What the gate actually does.** It compares pose evidence to geometry. Let
`efy` be the geometry-expected foot image-y (§7.3) and `ay` the lowest trusted
ankle image-y (`gy` in the detection payload). With bbox height `bh` and
tolerance `tol` (`--kpt-gate-tol`, UI **gate-tol** slider):

```
extend  iff  ay < efy − tol · bh        (ankle sits well ABOVE expected foot → truncated/occluded)
skip    iff  ay ≥ efy − tol · bh        (pose already reaches ~expected foot → box trustworthy)
```

| Condition | Extension |
|-----------|-----------|
| Lowest ankle well above expected foot | **Extend** — body doesn't reach full height in-frame |
| Lowest ankle reaches near expected foot | **Skip** — box already corroborated |
| No trusted ankle on this row | **Extend** — fall back to geometry |

`tol` is a fraction of bbox height (default **0.2**). It sets a **margin below
the expected foot** that the ankle must clear before extension runs: extend only
if the ankle sits at least `tol · bh` pixels *above* `efy`. Only the **ankle**
joints drive the gate; upper-body joints are ignored.

**Behavior across the range** (threshold = `efy − tol · bh`):

| `gate-tol` | Threshold | Effect |
|------------|-----------|--------|
| **0** | exactly `efy` | Extend whenever the ankle is even 1 px above the expected foot — almost always → same as **checkbox off** (pure geometry); steadiest cyan box |
| **0.2** (default) | `efy − 0.2·bh` | Ankle must be ~20% of bbox height above the foot to trigger extension |
| **large** (e.g. 0.35) | far above `efy` | Ankle must sit well above the foot → rarely extends → mostly skips; many detections near the threshold → noisy ankles flip extend/skip → most flicker |

Because `tol` is relative to bbox height, the same setting is a larger absolute
pixel band for near/large boxes and a smaller one for far/small boxes.

**Empirical split (reference rig).** On `project_20260709` cam_00, among
detections where geometry *would* extend the box downward, the gate-with-keypoints
checkbox changes how many actually extend vs skip:

| `gate-tol` | Extends | Skips |
|------------|---------|-------|
| 0.0 | ~100% | ~0% |
| 0.10 | ~81% | ~19% |
| 0.20 (default) | ~45% | ~55% |
| 0.35 | ~23% | ~77% |

At `tol = 0` the gate barely vetoes anything → behavior converges to
**checkbox-off** (pure geometry). Higher `tol` suppresses more extensions; the
mid-range (e.g. 0.10–0.20) is where many detections sit near the
`ay ≈ efy − tol·bh` threshold and per-frame ankle noise shows up as cyan-box
flicker. Exact percentages vary by camera/scene; treat this table as intuition,
not a guarantee.

> This is dataset-dependent by design: it is only offered when keypoints exist,
> and it degrades gracefully (geometry-only) on rows without a trusted ankle.

> ⚠️ **Trade-off — noisier recovery.** The gate keys off per-frame ankle
> keypoints, which jitter frame-to-frame (and can be hallucinated). Enabling it
> therefore makes the recovered **cyan** box flicker between extended and
> non-extended more than the smooth, geometry-only recovery. That instability is
> the cost of occlusion awareness and is accepted for now.
>
> **To calm it down, *lower* gate-tol** toward `0`: at `tol = 0` the gate extends
> on essentially everything geometry flags, which is the same as the
> **checkbox-off** (pure-geometry) behavior — the steadiest overlay. *Higher*
> gate-tol suppresses more extensions and, because many detections sit near the
> `ay ≈ efy − tol·bh` threshold, is where noisy ankles flip extend/skip and the
> cyan box flickers most. (Or just leave the gate off for the stable baseline.)

### 7.9 When to use it

**Good fit**

- Person visible from the waist/shoulders up; legs behind a counter, desk, or
  another object.
- Person clipped at the bottom of the frame.
- You want `center` snap (stable when keypoints are sparse) but need a plausible
  foot location.

**Poor fit**

- **Head** is occluded — we assume the bbox top ≈ head at `Z = h`.
- Person is sitting, crouching, or otherwise not standing — fixed `h` is wrong.
- True height differs a lot from `--person-height` — foot shifts proportionally.
- `bottom` or `top` snap with keypoints is more appropriate (ankle/nose anchors).

### 7.10 Relation to calibration

The recovery uses the **same MV `P`** as every other overlay. If `P` is wrong,
the inferred foot will be wrong too — but it will be **consistently** wrong with
the floor grid and virtual objects, which is still useful for MV verification.
The feature separates "bad calibration" from "truncated detector box" when
comparing yellow vs cyan: a large gap between them on a partially visible person
is expected; the cyan box is the hypothesis for where a full-height person
*should* project.

---

## 8. Inputs expected in the AMC output folder

- `**/results_ba_scaled_world/camInfo_*.yaml` — MV `projectionMatrix_3x4` (required)
- `single_view_results/cam_XX/rectified.mp4` — per-camera video
- `single_view_results/cam_XX/trajDump_Stream_0_3d.txt` — detections + keypoints
  (preferred), else `Det-bboxes.log` / `Det-Stream_0.log`

Detection files are required only for the browser viewer's reconstructed-person
objects. Static `--dump-virtual-gt` mode needs only scaled-world camera
parameters plus `rectified.jpg` or `rectified.mp4`.

Related helper: `core/visualization/extract_mv_camera_params.py` (standalone MV param
extractor used for the on-tile parameter readout).

---

## Appendix A — Head-pixel → foot-pixel as a precomputed homography

### A.1 Observation

§7.3 runs, per detection:

1. Top-edge center `(u_h, v_h)` → back-project to the head plane `Z = h`
2. Read world `(X, Y)` (vertical person: same ground position at `Z = 0`)
3. Re-project `(X, Y, 0)` → expected foot pixel `(u_f, v_f)`

Steps 1 and 3 are plane homographies induced by the same `P`. They compose into
**one** image-to-image homography — no need to touch world `(X, Y)` at runtime.

Let `p₀, p₁, p₂, p₃` denote the four columns of `P` (3-vectors). For a constant
world plane `Z = z₀`:

```
H(z₀) = [ p₀  p₁  p₂·z₀ + p₃ ]     (3×3)
```

Back-project to `Z = h` is `H(h)⁻¹`; project from `Z = 0` is `H(0)`. So:

```
G = H(0) · H(h)⁻¹
```

and

```
[ u_f ]       [ u_h ]
[ v_f ]  ~  G [ v_h ]
[  w  ]       [  1  ]
```

with `u_f = ···/w`, `v_f = ···/w`. This is **projective linear** (linear in
homogeneous coordinates), hence **rational** in pixel `(u, v)` — not an affine
line in `v_h` alone unless `G` has extra structure.

`G` depends on `P` and `h` only. It can be built once per camera at startup
(and recomputed when `--person-height` or `--perturb` changes). Each detection
then needs one 3×3 multiply instead of a 3×3 inverse plus a re-projection.

### A.2 When does `v_f` depend only on `v_h`?

Expanding the rational form:

```
v_f = (G₁₀·u_h + G₁₁·v_h + G₁₂) / (G₂₀·u_h + G₂₁·v_h + G₂₂)
```

`v_f` is independent of the top-center **x** (`u_h`) iff `G₁₀ = G₂₀ = 0`, giving
a true **1D** map:

```
v_f = (G₁₁·v_h + G₁₂) / (G₂₁·v_h + G₂₂)
```

A **LUT over image row** `v_h` (or four stored coefficients) is then exact.

| Case | `G₁₀`, `G₂₀` | `v_f` vs `u_h` (same `v_h`, `u` swept full frame width) |
|------|--------------|----------------------------------------------------------|
| Synthetic camera, roll = 0° | ≈ 0 | **0 px** spread — x truly irrelevant |
| Synthetic camera, roll = 15° | non-zero | **~20–40 px** spread |
| Real AMC `camInfo_*.yaml` (sampled) | non-zero | **~6–70 px** spread depending on camera |

So the shortcut “expected bottom `y` depends only on top `y`, not on top `x`”
holds **exactly** only in ideal no-roll geometry. On practical AMC mounts (pan,
tilt, and non-zero roll), horizontal head position **does** move the expected
foot row — by tens of pixels on some cameras. Treating recovery as 1D in `v_h`
only would be **aggressive** and would bias the cyan extended box left/right
of where the full `(u_h, v_h) → G` map places it.

The **general** precompute that is always correct: store `G` (9 floats) per
camera and evaluate with the actual top-center `(u_h, v_h) = (bx + bw/2, by)`.

### A.3 Relation to the current implementation

`generate_expected_foot_y_maps.py` evaluates the full 2D homography densely and
writes one float32 TIFF per camera. The viewer loads these with
`--expected-foot-y-dir`, serves their float32 values to the browser, and
bilinearly samples the map at each bbox top-center. Invalid rays are NaN.

The original explicit back-project / re-project chain remains as a correctness
fallback when no map is supplied, a map does not match the camera projection or
resolution, or the current person height differs from the generated height.
The implementation retains both `u_h` and `v_h`; it does not use the inaccurate
1D row-only shortcut.
