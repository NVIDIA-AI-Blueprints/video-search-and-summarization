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

import json
import logging
from math import ceil
import random

import time
from datetime import datetime
from typing import TextIO
import argparse
from contextlib import nullcontext

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema
from mdx.analytics.core.stream.source.source_factory import get_source
from mdx.analytics.core.stream.source.source_base import Source
from mdx.analytics.core.utils.schema_util import get_timestamp_from_proto_ts

# Setting up logging
logging.basicConfig(format="%(asctime)s.%(msecs)03d - %(message)s", datefmt="%y/%m/%d %H:%M:%S", level="INFO")

# Constants
DEFAULT_CONFIG = {
    "kafka": {
        "brokers": "localhost:9092",
        "topics": [
            {"name": "raw", "value": "mdx-raw"},
            {"name": "behavior", "value": "mdx-behavior"},
            {"name": "frames", "value": "mdx-frames"},
            {"name": "spaceUtilization", "value": "mdx-space-utilization"},
            {"name": "mtmc", "value": "mdx-mtmc"},
            {"name": "rtls", "value": "mdx-rtls"},
            {"name": "events", "value": "mdx-events"},
            {"name": "alerts", "value": "mdx-alerts"},
            {"name": "incidents", "value": "mdx-incidents"},
        ],
        "consumer": {
            "autoOffsetReset": "latest",
            "enableAutoCommit": False,
            "maxPollIntervalMs": 900000,
            "maxPartitionFetchBytes": 10485760,
            "maxPollRecords": 3000,
            "timeout": 0.1,
        },
        "group": "delay-test",
    },
    "redisStream": {
        "host": "localhost",
        "port": 6379,
        "consumer": {
            "readCount": 3000,
            "readBlockMs": 1,
        },
        "producer": {
            "maxLen": 10000,
        },
        "group": "delay-test",
        "streams": [
            {"name": "raw", "value": "mdx-raw"},
            {"name": "behavior", "value": "mdx-behavior"},
            {"name": "frames", "value": "mdx-frames"},
            {"name": "spaceUtilization", "value": "mdx-space-utilization"},
            {"name": "mtmc", "value": "mdx-mtmc"},
            {"name": "rtls", "value": "mdx-rtls"},
            {"name": "events", "value": "mdx-events"},
            {"name": "alerts", "value": "mdx-alerts"},
            {"name": "incidents", "value": "mdx-incidents"}
        ],
    },
    "mqtt": {
        "host": "localhost",
        "port": 1883,
        "clientId": "delay-test",
        "keepAliveSec": 60,
        "consumer": {
            "qos": 1,
            "maxPollCount": 3000,
            "pollTimeoutSec": 0.1,
        },
        "producer": {
            "qos": 1,
            "retain": True,
        },
        "topics": [
            {"name": "raw", "value": "mdx-raw"},
            {"name": "behavior", "value": "mdx-behavior"},
            {"name": "frames", "value": "mdx-frames"},
            {"name": "spaceUtilization", "value": "mdx-space-utilization"},
            {"name": "mtmc", "value": "mdx-mtmc"},
            {"name": "rtls", "value": "mdx-rtls"},
            {"name": "events", "value": "mdx-events"},
            {"name": "alerts", "value": "mdx-alerts"},
            {"name": "incidents", "value": "mdx-incidents"},
        ]
    },
    "app": [
        {
            "name": "sourceType",
            "value": "kafka"
        }
    ]
}

