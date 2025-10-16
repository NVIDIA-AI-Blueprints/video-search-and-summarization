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
from gi.repository import Gst, GObject, GstBase, GLib

import torch
import numpy as np
#from pyservicemaker import Buffer, BufferProvider, as_tensor, ColorFormat, Pipeline, Feeder
import pyds
from typing import Any, Optional
from collections import deque
from multiprocessing import Process
from perf import PERF_DATA

# Initialize GStreamer
Gst.init(None)

# Plugin constants
GST_PLUGIN_NAME = "eventgenerator"
DEFAULT_QUEUE_SIZE = 300
MIN_QUEUE_SIZE = 1
MAX_QUEUE_SIZE = 500
PGIE_CLASS_ID_VEHICLE = 0
PGIE_CLASS_ID_BICYCLE = 1
PGIE_CLASS_ID_PERSON = 2
PGIE_CLASS_ID_ROADSIGN = 3

class EventGenerator(GstBase.BaseTransform):
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
        "EventGenerator",
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

        # Video format information (populated in do_set_caps)
        self.width: Optional[int] = None
        self.height: Optional[int] = None
        self.format: Optional[str] = None
        self.framerate: Optional[Gst.Fraction] = None

        self.cache_data_event_sent: bool = False
        self.cache_frame_number: int = 0
        self.frame_number: int = 0
        self._gpu_id: int = 0

        np.random.seed(1000)
        self.rgb_array = np.random.random((1000, 3))
        self.perf_data = None
        self.perf_data = PERF_DATA(1, self._gpu_id, [0])
        GLib.timeout_add(10000, self.perf_data.perf_print_callback)

        
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
        self.frame_num: int = 0

        # Initialize the buffer queue WITHOUT fixed size so we can control dequeuing
        self.processed_buffer_count: int = 0
        self.num_buffers_before_event: int = 150
        self.num_buffers_after_event: int = 300
        self.pts_before_event: int =  5000000000 # 5 seconds
        self.pts_after_event: int =  10000000000  # 10 seconds

        # list of buffers to be processed by the encode pipeline

        # Event parameters
        self.event_received: bool = False
        self.event_number: int = 0
        self.event_buffers_window: int = self.num_buffers_before_event + self.num_buffers_after_event
        self.event_pts_window: int = self.pts_before_event + self.pts_after_event
        self.event_mode: str = "Frame"
        self.event_pts: int = None
        self.event_buffer_count: int = None

        # Add event probe to the sink pad to receive custom events from upstream

        Gst.info(f"{GST_PLUGIN_NAME} plugin started successfully")
        return True

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
                Gst.info("EOS event sent downstream successfully")
            else:
                raise ValueError(f"{GST_PLUGIN_NAME}: Failed to push EOS event downstream")
        else:
            raise ValueError(f"{GST_PLUGIN_NAME}: Could not get source pad to send EOS event")


        return Gst.FlowReturn.EOS

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
        if not gst_buffer:
            Gst.error("gsteventgenerator: Unable to get GstBuffer ")
            return Gst.FlowReturn.ERROR

        # Retrieve batch metadata from the gst_buffer
        # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
        # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        l_frame = batch_meta.frame_meta_list

        while l_frame:
            try:
                # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
                # The casting is done by pyds.NvDsFrameMeta.cast()
                # The casting also keeps ownership of the underlying memory
                # in the C code, so the Python garbage collector will leave
                # it alone.
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break

            stream_index = "stream{0}-{1}".format(frame_meta.pad_index, 0)
            self.perf_data.update_fps(stream_index)
            frame_number=frame_meta.frame_num
            l_obj=frame_meta.obj_meta_list
            num_rects = frame_meta.num_obj_meta
            #print("#"*50)
            obj_list=[]
            while l_obj:
                try: 
                    # Note that l_obj.data needs a cast to pyds.NvDsObjectMeta
                    # The casting is done by pyds.NvDsObjectMeta.cast()
                    obj_meta=pyds.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break

                # Get frame rate through this probe
                
                '''
                obj_meta.text_params.display_text = str(obj_meta.object_id)
                #FIXME: need config parameter
                obj_meta.rect_params.border_width = 0

                rgb_value = self.rgb_array[obj_meta.object_id % 1000]

                obj_meta.rect_params.border_color.set(rgb_value[0], rgb_value[1], rgb_value[2], 1.0)
                obj_meta.text_params.font_params.font_size = int(self.height / 20)

                rgb_value1 = self.rgb_array[999 - obj_meta.object_id % 1000]
                obj_meta.text_params.font_params.font_color.set(
                    rgb_value1[0], rgb_value1[1], rgb_value1[2], 1.0
                )

                #new_obj_meta = pyds.NvDsObjectMeta.cast(
                #    pyds.nvds_acquire_obj_meta_from_pool(batch_meta)
                #)
                #obj_list.append(new_obj_meta)
                new_obj_meta = obj_meta

                new_obj_meta.rect_params = obj_meta.rect_params
                mask_params = pyds.NvOSD_MaskParams.cast(new_obj_meta.mask_params)
                mask_params.threshold = 0.01
                mask_params.width = obj_meta.mask_params.width
                mask_params.height = obj_meta.mask_params.height

                if mask_params.data is None:
                    buffer = mask_params.alloc_mask_array()
                    buffer[:] = obj_meta.mask_params.get_mask_array().reshape(
                        obj_meta.mask_params.height * obj_meta.mask_params.width
                    )
                    # buffer[buffer > 0.1] = 0.45

                # new_obj_meta.rect_params.border_color.set(1.0, 0.0, 0.0, 0.2)
                
                new_obj_meta.rect_params.border_color.set(
                    rgb_value[0], rgb_value[1], rgb_value[2], 0.0
                )
                new_obj_meta.rect_params.border_width = 0
                mask_crop = None
                if obj_meta.mask_params.data is not None:
                    mask_crop = obj_meta.mask_params.get_mask_array().reshape(
                        obj_meta.mask_params.height, obj_meta.mask_params.width
                    )
                center = obj_meta.rect_params.left + obj_meta.rect_params.width/2, obj_meta.rect_params.top + obj_meta.rect_params.height/2
                if mask_crop is not None:
                    center = find_center(mask_crop)
                
                if center is not None:
                    cx, cy = center
                    obj_meta.text_params.x_offset = int(
                            cx - 0 + 0.95 * obj_meta.rect_params.left
                        )
                    obj_meta.text_params.y_offset = int(
                            cy - 0 + 0.95 * obj_meta.rect_params.top
                        )

                print ("#"*50, flush=True)
                print(f"obj_meta.text_params.x_offset: {obj_meta.text_params.x_offset}", flush=True)
                print(f"obj_meta.text_params.y_offset: {obj_meta.text_params.y_offset}", flush=True)    
                print(f"obj_meta.rect_params.left: {obj_meta.rect_params.left}", flush=True)
                print(f"obj_meta.rect_params.top: {obj_meta.rect_params.top}", flush=True)
                print(f"obj_meta.rect_params.width: {obj_meta.rect_params.width}", flush=True)
                print(f"obj_meta.rect_params.height: {obj_meta.rect_params.height}", flush=True)
                print(f"obj_meta.rect_params.border_width: {obj_meta.rect_params.border_width}", flush=True)
                print(f"obj_meta.rect_params.border_color: {obj_meta.rect_params.border_color}", flush=True)
                print(f"obj_meta.text_params.display_text: {obj_meta.text_params.display_text}", flush=True)
                print ("#"*50, flush=True)
                '''

                l_user_meta = obj_meta.obj_user_meta_list
                # Extract object level meta data from NvDsAnalyticsObjInfo
                while l_user_meta:
                    try:
                        user_meta = pyds.NvDsUserMeta.cast(l_user_meta.data)
                        # if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSOBJ.USER_META"):
                        if True:  # FIXME
                            user_meta_data = pyds.NvDsAnalyticsObjInfo.cast(user_meta.user_meta_data)
                            #if user_meta_data.dirStatus: print("Object {0} moving in direction: {1}".format(obj_meta.object_id, user_meta_data.dirStatus))                    
                            #if user_meta_data.lcStatus: 
                            #    print("Object {0} line crossing status: {1}".format(obj_meta.object_id, user_meta_data.lcStatus))
                            #if user_meta_data.ocStatus: print("Object {0} overcrowding status: {1}".format(obj_meta.object_id, user_meta_data.ocStatus))
                            #if user_meta_data.roiStatus: print("Object {0} roi status: {1}".format(obj_meta.object_id, user_meta_data.roiStatus))
                    except StopIteration:
                        break

                    try:
                        l_user_meta = l_user_meta.next
                    except StopIteration:
                        break
                try: 
                    l_obj=l_obj.next
                except StopIteration:
                    break

            for obj_meta in obj_list:
                #print(f"Adding meta {obj_meta.rect_params.left} {obj_meta.rect_params.top}", flush=True)
                pyds.nvds_add_obj_meta_to_frame(frame_meta, obj_meta, None)

            # Get meta data from NvDsAnalyticsFrameMeta
            l_user = frame_meta.frame_user_meta_list
            while l_user:
                try:
                    user_meta = pyds.NvDsUserMeta.cast(l_user.data)
                    if True:  # FIXME
                    # if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSFRAME.USER_META"):
                        user_meta_data = pyds.NvDsAnalyticsFrameMeta.cast(user_meta.user_meta_data)
                        #if user_meta_data.objInROIcnt: print("Objs in ROI: {0}".format(user_meta_data.objInROIcnt))                    
                        #if user_meta_data.objLCCumCnt: print("Linecrossing Cumulative: {0}".format(user_meta_data.objLCCumCnt))
                        src_pad = self.srcpad
                        if user_meta_data.ocStatus['OC']:  
                            if self.cache_data_event_sent is not True:
                                event_structure = Gst.Structure.new_empty("cache-data")
                                custom_event = Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, event_structure)
                                self.cache_event_pts = gst_buffer.pts

                                if src_pad:
                                    result = src_pad.push_event(custom_event)
                                    if result:
                                        print("===================CustomLib: cache-data event for overcrowding sent downstream successfully",flush=True)
                                    else:
                                        print("===================CustomLib: Failed to send cache-data event for overcrowding downstream",flush=True)
                                else:
                                    print("CustomLib: Could not get source pad to send custom event",flush=True)

                                self.cache_frame_number = 1
                                self.cache_data_event_sent = True
                            else:
                                self.cache_event_pts = gst_buffer.pts

                        elif self.cache_data_event_sent and  self.cache_event_pts + 2_000_000_000 <= gst_buffer.pts:
                            event_structure = Gst.Structure.new_empty("stop-cache-data")
                            custom_event = Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, event_structure)
                            if src_pad:
                                result = src_pad.push_event(custom_event)
                                if result:
                                    print("===================CustomLib: stop-cache-data event for overcrowding sent downstream successfully",flush=True)
                                else:
                                    print("===================CustomLib: Failed to send stop-cache-data event for overcrowding downstream",flush=True)
                            self.cache_data_event_sent = False
                            self.cache_frame_number = 0
                            self.cache_event_pts = gst_buffer.pts
                        #elif self.cache_data_event_sent:
                        #    self.cache_frame_number += 1
 
                        '''
                        if user_meta_data.objLCCurrCnt: 
                            #print("===============Linecrossing Current Frame: {0}".format(user_meta_data.objLCCurrCnt))
                            #print("===============cache_frame_number: {0}".format(self.cache_frame_number))
                            if self.cache_frame_number == 0 and user_meta_data.objLCCurrCnt['Exit'] > 0:
                                event_structure = Gst.Structure.new_empty("cache-data")
                                custom_event = Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, event_structure)
                                src_pad = self.srcpad
                                if src_pad:
                                    result = src_pad.push_event(custom_event)
                                    if result:
                                        print("===================CustomLib: cache-data event sent downstream successfully")
                                    else:
                                        print("===================CustomLib: Failed to send cache-data event downstream")
                                else:
                                    print("CustomLib: Could not get source pad to send custom event")

                                self.cache_frame_number += 1
                                self.cache_data_event_sent = True
                        '''
                except StopIteration:
                    break
                try:
                    l_user = l_user.next
                except StopIteration:
                    break
            try:
                l_frame=l_frame.next
            except StopIteration:
                break
            #print("#"*50)

        self.frame_number += 1
        if self.cache_data_event_sent:
            self.cache_frame_number += 1
            #if self.cache_frame_number > 450:
            #    self.cache_data_event_sent = False
            #    self.cache_frame_number = 0

        self.buffer_count += 1
        self.frame_num += 1 # Assuming frame_num also increments with each buffer

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

        # Clean up buffer queue

        # Log final statistics before resetting
        Gst.info(f"{GST_PLUGIN_NAME} plugin stopped successfully. Total buffers processed: {self.processed_buffer_count}")

        return True


# Register the plugin element
GObject.type_register(EventGenerator)

# Element factory registration tuple
# Format: (factory_name, rank, element_class)
__gstelementfactory__ = (
    GST_PLUGIN_NAME,
    Gst.Rank.NONE,  # Use NONE for custom plugins, PRIMARY for system plugins
    EventGenerator,
)



