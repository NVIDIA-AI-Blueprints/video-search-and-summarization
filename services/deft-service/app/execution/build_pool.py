"""Convert a materialized AOI snapshot into a DEFT ChangeNet training pool.

This is a real-mode pipeline step: a raw AOI snapshot (board images + JSONL
manifests) is NOT yet in the ChangeNet workspace layout the TAO stages expect.
``build_training_pool`` performs that conversion in place —

  * walk ``manifest*.jsonl`` in the workspace,
  * for every board record, load the real board image (locally materialized, or
    fetched via the injected ``fetch_image`` resolver),
  * crop each ``cropInfo`` component at its bbox into a per-component test crop,
  * resolve the golden reference: the board named by ``golden-ref`` when the
    snapshot carries one, otherwise the sample crop itself (dev fallback,
    matching the AOI backend's ``dev_use_sample_as_golden_ref``),
  * label each component from its AOI station ``confirmed_label`` when present
    (``defectType`` supplies only the defect SUBTYPE), falling back to
    ``aoiDefectInfo.defectType`` per ``DEFT_LABEL_POLICY``,
  * split leakage-free into train / val / kpi / mining-pool and write the four
    CSVs + paired crop images in the schema ``validate_training_pool`` enforces.

Spec templates (``baseline_spec.yaml`` / ``baseline_inference_spec.yaml``) and the
pretrained backbone (C-RADIOv2) are deployment assets, not per-run data, so they
are staged from ``DEFT_POOL_TEMPLATE_DIR`` when present.

No image is fabricated: every crop comes from a real board in the snapshot. When
the snapshot cannot satisfy the training contract (no defect components, no PASS
components for the FAR@recall sweep, or fewer objects than one batch), the step
raises ``PoolBuildError`` with an actionable message instead of silently
producing a degenerate pool.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app import config

logger = logging.getLogger("deft.build_pool")

_LIGHT = "SolderLight"
_PASS = "PASS"
_DEFECT = "DEFECT"
_COLUMNS = ["input_path", "golden_path", "label", "object_name", "boardname"]

_SANITIZE = re.compile(r"[^A-Za-z0-9]+")


class PoolBuildError(RuntimeError):
    """The snapshot could not be converted into a valid training pool."""


# Resolver: object-store key -> raw image bytes (e.g. presigned GET). Returns
# None/raises when the object is unavailable; callers fall back to local files.
FetchImage = Callable[[str], bytes | None]


def pool_needs_build(ws_root: Path) -> bool:
    """True when the workspace is a raw AOI snapshot (manifests present) that has
    not yet been converted into the ChangeNet layout (no training CSV)."""
    ws_root = Path(ws_root)
    has_manifest = any(ws_root.glob("manifest*.jsonl"))
    has_pool = (ws_root / "train/base/training_set.csv").is_file()
    return has_manifest and not has_pool


# ---------------------------------------------------------------------------
# manifest -> objects
# ---------------------------------------------------------------------------

@dataclass
class _Obj:
    object_name: str
    boardname: str
    label: str  # "PASS" or a defect class
    input_dir: str  # relative to kpi/images, e.g. "<name>_test"
    golden_dir: str  # relative to kpi/images, e.g. "<name>_golden"

    @property
    def is_defect(self) -> bool:
        return self.label != _PASS


def _token(value: str, fallback: str) -> str:
    tok = _SANITIZE.sub("_", (value or "").strip()).strip("_")
    return tok or fallback


def _board_id(rec: dict, idx: int) -> str:
    key = rec.get("file_path") or (rec.get("metadata") or {}).get("objStorePath") or ""
    stem = _token(Path(str(key)).stem, "board")
    # Always suffix the manifest index so two boards that share a filename (and
    # carry no/duplicate record id) can never collide into one object_name — a
    # collision would overwrite crops and duplicate CSV rows.
    return f"{stem}_{idx:04d}"


def _crops(meta: dict) -> list[dict]:
    crops = meta.get("cropInfo")
    if crops is None:
        crops = (meta.get("imageInfo") or {}).get("cropInfo")
    return crops or []


def _golden_key(meta: dict) -> str | None:
    # Manifest records carry the storage schema (flat, camelCase "goldenRef"); the
    # upload API's wire schema is nested under imageInfo and kebab-case. Accept both.
    image_info = meta.get("imageInfo") or {}
    for candidate in (
        meta.get("goldenRef"),
        meta.get("golden-ref"),
        image_info.get("goldenRef"),
        image_info.get("golden-ref"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def _coords(crop: dict) -> dict | None:
    c = (crop.get("crop") or {}).get("coordinates") or crop.get("coordinates")
    return c if isinstance(c, dict) else None


def _confirmed(crop: dict) -> str:
    """The AOI station's confirmation for this component, upper-cased.

    Accepts the snake_case storage form and the camelCase wire form for the
    same reason ``_golden_key`` does: manifest records come from the catalog
    schema, but a caller handing us an upload-shaped record should not silently
    lose the confirmation.
    """
    raw = crop.get("confirmed_label")
    if raw is None:
        raw = crop.get("confirmedLabel")
    return str(raw or "").strip().upper()


def _label(crop: dict) -> str | None:
    """The training label for one component, or None to exclude it.

    ``confirmed_label`` is AUTHORITATIVE on PASS vs DEFECT; ``defectType`` only
    supplies the defect subtype. The case that matters is a component the
    station flagged (``defectType="mouse bite"``) but confirmed as
    ``PASS`` — a false alarm. Labelling that by ``defectType`` teaches the model
    to reproduce the very error the FAR gate penalizes, so the confirmation wins.

    With no confirmation, ``DEFT_LABEL_POLICY`` decides: fall back to the
    station's own classification, or exclude the component.
    """
    confirmed = _confirmed(crop)
    defect = str((crop.get("aoiDefectInfo") or {}).get("defectType") or "").strip()
    machine_label = _PASS if (not defect or defect.upper() == _PASS) else defect.lower()

    if confirmed == _PASS:
        # Confirmed good. Overrides any defect the station reported.
        return _PASS
    if confirmed == _DEFECT:
        # Confirmed bad. Take the subtype from the station when it named one;
        # a bare DEFECT is still trainable as a generic defect class, which is
        # strictly better than dropping a confirmed defect on the floor.
        return machine_label if machine_label != _PASS else _DEFECT.lower()
    if config.LABEL_POLICY == "confirmed_only":
        return None
    return machine_label


def _iter_records(ws_root: Path):
    for mf in sorted(ws_root.glob("manifest*.jsonl")):
        for line in mf.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                logger.warning("skipping unparseable manifest line in %s", mf.name)


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def _load_image(ws_root: Path, key: str, fetch_image: FetchImage | None):
    from PIL import Image  # local import: Pillow is an execution-only dep

    def _open(src) -> "Image.Image":
        # A corrupt/unreadable image must not crash the whole build — raise
        # PoolBuildError so the caller skips just this board.
        try:
            return Image.open(src).convert("RGB")
        except Exception as exc:
            raise PoolBuildError(f"unreadable board image {key!r}: {exc}") from exc

    # strip any leading slash so the join stays under the workspace and matches
    # where materialize writes the object (<workspace>/<objStorePath>).
    key = key.lstrip("/")
    local = ws_root / key
    if local.is_file():
        return _open(local)
    if fetch_image is not None:
        try:
            data = fetch_image(key)
        except Exception as exc:  # resolver failures are surfaced as missing data
            raise PoolBuildError(f"could not fetch board image {key!r}: {exc}") from exc
        if data:
            return _open(io.BytesIO(data))
    raise PoolBuildError(
        f"board image {key!r} is neither materialized under the workspace nor "
        "fetchable; ensure the snapshot's board images are downloaded"
    )


def _num(v) -> int | None:
    """Coerce a coordinate to int, tolerating floats and numeric strings; None
    on anything non-numeric (null / garbage) so the crop is skipped, not crashed."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _crop(img, coords: dict):
    """Crop ``img`` at a top/left/width/height bbox, clamped to the image.

    Returns None when the bbox is malformed or lies entirely outside the image.
    """
    left, top = _num(coords.get("left")), _num(coords.get("top"))
    width, height = _num(coords.get("width")), _num(coords.get("height"))
    if None in (left, top, width, height):
        return None
    left, top = max(0, left), max(0, top)
    right = min(img.width, left + width)
    bottom = min(img.height, top + height)
    if right <= left or bottom <= top:
        return None
    return img.crop((left, top, right, bottom))