MAX_TITLE_LENGTH = 50
TABLE_WIDTH = MAX_TITLE_LENGTH + 71


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments for the delay test script.

    :return argparse.Namespace: Parsed arguments

    Examples::
        # Monitor all topics using MQTT
        python latency_monitor.py --source-type mqtt --host localhost

        # Monitor specific topics using Kafka with percentiles
        python latency_monitor.py --source-type kafka --topics raw frames --percentiles 50 99

        # Monitor with custom topic names
        python latency_monitor.py --topics raw behavior --topic-raw my-raw-topic --topic-behavior my-behavior-topic

        # Monitor with specific percentile latencies for detailed latency analysis
        python latency_monitor.py --percentiles 25 50 75 90 95 99 --output-file detailed_stats.txt
        
        # Use any percentile values you need (1-99)
        python latency_monitor.py --percentiles 10 20 30 40 50 60 70 80 90

        # Basic monitoring without percentiles (faster processing)
        python latency_monitor.py --topics raw behavior
    """
    parser = argparse.ArgumentParser(
        description="MDX Analytics Stream Delay Test Tool - Measures message processing delays",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
Examples:
  # Monitor all topics using Kafka (default)
  %(prog)s

  # Monitor specific topics using Kafka with p99 latency
  %(prog)s --source-type kafka --host localhost --port 9092 --topics raw frames behavior --percentiles 99

  # Use Redis Stream with custom settings
  %(prog)s --source-type redisStream --host localhost --port 6379

  # Dump statistics to file with custom percentiles
  %(prog)s --output-file statistics.txt --percentiles 25 50 75 90 95 99

  # Monitor with any percentile values you need (1-99)
  %(prog)s --topics raw frames --percentiles 10 30 50 70 90

  # Use custom topic names for specific topics
  %(prog)s --topics raw behavior --topic-raw custom-raw-topic --topic-behavior custom-behavior-topic

  # Fast monitoring without percentiles
  %(prog)s --topics behavior events --poll-interval 2

  # Comprehensive monitoring with all metrics
  %(prog)s --source-type mqtt --topics raw frames behavior events --percentiles 50 75 90 95 99 --output-file comprehensive_stats.txt
        """,
        usage="%(prog)s [options]"
    )

    # Source type selection
    parser.add_argument(
        "--source-type",
        choices=["kafka", "redisStream", "mqtt"],
        default="kafka",
        help="Type of message source to use"
    )

    # Common connection settings
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host address for Redis Stream or MQTT broker"
    )

    parser.add_argument(
        "--port",
        type=int,
        help="Port number (default: 6379 for Redis, 1883 for MQTT)"
    )

    # Topics/Streams selection
    parser.add_argument(
        "--topics",
        nargs="+",
        choices=["raw", "behavior", "frames", "spaceUtilization", "mtmc", "rtls", "events", "alerts", "incidents"],
        default=["raw", "behavior", "frames", "spaceUtilization", "mtmc", "rtls", "events", "alerts", "incidents"],
        help="Topics/streams to monitor"
    )

    # Custom topic values (optional)
    parser.add_argument(
        "--topic-raw",
        default=None,
        help="Custom topic name for raw messages (default: mdx-raw)"
    )
    parser.add_argument(
        "--topic-behavior",
        default=None,
        help="Custom topic name for behavior messages (default: mdx-behavior)"
    )
    parser.add_argument(
        "--topic-frames",
        default=None,
        help="Custom topic name for frames messages (default: mdx-frames)"
    )
    parser.add_argument(
        "--topic-space-utilization",
        default=None,
        help="Custom topic name for space utilization messages (default: mdx-space-utilization)"
    )
    parser.add_argument(
        "--topic-mtmc",
        default=None,
        help="Custom topic name for MTMC messages (default: mdx-mtmc)"
    )
    parser.add_argument(
        "--topic-rtls",
        default=None,
        help="Custom topic name for RTLS messages (default: mdx-rtls)"
    )
    parser.add_argument(
        "--topic-events",
        default=None,
        help="Custom topic name for events messages (default: mdx-events)"
    )
    parser.add_argument(
        "--topic-alerts",
        default=None,
        help="Custom topic name for alerts messages (default: mdx-alerts)"
    )
    parser.add_argument(
        "--topic-incidents",
        default=None,
        help="Custom topic name for incidents messages (default: mdx-incidents)"
    )

    # Polling settings
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Interval between polling batches in seconds"
    )

    # Percentiles to measure
    parser.add_argument(
        "--percentiles",
        nargs="+",
        type=int,
        default=[],
        help="Percentile values to measure (e.g., 50 75 90 95 99). Any integer from 1 to 99 is valid."
    )

    parser.add_argument(
        "--output-file",
        type=str,
        help="Save statistics to file (in addition to console output)"
    )

    return parser.parse_args()


