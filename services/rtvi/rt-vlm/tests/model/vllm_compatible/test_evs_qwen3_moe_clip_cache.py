######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

"""Qwen3-VL MoE must build the per-clip EVS state that the encode path reads.

``Qwen3VLMoeForConditionalGeneration`` (Qwen3-VL-30B-A3B and the other MoE
checkpoints) does not chain to ``Qwen3VLForConditionalGeneration.__init__``. It
calls ``super(Qwen3VLForConditionalGeneration, self).__init__()`` and then
repeats the dense initialisation itself, so every attribute the dense
``__init__`` sets has to be repeated there. Three EVS ones were missed:
``_evs_clip_cache``, ``evs_similarity_threshold`` and ``evs_skip_threshold``.

With no ``_evs_clip_cache`` on the model, ``prepare_evs_encode_cache`` has no
cache to seed and returns early. ``postprocess_video_embeds_evs`` then prunes
with no clip state -- visible in the logs as ``mode=rate`` where a session clip
should report ``mode=threshold`` -- and never records the pruned embeddings, so
``handle_evs_encode`` reports "no clip state for req=..." and the session layer
raises ``EVS encode failed: streaming cache miss``. That kills the first clip of
every EVS session on a MoE checkpoint, model warmup included.

The two threshold attributes are required for the same request rather than
separately: once a clip state exists, the pruning code reads them straight off
the model, so seeding only the cache would trade the cache miss for an
``AttributeError``.

The assertions read the overlay sources instead of importing vLLM, which keeps
this a pure no-GPU check that needs neither torch nor a built engine. They run
against the repo copy, and again against the copy installed into vLLM when the
tests run inside a built rtvi_vlm image -- ``apply_vllm_evs_patch.py`` copies
these files in verbatim, so the installed overlay is plain Python as well.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
OVERLAY_SRC_DIR = REPO_ROOT / "docker" / "rtvi_vlm" / "patches" / "evs_vllm_files"

DENSE_REL_PATH = "model_executor/models/qwen3_vl.py"
MOE_REL_PATH = "model_executor/models/qwen3_vl_moe.py"

DENSE_CLASS = "Qwen3VLForConditionalGeneration"
MOE_CLASS = "Qwen3VLMoeForConditionalGeneration"

# Set by the dense __init__ and read by vllm.multimodal.evs_qwen3_ops and
# vllm.multimodal.evs_runner_ops on every EVS encode request.
EVS_INIT_ATTRS = (
    "_evs_clip_cache",
    "evs_similarity_threshold",
    "evs_skip_threshold",
)

# Attributes the dense __init__ sets that the MoE __init__ is allowed to omit.
# Add a name here only once it is confirmed that the MoE path genuinely does not
# need it, otherwise the omission is the bug this module exists to catch.
MOE_MAY_OMIT: frozenset[str] = frozenset()

# Default install location used by docker/rtvi_vlm/patches/apply_vllm_evs_patch.py.
DEFAULT_INSTALLED_VLLM_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"

pytestmark = pytest.mark.no_gpu


def _class_init(path: Path, class_name: str) -> ast.FunctionDef:
    """The ``__init__`` defined directly on *class_name* in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name == "__init__":
                return child
        raise AssertionError(f"{class_name} in {path} defines no __init__")
    raise AssertionError(f"{path} defines no class {class_name}")


def _assigned_self_attrs(init: ast.FunctionDef) -> set[str]:
    """Names assigned as ``self.<name>`` anywhere in the body.

    Walks the whole body rather than its top level because the dense
    ``__init__`` builds the clip cache inside an ``if`` guarded on the pruning
    rate.
    """
    assigned: set[str] = set()
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)
    return assigned


def _assigns_call_result(init: ast.FunctionDef, attr: str, func_name: str) -> bool:
    """True when ``self.<attr> = <func_name>(...)`` appears in the body."""
    for node in ast.walk(init):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == func_name
        ):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == attr
            ):
                return True
    return False


def _chains_to_parent_init(init: ast.FunctionDef) -> bool:
    """True when the body calls ``super().__init__()`` with no arguments to ``super``.

    ``super(Qwen3VLForConditionalGeneration, self)`` starts the lookup *after*
    the dense class, which is what skips its ``__init__`` and forces the
    duplication this module checks. A bare ``super()`` would inherit that
    initialisation instead, and the duplicated assignments would no longer be
    required -- so treat either shape as correct.
    """
    for node in ast.walk(init):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "__init__"):
            continue
        owner = func.value
        if (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Name)
            and owner.func.id == "super"
            and not owner.args
        ):
            return True
    return False


