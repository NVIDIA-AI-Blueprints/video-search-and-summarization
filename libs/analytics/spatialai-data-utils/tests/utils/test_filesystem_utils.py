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

import argparse

from spatialai_data_utils.utils.filesystem_utils import (
    all_files_valid,
    load_json_from_file,
    make_dir,
    validate_file_path,
    validate_readable_file,
)


def test_validate_file_path_allows_expected_chars(tmp_path):
    # Create a path with allowed characters
    fname = tmp_path / "valid_name-#plus+.json"
    fname.write_text("{}")
    result = validate_file_path(str(fname))
    assert result == str(fname)


def test_validate_file_path_raises_on_invalid_chars(tmp_path):
    import pytest
    # Space is not allowed by the regex
    invalid_path = tmp_path / "invalid path.json"
    with pytest.raises(ValueError):
        validate_file_path(str(invalid_path))


def test_validate_file_path_rejects_trailing_newline(tmp_path):
    """Trailing ``\\n`` must be rejected.

    With ``re.match`` (default-mode), ``$`` matches before a trailing
    newline, so ``"foo.json\\n"`` slipped through and downstream
    ``open()`` calls would then fail with a confusing
    ``FileNotFoundError`` on a path that includes a literal newline.
    ``re.fullmatch`` rejects the whole input.
    """
    import pytest
    fname = tmp_path / "valid.json"
    fname.write_text("{}")
    with pytest.raises(ValueError, match="Invalid file path"):
        validate_file_path(str(fname) + "\n")

    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid file path"):
        validate_readable_file(str(fname) + "\n")


def test_load_json_from_file_success(tmp_path):
    import json
    fpath = tmp_path / "data.json"
    fpath.write_text(json.dumps({"a": 1}))
    data = load_json_from_file(str(fpath))
    assert data == {"a": 1}


def test_load_json_from_file_invalid_json_raises(tmp_path):
    import pytest
    fpath = tmp_path / "bad.json"
    fpath.write_text("{ not: valid json }")
    with pytest.raises(ValueError) as ei:
        load_json_from_file(str(fpath))
    assert "Invalid JSON format" in str(ei.value)


def test_load_json_from_file_missing_file_raises(tmp_path):
    import pytest
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError) as ei:
        load_json_from_file(str(missing))
    assert "An error occurred while loading file" in str(ei.value)


def test_all_files_valid_reports_zero_size(tmp_path):
    non_empty = tmp_path / "x.json"
    empty = tmp_path / "y.json"
    non_empty.write_text("{}")
    empty.write_text("")
    assert all_files_valid(str(non_empty), str(empty)) is False
    empty.write_text("1")
    assert all_files_valid(str(non_empty), str(empty)) is True


def test_all_files_valid_reports_missing_file(tmp_path):
    non_empty = tmp_path / "x.json"
    missing = tmp_path / "missing.json"
    non_empty.write_text("{}")

    assert all_files_valid(str(non_empty), str(missing)) is False


def test_make_dir_creates_directory(tmp_path):
    new_dir = tmp_path / "new_dir"
    make_dir(str(new_dir))
    assert new_dir.is_dir()


def test_validate_readable_file_accepts_regular_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    assert validate_readable_file(str(f)) == str(f)


def test_validate_readable_file_rejects_directory(tmp_path):
    """A directory passes exists+readable but is not a regular file —
    validate_readable_file must reject it explicitly so argparse
    callers don't end up handing a dir to a downstream file reader."""
    import pytest
    with pytest.raises(argparse.ArgumentTypeError, match="not a regular file"):
        validate_readable_file(str(tmp_path))


def test_validate_readable_file_rejects_missing(tmp_path):
    import pytest
    missing = tmp_path / "missing.json"
    with pytest.raises(argparse.ArgumentTypeError, match="does NOT exist"):
        validate_readable_file(str(missing))


def test_make_dir_rejects_symlink(tmp_path):
    import os
    target_dir = tmp_path / "real_dir"
    target_dir.mkdir()
    link_path = tmp_path / "link_dir"
    os.symlink(str(target_dir), str(link_path))
    import pytest
    with pytest.raises(ValueError):
        make_dir(str(link_path))
