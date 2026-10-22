"""Real AOI DEFT pipeline stages (workflow: aoi_active_learning).

Implements the DEFT skill's loop on the docker backend against a TAO
VisualChangeNet workspace:

    pre_loop   validate_training_pool -> baseline_eval
    loop x N   rca -> routing -> anomalygen -> data_mining -> train -> evaluate
    post_loop  register_candidate_model -> publish_results

Encodes the workspace/container conventions that actually make TAO runs
succeed (learned the hard way):
  * `results_dir` and every csv/checkpoint path passed to the container must
    be CONTAINER paths under the workspace mount — a host path silently
    writes checkpoints into the container's own filesystem and `--rm`
    discards them.
  * `train.checkpoint_interval=1` — the spec default (10) with 10 epochs
    saves nothing (epochs run 0..9). It also makes every epoch a selection
    candidate, which is the point: TAO's `*_latest.pth` is a symlink to the
    FINAL epoch, and the final epoch is routinely well past the best one, so
    the iteration checkpoint is chosen by lowest `val_loss` instead.
  * The predict stage reads `dataset.classify.infer_dataset.csv_path`.
  * Base train/val CSV rows are relative to kpi/images; per-iteration specs
    set images_dir to the workspace root, so base rows get a `kpi/images/`
    prefix and mined pool rows use input_path=augmentation/mining_pool/images
    with golden_path=kpi/images/<pool golden_path> (pool goldens live in the
    shared kpi tree).
  * Labels: `PASS` stays uppercase (dataloader sentinel), everything else
    lowercases.
  * Leakage gates: mined rows and every assembled training CSV are diffed
    against the validation set on (input_path, golden_path, label,
    object_name, boardname); any hit is a hard stop.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.config import CONTAINER_WORKSPACE, SIGLIP_MODEL, TAO_PYT_IMAGE
from app.execution import kpi
from app.execution.docker_backend import ContainerFailed, ContainerSpec, run_container, tail
from app.execution.workspace import (
    PASS_LABEL,
    Workspace,
    WorkspaceError,
    check_columns,
    image_file,
    leakage_pairs,
    normalize_label,
    read_rows,
    validate_existence,
    write_rows,
)
from app.schemas.config_models import ExperimentConfig
from app.store import RunRecord, utcnow

#: input_path written on every mined row, and the key `already` matches on.
POOL_INPUT_PATH = "augmentation/mining_pool/images"


class StageError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 1, logs: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.logs = logs


class RunCancelled(RuntimeError):
    pass


@dataclass
class StageResult:
    summary: str
    metrics: dict[str, float] | None = None
    stdout_tail: str = ""


@dataclass
class CheckpointChoice:
    """The checkpoint an iteration's training produced, and why it was picked."""

    path: Path
    epoch: int | None = None          # None when picked without val_loss evidence
    val_loss: float | None = None

    def describe(self) -> str:
        if self.epoch is None:
            return f"{self.path.name} (no per-epoch val_loss; fell back to latest)"
        return f"{self.path.name} (epoch {self.epoch}, val_loss={self.val_loss:.4f})"


@dataclass
class BestModel:
    far_pct: float = math.inf
    iteration: int = 1
    is_baseline: bool = True
    ckpt_host: Path | None = None
    ckpt_container: str | None = None
    threshold: float = 0.5
    metrics: dict[str, float] = field(default_factory=dict)


#: Fields worth surfacing from a TAO status record. Everything else it writes
#: (verbosity, free-text message, timestamps) is noise to a progress caller.
_TAO_STATUS_KEYS = ("epoch", "max_epoch", "step", "max_step", "eta", "time_per_epoch")


def _tao_status_path(args: list[str], ws: Workspace) -> Path | None:
    """Locate TAO's status.json for a stage from its ``results_dir=`` argument.

    TAO writes ``<results_dir>/<task>/status.json``. The task subdirectory is
    the stage's own name (train/, inference/), so glob rather than hard-code it.
    Returns None when the args carry no results_dir -- callers treat that as
    "no structured detail available", not an error.

    ``results_dir`` is an *in-container* path (``/data/workspace/...``), which
    does not exist in this service's own filesystem, so it must be mapped back
    through the workspace root rather than opened as-is.
    """
    prefix = "results_dir="
    results = next((a[len(prefix):] for a in args if a.startswith(prefix)), None)
    if not results:
        return None
    host = ws.from_container(results) if Path(results).is_absolute() else ws.host(results)
    if host is None:
        return None
    try:
        return next(iter(sorted(host.glob("*/status.json"))), None)
    except OSError:
        return None


