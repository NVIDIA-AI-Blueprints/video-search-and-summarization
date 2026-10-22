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
"""The DEFT-owned registry keys, and the YAML loader that walks them.

``aoi_job`` and ``its_job`` are ONE config schema (``DeftJobConfig``) behind TWO
registry keys: ITS needs no config field AOI lacks, so it is a distinct JOB TYPE
riding the same contract. That creates two properties worth pinning, neither of
which any other module covers:

* **One source of truth.** ``DEFT_JOB_CONFIG_TYPES`` is splatted into BOTH the
  facade registry (``src.config.JOB_CONFIG_TYPES``) and the import-bootstrap
  mirror in ``src/config/base.py``, so the two cannot disagree about which DEFT
  job types exist. Hand-writing a key in either place is the drift this catches.
* **Every construction door classifies the same.** ``load_deft_settings`` builds
  configs DIRECTLY — it runs during the bootstrap window and cannot import
  ``build_job_config`` — so it is the door most likely to be forgotten when the
  stamp mechanism changes. A forgotten stamp is silent: the config still loads,
  and ``config_type_for`` simply mislabels it ``aoi_job``.

The shipped ``config/config.deft.yaml`` now carries BOTH blocks; the variants
these tests need (empty, absent, mislabelled) are written into ``tmp_path``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src.config import JOB_CONFIG_TYPES, config_type_for
from src.config.base import Settings
from src.config.deft import DEFT_JOB_CONFIG_TYPES, AOIJobConfig, DeftJobConfig
from src.config_renderer import load_rendered_yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = REPO_ROOT / "config" / "config.deft.yaml"


@pytest.fixture(autouse=True)
def _deft_backend(monkeypatch):
    """Let the YAML's own ``backend.kind`` decide, not the ambient env."""
    monkeypatch.delenv("BACKEND", raising=False)


def _write_config(tmp_path: Path, _drop: tuple[str, ...] = (), **top_level) -> str:
    """The shipped deft config with extra top-level blocks merged in (or removed).

    Parsed and re-dumped rather than string-appended so a ``None`` block (an
    operator writing a bare ``its_job_config:``) can be expressed at all — YAML
    text concatenation cannot distinguish "absent" from "present and empty".
    ``_drop`` expresses the third state: genuinely absent, which is what a
    deployment that has not adopted a DEFT job type looks like.

    Rendered on the way in (rather than plain ``yaml.safe_load``) so the temp
    file carries concrete values: the shipped file's ``${AOI_SELECTION_TAG}``
    has no ``:-`` default, so a re-dumped copy of it would fail the SAME way at
    ``Settings.from_yaml`` time and mask what each case is actually asserting.
    """
    raw = load_rendered_yaml(SHIPPED_CONFIG, environ={"AOI_SELECTION_TAG": "aoi1"})
    for key in _drop:
        raw.pop(key, None)
    raw.update(top_level)
    path = tmp_path / "config.deft.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


ITS_JOB_CONFIG = {
    "use_case": "its",
    "model_name": "its-stream-detector",
    "selection": {"filters": {"created_after": "1970-01-01T00:00:00Z"}},
}


# --- one source of truth for the DEFT keys ----------------------------------


def test_both_deft_keys_are_the_same_schema():
    assert dict(DEFT_JOB_CONFIG_TYPES) == {"aoi_job": DeftJobConfig, "its_job": DeftJobConfig}
    assert AOIJobConfig is DeftJobConfig


def test_the_deft_sub_map_is_read_only():
    """Frozen for the same reason ``JOB_CONFIG_TYPES`` is: it is splatted into two
    registries, so a runtime mutation here would silently desync both."""
    with pytest.raises(TypeError):
        DEFT_JOB_CONFIG_TYPES["x"] = DeftJobConfig  # type: ignore[index]


def test_aoi_is_listed_before_its():
    """Insertion order is load-bearing, not cosmetic.

    ``config_type_for``'s ``isinstance`` fallback answers with the FIRST matching
    key, so an unstamped ``DeftJobConfig`` — one built outside a registry door —
    must keep classifying as ``aoi_job``, exactly as it did before ``its_job``
    existed. Reordering this map would silently reclassify every such object.
    """
    assert list(DEFT_JOB_CONFIG_TYPES) == ["aoi_job", "its_job"]


@pytest.mark.parametrize("job_type", sorted(DEFT_JOB_CONFIG_TYPES))
def test_the_registry_carries_every_deft_key(job_type):
    """``JOB_CONFIG_TYPES`` splats this map, so a DEFT key cannot go missing."""
    assert JOB_CONFIG_TYPES[job_type] is DEFT_JOB_CONFIG_TYPES[job_type]


