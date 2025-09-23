#
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
#

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstBase", "1.0")
from gi.repository import Gst, GObject, GstBase

import torch
from pyservicemaker import Buffer, BufferProvider, as_tensor, ColorFormat, Pipeline, Feeder
from typing import Any, Optional
from collections import deque
import multiprocessing as mp
import pyds
import numpy as np
import os
import json
import threading
import requests
import uuid
from datetime import datetime, timezone
import platform

# Initialize GStreamer
Gst.init(None)

# Plugin constants
GST_PLUGIN_NAME = "nvdscacheevent"
DEFAULT_QUEUE_SIZE = 1200
MIN_QUEUE_SIZE = 1
MAX_QUEUE_SIZE = 1500

class JsonCVMetadata:
    def __init__(self, request_id="dump", chunkIdx=1):
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
        return frame


# Define a class to store buffer data
class BufferData:
    """
    Data structure for storing buffer metadata and tensor data in the processing queue.

    This class encapsulates all necessary information about a video buffer including
    timing information, frame identification, and the actual tensor data for efficient
    buffer management and frame replacement operations.

    Attributes:
        frame_number (int): Sequential frame number for identification
        pts (int): Presentation timestamp in nanoseconds
        dts (int): Decode timestamp in nanoseconds
        duration (int): Buffer duration in nanoseconds
        batched_tensors (torch.Tensor): PyTorch tensor containing the frame data
    """

    def __init__(self, frame_number: int, pts: int, dts: int, duration: int, batched_tensors: torch.Tensor, metadata: dict):
        """
        Initialize BufferData instance with frame metadata and tensor data.

        Args:
            frame_number (int): Sequential frame number for identification
            pts (int): Presentation timestamp in nanoseconds
            dts (int): Decode timestamp in nanoseconds
            duration (int): Buffer duration in nanoseconds
            batched_tensors (torch.Tensor): PyTorch tensor containing the frame data
            metadata (dict): Metadata for the frame
        """
        self.frame_number = frame_number
        self.pts = pts
        self.dts = dts
        self.duration = duration
        self.batched_tensors = batched_tensors
        self.metadata = metadata

class MyBufferProvider(BufferProvider):
    def __init__(self, event_buffers_list):
        super().__init__()
        self.event_buffers_list = event_buffers_list
        self.total_buffers = len(event_buffers_list)
        self.count = 0
        self._eos_sent = False
        # Dedicated CUDA stream for non-blocking H2D copies
        self._cuda_stream = torch.cuda.Stream(device=torch.device("cuda"))

    def generate(self, size):
        """
        Generate a buffer from the event buffers list.
        """
        if len(self.event_buffers_list) == 0:
            if not self._eos_sent:
                self._eos_sent = True
                Gst.info(f"Encoded Buffer {self.count}/{self.total_buffers} Frames - sending EOS")
            return Buffer()

        buffer_data = self.event_buffers_list.pop(0)
        cpu_tensor = buffer_data.batched_tensors.contiguous()
        try:
            cpu_tensor = cpu_tensor.pin_memory()
        except Exception:
            pass
        # Perform non-blocking H2D copy on our private stream
        with torch.cuda.stream(self._cuda_stream):
            torch_tensor = cpu_tensor.to(device="cuda", non_blocking=True)
        # Ensure default stream (used by as_tensor) waits for our copy to finish
        torch.cuda.current_stream().wait_stream(self._cuda_stream)
        ds_tensor = as_tensor(torch_tensor[0], "HWC") # TODO: handle batch size > 1
        buffer = ds_tensor.wrap(ColorFormat.RGB)
        self.count += 1
        return buffer

def dumpcvmetadatatojson(event_buffers_list, filename="event_data"):

    cvmetalist = []
    for buffer_data in event_buffers_list:
        cvmetalist.append(buffer_data.metadata)
    with open(filename + ".json", "w") as f:
        json.dump(cvmetalist, f)

