# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Qwen3.5 has to carry the EVS state the code inherited from Qwen3-VL reads.

Stock vLLM 0.17.1 hard-disables video token pruning for Qwen3.5. The overlay
``qwen3_5.py`` turns it on for both the dense and the MoE class.

Neither Qwen3.5 class chains to ``Qwen3VLForConditionalGeneration.__init__``,
so nothing sets the attributes the EVS encode path reads off the model unless
``_init_evs_state`` does. A missing one fails the first clip of every EVS
session (``EVS encode failed: streaming cache miss`` or an ``AttributeError``).

Qwen3.5 also has no deepstack, but its released checkpoints ship
``deepstack_visual_indexes: []``. The Qwen3-VL test ``hasattr(...)`` is true for
that, which would send the inherited EVS functions down the deepstack branch.

Two tiers are covered:

* ``TestOverlaySourceInRepo`` reads the overlay source without importing vLLM,
  so it runs anywhere.
* ``TestInstalledOverlay`` builds a weightless model from the vLLM install and
  runs ``_init_evs_state``. It needs the overlay installed, i.e. a built
  rt-vlm image.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
OVERLAY_PATH = (
    REPO_ROOT
    / "docker"
    / "rtvi_vlm"
    / "patches"
    / "evs_vllm_public_files"
    / "model_executor"
    / "models"
    / "qwen3_5.py"
)

MODEL_CLASSES = ("Qwen3_5ForConditionalGeneration", "Qwen3_5MoeForConditionalGeneration")

# What vllm.multimodal.evs_qwen3_ops and evs_runner_ops read off the model, plus
# what the inherited Qwen3-VL methods read.
EVS_ATTRS = (
    "_tokenizer",
    "video_pruning_rate",
    "is_multimodal_pruning_enabled",
    "evs_skip_threshold",
    "evs_similarity_threshold",
    "_evs_clip_cache",
    "use_deepstack",
    "deepstack_num_level",
    "visual_dim",
    "multiscale_dim",
)

pytestmark = pytest.mark.no_gpu


def _class_def(tree: ast.Module, class_name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"{OVERLAY_PATH} defines no class {class_name}")


def _method(class_def: ast.ClassDef, name: str) -> ast.FunctionDef | None:
    for child in class_def.body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return child
    return None


def _assigned_self_attrs(func: ast.FunctionDef) -> set[str]:
    assigned: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)
    return assigned


def _calls_self_method(func: ast.FunctionDef, name: str) -> bool:
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            return True
    return False


@pytest.fixture(scope="module")
def overlay_tree() -> ast.Module:
    return ast.parse(OVERLAY_PATH.read_text(encoding="utf-8"))


class TestOverlaySourceInRepo:
    """Check the overlay that the image build copies into vLLM."""

    def test_dense_class_declares_pruning_support(self, overlay_tree):
        """The MoE class inherits the flag from the dense one."""
        dense = _class_def(overlay_tree, MODEL_CLASSES[0])
        flags = [
            node.value.value
            for node in dense.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "supports_multimodal_pruning"
                for t in node.targets
            )
            and isinstance(node.value, ast.Constant)
        ]
        assert flags == [True]

    @pytest.mark.parametrize("class_name", MODEL_CLASSES)
    def test_class_does_not_switch_pruning_off(self, overlay_tree, class_name):
        """Stock vLLM overrides these to refuse pruning; the overlay must not."""
        class_def = _class_def(overlay_tree, class_name)
        assert _method(class_def, "recompute_mrope_positions") is None
        init = _method(class_def, "__init__")
        assert init is not None
        assert "is_multimodal_pruning_enabled" not in _assigned_self_attrs(init)

    @pytest.mark.parametrize("class_name", MODEL_CLASSES)
    def test_init_sets_up_the_evs_state(self, overlay_tree, class_name):
        init = _method(_class_def(overlay_tree, class_name), "__init__")
        assert init is not None
        assert _calls_self_method(init, "_init_evs_state"), (
            f"{class_name}.__init__ never calls self._init_evs_state(), and it does "
            "not chain to Qwen3VLForConditionalGeneration.__init__, so the EVS encode "
            "path finds no clip cache on the model."
        )

    def test_init_evs_state_sets_every_evs_attribute(self, overlay_tree):
        func = _method(_class_def(overlay_tree, MODEL_CLASSES[0]), "_init_evs_state")
        assert func is not None
        missing = set(EVS_ATTRS) - _assigned_self_attrs(func)
        assert not missing, f"_init_evs_state does not set {sorted(missing)}"


