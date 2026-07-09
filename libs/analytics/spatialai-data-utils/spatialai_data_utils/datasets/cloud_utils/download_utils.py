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

import concurrent.futures
import json
import logging
import multiprocessing
import os
import re
import shutil
import tempfile
import sys
from datetime import datetime

import requests
from boto3.s3.transfer import TransferConfig

from spatialai_data_utils.datasets.cloud_utils.common import (
    format_base_prefix_path,
    get_base_prefix_path,
    get_storage_bucket,
    get_storage_client,
    get_storage_provider,
    parse_object_storage_url,
)


def sort_files_in_folders(base_path):
    """
    Iterate through each folder, read the file, sort its contents by timestamp, and save the sorted output.
    Uses the new timestamp-based directory sorting method.
    :param base_path: Path to the base directory containing the downloaded dataset folders.
    """
    dataset_to_download = [
        "ground-truth",
        "mdx-bev",
        "mdx-frames",
        "mdx-events",
        "mdx-behavior",
    ]
    empty_mdx_files = set()

    for dataset in dataset_to_download:
        dataset_path = os.path.join(base_path, dataset)
        if not os.path.exists(dataset_path):
            continue

        input_file_path = os.path.join(dataset_path, f"{dataset}.json")
        if os.path.exists(input_file_path) and os.path.getsize(input_file_path) > 0:
            output_file_path = os.path.join(dataset_path, f"{dataset}-sorted.json")
            sort_file_by_timestamp(input_file_path, output_file_path)

        elif dataset == "mdx-bev":
            logging.info(f"File size is 0 for {dataset} file found at {dataset_path}.")
            empty_mdx_files.add(dataset)
        else:
            logging.info(f"File size is 0 for {dataset} file found at {dataset_path}.")

    if "mdx-bev" in empty_mdx_files:
        logging.info(
            "Found mdx-bev.json file with file size 0. "
            "Cannot perform evaluation on empty prediction files."
        )
        print("--------------------------------------------------------------")
        sys.exit(1)


def sort_file_by_timestamp(
    input_file_path,
    output_file_path,
    timestamp_field="timestamp",
    file_extension="txt",
):
    """
    Process the input file, sort entries by timestamp, and save the synchronized output.
    :param input_file_path: Path to the input log file.
    :param output_file_path: Path to the output log file.
    :param timestamp_field: The key representing the timestamp in the JSON object.
    :param file_extension: The file extension for the output.
    """

    temp_dir_path = tempfile.mkdtemp()

    try:
        logging.info(f"Sorting the file: {input_file_path} based on timestamps.")
        with open(input_file_path, "r") as file:
            for line in file:
                try:
                    if '"' not in line and "'" in line:
                        line = line.replace("'", '"')
                    message = json.loads(line)

                    if message.get("sensorId") == "0":
                        continue

                    raw_timestamp = message.get(timestamp_field)
                    if not raw_timestamp:
                        continue

                    timestamp_dir = os.path.join(temp_dir_path, str(raw_timestamp))
                    if not os.path.exists(timestamp_dir):
                        os.makedirs(timestamp_dir)

                    with open(
                        os.path.join(timestamp_dir, f"{raw_timestamp}.{file_extension}"),
                        "a",
                    ) as temp_file:
                        temp_file.write(json.dumps(message) + "\n")

                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    logging.error(
                        f"Error processing message: {input_file_path} \n {line} \n {exc}"
                    )
                    sys.exit(1)

        with open(output_file_path, "w") as output_file:
            for timestamp_dir in sorted(
                os.listdir(temp_dir_path),
                key=lambda x: datetime.strptime(x, "%Y-%m-%dT%H:%M:%S.%fZ"),
            ):
                dir_path = os.path.join(temp_dir_path, timestamp_dir)
                for temp_file_name in os.listdir(dir_path):
                    temp_file_path = os.path.join(dir_path, temp_file_name)
                    with open(temp_file_path, "r") as temp_file:
                        for line in temp_file:
                            output_file.write(line)

        logging.info(f"File has been sorted. Output file located at: {output_file_path}")

    finally:
        shutil.rmtree(temp_dir_path)


