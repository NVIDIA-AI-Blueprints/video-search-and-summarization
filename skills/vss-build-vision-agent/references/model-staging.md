# RT-CV Detector Model Staging Gate

Stage the RT-CV **detector** ONNX onto the host before bring-up. This is an
**agent-performed** step gated on deployment: it runs at bring-up (SKILL.md
step 9) — after `resolved.yml` and the `references/data-directory.md` gate,
before `docker compose up`. A validate-only pass skips it (like the image
refresh); on such a run, report the detector as not-yet-staged and offer to stage
it on deploy or on request — never imply a manual, user-only step.

This is a **host-side step with no build-artifact footprint**: it does not touch
`resolved.yml`, `override.env`, or any `patches/` entry — it only downloads the
detector ONNX and sets file permissions. It is therefore cleanly removable: if
the RT-CV service later stages the detector itself inside ds-start, delete this
step and the mount contract is satisfied by the service instead.

Scope is the **detector only**. The Search SigLIP vision encoder is fetched by
ds-start phase 0 when `DS_MODEL_DOWNLOAD=auto` and is **not** staged here. The tracker ReID
model is baked into the RT-CV image (copied from an in-image path by the
container entrypoint), so it is not host-staged either.

## When it runs (and when it is a no-op)

Gate on the resolved `COMPOSE_PROFILES`. Only builds that carry an RT-CV
perception key need a staged detector:

| Perception key in `COMPOSE_PROFILES` | Foundation(s) | Detector to stage |
|---|---|---|
| `perception-alerts` | alerts `2d_cv` | GDINO **or** RT-DETR (ITS), per `MODEL_NAME_2D` |
| `perception-2d-fusion` | search, **and** combined alerts+search built on search | RT-DETR (warehouse) |

Skip entirely (no detector) for builds with no perception key — base, LVS, and
alerts `2d_vlm` (RT-VLM-only, no RT-CV).

## Credentials and human-in-the-loop

These are **gated NGC TAO** models. Prerequisites, handled before this step:

- `NGC_CLI_API_KEY` is set/normalized (`references/credentials.md`,
  `references/ngc.md`), and its org/team **entitlement** to these repositories
  was already proven by the artifact probe in `references/credentials.md`
  ("Profile-staged TAO/perception models"). A `401/403` there is a blocker — do
  not reach this step without an entitled key.
- Downloads are large (100s of MB–GB). This step is **idempotent**: it skips any
  target that already exists. In interactive runs, confirm before a fresh pull;
  in non-interactive/CI (`VSS_AUTO_DEPLOY=true`), run without prompting.

## Model matrix (refs, packaged artifact, staged destination)

Destinations are relative to `${VSS_DATA_DIR}/models` (bind-mounted into the
RT-CV container at `/opt/storage`). These refs mirror the authoritative
developer-profile staging in `deploy/docker/scripts/dev-profile.sh`; if upstream
bumps a version, update it from there rather than forking a third copy.

| Selector | NGC model ref | Packaged artifact | Staged destination |
|---|---|---|---|
| alerts, `MODEL_NAME_2D=GDINO` (default) | `nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm` | `mgdino_mask_head_pruned_dynamic_batch.onnx` | `gdino/mgdino_mask_head_pruned_dynamic_batch.onnx` |
| alerts, `MODEL_NAME_2D` != GDINO | `nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0` | `resnet50_trafficcamnet_rtdetr.fp16.onnx` | `rtdetr-its/model_epoch_035.fp16.onnx` (renamed) |
| search / combined | `nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2` (`--org nvidia`) | `rtdetr_warehouse_v1.0.2.fp16.onnx` | `rtdetr_warehouse_v1.0.2.fp16.onnx` (flat) |

The alerts default is GDINO; the profile is `DS_MODEL_FAMILY=rtdetr-gdino`, so
flipping `MODEL_NAME_2D` to RT-DETR is valid — re-run this step after such a
change to stage the other detector. Combined builds converge on the **single**
search RT-DETR (RT-CV is a singleton; combined does not stage GDINO).

## Stage

Run from the repository root, substituting `<name>` for the build directory.

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
ENV_FILE="$BUILD_DIR/override.env"

unquote() { local v="$1"; v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"; printf '%s' "$v"; }

DATA="$(unquote "$(sed -n 's/^VSS_DATA_DIR=//p' "$ENV_FILE")")"
COMPOSE_PROFILES="$(unquote "$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")")"
MODEL_NAME_2D="$(unquote "$(sed -n 's/^MODEL_NAME_2D=//p' "$ENV_FILE")")"
MODELS="$DATA/models"

# No-op for builds without an RT-CV perception key (base, LVS, alerts 2d_vlm).
case ",$COMPOSE_PROFILES," in
  *,perception-alerts,*|*,*|*,perception-2d-fusion,*) ;;
  *) echo "[skip] no RT-CV perception key in COMPOSE_PROFILES; no detector to stage"; exit 0 ;;
