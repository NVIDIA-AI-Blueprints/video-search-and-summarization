#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Script to read two mdx data json files, sort by timestamp, and compare _source content.
"""

import json
import sys
from typing import Any
from datetime import datetime
from pathlib import Path

# Because the trajectory to detect events is not always the same, we ignore some keys
EVENT_IGNORE_KEYS = ["distance", "speed", "analyticsModule", "speedOverTime", "object", "bearing", "direction", "timeInterval"]
# Because sampling, some attributes are not always same for behavior data
BEHAVIOR_IGNORE_KEYS = ["length", "locations", "edges", "smoothLocations", "speedOverTime", "direction", "info", "bearing", "speed", "distance"]

def parse_json_lines(file_path: str) -> list[dict[str, Any]]:
    """
    Parse a JSONL file (one JSON object per line) and return list of parsed objects.

    Args:
        file_path: Path to the JSONL file

    Returns:
        list of parsed JSON objects
    """
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                data.append(obj)
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse line {line_num} in {file_path}: {e}")
                continue
    return data


def extract_timestamp(obj: dict[str, Any], key: str = 'timestamp') -> datetime | None:
    """
    Extract timestamp from a data object.

    Args:
        obj: JSON object containing timestamp

    Returns:
        datetime object
    """
    try:
        # Try to get timestamp from _source.timestamp
        timestamp_str = obj.get('_source', {}).get(key)
        if timestamp_str:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

        return None
    except Exception as e:
        print(f"Warning: Failed to parse timestamp for object: {e}")
        return None


def extract_id(obj: dict[str, Any]) -> str:
    """
    Extract sensor ID from a data object.

    Args:
        obj: JSON object containing sensor information

    Returns:
        sensor ID string
    """
    try:
        # Try to get sensor ID from _source.sensor.id
        id = obj.get('_source', {}).get('id')
        if id:
            return id

        # If no sensor ID found, use a default
        print(f"Warning: No id found in _source: {obj.get('_id', 'unknown')}")
        return ""
    except Exception as e:
        print(f"Warning: Failed to extract id for object: {e}")
        return ""


def sort_by_id_and_timestamp(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sort data by timestamp first, then by sensor ID.

    Args:
        data: list of data objects

    Returns:
        Sorted list of data objects
    """
    return sorted(data, key=lambda obj: (extract_id(obj), extract_timestamp(obj)))