def build_config(args) -> dict:
    """
    Build configuration dictionary from command-line arguments.

    :param args: Parsed command-line arguments
    :return dict: Configuration dictionary for AppConfig
    """

    # Build topics/streams list with proper formatting
    def format_topic_value(topic):
        """Format topic name to match MDX topic naming convention, checking for custom values."""
        # Check if user provided a custom value for this topic
        custom_value = None
        if topic == "raw" and args.topic_raw:
            custom_value = args.topic_raw
        elif topic == "behavior" and args.topic_behavior:
            custom_value = args.topic_behavior
        elif topic == "frames" and args.topic_frames:
            custom_value = args.topic_frames
        elif topic == "spaceUtilization" and args.topic_space_utilization:
            custom_value = args.topic_space_utilization
        elif topic == "mtmc" and args.topic_mtmc:
            custom_value = args.topic_mtmc
        elif topic == "rtls" and args.topic_rtls:
            custom_value = args.topic_rtls
        elif topic == "events" and args.topic_events:
            custom_value = args.topic_events
        elif topic == "alerts" and args.topic_alerts:
            custom_value = args.topic_alerts
        elif topic == "incidents" and args.topic_incidents:
            custom_value = args.topic_incidents

        # If custom value provided, use it; otherwise get from DEFAULT_CONFIG
        if custom_value:
            return custom_value
        # Find the default value from DEFAULT_CONFIG
        for topic_config in DEFAULT_CONFIG["kafka"]["topics"]:
            if topic_config["name"] == topic:
                return topic_config["value"]
        # Topic not found in DEFAULT_CONFIG - this should never happen with valid topics
        raise ValueError(f"Topic '{topic}' not found in DEFAULT_CONFIG. Please check the configuration.")

    topics_list = [
        {"name": topic, "value": format_topic_value(topic)}
        for topic in args.topics
    ]

    config = {
        "app": [
            {
                "name": "sourceType",
                "value": args.source_type
            }
        ],
        # Initialize all possible source configs to avoid missing key errors
        "kafka": {},
        "redisStream": {},
        "mqtt": {}
    }

    if args.source_type == "kafka":
        port = args.port if args.port else 9092
        DEFAULT_CONFIG["kafka"]["brokers"] = args.host + ":" + str(port)
        DEFAULT_CONFIG["kafka"]["topics"] = topics_list
        config["kafka"] = DEFAULT_CONFIG["kafka"]
    elif args.source_type == "redisStream":
        port = args.port if args.port else 6379
        DEFAULT_CONFIG["redisStream"]["host"] = args.host
        DEFAULT_CONFIG["redisStream"]["port"] = port
        DEFAULT_CONFIG["redisStream"]["streams"] = topics_list
        config["redisStream"] = DEFAULT_CONFIG["redisStream"]
    elif args.source_type == "mqtt":
        port = args.port if args.port else 1883
        DEFAULT_CONFIG["mqtt"]["host"] = args.host
        DEFAULT_CONFIG["mqtt"]["port"] = port
        DEFAULT_CONFIG["mqtt"]["topics"] = topics_list
        config["mqtt"] = DEFAULT_CONFIG["mqtt"]

    return config


