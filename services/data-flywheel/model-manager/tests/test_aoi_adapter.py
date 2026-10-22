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

"""Unit tests for the AOI-specific serving adapter.

These cover the client-specific mechanics that moved out of ModelManager:
verb->AOI mapping (``apply_control``), the AOI routing/registry ->
``ServingState`` composition, and the adapter registry wiring.
"""

import logging
from unittest.mock import MagicMock

import pytest

from src.adapters.aoi.activation import ExplicitActivation
from src.adapters.aoi.adapter import AOIServingAdapter
from src.adapters.registry import get_adapter
from src.api.schemas import CleanupRequest
from src.config import settings
from src.core.errors import ModelManagerError
from src.core.manager import ModelManager

NAME = settings.model_name


def _single(version):
    return {
        "mode": "single",
        "primary_model": {"name": NAME, "version": version},
        "updated_at": "t",
    }


def _shadow(primary, shadow):
    return {
        "mode": "shadow",
        "primary_model": {"name": NAME, "version": primary},
        "shadow_model": {"name": NAME, "version": shadow},
        "updated_at": "t",
    }


def _canary(primary, canary, percent):
    return {
        "mode": "canary",
        "primary_model": {"name": NAME, "version": primary},
        "canary_model": {"name": NAME, "version": canary},
        "canary_percent": percent,
        "updated_at": "t",
    }


def _make_adapter(routing, loaded_versions=None, ready=True):
    activation = MagicMock()
    activation.stage_and_load.return_value = "/models/x/1"
    aoi_client = MagicMock()
    aoi_client.get_routing.return_value = routing
    aoi_client.put_routing.side_effect = lambda cfg: {**cfg, "updated_at": "t"}
    inference = MagicMock()
    inference.ready.return_value = ready
    inference.list_models.return_value = [
        {"name": NAME, "version": v, "status": "loaded"} for v in (loaded_versions or [])
    ]
    adapter = AOIServingAdapter(
        activation=activation,
        aoi_client=aoi_client,
        aoi_inference=inference,
    )
    return adapter, activation, aoi_client, inference


# ------------------------------------------------------------------- stage
def test_stage_delegates_to_activation():
    adapter, activation, _, _ = _make_adapter(_single("1.0.0"))
    repo_path = adapter.stage(NAME, "11", "file:///a", "abc", spec_uri="file:///s")
    activation.stage_and_load.assert_called_once_with(
        NAME,
        "11",
        "file:///a",
        "abc",
        spec_uri="file:///s",
        threshold=None,
        idempotency_key=None,
    )
    assert repo_path == "/models/x/1"


def test_stage_threads_threshold_to_activation():
    adapter, activation, _, _ = _make_adapter(_single("1.0.0"))
    adapter.stage(NAME, "11", "file:///a", "abc", spec_uri="file:///s", threshold=0.7)
    activation.stage_and_load.assert_called_once_with(
        NAME,
        "11",
        "file:///a",
        "abc",
        spec_uri="file:///s",
        threshold=0.7,
        idempotency_key=None,
    )


def test_stage_threads_idempotency_key_to_activation():
    """The key has to reach the load, or a retry starts a second conversion."""
    adapter, activation, _, _ = _make_adapter(_single("1.0.0"))
    adapter.stage(NAME, "11", "file:///a", "abc", idempotency_key="promo-7:pull")
    assert activation.stage_and_load.call_args.kwargs["idempotency_key"] == "promo-7:pull"


# ----------------------------------------------------------- apply mapping
def test_apply_control_shadow_maps_to_put_routing_shadow():
    adapter, _, aoi_client, _ = _make_adapter(_shadow("1.0.0", "1.1.0"))
    adapter.apply_control(
        control="shadow", name=NAME, candidate="1.1.0", from_version="1.0.0", canary_weight=None
    )
    body = aoi_client.put_routing.call_args.args[0]
    assert body["mode"] == "shadow"

    assert body["primary_model"] == {"model_name": NAME, "model_version": "1.0.0"}
    assert body["shadow_model"] == {"model_name": NAME, "model_version": "1.1.0"}


def test_apply_control_shadow_falls_back_to_candidate_when_no_from_version():
    adapter, _, aoi_client, _ = _make_adapter({})
    adapter.apply_control(
        control="shadow", name=NAME, candidate="1.1.0", from_version=None, canary_weight=None
    )
    body = aoi_client.put_routing.call_args.args[0]
    assert body["primary_model"] == {"model_name": NAME, "model_version": "1.1.0"}


