#!/usr/bin/env python3

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
Environment variable substitution script for config files.
Works with distroless Python images using tmpfs mount.

Usage:
    python env-substitute.py --source <path> --output <path> -- <command> [args...]
"""

import os
import sys
import re
import argparse


def substitute_env_vars(content):
    """
    Replace ${VAR_NAME} with environment variable values.
    """
    def replacer(match):
        var_name = match.group(1)
        value = os.environ.get(var_name, '')
        if not value:
            print(f"Warning: Environment variable {var_name} is not set or empty", file=sys.stderr)
        return value
    
    # Match ${VAR_NAME} pattern
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}'
    return re.sub(pattern, replacer, content)


def main():
    # Split arguments at '--' separator
    if '--' in sys.argv:
        separator_idx = sys.argv.index('--')
        entrypoint_args = sys.argv[1:separator_idx]
        command_args = sys.argv[separator_idx + 1:]
    else:
        print("Error: Missing '--' separator between entrypoint args and command", file=sys.stderr)
        print("Usage: env-substitute.py --source <path> --output <path> -- <command> [args...]", file=sys.stderr)
        sys.exit(1)
    
    # Parse named arguments for the entrypoint
    parser = argparse.ArgumentParser(
        description='Process config file with environment variable substitution'
    )
    parser.add_argument(
        '--source',
        required=True,
        help='Source config file path (with ${VAR} placeholders)'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Output config file path (with substituted values)'
    )
    
    try:
        args = parser.parse_args(entrypoint_args)
    except SystemExit as e:
        sys.exit(e.code)
    
    if not command_args:
        print("Error: No command provided after '--'", file=sys.stderr)
        sys.exit(1)
    
    print(f"Substituting environment variables in config...")
    print(f"  Source: {args.source}")
    print(f"  Output: {args.output}")
    
    # Read the source config
    try:
        with open(args.source, 'r') as f:
            config_content = f.read()
    except FileNotFoundError:
        print(f"Error: Source config file not found: {args.source}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading source config: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Substitute environment variables
    processed_content = substitute_env_vars(config_content)
    
    # Write processed config
    try:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(processed_content)
        print(f"Processed config written successfully")
    except Exception as e:
        print(f"Error writing processed config: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Execute the original command
    print(f"Executing: {' '.join(command_args)}")
    os.execvp(command_args[0], command_args)


if __name__ == '__main__':
    main()

