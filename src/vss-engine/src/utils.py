######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""Common utility methods."""

import asyncio
import json
import logging
import os
import re
import subprocess
import textwrap

import gi
import numpy as np
import yaml
from pymediainfo import MediaInfo

# from json_minify import json_minify

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")

from gi.repository import Gst, GstPbutils  # noqa: E402

Gst.init(None)

logger = logging.getLogger(__name__)


class MediaFileInfo:
    is_image = False
    video_codec = ""
    video_duration_nsec = 0
    video_fps = 0.0
    video_resolution = (0, 0)

    @staticmethod
    def _get_info_gst(uri_or_file: str, username="", password=""):
        uri_or_file = str(uri_or_file)
        media_file_info = MediaFileInfo()

        if uri_or_file.startswith("rtsp://") or uri_or_file.startswith("file://"):
            uri = uri_or_file
        else:
            uri = "file://" + os.path.abspath(str(uri_or_file))

        def select_stream(source, idx, caps):
            if "audio" in caps.to_string():
                return False
            return True

        def source_setup(discoverer, source):
            if uri.startswith("rtsp://"):
                source.connect("select-stream", select_stream)
                source.set_property("timeout", 1000000)
                if username and password:
                    source.set_property("user-id", username)
                    source.set_property("user-pw", password)

        discoverer = GstPbutils.Discoverer()
        discoverer.connect("source-setup", source_setup)

        try:
            file_info = discoverer.discover_uri(uri)
        except gi.repository.GLib.GError as e:
            raise Exception("Unsupported file type - " + uri + " Error:" + str(e))
        for stream_info in file_info.get_stream_list():
            if isinstance(stream_info, GstPbutils.DiscovererVideoInfo):
                media_file_info.video_duration_nsec = int(file_info.get_duration())
                media_file_info.video_codec = str(
                    GstPbutils.pb_utils_get_codec_description(stream_info.get_caps())
                )
                media_file_info.video_resolution = (
                    int(stream_info.get_width()),
                    int(stream_info.get_height()),
                )
                media_file_info.video_fps = float(
                    stream_info.get_framerate_num() / stream_info.get_framerate_denom()
                )
                media_file_info.is_image = bool(stream_info.is_image())
                break
        return media_file_info

    @staticmethod
    def _get_info_mediainfo(uri_or_file: str):
        if uri_or_file.startswith("file://"):
            file = uri_or_file[7:]
        else:
            file = uri_or_file

        media_file_info = MediaFileInfo()
        media_info = MediaInfo.parse(file)
        have_image_or_video = False
        for track in media_info.tracks:
            if track.track_type == "Video":
                media_file_info.is_image = False
                media_file_info.video_codec = track.format
                media_file_info.video_duration_nsec = float(track.duration) * 1000000
                media_file_info.video_fps = track.frame_rate
                media_file_info.video_resolution = (track.width, track.height)
                have_image_or_video = True
            if track.track_type == "Image":
                media_file_info.is_image = True
                media_file_info.video_codec = track.format
                media_file_info.video_duration_nsec = 0
                media_file_info.video_fps = 0
                media_file_info.video_resolution = (track.width, track.height)
                have_image_or_video = True

        if not have_image_or_video:
            raise Exception("Unsupported file type - " + file)
        return media_file_info

    @staticmethod
    def get_info(uri_or_file: str, username="", password=""):
        if str(uri_or_file).startswith("rtsp://"):
            return MediaFileInfo._get_info_gst(uri_or_file, username, password)
        else:
            return MediaFileInfo._get_info_mediainfo(str(uri_or_file))

    @staticmethod
    async def get_info_async(uri_or_file: str, username="", password=""):
        return await asyncio.get_event_loop().run_in_executor(
            None, MediaFileInfo.get_info, uri_or_file, username, password
        )