def test_apply_control_threads_idempotency_key_into_the_routing_body():
    adapter, _, aoi_client, _ = _make_adapter(_single("1.1.0"))
    adapter.apply_control(
        control="full_switch",
        name=NAME,
        candidate="1.1.0",
        from_version="1.0.0",
        canary_weight=None,
        idempotency_key="promo-7:full",
    )
    assert aoi_client.put_routing.call_args.args[0]["idempotency_key"] == "promo-7:full"


def test_apply_control_canary_maps_weight_to_percent():
    adapter, _, aoi_client, _ = _make_adapter(_canary("1.0.0", "1.1.0", 5))
    adapter.apply_control(
        control="canary", name=NAME, candidate="1.1.0", from_version="1.0.0", canary_weight=0.05
    )
    body = aoi_client.put_routing.call_args.args[0]
    assert body["mode"] == "canary"
    assert body["canary_percent"] == pytest.approx(5.0)


def test_apply_control_full_switch_maps_to_single():
    adapter, _, aoi_client, _ = _make_adapter(_single("1.1.0"))
    adapter.apply_control(
        control="full_switch",
        name=NAME,
        candidate="1.1.0",
        from_version="1.0.0",
        canary_weight=None,
    )
    body = aoi_client.put_routing.call_args.args[0]
    assert body == {
        "mode": "single",
        "primary_model": {"model_name": NAME, "model_version": "1.1.0"},
    }


@pytest.mark.parametrize("weight", [None, 0, 0.0, 1, 1.0, -0.1, 1.5])
def test_apply_control_canary_rejects_weights_with_no_wire_representation(weight):
    """``canary_percent`` is exclusive 0..100, so the endpoints cannot be sent.

    A missing weight is the case that used to hurt: it collapsed to 0% and
    produced a PUT the backend was always going to reject, with nothing in the
    error to say the weight was the problem.
    """
    adapter, _, aoi_client, _ = _make_adapter(_single("1.0.0"))
    with pytest.raises(ModelManagerError) as excinfo:
        adapter.apply_control(
            control="canary",
            name=NAME,
            candidate="1.1.0",
            from_version="1.0.0",
            canary_weight=weight,
        )
    assert excinfo.value.code == "bad_request"
    assert excinfo.value.http_status == 400
    aoi_client.put_routing.assert_not_called()


def test_apply_control_unload_calls_inference_unload():
    adapter, _, aoi_client, inference = _make_adapter(_single("1.0.0"))
    adapter.apply_control(
        control="unload", name=NAME, candidate="1.1.0", from_version="1.0.0", canary_weight=None
    )
    inference.unload.assert_called_once_with(NAME, "1.1.0", idempotency_key=None)
    aoi_client.put_routing.assert_not_called()


# ------------------------------------------------------ serving_state compose
def test_ready_versions_splits_ready_from_unavailable():
    """``ready_versions`` narrows to servable; ``loaded_versions`` must not.

    This is the real /v1/models payload after a full switch: the retired
    baseline stays in the repo as UNAVAILABLE. It must remain in
    ``loaded_versions`` (DFW mints its next version above that set, so dropping
    it yields a colliding floor) while ``ready_versions`` reports only what is
    actually serving.
    """
    adapter, _, _, inference = _make_adapter(_single("2"))
    inference.list_models.return_value = [
        {"name": NAME, "version": "1", "state": "UNAVAILABLE"},
        {"name": NAME, "version": "2", "state": "READY"},
    ]
    state = adapter.serving_state(NAME)
    assert state.loaded_versions == ["1", "2"]
    assert state.ready_versions == ["2"]


def test_ready_versions_defaults_to_ready_when_state_absent():
    """An older payload listing only loaded models (no ``state``) stays servable."""
    adapter, _, _, inference = _make_adapter(_single("1.0.0"))
    inference.list_models.return_value = [
        {"name": NAME, "version": "1.0.0", "status": "loaded"},
    ]
    state = adapter.serving_state(NAME)
    assert state.ready_versions == ["1.0.0"]