def _installed_vllm_root() -> Path | None:
    """The vLLM install carrying the EVS overlay, when the tests run inside the image."""
    root = Path(
        os.environ.get("VLLM_EVS_TARGET")
        or os.environ.get("VLLM_ROOT")
        or DEFAULT_INSTALLED_VLLM_ROOT
    )
    return root if (root / MOE_REL_PATH).is_file() else None


_INSTALLED_VLLM_ROOT = _installed_vllm_root()

requires_installed_overlay = pytest.mark.skipif(
    _INSTALLED_VLLM_ROOT is None,
    reason="no EVS overlay installed into vLLM (run inside a built rtvi_vlm image)",
)


def _assert_moe_init_sets(root: Path, attr: str) -> None:
    moe_init = _class_init(root / MOE_REL_PATH, MOE_CLASS)
    if _chains_to_parent_init(moe_init):
        return
    assert attr in _assigned_self_attrs(moe_init), (
        f"{MOE_CLASS}.__init__ never assigns self.{attr}, and it does not chain to "
        f"{DENSE_CLASS}.__init__, so it does not inherit that assignment either. "
        "EVS encode then fails the first clip of every session with "
        "'EVS encode failed: streaming cache miss'."
    )


class TestOverlaySourceInRepo:
    """Check the overlay that the image build copies into vLLM."""

    def test_dense_init_still_sets_the_evs_attributes(self):
        """Keeps the MoE checks honest.

        They compare against the dense class's contract, so if the dense
        ``__init__`` ever stops setting these the MoE assertions would start
        passing for the wrong reason.
        """
        dense_init = _class_init(OVERLAY_SRC_DIR / DENSE_REL_PATH, DENSE_CLASS)

        assert set(EVS_INIT_ATTRS) <= _assigned_self_attrs(dense_init)

    @pytest.mark.parametrize("attr", EVS_INIT_ATTRS)
    def test_moe_init_sets_the_evs_attribute(self, attr):
        _assert_moe_init_sets(OVERLAY_SRC_DIR, attr)

    def test_moe_init_repeats_every_dense_assignment(self):
        """Catches the next omission, not just the EVS ones.

        The duplicated ``__init__`` is only correct while it stays in step with
        the dense one, and a rebase onto a newer vLLM is the likely moment for
        an assignment to be dropped again.
        """
        moe_init = _class_init(OVERLAY_SRC_DIR / MOE_REL_PATH, MOE_CLASS)
        if _chains_to_parent_init(moe_init):
            pytest.skip(f"{MOE_CLASS}.__init__ inherits the dense assignments")
        dense_init = _class_init(OVERLAY_SRC_DIR / DENSE_REL_PATH, DENSE_CLASS)

        missing = _assigned_self_attrs(dense_init) - _assigned_self_attrs(moe_init)

        assert missing <= MOE_MAY_OMIT, (
            f"{MOE_CLASS}.__init__ does not set {sorted(missing - MOE_MAY_OMIT)}, which "
            f"{DENSE_CLASS}.__init__ does. Repeat the assignment, or record the name in "
            "MOE_MAY_OMIT once the MoE path is confirmed not to need it."
        )

    def test_moe_init_builds_the_clip_cache(self):
        """Assigning ``_evs_clip_cache = None`` is not enough on its own.

        ``prepare_evs_encode_cache`` treats a ``None`` cache exactly like a
        missing attribute, so the MoE path also has to construct the real cache
        the way the dense class does and store it on the attribute. Calling
        ``make_clip_evs_cache()`` and dropping the result would satisfy the
        assignment check above while leaving the cache ``None``.
        """
        moe_init = _class_init(OVERLAY_SRC_DIR / MOE_REL_PATH, MOE_CLASS)
        if _chains_to_parent_init(moe_init):
            pytest.skip(f"{MOE_CLASS}.__init__ inherits the dense clip-cache setup")

        assert _assigns_call_result(moe_init, "_evs_clip_cache", "make_clip_evs_cache"), (
            f"{MOE_CLASS}.__init__ never assigns make_clip_evs_cache() to "
            "self._evs_clip_cache, so the cache stays None and every EVS encode "
            "misses it."
        )


@requires_installed_overlay
class TestOverlayInstalledInVllm:
    """Catch an image whose installed overlay predates the MoE fix."""

    @pytest.mark.parametrize("attr", EVS_INIT_ATTRS)
    def test_moe_init_sets_the_evs_attribute(self, attr):
        _assert_moe_init_sets(_INSTALLED_VLLM_ROOT, attr)