# Topics to be processed
MESSAGE_TYPES = {
    "raw": {
        "topic": "raw",
        "proto_class": nvSchema.Frame,
        "timestamp_path": "timestamp",
        "sensor_id_path": "sensorId",
    },
    "frames": {
        "topic": "frames",
        "proto_class": nvSchema.Frame,
        "timestamp_path": "timestamp",
        "sensor_id_path": "sensorId",
    },
    "behavior": {
        "topic": "behavior",
        "proto_class": extSchema.Behavior,
        "timestamp_path": "end",
        "sensor_id_path": "sensor.id",
    },
    "mtmc": {
        "topic": "mtmc",
        "proto_class": None,  # Special case for JSON
        "timestamp_path": "end",
        "sensor_id_path": None,  # Fixed sensor ID
    },
    "spaceUtilization": {
        "topic": "spaceUtilization",
        "proto_class": extSchema.SpaceUtilization,
        "timestamp_path": "timestamp",
        "sensor_id_path": "id",
    },
    "rtls": {
        "topic": "rtls",
        "proto_class": nvSchema.Frame,
        "timestamp_path": "timestamp",
        "sensor_id_path": "sensorId",
    },
    "events": {
        "topic": "events",
        "proto_class": extSchema.Behavior,
        "timestamp_path": "end",
        "sensor_id_path": "sensor.id",
    },
    "alerts": {
        "topic": "alerts",
        "proto_class": extSchema.Behavior,
        "timestamp_path": "end",
        "sensor_id_path": "sensor.id",
    },
    "incidents": {
        "topic": "incidents",
        "proto_class": extSchema.Incident,
        "timestamp_path": "end",
        "sensor_id_path": "sensorId",
    },
}


