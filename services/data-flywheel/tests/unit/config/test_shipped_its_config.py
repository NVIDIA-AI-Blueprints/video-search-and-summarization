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
"""The SHIPPED config.deft.yaml actually loads, and ITS is really wired up.

Every other ITS config test builds its fixture inline, so all of them would stay
green against a shipped YAML that does not parse or does not declare ITS at all.
This file loads ``config/config.deft.yaml`` itself and asserts the two things
that make ITS a working second job type rather than a plausible-looking block:

1. the ``its_job_config:`` block builds into ``job_configs["its_job"]`` as a
   real ``DeftJobConfig`` — i.e. the deployment default the no-``config_name``
   submission path actually resolves; and
2. every use case the file declares is DECLARED — ``aoi`` and ``its`` both
   resolve, both to the deployment's single Storage Backend, via explicit
   entries.

(2) is the load-bearing one, and the emptiness is the subtle part. An empty map
would resolve every use case identically too — but it would do so by never
having heard of any of them, which is the failure this map exists to catch. A
POPULATED map of empty entries says "these three share the store ON PURPOSE"
and refuses the fourth. The only proof is reading the shipped file the
deployment actually runs on.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest import mock

import pytest
import yaml
from pydantic import ValidationError

from src.config import build_job_config, config_type_for
from src.config.base import Settings
from src.config.deft import AOIJobConfig, DeftJobConfig
from src.config_renderer import render_environment_values

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = REPO_ROOT / "config" / "config.deft.yaml"
ITS_EXAMPLE_JSON = REPO_ROOT / "config" / "its_config_example.json"
#: The ITS catalogue's ES index template, in the Active Learning repo — present
#: when this repo is checked out as a submodule of the deployment, absent when it
#: is cloned on its own.
SC_INDEX_TEMPLATE = (
    REPO_ROOT.parent
    / "deft-aoi-active-learning/deploy/docker/config/database/elasticsearch"
    / "templates/sc-template.json"
)
#: The DEPLOYED overlays: the files the running services actually mount, which
#: are NOT this repo's ``config/config.deft.yaml``. The bad ITS ``tag`` value
#: shipped in these and in nothing else, because nothing tested them.
DEPLOYED_CONFIGS = sorted(
    (REPO_ROOT.parents[1] / "deploy/deft-aoi-data-flywheel/service-configs").glob(
        "*/configs/config.deft.yaml"
    )
)

#: Container types a term filter cannot address, so nothing under them is a
#: legal selection key.
_UNSELECTABLE_TYPES = {"nested", "geo_point", "dense_vector"}


def _mapped_paths(properties: dict, prefix: str = "") -> frozenset[str]:
    """Dotted paths of every selectable leaf under an ES ``properties`` block."""
    paths: set[str] = set()
    for name, mapping in properties.items():
        if mapping.get("type") in _UNSELECTABLE_TYPES:
            continue
        if "properties" in mapping:
            paths |= _mapped_paths(mapping["properties"], f"{prefix}{name}.")
        else:
            paths.add(f"{prefix}{name}")
    return frozenset(paths)


#: Every field the ITS index template maps under ``metadata.content_metadata``,
#: which is exactly the set ``compile_selection`` can turn into a term filter
#: (see ``sc-template.json``, ``index_patterns: sc_*``, ``dynamic: strict``).
#: Kept here as data rather than read from the template at import time so the
#: guard below still guards in a standalone checkout of this repo;
#: ``test_the_mapped_field_set_is_the_one_the_index_template_really_declares``
#: re-derives it whenever the template is reachable.
ITS_SELECTION_FIELDS = frozenset(
    {
        "analyticsModule.description",
        "analyticsModule.id",
        "analyticsModule.source",
        "analyticsModule.version",
        "chunkIdx",
        "description",
        "doc_i",
        "doc_type",
        "frameId",
        "imageHeight",
        "imageSize",
        "imageType",
        "imageWidth",
        "ingestedAt",
        "objStorePath",
        "objectCount",
        "objectTypes",
        "place.altitude",
        "place.coordinate.x",
        "place.coordinate.y",
        "place.coordinate.z",
        "place.id",
        "place.info.lane",
        "place.info.level",
        "place.info.name",
        "place.name",
        "place.type",
        "schemaVersion",
        "sensor.altitude",
        "sensor.coordinate.x",
        "sensor.coordinate.y",
        "sensor.coordinate.z",
        "sensor.description",
        "sensor.id",
        "sensor.type",
        "sensorId",
        "tag",
        "timestamp",
        "uuid",
        "videoPath",
    }
)
#: ``created_after`` is not a mapped field: the backend special-cases it into a
#: range on ``timestamp``. Every OTHER key is prefixed verbatim.
ITS_SELECTION_KEYS = ITS_SELECTION_FIELDS | {"created_after"}

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(\|[a-z-]+)?(:-[^}]*)?\}")


def _deployed_its_job_config(path: Path) -> DeftJobConfig:
    """Build ``its_job_config`` from a deployed overlay, as the service loads it.

    The overlays declare their env vars with NO ``:-`` default (the deploy
    renderer has no such syntax), so every name referenced anywhere in the file
    has to be supplied or the render fails before the block is reachable. The
    values are irrelevant to what is asserted — only the literal filters are.
    """
    raw = path.read_text(encoding="utf-8")
    environ = {
        m.group(1): "true" if m.group(2) == "|bool" else "x" for m in _PLACEHOLDER.finditer(raw)
    }
    rendered = render_environment_values(yaml.safe_load(raw), environ=environ)
    return build_job_config("its_job", rendered["its_job_config"])


@pytest.fixture(autouse=True)
def _deft_backend(monkeypatch):
    """Let the YAML's own ``backend.kind`` decide, not the ambient env."""
    monkeypatch.delenv("BACKEND", raising=False)