def round_up(s):
    """
    Rounds up a string representation of a number to an integer.

    Example:
    >>> round_up("7.9s")
    8
    """
    # Strip any non-numeric characters from the string
    num_str = re.sub(r"[a-zA-Z]+", "", s)

    # Convert the string to a float and round up to the nearest integer
    num = float(num_str)
    return -(-num // 1)  # equivalent to math.ceil(num) in Python 3.x


def get_avg_time_per_chunk(GPU_in_use, Model_ID, yaml_file_path):
    """
    Returns the average time per query for a given GPU and Model ID
    from a VIA_runtime_stats YAML file.

    Args:
        GPU_in_use (str): The GPU in use (e.g. A100, H100)
        Model_ID (str): The Model ID (e.g. VILA)
        yaml_file_path (str): The path to the VIA_runtime_stats YAML file

    Returns:
        str: The average time per chunk (e.g. 2.5s, 1.8s)
    """

    def is_subset_s1_in_s2(string1, string2):
        # Returns True if string1 is a subset of string2, ignoring case
        pattern = re.compile(re.escape(string1), re.IGNORECASE)
        return bool(pattern.search(string2))

    def is_subset(string1, string2):
        return is_subset_s1_in_s2(string1, string2) or is_subset_s1_in_s2(string2, string1)

    with open(yaml_file_path, "r") as f:
        yaml_data = yaml.safe_load(f)

    max_atpc = 0.0
    max_atpc_as_is = "0"

    for entry in yaml_data["VIA_runtime_stats"]:
        if round_up(entry["average_time_per_chunk"]) > max_atpc:
            max_atpc = round_up(entry["average_time_per_chunk"])
            max_atpc_as_is = entry["average_time_per_chunk"]
        if is_subset(GPU_in_use, entry["GPU_in_use"]) and is_subset(Model_ID, entry["Model_ID"]):
            return entry["average_time_per_chunk"]

    # If no matching entry is found, return max of all
    return max_atpc_as_is


def get_available_gpus():
    """
    Returns an array of available NVIDIA GPUs with their names and memory sizes.

    Example output:
    [
        {"name": "GeForce RTX 3080", "memory": "12288 MiB"},
        {"name": "Quadro RTX 4000", "memory": "16384 MiB"}
    ]
    """
    try:
        # Run nvidia-smi command and capture output
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
        )

        # Split output into lines
        lines = output.decode("utf-8").strip().split("\n")

        # Initialize empty list to store GPU info
        gpus = []

        # Iterate over lines and extract GPU info
        for line in lines:
            cols = line.split(",")
            gpu_name = cols[0].strip()
            gpu_memory = cols[1].strip()
            gpus.append({"name": gpu_name, "memory": gpu_memory})

        return gpus

    except subprocess.CalledProcessError as e:
        print(f"Error running nvidia-smi: {e}")
        return []


# Convert the matrix to bit strings
def matrix_to_bit_strings(matrix):
    return ["".join(map(str, row)) for row in matrix]


# Run-length encoding function
def rle_encode(matrix):
    encoded = []
    for row in matrix:
        row_encoded = []
        current_value = row[0]
        count = 1
        for i in range(1, len(row)):
            if row[i] == current_value:
                count += 1
            else:
                row_encoded.append((int(current_value), count))
                current_value = row[i]
                count = 1
        row_encoded.append((int(current_value), count))
        encoded.append(row_encoded)
    return encoded


def find_object_with_key_value(json_array, target_key, target_value):
    # Loop through each object in the JSON array (list of dicts)
    for obj in json_array:
        if isinstance(obj, dict):
            # Check if the key-value pair exists in the current object
            if obj.get(target_key) == target_value:
                return obj  # Return the entire object if a match is found
    return None  # Return None if no match is found


