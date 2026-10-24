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

import os
from unittest.mock import MagicMock, patch

import pytest

from src.api.db import close_db, get_db, init_db


@pytest.fixture(autouse=True)
def reset_db_state():
    """Ensure clean database state before and after each test."""
    # Reset globals before test
    import src.api.db

    src.api.db._client = None
    src.api.db._db = None

    yield

    # Reset globals after test
    src.api.db._client = None
    src.api.db._db = None


class TestGetDb:
    """Test cases for the get_db function."""

    def test_get_db_not_initialized(self):
        """Test get_db raises RuntimeError when database is not initialized."""
        with pytest.raises(
            RuntimeError, match="Database not initialized. Call init_db\\(\\) first."
        ):
            get_db()

    def test_get_db_initialized(self):
        """Test get_db returns database when initialized."""
        # Directly set the global _db to simulate initialized state
        import src.api.db

        mock_database = MagicMock()
        src.api.db._db = mock_database

        result = get_db()
        assert result == mock_database


class TestInitDb:
    """Test cases for the init_db function."""

    @patch("src.api.db.MongoClient")
    @patch.dict(os.environ, {"MONGODB_URL": "mongodb://test:27017", "MONGODB_DB": "test_db"})
    def test_init_db_new_connection(self, mock_mongo_client):
        """Test init_db creates new connection with environment variables."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_db.flywheel_runs = mock_collection

        # Call init_db
        result = init_db()

        # Verify MongoClient was called with correct URL
        mock_mongo_client.assert_called_once_with("mongodb://test:27017")

        # Verify database was accessed with correct name
        mock_client.__getitem__.assert_called_once_with("test_db")

        # Verify indexes were created
        mock_collection.create_index.assert_any_call("workload_id")
        mock_collection.create_index.assert_any_call("started_at")
        assert mock_collection.create_index.call_count == 2

        # Verify correct database is returned
        assert result == mock_db

    @patch("src.api.db.MongoClient")
    def test_init_db_default_connection_params(self, mock_mongo_client):
        """Test init_db uses default connection parameters when env vars not set."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_db.flywheel_runs = mock_collection

        # Clear environment variables
        with patch.dict(os.environ, {}, clear=True):
            result = init_db()

        # Verify default values were used
        mock_mongo_client.assert_called_once_with("mongodb://localhost:27017")
        mock_client.__getitem__.assert_called_once_with("flywheel")

        assert result == mock_db

    @patch("src.api.db.MongoClient")
    def test_init_db_existing_connection_alive(self, mock_mongo_client):
        """Test init_db reuses existing connection when it's alive."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_admin = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_client.admin = mock_admin
        mock_db.flywheel_runs = mock_collection

        # Manually set up the connection state to simulate existing connection
        import src.api.db

        src.api.db._client = mock_client
        src.api.db._db = mock_db

        # Call init_db (should reuse connection)
        result = init_db()

        # Verify connection was not created again
        mock_mongo_client.assert_not_called()

        # Verify ping was called to check connection
        mock_admin.command.assert_called_with("ping")

        # Verify same database instance is returned
        assert result == mock_db

    @patch("src.api.db.MongoClient")
    def test_init_db_existing_connection_dead(self, mock_mongo_client):
        """Test init_db recreates connection when existing one is dead."""
        # Setup mocks for old connection
        old_mock_client = MagicMock()
        old_mock_db = MagicMock()
        old_mock_admin = MagicMock()

        old_mock_client.admin = old_mock_admin
        old_mock_admin.command.side_effect = Exception("Connection failed")

        # Setup mocks for new connection
        new_mock_client = MagicMock()
        new_mock_db = MagicMock()
        new_mock_collection = MagicMock()

        mock_mongo_client.return_value = new_mock_client
        new_mock_client.__getitem__.return_value = new_mock_db
        new_mock_db.flywheel_runs = new_mock_collection

        # Manually set up the old connection state
        import src.api.db

        src.api.db._client = old_mock_client
        src.api.db._db = old_mock_db

        # Call init_db (should recreate connection)
        result = init_db()

        # Verify new MongoClient was called
        mock_mongo_client.assert_called_once()

        # Verify old client was closed
        old_mock_client.close.assert_called_once()

        # Verify new database instance is returned
        assert result == new_mock_db

    @patch("src.api.db.MongoClient")
    def test_init_db_index_creation_error(self, mock_mongo_client):
        """Test init_db handles index creation errors gracefully."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_db.flywheel_runs = mock_collection

        # Mock index creation to raise an exception
        mock_collection.create_index.side_effect = Exception("Index creation failed")

        # init_db should propagate the exception
        with pytest.raises(Exception, match="Index creation failed"):
            init_db()


class TestCloseDb:
    """Test cases for the close_db function."""

    def test_close_db_with_connection(self):
        """Test close_db closes existing connection."""
        # Setup mock connection
        mock_client = MagicMock()

        # Manually set up the connection state
        import src.api.db

        src.api.db._client = mock_client
        src.api.db._db = MagicMock()

        # Close database
        close_db()

        # Verify client was closed
        mock_client.close.assert_called_once()

        # Verify both globals were reset
        assert src.api.db._client is None
        assert src.api.db._db is None

        # Verify get_db raises error after closing
        with pytest.raises(RuntimeError):
            get_db()

    def test_close_db_no_connection(self):
        """Test close_db works when no connection exists."""
        # Should not raise any exception
        close_db()

        # Verify get_db still raises error
        with pytest.raises(RuntimeError):
            get_db()


class TestIntegration:
    """Integration test cases for the db module."""

    @patch("src.api.db.MongoClient")
    def test_full_lifecycle(self, mock_mongo_client):
        """Test complete lifecycle: init -> get -> close."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_db.flywheel_runs = mock_collection

        # Initialize database
        db1 = init_db()
        assert db1 == mock_db

        # Get database
        db2 = get_db()
        assert db2 == mock_db
        assert db1 == db2

        # Close database
        close_db()
        mock_client.close.assert_called_once()

        # Verify get_db raises error after closing
        with pytest.raises(RuntimeError):
            get_db()

    @patch("src.api.db.MongoClient")
    def test_multiple_init_calls(self, mock_mongo_client):
        """Test multiple init_db calls with connection reuse."""
        # Setup mocks
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_admin = MagicMock()

        mock_mongo_client.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_client.admin = mock_admin
        mock_db.flywheel_runs = mock_collection

        # Call init_db multiple times
        db1 = init_db()
        db2 = init_db()
        db3 = init_db()

        # Verify all return same instance
        assert db1 == db2 == db3 == mock_db

        # Verify MongoClient was called only once
        mock_mongo_client.assert_called_once()

        # Verify ping was called for connection checks (2 calls for reuse)
        assert mock_admin.command.call_count == 2
