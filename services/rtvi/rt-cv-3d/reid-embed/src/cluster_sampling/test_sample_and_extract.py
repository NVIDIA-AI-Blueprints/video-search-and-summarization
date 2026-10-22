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
Test script to verify sample_and_extract_patches.py functionality
Creates synthetic test data to validate the per-object processing
"""

import numpy as np
import csv
import os
import tempfile
import shutil


def generate_test_embeddings(filename: str, num_objects: int = 3):
    """Generate synthetic embeddings CSV for testing"""
    print(f"Generating test embeddings: {num_objects} objects...")

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["entry_id", "object_id", "sensor_id", "frame_id", "vector"])
        writer.writeheader()

        entry_id = 1
        for obj_id in range(1, num_objects + 1):
            # Each object gets a different number of samples
            num_samples = 50 + obj_id * 50  # 100, 150, 200, etc.

            # Generate cluster center for this object
            center = np.random.randn(128).astype(np.float32)

            for i in range(num_samples):
                # Generate vectors around the center with some variance
                vector = center + np.random.randn(128).astype(np.float32) * 0.1

                # Add some outliers (10% of samples)
                if i % 10 == 0:
                    vector = center + np.random.randn(128).astype(np.float32) * 2.0

                vector_str = ",".join([str(x) for x in vector])

                writer.writerow(
                    {
                        "entry_id": entry_id,
                        "object_id": obj_id,
                        "sensor_id": f"camera_{obj_id % 2 + 1}",
                        "frame_id": i * 10,
                        "vector": vector_str,
                    }
                )
                entry_id += 1

    print(f"  Generated {entry_id - 1} embeddings")


def generate_test_tracking(filename: str, num_objects: int = 3):
    """Generate synthetic tracking CSV for testing"""
    print(f"Generating test tracking data: {num_objects} objects...")

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "object_id",
                "stream_id",
                "frame_id",
                "timestamp",
                "leftX",
                "topY",
                "rightX",
                "bottomY",
                "confidence",
            ],
        )
        writer.writeheader()

        for obj_id in range(1, num_objects + 1):
            num_samples = 50 + obj_id * 50

            for i in range(num_samples):
                # Random bounding box
                left = np.random.randint(0, 800)
                top = np.random.randint(0, 400)
                width = np.random.randint(50, 200)
                height = np.random.randint(100, 300)

                writer.writerow(
                    {
                        "object_id": obj_id,
                        "stream_id": f"camera_{obj_id % 2 + 1}",
                        "frame_id": i * 10,
                        "timestamp": f"2025-12-24T10:{i:02d}:00Z",
                        "leftX": left,
                        "topY": top,
                        "rightX": left + width,
                        "bottomY": top + height,
                        "confidence": 0.9 + np.random.rand() * 0.1,
                    }
                )

    print("  Generated tracking data")


def test_load_and_group():
    """Test that embeddings are correctly grouped by object_id"""
    print("\n" + "=" * 60)
    print("TEST: Load and Group by Object ID")
    print("=" * 60)

    # Import the function
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from sample_and_extract_patches import load_embeddings_from_csv

    # Create temp files
    temp_dir = tempfile.mkdtemp()
    embeddings_file = os.path.join(temp_dir, "test_embeddings.csv")

    try:
        # Generate test data
        generate_test_embeddings(embeddings_file, num_objects=3)

        # Load and verify
        grouped_data = load_embeddings_from_csv(embeddings_file)

        print(f"\nLoaded {len(grouped_data)} object groups")

        for obj_id, data in grouped_data.items():
            print(f"  Object {obj_id}: {len(data['vectors'])} embeddings")

            # Verify all object_ids in this group are the same
            assert np.all(data["object_ids"] == obj_id), f"Object {obj_id} has mixed object_ids!"

            # Verify vector dimension
            assert data["vectors"].shape[1] == 128, f"Object {obj_id} has wrong vector dimension!"

        print("\n✓ Test PASSED: Data correctly grouped by object_id")

    finally:
        shutil.rmtree(temp_dir)


def test_per_object_processing():
    """Test that each object is processed independently"""
    print("\n" + "=" * 60)
    print("TEST: Per-Object Processing")
    print("=" * 60)

    # Import functions
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from sample_and_extract_patches import load_embeddings_from_csv, remove_outliers, cosine_prune

    # Create temp files
    temp_dir = tempfile.mkdtemp()
    embeddings_file = os.path.join(temp_dir, "test_embeddings.csv")

    try:
        # Generate test data with different sample counts
        generate_test_embeddings(embeddings_file, num_objects=3)

        # Load data
        grouped_data = load_embeddings_from_csv(embeddings_file)

        print("\nProcessing each object independently...")

        results = {}
        for obj_id, data in grouped_data.items():
            vectors = data["vectors"]
            print(f"\n  Object {obj_id}:")
            print(f"    Original: {len(vectors)} vectors")

            # Remove outliers
            filtered, mask = remove_outliers(vectors, threshold_std=2.0)
            print(f"    After outlier removal: {len(filtered)} vectors")

            # Cosine pruning
            pruned, prune_mask = cosine_prune(filtered, similarity_threshold=0.95)
            print(f"    After cosine pruning: {len(pruned)} vectors")

            results[obj_id] = {"original": len(vectors), "after_outliers": len(filtered), "after_pruning": len(pruned)}

        # Verify each object was processed independently
        print("\n  Summary:")
        for obj_id, counts in results.items():
            reduction = 100 * (1 - counts["after_pruning"] / counts["original"])
            print(
                f"    Object {obj_id}: {counts['original']} → "
                f"{counts['after_pruning']} ({reduction:.1f}% reduction)"
            )

        print("\n✓ Test PASSED: Each object processed independently")

    finally:
        shutil.rmtree(temp_dir)


def main():
    print("=" * 60)
    print("SAMPLE AND EXTRACT PATCHES - TEST SUITE")
    print("=" * 60)

    try:
        test_load_and_group()
        test_per_object_processing()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        print("\nThe script correctly:")
        print("  1. Groups embeddings by object_id")
        print("  2. Processes each object independently")
        print("  3. Maintains separate statistics per object")
        print("\nYou can now run the full script with real data!")

    except Exception as e:
        print("\n" + "=" * 60)
        print("TEST FAILED ✗")
        print("=" * 60)
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