def _tao_status_records(path: Path) -> list[dict]:
    """Parse TAO's JSONL status file into records, in file order.

    Written concurrently by the container, so the tail can be a partial line
    and records are appended without separators in practice -- split on the
    brace boundary rather than assuming one JSON object per line, and drop
    whatever does not parse. Returns [] rather than raising: both callers
    treat an unreadable status file as "no information", never as an error.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    records = []
    for chunk in text.replace("}{", "}\n{").splitlines():
        chunk = chunk.strip()
        if not chunk.startswith("{"):
            continue
        try:
            records.append(json.loads(chunk))
        except ValueError:
            continue
    return records


def _read_tao_status(path: Path) -> dict | None:
    """Return the last usable record from TAO's JSONL status file.

    Walk backwards to the newest record carrying progress fields. Returns None
    rather than raising -- this is observability on a hot path, and a malformed
    record must never disturb a running stage.
    """
    for rec in reversed(_tao_status_records(path)):
        detail = {k: rec[k] for k in _TAO_STATUS_KEYS if k in rec}
        if detail:
            return detail
    return None


def _tao_val_losses(train_dir: Path) -> dict[int, float]:
    """Map epoch -> that epoch's validation loss, from TAO's status.json.

    TAO's status logger writes one ``"Training loop in progress"`` record per
    epoch from ``on_train_epoch_end``, tagged with ``epoch`` and carrying the
    ``kpi`` dict the module last published. Lightning runs
    ``on_validation_epoch_end`` *before* the ``on_train_epoch_end`` callbacks,
    so that dict is the same epoch's validation result -- the record tagged
    ``epoch=N`` holds epoch N's own ``val_loss``, with no off-by-one. The
    ``ModelCheckpoint`` that writes ``model_epoch_{N:03d}_*.pth`` fires from
    that same hook, so file and loss describe identical weights.

    status.json lives under the stage's own task subdirectory
    (``<results_dir>/train/status.json``), so search rather than hard-code it.
    """
    losses: dict[int, float] = {}
    for status in sorted(train_dir.rglob("status.json")):
        for rec in _tao_status_records(status):
            epoch, kpi = rec.get("epoch"), rec.get("kpi")
            if not isinstance(epoch, int) or not isinstance(kpi, dict):
                continue
            val_loss = kpi.get("val_loss")
            if isinstance(val_loss, int | float):
                losses[epoch] = float(val_loss)
    return losses


@dataclass
class AoiContext:
    record: RunRecord
    cfg: ExperimentConfig
    ws: Workspace
    run_dir: Path                       # host: <workspace>/results/service_run_<id8>
    cancel_event: threading.Event
    kpi_csv: Path = field(init=False)
    train_spec: Path = field(init=False)
    infer_spec: Path = field(init=False)
    base_ckpt: Path = field(init=False)

    current_iteration: int = 0
    best: BestModel = field(default_factory=BestModel)
    current_ckpt_container: str | None = None      # ckpt trained this iteration
    current_ckpt_host: Path | None = None          # ...its resolved host path
    combined_rows: list[dict] = field(default_factory=list)  # grows monotonically
    csv_fieldnames: list[str] = field(default_factory=list)
    val_rows_norm: list[dict] = field(default_factory=list)  # validation, kpi/images-prefixed
    last_inference_csv: Path | None = None
    gaps_rows: list[dict] = field(default_factory=list)
    mining_targets: list[dict] = field(default_factory=list)
    mined_rows_this_iter: list[dict] = field(default_factory=list)
    kpi_target_far_pct: float | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        c = self.cfg
        self.kpi_csv = self.ws.host(c.kpi.path or "kpi/testing_set.csv")
        self.train_spec = self.ws.host(c.training.initial_spec_path or "specs/baseline_spec.yaml")
        self.infer_spec = self.ws.host(c.kpi.inference_spec_path or "specs/baseline_inference_spec.yaml")
        self.base_ckpt = self.ws.host(c.model.checkpoint)
        self.kpi_target_far_pct = parse_kpi_target(c.pipeline.kpi_target)

    # -- helpers -------------------------------------------------------------
    def iter_dir(self, name: str) -> Path:
        d = self.run_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def container_name(self, stage: str) -> str:
        return f"deft-{str(self.record.id)[:8]}-{stage}-i{self.current_iteration}"

    def on_heartbeat(self, status_path: Path | None = None) -> None:
        """Mark the run as still working, and say how far in when we can.

        Called by the container runner whenever the stage's log grows. Every
        other field on ``Progress`` is step-granular, so during a multi-hour
        ``train`` they are all constant and a caller cannot distinguish a long
        step from a hang. ``updated_at`` moves throughout.

        ``status_path`` is TAO's own status file when the stage is a TAO
        container. It is JSONL: one record appended per epoch, carrying
        ``epoch`` / ``max_epoch`` / ``step`` / ``eta``. Reading the LAST record
        is strictly better than inferring progress from log size -- it is
        structured, it is TAO's own account of itself, and it gives a caller a
        real position rather than just "something happened".

        Best effort: a stage that publishes no status file, or a half-written
        record caught mid-append, still yields a bumped ``updated_at``.
        """
        self.record.progress.updated_at = utcnow()
        if status_path is None:
            return
        detail = _read_tao_status(status_path)
        if detail:
            self.record.progress.detail = detail

    def tao(self, args: list[str], *, stage: str, log_path: Path) -> None:
        spec = ContainerSpec(
            image=TAO_PYT_IMAGE,
            args=args,
            mounts={str(self.ws.root): CONTAINER_WORKSPACE},
            name=self.container_name(stage),
            log_path=log_path,
            env={"HF_HOME": f"{CONTAINER_WORKSPACE}/.hf"},
        )
        # TAO writes status.json under <results_dir>/<task>/status.json. Derive it
        # from the args we just built rather than guessing the layout.
        #
        # Resolve it per heartbeat, not once here: TAO creates the <task>
        # subdirectory itself, so at this point it does not exist and the glob
        # would pin None for the entire run -- `detail` would then never
        # populate no matter what TAO wrote. Latch the first hit so the common
        # case is one readdir, not one per heartbeat.
        status_path: Path | None = None

        def heartbeat() -> None:
            nonlocal status_path
            if status_path is None:
                status_path = _tao_status_path(args, self.ws)
            self.on_heartbeat(status_path)

        rc = run_container(spec, self.cancel_event, on_heartbeat=heartbeat)
        if rc == -2:
            raise RunCancelled()


def parse_kpi_target(target: str | None) -> float | None:
    """Parse targets like 'FAR<0.5%@recall=100%' -> 0.5 (percent)."""
    if not target:
        return None
    m = re.search(r"far\s*[<≤]=?\s*(\d+(?:\.\d+)?)\s*%", target, flags=re.IGNORECASE)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# pre_loop
# ---------------------------------------------------------------------------

def validate_training_pool(ctx: AoiContext) -> StageResult:
    ws = ctx.ws
    problems: list[str] = []
    for path in (ws.train_csv, ws.val_csv, ctx.kpi_csv, ctx.train_spec, ctx.infer_spec, ctx.base_ckpt):
        if not path.exists():
            problems.append(f"missing: {path}")
    if problems:
        raise StageError("workspace validation failed: " + "; ".join(problems))

    train_fields, train_rows = read_rows(ws.train_csv)
    check_columns(ws.train_csv, train_fields)
    val_fields, val_rows = read_rows(ws.val_csv)
    check_columns(ws.val_csv, val_fields)
    kpi_fields, kpi_rows = read_rows(ctx.kpi_csv)
    check_columns(ctx.kpi_csv, kpi_fields)

    if not ws.pool_csv.exists():
        raise StageError(f"mining pool CSV missing: {ws.pool_csv} (hard stop per the DEFT data contract)")
    _, pool_rows = read_rows(ws.pool_csv)
    if not pool_rows:
        raise StageError(f"mining pool {ws.pool_csv} has zero rows — no real-image contribution possible")

    leaks = leakage_pairs(train_rows, val_rows)
    if leaks:
        raise StageError(f"train/validation leakage: {leaks} validation rows appear in the training set")

    missing = validate_existence(ws, train_rows + val_rows, "kpi/images")
    if missing:
        raise StageError(f"{len(missing)} training/validation paths missing on disk, e.g. {missing[:3]}")

    # TAO-constraint preflight (GPU-proven failure modes surfaced up front,
    # instead of as opaque container crashes AFTER baseline_eval + mining):
    # (a) the pass/fail sampler divides by the defect count — an all-PASS
    #     training set crashes with ZeroDivisionError;
    # (b) every split's dataloader requires len(split) >= batch_size.
    n_defect_train = sum(1 for r in train_rows if normalize_label(r.get("label", "")) != PASS_LABEL)
    if n_defect_train == 0:
        raise StageError(
            "training set has no defect-labeled rows — TAO's pass/fail sampler "
            "requires at least one non-PASS row (label != PASS)"
        )
    batch = ctx.cfg.training.batch_size or 8
    for name, rows in (("training", train_rows), ("validation", val_rows)):
        if len(rows) < batch:
            raise StageError(
                f"{name} set has {len(rows)} rows but training.batch_size is {batch} — "
                "TAO requires every split to have at least batch_size rows "
                "(lower training.batch_size or add data)"
            )

    ctx.csv_fieldnames = train_fields
    # seed the monotonic combined set: base rows re-rooted to the workspace root
    ctx.combined_rows = [_reroot_base_row(r) for r in train_rows]
    for row in ctx.combined_rows:
        row["_provenance"] = "base_train"
    ctx.val_rows_norm = [_reroot_base_row(r) for r in val_rows]
    n_defect_kpi = sum(1 for r in kpi_rows if normalize_label(r["label"]) != PASS_LABEL)
    return StageResult(
        summary=(
            f"workspace ok: train={len(train_rows)} val={len(val_rows)} "
            f"kpi={len(kpi_rows)} ({n_defect_kpi} defects) pool={len(pool_rows)}; leakage=0"
        )
    )


def _reroot_base_row(row: dict) -> dict:
    out = dict(row)
    for col in ("input_path", "golden_path"):
        rel = (out.get(col) or "").strip()
        if rel and not rel.startswith("kpi/images"):
            out[col] = f"kpi/images/{rel}"
    out["label"] = normalize_label(out.get("label", ""))
    return out


# ---------------------------------------------------------------------------
# evaluation (pre_loop baseline_eval + loop evaluate)
# ---------------------------------------------------------------------------

def _run_inference(ctx: AoiContext, ckpt_container: str, out_name: str) -> Path:
    out_dir = ctx.iter_dir(out_name) / "inference"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "stage.log"
    args = [
        "visual_changenet", "inference",
        "-e", ctx.ws.container(ctx.infer_spec),
        f"results_dir={ctx.ws.container(out_dir)}",
        f"inference.checkpoint={ckpt_container}",
        f"dataset.classify.infer_dataset.csv_path={ctx.ws.container(ctx.kpi_csv)}",
        # KPI CSV paths are relative to its sibling images/ dir (the same base
        # validate_existence checks); generic specs carry images_dir '' which
        # TAO resolves against its own cwd — wire it explicitly like every
        # other path arg.
        f"dataset.classify.infer_dataset.images_dir={ctx.ws.container(ctx.kpi_csv.parent / 'images')}",
    ]
    backbone = _staged_backbone(ctx.ws)
    if backbone is not None:
        # Same skill Pre-Flight rule as the train stage: spec templates carry a
        # /data/pretrained_models/... backbone path that doesn't exist inside
        # the workspace mount — TAO's inference builds the model (and loads the
        # backbone) before applying the checkpoint, so it needs the override
        # just like train does.
        args.append(f"model.backbone.pretrained_backbone_path={ctx.ws.container(backbone)}")
    ctx.tao(args, stage=f"infer-{out_name}", log_path=log)
    csv_path = _find_inference_csv(out_dir)
    if csv_path is None:
        raise StageError(
            f"TAO inference produced no CSV with a siamese_score column under {out_dir}",
            logs=tail(log),
        )
    return csv_path


def _find_inference_csv(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.rglob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            header = path.open().readline()
        except OSError:
            continue
        if "siamese_score" in header:
            return path
    return None


def _evaluate(ctx: AoiContext, ckpt_container: str, ckpt_host: Path | None, out_name: str,
              iteration: int, is_baseline: bool) -> StageResult:
    inference_csv = _run_inference(ctx, ckpt_container, out_name)
    ctx.last_inference_csv = inference_csv
    analysis = kpi.analyze(inference_csv)
    metrics = analysis.metrics()
    (ctx.iter_dir(out_name) / "kpi_analysis.json").write_text(json.dumps(metrics, indent=2))

    if analysis.far_pct_100 < ctx.best.far_pct:
        ctx.best = BestModel(
            far_pct=analysis.far_pct_100,
            iteration=max(iteration, 1),
            is_baseline=is_baseline,
            ckpt_host=ckpt_host,
            ckpt_container=ckpt_container,
            threshold=analysis.threshold_100,
            metrics=metrics,
        )
    if ctx.kpi_target_far_pct is not None and analysis.far_pct_100 <= ctx.kpi_target_far_pct:
        ctx.stop_reason = "kpi_met"
    return StageResult(
        summary=(
            f"FAR@recall=100%={analysis.far_pct_100:.2f}% (threshold={analysis.threshold_100:.4f}, "
            f"AUC={analysis.auc:.3f}, defects={analysis.n_defect})"
        ),
        metrics=metrics,
    )


def baseline_eval(ctx: AoiContext) -> StageResult:
    return _evaluate(
        ctx,
        ckpt_container=ctx.ws.container(ctx.base_ckpt),
        ckpt_host=ctx.base_ckpt,
        out_name="baseline",
        iteration=1,
        is_baseline=True,
    )


def evaluate(ctx: AoiContext) -> StageResult:
    if ctx.current_ckpt_container is None:
        raise StageError("evaluate reached without a trained checkpoint for this iteration")
    it = ctx.current_iteration
    # Reuse the host path train() actually discovered — never re-guess a layout string,
    # or a best model whose real checkpoint doesn't match the guess publishes as None and
    # the runner silently falls back to the baseline weights.
    return _evaluate(
        ctx,
        ckpt_container=ctx.current_ckpt_container,
        ckpt_host=ctx.current_ckpt_host,
        out_name=f"iter{it}",
        iteration=it,
        is_baseline=False,
    )


# ---------------------------------------------------------------------------
# rca + routing
# ---------------------------------------------------------------------------

def rca(ctx: AoiContext) -> StageResult:
    if ctx.last_inference_csv is None:
        raise StageError("rca requires a prior evaluation (no inference CSV recorded)")
    fields, rows = read_rows(ctx.last_inference_csv)
    if "input_path" not in fields or "object_name" not in fields:
        # join scores back onto the KPI csv by row order (predict dataloader is unshuffled)
        _, kpi_rows = read_rows(ctx.kpi_csv)
        if len(kpi_rows) != len(rows):
            raise StageError(
                f"inference CSV lacks identity columns and row count ({len(rows)}) "
                f"!= KPI csv ({len(kpi_rows)}); cannot align gaps"
            )
        for kpi_row, inf_row in zip(kpi_rows, rows):
            inf_row.update({k: v for k, v in kpi_row.items() if k not in inf_row or not inf_row[k]})

    pass_rows = [r for r in rows if normalize_label(r["label"]) == PASS_LABEL]
    defect_rows = [r for r in rows if normalize_label(r["label"]) != PASS_LABEL]
    scores_d = sorted(float(r["siamese_score"]) for r in defect_rows)
    if not scores_d:
        raise StageError("KPI set has no defect rows; RCA cannot pick a recall=100% threshold")
    threshold = scores_d[0]

    false_alarms = sorted(
        (r for r in pass_rows if float(r["siamese_score"]) >= threshold),
        key=lambda r: float(r["siamese_score"]),
        reverse=True,
    )[:200]
    near_miss = sorted(defect_rows, key=lambda r: float(r["siamese_score"]))[:20]
    gaps = []
    for r in false_alarms + near_miss:
        g = dict(r)
        g["weakness"] = r["siamese_score"]
        gaps.append(g)
    ctx.gaps_rows = gaps

    out = ctx.iter_dir(f"iter{ctx.current_iteration}") / "rca_results"
    write_rows(out / "kpi_gaps.csv", list(gaps[0].keys()) if gaps else ["input_path"], gaps)
    return StageResult(
        summary=(
            f"threshold@recall100={threshold:.4f}; weak={len(gaps)} "
            f"({len(false_alarms)} PASS false-alarms, {len(near_miss)} near-miss defects)"
        )
    )


def routing(ctx: AoiContext) -> StageResult:
    mining = [r for r in ctx.gaps_rows if normalize_label(r["label"]) == PASS_LABEL]
    anomaly = [r for r in ctx.gaps_rows if normalize_label(r["label"]) != PASS_LABEL]
    ctx.mining_targets = mining
    out = ctx.iter_dir(f"iter{ctx.current_iteration}") / "routing_results"
    if mining:
        write_rows(out / "mining_gaps.csv", list(mining[0].keys()), mining)
    if anomaly:
        write_rows(out / "anomalygen_gaps.csv", list(anomaly[0].keys()), anomaly)
    return StageResult(summary=f"routed {len(mining)} -> mining, {len(anomaly)} -> anomalygen")


# ---------------------------------------------------------------------------
# anomalygen (optional; mining-only by default)
# ---------------------------------------------------------------------------

def anomalygen(ctx: AoiContext) -> StageResult:
    num_sdg = ctx.cfg.pipeline.num_sdg
    if not num_sdg:
        return StageResult(summary="skipped — pipeline.num_sdg not set (mining-only run)")
    raise StageError(
        "AnomalyGen is not enabled in this deployment: synthetic-defect generation "
        "requires the paidf-anomalygen container (~22GB model assets, >=48GB GPU). "
        "Set pipeline.num_sdg to null for a mining-only run, or generate defects "
        "out-of-band with the tao-skill-bank paidf-anomalygen skill and append them "
        "to the mining pool."
    )


# ---------------------------------------------------------------------------
# data_mining — SigLIP embeddings (in-container) + cosine k-NN (in-service)
# ---------------------------------------------------------------------------

_EMBED_SCRIPT = r"""
import json, os, sys
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

