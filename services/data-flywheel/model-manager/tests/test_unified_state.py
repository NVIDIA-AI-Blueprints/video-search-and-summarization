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

"""Normalization of the unified ``model-status`` payload (pure, no IO)."""

import pytest

from src.adapters.base import ServingState
from src.adapters.unified import state as state_mod

NAME = "changenet_classify"


def _status(versions=(), routing=None, name=NAME):
    return {
        "routing": routing or {},
        "models": [{"name": name, "versions": list(versions)}],
    }


class TestVersionSets:
    """``known`` / ``ready`` / ``resident`` answer three different questions."""

    @pytest.mark.parametrize(
        ("state", "is_ready", "is_resident"),
        [
            ("ready", True, True),
            ("serving", True, True),
            ("warming", False, True),
            # New in the revised contract, classified by the spec's own wait
            # semantics: "wait until model/status no longer reports updating,
            # then retry" means neither servable nor evicted. It falls through to
            # the conservative branch today; this row stops a future edit to
            # _SERVABLE_STATES silently promoting it.
            ("updating", False, True),
            ("unloading", False, True),
            ("accepted", False, True),
            ("failed", False, True),
            ("unloaded", False, False),
            # Unrecognized states fall through to the conservative branch.
            ("unknown", False, True),
        ],
    )
    def test_state_classification(self, state, is_ready, is_resident):
        rows = state_mod.version_rows(_status([{"version": "1", "state": state}]), NAME)
        known, ready, resident = state_mod.version_sets(rows)
        assert known == ["1"], "every reported version is part of the version floor"
        assert (("1" in ready), ("1" in resident)) == (is_ready, is_resident)

    def test_unloaded_stays_in_the_version_floor(self):
        """An evicted version still owns its directory.

        ``loaded_versions`` is what DFW mints its next version above, so dropping
        an unloaded one would hand back a colliding floor. Unload convergence
        reads ``resident_versions`` instead, which is why this is safe.
        """
        rows = state_mod.version_rows(
            _status([{"version": "1", "state": "unloaded"}, {"version": "2", "state": "ready"}]),
            NAME,
        )
        known, _, resident = state_mod.version_sets(rows)
        assert known == ["1", "2"]
        assert resident == ["2"]

    def test_missing_state_is_read_as_unloaded(self):
        rows = state_mod.version_rows(_status([{"version": "1"}]), NAME)
        _, ready, resident = state_mod.version_sets(rows)
        assert ready == [] and resident == []

    @pytest.mark.parametrize("row", [{"version": "1"}, {"version": "1", "state": 7}])
    def test_a_row_with_no_readable_state_is_resident_on_the_destructive_read(self, row):
        """A row that does not say its state cannot support "it is evicted", so the
        rmtree path must read it as resident. A swap only waits longer, so the
        lenient default is correct there.
        """
        rows = state_mod.version_rows(_status([row]), NAME)
        assert state_mod.version_sets(rows, strict_residency=True)[2] == ["1"]
        assert state_mod.version_sets(rows)[2] == [], "the swap path keeps the lenient default"

    def test_a_row_that_affirms_eviction_is_not_resident_on_either_read(self):
        """``unloaded`` must clear residency on the strict read too, or nothing is
        ever reclaimable.
        """
        rows = state_mod.version_rows(_status([{"version": "1", "state": "UNLOADED"}]), NAME)
        assert state_mod.version_sets(rows, strict_residency=True)[2] == []
        assert state_mod.version_sets(rows)[2] == []

    def test_uppercase_legacy_states_still_classify(self):
        """The legacy backend spells states in upper case; both must work."""
        rows = state_mod.version_rows(_status([{"version": "1", "state": "READY"}]), NAME)
        _, ready, _ = state_mod.version_sets(rows)
        assert ready == ["1"]


