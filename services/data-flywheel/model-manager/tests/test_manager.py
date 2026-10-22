# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the client-agnostic ModelManager orchestration.

ModelManager is exercised against an in-memory ``FakeAdapter`` returning scripted
``ServingState``s, so these tests assert the invariant mechanics (swap lifecycle,
readiness convergence, timeout, cleanup guard) independent of any serving client.
The AOI-specific mapping/composition lives in ``test_aoi_adapter.py``.
"""

import logging

import pytest

from src.adapters import activation as activation_mod
from src.adapters.base import (
    EvictionAttestation,
    ServingAdapter,
    ServingState,
    VersionInventory,
    VersionState,
)
from src.api.schemas import (
    ApplyRequest,
    CleanupRequest,
    PullRequest,
    RoutingEntry,
    UnloadRequest,
)
from src.config import settings
from src.core.errors import ModelManagerError
from src.core.manager import ModelManager, _control_reflected

NAME = settings.model_name


class FakeAdapter(ServingAdapter):
    """In-memory ``ServingAdapter`` that returns scripted ``ServingState``s.

    ``states`` may be a single ``ServingState`` (returned for every read) or a
    list; when a list is given, successive ``serving_state`` calls pop from the
    front and the final element repeats. This lets a test model the "before"
    (from_version) read separately from the post-apply convergence reads.

    ``inventories`` scripts the per-version inventory the same way. ``cleanup``
    makes one inventory read, so a list models successive reclaims within a
    single sweep tick -- the world moving between two deletes.
    """

    def __init__(self, states, *, stage_return="/models/x/1", inventories=None):
        if isinstance(states, ServingState):
            states = [states]
        self._states = list(states)
        self._stage_return = stage_return
        if isinstance(inventories, VersionInventory):
            inventories = [inventories]
        self._inventories = list(inventories) if inventories else None
        self.stage_calls: list[tuple] = []
        self.apply_calls: list[dict] = []
        self.unload_calls: list[tuple] = []
        self.state_reads: list[str] = []
        self.destructive_state_reads: list[bool] = []
        self.inventory_reads: list[tuple[str, str | None, bool]] = []
        self.deleted: list[tuple] = []

    def stage(
        self,
        name,
        version,
        artifact_uri,
        sha256,
        *,
        spec_uri=None,
        threshold=None,
        idempotency_key=None,
    ):
        self.stage_calls.append(
            (name, version, artifact_uri, sha256, spec_uri, threshold, idempotency_key)
        )
        return self._stage_return

    def apply_control(
        self, *, control, name, candidate, from_version, canary_weight, idempotency_key=None
    ):
        self.apply_calls.append(
            {
                "control": control,
                "name": name,
                "candidate": candidate,
                "from_version": from_version,
                "canary_weight": canary_weight,
                "idempotency_key": idempotency_key,
            }
        )

    def serving_state(self, name, *, destructive=False):
        self.state_reads.append(name)
        # So a test can assert the guard stack runs on the destructive read.
        self.destructive_state_reads.append(destructive)
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    def unload(self, name, version):
        self.unload_calls.append((name, version))
        return EvictionAttestation(version=version, evicted_affirmed=True)

    def inventory(self, name, *, reclaiming=None, evicted_by_caller=False):
        # With arguments, so a test can assert only the reclaim read names one.
        self.inventory_reads.append((name, reclaiming, evicted_by_caller))
        if self._inventories is None:
            # Pre-inventory cleanup tests only care about the routing guard.
            return _inventory(deletable=["1.4.0"])
        if len(self._inventories) > 1:
            return self._inventories.pop(0)
        return self._inventories[0]

    def delete_version(self, name, version):
        self.deleted.append((name, version))


def _manager(states, **kwargs):
    adapter = FakeAdapter(states, **kwargs)
    return ModelManager(adapter=adapter), adapter


def _state(
    default_version=None,
    routing=None,
    serving_versions=None,
    loaded=None,
    ready=True,
    routing_available=True,
    loaded_available=True,
    ready_versions=None,
    resident_versions=None,
):
    return ServingState(
        default_version=default_version,
        routing=routing or [],
        serving_versions=serving_versions or set(),
        loaded_versions=loaded or [],
        ready=ready,
        routing_available=routing_available,
        loaded_available=loaded_available,
        # "Everything loaded is servable", so existing cases keep their meaning.
        ready_versions=(loaded or []) if ready_versions is None else ready_versions,
        # Likewise "everything known is still in memory".
        resident_versions=(loaded or []) if resident_versions is None else resident_versions,
    )


def _inventory(versions=(), deletable=(), available=True, name=NAME):
    return VersionInventory(
        model_name=name,
        versions=[v if isinstance(v, VersionState) else VersionState(**v) for v in versions],
        deletable=list(deletable),
        available=available,
    )


# ------------------------------------------------------------------------ pull
def test_pull_delegates_to_adapter_stage():
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = PullRequest(
        model_name=NAME, version="1.1.0", artifact_uri="file:///a", format="onnx", sha256="abc"
    )
    resp = manager.pull(req)
    assert adapter.stage_calls == [(NAME, "1.1.0", "file:///a", "abc", None, None, None)]
    assert resp.status == "staged"
    assert resp.repo_path == "/models/x/1"


def test_pull_threads_spec_uri_to_adapter():
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = PullRequest(
        model_name=NAME,
        version="1.1.0",
        artifact_uri="file:///a",
        format="onnx",
        sha256="abc",
        spec_uri="s3://aoi-artifacts/runs/1.1.0/baseline_spec.yaml",
    )
    manager.pull(req)
    assert adapter.stage_calls[0][4] == "s3://aoi-artifacts/runs/1.1.0/baseline_spec.yaml"


def test_pull_threads_the_idempotency_key_to_the_adapter():
    """Without this the key never reaches the load, and a re-driven pull starts a
    second engine build for weights that are already being converted."""
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = PullRequest(
        model_name=NAME,
        version="1.1.0",
        artifact_uri="file:///a",
        format="onnx",
        sha256="abc",
        idempotency_key="promo-7:pull",
    )
    manager.pull(req)
    assert adapter.stage_calls[0][6] == "promo-7:pull"


def test_apply_threads_the_idempotency_key_to_the_adapter():
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = ApplyRequest(
        model_name=NAME, version="1.1.0", control="full_switch", idempotency_key="promo-7:full"
    )
    manager.apply(req)
    assert adapter.apply_calls[0]["idempotency_key"] == "promo-7:full"


def test_pull_threads_threshold_to_adapter():
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = PullRequest(
        model_name=NAME,
        version="1.1.0",
        artifact_uri="file:///a",
        format="onnx",
        sha256="abc",
        threshold=0.63,
    )
    manager.pull(req)
    assert adapter.stage_calls[0][5] == pytest.approx(0.63)


# ---------------------------------------------------------- apply orchestration
def test_apply_delegates_control_and_records_from_version():
    # The first read yields from_version 1.0.0; the convergence read reflects the
    # full_switch, so the swap succeeds.
    before = _state(default_version="1.0.0")
    after = _state(
        default_version="1.1.0",
        routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
        loaded=["1.1.0"],
        ready=True,
    )
    manager, adapter = _manager([before, after])
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)

    assert adapter.apply_calls[0] == {
        "control": "full_switch",
        "name": NAME,
        "candidate": "1.1.0",
        "from_version": "1.0.0",
        "canary_weight": None,
        "idempotency_key": None,
    }
    assert resp.from_version == "1.0.0"
    assert resp.to_version == "1.1.0"
    assert manager.convergence(resp.applied).converged is True


def test_a_redrive_whose_routing_already_landed_issues_no_second_write():
    """Readiness cannot be influenced by a routing write, so it must not earn one.

    The suppression used to be gated on FULL convergence -- routing reflected
    AND the candidate servable. A re-drive of a candidate whose routing had
    landed but whose engine was still building therefore failed the readiness
    half and issued a second routing write: exactly the reload-racing-live-
    inference risk the guard exists to prevent, taken on behalf of a condition
    the write cannot change.

    ``noop`` is reported from the same predicate, and that is the honest one for
    it: what makes a re-drive's ``from_version`` stale is the routing having
    landed, not the candidate being ready.
    """
    landed_but_building = _state(
        default_version="1.1.0",
        routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
        loaded=["1.0.0", "1.1.0"],
        ready_versions=["1.0.0"],  # the candidate is routed but NOT servable
    )
    manager, adapter = _manager(landed_but_building)

    resp = manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch"))

    assert adapter.apply_calls == [], (
        "routing already reflects the intent; a second write cannot make the "
        "candidate ready and only risks racing live inference"
    )
    assert resp.noop is True
    # Suppressing the write must not be mistaken for the swap having finished.
    assert manager.convergence(resp.applied).converged is False


def test_apply_threads_canary_weight_to_adapter():
    # Apply skips the routing write when live state already satisfies the intent,
    # so reusing post-apply state for both reads never calls the adapter.
    before = _state(
        default_version="1.0.0",
        routing=[RoutingEntry(version="1.0.0", role="default", weight=1.0)],
        loaded=["1.0.0", "1.1.0"],
        ready=True,
    )
    after = _state(
        default_version="1.0.0",
        routing=[
            RoutingEntry(version="1.0.0", role="default", weight=0.95),
            RoutingEntry(version="1.1.0", role="canary", weight=0.05),
        ],
        loaded=["1.0.0", "1.1.0"],
        ready=True,
    )
    manager, adapter = _manager([before, after])
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="canary", canary_weight=0.05)
    manager.apply(req)
    assert adapter.apply_calls[0]["canary_weight"] == pytest.approx(0.05)


def test_apply_succeeds_only_when_reflected_and_candidate_ready(monkeypatch):
    """Convergence needs the CANDIDATE servable, not just the routing shape.

    Reflected routing alone is not enough: if the version we rolled out is not
    actually READY in ``/v1/models``, the swap must not be called succeeded.
    """
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    manager, _ = _manager(
        _state(
            default_version="1.1.0",
            routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            loaded=["1.0.0", "1.1.0"],
            ready_versions=["1.0.0"],  # the candidate is staged but NOT servable
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)
    # Routing landed but the version cannot serve yet, and the reason is
    # non-terminal, so the caller polls rather than re-driving apply.
    assert manager.convergence(resp.applied).converged is False


def test_apply_succeeds_while_service_readiness_is_degraded():
    """A degraded service-wide /v1/ready must NOT block a landed swap.

    AOI Inference answers readiness 503 for reasons unrelated to the candidate
    (``engine_build_in_progress`` while it compiles). Gating convergence on it
    meant an already-applied swap never converged, so the caller re-drove
    ``apply`` — wasted recompiles, and a reload racing in-flight inference.
    Per-version state is the authority.
    """
    manager, _ = _manager(
        _state(
            default_version="1.1.0",
            routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            loaded=["1.1.0"],
            ready=False,  # service-wide flag is degraded ...
            ready_versions=["1.1.0"],  # ... but the candidate IS serving
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)
    assert manager.convergence(resp.applied).converged is True


def test_apply_does_not_fail_when_only_the_candidate_is_not_yet_servable(monkeypatch):
    """Routing applied + candidate still coming up is NOT a failure.

    A cold candidate compiles a TensorRT engine (~5 min) before it can serve, so
    the budget can expire with nothing actually wrong. Reporting ``failed`` there
    makes the caller re-drive ``apply``, and a model-level load reloads the
    model — every re-apply is another window for a reload to race in-flight
    inference. The reason is non-terminal, so the caller polls instead.
    """
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    manager, _ = _manager(
        _state(
            default_version="1.1.0",
            routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            loaded=["1.0.0", "1.1.0"],
            ready_versions=["1.0.0"],  # candidate staged, still building
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)

    report = manager.convergence(resp.applied)
    assert report.converged is False
    # Not terminal: the candidate is still coming up, so the caller's budget wins.
    assert report.reason.terminal is False


def test_apply_does_not_declare_failure_when_routing_has_not_reflected(monkeypatch):
    """Routing that has not landed is reported as unconverged, not as a failure."""
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    manager, _ = _manager(
        _state(
            default_version="1.0.0",  # never becomes 1.1.0
            routing=[RoutingEntry(version="1.0.0", role="default", weight=1.0)],
            loaded=["1.0.0"],
            ready_versions=["1.0.0"],
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)

    report = manager.convergence(resp.applied)
    assert report.converged is False
    # No ``readiness_timeout`` on purpose: the Model Manager reports what it
    # observes and the caller decides whether to keep waiting.
    assert report.reason.code == "default_version_mismatch"


def test_inline_readiness_budget_stays_short():
    """apply must NOT block for an engine build.

    Waiting for a cold candidate inline (~5 min of TensorRT compile) would hold
    the HTTP request open for the whole build, so any proxy/client timeout would
    surface as a re-applied swap. The slow path is left to the caller's
    ``convergence`` polls, bounded by their budget rather than a held connection.
    Asserted on the field defaults: conftest zeroes the interval so tests never
    sleep.
    """
    from src.config import Settings

    fields = Settings.model_fields
    budget_s = fields["readiness_max_checks"].default * fields["readiness_poll_interval"].default
    assert budget_s <= 60, f"inline readiness budget {budget_s}s would hold the request too long"


def test_apply_polls_the_whole_budget_before_declaring_failure(monkeypatch):
    """A routing change that has not reflected YET must not fail on check #1.

    Routing takes a moment to show up in the backend's state. Treating the first
    non-reflected read as terminal would bypass the poll budget entirely and fail
    a swap that was about to converge.
    """
    monkeypatch.setattr(settings, "readiness_max_checks", 3)
    not_yet = _state(default_version="1.0.0", routing=[], loaded=["1.0.0"])
    landed = _state(
        default_version="1.1.0",
        routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
        loaded=["1.0.0", "1.1.0"],
        ready_versions=["1.0.0", "1.1.0"],
    )

    manager, adapter = _manager([not_yet, not_yet, landed])
    resp = manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch"))

    assert manager.convergence(resp.applied).converged is True

    assert len(adapter.state_reads) >= 3


def test_convergence_reports_a_candidate_that_became_ready_after_apply(monkeypatch):
    """apply may return before the candidate is servable; convergence still lands it.

    The verdict is re-derived from live serving state on every poll, so an
    engine that finishes loading after apply returned is picked up by the next
    one. Nothing is recorded at apply time -- there is no stored phase to replay
    and nothing to reconcile -- which is what lets apply stay short without
    stranding the caller.
    """
    monkeypatch.setattr(settings, "readiness_max_checks", 1)
    # The candidate is not yet servable, so apply returns unlanded...
    building = _state(
        default_version="1.1.0",
        routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
        loaded=["1.0.0", "1.1.0"],
        ready_versions=["1.0.0"],
    )
    # ...and by the caller's poll the engine has finished loading.
    serving = _state(
        default_version="1.1.0",
        routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
        loaded=["1.0.0", "1.1.0"],
        ready_versions=["1.0.0", "1.1.0"],
    )
    # Apply reads twice (from_version, then the inline wait); the third is the
    # caller's convergence poll.
    manager, _ = _manager([building, building, serving])
    resp = manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch"))

    # Nothing is recorded: the answer is re-derived from live state every poll.
    assert manager.convergence(resp.applied).converged is True


def test_convergence_is_a_pure_read_that_can_be_repeated():
    """Polling twice gives the same answer and changes nothing.

    The read has no side effects, so any replica can answer it and a restart
    costs nothing.
    """
    manager, adapter = _manager(
        _state(
            default_version="1.1.0",
            routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            loaded=["1.1.0"],
            ready_versions=["1.1.0"],
        )
    )
    resp = manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch"))
    applies_after_apply = len(adapter.apply_calls)

    first = manager.convergence(resp.applied)
    second = manager.convergence(resp.applied)

    assert first.converged is True
    assert second.converged is True
    assert first.reason == second.reason
    assert (
        len(adapter.apply_calls) == applies_after_apply
    ), "a convergence poll must not issue a routing write"


def test_apply_stays_unconverged_when_the_loaded_lookup_is_unavailable(monkeypatch):
    """An unreadable loaded-set is "unknown", never proof the candidate serves."""
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    manager, _ = _manager(
        _state(
            default_version="1.1.0",
            routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            loaded=[],
            loaded_available=False,
            ready_versions=[],
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)
    report = manager.convergence(resp.applied)
    assert report.converged is False
    assert report.reason.code == "loaded_unreadable"


def test_apply_reports_a_reason_rather_than_a_readiness_timeout(monkeypatch):
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    # State never reflects the full_switch: the default stays 1.0.0.
    manager, _ = _manager(
        _state(
            default_version="1.0.0",
            routing=[RoutingEntry(version="1.0.0", role="default", weight=1.0)],
            loaded=["1.0.0"],
        )
    )
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch")
    resp = manager.apply(req)
    report = manager.convergence(resp.applied)
    assert report.converged is False
    # No ``readiness_timeout`` on purpose: the Model Manager reports what it
    # observes and the caller decides whether to keep waiting.
    assert report.reason.code == "default_version_mismatch"


# --------------------------------------------------------------------- cleanup
def test_cleanup_refuses_when_still_serving():
    manager, adapter = _manager(_state(serving_versions={"1.0.0", "1.1.0"}))
    req = CleanupRequest(model_name=NAME, version="1.1.0")
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(req)
    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert adapter.unload_calls == []


def test_the_reclaim_path_never_calls_unload():
    """The eviction this used to perform already happened at promotion time, and
    re-performing it on a backend that drops the row answers ``404
    MODEL_NOT_LOADED`` and reclaims nothing.
    """
    manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
    resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))
    assert adapter.unload_calls == []
    assert adapter.deleted == [(NAME, "1.4.0")]
    assert resp.status == "removed"


def test_only_the_reclaim_read_names_a_version():
    """An adapter may nominate an unpublished version only when the caller names
    one, so no other inventory read in this service can authorise a delete.
    ``unload`` reads one too, and must name nothing.
    """
    manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
    manager.unload(UnloadRequest(model_name=NAME, version="1.4.0"))
    manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", evicted_by_caller=True))
    assert adapter.inventory_reads == [(NAME, None, False), (NAME, "1.4.0", True)]


def test_cleanup_fails_closed_when_routing_unavailable():
    # A failed routing lookup makes serving_versions unknown, not empty, so the
    # reclaim refuses rather than delete a possibly-live version.
    manager, adapter = _manager(_state(serving_versions=set(), routing_available=False))
    req = CleanupRequest(model_name=NAME, version="1.4.0")
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(req)
    assert excinfo.value.code == "registry_unreachable"
    assert excinfo.value.http_status == 503
    assert excinfo.value.retryable is True
    assert adapter.unload_calls == []


def test_cleanup_reclaims_the_directory_the_backend_reports_deletable():
    """Every pull creates a version directory, and this is what reclaims one.

    The unload alone frees memory; without the delete the shared volume grows by
    one directory per promotion forever.
    """
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}),
        inventories=_inventory(
            versions=[{"version": "1.4.0", "on_disk": True, "deletable": True}],
            deletable=["1.4.0"],
        ),
    )
    resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert adapter.unload_calls == []
    assert adapter.deleted == [(NAME, "1.4.0")]
    assert resp.disk_reclaimed is True


def test_the_reclaim_makes_exactly_one_inventory_read():
    """One inventory read, which is not the same as one snapshot: ``cleanup`` also
    takes a destructive ``serving_state`` afterwards. Sharing one snapshot would
    make the guards staler than the permission they check, not tighter. What must
    not grow is the read that can nominate a version.
    """
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}),
        inventories=_inventory(
            versions=[{"version": "1.4.0", "on_disk": True}], deletable=["1.4.0"]
        ),
    )
    manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))
    assert adapter.inventory_reads == [(NAME, "1.4.0", False)]
    assert adapter.deleted == [(NAME, "1.4.0")]


def test_cleanup_leaves_the_directory_when_it_is_not_reported_deletable():
    """Only the backend's own answer authorises a delete. A reclaim evicts nothing,
    so skipping it strands no half-finished transaction.
    """
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}),
        inventories=_inventory(versions=[{"version": "1.4.0", "on_disk": True}], deletable=[]),
    )
    resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert adapter.unload_calls == []
    assert adapter.deleted == []
    assert resp.disk_reclaimed is False


def test_cleanup_fails_closed_when_the_inventory_is_unavailable():
    """``building``, ``routed_as``, ``streams`` and ``loaded`` all read ``row is
    not None and ...``, so an unreadable inventory is indistinguishable from four
    clean bills of health.
    """
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}), inventories=_inventory(available=False)
    )
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert excinfo.value.code == "registry_unreachable"
    assert excinfo.value.http_status == 503
    assert excinfo.value.retryable is True
    assert adapter.unload_calls == []
    assert adapter.deleted == []


def test_cleanup_refuses_a_version_whose_build_is_still_in_flight():
    """``building`` is the window routing state cannot show: the version is not in
    the loaded index yet, so it looks idle while a conversion is writing into it."""
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}),
        inventories=_inventory(
            versions=[{"version": "1.4.0", "on_disk": True, "building": True}], deletable=[]
        ),
    )
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert excinfo.value.retryable is True
    assert adapter.unload_calls == []
    assert adapter.deleted == []


def test_cleanup_refuses_a_version_the_backend_still_routes_to():
    """The second window routing state can miss: a loaded shadow/canary target that
    is not the primary."""
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}),
        inventories=_inventory(
            versions=[{"version": "1.4.0", "on_disk": True, "loaded": True, "routed_as": "shadow"}],
            deletable=[],
        ),
    )
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert excinfo.value.code == "conflict"
    assert "shadow" in excinfo.value.message
    assert adapter.unload_calls == []
    assert adapter.deleted == []


def test_an_unreadable_inventory_is_refused_not_reported_as_nothing_to_do():
    """Nothing happens before the delete any more, so an unreadable inventory can
    raise instead of returning False -- which a sweep would read as "processed".
    """
    manager, adapter = _manager(
        _state(serving_versions={"1.0.0"}), inventories=_inventory(available=False)
    )
    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert excinfo.value.code == "registry_unreachable"
    assert excinfo.value.retryable is True
    assert adapter.deleted == []


# ------------------------------------------------- cleanup: the reclaim roots
class TestTheReclaimRootsTheEvictionNeverCovered:
    """A backend's ``409 MODEL_IN_USE`` is about the version it was asked to
    unload: it says nothing about another version reading out of that version's
    directory, and its 2xx for an idle one is silent about attached streams.
    """

    def test_a_version_aliased_into_another_directory_is_never_reclaimed(self):
        """Observed live: ITS@4 came up ``ready`` reading out of ITS/1/. A load whose
        ``config_file`` points into another version's directory is accepted, so
        the paths a live version serves from can belong to one that is idle,
        unrouted and published deletable.
        """
        manager, adapter = _manager(
            _state(serving_versions={"4"}),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {
                        "version": "4",
                        "on_disk": True,
                        "loaded": True,
                        "artifact_paths": (
                            "/models/rtvi/ITS/1/serving/nvinferserver.pbtxt",
                            "/models/rtvi/ITS/1/model.engine",
                        ),
                    },
                ],
                deletable=["1"],
                name="ITS",
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name="ITS", version="1"))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.http_status == 409
        # Not retryable: an aliased instance does not resolve itself.
        assert excinfo.value.retryable is False
        assert "4" in excinfo.value.message
        assert adapter.deleted == []

    def test_a_version_serving_only_its_own_directory_is_still_reclaimable(self):
        """The control: v4 reads out of its own directory, so v1 must still be
        reclaimable or the refusal above is just the guard existing.
        """
        manager, adapter = _manager(
            _state(serving_versions={"4"}),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {
                        "version": "4",
                        "on_disk": True,
                        "loaded": True,
                        "artifact_paths": ("/models/rtvi/ITS/4/model.engine",),
                    },
                ],
                deletable=["1"],
                name="ITS",
            ),
        )

        assert manager.cleanup(CleanupRequest(model_name="ITS", version="1")).disk_reclaimed
        assert adapter.deleted == [("ITS", "1")]

    def test_the_aliasing_check_is_by_path_component_not_by_substring(self):
        """``ITS/1`` must not match ``ITS/11``. DFW mints v1, v10, v11 in sequence,
        so a substring test would refuse most reclaims forever.
        """
        manager, adapter = _manager(
            _state(serving_versions={"11"}),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {
                        "version": "11",
                        "on_disk": True,
                        "loaded": True,
                        "artifact_paths": ("/models/rtvi/ITS/11/model.engine",),
                    },
                ],
                deletable=["1"],
                name="ITS",
            ),
        )

        assert manager.cleanup(CleanupRequest(model_name="ITS", version="1")).disk_reclaimed
        assert adapter.deleted == [("ITS", "1")]

    def test_an_evicted_neighbour_that_merely_names_the_path_does_not_hold_it(self):
        """Nothing rewrites an evicted row's ``config``/``engine``, so the paths it
        last loaded against stay published forever. Refusing on any row naming
        the directory would have no exit. The root turns on the neighbour being
        loaded, not on the path matching.
        """
        manager, adapter = _manager(
            _state(serving_versions=set()),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {
                        "version": "4",
                        "on_disk": True,
                        "loaded": False,
                        "artifact_paths": ("/models/rtvi/ITS/1/model.engine",),
                    },
                ],
                deletable=["1"],
                name="ITS",
            ),
        )

        assert manager.cleanup(CleanupRequest(model_name="ITS", version="1")).disk_reclaimed
        assert adapter.deleted == [("ITS", "1")]

    def test_a_version_with_attached_streams_is_refused(self):
        """A stream outlives the routing entry that attached it, so its version is
        invisible to every routing guard above this one. Measured live: the
        refcount reaches 1 on the right version, follows a per-stream pin as it
        moves, and drops to 0 on detach.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}),
            inventories=_inventory(
                versions=[{"version": "1", "on_disk": True, "streams": 1}], deletable=["1"]
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.http_status == 409
        # Named: every root here answers ``conflict``, so only the reason
        # separates this from residency or aliasing.
        assert "stream(s) still attached" in excinfo.value.message
        # Retryable: streams detach on their own, and the next sweep finds zero.
        assert excinfo.value.retryable is True
        assert adapter.deleted == []

    def test_the_eviction_verb_is_refused_too_while_streams_are_attached(self):
        """The refcount belongs to the shared stack, not to cleanup's roots: unload
        takes the weights out of the memory a stream is reading. Refusing costs a
        leaked GPU slot -- the promotion controller logs a failed baseline unload
        without failing the promotion -- and not refusing costs a live stream.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}),
            inventories=_inventory(
                versions=[{"version": "1", "on_disk": True, "loaded": True, "streams": 1}]
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.unload(UnloadRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        assert "stream(s) still attached" in excinfo.value.message
        assert excinfo.value.retryable is True
        assert adapter.unload_calls == []
        # An eviction touches no files even when it refuses.
        assert adapter.deleted == []

    def test_a_backend_that_publishes_no_refcount_does_not_lose_its_reclaim(self):
        """AOI publishes ``streams`` nowhere, so reading absence as "unknown,
        refuse" would disable its sweep entirely. Zero and absent both permit.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {"version": "9", "on_disk": True, "streams": 0},
                ],
                deletable=["1"],
            ),
        )

        assert manager.cleanup(CleanupRequest(model_name=NAME, version="1")).disk_reclaimed