def download_single_file_from_storage_streaming(
    storage_client,
    bucket,
    file_key,
    temp_dir,
    dataset,
    transfer_config,
):
    """
    Download a single object-storage file to a temporary file.

    :param storage_client: Boto3 S3-compatible storage client.
    :type storage_client: botocore.client.BaseClient
    :param bucket: Source storage bucket name.
    :type bucket: str
    :param file_key: Object key to download.
    :type file_key: str
    :param temp_dir: Temporary directory where the downloaded file is written.
    :type temp_dir: str
    :param dataset: Dataset name used to group downloaded files.
    :type dataset: str
    :param transfer_config: Transfer configuration for boto3 downloads.
    :type transfer_config: boto3.s3.transfer.TransferConfig
    :return: Local temporary file path if successful, otherwise ``None``.
    :rtype: str | None
    """
    try:
        safe_filename = file_key.replace("/", "_").replace("\\", "_")
        temp_file_path = os.path.join(temp_dir, safe_filename)

        storage_client.download_file(
            Bucket=bucket,
            Key=file_key,
            Filename=temp_file_path,
            Config=transfer_config,
        )

        return temp_file_path
    except Exception as exc:
        logging.error(f"Error downloading {file_key}: {exc}")
        return None


def fetch_storage_keys_for_dataset(storage_client, bucket, prefix, dataset):
    """
    Fetch all object-storage keys for a single dataset.
    :param storage_client: Boto3 S3-compatible storage client
    :type storage_client: botocore.client.BaseClient
    :param bucket: Storage bucket name
    :type bucket: str
    :param prefix: Object-storage prefix path for the dataset
    :type prefix: str
    :param dataset: Dataset name
    :type dataset: str
    :return: List of file objects with metadata
    :rtype: list[dict]
    """
    file_objects = []
    try:
        paginator = storage_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if "Contents" in page:
                for obj in page["Contents"]:
                    file_key = obj["Key"]
                    if (
                        not file_key.endswith("/")
                        and file_key.startswith(prefix)
                        and file_key.count("/") == prefix.count("/")
                    ):
                        obj["dataset"] = dataset
                        file_objects.append(obj)

        return file_objects
    except Exception as exc:
        logging.error(f"Error fetching object-storage keys for {dataset}: {exc}")
        return []


def sort_dataset(dataset, path, simulation_id):
    """
    Sort a single dataset by timestamp.
    Designed to be called by multiprocessing.Pool.
    :param dataset: Dataset name
    :param path: Local path
    :param simulation_id: Simulation ID
    :return: True if successful, False otherwise
    """
    try:
        logging.info(f"Sorting dataset: {dataset}")
        input_file_path = os.path.join(path, simulation_id, dataset, f"{dataset}.json")
        output_file_path = os.path.join(
            path,
            simulation_id,
            dataset,
            f"{dataset}-sorted.json",
        )

        if not os.path.exists(input_file_path):
            logging.warning(f"Input file not found for sorting: {input_file_path}")
            return False

        if os.path.getsize(input_file_path) == 0:
            logging.warning(f"Input file is empty, skipping sorting: {input_file_path}")
            return False

        sort_file_by_timestamp(input_file_path, output_file_path)
        logging.info(f"Successfully sorted dataset: {dataset}")
        return True
    except Exception as exc:
        logging.error(f"Error sorting dataset {dataset}: {exc}")
        return False