class TestStreamRefcount:
    """Zero and absent both permit; anything positive refuses. AOI never publishes
    the field, so reading its silence as "refuse" would disable the sweep there.
    """

    @staticmethod
    def _row(**fields):
        return state_mod.version_rows(_status([{"version": "1", **fields}]), NAME)[0]

    def test_an_explicit_false_is_no_streams_not_one(self):
        """``bool`` subclasses ``int``, so a ``bool`` branch would fire ahead of the
        arithmetic and make ``streams: false`` mean one attachment.
        """
        assert state_mod.stream_refcount(self._row(serving={"streams": False})) == 0

    def test_an_explicit_true_is_one_attachment(self):
        assert state_mod.stream_refcount(self._row(serving={"streams": True})) == 1

    @pytest.mark.parametrize(
        ("published", "expected"),
        [
            ({"streams": 2}, 2),
            ({"streams": ["camA", "camB"]}, 2),
            # Negative is nonsense; it must not subtract from a real attachment.
            ({"streams": -3}, 0),
            # Present but unparseable is "an attachment I cannot see", not zero.
            ({"streams": "lots"}, 1),
        ],
    )
    def test_the_published_shapes(self, published, expected):
        assert state_mod.stream_refcount(self._row(serving=published)) == expected

    def test_a_row_publishing_nothing_has_no_opinion(self):
        """None, not 0 -- the AOI shape. The two must stay distinguishable."""
        assert state_mod.stream_refcount(self._row()) is None

    def test_the_serving_block_and_the_instances_are_summed(self):
        row = self._row(serving={"streams": 1}, instances=[{"streams": ["camA"]}])
        assert state_mod.stream_refcount(row) == 2


class TestAffirmsUnloaded:
    """The literal string, or nothing. The predicate the destructive path uses."""

    @pytest.mark.parametrize("state", ["unloaded", "UNLOADED", "Unloaded"])
    def test_the_literal_state_affirms_in_any_case(self, state):
        assert state_mod.affirms_unloaded({"version": "1", "state": state}) is True

    @pytest.mark.parametrize(
        "row",
        [
            {"version": "1"},
            {"version": "1", "state": None},
            {"version": "1", "state": 7},
            {"version": "1", "state": ["unloaded"]},
            {"version": "1", "state": "ready"},
        ],
    )
    def test_anything_else_does_not(self, row):
        """Including the shapes ``state_of`` maps to UNLOADED. That is the point."""
        assert state_mod.affirms_unloaded(row) is False


class TestVersionRows:
    def test_rows_for_other_models_are_ignored(self):
        status = {
            "models": [
                {"name": "other", "versions": [{"version": "9", "state": "ready"}]},
                {"name": NAME, "versions": [{"version": "1", "state": "ready"}]},
            ]
        }
        assert [r["version"] for r in state_mod.version_rows(status, NAME)] == ["1"]

    def test_a_malformed_row_is_skipped_but_the_rest_survive(self):
        """One bad entry says nothing about the others.

        This feeds every read path, so failing the whole lookup over a single
        unparseable row would be a far larger outage than the row itself.
        """
        status = _status([{"version": "1"}, "junk", {"no_version": True}, {"version": "2"}])
        assert [r["version"] for r in state_mod.version_rows(status, NAME)] == ["1", "2"]

    def test_a_non_list_models_field_yields_nothing(self):
        assert state_mod.version_rows({"models": "nope"}, NAME) == []