@pytest.fixture(scope="module")
def shipped() -> Settings:
    """The shipped deployment config, loaded exactly as the process loads it.

    ``AOI_SELECTION_TAG`` is supplied because the shipped file declares it with
    NO ``:-`` default, so the renderer fails the load outright without it — the
    deliberate contract pinned by ``test_shipped_deft_config_requires_selection_tag``.
    Set on ``os.environ`` rather than via ``monkeypatch`` because this fixture is
    module-scoped and ``monkeypatch`` is function-scoped. Clear
    ``DFW_ITS_MODEL_NAME`` inside the patch so an operator's ambient override
    cannot alter assertions about the shipped fallback.
    """
    with mock.patch.dict(
        os.environ,
        {"AOI_SELECTION_TAG": "aoi1", "DFW_ITS_MODEL_NAME": ""},
    ):
        return Settings.from_yaml(SHIPPED_CONFIG)


@pytest.fixture(scope="module")
def its(shipped: Settings) -> AOIJobConfig:
    return shipped.job_configs["its_job"]


# --------------------------------------------------------------------------- #
# 1. its_job_config resolves out of job_configs, as the deployment default
# --------------------------------------------------------------------------- #


def test_its_ships_as_the_deployment_default_for_its_own_job_type(shipped):
    """The one assertion that makes a config_name-less ITS submission possible.

    ``src/tasks/tasks.py`` resolves a job with no ``config_name`` through
    ``settings.job_configs.get(job_type)`` — the ONLY reader — so an ``its_job``
    submission dies in the worker with ``Unsupported job type: its_job`` unless
    the shipped YAML declares an ``its_job_config:`` block. Registering the job
    type, widening every gate and looping the loader over the registry all get
    you exactly nowhere without this block; it is the last link in the chain and
    the only one that lives in a data file rather than in code.

    Both DEFT defaults are asserted together on purpose: ITS getting its own job
    type must not cost AOI its deployment default.
    """
    assert set(shipped.job_configs) == {"aoi_job", "its_job"}
    assert shipped.job_configs["aoi_job"].use_case == "aoi"
    assert shipped.job_configs["its_job"].use_case == "its"


def test_a_config_name_less_submission_resolves_against_the_shipped_config(shipped):
    """Phase 1's exit criterion, driven through the reader that actually broke.

    ``POST /jobs {"job_type": "its_job"}`` with no ``workload_id``, no
    ``client_id`` and no ``config_name`` passes every API gate and then lands
    here, in the worker. Without an ``its_job_config:`` block this raised
    ``Unsupported job type: its_job`` — surfaced to the operator (tasks.py wraps
    it) as ``Failed to load configuration 'None'``, a message naming a
    ``config_name`` they never sent.

    The DEFT trigger path enters through the same door: ``_dispatch_aoi_job``
    passes ``config_name=trigger.config_name``, which is legitimately ``None``
    for the whole DEFT family.
    """
    from unittest.mock import patch

    from src.tasks import tasks as tasks_mod

    with patch.object(tasks_mod, "settings", shipped):
        for job_type in ("aoi_job", "its_job"):
            resolved = tasks_mod.load_job_configuration(job_type, None)
            assert resolved is shipped.job_configs[job_type]
        assert tasks_mod.load_job_configuration("its_job", None).use_case == "its"


