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
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from src.api.config_manager.exceptions import (
    ConfigurationAlreadyExistsError,
    ConfigurationNotFoundError,
    ConfigurationOperationError,
    ConfigurationUpdateError,
)
from src.api.config_manager.models import ConfigurationDocument
from src.api.config_manager.schemas import ConfigurationSummary
from src.api.db import get_db, init_db
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.config_manager")


class ConfigurationManager:
    """Manager class for configuration CRUD operations."""

    def __init__(self):
        try:
            self.db = get_db()
        except RuntimeError as e:
            # Database not yet initialised (e.g. during unit-test import). Do it now.
            if "Database not initialized" not in str(e):
                raise
            self.db = init_db()
        self.collection = self.db.configurations

    def list_configurations(self) -> list[ConfigurationSummary]:
        """
        List all configurations with summary information.

        Returns:
            List of ConfigurationSummary objects
        """
        try:
            configurations = []
            for doc in self.collection.find().sort("created_at", -1):
                config_doc = ConfigurationDocument.from_mongo(doc)
                summary = ConfigurationSummary(
                    id=str(config_doc.id),
                    name=config_doc.name,
                    description=config_doc.description,
                    config_type=config_doc.config_type,
                    created_at=config_doc.created_at,
                    updated_at=config_doc.updated_at,
                )
                configurations.append(summary)
            return configurations
        except Exception:
            logger.exception("Failed to list configurations")
            raise

    def get_configuration_count(self) -> int:
        """
        Get the total count of configurations.

        Returns:
            Total number of configurations
        """
        try:
            return self.collection.count_documents({})
        except Exception:
            logger.exception("Failed to get configuration count")
            raise

    def get_configuration(self, name: str) -> ConfigurationDocument | None:
        """
        Get a specific configuration by name.

        Args:
            name: Name of the configuration to retrieve

        Returns:
            ConfigurationDocument if found, None otherwise
        """
        try:
            doc = self.collection.find_one({"name": name})
            if doc:
                return ConfigurationDocument.from_mongo(doc)
            return None
        except Exception:
            logger.exception(f"Failed to get configuration '{name}'")
            raise

    def get_configuration_by_id(self, config_id: str) -> ConfigurationDocument | None:
        """
        Get a specific configuration by ID.

        Args:
            config_id: ID of the configuration to retrieve

        Returns:
            ConfigurationDocument if found, None otherwise
        """
        try:
            try:
                object_id = ObjectId(config_id)
            except InvalidId:
                logger.warning(f"Invalid ObjectId format: '{config_id}'")
                return None

            doc = self.collection.find_one({"_id": object_id})
            if doc:
                return ConfigurationDocument.from_mongo(doc)
            return None
        except Exception:
            logger.exception(f"Failed to get configuration by ID '{config_id}'")
            raise

    def create_configuration(self, config_data: dict[str, Any]) -> ConfigurationDocument:
        """
        Create a new configuration.

        Args:
            config_data: Configuration data dictionary

        Returns:
            Created ConfigurationDocument

        Raises:
            ConfigurationAlreadyExistsError: If configuration with same name already exists
        """
        try:
            # Create the configuration document
            config_doc = ConfigurationDocument(**config_data)
            try:
                result = self.collection.insert_one(config_doc.to_mongo())
                config_doc.id = result.inserted_id
            except DuplicateKeyError:
                raise ConfigurationAlreadyExistsError(config_data["name"]) from None

            logger.info(f"Created configuration '{config_doc.name}' with ID {config_doc.id}")
            return config_doc
        except ConfigurationAlreadyExistsError:
            raise
        except Exception:
            logger.exception("Failed to create configuration")
            raise

    def update_configuration(self, name: str, config_data: dict[str, Any]) -> ConfigurationDocument:
        """
        Update an existing configuration.

        Args:
            name: Name of the configuration to update
            config_data: Updated configuration data

        Returns:
            Updated ConfigurationDocument

        Raises:
            ConfigurationNotFoundError: If configuration not found
            ConfigurationUpdateError: If configuration name cannot be changed
            ConfigurationOperationError: If update operation fails
        """
        try:
            # Check if configuration exists
            existing = self.collection.find_one({"name": name})
            if not existing:
                raise ConfigurationNotFoundError(name)

            # Validate that name in config_data matches the name parameter
            if config_data.get("name") != name:
                raise ConfigurationUpdateError("Configuration name cannot be changed during update")

            # Update the configuration
            config_doc = ConfigurationDocument(**config_data)
            config_doc.updated_at = datetime.utcnow()

            # Preserve the existing _id
            config_doc.id = existing["_id"]

            result = self.collection.replace_one({"name": name}, config_doc.to_mongo())

            if result.matched_count == 0:
                # Configuration was deleted between check and update
                raise ConfigurationNotFoundError(name)

            logger.info(f"Updated configuration '{name}'")
            return config_doc
        except (ConfigurationNotFoundError, ConfigurationUpdateError):
            raise
        except Exception:
            logger.exception(f"Failed to update configuration '{name}'")
            raise

    def delete_configuration(self, name: str) -> str:
        """
        Delete a configuration.

        Args:
            name: Name of the configuration to delete

        Returns:
            Name of the deleted configuration

        Raises:
            ConfigurationNotFoundError: If configuration not found
            ConfigurationOperationError: If delete operation fails
        """
        try:
            # Check if configuration exists
            existing = self.collection.find_one({"name": name})
            if not existing:
                raise ConfigurationNotFoundError(name)

            # Delete the configuration
            result = self.collection.delete_one({"name": name})
            if result.deleted_count == 0:
                raise ConfigurationOperationError(f"Failed to delete configuration '{name}'")

            logger.info(f"Deleted configuration '{name}'")
            return name
        except ConfigurationNotFoundError:
            raise
        except Exception:
            logger.exception(f"Failed to delete configuration '{name}'")
            raise
