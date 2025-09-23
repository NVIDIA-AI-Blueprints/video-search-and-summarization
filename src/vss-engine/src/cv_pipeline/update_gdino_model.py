######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
import sys

import numpy as np
import onnx
from onnx import numpy_helper


def update_gdino_model(model_path, output_path):
    model = onnx.load(model_path)

    # Check all initializers for the problematic extreme value
    target_value = -3.4028234663852886e38
    min_float16 = np.finfo(np.float16).min
    found_tensors = []

    print(f"Searching for tensors with value: {target_value}")
    print(f"Will replace with minimum float16 value: {min_float16}")
    print("=" * 80)

    for i, tensor in enumerate(model.graph.initializer):
        arr = numpy_helper.to_array(tensor)

        # Check if this tensor contains the problematic value
        if np.any(np.isclose(arr, target_value, rtol=1e-6)):
            found_tensors.append(tensor.name)
            print(f"\nFound tensor #{i}: {tensor.name}")
            print(f"  Shape: {arr.shape}")
            print(f"  Dtype: {arr.dtype}")
            print(
                f"  Contains target value: {np.sum(np.isclose(arr, target_value, rtol=1e-6))} elements"
            )
            print(f"  Min value: {np.min(arr)}")
            print(f"  Max value: {np.max(arr)}")

            # Replace the extreme values
            arr_copy = arr.copy()
            mask = np.isclose(arr_copy, target_value, rtol=1e-6)
            arr_copy[mask] = min_float16

            new_tensor = numpy_helper.from_array(arr_copy, name=tensor.name)
            tensor.CopyFrom(new_tensor)
            print(f"  -> Replaced {np.sum(mask)} values with {min_float16}")

    print("\n" + "=" * 80)
    print(f"Summary: Found {len(found_tensors)} tensors with the problematic value")
    for name in found_tensors:
        print(f"  - {name}")

    # Save the updated model
    onnx.save(model, output_path)


if __name__ == "__main__":
    update_gdino_model(sys.argv[1], sys.argv[2])
