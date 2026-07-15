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

"""Merge multiple test-result CSV files (same schema) into one output CSV.

Expects CSVs with header: Test Name, Status, Duration_Seconds, Time_Stamp, Details, Error_message.
Uses the first file's header; subsequent files are appended by data rows only.
Missing files are skipped with a warning.
"""
import argparse
import csv
import sys
from pathlib import Path


def merge_csvs(input_paths, output_path):
    """Read input CSVs, merge rows (single header from first file), write output."""
    header = None
    total_rows = 0

    with open(output_path, "w", newline="") as out_f:
        writer = None

        for path in input_paths:
            p = Path(path)
            if not p.exists():
                print(f"Warning: skipping missing file {path}", file=sys.stderr)
                continue

            with open(p, newline="") as f:
                reader = csv.reader(f)
                row_header = next(reader, None)
                if row_header is None:
                    continue
                if header is None:
                    header = row_header
                    writer = csv.writer(out_f)
                    writer.writerow(header)
                elif row_header != header:
                    print(
                        f"Warning: header mismatch in {path}, using existing header",
                        file=sys.stderr,
                    )
                for row in reader:
                    if len(row) == len(header):
                        writer.writerow(row)
                        total_rows += 1
                    else:
                        print(
                            f"Warning: skipping row with wrong column count in {path}",
                            file=sys.stderr,
                        )

    if header is None:
        print("Error: no valid input files found", file=sys.stderr)
        sys.exit(1)
    num_files = len([p for p in input_paths if Path(p).exists()])
    print(f"Merged {total_rows} rows from {num_files} file(s) to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Merge test result CSV files into one.")
    parser.add_argument("-o", "--output", required=True, help="Output CSV path")
    parser.add_argument("inputs", nargs="+", help="Input CSV paths (order preserved)")
    args = parser.parse_args()
    merge_csvs(args.inputs, args.output)


if __name__ == "__main__":
    main()