def download_and_merge_data_from_storage(args, env_variables, local_path, overwrite=False, max_workers=4):
    """
    Download and merge data from configured object storage using a three-phase approach.
    Phase 1: Fetch all object-storage keys for all datasets
    Phase 2: Download all files using single ThreadPoolExecutor
    Phase 3: Sort all datasets using multiprocessing.Pool
    """

    simulation_id = env_variables["SIMULATION_ID"]
    bucket = get_storage_bucket(env_variables)
    dataset_to_download = (
        ["mdx-bev"]
        if args.only_mdx_bev_validation
        else ["ground-truth", "mdx-bev", "mdx-frames", "mdx-events", "mdx-behavior"]
    )
    logging.info(f"Starting three-phase download for {simulation_id}.")

    storage_client = get_storage_client(
        env_variables,
        max_pool_connections=100,
    )

    base_prefix_path = format_base_prefix_path(
        get_base_prefix_path(env_variables)
    )

    for dataset in dataset_to_download:
        os.makedirs(os.path.join(local_path, simulation_id, dataset), exist_ok=True)

    logging.info("Phase 1: Fetching all object-storage keys for all datasets...")

    all_file_objects = []
    dataset_file_counts = {}

    for dataset in dataset_to_download:
        prefix = f"{base_prefix_path}{simulation_id}/{dataset}/"
        local_file_path = os.path.join(
            local_path,
            simulation_id,
            dataset,
            f"{dataset}.json",
        )

        if os.path.exists(local_file_path) and os.path.getsize(local_file_path) > 0 and not overwrite:
            logging.info(f"Dataset {dataset} already exists locally. Skipping download.")
            dataset_file_counts[dataset] = -1
            continue

        file_objects = fetch_storage_keys_for_dataset(storage_client, bucket, prefix, dataset)

        if not file_objects:
            logging.info(f"No files found in object-storage prefix {prefix}.")
            dataset_file_counts[dataset] = 0
        else:
            file_count = len(file_objects)
            total_size = sum(obj["Size"] for obj in file_objects)
            logging.info(
                f"Found {file_count} files ({total_size / (1024 * 1024):.1f}MB) for {dataset}"
            )
            all_file_objects.extend(file_objects)
            dataset_file_counts[dataset] = file_count

    if not all_file_objects:
        logging.info("No files to download.")
        for dataset in dataset_to_download:
            dataset_path = os.path.join(local_path, simulation_id, dataset)
            if os.path.exists(dataset_path):
                input_file_path = os.path.join(dataset_path, f"{dataset}.json")
                if os.path.exists(input_file_path) and os.path.getsize(input_file_path) == 0:
                    if dataset == "mdx-bev":
                        logging.info(
                            "Found mdx-bev.json files with file size 0. "
                            "Cannot perform evaluation on empty prediction files."
                        )
                        print("--------------------------------------------------------------")
                        sys.exit(1)
        return

    total_files = len(all_file_objects)
    logging.info(
        f"Phase 1 complete: Total {total_files} files to download across all datasets"
    )

    logging.info(
        "Phase 2: Downloading all files using single ThreadPoolExecutor with TransferConfig..."
    )

    transfer_config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        max_concurrency=10,
        multipart_chunksize=8 * 1024 * 1024,
        use_threads=True,
        max_io_queue=1000,
    )
    logging.info(
        "TransferConfig: multipart_threshold=8MB, max_concurrency=10, multipart_chunksize=8MB"
    )

    temp_download_dir = tempfile.mkdtemp(prefix="download_all_")

    for dataset in dataset_to_download:
        os.makedirs(os.path.join(temp_download_dir, dataset), exist_ok=True)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            future_to_file = {}

            for obj in all_file_objects:
                future = executor.submit(
                    download_single_file_from_storage_streaming,
                    storage_client,
                    bucket,
                    obj["Key"],
                    temp_download_dir,
                    obj["dataset"],
                    transfer_config,
                )
                future_to_file[future] = obj

            downloaded_files_by_dataset = {dataset: [] for dataset in dataset_to_download}
            successful_downloads = 0
            total_downloaded_size = 0
            failed_downloads = []

            for future in concurrent.futures.as_completed(future_to_file):
                obj = future_to_file[future]
                temp_file_path = future.result()
                if temp_file_path:
                    downloaded_files_by_dataset[obj["dataset"]].append(temp_file_path)
                    successful_downloads += 1
                    total_downloaded_size += obj["Size"]

                    if successful_downloads % 100 == 0:
                        logging.info(
                            f"Downloaded {successful_downloads}/{total_files} files..."
                        )
                else:
                    failed_downloads.append(obj["Key"])

        if failed_downloads:
            missing_keys = ", ".join(failed_downloads[:10])
            if len(failed_downloads) > 10:
                missing_keys += ", ..."
            raise RuntimeError(
                f"Failed to download {len(failed_downloads)} object-storage objects; "
                f"aborting merge. Missing keys: {missing_keys}"
            )

        logging.info(
            f"Phase 2 complete: Downloaded {successful_downloads}/{total_files} files "
            f"({total_downloaded_size / (1024 * 1024):.1f}MB)"
        )

        logging.info("Concatenating downloaded files for each dataset...")
        failed_merges = []
        for dataset in dataset_to_download:
            if dataset_file_counts.get(dataset, 0) == 0:
                continue

            local_file_path = os.path.join(
                local_path,
                simulation_id,
                dataset,
                f"{dataset}.json",
            )
            temp_files = downloaded_files_by_dataset[dataset]

            if temp_files:
                with open(local_file_path, "w", encoding="utf-8") as output_file:
                    for temp_file_path in temp_files:
                        try:
                            with open(temp_file_path, "r", encoding="utf-8") as temp_file:
                                content = temp_file.read()
                                if content:
                                    if not content.endswith("\n"):
                                        content += "\n"
                                    output_file.write(content)
                        except (OSError, UnicodeDecodeError) as exc:
                            logging.error(
                                f"Error concatenating temp file {temp_file_path}: {exc}"
                            )
                            failed_merges.append(temp_file_path)

                logging.info(f"Concatenated {len(temp_files)} files for {dataset}")

        if failed_merges:
            failed_files = ", ".join(failed_merges[:10])
            if len(failed_merges) > 10:
                failed_files += ", ..."
            raise RuntimeError(
                f"Failed to merge {len(failed_merges)} downloaded files; "
                f"aborting. Files: {failed_files}"
            )

    finally:
        shutil.rmtree(temp_download_dir, ignore_errors=True)

    logging.info("Phase 3: Sorting all datasets using multiprocessing.Pool...")

    datasets_to_sort = [
        dataset
        for dataset in dataset_to_download
        if dataset_file_counts.get(dataset, 0) > 0
    ]

    if datasets_to_sort:
        with multiprocessing.Pool(processes=max_workers) as pool:
            tasks = []
            for dataset in datasets_to_sort:
                task = pool.apply_async(sort_dataset, args=(dataset, local_path, simulation_id))
                tasks.append((dataset, task))

            successful_sorts = []
            for dataset, task in tasks:
                try:
                    success = task.get()
                    if success:
                        successful_sorts.append(dataset)
                except Exception as exc:
                    logging.error(f"Dataset {dataset} sorting failed: {exc}")

        logging.info(
            f"Phase 3 complete: Successfully sorted {len(successful_sorts)} datasets: {successful_sorts}"
        )
    else:
        logging.info("Phase 3: No datasets to sort")

    for dataset in dataset_to_download:
        dataset_path = os.path.join(local_path, simulation_id, dataset)
        if os.path.exists(dataset_path):
            input_file_path = os.path.join(dataset_path, f"{dataset}.json")
            if os.path.exists(input_file_path) and os.path.getsize(input_file_path) == 0:
                if dataset == "mdx-bev":
                    logging.info(
                        "Found mdx-bev.json files with file size 0. "
                        "Cannot perform evaluation on empty prediction files."
                    )
                    print("--------------------------------------------------------------")
                    sys.exit(1)

    logging.info(f"All three phases complete for {simulation_id}!")


