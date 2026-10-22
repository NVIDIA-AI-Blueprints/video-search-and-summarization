"""Intra-step liveness: `Progress.updated_at` must move while a step is working.

Every other Progress field is step-granular, so during a multi-hour `train` they
are all constant. A caller polling the status endpoint then cannot tell a long
step from a hang -- which is exactly how Data Flywheel's no-progress watchdog
cancelled a healthy run that had already completed every epoch.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

from app.execution.docker_backend import ContainerSpec, run_container


def _spec(tmp_path: Path, **kw) -> ContainerSpec:
    return ContainerSpec(
        image="busybox", args=["true"], mounts={}, name="t",
        log_path=tmp_path / "stage.log", use_gpu=False, **kw
    )


class _Proc:
    """A container that stays alive for `alive_polls` poll cycles."""

    def __init__(self, alive_polls: int):
        self._left = alive_polls

    def poll(self):
        if self._left > 0:
            self._left -= 1
            return None
        return 0

    def wait(self, timeout=None):
        return 0


def test_heartbeat_fires_while_the_log_grows(tmp_path):
    log = tmp_path / "stage.log"
    beats: list[int] = []

    def grow_then_poll(*a, **kw):
        # simulate the container appending output between polls
        with open(log, "ab") as fh:
            fh.write(b"Epoch 1: 100%\n")
        return _Proc(alive_polls=3)

    with (
        patch("app.execution.docker_backend.subprocess.Popen", side_effect=grow_then_poll),
        patch("app.execution.docker_backend.time.sleep", side_effect=lambda s: _append(log)),
    ):
        run_container(_spec(tmp_path), threading.Event(), on_heartbeat=lambda: beats.append(1))

    # One beat per observed size change; the container "wrote" on every sleep.
    assert beats, "no heartbeat fired while the log was growing"


def _append(log: Path) -> None:
    with open(log, "ab") as fh:
        fh.write(b"Epoch n: 100%\n")


def test_no_heartbeat_when_the_log_is_static(tmp_path):
    """A hung container produces no output, so it must produce no heartbeat.

    This is the half that makes the signal meaningful: if it fired on every poll
    regardless, it would report a hung run as healthy forever.
    """
    log = tmp_path / "stage.log"
    log.write_bytes(b"started\n")
    beats: list[int] = []

    with (
        patch("app.execution.docker_backend.subprocess.Popen",
              side_effect=lambda *a, **kw: _Proc(alive_polls=4)),
        patch("app.execution.docker_backend.time.sleep", return_value=None),
    ):
        run_container(_spec(tmp_path), threading.Event(), on_heartbeat=lambda: beats.append(1))

    # The first poll observes the size once (last_size starts at -1); after that
    # a static log must be silent.
    assert len(beats) <= 1, f"a static log produced {len(beats)} heartbeats"


def test_runs_without_a_heartbeat_callback(tmp_path):
    """on_heartbeat is optional -- existing callers must be unaffected."""
    with (
        patch("app.execution.docker_backend.subprocess.Popen",
              side_effect=lambda *a, **kw: _Proc(alive_polls=2)),
        patch("app.execution.docker_backend.time.sleep", return_value=None),
    ):
        assert run_container(_spec(tmp_path), threading.Event()) == 0


def test_progress_carries_updated_at():
    """Optional and null by default, so an existing client sees no change."""
    from datetime import datetime, timezone

    from app.schemas.fine_tune_models import Progress

    p = Progress(current_iteration=1, total_iterations=5)
    assert p.updated_at is None
    assert "updated_at" in p.model_dump()

    now = datetime.now(timezone.utc)
    p.updated_at = now
    assert p.model_dump()["updated_at"] == now


# --------------------------------------------------------------------------- #
# TAO publishes structured progress of its own. Prefer it over inferring from
# log size: it is TAO's own account of itself, and gives a real position.
# --------------------------------------------------------------------------- #

def test_reads_the_last_tao_status_record(tmp_path):
    from app.execution.aoi_pipeline import _read_tao_status

    # TAO appends records with no separator between them -- this is the real
    # on-disk shape, captured from a run.
    f = tmp_path / "status.json"
    f.write_text(
        '{"date": "8/18/2026", "status": "STARTED", "message": "Starting"}'
        '{"epoch": 5, "max_epoch": 29, "step": 1500, "max_step": 8730, "eta": "0:26:12"}'
        '{"epoch": 6, "max_epoch": 29, "step": 1800, "max_step": 8730, "eta": "0:25:03"}'
    )
    d = _read_tao_status(f)
    assert d["epoch"] == 6 and d["max_epoch"] == 29
    assert d["eta"] == "0:25:03"
    # noise TAO also writes must not leak into the progress payload
    assert "verbosity" not in d and "message" not in d


def test_tolerates_a_partial_trailing_record(tmp_path):
    """The file is appended to by a live container, so the tail can be half a line."""
    from app.execution.aoi_pipeline import _read_tao_status

    f = tmp_path / "status.json"
    f.write_text(
        '{"epoch": 4, "max_epoch": 29}'
        '{"epoch": 5, "max_ep'          # caught mid-append
    )
    d = _read_tao_status(f)
    assert d == {"epoch": 4, "max_epoch": 29}


def test_missing_or_unreadable_status_is_not_an_error(tmp_path):
    from app.execution.aoi_pipeline import _read_tao_status

    assert _read_tao_status(tmp_path / "nope.json") is None
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert _read_tao_status(empty) is None


# --------------------------------------------------------------------------- #
# `results_dir` is what the TAO container sees, not what this service sees.
# Opening it verbatim silently yields no detail forever -- the failure is a
# permanent `None`, never an error, so only an explicit test catches it.
# --------------------------------------------------------------------------- #

def test_maps_the_container_results_dir_back_to_the_host(tmp_path):
    from app.config import CONTAINER_WORKSPACE
    from app.execution.aoi_pipeline import _tao_status_path
    from app.execution.workspace import Workspace

    ws = Workspace(root=tmp_path)
    # Real layout: results_dir=<container>/results/<run>/iter1/train, and TAO
    # writes status.json one level deeper, under its own task subdirectory.
    status = tmp_path / "results/run/iter1/train/train/status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"epoch": 2, "max_epoch": 29}')

    args = [f"results_dir={CONTAINER_WORKSPACE}/results/run/iter1/train"]
    assert _tao_status_path(args, ws) == status


def test_results_dir_outside_the_workspace_yields_no_detail(tmp_path):
    from app.execution.aoi_pipeline import _tao_status_path
    from app.execution.workspace import Workspace

    ws = Workspace(root=tmp_path)
    assert _tao_status_path(["results_dir=/somewhere/else"], ws) is None
    assert _tao_status_path(["no_results_dir=x"], ws) is None


def test_relative_results_dir_resolves_under_the_workspace_root(tmp_path):
    from app.execution.aoi_pipeline import _tao_status_path
    from app.execution.workspace import Workspace

    ws = Workspace(root=tmp_path)
    status = tmp_path / "out/train/status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"epoch": 1}')
    assert _tao_status_path(["results_dir=out"], ws) == status


def test_status_path_is_resolved_after_the_container_creates_it(tmp_path):
    """TAO makes its own results subdirectory, so the path cannot be resolved up front.

    Resolving once before `run_container` pins None for the whole run: at that
    moment `<results_dir>/<task>/` does not exist, so the glob matches nothing
    and `detail` never populates no matter what TAO later writes.
    """
    from app.config import CONTAINER_WORKSPACE
    from app.execution.aoi_pipeline import _tao_status_path
    from app.execution.workspace import Workspace

    ws = Workspace(root=tmp_path)
    args = [f"results_dir={CONTAINER_WORKSPACE}/results/run/iter1/train"]

    # Before the container runs, nothing to find -- this is the state the old
    # code cached forever.
    assert _tao_status_path(args, ws) is None

    # TAO starts and writes its first record.
    status = tmp_path / "results/run/iter1/train/train/status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"epoch": 0, "max_epoch": 29}')

    # A heartbeat arriving now must find it.
    assert _tao_status_path(args, ws) == status


def test_tao_resolves_the_status_path_lazily_per_heartbeat(tmp_path):
    """The regression this file exists for: `detail` must populate mid-run.

    Drives the real `AoiContext.tao` wiring with a stubbed container runner:
    the first heartbeat fires before TAO has created its results subdirectory
    (as it does in production), the second after. Caching the resolution before
    `run_container` passes every path-level test and still yields `detail: null`
    for the whole run, which is exactly what shipped.
    """
    import threading
    from types import SimpleNamespace

    from app.config import CONTAINER_WORKSPACE
    from app.execution import aoi_pipeline
    from app.execution.aoi_pipeline import AoiContext
    from app.execution.workspace import Workspace

    ws = Workspace(root=tmp_path)
    results = f"{CONTAINER_WORKSPACE}/results/run/iter1/train"
    status = tmp_path / "results/run/iter1/train/train/status.json"

    progress = SimpleNamespace(updated_at=None, detail=None)
    ctx = SimpleNamespace(
        ws=ws,
        cancel_event=threading.Event(),
        record=SimpleNamespace(id="0123456789ab", progress=progress),
        current_iteration=1,
        on_heartbeat=lambda p=None: AoiContext.on_heartbeat(ctx, p),
        container_name=lambda stage: f"deft-01234567-{stage}-i1",
    )

    def fake_run_container(spec, cancel_event, on_heartbeat=None):
        on_heartbeat()                      # before TAO has made its directory
        assert progress.detail is None
        status.parent.mkdir(parents=True)
        status.write_text('{"epoch": 7, "max_epoch": 29, "eta": "0:12:00"}')
        on_heartbeat()                      # after
        return 0

    with patch.object(aoi_pipeline, "run_container", fake_run_container):
        AoiContext.tao(ctx, [f"results_dir={results}"], stage="train",
                       log_path=tmp_path / "train.log")

    assert progress.detail == {"epoch": 7, "max_epoch": 29, "eta": "0:12:00"}
    assert progress.updated_at is not None
