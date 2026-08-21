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
Profile Configuration Manager

Manages profile-specific configuration generation using file operations.
Handles hardware profiles and deployment modes for GPU configurations.
"""

import json
import os
import sys
import re
import fnmatch
from typing import Dict, Any, Optional, List, Union, Tuple
from pathlib import Path
import ast
import operator
import shutil
from datetime import datetime

try:
    from .profile_configurator_utils import utils
except ImportError:
    from profile_configurator_utils import utils

# Add parent directory to path to import logger utility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.logger import get_logger

# Get module-level logger
logger = get_logger(__name__)


class ProfileConfigManager:
    """
    Manages profile-specific configuration generation using file operations.
    
    This class handles loading hardware profiles, processing configuration variables,
    and executing file operations based on the detected hardware and deployment mode.
    
    When DEPLOYMENT_MODES_ENABLED=true (default): uses MODE (2d/3d) for config and
    commons (warehouse-style). When false (mode-less): config is direct under
    hardware profile; commons use direct lists or a 'default' key (Option 1).
    
    Attributes:
        profile_configs: Dictionary containing all profile configurations
        hardware_profile: Detected hardware profile (e.g., 'L4', 'H100')
        deployment_modes_enabled: True if MODE (2d/3d) is used; false for mode-less
        deployment_profile: Deployment mode (e.g., '2d', '3d') or 'default' when mode-less
        env_vars: Dictionary of environment variables and computed variables
        config: Current configuration for the active profile
    """

    # Default configuration file name
    DEFAULT_CONFIG_FILE = 'gpu_configs_generic.yaml'
    DEFAULT_HARDWARE_PROFILE = 'default'
    DEFAULT_DEPLOYMENT_MODE = '3d'

    @staticmethod
    def _hardware_profile_names(profile_configs: Dict[str, Any]) -> List[str]:
        """Return configured hardware profile names, excluding shared commons metadata."""
        return [name for name in profile_configs.keys() if name != 'commons']

    def __init__(self, config_file: Optional[str] = None) -> None:
        """
        Initialize the Profile Config Manager.
        
        Args:
            config_file: Optional path to configuration file. If not provided,
                        uses PROFILE_CONFIG_FILE env var or default location.
        """
        logger.debug("Initializing ProfileConfigManager")
        logger.debug(f"Config file: {config_file}")
        self.profile_configs = self._load_profile_configs(config_file)
        self.hardware_profile = self.determine_hardware_profile()

        # DEPLOYMENT_MODES_ENABLED=true: use MODE (2d/3d) for config and commons (warehouse-style).
        # DEPLOYMENT_MODES_ENABLED=false: mode-less; config is direct under hardware profile (Option 1).
        deployment_modes_enabled_raw = os.getenv('DEPLOYMENT_MODES_ENABLED', 'true').strip().lower()
        self.deployment_modes_enabled = deployment_modes_enabled_raw == 'true'
        logger.debug(f"DEPLOYMENT_MODES_ENABLED: {self.deployment_modes_enabled}")

        if self.deployment_modes_enabled:
            self.deployment_profile = self.determine_deployment_profile(
                os.getenv('MODE', self.DEFAULT_DEPLOYMENT_MODE)
            )
            self.config = self.profile_configs.get(
                self.hardware_profile, {}
            ).get(self.deployment_profile, {})
        else:
            self.deployment_profile = 'default'
            # Option 1: config is directly under hardware profile (no 2d/3d nesting)
            self.config = self.profile_configs.get(self.hardware_profile, {})

        logger.debug(f"Hardware profile: {self.hardware_profile}, Deployment profile: {self.deployment_profile}")
        
        # Convert environment variables to a mutable dictionary
        self.env_vars: Dict[str, str] = dict(os.environ)
        # Holds non-string typed values (list, None) for variables computed from expressions
        self._typed_env_vars: Dict[str, Any] = {}
        logger.debug(f"Loaded {len(self.env_vars)} environment variables")
        
        if not self._has_effective_config():
            logger.warning(
                f"No configuration found for HW profile: {self.hardware_profile}"
                + (f" and MODE: {self.deployment_profile}" if self.deployment_modes_enabled else " (mode-less)")
            )
        else:
            if self.config:
                logger.info(
                    f"Found configurations for HW Profile: {self.hardware_profile}"
                    + (f" and MODE: {self.deployment_profile}" if self.deployment_modes_enabled else " (mode-less)")
                )
            else:
                logger.info(
                    f"Using common configurations for HW Profile: {self.hardware_profile}"
                    + (f" and MODE: {self.deployment_profile}" if self.deployment_modes_enabled else " (mode-less)")
                )
            # Run variable validation first (validates env vars before any processing)
            self._execute_variable_validation()
            # Execute prerequisites first (e.g., file counts that generate variables)
            self._execute_prerequisites()
            # Process variables before file operations
            self._process_config_variables()

    def determine_hardware_profile(self) -> str:
        """
        Determine the hardware profile from environment variables.
        
        Returns:
            Hardware profile name in uppercase, or 'default' when unset
        """
        hw_profile_env = os.getenv('HARDWARE_PROFILE')
        if hw_profile_env:
            hw_profile_upper = hw_profile_env.upper()
            logger.info(
                f"Using HARDWARE_PROFILE environment variable: {hw_profile_upper}"
            )
            
            if hw_profile_upper not in self.profile_configs:
                logger.warning(
                    f"Hardware profile: {hw_profile_upper} not found in "
                    f"GPU configurations: {self._hardware_profile_names(self.profile_configs)}. "
                    f"Using common configurations only"
                )
                return hw_profile_upper
            
            return hw_profile_upper
        
        logger.info(f"No HARDWARE_PROFILE set, using {self.DEFAULT_HARDWARE_PROFILE}")
        return self.DEFAULT_HARDWARE_PROFILE

    def determine_deployment_profile(self, mode: str) -> str:
        """
        Determine the deployment profile based on the MODE environment variable.
        
        Args:
            mode: Deployment mode string (e.g., '2d', '3d')
            
        Returns:
            Normalized deployment mode in lowercase
        """
        normalized_mode = mode.lower().strip()
        logger.debug(f"Deployment mode set to: {normalized_mode}")
        return normalized_mode

    def _get_commons_list(self, key: str) -> List[Any]:
        """
        Get the commons list for the given key (prerequisites, variables, variable_validation, file_operations).
        When DEPLOYMENT_MODES_ENABLED is true, uses deployment_profile (2d/3d).
        When false (mode-less), uses direct list or key 'default' under the section (Option 1).
        """
        commons = self.profile_configs.get('commons', {})
        section = commons.get(key)
        if section is None:
            return []
        if self.deployment_modes_enabled:
            return section.get(self.deployment_profile, []) if isinstance(section, dict) else []
        # Mode-less: section is either a list (direct) or a dict with optional 'default' key
        if isinstance(section, list):
            return section
        if isinstance(section, dict):
            return section.get('default', [])
        return []

    def _has_effective_config(self) -> bool:
        """
        Return True when either hardware-specific or common configuration applies.

        Common sections are intentionally valid for hardware profiles that do not
        have a top-level profile block in the configuration file.
        """
        if self.config:
            return True
        return any(
            self._get_commons_list(key)
            for key in (
                'prerequisites',
                'variables',
                'variable_validation',
                'file_operations',
            )
        )

    def _load_profile_configs(self, config_file: Optional[str] = None) -> Dict[str, Any]:
        """
        Load profile configurations from YAML file.
        
        Args:
            config_file: Optional path to configuration file
            
        Returns:
            Dictionary containing profile configurations
            
        Raises:
            FileNotFoundError: If configuration file doesn't exist
            ValueError: If YAML is invalid
        """
        # Determine config file path
        if config_file:
            config_path = config_file
        else:
            default_path = Path(__file__).parent / self.DEFAULT_CONFIG_FILE
            config_path = os.getenv('PROFILE_CONFIG_FILE', str(default_path))
        
        logger.info(f"Loading GPU configurations from: {config_path}")
        
        try:
            # Load YAML configuration using utility function
            gpu_configs = utils.read_yaml_file(config_path)
            
            if not isinstance(gpu_configs, dict):
                raise ValueError("Configuration file must contain a dictionary at root level")
            
            logger.info(
                f"Successfully loaded configurations for HW Profiles: "
                f"{self._hardware_profile_names(gpu_configs)}"
            )
            return gpu_configs
            
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Configuration file error: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load HW Profile configurations from {config_path}")
            raise

    def _substitute_env_vars(self, value: Any) -> Any:
        """
        Substitute environment variables in strings or nested structures.
        
        Supports ${VAR_NAME} syntax for variable substitution.
        
        Args:
            value: Value to process (can be str, dict, list, or other types)
            
        Returns:
            Value with all environment variable references substituted
        """
        if isinstance(value, str):
            # If the entire value is a single ${VAR} reference to a typed variable
            # (list or None), return it directly to preserve its type.
            simple_ref = re.fullmatch(r'\$\{(\w+)\}', value)
            if simple_ref:
                var_name = simple_ref.group(1)
                if var_name in self._typed_env_vars:
                    return self._typed_env_vars[var_name]
            # Replace environment variables in the string
            result = value
            for var_name, var_value in self.env_vars.items():
                result = result.replace(f"${{{var_name}}}", str(var_value))
            # Try to convert numeric strings to int/float after substitution
            return self._try_convert_to_number(result)
        elif isinstance(value, dict):
            # Recursively substitute in dictionary values
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            # Recursively substitute in list items
            return [self._substitute_env_vars(item) for item in value]
        else:
            # Return non-string values as-is
            return value

    def _try_convert_to_number(self, value: Any) -> Union[int, float, bool, Any]:
        """
        Try to convert a string value to int, float, or bool.
        
        Args:
            value: Value to convert
            
        Returns:
            Converted number or boolean if possible, otherwise original value
        """
        if not isinstance(value, str):
            return value
        
        # Try boolean conversion first (case-insensitive)
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
            
        try:
            # Try integer conversion
            if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                return int(value)
            # Try float conversion
            return float(value)
        except (ValueError, AttributeError):
            return value

    def _is_operation_enabled(self, operation: Dict[str, Any]) -> bool:
        """Return True if a prerequisite or file operation should run.

        Supports static booleans and computed values via ${VAR} substitution
        from env_vars (e.g. enabled: "${trim_sample_videos}").
        """
        enabled = operation.get('enabled', True)
        enabled = self._substitute_env_vars(enabled)
        enabled = self._try_convert_to_number(enabled)
        return bool(enabled)

    def _create_backup(self, file_path: str) -> Optional[str]:
        """
        Create a backup of the specified file with timestamp.
        
        Args:
            file_path: Path to the file to backup
            
        Returns:
            Path to the backup file if successful, None otherwise
        """
        if not utils.file_exists(file_path):
            logger.warning(f"Cannot backup non-existent file: {file_path}")
            return None
        
        try:
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path_obj = Path(file_path)
            backup_path = file_path_obj.parent / f"{file_path_obj.stem}.backup_{timestamp}{file_path_obj.suffix}"
            
            # Create backup
            shutil.copy2(file_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
            return str(backup_path)
            
        except Exception as e:
            logger.exception(f"Failed to create backup for {file_path}: {e}")
            return None

    def _evaluate_expression(self, expression: str) -> Union[int, float, bool]:
        """
        Safely evaluate mathematical expressions with support for min, max, basic operations,
        comparison operators, and ternary if-else expressions.
        
        Args:
            expression: String expression to evaluate
                       (e.g., "min(4, 5)", "2 + 3", "10 if x > 5 else 20")
            
        Returns:
            Evaluated result (int, float, or bool)
        """
        # Define safe functions and operators
        safe_functions = {
            'min': min,
            'max': max,
            'abs': abs,
            'round': round,
            'int': int,
            'float': float,
        }
        
        safe_operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }
        
        safe_comparisons = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
        }
        
        safe_bool_ops = {
            ast.And: lambda x, y: x and y,
            ast.Or: lambda x, y: x or y,
        }
        
        def eval_node(node):
            """Recursively evaluate AST nodes."""
            if isinstance(node, ast.Constant):  # Python 3.8+
                return node.value
            elif isinstance(node, ast.Num):  # Python 3.7 compatibility
                return node.n
            elif isinstance(node, ast.BinOp):
                left = eval_node(node.left)
                right = eval_node(node.right)
                op = safe_operators.get(type(node.op))
                if op is None:
                    raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
                return op(left, right)
            elif isinstance(node, ast.UnaryOp):
                operand = eval_node(node.operand)
                if isinstance(node.op, ast.UAdd):
                    return +operand
                elif isinstance(node.op, ast.USub):
                    return -operand
                elif isinstance(node.op, ast.Not):
                    return not operand
                else:
                    raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
            elif isinstance(node, ast.Compare):
                # Handle comparison operations (e.g., x > 5, a == b)
                left = eval_node(node.left)
                for op, comparator in zip(node.ops, node.comparators):
                    right = eval_node(comparator)
                    compare_op = safe_comparisons.get(type(op))
                    if compare_op is None:
                        raise ValueError(f"Unsupported comparison: {type(op).__name__}")
                    result = compare_op(left, right)
                    if not result:
                        return False
                    left = right
                return True
            elif isinstance(node, ast.BoolOp):
                # Handle boolean operations (and, or)
                bool_op = safe_bool_ops.get(type(node.op))
                if bool_op is None:
                    raise ValueError(f"Unsupported boolean operator: {type(node.op).__name__}")
                values = [eval_node(value) for value in node.values]
                result = values[0]
                for value in values[1:]:
                    result = bool_op(result, value)
                return result
            elif isinstance(node, ast.IfExp):
                # Handle ternary operator (value_if_true if condition else value_if_false)
                condition = eval_node(node.test)
                if condition:
                    return eval_node(node.body)
                else:
                    return eval_node(node.orelse)
            elif isinstance(node, ast.Call):
                func_name = node.func.id if isinstance(node.func, ast.Name) else None
                if func_name not in safe_functions:
                    raise ValueError(f"Unsupported function: {func_name}")
                args = [eval_node(arg) for arg in node.args]
                return safe_functions[func_name](*args)
            elif isinstance(node, ast.List):
                return [eval_node(elt) for elt in node.elts]
            elif isinstance(node, ast.NameConstant):  # Python 3.7 compatibility
                return node.value
            elif isinstance(node, ast.Name):
                if node.id == 'None':
                    return None
                if node.id == 'True':
                    return True
                if node.id == 'False':
                    return False
                # Should not reach here if substitution was done correctly
                raise ValueError(f"Undefined variable: {node.id}")
            else:
                raise ValueError(f"Unsupported expression type: {type(node).__name__}")
        
        try:
            # Parse and evaluate the expression
            tree = ast.parse(expression, mode='eval')
            result = eval_node(tree.body)
            logger.debug(f"Evaluated expression '{expression}' = {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to evaluate expression '{expression}': {e}")
            raise ValueError(f"Invalid expression '{expression}': {e}")

    def _execute_prerequisites(self) -> None:
        """
        Execute prerequisite operations before processing variables.
        Prerequisites can include operations like file_count that generate variables.
        """
        if not self._has_effective_config():
            return

        prerequisites = self._get_commons_list('prerequisites')
        if not prerequisites:
            logger.debug("No prerequisites to execute")
            return
            
        logger.info(f"Executing {len(prerequisites)} prerequisite operations")
        
        for i, operation in enumerate(prerequisites):
            operation_type = operation.get('operation_type')
            if not self._is_operation_enabled(operation):
                logger.info(f"Skipping prerequisite {i+1}/{len(prerequisites)}: {operation_type} (disabled)")
                continue
            logger.info(f"Executing prerequisite {i+1}/{len(prerequisites)}: {operation_type}")
            
            try:
                if operation_type == 'file_management':
                    result = self._execute_file_management(operation)
                    if not result:
                        logger.error(f"Prerequisite {i+1} failed: {operation_type}")
                    else:
                        logger.info(f"Prerequisite {i+1} completed successfully: {operation_type}")
                else:
                    logger.warning(f"Unsupported prerequisite operation type: {operation_type}")
                    
            except Exception as e:
                logger.exception(f"Prerequisite {i+1} failed with exception: {e}")

    def _process_config_variables(self) -> None:
        """
        Process the variables section from config and add computed values to env_vars.
        This should be called before executing file operations.
        """
        if not self._has_effective_config():
            return
        
        # Add max_streams_supported to env_vars if present
        if 'max_streams_supported' in self.config:
            self.env_vars['max_streams_supported'] = str(self.config['max_streams_supported'])
            logger.info(f"Added max_streams_supported = {self.config['max_streams_supported']}")

        # check use_commons:variables: true or false or 2d or 3d; default is true (2d/3d only when deployment_modes_enabled)
        use_common_variables = self.config.get('commons', {}).get('variables', "")
        if use_common_variables == "true" or not use_common_variables:
            variables = self._get_commons_list('variables')
        elif self.deployment_modes_enabled and use_common_variables in ['2d', '3d']:
            variables = self.profile_configs.get('commons', {}).get('variables', {}).get(use_common_variables, [])
        elif use_common_variables == "false":
            variables = []
        else:
            logger.warning(f"Invalid use_commons.variables value in Profile: {use_common_variables}. Must be true, false, 2d, or 3d.")
            logger.warning("Using default common variables")
            variables = self._get_commons_list('variables')
        
        
        if self.config.get('variables', []):
            variables.extend(self.config.get('variables', []))
        if not variables:
            logger.debug("No variables to process")
            return
        logger.debug(f"Variables: {variables}")
        logger.info(f"Processing {len(variables)} variable definitions")
        
        for var_def in variables:
            if not isinstance(var_def, dict):
                logger.warning(f"Invalid variable definition format: {var_def}")
                continue
            
            # Each variable definition is a dictionary with one key-value pair
            for var_name, var_expression in var_def.items():
                try:
                    # Substitute environment variables in the expression
                    substituted_expr = self._substitute_env_vars(var_expression)
                    logger.debug(f"Variable '{var_name}': '{var_expression}' -> '{substituted_expr}'")
                    
                    # Try to evaluate if it's an expression
                    if isinstance(substituted_expr, str):
                        try:
                            # Attempt to evaluate as a mathematical expression
                            result = self._evaluate_expression(substituted_expr)
                            if isinstance(result, list) or result is None:
                                self._typed_env_vars[var_name] = result
                                logger.info(f"Computed variable '{var_name}' = {result!r} (typed, from expression: {var_expression})")
                            else:
                                self.env_vars[var_name] = str(result)
                                logger.info(f"Computed variable '{var_name}' = {result} (from expression: {var_expression})")
                        except (ValueError, SyntaxError):
                            # If evaluation fails, treat as a literal string
                            self.env_vars[var_name] = substituted_expr
                            logger.info(f"Set variable '{var_name}' = '{substituted_expr}' (literal)")
                    else:
                        # Non-string value, convert to string
                        self.env_vars[var_name] = str(substituted_expr)
                        logger.info(f"Set variable '{var_name}' = {substituted_expr}")
                        
                except Exception as e:
                    logger.exception(f"Failed to process variable '{var_name}': {e}")
                    # Continue processing other variables even if one fails
                    continue

    def _execute_variable_validation(self) -> Tuple[bool, List[str]]:
        """
        Execute variable validation rules defined in the configuration.
        
        Validates environment variables against rules defined in the 
        'variable_validation' section of the config. Supports:
        - Simple allowed values validation
        - Pattern matching with wildcards
        - Conditional validation (validate only when a condition is met)
        
        Returns:
            Tuple of (success: bool, errors: List[str])
        """
        if not self._has_effective_config():
            return True, []
        
        errors: List[str] = []
        
        # Get validations from commons and profile-specific config
        # Check use_commons:variable_validation: true or false or 2d or 3d; default is true (2d/3d only when deployment_modes_enabled)
        use_common_validations = self.config.get('commons', {}).get('variable_validation', "")
        if use_common_validations == "true" or not use_common_validations:
            validations = self._get_commons_list('variable_validation')
        elif self.deployment_modes_enabled and use_common_validations in ['2d', '3d']:
            validations = self.profile_configs.get('commons', {}).get('variable_validation', {}).get(use_common_validations, [])
        elif use_common_validations == "false":
            validations = []
        else:
            logger.warning(f"Invalid use_commons.variable_validation value in Profile: {use_common_validations}. Must be true, false, 2d, or 3d.")
            logger.warning("Using default common variable validations")
            validations = self._get_commons_list('variable_validation')
        
        # Make a copy to avoid modifying the original
        validations = list(validations) if validations else []
        
        # Add profile-specific validations
        if self.config.get('variable_validation', []):
            validations.extend(self.config.get('variable_validation', []))
        
        if not validations:
            logger.debug("No variable validations to execute")
            return True, []
        
        logger.info(f"Executing {len(validations)} variable validation rules")
        
        for i, validation in enumerate(validations):
            if not isinstance(validation, dict):
                logger.warning(f"Invalid validation rule format at index {i}: {validation}")
                continue
            
            try:
                is_valid, error_msg = self._validate_single_rule(validation)
                if not is_valid and error_msg:
                    errors.append(error_msg)
                    logger.error(f"Validation failed: {error_msg}")
                elif is_valid:
                    var_name = validation.get('variable', 'unknown')
                    logger.debug(f"Validation passed for variable: {var_name}")
            except Exception as e:
                logger.exception(f"Error executing validation rule {i}: {e}")
                errors.append(f"Validation rule {i} failed with error: {str(e)}")
        
        if errors:
            logger.error(f"Variable validation completed with {len(errors)} error(s)")
            for error in errors:
                logger.error(f"  - {error}")
            sys.exit(1)
        else:
            logger.info("All variable validations passed successfully")
        
        return len(errors) == 0, errors

    def _validate_single_rule(self, rule: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate a single validation rule.
        
        Args:
            rule: Dictionary containing the validation rule
            
        Returns:
            Tuple of (is_valid: bool, error_message: Optional[str])
        """
        variable_name = rule.get('variable')
        if not variable_name:
            return False, "Validation rule missing 'variable' field"
        
        # Get the current value of the variable (from env_vars)
        current_value = self.env_vars.get(variable_name)
        
        # Check if there's a condition that must be met first
        condition = rule.get('condition')
        if condition:
            logger.debug(f"Evaluating condition: {condition}")
            condition_met = self._evaluate_validation_condition(condition)
            if not condition_met:
                # Condition not met, skip this validation
                logger.debug(f"Skipping validation for '{variable_name}' - condition not met")
                return True, None
        
        # If variable is not set and validation has 'required: false', skip
        if current_value is None:
            if rule.get('required', True):
                error_msg = rule.get('error_message', f"Required variable '{variable_name}' is not set")
                return False, error_msg
            else:
                logger.debug(f"Optional variable '{variable_name}' is not set, skipping validation")
                return True, None
        
        # Check allowed_values (exact match)
        allowed_values = rule.get('allowed_values', [])
        if allowed_values:
            logger.debug(f"Allowed values: {allowed_values}")
            logger.debug(f"Current value: {current_value}")
            if current_value not in allowed_values:
                error_msg = rule.get(
                    'error_message',
                    f"Variable '{variable_name}' has invalid value '{current_value}'. "
                    f"Allowed values: {allowed_values}"
                )
                return False, error_msg
            return True, None
        
        # Check allowed_patterns (wildcard/glob matching)
        allowed_patterns = rule.get('allowed_patterns', [])
        if allowed_patterns:
            logger.debug(f"Allowed patterns: {allowed_patterns}")
            logger.debug(f"Current value: {current_value}")
            matched = any(fnmatch.fnmatch(current_value, pattern) for pattern in allowed_patterns)
            if not matched:
                error_msg = rule.get(
                    'error_message',
                    f"Variable '{variable_name}' has invalid value '{current_value}'. "
                    f"Must match one of patterns: {allowed_patterns}"
                )
                return False, error_msg
            return True, None
        
        # Check regex pattern
        regex_pattern = rule.get('regex')
        if regex_pattern:
            logger.debug(f"Regex pattern: {regex_pattern}")
            logger.debug(f"Current value: {current_value}")
            if not re.match(regex_pattern, current_value):
                error_msg = rule.get(
                    'error_message',
                    f"Variable '{variable_name}' value '{current_value}' "
                    f"does not match required pattern: {regex_pattern}"
                )
                return False, error_msg
            return True, None
        
        # Check disallowed_values (values that are NOT allowed)
        disallowed_values = rule.get('disallowed_values', [])
        if disallowed_values:
            logger.debug(f"Disallowed values: {disallowed_values}")
            logger.debug(f"Current value: {current_value}")
            if current_value in disallowed_values:
                error_msg = rule.get(
                    'error_message',
                    f"Variable '{variable_name}' has disallowed value '{current_value}'. "
                    f"Disallowed values: {disallowed_values}"
                )
                return False, error_msg
            return True, None
        
        # Check disallowed_patterns (wildcard/glob patterns that are NOT allowed)
        disallowed_patterns = rule.get('disallowed_patterns', [])
        logger.debug(f"Disallowed patterns: {disallowed_patterns}")
        if disallowed_patterns:
            matched = any(fnmatch.fnmatch(current_value, pattern) for pattern in disallowed_patterns)
            if matched:
                error_msg = rule.get(
                    'error_message',
                    f"Variable '{variable_name}' has disallowed value '{current_value}'. "
                    f"Value must not match any of patterns: {disallowed_patterns}"
                )
                return False, error_msg
            return True, None
        
        # No validation criteria specified - pass by default
        logger.warning(f"Validation rule for '{variable_name}' has no validation criteria")
        return True, None

    def _evaluate_validation_condition(self, condition: Dict[str, Any]) -> bool:
        """
        Evaluate a validation condition.
        
        Supports conditions like:
        - { variable: "VAR_NAME", equals: "value" }
        - { variable: "VAR_NAME", not_equals: "value" }
        - { variable: "VAR_NAME", in: ["val1", "val2"] }
        - { variable: "VAR_NAME", not_in: ["val1", "val2"] }
        - { variable: "VAR_NAME", matches: "pattern*" }
        - { variable: "VAR_NAME", is_set: true/false }
        
        Args:
            condition: Dictionary containing the condition specification
            
        Returns:
            True if condition is met, False otherwise
        """
        # if not isinstance(condition, dict):
        #     logger.warning(f"Invalid condition format: {condition}")
        #     return False
        
        cond_variable = condition.get('variable')
        if not cond_variable:
            logger.warning("Condition missing 'variable' field")
            return False
        
        cond_value = self.env_vars.get(cond_variable)
        
        # Check is_set condition
        if 'is_set' in condition:
            expected_set = condition['is_set']
            is_actually_set = cond_value is not None and cond_value != ""
            return is_actually_set == expected_set
        
        # For other conditions, if variable is not set, condition is not met
        if cond_value is None:
            return False
        
        # Check equals condition
        if 'equals' in condition:
            return cond_value == str(condition['equals'])
        
        # Check not_equals condition
        if 'not_equals' in condition:
            return cond_value != str(condition['not_equals'])
        
        # Check in condition (value is in a list)
        if 'in' in condition:
            allowed_list = condition['in']
            if isinstance(allowed_list, list):
                return cond_value in [str(v) for v in allowed_list]
            return False
        
        # Check not_in condition (value is not in a list)
        if 'not_in' in condition:
            disallowed_list = condition['not_in']
            if isinstance(disallowed_list, list):
                return cond_value not in [str(v) for v in disallowed_list]
            return False
        
        # Check matches condition (pattern matching)
        if 'matches' in condition:
            pattern = condition['matches']
            return fnmatch.fnmatch(cond_value, pattern)
        
        logger.warning(f"Condition has no recognized comparison operator: {condition}")
        return False

    def _set_nested_dict_value(
        self, data: Dict[str, Any], key_path: str, value: Any
    ) -> None:
        """
        Set a value in a nested dictionary using dot notation.
        
        Args:
            data: Dictionary to modify
            key_path: Dot-separated path to the target key (e.g., 'a.b.c')
            value: Value to set
        """
        keys = key_path.split('.')
        current = data
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            elif not isinstance(current[key], dict):
                # Overwrite non-dict values with dict
                logger.warning(
                    f"Overwriting non-dict value at '{key}' in path '{key_path}'"
                )
                current[key] = {}
            current = current[key]
        
        # Set the final value
        current[keys[-1]] = value

    def _apply_json_array_kv_updates(
        self, content: Dict[str, Any], spec: Dict[str, Any]
    ) -> None:
        """
        Update dict entries inside a JSON array by matching a field (e.g. app[] where name=sourceType).

        spec keys:
          list_key: top-level key whose value is a list (e.g. "app")
          match_field: field to match on each element (default "name")
          updates: list of { match, set_key, set_value } — for each list item where
                   item[match_field] == match (after env substitution), set item[set_key].
        """
        list_key = self._substitute_env_vars(spec.get('list_key', ''))
        if not list_key:
            logger.warning("array_kv_updates: missing list_key, skipping")
            return
        match_field = self._substitute_env_vars(spec.get('match_field', 'name'))
        row_updates = spec.get('updates', [])
        if list_key not in content or not isinstance(content[list_key], list):
            logger.warning(
                f"array_kv_updates: '{list_key}' is missing or not a list, skipping"
            )
            return
        arr = content[list_key]
        for row in row_updates:
            match_raw = row.get('match')
            if match_raw is None:
                logger.warning("array_kv_updates: row missing 'match', skipping")
                continue
            match_val = self._substitute_env_vars(match_raw)
            set_key = row.get('set_key', 'value')
            if 'set_value' not in row:
                logger.warning(
                    f"array_kv_updates: no set_value for match={match_val}, skipping"
                )
                continue
            set_value = self._substitute_env_vars(row['set_value'])
            set_value = self._try_convert_to_number(set_value)
            matched = False
            for item in arr:
                if isinstance(item, dict) and str(item.get(match_field)) == str(
                    match_val
                ):
                    item[set_key] = set_value
                    matched = True
                    logger.debug(
                        f"array_kv_updates: set {list_key}[].{match_field}={match_val} "
                        f".{set_key} = {set_value!r}"
                    )
            if not matched:
                logger.warning(
                    f"array_kv_updates: no item in '{list_key}' with "
                    f"{match_field}={match_val!r}"
                )

    def _execute_yaml_update(self, operation: Dict[str, Any]) -> bool:
        """Execute a YAML file update operation."""
        target_file = self._substitute_env_vars(operation['target_file'])
        updates = operation.get('updates', {})
        backup_enabled = operation.get('backup', True)
        
        logger.debug(f"Executing YAML update on file: {target_file}")
        logger.debug(f"Updates to apply: {list(updates.keys())}")
        logger.debug(f"Backup enabled: {backup_enabled}")
        
        if not utils.file_exists(target_file):
            logger.warning(f"YAML file not found: {target_file}")
            return False

        # Create backup if enabled
        if backup_enabled:
            backup_path = self._create_backup(target_file)
            if not backup_path:
                logger.warning(f"Failed to create backup for {target_file}, continuing with update")

        try:
            logger.debug(f"Reading YAML file: {target_file}")
            # Read YAML file content
            content = utils.read_yaml_file(target_file)
            utils.set_flow_for_lists(content)
            
            # Apply updates with environment variable substitution
            for key, value in updates.items():
                # Substitute environment variables in the value
                substituted_value = self._substitute_env_vars(value)
                
                # Try to convert to appropriate type if it's a string number
                substituted_value = self._try_convert_to_number(substituted_value)
                
                # Priority 1: Check if key exists literally (even with dots)
                if key in content:
                    # Literal key exists - update it directly
                    content[key] = substituted_value
                    logger.debug(f"Updated YAML literal key '{key}' = {substituted_value}")
                elif '.' in key:
                    # Priority 2: Treat as nested path with dot notation
                    self._set_nested_dict_value(content, key, substituted_value)
                    logger.debug(f"Updated YAML nested path '{key}' = {substituted_value}")
                else:
                    # Priority 3: Simple key (no dots, doesn't exist yet)
                    content[key] = substituted_value
                    logger.debug(f"Updated YAML key '{key}' = {substituted_value}")
            

           
            # Write updated content back to file
            if utils.write_yaml_file(target_file, content):
                logger.info(f"Successfully updated YAML file: {target_file}")
                return True
            else:   
                raise Exception("Failed to write updated content")
            
        except Exception as e:
            logger.exception(f"Failed to update YAML file {target_file}: {e}")
            return False

    def _execute_text_config_update(self, operation: Dict[str, Any]) -> bool:
        """Execute a text configuration file update operation.

        Supports two sub-operations that can be used independently or together:
          * ``updates``  – update existing KEY=value lines in place
          * ``append_if_missing`` – append KEY=value lines only when the key
            does not already exist (ideal for .env files)
        """
        target_file = self._substitute_env_vars(operation['target_file'])
        updates = operation.get('updates', {})
        append_entries = operation.get('append_if_missing', {})
        backup_enabled = operation.get('backup', True)
        
        logger.debug(f"Executing text config update on file: {target_file}")
        logger.debug(f"Updates to apply: {list(updates.keys())}")
        logger.debug(f"Append-if-missing entries: {list(append_entries.keys())}")
        logger.debug(f"Backup enabled: {backup_enabled}")
        
        if not utils.file_exists(target_file):
            logger.warning(f"Text config file not found: {target_file}")
            return False

        if backup_enabled:
            backup_path = self._create_backup(target_file)
            if not backup_path:
                logger.warning(f"Failed to create backup for {target_file}, continuing with update")

        try:
            success = True

            if updates:
                substituted_updates = {}
                for key, value in updates.items():
                    substituted_updates[key] = self._substitute_env_vars(value)
                if not utils.update_config_parameters(target_file, substituted_updates):
                    logger.error(f"Failed to update existing parameters in {target_file}")
                    success = False

            if append_entries:
                substituted_append = {}
                for key, value in append_entries.items():
                    substituted_append[key] = str(self._substitute_env_vars(value))
                if not utils.append_if_missing(target_file, substituted_append):
                    logger.error(f"Failed to append entries to {target_file}")
                    success = False

            if success:
                logger.info(f"Successfully updated text config file: {target_file}")
            return success
            
        except Exception as e:
            logger.exception(f"Failed to update text config file {target_file}: {e}")
            return False

    def _execute_text_replace(self, operation: Dict[str, Any]) -> bool:
        """Execute a text find-replace operation on any text file.

        Supports ordered sub-operations: replace (literal/regex), comment_line,
        insert_after, and append_lines.  Suitable for .sh scripts,
        docker-compose files, or any text format where structured parsers
        (YAML/JSON) are too rigid.
        """
        target_file = self._substitute_env_vars(operation['target_file'])
        ops = operation.get('operations', [])
        backup_enabled = operation.get('backup', True)

        logger.debug(f"Executing text_replace on file: {target_file}")
        logger.debug(f"Number of sub-operations: {len(ops)}")

        if not utils.file_exists(target_file):
            logger.warning(f"Text file not found for text_replace: {target_file}")
            return False

        if backup_enabled:
            backup_path = self._create_backup(target_file)
            if not backup_path:
                logger.warning(f"Failed to create backup for {target_file}, continuing")

        try:
            substituted_ops = []
            for op in ops:
                sub = {}
                for k, v in op.items():
                    sub[k] = self._substitute_env_vars(v)
                substituted_ops.append(sub)

            if utils.text_replace(target_file, substituted_ops):
                logger.info(f"Successfully executed text_replace on {target_file}")
                return True
            else:
                raise Exception("text_replace returned failure")

        except Exception as e:
            logger.exception(f"text_replace failed on {target_file}: {e}")
            return False

    def _execute_json_update(self, operation: Dict[str, Any]) -> bool:
        """Execute a JSON file update operation."""
        target_file = self._substitute_env_vars(operation['target_file'])
        updates = operation.get('updates', {})
        backup_enabled = operation.get('backup', True)
        
        logger.debug(f"Executing JSON update on file: {target_file}")
        logger.debug(f"Updates to apply: {list(updates.keys())}")
        logger.debug(f"Backup enabled: {backup_enabled}")
        
        if not utils.file_exists(target_file):
            logger.warning(f"JSON file not found: {target_file}")
            return False

        # Create backup if enabled
        if backup_enabled:
            backup_path = self._create_backup(target_file)
            if not backup_path:
                logger.warning(f"Failed to create backup for {target_file}, continuing with update")

        try:
            logger.debug(f"Reading JSON file: {target_file}")
            # Read raw text to preserve formatting for write-back
            raw_text = utils.read_text_file(target_file)
            try:
                content = json.loads(raw_text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in file {target_file}: {e}")
            
            # Apply updates with environment variable substitution
            for key, value in updates.items():
                # Substitute environment variables in the value
                substituted_value = self._substitute_env_vars(value)
                
                # Try to convert to appropriate type if it's a string number
                substituted_value = self._try_convert_to_number(substituted_value)
                
                if '.' in key:
                    # Handle nested keys with dot notation
                    self._set_nested_dict_value(content, key, substituted_value)
                else:
                    content[key] = substituted_value
                logger.debug(f"Updated JSON key '{key}' = {substituted_value}")

            for spec in operation.get('array_kv_updates', []):
                self._apply_json_array_kv_updates(content, spec)

            # Write updated content back to file, preserving original formatting
            if utils.write_json_preserving(target_file, content, raw_text):
                logger.info(f"Successfully updated JSON file: {target_file}")
                return True
            else:
                raise Exception("Failed to write updated content")
            
        except Exception as e:
            logger.exception(f"Failed to update JSON file {target_file}: {e}")
            return False

    def _execute_file_management(self, operation: Dict[str, Any]) -> bool:
        """Execute a file management operation."""
        target_directories = operation.get('target_directories', [])
        file_management = operation.get('file_management', {})
        action = file_management.get('action')
        parameters = file_management.get('parameters', {})
        output_variable = file_management.get('output_variable', "")
        
        if action == 'keep_count':
            return self._execute_keep_count_operation(target_directories, parameters)
        elif action == 'file_count':
            return self._execute_file_count_operation(target_directories, parameters, output_variable)
        else:
            logger.warning(f"Unsupported file management action: {action}")
            return False

    def _execute_keep_count_operation(self, directories: List[str], parameters: Dict[str, Any]) -> bool:
        """Execute a keep_count file management operation."""
        # Substitute environment variables in count and pattern
        count_value = self._substitute_env_vars(parameters.get('count', 1))
        pattern = str(self._substitute_env_vars(parameters.get('pattern', '*.mp4')))

        # Convert count to integer if it's a string
        try:
            keep_count = int(count_value) if isinstance(count_value, str) else count_value
        except (ValueError, TypeError):
            logger.error(f"Invalid count value: {count_value}, using default of 1")
            keep_count = 1

        success = True
        removed_sensor_ids: List[str] = []
        for directory in directories:
            directory = self._substitute_env_vars(directory)

            if not utils.file_exists(directory):
                logger.error(f"Directory not found: {directory}")
                raise Exception(f"Directory not found: {directory}")

            try:
                # Find files matching the pattern
                files = list(Path(directory).glob(pattern))
                files.sort()  # Sort for consistent behavior

                if len(files) <= keep_count:
                    logger.info(f"Directory {directory}: Found {len(files)} files, keeping all (keep_count={keep_count})")
                    continue

                # Remove excess files
                files_to_remove = files[keep_count:]
                logger.info(f"Directory {directory}: Removing {len(files_to_remove)} excess files (keeping {keep_count})")

                for file_path in files_to_remove:
                    try:
                        os.remove(file_path)
                        logger.debug(f"Removed: {file_path}")
                        removed_sensor_ids.append(file_path.stem)
                    except OSError as e:
                        logger.exception(f"Failed to remove {file_path}: {e}")
                        success = False

            except Exception as e:
                logger.exception(f"Failed to process directory {directory}: {e}")
                success = False

        if removed_sensor_ids:
            if not self._filter_calibration_file(removed_sensor_ids):
                success = False

        return success

    def _filter_calibration_file(self, removed_sensor_ids: List[str]) -> bool:
        """
        Filter the calibration file by removing sensors and updating ROIs.

        Removes sensor entries whose IDs are in removed_sensor_ids. For each ROI,
        removes those IDs from the ROI's sensors list; ROIs whose sensors list
        becomes empty are dropped entirely.
        """
        calibration_dir = self.env_vars.get("CALIBRATION_DIR_MOUNT_PATH", "/usr/src/app/calibration_store")
        calibration_file_name = self.env_vars.get("CALIBRATION_FILE_NAME", "calibration.json")
        calibration_file_path = os.path.join(calibration_dir, calibration_file_name)

        if not utils.file_exists(calibration_file_path):
            logger.warning(f"Calibration file not found at {calibration_file_path}, skipping filtering")
            return True

        logger.info(f"Filtering calibration file {calibration_file_path}: removing sensors {removed_sensor_ids}")

        backup_path = self._create_backup(calibration_file_path)
        if not backup_path:
            logger.warning(f"Failed to create backup for {calibration_file_path}, continuing with filtering")

        try:
            with open(calibration_file_path, 'r') as f:
                data = json.load(f)

            removed_set = set(removed_sensor_ids)

            # Remove sensor entries
            original_sensor_count = len(data.get("sensors", []))
            data["sensors"] = [s for s in data.get("sensors", []) if s.get("id") not in removed_set]
            logger.info(f"Removed {original_sensor_count - len(data['sensors'])} sensor(s) from calibration file")

            # Update ROIs: remove sensor IDs from each ROI's sensors list,
            # drop the ROI entirely if no sensors remain
            updated_rois = []
            for roi in data.get("rois", []):
                roi_sensors = roi.get("sensors", [])
                filtered = [sid for sid in roi_sensors if sid not in removed_set]
                if not filtered and roi_sensors:
                    logger.info(f"Removing ROI '{roi.get('id')}': all its sensors were removed")
                    continue
                roi["sensors"] = filtered
                updated_rois.append(roi)
            data["rois"] = updated_rois

            with open(calibration_file_path, 'w') as f:
                json.dump(data, f, indent=4)

            logger.info(f"Calibration file updated: {len(data['sensors'])} sensor(s), {len(data['rois'])} ROI(s) remaining")
            return True

        except Exception as e:
            logger.exception(f"Failed to filter calibration file {calibration_file_path}: {e}")
            return False

    def _execute_file_count_operation(self, directories: List[str], parameters: Dict[str, Any], output_variable: str) -> bool:
        """
        Execute a file_count file management operation.
        Counts files matching a pattern and stores the result in output variables.
        """
        pattern = str(self._substitute_env_vars(parameters.get('pattern', '*')))
        total_file_count = 0
        
        for directory in directories:
            directory = self._substitute_env_vars(directory)
            
            if not utils.file_exists(directory):
                logger.warning(f"Directory not found: {directory}")
                continue
            
            try:
                # Find files matching the pattern
                files = list(Path(directory).glob(pattern))
                file_count = len(files)
                total_file_count += file_count
                
                logger.info(f"Directory {directory}: Found {file_count} files matching pattern '{pattern}'")
                
            except Exception as e:
                logger.exception(f"Failed to count files in directory {directory}: {e}")
                return False
        
        logger.info(f"Total files found across all directories: {total_file_count}")
        
        # Process output_variables to create new environment variables
        if output_variable:
            self.env_vars[output_variable] = str(total_file_count)
            logger.info(f"Set File Count output variable '{output_variable}' = {total_file_count}")        
        return True

    def execute_file_operations(self) -> bool:
        """Execute all file operations for the current profile configuration."""
        logger.debug(f"Starting execute_file_operations for {self.hardware_profile}/{self.deployment_profile}")
        if not self._has_effective_config():
            mode_msg = f" and MODE: {self.deployment_profile}" if self.deployment_modes_enabled else " (mode-less)"
            logger.error(f"No configuration found for HW Profile: {self.hardware_profile}{mode_msg}")
            return False
        
        # file_operations = utils.expand_list_anchors(
        #     self.profile_configs,
        #     [self.hardware_profile, self.deployment_profile, 'file_operations']
        # )
        # check use_commons:file_operations: true or false or 2d or 3d; default is true (2d/3d only when deployment_modes_enabled)
        use_common_file_operations = self.config.get('commons', {}).get('file_operations', "")
        if use_common_file_operations == "true" or not use_common_file_operations:
            file_operations = self._get_commons_list('file_operations')
        elif self.deployment_modes_enabled and use_common_file_operations in ['2d', '3d']:
            file_operations = self.profile_configs.get('commons', {}).get('file_operations', {}).get(use_common_file_operations, [])
        elif use_common_file_operations == "false":
            file_operations = self.config.get('file_operations', [])
        else:
            logger.warning(f"Invalid use.commons.file_operations value: {use_common_file_operations}. Must be true, false, 2d, or 3d.")
            logger.warning("Using default common file operations")
            file_operations = self._get_commons_list('file_operations')

        if self.config.get('file_operations', []):
            file_operations.extend(self.config.get('file_operations', []))
        
        logger.info(
            f"Executing {len(file_operations)} file operations for "
            f"{self.hardware_profile} GPU, {self.deployment_profile} profile"
        )
        
        success = True
        for i, operation in enumerate(file_operations):
            operation_type = operation.get('operation_type')
            if not self._is_operation_enabled(operation):
                logger.info(f"Skipping operation {i+1}/{len(file_operations)}: {operation_type} (disabled)")
                continue
            logger.info(f"Executing operation {i+1}/{len(file_operations)}: {operation_type}")
            
            try:
                if operation_type == 'yaml_update':
                    result = self._execute_yaml_update(operation)
                elif operation_type == 'text_config_update':
                    result = self._execute_text_config_update(operation)
                elif operation_type == 'text_replace':
                    result = self._execute_text_replace(operation)
                elif operation_type == 'json_update':
                    result = self._execute_json_update(operation)
                elif operation_type == 'file_management':
                    result = self._execute_file_management(operation)
                else:
                    logger.error(f"Unsupported operation type: {operation_type}")
                    result = False
                
                if not result:
                    success = False
                    logger.error(f"Operation {i+1} failed: {operation_type}")
                else:
                    logger.info(f"Operation {i+1} completed successfully: {operation_type}")
                    
            except Exception as e:
                logger.exception(f"Operation {i+1} failed with exception: {e}")
                success = False
        
        return success

    def generate_all_configs(self) -> bool:
        """
        Generate all configurations based on GPU type and profile.
        
        Returns:
            True if all operations succeeded, False otherwise
        """
        logger.info(
            f"Starting configuration generation for {self.hardware_profile} GPU "
            f"with {self.deployment_profile} profile"
        )
        
        try:
            success = self.execute_file_operations()
            
            if success:
                logger.info("Configuration generation completed successfully")
            else:
                logger.error("Some configuration operations failed")
            
            return success
            
        except Exception as e:
            logger.exception(f"Configuration generation failed: {e}")
            return False

    def get_configuration_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the current configuration.
        
        Returns:
            Dictionary containing hardware profile, deployment profile, and configuration
        """
        return {
            'hardware_profile': self.hardware_profile,
            'profile': self.deployment_profile,
            'configuration': self.config,
        }


# Readiness marker file path - used to signal successful profile configuration
PROFILE_CONFIG_READY_FILE = os.environ.get(
    'PROFILE_CONFIG_READY_FILE', 
    '/tmp/profile_config_ready'
)


def write_readiness_marker(success: bool) -> None:
    """
    Write a readiness marker file to indicate profile configuration status.
    
    Args:
        success: Whether the profile configuration was successful
    """
    try:
        if success:
            # Create the ready marker file
            with open(PROFILE_CONFIG_READY_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
            logger.info(f"Profile configuration readiness marker written to {PROFILE_CONFIG_READY_FILE}")
        else:
            # Remove the marker file if it exists (to indicate failure)
            if os.path.exists(PROFILE_CONFIG_READY_FILE):
                os.remove(PROFILE_CONFIG_READY_FILE)
            logger.info("Profile configuration readiness marker removed (configuration failed)")
    except Exception as e:
        logger.exception(f"Failed to write readiness marker: {e}")


def is_profile_config_ready() -> bool:
    """
    Check if profile configuration has completed successfully.
    
    Returns:
        True if profile configuration is complete, False otherwise
    """
    return os.path.exists(PROFILE_CONFIG_READY_FILE)


def main() -> int:
    """
    Main entry point for the profile configuration manager.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Configure logging at application entry point using our standardized logger
    from utils.logger import setup_logging
    setup_logging(os.environ.get('LOG_LEVEL', 'INFO'))
    
    try:
        manager = ProfileConfigManager()
        
        # Print configuration summary
        summary = manager.get_configuration_summary()
        logger.info("Configuration Summary:")
        for key, value in summary.items():
            logger.info(f"  {key}: {value}")

        if not manager._has_effective_config():
            logger.info("No configuration for current hardware profile; skipping file operations.")
            write_readiness_marker(True)
            return 0

        # Generate configurations
        success = manager.generate_all_configs()
        
        # Write readiness marker based on success
        write_readiness_marker(success)
        
        if success:
            logger.info("Hardware profile configuration management completed successfully")
            return 0
        else:
            logger.error("Hardware profile configuration management failed")
            return 1
            
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        write_readiness_marker(False)
        return 1


if __name__ == "__main__":
    exit(main())