def test_known_versions_includes_a_directory_the_backend_no_longer_lists(monkeypatch, tmp_path):
    """``GET /v1/models`` lists only resident versions, so an evicted one keeps its
    directory but has no row -- and DFW, minting above this floor, would mint
    onto it. Widening only: ``ready`` and ``resident`` must not pick a bare
    directory up or an unload never converges.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
    for version in ("1", "2", "7"):
        (tmp_path / NAME / version).mkdir(parents=True)

    adapter, _, _, _ = _make_adapter(
        {"mode": "single", "primary_model": {"model_name": NAME, "model_version": "1"}},
        loaded_versions=["1", "2"],
    )
    state = adapter.serving_state(NAME)

    assert sorted(state.loaded_versions) == ["1", "2", "7"]
    assert "7" not in state.ready_versions, "a bare directory is not servable"
    assert "7" not in state.resident_versions, "a bare directory is not resident"


def test_an_unreadable_backend_list_is_not_widened_into_a_floor(monkeypatch, tmp_path):
    """A failed read must stay "unknown", not become "whatever is on disk" --
    ``loaded_available`` False is what destructive and minting callers fail on.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
    (tmp_path / NAME / "9").mkdir(parents=True)

    adapter, _, _, inference = _make_adapter(
        {"mode": "single", "primary_model": {"model_name": NAME, "model_version": "1"}}
    )
    inference.list_models.side_effect = ModelManagerError(
        code="registry_unreachable", message="down", retryable=True, http_status=503
    )
    state = adapter.serving_state(NAME)

    assert state.loaded_available is False
    assert state.loaded_versions == []


def test_serving_state_full_switch_composition():
    adapter, _, _, _ = _make_adapter(_single("1.1.0"), loaded_versions=["1.1.0"])
    state = adapter.serving_state(NAME)
    assert state.default_version == "1.1.0"
    assert [(e.version, e.role, e.weight) for e in state.routing] == [("1.1.0", "default", 1.0)]
    assert state.loaded_versions == ["1.1.0"]
    assert state.ready is True
    assert state.routing_available is True


def test_serving_state_canary_composition():
    adapter, _, _, _ = _make_adapter(
        _canary("1.0.0", "1.1.0", 5), loaded_versions=["1.0.0", "1.1.0"]
    )
    state = adapter.serving_state(NAME)
    assert state.default_version == "1.0.0"
    entries = [(e.version, e.role, e.weight) for e in state.routing]
    assert entries == [("1.0.0", "default", 0.95), ("1.1.0", "canary", 0.05)]
    assert state.serving_versions == {"1.0.0", "1.1.0"}


def test_serving_state_shadow_composition():
    adapter, _, _, _ = _make_adapter(_shadow("1.0.0", "1.1.0"), loaded_versions=["1.0.0", "1.1.0"])
    state = adapter.serving_state(NAME)
    assert state.default_version == "1.0.0"
    entries = [(e.version, e.role, e.weight) for e in state.routing]
    assert entries == [("1.0.0", "default", 1.0), ("1.1.0", "shadow", 0.0)]


def test_serving_state_unload_composition():
    adapter, _, _, _ = _make_adapter(_single("1.0.0"), loaded_versions=["1.0.0"])
    state = adapter.serving_state(NAME)
    assert state.default_version == "1.0.0"
    assert [(e.version, e.role, e.weight) for e in state.routing] == [("1.0.0", "default", 1.0)]
    assert "1.1.0" not in state.loaded_versions


def test_serving_state_empty_routing_is_normalized():
    adapter, _, _, _ = _make_adapter({}, loaded_versions=[])
    state = adapter.serving_state(NAME)
    assert state.default_version is None
    assert state.routing == []
    assert state.serving_versions == set()


# -------------------------------------------------------- helper error paths
def test_a_routing_state_naming_no_primary_is_unknown_on_a_destructive_read():
    """``GET /v1/model-routing`` answers 200 for an object naming no primary, which
    leaves ``serving_versions`` empty with ``routing_available`` True -- every
    routing guard in ``cleanup`` then passes over a possibly-live version. The
    swap path keeps the lenient reading so a first rollout can happen at all.
    """
    adapter, _, _, _ = _make_adapter({})

    assert adapter.serving_state(NAME).routing_available is True, "the swap path is unchanged"
    assert adapter.serving_state(NAME, destructive=True).routing_available is False