class JsonCVMetadata:
    def __init__(self, request_id=-1, chunkIdx=-1):
        self.data = []  # Initialize an empty list to store entries
        self._request_id = request_id
        self._chunkIdx = chunkIdx
        # if request_id is provided, create a directory to save the masks of that request_id
        if self._request_id != -1 and self._chunkIdx != -1:
            self.mask_dir = "/tmp/via/masks/" + self._request_id
            # Check if the path exists
            if not os.path.exists(self.mask_dir):
                try:
                    os.makedirs(self.mask_dir)
                    print(f"Directory '{self.mask_dir}' created successfully.")
                except Exception as e:
                    print(f"An error occurred: {e}")

    def write_frame(self, frame_meta):
        import pyds

        # Frame level metadata
        frame = {}
        # frame["requestId"] = self._request_id
        # frame["version"] = "abc"
        frame["frameNo"] = frame_meta.frame_num
        # frame["fileName"] = "abc"
        frame["timestamp"] = frame_meta.buf_pts
        # frame["sensorId"] = frame_meta.source_id
        # frame["model"] = "VIA_CV"
        frame["frameWidth"] = frame_meta.source_frame_width
        frame["frameHeight"] = frame_meta.source_frame_height
        # frame["grounding"] = {}
        # Object level metadata
        objects = []
        l_obj = frame_meta.obj_meta_list
        while l_obj is not None:
            obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            object = {}
            object["id"] = obj_meta.object_id
            bbox = {}
            bbox["lX"] = round(obj_meta.rect_params.left, 2)
            bbox["tY"] = round(obj_meta.rect_params.top, 2)
            bbox["rX"] = round((obj_meta.rect_params.left + obj_meta.rect_params.width), 2)
            bbox["bY"] = round((obj_meta.rect_params.top + obj_meta.rect_params.height), 2)
            object["bbox"] = bbox
            object["type"] = obj_meta.obj_label
            object["conf"] = round(obj_meta.confidence, 2)
            # misc metadata for each object
            misc = []
            misc_object = {}
            misc_object["chId"] = -1
            misc_object["bbox"] = bbox
            misc_object["conf"] = round(obj_meta.confidence, 2)
            # mask data for each object
            segmentation = {}
            mask_params = pyds.NvOSD_MaskParams.cast(obj_meta.mask_params)
            if mask_params.data is not None:
                mask = obj_meta.mask_params.get_mask_array().reshape(
                    obj_meta.mask_params.height, obj_meta.mask_params.width
                )
                mask_uint8 = mask.astype(np.uint8)
                # write the mask to a file named ch_<chunk idx>_frm_<frame num>_obj_<obj id>.bin
                output_maks_file_name = (
                    "ch_"
                    + str(self._chunkIdx)
                    + "_frm_"
                    + str(frame_meta.frame_num)
                    + "_obj_"
                    + str(obj_meta.object_id)
                    + ".bin"
                )
                output_maks_file_path = self.mask_dir + "/" + output_maks_file_name
                with open(output_maks_file_path, "wb") as f:
                    # Write dimensions as 4-byte integers (little-endian)
                    f.write(np.array(mask_uint8.shape, dtype=np.int32).tobytes())
                    mask_uint8.tofile(f)
                    segmentation["mask"] = output_maks_file_path
                # bit_strings = matrix_to_bit_strings(mask_uint8)
                # encoded_matrix = rle_encode(mask_uint8)
                # segmentation["mask"] = encoded_matrix #bit_strings #mask_uint8.tolist()
            misc_object["seg"] = segmentation
            misc.append(misc_object)
            object["misc"] = misc
            objects.append(object)
            l_obj = l_obj.next
        frame["objects"] = objects
        self.data.append(frame)

    def write_past_frame_meta(self, batch_meta):
        import pyds

        l_user = batch_meta.batch_user_meta_list
        pastFrameObjList = {}
        while l_user is not None:
            try:
                user_meta = pyds.NvDsUserMeta.cast(l_user.data)
            except StopIteration:
                break
            if (
                user_meta
                and user_meta.base_meta.meta_type == pyds.NvDsMetaType.NVDS_TRACKER_PAST_FRAME_META
            ):
                try:
                    pPastFrameObjBatch = pyds.NvDsTargetMiscDataBatch.cast(
                        user_meta.user_meta_data
                    )  # See NvDsTargetMiscDataBatch for details
                except StopIteration:
                    break
                for trackobj in pyds.NvDsTargetMiscDataBatch.list(
                    pPastFrameObjBatch
                ):  # Iterate through list of NvDsTargetMiscDataStream objects
                    for pastframeobj in pyds.NvDsTargetMiscDataStream.list(
                        trackobj
                    ):  # Iterate through list of NvDsFrameObjList objects
                        # numobj = pastframeobj.numObj
                        uniqueId = pastframeobj.uniqueId
                        # classId = pastframeobj.classId
                        objLabel = pastframeobj.objLabel
                        for objlist in pyds.NvDsTargetMiscDataObject.list(
                            pastframeobj
                        ):  # Iterate through list of NvDsFrameObj objects
                            bbox = {}
                            bbox["lX"] = round(objlist.tBbox.left, 2)
                            bbox["tY"] = round(objlist.tBbox.top, 2)
                            bbox["rX"] = round((objlist.tBbox.left + objlist.tBbox.width), 2)
                            bbox["bY"] = round((objlist.tBbox.top + objlist.tBbox.height), 2)
                            bbox["conf"] = round(objlist.confidence, 2)
                            bbox["id"] = uniqueId
                            bbox["type"] = objLabel
                            frameNum = objlist.frameNum
                            if frameNum not in pastFrameObjList:
                                pastFrameObjList[frameNum] = []
                            pastFrameObjList[frameNum].append(bbox)
            try:
                l_user = l_user.next
            except StopIteration:
                break

        # Now that pastFrameObjList is filled, add it to json metadata
        for frameNum, pastFrameObjects in pastFrameObjList.items():
            frameObject = find_object_with_key_value(self.data, "frameNo", frameNum)
            if frameObject:
                for pastObject in pastFrameObjects:
                    object = {}
                    object["id"] = pastObject["id"]
                    bbox = {}
                    bbox["lX"] = pastObject["lX"]
                    bbox["tY"] = pastObject["tY"]
                    bbox["rX"] = pastObject["rX"]
                    bbox["bY"] = pastObject["bY"]
                    object["bbox"] = bbox
                    object["type"] = pastObject["type"]
                    object["conf"] = pastObject["conf"]
                    # misc metadata for each object
                    misc = []
                    misc_object = {}
                    misc_object["chId"] = -1
                    misc_object["bbox"] = bbox
                    misc_object["conf"] = pastObject["conf"]
                    misc.append(misc_object)
                    object["misc"] = misc
                    frameObject["objects"].append(object)
            else:
                print(
                    f"write_past_frame_meta:Couldn't find json object with frame number={frameNum}"
                )
                print("Ignoring the data")

    def write_json_file(self, filename: str):
        with open(filename, "w") as file:
            json.dump(self.data, file, separators=(",", ":"))
        #    json.dump(self.data, file, indent=4)
        # json_string = json.dumps(self.data)
        # minified_json = json_minify(json_string)
        # with open(filename, 'w') as file:
        #    file.write(minified_json)

    def get_pts_to_frame_num_map(self):
        pts_to_frame_num_map = {obj["timestamp"]: obj["frameNo"] for obj in self.data}
        # print(pts_to_frame_num_map)
        return pts_to_frame_num_map

    def get_max_object_id(self):
        max_id = max((obj["id"] for frame in self.data for obj in frame["objects"]), default=-1)
        return max_id

    def read_json_file(self, filename: str):
        if filename:
            with open(filename, "r") as f:
                self.data = json.load(f)
            # Once the data is loaded create pts : array index map
            self.pts_to_index_map = {frame["timestamp"]: idx for idx, frame in enumerate(self.data)}
            # Extract all object "type"s i.e. all object labels
            obj_labels = {obj["type"] for frame in self.data for obj in frame["objects"]}
            self.obj_labels_list = sorted(obj_labels)
            print(self.obj_labels_list)

    def get_frame_cv_meta(self, timestamp):
        frame_json_meta = None
        idx = self.pts_to_index_map.get(timestamp)
        if idx is not None:
            frame_json_meta = self.data[idx]
        else:
            # Find closest timestamp if exact match not found
            timestamps = list(self.pts_to_index_map.keys())
            closest_ts = min(timestamps, key=lambda x: abs(x - timestamp))
            # Check if closest timestamp is within 1% of requested timestamp
            if abs(closest_ts - timestamp) <= 0.01 * timestamp:
                idx = self.pts_to_index_map[closest_ts]
                frame_json_meta = self.data[idx]
                print(
                    f"get_frame_cv_meta() : Exact timestamp {timestamp} not found, \
                        using closest timestamp {closest_ts}"
                )
            else:
                print(
                    f"get_frame_cv_meta() : Json metadata doesn't contain \
                        a frame with timestamp = {timestamp}"
                )
        return frame_json_meta

    def get_obj_labels_list(self):
        return self.obj_labels_list

    def get_cv_meta(self):
        return self.data