manifest = json.load(open(sys.argv[1]))
model_id = manifest["model_id"]
device = "cuda" if torch.cuda.is_available() else "cpu"
processor = AutoImageProcessor.from_pretrained(model_id)
model = AutoModel.from_pretrained(model_id).to(device).eval()

def embed(paths):
    out, missing = [], 0
    batch, keep = [], []
    def flush():
        nonlocal batch, keep
        if not batch:
            return
        inputs = processor(images=batch, return_tensors="pt").to(device)
        with torch.no_grad():
            feats = model.get_image_features(**inputs)
        feats = torch.nn.functional.normalize(feats, dim=-1).cpu().tolist()
        for k, f in zip(keep, feats):
            out.append({"path": k, "emb": f})
        batch, keep = [], []
    for p in paths:
        if not os.path.exists(p):
            missing += 1
            continue
        try:
            batch.append(Image.open(p).convert("RGB")); keep.append(p)
        except Exception:
            missing += 1
            continue
        if len(batch) == 16:
            flush()
    flush()
    return out, missing

targets, tmiss = embed(manifest["targets"])
pool, pmiss = embed(manifest["pool"])
json.dump({"targets": targets, "pool": pool, "missing": {"targets": tmiss, "pool": pmiss}},
          open(sys.argv[2], "w"))
