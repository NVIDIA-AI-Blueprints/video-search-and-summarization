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

import json
import logging
from json import loads
from datetime import datetime, timedelta
from mdx.anomaly.protobuf.ext_pb2 import GeoLocation
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from google.protobuf.timestamp_pb2 import Timestamp
from jsonschema import validate
from mdx.anomaly.protobuf import (
    Behavior as nvSchemaBehavior,
    Incident as nvSchemaIncident,
    ImageFormat
)
from mdx.anomaly.protobuf.schema_pb2 import Place

from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

def protobuf_anomaly_to_json_string(anomaly_pb, message_type):
    try:
        # Choose appropriate protobuf class based on message type
        if message_type.lower() == 'incident':
            proto_message = nvSchemaIncident()
        else:  # Default to Behavior
            proto_message = nvSchemaBehavior()
        
        # Parse the serialized Protobuf message
        proto_message.ParseFromString(anomaly_pb)
        message_json = json_format.MessageToJson(proto_message, always_print_fields_with_no_presence=True)

        return message_json

    except DecodeError as e:
        logging.error("Failed to parse Protobuf message: %s", e)
        # Log part of the input for inspection
        logging.debug("Message content (truncated): %s", anomaly_pb[:100])
        raise

def protobuf_anomalies_to_json_string_list(anomaly_pbs, message_type):
    result = []
    for topic in anomaly_pbs.keys():
        sub_anomaly_pbs = anomaly_pbs[topic]
        for _, anomaly_pb, *__ in sub_anomaly_pbs:  # Ignore key and kafka_ts_ms if present
            json_str = protobuf_anomaly_to_json_string(anomaly_pb, message_type)
            result.append(json_str)

    return result


def convert_json_to_protobuf(anomaly_json: dict) -> nvSchemaBehavior:
    """
    Converts a JSON dictionary to a Protobuf message.

    Args:
        anomaly_json (dict): JSON representation of the anomaly.

    Returns:
        nvSchemaBehavior: Protobuf message.
    """
    try:
        proto_message = nvSchemaBehavior()
        json_format.ParseDict(anomaly_json, proto_message)
        return proto_message
    except Exception as e:
        logging.error(
            f"Error converting JSON to Protobuf: {e}", exc_info=True)
        raise

def _stringify_map_values(target: Dict[str, Any]) -> None:
    for key, value in list(target.items()):
        if isinstance(value, dict) or isinstance(value, list):
            target[key] = json.dumps(value)
        elif value is None:
            target[key] = ""
        else:
            target[key] = str(value)


def convert_incident_to_protobuf_incident(incident_json: dict,
                                          ignore_unknown_fields: bool = True) -> nvSchemaIncident:
    """Convert an incident JSON dictionary to an ``nvSchemaIncident`` protobuf message.

    The converter relies on ``google.protobuf.json_format.ParseDict`` so that the
    JSON representation aligns with the canonical protobuf schema. ``ignore_unknown_fields``
    defaults to ``True`` allowing downstream enrichment (e.g. VLM metadata) without
    breaking serialization.

    Args:
        incident_json: JSON representation of the incident.
        ignore_unknown_fields: Whether unknown JSON properties should be ignored
            during conversion. Defaults to ``True``.

    Returns:
        nvSchemaIncident: Parsed protobuf message.
    """
    try:
        proto_message = nvSchemaIncident()

        incident_copy = json.loads(json.dumps(incident_json))

        info_block = incident_copy.get("info")
        if isinstance(info_block, dict):
            _stringify_map_values(info_block)

        json_format.ParseDict(
            incident_copy,
            proto_message,
            ignore_unknown_fields=ignore_unknown_fields
        )
        return proto_message
    except Exception as exc:  # pragma: no cover - caller decides recovery strategy
        logger.error(
            "Error converting JSON to Incident protobuf",
            exc_info=True,
            extra={"error": str(exc)}
        )
        raise