class TestComposeRouting:
    def test_single(self):
        default, entries = state_mod.compose_routing(
            {"mode": "single", "model": {"name": NAME, "version": "2"}}, NAME
        )
        assert default == "2"
        assert [(e.version, e.role, e.weight) for e in entries] == [("2", "default", 1.0)]

    def test_shadow(self):
        default, entries = state_mod.compose_routing(
            {
                "mode": "shadow",
                "model": {"name": NAME, "version": "1"},
                "shadow": {"name": NAME, "version": "2"},
            },
            NAME,
        )
        assert default == "1"
        assert [(e.version, e.role, e.weight) for e in entries] == [
            ("1", "default", 1.0),
            ("2", "shadow", 0.0),
        ]

    def test_canary_splits_the_weight(self):
        default, entries = state_mod.compose_routing(
            {
                "mode": "canary",
                "model": {"name": NAME, "version": "1"},
                "canary": {"name": NAME, "version": "2"},
                "canary_percent": 5,
            },
            NAME,
        )
        assert default == "1"
        assert [(e.version, e.role, e.weight) for e in entries] == [
            ("1", "default", 0.95),
            ("2", "canary", 0.05),
        ]

    def test_fractional_canary_percent_round_trips_within_tolerance(self):
        """Convergence compares the read-back weight to within 1e-6.

        The backend accepts a fractional percent, so 12.5% has to survive the
        percent -> fraction conversion closely enough for the swap to settle.
        """
        _, entries = state_mod.compose_routing(
            {
                "mode": "canary",
                "model": {"name": NAME, "version": "1"},
                "canary": {"name": NAME, "version": "2"},
                "canary_percent": 12.5,
            },
            NAME,
        )
        canary = next(e for e in entries if e.role == "canary")
        assert abs(canary.weight - 0.125) <= 1e-6

    @pytest.mark.parametrize("slot", ["shadow", "canary"])
    def test_a_foreign_shadow_or_canary_ref_is_dropped(self, slot):
        """compose_routing must not disagree with serving_versions.

        serving_versions has always skipped foreign refs; emitting an entry for
        one here would make ServingState internally inconsistent — convergence
        would match on a version cleanup considers idle.
        """
        routing = {
            "mode": slot,
            "model": {"name": NAME, "version": "1"},
            slot: {"name": "other", "version": "2"},
            **({"canary_percent": 10} if slot == "canary" else {}),
        }
        default, entries = state_mod.compose_routing(routing, NAME)
        assert default == "1"
        assert [e.role for e in entries] == ["default"]
        assert "2" not in state_mod.serving_versions(routing, NAME)

    def test_routing_for_another_model_is_not_attributed_to_this_one(self):
        """The unified routing policy is service-global and names its model.

        Reporting another model's version as this one's default would make every
        convergence check compare against a version unrelated to the promotion.
        """
        default, entries = state_mod.compose_routing(
            {"mode": "single", "model": {"name": "other", "version": "7"}}, NAME
        )
        assert default is None and entries == []

    @pytest.mark.parametrize("sentinel", ["None", "none", "null", ""])
    def test_degenerate_version_strings_read_as_no_default(self, sentinel):
        """A service with no stable version renders it as the string "None".

        Left alone that is a valid-looking version resolving to nothing, and
        every convergence check would compare against it.
        """
        default, entries = state_mod.compose_routing(
            {"mode": "single", "model": {"name": NAME, "version": sentinel}}, NAME
        )
        assert default is None and entries == []


class TestServingVersions:
    def test_includes_shadow_and_canary_targets(self):
        """``cleanup`` refuses to delete anything in this set."""
        routing = {
            "mode": "canary",
            "model": {"name": NAME, "version": "1"},
            "canary": {"name": NAME, "version": "2"},
            "canary_percent": 10,
        }
        assert state_mod.serving_versions(routing, NAME) == {"1", "2"}

    def test_empty_routing_is_an_empty_set(self):
        assert state_mod.serving_versions({}, NAME) == set()


