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
import re

import boto3


def get_s3_client(
    aws_access_key_id,
    aws_secret_access_key,
    region,
    max_pool_connections=50,
):
    """Create an S3 client with a bounded connection pool and retries."""
    return boto3.client(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=region,
        config=boto3.session.Config(
            max_pool_connections=max_pool_connections,
            retries={"max_attempts": 3},
        ),
    )


def _get_s3_client_from_env(env_variables, max_pool_connections=50):
    return get_s3_client(
        env_variables["AWS_ACCESS_KEY_ID"],
        env_variables["AWS_SECRET_ACCESS_KEY"],
        env_variables["AWS_REGION"],
        max_pool_connections=max_pool_connections,
    )


def list_files(env_variables, aws_s3_base_prefix_path):
    """Yield file keys under the given S3 prefix."""
    s3_client = _get_s3_client_from_env(env_variables)
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=env_variables["AWS_BUCKET"],
        Prefix=aws_s3_base_prefix_path,
    ):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def count_the_files_in_s3(env_variables, aws_s3_base_prefix_path):
    """Count objects under a given S3 prefix."""
    s3_client = _get_s3_client_from_env(env_variables)
    paginator = s3_client.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(
        Bucket=env_variables["AWS_BUCKET"],
        Prefix=aws_s3_base_prefix_path,
    )

    total_count = 0
    for page in page_iterator:
        total_count += page.get("KeyCount", 0)

    return total_count


def get_ldrcolor_directories(s3_client, bucket, prefix):
    """Return sorted S3 directory prefixes that contain LdrColor files."""
    ldrcolor_directories = set()

    try:
        paginator = s3_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if "Contents" in page:
                for obj in page["Contents"]:
                    key = obj["Key"]
                    path = key.split("/")
                    if len(path) >= 2 and path[-2] == "LdrColor":
                        directory_path = "/".join(path[:-1]) + "/"
                        ldrcolor_directories.add(directory_path)

        if ldrcolor_directories:
            return sorted(list(ldrcolor_directories))

        logging.error("No directories containing 'LdrColor/' found")
        exit(1)

    except Exception as exc:
        logging.error(f"Error: {exc}")
        exit(1)


def format_aws_s3_base_prefix_path(aws_s3_base_prefix_path):
    """
    Normalize an S3 base prefix so non-empty prefixes end with ``/``.

    :param aws_s3_base_prefix_path: S3 prefix from environment/configuration.
    :type aws_s3_base_prefix_path: str
    :return: The original empty prefix, or the prefix with a trailing slash.
    :rtype: str
    """
    if aws_s3_base_prefix_path != "" and not aws_s3_base_prefix_path.endswith("/"):
        return f"{aws_s3_base_prefix_path}/"
    return aws_s3_base_prefix_path


def convert_https_to_s3_url(file_source):
    """Convert HTTPS S3 URLs to S3 protocol URLs."""
    try:
        return re.sub(
            r"^https?://s3(\.[^/]+)?\.amazonaws\.com/([^/]+)/(.*)",
            r"s3://\2/\3",
            re.sub(
                r"^https?://([^/]+)\.s3(\.[^/]+)?\.amazonaws\.com/(.*)",
                r"s3://\1/\3",
                file_source,
            ),
        )
    except Exception as exc:
        logging.error(f"Error converting http-server to s3 url: {exc}")
        raise