class TestResidencyIsARootOfItsOwn:
    """The unload inside the old ``cleanup`` answered "is this still in memory?".
    ``infer_deletable``'s residency check is on the inference path only, so a
    backend publishing its own list reaches the ``rmtree`` with nothing asking.
    """

    def test_a_resident_version_is_refused_however_the_backend_published_it(self):
        """The published list says yes and the loaded-model index says it is in
        memory. The backend's answer is not the last word on that one.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}, loaded=["1", "2"], resident_versions=["1", "2"]),
            inventories=_inventory(versions=[{"version": "1", "on_disk": True}], deletable=["1"]),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        # Named: every root here answers ``conflict``, and this must be residency.
        assert "resident in serving memory" in excinfo.value.message
        assert excinfo.value.retryable is True
        assert adapter.deleted == []

    def test_a_row_that_says_loaded_refuses_even_when_the_index_forgot_it(self):
        """``resident_versions`` and ``row.loaded`` come from different endpoints on
        both shipped backends, and either may be the one that knows.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}),
            inventories=_inventory(
                versions=[{"version": "1", "on_disk": True, "loaded": True}], deletable=["1"]
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert "resident in serving memory" in excinfo.value.message
        assert adapter.deleted == []

    def test_an_unreadable_loaded_set_is_refused_rather_than_read_as_empty(self):
        """On the AOI path the loaded-model lookup is a separate probe from routing
        and fails on its own, leaving ``resident_versions`` empty.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}, loaded_available=False),
            inventories=_inventory(versions=[{"version": "1", "on_disk": True}], deletable=["1"]),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "registry_unreachable"
        assert excinfo.value.http_status == 503
        assert excinfo.value.retryable is True
        assert "no longer resident" in excinfo.value.message
        assert adapter.deleted == []

    def test_the_unload_verb_is_not_gated_on_residency(self):
        """Evicting is what unload is for. Put this root in the shared stack and
        every unload in the service refuses itself.
        """
        manager, adapter = _manager(
            _state(serving_versions={"2"}, loaded=["1", "2"], resident_versions=["1", "2"])
        )

        manager.unload(UnloadRequest(model_name=NAME, version="1"))

        assert adapter.unload_calls == [(NAME, "1")]


class TestTheGuardStackRunsOnTheDestructiveRead:
    """Readable-is-not-complete is applied only when asked, and it used to be
    asked only by the evidence feeding the deletable inference -- which a
    publishing backend never consumes. Both destructive verbs ask for it now; the
    lenient reading is for a swap, which writes routing and deletes nothing.
    """

    def test_cleanup_scores_its_guards_against_a_destructive_read(self):
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))
        assert adapter.destructive_state_reads == [True]

    def test_the_eviction_verb_is_scored_against_the_destructive_read_too(self):
        """``unload`` judged its routing guard against the lenient reading, so a
        ``200`` stating no policy read as "nothing is routed" and the guard passed
        over what may be the live default. Asserted on the read because the
        lenient answer is a PASS and no outcome assertion can see it.
        """
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
        manager.unload(UnloadRequest(model_name=NAME, version="1.4.0"))
        assert adapter.destructive_state_reads == [True]


class TestTheCallerAssertionIsNarrow:
    """``evicted_by_caller`` is the one term in the delete decision that is not a
    read, so it must be unable to clear anything else: a restored-from-backup
    Mongo can assert an eviction that never happened.
    """

    def test_the_caller_assertion_cannot_clear_a_routing_guard(self):
        """The version is the live default and the caller swears it was evicted. The
        guard stack runs on ``serving_state``, which the assertion cannot reach.
        """
        manager, adapter = _manager(_state(serving_versions={"1.4.0"}))

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(
                CleanupRequest(model_name=NAME, version="1.4.0", evicted_by_caller=True)
            )

        assert excinfo.value.code == "conflict"
        # Named: all three refusals are ``conflict``; this must be the routing one.
        assert "still serving as default/canary/shadow" in excinfo.value.message
        assert adapter.deleted == []

    def test_the_caller_assertion_cannot_clear_a_root(self):
        """An asserted eviction says nothing about a live aliaser. ``loaded`` on v4
        matters: the root refuses for live neighbours only, because an evicted
        row keeps publishing the paths it last read.
        """
        manager, adapter = _manager(
            _state(serving_versions=set()),
            inventories=_inventory(
                versions=[
                    {"version": "1", "on_disk": True},
                    {
                        "version": "4",
                        "on_disk": True,
                        "loaded": True,
                        "artifact_paths": ("/m/ITS/1/model.engine",),
                    },
                ],
                deletable=["1"],
                name="ITS",
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name="ITS", version="1", evicted_by_caller=True))

        assert excinfo.value.code == "conflict"
        assert "version 4 is serving out of this directory" in excinfo.value.message
        assert adapter.deleted == []

    def test_the_assertion_travels_to_the_adapter_only_on_the_reclaim_read(self):
        """An argument to one read, not a field on the manager -- where it goes is
        the whole of its blast radius.
        """
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", evicted_by_caller=True))
        assert adapter.inventory_reads == [(NAME, "1.4.0", True)]


# ------------------------------------------------------------ cleanup: G0 age
class TestTheAgeFloorRunsFirst:
    """G0 -- the age floor. The backend cannot see the hazard it covers: a
    directory being written into right now is published deletable within seconds
    of its ``mkdir``. So it runs before the reads, and these tests say so.
    """

    def test_a_directory_younger_than_the_floor_is_refused_before_any_read(self, monkeypatch):
        """``inventory_reads == []`` is what pins the placement: moved below the
        reads the directory still survives, so no outcome assertion would notice.
        """
        monkeypatch.setattr(activation_mod, "version_age_seconds", lambda name, version: 60.0)
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", min_age_s=3600))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.http_status == 409
        # Not retryable: no point burning a worker slot re-asking a clock.
        assert excinfo.value.retryable is False
        assert adapter.deleted == []
        # Not even the read that proves no build is in flight.
        assert adapter.inventory_reads == []
        assert adapter.state_reads == []

    def test_an_unknowable_directory_age_is_refused(self, monkeypatch):
        """``None`` means "I could not find out", which is not "old enough". Not the
        missing-directory case -- that answers ``DIRECTORY_ABSENT``.
        """
        monkeypatch.setattr(activation_mod, "version_age_seconds", lambda name, version: None)
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", min_age_s=3600))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.http_status == 409
        assert excinfo.value.retryable is False
        assert adapter.unload_calls == []
        assert adapter.deleted == []

    def test_the_deployment_floor_outranks_a_smaller_request(self, monkeypatch):
        """The floor is ``max(request, setting)``, so a caller cannot lower it."""
        monkeypatch.setattr(activation_mod, "version_age_seconds", lambda name, version: 60.0)
        monkeypatch.setattr(settings, "reclaim_min_age_s", 3600.0)
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", min_age_s=0.0))

        assert excinfo.value.code == "conflict"
        assert adapter.unload_calls == []

    def test_the_default_floor_is_inert_and_never_reads_the_directory_age(self, monkeypatch):
        """With both defaults the floor is inert, and the absence of the call is the
        point: reading the age and comparing ``age < 0.0`` leaves every outcome
        identical while putting a whole-tree walk on every caller's reclaim.
        Scoped to the age -- ``version_directory_absent`` stats regardless.
        """
        calls: list[tuple[str, str]] = []

        def _spy(name, version):
            calls.append((name, version))
            return 0.0

        monkeypatch.setattr(activation_mod, "version_age_seconds", _spy)
        assert settings.reclaim_min_age_s == 0.0, "the shipped default is no floor"
        assert CleanupRequest(model_name=NAME, version="1.4.0").min_age_s is None

        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))
        resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert calls == [], "the age was read even though no floor was in force"
        assert adapter.deleted == [(NAME, "1.4.0")]
        assert resp.disk_reclaimed is True

    def test_an_absent_directory_is_not_refused_by_the_floor(self, tmp_path, monkeypatch):
        """The real ``version_age_seconds`` runs here, so a missing directory arrives
        as ``DIRECTORY_ABSENT`` and not the ``None`` the test above refuses. Live
        case: the sweep reclaims, loses the write retiring its own nomination,
        re-offers the version, and a 409 there refuses it forever.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / NAME).mkdir(parents=True)
        monkeypatch.setattr(settings, "reclaim_min_age_s", 86400.0)
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert resp.disk_reclaimed is True
        # Let through: an early return satisfies "no 409" while skipping the
        # refusal stack.
        assert adapter.inventory_reads == [(NAME, "1.4.0", False)]
        assert adapter.state_reads == [NAME]
        # Reported from the goal state, not from an rmtree.
        assert adapter.deleted == []

    def test_an_absence_a_pull_has_since_claimed_is_still_refused(self, tmp_path, monkeypatch):
        """The race the floor used to mask: absent when the age was read, claimed by
        a pull by the time the reclaim reaches the marker. The interlock reads
        later than the floor and closer to the ``rmtree``, so it still refuses.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / NAME / "1.4.0"
        version_dir.mkdir(parents=True)
        (version_dir / activation_mod.STAGING_LOCK_FILENAME).write_text("{}")
        monkeypatch.setattr(settings, "reclaim_min_age_s", 86400.0)
        # Absent at the floor's read; the marker lands before the interlock's.
        monkeypatch.setattr(
            activation_mod,
            "version_age_seconds",
            lambda name, version: activation_mod.DIRECTORY_ABSENT,
        )
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert excinfo.value.code == "conflict"
        assert "a pull is staging into its directory" in excinfo.value.message
        assert adapter.deleted == []

    def test_the_age_floor_does_not_apply_to_the_unload_verb(self, monkeypatch):
        """In ``_evict`` this would put the promotion path's baseline eviction behind
        a clock, so a full switch would stop freeing GPU wherever a floor is set.
        """
        monkeypatch.setattr(settings, "reclaim_min_age_s", 86400.0)
        monkeypatch.setattr(
            activation_mod,
            "version_age_seconds",
            lambda name, version: pytest.fail("unload must not consult the directory age"),
        )
        manager, adapter = _manager(_state(serving_versions={"1.0.0"}))

        resp = manager.unload(UnloadRequest(model_name=NAME, version="1.4.0"))

        assert adapter.unload_calls == [(NAME, "1.4.0")]
        assert resp.version == "1.4.0"


def _repo(tmp_path, monkeypatch, *versions):
    """Point the manager at a real model repository holding ``versions``. Cleanup
    proves a directory absent before reporting a reclaim already done, and the
    shipped default ``/models`` does not exist under test.
    """
    repo = tmp_path / "repo"
    (repo / NAME).mkdir(parents=True)
    for version in versions:
        (repo / NAME / version).mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(repo))
    return repo


# ------------------------------------------------ cleanup: the already-gone answer
class TestAnAlreadyGoneDirectoryIsReclaimed:
    """The goal state is "the directory is not there", and here it already holds.

    Measured live on both backends: a repeat of a successful reclaim answered
    ``200 {"disk_reclaimed": false}`` and logged "kept ITS@5 on disk" about a
    directory ``ls`` proved gone, so the sweep re-offered it every tick forever.
    ``deletable=[]`` models a backend refusing an absent directory on its own.
    """

    def test_a_proven_absence_reports_the_reclaim_done(self, tmp_path, monkeypatch, caplog):
        """``deleted == []`` separates this from a delete; the log assertion separates
        it from the refusal it used to be reported as.
        """
        _repo(tmp_path, monkeypatch)
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=[]),
        )

        with caplog.at_level(logging.INFO):
            resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert resp.disk_reclaimed is True
        assert resp.status == "removed"
        assert adapter.deleted == []
        assert "its directory is already gone" in caplog.text
        assert "kept" not in caplog.text

    def test_a_vanished_directory_that_is_still_routed_is_still_refused(
        self, tmp_path, monkeypatch
    ):
        """The absence is answered below the guards. A directory that vanished while
        routing still names it is alarming, and answering absence first would
        report a live version's missing weights as a tidy ``200 removed``.
        """
        _repo(tmp_path, monkeypatch)
        manager, adapter = _manager(
            _state(serving_versions={"1.4.0"}, default_version="1.4.0"),
            inventories=_inventory(deletable=[]),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert excinfo.value.code == "conflict"
        assert "still serving as default/canary/shadow" in excinfo.value.message
        assert adapter.deleted == []

    def test_a_dry_run_reports_the_absence_without_claiming_the_reclaim(
        self, tmp_path, monkeypatch, caplog
    ):
        """``status`` observes; ``disk_reclaimed`` authorises. A true is what makes DFW
        retire the nomination, and the sweep ships ``dry_run=True``, so reporting
        the postcondition there would have the first dry tick writing Mongo.
        """
        _repo(tmp_path, monkeypatch)
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=[]),
        )

        with caplog.at_level(logging.INFO):
            resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", dry_run=True))

        assert resp.status == "removed"
        assert resp.disk_reclaimed is False
        assert adapter.deleted == []
        assert "dry run" in caplog.text
        assert "no directory on disk" in caplog.text

    def test_an_unmounted_repository_is_not_an_absence(self, tmp_path, monkeypatch, caplog):
        """At the version directory an unmounted volume and a swept repo both stat
        ``FileNotFoundError``, so proving absence off that alone would report
        every version in the deployment reclaimed.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "not-mounted"))
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=[]),
        )

        with caplog.at_level(logging.INFO):
            resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert resp.disk_reclaimed is False
        assert resp.status == "kept"
        assert adapter.deleted == []
        assert "not a directory" in caplog.text


