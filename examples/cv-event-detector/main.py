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
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from pyservicemaker import Pipeline, Probe, BatchMetadataOperator, osd, postprocessing
from multiprocessing import Process
import sys
import platform
import os
import torch
import torchvision.ops as ops
import math
from typing import List
from simple_config_updater import update_config_type_name
import multiprocessing
from uuid import UUID
import uuid
import time
import re
import signal
import contextlib
import sysconfig
import numpy as np
np.random.seed(1000)

print(sysconfig.get_platform())
architecture = sysconfig.get_platform()

# Global storage for streaminfo
asset_map = {}
# Global storage for pipelines
pipeline_map = {}

PIPELINE_NAME = "deepstream-test1"

STREAM_WIDTH = 1920
STREAM_HEIGHT = 1080
DISABLE_SOM_OVERLAY = (os.environ.get("DISABLE_SOM_OVERLAY", "false") == "true")

USE_GDINO = (os.environ.get("USE_GDINO", "true") == "true")
if USE_GDINO:
    CONFIG_FILE_PATH="./gdinoconfig_grpc.txt"
else:
    CONFIG_FILE_PATH="./nvdsinfer_config.yaml"


#TRACKER_CONFIG_FILE_PATH = (os.environ.get("USE_TRACKER_CONFIG",
# "/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_perf.yml"))
#TRACKER_CONFIG_FILE_PATH ="/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml"
CONFIG_NVDSANALYTICS_FILE_PATH="./config_nvdsanalytics.txt"
TRACKER_CONFIG_FILE_PATH = (os.environ.get("USE_TRACKER_CONFIG", "via_tracker_config_fast.yml"))

# Clip cache time settings (in seconds)
CLIP_CACHE_PRE_EV_TIME = int(os.environ.get("CLIP_CACHE_PRE_EV_TIME", "5"))  # 5 seconds default
CLIP_CACHE_POST_EV_TIME = int(os.environ.get("CLIP_CACHE_POST_EV_TIME", "25"))  # 25 seconds default

# File streaming mode setting
ENABLE_FILE_STREAMING_MODE = (os.environ.get("ENABLE_FILE_STREAMING_MODE", "false") == "true")
print("ENABLE_FILE_STREAMING_MODE=", ENABLE_FILE_STREAMING_MODE)
print("DISABLE_SOM_OVERLAY=", DISABLE_SOM_OVERLAY)

# Security Configuration for File Processing
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB max file size
MAX_LINE_COUNT = 50000  # Maximum number of lines to process
MAX_LINE_LENGTH = 1024  # Maximum length per line
FILE_OPERATION_TIMEOUT = 30  # Timeout for file operations in seconds

class FileProcessingError(Exception):
    """Custom exception for file processing security violations"""
    pass

@contextlib.contextmanager
def timeout_context(seconds):
    """Context manager for timing out operations"""
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Operation timed out after {seconds} seconds")

    # Set the signal handler
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)

    try:
        yield
    finally:
        # Reset the alarm and restore the old handler
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