class TestRoutingNamesOtherModel:
    """The routing policy is service-global; a foreign ref makes it not ours."""

    def test_a_foreign_primary_is_detected(self):
        routing = {"mode": "single", "model": {"name": "other", "version": "1"}}
        assert state_mod.routing_names_other_model(routing, NAME) is True

    @pytest.mark.parametrize("slot", ["shadow", "canary"])
    def test_a_foreign_shadow_or_canary_is_detected(self, slot):
        """The slot the primary guard alone does not cover.

        Convergence matches on a bare version number, so another model's canary
        at v2 satisfies `_control_reflected("canary", candidate="2", ...)`
        exactly — the swap reports succeeded while this model has no canary.
        """
        routing = {
            "mode": slot,
            "model": {"name": NAME, "version": "1"},
            slot: {"name": "other", "version": "2"},
        }
        assert state_mod.routing_names_other_model(routing, NAME) is True

    def test_our_own_policy_is_not_flagged(self):
        routing = {
            "mode": "canary",
            "model": {"name": NAME, "version": "1"},
            "canary": {"name": NAME, "version": "2"},
            "canary_percent": 5,
        }
        assert state_mod.routing_names_other_model(routing, NAME) is False

    def test_a_ref_without_a_name_is_treated_as_foreign(self):
        """``name`` is required on the spec's ModelRef.

        A ref without one is a non-conformant payload, and "I cannot tell whose
        version this is" has to read as "not ours" — the other reading
        attributes someone else's rollout to this model.
        """
        routing = {"mode": "single", "model": {"version": "1"}}
        assert state_mod.routing_names_other_model(routing, NAME) is True

    @pytest.mark.parametrize("routing", [{}, None, "junk"])
    def test_absent_or_malformed_routing_is_not_flagged(self, routing):
        assert state_mod.routing_names_other_model(routing, NAME) is False


class TestRoutedRoles:
    def test_wire_model_role_is_renamed_to_default(self):
        rows = [
            {"version": "1", "default_for": ["model"]},
            {"version": "2", "default_for": ["shadow"]},
            {"version": "3"},
        ]
        assert state_mod.routed_roles(rows) == {"1": "default", "2": "shadow"}


class TestInferMode:
    """``mode`` exists only on the flat status view, so it is recovered from slots.

    The schema declares ``shadow`` and ``canary`` mutually exclusive, which is
    what makes this exact rather than a guess.
    """

    @pytest.mark.parametrize(
        ("policy", "expected"),
        [
            ({"model": {"name": NAME, "version": "1"}}, "single"),
            (
                {"model": {"name": NAME, "version": "1"}, "shadow": {"name": NAME, "version": "2"}},
                "shadow",
            ),
            (
                {
                    "model": {"name": NAME, "version": "1"},
                    "canary": {"name": NAME, "version": "2"},
                    "canary_percent": 10,
                },
                "canary",
            ),
            ({}, "single"),
        ],
    )
    def test_the_occupied_slot_names_the_mode(self, policy, expected):
        assert state_mod.infer_mode(policy) == expected

    def test_a_null_slot_reads_as_absent_not_as_occupied(self):
        """JSON ``null`` is how the contract CLEARS a shadow.

        A backend echoing the cleared slot back as ``null`` is describing a
        policy with no shadow; reading that as "shadow mode" would compose a
        shadow entry for a rollout that is over.
        """
        policy = {"model": {"name": NAME, "version": "1"}, "shadow": None}
        assert state_mod.infer_mode(policy) == "single"

    def test_a_non_dict_policy_is_single_rather_than_an_exception(self):
        assert state_mod.infer_mode(None) == "single"
        assert state_mod.infer_mode("shadow") == "single"

    def test_both_slots_filled_prefers_canary(self):
        """Non-conformant input: the schema forbids this, so pick the safe reading.

        Canary keeps the weighted split, which is the reading that cannot
        silently put live traffic on an unvalidated version.
        """
        policy = {
            "model": {"name": NAME, "version": "1"},
            "shadow": {"name": NAME, "version": "2"},
            "canary": {"name": NAME, "version": "3"},
            "canary_percent": 5,
        }
        assert state_mod.infer_mode(policy) == "canary"


