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

"""Unit tests for ``src.backends.nmp.data_uploader`` against the NMP 26.03 SDK.

In NMP 26.03 datasets are unified into ``client.files.filesets``; the legacy
HuggingFace-backed code path (``HfApi``, ``client.datastore.*``, namespace
endpoints, separate dataset/repo objects) is gone. These tests mock the
``NeMoPlatform`` client and exercise the new fileset-based upload/verify/
delete flow.
"""

import os
import tempfile
from concurrent.futures import TimeoutError as FutureTimeoutError
from unittest.mock import Mock, patch

import pytest
import requests
from nemo_platform import APIStatusError

from src.backends.nmp.data_uploader import UPLOAD_TIMEOUT, DataUploader

# --------------------------------------------------------------------------- #
# Module-level fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def mock_sleep_globally():
    """Patch ``time.sleep`` inside the retry helper so retries don't actually wait."""
    with patch("src.lib.retry.sleep") as mock_sleep:
        yield mock_sleep


@pytest.fixture
def mock_settings():
    """Mock the ``settings`` object imported by data_uploader."""
    with patch("src.backends.nmp.data_uploader.settings") as mock_settings:
        mock_settings.system_config.nmp_config.nemo_base_url = "http://test-nemo-url"
        mock_settings.system_config.nmp_config.datastore_base_url = "http://test-datastore-url"
        mock_settings.system_config.nmp_config.nmp_namespace = "test-namespace"
        mock_settings.system_config.nmp_config.workspace = "test-workspace"
        yield mock_settings


def _build_uploader(dataset_name: str = "test-dataset") -> tuple[DataUploader, Mock]:
    """Construct a ``DataUploader`` with a fully mocked NMP client.

    Returns the uploader and the mock ``NeMoPlatform`` instance so callers can
    configure ``client.files.filesets.create/retrieve/delete`` and
    ``client.files._upload_file`` return values / side effects.
    """
    with patch("src.backends.nmp.data_uploader.NeMoPlatform") as mock_nemo_class:
        mock_client = Mock()
        mock_nemo_class.return_value = mock_client
        uploader = DataUploader(dataset_name)
        # Ensure the patched mock is what the uploader is holding.
        uploader.nemo_client = mock_client
    return uploader, mock_client


@pytest.fixture
def data_uploader(mock_settings):
    """Default DataUploader fixture used across most test classes."""
    uploader, _ = _build_uploader()
    yield uploader


# --------------------------------------------------------------------------- #
# Initialization
# --------------------------------------------------------------------------- #


class TestDataUploader:
    """Tests for DataUploader construction."""

    def test_init_success(self, mock_settings):
        """Constructor wires settings and instantiates a NeMoPlatform client."""
        with patch("src.backends.nmp.data_uploader.NeMoPlatform") as mock_nemo_class:
            mock_nemo_class.return_value = Mock()
            uploader = DataUploader("test-dataset")

            assert uploader.entity_host == "http://test-nemo-url"
            assert uploader.ds_host == "http://test-datastore-url"
            assert uploader.namespace == "test-namespace"
            assert uploader.workspace == "test-workspace"
            assert uploader.dataset_name == "test-dataset"

            mock_nemo_class.assert_called_once_with(
                base_url="http://test-nemo-url",
                inference_base_url=None,
                workspace="test-workspace",
            )

    def test_init_missing_nemo_url(self, mock_settings):
        """Initialization fails without ``nemo_base_url``."""
        mock_settings.system_config.nmp_config.nemo_base_url = None

        with pytest.raises(AssertionError, match="nemo_base_url must be set in config"):
            DataUploader("test-dataset")

    def test_init_missing_datastore_url(self, mock_settings):
        """Initialization fails without ``datastore_base_url``."""
        mock_settings.system_config.nmp_config.datastore_base_url = None

        with pytest.raises(AssertionError, match="datastore_base_url must be set in config"):
            DataUploader("test-dataset")


# --------------------------------------------------------------------------- #
# Fileset creation (formerly _create_namespaces / _create_repo)
# --------------------------------------------------------------------------- #


