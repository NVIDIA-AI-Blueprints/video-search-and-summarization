#!/usr/bin/env python3
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

"""
Example: Uploading benchmark results to MinIO

This example demonstrates how to upload benchmark result files to MinIO object storage.
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path to import benchmark modules
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from vss_perf_common import MinIOUploader, upload_result_file  # noqa: E402

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def example_simple_upload():
    """Simple upload using convenience function."""
    logger.info("=== Example 1: Simple Upload ===")

    # Upload a file using the convenience function
    success = upload_result_file(
        local_file_path="vss-perf-report/benchmark_results/lvs_tp1_20260211_143022.json",
        service="LVS",  # Required parameter
        endpoint="10.111.52.248:9000",
        bucket="perf-results",
    )

    if success:
        logger.info("✓ Upload successful!")
    else:
        logger.error("✗ Upload failed!")

    return success


def example_uploader_class():
    """Upload using the MinIOUploader class with more control."""
    logger.info("\n=== Example 2: Using MinIOUploader Class ===")

    # Initialize uploader
    uploader = MinIOUploader(
        endpoint="10.111.52.248:9000",
        access_key="minioadmin",  # or use env var MINIO_ACCESS_KEY
        secret_key="minioadmin",  # or use env var MINIO_SECRET_KEY
        secure=False,  # HTTP (set True for HTTPS)
    )

    # Upload with standard path structure
    success = uploader.upload_benchmark_result(
        local_file_path="vss-perf-report/benchmark_results/lvs_tp1_20260211_143022.json",
        service="LVS",
        bucket_name="perf-results",
    )

    if success:
        logger.info("✓ Upload successful!")

        # List uploaded files
        logger.info("\nListing files in LVS folder:")
        objects = uploader.list_objects("perf-results", prefix="LVS/")
        for obj in objects:
            logger.info(f"  - {obj}")
    else:
        logger.error("✗ Upload failed!")

    return success


def example_custom_path():
    """Upload to a custom path in MinIO."""
    logger.info("\n=== Example 3: Custom Upload Path ===")

    uploader = MinIOUploader(
        endpoint="10.111.52.248:9000",
        secure=False,
    )

    # Upload to a custom path
    success = uploader.upload_file(
        local_file_path="vss-perf-report/benchmark_results/lvs_tp1_20260211_143022.json",
        bucket_name="perf-results",
        object_name="custom/path/my_result.json",  # Custom path
        content_type="application/json",
    )

    if success:
        logger.info("✓ Upload successful to custom path!")
    else:
        logger.error("✗ Upload failed!")

    return success


def main():
    """Run all examples."""
    logger.info("MinIO Upload Examples")
    logger.info("=" * 80)
    logger.info("\nMake sure you have:")
    logger.info("  1. MinIO running at http://10.111.52.248:9000")
    logger.info("  2. Credentials set (env vars MINIO_ACCESS_KEY and MINIO_SECRET_KEY)")
    logger.info("  3. A sample benchmark result file")
    logger.info("\n")

    try:
        # Run examples
        example_simple_upload()
        example_uploader_class()
        example_custom_path()

        logger.info("\n" + "=" * 80)
        logger.info("Examples complete!")
        logger.info("View results at: http://10.111.52.248:9000/browser/perf-results/LVS")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Example failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
