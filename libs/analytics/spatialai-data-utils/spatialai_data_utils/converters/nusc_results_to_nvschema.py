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

import os
import json
import datetime

from spatialai_data_utils.constants import FPS
from spatialai_data_utils.datasets.scenes import get_scene_info_from_token
from spatialai_data_utils.core.geometry.rotation import euler_from_quaternion


class FloatEncoder(json.JSONEncoder):
    """
    Custom JSON encoder to format float numbers with fixed precision.

    Overrides the default encode method to format floats to 9 decimal places.
    Recursively handles floats within lists and dictionaries.
    """

    def encode(self, obj):
        if isinstance(obj, float):
            return format(obj, ".9f")
        if isinstance(obj, list):
            return "[" + ", ".join(self.encode(e) for e in obj) + "]"
        if isinstance(obj, dict):
            return (
                "{"
                + ", ".join(
                    f"{json.dumps(k)}: {self.encode(v)}" for k, v in obj.items()
                )
                + "}"
            )
        return super().encode(obj)


def convert_sparse4d_to_nvschema(
    json_path, output_path, map_class_names, save_embedding=True
):
    """
    Convert Sparse4D tracking results JSON to NVschema format JSON-lines files.

    Loads Sparse4D results, iterates through frames, calculates timestamps,
    formats each tracked object according to NVschema v4.0 specification
    (including ID, type, confidence, coordinates, 3D bounding box, and optionally
    embedding). Handles scenes split by BEV sensor groups if indicated by '+'
    in the scene name. Writes the output as one JSON object per line to separate
    files for each scene/BEV group in the specified output directory.

    :param json_path: Path to the input Sparse4D results JSON file (must contain tracking info).
    :type json_path: str
    :param output_path: Path to the directory where output NVschema JSON-lines files
                        will be saved (one file per scene/BEV group).
    :type output_path: str
    :param save_embedding: Flag indicating whether to include ReID embeddings
                           (if available as 'reid_embedding' in the input)
                           in the output NVschema. Defaults to True.
    :type save_embedding: bool, optional
    """
    fps = FPS
    base_timestamp = datetime.datetime.now(datetime.timezone.utc)

    print(f"loading results from {json_path} ...")
    with open(json_path, "r") as f:
        results_dict = json.load(f)

    print(f"converting to {output_path} ...")
    # Create a separate output for each BEV group
    bev_group_outputs = {}

    for frame_token, frame_objects in results_dict["results"].items():
        full_scene_name, frame_id = get_scene_info_from_token(frame_token)
        if "+" in full_scene_name:
            scene_name, bev_group_name = full_scene_name.split("+")
        else:
            bev_group_name = "bev-sensor-1"

        if full_scene_name not in bev_group_outputs:
            bev_group_outputs[full_scene_name] = []

        output_data = {
            "version": "4.0",
            "id": str(frame_id),  # Example ID, replace with dynamic logic if needed
            "sensorId": bev_group_name,
        }

        time_difference = datetime.timedelta(seconds=int(frame_id) / fps)

        # Calculate new timestamp
        new_timestamp = base_timestamp + time_difference

        # Format the timestamp with 3 digits for seconds
        formatted_timestamp = new_timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        output_data["timestamp"] = formatted_timestamp
        output_data["objects"] = []

        for obj in frame_objects:
            # obj["size"] is in nuscenes convention [l, w, h];
            # NVSchema spec expects [w, l, h], so swap back.
            size = [obj["size"][1], obj["size"][0], obj["size"][2]]
            bbox_coordinates = (
                obj["translation"]
                + size
                + list(euler_from_quaternion(*obj["rotation"]))
            )

            # Convert all bbox3d coordinates to proper decimal format
            bbox_coordinates = [float(f"{coord:.9f}") for coord in bbox_coordinates]
            if save_embedding and "reid_embedding" in obj:
                embedding = [{"vector": obj["reid_embedding"]}]
            else:
                embedding = [{}]

            obj_data = {
                "id": str(obj["tracking_id"]),
                "type": map_class_names[obj["tracking_name"]],
                "confidence": float(f"{obj['tracking_score']:.9f}"),
                "coordinate": {
                    "x": float(f"{obj['translation'][0]:.9f}"),
                    "y": float(f"{obj['translation'][1]:.9f}"),
                    "z": float(f"{obj['translation'][2]:.9f}"),
                },
                "bbox3d": {
                    "coordinates": bbox_coordinates,
                    "embedding": embedding,
                    "confidence": float(f"{obj['tracking_score']:.9f}"),
                },
            }
            output_data["objects"].append(obj_data)
        bev_group_outputs[full_scene_name].append(output_data)

    for full_scene_name in bev_group_outputs.keys():
        output_path_bev = os.path.join(output_path, f"{full_scene_name}.json")
        os.makedirs(output_path, exist_ok=True)
        with open(output_path_bev, "w") as out:
            for frame_data in bev_group_outputs[full_scene_name]:
                out.write(json.dumps(frame_data, cls=FloatEncoder) + "\n")
        print(f"saved nvschema results to {output_path_bev}.")
