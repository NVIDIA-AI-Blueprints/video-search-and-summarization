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

import re

import boto3

SUPPORTED_STORAGE_PROVIDERS = {"aws", "gcs"}


def _create_boto3_storage_client(
    access_key_id,
    secret_access_key,
    region,
    max_pool_connections=50,
    endpoint_url=None,
    use_gcs_compatibility_config=False,
):
    """
    Create a boto3 S3-compatible storage client with bounded retries.

    :param access_key_id: Access key ID for the configured storage provider.
    :type access_key_id: str
    :param secret_access_key: Secret access key for the configured storage provider.
    :type secret_access_key: str
    :param region: Storage region, or provider-specific placeholder for GCS HMAC.
    :type region: str
    :param max_pool_connections: Maximum boto3 connection-pool size.
    :type max_pool_connections: int
    :param endpoint_url: Optional explicit storage endpoint URL. GCS HMAC uses
        ``GCS_ENDPOINT_URL``; AWS S3 leaves this unset for regional resolution.
    :type endpoint_url: str | None
    :param use_gcs_compatibility_config: Whether to apply boto3 settings
        required by GCS HMAC storage compatibility.
    :type use_gcs_compatibility_config: bool
    :return: Configured boto3 S3-compatible storage client.
    :rtype: botocore.client.BaseClient
    """
    config_kwargs = {
        "max_pool_connections": max_pool_connections,
        "retries": {"max_attempts": 3},
    }
    if use_gcs_compatibility_config:
        config_kwargs["signature_version"] = "s3v4"
        config_kwargs["request_checksum_calculation"] = "when_required"
        config_kwargs["response_checksum_validation"] = "when_required"
        config_kwargs["s3"] = {"addressing_style": "path"}

    return boto3.client(
        "s3",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
        endpoint_url=endpoint_url,
        config=boto3.session.Config(**config_kwargs),
    )


def get_storage_provider(env_variables):
    """Return the configured cloud storage provider."""
    provider = env_variables.get("STORAGE_PROVIDER", "aws").lower()
    if provider not in SUPPORTED_STORAGE_PROVIDERS:
        raise ValueError(
            f"Unsupported storage provider '{provider}'. "
            f"Supported providers: {sorted(SUPPORTED_STORAGE_PROVIDERS)}"
        )
    return provider


def get_storage_bucket(env_variables):
    """Return the configured bucket for the selected storage provider."""
    if get_storage_provider(env_variables) == "gcs":
        return env_variables["GCS_BUCKET"]
    return env_variables["AWS_BUCKET"]


def get_storage_client(env_variables, max_pool_connections=50):
    """
    Create a boto3 object-storage client for AWS S3 or GCS HMAC credentials.

    :param env_variables: Environment/configuration values containing
        ``STORAGE_PROVIDER`` and the selected provider credentials.
    :type env_variables: dict
    :param max_pool_connections: Maximum boto3 connection-pool size.
    :type max_pool_connections: int
    :return: Configured boto3 S3-compatible storage client.
    :rtype: botocore.client.BaseClient
    """
    if get_storage_provider(env_variables) == "gcs":
        # AWS S3 uses boto3's regional endpoint resolution; GCS HMAC
        # needs the GCS endpoint.
        return _create_boto3_storage_client(
            env_variables["GCS_HMAC_ACCESS_KEY_ID"],
            env_variables["GCS_HMAC_SECRET_ACCESS_KEY"],
            env_variables.get("GCS_REGION", "auto"),
            max_pool_connections=max_pool_connections,
            endpoint_url=env_variables["GCS_ENDPOINT_URL"],
            use_gcs_compatibility_config=True,
        )

    return _create_boto3_storage_client(
        env_variables["AWS_ACCESS_KEY_ID"],
        env_variables["AWS_SECRET_ACCESS_KEY"],
        env_variables["AWS_REGION"],
        max_pool_connections=max_pool_connections,
    )


def _get_storage_client_from_env(env_variables, max_pool_connections=50):
    return get_storage_client(env_variables, max_pool_connections=max_pool_connections)


def list_files(env_variables, storage_prefix_path):
    """Yield file keys under the given object-storage prefix."""
    storage_client = _get_storage_client_from_env(env_variables)
    paginator = storage_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=get_storage_bucket(env_variables),
        Prefix=storage_prefix_path,
    ):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def count_the_files_in_storage(env_variables, storage_prefix_path):
    """Count objects under a given object-storage prefix."""
    storage_client = _get_storage_client_from_env(env_variables)
    paginator = storage_client.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(
        Bucket=get_storage_bucket(env_variables),
        Prefix=storage_prefix_path,
    )

    total_count = 0
    for page in page_iterator:
        total_count += page.get("KeyCount", 0)

    return total_count