def sort_by_id_and_timestamp_and_event_type(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sort events by id, timestamp, then event.type. The event.type tiebreaker is
    needed because tripwire and ROI events fired by the same trajectory share
    the same (id, timestamp) and Elasticsearch's _doc retrieval order isn't
    insertion-stable across runs.

    Args:
        data: list of event data objects

    Returns:
        Sorted list of event data objects
    """
    def get_sort_key(obj: dict[str, Any]) -> tuple:
        event = obj.get('_source', {}).get('event') or {}
        event_type = event.get('type', '') if isinstance(event, dict) else ''
        return (extract_id(obj), extract_timestamp(obj), event_type)

    return sorted(data, key=get_sort_key)


def sort_by_id_and_sensorid(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sort data by timestamp first, then by sensor ID.

    Args:
        data: list of data objects

    Returns:
        Sorted list of data objects
    """
    return sorted(data, key=lambda obj: (extract_id(obj), obj.get('_source', {}).get('sensorId')))


def sort_by_incident_fingerprint(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sort incidents by fingerprint fields matching Logstash fingerprint logic:
    - If info.primaryObjectId exists: timestamp, category, sensorId, info.primaryObjectId
    - Otherwise: timestamp, category, sensorId

    Args:
        data: list of incident data objects

    Returns:
        Sorted list of incident data objects
    """
    def get_sort_key(obj: dict[str, Any]) -> tuple:
        source = obj.get('_source', {})
        timestamp = extract_timestamp(obj)
        category = source.get('category', '')
        sensor_id = source.get('sensorId', '')
        info = source.get('info') or {}
        primary_object_id = info.get('primaryObjectId') if isinstance(info, dict) else None
        
        # Match Logstash fingerprint logic:
        # if info and info.primaryObjectId: sort by timestamp, category, sensorId, primaryObjectId
        # else: sort by timestamp, category, sensorId
        if primary_object_id:
            return (timestamp or datetime.min, category, sensor_id, str(primary_object_id))
        else:
            return (timestamp or datetime.min, category, sensor_id)
    
    return sorted(data, key=get_sort_key)


def compare_sources(data1: list[dict[str, Any]], data2: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compare _source content between two datasets.

    Args:
        data1: First dataset
        data2: Second dataset

    Returns:
        dictionary with comparison results
    """
    comparison = {
        'total_records_file1': len(data1),
        'total_records_file2': len(data2),
        'differences': [],
        'result': 'pass'
    }

    time_delta_ms = int((extract_timestamp(data2[0]) - extract_timestamp(data1[0])).total_seconds() * 1000)
    data_type = data1[0]['_source']['type']

    # Set tolerance for mdx-events, the timestamp is not always the same because of detection and messages count in each batch
    timestamp_tolerance_ms = 0
    if data_type == "mdx-events" or data_type == "mdx-space-utilization":
        timestamp_tolerance_ms = 100

    for i in range(len(data1)):
        source1 = data1[i].get('_source', {})
        source2 = data2[i].get('_source', {})
        timestamp1 = extract_timestamp(data1[i])
        timestamp2 = extract_timestamp(data2[i])
        end1 = extract_timestamp(data1[i], 'end')
        end2 = extract_timestamp(data2[i], 'end')

        diff = {}
        
        # Check timestamp difference with tolerance
        if timestamp1 and timestamp2:
            actual_delta_ms = int((timestamp2 - timestamp1).total_seconds() * 1000)
            delta_diff_ms = abs(actual_delta_ms - time_delta_ms)
            
            if delta_diff_ms > timestamp_tolerance_ms:
                diff['id'] = source1.get('id')
                diff["timestamp"] = {
                    'type': 'value_difference',
                    'value1': timestamp1,
                    'value2': timestamp2,
                    'actual_time_delta_ms': actual_delta_ms,
                    'expected_time_delta_ms': time_delta_ms,
                    'difference_ms': delta_diff_ms,
                    'tolerance_ms': timestamp_tolerance_ms
                }

        # Check end timestamp difference with tolerance
        if end1 and end2:
            actual_end_delta_ms = int((end2 - end1).total_seconds() * 1000)
            end_delta_diff_ms = abs(actual_end_delta_ms - time_delta_ms)
            
            if end_delta_diff_ms > timestamp_tolerance_ms:
                diff['id'] = source1.get('id')
                diff['end'] = {
                    'type': 'value_difference',
                    'value1': end1,
                    'value2': end2,
                    'actual_time_delta_ms': actual_end_delta_ms,
                    'expected_time_delta_ms': time_delta_ms,
                    'difference_ms': end_delta_diff_ms,
                    'tolerance_ms': timestamp_tolerance_ms
                }

        # Compare the sources
        differences = compare_source_objects(source1, source2)

        if differences:
            diff['id'] = source1.get('id')
            diff.update(differences)

        if diff:
            comparison['differences'].append(diff)

    if data_type == "mdx-behavior":
        if comparison['total_records_file1'] != comparison['total_records_file2']:
            comparison['result'] = 'fail'
        # elif len(comparison['differences']) >= len(data1) * 0.01:
        #     comparison['result'] = 'fail'
    elif len(comparison['differences']) > 0:
        comparison['result'] = 'fail'
    comparison['data_type'] = data_type
    return comparison


def is_within_percent(value1: float, value2: float, percent: float = 60) -> bool:
    """
    Check if the difference between two values is less than the given percentage.
    
    Args:
        value1: First value
        value2: Second value
        percent: Percentage tolerance (default is 50%)
        
    Returns:
        True if difference is less than the given percentage, False otherwise
    """
    if value1 == 0 and value2 == 0:
        return True
    if value1 == 0 or value2 == 0:
        return False
    
    # Calculate percentage difference
    percentage_diff = abs(value1 - value2) / max(abs(value1), abs(value2)) * 100
    return percentage_diff <= percent

def ignore_keys(differences: dict[str, Any], keys: list[str]) -> None:
    for key in keys:
        if key in differences:
            del differences[key]


def compare_object_timeline(timeline1: str, timeline2: str, float_tolerance: float = 1e-4) -> bool:
    """
    Compare two objectTimeline JSON strings by comparing time deltas (end - start) for each interval.
    
    Args:
        timeline1: First objectTimeline JSON string
        timeline2: Second objectTimeline JSON string
        float_tolerance: Tolerance for float comparisons
        
    Returns:
        True if timelines are considered equal (same objects with same durations), False otherwise
    """
    try:
        timeline1_dict = json.loads(timeline1) if isinstance(timeline1, str) else timeline1
        timeline2_dict = json.loads(timeline2) if isinstance(timeline2, str) else timeline2
        
        # Get all object IDs from both timelines
        all_object_ids = set(timeline1_dict.keys()) | set(timeline2_dict.keys())
        
        if set(timeline1_dict.keys()) != set(timeline2_dict.keys()):
            return False
        
        # Compare time deltas for each object
        for object_id in all_object_ids:
            intervals1 = timeline1_dict.get(object_id, [])
            intervals2 = timeline2_dict.get(object_id, [])
            
            if len(intervals1) != len(intervals2):
                return False
            
            # Calculate time deltas for each interval
            deltas1 = []
            deltas2 = []
            
            for interval in intervals1:
                start = datetime.fromisoformat(interval["start"].replace('Z', '+00:00'))
                end = datetime.fromisoformat(interval["end"].replace('Z', '+00:00'))
                deltas1.append((end - start).total_seconds())
            
            for interval in intervals2:
                start = datetime.fromisoformat(interval["start"].replace('Z', '+00:00'))
                end = datetime.fromisoformat(interval["end"].replace('Z', '+00:00'))
                deltas2.append((end - start).total_seconds())
            
            # Compare deltas (sort to ignore order, allow for small floating point differences)
            if len(deltas1) != len(deltas2):
                return False
            
            sorted_deltas1 = sorted(deltas1)
            sorted_deltas2 = sorted(deltas2)
            
            for delta1, delta2 in zip(sorted_deltas1, sorted_deltas2):
                if abs(delta1 - delta2) > float_tolerance:
                    return False
        
        return True
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # If parsing fails, fall back to string comparison
        return timeline1 == timeline2

def compare_source_objects(source1: dict[str, Any],
                           source2: dict[str, Any],
                           float_tolerance: float = 1e-4) -> dict[str, Any]:
    """
    Compare two source objects and return differences.

    Args:
        source1: First source object
        source2: Second source object
        float_tolerance: Tolerance for float comparisons

    Returns:
        dictionary containing differences
    """
    differences = {}

    # Get all keys from both sources
    all_keys = set(source1.keys()) | set(source2.keys())

    for key in all_keys:
        value1 = source1.get(key)
        value2 = source2.get(key)
        if key == 'Id':
            continue
        if key == 'timestamp':
            continue
        if key == 'end':
            continue

        # Check if key exists in both sources
        if key not in source1:
            differences[key] = {'type': 'missing_in_source1', 'value2': value2}
        elif key not in source2:
            differences[key] = {'type': 'missing_in_source2', 'value1': value1}
        else:
            # Key exists in both, compare values
            # Special handling for info field in incidents - compare objectTimeline by time deltas
            if key == 'info' and source1.get("type") == "mdx-incidents":
                if isinstance(value1, dict) and isinstance(value2, dict):
                    # Compare info dict with special handling for objectTimeline
                    info_diff = {}
                    all_info_keys = set(value1.keys()) | set(value2.keys())
                    for info_key in all_info_keys:
                        if info_key == 'objectTimeline':
                            # Compare objectTimeline by time deltas instead of raw timestamps
                            timeline1 = value1.get(info_key)
                            timeline2 = value2.get(info_key)
                            if timeline1 is None and timeline2 is None:
                                continue
                            elif timeline1 is None or timeline2 is None:
                                info_diff[info_key] = {
                                    'type': 'value_difference',
                                    'value1': timeline1,
                                    'value2': timeline2
                                }
                            elif not compare_object_timeline(timeline1, timeline2, float_tolerance):
                                info_diff[info_key] = {
                                    'type': 'value_difference',
                                    'value1': timeline1,
                                    'value2': timeline2
                                }
                        elif info_key not in value1:
                            info_diff[info_key] = {'type': 'missing_in_source1', 'value2': value2.get(info_key)}
                        elif info_key not in value2:
                            info_diff[info_key] = {'type': 'missing_in_source2', 'value1': value1.get(info_key)}
                        elif not values_equal(value1[info_key], value2[info_key], info_key, float_tolerance):
                            info_diff[info_key] = {
                                'type': 'value_difference',
                                'value1': value1[info_key],
                                'value2': value2[info_key]
                            }
                    if info_diff:
                        differences[key] = info_diff
                else:
                    # Fallback to normal comparison if not dicts
                    if not values_equal(value1, value2, key, float_tolerance):
                        differences[key] = {
                            'type': 'value_difference',
                            'value1': value1,
                            'value2': value2
                        }
            else:
                if not values_equal(value1, value2, key, float_tolerance):
                    differences[key] = {
                        'type': 'value_difference',
                        'value1': value1,
                        'value2': value2
                    }

    if source1.get("type") == "mdx-events":
        ignore_keys(differences, EVENT_IGNORE_KEYS)
    elif source1.get("type") == "mdx-behavior":
        # Todo: check if it's expected
        ignore_keys(differences, ["edges"])
        if source1["timeInterval"] > 10 and "timeInterval" not in differences:
            ignore_keys(differences, BEHAVIOR_IGNORE_KEYS)

    return differences


def values_equal(value1: Any, value2: Any, key: str, float_tolerance: float = 1e-4) -> bool:
    """
    Compare two values with special handling for floats.

    Args:
        value1: First value
        value2: Second value
        key: Key of the value
        float_tolerance: Tolerance for float comparisons

    Returns:
        True if values are considered equal, False otherwise
    """
    # Handle None values
    if value1 is None and value2 is None:
        return True
    if value1 is None or value2 is None:
        return False

    # Handle float comparisons
    if isinstance(value1, float) and isinstance(value2, float):
        return abs(float(value1) - float(value2)) <= float_tolerance

    if key.endswith("Objects") and isinstance(value1, str) and isinstance(value2, str):
        value1_list = value1.split("|")
        value2_list = value2.split("|")
        value1_set_list = [set(val.split(",")) for val in value1_list]
        value2_set_list = [set(val.split(",")) for val in value2_list]

        set1_of_frozensets = set(frozenset(s) for s in value1_set_list)
        set2_of_frozensets = set(frozenset(s) for s in value2_set_list)
        return set1_of_frozensets == set2_of_frozensets

    if key.endswith("Types") and isinstance(value1, str) and isinstance(value2, str):
        value1_set = set(value1.split("|"))
        value2_set = set(value2.split("|"))
        return value1_set == value2_set

    # Handle lists (recursive comparison)
    if isinstance(value1, list) and isinstance(value2, list):
        if len(value1) != len(value2):
            return False
        return all(values_equal(v1, v2, key, float_tolerance) for v1, v2 in zip(value1, value2))

    # Handle dictionaries (recursive comparison)
    if isinstance(value1, dict) and isinstance(value2, dict):
        if set(value1.keys()) != set(value2.keys()):
            return False
        return all(values_equal(value1[k], value2[k], k, float_tolerance) for k in value1.keys())

    # Default comparison
    return value1 == value2


def print_comparison_summary(comparison: dict[str, Any]):
    """
    Print a summary of the comparison results.
    
    Args:
        comparison: Comparison results dictionary
    """
    print("Comparison Summary:")
    print(f"  Expected records: {comparison['total_records_file1']}")
    print(f"  Actual records: {comparison['total_records_file2']}")

    print(f"  Total differences: {len(comparison['differences'])}")
    for i in range(len(comparison['differences'])):
        print(f"  Difference {i+1}: {comparison['differences'][i]}\n")


def main():
    """Main function to execute the comparison."""
    # Default file paths
    file1_path = "tests/integration/expected_output/warehouse_2d/mdx-behavior-data.json"
    file2_path = "tests/integration/docker_compose/apps_data/data_log/tmp/mdx-behavior-data.json"

    # Allow command line arguments to override defaults
    if len(sys.argv) >= 3:
        file1_path = sys.argv[1]
        file2_path = sys.argv[2]

    # Check if files exist
    if not Path(file1_path).exists():
        print(f"Error: File 1 does not exist: {file1_path}")
        sys.exit(1)

    if not Path(file2_path).exists():
        print(f"Error: File 2 does not exist: {file2_path}")
        sys.exit(1)

    # Read and parse files
    data1 = parse_json_lines(file1_path)
    data2 = parse_json_lines(file2_path)

    # sort by id and sensor id if frames or raw
    data_type = data1[0].get('_source', {}).get('type')
    if data_type == 'mdx-frames' or data_type == 'mdx-raw':
        sorted_data1 = sort_by_id_and_sensorid(data1)
        sorted_data2 = sort_by_id_and_sensorid(data2)
    elif data_type == 'mdx-incidents':
        # Sort incidents by fingerprint fields: timestamp, category, sensorId, [primaryObjectId]
        sorted_data1 = sort_by_incident_fingerprint(data1)
        sorted_data2 = sort_by_incident_fingerprint(data2)
    elif data_type == 'mdx-events':
        # Events at the same (id, timestamp) need event.type as tiebreaker
        sorted_data1 = sort_by_id_and_timestamp_and_event_type(data1)
        sorted_data2 = sort_by_id_and_timestamp_and_event_type(data2)
    else:
        # Sort by id and timestamp
        sorted_data1 = sort_by_id_and_timestamp(data1)
        sorted_data2 = sort_by_id_and_timestamp(data2)

    # Compare sources
    comparison = compare_sources(sorted_data1, sorted_data2)

    print("="*30)
    print(f"Data Type: {comparison['data_type']}")
    print("="*30)
    # Print summary
    print_comparison_summary(comparison)

    # # Save comparison results
    # comparison_file = output_dir / "comparison_results.json"
    # with open(comparison_file, 'w', encoding='utf-8') as f:
    #     json.dump(comparison, f, indent=2, default=str)

    # print(f"Comparison results saved to: {comparison_file}")
    print(f"Result: {comparison['result']}\n")


if __name__ == "__main__":
    main()