def test_its_default_is_a_real_deft_job_config(its):
    """ITS has its own JOB TYPE but no schema of its own — ``its_job`` and
    ``aoi_job`` are the same class, differing only in the ``use_case`` they
    carry at runtime."""
    assert isinstance(its, AOIJobConfig)  # == DeftJobConfig
    assert its.use_case == "its"
    # The MLflow registry key: change it and the flywheel resolves its baseline
    # from a different @production alias and registers candidates elsewhere.
    #
    # It is also the SERVING catalog key. The model builder looks this exact
    # string up in rtvi_cv/profiles/models.yaml and refuses an unknown one
    # ("unsupported model 'x'; configured models: GDINO") before it stages or
    # exports anything — so the mismatch surfaces at the `pull` gate, after a
    # full training run has already cleared the offline gate. Pinned to the
    # catalog's spelling so the two keys cannot drift apart silently.
    assert its.model_name == "GDINO"
    # This value rides as PullRequest.format and names the artifact DEFT HANDS
    # OVER, not the format the serving stack ends up running. DEFT publishes a
    # training checkpoint, so "pth" is the truthful value: the Model Manager
    # stages it as model.pth and the builder selects the `gdino_pth` handler off
    # that extension, which exports ONNX and compiles the engine itself.
    #
    # NOT "tensorrt", which would tell the backend not to build at all and
    # expect a prebuilt .engine pinned to one GPU/driver/TensorRT triple. Typed
    # on DeftJobConfig so it survives the JobConfig round trip, and a member of
    # the Model Manager's ModelFormat enum so the `pull` gate accepts it.
    assert its.model_format == "pth"
    # The value is only legal if the Model Manager's PullRequest.format enum
    # admits it; a mismatch is a 422 at the `pull` gate, after a full training
    # run has already cleared the offline gate. Read from source rather than
    # imported — the Model Manager's top-level `src` package collides with
    # DFW's, so importing it here would shadow the module under test.
    model_format_enum = (Path(__file__).parents[3] / "model-manager/src/api/schemas.py").read_text()
    assert 'ModelFormat = Literal["onnx", "pth", "tensorrt", "triton"]' in model_format_enum


def test_its_model_name_can_be_overridden_for_an_rt_cv_contract() -> None:
    """Deployments can align the ITS registry key with RT-CV without editing YAML."""
    with mock.patch.dict(
        os.environ,
        {"AOI_SELECTION_TAG": "aoi1", "DFW_ITS_MODEL_NAME": "rt-cv-its-model"},
    ):
        settings = Settings.from_yaml(SHIPPED_CONFIG)

    assert settings.job_configs["its_job"].model_name == "rt-cv-its-model"


def test_created_after_is_the_only_facet_that_ships(its):
    """``created_after`` rides FLAT and alone, with no facet beside it.

    It is adopted verbatim because SnapshotSelectionConfig refuses to load
    without it (the /v1/snapshot contract requires it), so an ITS config that
    omitted it would kill the process at startup.

    Nothing else ships. ``jobType`` used to, on the theory that it separated ITS
    rows from AOI ones inside a shared catalogue — but ``use_case: its`` already
    routes to the ITS catalogue, so there are no AOI rows for it to separate,
    and the field is not in that catalogue's index template either. Checked
    against a live 24,593-document ITS index: zero documents carry it.
    """
    filters = its.selection.filters
    assert filters["created_after"] == "1970-01-01T00:00:00Z"
    assert set(filters) == {"created_after"}


def test_no_facet_ships_that_the_index_template_does_not_define(its):
    """A facet name outside the index template selects nothing, and says success.

    ``compile_selection`` turns every selection key into a term filter on
    ``metadata.content_metadata.<key>``, and that index template is
    ``dynamic: strict``. The selection object itself is OPEN, so a name outside
    the set is ACCEPTED rather than rejected: the snapshot is created, the status
    reads ready, and the training pool is empty. Nothing in the run reports a
    problem.

    Driven off the WHOLE mapped set rather than a list of the names that once
    shipped wrong, because the next wrong name will be a different one. The four
    that did ship are named underneath anyway: they are asserted ABSENT, not
    merely outside the set, so a revert has to argue with a named test rather
    than slip back in as a plausible-looking default.
    """
    assert set(its.selection.filters) <= ITS_SELECTION_KEYS
    for invented in ("cameras", "sites", "event_class", "sample_fps"):
        assert invented not in its.selection.filters


