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
Utility functions for file operations and configuration management.

This module provides common utility functions for reading and writing
configuration files (YAML, JSON, text) used by the Profile Config Manager.
"""

import os
import sys
import json
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

# Add parent directory to path to import logger utility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from utils.logger import get_logger

# Configure YAML handler with sensible defaults
yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False
yaml.width = 4096  # Prevent line folding for readability
yaml.boolean_representation = ['False', 'True']  # Python-style booleans

# Get module-level logger
logger = get_logger(__name__)


def read_yaml_file(file_path: str) -> Dict[str, Any]:
    """
    Read and parse a YAML file.
    
    Args:
        file_path: Path to the YAML file
        
    Returns:
        Dictionary containing the parsed YAML content
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the YAML is invalid
    """
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"YAML file not found: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = yaml.load(f)
        
        logger.debug(f"Successfully read YAML file: {file_path}")
        return content
        
    except Exception as e:
        logger.error(f"Failed to read YAML file {file_path}: {e}")
        raise


def write_yaml_file(file_path: str, data: Dict[str, Any]) -> bool:
    """
    Write data to a YAML file.
    
    Args:
        file_path: Path to the YAML file
        data: Dictionary to write as YAML
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        file_path_obj = Path(file_path)
        if file_path_obj.parent != Path('.'):
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
        
        logger.info(f"Successfully wrote YAML file: {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to write YAML file {file_path}: {e}")
        return False


def read_text_file(file_path: str) -> str:
    """
    Read a text file and return its contents.
    
    Args:
        file_path: Path to the text file
        
    Returns:
        String containing the file contents
        
    Raises:
        FileNotFoundError: If the file doesn't exist
    """
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Text file not found: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        logger.debug(f"Successfully read text file: {file_path}")
        return content
        
    except Exception as e:
        logger.error(f"Failed to read text file {file_path}: {e}")
        raise


def write_text_file(file_path: str, content: str) -> bool:
    """
    Write content to a text file.
    
    Args:
        file_path: Path to the text file
        content: String content to write
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        file_path_obj = Path(file_path)
        if file_path_obj.parent != Path('.'):
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Successfully wrote text file: {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to write text file {file_path}: {e}")
        return False


def read_json_file(file_path: str) -> Dict[str, Any]:
    """
    Read and parse a JSON file.
    
    Args:
        file_path: Path to the JSON file
        
    Returns:
        Dictionary containing the parsed JSON content
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the JSON is invalid
    """
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"JSON file not found: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        
        logger.debug(f"Successfully read JSON file: {file_path}")
        return content
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in file {file_path}: {e}")
        raise ValueError(f"Invalid JSON in file {file_path}: {e}")
    except Exception as e:
        logger.error(f"Failed to read JSON file {file_path}: {e}")
        raise


def write_json_file(file_path: str, data: Dict[str, Any], indent: int = 2) -> bool:
    """
    Write data to a JSON file.
    
    Args:
        file_path: Path to the JSON file
        data: Dictionary to write as JSON
        indent: Number of spaces for indentation (default: 2)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        file_path_obj = Path(file_path)
        if file_path_obj.parent != Path('.'):
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        
        logger.info(f"Successfully wrote JSON file: {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to write JSON file {file_path}: {e}")
        return False


def detect_json_indent(raw_text: str) -> Optional[Union[int, str]]:
    """
    Detect the indentation token used in a JSON string.

    Scans lines for the first one that begins with leading whitespace.
    Returns:
      - str '\t'  if tab-indented
      - int N     if space-indented (N = number of leading spaces on first indented line)
      - None      if no indented line found (compact / single-line JSON)
    """
    for line in raw_text.splitlines():
        if line and line[0] == '\t':
            return '\t'
        if line and line[0] == ' ':
            count = len(line) - len(line.lstrip(' '))
            return count
    return None


def write_json_preserving(file_path: str, data: Dict[str, Any], original_raw: str) -> bool:
    """
    Write data to a JSON file, preserving the indentation style and
    trailing-newline convention of original_raw.

    - Calls detect_json_indent(original_raw) to get indent token.
    - compact (None): json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    - space/tab:      json.dumps(data, indent=indent, ensure_ascii=False)
    - Trailing newline: if original_raw ends with '\\n', ensure output ends
      with exactly one '\\n'. Otherwise strip any trailing '\\n'.
    - Ensures parent directory exists (Path(file_path).parent.mkdir).
    - Logs INFO on success, ERROR on exception.
    - Returns True on success, False on failure.
    """
    try:
        indent = detect_json_indent(original_raw)
        if indent is None:
            serialised = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        else:
            serialised = json.dumps(data, indent=indent, ensure_ascii=False)

        # Preserve trailing-newline convention of original_raw
        if original_raw.endswith('\n') and not serialised.endswith('\n'):
            serialised += '\n'
        elif not original_raw.endswith('\n') and serialised.endswith('\n'):
            serialised = serialised.rstrip('\n')

        # Ensure parent directory exists
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(serialised)

        logger.info(f"Successfully wrote JSON file (preserving style): {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to write JSON file {file_path}: {e}")
        return False