def get_calibration_from_storage(
    env_variables,
    calibration_url,
    simulation_id,
    output_root_dir,
    overwrite_file=True,
):
    """Download a calibration file from configured object storage or HTTP."""
    logging.info(f"Downloading calibration file for {simulation_id}.")
    local_file_path = os.path.join(output_root_dir, simulation_id, "calibration.json")
    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

    if os.path.exists(local_file_path) and not overwrite_file:
        logging.info(f"File: {local_file_path} already exists. Skipping download.")
        return

    source_provider, bucket, key = parse_object_storage_url(calibration_url)
    if source_provider:
        configured_provider = get_storage_provider(env_variables)
        if source_provider != configured_provider:
            raise ValueError(
                f"Calibration URL provider '{source_provider}' does not match "
                f"STORAGE_PROVIDER '{configured_provider}'."
            )
        storage_client = get_storage_client(env_variables)
        storage_client.download_file(bucket, key, local_file_path)
    elif re.match(r"^(http://|https://)", calibration_url):
        response = requests.get(calibration_url)
        response.raise_for_status()
        with open(local_file_path, "wb") as file:
            file.write(response.content)
    else:
        raise ValueError(f"Unsupported URL scheme: {calibration_url}")

    logging.info(f"File downloaded successfully to {local_file_path}")