def send_alert_verification(url,video_path, metadata_path):
    """
    Send alert verification POST request to the verification service.

    Args:
        video_path (str): Path to the generated video file
        metadata_path (str): Path to the generated metadata JSON file
    """
    print(f"Sending alert verification request to {url} for {video_path}")
    try:
        # Construct the JSON payload for the POST request
        payload = {
            "version": "1.0",
            "id": str(uuid.uuid4()),
            "@timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sensorId": "nvdscacheevent-sensor",
            "videoPath": video_path,
            "cvMetadataPath": metadata_path,
            "confidence": 1,
            "alert": {
                "severity": "CRITICAL",
                "status": "VERIFICATION_PENDING",
                "type": "cache-event-alert",
                "description": "Cache event detected requiring verification"
            },
            "event": {
                "type": "cache-data-event",
                "description": "Event triggered from cache-data signal"
            },
            "vssParams": {
                "vlmParams": {
                    "prompt": "Did the vehicle cross the line marked in green color labeled Line Crossing and describe the type of the vehicle?", #FIXME: add prompt here,
                    "temperature": 0.3,
                    "top_p": 0.3,

                },
                "cv_metadata_overlay": True,
                "enable_caption": True,
                "debug": False
            }
        }

        # Send POST request to the verification service
        #url = "http://localhost:8000/verifyAlert"
        headers = {"Content-Type": "application/json"}
        print(f"Sending POST request to {url} with payload: {payload}")

        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            print(f"Alert verification request sent successfully for {video_path}")
            print(f"Response: {response.json()}")
        else:
            Gst.error(f"Alert verification request failed with status code: {response.status_code}")
            Gst.error(f"Response: {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"Error sending alert verification request: {e}")
    except Exception as e:
        print(f"Unexpected error in send_alert_verification: {e}")

def encode_join_function(plugin_instance):
    """
    Wait to join encode threads that have completed execution.
    
    This function runs in a separate thread and continuously monitors the encode_threads
    list to join threads that have finished execution. It helps prevent thread resource
    leaks by properly cleaning up completed threads.
    
    Args:
        plugin_instance: Reference to the NvDsCacheEvent plugin instance containing
                        encode_threads list and encode_join_threads_running flag
    """
    import time
    
    print("encode_join_function: Started monitoring encode threads")
    
    while plugin_instance.encode_join_threads_running:
        try:
            # Create a copy of the current thread list to iterate safely
            threads_to_check = plugin_instance.encode_threads.copy()
            
            for thread in threads_to_check:
                # Check if thread has completed
                if not thread.is_alive():
                    try:
                        # Join the completed thread
                        thread.join(timeout=1.0)
                        print(f"encode_join_function: Successfully joined completed thread {thread.name}")
                        
                        # Remove the thread from the original list
                        if thread in plugin_instance.encode_threads:
                            plugin_instance.encode_threads.remove(thread)
                            print(f"encode_join_function: Removed thread from list. Remaining threads: {len(plugin_instance.encode_threads)}")
                            
                    except Exception as e:
                        print(f"encode_join_function: Error joining thread {thread.name}: {e}")
            
            # Sleep for a short period to avoid busy waiting
            time.sleep(0.5)
            
        except Exception as e:
            print(f"encode_join_function: Error in main loop: {e}")
            time.sleep(1.0)  # Sleep longer on error
    
    # Final cleanup - join any remaining threads
    #print("encode_join_function: Stopping - joining any remaining threads")
    remaining_threads = plugin_instance.encode_threads.copy()
    for thread in remaining_threads:
        try:
            if thread.is_alive():
                #print(f"encode_join_function: Joining remaining thread {thread.name}")
                thread.join(timeout=5.0)  # Give more time for final cleanup
            if thread in plugin_instance.encode_threads:
                plugin_instance.encode_threads.remove(thread)
        except Exception as e:
            print(f"encode_join_function: Error joining remaining thread: {e}")
    
    print("encode_join_function: Finished monitoring encode threads")


def encode_pipeline_function(event_buffers_list, width, height, output_filename="event_data", vss_server_url=None):
    """
    Push the buffers to the encode pipeline.

    Args:
        event_buffers_list: List of BufferData objects to encode
        width: Video width
        height: Video height
        output_filename: Output file name for the encoded video
    """

    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(output_filename)
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
            print(f"Created output directory: {output_dir}")
        except Exception as e:
            print(f"Error creating output directory {output_dir}: {e}")

    # Initialize GStreamer for encoding
    Gst.init(None)

    Gst.info(f"Pushing {len(event_buffers_list)} buffers from #{event_buffers_list[0].frame_number} to #{event_buffers_list[-1].frame_number} -> {output_filename}")
    Gst.info(f"Metadata: {event_buffers_list[0].metadata} {event_buffers_list[-1].metadata}")
    dumpcvmetadatatojson(event_buffers_list, output_filename)


    encode_pipeline = Pipeline("encode-pipeline")
    encode_pipeline.add("appsrc", "src", {"caps": f"video/x-raw(memory:NVMM), format=RGB, width={width}, height={height}, framerate=30/1", "do-timestamp": True})
    encode_pipeline.add("nvvideoconvert", "convert", {"nvbuf-memory-type": 2, "compute-hw": 1})
    encode_pipeline.add("nvstreammux", "mux", {"batch-size": 1, "width": width, "height": height})    
    encode_pipeline.add("nvvideoconvert", "convert1", {"nvbuf-memory-type": 2, "compute-hw": 1})
    encode_pipeline.add("h264parse", "parser")
    encode_pipeline.add("qtmux", "qtmux")
    encode_pipeline.add("filesink", "sink", {"location": output_filename + ".mp4"})

    if (platform.machine() == "aarch64" or os.path.exists("/dev/v4l2-nvenc")):
        encode_pipeline.add("nvv4l2h264enc", "encoder")
        encode_pipeline.add("capsfilter", "capsfilter", {"caps": "video/x-raw(memory:NVMM), format=NV12"})
        #encode_pipeline.add("nvvideoencfilesinkbin", "sink", {"output-file": output_filename + ".mp4", "enc-type": 0, "bitrate": 80000000, "container":1})
    else:
        encode_pipeline.add("x264enc", "encoder")
        encode_pipeline.add("capsfilter", "capsfilter", {"caps": "video/x-raw, format=I420"})
        #encode_pipeline.add("nvvideoencfilesinkbin", "sink", {"output-file": output_filename + ".mp4", "enc-type": 1, "bitrate": 80000000, "container":1})

    #encode_pipeline.link("src", "convert").link(("convert", "mux"), ("", "sink_%u")).link("mux", "encoder").link("encoder", "parser").link("parser", "qtmux").link("qtmux", "sink")
    encode_pipeline.link("src", "convert").link(("convert", "mux"), ("", "sink_%u")).link("mux", "convert1").link("convert1", "capsfilter").link("capsfilter", "encoder").link("encoder", "parser").link("parser", "qtmux").link("qtmux", "sink")
    encode_pipeline.attach("src", Feeder("feeder", MyBufferProvider(event_buffers_list)), tips="need-data/enough-data")
    print("Starting encode pipeline")
    encode_pipeline.start().wait()
    file_name = output_filename + ".mp4"
    encode_pipeline.stop()
    encode_pipeline = None

    # Write output filename to info.txt in append mode
    info_file_path = os.path.join(output_dir, "info.txt")
    try:
        with open(info_file_path, "a") as info_file:
            info_file.write(f"{output_filename}\n")
        print(f"Output filename written to info.txt: {output_filename}")
    except Exception as e:
        print(f"Error writing to info.txt: {e}")

    if os.path.exists(file_name):
        Gst.info(f"Video file created successfully: {file_name}")
        print(f"Video file created successfully: {file_name}")
        # Send curl POST request for alert verification
        if vss_server_url is not None:
            send_alert_verification(vss_server_url, file_name, output_filename + ".json")


class NvDsCacheEvent(GstBase.BaseTransform):
    """
    Advanced NVIDIA DeepStream Cache Event Plugin with Intelligent Buffer Management.

    This plugin implements sophisticated buffer management and temporal frame control
    capabilities for NVIDIA DeepStream SDK applications. It provides intelligent
    frame queuing, selective processing based on cache-data events, and advanced
    frame replacement logic for video editing and analytics applications.

    The plugin operates in multiple modes:
    - **Queue Mode**: Maintains a rolling buffer cache for temporal processing
    - **PTS Mode**: Processes frames within specific timestamp windows around events
    - **Frame Mode**: Processes frames within specific frame number windows around events

    Core Functionality:
    - Intelligent buffer queue management with configurable size limits
    - Frame-accurate processing windows based on cache-data events
    - Advanced frame replacement logic for video editing applications
    - Pad probe integration for precise frame flow control
    - Custom cache-data event handling for upstream/downstream communication
    - Hardware-accelerated NVMM memory format processing
    - Direct encoding pipeline integration for captured segments

    Cache-Data Event Processing Workflow:
    1. Continuously buffers incoming frames in a managed queue system
    2. Maintains all frames in queue until cache-data event is received
    3. Upon receiving cache-data event, processes frames within configured windows
    4. Provides frames from before and after the event trigger point
    5. Performs frame replacement operations during the processing window
    6. Initiates encoding pipeline for captured frames when window is complete
    7. Continues main processing after encoding is finished

    Event Window Processing:
    - Receives cache-data events with trigger points (frame number or PTS)
    - Configures processing windows around the trigger point
    - Processes frames from (trigger - window) to (trigger + window)
    - Handles both frame-based and timestamp-based windowing
    - Automatically initiates encoding when window processing is complete

    Properties:
        queue-size (int): Size of the buffer cache queue (1-500, default: 50)
                         Controls memory usage and temporal processing capability
        vss-server-url (str): URL of the VSS server (default: http://localhost:8000/verifyAlert)
        output-folder (str): Folder path where output files will be saved (default: /tmp/via/output)

    Custom Events:
        cache-data: Simple event to trigger frame processing
            No parameters required - uses current frame position as trigger point

    Window Configuration:
        num_buffers_before_event (int): Number of frames to process before trigger (default: 150)
        num_buffers_after_event (int): Number of frames to process after trigger (default: 300)
        pts_before_event (int): Time window before trigger in nanoseconds (default: 5s)
        pts_after_event (int): Time window after trigger in nanoseconds (default: 10s)
    """

    # Plugin metadata for gst-inspect-1.0
    __gstmetadata__ = (
        "NvDsCacheEvent",
        "Generic/Filter",
        "NVIDIA Deep Stream segmentor plugin with cache-data event processing - implements intelligent buffer queue management and temporal frame control for video analytics applications with event-driven processing windows",
        "NVIDIA Corporation. Post on DeepStream forum for queries @ "
        "https://forums.developer.nvidia.com/c/accelerated-computing/intelligent-video-analytics/",
    )

    # Custom signal definitions
    __gsignals__ = {
        "process-data": (
            GObject.SignalFlags.RUN_LAST,           # Signal flags
            GObject.TYPE_NONE,                      # Return type (void)
            (GObject.TYPE_STRING, GObject.TYPE_INT64, GObject.TYPE_INT64),  # Parameters: (mode, start_value, end_value)
        ),
    }

    # Pad Template Definition
    # Used for defining the capabilities of the inputs and outputs.
    # Description of a pad that the element will create and use. it contains:
    #    - A short name for the pad.
    #    - Pad direction.
    #    - Existence property. This indicates whether the pad exists always
    #      (an "always" pad), only in some cases (a "sometimes" pad) or only if
    #      the application requested such a pad (a "request" pad).
    #    - Supported types/formats by this element (capabilities).
    src_format = Gst.Caps.from_string(f"video/x-raw(memory:NVMM),format=RGB")
    sink_format = Gst.Caps.from_string(f"video/x-raw(memory:NVMM),format=RGB")

    src_pad_template = Gst.PadTemplate.new(
        "src",
        Gst.PadDirection.SRC,
        Gst.PadPresence.ALWAYS,
        src_format
    )
    sink_pad_template = Gst.PadTemplate.new(
        "sink",
        Gst.PadDirection.SINK,
        Gst.PadPresence.ALWAYS,
        sink_format
    )
    __gsttemplates__ = (src_pad_template, sink_pad_template)

    # Property definitions with proper validation
    __gproperties__ = {
        "queue-size": (
            int,                                                              # type
            "Queue Size",                                                     # nick
            "Size of the buffer cache queue",                                 # blurb
            MIN_QUEUE_SIZE,                                                   # min
            MAX_QUEUE_SIZE,                                                   # max
            DEFAULT_QUEUE_SIZE,                                               # default
            GObject.ParamFlags.READWRITE,                                     # flags
        ),
        "vss-server-url": (
            str,
            "VSS Server URL",
            "URL of the VSS server",
            "http://localhost:8000/verifyAlert",
            GObject.ParamFlags.READWRITE,
        ),
        "output-folder": (
            str,
            "Output Folder",
            "Folder path where the encoded clip files will be saved",
            "/tmp/via/output",
            GObject.ParamFlags.READWRITE,
        ),
        "streamname": (
            str,
            "Stream Name",
            "Name of the stream which is used for writing the output clip name",
            "",
            GObject.ParamFlags.READWRITE,
        ),
        "clip-cache-pre-ev-time": (
            int,
            "Clip Cache Pre Event Time",
            "Time window before event in seconds for clip caching",
            0,                                                                # min
            100,                                                              # max (100 seconds)
            5,                                                                # default (5 seconds)
            GObject.ParamFlags.READWRITE,
        ),
        "clip-cache-post-ev-time": (
            int,
            "Clip Cache Post Event Time",
            "Time window after event in seconds for clip caching",
            0,                                                                # min
            100,                                                              # max (100 seconds)
            20,                                                               # default (20 seconds)
            GObject.ParamFlags.READWRITE,
        ),
    }

    def __init__(self) -> None:
        """
        Initialize the NvDsCacheEvent instance.

        This constructor is called once per element instance creation and
        sets up the initial state for the advanced buffer management and
        frame control plugin. It initializes all internal state variables,
        configures default property values, and prepares the plugin for
        GStreamer pipeline integration.

        The method initializes:
        - Plugin properties (queue_size) with default values
        - Video format placeholders (width, height, format, framerate)
        - Runtime state will be initialized later in do_start()
        - Buffer queue system preparation
        - Processing mode configuration

        Note:
            This method should not perform any heavy initialization or
            resource allocation. Use do_start() for runtime initialization
            including buffer queue setup and pad probe installation.
        """
        GstBase.BaseTransform.__init__(self)

        # Initialize properties with default values
        self.queue_size: int = DEFAULT_QUEUE_SIZE
        self.vss_server_url: str = None
        self.output_folder: str = "/tmp/via/output"
        self.streamname: str = ""
        self.clip_cache_pre_ev_time: int = 5  # 5 seconds default
        self.clip_cache_post_ev_time: int = 20  # 20 seconds default
        self.encode_join_threads: Optional[threading.Thread] = None
        self.encode_join_threads_running: bool = False

        # Video format information (populated in do_set_caps)
        self.width: Optional[int] = None
        self.height: Optional[int] = None
        self.format: Optional[str] = None
        self.framerate: Optional[Gst.Fraction] = None
        self.encode_threads: Optional[list[threading.Thread]]=[]
        self._cuda_stream = torch.cuda.Stream(device=torch.device("cuda"))

    def do_start(self) -> bool:
        """
        Start the plugin and initialize runtime state and cache-data event processing system.

        Called when the element transitions from READY to PAUSED state.
        This method initializes the complete runtime environment including
        buffer queue management, pad probes, and cache-data event processing
        state variables. It's the proper place to allocate resources and
        prepare for frame processing.

        Runtime State Initialization:
        - buffer_count: Counter for processed buffers
        - eos_received: Flag to track EOS event status
        - buffer_queue: Deque for intelligent buffer caching
        - processed_buffer_count: Counter for processed frames
        - Cache-data event processing parameters (PTS and frame number ranges)

        Cache-Data Event Processing Setup:
        - event_received: Flag indicating if cache-data event was received
        - event_buffers_window: Frame window size for event processing (default: 100 frames)
        - event_pts_window: PTS window size for event processing (default: 10 seconds)
        - event_mode: Processing mode ("Frame" or "PTS")
        - event_pts/event_buffer_count: Trigger point for event processing

        Pad Probe Configuration:
        - Sink pad probe: Handles custom cache-data events for processing configuration

        Returns:
            bool: True if the element started successfully and all resources
                  are properly initialized, False otherwise.
        """
        # Initialize runtime state variables - these reset each time the pipeline restarts
        self.buffer_count: int = 0
        self.eos_received: bool = False
        self.buffer_pts: int = 0

        # Initialize the buffer queue WITHOUT fixed size so we can control dequeuing
        self.buffer_queue: deque[BufferData] = deque()
        self.processed_buffer_count: int = 0
        self.num_buffers_before_event: int = 100
        self.num_buffers_after_event: int = 100
        self.pts_before_event: int = self.clip_cache_pre_ev_time * 1_000_000_000  # Convert seconds to nanoseconds
        self.pts_after_event: int = self.clip_cache_post_ev_time * 1_000_000_000  # Convert seconds to nanoseconds

        # list of buffers to be processed by the encode pipeline
        self.event_buffers_list: list[BufferData] = []

        # Event parameters
        self.event_received: bool = False
        self.event_number: int = 0
        self.event_buffers_window: int = self.num_buffers_before_event + self.num_buffers_after_event
        self.event_pts_window: int = self.pts_before_event + self.pts_after_event
        self.event_mode: str = "PTS"
        self.event_pts: int = None
        self.event_buffer_count: int = None
        self.stop_event_received: bool = False

        # Add event probe to the sink pad to receive custom events from upstream
        self.sink_pad: Gst.Pad = self.get_static_pad("sink")
        self.sink_pad.add_probe(Gst.PadProbeType.EVENT_BOTH, self.sink_pad_probe)
        self.encode_join_threads_running = True
        self.encode_join_threads = threading.Thread(
                    target=encode_join_function,
                    args=(self,),
                    daemon=False # Make it a daemon thread so it doesn't block program exit
                )
        self.encode_join_threads.start()

        Gst.info(f"{GST_PLUGIN_NAME} plugin started successfully")
        return True

    def do_get_property(self, prop: GObject.ParamSpec) -> Any:
        """
        Retrieve the value of a specified property.

        Args:
            prop: Property specification containing name, type, and flags

        Returns:
            The current value of the requested property

        Raises:
            AttributeError: If the property name is not recognized
        """
        if prop.name == "queue-size":
            return self.queue_size
        elif prop.name == "vss-server-url":
            return self.vss_server_url
        elif prop.name == "output-folder":
            return self.output_folder
        elif prop.name == "streamname":
            return self.streamname
        elif prop.name == "clip-cache-pre-ev-time":
            return self.clip_cache_pre_ev_time
        elif prop.name == "clip-cache-post-ev-time":
            return self.clip_cache_post_ev_time
        else:
            raise AttributeError(f"{GST_PLUGIN_NAME}: Unknown property '{prop.name}'")

    def do_set_property(self, prop: GObject.ParamSpec, value: Any) -> None:
        """
        Set the value of a specified property with validation.

        Args:
            prop: Property specification containing name, type, and flags
            value: New value to set for the property

        Raises:
            AttributeError: If the property name is not recognized
            ValueError: If the property value is invalid
        """
        if prop.name == "queue-size":
            if not isinstance(value, int):
                raise ValueError(f"{GST_PLUGIN_NAME}: Queue-size must be integer, got '{type(value)}'")
            if value < MIN_QUEUE_SIZE or value > MAX_QUEUE_SIZE:
                raise ValueError(
                    f"{GST_PLUGIN_NAME}: Queue-size must be between {MIN_QUEUE_SIZE} and "
                    f"{MAX_QUEUE_SIZE}, got '{value}'"
                )
            self.queue_size = value
            Gst.info(f"Queue Size: {self.queue_size}")

        elif prop.name == "vss-server-url":
            if not isinstance(value, str):
                raise ValueError(f"{GST_PLUGIN_NAME}: VSS server URL must be string, got '{type(value)}'")
            self.vss_server_url = value
            Gst.info(f"VSS Server URL: {self.vss_server_url}")
        elif prop.name == "output-folder":
            if not isinstance(value, str):
                raise ValueError(f"{GST_PLUGIN_NAME}: Output folder must be string, got '{type(value)}'")
            self.output_folder = value
            Gst.info(f"Output Folder: {self.output_folder}")
        elif prop.name == "streamname":
            if not isinstance(value, str):
                raise ValueError(f"{GST_PLUGIN_NAME}: Stream name must be string, got '{type(value)}'")
            self.streamname = value
            Gst.info(f"Stream Name: {self.streamname}")
        elif prop.name == "clip-cache-pre-ev-time":
            if not isinstance(value, int):
                raise ValueError(f"{GST_PLUGIN_NAME}: Clip cache pre event time must be integer, got '{type(value)}'")
            if value < 0 or value > 100:
                raise ValueError(f"{GST_PLUGIN_NAME}: Clip cache pre event time must be between 0 and 100 seconds, got '{value}'")
            self.clip_cache_pre_ev_time = value
            Gst.info(f"Clip Cache Pre Event Time: {self.clip_cache_pre_ev_time} seconds")
        elif prop.name == "clip-cache-post-ev-time":
            if not isinstance(value, int):
                raise ValueError(f"{GST_PLUGIN_NAME}: Clip cache post event time must be integer, got '{type(value)}'")
            if value < 0 or value > 100:
                raise ValueError(f"{GST_PLUGIN_NAME}: Clip cache post event time must be between 0 and 100 seconds, got '{value}'")
            self.clip_cache_post_ev_time = value
            Gst.info(f"Clip Cache Post Event Time: {self.clip_cache_post_ev_time} seconds")
        else:
            raise AttributeError(f"{GST_PLUGIN_NAME}: Attempt to set unknown property '{prop.name}'")

    def do_set_caps(self, incaps: Gst.Caps, outcaps: Gst.Caps) -> bool:
        """
        Configure the plugin based on input and output capabilities.

        Extracts video format information from input caps and stores it
        for use during buffer processing and queue management. This method
        is called when the pipeline negotiates capabilities and is critical
        for proper DeepStream metadata handling, NVMM memory format processing,
        and buffer queue sizing calculations.

        The method validates that the input caps contain valid video format
        information and extracts essential parameters needed for buffer
        processing, including dimensions, format, and framerate. This information
        is used for tensor shape validation and buffer queue memory management.

        Args:
            incaps (Gst.Caps): Input capabilities describing the incoming stream format.
                              Must contain valid video/x-raw(memory:NVMM) format information.
            outcaps (Gst.Caps): Output capabilities describing the outgoing stream format.
                               Should match input caps for passthrough operations.

        Returns:
            bool: True if capabilities are successfully processed and video format
                  information is extracted for buffer management, False otherwise.

        Raises:
            ValueError: If input caps are empty or do not contain valid video format structure.
        """
        if incaps.get_size() == 0:
            raise ValueError(f"{GST_PLUGIN_NAME}: Input caps is empty")

        struct = incaps.get_structure(0)
        if not struct:
            raise ValueError(f"{GST_PLUGIN_NAME}: Could not get structure from input caps")

        # Extract video format information with error checking
        self.width = struct.get_int("width").value
        self.height = struct.get_int("height").value
        self.format = struct.get_string("format")
        self.framerate = struct.get_fraction("framerate")

        # Log the video format information
        Gst.info(f"Width: {self.width}")
        Gst.info(f"Height: {self.height}")
        Gst.info(f"Format: {self.format}")
        Gst.info(f"Framerate: {self.framerate}")

        return True

    def send_eos_downstream(self) -> Gst.FlowReturn:
        """
        Send EOS event downstream and return EOS flow status.

        This method handles the complete EOS workflow:
        1. Sets the EOS received flag
        2. Creates and sends EOS event downstream
        3. Returns appropriate flow return value

        Returns:
            Gst.FlowReturn.EOS: Always returns EOS to signal completion
        """
        # Get source pad and push EOS event downstream
        src_pad = self.get_static_pad("src")
        if src_pad:
            result = src_pad.push_event(Gst.Event.new_eos())
            if result:
                self.eos_received = True
                self.queue_clear()
                Gst.info("EOS event sent downstream successfully")
            else:
                raise ValueError(f"{GST_PLUGIN_NAME}: Failed to push EOS event downstream")
        else:
            raise ValueError(f"{GST_PLUGIN_NAME}: Could not get source pad to send EOS event")


        return Gst.FlowReturn.EOS

    def sink_pad_probe(self, pad, info):
        """
        Sink pad event probe callback for receiving custom 'cache-data' events from upstream elements.

        This probe is called for each event passing through the sink pad and specifically
        looks for custom 'cache-data' events sent from upstream elements. When such an
        event is detected, it uses the current buffer's frame number and PTS as the
        trigger point for processing window configuration.

        Event Processing Logic:
        - Receives custom 'cache-data' events from upstream elements
        - Uses current buffer_count as trigger frame number
        - Uses current buffer_pts as trigger PTS
        - Processes events with structured data format
        - Updates buffer queue to contain only relevant frames within the event window
        - Enables frame processing once the event is received

        Args:
            pad: The sink pad receiving the event
            info: Probe information containing the event data

        Returns:
            Gst.PadProbeReturn.OK: Pass the event downstream and continue processing
        """
        try:
            if info.type & (Gst.PadProbeType.EVENT_DOWNSTREAM | Gst.PadProbeType.EVENT_UPSTREAM):
                event = info.get_event()
                if not event:
                    return Gst.PadProbeReturn.OK

                # Check if this is a custom event with our "cache-data" structure
                if event.type == Gst.EventType.CUSTOM_DOWNSTREAM:
                    try:
                        structure = event.get_structure()
                        if structure and structure.get_name() == "cache-data" and self.event_received == False:
                            self.event_buffer_count = self.buffer_count -1
                            self.event_pts = self.buffer_pts
                            self.event_number += 1
                            Gst.info(f"*** cache-data event-{self.event_number} received at #{self.buffer_count -1} PTS:{self.event_pts / 1_000_000_000:.3f}s ***")
                            if self.event_mode == "PTS":
                                Gst.info(f"Processing Buffers: {(self.event_pts - self.pts_before_event)/1_000_000_000:.3f}s .... {(self.event_pts + self.pts_after_event)/1_000_000_000:.3f}s")
                            elif self.event_mode == "Frame":
                                Gst.info(f"Processing Buffers: #{self.event_buffer_count - self.num_buffers_before_event} .... #{self.event_buffer_count + self.num_buffers_after_event}")
                            # Update the queue to contain only frames within the event window
                            self.update_event_buffer_list()
                            self.event_received = True
                        elif structure and structure.get_name() == "stop-cache-data" and self.event_received == True:
                            self.stop_event_received = True
                            Gst.info(f"*** stop-cache-data event received ***")
                        else:
                            Gst.warning(f"Failed to extract value from cache-data event structure")
                    except Exception as e:
                        Gst.warning(f"Error processing cache-data event structure: {e}")

        except Exception as e:
            Gst.error(f"Error in sink pad event probe: {e}")

        return Gst.PadProbeReturn.OK

    def update_event_buffer_list(self) -> None:
        """
        Update the event buffer list to contain only frames within the event processing window.

        This method manages the event buffer list to ensure it contains only the frames
        that are within the configured processing window around the event trigger point.
        The window includes frames both before and after the event occurrence.

        Processing Window Logic:
        - For Frame Mode: Keeps frames from (trigger_frame - window) to (trigger_frame + window)
        - For PTS Mode: Keeps frames from (trigger_pts - window) to (trigger_pts + window)
        - Removes frames outside the processing window to optimize memory usage
        - Maintains chronological order of frames in the queue

        Window Management:
        - Removes frames that are too old (before the start of the window)
        - Preserves frames that will be needed for processing
        - Ensures efficient memory usage by limiting queue size

        Returns:
            None
        """
        if not self.buffer_queue:
            Gst.info("Queue is empty, no frames to update")
            return

        if self.event_mode == "Frame":
            # Calculate the start of the processing window (frames before the event)
            window_start_frame = self.event_buffer_count - self.num_buffers_before_event
            # Get frames that are before the processing window start
            index = 0
            while index < len(self.buffer_queue):
                if self.buffer_queue[index].frame_number >= window_start_frame:
                    break
                index += 1

        elif self.event_mode == "PTS":
            # Calculate the start of the processing window (time before the event)
            window_start_pts = self.event_pts - self.pts_before_event
            # Get frames that are before the processing window start
            index = 0
            while index < len(self.buffer_queue):
                if self.buffer_queue[index].pts >= window_start_pts:
                    break
                index += 1

        else:
            Gst.warning(f"Unknown event mode: {self.event_mode}")
            return

        # Update the event buffer list with valid frames
        # Convert deque to list for slicing operation
        queue_list = list(self.buffer_queue)
        self.event_buffers_list.extend(queue_list[index:])

        # Log the event buffer list state
        if self.event_buffers_list:
            first_buffer = self.event_buffers_list[0]
            last_buffer = self.event_buffers_list[-1]
            Gst.info(f"Event Buffer List Status => Buffer #{first_buffer.frame_number} to #{last_buffer.frame_number} "
                    f"(PTS: {first_buffer.pts / 1_000_000_000:.3f}s to {last_buffer.pts / 1_000_000_000:.3f}s)")
        else:
            Gst.info("Event Buffer List is empty after update")

        # log the queue state
        if self.buffer_queue:
            first_buffer = self.buffer_queue[0]
            last_buffer = self.buffer_queue[-1]
            Gst.info(f"Buffer Queue Status => Buffer #{first_buffer.frame_number} to #{last_buffer.frame_number} "
                    f"(PTS: {first_buffer.pts / 1_000_000_000:.3f}s to {last_buffer.pts / 1_000_000_000:.3f}s)")
        else:
            Gst.info("Buffer Queue is empty after update")

    def queue_clear(self) -> None:
        """
        Clear the buffer queue.
        """
        while self.buffer_queue:
            self.buffer_queue.popleft()
        Gst.info("Buffer Queue is Cleared")

    def do_transform_ip(self, gst_buffer: Gst.Buffer) -> Gst.FlowReturn:
        """
        Process the input buffer in-place with advanced buffer queue management.

        This method implements sophisticated buffer processing with intelligent
        queue management, frame replacement logic, and temporal processing
        capabilities. It demonstrates the complete workflow for DeepStream
        buffer processing with advanced features:

        Core Processing Pipeline:
        1. Converting Gst.Buffer to PyServiceMaker Buffer for DeepStream access
        2. Extracting batch metadata (NvDsBatchMeta) from the buffer
        3. Iterating through frame metadata (NvDsFrameMeta) in the batch
        4. Accessing individual frame data from NvBufSurface
        5. Converting frame data to PyTorch tensors for processing
        6. Advanced buffer queue management and frame replacement logic

        Buffer Queue Management:
        - Maintains intelligent buffer cache with configurable queue size
        - Performs frame replacement based on processing windows
        - Implements rolling buffer system for temporal processing
        - Handles queue overflow with automatic cleanup

        Frame Processing Logic:
        - Checks processing windows for frame inclusion/exclusion
        - Retrieves queued frames for replacement operations
        - Stacks frame tensors for batch processing
        - Manages frame metadata and timing information

        Temporal Control:
        - Processes frames within configured PTS or frame number windows
        - Handles EOS signaling when processing window is complete
        - Supports frame-accurate video processing workflows

        Args:
            gst_buffer (Gst.Buffer): Input buffer containing video data to be processed.
                                   Must contain valid DeepStream metadata and NVMM memory format.

        Returns:
            Gst.FlowReturn.OK: Buffer processed successfully and added to queue
            Gst.FlowReturn.EOS: End of stream reached (processing window complete)
            Gst.FlowReturn.ERROR: Processing error occurred (handled by base class)

        Note:
            This method implements advanced buffer management and is not a simple
            template. The frame replacement logic and queue management are core
            features of this segmentor plugin.
        """
        # Check if EOS has already been received
        if self.eos_received:
            return Gst.FlowReturn.EOS

        # Basic buffer information logging
        Gst.info(f"Buffer #{self.buffer_count} PTS={gst_buffer.pts / 1_000_000_000:.3f}s")

        # Convert the Gst.Buffer to a PyServiceMaker Buffer object
        buffer = Buffer(gst_buffer)

        # Get the batch metadata from the buffer
        batch_meta = buffer.batch_meta

        # If the buffer didn't contain NvDsFrameMeta
        if batch_meta.n_frames == 0:
            Gst.warning("NvDsFrameMeta not found in the buffer")
            return Gst.FlowReturn.OK

        # Iterate over each frame metadata in the batch
        bacthed_frame_data = []
        for frame_meta in batch_meta.frame_items:
            # Access the frame data from the NvBufSurface
            frame_data_tensor = buffer.extract(frame_meta.batch_id)
            torch_frame_data = torch.utils.dlpack.from_dlpack(frame_data_tensor)
            if torch_frame_data is None or torch_frame_data.numel() == 0:
                continue
            bacthed_frame_data.append(torch_frame_data)

        if len(bacthed_frame_data) == 0:
            Gst.warning("No valid frames extracted from batch; skipping enqueue")
            return Gst.FlowReturn.OK

        # Stack individual frame tensors into a single batched tensor
        with torch.cuda.stream(self._cuda_stream):
            batched_tensors = torch.stack(bacthed_frame_data, dim=0)
            batched_tensors_cpu = batched_tensors.contiguous().to("cpu", non_blocking=True)
        #if batched_tensors.is_cuda:
            # Ensure all device work is complete before copying to CPU
         #   torch.cuda.synchronize()

        torch.cuda.current_stream().wait_stream(self._cuda_stream)

        cvmetadataObject = JsonCVMetadata()

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                metadata = cvmetadataObject.write_frame(frame_meta)
                l_frame = l_frame.next
            except StopIteration:
                break


        current_buffer_data = BufferData(self.buffer_count, gst_buffer.pts, gst_buffer.dts, gst_buffer.duration, batched_tensors_cpu, metadata)

        # Push current buffer data to the queue
        self.buffer_queue.append(current_buffer_data)

        if self.event_received:
            self.event_buffers_list.append(current_buffer_data)

        # Remove oldest buffer if queue exceeds maximum size
        if len(self.buffer_queue) > self.queue_size:
            Gst.debug(f"Queue is full, Removing buffer #{self.buffer_queue[0].frame_number} with PTS={self.buffer_queue[0].pts/1_000_000_000:.3f}s")
            self.buffer_queue.popleft()

        # End of event window reached - send EOS when processing window is complete
        if self.event_received:
            window_end_reached = False

            if self.event_mode == "Frame":
                # Calculate the end of the processing window (frames after the event)
                window_end_frame = self.event_buffer_count + self.num_buffers_after_event
                if self.buffer_count >= window_end_frame:
                    window_end_reached = True
                    Gst.info(f"Frame window processing complete: Current frame #{self.buffer_count} reached window end #{window_end_frame}")

            elif self.event_mode == "PTS":
                # Calculate the end of the processing window (time after the event)
                window_end_pts = self.event_pts + self.pts_after_event
                if gst_buffer.pts >= window_end_pts:
                    window_end_reached = True
                    Gst.info(f"PTS window processing complete: Current PTS {gst_buffer.pts / 1_000_000_000:.3f}s reached window end {window_end_pts / 1_000_000_000:.3f}s")

            if window_end_reached or self.stop_event_received:
                self.event_received = False
                self.stop_event_received = False
                # Run the encode pipeline to process captured frames in a separate thread
                Gst.info(f"Calling encode pipeline with {len(self.event_buffers_list)} buffers")

                # Create a copy of the event buffers list for the thread
                event_buffers_copy = self.event_buffers_list.copy()
                start_pts = event_buffers_copy[0].pts
                end_pts = event_buffers_copy[-1].pts
                output_filename = os.path.join(self.output_folder, f"{self.streamname}_st_{start_pts / 1_000_000_000:.3f}_end_{end_pts / 1_000_000_000:.3f}_clip_{self.event_number}")

                # Delete info.txt file if it exists when event_number=1
                if self.event_number == 1:
                    info_file_path = os.path.join(self.output_folder, "info.txt")
                    if os.path.exists(info_file_path):
                        try:
                            os.remove(info_file_path)
                            print(f"Deleted existing info.txt file: {info_file_path}")
                        except Exception as e:
                            print(f"Error deleting info.txt file: {e}")

                # Start encoding in a separate process (spawned)
                ctx = mp.get_context("spawn")
                encode_process = ctx.Process(
                    target=encode_pipeline_function,
                    args=(event_buffers_copy, self.width, self.height, output_filename, self.vss_server_url),
                    daemon=True,
                    name=f"encode_process_{self.event_number}"
                )
                encode_process.start()
                self.encode_threads.append(encode_process)

                # Clear the original list immediately
                self.event_buffers_list.clear()

        # Update buffer counter
        self.buffer_count += 1
        self.buffer_pts = gst_buffer.pts

        return Gst.FlowReturn.OK

    def do_stop(self) -> bool:
        """
        Clean up resources and reset state when the plugin stops.

        Called when the element transitions from PAUSED to READY state.
        This method cleans up any resources acquired in do_start(),
        resets runtime state variables, clears buffer queues, and logs final
        statistics. It prepares the plugin for potential pipeline restart.

        Returns:
            bool: True if cleanup was successful and plugin is ready
                  for shutdown or restart, False otherwise.
        """
         # Reset runtime state variables
        self.eos_received = False

        # Stop the encode join thread
        self.encode_join_threads_running = False
        if self.encode_join_threads and self.encode_join_threads.is_alive():
            self.encode_join_threads.join(timeout=10.0)
        self.encode_join_threads = None 

        # Clean up buffer queue
        self.queue_clear()

        # Wait for all encode threads to complete
        for thread in self.encode_threads:
            thread.join()
        self.encode_threads.clear()

        print(f"NvDsCacheEvent plugin stopped successfully. Total buffers processed: {self.processed_buffer_count}")
        # Log final statistics before resetting
        Gst.warning(f"{GST_PLUGIN_NAME} plugin stopped successfully. Total buffers processed: {self.processed_buffer_count}")

        return True


# Register the plugin element
GObject.type_register(NvDsCacheEvent)

# Element factory registration tuple
# Format: (factory_name, rank, element_class)
__gstelementfactory__ = (
    GST_PLUGIN_NAME,
    Gst.Rank.NONE,  # Use NONE for custom plugins, PRIMARY for system plugins
    NvDsCacheEvent,
)
