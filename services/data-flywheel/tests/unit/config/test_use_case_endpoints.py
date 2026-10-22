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
"""Per-use-case endpoint overrides on ``DEFTConfig``.

Two use cases (AOI and its streaming sibling ITS) can run in ONE process against
DIFFERENT object stores. The seam that allows that is ``for_use_case``, which
hands every existing consumer — ``StorageBackendClient``, ``presign_pull_locators``,
both of which already take a whole ``DEFTConfig`` — the config as that use case
sees it, with no signature change anywhere.

Two properties are load-bearing here and they are in tension, which is why both
are pinned:

* A deployment that declares NOTHING (``use_case_endpoints: {}``) must get back
  its own config object for every lookup. That is the pre-ITS world, and if it
  ever stops holding, AOI's behaviour changes on a config that did not change.
* A deployment that declares SOMETHING has declared EVERYTHING: ``for_use_case``
  refuses a use case absent from a non-empty map instead of silently handing it
  the top-level endpoints. Returning self there made "undeclared" and "declares
  no overrides" indistinguishable, so an ITS run read AOI's store and said
  nothing about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config.deft import DEFTConfig, UseCaseEndpoints
from src.config_renderer import load_rendered_yaml

SHIPPED_CONFIG = Path(__file__).resolve().parents[3] / "config" / "config.deft.yaml"


def _shipped_deft_config() -> DEFTConfig:
    """The ``deft_config`` block exactly as shipped (no env, no backend loader).

    Rendered rather than raw-loaded: every endpoint in the file is a
    ``${DFW_*:-default}`` scalar, so a plain ``yaml.safe_load`` would hand the
    model the placeholder TEXT. Passing only ``AOI_SELECTION_TAG`` (the one
    variable with no default) is what makes this "as shipped" — every other
    value falls back to the default written in the file.
    """
    data = load_rendered_yaml(SHIPPED_CONFIG, environ={"AOI_SELECTION_TAG": "aoi1"})
    return DEFTConfig(**data.get("deft_config", {}))


# --- the shipped config declares BOTH use cases ------------------------------


def test_the_shipped_map_declares_every_use_case_the_file_runs():
    """The map ships POPULATED, because shipping it empty taught the wrong shape.

    An empty map is not "no policy", it is "every use case shares AOI's store",
    and it reads identically to a deployment that meant that. With ITS in the
    same file that is a live cross-store bug rather than a latent one: `its`
    would have snapshotted, presigned and promoted against `s3://aoi-artifacts`
    and reported success throughout.

    ``aoi: {}`` is stated rather than omitted on purpose — see
    ``test_aoi_declares_no_overrides_and_still_resolves_to_the_top_level``.

    The set must match the use cases registered in ``src/backends/deft/eval.py``:
    the map is EXHAUSTIVE once non-empty, so a registered use case missing here
    is refused at snapshot rather than inheriting.
    """
    assert set(_shipped_deft_config().use_case_endpoints) == {"aoi", "its"}


def test_aoi_declares_no_overrides_and_still_resolves_to_the_top_level():
    """The empty entry is a DECLARATION, and it must cost AOI nothing.

    ``aoi: {}`` states "inherits everything above" — the thing an OMITTED entry
    can no longer mean now that the map is exhaustive. Identity, not equality:
    the no-override path returns SELF, so AOI callers cannot pay a copy per
    lookup and cannot observe a different object than before ITS landed.
    """
    cfg = _shipped_deft_config()
    resolved = cfg.for_use_case("aoi")

    assert resolved is cfg
    assert resolved.model_dump() == cfg.model_dump()
    assert cfg.storage_backend_url == "http://storage-backend:8080"
    assert cfg.object_store_uri == "s3://aoi-artifacts"


def test_its_declares_nothing_and_therefore_shares_the_one_storage_backend():
    """ITS is an empty entry, so it resolves exactly where AOI does.

    The shipped deployment fronts ONE Storage Backend — DFW is deployed per use
    case, and the Storage Backend is growing multi-use-case support of its own,
    so ITS is separated by prefix inside the shared bucket rather than by a
    second host and a second bearer. Identity, not equality: an entry that
    overrides nothing short-circuits to self, so a distinct object here would
    mean the file quietly reintroduced a field.

    The credential is still asserted, because unset means INHERIT and that is
    what makes a HALF-written override dangerous: repointing only the URL would
    send this bearer to a host it was never issued for. That mechanism is
    exercised on a fixture in ``test_unstated_fields_inherit_rather_than_reset_to_defaults``;
    what is pinned here is that the SHIPPED file does not use it.
    """
    cfg = _shipped_deft_config()
    its = cfg.for_use_case("its")

    assert its is cfg
    assert its.storage_backend_url == "http://storage-backend:8080"
    assert its.object_store_uri == "s3://aoi-artifacts"
    assert its.storage_api_key_env == DEFTConfig().storage_api_key_env
    assert its.deft_base_url == "http://deft:8000"


@pytest.mark.parametrize("use_case", ["not-a-use-case", "aoi_v2"])
def test_the_shipped_config_refuses_a_use_case_it_never_declared(use_case):
    """Exhaustive means exhaustive — a new use case must be added here first.

    The failure is the point. Before this, a job config naming a use case the
    deployment had never heard of ran to completion against AOI's store; now it
    cannot start, and the message names the use case and what was declared.

    Every name here is one nothing in this repo registers. A REGISTERED use case
    appearing in this list would mean the guard was refusing something the
    deployment can legitimately run -- the bug, not the guard working.
    """
    with pytest.raises(ValueError, match=use_case):
        _shipped_deft_config().for_use_case(use_case)


def test_a_missing_use_case_is_still_the_legacy_top_level_path():
    """``None`` is not an undeclared use case, it is a caller that never routed.

    Pre-ITS call sites pass no use case at all (a promotion record written before
    the field existed). Refusing those would break a path that has no store to
    cross — there is only ever one for a caller that names none.
    """
    cfg = _shipped_deft_config()
    assert cfg.for_use_case(None) is cfg
    assert cfg.for_use_case("") is cfg


# --- a typo is a config error, never a silent inherit ------------------------


@pytest.mark.parametrize(
    "typo",
    [
        "storage_url",  # storage_backend_url
        "object_store",  # object_store_uri
        "storage_api_key",  # storage_api_key_env
        "presign_expires",  # presign_expires_s
    ],
)
def test_an_unknown_key_is_rejected_rather_than_inherited(typo):
    """FAIL-CLOSED on the one block where fail-open crosses two data stores.

    Every field here is optional and None means "inherit the AOI value", so a
    dropped unknown key does not degrade to "no override" — it degrades to
    "silently use AOI's object store for ITS". The typo has to raise.
    """
    with pytest.raises(ValidationError) as excinfo:
        UseCaseEndpoints(**{typo: "http://somewhere"})
    # The message has to name the key, or an operator cannot act on it.
    assert typo in str(excinfo.value)


def test_the_typo_fails_when_the_whole_deft_config_is_built():
    """At STARTUP, not at first storage call: the map is built by DEFTConfig."""
    with pytest.raises(ValidationError, match="object_store"):
        DEFTConfig(use_case_endpoints={"its": {"object_store": "s3://sc-artifacts"}})


def test_an_undeclared_use_case_is_refused_rather_than_inheriting():
    """The bug this contract exists to kill, at its smallest.

    A map that declares only ``its`` says nothing about ``aoi``. Resolving
    ``aoi`` to SELF here looked harmless — it returns the top-level endpoints,
    which is what AOI wants — but it is the SAME code path that handed every
    other undeclared use case a store it was never meant to touch, and there is
    no way to tell the two apart from inside. So it raises, and the deployment
    states its intent with an ``aoi: {}`` entry (proved below).
    """
    cfg = DEFTConfig(
        use_case_endpoints={"its": UseCaseEndpoints(object_store_uri="s3://sc-artifacts")}
    )
    with pytest.raises(ValueError, match="use_case_endpoints"):
        cfg.for_use_case("aoi")


def test_the_predicate_is_the_declaration_and_never_the_name():
    """``use_case != "aoi"`` would be the wrong rule, and this is why.

    An AOI-family use case registered against the AOI evaluator legitimately
    shares the top-level endpoints, and its NAME says nothing about that. Keying
    off the name would hard-fail it while waving through any use case that
    happened to be spelled ``aoi``; keying off the DECLARATION gets both right,
    and gets there without the config module knowing a single use-case name --
    which is why the fixture below uses a name this repo does not register.
    """
    shared = DEFTConfig(
        use_case_endpoints={
            "aoi": UseCaseEndpoints(),
            "aoi_family_sibling": UseCaseEndpoints(),
            "its": UseCaseEndpoints(object_store_uri="s3://sc-artifacts"),
        }
    )
    assert shared.for_use_case("aoi_family_sibling") is shared
    assert shared.for_use_case("aoi") is shared
    assert shared.for_use_case("its").object_store_uri == "s3://sc-artifacts"


def test_an_empty_map_declares_nothing_so_nothing_can_be_undeclared():
    """Back-compat, stated as a rule rather than as a special case.

    A deployment that never adopted the map keeps exactly its pre-ITS behaviour:
    one set of endpoints for everyone, no guard, no copy. Fail-closed only has
    an opinion once someone has expressed one.
    """
    cfg = DEFTConfig()
    for use_case in ("aoi", "its", "a_sibling", "anything", None, ""):
        assert cfg.for_use_case(use_case) is cfg


def test_a_declared_entry_of_all_nones_inherits_everything_and_copies_nothing():
    """The empty entry passes the guard and still short-circuits to SELF.

    Declaration and override are different questions: ``its: {}`` answers the
    first (yes, deliberately) and the second (nothing). Only the first is what
    the guard reads.
    """
    cfg = DEFTConfig(use_case_endpoints={"its": UseCaseEndpoints()})
    assert cfg.for_use_case("its") is cfg


# --- overrides win, per use case, without mutating the original -------------


def test_override_wins_for_its_and_leaves_aoi_alone():
    cfg = DEFTConfig(
        storage_backend_url="http://storage-backend:8080",
        object_store_uri="s3://aoi-artifacts",
        storage_api_key_env="STORAGE_BACKEND_API_KEY",
        presign_expires_s=3600,
        use_case_endpoints={
            # Stated, because the map is exhaustive once non-empty: AOI's
            # "inherits, deliberately" has to be written down too.
            "aoi": UseCaseEndpoints(),
            "its": UseCaseEndpoints(
                storage_backend_url="http://sc-backend:8080",
                object_store_uri="s3://sc-artifacts",
                storage_api_key_env="ITS_STORAGE_API_KEY",
                presign_expires_s=600,
            ),
        },
    )

    its = cfg.for_use_case("its")
    assert its.storage_backend_url == "http://sc-backend:8080"
    assert its.object_store_uri == "s3://sc-artifacts"
    assert its.storage_api_key_env == "ITS_STORAGE_API_KEY"
    assert its.presign_expires_s == 600

    # AOI, resolved from the SAME config object, is untouched.
    aoi = cfg.for_use_case("aoi")
    assert aoi.storage_backend_url == "http://storage-backend:8080"
    assert aoi.object_store_uri == "s3://aoi-artifacts"
    assert aoi.storage_api_key_env == "STORAGE_BACKEND_API_KEY"
    assert aoi.presign_expires_s == 3600


def test_resolving_does_not_mutate_the_original():
    """``for_use_case`` is a pure read — three processes call it concurrently."""
    cfg = DEFTConfig(
        object_store_uri="s3://aoi-artifacts",
        use_case_endpoints={"its": UseCaseEndpoints(object_store_uri="s3://sc-artifacts")},
    )
    before = cfg.model_dump()

    cfg.for_use_case("its")

    assert cfg.model_dump() == before
    assert cfg.object_store_uri == "s3://aoi-artifacts"


def test_unstated_fields_inherit_rather_than_reset_to_defaults():
    """A partial entry states only what differs; the rest is the top level."""
    cfg = DEFTConfig(
        deft_base_url="http://deft-prod:8000",
        storage_backend_url="http://storage-backend:8080",
        api_key_env="DEFT_API_KEY",
        storage_api_key_env="STORAGE_BACKEND_API_KEY",
        presign_expires_s=1800,
        use_case_endpoints={"its": UseCaseEndpoints(object_store_uri="s3://sc-artifacts")},
    )

    its = cfg.for_use_case("its")
    assert its.object_store_uri == "s3://sc-artifacts"
    assert its.deft_base_url == "http://deft-prod:8000"
    assert its.storage_backend_url == "http://storage-backend:8080"
    assert its.api_key_env == "DEFT_API_KEY"
    assert its.storage_api_key_env == "STORAGE_BACKEND_API_KEY"
    assert its.presign_expires_s == 1800


def test_resolved_copy_still_carries_the_map():
    """Resolution is idempotent, so a resolved config can be passed on safely."""
    cfg = DEFTConfig(
        use_case_endpoints={"its": UseCaseEndpoints(object_store_uri="s3://sc-artifacts")}
    )
    once = cfg.for_use_case("its")
    # Assert the map SURVIVED the copy, directly. The round-trip equality below
    # cannot see its loss: a copy that dropped the map resolves to no overrides,
    # so `once.for_use_case("its")` returns `once` itself and the dumps match
    # trivially — the test passed in exactly the world its name forbids.
    assert once.use_case_endpoints == cfg.use_case_endpoints
    twice = once.for_use_case("its")
    assert twice.model_dump() == once.model_dump()


# --- endpoints_for distinguishes 'no entry' from 'entry that inherits' -------


def test_endpoints_for_returns_none_when_nothing_is_declared():
    """``for_use_case`` collapses both to SELF, so the snapshot guard — which
    demands a DECLARATION, not an override — has to read this instead."""
    cfg = DEFTConfig(use_case_endpoints={"its": UseCaseEndpoints()})
    assert cfg.endpoints_for("aoi") is None
    assert cfg.endpoints_for(None) is None
    assert cfg.endpoints_for("its") == UseCaseEndpoints()


def test_every_override_field_is_a_deft_config_field():
    """What lets ``for_use_case`` apply an entry as a plain model_copy update.

    A field here with no ``DEFTConfig`` counterpart would become a phantom
    attribute on the copy — set, and read by nothing.
    """
    assert set(UseCaseEndpoints.model_fields) <= set(DEFTConfig.model_fields)


# --- bounds still apply to an override --------------------------------------


@pytest.mark.parametrize("bad", [59, 0, -1, 86401])
def test_presign_expires_s_bounds_are_enforced_on_overrides(bad):
    """An override is an escape hatch from the default, not from the contract."""
    with pytest.raises(ValidationError):
        UseCaseEndpoints(presign_expires_s=bad)


@pytest.mark.parametrize("good", [60, 3600, 86400])
def test_presign_expires_s_accepts_the_documented_range(good):
    assert UseCaseEndpoints(presign_expires_s=good).presign_expires_s == good


def test_bad_bounds_fail_when_the_whole_deft_config_is_built():
    """The failure lands at config load, not at first presign."""
    with pytest.raises(ValidationError):
        DEFTConfig(use_case_endpoints={"its": {"presign_expires_s": 10}})


def test_map_accepts_plain_dicts_from_yaml():
    """YAML hands nested plain dicts; pydantic must coerce them to the model."""
    cfg = DEFTConfig(
        use_case_endpoints={
            "its": {"object_store_uri": "s3://sc-artifacts", "presign_expires_s": 600}
        }
    )
    assert isinstance(cfg.use_case_endpoints["its"], UseCaseEndpoints)
    assert cfg.for_use_case("its").object_store_uri == "s3://sc-artifacts"