def test_serving_state_tolerates_unavailable_routing():
    adapter, _, aoi_client, _ = _make_adapter(_single("1.0.0"))
    aoi_client.get_routing.side_effect = ModelManagerError(
        code="internal", message="down", retryable=True, http_status=502
    )
    state = adapter.serving_state(NAME)
    assert state.default_version is None
    assert state.serving_versions == set()
    # Signalled so destructive callers can fail closed.
    assert state.routing_available is False


def test_serving_state_tolerates_unavailable_list_models():
    adapter, _, _, inference = _make_adapter(_single("1.0.0"))
    inference.list_models.side_effect = ModelManagerError(
        code="internal", message="down", retryable=True, http_status=502
    )
    state = adapter.serving_state(NAME)
    assert state.loaded_versions == []
    # Signalled so unload convergence can fail closed.
    assert state.loaded_available is False


def test_unload_delegates_to_inference():
    adapter, _, _, inference = _make_adapter(_single("1.0.0"))
    adapter.unload(NAME, "1.4.0")
    inference.unload.assert_called_once_with(NAME, "1.4.0")


# ------------------------------------------------- canonical routing ref keys
def _canonical_single(version):
    return {
        "mode": "single",
        "primary_model": {"model_name": NAME, "model_version": version},
        "updated_at": "t",
    }


def test_routing_read_accepts_canonical_ref_keys():
    """The canonical ref is ``{model_name, model_version}``, and reads must see it.

    A reader that understood only the legacy ``version`` key resolves every ref to
    None against a canonical response. That is not a cosmetic gap: an empty routing
    view reads as "nothing is serving", so a swap waits on a convergence it can
    never observe and cleanup believes a live version is idle.
    """
    adapter, _, _, _ = _make_adapter(_canonical_single("2"), loaded_versions=["2"])
    state = adapter.serving_state(NAME)

    assert state.default_version == "2"
    assert state.serving_versions == {"2"}
    assert [(e.version, e.role, e.weight) for e in state.routing] == [("2", "default", 1.0)]


def test_routing_read_accepts_canonical_shadow_and_canary_refs():
    shadow = {
        "mode": "shadow",
        "primary_model": {"model_name": NAME, "model_version": "1"},
        "shadow_model": {"model_name": NAME, "model_version": "2"},
    }
    adapter, _, _, _ = _make_adapter(shadow, loaded_versions=["1", "2"])
    state = adapter.serving_state(NAME)
    assert [(e.version, e.role) for e in state.routing] == [("1", "default"), ("2", "shadow")]
    assert state.serving_versions == {"1", "2"}

    canary = {
        "mode": "canary",
        "primary_model": {"model_name": NAME, "model_version": "1"},
        "canary_model": {"model_name": NAME, "model_version": "2"},
        "canary_percent": 25,
    }
    adapter, _, _, _ = _make_adapter(canary, loaded_versions=["1", "2"])
    state = adapter.serving_state(NAME)
    assert [(e.version, e.role, e.weight) for e in state.routing] == [
        ("1", "default", 0.75),
        ("2", "canary", 0.25),
    ]
    assert state.serving_versions == {"1", "2"}


def test_routing_read_still_accepts_the_legacy_ref_keys():
    """Legacy ``{name, version}`` refs stay readable, so a deployment that has not
    moved to the canonical spelling keeps working."""
    adapter, _, _, _ = _make_adapter(_shadow("1.0.0", "1.1.0"), loaded_versions=["1.0.0", "1.1.0"])
    state = adapter.serving_state(NAME)
    assert state.default_version == "1.0.0"
    assert state.serving_versions == {"1.0.0", "1.1.0"}


# -------------------------------------------------------- resident vs known set
def test_resident_versions_excludes_evicted_while_the_floor_keeps_it():
    """An UNAVAILABLE version has been evicted from memory but still owns a dir.

    Both facts are needed at once and by different callers: an unload is only
    observably complete once the version stops being resident, while the version
    floor DFW mints above must keep counting the directory or it hands back a
    colliding number.
    """
    adapter, _, _, inference = _make_adapter(_single("2"))
    inference.list_models.return_value = [
        {"name": NAME, "version": "1", "state": "UNAVAILABLE"},
        {"name": NAME, "version": "2", "state": "READY"},
    ]
    state = adapter.serving_state(NAME)

    assert state.loaded_versions == ["1", "2"], "the version floor must keep evicted versions"
    assert state.resident_versions == ["2"]
    assert state.ready_versions == ["2"]