class TestDataUploaderFilesetCreation:
    """Tests for the idempotent ``_ensure_fileset`` helper."""

    def test_ensure_fileset_success(self, data_uploader):
        """Successful fileset creation calls the SDK with the expected kwargs."""
        data_uploader.nemo_client.files.filesets.create.return_value = Mock()

        data_uploader._ensure_fileset()

        data_uploader.nemo_client.files.filesets.create.assert_called_once_with(
            name="test-dataset",
            workspace="test-workspace",
            description="DFW dataset: test-dataset",
            purpose="dataset",
        )
        assert data_uploader._fileset_ready is True

    def test_ensure_fileset_already_exists_409(self, data_uploader):
        """A 409 from the SDK is treated as ``already exists`` and swallowed."""
        api_error = APIStatusError("Conflict", response=Mock(), body="Already exists")
        api_error.status_code = 409
        data_uploader.nemo_client.files.filesets.create.side_effect = api_error

        data_uploader._ensure_fileset()  # should not raise
        assert data_uploader._fileset_ready is True

    def test_ensure_fileset_already_exists_422(self, data_uploader):
        """A 422 from the SDK is also treated as already-exists."""
        api_error = APIStatusError("Unprocessable", response=Mock(), body="Already exists")
        api_error.status_code = 422
        data_uploader.nemo_client.files.filesets.create.side_effect = api_error

        data_uploader._ensure_fileset()  # should not raise
        assert data_uploader._fileset_ready is True

    def test_ensure_fileset_api_error_non_conflict(self, data_uploader):
        """Non-409/422 APIStatusError propagates."""
        api_error = APIStatusError("Server Error", response=Mock(), body="Internal")
        api_error.status_code = 500
        data_uploader.nemo_client.files.filesets.create.side_effect = api_error

        with pytest.raises(APIStatusError, match="Server Error"):
            data_uploader._ensure_fileset()

    def test_ensure_fileset_unknown_failure(self, data_uploader):
        """Unrelated exceptions propagate."""
        data_uploader.nemo_client.files.filesets.create.side_effect = Exception("Server error")

        with pytest.raises(Exception, match="Server error"):
            data_uploader._ensure_fileset()

    def test_ensure_fileset_is_idempotent(self, data_uploader):
        """Repeated ``_ensure_fileset`` calls invoke the SDK only once."""
        data_uploader.nemo_client.files.filesets.create.return_value = Mock()

        data_uploader._ensure_fileset()
        data_uploader._ensure_fileset()
        data_uploader._ensure_fileset()

        assert data_uploader.nemo_client.files.filesets.create.call_count == 1


# --------------------------------------------------------------------------- #
# File upload
# --------------------------------------------------------------------------- #


