"""evaluate() must reuse the checkpoint host path train() discovered, never
re-guess a layout string — otherwise a best model whose real checkpoint doesn't
match the guess publishes with ckpt_host=None and the runner silently falls back
to the baseline weights (wrong model, right-looking metrics).

Run: uv run --with "pydantic>=2.6" pytest tests/test_aoi_pipeline_ckpt.py
"""

from __future__ import annotations

import json
import types

import app.execution.aoi_pipeline as ap


def test_evaluate_reuses_discovered_ckpt_host(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_eval(ctx, *, ckpt_container, ckpt_host, out_name, iteration, is_baseline):
        captured.update(
            ckpt_container=ckpt_container, ckpt_host=ckpt_host,
            iteration=iteration, is_baseline=is_baseline,
        )
        return "ok"

    monkeypatch.setattr(ap, "_evaluate", fake_eval)
    # A layout that does NOT match the old hard-coded guess
    # (iter{it}/train/train/changenet_model_classify_latest.pth) — the discovered
    # path must still be the one evaluate hands to _evaluate.
    discovered = tmp_path / "iter1/train/model_epoch_007.pth"
    ctx = types.SimpleNamespace(
        current_ckpt_container="/workspace/iter1/ckpt.pth",
        current_ckpt_host=discovered,
        current_iteration=1,
        run_dir=tmp_path,
    )

    ap.evaluate(ctx)

    assert captured["ckpt_host"] == discovered  # reused, not re-guessed to None
    assert captured["ckpt_container"] == "/workspace/iter1/ckpt.pth"
    assert captured["is_baseline"] is False


def test_evaluate_without_trained_ckpt_raises(monkeypatch):
    ctx = types.SimpleNamespace(current_ckpt_container=None, current_iteration=1)
    try:
        ap.evaluate(ctx)
    except ap.StageError:
        return
    raise AssertionError("evaluate must raise StageError when no checkpoint was trained")


def test_prune_keeps_discovered_ckpt_and_drops_the_rest(tmp_path):
    train_dir = tmp_path / "iter1/train"
    train_dir.mkdir(parents=True)
    for epoch in range(4):
        (train_dir / f"model_epoch_00{epoch}_step_0011{epoch}.pth").write_bytes(b"w")
    keep = train_dir / "model_epoch_003_step_00113.pth"

    removed = ap._prune_epoch_checkpoints(train_dir, keep)

    assert removed == 3
    assert keep.exists()
    assert sorted(p.name for p in train_dir.rglob("*.pth")) == [keep.name]


def test_prune_retains_tao_latest_symlinkable_checkpoint(tmp_path):
    # *_latest.pth is TAO's own conventional name and _discover_checkpoint still
    # falls back to it, so it must survive even when it is not the keeper we were
    # handed — and even when _relink_tao_latest could not re-point it (a plain
    # file here, as on a filesystem without symlinks).
    train_dir = tmp_path / "iter1/train"
    (train_dir / "nested").mkdir(parents=True)
    latest = train_dir / "nested/changenet_model_classify_latest.pth"
    latest.write_bytes(b"w")
    keep = train_dir / "model_epoch_009_step_01026.pth"
    keep.write_bytes(b"w")
    (train_dir / "model_epoch_008_step_00912.pth").write_bytes(b"w")

    removed = ap._prune_epoch_checkpoints(train_dir, keep)

    assert removed == 1
    assert latest.exists() and keep.exists()


def test_prune_does_not_touch_other_iterations(tmp_path):
    # The previous iteration's best is the next iteration's warm start; pruning
    # iter2 must leave iter1 intact.
    prev = tmp_path / "iter1/train"
    prev.mkdir(parents=True)
    prev_best = prev / "model_epoch_004_step_00570.pth"
    prev_best.write_bytes(b"w")
    train_dir = tmp_path / "iter2/train"
    train_dir.mkdir(parents=True)
    keep = train_dir / "model_epoch_004_step_00570.pth"
    keep.write_bytes(b"w")
    (train_dir / "model_epoch_003_step_00456.pth").write_bytes(b"w")

    removed = ap._prune_epoch_checkpoints(train_dir, keep)

    assert removed == 1
    assert prev_best.exists()


# --- checkpoint selection: best val_loss, not TAO's *_latest (final epoch) ----
#
# TAO writes one status record per epoch from on_train_epoch_end, tagged with
# `epoch` and carrying the kpi dict on_validation_epoch_end just published — so
# the record tagged epoch=N holds epoch N's own val_loss, matching
# model_epoch_{N:03d}_*.pth saved from that same hook.


def _write_status(train_dir, val_losses: dict[int, float], *, nested="train"):
    """Emit a status.json shaped like TAO's: brace-concatenated, no separators."""
    task_dir = train_dir / nested if nested else train_dir
    task_dir.mkdir(parents=True, exist_ok=True)
    recs = [{"date": "9/14/2026", "message": "Starting Training Loop."}]
    for epoch, loss in sorted(val_losses.items()):
        recs.append({"message": "Eval metrics generated.", "kpi": {"val_loss": loss}})
        recs.append({
            "epoch": epoch, "max_epoch": max(val_losses), "step": (epoch + 1) * 38,
            "message": "Training loop in progress",
            "kpi": {"val_loss": loss, "val_acc": 84.7, "val_fpr": 0.0},
        })
        recs.append({"message": "Train metrics generated.", "kpi": {"train_loss": 0.3}})
    (task_dir / "status.json").write_text("".join(json.dumps(r) for r in recs))
    return task_dir


def _write_epochs(task_dir, epochs, *, latest_of=None):
    for epoch in epochs:
        (task_dir / f"model_epoch_{epoch:03d}_step_{(epoch + 1) * 38:05d}.pth").write_bytes(b"w")
    if latest_of is not None:
        link = task_dir / "changenet_model_classify_latest.pth"
        link.symlink_to(f"model_epoch_{latest_of:03d}_step_{(latest_of + 1) * 38:05d}.pth")
        return link
    return None


def test_discover_picks_lowest_val_loss_epoch_not_the_latest(tmp_path):
    train_dir = tmp_path / "iter3/train"
    task_dir = _write_status(train_dir, {0: 0.389, 1: 0.760, 2: 0.443, 3: 0.336, 4: 0.441})
    _write_epochs(task_dir, range(5), latest_of=4)

    choice = ap._discover_checkpoint(train_dir)

    assert choice.epoch == 3                      # not 4, which *_latest points at
    assert choice.path.name == "model_epoch_003_step_00152.pth"
    assert choice.val_loss == 0.336
    assert "epoch 3" in choice.describe() and "0.3360" in choice.describe()


def test_discover_breaks_val_loss_ties_toward_the_earlier_epoch(tmp_path):
    # Equal validation loss, less overfitting.
    train_dir = tmp_path / "iter1/train"
    task_dir = _write_status(train_dir, {0: 0.50, 1: 0.32, 2: 0.41, 3: 0.32})
    _write_epochs(task_dir, range(4), latest_of=3)

    assert ap._discover_checkpoint(train_dir).epoch == 1


def test_discover_ignores_epochs_with_no_checkpoint_on_disk(tmp_path):
    # A coarser checkpoint_interval must degrade to "best of what was saved",
    # never name a file that does not exist.
    train_dir = tmp_path / "iter1/train"
    task_dir = _write_status(train_dir, {0: 0.50, 1: 0.20, 2: 0.41, 3: 0.44})
    _write_epochs(task_dir, [0, 2, 3], latest_of=3)   # epoch 1 (the best) never saved

    choice = ap._discover_checkpoint(train_dir)

    assert choice.epoch == 2 and choice.path.exists()


def test_discover_falls_back_to_tao_latest_without_val_loss(tmp_path):
    train_dir = tmp_path / "iter1/train"
    task_dir = train_dir / "train"
    task_dir.mkdir(parents=True)
    link = _write_epochs(task_dir, range(3), latest_of=2)   # no status.json at all

    choice = ap._discover_checkpoint(train_dir)

    assert choice.path == link
    assert choice.epoch is None and "fell back" in choice.describe()


def test_discover_falls_back_when_status_json_is_unparseable(tmp_path):
    train_dir = tmp_path / "iter1/train"
    task_dir = train_dir / "train"
    task_dir.mkdir(parents=True)
    link = _write_epochs(task_dir, range(3), latest_of=2)
    (task_dir / "status.json").write_text('{"message": "Starting"}{"epoch": 1, "kpi": {"val_l')

    assert ap._discover_checkpoint(train_dir).path == link


def test_discover_returns_none_when_nothing_was_written(tmp_path):
    train_dir = tmp_path / "iter1/train"
    train_dir.mkdir(parents=True)

    assert ap._discover_checkpoint(train_dir) is None


def test_relink_then_prune_leaves_only_the_selected_epoch(tmp_path):
    # End state of train(): one real checkpoint (the best), and TAO's
    # conventional filename still resolving to it rather than dangling.
    train_dir = tmp_path / "iter3/train"
    task_dir = _write_status(train_dir, {0: 0.389, 1: 0.760, 2: 0.336, 3: 0.441})
    link = _write_epochs(task_dir, range(4), latest_of=3)

    choice = ap._discover_checkpoint(train_dir)
    ap._relink_tao_latest(train_dir, choice.path)
    removed = ap._prune_epoch_checkpoints(train_dir, choice.path)

    assert removed == 3
    assert choice.path.exists()
    assert link.is_symlink() and link.resolve() == choice.path.resolve()
    assert sorted(p.name for p in task_dir.glob("model_epoch_*.pth")) == [choice.path.name]


def test_relink_is_a_noop_when_latest_already_points_at_the_keeper(tmp_path):
    train_dir = tmp_path / "iter1/train"
    task_dir = _write_status(train_dir, {0: 0.20, 1: 0.44})
    link = _write_epochs(task_dir, range(2), latest_of=0)
    keep = task_dir / "model_epoch_000_step_00038.pth"

    ap._relink_tao_latest(train_dir, keep)

    assert link.is_symlink() and link.resolve() == keep.resolve()
