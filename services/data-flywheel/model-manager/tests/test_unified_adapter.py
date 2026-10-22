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

"""The six ``ServingAdapter`` methods against a unified backend."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.registry import get_adapter
from src.adapters.unified.adapter import UnifiedServingAdapter
from src.adapters.unified.client import UnifiedModelClient
from src.api.schemas import CleanupRequest, UnloadRequest
from src.config import settings
from src.core.errors import ModelManagerError
from src.core.manager import ModelManager

NAME = "changenet_classify"


def _make_adapter(status=None, routing_endpoint=None):
    client = MagicMock()
    client.get_status.return_value = status if status is not None else {}
    # None means "no routing view in the response". A DELETE-inference test must
    # override it or it passes on unreadable routing, not on its named guard.
    client.get_routing.return_value = routing_endpoint
    # The real client returns ``(status_code, body)`` from unload.
    client.unload.return_value = (200, {"state": "unloaded"})
    activation = MagicMock()
    activation.stage_and_load.return_value = f"/models/{NAME}/2"
    adapter = UnifiedServingAdapter(client=client, activation=activation)
    return adapter, client, activation


_ROUTING_ENDPOINT = {"default": {"model": {"name": NAME, "version": "1"}}, "routes": []}


def _status(versions=(), routing=None):
    return {"routing": routing or {}, "models": [{"name": NAME, "versions": list(versions)}]}


class TestStage:
    def test_delegates_to_activation(self):
        adapter, _, activation = _make_adapter()
        assert adapter.stage(NAME, "2", "file:///a", "abc", threshold=0.5) == f"/models/{NAME}/2"
        assert activation.stage_and_load.call_args.args == (NAME, "2", "file:///a", "abc")
        assert activation.stage_and_load.call_args.kwargs["threshold"] == 0.5

    def test_rejects_a_semver_version_before_spending_the_download(self):
        """The spec advertises semver for AOI; the implementation rejects it.

        Catching it here turns a mid-promotion 400 into a boundary rejection,
        before a multi-hundred-megabyte download has been made.
        """
        adapter, _, activation = _make_adapter()
        with pytest.raises(ModelManagerError) as excinfo:
            adapter.stage(NAME, "1.0.0", "file:///a", "abc")
        activation.stage_and_load.assert_not_called()
        assert excinfo.value.code == "bad_request"

    def test_rejects_an_unusable_model_name(self):
        adapter, _, activation = _make_adapter()
        with pytest.raises(ModelManagerError):
            adapter.stage("../etc", "2", "file:///a", "abc")
        activation.stage_and_load.assert_not_called()


class TestApplyControl:
    def test_shadow_body(self):
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="shadow", name=NAME, candidate="2", from_version="1", canary_weight=None
        )
        assert client.put_routing.call_args.args[0] == {
            "model": {"name": NAME, "version": "1"},
            "shadow": {"name": NAME, "version": "2"},
        }

    def test_canary_body_converts_the_fraction_to_a_percent(self):
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="canary", name=NAME, candidate="2", from_version="1", canary_weight=0.05
        )
        body = client.put_routing.call_args.args[0]
        assert body["canary"] == {"name": NAME, "version": "2"}
        assert body["canary_percent"] == pytest.approx(5.0)

    def test_full_switch_sends_only_the_primary(self):
        """The routing write replaces the whole default policy.

        Naming only the new primary is what clears a live shadow or canary, and
        convergence requires exactly that: after a full switch every routing
        entry must be ``default``.
        """
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="full_switch", name=NAME, candidate="2", from_version="1", canary_weight=None
        )
        assert client.put_routing.call_args.args[0] == {"model": {"name": NAME, "version": "2"}}

    @pytest.mark.parametrize("weight", [None, 0, 1, 1.5])
    def test_degenerate_canary_weights_are_refused(self, weight):
        adapter, client, _ = _make_adapter()
        with pytest.raises(ModelManagerError):
            adapter.apply_control(
                control="canary", name=NAME, candidate="2", from_version="1", canary_weight=weight
            )
        client.put_routing.assert_not_called()

    @pytest.mark.parametrize(("control", "weight"), [("shadow", None), ("canary", 0.05)])
    def test_partial_traffic_refuses_to_guess_the_primary(self, control, weight):
        """``from_version=None`` must not fall back to the candidate.

        ``ModelManager.apply`` derives from_version from the current default,
        which is None after any failed status read. Falling back to the candidate
        emits ``{model: v2, shadow: v2}`` — the untested candidate takes 100% of
        traffic — and convergence re-derives the same guess, so the swap is
        recorded succeeded while DFW believes it is observing a shadow.
        """
        adapter, client, _ = _make_adapter()
        with pytest.raises(ModelManagerError) as excinfo:
            adapter.apply_control(
                control=control, name=NAME, candidate="2", from_version=None, canary_weight=weight
            )
        client.put_routing.assert_not_called()
        assert excinfo.value.code == "registry_unreachable"
        assert excinfo.value.retryable is True, "a transient read blip should be re-driven"

    def test_full_switch_needs_no_prior_primary(self):
        """A full switch names only the candidate, so it has nothing to guess."""
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="full_switch", name=NAME, candidate="2", from_version=None, canary_weight=None
        )
        assert client.put_routing.call_args.args[0] == {"model": {"name": NAME, "version": "2"}}

    def test_only_the_portable_core_is_sent(self):
        """One client for every backend means never emitting an optional field.

        The contract makes an unsupported field a hard rejection, so the safe set
        is the intersection every implementation must accept.
        """
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="shadow", name=NAME, candidate="2", from_version="1", canary_weight=None
        )
        body = client.put_routing.call_args.args[0]
        assert set(body) == {"model", "shadow"}
        assert set(body["model"]) == {"name", "version"}


class TestServingState:
    def test_composes_from_a_single_status_read(self):
        adapter, client, _ = _make_adapter(
            status=_status(
                [
                    {"version": "1", "state": "ready", "default_for": ["model"]},
                    {"version": "2", "state": "unloaded"},
                ],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            )
        )
        state = adapter.serving_state(NAME)
        assert client.get_status.call_count == 1, "one call, not the legacy three"
        assert state.default_version == "1"
        assert state.loaded_versions == ["1", "2"], "the floor keeps evicted versions"
        assert state.ready_versions == ["1"]
        assert state.resident_versions == ["1"]
        assert state.serving_versions == {"1"}
        assert state.routing_available and state.loaded_available

    def test_an_unreadable_backend_is_unknown_on_both_axes(self):
        """One status read now answers both questions, so one failure blanks both.

        That is strictly more conservative than reporting either as empty: an
        empty routing view reads as "nothing is serving" and would let cleanup
        delete a live version.
        """
        adapter, client, _ = _make_adapter()
        client.get_status.side_effect = ModelManagerError(
            code="internal", message="down", retryable=True, http_status=502
        )
        state = adapter.serving_state(NAME)
        assert state.routing_available is False
        assert state.loaded_available is False
        assert state.default_version is None

    def test_absent_routing_object_reads_as_unknown_not_as_nothing_routed(self):
        """``routing`` is an OPTIONAL property of model-status.

        Claiming availability off the HTTP status alone would defeat cleanup's
        fail-closed guard, which exists to stop it unloading a live primary on
        the strength of an empty routing view.
        """
        adapter, _, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": [{"version": "1", "state": "ready"}]}]}
        )
        state = adapter.serving_state(NAME)
        assert state.routing_available is False
        assert state.loaded_available is True, "the version rows were still readable"
        assert state.loaded_versions == ["1"]

    @pytest.mark.parametrize("slot", ["shadow", "canary"])
    def test_a_foreign_shadow_or_canary_makes_routing_unknown(self, slot):
        """A service-global policy rolling out a DIFFERENT model is not ours.

        Composing a partial view of it would let another model's canary at v2
        satisfy this model's canary convergence check.
        """
        adapter, _, _ = _make_adapter(
            status=_status(
                [{"version": "1", "state": "ready"}, {"version": "2", "state": "ready"}],
                routing={
                    "mode": slot,
                    "model": {"name": NAME, "version": "1"},
                    slot: {"name": "other_model", "version": "2"},
                    **({"canary_percent": 10} if slot == "canary" else {}),
                },
            )
        )
        state = adapter.serving_state(NAME)
        assert state.routing_available is False
        assert state.routing == []
        assert state.default_version is None
        assert state.serving_versions == set()

    def test_the_destructive_read_calls_an_unreadable_state_resident(self):
        """The residency root has two sources and a test driven through ``cleanup``
        is satisfied by whichever refuses first, so the ``destructive`` ->
        ``strict_residency`` wiring needs its own assertion.
        """
        adapter, _, _ = _make_adapter(
            status=_status(
                [{"version": "1", "state": "ready"}, {"version": "2"}],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            ),
            routing_endpoint=_ROUTING_ENDPOINT,
        )

        assert adapter.serving_state(NAME, destructive=True).resident_versions == ["1", "2"]
        assert adapter.serving_state(NAME).resident_versions == ["1"]

    def test_ready_is_derived_since_there_is_no_readiness_endpoint(self):
        adapter, _, _ = _make_adapter(
            status=_status(
                [{"version": "1", "state": "ready"}],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            )
        )
        assert adapter.serving_state(NAME).ready is True


class TestUnloadGate:
    def test_the_shipped_default_permits_unload(self):
        """Pins the DEFAULT itself, with nothing patched.

        Every other test here monkeypatches the setting, so the shipped value was
        asserted by nothing at all: the whole suite passed identically with it
        True and with it False, which is how a switch that decides whether a
        destructive call goes out could have been flipped in either direction
        without CI noticing.

        It reads the field rather than calling ``unload`` so that the failure
        names the thing that changed. Flip the default and this is the test that
        goes red.
        """
        assert settings.unified_allow_unload is True

    def test_refused_when_the_deployment_disables_it(self, monkeypatch):
        """The escape hatch still works, for a backend that evicts model-wide."""
        monkeypatch.setattr(settings, "unified_allow_unload", False)
        adapter, client, _ = _make_adapter()
        with pytest.raises(ModelManagerError) as excinfo:
            adapter.unload(NAME, "1")
        client.unload.assert_not_called()
        assert excinfo.value.code == "conflict"
        assert excinfo.value.retryable is False

    def test_apply_controls_unload_verb_is_gated_too(self, monkeypatch):
        """The gate covers `apply {control: unload}`, not just `cleanup`.

        Worth keeping now the default is permissive: `ModelManager.apply` applies
        no routing or serving guard for the unload verb, so on that path this
        setting is the only thing standing between a caller and the backend.
        """
        monkeypatch.setattr(settings, "unified_allow_unload", False)
        adapter, client, _ = _make_adapter()
        with pytest.raises(ModelManagerError):
            adapter.apply_control(
                control="unload", name=NAME, candidate="1", from_version=None, canary_weight=None
            )
        client.unload.assert_not_called()

    def test_issued_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "unified_allow_unload", True)
        adapter, client, _ = _make_adapter()
        adapter.unload(NAME, "1")
        client.unload.assert_called_once_with(NAME, "1")


class TestTheVersionFloor:
    """``loaded_versions`` is the floor DFW mints its next version above.

    The revised contract lets a conforming backend DROP an unloaded version's
    row from ``model-status`` entirely instead of retaining a tombstone
    ("Implementations may omit unloaded versions ... absence of a requested
    version from a successful status snapshot means that version is not
    loaded"). The status view was already narrower than disk for a version
    staged but never loaded; the revision widens that to EVERY evicted version.

    A floor that shrinks is not a cosmetic inaccuracy. Unified cleanup never
    reclaims disk (``deletable`` is empty, always), so the directory outlives
    the row, and ``stage_and_load`` opens the version directory with
    ``exist_ok=True``. DFW re-mints onto it, the weights are overwritten under
    the canonical name, and the previous candidate's ``spec.yaml`` and
    ``metadata.json`` survive -- both are written conditionally. The model then
    serves one candidate's weights under another's decision threshold, with
    nothing raising and the promotion recorded ``succeeded``.
    """

    def test_a_directory_the_backend_never_reports_still_holds_the_floor(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        for version in ("1", "2", "3"):
            (tmp_path / NAME / version).mkdir(parents=True)
        adapter, _, _ = _make_adapter(status=_status([{"version": "1", "state": "ready"}]))

        state = adapter.serving_state(NAME)

        assert state.loaded_versions == ["1", "2", "3"]
        # The union widens the FLOOR only. Leaking it into a convergence set is
        # worse than the bug: a directory counts as servable (a swap reports
        # success with nothing loaded) or resident (an unload never converges).
        assert state.ready_versions == ["1"]
        assert state.resident_versions == ["1"]

    def test_the_backends_own_ordering_is_preserved(self, monkeypatch, tmp_path):
        """Disk-only extras are appended, not merged in sorted order.

        The backend's sequence is meaningful and callers read the tail; sorting
        the whole list would reorder what the backend said.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        for version in ("1", "2", "3"):
            (tmp_path / NAME / version).mkdir(parents=True)
        adapter, _, _ = _make_adapter(
            status=_status([{"version": "2", "state": "ready"}, {"version": "1", "state": "ready"}])
        )

        assert adapter.serving_state(NAME).loaded_versions == ["2", "1", "3"]

    def test_an_unreadable_status_read_still_reports_the_floor_as_unknown(
        self, monkeypatch, tmp_path
    ):
        """Disk must never manufacture a floor the backend did not confirm.

        ``loaded_available=False`` is what makes DFW refuse to mint rather than
        guess. Answering a disk-derived floor here would convert "I could not
        find out" into a confident number.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "7").mkdir(parents=True)
        adapter, client, _ = _make_adapter()
        client.get_status.side_effect = ModelManagerError(
            code="internal", message="down", retryable=True, http_status=502
        )

        state = adapter.serving_state(NAME)

        assert state.loaded_available is False
        assert state.loaded_versions == []

    def test_a_missing_model_repo_degrades_to_the_reported_floor(self, monkeypatch, tmp_path):
        """A deployment where this process cannot see the repo keeps working."""
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "absent"))
        adapter, _, _ = _make_adapter(status=_status([{"version": "1", "state": "ready"}]))

        assert adapter.serving_state(NAME).loaded_versions == ["1"]


class TestInventory:
    def test_deletable_is_empty_without_an_attestation(self, monkeypatch, tmp_path):
        """A read nobody unloaded for can never nominate anything, which is what
        keeps ``cleanup``'s pre-unload read from authorising a delete.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        adapter, _, _ = _make_adapter(
            status=_status(
                [{"version": "1", "state": "ready"}, {"version": "2", "state": "unloaded"}],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            )
        )
        inventory = adapter.inventory(NAME)
        assert inventory.available is True
        assert inventory.deletable == []
        assert [v.version for v in inventory.versions] == ["1", "2"]
        assert all(v.deletable is False for v in inventory.versions)

    def test_a_routed_version_is_never_inferred_deletable(self, monkeypatch, tmp_path):
        """Evicted AND routed happen together: deft-aoi-inference publishes
        ``default_for: ["model"]`` on a row with ``state: "unloaded"``. So
        non-residency is not a proxy for non-routing.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        # The policy names a different version on purpose, leaving the row's own
        # ``default_for`` as the only guard; both naming "2" would pass with the
        # row check deleted.
        adapter, _, _ = _make_adapter(
            routing_endpoint=_ROUTING_ENDPOINT,
            status=_status(
                [
                    {"version": "1", "state": "ready"},
                    {"version": "2", "state": "unloaded", "default_for": ["model"]},
                ],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            ),
        )
        inventory = adapter.inventory(NAME, reclaiming="2")
        assert inventory.deletable == []

    def test_empty_models_never_authorizes_a_delete(self, monkeypatch, tmp_path):
        """``compose`` manufactures fully-idle rows out of absence, so a payload
        that forgot the model looks like one where everything is idle.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        for version in ("1", "2", "3"):
            (tmp_path / NAME / version).mkdir(parents=True)
        adapter, _, _ = _make_adapter(
            status={"routing": {}, "models": []}, routing_endpoint=_ROUTING_ENDPOINT
        )
        # Attests "2" because routing names "1": attesting "1" is refused by the
        # routing check even with the model-entry guard gone.
        inventory = adapter.inventory(NAME, reclaiming="2")
        assert inventory.deletable == []
        assert sorted(p.name for p in (tmp_path / NAME).iterdir()) == ["1", "2", "3"]

    def test_the_inferred_set_never_holds_more_than_the_attested_version(
        self, monkeypatch, tmp_path
    ):
        """Three idle unloaded versions on disk, one attested. Every cleanup guard
        is pre-unload and scoped to the retired version, so any other entry would
        reach the list having passed nothing.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        for version in ("1", "2", "3"):
            (tmp_path / NAME / version).mkdir(parents=True)
        adapter, _, _ = _make_adapter(
            routing_endpoint=_ROUTING_ENDPOINT,
            status=_status(
                [
                    {"version": "1", "state": "unloaded"},
                    {"version": "2", "state": "unloaded"},
                    {"version": "3", "state": "unloaded"},
                ]
            ),
        )
        # Attests "2" because "1" is the routed default: attesting it is refused
        # by the routing check and the singleton rule never runs.
        inventory = adapter.inventory(NAME, reclaiming="2")
        assert inventory.deletable == ["2"]

    def test_a_row_that_does_not_state_its_state_is_not_evidence(self, monkeypatch, tmp_path):
        """Needs the literal "unloaded". ``state_of`` defaults an absent or
        non-string field to UNLOADED, which is safe for a swap and fatal before
        an rmtree, so the inference reads the raw value.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        for row in (
            {"version": "2"},
            {"version": "2", "state": None},
            {"version": "2", "state": 7},
        ):
            adapter, _, _ = _make_adapter(status=_status([row]), routing_endpoint=_ROUTING_ENDPOINT)
            assert adapter.inventory(NAME, reclaiming="2").deletable == []

    def test_an_absent_row_is_evidence_only_when_the_caller_asserts_an_eviction(
        self, monkeypatch, tmp_path
    ):
        """The spec lets a backend omit unloaded versions and RTVI-CV drops the row
        outright -- a re-unload there answers ``404 MODEL_NOT_LOADED`` identically
        to a never-loaded and to an unknown version. An absent row therefore says
        nothing; only the caller's own record of evicting it can speak.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        adapter, _, _ = _make_adapter(
            status=_status([{"version": "1", "state": "ready"}]),
            routing_endpoint=_ROUTING_ENDPOINT,
        )
        assert adapter.inventory(NAME, reclaiming="2", evicted_by_caller=True).deletable == ["2"]
        assert adapter.inventory(NAME, reclaiming="2", evicted_by_caller=False).deletable == []

    def test_the_two_reads_must_agree_that_the_eviction_landed(self, monkeypatch, tmp_path):
        """``compose`` judges the row from the status it was handed; the residency
        check comes from a second, later read. A re-load racing the cleanup makes
        them disagree, and then the conservative one wins. Two payloads on
        purpose -- with one, the residency check is redundant.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        evicted = _status([{"version": "2", "state": "unloaded"}])
        resident_again = _status([{"version": "2", "state": "ready"}])
        adapter, client, _ = _make_adapter(routing_endpoint=_ROUTING_ENDPOINT)
        # First read feeds compose, the second the evidence.
        client.get_status.side_effect = [evicted, resident_again]
        assert adapter.inventory(NAME, reclaiming="2").deletable == []

    def test_a_failed_routing_endpoint_blocks_the_inference(self, monkeypatch, tmp_path):
        """The endpoint is the only source that sees per-stream pins, so falling
        back to another one reports routing readable with the pin set silently
        incomplete. Unreadable is the honest answer.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        adapter, client, _ = _make_adapter(
            status=_status(
                [{"version": "1", "state": "ready"}, {"version": "2", "state": "unloaded"}],
                routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
            )
        )
        client.get_routing.side_effect = ModelManagerError(
            code="registry_unreachable", message="down", retryable=True, http_status=503
        )
        assert adapter.inventory(NAME, reclaiming="2").deletable == []
        # The ordinary read may still fall back to another source.
        assert adapter.serving_state(NAME).routing_available is True

    def test_the_forced_endpoint_outranks_a_stale_status_policy(self, monkeypatch, tmp_path):
        """The flat ``model-status.routing`` key is a cached rendering; the routing
        endpoint is authoritative. Cheapest-first would let the stale copy
        outrank the fresh one on the read that ends in an rmtree.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        for version in ("1", "2"):
            (tmp_path / NAME / version).mkdir(parents=True)
        stale = _status(
            [{"version": "1", "state": "ready"}, {"version": "2", "state": "unloaded"}],
            routing={"mode": "single", "model": {"name": NAME, "version": "1"}},
        )
        adapter, client, _ = _make_adapter(status=stale)
        client.get_routing.return_value = {
            "default": {"model": {"name": NAME, "version": "2"}},
            "routes": [],
        }
        assert adapter.inventory(NAME, reclaiming="2").deletable == []

        # Control: identical but for what the endpoint says. A refusal above from
        # some other guard would refuse here too.
        adapter, client, _ = _make_adapter(status=stale)
        client.get_routing.return_value = {
            "default": {"model": {"name": NAME, "version": "1"}},
            "routes": [],
        }
        assert adapter.inventory(NAME, reclaiming="2").deletable == ["2"]

    def test_a_published_entry_with_no_backend_row_is_dropped(self, monkeypatch, tmp_path):
        """A published entry with no row clears every guard written ``row is not
        None``. Intersected against the backend-reported rows, not ``versions``,
        which already holds the synthetic disk-only rows.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "9").mkdir(parents=True)
        status = {
            "routing": {},
            "models": [
                {
                    "name": NAME,
                    "deletable": ["9"],
                    "versions": [{"version": "1", "state": "ready"}],
                }
            ],
        }
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)
        assert adapter.inventory(NAME).deletable == []

    def test_a_publishing_backend_that_drops_rows_is_told_it_reclaims_nothing(
        self, monkeypatch, tmp_path, caplog
    ):
        """A backend that both publishes ``deletable`` and drops the unloaded row can
        never reclaim here: the row intersection removes the only authority.
        Both habits are legal and no shipped backend has both, so it is latent.
        The refusal is right; the silence is not.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "9").mkdir(parents=True)
        status = {
            "routing": {},
            # RTVI-CV's row-dropping habit plus deft's publishing habit.
            "models": [{"name": NAME, "deletable": ["9"], "versions": []}],
        }
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)

        with caplog.at_level(logging.WARNING, logger="src.adapters.base"):
            assert adapter.inventory(NAME, reclaiming="9").deletable == []

        # Named, not merely logged: the operator needs to know which version.
        assert any(
            record.levelname == "WARNING"
            and "refusing to reclaim" in record.getMessage()
            and f"{NAME}@9" in record.getMessage()
            for record in caplog.records
        ), caplog.text

    def test_a_published_list_is_taken_verbatim_and_suppresses_inference(
        self, monkeypatch, tmp_path
    ):
        """A published ``[]`` is an answer, not silence, so the inference must not
        talk over it even with every precondition met.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        rows = [{"version": "2", "state": "unloaded"}]
        published = {"routing": {}, "models": [{"name": NAME, "deletable": [], "versions": rows}]}
        adapter, _, _ = _make_adapter(status=published, routing_endpoint=_ROUTING_ENDPOINT)
        assert adapter.inventory(NAME, reclaiming="2").deletable == []

        speaks = {"routing": {}, "models": [{"name": NAME, "deletable": ["2"], "versions": rows}]}
        adapter, _, _ = _make_adapter(status=speaks, routing_endpoint=_ROUTING_ENDPOINT)
        assert adapter.inventory(NAME).deletable == ["2"]

    def test_a_malformed_published_list_reports_unknown_rather_than_nothing(
        self, monkeypatch, tmp_path
    ):
        """``available=False`` so cleanup fails closed; an empty list would be
        indistinguishable from "nothing is deletable".
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        status = {
            "routing": {},
            "models": [{"name": NAME, "deletable": 5, "versions": [{"version": "2"}]}],
        }
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)
        assert adapter.inventory(NAME, reclaiming="2").available is False
        # With no attestation nothing is authorised, so the same payload degrades
        # to silence rather than an outage.
        assert adapter.inventory(NAME).available is True

    def test_the_published_roots_are_carried_up_per_version(self, monkeypatch, tmp_path):
        """``instances[]`` and ``serving`` reach ``VersionState`` unjudged; the
        refusals live in ``ModelManager``. Carriage needs its own test because
        the manager's tests build their rows by hand.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        status = _status(
            [
                {
                    "version": "1",
                    "state": "ready",
                    "serving": {"streams": 2},
                    "instances": [
                        {
                            "streams": ["camA"],
                            "config": f"/models/{NAME}/1/serving.pbtxt",
                            "engine": "/m/1/model.engine",
                        }
                    ],
                },
                {"version": "2", "state": "unloaded"},
            ]
        )
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)
        rows = {row.version: row for row in adapter.inventory(NAME).versions}

        assert rows["1"].streams == 3, "the serving block and the instance both count"
        assert rows["1"].artifact_paths == (
            f"/models/{NAME}/1/serving.pbtxt",
            "/m/1/model.engine",
        )
        # AOI publishes neither: "no opinion", not "zero streams reported".
        assert rows["2"].streams is None
        assert rows["2"].artifact_paths == ()

    def test_a_row_level_config_path_is_carried_up_too(self, monkeypatch, tmp_path):
        """``config`` sits on the row as well as inside ``instances[]`` -- the row
        is the spelling the spec declares (``ModelVersionStatus.config``) and
        captured payloads carry both. Read only the instances and a row-only
        alias reaches the aliasing check with no paths at all. The duplicate
        collapses: the same path twice is one fact.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        status = _status(
            [
                {
                    "version": "3",
                    "state": "ready",
                    "config": "/models/rtvi/ITS/1/config.txt",
                    "engine": "/models/rtvi/ITS/1/model.engine",
                    "instances": [{"config": "/models/rtvi/ITS/1/config.txt"}],
                }
            ]
        )
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)
        rows = {row.version: row for row in adapter.inventory(NAME).versions}

        assert rows["3"].artifact_paths == (
            "/models/rtvi/ITS/1/config.txt",
            "/models/rtvi/ITS/1/model.engine",
        )

    def test_a_row_with_no_readable_state_is_carried_up_as_loaded(self, monkeypatch, tmp_path):
        """``VersionState.loaded`` is the other half of ``cleanup``'s residency
        root. Composed from ``state_of``, an absent or non-string state would
        empty both halves at once. The ``unloaded`` row is asserted alongside so
        a composer that calls everything loaded cannot pass.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        status = _status(
            [
                {"version": "1"},
                {"version": "2", "state": 7},
                {"version": "3", "state": "unloaded"},
                {"version": "4", "state": "ready"},
            ]
        )
        adapter, _, _ = _make_adapter(status=status, routing_endpoint=_ROUTING_ENDPOINT)
        rows = {row.version: row for row in adapter.inventory(NAME).versions}

        assert rows["1"].loaded is True, "an absent state is not an eviction"
        assert rows["2"].loaded is True, "a non-string state is not an eviction"
        assert rows["3"].loaded is False, "the backend's own word still releases the version"
        assert rows["4"].loaded is True

    def test_reports_unavailable_instead_of_raising(self):
        adapter, client, _ = _make_adapter()
        client.get_status.side_effect = ModelManagerError(
            code="internal", message="down", retryable=True, http_status=502
        )
        inventory = adapter.inventory(NAME)
        assert inventory.available is False
        assert inventory.versions == []

    def test_on_disk_is_read_from_the_repository(self, monkeypatch, tmp_path):
        """The status view is narrower than the disk: a staged-but-unloaded
        version never appears in the serving process's index."""
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "1").mkdir(parents=True)
        (tmp_path / NAME / "7").mkdir(parents=True)
        adapter, _, _ = _make_adapter(status=_status([{"version": "1", "state": "ready"}]))
        inventory = adapter.inventory(NAME)
        assert inventory.version("1").on_disk is True
        # A directory the backend has never heard of still occupies disk.
        assert inventory.version("7") is not None
        assert inventory.version("7").on_disk is True
        assert inventory.version("7").ready is False

    def test_routed_role_is_renamed_from_the_wire_spelling(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        adapter, _, _ = _make_adapter(
            status=_status([{"version": "1", "state": "ready", "default_for": ["model"]}])
        )
        assert adapter.inventory(NAME).version("1").routed_as == "default"


class TestDeleteVersion:
    def test_delegates_to_activation(self):
        adapter, _, activation = _make_adapter()
        adapter.delete_version(NAME, "3")
        activation.delete.assert_called_once_with(NAME, "3")


class TestRegistry:
    def test_one_unified_client_serves_every_backend(self):
        """No per-use-case variant: the registry has exactly one unified entry."""
        from src.adapters.registry import _ADAPTERS

        assert sorted(_ADAPTERS) == ["aoi", "unified"]
        assert isinstance(get_adapter("unified"), UnifiedServingAdapter)

    def test_the_legacy_client_is_untouched(self):
        from src.adapters.aoi.adapter import AOIServingAdapter

        assert isinstance(get_adapter("aoi"), AOIServingAdapter)


def _policy(version="1", shadow=None):
    policy = {"model": {"name": NAME, "version": version}}
    if shadow is not None:
        policy["shadow"] = {"name": NAME, "version": shadow}
    return policy


def _rows(*versions):
    return [{"version": v, "state": "ready"} for v in versions]


class TestRoutingSourceChain:
    """Which of the three places the routing policy is read from, and when.

    The serving image that dropped ``model-status.routing`` is what this exists
    for: the key is absent entirely, so the adapter has to find the policy
    elsewhere or every promotion stalls before it starts.
    """

    def test_status_routing_wins_and_costs_no_round_trip(self):
        """Step 1 is free, so a backend that fills it must never be charged for step 2.

        This is the AOI no-regression guarantee.
        """
        adapter, client, _ = _make_adapter(
            status={
                "routing": {"mode": "single", "model": {"name": NAME, "version": "1"}},
                "models": [{"name": NAME, "versions": _rows("1")}],
            }
        )
        state = adapter.serving_state(NAME)
        assert state.default_version == "1"
        assert state.routing_available is True
        client.get_routing.assert_not_called()

    def test_an_empty_status_routing_is_an_answer_but_not_the_last_word(self):
        """``{}`` in a key the backend FILLED means "nothing is routed".

        Every released version read it that way, so it must still resolve to
        ``routing_available``. But it does NOT end the search: a backend can fill
        the key with ``{}`` while enforcing a policy it reports elsewhere, and
        stopping here would report ``serving_versions`` empty for a live version.
        """
        adapter, client, _ = _make_adapter(
            status={"routing": {}, "models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = {}
        state = adapter.serving_state(NAME)
        assert state.routing_available is True, "read it; nothing is routed"
        assert state.default_version is None

    def test_an_empty_step_one_does_not_hide_a_policy_the_endpoint_holds(self):
        """The reason the empty answer is held in reserve rather than acted on.

        Stopping at an empty step 1 would report no serving versions for a
        version that is in fact the live default -- and ``serving_versions`` is
        the input cleanup's second guard trusts.
        """
        adapter, client, _ = _make_adapter(
            status={"routing": {}, "models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = {"default": _policy("1"), "routing_revision": 3}
        state = adapter.serving_state(NAME)
        assert state.default_version == "1"
        assert state.serving_versions == {"1"}, "the live primary must not read as unrouted"

    def test_the_routing_endpoint_answers_when_status_has_no_routing_key(self):
        """The actual RTVI-CV case: keys are default/models/routing_revision/streams."""
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1", "2")}]}
        )
        client.get_routing.return_value = {
            "default": _policy("1", shadow="2"),
            "routes": [],
            "routing_revision": 4,
        }
        state = adapter.serving_state(NAME)
        assert state.routing_available is True
        assert state.default_version == "1"
        assert [(e.version, e.role) for e in state.routing] == [("1", "default"), ("2", "shadow")]
        assert state.serving_versions == {"1", "2"}

    def test_status_default_answers_when_the_endpoint_does_not(self):
        """Step 3: degraded, default slot only, but enough to converge."""
        adapter, client, _ = _make_adapter(
            status={"default": _policy("1"), "models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.side_effect = ModelManagerError(
            code="not_found", message="no such route", retryable=False, http_status=404
        )
        state = adapter.serving_state(NAME)
        assert state.routing_available is True
        assert state.default_version == "1"

    def test_a_failing_endpoint_does_not_by_itself_make_routing_unknown(self):
        """A 404 from an endpoint the backend never implemented is not an outage."""
        adapter, client, _ = _make_adapter(
            status={"default": _policy("3"), "models": [{"name": NAME, "versions": _rows("3")}]}
        )
        client.get_routing.side_effect = ModelManagerError(
            code="internal", message="boom", retryable=True, http_status=503
        )
        assert adapter.serving_state(NAME).default_version == "3"

    def test_three_misses_report_routing_as_unknown(self):
        """Fail closed. Not "nothing is routed" -- cleanup would then delete a live primary."""
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = None
        state = adapter.serving_state(NAME)
        assert state.routing_available is False
        assert state.loaded_available is True, "version rows were still readable"

    def test_a_response_carrying_no_routing_view_is_not_an_answer(self):
        """``None`` is what the client returns for a body with no routing object.

        That includes a 200 carrying an error page, so reading it as "nothing is
        routed" would let a malformed response unlock cleanup.
        """
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = None
        assert adapter.serving_state(NAME).routing_available is False

    def test_an_empty_routing_object_is_an_answer(self):
        """A conformant backend with nothing routed and no RTVI extensions.

        Its whole routing view is ``{}``. Refusing it would leave cleanup
        answering 503 forever on a backend that is behaving perfectly.
        """
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = {}
        state = adapter.serving_state(NAME)
        assert state.routing_available is True
        assert state.default_version is None

    def test_an_endpoint_that_answers_with_no_default_reports_nothing_routed(self):
        """A real routing view carrying no policy IS an answer.

        ``routing_revision: 7`` with empty ``routes`` and no ``default`` is a
        backend saying, in its own words, that nothing is routed -- distinct from
        the manufactured ``{}`` above.
        """
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = {"routing_revision": 7, "routes": []}
        state = adapter.serving_state(NAME)
        assert state.routing_available is True
        assert state.default_version is None
        assert state.serving_versions == set()

    def test_an_incomplete_routing_read_refuses(self, monkeypatch, tmp_path):
        """A routing view with no ``default`` means "nothing is routed" to a swap,
        and that reading stays. Not good enough before an rmtree: on the image
        whose model-filtered status carries no ``routing`` key, this endpoint is
        the only source, so a 200 shaped like an answer empties
        ``serving_versions`` and every routing guard passes over a live version.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        adapter, client, _ = _make_adapter(status=_status([{"version": "2", "state": "unloaded"}]))
        client.get_routing.return_value = {"routing_revision": 7, "routes": []}

        assert adapter.serving_state(NAME).routing_available is True, "the swap path is unchanged"
        assert adapter.inventory(NAME, reclaiming="2").deletable == []

    def test_an_incomplete_routing_read_refuses_a_published_list_too(self, monkeypatch, tmp_path):
        """A backend publishing its own ``deletable`` never calls
        ``infer_deletable``, so a completeness rule reachable only from there is
        thrown away and ``cleanup``'s routing guard passes on the same 200.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        adapter, client, activation = _make_adapter(
            status={
                "models": [
                    {
                        "name": NAME,
                        "versions": [{"version": "2", "state": "unloaded"}],
                        "deletable": ["2"],
                    }
                ]
            }
        )
        client.get_routing.return_value = {"routing_revision": 7, "routes": []}

        with pytest.raises(ModelManagerError) as excinfo:
            ModelManager(adapter=adapter).cleanup(CleanupRequest(model_name=NAME, version="2"))

        # On the reason: every other root also refuses a version it cannot read.
        assert excinfo.value.code == "registry_unreachable"
        assert "serving/routing state is unavailable" in excinfo.value.message
        activation.delete.assert_not_called()

    def test_an_incomplete_routing_read_refuses_the_eviction_verb_too(self, monkeypatch, tmp_path):
        """Same 200, same vacuous guard, on the verb that empties the GPU.
        ``_evict`` read with ``destructive=False``, so this payload left
        ``serving_versions`` empty and let ``unload`` evict the live default.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path))
        (tmp_path / NAME / "2").mkdir(parents=True)
        adapter, client, _ = _make_adapter(status=_status([{"version": "2", "state": "ready"}]))
        client.get_routing.return_value = {"routing_revision": 7, "routes": []}

        with pytest.raises(ModelManagerError) as excinfo:
            ModelManager(adapter=adapter).unload(UnloadRequest(model_name=NAME, version="2"))

        # On the reason: several causes refuse here, and this is the routing
        # completeness rule.
        assert excinfo.value.code == "registry_unreachable"
        assert excinfo.value.retryable is True
        assert "serving/routing state is unavailable" in excinfo.value.message
        client.unload.assert_not_called()

    def test_a_version_pinned_only_to_a_stream_is_still_serving(self):
        """Explicit per-stream routes are visible at step 2 and nowhere else.

        Such a version appears in no default policy, so without this it reads as
        idle and cleanup would delete it out from under the streams routed to it.
        """
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1", "5")}]}
        )
        client.get_routing.return_value = {
            "default": _policy("1"),
            "routes": [{"streams": ["camA"], "model": {"name": NAME, "version": "5"}}],
            "routing_revision": 9,
        }
        state = adapter.serving_state(NAME)
        assert state.serving_versions == {"1", "5"}
        assert [e.version for e in state.routing] == ["1"], "a stream pin is not a routing entry"

    def test_a_foreign_stream_route_is_not_counted_as_ours(self):
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("1")}]}
        )
        client.get_routing.return_value = {
            "default": _policy("1"),
            "routes": [{"streams": ["camA"], "model": {"name": "other_model", "version": "5"}}],
            "routing_revision": 9,
        }
        assert adapter.serving_state(NAME).serving_versions == {"1"}


class TestStreamPinsSurviveTheChain:
    """Pins are a fact about the FETCH, not about whichever view wins."""

    def test_pins_survive_a_later_source_supplying_the_policy(self):
        """Step 2 holds only stream routes; step 3 holds the default.

        Attaching pins to the winning view would drop the pinned version here,
        and cleanup would then see a version serving live streams as idle.
        """
        adapter, client, _ = _make_adapter(
            status={
                "default": _policy("1"),
                "models": [{"name": NAME, "versions": _rows("1", "5")}],
            }
        )
        client.get_routing.return_value = {
            "routes": [{"streams": ["camA"], "model": {"name": NAME, "version": "5"}}],
            "routing_revision": 9,
        }
        state = adapter.serving_state(NAME)
        assert state.default_version == "1", "the policy came from model-status.default"
        assert state.serving_versions == {"1", "5"}, "the stream pin must not be lost"

    def test_pins_survive_when_nothing_supplies_a_policy_at_all(self):
        adapter, client, _ = _make_adapter(
            status={"models": [{"name": NAME, "versions": _rows("5")}]}
        )
        client.get_routing.return_value = {
            "routes": [{"streams": ["camA"], "model": {"name": NAME, "version": "5"}}],
            "routing_revision": 9,
        }
        state = adapter.serving_state(NAME)
        assert state.routing_available is True, "read it; no default policy is set"
        assert state.default_version is None
        assert state.serving_versions == {"5"}

    def test_a_cheaper_source_winning_means_the_pins_are_never_fetched(self):
        """The gap this ordering accepts, pinned so it cannot regress silently.

        Only step 2 sees stream routes, and the chain stops at the first
        substantive view. So a backend that fills ``model-status.routing`` AND
        serves ``routes[]`` never gets asked, and a stream-pinned version is
        missing from ``serving_versions`` -- which is what cleanup's second guard
        consults.

        No shipping backend is that intersection: AOI fills step 1 and has no
        stream routes; the image that dropped the flat key serves routes but
        cannot answer step 1. Closing it costs an unconditional round trip on
        every AOI status read, which is the regression the ordering exists to
        avoid. If this test ever starts failing because a real backend landed in
        the intersection, the trade has to be revisited -- that is the point of
        asserting it.
        """
        adapter, client, _ = _make_adapter(
            status={
                "routing": {"mode": "single", "model": {"name": NAME, "version": "1"}},
                "models": [{"name": NAME, "versions": _rows("1", "5")}],
            }
        )
        client.get_routing.return_value = {
            "default": _policy("1"),
            "routes": [{"streams": ["camA"], "model": {"name": NAME, "version": "5"}}],
        }
        state = adapter.serving_state(NAME)
        assert client.get_routing.called is False, "step 1 answered, so step 2 is not consulted"
        assert state.serving_versions == {"1"}, "the documented gap: v5's stream pin is unseen"

    def test_a_transient_endpoint_failure_does_not_demote_later_reads(self):
        """No memo: one blip must not pin the adapter to a weaker source.

        Step 3 never misses, so a remembered source would never be re-resolved
        and every later tick would silently lose the stream pins.
        """
        adapter, client, _ = _make_adapter(
            status={
                "default": _policy("1"),
                "models": [{"name": NAME, "versions": _rows("1", "5")}],
            }
        )
        good = {
            "default": _policy("1"),
            "routes": [{"streams": ["camA"], "model": {"name": NAME, "version": "5"}}],
            "routing_revision": 9,
        }
        client.get_routing.side_effect = [
            good,
            ModelManagerError(code="internal", message="blip", retryable=True, http_status=503),
            good,
        ]
        assert adapter.serving_state(NAME).serving_versions == {"1", "5"}
        assert adapter.serving_state(NAME).serving_versions == {"1"}, "degraded for this tick only"
        assert adapter.serving_state(NAME).serving_versions == {"1", "5"}, "and back again"


class TestFullSwitchAgainstAMergingBackend:
    """A routing write that MERGES leaves the shadow behind; one that REPLACES does not."""

    def test_a_replacing_backend_is_only_ever_sent_the_primary(self):
        """AOI's habit. The extra field must not appear on its wire at all."""
        adapter, client, _ = _make_adapter()
        adapter.apply_control(
            control="full_switch", name=NAME, candidate="2", from_version="1", canary_weight=None
        )
        client.put_routing.assert_called_once_with({"model": {"name": NAME, "version": "2"}})

    def test_a_role_conflict_is_retried_naming_both_roles(self):
        """RTVI-CV's habit: promoting the version that is currently shadowing itself.

        ``retryable=False`` is what admits this to the retry at all, not an
        incidental detail of the fixture -- see the classification in ``errors``.
        """
        adapter, client, _ = _make_adapter()
        client.put_routing.side_effect = [
            ModelManagerError(
                code="conflict",
                message="'Car@2' is already the default shadow",
                retryable=False,
                http_status=409,
            ),
            None,
        ]
        adapter.apply_control(
            control="full_switch", name=NAME, candidate="2", from_version="1", canary_weight=None
        )
        assert client.put_routing.call_count == 2
        assert client.put_routing.call_args_list[1].args[0] == {
            "model": {"name": NAME, "version": "2"},
            "shadow": None,
        }

    @pytest.mark.parametrize(
        "detail", ["'Car@2' is not READY", "a model update is already in progress"]
    )
    def test_a_self_clearing_conflict_is_re_driven_not_answered_with_a_shadow(self, detail):
        """``MODEL_NOT_LOADED`` and ``UPDATE_IN_FLIGHT`` at 409 are not role conflicts.

        Both clear on their own. Answering them with a shadow-clearing write
        costs the promotion its retryability, because the second write's own
        failure REPLACES the first error -- and on a backend that rejects
        ``default.shadow`` that replacement is terminal. A condition that
        resolves in seconds then reaches DFW as a failed promotion plus an
        auto-rollback.
        """
        adapter, client, _ = _make_adapter()
        original = ModelManagerError(
            code="conflict", message=detail, retryable=True, http_status=409
        )
        client.put_routing.side_effect = original
        with pytest.raises(ModelManagerError) as excinfo:
            adapter.apply_control(
                control="full_switch",
                name=NAME,
                candidate="2",
                from_version=None,
                canary_weight=None,
            )
        assert client.put_routing.call_count == 1, "no shadow-clearing second write"
        # Identity, not just the flag: a freshly built error satisfies a count
        # check while losing what DFW branches on.
        assert excinfo.value is original
        assert excinfo.value.retryable is True, "DFW must still re-drive this"

    def test_a_refusal_that_is_not_a_conflict_is_not_retried(self):
        """Only the role conflict has this remedy; anything else must surface."""
        adapter, client, _ = _make_adapter()
        client.put_routing.side_effect = ModelManagerError(
            code="bad_request", message="nope", retryable=False, http_status=400
        )
        with pytest.raises(ModelManagerError):
            adapter.apply_control(
                control="full_switch",
                name=NAME,
                candidate="2",
                from_version=None,
                canary_weight=None,
            )
        assert client.put_routing.call_count == 1

    def test_a_conflict_that_survives_the_retry_still_raises(self):
        adapter, client, _ = _make_adapter()
        client.put_routing.side_effect = ModelManagerError(
            code="conflict", message="still no", retryable=False, http_status=409
        )
        with pytest.raises(ModelManagerError):
            adapter.apply_control(
                control="full_switch",
                name=NAME,
                candidate="2",
                from_version=None,
                canary_weight=None,
            )
        assert client.put_routing.call_count == 2


class TestSurvivingRoles:
    """``_surviving_roles`` decides whether a full switch names a role to clear.

    Both of its branches are load-bearing and neither was pinned. The canary arm
    was the reported finding; the ``else {}`` arm is the same defect one line
    over, and worse for having a test NAMED as though it covered it -- the
    conformance fake records method and path only, and its routing layer drops a
    null shadow, so sending the clear unconditionally left every assertion green.

    Asserted on the adapter's return value rather than through the fake, because
    the fake is exactly what could not see the difference.
    """

    def test_an_occupied_shadow_is_named_so_the_switch_clears_it(self):
        adapter, client, _ = _make_adapter(_status(versions=_rows("1", "2")))
        client.get_routing.return_value = {"default": _policy("1", shadow="2")}
        assert adapter._surviving_roles(NAME, "2") == {"shadow": None}

    def test_an_occupied_canary_is_named_too(self):
        """``canary: null`` is not expressible, so the clear travels as ``shadow``."""
        adapter, client, _ = _make_adapter(_status(versions=_rows("1", "2")))
        client.get_routing.return_value = {
            "default": {
                "model": {"name": NAME, "version": "1"},
                "canary": {"name": NAME, "version": "2"},
                "canary_percent": 5.0,
            }
        }
        assert adapter._surviving_roles(NAME, "2") == {"shadow": None}

    def test_nothing_occupied_names_nothing(self):
        """The ``else {}`` arm: a backend with no shadow or canary never sees the field.

        This is the branch that shipped unpinned. Sending ``shadow: null``
        unconditionally would be invisible to the conformance fake but is a real
        wire change against every backend -- including replacing ones, which
        would then receive a field they never got before.
        """
        adapter, client, _ = _make_adapter(_status(versions=_rows("1", "2")))
        client.get_routing.return_value = {"default": _policy("1")}
        assert adapter._surviving_roles(NAME, "2") == {}

    def test_an_unreadable_routing_view_fails_open(self):
        """Cannot read routing -> send the bare policy and rely on the 409 retry.

        Naming a role we have not observed would be a guess about live traffic.
        """
        adapter, client, _ = _make_adapter()
        client.get_status.side_effect = ModelManagerError(
            code="internal", message="boom", retryable=True
        )
        assert adapter._surviving_roles(NAME, "2") == {}


class _Wire:
    """Transport-level fake: a request handler plus the routing bodies it saw.

    Every test above hands the adapter a MagicMock CLIENT and asserts the POLICY
    it is given. That is the right seam for the adapter's own decisions -- and it
    is why enabling ``unified_stream_routes`` breaks none of them: the mirror is
    composed inside ``client.put_routing``, one level BELOW that seam. Which is
    also why nothing up there can tell whether the mirror reached a given rung.
    These drive a real ``UnifiedModelClient`` with only ``requests`` faked, so
    the body that would go on the wire is observable.
    """

    def __init__(self, routing_responses=(200,)):
        self.routing_responses = list(routing_responses)
        self.routing_values: list[dict] = []

    def __call__(self, method, url, **kwargs):
        if url.endswith("/api/v1/model/status"):
            return _WireResponse(
                200,
                {
                    "model-status": {
                        "routing": {"model": {"name": NAME, "version": "1"}},
                        "models": [{"name": NAME, "versions": _rows("1", "2")}],
                    }
                },
            )
        if method == "GET" and url.endswith("/api/v1/model/routing"):
            return _WireResponse(200, {"routing": {"default": _policy("1")}})
        self.routing_values.append(kwargs["json"]["value"])
        status = self.routing_responses.pop(0)
        if status == 409:
            return _WireResponse(
                409,
                {
                    "error": {
                        "code": "MODEL_IN_USE",
                        "message": f"'{NAME}@2' is already the default shadow",
                    }
                },
            )
        return _WireResponse(200, {"state": "ready"})


class _WireResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def close(self):
        pass


def _adapter_over_a_real_client():
    client = UnifiedModelClient(base_url="http://inf")
    return UnifiedServingAdapter(client=client, activation=MagicMock())


class TestStreamRouteMirrorReachesEveryRung:
    """``unified_stream_routes`` observed on the wire, rung by rung."""

    def _drive(self, wire, control, weight=None, from_version="1"):
        adapter = _adapter_over_a_real_client()
        with patch("src.clients.http.requests.request", side_effect=wire):
            adapter.apply_control(
                control=control,
                name=NAME,
                candidate="2",
                from_version=from_version,
                canary_weight=weight,
            )
        return wire.routing_values

    def test_the_shadow_rung_carries_it(self, monkeypatch):
        """A promotion whose shadow write stayed default-only never attaches.

        The candidate is then still unbound when the switch lands, so the switch
        is the first bind and takes the very path the mirror exists to avoid.
        """
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        values = self._drive(_Wire(), "shadow")
        policy = _policy("1", shadow="2")
        assert values == [{"default": policy, "routes": [{"streams": "all", **policy}]}]

    def test_the_full_switch_rung_carries_it(self, monkeypatch):
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        values = self._drive(_Wire(), "full_switch")
        policy = _policy("2")
        assert values == [{"default": policy, "routes": [{"streams": "all", **policy}]}]

    def test_the_409_retry_body_carries_it_too(self, monkeypatch):
        """The retry is the write that killed the container in the field.

        The ladder that reproduced it was default-only shadow, default-only
        promote, ``409 MODEL_IN_USE``, then this retry naming ``shadow: null`` --
        and the retry is the write the backend answered by building a fresh
        inference bin against the OUTGOING version's ensemble. A mirror that
        reached the first write but not the retry would leave the failure in
        place while looking fixed.
        """
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        values = self._drive(_Wire(routing_responses=(409, 200)), "full_switch")
        assert len(values) == 2, f"the conflict retry did not fire: {values}"
        primary = _policy("2")
        cleared = {**primary, "shadow": None}
        assert values[0] == {"default": primary, "routes": [{"streams": "all", **primary}]}
        assert values[1] == {"default": cleared, "routes": [{"streams": "all", **cleared}]}

    def test_the_canary_rung_stays_default_only(self, monkeypatch):
        """No ``StreamRoute`` can express a canary, so none is emitted.

        Mirroring the policy MINUS the canary would pin every stream to the
        primary and silently defeat the canary the write was establishing.
        """
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        values = self._drive(_Wire(), "canary", weight=0.05)
        assert [set(v) for v in values] == [{"default"}]

    @pytest.mark.parametrize(
        ("control", "weight"), [("shadow", None), ("canary", 0.05), ("full_switch", None)]
    )
    def test_off_no_rung_puts_anything_but_default_on_the_wire(self, control, weight, monkeypatch):
        """The flag-off guard, restated at the wire.

        Every other body assertion in this file is a flag-off body, but they all
        stop at the policy handed to the client. This one is downstream of the
        seam the mirror lives on, which is the only place a default-off
        regression could hide.
        """
        monkeypatch.setattr(settings, "unified_stream_routes", False)
        values = self._drive(_Wire(), control, weight=weight)
        assert [set(v) for v in values] == [{"default"}]
