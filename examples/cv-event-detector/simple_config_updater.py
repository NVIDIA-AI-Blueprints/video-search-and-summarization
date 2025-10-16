######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
import os
import re
from pathlib import Path


def update_config_type_name(config_file_path: str, new_type_name: str, threshold=0.3, frame_skip_interval=1) -> str:
    """
    Updates a config file by replacing the text between quotes and before semicolon
    in the line starting with "type_name: " and writes to a new config file.

    Args:
        config_file_path (str): Path to the original config file
        new_type_name (str): The new type_name value to set (without quotes or semicolon)

    Returns:
        str: Path to the new config file

    Raises:
        FileNotFoundError: If the original config file doesn't exist
        ValueError: If no line starting with "type_name: " is found
    """

    # Check if the original file exists
    if not os.path.exists(config_file_path):
        raise FileNotFoundError(f"Config file not found: {config_file_path}")

    # Create new config file path in the same directory
    original_path = Path(config_file_path)
    new_config_path = original_path.parent / f"updated_{original_path.name}"

    type_name_found = False

    try:
        with open(config_file_path, 'r', encoding='utf-8') as original_file:
            with open(new_config_path, 'w', encoding='utf-8') as new_file:
                for line in original_file:
                    # Check if the line starts with "type_name: " (with optional whitespace)
                    if line.strip().startswith("type_name:"):
                        # Use regex to find the pattern: type_name: "text;number"
                        # and replace only the "text" part
                        pattern = r'(type_name:\s*")([^"]*)(;.*)'
                        match = re.search(pattern, line)

                        if match:
                            # Extract the parts: prefix, new text, suffix
                            prefix = match.group(1)  # type_name: "
                            suffix = match.group(3)  # ;number"
                            #print(f"prefix: {prefix}, suffix: {suffix}")
                            # Create new line with updated text
                            new_line = f'\t{prefix}{new_type_name};{threshold}"\n'
                            new_file.write(new_line)
                            type_name_found = True
                            print(f"new_line: {new_line}")
                            continue
                        else:
                            # Fallback: if regex doesn't match, replace the entire line
                            new_line = f'\ttype_name: "{new_type_name};{threshold}"\n'
                            new_file.write(new_line)
                            type_name_found = True
                            continue
                    if line.strip().startswith("interval:"):
                        new_line = f'\tinterval: {frame_skip_interval}\n'
                        print(f"new_line: {new_line}")
                        new_file.write(new_line)
                        continue
                        # Write the line unchanged
                    new_file.write(line)

        if not type_name_found:
            raise ValueError("No line starting with 'type_name: ' found in the config file")

        return str(new_config_path)

    except Exception as e:
        # Clean up the new file if there was an error
        if os.path.exists(new_config_path):
            os.remove(new_config_path)
        raise e


# Example usage
if __name__ == "__main__":
    # Example: Update the gdinoconfig.txt file
    config_file = "./gdinoconfig.txt"
    new_type_name = "bus . van ."
    threshold = 0.5


    def detection_classes_to_prompt(detection_classes):
        prompt = ""
        for i, class_name in enumerate(detection_classes):
            if i == len(detection_classes) - 1:
                prompt += f"{class_name} ."
            else:
                prompt += f"{class_name} . "
        return prompt

    detection_classes = ["bus", "van"]
    new_type_name = detection_classes_to_prompt(detection_classes)
    print(f"new_type_name: {new_type_name}")

    try:
        new_config_path = update_config_type_name(config_file, new_type_name)
        print(f"Updated config saved to: {new_config_path}")

        # Show the updated line
        with open(new_config_path, 'r') as f:
            for line in f:
                if line.strip().startswith("type_name:"):
                    print(f"Updated type_name: {line.strip()}")
                    break

    except Exception as e:
        print(f"Error: {e}")