# ------------------------------------------------------ cleanup: response status
class TestTheStatusSaysWhatHappened:
    """``status`` was a ``Literal["removed"]`` constant, so a dry run that deleted
    nothing and a refusal that reclaimed nothing both answered "removed". It now
    carries the postcondition -- is the directory there when this returns --
    while ``disk_reclaimed`` says whether the caller may stop offering the
    version.
    """

    def test_a_reclaim_reports_removed(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch, "1.4.0")
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=["1.4.0"]),
        )

        resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert adapter.deleted == [(NAME, "1.4.0")]
        assert resp.model_dump() == {
            "version": "1.4.0",
            "status": "removed",
            "disk_reclaimed": True,
        }

    def test_a_directory_left_in_place_reports_kept(self, tmp_path, monkeypatch):
        """The refusal that is a 200, which constant "removed" made unreadable."""
        _repo(tmp_path, monkeypatch, "1.4.0")
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=[]),
        )

        resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert adapter.deleted == []
        assert resp.model_dump() == {
            "version": "1.4.0",
            "status": "kept",
            "disk_reclaimed": False,
        }

    def test_a_dry_run_over_a_directory_that_is_there_reports_kept(self, tmp_path, monkeypatch):
        """The directory survives the call by request, so "removed" would be false."""
        _repo(tmp_path, monkeypatch, "1.4.0")
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(deletable=["1.4.0"]),
        )

        resp = manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0", dry_run=True))

        assert adapter.deleted == []
        assert resp.model_dump() == {
            "version": "1.4.0",
            "status": "kept",
            "disk_reclaimed": False,
        }


