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

from spatialai_data_utils.datasets.cloud_utils.s3_utils.common import get_s3_client

def combine_and_upload_detection_metrics_csv_to_s3(
    env_variables,
    input_csvs_path,
    local_output_csv_dump_path,
    s3_dump_path,
):
    """
    Combine per-sensor detection metrics CSV files and upload the result to S3.

    Recursively searches ``input_csvs_path`` for ``detection_metrics.csv`` files,
    concatenates them into one CSV, writes the combined CSV locally, and uploads
    the combined content to the configured S3 bucket.

    :param env_variables: Environment/configuration values containing AWS
        credentials, region, and bucket name.
    :type env_variables: dict
    :param input_csvs_path: Root directory to search for detection metrics CSV
        files.
    :type input_csvs_path: str
    :param local_output_csv_dump_path: Local path where the combined CSV is
        written before upload.
    :type local_output_csv_dump_path: str
    :param s3_dump_path: Destination object key for the combined CSV in S3.
    :type s3_dump_path: str
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
        upload_csv_to_s3(
            combined_df,
            env_variables["AWS_ACCESS_KEY_ID"],
            env_variables["AWS_SECRET_ACCESS_KEY"],
            env_variables["AWS_REGION"],
            env_variables["AWS_BUCKET"],
            s3_dump_path,
        )
    else:
        logging.info(
            f"!!No detection metrics csv files found in {input_csvs_path}. Exiting..."
        )


def upload_csv_to_s3(
    df,
    access_key_id,
    secret_access_key,
    region,
    bucket,
    output_directory_path,
):
    """
    Upload a pandas DataFrame as CSV content to S3.

    :param df: DataFrame to serialize as CSV.
    :type df: pandas.DataFrame
    :param access_key_id: AWS access key ID.
    :type access_key_id: str
    :param secret_access_key: AWS secret access key.
    :type secret_access_key: str
    :param region: AWS region for the S3 client.
    :type region: str
    :param bucket: Destination S3 bucket.
    :type bucket: str
    :param output_directory_path: Destination object key in S3.
    :type output_directory_path: str
    :return: None.
    :rtype: None
    """
    s3_client = get_s3_client(access_key_id, secret_access_key, region)
    logging.info(f"Uploading {output_directory_path} to {bucket}")
    s3_client.put_object(
        Bucket=bucket,
        Key=output_directory_path,
        Body=df.to_csv(index=False),
    )
    logging.info(f"Uploaded {output_directory_path} to {bucket}")