def test_the_mapped_field_set_is_the_one_the_index_template_really_declares():
    """:data:`ITS_SELECTION_FIELDS` is a COPY of a file in another repo.

    A copy that nobody re-reads is how the guard above stops guarding: the
    template gains a field, the copy does not, and a legitimate new facet fails
    a test — or worse, the template drops one and the copy keeps blessing it.
    Re-derived from the template itself whenever the ITS repo is checked out
    beside this one, which is the layout the deployment uses.
    """
    if not SC_INDEX_TEMPLATE.is_file():
        pytest.skip(f"ITS index template not checked out at {SC_INDEX_TEMPLATE}")

    mappings = json.loads(SC_INDEX_TEMPLATE.read_text(encoding="utf-8"))["template"]["mappings"]
    content_metadata = mappings["properties"]["metadata"]["properties"]["content_metadata"]
    assert _mapped_paths(content_metadata["properties"]) == ITS_SELECTION_FIELDS


def test_the_deployed_overlays_ship_no_facet_the_index_template_rejects():
    """The files the SERVICES mount, which is where the bad facet actually shipped.

    ``config/config.deft.yaml`` is this repo's reference; each deployed service
    mounts its OWN overlay, and no test read those at all — so an ITS selection
    carrying ``tag: "its-small-200b"`` (a real field name with a value no
    producer stamps) could sit in all four of them while every test here stayed
    green. Asserted on the PARSED filters rather than on file text: the overlays
    delete the line where the reference comments it, and both are correct.

    The four services run one deployment, so their ITS selections must also be
    identical to each other — a per-service selection is a flywheel training on
    a different pool than the one its API reports.
    """
    if not DEPLOYED_CONFIGS:
        pytest.skip("deployed service-config overlays are not checked out beside this repo")

    selections = {}
    for path in DEPLOYED_CONFIGS:
        filters = _deployed_its_job_config(path).selection.filters
        assert set(filters) <= ITS_SELECTION_KEYS, path
        # A wrong VALUE on a right field name empties the pool just as silently
        # as a wrong name, and `tag` is the one an operator reaches for first.
        assert "tag" not in filters, path
        assert filters["created_after"]
        selections[path.parent.parent.name] = filters

    assert len({json.dumps(f, sort_keys=True) for f in selections.values()}) == 1, selections


def test_the_narrowing_knob_that_ships_commented_is_one_the_index_defines(its):
    """``tag`` is discoverable-but-unset; ``location``/``machineId`` are gone.

    A narrowing knob has to survive two separate ways of being wrong. A wrong
    NAME is accepted and matches nothing; a wrong VALUE is accepted and matches
    nothing. ``location``/``machineId`` shipped commented on the belief they were
    real facets held back only for the second reason — but the live ITS index
    defines neither, so uncommenting one would have emptied the pool no matter
    what value was filled in.

    ``tag`` is in the template and is what the ITS loader stamps on an ingest, so
    it takes their place: named and explained for discoverability, commented
    because the value is a deployment's own.
    """
    assert "location" not in its.selection.filters
    assert "machineId" not in its.selection.filters
    assert "tag" not in its.selection.filters
    yaml_text = SHIPPED_CONFIG.read_text()
    assert "# location:" not in yaml_text
    assert "# machineId:" not in yaml_text
    assert "# tag:" in yaml_text


def test_the_media_retention_hazard_is_recorded_even_though_no_facet_ships(its):
    """The ITS catalogue outlives its media, and the file has to keep saying so.

    A retention sweep deletes frames and leaves the metadata rows behind, so a
    pure time-window selection mixes rows that still have pixels with rows that
    only remember having had them. Training on the second kind fails in the worst
    available shape: the snapshot is created, every status reads ``ready``, DEFT
    mirrors the prefix, and the run trains on whatever survived — with no error
    and no number anywhere saying how much that was. DFW moves data by reference
    and never opens an object, so the selection is the only place this can be
    caught at all.

    But the facet NAME is a guess, and a guessed name in the deployment default
    selects an empty pool rather than a smaller one (the selection object is
    open, so an unrecognised key is accepted and matches nothing). So the
    line ships COMMENTED OUT with its rationale intact: the hazard stays on the
    record for whoever agrees the name with the store team, without a fabricated
    key being live in the meantime. This pins both halves — no live facet, and
    the reasoning not quietly deleted along with it.
    """
    assert "media_present" not in its.selection.filters
    yaml_text = SHIPPED_CONFIG.read_text()
    assert "# media_present: true" in yaml_text
    assert "MEDIA RETENTION" in yaml_text