def convert_behavior_to_protobuf_behavior(behavior: dict) -> nvSchemaBehavior:
    """
    Converts a behavior JSON dictionary to a protobuf nvSchemaBehavior object.

    Args:
        behavior (dict): Behavior/anomaly JSON dictionary.

    Returns:
        nvSchemaBehavior: Protobuf object representing the behavior.
    """
    protobuf_behavior = nvSchemaBehavior()

    # Map direct fields
    protobuf_behavior.id = behavior.get("id", "")
    protobuf_behavior.edges.extend(behavior.get("edges", []))
    protobuf_behavior.distance = behavior.get("distance", 0.0)
    protobuf_behavior.speed = behavior.get("speed", 0.0)
    protobuf_behavior.speedOverTime.extend(behavior.get("speedOverTime", []))
    protobuf_behavior.timeInterval = behavior.get("timeInterval", 0.0)
    protobuf_behavior.bearing = behavior.get("bearing", 0.0)
    protobuf_behavior.direction = behavior.get("direction", "")
    protobuf_behavior.length = behavior.get("length", 0)

    # Timestamps (directly updating the fields from the behavior dictionary)
    protobuf_behavior.timestamp.FromJsonString(behavior.get("timestamp", "1970-01-01T00:00:00Z"))
    protobuf_behavior.end.FromJsonString(behavior.get("end", "1970-01-01T00:00:00Z"))

    # Locations
    map_geo_location(protobuf_behavior.locations, behavior.get("locations"))
    map_geo_location(protobuf_behavior.smoothLocations, behavior.get("smoothLocations"))

    # Place
    protobuf_behavior.place.CopyFrom(place_to_nv_place(behavior.get("place", {})))

    # Sensor, Analytics Module, Object, and Event
    protobuf_behavior.sensor.id = behavior.get("sensor", {}).get("id", "")
    protobuf_behavior.analyticsModule.id = behavior.get("analyticsModule", {}).get("id", "")

    protobuf_behavior.analyticsModule.info['dropped'] = str(behavior.get("dropped", False))
    am_info = dict(behavior.get("analyticsModule", {}).get("info", {}))
    _stringify_map_values(am_info)
    for key, value in am_info.items():
        protobuf_behavior.analyticsModule.info[key] = value

    protobuf_behavior.object.id = behavior.get("object", {}).get("id", "")
    protobuf_behavior.object.type = behavior.get("object", {}).get("type", "")
    protobuf_behavior.object.confidence = behavior.get("object", {}).get("confidence", 0.0)

    # Video path and info
    protobuf_behavior.videoPath = behavior.get("videoPath", "")
    info_block = behavior.get("info", {})
    if isinstance(info_block, dict):
        _stringify_map_values(info_block)
        for key, value in info_block.items():
            protobuf_behavior.info[key] = value

    # Embeddings
    for embedding in behavior.get("embeddings", []):
        protobuf_behavior.embeddings.add().vector.extend(embedding.get("vector", []))

    
    return protobuf_behavior

def map_geo_location(proto_geo_location, geo_location_dict):
    """Maps geo-location data from dict to protobuf."""
    if geo_location_dict:
        proto_geo_location.type = geo_location_dict.get("type", "")
        for coord in geo_location_dict.get("coordinates", []):
            point = GeoLocation.Point(point=coord.get("point", []))
            proto_geo_location.coordinates.append(point)

def place_to_nv_place(place: Optional[dict]) -> Any:
    """
    Convert place dictionary to a protobuf Place object.

    Args:
        place (dict): A place dictionary to convert.

    Returns:
        Place: Protobuf Place object.
    """
    proto_place = Place(
        name=place.get("name", "")
    )
    return proto_place