def ensure_directory_exists(directory_path: str) -> bool:
    """
    Ensure a directory exists, creating it if necessary.
    
    Args:
        directory_path: Path to the directory
        
    Returns:
        True if directory exists or was created successfully
    """
    try:
        os.makedirs(directory_path, exist_ok=True)
        logger.debug(f"Directory ensured: {directory_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create directory {directory_path}: {e}")
        return False


def file_exists(file_path: str) -> bool:
    """
    Check if a file exists.
    
    Args:
        file_path: Path to check
        
    Returns:
        True if file exists, False otherwise
    """
    return os.path.exists(file_path)


def update_config_parameters(file_path: str, parameter_updates: Dict[str, str]) -> bool:
    """
    Update configuration parameters in a text file by parsing lines directly.
    
    This function reads a configuration file, finds lines with parameters in the format
    'parameter_name=value', and updates them with new values while preserving
    indentation and comments.
    
    Args:
        file_path: Path to the configuration file
        parameter_updates: Dictionary mapping parameter names to new values
        
    Returns:
        True if successful, False otherwise
        
    Example:
        update_config_parameters('/path/to/config.txt', {
            'batch-size': '8',
            'max-batch-size': '16'
        })
    """
    try:
        if not file_exists(file_path):
            logger.error(f"Configuration file not found: {file_path}")
            return False
        
        # Read text file content and split into lines
        content = read_text_file(file_path)
        lines = content.splitlines()
        
        # Process each line and update configuration parameters
        updated_lines = []
        updated_params = set()
        
        for line in lines:
            line_stripped = line.strip()
            updated = False
            
            # Check if this line contains a parameter we need to update
            for param_name, new_value in parameter_updates.items():
                if line_stripped.startswith(f'{param_name}='):
                    # Preserve indentation and comments
                    indent = len(line) - len(line.lstrip())
                    prefix = ' ' * indent
                    
                    # Check if there's a comment at the end
                    comment_idx = line_stripped.find('#')
                    comment = ''
                    if comment_idx > 0:
                        comment = ' ' + line_stripped[comment_idx:]
                    
                    # Create new line with updated value
                    updated_line = f'{prefix}{param_name}={new_value}{comment}'
                    updated_lines.append(updated_line)
                    updated_params.add(param_name)
                    updated = True
                    logger.debug(f"Updated parameter: {param_name} = {new_value}")
                    break
            
            # If line wasn't updated, keep it as is
            if not updated:
                updated_lines.append(line)
        
        # Log any parameters that weren't found
        missing_params = set(parameter_updates.keys()) - updated_params
        if missing_params:
            logger.warning(f"Parameters not found in {file_path}: {missing_params}")
        
        # Reconstruct content and write back to file
        updated_content = '\n'.join(updated_lines)
        if write_text_file(file_path, updated_content):
            logger.info(f"Successfully updated {len(updated_params)} parameters in {file_path}")
            return True
        else:
            logger.error(f"Failed to write updated content to {file_path}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to update config parameters in {file_path}: {e}")
        return False

def append_if_missing(file_path: str, entries: Dict[str, str]) -> bool:
    """
    Append KEY=value lines to a file only if the KEY is not already present.

    Designed for .env files where new variables may need to be added without
    overwriting existing ones.

    Args:
        file_path: Path to the file
        entries: Dictionary mapping variable names to values

    Returns:
        True if successful, False otherwise
    """
    try:
        if not file_exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        content = read_text_file(file_path)
        lines = content.splitlines()

        existing_keys: set = set()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                existing_keys.add(stripped.split('=', 1)[0].strip())

        appended = []
        for key, value in entries.items():
            if key not in existing_keys:
                appended.append(f"{key}={value}")
                logger.debug(f"Will append: {key}={value}")
            else:
                logger.debug(f"Key already present, skipping: {key}")

        if not appended:
            logger.info(f"No new entries to append to {file_path}")
            return True

        if content and not content.endswith('\n'):
            content += '\n'

        content += '\n'.join(appended) + '\n'
        if write_text_file(file_path, content):
            logger.info(f"Appended {len(appended)} entries to {file_path}")
            return True
        return False

    except Exception as e:
        logger.error(f"Failed to append entries to {file_path}: {e}")
        return False


