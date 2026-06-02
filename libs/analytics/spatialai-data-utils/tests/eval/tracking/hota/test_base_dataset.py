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

"""Coverage supplement for ``hota.datasets._base_dataset._BaseDataset``
helpers — pins:

* ``get_display_name`` default (tracker name as-is),
* ``_load_simple_text_file`` raises for misconfig, zip-file branch,
  ignore-region filter, valid_filter, remove_negative_ids,
  convert_filter, file-parse + outer-file-not-found exceptions,
* ``_calculate_box_ious`` invalid box_format raise,
* ``_check_unique_ids`` raise paths for tracker + GT duplicates."""

import io
import os
import zipfile

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking.hota.datasets._base_dataset import (
    _BaseDataset,
)
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


# ---------------------------------------------------------------------------
# Concrete dataset subclass to expose the abstract base methods
# ---------------------------------------------------------------------------


class _ConcreteDataset(_BaseDataset):
    """Bare-minimum concrete subclass — implements just enough of the
    abstract interface for ``__init__`` to succeed. Most tests here
    only exercise static / class methods that don't depend on the
    instance state, so the subclass can be largely empty."""

    def __init__(self):
        super().__init__()

    # Abstract method stubs (never called in these tests).
    @staticmethod
    def get_default_dataset_config():  # pragma: no cover
        return {}

    def _load_raw_file(self, tracker, seq, is_gt):  # pragma: no cover
        return {}

    def get_preprocessed_seq_data(self, raw_data, cls):  # pragma: no cover
        return raw_data

    def _calculate_similarities(self, gt_dets_t, tracker_dets_t):  # pragma: no cover
        return np.zeros((len(gt_dets_t), len(tracker_dets_t)))


# ---------------------------------------------------------------------------
# get_display_name default
# ---------------------------------------------------------------------------


def test_get_display_name_returns_tracker_name_by_default():
    ds = _ConcreteDataset()
    assert ds.get_display_name("trkA") == "trkA"


# ---------------------------------------------------------------------------
# _load_simple_text_file — config-validation raises
# ---------------------------------------------------------------------------


def test_load_simple_text_file_raises_when_remove_negative_ids_without_id_col(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("1,1,2,3\n")
    with pytest.raises(TrackEvalException, match="id_col is not given"):
        _BaseDataset._load_simple_text_file(
            str(p), remove_negative_ids=True, id_col=None,
        )


def test_load_simple_text_file_raises_when_is_zipped_without_zip_file(tmp_path):
    """The inner ``no zip_file`` TrackEvalException gets re-wrapped by
    the outer file-level try/except into the generic 'cannot be read'
    message, but the inner branch still executes (covers the inner
    raise line)."""
    with pytest.raises(TrackEvalException, match="cannot be read"):
        _BaseDataset._load_simple_text_file(
            "doesnt-matter", is_zipped=True, zip_file=None,
        )


def test_load_simple_text_file_raises_on_missing_file(tmp_path):
    """The outer ``try/except`` catches the FileNotFoundError raised
    by ``open(...)`` and re-raises as a TrackEvalException."""
    with pytest.raises(TrackEvalException, match="cannot be read"):
        _BaseDataset._load_simple_text_file(
            str(tmp_path / "doesnt-exist.txt"),
        )


# ---------------------------------------------------------------------------
# _load_simple_text_file — zip-file branch
# ---------------------------------------------------------------------------


def test_load_simple_text_file_reads_from_zip(tmp_path):
    """When ``is_zipped=True`` the loader opens the file from inside
    a zip archive."""
    zip_path = tmp_path / "data.zip"
    inner = "seqA.txt"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(inner, "1,1,10,20,30,40,1,1,1.0\n2,1,11,21,30,40,1,1,1.0\n")
    read_data, _ = _BaseDataset._load_simple_text_file(
        inner, is_zipped=True, zip_file=str(zip_path),
    )
    assert "1" in read_data and "2" in read_data


# ---------------------------------------------------------------------------
# _load_simple_text_file — ignore-region + valid + convert + negative-id
# ---------------------------------------------------------------------------


def test_load_simple_text_file_handles_ignore_region_rows(tmp_path):
    """``crowd_ignore_filter`` splits ignore rows from the main
    read_data dict — those rows then live in ``ignore_data``."""
    p = tmp_path / "f.txt"
    p.write_text(
        "1,1,10,20,30,40,1,IGNORE,1.0\n"
        "1,2,10,20,30,40,1,real,1.0\n"
    )
    read_data, ignore_data = _BaseDataset._load_simple_text_file(
        str(p), crowd_ignore_filter={7: {"ignore"}},
    )
    assert "1" in ignore_data
    assert "1" in read_data
    # Real det has class 'real' in col 7; ignore row was peeled off.
    assert ignore_data["1"][0][7].lower() == "ignore"


def test_load_simple_text_file_remove_negative_ids(tmp_path):
    """``remove_negative_ids=True`` drops rows whose ``id_col`` value
    is negative."""
    p = tmp_path / "f.txt"
    p.write_text(
        "1,-5,10,20,30,40,1,1,1.0\n"
        "1,7,10,20,30,40,1,1,1.0\n"
    )
    read_data, _ = _BaseDataset._load_simple_text_file(
        str(p), remove_negative_ids=True, id_col=1,
    )
    # Only the positive-id row survived.
    assert len(read_data["1"]) == 1
    assert read_data["1"][0][1] == "7"


def test_load_simple_text_file_convert_filter_remaps_column(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("1,1,10,20,30,40,1,person,1.0\n")
    read_data, _ = _BaseDataset._load_simple_text_file(
        str(p), convert_filter={7: {"person": 42}},
    )
    # The class column was remapped from "person" -> 42.
    assert read_data["1"][0][7] == 42


def test_load_simple_text_file_raises_on_unparseable_row(tmp_path):
    """A row with a non-numeric ``time_col`` value triggers the
    per-row Exception handler -> the inner 'cannot be read correctly'
    raise, which is then re-wrapped by the outer file-level catch
    into the generic 'cannot be read' message."""
    p = tmp_path / "f.txt"
    p.write_text("not-a-number,1,10,20\n")
    with pytest.raises(TrackEvalException, match="cannot be read"):
        # csv.Sniffer needs at least one delimiter — provide one.
        _BaseDataset._load_simple_text_file(str(p), force_delimiters=",")


# ---------------------------------------------------------------------------
# _calculate_box_ious — invalid box_format
# ---------------------------------------------------------------------------


def test_calculate_box_ious_invalid_format_raises():
    boxes = np.array([[0.0, 0.0, 1.0, 1.0]])
    with pytest.raises(TrackEvalException, match="not implemented"):
        _BaseDataset._calculate_box_ious(boxes, boxes, box_format="bogus")


# ---------------------------------------------------------------------------
# _check_unique_ids — tracker + GT duplicate-ID raises
# ---------------------------------------------------------------------------


def test_check_unique_ids_raises_on_tracker_duplicates():
    data = {
        "seq": "seqA",
        "gt_ids": [np.array([0, 1])],
        "tracker_ids": [np.array([5, 5])],  # duplicate tracker ID
    }
    with pytest.raises(TrackEvalException,
                         match="Tracker predicts the same ID"):
        _BaseDataset._check_unique_ids(data)


def test_check_unique_ids_raises_on_gt_duplicates():
    data = {
        "seq": "seqA",
        "gt_ids": [np.array([2, 2])],  # duplicate GT ID
        "tracker_ids": [np.array([0, 1])],
    }
    with pytest.raises(TrackEvalException,
                         match="Ground-truth has the same ID"):
        _BaseDataset._check_unique_ids(data)