# ------------------------------------------------------- unload response shape
def test_the_unload_response_carries_no_disk_reclaimed():
    """Absent, not a constant ``false``: that would be indistinguishable from
    cleanup's degraded outcome, which is the only signal an operator has for a
    volume that will not drain. Both assertions are needed -- ``hasattr`` alone
    misses a field excluded from the dump, and the dump alone misses ``exclude``.
    """
    manager, _ = _manager(_state(serving_versions={"1.0.0"}))
    resp = manager.unload(UnloadRequest(model_name=NAME, version="1.4.0"))

    assert "disk_reclaimed" not in resp.model_dump()
    assert not hasattr(resp, "disk_reclaimed")
    assert resp.model_dump() == {"version": "1.4.0", "status": "removed"}


def test_cleanup_and_unload_share_one_guard_stack(monkeypatch):
    """A sentinel raised from ``_refuse_if_in_use`` must surface from both verbs,
    which is what stops the two refusal stacks drifting apart.
    """

    class _SentinelError(Exception):
        pass

    def _boom(self, name, version, state, inventory):
        raise _SentinelError(f"{name}@{version}")

    monkeypatch.setattr(ModelManager, "_refuse_if_in_use", _boom)
    manager, _ = _manager(_state(serving_versions={"1.0.0"}))

    with pytest.raises(_SentinelError):
        manager.unload(UnloadRequest(model_name=NAME, version="1.4.0"))
    with pytest.raises(_SentinelError):
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))


