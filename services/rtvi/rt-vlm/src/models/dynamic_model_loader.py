# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Dynamic model loader for all VLM models.

This module provides functionality to dynamically load VLM models
that inherit from BaseVlmModel. It can load both custom models from
module paths and built-in models from class paths.
"""

import importlib
import os
from typing import Type

from models.base_vlm_model import BaseVlmModel, InputConfig


class DynamicModelLoader:
    """
    Dynamic loader for all VLM models.

    This class handles loading models that inherit from BaseVlmModel.
    It can load both custom models from module paths and built-in models
    from class paths (e.g., "models.vllm_compatible.vllm_compatible_model.VllmCompatible").
    """

    MODULE_NAME = "inference"

    def __init__(self, model_source: str):
        """
        Initialize the dynamic model loader.

        Args:
            model_source: Either a path to a directory containing custom model implementation
                         or a class path string (e.g., "models.vllm_compatible.vllm_compatible_model.VllmCompatible")  # noqa: E501
        """
        self._model_source = model_source
        self._is_class_path = self._is_class_path_format(model_source)

        if self._is_class_path:
            self._load_class_path()
        else:
            self._load_module_path()

    def _is_class_path_format(self, source: str) -> bool:
        """Check if the source is a class path format (contains dots and no slashes)."""
        return "." in source and os.path.sep not in source and not os.path.exists(source)

    def _load_class_path(self):
        """Load a built-in model from a class path."""
        self._implementation_path = None
        self._module_file_path = None
        self._module = None

    def _load_module_path(self):
        """Load a custom model from a module path."""
        if not os.path.isabs(self._model_source):
            self._model_source = os.path.abspath(self._model_source)

        self._implementation_path = self._model_source
        self._module_file_path = os.path.join(self._implementation_path, f"{self.MODULE_NAME}.py")

        # Load the module
        self._load_module()

    def _load_module(self):
        """Load the custom model module."""
        if not os.path.exists(self._module_file_path):
            raise FileNotFoundError(f"Custom model module not found: {self._module_file_path}")

        spec = importlib.util.spec_from_file_location(self.MODULE_NAME, self._module_file_path)
        self._module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._module)

    def get_model_class(self) -> Type[BaseVlmModel]:
        """
        Get the model class from the loaded module or class path.

        Returns:
            The model class that inherits from BaseVlmModel

        Raises:
            AttributeError: If the module doesn't contain a valid model class
            ImportError: If the class path cannot be imported
        """
        if self._is_class_path:
            return self._get_class_from_path()
        else:
            return self._get_class_from_module()

    def _get_class_from_path(self) -> Type[BaseVlmModel]:
        """Get the model class from a class path string."""
        try:
            module_path, class_name = self._model_source.rsplit(".", 1)
            module = importlib.import_module(module_path)
            model_class = getattr(module, class_name)
            if not issubclass(model_class, BaseVlmModel):
                raise TypeError(f"Class {class_name} does not inherit from BaseVlmModel")
            return model_class
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Failed to import model class {self._model_source}: {e}")

    def _get_class_from_module(self) -> Type[BaseVlmModel]:
        """Get the model class from a loaded module."""
        # Look for common class names
        possible_names = ["Model", "VlmModel", "CustomModel", "Inference"]

        for name in possible_names:
            if hasattr(self._module, name):
                model_class = getattr(self._module, name)
                if issubclass(model_class, BaseVlmModel):
                    return model_class

        # If no standard name found, look for any class that inherits from BaseVlmModel
        for attr_name in dir(self._module):
            attr = getattr(self._module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseVlmModel) and attr != BaseVlmModel:
                return attr

        raise AttributeError(
            f"No class inheriting from BaseVlmModel found in {self._module_file_path}. "
            f"Expected one of: {possible_names}"
        )

    def create_model(self, model_path: str, **kwargs) -> BaseVlmModel:
        """
        Create an instance of the custom model.

        Args:
            model_path: Path to the model weights/files
            **kwargs: Arguments to pass to the model constructor

        Returns:
            Instance of the custom model
        """
        model_class = self.get_model_class()
        return model_class(model_path, **kwargs)

    def get_input_config(self, model_path: str, vlm_model_type: str = "") -> InputConfig:
        """
        Get input configuration from the model class.

        Args:
            model_path: Path to the model weights/files

        Returns:
            InputConfig dataclass containing input configuration parameters
        """
        model_class = self.get_model_class()
        return model_class.get_input_config(model_path, vlm_model_type)


def load_model(model_source: str, model_path: str, **kwargs) -> BaseVlmModel:
    """
    Convenience function to load any model (built-in or custom).

    Args:
        model_source: Either a path to custom model implementation directory
                     or a class path string (e.g., "models.vllm_compatible.vllm_compatible_model.VllmCompatible")  # noqa: E501
        model_path: Path to the model weights/files
        **kwargs: Additional arguments for model creation

    Returns:
        Instance of the model
    """
    loader = DynamicModelLoader(model_source)
    return loader.create_model(model_path, **kwargs)


def load_custom_model(implementation_path: str, model_path: str, **kwargs) -> BaseVlmModel:
    """
    Convenience function to load a custom model.

    Args:
        implementation_path: Path to the custom model implementation directory
        model_path: Path to the model weights/files
        **kwargs: Additional arguments for model creation

    Returns:
        Instance of the custom model
    """
    return load_model(implementation_path, model_path, **kwargs)


def load_builtin_model(class_path: str, model_path: str, **kwargs) -> BaseVlmModel:
    """
    Convenience function to load a built-in model.

    Args:
        class_path: Class path string (e.g., "models.vllm_compatible.vllm_compatible_model.VllmCompatible")
        model_path: Path to the model weights/files
        **kwargs: Additional arguments for model creation

    Returns:
        Instance of the built-in model
    """
    return load_model(class_path, model_path, **kwargs)