class TestAsStatusView:
    """Adapting a ``DefaultRoutingPolicy`` to the flat shape is only the mode."""

    def test_every_other_key_passes_through_untouched(self):
        policy = {
            "model": {"name": NAME, "version": "1"},
            "canary": {"name": NAME, "version": "2"},
            "canary_percent": 10,
        }
        view = state_mod.as_status_view(policy)
        assert view == {**policy, "mode": "canary"}

    def test_the_adapted_view_feeds_compose_routing_unchanged(self):
        """The point of the adaptation: no downstream rule learns a new shape."""
        policy = {"model": {"name": NAME, "version": "1"}, "shadow": {"name": NAME, "version": "2"}}
        default_version, entries = state_mod.compose_routing(state_mod.as_status_view(policy), NAME)
        assert default_version == "1"
        assert [(e.version, e.role) for e in entries] == [("1", "default"), ("2", "shadow")]

    def test_it_does_not_mutate_the_caller_s_policy(self):
        policy = {"model": {"name": NAME, "version": "1"}}
        state_mod.as_status_view(policy)
        assert "mode" not in policy, "the backend's payload must not grow a synthesized field"

    def test_a_non_dict_policy_adapts_to_nothing(self):
        assert state_mod.as_status_view(None) == {}


class TestModeSynthesisIsWhatKeepsFullSwitchHonest:
    """The case the whole adaptation exists for.

    RTVI-CV MERGES a routing write, so a shadow can survive a ``full_switch``.
    Reading that policy without synthesizing a mode drops the surviving shadow
    from the entries, ``all(e.role == "default")`` then passes, and the swap is
    recorded succeeded while a stale shadow still takes traffic.
    """

    def test_a_surviving_shadow_is_visible_and_blocks_convergence(self):
        from src.core.manager import _control_reflected

        policy = {"model": {"name": NAME, "version": "2"}, "shadow": {"name": NAME, "version": "3"}}
        default_version, entries = state_mod.compose_routing(state_mod.as_status_view(policy), NAME)
        assert [(e.version, e.role) for e in entries] == [("2", "default"), ("3", "shadow")]

        state = ServingState(
            default_version=default_version,
            routing=entries,
            routing_available=True,
            loaded_available=True,
            ready_versions=["2", "3"],
        )
        assert not _control_reflected("full_switch", "2", state), "a live shadow is not converged"

    def test_without_the_synthesized_mode_it_would_report_success(self):
        """Pins WHY the adaptation is not cosmetic; this is the bug it prevents."""
        from src.core.manager import _control_reflected

        raw = {"model": {"name": NAME, "version": "2"}, "shadow": {"name": NAME, "version": "3"}}
        default_version, entries = state_mod.compose_routing(raw, NAME)  # no mode
        assert [(e.version, e.role) for e in entries] == [("2", "default")], "shadow dropped"

        state = ServingState(
            default_version=default_version,
            routing=entries,
            routing_available=True,
            loaded_available=True,
            ready_versions=["2"],
        )
        assert _control_reflected("full_switch", "2", state), "the false success being prevented"


class TestLegalPoliciesDoNotLogAsMalformed:
    def test_a_shadow_only_policy_names_no_primary_and_says_so_quietly(self, caplog):
        """The schema's anyOf admits ``required: [shadow]`` with no model slot."""
        policy = {"shadow": {"name": NAME, "version": "2"}}
        with caplog.at_level("WARNING"):
            default_version, entries = state_mod.compose_routing(
                state_mod.as_status_view(policy), NAME
            )
        assert (default_version, entries) == (None, [])
        assert "names model" not in caplog.text, "legal payload must not look foreign"
        assert state_mod.serving_versions(state_mod.as_status_view(policy), NAME) == {"2"}

    def test_a_foreign_primary_still_warns(self, caplog):
        policy = {"model": {"name": "someone_else", "version": "2"}}
        with caplog.at_level("WARNING"):
            state_mod.compose_routing(state_mod.as_status_view(policy), NAME)
        assert "names model" in caplog.text

    def test_declared_mode_loses_to_the_occupied_slots(self):
        """Documented decision: slots are ground truth for what is occupied."""
        policy = {"mode": "canary", "model": {"name": NAME, "version": "1"}}
        assert state_mod.as_status_view(policy)["mode"] == "single"