# ------------------------------------------------------------ apply asymmetry
class TestApplyUnloadIsDeliberatelyUnguarded:
    """``apply {control: "unload"}`` runs none of the cleanup refusal stack; its
    only protections are ``unified_allow_unload`` and the backend's own ``409
    MODEL_IN_USE``. Adding the guards is a frozen-contract behaviour change, so
    do not "fix" the asymmetry by editing these tests -- change the design first.
    """

    def test_apply_unload_bypasses_every_guard_cleanup_would_apply(self, monkeypatch):
        """The version is in ``serving_versions`` (G2) and ``routed_as="shadow"``
        (G5), and ``apply`` still issues the control. Empty ``inventory_reads``
        proves the stack was never entered: both guarded verbs read it always.
        """
        monkeypatch.setattr(settings, "readiness_max_checks", 1)
        routed = _inventory(
            versions=[{"version": "1.1.0", "on_disk": True, "loaded": True, "routed_as": "shadow"}],
            deletable=[],
        )
        manager, adapter = _manager(
            _state(
                default_version="1.0.0",
                serving_versions={"1.0.0", "1.1.0"},
                loaded=["1.0.0", "1.1.0"],
                resident_versions=["1.0.0", "1.1.0"],
            ),
            inventories=routed,
        )

        with pytest.raises(ModelManagerError):
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.1.0"))
        assert adapter.inventory_reads != [], "cleanup did not reach the guard stack"
        adapter.inventory_reads.clear()

        manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="unload"))

        assert [call["control"] for call in adapter.apply_calls] == ["unload"]
        assert adapter.apply_calls[0]["candidate"] == "1.1.0"
        assert adapter.inventory_reads == [], (
            "apply entered the cleanup guard stack; that is a behaviour change, "
            "not a refactor -- see the class docstring"
        )
        assert adapter.deleted == [], "apply must never reclaim disk"

    def test_the_routing_controls_make_no_inventory_read_either(self, monkeypatch):
        """The hot path stays cheap: no extra status read plus disk listing."""
        monkeypatch.setattr(settings, "readiness_max_checks", 1)
        manager, adapter = _manager(
            _state(
                default_version="1.1.0",
                loaded=["1.0.0", "1.1.0"],
                ready_versions=["1.0.0", "1.1.0"],
                routing=[RoutingEntry(version="1.1.0", role="default", weight=1.0)],
            )
        )
        manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="full_switch"))
        assert adapter.inventory_reads == []


