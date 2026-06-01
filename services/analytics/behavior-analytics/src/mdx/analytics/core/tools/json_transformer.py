# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import argparse
import json
import logging
import os

from google.protobuf.json_format import MessageToDict

from mdx.analytics.core.utils.io_utils import validate_file_path
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame_legacy

logging.basicConfig(format="%(asctime)s.%(msecs)03d - %(message)s", datefmt="%y/%m/%d %H:%M:%S", level=logging.INFO)
MILLIS_PER_SECOND = 1000


class JsonTransformer:
    """
    A utility class for transforming JSON files from an old format to a new protobuf-based JSON format.
    The transformation process involves:
    1. Reading frames from the input JSON file
    2. Converting each frame to a protobuf format
    3. Sorting frames by timestamp
    4. Writing the transformed frames to the output file

    :ivar str input_path: Path to the input JSON file containing frames in the old format.
    :ivar str output_path: Path where the transformed protobuf JSON file will be written.
    :raises FileNotFoundError: If the input file does not exist.
    :raises json.JSONDecodeError: If the input file contains invalid JSON.

    Examples::
        >>> transformer = JsonTransformer("input.json", "output.json")
        >>> transformer.transform()
    """

    def __init__(self, input_path: str, output_path: str) -> None:
        """
        Initialize the JsonTransformer with input and output file paths.
        Validates that the input file exists and both paths are valid.

        :param str input_path: Path to the input JSON file.
        :param str output_path: Path for the output protobuf JSON file.
        """
        # Make sure the input file exists
        self.input_path = validate_file_path(input_path)
        if not os.path.exists(self.input_path):
            logging.error(f"ERROR: The indicated input file `{self.input_path}` does NOT exist.")
            exit(1)

        self.output_path = validate_file_path(output_path)

    def transform(self) -> None:
        """
        Transform the input JSON file to the new protobuf JSON format.
        The process includes:
        1. Loading frames from the input file
        2. Converting each frame to protobuf format
        3. Sorting frames by timestamp
        4. Writing transformed frames to the output file

        :return: None

        Examples::
            >>> transformer = JsonTransformer("input.json", "output.json")
            >>> transformer.transform()
        """
        try:
            # Init data, which is list of protobuf frames
            logging.info(f"loading data from file: {self.input_path}")
            with open(self.input_path) as f:
                frames = [dict_frame_to_protobuf_frame_legacy(json.loads(line)) for line in f if line.strip()]
            frames.sort(key=lambda x: x.timestamp.ToMilliseconds())

            # Write new format of data into new file
            logging.info(f"writing data into file: {self.output_path}")
            with open(self.output_path, "w") as file:
                for frame in frames:
                    frame_json = MessageToDict(frame)
                    file.write(json.dumps(frame_json) + "\n")

        except Exception as e:
            logging.error(f"An error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default="", help="The input data file path")
    parser.add_argument("--output", type=str, default="", help="The output data file path")
    args = parser.parse_args()
    transfomer = JsonTransformer(args.input, args.output)
    transfomer.transform()