def test_the_kpi_set_is_not_minted_from_storage_and_nothing_static_is_pinned(its):
    """ITS does NOT mint its KPI set, and pins no locator either.

    The two fields are a BRANCH, not a pair: the handler only calls
    ``_pull_kpi_set`` when ``pull_from_storage`` is true, and ``start_run``
    prefers the pulled prefix over ``uri`` — so shipping both would leave one of
    them dead while it sits in the file looking authoritative. ``uri`` is
    therefore asserted as ``None``, not as some harmless-looking default.

    ``None`` and not the empty string: ``uri: ""`` would read the same to a
    human and reach ``start_run`` as a falsy value that only survives because of
    a trailing ``or None``. The ``|nullable-string`` render is what makes the
    absent env var an actual YAML null.

    Why OFF, where ``aoi_job_config`` has it ON: the mint is AOI-SHAPED.
    ``POST /v1/kpi`` sends a hardcoded ``{'tag': 'kpi'}`` selection
    (``StorageBackendClient.create_kpi_set``, which REFUSES any other), so no
    config surface can make it ITS-shaped. Against a store that does not carry
    that tag for ITS records the pull returns an EMPTY set rather than an error
    — the offline gate then judges the candidate on nothing while every stage
    reports success. Off by default, DEFT carves the kpi split out of the
    training snapshot instead: unfrozen, but real.

    ``DFW_ITS_KPI_SET_URI`` is the way to a frozen set, which is what makes two
    runs strictly comparable. It must name ONE object — a ``.tar.gz`` of
    ``images/<site>/`` + ``labels/`` + ``mapping.yaml`` — not a prefix: a prefix
    delivers the manifest mirror with no labels, because the KITTI ground truth
    rides in record metadata and never becomes objects of its own.
    """
    assert its.kpi.pull_from_storage is False
    assert its.kpi.uri is None


def test_a_mistyped_model_format_is_refused_at_config_load() -> None:
    """The whole point of the closed set: fail at startup, not at the `pull` gate.

    ``model_format`` is validated by the Model Manager, not by DFW, so a typo in
    a free-form ``str`` field would load cleanly, survive selection, mining and a
    full training run, clear the offline gate, and only then 422 — the most
    expensive place in the pipeline to discover a four-character mistake.

    Typed as a closed set, the same typo cannot leave the config loader.
    """
    selection = {"mode": "query", "filters": {"created_after": "1970-01-01T00:00:00Z"}}
    for legal in ("onnx", "pth", "tensorrt", "triton"):
        assert (
            DeftJobConfig(
                use_case="its",
                model_name="GDINO",
                model_format=legal,
                selection=selection,
            ).model_format
            == legal
        )

    with pytest.raises(ValidationError):
        DeftJobConfig(
            use_case="its",
            model_name="GDINO",
            model_format="pht",  # transposed
            selection=selection,
        )


def test_the_deft_block_is_profile_mode_plus_the_sanctioned_overrides(its):
    """A named DEFT profile owns the architecture; `overrides` is how it bends.

    Restating a profile value as a SIBLING of ``profile`` would not merge with
    it, it would fight it, and which side won would depend on DEFT's own
    precedence rather than on anything visible in the config. ``overrides`` is
    the one sanctioned channel, so the block ships exactly these two keys and
    everything tuned rides inside the second.
    """
    assert set(its.deft) == {"profile", "overrides"}
    assert its.deft["profile"] == "smart-city-object-detection"


def test_the_overrides_ship_live_and_carry_the_mandatory_checkpoint(its):
    """``deft.overrides`` bends the profile, and one override is near-mandatory.

    ``model.checkpoint`` is not really optional in practice: the profile's
    fallback baseline names ITS's own bucket, while the Storage Backend presigns
    ONLY keys inside the bucket it owns. A deployment whose object store differs
    from the profile's must repoint it or the run dies in ``materialize`` with
    "Storage Backend returned no URL" — a presign refusal, which reads nothing
    like the missing-file it resembles.

    These values shipped as a COMMENTED shape until a live run settled them, so
    they are pinned here as the validated set rather than as illustration.

    The checkpoint bucket must match this deployment's DFW_OBJECT_STORE_URI /
    AWS_BUCKET: the backend strips only its OWN bucket from a URI, so a second
    bucket's name folds into the key and fails the prefix allowlist with a
    misleading "key must start with..." error.
    """
    overrides = its.deft["overrides"]
    assert overrides["training"] == {"epochs": 5}
    assert overrides["mining"] == {"multiplier": 3}
    assert overrides["classes"] == {
        "road_sign": 0.22,
        "car": 0.65,
        "person": 0.05,
        "bicycle": 0.5,
    }
    assert overrides["model"]["checkpoint"] == "s3://its-samples/models/baseline/grounding_dino.pth"

    yaml_text = SHIPPED_CONFIG.read_text()
    assert "presigns only keys inside the bucket it owns" in yaml_text