def test_a_version_mid_load_is_resident_but_not_ready():
    """A transitional state is still held in memory, so an unload is NOT yet done —
    but it cannot serve either, so a swap must not converge on it."""
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_models.return_value = [
        {"name": NAME, "version": "2", "state": "LOADING"},
    ]
    state = adapter.serving_state(NAME)

    assert state.resident_versions == ["2"]
    assert state.ready_versions == []


def test_resident_versions_defaults_to_resident_when_state_absent():
    """An older payload that lists only loaded models omits ``state`` entirely."""
    adapter, _, _, inference = _make_adapter(_single("1.0.0"))
    inference.list_models.return_value = [{"name": NAME, "version": "1.0.0", "status": "loaded"}]
    assert adapter.serving_state(NAME).resident_versions == ["1.0.0"]


# ------------------------------------------------------------------- inventory
INVENTORY_PAYLOAD = {
    "model_name": NAME,
    "routing_mode": "shadow",
    "versions": [
        {
            "version": "1",
            "on_disk": True,
            "loaded": True,
            "ready": True,
            "state": "READY",
            "routed_as": "primary",
            "building": False,
            "deletable": False,
        },
        {
            "version": "2",
            "on_disk": True,
            "loaded": True,
            "ready": True,
            "state": "READY",
            "routed_as": "shadow",
            "building": False,
            "deletable": False,
        },
        {
            "version": "3",
            "on_disk": True,
            "loaded": False,
            "ready": False,
            "state": None,
            "routed_as": None,
            "building": False,
            "deletable": True,
        },
    ],
    "deletable": ["3"],
}


def test_inventory_parses_rows_and_takes_deletable_from_the_top_level_list():
    """``deletable`` is read verbatim, never recomputed from the rows.

    That list is the backend's single delete authority, and re-deriving it here
    would reintroduce exactly the races it exists to close.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = INVENTORY_PAYLOAD
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert inventory.deletable == ["3"]
    assert inventory.routing_mode == "shadow"
    assert [v.version for v in inventory.versions] == ["1", "2", "3"]

    primary = inventory.version("1")
    assert (primary.routed_as, primary.loaded, primary.ready, primary.deletable) == (
        "primary",
        True,
        True,
        False,
    )
    shadow = inventory.version("2")
    assert shadow.routed_as == "shadow"
    spare = inventory.version("3")
    assert (spare.on_disk, spare.loaded, spare.routed_as, spare.deletable) == (
        True,
        False,
        None,
        True,
    )
    assert inventory.version("nope") is None


def test_inventory_reports_unavailable_instead_of_raising():
    """The caller that needs this is about to delete something, so it must be able
    to tell "nothing is deletable" from "the backend did not answer"."""
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.side_effect = ModelManagerError(
        code="internal", message="down", retryable=True, http_status=502
    )
    inventory = adapter.inventory(NAME)

    assert inventory.available is False
    assert inventory.deletable == []
    assert inventory.versions == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"versions": [], "deletable": []},
        {"versions": [{"no_version": True}, "junk"], "deletable": ["3", 7, None]},
    ],
)
def test_inventory_stays_available_for_absent_fields_and_unusable_rows(payload):
    """An iterable array is a usable answer, even where individual rows are not.

    Absent fields are legitimate too — an older or partial payload omits them —
    so neither case is grounds for declaring the whole inventory unreadable. The
    companion test below covers the case that *is*; the two look alike and must
    not be merged.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = payload
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert inventory.versions == []
    assert all(isinstance(v, str) for v in inventory.deletable)


def test_inventory_coerces_numeric_deletable_entries_to_version_strings():
    """The list has to speak the same vocabulary as the rows beside it.

    ``_version_state`` coerces with ``str(row["version"])``, so a payload numbering
    its versions yields rows keyed ``"3"``. Leaving the deletable list holding ``3``
    would make the containment test every caller performs never match, and reclaim
    would stop with every individual signal looking correct.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        "versions": [{"version": 3, "on_disk": True, "deletable": True}],
        "deletable": [3],
    }
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert inventory.deletable == ["3"]
    assert inventory.version("3") is not None


def test_inventory_keeps_the_row_for_a_numeric_zero_version():
    """Version ``0`` is a servable target, and a falsy one, so it needs saying.

    ``_usable_deletable`` coerces a numeric ``0`` into ``"0"``, so a truthiness test
    on the row's version authorises deleting a version whose row does not exist —
    and ``cleanup``'s ``building`` and ``routed_as`` guards only refuse when there is
    a row to read, so both would be skipped for a version
    ``_reject_unservable_target`` accepts as perfectly valid.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        "versions": [{"version": 0, "on_disk": True, "building": True}],
        "deletable": [0],
    }
    inventory = adapter.inventory(NAME)

    assert inventory.deletable == ["0"]
    row = inventory.version("0")
    assert row is not None
    assert row.building is True