def convert_detected_event_to_incident(sampling_entity: dict) -> nvSchemaIncident:
    """
    Converts a detected event dict to an Incident protobuf message.
    Only for sampling entities where VLM detected a scenario.
    
    Args:
        sampling_entity: Dictionary containing sampling entity data
        
    Returns:
        nvSchemaIncident: Protobuf message representing the incident
    """
    try:
        incident = nvSchemaIncident()
        
        # Extract timestamp from sampling entity
        entity_timestamp = datetime.strptime(
            sampling_entity['timeStamp'], 
            '%Y-%m-%dT%H:%M:%S.%fZ'
        ) if isinstance(sampling_entity['timeStamp'], str) else sampling_entity['timeStamp']
        
        # Set timestamps from entity
        timestamp = Timestamp()
        timestamp.FromDatetime(entity_timestamp)
        incident.timestamp.CopyFrom(timestamp)
        incident.end.CopyFrom(timestamp)  # Using same timestamp for end

        # Calculate end time based on the configurable duration
        end_timestamp = entity_timestamp + timedelta(minutes=2)  # Default to 2 minutes
        end_time = Timestamp()
        end_time.FromDatetime(end_timestamp)
        incident.end.CopyFrom(end_time)
        
        # Required fields
        incident.sensorId = sampling_entity['sensorName']
        
        # Required object IDs with dummy values
        incident.objectIds.extend(["999"])
        
        # Set isAnomaly to true since it's a detected event
        incident.isAnomaly = True

        incident.category = "Others"
        
        # Map sensorLocation to place
        sensor_location = sampling_entity.get('sensorLocation', 'unknown_location')
        incident.place.CopyFrom(place_to_nv_place({"name": sensor_location}))
        
        # Add VLM response details if available
        vlm_response = sampling_entity.get('vlmResponse', {})
        if vlm_response and 'response' in vlm_response:
            response = vlm_response['response'][0]
            metadata = response.get('metadata', {})
            
            incident.info.update({
                "vlm_description": response.get('content', ''),
                "scenario_detected": str(metadata.get('scenario_detected', False)),
                "sampling_prompt": sampling_entity.get('prompt', '')
            })

        logging.debug(
            f"Created incident protobuf for detected event: {sampling_entity['sensorName']}"
        )
        return incident

    except Exception as e:
        logging.error(
            f"Error converting detected event to incident: {e}",
            exc_info=True
        )
        raise

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

def extract_core_fields(json_data: Dict[str, Any], schema_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract core fields using schema configuration
    
    Args:
        json_data: JSON data to extract from
        schema_config: Configuration containing schema_fields mapping
        
    Returns:
        Dictionary with extracted core fields
    """
    schema_fields = schema_config.get('schema_fields', {})
    defaults = schema_fields.get('defaults', {})
    
    return {
        'message_id': get_nested_field(json_data, schema_fields.get('message_id')),
        'timestamp': get_nested_field(json_data, schema_fields.get('timestamp')),
        'sensor_id': get_nested_field(json_data, schema_fields.get('sensor_id')),
        'vehicle_id': get_nested_field(json_data, schema_fields.get('vehicle_id')),
        'anomaly_type': get_nested_field(json_data, schema_fields.get('anomaly_type'), defaults.get('anomaly_type')),
        'stream_id': get_nested_field(json_data, schema_fields.get('stream_id'), defaults.get('stream_id')),
        'alert_type': get_nested_field(json_data, schema_fields.get('alert_type')),
        'media_file_path': get_nested_field(json_data, schema_fields.get('media_file_path'))
    }

def validate_required_fields(json_data: Dict[str, Any], schema_config: Dict[str, Any]) -> bool:
    """
    Validate that required fields are present in JSON data
    
    Args:
        json_data: JSON data to validate
        schema_config: Configuration containing required_fields list
        
    Returns:
        True if all required fields are present, False otherwise
    """
    required_fields = schema_config.get('required_fields', [])
    
    for field_path in required_fields:
        if get_nested_field(json_data, field_path) is None:
            logger.error(f"Required field missing: {field_path}")
            return False
    
    return True
