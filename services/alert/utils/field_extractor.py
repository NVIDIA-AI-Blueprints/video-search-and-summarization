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
import yaml
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Cache for loaded schemas
_schema_cache = {}

def load_schema(schema_file: str) -> Dict[str, Any]:
    """Load schema from file with caching"""
    if schema_file in _schema_cache:
        return _schema_cache[schema_file]
    
    try:
        # Look for schema file in schemas directory
        if not os.path.isabs(schema_file):
            schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schemas', schema_file)
        else:
            schema_path = schema_file
        
        with open(schema_path, 'r') as f:
            schema = yaml.safe_load(f)
            _schema_cache[schema_file] = schema
            logger.info(f"Loaded schema from {schema_path}")
            return schema
            
    except Exception as e:
        logger.error(f"Failed to load schema from {schema_file}: {e}")
        return {}

def get_nested_field(data: Dict[str, Any], field_path: str, default: Any = None) -> Any:
    """
    Extract value from nested dictionary using dot notation
    
    Args:
        data: Dictionary to extract from
        field_path: Dot-separated path (e.g., "sensor.id")
        default: Default value if field not found
        
    Returns:
        Extracted value or default
        
    Example:
        get_nested_field({"sensor": {"id": "cam_12"}}, "sensor.id", "unknown")
        Returns: "cam_12"
    """
    if not field_path:
        return default
    
    try:
        keys = field_path.split('.')
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    except Exception as e:
        logger.debug(f"Error extracting field '{field_path}': {e}")
        return default

def extract_core_fields(json_data: Dict[str, Any], schema_file: str = 'request_schema.yaml') -> Dict[str, Any]:
    """
    Extract core fields using schema file
    
    Args:
        json_data: JSON data to extract from
        schema_file: Schema file to use for field mapping
        
    Returns:
        Dictionary with extracted core fields
    """
    schema = load_schema(schema_file)
    if not schema:
        logger.error(f"Failed to load schema {schema_file}, using fallback")
        return _extract_fields_fallback(json_data)
    
    fields = schema.get('fields', {})
    defaults = schema.get('defaults', {})
    
    return {
        'message_id': get_nested_field(json_data, fields.get('message_id')),
        'timestamp': get_nested_field(json_data, fields.get('timestamp')),
        'sensor_id': get_nested_field(json_data, fields.get('sensor_id')),
        'vehicle_id': get_nested_field(json_data, fields.get('vehicle_id')),
        'anomaly_type': get_nested_field(json_data, fields.get('anomaly_type'), defaults.get('anomaly_type')),
        'stream_id': get_nested_field(json_data, fields.get('stream_id'), defaults.get('stream_id')),
        'alert_type': get_nested_field(json_data, fields.get('alert_type'), defaults.get('alert_type')),
        'media_file_path': get_nested_field(json_data, fields.get('media_file_path'))
    }

def _extract_fields_fallback(json_data: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback field extraction when schema loading fails"""
    return {
        'message_id': get_nested_field(json_data, 'eventId'),
        'timestamp': get_nested_field(json_data, 'timestamp'),
        'sensor_id': get_nested_field(json_data, 'sensor.id'),
        'vehicle_id': get_nested_field(json_data, 'object.id'),
        'anomaly_type': get_nested_field(json_data, 'analyticsModule.id', 'unknown'),
        'stream_id': get_nested_field(json_data, 'streamId', 'default_stream'),
        'alert_type': get_nested_field(json_data, 'alertType', 'general'),
        'media_file_path': get_nested_field(json_data, 'mediaFilePath')
    }

def validate_required_fields(json_data: Dict[str, Any], schema_file: str = 'request_schema.yaml') -> bool:
    """
    Validate that required fields are present in JSON data
    
    Args:
        json_data: JSON data to validate
        schema_file: Schema file containing required_fields list
        
    Returns:
        True if all required fields are present, False otherwise
    """
    schema = load_schema(schema_file)
    if not schema:
        logger.warning(f"Failed to load schema {schema_file}, skipping validation")
        return True  # Allow processing to continue
    
    required_fields = schema.get('required_fields', [])
    
    for field_path in required_fields:
        if get_nested_field(json_data, field_path) is None:
            logger.error(f"Required field missing: {field_path}")
            return False
    
    return True

def format_output_message(core_fields: Dict[str, Any], enhanced_data: Optional[Dict[str, Any]] = None, schema_file: str = 'response_schema.yaml') -> Dict[str, Any]:
    """
    Format output message using response schema template
    
    Args:
        core_fields: Extracted core fields
        enhanced_data: Additional data from processing (VLM analysis, etc.)
        schema_file: Response schema file to use for formatting
        
    Returns:
        Formatted output message
    """
    schema = load_schema(schema_file)
    if not schema:
        logger.error(f"Failed to load response schema {schema_file}")
        return core_fields
    
    template = schema.get('output_template', {})
    defaults = schema.get('output_defaults', {})
    
    # Merge core fields with enhanced data
    all_data = {**core_fields}
    if enhanced_data:
        all_data.update(enhanced_data)
    
    # Add defaults for missing fields
    for key, default_value in defaults.items():
        if key not in all_data:
            all_data[key] = default_value
    
    # Format template with actual data
    try:
        formatted_message = _format_template(template, all_data)
        return formatted_message
    except Exception as e:
        logger.error(f"Failed to format output message: {e}")
        return all_data

def _format_template(template: Any, data: Dict[str, Any]) -> Any:
    """Recursively format template with data"""
    if isinstance(template, dict):
        return {key: _format_template(value, data) for key, value in template.items()}
    elif isinstance(template, list):
        return [_format_template(item, data) for item in template]
    elif isinstance(template, str) and template.startswith('{') and template.endswith('}'):
        field_name = template[1:-1]
        return data.get(field_name, template)
    else:
        return template 