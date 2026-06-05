#!/usr/bin/env python3
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
Centralized Logging Configuration

Provides a single point of configuration for all Alert Bridge components.
Reads logging configuration from config.yaml and applies it consistently.
"""

import logging
import os
import re
import yaml
from typing import Dict, Any


# Regex pattern to match base64 data URLs: data:<mime>;base64,<data>
_BASE64_DATA_URL_PATTERN = re.compile(
    r'(data:[a-zA-Z0-9/+.-]+;base64,)([A-Za-z0-9+/=]{100,})',
    re.IGNORECASE
)

# Default truncation length for base64 content (show first N chars)
_BASE64_TRUNCATE_LENGTH = 50


def _truncate_base64(text: str, max_length: int = _BASE64_TRUNCATE_LENGTH) -> str:
    """Truncate base64 data URLs in text to prevent excessively long logs.
    
    Replaces: data:video/mp4;base64,AAABBBCCC...very_long_string...
    With:     data:video/mp4;base64,AAABBBCCC...[truncated 12345 chars]
    """
    def replacer(match):
        prefix = match.group(1)  # e.g., "data:video/mp4;base64,"
        data = match.group(2)    # the actual base64 string
        if len(data) > max_length:
            return f"{prefix}{data[:max_length]}...[truncated {len(data) - max_length} chars]"
        return match.group(0)
    
    return _BASE64_DATA_URL_PATTERN.sub(replacer, text)


class _SingleLineFormatter(logging.Formatter):
    """Formatter that collapses newlines and carriage returns into spaces.

    Keeps logs single-line even if the message contains embedded newlines.
    Also truncates base64 data URLs to prevent excessively long logs.
    """

    def __init__(self, fmt=None, datefmt=None, style='%', truncate_base64: bool = True):
        super().__init__(fmt, datefmt, style)
        self.truncate_base64 = truncate_base64

    def format(self, record: logging.LogRecord) -> str:
        original_msg = record.getMessage()
        # Replace newlines and carriage returns to keep one line
        safe_msg = original_msg.replace("\n", " ").replace("\r", " ")
        # Truncate base64 content if enabled
        if self.truncate_base64:
            safe_msg = _truncate_base64(safe_msg)
        record.message = safe_msg
        formatted = super().format(record)
        formatted = formatted.replace("\n", " ").replace("\r", " ")
        if self.truncate_base64:
            formatted = _truncate_base64(formatted)
        return formatted

def setup_logging(config_file: str = "config.yaml") -> None:
    """
    Setup logging configuration from config.yaml for all Alert Bridge components.
    
    Args:
        config_file: Path to configuration file containing logging settings
        
    Raises:
        FileNotFoundError: If config file is not found
        ValueError: If logging configuration is invalid
    """
    try:
        # Load configuration
        with open(config_file, 'r') as file:
            config = yaml.safe_load(file)
        
        # Get logging configuration
        logging_config = config.get('logging', {})
        
        # Extract settings with defaults and allow env overrides
        log_level = os.getenv('LOG_LEVEL_ROOT', logging_config.get('level', 'INFO')).upper()
        log_format = logging_config.get('format', '%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s')
        third_party_level = os.getenv('LOG_LEVEL_3P', logging_config.get('third_party_level', 'WARNING')).upper()
        single_line = os.getenv('LOG_SINGLE_LINE', 'true').lower() in ('1', 'true', 'yes')
        truncate_base64 = os.getenv('LOG_TRUNCATE_BASE64', logging_config.get('truncate_base64', 'true'))
        truncate_base64 = str(truncate_base64).lower() in ('1', 'true', 'yes')
        
        # Validate log level
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if log_level not in valid_levels:
            raise ValueError(f"Invalid log level '{log_level}'. Must be one of: {valid_levels}")
        
        # Configure logging
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format=log_format,
            force=True  # Override any existing configuration
        )

        # Apply single-line formatter globally (keeps minimal impact on existing call sites)
        if single_line or truncate_base64:
            root_logger = logging.getLogger()
            for handler in root_logger.handlers:
                try:
                    handler.setFormatter(_SingleLineFormatter(log_format, truncate_base64=truncate_base64))
                except Exception:
                    # Best-effort; keep default formatter if something goes wrong
                    pass

        # Demote noisy third-party libraries in production
        for name in (
            'urllib3',
            'urllib3.connectionpool',
            'httpcore',
            'httpcore.connection',
            'httpcore.http11',
            'httpx',
            'httpx._client',
            'openai',
            'openai._base_client',
            'elastic_transport',
            'elastic_transport.transport',
            'elasticsearch',
        ):
            try:
                logging.getLogger(name).setLevel(getattr(logging, third_party_level, logging.WARNING))
            except Exception:
                # Ignore unknown logger names
                pass
        
        # Log successful configuration
        logger = logging.getLogger(__name__)
        logger.info(
            f"Logging configured",
            extra={
                "config_file": config_file,
                "log_level": log_level,
                "log_format": log_format,
                "third_party_level": third_party_level,
                "single_line": single_line,
                "truncate_base64": truncate_base64,
            },
        )
        
    except FileNotFoundError:
        # Fallback to default configuration
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger(__name__)
        logger.warning(f"Config file '{config_file}' not found. Using default logging configuration.")
        
    except Exception as e:
        # Fallback to default configuration
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to load logging configuration from '{config_file}': {e}. Using defaults.")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with proper naming convention.
    
    Args:
        name: Logger name (typically __name__ or class name)
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def enforce_log_level(config_file: str = "config.yaml") -> None:
    """
    Re-apply configured log level to all loggers.
    Call after all modules are initialized to override any hardcoded setLevel() calls.
    """
    try:
        with open(config_file, 'r') as file:
            config = yaml.safe_load(file)
        
        log_level = os.getenv('LOG_LEVEL_ROOT', config.get('logging', {}).get('level', 'INFO')).upper()
        third_party_level = os.getenv('LOG_LEVEL_3P', config.get('logging', {}).get('third_party_level', 'WARNING')).upper()
        configured_level = getattr(logging, log_level, logging.INFO)
        tp_level = getattr(logging, third_party_level, logging.WARNING)
        
        # Set root logger level to ensure new loggers inherit correct level
        logging.getLogger().setLevel(configured_level)
        
        # Third-party libraries to demote
        third_party_prefixes = (
            'urllib3', 'httpcore', 'httpx', 'openai', 'elastic_transport', 'elasticsearch',
        )
        
        # Set all existing loggers
        for logger_name in logging.root.manager.loggerDict:
            if any(logger_name.startswith(prefix) for prefix in third_party_prefixes):
                logging.getLogger(logger_name).setLevel(tp_level)
            else:
                logging.getLogger(logger_name).setLevel(configured_level)
    except Exception:
        pass 