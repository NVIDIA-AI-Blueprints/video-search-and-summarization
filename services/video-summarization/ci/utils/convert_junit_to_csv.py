#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Convert pytest junit-xml to CSV format."""
import csv
import sys
import xml.etree.ElementTree as ET
from datetime import datetime


def parse_junit_xml_to_csv(xml_file, csv_file):
    """Parse junit XML and convert to CSV with required fields."""
    # Parse the XML
    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Get the testsuite element
    testsuite = root.find("testsuite")
    if testsuite is None:
        testsuite = root if root.tag == "testsuite" else None

    if testsuite is None:
        print("Error: No testsuite found in XML", file=sys.stderr)
        return

    # Get the timestamp from the testsuite
    timestamp = testsuite.get("timestamp", datetime.now().isoformat())

    # Open CSV file for writing
    with open(csv_file, "w", newline="") as f:
        writer = csv.writer(f)

        # Write header
        writer.writerow(
            ["Test Name", "Status", "Duration_Seconds", "Time_Stamp", "Details", "Error_message"]
        )

        # Process each test case
        for testcase in testsuite.findall("testcase"):
            test_name = f"{testcase.get('classname')}.{testcase.get('name')}"
            duration = testcase.get("time", "0")

            # Determine status and error message
            error_elem = testcase.find("error")
            failure_elem = testcase.find("failure")
            skipped_elem = testcase.find("skipped")

            if error_elem is not None:
                status = "error"
                error_message = error_elem.get("message", "")
                details = error_elem.text or ""
            elif failure_elem is not None:
                status = "failure"
                error_message = failure_elem.get("message", "")
                details = failure_elem.text or ""
            elif skipped_elem is not None:
                status = "skipped"
                error_message = skipped_elem.get("message", "")
                details = skipped_elem.text or ""
            else:
                status = "passed"
                error_message = ""
                details = ""

            # Write row
            writer.writerow([test_name, status, duration, timestamp, details, error_message])

    print(f"Converted {len(list(testsuite.findall('testcase')))} tests to {csv_file}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: convert_junit_to_csv.py <junit_xml_file> <output_csv_file>")
        sys.exit(1)

    parse_junit_xml_to_csv(sys.argv[1], sys.argv[2])