def _installed_qwen3_5():
    """The installed ``qwen3_5`` module when it carries the overlay, else skip."""
    if importlib.util.find_spec("vllm") is None:
        pytest.skip("vLLM is not installed (run inside a built rt-vlm image)")
    from vllm.model_executor.models import qwen3_5

    if not hasattr(qwen3_5.Qwen3_5ForConditionalGeneration, "_init_evs_state"):
        pytest.skip("the installed vLLM does not carry the Qwen3.5 EVS overlay")
    return qwen3_5


def _bare_model(qwen3_5, class_name, monkeypatch, vision_config=None, **mm_kwargs):
    """A model with only the state ``_init_evs_state`` needs, no weights."""
    from torch import nn
    from vllm.config.multimodal import MultiModalConfig
    from vllm.transformers_utils.configs.qwen3_5 import Qwen3_5Config

    tokenizer = object()
    monkeypatch.setattr(qwen3_5, "cached_tokenizer_from_config", lambda model_config: tokenizer)
    cls = getattr(qwen3_5, class_name)
    model = cls.__new__(cls)
    nn.Module.__init__(model)
    model.config = Qwen3_5Config(vision_config=vision_config)
    model.multimodal_config = MultiModalConfig(**mm_kwargs)
    model._init_evs_state(SimpleNamespace(model_config=None))
    return model, tokenizer


@pytest.mark.parametrize("class_name", MODEL_CLASSES)
class TestInstalledOverlay:
    """Run the overlay as installed into vLLM."""

    def test_supports_multimodal_pruning(self, class_name):
        qwen3_5 = _installed_qwen3_5()
        from vllm.model_executor.models.interfaces import supports_multimodal_pruning
        from vllm.model_executor.models.qwen3_vl import Qwen3VLForConditionalGeneration

        cls = getattr(qwen3_5, class_name)
        assert supports_multimodal_pruning(cls)
        # The Qwen3-VL implementation, not an override that refuses to run.
        assert (
            cls.recompute_mrope_positions
            is Qwen3VLForConditionalGeneration.recompute_mrope_positions
        )

    def test_evs_state_with_pruning(self, class_name, monkeypatch):
        qwen3_5 = _installed_qwen3_5()
        model, tokenizer = _bare_model(
            qwen3_5,
            class_name,
            monkeypatch,
            video_pruning_rate=0.5,
            evs_skip_threshold=0.1,
            evs_similarity_threshold=0.9,
        )

        for attr in EVS_ATTRS:
            assert hasattr(model, attr), attr
        assert model._tokenizer is tokenizer
        assert model.is_multimodal_pruning_enabled is True
        assert model.video_pruning_rate == 0.5
        assert model.evs_skip_threshold == 0.1
        assert model.evs_similarity_threshold == 0.9
        assert model._evs_clip_cache is not None

    def test_evs_state_without_pruning(self, class_name, monkeypatch):
        qwen3_5 = _installed_qwen3_5()
        model, _ = _bare_model(qwen3_5, class_name, monkeypatch)

        for attr in EVS_ATTRS:
            assert hasattr(model, attr), attr
        assert model.is_multimodal_pruning_enabled is False
        assert model._evs_clip_cache is None

    # Released checkpoints (e.g. Qwen/Qwen3.5-0.8B) carry an empty list rather
    # than omitting the key.
    @pytest.mark.parametrize("vision_config", [None, {"deepstack_visual_indexes": []}])
    def test_no_deepstack(self, class_name, vision_config, monkeypatch):
        """The EVS embeddings stay ``out_hidden_size`` wide with nothing appended."""
        qwen3_5 = _installed_qwen3_5()
        model, _ = _bare_model(
            qwen3_5, class_name, monkeypatch, vision_config=vision_config, video_pruning_rate=0.5
        )

        assert model.use_deepstack is False
        assert model.deepstack_num_level == 0
        assert model.visual_dim == model.config.vision_config.out_hidden_size
        assert model.multiscale_dim == 0