def get_ldrcolor_directories(storage_client, bucket, prefix):
    """Return sorted object-storage directory prefixes that contain LdrColor files."""
    ldrcolor_directories = set()

    paginator = storage_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if "Contents" in page:
            for obj in page["Contents"]:
                key = obj["Key"]
                path = key.split("/")
                if len(path) >= 2 and path[-2] == "LdrColor":
                    directory_path = "/".join(path[:-1]) + "/"
                    ldrcolor_directories.add(directory_path)

    if not ldrcolor_directories:
        raise RuntimeError(
            f"No directories containing 'LdrColor/' found under prefix: {prefix}"
        )

    return sorted(list(ldrcolor_directories))


def format_base_prefix_path(base_prefix_path):
    """
    Normalize a base prefix so non-empty prefixes end with ``/``.

    :param base_prefix_path: Prefix from environment/configuration.
    :type base_prefix_path: str
    :return: The original empty prefix, or the prefix with a trailing slash.
    :rtype: str
    """
    if base_prefix_path != "" and not base_prefix_path.endswith("/"):
        return f"{base_prefix_path}/"
    return base_prefix_path


def get_base_prefix_path(env_variables):
    """Return the provider-neutral base prefix path."""
    return env_variables["BASE_PREFIX_PATH"]


def _parse_aws_s3_url(file_source):
    """Parse AWS S3 URI, path-style HTTPS, or virtual-hosted HTTPS URLs."""
    if file_source.startswith("s3://"):
        parts = file_source.split("/", 3)
        if len(parts) < 4:
            raise ValueError(f"Invalid S3 URL: {file_source}")
        return "aws", parts[2], parts[3]

    s3_path_style = re.match(
        r"^https?://s3(\.[^/]+)?\.amazonaws\.com/([^/]+)/(.*)",
        file_source,
    )
    if s3_path_style:
        return "aws", s3_path_style.group(2), s3_path_style.group(3)

    s3_virtual_hosted = re.match(
        r"^https?://([^/]+)\.s3(\.[^/]+)?\.amazonaws\.com/(.*)",
        file_source,
    )
    if s3_virtual_hosted:
        return "aws", s3_virtual_hosted.group(1), s3_virtual_hosted.group(3)

    return None, None, None


def _parse_gcs_url(file_source):
    """Parse GCS URI, path-style HTTPS, or virtual-hosted HTTPS URLs."""
    if file_source.startswith("gs://"):
        parts = file_source.split("/", 3)
        if len(parts) < 4:
            raise ValueError(f"Invalid GCS URL: {file_source}")
        return "gcs", parts[2], parts[3]

    gcs_path_style = re.match(
        r"^https?://storage\.googleapis\.com/([^/]+)/(.*)",
        file_source,
    )
    if gcs_path_style:
        return "gcs", gcs_path_style.group(1), gcs_path_style.group(2)

    gcs_virtual_hosted = re.match(
        r"^https?://([^/]+)\.storage\.googleapis\.com/(.*)",
        file_source,
    )
    if gcs_virtual_hosted:
        return "gcs", gcs_virtual_hosted.group(1), gcs_virtual_hosted.group(2)

    return None, None, None


def parse_object_storage_url(file_source):
    """
    Parse an AWS S3 or GCS object URL.

    Supported forms include ``s3://bucket/key``, ``gs://bucket/key``, AWS S3
    path-style or virtual-hosted HTTPS URLs, and GCS path-style or
    virtual-hosted HTTPS URLs.

    :param file_source: Object URL to parse.
    :type file_source: str
    :return: Tuple of ``(provider, bucket, key)`` where provider is ``aws`` or
        ``gcs``. Returns ``(None, None, None)`` when the URL is not recognized
        as object storage.
    :rtype: tuple[str | None, str | None, str | None]
    :raises ValueError: If a recognized object-storage URI is malformed.
    """
    if file_source.startswith("s3://") or re.match(
        r"^https?://(s3(\.[^/]+)?\.amazonaws\.com|[^/]+\.s3(\.[^/]+)?\.amazonaws\.com)/",
        file_source,
    ):
        return _parse_aws_s3_url(file_source)

    if file_source.startswith("gs://") or re.match(
        r"^https?://(storage\.googleapis\.com|[^/]+\.storage\.googleapis\.com)/",
        file_source,
    ):
        return _parse_gcs_url(file_source)

    return None, None, None
