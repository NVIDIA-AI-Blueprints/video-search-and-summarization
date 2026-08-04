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

"""
Centralized logging configuration module.

This module provides a standardized logging setup for the entire application.
It follows Python logging best practices by:
- Configuring logging once at the application entry point
- Using module-level loggers via logging.getLogger(__name__)
- Supporting environment variable-based log level configuration
- Providing consistent log formatting across all modules
"""

import logging
import os
import sys


# Flag to ensure logging is configured only once
_logging_configured = False


def setup_logging(log_level: str = None) -> None:
    """
    Configure logging for the entire application.
    
    This function should be called once at application startup (e.g., in the main application module).
    It sets up the root logger with a consistent format and log level.
    
    Args:
        log_level: Log level to use (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                  If None, reads from LOG_LEVEL environment variable (default: INFO)
    
    Example:
        from utils.logger import setup_logging
        setup_logging()  # Call once at application startup
    """
    global _logging_configured
    
    # Avoid multiple configuration calls
    if _logging_configured:
        return
    
    # Determine log level from parameter or environment variable
    if log_level is None:
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    
    # Validate log level
    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        print(f"Invalid log level: {log_level}, defaulting to INFO", file=sys.stderr)
        numeric_level = logging.INFO
    
    # Configure root logger
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True  # Force reconfiguration if already configured
    )
    
    _logging_configured = True
    
    # Log initial configuration
    root_logger = logging.getLogger(__name__)
    root_logger.info(f"Logging configured with level: {log_level}")


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance for a specific module.
    
    This function returns a module-specific logger. If logging hasn't been 
    configured yet, it will set up logging with default settings.
    
    Args:
        name: Name for the logger (typically __name__ from the calling module).
              If None, returns the root logger.
    
    Returns:
        logging.Logger: A configured logger instance
    
    Example:
        from utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("This is an info message")
    """
    # Ensure logging is configured
    if not _logging_configured:
        setup_logging(os.environ.get('LOG_LEVEL', 'INFO'))
    
    return logging.getLogger(name)


# # For backward compatibility - prefer using get_logger(__name__) in new code
# def configure_logger(log_level: str = None) -> logging.Logger:
#     """
#     Legacy function for backward compatibility.
    
#     DEPRECATED: Use get_logger(__name__) instead for proper module-level logging.
    
#     This function attempts to get the caller's module name automatically,
#     but it's better to explicitly pass __name__ using get_logger(__name__).
#     """
#     # Ensure logging is set up
#     if not _logging_configured:
#         setup_logging(log_level)
    
#     # Try to get the caller's module name
#     import inspect
#     frame = inspect.currentframe()
#     try:
#         caller_frame = frame.f_back
#         caller_module = caller_frame.f_globals.get('__name__', 'unknown')
#         return logging.getLogger(caller_module)
#     finally:
#         del frame