# --------------------------------------------------------------- model_name
def test_apply_persists_model_name_in_record():
    manager, _ = _manager(_state(default_version="1.0.0"))
    req = ApplyRequest(model_name="other-model", version="1.1.0", control="full_switch")
    resp = manager.apply(req)
    assert resp.applied.model_name == "other-model"


def test_convergence_queries_the_model_named_by_the_expectation():
    """The expectation carries the model name, so the poll needs no lookup.

    That is what lets any replica answer a poll for an apply it never saw.
    """
    manager, adapter = _manager(_state(default_version="1.0.0"))
    req = ApplyRequest(model_name="other-model", version="1.1.0", control="shadow")
    resp = manager.apply(req)
    adapter.state_reads.clear()
    manager.convergence(resp.applied)
    assert adapter.state_reads == ["other-model"]


def test_apply_canary_wrong_weight_never_converges(monkeypatch):
    # Requested 5%, reported 50%: never converges.
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    observed = _state(
        default_version="1.0.0",
        routing=[
            RoutingEntry(version="1.0.0", role="default", weight=0.5),
            RoutingEntry(version="1.1.0", role="canary", weight=0.5),
        ],
        loaded=["1.0.0", "1.1.0"],
        ready=True,
    )
    manager, _ = _manager(observed)
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="canary", canary_weight=0.05)
    resp = manager.apply(req)
    report = manager.convergence(resp.applied)
    assert report.converged is False
    assert report.reason.code == "canary_weight_mismatch"


