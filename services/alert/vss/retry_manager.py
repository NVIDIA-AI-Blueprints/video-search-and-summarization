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
import time
from typing import Callable, Any, TypeVar, Optional, Set, Type
from functools import wraps

from handlers.exception_handler.vss_exceptions import (
    VSSRetryExhaustedError, VSSMediaUploadError, VSSPromptError, 
    VSSException
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RetryManager:
    """Manages retry logic for VSS operations."""
    
    # Define non-retriable exception types
    NON_RETRIABLE_EXCEPTIONS: Set[Type[Exception]] = {
        VSSMediaUploadError,  # File not found, invalid file, etc.
        VSSPromptError,       # No suitable prompt found
    }
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        """
        Initialize the retry manager.
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay between retries (exponential backoff)
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def _is_retriable(self, exception: Exception) -> bool:
        """
        Check if an exception is retriable.
        
        Args:
            exception: The exception to check
            
        Returns:
            True if the exception should be retried, False otherwise
        """
        # Check if it's a non-retriable exception type
        for non_retriable_type in self.NON_RETRIABLE_EXCEPTIONS:
            if isinstance(exception, non_retriable_type):
                # Special case: Check if it's a file not found error
                if isinstance(exception, VSSMediaUploadError) and "file not found" in str(exception).lower():
                    return False
                # For other VSSMediaUploadError cases, we might want to retry (e.g., network issues)
                # But for now, treat all media upload errors as non-retriable
                return False
        
        return True
    
    def with_retries(
        self,
        operation: Callable[..., T],
        operation_name: str,
        *args,
        **kwargs
    ) -> T:
        """
        Execute an operation with retry logic.
        
        Args:
            operation: The function to execute
            operation_name: Name of the operation for logging
            *args: Arguments to pass to the operation
            **kwargs: Keyword arguments to pass to the operation
            
        Returns:
            The result of the operation
            
        Raises:
            VSSRetryExhaustedError: If all retries are exhausted
            Non-retriable exceptions are re-raised immediately
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                self.logger.debug("Attempting operation", extra={
                    "operation": operation_name,
                    "attempt": attempt + 1,
                    "max_attempts": self.max_retries
                })
                
                result = operation(*args, **kwargs)
                
                if attempt > 0:
                    self.logger.info("Operation succeeded after retry", extra={
                        "operation": operation_name,
                        "attempt": attempt + 1
                    })
                
                return result
                
            except Exception as e:
                last_error = e
                
                # Check if this is a non-retriable error
                if not self._is_retriable(e):
                    self.logger.error("Non-retriable error encountered", extra={
                        "operation": operation_name,
                        "error": str(e),
                        "error_type": type(e).__name__
                    })
                    # Re-raise the original exception without wrapping
                    raise
                
                if attempt < self.max_retries - 1:
                    wait_time = self.base_delay * (2 ** attempt)
                    self.logger.warning("Operation failed, retrying", extra={
                        "operation": operation_name,
                        "attempt": attempt + 1,
                        "wait_time": wait_time,
                        "error": str(e),
                        "error_type": type(e).__name__
                    })
                    time.sleep(wait_time)
                else:
                    self.logger.error("All retries exhausted", extra={
                        "operation": operation_name,
                        "attempts": self.max_retries,
                        "error": str(e)
                    })
        
        raise VSSRetryExhaustedError(
            f"Operation '{operation_name}' failed after {self.max_retries} attempts: {str(last_error)}"
        )
    
    def retry_decorator(self, operation_name: Optional[str] = None):
        """
        Decorator for adding retry logic to functions.
        
        Args:
            operation_name: Name of the operation for logging
            
        Returns:
            Decorator function
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @wraps(func)
            def wrapper(*args, **kwargs) -> T:
                name = operation_name or func.__name__
                return self.with_retries(func, name, *args, **kwargs)
            return wrapper
        return decorator 