# --- the loader walks those keys --------------------------------------------


def test_an_its_job_config_block_loads_under_its_own_job_type(tmp_path):
    """The point of ITS-R-03: a deployment can ship a default for BOTH job types.

    ``load_job_configuration`` resolves a bare ``job_type`` out of
    ``settings.job_configs``, so this block — not a Mongo round trip — is what
    makes ``POST /jobs {"job_type": "its_job"}`` work with no ``config_name``.
    """
    settings = Settings.from_yaml(_write_config(tmp_path, its_job_config=ITS_JOB_CONFIG))

    assert set(settings.job_configs) == {"aoi_job", "its_job"}
    assert settings.job_configs["its_job"].model_name == "its-stream-detector"
    # ITS did not displace AOI — they are siblings, not alternatives.
    assert settings.job_configs["aoi_job"].use_case == "aoi"


def test_yaml_loaded_configs_classify_as_the_key_they_loaded_under(tmp_path):
    """The stamp reaches the YAML door too, not just ``build_job_config``.

    ``load_deft_settings`` cannot import the facade, so it stamps directly. Were
    it to forget, both entries would still load and ``config_type_for`` would
    quietly call the ITS one ``aoi_job`` — one class, two keys, no exception.
    """
    settings = Settings.from_yaml(_write_config(tmp_path, its_job_config=ITS_JOB_CONFIG))

    for job_type, config in settings.job_configs.items():
        assert config_type_for(config) == job_type


def test_classification_follows_the_block_not_the_use_case(tmp_path):
    """job_type and use_case are DISJOINT axes.

    An ``its_job_config:`` block declaring ``use_case: "aoi"`` is legal — the job
    type picks the pipeline and the deployment default, the use case picks the
    evaluator and the store. Classification must follow the block it was
    declared in, so nothing here may sniff the payload.
    """
    settings = Settings.from_yaml(
        _write_config(tmp_path, its_job_config={**ITS_JOB_CONFIG, "use_case": "aoi"})
    )

    assert config_type_for(settings.job_configs["its_job"]) == "its_job"


def test_an_absent_block_leaves_job_configs_untouched(tmp_path):
    """A DEFT job type with no ``<job_type>_config:`` block gets no default.

    The shipped config now declares BOTH DEFT blocks, so this deletes the ITS one
    to exercise the absent case: a deployment that has not adopted ITS must load
    byte-identically to a pre-ITS one.
    """
    settings = Settings.from_yaml(_write_config(tmp_path, _drop=("its_job_config",)))

    assert set(settings.job_configs) == {"aoi_job"}


def test_the_shipped_config_declares_a_default_for_every_deft_key(tmp_path):
    """Registering ``its_job`` and looping the loader is inert without the BLOCK.

    ``settings.job_configs`` is the only place ``load_job_configuration`` looks
    for a no-``config_name`` submission, so a DEFT registry key with no shipped
    ``<job_type>_config:`` block is a job type the API accepts and the worker
    then rejects with ``Unsupported job type``. Asserted over the registry rather
    than over a literal pair so a third DEFT key cannot ship half-wired.
    """
    settings = Settings.from_yaml(_write_config(tmp_path))

    assert set(settings.job_configs) == set(DEFT_JOB_CONFIG_TYPES)


@pytest.mark.parametrize("block", ["aoi_job_config", "its_job_config"])
def test_an_empty_block_counts_as_absent(tmp_path, block):
    """A bare ``its_job_config:`` key parses to ``None`` and is skipped.

    Chosen over the previous ``in config_data`` membership test because ``None``
    would otherwise reach ``from_dict`` as ``cls(**None)`` and abort STARTUP with
    a ``TypeError`` naming no block at all. Applies to ``aoi_job_config`` too:
    emptying it now means "absent" rather than "crash".
    """
    settings = Settings.from_yaml(_write_config(tmp_path, **{block: None}))

    assert block.removesuffix("_config") not in settings.job_configs


def test_the_deft_blocks_load_during_package_import(tmp_path):
    """The real bootstrap path, in a fresh interpreter.

    ``import src.config`` builds ``settings`` from ``DFW_CONFIG_PATH`` *inside*
    the facade's own import. That is the one moment the facade cannot be imported
    back, which is why ``JOB_CONFIG_TYPES`` lives in ``src/config/base.py``,
    imported BELOW the classes the backend loaders need. A regression here would
    not show up in any in-process test — the facade is already imported by then —
    so it is checked the only way it can be.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import src.config as c; print(sorted(c.settings.job_configs))"],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DFW_CONFIG_PATH": _write_config(tmp_path),
            "BACKEND": "deft",
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(sorted(DEFT_JOB_CONFIG_TYPES))
