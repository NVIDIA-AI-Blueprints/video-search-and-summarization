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

import logging
import os

import pandas as pd

from spatialai_data_utils.datasets.cloud_utils.common import (
    get_storage_bucket,
    get_storage_client,
)


def combine_and_upload_detection_metrics_csv_to_storage(
    env_variables,
    input_csvs_path,
    local_output_csv_dump_path,
    storage_output_path,
):
    """
    Combine per-sensor detection metrics CSV files and upload the result to object storage.

    Recursively searches ``input_csvs_path`` for ``detection_metrics.csv`` files,
    concatenates them into one CSV, writes the combined CSV locally, and uploads
    the combined content to the configured object-storage bucket.

    :param env_variables: Environment/configuration values containing storage
        provider credentials and bucket name.
    :type env_variables: dict
    :param input_csvs_path: Root directory to search for detection metrics CSV
        files.
    :type input_csvs_path: str
    :param local_output_csv_dump_path: Local path where the combined CSV is
        written before upload.
    :type local_output_csv_dump_path: str
    :param storage_output_path: Destination object key for the combined CSV.
    :type storage_output_path: str
    :return: None.
    :rtype: None
    """
    all_dfs = []

    for root, dirs, files in os.walk(input_csvs_path):
        if "detection_metrics.csv" in files:
            file_path = os.path.join(root, "detection_metrics.csv")
            df = pd.read_csv(file_path)
            all_dfs.append(df)

    if all_dfs:
        combined_df = pd.concat(all_dfs, ignore_index=True)
        combined_df.to_csv(local_output_csv_dump_path, index=False)
        upload_csv_to_storage(
            combined_df,
            env_variables,
            storage_output_path,
        )
    else:
        logging.info(
            f"!!No detection metrics csv files found in {input_csvs_path}. Exiting..."
        )


def upload_csv_to_storage(df, env_variables, output_directory_path):
    """
    Upload a pandas DataFrame as CSV content to configured object storage.

    :param df: DataFrame to serialize as CSV.
    :type df: pandas.DataFrame
    :param env_variables: Environment/configuration values containing storage
        provider credentials and bucket name.
    :type env_variables: dict
    :param output_directory_path: Destination object key in the configured
        storage bucket.
    :type output_directory_path: str
    :return: None.
    :rtype: None
    """
    storage_client = get_storage_client(env_variables)
    bucket = get_storage_bucket(env_variables)
    logging.info(f"Uploading {output_directory_path} to {bucket}")
    storage_client.put_object(
        Bucket=bucket,
        Key=output_directory_path,
        Body=df.to_csv(index=False),
    )
    logging.info(f"Uploaded {output_directory_path} to {bucket}")
