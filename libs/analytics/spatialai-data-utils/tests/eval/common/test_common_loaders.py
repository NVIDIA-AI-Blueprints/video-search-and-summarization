# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Coverage supplement for ``eval.common.loaders.load_gt`` — pins the
two branches the existing detection / tracking tests don't reach:

* annotations with ``gt_names[i] is None`` are silently skipped
  (upstream filtering convention), and
* ``verbose=True`` prints the post-load summary line.
"""

import pytest

from spatialai_data_utils.eval.common.loaders import load_gt
from spatialai_data_utils.eval.detection.data_classes import DetectionBox
from spatialai_data_utils.eval.tracking import data_classes as tracking_dc
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingConfig,
    TrackingMetricData,
)


def _make_sample(*, token="A__0", names, n_boxes=None):
    """Build one sample dict consumable by ``load_gt``.

    The annotation count is inferred from ``names`` so callers can pass
    ``[None, "Person"]`` to exercise the skip branch."""
    n = n_boxes if n_boxes is not None else len(names)
    return {
        "token": token,
        "scene_name": "A",
        "frame_idx": 0,
        "gt_boxes": [[0.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0] for _ in range(n)],
        "gt_names": names,
        "gt_velocity": [[0.0, 0.0] for _ in range(n)],
        "instance_inds": list(range(n)),
    }


def test_load_gt_skips_annotations_with_none_class_name():
    """``gt_names[i] is None`` indicates the annotation was filtered
    out upstream; ``load_gt`` must silently skip it instead of
    instantiating a box with ``detection_name=None``."""
    samples = [_make_sample(names=[None, "Person", None, "Person"])]
    out = load_gt(samples, DetectionBox, verbose=False)
    assert len(out.boxes["A__0"]) == 2  # two None entries dropped


def test_load_gt_verbose_prints_summary_line(capsys):
    """``verbose=True`` triggers the ``Loaded ground truth ...``
    summary print at the end."""
    samples = [_make_sample(names=["Person"])]
    load_gt(samples, DetectionBox, verbose=True)
    out = capsys.readouterr().out
    assert "Loaded ground truth" in out
    assert "1 samples" in out


def test_load_gt_for_tracking_box_path():
    """Exercise the ``box_cls is TrackingBox`` branch (different
    constructor path than DetectionBox, requires TRACKING_NAMES
    populated via a TrackingConfig instance)."""
    saved = list(tracking_dc.TRACKING_NAMES)
    saved_nelem = TrackingMetricData.nelem
    try:
        TrackingConfig(
            tracking_names=["Person"],
            pretty_tracking_names={"Person": "P"},
            class_range={"Person": 50},
            dist_fcn="center_distance",
            dist_th_tp=1.0, min_recall=0.1,
            max_boxes_per_sample=100,
            metric_worst={}, num_thresholds=4,
        )
        samples = [_make_sample(names=["Person"])]
        out = load_gt(samples, TrackingBox, verbose=False)
        boxes = out.boxes["A__0"]
        assert len(boxes) == 1 and isinstance(boxes[0], TrackingBox)
        # tracking_id was coerced to str even though instance_inds[0] = 0
        assert boxes[0].tracking_id == "0"
    finally:
        tracking_dc.TRACKING_NAMES = saved
        TrackingMetricData.nelem = saved_nelem


def test_load_gt_rejects_unsupported_box_class():
    """Any ``box_cls`` other than DetectionBox / TrackingBox raises
    NotImplementedError (the only branch left to cover in the
    dispatcher head)."""
    class _Other:
        pass
    with pytest.raises(NotImplementedError, match="Invalid box_cls"):
        load_gt([], _Other, verbose=False)


# ===================================================================
# load_gt instance_inds optionality
# ===================================================================
#
# After the ``eval/common/loaders.py`` cleanup, the detection branch of
# ``load_gt`` no longer dereferences ``instance_inds`` (the tracking
# branch still requires it).  This pin guards against an accidental
# re-introduction of the unconditional ``sample["instance_inds"]`` read.

class TestLoadGtInstanceInds:
    """Detection callers don't need ``instance_inds``; tracking callers do."""

    def _seed_class_lists(self, monkeypatch):
        """Temporarily set the global class lists so the box constructors'
        asserts pass. Uses ``monkeypatch.setattr`` so the original values are
        restored automatically at the end of the test — earlier inline
        mutation leaked state into subsequent tests.

        ``DETECTION_NAMES`` / ``TRACKING_NAMES`` don't pre-exist on the
        modules (the constructors lazily look them up via ``getattr``), so we
        pass ``raising=False`` to let monkeypatch *create* the attribute and
        delete it on teardown — strictly cleaner than leaving an empty list
        behind for the next test."""
        import spatialai_data_utils.eval.detection.data_classes as ddc
        import spatialai_data_utils.eval.tracking.data_classes as tdc
        monkeypatch.setattr(ddc, "DETECTION_NAMES", ["Person"], raising=False)
        monkeypatch.setattr(tdc, "TRACKING_NAMES", ["Person"], raising=False)

    def _make_sample(self, token, n, *, with_inds=True):
        sample = {
            "token": token,
            "scene_name": "scene_1",
            "gt_boxes": [[float(i)] * 9 for i in range(n)],
            "gt_names": ["Person"] * n,
            "gt_velocity": [[0.0, 0.0, 0.0]] * n,
            "valid_flag": [True] * n,
        }
        if with_inds:
            sample["instance_inds"] = list(range(100, 100 + n))
        return sample

    def test_detection_path_does_not_require_instance_inds(self, monkeypatch):
        from spatialai_data_utils.eval.common.loaders import load_gt
        from spatialai_data_utils.eval.detection.data_classes import DetectionBox
        self._seed_class_lists(monkeypatch)
        infos = [self._make_sample("s0", 2, with_inds=False)]
        gt = load_gt(infos, DetectionBox)
        # Boxes loaded successfully without raising KeyError on instance_inds.
        assert gt.sample_tokens == ["s0"]
        assert len(gt.boxes["s0"]) == 2

    def test_tracking_path_still_requires_instance_inds(self, monkeypatch):
        from spatialai_data_utils.eval.common.loaders import load_gt
        from spatialai_data_utils.eval.tracking.data_classes import TrackingBox
        self._seed_class_lists(monkeypatch)
        infos = [self._make_sample("s0", 2, with_inds=False)]
        with pytest.raises(KeyError, match="instance_inds"):
            load_gt(infos, TrackingBox)

    def test_unknown_box_class_raises_clear_error(self):
        from spatialai_data_utils.eval.common.loaders import load_gt
        infos = [self._make_sample("s0", 1, with_inds=True)]
        with pytest.raises(NotImplementedError, match="Invalid box_cls"):
            load_gt(infos, str)