def _write_crop(images_dir: Path, rel_dir: str, object_name: str, img) -> None:
    out = images_dir / rel_dir
    out.mkdir(parents=True, exist_ok=True)
    img.save(out / f"{object_name}_{_LIGHT}.jpg", "JPEG")


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

def _partition(objects: list[_Obj], batch: int, *, carve_kpi: bool = True) -> dict[str, list[_Obj]]:
    """Leakage-free split (object_names are unique) that satisfies the
    validate_training_pool contract: train >= batch with >=1 defect, val >=
    batch, kpi has both a PASS and a defect row, pool non-empty.

    Reserve the mandatory members first (kpi one-of-each, train a defect + a
    full batch, val a full batch, pool one), then distribute the remainder
    round-robin. This never emits an invalid split, and only raises when the
    snapshot genuinely can't cover the minimum — no needless rejection of data a
    smarter carve could split.

    When ``carve_kpi`` is False, the KPI/test split is NOT taken from ``objects``
    (an external KPI set supplies it) — only train/val/mining are built here.
    """
    from collections import deque

    defects = deque(sorted((o for o in objects if o.is_defect), key=lambda o: o.object_name))
    passes = deque(sorted((o for o in objects if not o.is_defect), key=lambda o: o.object_name))
    if not defects:
        raise PoolBuildError(
            "snapshot has no defect components — TAO's pass/fail sampler needs "
            ">=1 defect row; the AOI data must include at least one defect"
        )
    if not passes:
        raise PoolBuildError(
            "snapshot has no PASS (good) components — the FAR@recall sweep needs "
            "good components as well as defects; the AOI data must include both"
        )

    total_d, total_p = len(defects), len(passes)
    picked_d = picked_p = 0

    def take(want: bool | None):
        """Pop a defect (True) / pass (False) / whichever keeps the running
        defect:pass ratio closest to the global ratio (None) — so every split is
        stratified. Returns None when the requested class is exhausted."""
        nonlocal picked_d, picked_p
        if want is True:
            if not defects:
                return None
            picked_d += 1
            return defects.popleft()
        if want is False:
            if not passes:
                return None
            picked_p += 1
            return passes.popleft()
        # pick the class that is proportionally behind: picked_d/total_d <= picked_p/total_p
        if defects and (not passes or picked_d * total_p <= picked_p * total_d):
            picked_d += 1
            return defects.popleft()
        if passes:
            picked_p += 1
            return passes.popleft()
        return None

    def short(what: str):
        return PoolBuildError(
            f"insufficient converted objects for batch_size={batch}: {what}. "
            f"Have {len(objects)} components "
            f"({sum(o.is_defect for o in objects)} defect / "
            f"{sum(not o.is_defect for o in objects)} pass); provide more AOI "
            "components or lower training.batch_size"
        )

    train: list[_Obj] = []
    val: list[_Obj] = []
    kpi: list[_Obj] = []
    pool: list[_Obj] = []

    # KPI carries both classes (FAR@recall) — only when carving it from this pool
    # (an external KPI set, when present, is validated + used instead).
    if carve_kpi:
        for want, msg in ((True, "KPI needs a defect row"), (False, "KPI needs a PASS row")):
            o = take(want)
            if o is None:
                raise short(msg)
            kpi.append(o)
    d0 = take(True)
    if d0 is None:
        raise short("train needs a defect row")
    train.append(d0)
    o = take(None)
    if o is None:
        raise short("mining pool needs a row")
    pool.append(o)

    # Fill train and val to a full batch each.
    for bucket, name in ((train, "train"), (val, "val")):
        while len(bucket) < batch:
            o = take(None)
            if o is None:
                raise short(f"{name} needs {batch} rows")
            bucket.append(o)

    # Distribute whatever is left, round-robin, so all splits grow evenly.
    targets = [kpi, pool, train, val] if carve_kpi else [pool, train, val]
    i = 0
    while (o := take(None)) is not None:
        targets[i % len(targets)].append(o)
        i += 1

    splits = {"train": train, "val": val, "test": kpi, "pool": pool}
    _validate_splits(splits, batch, require_kpi=carve_kpi)
    return splits