class Stats:
    """
    Class for tracking message statistics with optional percentile analysis.

    This class provides functionality for:
    - Counting messages
    - Tracking delay statistics (min, max, average)
    - Calculating percentile delays (p50, p75, p90, p95, p99)
    - Calculating message sizes
    - Formatting statistics for display

    :ivar int count: Number of messages processed.
    :ivar float total_delay: Sum of all message delays.
    :ivar float total_size: Sum of all message sizes.
    :ivar float min_delay: Minimum observed delay.
    :ivar float max_delay: Maximum observed delay.
    :ivar list[int] percentiles: List of percentiles to calculate (any integer from 1 to 99).
    :ivar list[float] delays: List of individual delay values for percentile calculation.

    Examples::
        >>> stats = Stats()
        >>> stats.count = 10
        >>> stats.total_delay = 100.0
        >>> print(stats)  # Output: |         10 |      0.000 |     10.000 |     10.000 |       0.000 |
    """

    def __init__(self, percentiles: list[int] = []) -> None:
        """
        Initialize statistics tracking with optional percentile calculation.

        This method:
        1. Sets initial count to 0
        2. Initializes delay and size totals
        3. Sets min/max delay bounds
        4. Configures percentile tracking if specified
        5. Initializes delays list for percentile calculation

        :param list[int] percentiles: List of percentiles to calculate (any integer from 1 to 99).
                                       Empty list means no percentile calculation.
        :return: None

        Examples::
            >>> # Basic stats without percentiles
            >>> stats = Stats()
            >>> print(f"Initialized stats with count: {stats.count}")

            >>> # Stats with P50 and P99 percentiles
            >>> stats_p99 = Stats(percentiles=[50, 99])
            >>> print(f"Will calculate percentiles: {stats_p99.percentiles}")
        """
        self.count = 0
        self.total_delay = 0.0
        self.total_size = 0.0
        self.min_delay = float("inf")  # Initialize with infinity so first value becomes min
        self.max_delay = float("-inf")  # Initialize with negative infinity so first value becomes max
        self.percentiles = list(percentiles)
        self.delays = []

    def __str__(self) -> str:
        """
        Format statistics for display including percentiles if configured.

        This method:
        1. Calculates average delay
        2. Calculates average message size
        3. Calculates percentile delays if percentiles are configured
        4. Formats values into a table row

        :return str: Formatted string containing statistics with optional percentile columns.

        Examples::
            >>> # Basic stats without percentiles
            >>> stats = Stats()
            >>> stats.count = 5
            >>> stats.total_delay = 50.0
            >>> print(stats)  # Output: |          5 |      0.000 |     10.000 |     10.000 |       0.000 |

            >>> # Stats with percentiles
            >>> stats_p99 = Stats(percentiles=[50, 99])
            >>> stats_p99.count = 100
            >>> # Output includes additional P50 and P99 columns:
            >>> print(stats)  # Output: |        100 |      0.000 |     10.000 |      5.000 |       2.000 |      5.000 |      9.900 |
        """

        if self.count == 0:
            return "| No messages received |"

        avg_delay = self.total_delay / self.count
        avg_msg_size_in_kb = self.total_size / self.count

        percentile_delays = ""

        if self.percentiles and self.delays:
            for percentile in self.percentiles:
                # Using Nearest Rank Method for percentile calculation
                # Position = max(1, ceil(P/100 * N)) where P is percentile, N is count
                # This ensures position is always between 1 and N (inclusive)
                # For small sample sizes, this gives the closest available value
                p = max(1, ceil((percentile / 100) * self.count))
                percentile_value = self.get_percentile(p)
                percentile_delays += f" {percentile_value:10.3f} |"

        return (
            f"| {self.count:11d} | {self.min_delay:10.3f} | {self.max_delay:10.3f} | "
            f"{avg_delay:10.3f} | {avg_msg_size_in_kb:11.3f} |"
            f"{percentile_delays}"
        )

    def get_percentile(self, p: int) -> float:
        """
        Calculate the p-th percentile delay using quickselect algorithm.

        This method uses a quickselect (partial quicksort) algorithm to efficiently
        find the p-th smallest value in the delays list without fully sorting it.
        This provides O(n) average time complexity instead of O(n log n) for full sorting.

        :param int p: The position for percentile calculation (1-indexed).
        :return float: The delay value at the p-th percentile position.

        Examples::
            >>> stats = Stats(percentiles=[50, 99])
            >>> stats.delays = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
            >>> # For P50 with 10 values: position = max(1, ceil(50/100 * 10)) = 5
            >>> p50_delay = stats.get_percentile(5)
            >>> print(f"P50 delay: {p50_delay}")  # Output: P50 delay: 0.5
            
            >>> # With 2 values [0.1, 0.2]:
            >>> # P10: position = max(1, ceil(0.1 * 2)) = max(1, ceil(0.2)) = max(1, 1) = 1 -> 0.1
            >>> # P30: position = max(1, ceil(0.3 * 2)) = max(1, ceil(0.6)) = max(1, 1) = 1 -> 0.1
            >>> # P50: position = max(1, ceil(0.5 * 2)) = max(1, ceil(1.0)) = max(1, 1) = 1 -> 0.1  
            >>> # P90: position = max(1, ceil(0.9 * 2)) = max(1, ceil(1.8)) = max(1, 2) = 2 -> 0.2

        Note:
            This method modifies the delays list during partitioning for efficiency.
            The algorithm is optimized for repeated percentile calculations.
        """

        # Handle edge cases
        if not self.delays or p < 1:
            return 0.0
        if p > len(self.delays):
            # Return the maximum delay if position exceeds available data
            return self.max_delay if hasattr(self, 'max_delay') else max(self.delays)

        def _qselect_partition(lo: int, hi: int):
            pivot = random.randint(lo, hi)
            pivot_val = self.delays[pivot]
            self.delays[hi], self.delays[pivot] = self.delays[pivot], self.delays[hi]

            next_pivot = lo
            for curr in range(lo, hi):
                if self.delays[curr] < pivot_val:
                    self.delays[next_pivot], self.delays[curr] = self.delays[curr], self.delays[next_pivot]
                    next_pivot += 1

            self.delays[hi], self.delays[next_pivot] = self.delays[next_pivot], self.delays[hi]
            return next_pivot

        lo, hi = 0, len(self.delays) - 1
        while lo <= hi:
            pivot = _qselect_partition(lo, hi)
            if pivot == p - 1:
                return self.delays[pivot]
            elif pivot < p - 1:
                lo = pivot + 1
            else:
                hi = pivot - 1

        # This should never be reached with our percentile calculation formula
        # The quickselect algorithm should always find the p-th element
        # But keeping a fallback for safety
        return self.delays[p-1]


