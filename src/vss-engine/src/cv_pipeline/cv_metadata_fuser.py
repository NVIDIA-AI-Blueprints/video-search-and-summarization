######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import json
import os
import shutil
import subprocess
import sys

import yaml

sys.path.append(os.path.dirname(__file__) + "/..")
from utils import get_json_file_name  # noqa: E402


class CVMetadataFuser:
    def __init__(self) -> None:
        return

    def fuse_chunks(
        self,
        fusion_config: str,
        request_id,
        max_chunk_length,
        chunk_start_frame_nums,
        max_object_id,
    ):
        if len(chunk_start_frame_nums) == 1:
            # copy file get_json_file_name(request_id, 0) to get_json_file_name(request_id, "fused")
            shutil.copy(get_json_file_name(request_id, 0), get_json_file_name(request_id, "fused"))
            return
        if max_object_id < 0:
            print("Warning : max_object_id is less than 0. Assuming no objects in input chunks")
            return
        # Create a temporary fusion config file for the request
        # Need to update "maxTargetsPerStream" in the fusion config file
        # based on the max numer of objects present in input chunks
        # maxTargetsPerStream = (max_object_id + 1) * num_chunks + some extra buffer
        with open(fusion_config) as f:
            input_fusion_config = yaml.safe_load(f)
        if max_object_id >= 0:
            num_chunks = len(chunk_start_frame_nums)
            max_targets_per_stream = int(max((max_object_id + 1) * num_chunks * 1.05, 100))
            if (
                input_fusion_config.get("TargetManagement", {}).get("maxTargetsPerStream", {})
                is not None
            ):
                input_fusion_config["TargetManagement"][
                    "maxTargetsPerStream"
                ] = max_targets_per_stream
        fusion_config_file_name = str(request_id) + "_fusion_config.yaml"
        with open(fusion_config_file_name, "w") as f:
            yaml.dump(input_fusion_config, f)

        # Write the meatada fusion config yaml file
        yaml_file_name = str(request_id) + "_config.yaml"
        config = {}
        config["fusionConfigFile"] = fusion_config_file_name
        config["maxInputChunkLength"] = max_chunk_length
        fused_file_name = get_json_file_name(request_id, "fused")
        config["fusedOutput"] = fused_file_name
        config["input"] = []
        # num_chunks = len(chunk_start_frame_nums)
        for idx, chunk_start_frame_num in chunk_start_frame_nums.items():
            chunk_file_name = get_json_file_name(request_id, idx)
            chunk_input = {}
            chunk_input["startFrameNum"] = chunk_start_frame_num
            chunk_input["metadata"] = chunk_file_name
            config["input"].append(chunk_input)
        # Write the list of objects to a YAML file
        with open(yaml_file_name, "w") as yaml_file:
            yaml.dump(config, yaml_file)

        # Call the metadata fusion executable
        executable_path = "metadata_fusion_app"
        args = [yaml_file_name]
        try:
            result = subprocess.run(
                [executable_path] + args, capture_output=True, text=True, check=True
            )
            print("Metadata fusion output:")
            print(result.stdout)  # Print standard output
            if result.returncode:
                raise Exception("Failed to run metadata fusion!!")
        except subprocess.CalledProcessError as e:
            print(f"An error occurred: {e}")
            print(e.stderr)  # Print standard error output if there is an error

    @staticmethod
    # Performs two tasks :
    # 1. Finds the max chunk length
    # 2. Finds the chunk start frame numbers
    def get_chunk_info_for_fusion(pts_to_frame_num_per_chunk):
        max_chunk_length = 0
        chunk_start_frame_nums = {}
        chunk_offset = 0
        prev_pts_to_frame_num = []
        prev_end_frame_num = 0
        for i in range(len(pts_to_frame_num_per_chunk)):
            pts_to_frame_num = pts_to_frame_num_per_chunk[i]
            if len(pts_to_frame_num) == 0:
                print(f"Warning : pts_to_frame_num is empty for chunk {i}")
                continue
            start_frame_num = list(pts_to_frame_num.values())[0]
            end_frame_num = list(pts_to_frame_num.values())[-1]
            num_frames = end_frame_num - start_frame_num + 1
            max_chunk_length = max(max_chunk_length, num_frames)
            if i != 0:
                start_pts = list(pts_to_frame_num.keys())[0]
                # check for start timestamp of current chunk in previous chunk
                # if it exists, means there is an overlap.
                # So, we find the corresponding frame and add it as an offset
                # if it doesn't exist, there is not overlap
                if start_pts in prev_pts_to_frame_num:
                    chunk_offset = chunk_offset + prev_pts_to_frame_num[start_pts]
                else:
                    chunk_offset = chunk_offset + prev_end_frame_num + 1
                start_frame_num = start_frame_num + chunk_offset
            chunk_start_frame_nums[i] = start_frame_num
            prev_pts_to_frame_num = pts_to_frame_num
            prev_end_frame_num = end_frame_num
        return (max_chunk_length, chunk_start_frame_nums)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Metadata fusion")

    parser.add_argument(
        "request_id", type=str, help="Request ID for which metadata fusion is to be performed"
    )

    parser.add_argument(
        "--num-chunks",
        default=1,
        type=int,
        help="Number of chunks to be fused",
    )

    parser.add_argument(
        "--fusion-config",
        default="config/MOT_EVAL_config_fusion.yml",
        type=str,
        help="Metadata fusion config file",
    )

    args = parser.parse_args()

    # Now that we have request_id & num_chunks, get max_chunk_length & chunk_start_frame_numbers
    pts_to_frame_num_per_chunk = []
    max_object_id = -1
    for i in range(args.num_chunks):
        chunk_file_name = get_json_file_name(args.request_id, i)
        if os.path.isfile(chunk_file_name):
            with open(chunk_file_name, "r") as chunk_file:
                data = json.load(chunk_file)
            pts_to_frame_num = {obj["timestamp"]: obj["frameNo"] for obj in data}
            pts_to_frame_num_per_chunk.append(pts_to_frame_num)
            max_id = max((obj["id"] for frame in data for obj in frame["objects"]), default=-1)
            max_object_id = max(max_object_id, max_id)
        else:
            raise FileNotFoundError(
                f"The file '{chunk_file_name}' does not exist or is not a file."
            )

    (max_chunk_length, chunk_start_frame_nums) = CVMetadataFuser.get_chunk_info_for_fusion(
        pts_to_frame_num_per_chunk
    )

    metadata_fuser = CVMetadataFuser()

    print(max_chunk_length)
    print(chunk_start_frame_nums)
    if args.num_chunks != len(chunk_start_frame_nums):
        print(
            f"Error : num_chunks {args.num_chunks} != len(chunk_start_frame_nums)  \
                {len(chunk_start_frame_nums)}"
        )
        sys.exit(1)

    metadata_fuser.fuse_chunks(
        fusion_config=args.fusion_config,
        request_id=args.request_id,
        max_chunk_length=max_chunk_length,
        chunk_start_frame_nums=chunk_start_frame_nums,
        max_object_id=max_object_id,
    )

    print("Metadata fusion completed!!")