def validate_file_for_processing(file_path: str) -> None:
    """
    Validate file before processing to prevent DoS attacks

    Args:
        file_path: Path to the file to validate

    Raises:
        FileProcessingError: If file fails security validation
        FileNotFoundError: If file doesn't exist
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Config file not found: {file_path}")

    # Check file size
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE_BYTES:
        raise FileProcessingError(
            f"File size {file_size} bytes exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes"
        )

    # Quick line count check (approximate)
    try:
        with timeout_context(FILE_OPERATION_TIMEOUT):
            with open(file_path, 'rb') as f:
                line_count = sum(1 for _ in f)
                if line_count > MAX_LINE_COUNT:
                    raise FileProcessingError(
                        f"File has {line_count} lines, exceeds maximum of {MAX_LINE_COUNT} lines"
                    )
    except TimeoutError:
        raise FileProcessingError(f"File validation timed out after {FILE_OPERATION_TIMEOUT} seconds")

def safe_read_config_file(file_path: str) -> List[str]:
    """
    Safely read a config file with bounds checking

    Args:
        file_path: Path to the config file

    Returns:
        List[str]: Lines from the file

    Raises:
        FileProcessingError: If file fails security validation
    """
    # Validate file first
    validate_file_for_processing(file_path)

    lines = []
    try:
        with timeout_context(FILE_OPERATION_TIMEOUT):
            with open(file_path, 'r', encoding='utf-8') as file:
                for line_num, line in enumerate(file, 1):
                    # Check line count limit
                    if line_num > MAX_LINE_COUNT:
                        raise FileProcessingError(f"Exceeded maximum line count of {MAX_LINE_COUNT}")

                    # Check line length limit
                    if len(line) > MAX_LINE_LENGTH:
                        raise FileProcessingError(
                            f"Line {line_num} exceeds maximum length of {MAX_LINE_LENGTH} characters"
                        )

                    lines.append(line)

    except TimeoutError:
        raise FileProcessingError(f"File reading timed out after {FILE_OPERATION_TIMEOUT} seconds")
    except UnicodeDecodeError as e:
        raise FileProcessingError(f"File encoding error: {e}")

    return lines

def update_nvdsanalytics_config(config_file_path: str, new_threshold: int, gdino_rois:List[int]) -> str:
    """
    Update the object-threshold value in the nvdsanalytics config file.

    Args:
        config_file_path: Path to the config_nvdsanalytics.txt file
        new_threshold: New threshold value to set

    Returns:
        str: Path to the updated config file (same as input if successful)

    Raises:
        FileNotFoundError: If the config file doesn't exist
        ValueError: If the new_threshold is not a positive integer
        RuntimeError: If the object-threshold line is not found in the file
    """
    # Validate input parameters
    if not isinstance(new_threshold, int) or new_threshold <= 0:
        raise ValueError("new_threshold must be a positive integer")

    # Safely read the config file with security validation
    try:
        lines = safe_read_config_file(config_file_path)
    except FileProcessingError as e:
        raise ValueError(f"File processing error: {e}")
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e))

    # Find and replace the object-threshold line
    object_threshold_found = False
    for i, line in enumerate(lines):
        # Additional safety check for loop bounds (should not be needed due to safe_read_config_file, but defensive)
        if i >= MAX_LINE_COUNT:
            raise FileProcessingError(f"Loop exceeded maximum iterations of {MAX_LINE_COUNT}")

        # Look for lines that start with "object-threshold="
        if line.strip().startswith("object-threshold="):
            lines[i] = f"object-threshold={new_threshold}\n"
            object_threshold_found = True
            break

    if not object_threshold_found:
        raise RuntimeError("object-threshold line not found in the config file")

    # Find and replace the object-threshold line
    if gdino_rois:
        rois_found = False
        rois_string = f"{gdino_rois[0]};{gdino_rois[1]};"\
                      f"{gdino_rois[2]+gdino_rois[0]-1};{gdino_rois[1]};"\
                      f"{gdino_rois[2]+gdino_rois[0]-1};{gdino_rois[1]+gdino_rois[3]-1};"\
                      f"{gdino_rois[0]};{gdino_rois[3]+gdino_rois[1]-1}"
        for i, line in enumerate(lines):
            # Additional safety check for loop bounds
            if i >= MAX_LINE_COUNT:
                raise FileProcessingError(f"Loop exceeded maximum iterations of {MAX_LINE_COUNT}")

            # Look for lines that start with "roi-OC="
            if line.strip().startswith("roi-OC="):
                lines[i] = f"roi-OC={rois_string}\n"
                rois_found = True
                break

        if not rois_found:
            raise RuntimeError("rois line not found in the config file")


    # Write the updated content back to the file
    with open(config_file_path, 'w') as file:
        file.writelines(lines)

    print(f"Updated object-threshold to {new_threshold} in {config_file_path}")
    return config_file_path

def update_infer_config_threshold(config_file_path: str, threshold_value: float, frame_skip_interval: int) -> str:
    """
    Add or update the threshold value in the [class-attrs-all] section of an inference config file.

    Args:
        config_file_path: Path to the config_infer_primary.txt file
        threshold_value: Threshold value to set (float)

    Returns:
        str: Path to the updated config file (same as input if successful)

    Raises:
        FileNotFoundError: If the config file doesn't exist
        ValueError: If the threshold_value is not a valid float or file processing fails
        RuntimeError: If the [class-attrs-all] section is not found in the file
        FileProcessingError: If file size or content exceeds security limits
    """
    # Validate input parameters
    if not isinstance(threshold_value, (float)):
        raise ValueError("threshold_value must be float")

    if threshold_value < 0.0 or threshold_value > 1.0:
        raise ValueError("threshold_value must be between 0.0 and 1.0")

    # Safely read the config file with security validation
    try:
        lines = safe_read_config_file(config_file_path)
    except FileProcessingError as e:
        raise ValueError(f"File processing error: {e}")
    except FileNotFoundError as e:
        raise FileNotFoundError(str(e))

    # Find the [class-attrs-all] section and add/update threshold
    class_attrs_all_found = False
    threshold_updated = False

    for i, line in enumerate(lines):
        # Additional safety check for loop bounds
        if i >= MAX_LINE_COUNT:
            raise FileProcessingError(f"Loop exceeded maximum iterations of {MAX_LINE_COUNT}")

        line_stripped = line.strip()

        # Check if we found the [class-attrs-all] section
        if line_stripped == "[class-attrs-all]":
            class_attrs_all_found = True
            continue

        # If we're in the [class-attrs-all] section, look for existing threshold
        if class_attrs_all_found and not threshold_updated:
            # If we hit another section (starts with [), we're done with [class-attrs-all]
            if line_stripped.startswith("[") and line_stripped.endswith("]"):
                # We've moved to the next section, so add threshold at the end of [class-attrs-all]
                if not threshold_updated:
                    # Find the last line of the [class-attrs-all] section
                    insert_index = i
                    loop_counter = 0
                    while (insert_index > 0 and
                           (lines[insert_index - 1].strip() == "" or
                            lines[insert_index - 1].strip().startswith("#")) and
                           loop_counter < MAX_LINE_COUNT):
                        insert_index -= 1
                        loop_counter += 1
                    lines.insert(insert_index, f"threshold={threshold_value}\n")
                    threshold_updated = True

            # Check if threshold already exists in this section
            if line_stripped.startswith("threshold="):
                lines[i] = f"threshold={threshold_value}\n"
                threshold_updated = True

        if  line_stripped.startswith("interval=") and not frame_skip_interval_updated:
            lines[i] = f"interval={frame_skip_interval}\n"
            frame_skip_interval_updated = True

        if frame_skip_interval_updated and threshold_updated:
            break

    # If we found [class-attrs-all] but didn't update threshold, add it at the end
    if class_attrs_all_found and not threshold_updated:
        # Find the end of the [class-attrs-all] section
        loop_counter = 0
        for i in range(len(lines) - 1, -1, -1):
            # Additional safety check for loop bounds
            if loop_counter >= MAX_LINE_COUNT:
                raise FileProcessingError(f"Backward loop exceeded maximum iterations of {MAX_LINE_COUNT}")
            loop_counter += 1

            line_stripped = lines[i].strip()
            if line_stripped == "[class-attrs-all]":
                # Add threshold right after the section header
                lines.insert(i + 1, f"threshold={threshold_value}\n")
                threshold_updated = True
                break
            elif line_stripped.startswith("[") and line_stripped.endswith("]"):
                # We found the next section, so add threshold before it
                lines.insert(i, f"threshold={threshold_value}\n")
                threshold_updated = True
                break

    if not class_attrs_all_found:
        raise RuntimeError("[class-attrs-all] section not found in the config file")

    if not threshold_updated:
        raise RuntimeError("Failed to add threshold to [class-attrs-all] section")

    # Write the updated content back to the file
    with open(config_file_path, 'w') as file:
        file.writelines(lines)

    print(f"Added/updated threshold={threshold_value} in [class-attrs-all] section of {config_file_path}")
    return config_file_path

class ObjectCounterMarker(BatchMetadataOperator):
    def __init__(self, gdinoprompt:str):
        super().__init__()
        self.gdinoprompt = gdinoprompt.strip()
        self.class_list = self.gdinoprompt.split(" . ")
        self.class_dict = {}

        self.rgb_array = np.random.random((1000, 3))
        print(f"Object Counter Marker: {self.class_list}")

    def handle_metadata(self, batch_meta):
        for frame_meta in batch_meta.frame_items:
            for class_name in self.class_list:
                self.class_dict[class_name] = 0

            for object_meta in frame_meta.object_items:
                object_meta.rect_params.border_width =0
                center = object_meta.rect_params.left + object_meta.rect_params.width/2, object_meta.rect_params.top + object_meta.rect_params.height/2
                if center is not None:
                    cx, cy = center
                    object_meta.text_params.x_offset = int(
                            cx - 0 + 0.95 * object_meta.rect_params.left
                        )
                    object_meta.text_params.y_offset = int(
                            cy - 0 + 0.95 * object_meta.rect_params.top
                        )
                rgb_value = self.rgb_array[object_meta.object_id % 1000]
                object_meta.rect_params.border_color = osd.Color(rgb_value[0], rgb_value[1], rgb_value[2], 1.0)

                rgb_value1 = self.rgb_array[999 - object_meta.object_id % 1000]
                #text_params = osd.Text()
                text_params = object_meta.text_params
                text_params.display_text = str(object_meta.object_id).encode('ascii')
                text_params.x_offset = int(cx)
                text_params.y_offset = int(cy)
                text_params.font_params.name = osd.FontFamily.Serif
                text_params.font_params.size = int(object_meta.rect_params.height*0.1)
                text_params.font_params.color = osd.Color(rgb_value1[0], rgb_value1[1], rgb_value1[2], 1.0)
                text_params.set_bg_clr = True
                text_params.text_bg_clr = osd.Color(0.0, 0.0, 0.0, 1.0)
                object_meta.text_params = text_params

                for class_name in self.class_list:
                    if class_name == object_meta.label:
                        self.class_dict[class_name] += 1

            #print(f"Object Counter: Pad Idx={frame_meta.pad_index},"
            #      f"Frame Number={frame_meta.frame_number},"
            #      f"{self.class_dict}")
            '''
            display_text = f"{self.class_dict}"
            display_meta = batch_meta.acquire_display_meta()
            text = osd.Text()
            text.display_text = display_text.encode('ascii')
            text.x_offset = 10
            text.y_offset = 12
            text.font.name = osd.FontFamily.Serif
            text.font.size = 12
            text.font.color = osd.Color(1.0, 1.0, 1.0, 1.0)
            text.set_bg_color = True
            text.bg_color = osd.Color(0.0, 0.0, 0.0, 1.0)
            display_meta.add_text(text)
            frame_meta.append(display_meta)
            '''

def cveventrecorder(file_path, gdinoprompt:str, output_folder:str, gdinothreshold:float, gdino_rois:List[List[int]], frame_skip_interval:int, object_detection_threshold=3,streamname:str=None):

    # update the config file with the new prompt if it exists else use
    # the default prompt
    default_gdinothreshold = 0.3
    if gdinothreshold:
        threshold = gdinothreshold
    else:
        threshold = default_gdinothreshold

    if gdino_rois[0] == []:
        gdino_rois = [0,0,1920,1080]
    else:
        gdino_rois = gdino_rois[0]


    #if architecture == 'linux-aarch64':
    #    gdinoprompt = None

    if frame_skip_interval is None:
        frame_skip_interval = 0

    if gdinoprompt and USE_GDINO:
        updated_inferconfig_file = update_config_type_name(CONFIG_FILE_PATH, gdinoprompt, threshold, frame_skip_interval)
        print(f"########Using prompt: {gdinoprompt} {threshold} {frame_skip_interval} in {updated_inferconfig_file}")
    else:
        updated_inferconfig_file = CONFIG_FILE_PATH

    # Update the nvdsanalytics config with object threshold
    update_nvdsanalytics_config(CONFIG_NVDSANALYTICS_FILE_PATH, object_detection_threshold, gdino_rois)
    print(f"#######Using object-threshold: {object_detection_threshold} in {CONFIG_NVDSANALYTICS_FILE_PATH}")
    print(f"#######Using roi-OC: {gdino_rois} in {CONFIG_NVDSANALYTICS_FILE_PATH}")

    # Update the inference config with threshold value
    try:
        # Convert object_detection_threshold to a float between 0 and 1
        # Assuming object_detection_threshold is the number of objects, we'll use a default inference threshold
        update_infer_config_threshold(updated_inferconfig_file, gdinothreshold, frame_skip_interval)
        print(f"#######Using inference threshold: {gdinothreshold} in {updated_inferconfig_file} and tracking interval: {frame_skip_interval}")
    except Exception as e:
        print(f"Warning: Could not update inference config threshold: {e}")

    pipeline = Pipeline(PIPELINE_NAME)
#    pipeline.add("filesrc", "src", {"location": file_path})
#    pipeline.add("decodebin", "decbin")
    if file_path.startswith("rtsp://") or file_path.startswith("file://"):
        pipeline.add("nvurisrcbin", "decbin", {"uri": file_path})
        print(f"######## Using RTSP or file path file_path: {file_path}")
    else :
        pipeline.add("nvurisrcbin", "decbin", {"uri": "file://" + file_path})

    if file_path.startswith("rtsp://"):
        pipeline["decbin"].set({"latency": 500, "leaky": 2, "max-size-buffers": 2, "num-extra-surfaces": 10,  "init-rtsp-reconnect-interval": 10})
        is_live = True
    else:
        is_live = False

    pipeline.add("nvstreammux", "mux", 
                {"batch-size": 1, "width": STREAM_WIDTH, "height": STREAM_HEIGHT, 
                "batched-push-timeout": -1, "live-source": is_live, "buffer-pool-size": 16})
    if USE_GDINO:
        pipeline.add("nvinferserver", "inferserver", {"config-file-path": updated_inferconfig_file})
    else:
        pipeline.add("nvinfer", "inferserver", {"config-file-path": updated_inferconfig_file})

    pipeline.add("queue", "queue1")
    pipeline.add("nvtracker", "tracker", {"user-meta-pool-size": 256,
                 "ll-lib-file":"/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so",
                 "ll-config-file":TRACKER_CONFIG_FILE_PATH, "compute-hw": 1})
    pipeline.attach("tracker", Probe("counter", ObjectCounterMarker(gdinoprompt)))
    pipeline.add("queue", "queue2")
    pipeline.add("queue", "queue3")
    pipeline.add("queue", "queue4")
    pipeline.add("nvvideoconvert", "convert", {"compute-hw": 1})
    pipeline.add("nvvideoconvert", "convert2", {"compute-hw": 1})
    pipeline.add("nvvideoconvert", "convert3", {"compute-hw": 1})
    pipeline.add("nvdsanalytics", "nvdsanalytics",{"config-file": CONFIG_NVDSANALYTICS_FILE_PATH})
    if DISABLE_SOM_OVERLAY:
        pipeline.add("queue", "nvdsosd")
    else:
        pipeline.add("nvdsosd", "nvdsosd")
    pipeline.add("eventgenerator", "eventgenerator")
    print("CLIP_CACHE_PRE_EV_TIME", CLIP_CACHE_PRE_EV_TIME)
    print("CLIP_CACHE_POST_EV_TIME", CLIP_CACHE_POST_EV_TIME)
    pipeline.add("nvdscacheevent", "cacheevent", {
        "output-folder": output_folder,
        "streamname": streamname,
        "clip-cache-pre-ev-time": CLIP_CACHE_PRE_EV_TIME,
        "clip-cache-post-ev-time": CLIP_CACHE_POST_EV_TIME
    })
    #pipeline.add("nvdscacheevent", "cacheevent")
    print("ENABLE_FILE_STREAMING_MODE=", ENABLE_FILE_STREAMING_MODE)
    if is_live:
        pipeline.add("fakesink", "sink", {"sync": False, "qos": True}) #qos default is false
    else:
        pipeline.add("fakesink", "sink", {"sync": True if ENABLE_FILE_STREAMING_MODE else False, "qos": False}) #qos default is false

    pipeline.link(("decbin", "mux"), ("", "sink_%u"))
    pipeline.link("mux", "inferserver", "queue1", "tracker", "queue2", "convert",
     "nvdsanalytics", "convert2" , "nvdsosd", "convert3", "queue3","eventgenerator", "queue4", "cacheevent", "sink")
    pipeline.start().wait()
    # Read info.txt file and populate events list
    '''
    events = []
    if output_folder:
        info_file_path = os.path.join(output_folder, "info.txt")
        if os.path.exists(info_file_path):
            try:
                with open(info_file_path, "r") as info_file:
                    for line_num, line in enumerate(info_file, 1):
                        print(f"######## line: {line}", flush=True)
                        line = line.strip()
                        if line:  # Skip empty lines
                            # Create event entry for each line in info.txt
                            event = EventOutput(
                                event_type="over_crowding",
                                clip=f"{line}.mp4",
                                metadata=f"{line}.json"
                            )
                            events.append(event)
                print(f"Read {len(events)} events from info.txt file")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to get the list of output clips: {str(e)}")
    '''

    print(f"====================Pipeline finished====================")
    time.sleep(1)



# Create FastAPI app instance
app = FastAPI(
    title="CV pipeline event based recording",
    description="A simple API that records events from a video stream",
    version="1.0.0"
)

class CVParams(BaseModel):
    gdinoprompt: Optional[str] = None
    gdinothreshold: Optional[float] = 0.3
    gdino_rois: Optional[List[List[int]]] = None
    overlay: Optional[bool] = True

# Define the request model
class AddStreamRequest(BaseModel):
    version: str = Field(description="Version of the API, for ex 1.0")
    timestamp: Optional[str] = Field(None, description="Timestamp of the input stream(Applicable only for file input case),request in ISO format, for example 2025-07-21T12:00:00.000Z")
    stream_url: str = Field(description="URL of the video stream to analyze. For example rtsp://192.168.1.100:554/stream1 or file:///path/to/video.mp4")
    pipeline_id: str = Field(description="Unique pipeline identifier returned by the create pipeline API")
    output_folder: Optional[str] = Field(None, description="Output folder path")
    sensor_id: Optional[str] = Field(None, description="Unique sensor identifier, for example uniqueSensorID1")
    stream_name: Optional[str] = Field(None, description="Name of the stream, for example warehouse cam 1")
    processing_state: Optional[str] = Field(None, description="enabled indicates start processing immediately, disabled indicates start processing on request")
    cv_params: Optional[CVParams] = Field(None, description="CV parameters")

class EventOutput(BaseModel):
    event_type: Optional[str] = Field(None, description="Type of the event, for example \"over_crowding\"")
    clip: Optional[str] = Field(None, description="Path to the clip file corresponding to the event")
    metadata: Optional[str] = Field(None, description="Path to the JSON CV metadata file")

# Define the response model for AddStreamRequest
class AddStreamResponse(BaseModel):
    stream_id: UUID = Field(description="Unique stream identifier, which can be referenced in the API endpoints.")
    pipeline_id: str = Field(description="Unique pipeline identifier on which this stream is being processed.")
    status: str = Field(description="Status of the request")
    processing_state: str = Field(description="Processing state of the stream, enabled indicates processing started, disabled indicates processing not yet started")
    message: str = Field(description="Success or error message")
    timestamp: str = Field(description="Timestamp of the request in ISO format")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")
    events: List[EventOutput] = Field(description="List of events")


class StreamInfo:
    def __init__(self):
        self.stream_id: str = ""
        self.video_path: str = ""
        self.process: Optional[multiprocessing.Process] = None
        self.version: str = ""
        self.timestamp: str = ""
        self.pipeline_id: str = ""
        self.output_folder: Optional[str] = None
        self.sensor_id: Optional[str] = None
        self.stream_name: Optional[str] = None
        self.processing_state: Optional[str] = None
        self.cv_params: Optional[CVParams] = None

class RemoveStreamResponse(BaseModel):
    stream_id: str = Field(description="Unique stream identifier returned by the POST /api/addstream endpoint")
    status: str = Field(description="Status of the request")
    message: str = Field(description="Success or error message")
    timestamp: str = Field(description="Timestamp of the request in ISO format")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

class RemoveStreamRequest(BaseModel):
    stream_id: str = Field(
        description="Unique stream identifier, which can be referenced in the API endpoints."
    )
    version: Optional[str] = Field(None, description="Version of the API, for ex 1.0")

# Define the pipeline parameters model
class PipelineParams(BaseModel):
    min_clip_duration: int = Field(description="Minimum clip duration in seconds")
    max_clip_duration: int = Field(description="Maximum clip duration in seconds")
    frame_skip_interval: int = Field(description="Frame skip interval")
    minimum_detection_threshold: int = Field(description="Minimum detection threshold for number of objects")

# Define the pipeline request model
class CreatePipelineRequest(BaseModel):
    name: str = Field(description="Name of the pipeline")
    endpoint_url: Optional[str] = Field(None, description="Optional endpoint URL for the pipeline")
    config: Optional[str] = Field(None, description="Optional config file path")
    type: str = Field(description="Type of pipeline (e.g., object_detection)")
    params: PipelineParams = Field(description="Pipeline parameters")

# Define the pipeline response model
class CreatePipelineResponse(BaseModel):
    id: str = Field(description="Unique pipeline identifier")
    status: str = Field(description="Status of the pipeline creation")
    message: str = Field(description="Success or error message")
    created_at: Optional[str] = Field(None, description="Timestamp when pipeline was created (ISO format)")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

# Define the delete pipeline request model
class DeletePipelineRequest(BaseModel):
    id: str = Field(description="Unique pipeline identifier to delete")
    cleanup_resources: Optional[bool] = Field(True, description="Optional flag to delete all registered streams running under this pipeline")

# Define the delete pipeline response model
class DeletePipelineResponse(BaseModel):
    id: str = Field(description="Unique pipeline identifier")
    status: str = Field(description="Status of the pipeline deletion")
    message: str = Field(description="Success or error message")
    deleted_at: Optional[str] = Field(None, description="Timestamp when pipeline was deleted (ISO format)")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

# Define the pipeline list item model
class PipelineListItem(BaseModel):
    id: str = Field(description="Unique pipeline identifier")
    config: Optional[str] = Field(None, description="Config file path")
    created_at: str = Field(description="Timestamp when pipeline was created (ISO format)")

# Define the get all pipelines response model
class GetPipelinesResponse(BaseModel):
    status: str = Field(description="Status of the request")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")
    pipelines: List[PipelineListItem] = Field(description="List of pipelines")

# Define the get pipeline by ID response model
class GetPipelineResponse(BaseModel):
    status: str = Field(description="Status of the request")
    id: str = Field(description="Unique pipeline identifier")
    endpoint_url: Optional[str] = Field(None, description="Endpoint URL for the pipeline")
    config: Optional[str] = Field(None, description="Config file path")
    created_at: str = Field(description="Timestamp when pipeline was created (ISO format)")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

# Define the stream list item model
class StreamListItem(BaseModel):
    stream_id: str = Field(description="Unique stream identifier")
    pipeline_id: str = Field(description="Unique pipeline identifier")
    processing_state: str = Field(description="Processing state of the stream (enabled/disabled)")
    timestamp: str = Field(description="Timestamp when stream was created (ISO format)")

# Define the get all streams response model
class GetStreamsResponse(BaseModel):
    status: str = Field(description="Status of the request")
    streams: List[StreamListItem] = Field(description="List of streams")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

# Define the get stream by ID response model
class GetStreamResponse(BaseModel):
    stream_id: str = Field(description="Unique stream identifier")
    pipeline_id: str = Field(description="Unique pipeline identifier")
    processing_state: str = Field(description="Processing state of the stream (enabled/disabled)")
    timestamp: str = Field(description="Timestamp when stream was created (ISO format)")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

class StreamConfig(BaseModel):
    processing_state: str = Field(description="Processing state of the stream (enabled/disabled)")

# Define the stream config request model
class StreamConfigRequest(BaseModel):
    stream_id: str = Field(description="Unique stream identifier")
    config: StreamConfig = Field(description="Stream configuration")

# Define the stream config response model
class StreamConfigResponse(BaseModel):
    status: str = Field(description="Status of the request (success/failure)")
    stream_id: str = Field(description="Unique stream identifier")
    processing_state: str = Field(description="Processing state of the stream (enabled/disabled)")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

class ProcessingStatusResponse(BaseModel):
    stream_id: str = Field(description="Unique stream identifier")
    status: str = Field(description="High-level processing status (processing_pending/completed/terminated/unknown)")
    message: str = Field(description="Human-readable status message")
    processing_state: Optional[str] = Field(None, description="Current processing state as tracked by the service")
    completed_at: Optional[str] = Field(None, description="Timestamp when processing finished (ISO format)")
    events: Optional[List[EventOutput]] = Field(None, description="List of events if available on completion")
    error_code: Optional[int] = Field(None, description="Error code if any")
    error_details: Optional[str] = Field(None, description="Error details if any")

class PipelineInfo:
    def __init__(self):
        self.id: str = ""
        self.name: str = ""
        self.endpoint_url: Optional[str] = None
        self.config: Optional[str] = None
        self.type: str = ""
        self.params: Optional[PipelineParams] = None
        self.created_at: Optional[str] = None
        self.status: str = ""
        self.error_code: Optional[int] = None
        self.error_details: Optional[str] = None

@app.get("/")
async def root():
    """Root endpoint that provides basic information about the API"""
    return {
        "message": "CV pipeline event based recording",
        "version": "1.0.0",
        "endpoints": {
            "/": "This information page",
            "/api/addstream": "POST endpoint to add a video stream for event recording",
            "/api/stream": "DELETE endpoint to remove a video stream",
            "/api/pipeline": "POST endpoint to create a new pipeline, DELETE endpoint to delete a pipeline",
            "/api/pipelines": "GET endpoint to list all pipelines",
            "/api/pipelines/{id}": "GET endpoint to get a specific pipeline by ID",
            "/api/streams": "GET endpoint to list the information of all streams",
            "/api/streams/{id}": "GET endpoint to get the information of a specific stream by ID",
            "/api/streams/{id}/status": "GET endpoint to get processing status for a specific stream (pending/completed)",
            "/api/stream/{id}/config": "POST endpoint to update stream configuration",
            "/health": "Health check endpoint",
            "/docs": "API documentation (Swagger UI)",
            "/redoc": "Alternative API documentation"
        }
    }

@app.post("/api/addstream", response_model=AddStreamResponse)
async def add_stream(request: AddStreamRequest, response: Response):
    """
    Event based recording of a video stream.

    Args:
        request: AddStreamRequest containing video_path

    Returns:
        AddStreamResponse with the result and video_path
    """
    try:
        events = []
        if request.pipeline_id not in pipeline_map:
            raise HTTPException(status_code=404, detail=f"Pipeline with id {request.pipeline_id} not found")

        # Record events from the video stream
        # call the cveventrecorder
        # use the global process here

        # Extract CV parameters
        gdinoprompt = request.cv_params.gdinoprompt if request.cv_params else None
        gdinothreshold = request.cv_params.gdinothreshold if request.cv_params else 0.3
        print (f"#########request.cv_params: {request.cv_params}")
        print(f"#########gdinoprompt: {gdinoprompt}")
        print(f"#########gdinothreshold: {gdinothreshold}")
        gdino_rois = request.cv_params.gdino_rois if request.cv_params else None
        print(f"#########gdino_rois: {gdino_rois[0]}")
        print(f"#########gdino_rois length : {len(gdino_rois)}")

        asset_id = str(uuid.uuid4())
        global asset_map
        while asset_id in asset_map:
            asset_id = str(uuid.uuid4())

        stream_info = StreamInfo()
        stream_info.stream_id = asset_id
        stream_info.video_path = request.stream_url
        stream_info.cv_params = request.cv_params
        stream_info.output_folder = request.output_folder
        stream_info.pipeline_id = request.pipeline_id
        stream_info.sensor_id = request.sensor_id
        stream_info.stream_name = request.stream_name
        stream_info.processing_state = "disabled"
        stream_info.timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S.%fZ')

        pipeline_config = pipeline_map[request.pipeline_id]
        print(f"#########pipeline_config.params.minimum_detection_threshold: {pipeline_config.params.minimum_detection_threshold}")

        process = multiprocessing.Process(target=cveventrecorder, args=(request.stream_url, gdinoprompt, request.output_folder, gdinothreshold,
                                                                        gdino_rois,
                                                                        pipeline_config.params.frame_skip_interval,
                                                                        pipeline_config.params.minimum_detection_threshold,
                                                                        request.stream_name+"_"+str(stream_info.timestamp)))
        stream_info.processing_state = "enabled"
        stream_info.process = process
        asset_map[asset_id] = stream_info
        process.start()

        stream_info.processing_state = "running"
        '''
        while True:
            if process.is_alive():
                result = process.join(timeout=5)
                print(f"#########Waiting for process to finish: {result} {process.exitcode} {asset_id}")
                if process.is_alive():
                    continue
                else:
                    if process.exitcode == 0:
                        stream_info.processing_state = "completed"
                        break
                    else:
                        stream_info.processing_state = "terminated"
                        break
                break
        '''

        if process.is_alive():
            result = process.join(timeout=1)
            print(f"#########Waiting for process to finish: {result} {process.exitcode} {asset_id}")
            if process.exitcode == 0:
                stream_info.processing_state = "completed"
            elif process.exitcode is not None:
                stream_info.processing_state = "terminated"
            else:
                stream_info.processing_state = "running"
    # Remove the premature termination - let the process run until video ends naturally
        # or until explicitly stopped via removestream endpoint
        result = "Event based recording of the video stream is started"

        # Set HTTP status code to 202 when processing is running/pending
        try:
            state_lower = (stream_info.processing_state or "").lower()
            if state_lower in ["running", "enabled"]:
                response.status_code = 202
            elif state_lower in ["completed"]:
                response.status_code = 200
        except Exception:
            pass

        return AddStreamResponse(stream_id=asset_id,
                                 pipeline_id=request.pipeline_id,
                                 status="success",
                                 processing_state= stream_info.processing_state,
                                 message="Stream submitted successfully.",
                                 timestamp=stream_info.timestamp,
                                 error_code=None,
                                 error_details=None,
                                 events=events)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in adding stream: {str(e)}")

@app.delete("/api/stream", response_model=RemoveStreamResponse)
async def delete_stream(request: RemoveStreamRequest):
    """
    Remove a stream from the event based recording.

    Args:
        request: RemoveStreamRequest containing the UUID of the stream to remove

    Returns:
        RemoveStreamResponse with the result and stream_id
    """
    try:
        # Record events from the video stream
        # call the cveventrecorder
        # use the global process here
        streamid = str(request.stream_id)
        global asset_map

        if streamid in asset_map:
            process = asset_map[streamid].process

            # Terminate the process gracefully
            if process.is_alive():
                process.terminate()

                # Wait for the process to finish with a timeout
                process.join(timeout=10)

                # If process is still alive after timeout, force kill it
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)

            asset_map.pop(streamid)

            return RemoveStreamResponse(stream_id=streamid,
                                        status="success",
                                        message="Stream removed successfully",
                                        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                                        error_code=None,
                                        error_details=None)
        else:
            return RemoveStreamResponse(stream_id=streamid,
                                        status="error",
                                        message=f"Stream with id {streamid} not found",
                                        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                                        error_code=404,
                                        error_details=f"Stream {streamid} does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in removing stream: {str(e)}")

@app.post("/api/pipeline", response_model=CreatePipelineResponse)
async def create_pipeline(request: CreatePipelineRequest):
    """
    Create a new pipeline with specified configuration.

    Args:
        request: CreatePipelineRequest containing pipeline configuration

    Returns:
        CreatePipelineResponse with the pipeline creation result
    """
    try:
        # Generate unique pipeline ID
        pipeline_id = str(uuid.uuid4())
        global pipeline_map
        while pipeline_id in pipeline_map:
            pipeline_id = str(uuid.uuid4())

        # Store pipeline configuration
        pipeline_config = PipelineInfo()
        pipeline_config.id = pipeline_id
        pipeline_config.name = request.name
        pipeline_config.endpoint_url = request.endpoint_url
        pipeline_config.config = request.config
        pipeline_config.type = request.type
        pipeline_config.params = request.params
        pipeline_config.created_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        pipeline_config.status = "created"
        pipeline_config.error_code = None
        pipeline_config.error_details = None

        pipeline_map[pipeline_id] = pipeline_config

        #TODO: Invoke pipeline creation code.

        # Return success response
        return CreatePipelineResponse(
            id=pipeline_id,
            status="success",
            message="Pipeline Created Successfully",
            created_at=pipeline_config.created_at,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return CreatePipelineResponse(
            id="",
            status="error",
            message="Failed to create pipeline",
            error_code=500,
            error_details=str(e)
        )

@app.delete("/api/pipeline", response_model=DeletePipelineResponse)
async def delete_pipeline(request: DeletePipelineRequest):
    """
    Delete a pipeline and optionally cleanup associated resources.

    Args:
        request: DeletePipelineRequest containing pipeline ID and cleanup flag

    Returns:
        DeletePipelineResponse with the pipeline deletion result
    """
    try:
        pipeline_id = request.id
        cleanup_resources = request.cleanup_resources
        global pipeline_map, asset_map

        # Check if pipeline exists
        if pipeline_id not in pipeline_map:
            return DeletePipelineResponse(
                id=pipeline_id,
                status="error",
                message=f"Pipeline with id {pipeline_id} not found",
                error_code=404,
                error_details=f"Pipeline {pipeline_id} does not exist"
            )

        # If cleanup_resources is True, remove all streams associated with this pipeline
        if cleanup_resources:
            #TODO: Get the stream associated with this pipeline if any and then invoke remove stream here
            pass

        #TODO: invoke removing the pipeline code

        # Remove the pipeline from the database
        pipeline_map.pop(pipeline_id)

        # Return success response
        return DeletePipelineResponse(
            id=pipeline_id,
            status="success",
            message="Pipeline Deleted Successfully",
            deleted_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return DeletePipelineResponse(
            id=request.id if hasattr(request, 'id') else "",
            status="error",
            message="Failed to delete pipeline",
            error_code=500,
            error_details=str(e)
        )

@app.get("/api/pipelines", response_model=GetPipelinesResponse)
async def get_pipelines():
    """
    Get all pipelines.

    Returns:
        GetPipelinesResponse with the list of all pipelines
    """
    try:
        global pipeline_map

        # Convert pipeline_map to list of PipelineListItem objects
        pipelines = []
        for pipeline_id, pipeline_info in pipeline_map.items():
            pipeline_item = PipelineListItem(
                id=pipeline_info.id,
                config=pipeline_info.config,
                created_at=pipeline_info.created_at
            )
            pipelines.append(pipeline_item)

        # Return success response
        return GetPipelinesResponse(
            status="success",
            error_code=None,
            error_details=None,
            pipelines=pipelines
        )

    except Exception as e:
        # Return error response
        return GetPipelinesResponse(
            status="error",
            error_code=500,
            error_details=str(e),
            pipelines=[]
        )

@app.get("/api/pipelines/{pipeline_id}", response_model=GetPipelineResponse)
async def get_pipeline(pipeline_id: str):
    """
    Get a specific pipeline by ID.

    Args:
        pipeline_id: Unique pipeline identifier

    Returns:
        GetPipelineResponse with the pipeline details
    """
    try:
        global pipeline_map

        # Check if pipeline exists
        if pipeline_id not in pipeline_map:
            return GetPipelineResponse(
                status="error",
                id=pipeline_id,
                endpoint_url=None,
                config=None,
                created_at=None,
                error_code=404,
                error_details=f"Pipeline {pipeline_id} not found"
            )

        pipeline_info = pipeline_map[pipeline_id]

        # Return success response
        return GetPipelineResponse(
            status="success",
            id=pipeline_info.id,
            endpoint_url=pipeline_info.endpoint_url,
            config=pipeline_info.config,
            created_at=pipeline_info.created_at,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return GetPipelineResponse(
            status="error",
            id=pipeline_id,
            endpoint_url=None,
            config=None,
            created_at=None,
            error_code=500,
            error_details=str(e)
        )

@app.get("/api/streams", response_model=GetStreamsResponse)
async def get_streams():
    """
    Get all streams.

    Returns:
        GetStreamsResponse with the list of all streams
    """
    try:
        global asset_map

        # Convert asset_map to list of StreamListItem objects
        streams = []
        for stream_id, stream_info in asset_map.items():
            print(f"######## stream_info.stream_id: {stream_info.stream_id}", flush=True)
            print(f"######## stream_info.pipeline_id: {stream_info.pipeline_id}", flush=True)
            print(f"######## stream_info.processing_state: {stream_info.processing_state}", flush=True)
            print(f"######## stream_info.timestamp: {stream_info.timestamp}", flush=True)
            stream_item = StreamListItem(
                stream_id=stream_info.stream_id,
                pipeline_id=stream_info.pipeline_id,
                processing_state=stream_info.processing_state,
                timestamp=stream_info.timestamp
            )
            streams.append(stream_item)

        # Return success response
        return GetStreamsResponse(
            status="success",
            streams=streams,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return GetStreamsResponse(
            status="error",
            streams=[],
            error_code=500,
            error_details=str(e)
        )

@app.get("/api/streams/{stream_id}", response_model=GetStreamResponse)
async def get_stream(stream_id: str):
    """
    Get a specific stream by ID.

    Args:
        stream_id: Unique stream identifier

    Returns:
        GetStreamResponse with the stream details
    """
    try:
        global asset_map

        # Check if stream exists
        if stream_id not in asset_map:
            return GetStreamResponse(
                status="error",
                stream_id=stream_id,
                pipeline_id="",
                processing_state="",
                timestamp="",
                error_code=404,
                error_details=f"Stream {stream_id} not found"
            )

        stream_info = asset_map[stream_id]

        # Return success response
        return GetStreamResponse(
            stream_id=stream_info.stream_id,
            pipeline_id=stream_info.pipeline_id,
            processing_state=stream_info.processing_state or "enabled",
            timestamp=stream_info.timestamp,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return GetStreamResponse(
            status="error",
            stream_id=stream_id,
            pipeline_id="",
            processing_state="",
            timestamp="",
            error_code=500,
            error_details=str(e)
        )

@app.get("/api/streams/{stream_id}/status", response_model=ProcessingStatusResponse)
async def get_stream_status(stream_id: str, timeout_ms: Optional[int] = 2000, response: Response = None):
    """
    Get processing status for a specific stream. Indicates whether processing is pending or completed.
    """
    try:
        global asset_map

        if stream_id not in asset_map:
            return ProcessingStatusResponse(
                stream_id=stream_id,
                status="unknown",
                message=f"Stream {stream_id} not found",
                processing_state=None,
                completed_at=None,
                events=None,
                error_code=404,
                error_details=f"Stream {stream_id} does not exist"
            )

        stream_info = asset_map[stream_id]
        # Optionally wait for the process to finish up to timeout_ms, then
        # refresh processing_state based on actual process status if available
        processing_state = stream_info.processing_state or "enabled"
        try:
            proc = stream_info.process
            if proc is not None:
                # Block for up to timeout_ms to see if process finishes
                try:
                    wait_seconds = max(0.0, float(timeout_ms or 0) / 1000.0)
                except Exception:
                    wait_seconds = 0.0
                if wait_seconds > 0.0:
                    proc.join(timeout=wait_seconds)
                if proc.is_alive():
                    processing_state = "running"
                else:
                    # Process finished. Infer outcome from exitcode if possible
                    if proc.exitcode == 0:
                        processing_state = "completed"
                    else:
                        processing_state = "terminated"
        except Exception:
            # If we cannot inspect the process, keep existing state
            pass

        # Map internal processing state to high-level status
        if processing_state.lower() in ["completed"]:
            high_level_status = "completed"
        elif processing_state.lower() in ["terminated", "failed", "error"]:
            high_level_status = "terminated"
        else:
            high_level_status = "processing_pending"

        # If completed, try to collect events from output folder
        collected_events = None
        if high_level_status == "completed" and stream_info.output_folder:
            info_file_path = os.path.join(stream_info.output_folder, "info.txt")
            if os.path.exists(info_file_path):
                try:
                    collected_events = []
                    with open(info_file_path, "r") as info_file:
                        for line in info_file:
                            line = line.strip()
                            if not line:
                                continue
                            collected_events.append(EventOutput(
                                event_type="over_crowding",
                                clip=f"{line}.mp4",
                                metadata=f"{line}.json"
                            ))
                except Exception as read_err:
                    # If reading fails, leave events as None but include error details
                    return ProcessingStatusResponse(
                        stream_id=stream_id,
                        status=high_level_status,
                        message="Completed, but failed to read events",
                        processing_state=processing_state,
                        completed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                        events=None,
                        error_code=500,
                        error_details=str(read_err)
                    )

        message = (
            "Processing is pending" if high_level_status == "processing_pending" else
            ("Processing completed" if high_level_status == "completed" else "Processing terminated")
        )

        if response is not None:
            if high_level_status == "processing_pending":
                print(f"#########Status API Processing pending: {stream_id}",flush=True)
                response.status_code = 202
            elif high_level_status == "completed":
                print(f"#########Status API Processing completed: {stream_id}",flush=True)
                response.status_code = 200

        return ProcessingStatusResponse(
            stream_id=stream_id,
            status=high_level_status,
            message=message,
            processing_state=processing_state,
            completed_at=(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if high_level_status == "completed" else None),
            events=collected_events,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        return ProcessingStatusResponse(
            stream_id=stream_id,
            status="unknown",
            message="Failed to retrieve status",
            processing_state=None,
            completed_at=None,
            events=None,
            error_code=500,
            error_details=str(e)
        )

@app.post("/api/stream/{stream_id}/config", response_model=StreamConfigResponse)
async def update_stream_config(stream_id: str, request: StreamConfigRequest):
    """
    Update stream configuration.

    Args:
        stream_id: Unique stream identifier (path parameter)
        request: StreamConfigRequest containing the configuration to update

    Returns:
        StreamConfigResponse with the update result
    """
    try:
        global asset_map

        # Check if stream exists
        if stream_id not in asset_map:
            return StreamConfigResponse(
                status="failure",
                stream_id=stream_id,
                processing_state="",
                error_code=404,
                error_details=f"Stream {stream_id} not found"
            )

        # Validate that the stream_id in the request matches the path parameter
        if request.stream_id != stream_id:
            return StreamConfigResponse(
                status="failure",
                stream_id=stream_id,
                processing_state="",
                error_code=400,
                error_details="Stream ID in request body does not match path parameter"
            )

        stream_info = asset_map[stream_id]

        #TODO: if stream is enabled here, then the response needs to be update.

        #update the stream processing state to the request state
        stream_info.processing_state = request.config.processing_state

        # Return success response
        return StreamConfigResponse(
            status="success",
            stream_id=stream_id,
            processing_state=stream_info.processing_state,
            error_code=None,
            error_details=None
        )

    except Exception as e:
        # Return error response
        return StreamConfigResponse(
            status="failure",
            stream_id=stream_id,
            processing_state="",
            error_code=500,
            error_details=str(e)
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "cv-pipeline-event-based-recording"}

def test_file_processing_security():
    """
    Test function to demonstrate file processing security improvements
    """
    print("\n=== File Processing Security Test ===")

    # Test file size validation
    print("Testing file processing security limits...")
    print(f"✅ File size limit: {MAX_FILE_SIZE_BYTES} bytes ({MAX_FILE_SIZE_BYTES / (1024*1024):.1f} MB)")
    print(f"✅ Line count limit: {MAX_LINE_COUNT} lines")
    print(f"✅ Line length limit: {MAX_LINE_LENGTH} characters")
    print(f"✅ File operation timeout: {FILE_OPERATION_TIMEOUT} seconds")
    print(f"✅ Loop iteration bounds checking: Enabled")
    print(f"✅ Safe file reading with encoding validation: Enabled")

    print("=== End File Processing Security Test ===\n")

# Uncomment the line below to run the security test
# test_file_processing_security()

if __name__ == "__main__":
    import uvicorn

    cvport = os.environ.get("NV_CV_EVENT_DETECTOR_API_PORT", "")
    if cvport == "":
        raise Exception("NV_CV_EVENT_DETECTOR_API_PORT is not set")

    uvicorn.run(app, host="0.0.0.0", port=int(cvport))