def calculate_delay(protobuf_timestamp: int, stream_timestamp: int) -> float:
    """
    Calculate delay between protobuf and stream timestamps.

    This method:
    1. Computes time difference in seconds
    2. Handles negative delays with warning
    3. Returns delay value

    :param int protobuf_timestamp: Timestamp from protobuf message in milliseconds.
    :param int stream_timestamp: Timestamp from stream message in milliseconds.
    :return float: Delay in seconds.

    Examples::
        >>> delay = calculate_delay(1000, 2000)
        >>> print(f"Delay: {delay} seconds")  # Output: Delay: 1.0 seconds
    """
    delay_seconds = (stream_timestamp - protobuf_timestamp) / 1000.0
    if delay_seconds < 0:
        logging.warning(
            f"Negative delay detected: {delay_seconds:.2f}s "
            f"(stream_ts: {stream_timestamp}, proto_ts: {protobuf_timestamp})"
        )
    return delay_seconds


def print_stats_table(
    title: str,
    stats_dict: dict[str, Stats],
    title_name: str = "Sensor",
    percentiles: list[int] = [],
    output_file: TextIO | None = None
) -> None:
    """
    Print a formatted table of statistics.

    This method:
    1. Prints table header with column names
    2. Prints separator line
    3. Prints statistics for each sensor
    4. Prints total statistics if available

    :param str title: Title to display above the table.
    :param dict[str, Stats] stats_dict: Dictionary mapping sensor IDs to their statistics.
    :param str title_name: Column header for the sensor ID column. Defaults to "Sensor".
    :param TextIO | None output_file: File to write statistics to.
    :return: None

    Examples::
        >>> stats = {"sensor1": Stats(), "sensor2": Stats()}
        >>> print_stats_table("Test Results", stats)
    """
    if not stats_dict:
        return

    table_width = TABLE_WIDTH + (len(percentiles) * 13)

    lines = []
    lines.append(f"\n{title}")
    lines.append("-" * table_width)
    lines.append(
        f"| {title_name}"
        + " " * (MAX_TITLE_LENGTH - len(title_name))
        + " |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |"
        + "".join([ f"     p{percentile}(s) |" for percentile in percentiles] )
    )

    lines.append("|" + "-" * (table_width - 2) + "|")

    for sensor_id, stats in sorted(stats_dict.items()):
        # Truncate sensor_id if it's too long
        if len(str(sensor_id)) > MAX_TITLE_LENGTH:
            sensor_display = str(sensor_id)[: MAX_TITLE_LENGTH - 3] + "..."
        else:
            sensor_display = str(sensor_id)
        lines.append(f"| {sensor_display:<{MAX_TITLE_LENGTH}} {stats}")

    lines.append("-" * table_width)
    for line in lines:
        print(line)
    try:
        if output_file:
            for line in lines:
                output_file.write(line + "\n")
            output_file.flush()
    except OSError as e:
        logging.error(f"Failed to write to output file: {e}")