def test_inventory_keeps_an_empty_version_row_where_it_can_do_no_harm():
    """The filter is allowed to be wider than ``_usable_deletable``, never narrower.

    ``""`` survives that list as a string, so dropping the matching row here would
    rebuild the same asymmetry version ``0`` suffers from. Keeping it costs nothing:
    rows are only ever read by a lookup keyed on a caller's version, and every path
    that supplies one — the ``PathComponent`` schema, ``_reject_unservable_target``,
    ``_version_dir`` — refuses an empty string, so nothing can name this row.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        "versions": [{"version": "", "on_disk": True}],
        "deletable": [""],
    }
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert [v.version for v in inventory.versions] == [""]


def test_inventory_logs_the_deletable_entries_it_cannot_use(caplog):
    """Dropping them silently is what made contract drift undiagnosable.

    An all-numeric list used to collapse to an empty one on an inventory still
    reporting ``available=True``, which is indistinguishable from an honest
    "nothing is deletable" — so reclaim stopped with nothing anywhere saying why.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        # Rows for the usable entries, pinning the element-level coercion rather
        # than the row intersection.
        "versions": [{"version": "3"}],
        "deletable": ["3", None, {"version": "4"}, True],
    }
    with caplog.at_level(logging.WARNING):
        inventory = adapter.inventory(NAME)

    # The usable entry still authorises its own deletion; the rest are reported.
    assert inventory.deletable == ["3"]
    assert inventory.available is True
    assert "not usable as a version" in caplog.text


def test_inventory_keeps_the_usable_half_of_a_partly_junk_deletable_list():
    """A partially valid list must not be escalated to "unavailable".

    That is the element-level convention this module documents: one unusable entry
    says nothing about the rest, and refusing the whole list would block reclaim of
    directories the backend has explicitly cleared. The envelope-level rule is the
    opposite and is pinned separately.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        "versions": [{"version": "3"}, {"version": "4"}],
        "deletable": [None, "3", "4"],
    }
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert inventory.deletable == ["3", "4"]


def test_a_deletable_entry_with_no_row_is_not_passed_through():
    """``cleanup``'s ``building``, ``routed_as`` and ``loaded`` guards all read
    ``row is not None and ...``, so a deletable with no row clears all of them.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {
        "versions": [{"version": "3"}],
        "deletable": ["3", "9"],
    }
    inventory = adapter.inventory(NAME)

    assert inventory.available is True
    assert inventory.deletable == ["3"], "a version with no row cleared the row-shaped guards"
    assert inventory.version("9") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"versions": None, "deletable": []},
        {"versions": [], "deletable": None},
        {"versions": None, "deletable": None},
        {"versions": "not-a-list", "deletable": []},
        {"versions": [], "deletable": "not-a-list"},
        {"versions": {}, "deletable": []},
        {"versions": [], "deletable": 7},
    ],
)
def test_inventory_reports_unavailable_when_an_array_field_is_not_an_array(payload):
    """A present-but-unusable array is no answer, and must not read as "empty".

    This is the guard behind ``cleanup``: its pre-unload checks are phrased as "if
    the row says building/routed, refuse", so a version with no row passes them.
    Reading a malformed envelope as an empty inventory strips every version of its
    row and would clear those checks for the one version that is mid-build or
    serving live traffic.

    ``null`` is called out separately from ``"not-a-list"`` because it is the case
    ``dict.get(key, [])`` silently mishandles: the default applies only to an
    absent key, so a present ``null`` comes back as ``None``.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = payload
    inventory = adapter.inventory(NAME)

    assert inventory.available is False
    assert inventory.deletable == [], "a malformed envelope must authorise no deletions"
    assert inventory.versions == []


@pytest.mark.parametrize("versions_field", [None, "not-a-list"])
def test_a_malformed_inventory_stops_cleanup_before_it_unloads_anything(versions_field):
    """The reason the unavailable/empty choice matters, proven end to end.

    Read as an empty inventory, every version loses its row and ``cleanup``'s
    "refuse if building / refuse if routed" checks pass by default — so a live or
    mid-build version would be evicted on the strength of an unparseable payload.
    Driven through the real adapter and the real ``ModelManager`` because the
    hazard is in how the two compose, which neither one alone would show.
    """
    adapter, _, _, inference = _make_adapter(_single("1.0.0"))
    inference.list_versions.return_value = {
        "versions": versions_field,
        "deletable": ["1.4.0"],
    }
    manager = ModelManager(adapter=adapter)

    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="1.4.0"))

    assert excinfo.value.code == "registry_unreachable"
    assert excinfo.value.http_status == 503
    assert excinfo.value.retryable is True
    inference.unload.assert_not_called()
    adapter.activation.delete.assert_not_called()


@pytest.mark.parametrize(
    "row",
    [
        {"version": 0, "on_disk": True, "building": True},
        {"version": 0, "on_disk": True, "loaded": True, "routed_as": "shadow"},
    ],
)
def test_a_deletable_entry_keeps_its_guards_for_a_numerically_zero_version(row):
    """A ``deletable`` entry with no row is a delete with two guards skipped.

    ``deletable`` is authoritative and ``cleanup`` reads the row only to *refuse*, so
    the two hazards routing state cannot show — a conversion writing into the version
    directory, and a loaded shadow/canary target — are checked as "if there is a row
    that says so". Drop the row and the version sails past both. Driven through the
    real adapter and the real ``ModelManager`` because that composition is the whole
    hazard: each layer is defensible alone.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_versions.return_value = {"versions": [row], "deletable": [0]}
    manager = ModelManager(adapter=adapter)

    with pytest.raises(ModelManagerError) as excinfo:
        manager.cleanup(CleanupRequest(model_name=NAME, version="0"))

    assert excinfo.value.code == "conflict"
    inference.unload.assert_not_called()
    adapter.activation.delete.assert_not_called()