class TestDataUploaderFileUpload:
    """Tests for ``upload_file``."""

    def test_upload_file_success(self, data_uploader):
        """Uploading a real file calls the private ``_upload_file`` primitive."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            data_uploader.nemo_client.files.filesets.create.return_value = Mock()
            data_uploader.nemo_client.files._upload_file.return_value = Mock()

            result = data_uploader.upload_file(temp_file_path, "training")

            expected_filename = os.path.basename(temp_file_path)
            expected_path = f"training/{expected_filename}"
            assert result == expected_path

            data_uploader.nemo_client.files._upload_file.assert_called_once()
            call_kwargs = data_uploader.nemo_client.files._upload_file.call_args.kwargs
            assert call_kwargs["path"] == expected_path
            assert call_kwargs["workspace"] == "test-workspace"
            assert call_kwargs["name"] == "test-dataset"
            # body should be a BytesIO-like buffer containing the file's bytes.
            assert call_kwargs["body"].getvalue() == b'{"test": "data"}'
        finally:
            os.unlink(temp_file_path)

    def test_upload_file_invalid_data_type(self, data_uploader):
        """An invalid data_type raises an AssertionError before any SDK call."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            with pytest.raises(AssertionError, match="data_type must be one of"):
                data_uploader.upload_file(temp_file_path, "invalid_type")
        finally:
            os.unlink(temp_file_path)

    def test_upload_file_nonexistent_file(self, data_uploader):
        """A missing file raises an AssertionError."""
        with pytest.raises(AssertionError, match="Data file at .* does not exist"):
            data_uploader.upload_file("/nonexistent/file.jsonl", "training")

    def test_upload_file_fileset_already_ready(self, data_uploader):
        """If the fileset is already ready, ``_upload_file`` is invoked without re-creating."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            # Simulate a pre-existing fileset.
            data_uploader._fileset_ready = True

            result = data_uploader.upload_file(temp_file_path, "validation")

            expected_filename = os.path.basename(temp_file_path)
            expected_path = f"validation/{expected_filename}"
            assert result == expected_path

            data_uploader.nemo_client.files.filesets.create.assert_not_called()
            data_uploader.nemo_client.files._upload_file.assert_called_once()
        finally:
            os.unlink(temp_file_path)


# --------------------------------------------------------------------------- #
# upload_data / upload_tsv_file
# --------------------------------------------------------------------------- #


class TestDataUploaderDataUpload:
    """Tests for ``upload_data`` and ``upload_tsv_file``."""

    def test_upload_data_success(self, data_uploader):
        """Uploading a raw string calls ``_upload_file`` and then register_dataset."""
        test_data = '{"test": "data"}\n{"another": "line"}'
        file_path = "test/data.jsonl"

        data_uploader.nemo_client.files.filesets.create.return_value = Mock()
        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.upload_data(test_data, file_path)

        assert result == file_path
        data_uploader.nemo_client.files._upload_file.assert_called_once()
        upload_kwargs = data_uploader.nemo_client.files._upload_file.call_args.kwargs
        assert upload_kwargs["path"] == file_path
        assert upload_kwargs["body"].getvalue() == test_data.encode("utf-8")
        # register_dataset is called inside upload_data, which calls retrieve.
        data_uploader.nemo_client.files.filesets.retrieve.assert_called_once_with(
            name="test-dataset", workspace="test-workspace"
        )

    def test_upload_data_fileset_already_ready(self, data_uploader):
        """If the fileset already exists, no create call is issued."""
        test_data = '{"test": "data"}'
        file_path = "test/data.jsonl"

        data_uploader._fileset_ready = True
        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.upload_data(test_data, file_path)

        assert result == file_path
        data_uploader.nemo_client.files.filesets.create.assert_not_called()

    def test_upload_tsv_file_success(self, data_uploader):
        """TSV data is encoded and uploaded as a single fileset entry."""
        test_data = [
            {"query-id": "q1", "corpus-id": "c1", "score": 1.0},
            {"query-id": "q2", "corpus-id": "c2", "score": 0.8},
        ]
        file_path = "evaluation/qrels.tsv"

        data_uploader.nemo_client.files.filesets.create.return_value = Mock()
        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.upload_tsv_file(test_data, file_path)

        assert result == file_path
        data_uploader.nemo_client.files._upload_file.assert_called_once()
        upload_kwargs = data_uploader.nemo_client.files._upload_file.call_args.kwargs
        assert upload_kwargs["path"] == file_path
        tsv_bytes = upload_kwargs["body"].getvalue()
        # Verify the TSV header and a row are present.
        assert b"query-id\tcorpus-id\tscore" in tsv_bytes
        assert b"q1\tc1\t1.0" in tsv_bytes

    def test_upload_tsv_file_fileset_already_ready(self, data_uploader):
        """TSV upload skips fileset creation when already ready."""
        test_data = [{"query-id": "q1", "corpus-id": "c1", "score": 1.0}]
        file_path = "evaluation/qrels.tsv"

        data_uploader._fileset_ready = True
        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.upload_tsv_file(test_data, file_path)

        assert result == file_path
        data_uploader.nemo_client.files.filesets.create.assert_not_called()
        data_uploader.nemo_client.files._upload_file.assert_called_once()


# --------------------------------------------------------------------------- #
# verify_dataset / register_dataset / get_file_uri
# --------------------------------------------------------------------------- #


class TestDataUploaderDatasetManagement:
    """Tests for the dataset management methods."""

    def test_verify_dataset_success(self, data_uploader):
        """``verify_dataset`` returns the fileset dict with a ``files_url`` injected."""
        data_uploader._fileset_ready = True

        retrieved = Mock()
        retrieved.model_dump.return_value = {
            "name": "test-dataset",
            "workspace": "test-workspace",
        }
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.verify_dataset()

        assert result["name"] == "test-dataset"
        assert result["workspace"] == "test-workspace"
        assert result["files_url"] == "fileset://test-workspace/test-dataset"
        data_uploader.nemo_client.files.filesets.retrieve.assert_called_once_with(
            name="test-dataset", workspace="test-workspace"
        )

    def test_verify_dataset_no_files_uploaded(self, data_uploader):
        """Calling verify_dataset before any upload raises ValueError."""
        with pytest.raises(ValueError, match="No files have been uploaded yet"):
            data_uploader.verify_dataset()

    def test_verify_dataset_request_failure(self, data_uploader):
        """SDK retrieve failure is wrapped in AssertionError."""
        data_uploader._fileset_ready = True
        data_uploader.nemo_client.files.filesets.retrieve.side_effect = Exception(
            "Failed to fetch dataset"
        )

        with pytest.raises(AssertionError, match="Failed to fetch fileset"):
            data_uploader.verify_dataset()

    def test_register_dataset_create_new(self, data_uploader):
        """``register_dataset`` creates the fileset (idempotently) and retrieves it."""
        data_uploader.nemo_client.files.filesets.create.return_value = Mock()
        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.register_dataset("Test description", "test-project")

        assert result["id"] == "fs-1"
        # files_url is injected by the implementation.
        assert result["files_url"] == "fileset://test-workspace/test-dataset"
        data_uploader.nemo_client.files.filesets.create.assert_called_once_with(
            name="test-dataset",
            workspace="test-workspace",
            description="DFW dataset: test-dataset",
            purpose="dataset",
        )
        data_uploader.nemo_client.files.filesets.retrieve.assert_called_once_with(
            name="test-dataset", workspace="test-workspace"
        )

    def test_register_dataset_update_existing(self, data_uploader):
        """A 409 on create is swallowed and retrieve still returns the fileset."""
        api_error = APIStatusError("Conflict", response=Mock(), body="Already exists")
        api_error.status_code = 409
        data_uploader.nemo_client.files.filesets.create.side_effect = api_error

        retrieved = Mock()
        retrieved.model_dump.return_value = {"id": "fs-1"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.register_dataset("Updated description")

        assert result["id"] == "fs-1"
        assert result["files_url"] == "fileset://test-workspace/test-dataset"

    def test_register_dataset_failure(self, data_uploader):
        """SDK retrieve failure during register_dataset surfaces as AssertionError."""
        data_uploader.nemo_client.files.filesets.create.return_value = Mock()
        data_uploader.nemo_client.files.filesets.retrieve.side_effect = Exception(
            "Failed to fetch fileset"
        )

        with pytest.raises(AssertionError, match="Failed to register fileset"):
            data_uploader.register_dataset()

    def test_register_dataset_api_error_non_conflict(self, data_uploader):
        """A non-409/422 APIStatusError from create propagates out."""
        api_error = APIStatusError("Server Error", response=Mock(), body="Internal")
        api_error.status_code = 500
        data_uploader.nemo_client.files.filesets.create.side_effect = api_error

        with pytest.raises(APIStatusError, match="Server Error"):
            data_uploader.register_dataset()

    def test_get_file_uri_success(self, data_uploader):
        """``get_file_uri`` returns the canonical fileset URI."""
        data_uploader._fileset_ready = True
        retrieved = Mock()
        retrieved.model_dump.return_value = {"name": "test-dataset"}
        data_uploader.nemo_client.files.filesets.retrieve.return_value = retrieved

        result = data_uploader.get_file_uri()
        assert result == "fileset://test-workspace/test-dataset"

    def test_get_file_uri_no_files_uploaded(self, data_uploader):
        """``get_file_uri`` raises if no upload has happened (delegates to verify_dataset)."""
        with pytest.raises(ValueError, match="No files have been uploaded yet"):
            data_uploader.get_file_uri()


# --------------------------------------------------------------------------- #
# upload_data_from_folder
# --------------------------------------------------------------------------- #


class TestDataUploaderFolderUpload:
    """Tests for ``upload_data_from_folder``."""

    def test_upload_data_from_folder_nonexistent(self, data_uploader):
        """A missing folder raises ValueError."""
        with pytest.raises(ValueError, match="Data folder .* does not exist"):
            data_uploader.upload_data_from_folder("/nonexistent/folder")

    def test_upload_data_from_folder_success(self, data_uploader):
        """All three sub-folders are walked and ``upload_file`` is called per file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            customization_dir = os.path.join(temp_dir, "customization")
            validation_dir = os.path.join(temp_dir, "validation")
            evaluation_dir = os.path.join(temp_dir, "evaluation")

            os.makedirs(customization_dir)
            os.makedirs(validation_dir)
            os.makedirs(evaluation_dir)

            with open(os.path.join(customization_dir, "train.jsonl"), "w") as f:
                f.write('{"train": "data"}')
            with open(os.path.join(validation_dir, "val.jsonl"), "w") as f:
                f.write('{"val": "data"}')
            with open(os.path.join(evaluation_dir, "test.jsonl"), "w") as f:
                f.write('{"test": "data"}')

            with (
                patch.object(data_uploader, "upload_file") as mock_upload,
                patch.object(data_uploader, "register_dataset") as mock_register,
            ):
                data_uploader.upload_data_from_folder(temp_dir, "Test description", "test-project")

                assert mock_upload.call_count == 3
                mock_register.assert_called_once_with(
                    description="Test description", project="test-project"
                )

    def test_upload_data_from_folder_missing_subfolders(self, data_uploader):
        """Missing sub-folders are skipped without erroring."""
        with tempfile.TemporaryDirectory() as temp_dir:
            customization_dir = os.path.join(temp_dir, "customization")
            os.makedirs(customization_dir)

            with open(os.path.join(customization_dir, "train.jsonl"), "w") as f:
                f.write('{"train": "data"}')

            with (
                patch.object(data_uploader, "upload_file") as mock_upload,
                patch.object(data_uploader, "register_dataset") as mock_register,
            ):
                data_uploader.upload_data_from_folder(temp_dir)

                assert mock_upload.call_count == 1
                mock_register.assert_called_once()

    def test_upload_data_from_folder_no_jsonl_files(self, data_uploader):
        """A folder with no .jsonl files results in zero uploads but still registers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            customization_dir = os.path.join(temp_dir, "customization")
            os.makedirs(customization_dir)

            with open(os.path.join(customization_dir, "data.txt"), "w") as f:
                f.write("not a jsonl file")

            with (
                patch.object(data_uploader, "upload_file") as mock_upload,
                patch.object(data_uploader, "register_dataset") as mock_register,
            ):
                data_uploader.upload_data_from_folder(temp_dir)

                mock_upload.assert_not_called()
                mock_register.assert_called_once()


# --------------------------------------------------------------------------- #
# delete_dataset / unregister_dataset
# --------------------------------------------------------------------------- #


class TestDataUploaderDeletion:
    """Tests for fileset deletion."""

    def test_delete_dataset_is_noop(self, data_uploader):
        """In NMP 26.03, ``delete_dataset`` is folded into ``unregister_dataset``.

        It logs and returns without touching the SDK.
        """
        data_uploader.delete_dataset()  # should not raise
        data_uploader.nemo_client.files.filesets.delete.assert_not_called()

    def test_unregister_dataset_success(self, data_uploader):
        """``unregister_dataset`` calls ``client.files.filesets.delete`` with name/workspace."""
        data_uploader.nemo_client.files.filesets.delete.return_value = Mock()

        data_uploader.unregister_dataset()

        data_uploader.nemo_client.files.filesets.delete.assert_called_once_with(
            name="test-dataset", workspace="test-workspace"
        )

    def test_unregister_dataset_failure(self, data_uploader):
        """An unhandled SDK error from delete is re-raised."""
        data_uploader.nemo_client.files.filesets.delete.side_effect = Exception(
            "Failed to unregister fileset"
        )

        with pytest.raises(Exception, match="Failed to unregister fileset"):
            data_uploader.unregister_dataset()


# --------------------------------------------------------------------------- #
# Retry logic
# --------------------------------------------------------------------------- #


class TestDataUploaderRetryLogic:
    """Tests for the ``retry_on_transient_error`` integration."""

    def test_ensure_fileset_retry_on_transient_error(self, data_uploader, mock_sleep_globally):
        """A transient ConnectionError on create is retried, then succeeds."""
        transient_error = requests.exceptions.ConnectionError("Connection aborted")
        data_uploader.nemo_client.files.filesets.create.side_effect = [
            transient_error,
            Mock(),
        ]

        data_uploader._ensure_fileset()

        assert data_uploader.nemo_client.files.filesets.create.call_count == 2
        assert mock_sleep_globally.call_count == 1

    def test_verify_dataset_retry_on_transient_error(self, data_uploader, mock_sleep_globally):
        """A transient ConnectionError on retrieve is retried."""
        data_uploader._fileset_ready = True
        transient_error = requests.exceptions.ConnectionError("Connection aborted")
        retrieved = Mock()
        retrieved.model_dump.return_value = {"name": "test-dataset"}
        data_uploader.nemo_client.files.filesets.retrieve.side_effect = [
            transient_error,
            retrieved,
        ]

        result = data_uploader.verify_dataset()

        assert result["files_url"] == "fileset://test-workspace/test-dataset"
        assert data_uploader.nemo_client.files.filesets.retrieve.call_count == 2
        assert mock_sleep_globally.call_count == 1

    def test_unregister_dataset_retry_on_transient_error(self, data_uploader, mock_sleep_globally):
        """A transient ConnectionError on delete is retried."""
        transient_error = requests.exceptions.ConnectionError("Connection reset")
        data_uploader.nemo_client.files.filesets.delete.side_effect = [
            transient_error,
            None,
        ]

        data_uploader.unregister_dataset()

        assert data_uploader.nemo_client.files.filesets.delete.call_count == 2
        assert mock_sleep_globally.call_count == 1

    def test_ensure_fileset_fails_after_max_retries(self, data_uploader, mock_sleep_globally):
        """The retry helper exhausts retries and re-raises the original transient error."""
        transient_error = requests.exceptions.ConnectionError("Connection aborted")
        data_uploader.nemo_client.files.filesets.create.side_effect = transient_error

        with pytest.raises(requests.exceptions.ConnectionError):
            data_uploader._ensure_fileset()

        # max_retries defaults to 3 → 3 attempts, 2 sleeps between them.
        assert data_uploader.nemo_client.files.filesets.create.call_count == 3
        assert mock_sleep_globally.call_count == 2

    def test_non_transient_error_no_retry(self, data_uploader, mock_sleep_globally):
        """Non-transient errors are not retried."""
        data_uploader._fileset_ready = True
        data_uploader.nemo_client.files.filesets.retrieve.side_effect = ValueError("Invalid input")

        # verify_dataset wraps non-transient errors in AssertionError.
        with pytest.raises(AssertionError, match="Failed to fetch fileset"):
            data_uploader.verify_dataset()

        assert data_uploader.nemo_client.files.filesets.retrieve.call_count == 1
        mock_sleep_globally.assert_not_called()


# --------------------------------------------------------------------------- #
# Timeout handling
# --------------------------------------------------------------------------- #


class TestDataUploaderTimeoutHandling:
    """Tests for the ``_execute_with_timeout`` wrapping inside upload paths."""

    def test_upload_file_timeout_on_hanging_api(self, data_uploader):
        """When the inner Future times out, upload_file raises TimeoutError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            data_uploader._fileset_ready = True

            with patch("src.backends.nmp.data_uploader.ThreadPoolExecutor") as mock_executor_class:
                mock_executor = Mock()
                mock_executor._shutdown = False
                mock_executor_class.return_value = mock_executor
                mock_future = Mock()
                mock_executor.submit.return_value = mock_future
                mock_future.result.side_effect = FutureTimeoutError()

                with pytest.raises(
                    TimeoutError,
                    match=rf"upload_file.*timed out after {UPLOAD_TIMEOUT} seconds",
                ):
                    data_uploader.upload_file(temp_file_path, "training")
        finally:
            os.unlink(temp_file_path)

    def test_upload_tsv_file_timeout_on_hanging_api(self, data_uploader):
        """upload_tsv_file surfaces the inner timeout."""
        test_data = [{"query-id": "q1", "corpus-id": "c1", "score": 1.0}]
        file_path = "evaluation/qrels.tsv"

        data_uploader._fileset_ready = True

        with patch("src.backends.nmp.data_uploader.ThreadPoolExecutor") as mock_executor_class:
            mock_executor = Mock()
            mock_executor._shutdown = False
            mock_executor_class.return_value = mock_executor
            mock_future = Mock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.side_effect = FutureTimeoutError()

            with pytest.raises(
                TimeoutError,
                match=rf"upload_file.*timed out after {UPLOAD_TIMEOUT} seconds",
            ):
                data_uploader.upload_tsv_file(test_data, file_path)

    def test_upload_data_timeout_on_hanging_api(self, data_uploader):
        """upload_data surfaces the inner timeout."""
        test_data = '{"test": "data"}'
        file_path = "test/data.jsonl"

        data_uploader._fileset_ready = True

        with patch("src.backends.nmp.data_uploader.ThreadPoolExecutor") as mock_executor_class:
            mock_executor = Mock()
            mock_executor._shutdown = False
            mock_executor_class.return_value = mock_executor
            mock_future = Mock()
            mock_executor.submit.return_value = mock_future
            mock_future.result.side_effect = FutureTimeoutError()

            with pytest.raises(
                TimeoutError,
                match=rf"upload_file.*timed out after {UPLOAD_TIMEOUT} seconds",
            ):
                data_uploader.upload_data(test_data, file_path)

    def test_upload_file_timeout_retry_then_success(self, data_uploader, mock_sleep_globally):
        """A TimeoutError counts as transient — upload_file retries once then succeeds."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            data_uploader._fileset_ready = True

            with patch("src.backends.nmp.data_uploader.ThreadPoolExecutor") as mock_executor_class:
                mock_executor1 = Mock()
                mock_executor1._shutdown = False
                mock_executor2 = Mock()
                mock_executor2._shutdown = False
                mock_executor_class.side_effect = [mock_executor1, mock_executor2]

                mock_future1 = Mock()
                mock_future2 = Mock()
                mock_executor1.submit.return_value = mock_future1
                mock_executor2.submit.return_value = mock_future2

                mock_future1.result.side_effect = FutureTimeoutError()
                mock_future2.result.return_value = None

                result = data_uploader.upload_file(temp_file_path, "validation")

                expected_filename = os.path.basename(temp_file_path)
                expected_path = f"validation/{expected_filename}"
                assert result == expected_path
                assert mock_sleep_globally.call_count == 1
        finally:
            os.unlink(temp_file_path)

    def test_upload_file_timeout_uses_correct_timeout_value(self, data_uploader):
        """``upload_file`` passes ``UPLOAD_TIMEOUT`` to ``future.result``."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as temp_file:
            temp_file.write('{"test": "data"}')
            temp_file_path = temp_file.name

        try:
            data_uploader._fileset_ready = True

            with patch("src.backends.nmp.data_uploader.ThreadPoolExecutor") as mock_executor_class:
                mock_executor = Mock()
                mock_executor._shutdown = False
                mock_executor_class.return_value = mock_executor
                mock_future = Mock()
                mock_executor.submit.return_value = mock_future
                mock_future.result.side_effect = FutureTimeoutError()

                with pytest.raises(TimeoutError) as exc_info:
                    data_uploader.upload_file(temp_file_path, "training")

                assert str(UPLOAD_TIMEOUT) in str(exc_info.value)
                mock_future.result.assert_called_with(timeout=UPLOAD_TIMEOUT)
        finally:
            os.unlink(temp_file_path)