class StatsAggregator:
    """
    Class for aggregating statistics across multiple sensors with optional percentile tracking.

    This class provides functionality for:
    - Tracking statistics per sensor
    - Updating statistics with new measurements
    - Managing multiple sensor statistics with percentile calculation
    - Retrieving aggregated statistics including percentiles
    - Creating Stats objects with consistent percentile configuration

    :ivar dict[str, Stats] stats: Dictionary mapping sensor IDs to their statistics.
    :ivar list[int] percentiles: List of percentiles to calculate for all sensors.

    Examples::
        >>> # Basic aggregator without percentiles
        >>> aggregator = StatsAggregator()
        >>> aggregator.update(1.0, 1024, "sensor1")
        >>> stats = aggregator.get_stats()
        >>> print(f"Stats for sensor1: {stats['sensor1']}")

        >>> # Aggregator with percentiles
        >>> aggregator_p99 = StatsAggregator(percentiles=[50, 99])
        >>> aggregator_p99.update(0.5, 2048, "camera_001")
    """

    def __init__(self, percentiles: list[int] = []) -> None:
        """
        Initialize statistics aggregator with optional percentile tracking.

        This method:
        1. Creates empty statistics dictionary
        2. Prepares for sensor-specific tracking
        3. Configures percentile calculation for all sensors

        :param list[int] percentiles: List of percentiles to calculate for all sensors.
                                       Empty list means no percentile calculation.
        :return: None

        Examples::
            >>> # Basic aggregator without percentiles
            >>> aggregator = StatsAggregator()

            >>> # Aggregator with P50 and P99 percentiles
            >>> aggregator_p99 = StatsAggregator(percentiles=[50, 99])
        """
        self.stats = {}
        self.percentiles = list(percentiles)

    def update(self, delay: float, data_size: float, sensorId: str) -> None:
        """
        Update statistics for a sensor including percentile data if configured.

        This method:
        1. Creates new Stats object if sensor not tracked (with percentiles if configured)
        2. Updates count, delay, and size statistics
        3. Updates min/max delay values
        4. Adds delay to delays list for percentile calculation if percentiles are enabled

        :param float delay: Message delay in seconds.
        :param float data_size: Message size in KB.
        :param str sensorId: ID of the sensor.
        :return: None

        Examples::
            >>> # Basic update without percentiles
            >>> aggregator = StatsAggregator()
            >>> aggregator.update(1.0, 1024, "sensor1")
            >>> print(f"Updated stats for sensor1")

            >>> # Update with percentiles - delay will be stored for P50/P99 calculation
            >>> aggregator_p99 = StatsAggregator(percentiles=[50, 99])
            >>> aggregator_p99.update(0.5, 2048, "camera_001")
            >>> # Delay 0.5s added to delays list for percentile calculation
        """
        if sensorId not in self.stats:
            self.stats[sensorId] = Stats(self.percentiles)
        self.stats[sensorId].count += 1
        self.stats[sensorId].total_delay += delay
        self.stats[sensorId].total_size += data_size
        self.stats[sensorId].min_delay = min(self.stats[sensorId].min_delay, delay)
        self.stats[sensorId].max_delay = max(self.stats[sensorId].max_delay, delay)
        if self.percentiles:
            self.stats[sensorId].delays.append(delay) 

    def get_stats(self) -> dict[str, Stats]:
        """
        Get all collected statistics.

        :return dict[str, Stats]: Dictionary mapping sensor IDs to their statistics.

        Examples::
            >>> aggregator = StatsAggregator()
            >>> stats = aggregator.get_stats()
            >>> print(f"Found stats for {len(stats)} sensors")
        """
        return self.stats


def process_message(
    source: Source, config: AppConfig, message_type: str, aggregators: dict[str, StatsAggregator], percentiles: list[int], output_file: TextIO | None = None
) -> None:
    """
    Process messages from Stream and update statistics.

    This method:
    1. Reads messages from Stream
    2. Extracts timestamps and sensor IDs
    3. Calculates delays
    4. Updates statistics
    5. Prints results

    :param Source source: Stream source for reading messages.
    :param AppConfig config: Application configuration.
    :param str message_type: Type of message to process.
    :param dict[str, StatsAggregator] aggregators: Dictionary of statistics aggregators.
    :param TextIO | None output_file: File to write statistics to.
    :return: None

    Examples::
        >>> source = get_source(config)
        >>> aggregators = {}

        >>> process_message(source, config, "raw", aggregators)
        >>> print("Processed raw messages")
    """
    if message_type not in aggregators:
        aggregators[message_type] = StatsAggregator(percentiles)
    aggregator = aggregators[message_type]
    topic_id = MESSAGE_TYPES[message_type]["topic"]
    proto_class = MESSAGE_TYPES[message_type]["proto_class"]
    batch_msgs = source.read(topic_id)
    timestamp_path = MESSAGE_TYPES[message_type]["timestamp_path"]
    sensor_id_path = MESSAGE_TYPES[message_type]["sensor_id_path"]

    for message in batch_msgs:
        stream_timestamp = message.timestamp
        if proto_class is None:
            data = json.loads(message.value.decode("utf-8"))
            data_timestamp = datetime.fromisoformat(data[timestamp_path].replace("Z", "")).timestamp() * 1000
        else:
            data = proto_class().FromString(message.value)
            timestamp_field = getattr(data, timestamp_path)
            data_timestamp = get_timestamp_from_proto_ts(timestamp_field).timestamp() * 1000
            # Skip latency calculation for completed incidents because they are sent after expiration window not immediately
            if message_type == "incidents" and data.info.get("isComplete") == "true":
                continue
                
        delay = calculate_delay(data_timestamp, stream_timestamp)

        # Handle nested attribute paths for sensor_id
        if sensor_id_path and "." in sensor_id_path:
            # Split the path and access nested attributes
            parts = sensor_id_path.split(".")
            current = data
            for part in parts:
                current = getattr(current, part)
            sensor_id = current
        else:
            sensor_id = "MM" if not sensor_id_path else getattr(data, sensor_id_path)

        aggregator.update(delay, len(message.value) / 1024, sensor_id)

    if message_type == "spaceUtilization":
        print_stats_table(f"SPACE UTILIZATION MESSAGES", aggregator.get_stats(), title_name="Buffer Zone", percentiles=percentiles, output_file=output_file)
        return
    print_stats_table(f"{message_type.upper()} MESSAGES", aggregator.get_stats(), percentiles=percentiles, output_file=output_file)


