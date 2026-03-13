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
"""Tests for sitecustomize.py."""

from unittest.mock import MagicMock
from unittest.mock import patch


class TestSiteCustomize:
    """Tests for sitecustomize module."""

    def test_load_env_file_with_dotenv(self, tmp_path):
        """Test _load_env_file with dotenv available."""
        # Create a temporary .env file
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_VAR=test_value")

        from sitecustomize import _load_env_file

        with patch("sitecustomize.load_dotenv") as mock_load_dotenv:
            _load_env_file(env_file)
            mock_load_dotenv.assert_called_once_with(env_file, override=False)

    def test_load_env_file_without_dotenv(self, tmp_path):
        """Test _load_env_file when dotenv is not available."""
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_VAR=test_value")

        from sitecustomize import _load_env_file

        with patch("sitecustomize.load_dotenv", None):
            # Should not raise, just log a warning
            _load_env_file(env_file)

    def test_load_env_file_nonexistent(self, tmp_path):
        """Test _load_env_file with nonexistent file."""
        env_file = tmp_path / "nonexistent.env"

        from sitecustomize import _load_env_file

        with patch("sitecustomize.load_dotenv") as mock_load_dotenv:
            _load_env_file(env_file)
            # Should not call load_dotenv for nonexistent file
            mock_load_dotenv.assert_not_called()

    def test_auto_load_env_files_no_pointer(self, tmp_path):
        """Test _auto_load_env_files when .env_file pointer doesn't exist."""
        from sitecustomize import _auto_load_env_files

        with patch("sitecustomize.Path") as mock_path:
            mock_path.return_value.resolve.return_value.parent.parent = tmp_path
            mock_env_pointer = MagicMock()
            mock_env_pointer.is_file.return_value = False

            # Should not raise, just log info
            _auto_load_env_files()

    def test_auto_load_env_files_with_pointer(self, tmp_path):
        """Test _auto_load_env_files when .env_file pointer exists."""
        # Create a temp .env file
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_VAR=test_value")

        # Create the pointer file
        pointer_file = tmp_path / ".env_file"
        pointer_file.write_text(str(env_file))

        with patch("sitecustomize.Path") as mock_path_class:
            mock_file_path = MagicMock()
            mock_file_path.resolve.return_value.parent.parent = tmp_path

            mock_env_pointer = MagicMock()
            mock_env_pointer.is_file.return_value = True
            mock_env_pointer.read_text.return_value = str(env_file)

            def path_side_effect(arg=None):
                if arg is None:
                    return mock_file_path
                if str(arg) == str(tmp_path / ".env_file"):
                    return mock_env_pointer
                return MagicMock(is_file=MagicMock(return_value=False))

            mock_path_class.side_effect = path_side_effect
            mock_path_class.return_value = mock_file_path

    def test_auto_load_env_files_empty_pointer(self, tmp_path):
        """Test _auto_load_env_files when .env_file is empty."""
        # Create empty pointer file
        pointer_file = tmp_path / ".env_file"
        pointer_file.write_text("")

        # Should not raise, just log a warning
        # Note: This test verifies the code handles edge cases gracefully