# ---------------------------------------------------- generic convergence check
def test_control_reflected_full_switch():
    reflected = _state(
        default_version="2", routing=[RoutingEntry(version="2", role="default", weight=1.0)]
    )
    assert _control_reflected("full_switch", "2", reflected) is True
    # A canary entry means routing is not all-default.
    mixed = _state(
        default_version="2",
        routing=[
            RoutingEntry(version="2", role="default", weight=0.9),
            RoutingEntry(version="3", role="canary", weight=0.1),
        ],
    )
    assert _control_reflected("full_switch", "2", mixed) is False


def test_control_reflected_shadow_and_canary():
    shadow = _state(
        default_version="1",
        routing=[
            RoutingEntry(version="1", role="default", weight=1.0),
            RoutingEntry(version="2", role="shadow", weight=0.0),
        ],
    )
    assert _control_reflected("shadow", "2", shadow, from_version="1") is True
    assert _control_reflected("shadow", "9", shadow, from_version="1") is False
    # Shadow against the wrong primary must NOT be treated as converged.
    assert _control_reflected("shadow", "2", shadow, from_version="9") is False

    canary = _state(
        default_version="1",
        routing=[
            RoutingEntry(version="1", role="default", weight=0.9),
            RoutingEntry(version="2", role="canary", weight=0.1),
        ],
    )
    assert _control_reflected("canary", "2", canary, from_version="1", canary_weight=0.1) is True
    # A different canary weight (requested 5%, observed 10%) is not converged.
    assert _control_reflected("canary", "2", canary, from_version="1", canary_weight=0.05) is False

    assert _control_reflected("canary", "2", canary, from_version="9", canary_weight=0.1) is False


