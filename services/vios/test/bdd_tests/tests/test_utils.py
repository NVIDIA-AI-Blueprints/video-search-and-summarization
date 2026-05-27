# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Shared test utilities for all BDD tests.

Provides common functions for error reporting and test assertions.
"""
import logging
from typing import List, Dict, Any

import pytest

logger = logging.getLogger(__name__)


def assert_with_detailed_failure(
    condition: bool,
    test_name: str,
    expected: str,
    actual: str,
    failed_items: List[Dict[str, Any]] = None,
    additional_info: str = None
) -> None:
    """
    Assert with detailed error logging for better debugging.
    
    Args:
        condition: Boolean condition to assert
        test_name: Name of the test (e.g., "Video Download Validation")
        expected: What was expected
        actual: What actually happened
        failed_items: List of failed items with details
        additional_info: Additional troubleshooting information
    """
    if condition:
        return
    
    # Log detailed error information
    logger.error("\n" + "=" * 60)
    logger.error("%s FAILED", test_name.upper())
    logger.error("=" * 60)
    
    if failed_items:
        logger.error("Failed Items:")
        for item in failed_items:
            logger.error("  - %s", item.get('description', str(item)))
    
    logger.error("")
    logger.error("Summary:")
    logger.error("  Expected: %s", expected)
    logger.error("  Actual: %s", actual)
    
    if additional_info:
        logger.error("")
        logger.error("Additional Information:")
        logger.error("  %s", additional_info)
    
    logger.error("=" * 60)
    
    # Build pytest failure message
    failure_msg = f"\n\n{test_name.upper()} FAILED\n\n"
    
    if failed_items:
        failure_msg += "Failed Items:\n"
        for item in failed_items:
            failure_msg += f"  - {item.get('description', str(item))}\n"
        failure_msg += "\n"
    
    failure_msg += f"Expected: {expected}\n"
    failure_msg += f"Actual: {actual}\n"
    
    if additional_info:
        failure_msg += f"\n{additional_info}\n"
    
    pytest.fail(failure_msg)


def format_validation_failure(
    valid_count: int,
    total_count: int,
    invalid_items: List[Dict[str, Any]],
    item_type: str = "item"
) -> Dict[str, Any]:
    """
    Format validation failure information for detailed error reporting.
    
    Args:
        valid_count: Number of valid items
        total_count: Total number of items
        invalid_items: List of invalid items with error details
        item_type: Type of item being validated (e.g., "video", "picture")
        
    Returns:
        Dictionary with formatted failure information
    """
    failed_items = []
    for item in invalid_items:
        desc = f"{item_type} "
        
        if 'stream_id' in item:
            desc += f"stream={item['stream_id'][:12]}... "
        if 'index' in item:
            desc += f"index={item['index']} "
        if 'error' in item:
            desc += f"error: {item['error']}"
        
        failed_items.append({'description': desc})
    
    return {
        'failed_items': failed_items,
        'expected': f"All {total_count} {item_type}s must be valid",
        'actual': f"{valid_count}/{total_count} {item_type}s are valid ({total_count - valid_count} invalid)",
        'additional_info': f"Check logs above for detailed validation errors"
    }