print(f"embedded targets={len(targets)} (missing {tmiss}) pool={len(pool)} (missing {pmiss})")
"""


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # embeddings are L2-normalized


def data_mining(ctx: AoiContext) -> StageResult:
    it_dir = ctx.iter_dir(f"iter{ctx.current_iteration}") / "mining_filter"
    it_dir.mkdir(parents=True, exist_ok=True)
    if not ctx.mining_targets:
        ctx.mined_rows_this_iter = []
        return StageResult(summary="no mining targets routed this iteration — nothing mined")

    _, pool_rows = read_rows(ctx.ws.pool_csv)
    kpi_images = ctx.ws.root / "kpi/images"
    target_files = []
    for row in ctx.mining_targets:
        rel = (row.get("input_path") or "").replace(f"{CONTAINER_WORKSPACE}/", "").replace("kpi/images/", "")
        f = image_file(kpi_images, rel, row.get("object_name", ""))
        target_files.append(str(f))
    pool_files = [str(ctx.ws.pool_dir / (r.get("input_path") or "")) for r in pool_rows]

    manifest = it_dir / "embed_manifest.json"
    out_json = it_dir / "embeddings.json"
    script = it_dir / "embed.py"
    script.write_text(_EMBED_SCRIPT)
    manifest.write_text(json.dumps({
        "model_id": SIGLIP_MODEL,
        "targets": [str(Path(p)).replace(str(ctx.ws.root), CONTAINER_WORKSPACE) for p in target_files],
        "pool": [str(Path(p)).replace(str(ctx.ws.root), CONTAINER_WORKSPACE) for p in pool_files],
    }))
    ctx.tao(
        ["python3", ctx.ws.container(script), ctx.ws.container(manifest), ctx.ws.container(out_json)],
        stage="mine-embed",
        log_path=it_dir / "stage.log",
    )
    data = json.loads(out_json.read_text())
    targets = data["targets"]
    pool = data["pool"]
    if not targets or not pool:
        raise StageError(
            f"embedding produced no usable vectors (targets={len(targets)}, pool={len(pool)}; "
            f"missing={data.get('missing')})",
            logs=tail(it_dir / "stage.log"),
        )

    min_sim = ctx.cfg.mining.min_similarity if ctx.cfg.mining.min_similarity is not None else 0.9
    topn = ctx.cfg.mining.topn
    pool_by_path = {str(ctx.ws.pool_dir / (r.get("input_path") or "")): r for r in pool_rows}
    already = {
        (r.get("input_path"), r.get("object_name"))
        for r in ctx.combined_rows
        if r.get("_provenance") == "mining_pool"
    }

    def _consumed(p: dict) -> bool:
        row = pool_by_path.get(p["path"].replace(CONTAINER_WORKSPACE, str(ctx.ws.root)))
        return row is not None and (POOL_INPUT_PATH, row.get("object_name")) in already

    # Prune BEFORE the top-N cut. The pool is never rewritten, so a consumed
    # candidate stays a nearest neighbour, retakes its slot every iteration and
    # is then dropped below -- the shortlist fills with rows already trained on
    # and the stage mines nothing while still reporting success.
    pool = [p for p in pool if not _consumed(p)]

    kept_paths: dict[str, float] = {}
    for t in targets:
        sims = sorted(((_cosine(t["emb"], p["emb"]), p["path"]) for p in pool), reverse=True)[:topn]
        for sim, path in sims:
            if sim >= min_sim:
                host_path = path.replace(CONTAINER_WORKSPACE, str(ctx.ws.root))
                kept_paths[host_path] = max(sim, kept_paths.get(host_path, 0.0))

    mined: list[dict] = []
    for host_path, sim in sorted(kept_paths.items(), key=lambda kv: -kv[1]):
        pool_row = pool_by_path.get(host_path)
        if pool_row is None:
            continue
        row = {k: pool_row.get(k, "") for k in ctx.csv_fieldnames}
        row["input_path"] = POOL_INPUT_PATH
        golden = (pool_row.get("golden_path") or "").strip()
        row["golden_path"] = f"kpi/images/{golden}" if golden and not golden.startswith("kpi/images") else golden
        row["label"] = normalize_label(pool_row.get("label", PASS_LABEL))
        row["_provenance"] = "mining_pool"
        row["_similarity"] = f"{sim:.4f}"
        if (row["input_path"], row.get("object_name")) in already:
            continue
        mined.append(row)

    # mid-iteration leakage gate on just the new rows
    leaks = leakage_pairs(mined, ctx.val_rows_norm)
    if leaks:
        raise StageError(f"mining leakage: {leaks} mined rows collide with the validation set")

    ctx.mined_rows_this_iter = mined
    write_rows(it_dir / "mining_pool.csv", ctx.csv_fieldnames + ["_similarity"], mined)
    write_rows(
        it_dir / "knn_summary.csv",
        ["candidate_count", "kept_count", "similarity_threshold", "topn"],
        [{
            "candidate_count": str(len(pool)),
            "kept_count": str(len(mined)),
            "similarity_threshold": str(min_sim),
            "topn": str(topn),
        }],
    )
    return StageResult(
        summary=f"mined {len(mined)}/{len(pool)} pool images (cosine>={min_sim}, topn={topn}, targets={len(targets)})"
    )


# ---------------------------------------------------------------------------
# train (assemble -> validate -> TAO train)
# ---------------------------------------------------------------------------

def train(ctx: AoiContext) -> StageResult:
    it = ctx.current_iteration
    it_dir = ctx.iter_dir(f"iter{it}")
    ds_dir = it_dir / "dataset"
    ds_dir.mkdir(parents=True, exist_ok=True)

    for row in ctx.mined_rows_this_iter:
        tagged = dict(row)
        ctx.combined_rows.append(tagged)
    combined_csv = ds_dir / f"train_combined_iter{it}.csv"
    write_rows(combined_csv, ctx.csv_fieldnames, ctx.combined_rows)
    write_rows(
        ds_dir / f"train_combined_iter{it}_provenance.csv",
        ctx.csv_fieldnames + ["_provenance"],
        ctx.combined_rows,
    )
    val_csv = ds_dir / f"validation_set_iter{it}.csv"
    write_rows(val_csv, ctx.csv_fieldnames, ctx.val_rows_norm)

    missing = validate_existence(ctx.ws, ctx.combined_rows, "")
    if missing:
        raise StageError(f"{len(missing)} assembled training paths missing on disk, e.g. {missing[:3]}")
    leaks = leakage_pairs(ctx.combined_rows, ctx.val_rows_norm)
    if leaks:
        raise StageError(f"assembled training CSV leaks {leaks} validation rows — hard stop")

    warm_start = ctx.best.ckpt_container or ctx.ws.container(ctx.base_ckpt)
    train_dir = it_dir / "train"
    epochs = ctx.cfg.training.epochs
    batch = ctx.cfg.training.batch_size or 8
    args = [
        "visual_changenet", "train",
        "-e", ctx.ws.container(ctx.train_spec),
        f"results_dir={ctx.ws.container(train_dir)}",
        f"train.pretrained_model_path={warm_start}",
        f"train.num_epochs={epochs}",
        # Without this the spec's own num_gpus wins, which is 1 in every spec we
        # ship -- so the request's value was accepted, validated, and silently
        # ignored, and training ran single-GPU on any box.
        f"train.num_gpus={ctx.cfg.training.num_gpus}",
        "train.checkpoint_interval=1",
        "train.validation_interval=1",
        f"dataset.classify.batch_size={batch}",
    ]
    backbone = _staged_backbone(ctx.ws)
    if backbone is not None:
        # skill Pre-Flight rule: point the spec at the workspace-staged backbone
        # (spec templates often carry a /data/pretrained_models/... path that
        # doesn't exist inside the workspace mount)
        args.append(f"model.backbone.pretrained_backbone_path={ctx.ws.container(backbone)}")
    args += [
        f"dataset.classify.train_dataset.csv_path={ctx.ws.container(combined_csv)}",
        f"dataset.classify.train_dataset.images_dir={CONTAINER_WORKSPACE}",
        f"dataset.classify.validation_dataset.csv_path={ctx.ws.container(val_csv)}",
        f"dataset.classify.validation_dataset.images_dir={CONTAINER_WORKSPACE}",
        f"dataset.classify.test_dataset.csv_path={ctx.ws.container(val_csv)}",
        f"dataset.classify.test_dataset.images_dir={CONTAINER_WORKSPACE}",
    ]
    ctx.tao(args, stage="train", log_path=it_dir / "train.log")

    choice = _discover_checkpoint(train_dir)
    if choice is None:
        raise StageError(
            f"training reported success but no checkpoint found under {train_dir} "
            "(results_dir must be a container path under the workspace mount)",
            logs=tail(it_dir / "train.log"),
        )
    ckpt = choice.path
    ctx.current_ckpt_container = ctx.ws.container(ckpt)
    ctx.current_ckpt_host = ckpt  # keep the resolved host path; evaluate must not re-guess it
    _relink_tao_latest(train_dir, ckpt)
    pruned = _prune_epoch_checkpoints(train_dir, ckpt)
    n_mined = sum(1 for r in ctx.combined_rows if r.get("_provenance") == "mining_pool")
    return StageResult(
        summary=(
            f"trained {epochs} epochs on {len(ctx.combined_rows)} rows "
            f"({n_mined} mined) from {Path(warm_start).name}; ckpt={choice.describe()}"
            + (f"; pruned {pruned} epoch checkpoints" if pruned else "")
        )
    )


def _staged_backbone(ws: Workspace) -> Path | None:
    backbone_dir = ws.root / "augmentation/backbone"
    for pattern in ("*.safetensors", "*.pth", "*.ckpt"):
        found = sorted(backbone_dir.glob(pattern))
        if found:
            return found[0]
    return None


#: TAO's per-epoch checkpoint filename: ModelCheckpoint(filename='model_{epoch:03d}_{step:05d}')
#: rendered with CHECKPOINT_EQUALS_CHAR='_' -> model_epoch_029_step_01110.pth
_EPOCH_CKPT_RE = re.compile(r"^model_epoch_(\d+)_step_\d+\.pth$")


def _epoch_checkpoints(train_dir: Path) -> dict[int, Path]:
    """Map epoch -> its checkpoint file, for the epochs TAO actually saved."""
    found: dict[int, Path] = {}
    for path in train_dir.rglob("model_epoch_*.pth"):
        m = _EPOCH_CKPT_RE.match(path.name)
        if m:
            found[int(m.group(1))] = path
    return found


def _discover_checkpoint(train_dir: Path) -> CheckpointChoice | None:
    """Pick this iteration's checkpoint: the epoch with the lowest val_loss.

    TAO's `changenet_model_classify_latest.pth` is a symlink to the FINAL
    epoch, and on this workload the final epoch is routinely 20-55% worse by
    val_loss than the best one -- training overfits a few-hundred-row set long
    before it stops. `train.checkpoint_interval=1` means every epoch is on
    disk, so selection is a local lookup rather than a re-run.

    Only epochs with BOTH a checkpoint on disk and a val_loss are eligible, so
    a coarser checkpoint_interval degrades to "best of what was saved" instead
    of naming a file that does not exist. Ties go to the earlier epoch: equal
    validation loss, less overfitting.

    Falls back to TAO's `*_latest.pth` (then to the newest `.pth`) when the
    status file is missing or carries no per-epoch val_loss -- a successful
    training run must never fail on selection bookkeeping.
    """
    by_epoch = _epoch_checkpoints(train_dir)
    losses = _tao_val_losses(train_dir)
    eligible = [e for e in by_epoch if e in losses]
    if eligible:
        best = min(eligible, key=lambda e: (losses[e], e))
        return CheckpointChoice(path=by_epoch[best], epoch=best, val_loss=losses[best])

    preferred = list(train_dir.rglob("changenet_model_classify_latest.pth"))
    if preferred:
        return CheckpointChoice(path=preferred[0])
    candidates = sorted(train_dir.rglob("*.pth"), key=lambda p: p.stat().st_mtime, reverse=True)
    return CheckpointChoice(path=candidates[0]) if candidates else None


def _relink_tao_latest(train_dir: Path, keep: Path) -> None:
    """Re-point TAO's `*_latest.pth` at the checkpoint we selected.

    TAO writes it as a symlink to the final epoch (`save_last='link'`). Once
    pruning removes that epoch the link dangles, and anything reaching for
    TAO's conventional filename -- a resumed run, an operator, a hand-copied
    inference spec -- lands on a broken path. Re-pointing keeps the name valid
    and makes it mean "the checkpoint this iteration selected".

    Best effort: `_discover_checkpoint` returns the concrete epoch file and
    every downstream record (`best_model.json`, the warm start, the published
    inference spec) names that file, so a filesystem that refuses symlinks
    costs nothing beyond the final epoch surviving the prune.
    """
    for link in train_dir.rglob("changenet_model_classify_latest.pth"):
        if link.resolve() == keep.resolve():
            continue
        # Match TAO's own form -- a bare relative name -- so the link keeps
        # resolving under the container mount as well as on the host.
        target = keep.name if keep.parent == link.parent else str(keep)
        try:
            link.unlink()
            link.symlink_to(target)
        except OSError:
            continue


def _prune_epoch_checkpoints(train_dir: Path, keep: Path) -> int:
    """Drop the per-epoch checkpoints nothing downstream reads; return the count.

    `train.checkpoint_interval=1` guarantees a checkpoint exists at all (the
    spec default of 10 saves nothing on a short run — see the module docstring);
    it is not a request to retain one per epoch. Only the checkpoint
    `_discover_checkpoint` resolves is ever evaluated, warm-started from, or
    registered, so the rest are write-only at ~1.7 GB apiece. Pruning here
    rather than after the loop keeps peak disk flat instead of spiking to the
    full run's worth of checkpoints.

    The keeper and TAO's `*_latest.pth` are always retained. `_relink_tao_latest`
    normally points that symlink at the keeper, so retaining it costs nothing;
    when re-pointing was not possible it still holds the final epoch, and
    keeping one extra checkpoint beats leaving a dangling link. Only this
    iteration's `train_dir` is touched, so an earlier iteration's best (the next
    iteration's warm start) is out of scope.
    """
    keepers = {keep.resolve()}
    keepers.update(p.resolve() for p in train_dir.rglob("changenet_model_classify_latest.pth"))
    removed = 0
    for stale in train_dir.rglob("*.pth"):
        if stale.resolve() in keepers:
            continue
        try:
            stale.unlink()
        except OSError:
            # Reclaiming disk is best-effort: a checkpoint we cannot remove is
            # not a reason to fail a training run that already succeeded.
            continue
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# post_loop
# ---------------------------------------------------------------------------

def register_candidate_model(ctx: AoiContext) -> StageResult:
    best = ctx.best
    if best.ckpt_host is None and best.ckpt_container is None:
        raise StageError("no evaluated checkpoint to register")
    backbone = ctx.ws.root / "augmentation/backbone/model.safetensors"
    meta = {
        "checkpoint": str(best.ckpt_host) if best.ckpt_host else best.ckpt_container,
        "best_iteration": best.iteration,
        "best_is_baseline": best.is_baseline,
        "threshold": best.threshold,
        "far_pct": best.far_pct,
        "backbone": str(backbone) if backbone.exists() else None,
        "images_dir": str(ctx.ws.root / "kpi/images"),
        "training_spec": str(ctx.train_spec),
        "metrics": best.metrics,
    }
    (ctx.run_dir / "best_model.json").write_text(json.dumps(meta, indent=2))

    spec_text = ctx.infer_spec.read_text()
    ckpt_line = ctx.best.ckpt_container or ctx.ws.container(ctx.base_ckpt)
    patched, n = re.subn(r"(?m)^(\s*checkpoint:\s*).*$", rf"\g<1>{ckpt_line}", spec_text, count=1)
    (ctx.run_dir / "best_model_inference_spec.yaml").write_text(patched if n else spec_text)
    return StageResult(
        summary=(
            f"registered best model: iter{best.iteration}"
            f"{' (baseline)' if best.is_baseline else ''} FAR={best.far_pct:.2f}% "
            f"threshold={best.threshold:.4f}"
        )
    )


def publish_results(ctx: AoiContext) -> StageResult:
    # results payload is assembled by the runner from ctx.best; here we make
    # the on-disk artifacts contract real (deft_state.json / loop_log.jsonl /
    # DEFT_Loop_Report.html are written by the runner's finalizer as well).
    return StageResult(summary=f"results published under {ctx.run_dir}")


STAGES = {
    "validate_training_pool": validate_training_pool,
    "baseline_eval": baseline_eval,
    "rca": rca,
    "routing": routing,
    "anomalygen": anomalygen,
    "data_mining": data_mining,
    "train": train,
    "evaluate": evaluate,
    "register_candidate_model": register_candidate_model,
    "publish_results": publish_results,
}
