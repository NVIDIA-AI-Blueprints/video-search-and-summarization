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
Script to remove vulnerable libexpat libraries from Docker image.
Designed to run in distroless images without shell.
"""

import glob
import os
import shutil
import sys


def remove_path(path):
    """Remove a file or directory, handling errors gracefully."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"✓ Removed directory: {path}")
            return True
        elif os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
            print(f"✓ Removed file: {path}")
            return True
        else:
            print(f"⚠ Path does not exist or is not a file/directory: {path}", file=sys.stderr)
            return False
    except PermissionError as e:
        print(f"✗ Permission denied: {path}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"✗ Could not remove {path}: {e}", file=sys.stderr)
        return False


def find_all_expat_files():
    """Recursively find all libexpat files in common locations."""
    search_paths = [
        "/usr/lib",
        "/var/lib/dpkg",
        "/usr/share/doc",
        "/usr/share/doc-base",
    ]

    found = []
    for base_path in search_paths:
        if not os.path.exists(base_path):
            continue
        for root, dirs, files in os.walk(base_path):
            for item in dirs + files:
                # Only look for expat files (NOT sqlite)
                if "expat" in item.lower():
                    full_path = os.path.join(root, item)
                    found.append(full_path)
    return found


def main():
    """Remove vulnerable libraries and their metadata."""
    print("=" * 70)
    print("VULNERABILITY CLEANUP SCRIPT")
    print("=" * 70)
    print("    Only removing libexpat files")

    # First, do a comprehensive search to see what exists
    print("\n🔍 Scanning for libexpat files...")
    all_expat = find_all_expat_files()
    if all_expat:
        print(f"Found {len(all_expat)} libexpat-related files:")
        for path in sorted(all_expat):
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            file_type = "DIR" if os.path.isdir(path) else "FILE"
            print(f"  [{file_type}] {path} ({size} bytes)")
    else:
        print("  No libexpat files found")

    # Patterns for files/directories to remove
    # NOTE: Removed libsqlite3 patterns - application needs it!
    patterns = [
        # Expat libraries (both libexpat and libexpatw variants)
        "/usr/lib/*/libexpat.so*",
        "/usr/lib/*/libexpatw.so*",
        "/usr/lib/*/*/libexpat.so*",
        "/usr/lib/*/*/libexpatw.so*",
        # Expat dpkg metadata
        "/var/lib/dpkg/status.d/libexpat*",
        "/var/lib/dpkg/info/libexpat*",
        # Expat documentation
        "/usr/share/doc/libexpat*",
        "/usr/share/doc-base/libexpat*",
    ]

    removed_count = 0
    failed_count = 0

    print(f"\n🧹 Attempting to remove files using {len(patterns)} patterns...")

    for pattern in patterns:
        print(f"\n  Pattern: {pattern}")
        matches = glob.glob(pattern, recursive=False)
        if matches:
            print(f"    → Found {len(matches)} matches")
            for match in matches:
                if remove_path(match):
                    removed_count += 1
                else:
                    failed_count += 1
        else:
            print("    → No matches")

    # Verify cleanup
    print("\n🔍 Verifying cleanup...")
    remaining = find_all_expat_files()

    print(f"\n{'='*70}")
    print("CLEANUP SUMMARY")
    print(f"{'='*70}")
    print(f"✓ Successfully removed: {removed_count} libexpat items")
    if failed_count > 0:
        print(f"✗ Failed to remove: {failed_count} items")
    if remaining:
        print(f"⚠  Still remaining: {len(remaining)} libexpat-related items")
        for path in sorted(remaining):
            print(f"    {path}")
        print("\n⚠️  WARNING: libexpat cleanup incomplete!")
        return 1
    else:
        print("✓ All libexpat files successfully removed")
        print(f"{'='*70}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
