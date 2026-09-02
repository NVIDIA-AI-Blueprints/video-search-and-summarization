#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


from flask import Flask, jsonify, request, send_file
from PIL import Image
from io import BytesIO
import os
from utils.carla_utils import quick_preview_frame

"""

API Server
====================================================================

Description:
------------
API Server to Generate Image frames for autocalibration


Inputs:
-------
- Camera specification file (YAML): Contains camera positions, orientations, FOV, and resolution.

Outputs:
--------
- image frame (.png): 
"""

app = Flask(__name__)

MANDATORY_FIELDS_DEFINITION = {
    "id": (str, None, None, "String"),
    "position": (list, 3, float, "List of 3 numbers (X, Y, Z)"),
    "orientation": (list, 3, float, "List of 3 numbers (Pitch, Yaw, Roll)"),
    "fov": ((int, float), None, None, "Number (int or float)"),
    "fps": ((int, float), None, None, "Number (int or float)"),  # Added fps
    "resolution": (list, 2, float, "List of 2 numbers (Width, Height)"),
    "label": ((int, float), None, None, "Number (int or float)"),
    "geo_position": (bool, None, None, "Boolean (True/False)"),
    "map_name": (str, None, None, "String"),
}


def validate_camera_specs(specs_list):
    """
    STRICT VALIDATION for POST request: Checks presence, type, and structure
    of ALL mandatory fields for every camera object.
    """
    if not isinstance(specs_list, list):
        return False, "Input must be a list of camera specifications."

    for i, spec in enumerate(specs_list):
        if not isinstance(spec, dict):
            return False, f"Camera spec at index {i} must be a dictionary."

        for field, (
            expected_type,
            expected_length,
            _,
            type_desc,
        ) in MANDATORY_FIELDS_DEFINITION.items():

            # 1. Check Presence
            if field not in spec:
                return (
                    False,
                    f"Camera spec at index {i} (ID: {spec.get('id', 'N/A')}) is missing the mandatory field: '{field}'.",
                )

            value = spec[field]

            # 2. Check Type
            if not isinstance(value, expected_type):
                current_type = type(value).__name__
                return (
                    False,
                    f"Camera spec at index {i} (ID: {spec.get('id', 'N/A')}) field '{field}' must be a {type_desc}, but found '{current_type}'.",
                )

            # 3. Check List Length/Structure
            if expected_length is not None and isinstance(value, list):
                if len(value) != expected_length:
                    return (
                        False,
                        f"Camera spec at index {i} (ID: {spec['id']}) field '{field}' must have exactly {expected_length} elements.",
                    )

                # Check list elements are numbers
                if not all(isinstance(item, (int, float)) for item in value):
                    return (
                        False,
                        f"Camera spec at index {i} (ID: {spec['id']}) field '{field}' must contain only numbers.",
                    )

    return True, "All camera specifications are valid."


@app.route("/api/camera_specs", methods=["GET"])
def get_camera_specs():
    """
    Handles GET requests: Returns image frame corresponding to the cam specs
    """

    query_params = request.args.to_dict()
    validated_args = {}

    missing_fields = [k for k in MANDATORY_FIELDS_DEFINITION if k not in query_params]
    if missing_fields:
        return (
            jsonify(
                {
                    "error": "Missing mandatory query arguments.",
                    "details": f"The following fields are required for this GET query: {', '.join(missing_fields)}. All fields must be provided for lookup.",
                }
            ),
            400,
        )

    for key, (
        expected_type,
        expected_len,
        elem_type,
        type_desc,
    ) in MANDATORY_FIELDS_DEFINITION.items():
        value = query_params[key]

        # A. Handle List arguments (position, orientation, resolution)
        if expected_type == list:
            parts = value.split(",")
            if len(parts) != expected_len:
                return (
                    jsonify(
                        {
                            "error": f"Invalid format for '{key}'.",
                            "details": f"Must be a comma-separated string with exactly {expected_len} numeric values.",
                        }
                    ),
                    400,
                )

            try:
                # Convert to list of numbers
                parsed_value = [elem_type(p.strip()) for p in parts]
                validated_args[key] = parsed_value
            except ValueError:
                return (
                    jsonify(
                        {
                            "error": f"Invalid format for '{key}'.",
                            "details": f"All elements in '{key}' must be valid numbers (e.g., 10.0,20.0,30.0).",
                        }
                    ),
                    400,
                )

        # B. Handle Boolean argument (geo_position)
        elif expected_type == bool:
            if value.lower() in ("true", "1"):
                validated_args[key] = True
            elif value.lower() in ("false", "0"):
                validated_args[key] = False
            else:
                return (
                    jsonify(
                        {
                            "error": f"Invalid value for '{key}'.",
                            "details": "Value must be 'true' or 'false'.",
                        }
                    ),
                    400,
                )

        # C. Handle Numeric arguments (fov, fps, label)
        elif expected_type in ((int, float), int, float):
            try:
                # Convert to float for filtering comparison
                validated_args[key] = float(value)
            except ValueError:
                return (
                    jsonify(
                        {
                            "error": f"Invalid value for '{key}'.",
                            "details": "Value must be a valid number.",
                        }
                    ),
                    400,
                )

        # D. Handle String arguments (id, map_name)
        elif expected_type == str:
            validated_args[key] = value

    # call carla
    PORT = os.getenv("CARLA_PORT", "2000")
    HOST = os.getenv("CARLA_HOST", "localhost")
    rgb_image, _ = quick_preview_frame(
        validated_args, HOST, int(PORT), validated_args["map_name"]
    )
    pil_image = Image.fromarray(rgb_image, "RGB")
    img_io = BytesIO()
    pil_image.save(img_io, "JPEG", quality=70)
    img_io.seek(0)

    return send_file(img_io, mimetype="image/jpeg", as_attachment=False)


if __name__ == "__main__":

    custom_port = int(os.getenv("API_PORT", "7865"))
    app.run(host="0.0.0.0", debug=True, port=custom_port)