def test_the_offline_gate_is_one_detection_constraint(its):
    """``far`` is a defect-classification rate; it means nothing for a detector.

    And there is exactly ONE constraint. Every constraint is an independent bet
    that DEFT emits a key by that exact name, and every one of them can reject
    every candidate forever on its own (an unfound metric is unmet — correctly,
    fail-closed). ``macro_ap`` is read off ``_collect_kpi_metrics`` in
    deft-service ``app/execution/its_adapter.py`` — the mean of the per-class
    ``AP`` column, i.e. AP at IoU 0.50. That source MR is still a draft, so the
    key is confirmed-but-not-frozen and one bet is still the right number.
    """
    constraints = {c.metric: (c.op, c.threshold) for c in its.eval.kpi_target}
    assert constraints == {"macro_ap": (">=", 0.55)}
    assert "FAR_pct" not in constraints


def test_online_eval_is_off_for_v1_and_says_so_explicitly(its):
    """§6.4 defers the online gate; ``false`` has to be WRITTEN, not implied.

    ``enabled`` is tri-state precisely so "unstated" cannot mean "opted out":
    skipping the online gate is fail-open, and a dropped line must never be able
    to do it. So assert the literal ``False``, not the derived ``online_enabled``
    — the latter is also False for an unstated field, which is the case this
    assertion exists to exclude.

    The tuning that used to sit beside it (online_target / min_sample_size /
    max_observation_window_s / on_timeout) is GONE rather than parked behind the
    flag: numbers nothing reads, describing a gate whose v2 shape is unsettled,
    imply a decision nobody has made.
    """
    harness = its.promotion_policy.eval_harness
    assert harness.enabled is False
    assert harness.online_enabled is False
    assert harness.online_target == []
    assert harness.thresholds == {}


def test_no_automatic_rollback_because_nothing_produces_a_verdict(its):
    """The flag acts only on an ONLINE regression verdict.

    With the online gate off there is no verdict for it to act on, so ``true``
    would advertise a safety net that cannot fire — worse than no net, because
    an operator would stop watching for what it claims to catch.
    """
    assert its.promotion_policy.auto_rollback_on_regression is False


def test_the_v1_ladder_is_pull_human_full(its):
    """THE v1 posture, in the three values that compose it.

    ``pull -> pending_approval -> full_switch``. Each of the three is separately
    capable of silently changing what a rollout does:

    * ``shadow: false`` — a soak stage that a fresh stack CANNOT run. A shadow is
      relative to something already serving, and with no default version the
      Model Manager refuses the routing write rather than install the candidate
      as the live primary by accident, so the deploy gate fails
      ``registry_unreachable`` on every attempt. Off is the honest first-run
      posture; flip it on once a version is promoted and there is something to
      shadow. Until then the human at ``hitl.full`` is the whole soak.
    * ``canary: false`` — the ITS serving stack has no partial-traffic routing,
      so a canary stage is not a slower rollout, it is a request the stack
      cannot honour.
    * ``hitl.full: true`` — the ONLY judgement before production once the
      offline gate has passed, because the online gate is deferred.
      Flipping this to ``false`` would auto-promote every ITS candidate that
      cleared the offline gate, with nothing anywhere reporting that a decision
      had been skipped. It is the single most dangerous edit in the block.

    Driven end to end in ``tests/unit/promotion/test_its_e2e_promotion.py``;
    pinned here on the file the deployment actually loads.
    """
    policy = its.promotion_policy
    assert policy.deployment_stages.shadow is False
    assert policy.deployment_stages.canary is False
    assert policy.hitl.full is True
    # The cheap gates stay automatic — a human at the pull would be asked to
    # approve a change no traffic can see.
    assert policy.hitl.pull is False
    assert policy.hitl.deploy is False
    # `hitl.canary: true` is DEAD CONFIG on a disabled stage, carried
    # deliberately so re-enabling the stage restores the approval that used to
    # guard it. Pinned so that "why is this true?" has an answer in the tree.
    assert policy.hitl.canary is True