def get_json_file_name(request_id, chunk_idx):
    filename = str(request_id) + "_" + str(chunk_idx) + ".json"
    return filename


def process_highlight_request(messages):
    # Extract scenario from the request if present
    scenarios = None
    message_lower = ""
    if messages is not None:
        message_lower = messages.lower()

    if message_lower != "" and message_lower != "generate video highlight":
        # Split the message by dots and clean up each word
        scenarios = [word.strip() for word in message_lower.split(",") if word.strip()]

    # Define the base highlight_query prompt template
    highlight_query = textwrap.dedent(
        """
        Analyze the video content and generate whole video highlights.

        IMPORTANT:
        - If analyzing a specific scenario, focus ONLY on timestamps and
          segments containing that scenario.
        - If no specific scenario is mentioned, provide a comprehensive overview
          of key moments.
        - If no matching scenarios are found, return ONLY the string
          "No matching scenarios found"

        If matching scenarios ARE found, Generate the response in the following
        JSON format:
        {
            "type": "highlight",
            "highlightResponse": {
                "timestamps": [<time_points>],
                "marker_labels": [<short_descriptive_titles>],
                "start_times": [<section_start_times>],
                "end_times": [<section_end_times>],
                "descriptions": [<detailed_section_summaries>]
            }
        }

        Guidelines when scenarios are found:
        - Each timestamp marks when the requested scenario or significant event
          occurs and timestamps should be sorted in increasing order.
        - Marker_labels should clearly indicate what happens
        - Start_times and end_times should capture the full duration of each
          event
        - Descriptions should detail what happens in each highlighted section
        - All time values must be in seconds
        - For scenario-specific requests:
          * Only include segments that match the requested scenario
          * Label and describe the specific events clearly
        - For general highlight requests:
          * Include diverse important moments
          * Cover the key events throughout the video
        - Maintain chronological order in all arrays
        IMPORTANT: Return either:
        1. The string "No matching scenarios found" if no matches exist
        2. The JSON object if matches are found
        Do not mix these formats or add additional text.
        """
    )

    # Add scenario if specified
    if scenarios:
        highlight_query += f'\nREQUESTED SCENARIO: "{scenarios}"\n'

    return highlight_query


