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
Path validation utilities for secure file operations.
Prevents directory traversal attacks and validates path accessibility.
"""

import os
from typing import Optional


def validate_directory_path(path: str, 
                          path_type: str = "directory",
                          must_exist: bool = False,
                          create_if_missing: bool = False,
                          require_read: bool = False,
                          require_write: bool = False) -> str:
    """
    Validate and sanitize a directory path to prevent security vulnerabilities.
    
    Args:
        path: The directory path to validate
        path_type: Description of the path type for error messages
        must_exist: Whether the directory must already exist
        create_if_missing: Whether to create the directory if it doesn't exist
        require_read: Whether read permission is required
        require_write: Whether write permission is required
        
    Returns:
        str: The validated and normalized absolute path
        
    Raises:
        ValueError: If path is empty or invalid
        FileNotFoundError: If path doesn't exist and must_exist=True
        NotADirectoryError: If path exists but is not a directory
        PermissionError: If required permissions are not available
        OSError: If directory creation fails
    """
    if not path:
        raise ValueError(f"{path_type} path cannot be empty")
    
    # Resolve the path to prevent directory traversal attacks
    normalized_path = os.path.abspath(path)
    
    # Check if path exists
    path_exists = os.path.exists(normalized_path)
    
    if not path_exists:
        if must_exist:
            raise FileNotFoundError(f"{path_type} does not exist: {normalized_path}")
        elif create_if_missing:
            try:
                os.makedirs(normalized_path, exist_ok=True)
                path_exists = True
            except OSError as e:
                raise OSError(f"Cannot create {path_type}: {normalized_path}. Error: {e}")
    
    # If path exists, validate it's a directory
    if path_exists:
        if not os.path.isdir(normalized_path):
            raise NotADirectoryError(f"{path_type} is not a directory: {normalized_path}")
        
        # Check permissions
        if require_read and not os.access(normalized_path, os.R_OK):
            raise PermissionError(f"No read permission for {path_type}: {normalized_path}")
        
        if require_write and not os.access(normalized_path, os.W_OK):
            raise PermissionError(f"No write permission for {path_type}: {normalized_path}")
    
    return normalized_path


def validate_file_path(path: str,
                      path_type: str = "file",
                      must_exist: bool = False,
                      require_read: bool = False,
                      require_write: bool = False) -> str:
    """
    Validate and sanitize a file path to prevent security vulnerabilities.
    
    Args:
        path: The file path to validate
        path_type: Description of the path type for error messages
        must_exist: Whether the file must already exist
        require_read: Whether read permission is required
        require_write: Whether write permission is required
        
    Returns:
        str: The validated and normalized absolute path
        
    Raises:
        ValueError: If path is empty
        FileNotFoundError: If file doesn't exist and must_exist=True
        IsADirectoryError: If path exists but is a directory
        PermissionError: If required permissions are not available
    """
    if not path:
        raise ValueError(f"{path_type} path cannot be empty")
    
    # Resolve the path to prevent directory traversal attacks
    normalized_path = os.path.abspath(path)
    
    # Check if path exists
    if os.path.exists(normalized_path):
        if os.path.isdir(normalized_path):
            raise IsADirectoryError(f"{path_type} path points to a directory: {normalized_path}")
        
        # Check permissions
        if require_read and not os.access(normalized_path, os.R_OK):
            raise PermissionError(f"No read permission for {path_type}: {normalized_path}")
        
        if require_write and not os.access(normalized_path, os.W_OK):
            raise PermissionError(f"No write permission for {path_type}: {normalized_path}")
    
    elif must_exist:
        raise FileNotFoundError(f"{path_type} does not exist: {normalized_path}")
    
    return normalized_path


def validate_output_directory(path: str, path_type: str = "output directory") -> str:
    """
    Convenience function to validate an output directory path.
    Creates the directory if it doesn't exist and ensures write permissions.
    
    Args:
        path: The output directory path to validate
        path_type: Description of the path type for error messages
        
    Returns:
        str: The validated and normalized absolute path
    """
    return validate_directory_path(
        path=path,
        path_type=path_type,
        create_if_missing=True,
        require_write=True
    )


def validate_input_directory(path: str, path_type: str = "input directory") -> str:
    """
    Convenience function to validate an input directory path.
    Ensures the directory exists and has read permissions.
    
    Args:
        path: The input directory path to validate
        path_type: Description of the path type for error messages
        
    Returns:
        str: The validated and normalized absolute path
    """
    return validate_directory_path(
        path=path,
        path_type=path_type,
        must_exist=True,
        require_read=True
    )


def validate_input_file(path: str, path_type: str = "input file") -> str:
    """
    Convenience function to validate an input file path.
    Ensures the file exists and has read permissions.
    
    Args:
        path: The input file path to validate
        path_type: Description of the path type for error messages
        
    Returns:
        str: The validated and normalized absolute path
    """
    return validate_file_path(
        path=path,
        path_type=path_type,
        must_exist=True,
        require_read=True
    )