@pytest.mark.parametrize(
    "version",
    [
        "v2",  # the plausible-looking one: a valid directory name Triton cannot serve
        "1.4.0",  # semver, which this service's own schema layer accepts
        "latest",
        "-1",
        "1 ",
        "1_2",
        "²",  # str.isdigit() is True for this, so the server would accept it
        "٢",
    ],
)
def test_stage_refuses_a_version_the_backend_cannot_serve(version):
    """Refused locally instead of after a multi-hundred-megabyte download.

    ``²`` and ``٢`` are the deliberate divergence: ``str.isdigit()`` is True for
    both, so the server accepts them, but nothing downstream can — ``int()`` refuses
    them and Triton needs an integer-named directory.
    """
    adapter, activation, _, _ = _make_adapter(_single("1"))
    with pytest.raises(ModelManagerError) as excinfo:
        adapter.stage(NAME, version, "file:///a", "abc")

    assert excinfo.value.code == "bad_request"
    assert excinfo.value.retryable is False
    assert excinfo.value.http_status == 400
    assert "non-negative integer" in excinfo.value.message
    activation.stage_and_load.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "-leading-hyphen",
        ".leading-dot",
        "_leading-underscore",
        "has space",
        "has/slash",
        "has:colon",
        "",
        "x" * 129,  # the server bounds the name at 128 characters
    ],
)
def test_stage_refuses_a_model_name_the_backend_cannot_serve(name):
    adapter, activation, _, _ = _make_adapter(_single("1"))
    with pytest.raises(ModelManagerError) as excinfo:
        adapter.stage(name, "1", "file:///a", "abc")

    assert excinfo.value.code == "bad_request"
    assert excinfo.value.retryable is False
    assert "model_name" in excinfo.value.message
    activation.stage_and_load.assert_not_called()


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("aoi-secondary-inspection", "1"),
        ("changenet_classify", "0"),
        ("model.v2", "42"),
        ("A1", "007"),
        ("x" * 128, "12345"),
    ],
)
def test_stage_accepts_what_the_backend_accepts(name, version):
    """The guard must not reject anything DFW actually mints.

    DFW's serving version comes from an integer counter, and model names are the
    hyphenated/underscored/dotted identifiers already in use.
    """
    adapter, activation, _, _ = _make_adapter(_single("1"))
    adapter.stage(name, version, "file:///a", "abc")
    activation.stage_and_load.assert_called_once()