def main() -> None:
    """
    Main execution function for delay testing.

    This method:
    1. Initializes configuration and source
    2. Processes messages in batches
    3. Prints statistics periodically
    4. Handles graceful shutdown

    :return: None

    Examples::
        >>> main()
        >>> # Output: Periodic statistics reports
    """
    # Parse command-line arguments
    args = parse_arguments()

    # Configure logging based on arguments
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d - %(message)s",
        datefmt="%y/%m/%d %H:%M:%S",
        level="INFO"
    )

    # Build configuration from arguments
    config_dict = build_config(args)

    # Show configuration
    print("\nFull Configuration:")
    print(json.dumps(config_dict, indent=2))

    config = AppConfig(**config_dict)

    # Initialize source
    source = get_source(config)
    batch_id = 0
    aggregators = {}

    # Filter MESSAGE_TYPES based on selected topics
    selected_message_types = {
        k: v for k, v in MESSAGE_TYPES.items()
        if v["topic"] in args.topics
    }

    percentiles: list[int] = args.percentiles

    if args.output_file:
        logging.info(f"Saving statistics to: {args.output_file}")

    # Use context manager for file handling
    context = open(args.output_file, 'w') if args.output_file else nullcontext()

    try:
        with context as output_file:
            # Write header if output file is specified
            if output_file:
                try:
                    output_file.write(f"MDX Analytics Stream Delay Test - Started at {datetime.now()}\n")
                    output_file.write(f"Configuration: source={args.source_type}, topics={args.topics}\n")
                    output_file.write("=" * TABLE_WIDTH + "\n")
                except OSError as e:
                    logging.error(f"Failed to write to output file: {e}")
                    output_file = None

            try:
                while True:
                    batch_id += 1

                    # Prepare output buffer for both console and file
                    output_lines = []
                    output_lines.append("\n" + "=" * TABLE_WIDTH)
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    output_lines.append(f"Statistics Report - {current_time} - Batch ID: {batch_id}")
                    output_lines.append("=" * TABLE_WIDTH)

                    # Print to console
                    for line in output_lines:
                        print(line)

                    # Write to file if specified
                    if output_file:
                        try:
                            for line in output_lines:
                                output_file.write(line + "\n")
                            output_file.flush()
                        except OSError as e:
                            logging.error(f"Failed to write to output file: {e}")
                            output_file = None

                    for message_type in selected_message_types:
                        process_message(source, config, message_type, aggregators, percentiles, output_file)

                    time.sleep(args.poll_interval)

            except KeyboardInterrupt:
                print("\nShutting down...")
                if output_file:
                    try:
                        output_file.write(f"\nShutdown at {datetime.now()}\n")
                        output_file.flush()
                    except OSError:
                        pass  # Ignore write errors during shutdown
                source.close()

    except OSError as e:
        logging.error(f"Failed to open output file: {e}")
        # Continue without file output - the main loop will run with output_file as None
        logging.info("Continuing without file output...")


if __name__ == "__main__":
    main()