def _validate_splits(splits: dict[str, list[_Obj]], batch: int, *, require_kpi: bool = True) -> None:
    """Fail fast if the assembled splits violate the validate_training_pool
    contract — never emit a pool TAO would choke on."""
    train, val, test, pool = (splits["train"], splits["val"], splits["test"], splits["pool"])
    problems: list[str] = []
    if len(train) < batch:
        problems.append(f"train has {len(train)} rows < batch_size {batch}")
    if not any(o.is_defect for o in train):
        problems.append("train has no defect row")
    if len(val) < batch:
        problems.append(f"val has {len(val)} rows < batch_size {batch}")
    if not pool:
        problems.append("mining pool is empty")
    if require_kpi and not (any(o.is_defect for o in test) and any(not o.is_defect for o in test)):
        problems.append("kpi split needs both a PASS and a defect row for FAR@recall")
    if problems:
        raise PoolBuildError(
            "cannot assemble a valid training pool from this snapshot: "
            + "; ".join(problems)
            + " — provide more balanced AOI components or lower training.batch_size"
        )


# ---------------------------------------------------------------------------
# csv + templates
# ---------------------------------------------------------------------------

def _write_csv(path: Path, objects: list[_Obj]) -> None:
    """Base train/val/kpi CSVs: input_path/golden_path are dirs under kpi/images."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for o in objects:
            w.writerow({
                "input_path": o.input_dir,
                "golden_path": o.golden_dir,
                "label": o.label,
                "object_name": o.object_name,
                "boardname": o.boardname,
            })


def _write_pool(pool_dir: Path, images_dir: Path, objects: list[_Obj]) -> None:
    """Mining pool: test crop copied to augmentation/mining_pool/images/<obj>.jpg
    (input_path is that file); golden stays in the shared kpi/images tree."""
    pool_images = pool_dir / "images"
    pool_images.mkdir(parents=True, exist_ok=True)
    rows = []
    for o in objects:
        fname = f"{o.object_name}_{_LIGHT}.jpg"
        shutil.copyfile(images_dir / o.input_dir / fname, pool_images / fname)
        rows.append({
            "input_path": f"images/{fname}",
            "golden_path": o.golden_dir,
            "label": o.label,
            "object_name": o.object_name,
            "boardname": o.boardname,
        })
    pool_csv = pool_dir / "mining_pool.csv"
    with open(pool_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


_SPEC_NAMES = ("baseline_spec.yaml", "baseline_inference_spec.yaml")
_BACKBONE_SUFFIXES = (".safetensors", ".pth", ".ckpt")
# DEFT's bundled default ChangeNet specs — so `profile: aoi_default` runs with zero
# external setup. Overridable per-run via training.initial_spec_path /
# kpi.inference_spec_path (materialized) or per-host via DEFT_POOL_TEMPLATE_DIR (dev).
_BUNDLED_SPECS_DIR = Path(__file__).resolve().parent.parent / "data" / "specs"
# Pretrained C-RADIOv2-B backbone (public HF; 393 MB) — fetched once and cached so a
# run needs no hand-placed backbone. Overridable via DEFT_BACKBONE_URL (empty
# disables the fetch) and DEFT_BACKBONE_CACHE.
_DEFAULT_BACKBONE_URL = "https://huggingface.co/nvidia/C-RADIOv2-B/resolve/main/model.safetensors"


def _has_backbone(bb_dir: Path) -> bool:
    return any(f.suffix in _BACKBONE_SUFFIXES for f in bb_dir.glob("*") if f.is_file())


_FETCH_HINT = (
    "provide it via DEFT_POOL_TEMPLATE_DIR/backbone, point DEFT_BACKBONE_URL at an "
    "internal mirror, pre-seed DEFT_BACKBONE_CACHE, or set HF_TOKEN"
)
# HF throttles anonymous downloads per source IP (429); retry transient failures with
# backoff before falling back. The cache makes this a one-time cost per host.
_TRANSIENT_HTTP = (429, 500, 502, 503, 504)


def _fetch_backbone() -> Path | None:
    """Ensure the pretrained backbone is cached locally (download once from HF);
    return the cached path, or None if disabled/failed (caller falls back).

    Robustness: a populated cache short-circuits the network; an HF_TOKEN raises the
    anonymous rate limit; transient failures (429/5xx) retry with backoff. For a
    throttled/air-gapped host, point DEFT_BACKBONE_URL at an internal mirror or
    pre-seed DEFT_BACKBONE_CACHE."""
    cache = Path(
        os.environ.get("DEFT_BACKBONE_CACHE")
        or (Path.home() / ".cache" / "deft" / "c_radio_v2_b.safetensors")
    )
    if cache.is_file() and cache.stat().st_size > 0:
        return cache  # already downloaded (or pre-staged)
    url = os.environ.get("DEFT_BACKBONE_URL", _DEFAULT_BACKBONE_URL).strip()
    if not url:
        return None  # download disabled and no cache
    import time
    import urllib.error
    import urllib.request

    token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        retries = max(0, int(os.environ.get("DEFT_BACKBONE_RETRIES", "3")))
    except ValueError:
        retries = 3
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Per-process temp so two concurrent first-time fetches can't interleave writes to
    # one .part file and promote a corrupt backbone into the shared cache.
    tmp = cache.parent / f".{cache.name}.{os.getpid()}.part"
    for attempt in range(retries + 1):
        try:
            logger.info(
                "fetching pretrained backbone from %s (one-time, ~393MB, attempt %d/%d) -> %s",
                url, attempt + 1, retries + 1, cache,
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
                expected = resp.headers.get("Content-Length")
                shutil.copyfileobj(resp, fh, length=1 << 20)
            # Integrity: reject a truncated stream or a tiny error body served with 200
            # before it can poison the cache for every later run.
            got = tmp.stat().st_size
            if expected is not None and expected.isdigit() and got != int(expected):
                raise OSError(f"truncated backbone download: got {got} of {expected} bytes")
            if got < (1 << 20):
                raise OSError(f"backbone response too small ({got} bytes) — not the model")
            tmp.replace(cache)
            logger.info("backbone cached at %s (%d bytes)", cache, cache.stat().st_size)
            return cache
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code in _TRANSIENT_HTTP and attempt < retries:
                wait = float(exc.headers.get("Retry-After") or 0) or 2.0 * (2**attempt)
                logger.warning(
                    "backbone fetch HTTP %s (%s); retrying in %.0fs",
                    exc.code, "rate-limited" if exc.code == 429 else "server error", min(wait, 30.0),
                )
                time.sleep(min(wait, 30.0))
                continue
            logger.warning("backbone fetch failed (HTTP %s); %s", exc.code, _FETCH_HINT)
            return None
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if attempt < retries:
                wait = 2.0 * (2**attempt)
                logger.warning("backbone fetch error (%s); retrying in %.0fs", exc, wait)
                time.sleep(min(wait, 30.0))
                continue
            logger.warning("backbone fetch failed (%s); %s", exc, _FETCH_HINT)
            return None
    return None


def _stage_templates(ws_root: Path) -> None:
    """Ensure the workspace has the ChangeNet specs + backbone the TAO stages need.

    Precedence — specs: an already-present spec (a materialized URI) wins, else
    DEFT_POOL_TEMPLATE_DIR/specs (dev), else DEFT's bundled defaults. Backbone: an
    already-present backbone wins, else DEFT_POOL_TEMPLATE_DIR/backbone, else the
    HF-fetched (cached) default. So `profile: aoi_default` runs with zero setup.
    """
    tpl = os.environ.get("DEFT_POOL_TEMPLATE_DIR")

    # --- specs ---
    # Precedence per spec: materialized (already in the workspace) > template dir >
    # bundled default. The fallback is per-file: a template dir that supplies only one
    # spec still gets the other from the bundled default (no all-or-nothing gap).
    specs = ws_root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    for name in _SPEC_NAMES:
        dst = specs / name
        if dst.exists():
            continue  # a materialized initial_spec_path / inference_spec_path wins
        tpl_src = (Path(tpl) / "specs" / name) if tpl else None
        if tpl_src and tpl_src.is_file():
            shutil.copyfile(tpl_src, dst)
            logger.info("staged spec %s (template dir)", name)
        elif (_BUNDLED_SPECS_DIR / name).is_file():
            shutil.copyfile(_BUNDLED_SPECS_DIR / name, dst)
            logger.info("staged spec %s (bundled default)", name)

    # --- backbone ---
    bb_dst = ws_root / "augmentation" / "backbone"
    if _has_backbone(bb_dst):
        return  # already present (e.g. materialized)
    src_bb: Path | None = None
    tpl_backbone = (Path(tpl) / "backbone") if tpl else None
    if tpl_backbone and tpl_backbone.is_dir():
        src_bb = next(
            (f for f in tpl_backbone.iterdir() if f.suffix in _BACKBONE_SUFFIXES), None
        )
    if src_bb is None:
        src_bb = _fetch_backbone()  # HF default, cached
    if src_bb is not None:
        bb_dst.mkdir(parents=True, exist_ok=True)
        # The results/register/publish contract (real_runner, aoi_pipeline) reads the
        # backbone at exactly augmentation/backbone/model.safetensors, so a safetensors
        # source must be staged under that canonical name (not its cache/template name)
        # or the backbone URI silently falls back to the checkpoint. Other formats keep
        # their name (the training glob accepts *.pth/*.ckpt too).
        dst_name = "model.safetensors" if src_bb.suffix == ".safetensors" else src_bb.name
        shutil.copyfile(src_bb, bb_dst / dst_name)
        logger.info("staged backbone %s -> %s", src_bb.name, dst_name)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _convert_manifests(
    manifest_root: Path,
    images_dir: Path,
    *,
    fetch_image: FetchImage | None = None,
    id_prefix: str = "",
) -> tuple[list[_Obj], dict[str, int]]:
    """Crop every board referenced by ``manifest_root``'s manifests into paired
    test/golden component crops under ``images_dir``; return the objects + stats.

    ``id_prefix`` namespaces object names so two independently-converted sets (the
    training snapshot and an external KPI set) can share ``images_dir`` without
    colliding. Unreadable boards and out-of-bounds/malformed crops are skipped.
    """
    objects: list[_Obj] = []
    stats = {"boards": 0, "skipped_boards": 0, "skipped_crops": 0, "sample_golden": 0,
             "unconfirmed_crops": 0}
    for idx, rec in enumerate(_iter_records(manifest_root)):
        meta = rec.get("metadata") or {}
        board_key = rec.get("file_path") or meta.get("objStorePath")
        crops = _crops(meta)
        if not board_key or not crops:
            continue
        stats["boards"] += 1
        board_id = f"{id_prefix}{_board_id(rec, idx)}"
        try:
            board_img = _load_image(manifest_root, str(board_key), fetch_image)
        except PoolBuildError as exc:
            logger.warning("%s; skipping board", exc)
            stats["skipped_boards"] += 1
            continue

        golden_key = _golden_key(meta)
        golden_img = None
        if golden_key:
            try:
                golden_img = _load_image(manifest_root, golden_key, fetch_image)
            except PoolBuildError as exc:
                logger.warning("golden-ref %s unavailable (%s); sample-as-golden", golden_key, exc)

        for crop in crops:
            coords = _coords(crop)
            comp_id = str(crop.get("id") or "").strip()
            if not coords or not comp_id:
                stats["skipped_crops"] += 1
                continue
            label = _label(crop)
            if label is None:
                # confirmed_only: no trustworthy label for this component, so it
                # is excluded rather than trained on an unverified classification.
                stats["unconfirmed_crops"] += 1
                continue
            test_crop = _crop(board_img, coords)
            if test_crop is None:
                logger.warning("crop %s/%s bbox outside image; skipping", board_id, comp_id)
                stats["skipped_crops"] += 1
                continue
            if golden_img is not None:
                golden_crop = _crop(golden_img, coords) or test_crop
            else:
                golden_crop = test_crop  # dev sample-as-golden fallback
                stats["sample_golden"] += 1

            object_name = _token(f"{board_id}__{comp_id}", f"obj{len(objects):05d}")
            input_dir = f"{object_name}_test"
            golden_dir = f"{object_name}_golden"
            _write_crop(images_dir, input_dir, object_name, test_crop)
            _write_crop(images_dir, golden_dir, object_name, golden_crop)
            objects.append(_Obj(object_name, board_id, label, input_dir, golden_dir))
    return objects, stats


def _validate_kpi_objs(kpi_objs: list[_Obj]) -> None:
    if not (any(o.is_defect for o in kpi_objs) and any(not o.is_defect for o in kpi_objs)):
        raise PoolBuildError(
            "external KPI set needs both a PASS and a defect component for the FAR@recall sweep"
        )


def build_training_pool(
    ws_root: Path,
    *,
    kpi_source: Path | str | None = None,
    fetch_image: FetchImage | None = None,
    batch_size: int = 8,
) -> None:
    """Convert the raw AOI snapshot at ``ws_root`` into a ChangeNet pool in place.

    When ``kpi_source`` is given (a materialized KPI-set manifest dir), its boards
    become the ``kpi/testing_set.csv`` eval set and the training snapshot is split
    into train/val/mining only; otherwise the kpi split is carved from the snapshot.
    """
    ws_root = Path(ws_root)
    images_dir = ws_root / "kpi" / "images"

    train_objs, st = _convert_manifests(ws_root, images_dir, fetch_image=fetch_image)
    if not train_objs:
        raise PoolBuildError(
            f"no usable components in the snapshot ({st['boards']} boards, "
            f"{st['skipped_boards']} unreadable, {st['skipped_crops']} bad crops)"
        )
    if st["sample_golden"]:
        logger.warning(
            "%d/%d crops used the sample as its own golden reference (no golden-ref "
            "in the snapshot); defect rows carry no test-vs-golden change signal — "
            "provide real golden references for a trainable defect signal",
            st["sample_golden"], len(train_objs),
        )

    if kpi_source is not None:
        kpi_objs, kst = _convert_manifests(
            Path(kpi_source), images_dir, fetch_image=fetch_image, id_prefix="kpiset_"
        )
        if not kpi_objs:
            raise PoolBuildError(
                f"external KPI set has no usable components ({kst['boards']} boards, "
                f"{kst['skipped_boards']} unreadable, {kst['skipped_crops']} bad crops)"
            )
        _validate_kpi_objs(kpi_objs)
        splits = _partition(train_objs, batch_size, carve_kpi=False)
        splits["test"] = kpi_objs
        logger.info(
            "using external KPI set: %d components (%d defect / %d pass)",
            len(kpi_objs), sum(o.is_defect for o in kpi_objs),
            sum(not o.is_defect for o in kpi_objs),
        )
    else:
        splits = _partition(train_objs, batch_size)

    _write_csv(ws_root / "train/base/training_set.csv", splits["train"])
    _write_csv(ws_root / "train/base/validation_set.csv", splits["val"])
    _write_csv(ws_root / "kpi/testing_set.csv", splits["test"])
    _write_pool(ws_root / "augmentation" / "mining_pool", images_dir, splits["pool"])
    _stage_templates(ws_root)

    logger.info(
        "training pool built at %s from %d boards: train=%d val=%d kpi=%d pool=%d "
        "(%d defect / %d pass)%s",
        ws_root, st["boards"], len(splits["train"]), len(splits["val"]), len(splits["test"]),
        len(splits["pool"]), sum(o.is_defect for o in train_objs),
        sum(not o.is_defect for o in train_objs),
        " [external KPI set]" if kpi_source is not None else "",
    )