def test_control_reflected_unload():
    assert _control_reflected("unload", "2", _state(loaded=["1"])) is True
    assert _control_reflected("unload", "2", _state(loaded=["1", "2"])) is False
    # Unknown loaded set (list_models failed) must not count as "gone".
    assert _control_reflected("unload", "2", _state(loaded=[], loaded_available=False)) is False


def test_unload_converges_once_the_version_is_evicted_not_once_it_is_deleted():
    """An unloaded version keeps its directory, so it stays in the known set.

    Judging the unload against that set would wait for something that never
    happens — the caller would poll out its whole budget and fail a promotion whose
    unload worked. Eviction from memory is the observable event.
    """
    evicted = _state(loaded=["1", "2"], resident_versions=["1"], ready_versions=["1"])
    assert _control_reflected("unload", "2", evicted) is True

    still_resident = _state(loaded=["1", "2"], resident_versions=["1", "2"], ready_versions=["1"])
    assert _control_reflected("unload", "2", still_resident) is False


def test_apply_unload_succeeds_when_the_version_is_evicted_but_still_on_disk():
    """The end-to-end version of the above, through a real swap."""
    manager, _ = _manager(
        _state(loaded=["1.0.0", "1.1.0"], resident_versions=["1.0.0"], ready_versions=["1.0.0"])
    )
    resp = manager.apply(ApplyRequest(model_name=NAME, version="1.1.0", control="unload"))
    assert manager.convergence(resp.applied).converged is True


def test_apply_unload_does_not_succeed_when_loaded_set_unknown(monkeypatch):
    # list_models blipped while /v1/ready is 200: an unconfirmed unload is not
    # a succeeded swap.
    monkeypatch.setattr(settings, "readiness_max_checks", 2)
    manager, _ = _manager(_state(loaded=[], loaded_available=False, ready=True))
    req = ApplyRequest(model_name=NAME, version="1.1.0", control="unload")
    resp = manager.apply(req)
    report = manager.convergence(resp.applied)
    assert report.converged is False
    assert report.reason.code == "loaded_unreadable"


def test_control_reflected_unknown_control_is_false():
    assert _control_reflected("bogus", "2", _state()) is False


class TestUnreadableRoutingIsNotConverged:
    """Convergence must not be satisfied by a routing view nobody could read."""

    @pytest.mark.parametrize("control", ["full_switch", "shadow", "canary"])
    def test_the_three_routing_controls_fail_closed(self, control):
        state = _state(default_version="2", routing_available=False, ready_versions=["2"])
        assert not _control_reflected(control, "2", state, from_version="1")

    def test_full_switch_is_not_satisfied_by_an_empty_entry_list(self):
        """``all([])`` is True, so this is the shape that would report false success."""
        state = _state(default_version="2", routing=[], routing_available=False)
        assert not _control_reflected("full_switch", "2", state)

    def test_unload_is_exempt_because_it_is_judged_on_residency(self):
        """Requiring a readable routing view here would strand every eviction."""
        state = _state(routing_available=False, resident_versions=["3"])
        assert _control_reflected("unload", "2", state)


class TestTheStagingInterlockIsAReclaimRoot:
    """A directory a pull is staging into may not be reclaimed, whoever nominates
    it -- deft publishes one deletable within seconds of its ``mkdir``, and
    nothing else in the conjunction refuses it. The age floor infers a running
    pull from a side effect; ``.staging.lock`` is the writer saying so.
    """

    def test_a_directory_being_staged_survives_a_sweep(self, monkeypatch):
        """The backend reports the version deletable here, so with the interlock
        dropped every other guard passes and the rmtree runs on a live pull.
        """
        monkeypatch.setattr(activation_mod, "staging_in_progress", lambda name, version: True)
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(
                versions=[{"version": "1.4.0", "on_disk": True, "deletable": True}],
                deletable=["1.4.0"],
            ),
        )

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.http_status == 409
        # Not retryable: the download has minutes to run and the next tick re-asks.
        assert excinfo.value.retryable is False
        assert adapter.deleted == []

    def test_a_published_directory_is_not_held_by_a_stale_predicate(self, monkeypatch):
        """The control, so the refusal above is a property of the marker rather than
        of the guard existing.
        """
        monkeypatch.setattr(activation_mod, "staging_in_progress", lambda name, version: False)
        manager, adapter = _manager(
            _state(serving_versions={"1.0.0"}),
            inventories=_inventory(
                versions=[{"version": "1.4.0", "on_disk": True, "deletable": True}],
                deletable=["1.4.0"],
            ),
        )

        assert manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0")).disk_reclaimed
        assert adapter.deleted == [(NAME, "1.4.0")]


class TestTheRootsAreReadPerVersionNotPerTick:
    """TOCTOU. A tick nominates a page of versions and issues them one at a time
    with retry sleeps between, so a snapshot from the start of the tick is
    minutes stale by the last ``rmtree``. Nothing caches today, which is why this
    is pinned: memoising the inventory would not look like a safety change.
    """

    def test_roots_are_re_read_before_each_delete(self, monkeypatch):
        """Two reclaims in one tick; v2 comes back into memory after v1 is gone, so
        the first must reclaim and the second must refuse. Residency is the probe
        rather than the stream refcount because it sits behind the staging
        marker, so both reads below actually happen.
        """
        staging_reads: list[tuple[str, str]] = []

        def _record(name: str, version: str) -> bool:
            staging_reads.append((name, version))
            return False

        monkeypatch.setattr(activation_mod, "staging_in_progress", _record)

        at_tick_start = _inventory(
            versions=[{"version": "1", "on_disk": True}, {"version": "2", "on_disk": True}],
            deletable=["1", "2"],
            name="ITS",
        )
        # v1 gone, v2 re-loaded: only visible to a caller that reads again.
        after_the_first_delete = _inventory(
            versions=[{"version": "2", "on_disk": True, "loaded": True}],
            deletable=["2"],
            name="ITS",
        )
        manager, adapter = _manager(
            _state(serving_versions=set()),
            inventories=[at_tick_start, after_the_first_delete],
        )

        assert manager.cleanup(CleanupRequest(model_name="ITS", version="1")).disk_reclaimed

        with pytest.raises(ModelManagerError) as excinfo:
            manager.cleanup(CleanupRequest(model_name="ITS", version="2"))

        # The tick-start snapshot shows v2 idle and deletable, so a stale read
        # fails here by deleting.
        assert excinfo.value.code == "conflict"
        assert "resident in serving memory" in excinfo.value.message
        assert adapter.deleted == [("ITS", "1")]
        # Both halves: a per-tick snapshot of the local marker is the same defect,
        # invisible in the outcome above.
        assert adapter.inventory_reads == [("ITS", "1", False), ("ITS", "2", False)]
        assert staging_reads == [("ITS", "1"), ("ITS", "2")]
