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

"""Minting a serving/Triton version that cannot collide with a live one.

The counter alone cannot answer this: it records what *this* DFW handed out, not
what the serving stack holds. These tests pin the combination — counter, live
stack, and the refusal to guess when neither can say.
"""

import pytest

from src.tasks.promotion.serving_version import (
    ObservedServing,
    ServingVersionUnknownError,
    next_serving_version,
    observed_serving_floor,
)

MODEL = "changenet_classify"


def _seed(db, model_name: str, seq: int) -> None:
    db.serving_version_counters.insert_one({"_id": model_name, "seq": seq})


def _counter(db, model_name: str = MODEL):
    return db.serving_version_counters.find_one({"_id": model_name})


# --- the floor helper -------------------------------------------------------


class TestObservedServingFloor:
    def test_takes_the_maximum(self):
        assert observed_serving_floor(["1", "2"]) == 2

    def test_compares_numerically_not_lexicographically(self):
        """`max("9", "10")` is "9" as strings — that would re-mint onto v10."""
        assert observed_serving_floor(["9", "10"]) == 10

    def test_ignores_unloaded_state_because_the_slot_is_still_taken(self):
        # The caller passes versions regardless of state; nothing here filters.
        assert observed_serving_floor(["1", "2", "3"]) == 3

    def test_skips_non_integer_versions(self):
        """The published contract's own examples are semver ("1.0.0").

        Those cannot take part in integer arithmetic. Skipping them is honest;
        coercing them would invent a floor.
        """
        assert observed_serving_floor(["1", "1.0.0", "prod", "7"]) == 7

    def test_all_non_integer_is_unknown_not_zero(self):
        assert observed_serving_floor(["1.0.0", "prod"]) is None

    def test_empty_and_none_are_unknown(self):
        assert observed_serving_floor([]) is None
        assert observed_serving_floor(None) is None

    def test_normalises_leading_zeros(self):
        """ "007" and "7" are the same directory on disk."""
        assert observed_serving_floor(["007"]) == 7


# --- minting ----------------------------------------------------------------


class TestMintingFromTheCounter:
    def test_counter_advances(self, fake_db):
        _seed(fake_db, MODEL, 5)
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=None)) == 6

    def test_repeated_mints_never_repeat(self, fake_db):
        _seed(fake_db, MODEL, 1)
        seen = [
            next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=2))
            for _ in range(4)
        ]
        assert seen == [3, 4, 5, 6]
        assert len(set(seen)) == len(seen)

    def test_day_zero_stack_that_holds_nothing_mints_past_the_seed(self, fake_db):
        """A genuinely fresh deployment: reachable stack, no versions yet.

        The AOI seed writes `changenet_classify/1/`, so the first candidate is 2.
        """
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=None)) == 2


class TestLiveStackWins:
    def test_live_floor_lifts_a_stale_counter(self, fake_db):
        """The scenario observed on the live box.

        The stack serves version 2; a counter that lagged would otherwise re-mint
        it, overwrite the incumbent's weights, and be rejected 422 at routing.
        """
        _seed(fake_db, MODEL, 1)
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=2)) == 3

    def test_fresh_dfw_against_an_already_promoted_stack(self, fake_db):
        """No counter at all — a new install, restored Mongo, or second DFW.

        This is the case a hard-coded baseline of 1 gets wrong: it would mint 2,
        which is exactly what that stack is already serving.
        """
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=2)) == 3
        assert _counter(fake_db)["seq"] == 3

    def test_counter_wins_when_it_is_ahead(self, fake_db):
        """A version minted but not yet staged is not visible to the stack."""
        _seed(fake_db, MODEL, 9)
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=2)) == 10

    def test_a_lower_live_floor_never_drags_the_counter_back(self, fake_db):
        """After a rollback the ROUTED version drops while the repo keeps both.

        Even if a caller passed the lower number, the counter must not regress.
        """
        _seed(fake_db, MODEL, 7)
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=1)) == 8

    def test_the_observed_floor_is_persisted_so_it_holds_after_a_wipe(self, fake_db):
        next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=41))
        assert _counter(fake_db)["seq"] == 42


class TestRefusesToGuess:
    def test_unknown_stack_and_no_counter_raises(self, fake_db):
        """Neither authority can speak — minting here is how weights get lost."""
        with pytest.raises(ServingVersionUnknownError) as exc:
            next_serving_version(fake_db, MODEL, observed=None)
        assert MODEL in str(exc.value)

    def test_unknown_stack_leaves_no_counter_behind(self, fake_db):
        """The refusal must not upsert a guess for the next call to inherit."""
        with pytest.raises(ServingVersionUnknownError):
            next_serving_version(fake_db, MODEL, observed=None)
        assert _counter(fake_db) is None

    def test_unknown_stack_is_survivable_once_dfw_has_minted_before(self, fake_db):
        """A brief outage must not block a deployment DFW already owns."""
        _seed(fake_db, MODEL, 4)
        assert next_serving_version(fake_db, MODEL, observed=None) == 5

    def test_default_argument_is_the_cautious_one(self, fake_db):
        """Omitting `observed` means "I did not ask", not "nothing is there"."""
        with pytest.raises(ServingVersionUnknownError):
            next_serving_version(fake_db, MODEL)


class TestPerModelIsolation:
    def test_counters_do_not_bleed_across_models(self, fake_db):
        _seed(fake_db, MODEL, 5)
        assert (
            next_serving_version(fake_db, "other-model", observed=ObservedServing(floor=None)) == 2
        )
        assert next_serving_version(fake_db, MODEL, observed=ObservedServing(floor=None)) == 6