def test_no_canary_ladder_is_shipped_at_all(its):
    """No dead ladder left behind, and no numbers to mistune.

    The YAML block is absent entirely. The ``min_length`` rule rejects an
    explicitly EMPTY ``steps`` list (a ladder that would ask a serving backend
    for a 100% "canary" and be refused); it never required the block to be
    present, so the "it must stay non-empty" reading that kept three unread
    numbers in the file was simply wrong.

    Asserted on ``model_fields_set``, NOT on the value. Comparing the value to
    the model default (or to a literal) cannot see this regression at all: the
    default IS ``[5, 25, 50]`` and the AOI block ships exactly ``steps: [5, 25,
    50]``, so pasting AOI's canary_steps block into ``its_job_config`` — the
    precise "dead ladder left behind" this test forbids — left the value
    identical and the assertion green. ``model_fields_set`` is the only thing
    that distinguishes "the file states nothing here" from "the file states the
    default", which is the claim in the name.
    """
    assert "canary_steps" not in its.promotion_policy.model_fields_set
    # The sibling block DOES state it, so the assertion above is discriminating
    # rather than true of every config.
    assert its.promotion_policy.canary_steps.steps == [5, 25, 50]  # the untouched default


def test_the_snapshot_wait_budgets_a_video_decode(its):
    """An ITS snapshot DECODES VIDEO before it reports ready.

    ``POST /v1/snapshot`` obliges the ITS store to turn footage into sampled
    frames + labels, because DEFT has no video-native input at all. That is real
    work an inspection snapshot never does, and giving up early does not merely
    fail the run — it abandons a snapshot the store is still building.
    """
    assert its.snapshot_poll_max_attempts == 120
    # 120 x the 5s default = 600s, double the AOI-shaped default of 60.
    assert its.snapshot_poll_max_attempts * its.snapshot_poll_interval_s == 600.0


def test_the_swap_settle_budget_is_a_different_clock_and_stays_at_90(its):
    """The raised poll budget went on the snapshot knob, and ONLY on it.

    The design writes a single ``poll_max_attempts: 120`` in a flat block that
    has one clock. This tree has two, and they budget unrelated waits:

    * ``snapshot_poll_max_attempts`` x ``snapshot_poll_interval_s`` (5s) — the
      ``POST /v1/snapshot`` wait. This is where ITS is genuinely slower: the
      store DECODES VIDEO before it reports ready.
    * ``promotion_policy.poll_max_attempts`` x ``poll_interval_s`` (10s) — the
      SWAP-SETTLE budget, after a routing change has already been issued.

    Nothing about ITS makes a routing swap settle more slowly, so raising the
    second one buys nothing and costs something real: it is the interval after
    which a swap that never lands is finally reported, so a larger number is
    strictly a longer silence. Pinned as an equality (not ``!= 120``) because the
    claim is "this value was considered and left alone", which a bare inequality
    would not survive a later edit to 100.
    """
    policy = its.promotion_policy
    assert policy.poll_max_attempts == 90
    assert policy.poll_interval_s == 10.0
    assert policy.poll_max_attempts * policy.poll_interval_s == 900.0


# --------------------------------------------------------------------------- #
# 2. one Storage Backend, declared for every use case that runs against it
# --------------------------------------------------------------------------- #


def test_every_shipped_job_config_declares_its_own_store(shipped):
    """The two halves of the file must agree, and only this test can see both.

    ``use_case_endpoints`` is exhaustive once non-empty, so a ``use_case`` named
    in a ``*_job_config:`` block with no matching entry is a deployment that
    hard-fails at its first snapshot. The two blocks sit 150 lines apart and
    nothing else reads them together — adding a third job block and forgetting
    the endpoint line is exactly the edit this catches.
    """
    deft_config = shipped.system_config.deft_config
    declared = sorted(config.use_case for config in shipped.job_configs.values())
    assert declared == ["aoi", "its"]
    # Subset, not equality: a use case registered in code (src/backends/deft/
    # eval.py) and reached through a named Mongo config needs a map entry even
    # with no YAML job block, or its first snapshot hard-fails. The direction
    # that matters is the one that catches the forgotten line: every job block
    # must be in the map.
    assert set(declared) <= set(deft_config.use_case_endpoints)
    # Resolution actually succeeds for each — membership alone would not prove
    # the entries validate.
    for use_case in declared:
        assert deft_config.for_use_case(use_case) is not None