esac

: "${NGC_CLI_API_KEY:?NGC_CLI_API_KEY required to stage gated TAO detector models}"

# Download one TAO model version and place a named artifact at an absolute dest.
# Idempotent (skips if dest exists). TAO package layout is not version-stable,
# so fall back to a name search when the packaged path is absent.
#   $1 ngc model ref | $2 extract_dir | $3 packaged artifact basename
#   $4 dest abs path  | $5.. extra ngc args (e.g. --org nvidia)
stage_model() {
  local ref="$1" xdir="$2" art="$3" dest="$4"; shift 4
  if [ -s "$dest" ]; then echo "[skip] already staged: $dest"; return 0; fi
  echo "[stage] $ref -> $dest"
  mkdir -p "$(dirname "$dest")"
  local work; work="$(mktemp -d)"
  if ! ( cd "$work" && env NGC_CLI_API_KEY="$NGC_CLI_API_KEY" \
           ngc registry model download-version "$ref" "$@" ); then
    echo "[error] NGC download failed for $ref" >&2; rm -rf "$work"; return 1
  fi
  local src="$work/$xdir/$art"
  [ -s "$src" ] || src="$(find "$work" -type f -name "$art" -print -quit)"
  if [ -z "$src" ] || [ ! -s "$src" ]; then
    echo "[error] artifact '$art' not found after downloading $ref" >&2
    rm -rf "$work"; return 1
  fi
  mv -f "$src" "$dest"
  rm -rf "$work"
  echo "[ok] staged $dest"
}

# Alerts (2d_cv): stage the detector selected by MODEL_NAME_2D (Foundation default GDINO).
case ",$COMPOSE_PROFILES," in
  *,perception-alerts,*)
    case "${MODEL_NAME_2D:-GDINO}" in
      GDINO|gdino)
        stage_model \
          "nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm" \
          "mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm" \
          "mgdino_mask_head_pruned_dynamic_batch.onnx" \
          "$MODELS/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"
        ;;
      *)
        stage_model \
          "nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0" \
          "trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0" \
          "resnet50_trafficcamnet_rtdetr.fp16.onnx" \
          "$MODELS/rtdetr-its/model_epoch_035.fp16.onnx"
        ;;
    esac
    ;;
esac

# Search / combined (built on search): RT-DETR warehouse detector.
case ",$COMPOSE_PROFILES," in
  *,*|*,perception-2d-fusion,*)
    stage_model \
      "nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2" \
      "rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2" \
      "rtdetr_warehouse_v1.0.2.fp16.onnx" \
      "$MODELS/rtdetr_warehouse_v1.0.2.fp16.onnx" \
      --org nvidia
    ;;
esac

# Permissions. Directories must be traversable and WRITABLE by the RT-CV
# container (it runs as a non-matching UID and writes generated TensorRT
# `.engine` files back into this mounted tree on first boot). The ONNX inputs
# are read-only, so 644 (world-readable) is sufficient for a foreign-UID reader.
find "$MODELS" -type d -exec chmod 777 {} +
find "$MODELS" -type f -name '*.onnx' -exec chmod 644 {} +
```

## Engine scratch directory (alerts)

Separate from the `models` mount, alerts RT-CV builds its TensorRT engines into
an engines directory (e.g. `${VSS_APPS_DIR}/engines/{gdino,rtdetr-its}`) that
must be world-writable for the same foreign-UID reason. Pre-create and
`chmod -R 777` it before bring-up (mirrors `dev-profile.sh` and
`vss-manage-alerts/references/deploy-alerts.md § cv-verification host-prep`).
This is engine scratch, not model input, so it is not covered by the ONNX
permission rule above.

## Verify

```bash
# alerts (whichever MODEL_NAME_2D selected)
test -f "$MODELS/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx" && echo "GDINO staged"
test -f "$MODELS/rtdetr-its/model_epoch_035.fp16.onnx" && echo "alerts RT-DETR staged"
# search / combined
test -f "$MODELS/rtdetr_warehouse_v1.0.2.fp16.onnx" && echo "search RT-DETR staged"
```

## Sources

- `deploy/docker/scripts/dev-profile.sh` (alerts and search staging branches — authoritative NGC refs and target paths)
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml` (the `$VSS_DATA_DIR/models -> /opt/storage` mount)
- `deploy/docker/developer-profiles/dev-profile-alerts/deepstream/init-scripts/ds-start.sh` (engine build paths; ReID copied from the image)
- `skills/vss-build-vision-agent/references/data-directory.md` (directory creation and base permissions)
- `skills/vss-build-vision-agent/references/credentials.md` (NGC entitlement probe for profile-staged models)
- `skills/vss-build-vision-agent/references/services/rt-cv.md` (detector/model-family owner contract)
