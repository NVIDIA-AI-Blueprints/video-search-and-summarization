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
Script to generate line charts from MDX Analytics Stream statistics file.
Plots average of sensor averages over time for specified message types.
"""

import argparse
import re
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import sys
import statistics

from collections import defaultdict
from datetime import datetime
from pydantic import BaseModel, Field


# Constants
MESSAGE_TYPES = ['RAW', 'FRAMES', 'BEHAVIOR', 'EVENTS', 'MTMC', 'RTLS', 'ALERTS']

# Chart configuration
FIGURE_SIZE = (12, 6)
FIGURE_SIZE_COMBINED = (14, 7)
CHART_DPI = 100
LABEL_FREQUENCY = 10  # Show every nth label to avoid crowding

# Regex patterns (compiled once for efficiency)
TIMESTAMP_PATTERN = re.compile(r'Statistics Report - (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
MESSAGE_TYPE_PATTERN = re.compile(r'^(RAW|FRAMES|BEHAVIOR|EVENTS|MTMC|RTLS|ALERTS) MESSAGES', re.IGNORECASE)
ANY_MESSAGE_TYPE_PATTERN = re.compile(r'^[A-Z]+ MESSAGES')
DATA_LINE_PATTERN = re.compile(r'\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*(?:\|\s*([\d.]+)\s*)?(?:\|\s*([\d.]+)\s*)?(?:\|\s*([\d.]+)\s*)?(?:\|\s*([\d.]+)\s*)?\|')
PERCENTILE_PATTERN = re.compile(r'p\d{2}\(s\)', re.IGNORECASE)
HEADER_PATTERN = re.compile(r'\|\s*Sensor\s*\|\s*Count\s*\|\s*Min\(s\)\s*\|\s*Max\(s\)\s*\|\s*Avg\(s\)\s*\|\s*Size\(KB\)\s*(?:\|\s*p\d{2}\(s\)\s*){0,4}\|', re.IGNORECASE)

# Colors for message types in combined chart
MESSAGE_TYPE_COLORS = {
    'RAW': 'blue',
    'FRAMES': 'green',
    'BEHAVIOR': 'orange',
    'EVENTS': 'red',
    'MTMC': 'brown',
    'RTLS': 'pink',
    'ALERTS': 'gray'
}


class ChartData(BaseModel):
    timestamps: list[datetime] = Field(default_factory=list)
    averages: dict[str, list[float | None]] = Field(default_factory=lambda: defaultdict(list))
    percentiles: dict[str, list[float | None]] = Field(default_factory=lambda: defaultdict(list))


class LineChartMetadata(BaseModel):
    title: str
    x_label: str
    y_label: str


def parse_statistics_file(filename: str, percentile: int | None = None) -> ChartData:
    """
    Parse the statistics file and extract data for each message type.
    
    Returns a dictionary with structure:
    {
        'timestamp': [list of datetime objects],
        'RAW': [list of average values or None],
        'FRAMES': [list of average values or None],
        ...
    }
    
    Raises:
        FileNotFoundError: If the input file doesn't exist
        ValueError: If the file format is invalid
    """

    chart_data = ChartData()

    try:
        with open(filename) as f:
            lines = f.readlines()
    except FileNotFoundError:
        raise FileNotFoundError(f"Statistics file not found: {filename}")
    except OSError as e:
        raise OSError(f"Error reading file {filename}: {e}")
    
    current_timestamp = None
    current_message_type = None
    sensor_averages = []
    percentile_cols = {}
    sensor_percentiles = []
    
    # Use a dictionary to track timestamp indices for O(1) lookup
    timestamp_indices = {}
    
    def save_data_point(chart_data, current_timestamp, current_message_type):
        """Save the accumulated data point for the current timestamp and message type."""
        if current_timestamp and current_message_type and sensor_averages:
            avg_of_avgs = sum(sensor_averages) / len(sensor_averages)
            percentile_median = statistics.median(sensor_percentiles) if sensor_percentiles else None
            # Only add timestamp once per report
            if current_timestamp not in timestamp_indices:
                idx = len(chart_data.timestamps)
                timestamp_indices[current_timestamp] = idx
                chart_data.timestamps.append(current_timestamp)
                # Initialize with None for missing message types
                for msg_type in MESSAGE_TYPES:
                    chart_data.averages[msg_type].append(None)
                    chart_data.percentiles[msg_type].append(None)
            else:
                idx = timestamp_indices[current_timestamp]
            
            # Update the value for current message type
            chart_data.averages[current_message_type][idx] = avg_of_avgs
            chart_data.percentiles[current_message_type][idx] = percentile_median
    
    for i, line in enumerate(lines):
        # Check for timestamp
        timestamp_match = TIMESTAMP_PATTERN.search(line)
        if timestamp_match:
            # Save previous data if exists
            save_data_point(chart_data, current_timestamp, current_message_type)
            
            # Parse new timestamp
            current_timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
            sensor_averages = []
            percentile_cols = {}
            sensor_percentiles = []
            continue
        
        # Check for message type
        message_match = MESSAGE_TYPE_PATTERN.search(line)
        if message_match:
            # Save previous message type data if exists
            save_data_point(chart_data, current_timestamp, current_message_type)

            current_message_type = message_match.group(1).upper()
            sensor_averages = []
            percentile_cols = {}
            sensor_percentiles = []
            continue
        
        # Check for any other message type (like SPACE MESSAGES) to reset current_message_type
        if ANY_MESSAGE_TYPE_PATTERN.search(line):
            # Save previous message type data if exists
            save_data_point(chart_data, current_timestamp, current_message_type)
            current_message_type = None  # Reset to prevent mixing data
            sensor_averages = []
            percentile_cols = {}
            sensor_percentiles = []
            continue

        # Check for header line
        if percentile:
            header_match = HEADER_PATTERN.search(line)
            if header_match and current_message_type and not percentile_cols:
                try:
                    columns = [col.strip() for col in line.split('|')]

                    for col_index, col_name in enumerate(columns):
                        if col_name:
                            if (p_col_match := PERCENTILE_PATTERN.search(col_name)):
                                percentile_col = p_col_match.group()[1:3]  # Characters 1-2
                                percentile_cols[int(percentile_col)] = col_index

                except (ValueError, IndexError) as e:
                    print(f"Warning: Could not parse header on line {i+1}: {e}")

        # Check for data line
        data_match = DATA_LINE_PATTERN.search(line)
        if data_match and current_message_type:
            try:
                # Extract average value (5th column in the matched groups)
                avg_value = float(data_match.group(5))
                sensor_averages.append(avg_value)

                if percentile and (col_index := percentile_cols.get(percentile)) is not None:
                    group_value = data_match.group(col_index)
                    if group_value is not None:  # Optional groups can be None
                        percentile_value = float(group_value)
                        sensor_percentiles.append(percentile_value)
                    else:
                        print(f"Warning: Percentile p{percentile} group {col_index} is None")

            except (ValueError, IndexError) as e:
                print(f"Warning: Could not parse value on line {i+1}: {e}")
    
    # Save last data point if exists
    save_data_point(chart_data, current_timestamp, current_message_type)
    
    return chart_data


def create_line_chart(chart_metadata: LineChartMetadata, data: dict[str, list], message_type: str, output_file: str | None  = None) -> None:
    """
    Create a line chart for the specified message type.
    
    Args:
        data: Parsed data dictionary
        message_type: Type of messages to plot
        filename: Input filename (for chart title)
        output_file: Output filename for saving the chart (optional)
    """
    # Filter out None values
    timestamps = []
    values = []
    
    for i, ts in enumerate(data['timestamp']):
        if data[message_type][i] is not None:
            timestamps.append(ts)
            values.append(data[message_type][i])
    
    if not timestamps:
        print(f"No data found for {message_type} messages")
        return
    
    # Create the plot
    plt.figure(figsize=FIGURE_SIZE)
    plt.plot(timestamps, values, marker='o', linestyle='-', linewidth=2, markersize=6)
    
    # Format the plot
    plt.xlabel(chart_metadata.x_label, fontsize=12)
    plt.ylabel(chart_metadata.y_label, fontsize=12)
    plt.title(f'{message_type} {chart_metadata.title}', fontsize=14)
    plt.grid(True, alpha=0.3)
    
    # Format x-axis to show time nicely
    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    
    # Add value labels on points (show every nth label to avoid crowding)
    label_step = max(1, len(timestamps) // LABEL_FREQUENCY)
    for i, (x, y) in enumerate(zip(timestamps, values)):
        if i % label_step == 0:
            plt.annotate(f'{y:.3f}', 
                        xy=(x, y), 
                        xytext=(0, 5),
                        textcoords='offset points',
                        ha='center',
                        fontsize=8)
    
    plt.tight_layout()
    
    if output_file:
        plt.savefig(output_file, dpi=CHART_DPI, bbox_inches='tight')
        print(f"Chart saved to {output_file}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description='Generate line charts from MDX Analytics Stream statistics file'
    )
    parser.add_argument(
        '-f', '--filename',
        required=True,
        help='Path to the statistics file'
    )
    parser.add_argument(
        '-t', '--type',
        required=True,
        choices=['raw', 'frames', 'behavior', 'events', 'mtmc', 'rtls', 'alerts', 'all'],
        help='Type of messages to plot (raw, frames, behavior, events, mtmc, rtls, alerts, or all for all types)'
    )
    parser.add_argument(
        "-p", "--percentile",
        type=int,
        help="Percentile value to plot (any integer from 1 to 99)"
    )
    parser.add_argument(
        '-o', '--output',
        help='Output filename for saving the chart (if not specified, chart will be displayed), must end with .png'
    )
    parser.add_argument(
        '--combine',
        action='store_true',
        help='When using all, combine all message types in one chart'
    )
    
    args = parser.parse_args()
    
    # Convert message type to uppercase for internal processing
    message_type = args.type.upper()

    if args.output and not args.output.endswith('.png'):
        print(f'Not a valid output file {args.output}, must end with .png')
        sys.exit(1)
    
    # Validate percentile value if provided
    if args.percentile is not None and (args.percentile < 1 or args.percentile > 99):
        print(f'Error: Percentile value must be between 1 and 99, got {args.percentile}')
        sys.exit(1)

    # Parse the statistics file
    print(f"Parsing {args.filename}...")
    try:
        chart_data = parse_statistics_file(args.filename, args.percentile)

    except (FileNotFoundError, OSError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    if not chart_data.timestamps:
        print("No data found in the file")
        return
    
    print(f"Found {len(chart_data.timestamps)} time points")
    
    # Use the constant for message types
    all_types = MESSAGE_TYPES
    
    # Generate chart(s)
    if message_type == 'ALL':
        if args.combine:    #TODO: use create_line_chart()
            # Create combined chart
            plt.figure(figsize=FIGURE_SIZE_COMBINED)
            
            # Plot each message type that has data
            for msg_type in all_types:
                timestamps = []
                values = []
                
                for i, ts in enumerate(chart_data.timestamps):
                    if chart_data.averages[msg_type][i] is not None:
                        timestamps.append(ts)
                        values.append(chart_data.averages[msg_type][i])
                
                if timestamps:
                    plt.plot(timestamps, values, 
                            marker='o', 
                            linestyle='-', 
                            linewidth=2, 
                            markersize=5,
                            label=msg_type,
                            color=MESSAGE_TYPE_COLORS[msg_type])
            
            plt.xlabel('Time', fontsize=12)
            plt.ylabel('Average Delay (seconds)', fontsize=12)
            plt.title(f'All Message Types - Averages Over Time\n{args.filename}', fontsize=14)
            plt.grid(True, alpha=0.3)
            plt.legend(loc='best')
            
            ax = plt.gca()
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            if args.output:
                output_file = args.output
                plt.savefig(output_file, dpi=CHART_DPI, bbox_inches='tight')
                print(f"Combined chart saved to {output_file}")
            else:
                plt.show()

        else:
            # Create separate charts for each message type that has data
            for msg_type in all_types:
                # Check if this message type has any data
                has_data = any(chart_data.averages[msg_type][i] is not None for i in range(len(chart_data.timestamps)))
                if has_data:
                    print(f"Creating chart for {msg_type} messages...")
                    if args.output:
                        output_file = args.output.replace('.', f'_{msg_type}.')
                    else:
                        output_file = None

                    chart_metadata = LineChartMetadata(
                        title=f'Messages - Averages Over Time\n{args.filename}',
                        x_label='Time',
                        y_label='Average Delay (seconds)'
                    )

                    data = {
                        'timestamp': chart_data.timestamps,
                        msg_type: chart_data.averages[msg_type]
                    }

                    create_line_chart(chart_metadata, data, msg_type, output_file)

                if args.percentile:
                    has_data = any(chart_data.percentiles[msg_type][i] is not None for i in range(len(chart_data.timestamps)))
                    if has_data:
                        print(f"Creating percentile chart for {msg_type} messages...")
                        if args.output:
                            output_file = args.output.replace('.', f'_p{args.percentile}_{msg_type}.')
                        else:
                            output_file = None

                        chart_metadata = LineChartMetadata(
                            title=f'Messages - p{args.percentile} Over Time\n{args.filename}',
                            x_label='Time',
                            y_label=f'p{args.percentile} Delay (seconds)'
                        )

                        data = {
                            'timestamp': chart_data.timestamps,
                            msg_type: chart_data.percentiles[msg_type]
                        }

                        create_line_chart(chart_metadata, data, msg_type, output_file)

    else:
        # print(f'0000 Chartdata: {chart_data}')

        print(f"Creating chart for {message_type} messages...")
        chart_metadata = LineChartMetadata(
            title=f'Messages - Averages Over Time\n{args.filename}',
            x_label='Time',
            y_label='Average Delay (seconds)'
        )

        data = {
            'timestamp': chart_data.timestamps,
            message_type: chart_data.averages[message_type]
        }

        create_line_chart(chart_metadata, data, message_type, args.output)

        if args.percentile:
            print(f"Creating percentile chart for {message_type} messages...")

            if args.output:
                output_file = args.output.replace('.', f'_p{args.percentile}.')
            else:
                output_file = None

            chart_metadata = LineChartMetadata(
                title=f'Messages - p{args.percentile} Over Time\n{args.filename}',
                x_label='Time',
                y_label=f'p{args.percentile} Delay (seconds)'
            )

            data = {
                'timestamp': chart_data.timestamps,
                message_type: chart_data.percentiles[message_type]
            }

            create_line_chart(chart_metadata, data, message_type, output_file)

    print("Done!")


if __name__ == '__main__':
    main()