def test_aoi_still_resolves_to_the_top_level_endpoints_it_always_had(shipped):
    """Adding ITS must not move AOI, and ``aoi: {}`` is how that is stated.

    The map is no longer empty, so "AOI is unmoved" can no longer be argued from
    the map's emptiness — it has to be read off the resolution. ``aoi: {}``
    declares AOI without overriding anything, so ``for_use_case`` still
    short-circuits to SELF: identity, not equality, so an AOI caller cannot
    observe even a different object than it did before ITS landed.

    ``None`` resolves the same way — a caller that names no use case predates
    the routing entirely and has only one store to reach.
    """
    deft_config = shipped.system_config.deft_config

    assert deft_config.for_use_case("aoi") is deft_config
    assert deft_config.for_use_case(None) is deft_config
    # The entry exists (so the guard passes) but overrides nothing (so the
    # resolution is a no-op). Both halves matter; either alone is a different
    # config.
    assert deft_config.endpoints_for("aoi") is not None
    assert deft_config.endpoints_for("aoi").model_dump(exclude_none=True) == {}

    assert deft_config.storage_backend_url == "http://storage-backend:8080"
    assert deft_config.object_store_uri == "s3://aoi-artifacts"


def test_its_shares_the_one_storage_backend_and_says_so_by_declaring_nothing(shipped):
    """ITS resolves to the SAME store as AOI, and the empty entry is the claim.

    DFW is deployed per use case, so a deployment fronts exactly one Storage
    Backend regardless; and the Storage Backend is growing multi-use-case
    support, so a second host is not what separates ITS data — prefixes within
    the shared bucket are. ``its: {}`` states that, and the assertion is
    IDENTITY: ``for_use_case`` short-circuits to self when an entry overrides
    nothing, so a copy here would mean some field was quietly reintroduced.

    The credential is asserted too, for the same reason it used to be asserted
    as different: unset means INHERIT, so a half-written override is the one
    shape that sends this bearer somewhere it was not issued for.
    """
    deft_config = shipped.system_config.deft_config
    its = deft_config.for_use_case("its")

    assert its is deft_config
    assert its.storage_backend_url == "http://storage-backend:8080"
    assert its.object_store_uri == "s3://aoi-artifacts"
    assert its.storage_api_key_env == "STORAGE_BACKEND_API_KEY"


def test_the_its_entry_survives_as_an_empty_declaration(shipped):
    """The empty entry is load-bearing, and deleting it is the tempting edit.

    It overrides nothing, so it reads as dead weight — but the map is EXHAUSTIVE
    once non-empty, so removing ``its`` does not restore inheritance, it makes
    ``for_use_case("its")`` raise and refuses every ITS run at snapshot, presign
    and promotion. Both halves are pinned: the entry EXISTS (guard passes) and
    it is EMPTY (resolution is a no-op). Either alone is a different config.
    """
    deft_config = shipped.system_config.deft_config

    assert "its" in deft_config.use_case_endpoints
    assert deft_config.endpoints_for("its") is not None
    assert deft_config.endpoints_for("its").model_dump(exclude_none=True) == {}


def test_an_undeclared_use_case_cannot_run_against_the_shipped_config(shipped):
    """Fail-closed, proved on the file the deployment actually loads.

    With the map populated, a job config naming an undeclared use case is
    refused at endpoint resolution instead of quietly reading AOI's store. This
    is the cost of the populated map and it is deliberate: adding a use case now
    requires adding a line, and forgetting is loud.
    """
    deft_config = shipped.system_config.deft_config
    with pytest.raises(ValueError, match="use_case_endpoints"):
        deft_config.for_use_case("some_new_use_case")


# --------------------------------------------------------------------------- #
# the POST payload example must stay in step with the YAML
# --------------------------------------------------------------------------- #


def test_the_example_payload_matches_the_shipped_default_config(its):
    """``config/its_config_example.json`` is the same config via POST /api/configs.

    Two doors to one config is fine; two DIFFERENT configs behind one name is
    not — an operator who follows the example would get a job that behaves
    differently from the one the deployment declares.
    """
    payload = json.loads(ITS_EXAMPLE_JSON.read_text(encoding="utf-8"))
    # ITS has its own job type now; the example must teach the current shape,
    # not the superseded "ride aoi_job with use_case: its" one.
    assert payload["config_type"] == "its_job"
    # Built through the same door POST /api/configs uses, so the example is
    # proven acceptable rather than merely proofread.
    built = build_job_config(payload["config_type"], payload["config_data"])
    assert built.model_dump() == its.model_dump()
    # ...and the two doors must CLASSIFY it the same too. aoi_job and its_job are
    # the same class, so this is decided by the stamp each builder applies, not
    # by isinstance: a YAML block under the wrong key would round-trip through
    # model_dump() identically while answering a different config_type.
    assert config_type_for(built) == config_type_for(its) == "its_job"
