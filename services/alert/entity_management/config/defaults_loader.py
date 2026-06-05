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
Defaults Configuration Loader

Loads default values from external YAML configuration file.
Provides simple, reliable configuration management with validation.
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AlertsDefaultConfig:
    """
    Configuration container for loaded defaults.
    
    Provides structured access to all default values and configuration.
    """
    # Core parameter defaults
    vlm_params: Dict[str, Any] = field(default_factory=dict)
    vss_params: Dict[str, Any] = field(default_factory=dict)
    request_defaults: Dict[str, Any] = field(default_factory=dict)
    
    # Validation configuration
    validation_config: Dict[str, Any] = field(default_factory=dict)
    field_validation: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    
    # Metadata
    schema_version: str = "2.0.0"
    last_loaded: Optional[str] = None
    config_source: Optional[str] = None


class AlertsDefaultsConfigLoader:
    """
    Configuration loader for Alert Bridge system defaults.
    
    Loads and validates defaults from a single YAML configuration file
    for VSS and VLM parameters used in alert processing.
    """
    
    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize alerts defaults config loader.
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Set configuration directory search roots (env → explicit → defaults)
        self.search_paths: list[Path] = []
        env_path = os.getenv("ALERT_BRIDGE_DEFAULTS_FILE")
        if env_path:
            p = Path(env_path)
            if p.exists():
                self.search_paths.append(p)
        # Prefer repository root alongside config.yaml
        self.search_paths.append(Path.cwd() / "alert_request_defaults.yaml")
        
        # Configuration state
        self._loaded_config: Optional[AlertsDefaultConfig] = None
        
        self.logger.info(f"Initialized alerts defaults config loader with search_paths: {[str(p) for p in self.search_paths]}")
    
    def load_defaults(self) -> AlertsDefaultConfig:
        """
        Load default configuration from external file.
        
        Returns:
            Loaded and validated configuration
            
        Raises:
            FileNotFoundError: If configuration file is missing
            ValueError: If configuration validation fails
        """
        # Return cached config if already loaded
        if self._loaded_config:
            return self._loaded_config
        
        try:
            self.logger.info("Loading defaults configuration")
            
            # Load configuration
            config = self._load_config_file()
            
            # Validate configuration
            self._validate_configuration(config)
            
            # Create DefaultsConfig object
            defaults_config = self._create_defaults_config(config)
            
            # Cache the configuration
            self._loaded_config = defaults_config
            
            self.logger.info(
                "Successfully loaded defaults configuration",
                extra={
                    "config_source": defaults_config.config_source,
                    "vlm_params_count": len(defaults_config.vlm_params),
                    "vss_params_count": len(defaults_config.vss_params)
                }
            )
            
            return defaults_config
            
        except Exception as e:
            self.logger.error(
                f"Failed to load defaults configuration: {str(e)}",
                extra={"config_dir": str(self.config_dir)},
                exc_info=True
            )
            raise
    
    def _load_config_file(self) -> Dict[str, Any]:
        """Load the alert_request_defaults.yaml configuration file from known locations."""
        last_err: Optional[Exception] = None
        for candidate in self.search_paths:
            try:
                # When env provided a directory, candidate may be a dir; normalize
                path = candidate
                if path.is_dir():
                    path = path / "alert_request_defaults.yaml"
                if not path.exists():
                    continue
                with open(path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                if not config:
                    raise ValueError("Configuration file is empty or invalid")
                self.logger.debug(f"Loaded configuration from {path}")
                self._resolved_path = str(path)
                return config
            except Exception as e:
                last_err = e
                continue
            
        raise FileNotFoundError(f"Configuration file not found in search paths. Last error: {last_err}")
    
    def _validate_configuration(self, config: Dict[str, Any]) -> None:
        """Validate the loaded configuration."""
        # Validate required sections
        required_sections = ['vlm_params', 'vss_params', 'request_defaults']
        missing_sections = [section for section in required_sections if section not in config]
        
        if missing_sections:
            raise ValueError(f"Missing required configuration sections: {missing_sections}")
        
        # Validate constraint compliance
        constraints = config.get('constraints', {})
        if constraints:
            self._validate_against_constraints(config, constraints)
        
        # Validate schema version
        schema = config.get('schema', {})
        if schema.get('version') != "2.0.0":
            self.logger.warning(f"Schema version mismatch: {schema.get('version')} != 2.0.0")
    
    def _validate_against_constraints(self, config: Dict[str, Any], constraints: Dict[str, Any]) -> None:
        """Validate parameter values against defined constraints."""
        for section, section_constraints in constraints.items():
            if section not in config:
                continue
            
            section_config = config[section]
            for param, param_constraints in section_constraints.items():
                if param not in section_config:
                    continue
                
                value = section_config[param]
                
                # Validate min/max constraints
                if 'min' in param_constraints and value < param_constraints['min']:
                    raise ValueError(f"{section}.{param} value {value} below minimum {param_constraints['min']}")
                
                if 'max' in param_constraints and value > param_constraints['max']:
                    raise ValueError(f"{section}.{param} value {value} above maximum {param_constraints['max']}")
    
    def _create_defaults_config(self, config: Dict[str, Any]) -> AlertsDefaultConfig:
        """Create AlertsDefaultConfig object from loaded configuration."""
        return AlertsDefaultConfig(
            vlm_params=config.get('vlm_params', {}),
            vss_params=config.get('vss_params', {}),
            request_defaults=config.get('request_defaults', {}),
            validation_config=config.get('validation', {}),
            field_validation=config.get('field_validation', {}),
            constraints=config.get('constraints', {}),
            schema_version=config.get('schema', {}).get('version', '2.0.0'),
            last_loaded=self._get_current_timestamp(),
            config_source=getattr(self, '_resolved_path', None)
        )
    
    def _get_current_timestamp(self) -> str:
        """Get current timestamp for tracking."""
        from datetime import datetime
        return datetime.utcnow().isoformat()
    
    def get_config_info(self) -> Dict[str, Any]:
        """Get information about the loaded configuration."""
        if not self._loaded_config:
            return {"status": "not_loaded"}
        
        return {
            "status": "loaded",
            "schema_version": self._loaded_config.schema_version,
            "last_loaded": self._loaded_config.last_loaded,
            "config_source": self._loaded_config.config_source,
            "parameter_counts": {
                "vlm_params": len(self._loaded_config.vlm_params),
                "vss_params": len(self._loaded_config.vss_params),
                "request_defaults": len(self._loaded_config.request_defaults)
            }
        } 