def test_stage_rejects_before_creating_a_directory_or_downloading_anything(tmp_path, monkeypatch):
    """The whole point of the check is *when* it happens.

    Driven through the real ``ExplicitActivation`` and a real repo path, because a
    mocked activation cannot show that no directory was created. The download is
    monkeypatched to fail loudly rather than mocked out, so an ordering regression
    surfaces as this test failing rather than as a silent extra fetch.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(repo_root))

    def _no_downloads(*args, **kwargs):
        raise AssertionError("a download was attempted for a version the backend rejects")

    monkeypatch.setattr(ExplicitActivation, "_download", _no_downloads)
    adapter = AOIServingAdapter(
        activation=ExplicitActivation(inference=MagicMock()),
        aoi_client=MagicMock(),
        aoi_inference=MagicMock(),
    )

    with pytest.raises(ModelManagerError) as excinfo:
        adapter.stage(NAME, "v2", "file:///a", "abc")

    assert excinfo.value.code == "bad_request"
    assert list(repo_root.iterdir()) == [], "a directory was created for a rejected version"


def test_the_backend_check_does_not_replace_the_path_containment_guard(tmp_path, monkeypatch):
    """Two different checks, and the security one must survive independently.

    A traversal value never reaches the adapter (the schema rejects it first), so the
    containment guard is only reachable by calling the activation layer directly —
    which is precisely the bypass it exists to cover. Asserted here so that folding
    the two checks together cannot look harmless.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(repo_root))
    activation = ExplicitActivation(inference=MagicMock())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.delete(NAME, "../../escape")

    assert excinfo.value.code == "bad_request"
    assert "model repository" in excinfo.value.message


def test_serving_state_skips_malformed_models_rows_instead_of_raising():
    """Only the response envelope is validated upstream, so rows can be anything.

    ``serving_state`` is what ``apply``, ``status`` and ``cleanup`` all read, so a
    single bad row that raised would turn every one of them into a 500. Skipping
    it degrades to the same "version not reported" path an absent row takes.
    """
    adapter, _, _, inference = _make_adapter(_single("2"))
    inference.list_models.return_value = [
        "not-a-dict",
        None,
        42,
        ["nested"],
        {"no_name": True},
        {"name": NAME},
        {"name": NAME, "version": None},
        {"name": NAME, "version": 7},
        {"name": NAME, "version": "2", "state": "READY"},
    ]
    state = adapter.serving_state(NAME)

    assert state.loaded_available is True, "malformed rows must not read as 'lookup failed'"
    assert state.loaded_versions == ["2"]
    assert state.ready_versions == ["2"]
    assert state.resident_versions == ["2"]


@pytest.mark.parametrize("models", [None, "not-a-list", {}, 7])
def test_serving_state_reports_unavailable_when_models_is_not_a_list(models):
    """The collection not being a list is unknown, not empty.

    Distinct from a list containing junk rows (below), which stays available. The
    guarantee is enforced in ``list_models`` itself, so this pins the adapter's own
    check — ``inference`` is injected, and the guarantee has to hold for whatever
    is passed in, not just for the one implementation that promises it.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_models.return_value = models
    state = adapter.serving_state(NAME)

    assert state.loaded_available is False
    assert state.loaded_versions == []
    assert state.ready_versions == []
    assert state.resident_versions == []


def test_a_models_payload_that_is_entirely_malformed_reports_empty_not_unavailable():
    """Still 'available': the backend answered, it just reported nothing usable.

    Conflating that with an unreachable lookup would make cleanup fail closed
    forever on a backend that is up and simply has no versions.
    """
    adapter, _, _, inference = _make_adapter(_single("1"))
    inference.list_models.return_value = ["junk", None]
    state = adapter.serving_state(NAME)

    assert state.loaded_available is True
    assert state.loaded_versions == []


def test_delete_version_delegates_to_activation():
    adapter, activation, _, _ = _make_adapter(_single("1"))
    adapter.delete_version(NAME, "3")
    activation.delete.assert_called_once_with(NAME, "3")


# ---------------------------------------------------------------- registry
def test_registry_returns_aoi_adapter():
    assert isinstance(get_adapter("aoi"), AOIServingAdapter)


def test_registry_unknown_client_raises_internal():
    with pytest.raises(ModelManagerError) as excinfo:
        get_adapter("nope")
    assert excinfo.value.code == "internal"
    assert excinfo.value.http_status == 500
