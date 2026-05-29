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
import requests
import time
import threading
from typing import Optional, Dict
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class ThreadSafeSessionManager:
    """Thread-safe session manager for VSS API communication."""
    
    def __init__(self, base_url: str, enable_pooling: bool = True, pool_size: int = 5,
                 retry_interval: int = 5, max_retry_interval: int = 60):
        """
        Initialize the ThreadSafeSessionManager.
        
        Args:
            base_url: The base URL for the VSS API
            enable_pooling: Whether to use connection pooling
            pool_size: Size of the connection pool
            retry_interval: Initial retry interval in seconds (default: 5)
            max_retry_interval: Maximum retry interval in seconds (default: 60)
        """
        self.base_url = base_url
        self.enable_pooling = enable_pooling
        self.pool_size = pool_size
        self.retry_interval = retry_interval
        self.max_retry_interval = max_retry_interval
        
        # Thread-local storage for sessions
        self._local = threading.local()
        
        # Shared state with locks
        self._model_id: Optional[str] = None
        self._model_id_lock = threading.RLock()
        self._connected = False
        self._connection_lock = threading.RLock()
        
        # Connection pool (if enabled)
        self._session_pool: Dict[int, requests.Session] = {}
        self._pool_lock = threading.Lock()
        self._pool_index = 0
        
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def initialize(self) -> None:
        """Initialize the session manager with limited retry attempts."""
        with self._model_id_lock:
            if self._model_id is None:
                max_attempts = 3
                retry_interval = self.retry_interval
                
                self.logger.info(f"Initializing VSS connection (max {max_attempts} attempts)...")
                
                for attempt in range(1, max_attempts + 1):
                    temp_session = requests.Session()
                    
                    try:
                        self.logger.info(f"Attempting to connect to VSS (attempt {attempt}/{max_attempts})...")
                        self._model_id = self._get_model_id_with_session(temp_session)
                        
                        if self._model_id:
                            self.logger.info(f"Successfully connected to VSS with model ID: {self._model_id}")
                            self._set_connected(True)
                            return
                        else:
                            self.logger.warning(f"VSS models endpoint returned no models (attempt {attempt}/{max_attempts})")
                            
                    except Exception as e:
                        self.logger.warning(f"Failed to connect to VSS (attempt {attempt}/{max_attempts}): {e}")
                    finally:
                        temp_session.close()
                    
                    # Don't wait after the last attempt
                    if attempt < max_attempts:
                        self.logger.info(f"Retrying in {retry_interval} seconds...")
                        time.sleep(retry_interval)
                        # Simple exponential backoff
                        retry_interval = min(retry_interval * 2, self.max_retry_interval)
                
                # All attempts failed
                self.logger.warning(
                    f"Failed to connect to VSS after {max_attempts} attempts. "
                    "Proceeding without VSS connection. API calls will handle connection errors."
                )
                self._set_connected(False)
                    
            else:
                self.logger.info(f"Already initialized with model ID: {self._model_id}")
                
    def _set_connected(self, connected: bool) -> None:
        """Set connection status (thread-safe)."""
        with self._connection_lock:
            self._connected = connected
            
    def is_connected(self) -> bool:
        """Check if connected to VSS (thread-safe)."""
        with self._connection_lock:
            return self._connected
    
    def get_session(self) -> requests.Session:
        """
        Get a session for the current thread.
        
        Returns:
            The requests session for the current thread
        """
        if self.enable_pooling:
            return self._get_pooled_session()
        else:
            return self._get_thread_local_session()
    
    def _get_thread_local_session(self) -> requests.Session:
        """Get or create a thread-local session."""
        if not hasattr(self._local, 'session') or self._local.session is None:
            self._local.session = requests.Session()
            self.logger.debug(f"Created new session for thread {threading.current_thread().name}")
        return self._local.session
    
    def _get_pooled_session(self) -> requests.Session:
        """Get a session from the pool."""
        thread_id = threading.get_ident()
        
        with self._pool_lock:
            # Check if this thread already has a session
            if thread_id in self._session_pool:
                return self._session_pool[thread_id]
            
            # Create a new session for this thread
            if len(self._session_pool) < self.pool_size:
                session = requests.Session()
                self._session_pool[thread_id] = session
                self.logger.debug(f"Added session to pool for thread {threading.current_thread().name}")
                return session
            else:
                # Pool is full, return a shared session (round-robin)
                sessions = list(self._session_pool.values())
                session = sessions[self._pool_index % len(sessions)]
                self._pool_index += 1
                return session
    
    @contextmanager
    def session_context(self):
        """Context manager for session usage."""
        session = self.get_session()
        try:
            yield session
        except Exception as e:
            self.logger.error(f"Error in session context: {e}")
            raise
        # Note: We don't close the session here as it may be reused
    
    def _get_model_id_with_session(self, session: requests.Session) -> Optional[str]:
        """Get the first available model ID using provided session."""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Fetching VSS models (attempt {attempt + 1}/{max_retries})")
                response = session.get(f"{self.base_url}/models", timeout=10)
                response.raise_for_status()
                models = response.json()
                
                if models and models.get("data"):
                    model_id = models["data"][0]["id"]
                    self.logger.info(f"VSS model selected: {model_id}")
                    return model_id
                else:
                    self.logger.warning("No VSS models available")
                    
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    
            except requests.RequestException as e:
                self.logger.error(f"Failed to fetch VSS models: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
        
        return None
    
    def get_model_id(self) -> Optional[str]:
        """
        Get the current model ID (thread-safe).
        
        Returns:
            The model ID or None if not available
        """
        with self._model_id_lock:
            return self._model_id
    
    def recreate_session(self) -> requests.Session:
        """Recreate the session for the current thread."""
        if self.enable_pooling:
            thread_id = threading.get_ident()
            with self._pool_lock:
                if thread_id in self._session_pool:
                    old_session = self._session_pool[thread_id]
                    try:
                        old_session.close()
                    except Exception:
                        pass
                    self._session_pool[thread_id] = requests.Session()
                    return self._session_pool[thread_id]
        else:
            if hasattr(self._local, 'session'):
                try:
                    self._local.session.close()
                except Exception:
                    pass
            self._local.session = requests.Session()
            return self._local.session
        
        # Fallback
        return requests.Session()
    
    def close(self) -> None:
        """Close all sessions."""
        # Close thread-local sessions
        if hasattr(self._local, 'session'):
            try:
                self._local.session.close()
            except Exception as e:
                self.logger.debug(f"Error closing thread-local session: {e}")
            self._local.session = None
        
        # Close pooled sessions
        with self._pool_lock:
            for session in self._session_pool.values():
                try:
                    session.close()
                except Exception as e:
                    self.logger.debug(f"Error closing pooled session: {e}")
            self._session_pool.clear()
        
        self.logger.info("All sessions closed")
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass


# Backward compatibility wrapper
class SessionManager(ThreadSafeSessionManager):
    """Backward compatible session manager that is thread-safe."""
    
    def __init__(self, base_url: str):
        """Initialize with thread safety enabled by default."""
        super().__init__(base_url, enable_pooling=True, pool_size=5)
        self.logger.info("Using thread-safe SessionManager with connection pooling") 