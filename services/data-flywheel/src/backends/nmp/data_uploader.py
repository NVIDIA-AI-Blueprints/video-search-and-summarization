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
import csv
import io
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from nemo_platform import APIStatusError, APITimeoutError, NeMoPlatform

from src.backends.nmp.retry_utils import retry_on_transient_error
from src.backends.nmp.workspace import ensure_workspace
from src.config import settings
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.data_uploader")

# Timeout for DS operations (fileset creation, deletion)
REQUESTS_TIMEOUT = 300  # seconds
# Timeout for upload operations (large training data uploads)
UPLOAD_TIMEOUT = 3600  # seconds (60 minutes)


def _execute_with_timeout(
    func: Callable,
    timeout: float,
    operation_name: str,
    *args,
    **kwargs,
) -> Any:
    """Execute a function with a timeout using a single-worker ThreadPoolExecutor."""
    executor = ThreadPoolExecutor(max_workers=1)
    executor_shutdown = False
    try:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as err:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            executor_shutdown = True
            raise TimeoutError(f"{operation_name} timed out after {timeout} seconds") from err
    finally:
        if not executor_shutdown:
            executor.shutdown(wait=True)


class DataUploader:
    def __init__(self, dataset_name: str):
        """Initialize the DataUploader against an NMP 26.03 fileset.

        In NMP 26.03 datasets are no longer a top-level resource — they are unified into
        ``client.files.filesets``. Files are uploaded directly via the SDK, removing the
        prior dependency on ``huggingface_hub.HfApi`` against a Data Store HF endpoint.
        """
        self.entity_host = settings.system_config.nmp_config.nemo_base_url
        assert self.entity_host, "nemo_base_url must be set in config"

        self.ds_host = settings.system_config.nmp_config.datastore_base_url
        assert self.ds_host, "datastore_base_url must be set in config"

        self.namespace = settings.system_config.nmp_config.nmp_namespace
        self.workspace = settings.system_config.nmp_config.workspace
        self.dataset_name = dataset_name

        self.nemo_client = NeMoPlatform(
            base_url=self.entity_host,
            inference_base_url=None,  # Not needed for data operations
            workspace=self.workspace,
        )

        # Tracks files uploaded into the fileset for verify_dataset / get_file_uri.
        self._uploaded_paths: list[str] = []

    def _ensure_fileset(self) -> None:
        """Create the fileset if it doesn't already exist (idempotent)."""
        if getattr(self, "_fileset_ready", False):
            return

        # The fileset lives under an NMP workspace that must exist first (26.03).
        ensure_workspace(self.nemo_client, self.workspace)

        try:
            retry_on_transient_error(
                self.nemo_client.files.filesets.create,
                f"create_fileset({self.dataset_name})",
                name=self.dataset_name,
                workspace=self.workspace,
                description=f"DFW dataset: {self.dataset_name}",
                purpose="dataset",
            )
            logger.info(f"Created fileset {self.workspace}/{self.dataset_name}")
        except APIStatusError as e:
            if getattr(e, "status_code", None) in (409, 422):
                logger.info(f"Fileset {self.workspace}/{self.dataset_name} already exists, reusing")
            else:
                logger.error(f"Failed to create fileset: {e}")
                raise
        except Exception as e:
            logger.error(f"Failed to create fileset: {e}")
            raise

        self._fileset_ready = True

    def _upload_bytes(self, data: bytes, path_in_repo: str) -> None:
        """Upload raw bytes to the fileset under ``path_in_repo``."""
        self._ensure_fileset()

        files_resource = self.nemo_client.files

        def _do_upload():
            buffer = io.BytesIO(data)
            # nemo_platform exposes _upload_file as the underlying upload primitive on the
            # files resource — it accepts (path, body, workspace, name).
            return files_resource._upload_file(
                path=path_in_repo,
                body=buffer,
                workspace=self.workspace,
                name=self.dataset_name,
            )

        retry_on_transient_error(
            _execute_with_timeout,
            f"upload_file({path_in_repo})",
            _do_upload,
            UPLOAD_TIMEOUT,
            f"upload_file({path_in_repo})",
        )

        if path_in_repo not in self._uploaded_paths:
            self._uploaded_paths.append(path_in_repo)

    def upload_file(
        self,
        data_fp: str,
        data_type: str,
    ) -> str:
        """Upload a single data file to the fileset.

        Args:
            data_fp: Path to the data file.
            data_type: Type of data (training, validation, or testing).
        """
        valid_types = ["training", "validation", "testing"]
        assert data_type in valid_types, f"data_type must be one of {valid_types}"
        assert os.path.exists(data_fp), f"Data file at '{data_fp}' does not exist"

        filename = os.path.basename(data_fp)
        path_in_repo = f"{data_type}/{filename}"

        with open(data_fp, "rb") as f:
            self._upload_bytes(f.read(), path_in_repo)

        return path_in_repo

    def upload_tsv_file(
        self,
        data: list[dict],
        file_path: str,
    ) -> str:
        """Upload a TSV file (constructed from ``data``) to the fileset."""
        tsv_string_buffer = io.StringIO()
        fieldnames = ["query-id", "corpus-id", "score"]
        writer = csv.DictWriter(
            tsv_string_buffer, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(data)
        tsv_content_bytes = tsv_string_buffer.getvalue().encode("utf-8")

        self._upload_bytes(tsv_content_bytes, file_path)
        self.register_dataset()
        return file_path

    def upload_data(
        self,
        data: str,
        file_path: str,
    ) -> str:
        """Upload a string as a file in the fileset."""
        data_bytes = data.encode("utf-8")
        self._upload_bytes(data_bytes, file_path)
        self.register_dataset()
        return file_path

    def verify_dataset(self) -> dict[str, Any]:
        """Verify the fileset is registered and return its metadata."""
        if not getattr(self, "_fileset_ready", False):
            msg = "No files have been uploaded yet. Call upload_data() first."
            logger.error(msg)
            raise ValueError(msg)

        try:
            fileset_obj = retry_on_transient_error(
                self.nemo_client.files.filesets.retrieve,
                f"verify_dataset({self.dataset_name})",
                name=self.dataset_name,
                workspace=self.workspace,
            )
            fileset_dict = (
                fileset_obj.model_dump()
                if hasattr(fileset_obj, "model_dump")
                else dict(fileset_obj)
            )
        except Exception as e:
            raise AssertionError(f"Failed to fetch fileset: {e}") from e

        # Carry the legacy "files_url" key the rest of DFW reads, mapped to the
        # canonical fileset URI used by customization/evaluation specs in 26.03.
        fileset_dict.setdefault("files_url", f"fileset://{self.workspace}/{self.dataset_name}")
        return fileset_dict

    def register_dataset(self, description: str = "", project: str = "flywheel") -> dict[str, Any]:
        """Register the fileset (idempotent) and return its metadata."""
        self._ensure_fileset()

        try:
            fileset = retry_on_transient_error(
                self.nemo_client.files.filesets.retrieve,
                f"register_dataset_retrieve({self.dataset_name})",
                name=self.dataset_name,
                workspace=self.workspace,
            )
            fileset_dict = fileset.model_dump() if hasattr(fileset, "model_dump") else dict(fileset)
            fileset_dict.setdefault("files_url", f"fileset://{self.workspace}/{self.dataset_name}")
            return fileset_dict
        except APITimeoutError as e:
            logger.error(f"Fileset retrieval timed out after retries: {e}")
            raise AssertionError(
                "Failed to register fileset after retries: Request timed out."
            ) from e
        except Exception as e:
            raise AssertionError(f"Failed to register fileset: {e}") from e

    def get_file_uri(self) -> str:
        """Return the fileset URI used by NMP 26.03 customization/evaluation specs."""
        self.verify_dataset()
        return f"fileset://{self.workspace}/{self.dataset_name}"

    def upload_data_from_folder(
        self, data_folder: str, description: str = "", project: str = ""
    ) -> None:
        """Load data from a folder structure into the fileset."""
        if not os.path.exists(data_folder):
            msg = f"Data folder {data_folder} does not exist"
            logger.error(msg)
            raise ValueError(msg)

        folder_type_map = {
            "customization": "training",
            "validation": "validation",
            "evaluation": "testing",
        }

        for folder_name, data_type in folder_type_map.items():
            folder_path = os.path.join(data_folder, folder_name)

            if not os.path.exists(folder_path):
                logger.warning(f"Warning: Folder {folder_name} not found in {data_folder}")
                continue

            jsonl_files = [f for f in os.listdir(folder_path) if f.endswith(".jsonl")]

            if not jsonl_files:
                logger.warning(f"Warning: No .jsonl files found in {folder_name}")
                continue

            for jsonl_file in jsonl_files:
                file_path = os.path.join(folder_path, jsonl_file)
                self.upload_file(data_fp=file_path, data_type=data_type)
                logger.info(f"Uploaded {jsonl_file} as {data_type} data")

        self.register_dataset(description=description, project=project)

    def delete_dataset(self) -> None:
        """Delete the fileset's underlying file storage.

        In 26.03 the fileset and its files share a lifecycle, so this is folded into
        ``unregister_dataset``. Kept for API compatibility.
        """
        logger.info(
            f"delete_dataset({self.dataset_name}) is folded into unregister_dataset() in NMP 26.03"
        )

    def unregister_dataset(self) -> None:
        """Delete the fileset (and its files) from NMP 26.03."""
        try:
            retry_on_transient_error(
                self.nemo_client.files.filesets.delete,
                f"unregister_dataset({self.dataset_name})",
                name=self.dataset_name,
                workspace=self.workspace,
            )
            logger.info(f"Successfully unregistered fileset {self.workspace}/{self.dataset_name}")
        except Exception as e:
            logger.error(f"Failed to unregister fileset: {e}")
            raise
