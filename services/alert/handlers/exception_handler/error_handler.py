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
import traceback
from typing import Any, Optional, Callable
from functools import wraps

from .vss_exceptions import (
    VSSException, VSSConnectionError, VSSModelError,
    VSSMediaUploadError, VSSAPIError, VSSRetryExhaustedError
)

logger = logging.getLogger(__name__)


class ErrorHandler:
    """Centralized error handling for VSS operations."""
    
    def __init__(self):
        """Initialize the ErrorHandler."""
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def handle_error(
        self, 
        error: Exception, 
        operation: str,
        context: dict = None
    ) -> None:
        """
        Handle and log errors with context.
        
        Args:
            error: The exception that occurred
            operation: Description of the operation that failed
            context: Additional context information
        """
        error_message = f"Error during {operation}: {str(error)}"
        
        if context:
            error_message += f" | Context: {context}"
            
        self.logger.error(error_message)
        
        # Log stack trace for unexpected errors
        if not isinstance(error, VSSException):
            self.logger.error(f"Stack trace:\n{traceback.format_exc()}")
            
    def with_retry(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backoff_factor: float = 2.0,
        exceptions: tuple = (Exception,)
    ) -> Callable:
        """
        Decorator for adding retry logic to functions.
        
        Args:
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries in seconds
            backoff_factor: Factor to multiply delay by after each retry
            exceptions: Tuple of exceptions to catch and retry on
            
        Returns:
            Decorated function with retry logic
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs) -> Any:
                last_exception = None
                
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        last_exception = e
                        
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (backoff_factor ** attempt)
                            self.logger.warning(
                                f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}). "
                                f"Retrying in {wait_time} seconds... Error: {str(e)}"
                            )
                            import time
                            time.sleep(wait_time)
                        else:
                            self.logger.error(
                                f"{func.__name__} failed after {max_retries} attempts. "
                                f"Final error: {str(e)}"
                            )
                            
                # All retries exhausted
                raise VSSRetryExhaustedError(
                    f"Operation '{func.__name__}' failed after {max_retries} attempts"
                ) from last_exception
                
            return wrapper
        return decorator
        
    def log_request_error(
        self,
        operation: str,
        error: Exception,
        request_details: dict = None
    ) -> None:
        """
        Log detailed information about a request error.
        
        Args:
            operation: Description of the operation
            error: The exception that occurred
            request_details: Details about the request
        """
        self.logger.error(f"Request error during {operation}: {error}")
        
        if hasattr(error, 'response'):
            response = error.response
            self.logger.error(f"Response status: {response.status_code}")
            if hasattr(response, 'text'):
                self.logger.error(f"Response message: {response.text}")
                
        if request_details:
            self.logger.error(f"Request details: {request_details}")
            
    def create_safe_wrapper(self, default_return: Any = None) -> Callable:
        """
        Create a decorator that catches exceptions and returns a default value.
        
        Args:
            default_return: Value to return when an exception occurs
            
        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs) -> Any:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    self.handle_error(e, func.__name__)
                    return default_return
            return wrapper
        return decorator 