def text_replace(file_path: str, operations: List[Dict[str, Any]]) -> bool:
    """
    Perform ordered text-replacement operations on a file.

    Each operation in the list is a dict that must contain an ``action`` key.
    Supported actions:

    * ``replace`` – find-and-replace (literal or regex).
      Keys: ``pattern``, ``replacement``, ``regex`` (bool, default False),
      ``count`` (int, 0 = all, default 0).

    * ``comment_line`` – prefix matching lines with a comment marker.
      Keys: ``pattern`` (literal substring or regex), ``regex`` (bool),
      ``marker`` (str, default ``# ``).

    * ``insert_after`` – insert one or more lines after the first line that
      matches a pattern.
      Keys: ``pattern``, ``lines`` (list[str] or str), ``regex`` (bool).

    * ``append_lines`` – append lines to the end of the file.
      Keys: ``lines`` (list[str] or str).

    Args:
        file_path: Path to the target file
        operations: Ordered list of operation dicts

    Returns:
        True if all operations succeeded, False otherwise
    """
    import re as _re

    try:
        if not file_exists(file_path):
            logger.error(f"File not found for text_replace: {file_path}")
            return False

        content = read_text_file(file_path)

        for op in operations:
            action = op.get('action')

            if action == 'replace':
                pattern = op['pattern']
                replacement = op.get('replacement', '')
                use_regex = op.get('regex', False)
                count = op.get('count', 0)

                if use_regex:
                    content = _re.sub(pattern, replacement, content,
                                      count=count if count else 0)
                else:
                    if count:
                        content = content.replace(pattern, replacement, count)
                    else:
                        content = content.replace(pattern, replacement)
                logger.debug(f"text_replace: replaced pattern '{pattern}'")

            elif action == 'comment_line':
                pattern = op['pattern']
                use_regex = op.get('regex', False)
                marker = op.get('marker', '# ')
                new_lines = []
                for line in content.splitlines(True):
                    stripped = line.rstrip('\n\r')
                    matched = (
                        _re.search(pattern, stripped) if use_regex
                        else pattern in stripped
                    )
                    if matched and not stripped.lstrip().startswith(marker.strip()):
                        indent = len(stripped) - len(stripped.lstrip())
                        new_lines.append(
                            stripped[:indent] + marker + stripped[indent:] + '\n'
                        )
                    else:
                        new_lines.append(line if line.endswith('\n') else line + '\n')
                content = ''.join(new_lines)
                logger.debug(f"text_replace: commented lines matching '{pattern}'")

            elif action == 'insert_after':
                pattern = op['pattern']
                use_regex = op.get('regex', False)
                insert_lines = op.get('lines', [])
                if isinstance(insert_lines, str):
                    insert_lines = [insert_lines]

                insert_block = '\n'.join(insert_lines) + '\n'
                new_lines = []
                inserted = False
                for line in content.splitlines(True):
                    new_lines.append(line)
                    if not inserted:
                        stripped = line.rstrip('\n\r')
                        matched = (
                            _re.search(pattern, stripped) if use_regex
                            else pattern in stripped
                        )
                        if matched:
                            new_lines.append(insert_block)
                            inserted = True
                content = ''.join(new_lines)
                if inserted:
                    logger.debug(f"text_replace: inserted lines after '{pattern}'")
                else:
                    logger.warning(f"text_replace: no line matched '{pattern}' for insert_after")

            elif action == 'append_lines':
                append = op.get('lines', [])
                if isinstance(append, str):
                    append = [append]
                if content and not content.endswith('\n'):
                    content += '\n'
                content += '\n'.join(append) + '\n'
                logger.debug(f"text_replace: appended {len(append)} lines")

            else:
                logger.warning(f"text_replace: unknown action '{action}'")

        return write_text_file(file_path, content)

    except Exception as e:
        logger.error(f"text_replace failed on {file_path}: {e}")
        return False


def set_flow_for_lists(obj: Union[Dict, List, Any]) -> None:
    """
    Recursively set all lists to use flow style (inline) in YAML.
    
    Args:
        obj: Object to process (dict, list, or other)
    """
    if isinstance(obj, dict):
        for v in obj.values():
            set_flow_for_lists(v)
    elif isinstance(obj, CommentedSeq):
        obj.fa.set_flow_style()
        for item in obj:
            set_flow_for_lists(item)


# def expand_list_anchors(config_data: Dict[str, Any], path_keys: List[str]) -> List[Any]:
#     """
#     Expand list anchors in YAML data.
    
#     This function handles YAML anchor expansion for lists, which is not
#     automatically handled by standard YAML merge operations.
    
#     Args:
#         config_data: The full configuration dictionary
#         path_keys: Path to the list to expand (e.g., ['L4', '3d', 'file_operations'])
        
#     Returns:
#         Expanded list with all anchor references resolved
#     """
#     merger = YAMLListMerger(config_data)
#     return merger.get_merged_list(*path_keys)