class StreamSettingsCache:
    def __init__(
        self,
        stream_settings_fp: str = "/tmp/.stream_settings_cache.json",
        logger: logging.Logger = None,
    ):
        self.stream_settings_fp = stream_settings_fp
        self.logger = logger

    def update_stream_settings(self, video_id: str, stream_settings: dict):
        """
        Save/Update stream settings to a json file
        """
        try:
            existing_settings = self.load_stream_settings()
            # Update with new settings
            existing_settings.update({video_id: stream_settings})

            # Save updated settings
            with open(self.stream_settings_fp, "w") as f:
                json.dump(existing_settings, f, indent=4)
            self.logger.debug(f"Stream settings updated: {self.stream_settings_fp}")
        except Exception as e:
            self.logger.error(f"Failed to save stream settings: {str(e)}")

    def load_stream_settings(self, video_id: str = None):
        """
        Load stream settings from a json file
        """
        if os.path.exists(self.stream_settings_fp):
            with open(self.stream_settings_fp, "r") as f:
                existing_settings = json.load(f)
        else:
            existing_settings = {}

        if video_id:
            existing_stream_settings = existing_settings.get(video_id, {})
            self.logger.debug(f"Stream settings for {video_id}: {existing_stream_settings}")
            return existing_stream_settings
        else:
            self.logger.debug(f"ALL Streams settings: {existing_settings}")
            return existing_settings

    def transform_query(self, query_dict: dict) -> dict:
        """
        Transform the query string into a dictionary
        """
        if not query_dict:
            self.logger.error("Empty Query params!")
            return {}

        # Define the fields we want to keep
        required_fields = {
            "id",
            "model",
            "chunk_duration",
            "summary_duration",
            "temperature",
            "seed",
            "max_tokens",
            "top_p",
            "top_k",
            "stream",
            "enable_chat",
            "enable_chat_history",
            "stream_options",
            "num_frames_per_chunk",
            "vlm_input_width",
            "vlm_input_height",
            "enable_cv_metadata",
            "enable_audio",
            "prompt",
            "caption_summarization_prompt",
            "summary_aggregation_prompt",
            "cv_pipeline_prompt",
            "tools",
        }

        # Extract only the required fields and remove None values
        filtered_dict = {
            k: v for k, v in query_dict.items() if k in required_fields and v is not None
        }

        return filtered_dict
