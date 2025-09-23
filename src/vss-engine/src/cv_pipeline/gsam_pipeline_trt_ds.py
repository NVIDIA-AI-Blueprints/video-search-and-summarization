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

import os
import re
import shutil
import signal
import sys
import time
import uuid
from ctypes import c_char_p, cdll
from functools import partial
from typing import Callable

import cv2
import gi
import numpy as np
import pyds

from file_splitter import ChunkInfo
from utils import JsonCVMetadata, MediaFileInfo, get_json_file_name

from .FPS import PERF_DATA

sys.path.append(os.path.dirname(__file__) + "/..")

gi.require_version("Gst", "1.0")
if True:
    from gi.repository import GLib, Gst


libcudart = cdll.LoadLibrary("libcudart.so")
libcudart.cudaGetErrorString.restype = c_char_p


def cudaSetDevice(device_idx):
    """
    Set the CUDA device to use.

    Args:
        device_idx: Index of the GPU to use

    Returns:
        int: The actual GPU index that was used (may differ from requested)

    Raises:
        RuntimeError: If no GPUs are available
    """

    ret = libcudart.cudaSetDevice(device_idx)
    if ret != 0:
        error_string = libcudart.cudaGetErrorString(ret)
        raise RuntimeError(f"cudaSetDevice: {device_idx} " + str(error_string))

    return device_idx


perf_data = None
UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF
"""
# BN : TBD : use custom context to use the flag ctx_flags.BLOCKING_SYNC
# Requires the context to be passed and made as current context before using
cuda.init()
device = cuda.Device(0)
cuda_driver_context = device.make_context(flags=cuda.ctx_flags.BLOCKING_SYNC)

atexit.register(lambda: cuda_driver_context.pop())
"""


np.random.seed(1000)
rgb_array = np.random.random((1000, 3))


def signal_handler(gsam_pipeline, sig, frame):
    gsam_pipeline.stop()
    print("You pressed Ctrl+C!")
    sys.exit(0)


def get_mask(mask, x1, y1, x2, y2):
    mask = mask.cpu().numpy()
    c, h, w = mask.shape
    mask_image = mask.reshape(h, w, 1).astype(
        np.float32
    )  # * self.color[self._keys[parsed[0]]].reshape(1, 1, -1)
    mask_crop = mask_image[int(y1) : int(y2), int(x1) : int(x2), :]
    return mask_crop


def get_border(mask, border_width=3):
    # Convert mask to uint8 format (0 or 255)
    mask_uint8 = (mask * 255).astype(np.uint8)

    # Perform dilation and erosion
    kernel = np.ones((border_width, border_width), np.uint8)
    dilation = cv2.dilate(mask_uint8, kernel, iterations=1)
    erosion = cv2.erode(mask_uint8, kernel, iterations=1)

    # Subtract the original mask from the eroded mask to get the border
    border = cv2.subtract(dilation, erosion)

    # Convert border to binary format
    border = (border > 0).astype(np.float32)

    h, w = border.shape
    return border.reshape(h, w, 1)


def find_center(mask):
    # Calculate the moments of the mask
    moments = cv2.moments(mask)

    # Calculate the centroid
    if moments["m00"] != 0:
        center_x = int(moments["m10"] / moments["m00"])
        center_y = int(moments["m01"] / moments["m00"])
        return center_x, center_y
    else:
        # If the mask has no area, return None
        return None


def streammux_src_pad_buffer_probe(pad, info, text_prompts):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return

    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))

    pyds.nvds_acquire_meta_lock(batch_meta)

    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting is done by pyds.NvDsFrameMeta.cast()
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            frame_number = frame_meta.frame_num
        except StopIteration:
            break

        for text_prompt in text_prompts:
            user_meta = pyds.nvds_acquire_user_meta_from_pool(batch_meta)

            if user_meta:
                print(f"Adding text prompt user meta for {frame_number}")
                data = pyds.alloc_custom_struct_text_prompt(user_meta)
                data.textPrompt = text_prompt.text
                data.textPrompt = pyds.get_string(data.textPrompt)
                data.objectIdTextPrompt = text_prompt.object_id
                data.threshold = text_prompt.threshold

                user_meta.user_meta_data = data
                user_meta.base_meta.meta_type = pyds.NvDsMetaType.NVDS_USER_META_2

                pyds.nvds_add_user_meta_to_frame(frame_meta, user_meta)
            else:
                print("failed to acquire user meta")

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    pyds.nvds_release_meta_lock(batch_meta)
    return Gst.PadProbeReturn.OK


def tracker_src_pad_buffer_probe(pad, info, u_data):
    # cudaSetDevice(0)
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return
    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            u_data.metadata.write_frame(frame_meta)
        except StopIteration:
            break

        # Get frame rate through this probe
        stream_index = "stream{0}-{1}".format(frame_meta.pad_index, u_data._chunk.chunkIdx)
        global perf_data
        perf_data.update_fps(stream_index)

        l_obj = frame_meta.obj_meta_list
        obj_list = []
        while l_obj is not None:
            try:
                # Casting l_obj.data to pyds.NvDsObjectMeta
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                obj_meta.text_params.display_text = str(obj_meta.object_id)
                if not u_data._draw_bbox:
                    obj_meta.rect_params.border_width = 0

                rgb_value = rgb_array[obj_meta.object_id % 1000]
                # obj_meta.rect_params.border_color.set(*u_data._mask_color)

                obj_meta.rect_params.border_color.set(rgb_value[0], rgb_value[1], rgb_value[2], 1.0)
                obj_meta.text_params.font_params.font_size = int(u_data._pipeline_height / 20)

                rgb_value1 = rgb_array[999 - obj_meta.object_id % 1000]
                obj_meta.text_params.font_params.font_color.set(
                    rgb_value1[0], rgb_value1[1], rgb_value1[2], 1.0
                )

                new_obj_meta = pyds.NvDsObjectMeta.cast(
                    pyds.nvds_acquire_obj_meta_from_pool(batch_meta)
                )

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
                    rgb_value[0], rgb_value[1], rgb_value[2], 0.3
                )
                obj_list.append(new_obj_meta)

                mask_crop = None

                if u_data._center_text_on_object or u_data._mask_border_width:
                    if obj_meta.mask_params.data is not None:
                        mask_crop = obj_meta.mask_params.get_mask_array().reshape(
                            obj_meta.mask_params.height, obj_meta.mask_params.width
                        )

                if u_data._center_text_on_object:
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

                # if u_data._mask_border_width:
                #    mask_crop = get_border(mask_crop, u_data._mask_border_width)
                #    obj_meta.mask_params.get_mask_array()[:] = mask_crop.ravel()

            except StopIteration:
                break

            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        try:
            for obj_meta in obj_list:
                #    print(
                #        f"Adding meta {obj_meta.rect_params.left} {obj_meta.rect_params.top}"
                #    )
                pyds.nvds_add_obj_meta_to_frame(frame_meta, obj_meta, None)
            # indicate inference is performed on the frame
            # frame_meta.bInferDone = True
            l_frame = l_frame.next
        except StopIteration:
            break

        # u_data.metadata.write_past_frame_meta(batch_meta)

    return Gst.PadProbeReturn.OK


class GSAMPipeline:
    def __init__(
        self,
        chunk: ChunkInfo,
        on_gsam_result: Callable[[ChunkInfo, int, any], None],
        pipeline_width=0,
        pipeline_height=0,
        mask_border_width=0,
        mask_color=[0, 1, 0, 0.2],
        center_text_on_object=False,
        output_file=None,
        text_prompts=[],
        is_rgb=False,
        draw_bbox=True,
        tracker_config="",
        inference_interval=0,
        request_id="",
        gpu_id=0,
        batch_size=4,
        buffer_pool_size=20,
        process_id=0,
    ):
        # print(
        #    f"{inspect.currentframe().f_lineno} - Initializing GSAMPipeline {chunk}"
        #    f"gpu_id-{gpu_id} tracker-{tracker_config} "
        #    f"prompts -{text_prompts} interval {inference_interval} "
        #    f"request_id {request_id} batch_size {batch_size} buffer_pool_size {buffer_pool_size}"
        # )

        # Verify GPU availability and adjust gpu_id if needed

        self._gdino_model = None
        if pipeline_width == 0 or pipeline_height == 0:
            stream_width, stream_height = MediaFileInfo.get_info(chunk.file).video_resolution
            self._pipeline_width = stream_width
            self._pipeline_height = stream_height
        else:
            self._pipeline_width = pipeline_width
            self._pipeline_height = pipeline_height
        self._chunk = chunk
        self._output_file = output_file
        self._on_gsam_result = on_gsam_result
        self._text_prompts = text_prompts
        self._is_rgb = is_rgb
        self._mask_border_width = mask_border_width
        self._mask_color = mask_color
        self._center_text_on_object = center_text_on_object
        self._draw_bbox = draw_bbox
        self._inference_interval = inference_interval
        self._frame_no = 0
        self._batch_size = batch_size
        self._buffer_pool_size = buffer_pool_size
        self._request_id = request_id
        self.metadata = JsonCVMetadata(request_id=request_id, chunkIdx=self._chunk.chunkIdx)
        self._gpu_id = gpu_id

        if self._chunk.start_pts < 0:
            self._chunk.start_pts = 0

        if self._chunk.end_pts < 0:
            self._chunk.end_pts = MediaFileInfo.get_info(chunk.file).video_duration_nsec

        pipeline = Gst.Pipeline()
        # uridecodebin has hang issues when combined with nvinfserver, hence cannot be used
        # uridecodebin3 fails in seek_simple, hence cannot be used
        # hence using filesrc and decodebin
        filesrc = Gst.ElementFactory.make("filesrc", None)
        filesrc.set_property("location", os.path.abspath(self._chunk.file))
        nvurisrcbin = Gst.ElementFactory.make("decodebin")
        pipeline.add(filesrc)
        pipeline.add(nvurisrcbin)
        filesrc.link(nvurisrcbin)

        # if self._chunk.start_pts > 0:
        #    nvurisrcbin.set_property("filter-start-time", self._chunk.start_pts)
        # if self._chunk.end_pts >= 0:
        #    nvurisrcbin.set_property("filter-end-time", self._chunk.end_pts)
        nvstreammux = Gst.ElementFactory.make("nvstreammux")
        pipeline.add(nvstreammux)
        nvstreammux.set_property("width", self._pipeline_width)
        nvstreammux.set_property("height", self._pipeline_height)
        nvstreammux.set_property("batch-size", self._batch_size)
        # nvstreammux.set_property("live-source", 0)
        nvstreammux.set_property("batched-push-timeout", -1)
        nvstreammux.set_property("gpu-id", self._gpu_id)
        nvstreammux.set_property("buffer-pool-size", self._buffer_pool_size)

        # videoconvert = Gst.ElementFactory.make("nvvideoconvert")
        # videoconvert.set_property("nvbuf-memory-type", 2)
        # videoconvert.set_property("gpu-id", self._gpu_id)
        # videoconvert.set_property("interpolation-method", 1)
        # pipeline.add(videoconvert)
        queue = Gst.ElementFactory.make("queue")
        pipeline.add(queue)

        # Add a tee, queue and fakesink reuired for seeking
        # decoder -> tee -> queue -> fakesink
        #        |-> queue nvstreammux
        tee = Gst.ElementFactory.make("tee")
        pipeline.add(tee)
        tee_pad = tee.get_static_pad("sink")

        def eos_buffer_probe(pad, info, data):
            buffer = info.get_buffer()
            if buffer.pts < self._chunk.start_pts:
                return Gst.PadProbeReturn.DROP
            if buffer.pts >= self._chunk.end_pts:
                tee_pad.send_event(Gst.Event.new_eos())
                return Gst.PadProbeReturn.DROP
            return Gst.PadProbeReturn.OK

        tee_pad.add_probe(Gst.PadProbeType.BUFFER, eos_buffer_probe, self)

        queue_tee_streammux = Gst.ElementFactory.make("queue")
        pipeline.add(queue_tee_streammux)
        queue_tee_fakesink = Gst.ElementFactory.make("queue")
        pipeline.add(queue_tee_fakesink)
        seek_fakesink = Gst.ElementFactory.make("fakesink")
        seek_fakesink.set_property("async", False)
        seek_fakesink.set_property("sync", False)
        pipeline.add(seek_fakesink)

        capsfilter = Gst.ElementFactory.make("capsfilter")
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"),
        )
        pipeline.add(capsfilter)
        unique_filename = f"/tmp/config_nvinferserver_{uuid.uuid4()}.txt"
        self._unique_filename = unique_filename

        # Copy the file to /tmp with the unique filename
        shutil.copy(
            "/opt/nvidia/TritonGdino/config_triton_nvinferserver_gdino.txt", unique_filename
        )

        # if not os.path.exists("/tmp/nvdsinferserver_custom_impl_gdino/"):
        try:
            shutil.copytree(
                "/opt/nvidia/TritonGdino/nvdsinferserver_custom_impl_gdino/",
                "/tmp/nvdsinferserver_custom_impl_gdino/",
            )
        except Exception as e:
            print(f"Error copying nvdsinferserver_custom_impl_gdino: {e}")
        # else:
        #    print("nvdsinferserver_custom_impl_gdino already exists in /tmp")

        # if not os.path.exists(f"/tmp/TritonGdino_{self._gpu_id}/"):
        if not self._check_required_files(f"/tmp/TritonGdino_{self._gpu_id}/"):
            try:
                # os.makedirs(f"/tmp/TritonGdino_{self._gpu_id}/", exist_ok=True)
                shutil.copytree(
                    "/opt/nvidia/TritonGdino/",
                    f"/tmp/TritonGdino_{self._gpu_id}/",
                    dirs_exist_ok=True,
                    symlinks=True,  # Preserve symlinks
                    ignore_dangling_symlinks=True,  # Don't fail on broken symlinks
                    copy_function=shutil.copy2,  # Preserves metadata and overwrites existing files
                )
            except Exception as e:
                print(f"Error copying TritonGdino: {e}")

        if self._gpu_id != 0:
            try:
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_preprocess/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()
                    modified_content = re.sub(
                        r"gpus:\s*\[\s*0\s*\]", f"gpus: [{self._gpu_id}]", content
                    )
                # Use a regex pattern that allows for optional spaces around the colon and brackets

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_preprocess/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)

                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/" "triton_model_repo/gdino_trt/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()
                    modified_content = re.sub(
                        r"gpus:\s*\[\s*0\s*\]", f"gpus: [{self._gpu_id}]", content
                    )

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/" "triton_model_repo/gdino_trt/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)

                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_postprocess/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()
                    modified_content = re.sub(
                        r"gpus:\s*\[\s*0\s*\]", f"gpus: [{self._gpu_id}]", content
                    )

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_postprocess/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)

            except Exception as e:
                print(f"Error copying TritonGdino: {e}")

        threshold = None
        # Check if last element has confidence threshold
        if self._text_prompts and ";" in self._text_prompts[-1]:
            # Split the last element into text and threshold
            text, threshold = self._text_prompts[-1].split(";")
            # Strip punctuation from text
            text = text.strip()
            # Remove any trailing periods and whitespace from threshold
            threshold = threshold.strip().rstrip(".")
            try:
                threshold = float(threshold)
                # Replace last element with just the text
                self._text_prompts[-1] = text.rstrip(".")
            except ValueError:
                print(f"Warning: Invalid threshold format in prompt: {self._text_prompts[-1]}")

        prompt_text = " . ".join(self._text_prompts) + " . "
        # print (self._text_prompts)

        # Try to find the type_name pattern in the config file content
        with open(unique_filename, "r") as file:
            content = file.read()

        # Pattern to match the entire type_name including optional threshold
        existing_pattern = r'type_name:\s*"([^"]+?)(?:;[0-9]*\.?[0-9]+)?"'
        match = re.search(existing_pattern, content)

        if match:
            # Extract the full matched type_name string
            full_match = match.group(0)

            # Check if the existing type_name has a threshold value (format: "text;threshold")
            if ";" in full_match:
                # Extract existing threshold, removing trailing quote
                existing_threshold = full_match.split(";")[1].rstrip('"')

                # If a new threshold was provided in text_prompts, use it
                if threshold is not None:
                    new_type_name = f'type_name: "{prompt_text.strip()};{threshold}"'
                # Otherwise keep the existing threshold from config
                else:
                    new_type_name = f'type_name: "{prompt_text.strip()};{existing_threshold}"'

            # No threshold in existing type_name
            else:
                # If a new threshold was provided in text_prompts, use it
                if threshold is not None:
                    new_type_name = f'type_name: "{prompt_text.strip()};{threshold}"'
                # No threshold anywhere, use default 0.3
                else:
                    new_type_name = f'type_name: "{prompt_text.strip()};0.3"'

            # Replace the old type_name with the new one, preserving rest of content
            modified_content = re.sub(full_match, new_type_name, content)

        # Could not find type_name pattern in config
        else:
            print("Warning: Could not find type_name pattern in config file")
            modified_content = content  # Keep content unchanged

        # print (modified_content)

        # Update GPU IDs in config from default [0] to specified GPU ID
        modified_content = re.sub(
            r"gpu_ids:\s*\[\s*0\s*\]", f"gpu_ids: [{self._gpu_id}]", modified_content
        )

        # Update device ID from default 0 to specified GPU ID
        modified_content = re.sub(r"device:\s*0", f"device: {self._gpu_id}", modified_content)

        # Update Triton model repository path to use unique temp directory for each GPU
        # This prevents conflicts when multiple instances run on different GPUs
        modified_content = re.sub(
            r"root:\s*\"./triton_model_repo/\"",
            f'root: "/tmp/TritonGdino_{self._gpu_id}/triton_model_repo/"',
            modified_content,
        )

        # Set inference interval - controls how often inference is performed
        # 0 means every frame, N means every Nth frame
        print(f"Setting GDINO Inference interval to : {self._inference_interval}")
        modified_content = re.sub(
            r"interval:\s*0", f"interval: {self._inference_interval}", modified_content
        )

        # Write the modified content back to the file
        with open(unique_filename, "w") as file:
            file.write(modified_content)
        # Set the property to use the modified file
        nvdsinferserver = Gst.ElementFactory.make("nvinferserver")
        pipeline.add(nvdsinferserver)
        nvdsinferserver.set_property("config-file-path", unique_filename)
        nvdsinferserver.set_property("interval", self._inference_interval)

        nvvideoconvert2 = Gst.ElementFactory.make("nvvideoconvert")
        nvvideoconvert2.set_property("gpu-id", self._gpu_id)
        pipeline.add(nvvideoconvert2)

        queue2 = Gst.ElementFactory.make("queue")
        pipeline.add(queue2)

        queue3 = Gst.ElementFactory.make("queue")
        pipeline.add(queue3)
        if tracker_config == "":
            nvtracker = Gst.ElementFactory.make("queue")
        else:
            nvtracker = Gst.ElementFactory.make("nvtracker")
            nvtracker.set_property("user-meta-pool-size", 256)
            nvtracker.set_property(
                "ll-lib-file",
                "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so",
            )
            nvtracker.set_property("gpu-id", self._gpu_id)
            nvtracker.set_property(
                "ll-config-file",
                tracker_config,
                #    "/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_perf.yml",
            )

        pipeline.add(nvtracker)
        nvtrackersrcpad = nvtracker.get_static_pad("src")
        nvtrackersrcpad.add_probe(Gst.PadProbeType.BUFFER, tracker_src_pad_buffer_probe, self)

        def cb_newpad(nvurisrcbin, nvurisrcbin_pad, data_dict, tee):
            print("Decode callback")
            sinkpad = tee.get_compatible_pad(nvurisrcbin_pad, None)
            if sinkpad is not None:
                nvurisrcbin_pad.link(sinkpad)
            else:
                print("No compatible pad found to link nvurisrcbin_pad")

        data_dict = {}
        nvurisrcbin.connect("pad-added", cb_newpad, data_dict, tee)

        def cb_elem_added(elem, username, password):
            if "nvv4l2decoder" in elem.get_factory().get_name():
                elem.set_property("gpu-id", self._gpu_id)
                # BN : TBD : for long videos : chunk 0 processes first 4 frames twice
                # the first 4 buffers are not released
                # Hence this is a hack. Add 4 more buffers
                # Needs to be fixed
                elem.set_property("num-extra-surfaces", 8)

        nvurisrcbin.connect(
            "deep-element-added",
            lambda bin, subbin, elem, username="", password="": cb_elem_added(
                elem, username, password
            ),
        )

        tee.link(queue_tee_streammux)
        queue_tee_streammux_src_pad = queue_tee_streammux.get_static_pad("src")
        mux_sinkpad = nvstreammux.request_pad_simple("sink_0")
        queue_tee_streammux_src_pad.link(mux_sinkpad)

        tee.link(queue_tee_fakesink)
        queue_tee_fakesink.link(seek_fakesink)

        nvstreammux.link(queue)
        queue.link(nvdsinferserver)
        # queue.link(videoconvert)
        # videoconvert.link(capsfilter)
        # capsfilter.link(nvdsinferserver)
        nvdsinferserver.link(queue2)
        # queue2.link(nvvideoconvert2)
        # nvvideoconvert2.link(queue3)
        queue2.link(nvtracker)
        # nvtracker.link(nvdslogger)
        ####################
        if self._output_file:
            nvvideoconvert = Gst.ElementFactory.make("nvvideoconvert")
            nvvideoconvert.set_property("gpu-id", self._gpu_id)
            pipeline.add(nvvideoconvert)

            nvdsosd = Gst.ElementFactory.make("nvdsosd")
            pipeline.add(nvdsosd)
            nvdsosd.set_property("display-mask", True)
            nvdsosd.set_property("process-mode", 0)
            nvdsosd.set_property("gpu-id", self._gpu_id)

            videoconvert1 = Gst.ElementFactory.make("nvvideoconvert")
            videoconvert1.set_property("gpu-id", self._gpu_id)
            pipeline.add(videoconvert1)

            filesink = Gst.ElementFactory.make("filesink")
            filesink.set_property("location", self._output_file)
            filesink.set_property("async", False)
            pipeline.add(filesink)

            if self._output_file.endswith(".jpg"):
                jpegenc = Gst.ElementFactory.make("jpegenc")
                pipeline.add(jpegenc)

                videoconvert1.link(jpegenc)
                jpegenc.link(filesink)
            else:
                videoenc = Gst.ElementFactory.make("x264enc")
                pipeline.add(videoenc)

                mkvmux = Gst.ElementFactory.make("matroskamux")
                pipeline.add(mkvmux)

                videoconvert1.link(videoenc)
                videoenc.link_filtered(mkvmux, Gst.Caps.from_string("video/x-h264, profile=high"))
                mkvmux.link(filesink)

            # nvdslogger.link(nvvideoconvert)
            nvtracker.link(nvvideoconvert)
            nvvideoconvert.link(nvdsosd)
            nvdsosd.link(videoconvert1)

        else:
            appsink = Gst.ElementFactory.make("fakesink")
            appsink.set_property("async", False)
            appsink.set_property("sync", False)
            pipeline.add(appsink)
            # nvdslogger.link(appsink)
            nvtracker.link(appsink)

        ####################
        self._pipeline = pipeline

    def _check_required_files(self, path, max_attempts=2, wait_seconds=2):
        """
        Check that all required files and directories exist.
        Wait for them to appear if necessary, with timeout.

        Args:
            max_attempts: Maximum number of attempts to check for files
            wait_seconds: Seconds to wait between attempts

        Raises:
            FileNotFoundError: If required files are not found after max attempts
        """
        required_files = [
            "/tmp/nvdsinferserver_custom_impl_gdino/libnvdstriton_custom_impl_gdino.so",
            f"{path}/triton_model_repo/ensemble_python_gdino/config.pbtxt",
            f"{path}/triton_model_repo/gdino_preprocess/config.pbtxt",
            f"{path}/triton_model_repo/gdino_preprocess/1/model.py",
            f"{path}/triton_model_repo/gdino_trt/config.pbtxt",
            f"{path}/triton_model_repo/gdino_trt/1/model.plan",
            f"{path}/triton_model_repo/gdino_postprocess/config.pbtxt",
            f"{path}/triton_model_repo/gdino_postprocess/1/model.py",
        ]

        missing_files = []
        for attempt in range(max_attempts):
            missing_files = []
            for file_path in required_files:
                if not os.path.exists(file_path):
                    print(f"Required file/directory not found: {file_path}")
                    missing_files.append(file_path)

            if not missing_files:
                print("All required files found!")
                return True

            if attempt < max_attempts - 1:
                print(
                    f"Waiting {wait_seconds} seconds for required files..."
                    f" (attempt {attempt+1}/{max_attempts})"
                )
                time.sleep(wait_seconds)

        if missing_files:
            print(
                f"Required files not found after {max_attempts} "
                f"attempts: {', '.join(missing_files)}"
            )
            return False

    def stop(self):
        global perf_data
        if perf_data is not None:
            perf_data.set_end()
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._loop.quit()
        # self._loop = None

    def start(self):
        # Add a debug print with filename and line number
        # print(f"{__file__}:{inspect.currentframe().f_lineno} "
        # "GPU {self._gpu_id} - Starting GSAM pipeline")
        cudaSetDevice(self._gpu_id)
        start_time = time.time()
        self._loop = GLib.MainLoop()
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()

        def bus_call(bus, message, loop):
            t = message.type
            if t == Gst.MessageType.EOS:
                sys.stdout.write("End-of-stream\n")
                loop.quit()
            elif t == Gst.MessageType.WARNING:
                err, debug = message.parse_warning()
                sys.stderr.write("Warning: %s: %s\n" % (err, debug))
            elif t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                sys.stderr.write("Error: %s: %s\n" % (err, debug))
                loop.quit()
            return True

        bus.connect("message", bus_call, self._loop)

        # SEEK to start PTS
        start_pts = self._chunk.start_pts - self._chunk.pts_offset_ns
        print(start_pts)
        self._pipeline.set_state(Gst.State.PAUSED)
        self._pipeline.get_state(Gst.CLOCK_TIME_NONE)
        self._pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT | Gst.SeekFlags.SNAP_BEFORE,
            start_pts,
        )

        global perf_data
        perf_data = None
        perf_data = PERF_DATA(1, self._gpu_id, [self._chunk.chunkIdx])
        GLib.timeout_add(10000, perf_data.perf_print_callback)

        self._pipeline.set_state(Gst.State.PLAYING)

        self._loop.run()

        self._pipeline.set_state(Gst.State.NULL)
        end_time = time.time()
        # Return pts => frame number map to be used for getting chunk start frame number
        pts_to_frame_num_map = self.metadata.get_pts_to_frame_num_map()
        max_object_id = self.metadata.get_max_object_id()
        # self.metadata.write_json_file(str(self._request_id)+"_"+str(self._chunk.chunkIdx)+".json")
        self.metadata.write_json_file(get_json_file_name(self._request_id, self._chunk.chunkIdx))

        print(f"[PERF] gsam pipeline time = {(end_time - start_time) * 1000.0} ms")

        # Define the path to the file you want to delete
        file_path = self._unique_filename

        # Check if the file exists before attempting to delete it
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"File {file_path} has been deleted.")
        else:
            print(f"File {file_path} does not exist.")
        return pts_to_frame_num_map, max_object_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="gsam Pipeline")

    parser.add_argument("file", type=str, help="File to run the gsam pipeline on")

    parser.add_argument(
        "--pipeline-width",
        default=0,
        type=int,
        help="Width of the frame to feed to gsam",
    )
    parser.add_argument(
        "--pipeline-height",
        default=0,
        type=int,
        help="Height of the frame to feed to gsam",
    )

    parser.add_argument(
        "--input-start-time",
        default=0,
        type=int,
        help="Start time of the chunk in the file",
    )
    parser.add_argument(
        "--input-end-time",
        default=-1,
        type=int,
        help="End time of the chunk in the file",
    )
    parser.add_argument("--text-prompt", default="", type=str, help="Text prompt for GD")

    parser.add_argument(
        "--output-file",
        type=str,
        default="",
        help="File to write visualized gsam output to",
    )

    # parser.add_argument(
    #    "--gdino-engine", type=str, default="", help="Engine file for grounding dino"
    # )

    parser.add_argument(
        "--sam-enc-engine", type=str, default="", help="Engine file for SAM encoder"
    )

    parser.add_argument(
        "--sam-dec-engine", type=str, default="", help="Engine file for SAM decoder"
    )
    parser.add_argument("--tracker-config", type=str, default="", help="NVDCF Tracker config file")
    parser.add_argument("--is-rgb", action="store_true", default=False, help="RGB pipe or RGBA")

    parser.add_argument(
        "--single-bbox-sam",
        action="store_true",
        default=False,
        help="Init decoder for single bbox",
    )

    parser.add_argument(
        "--enable-bbox-sam",
        action="store_true",
        default=False,
        help="Enable SAM inside probe",
    )
    parser.add_argument(
        "--batch-size",
        default=4,
        type=int,
        help="Batch size",
    )
    parser.add_argument(
        "--buffer-pool-size",
        default=20,
        type=int,
        help="Buffer pool size",
    )
    parser.add_argument(
        "--inference-interval",
        default=0,
        type=int,
        help="Tracker inference interval",
    )
    parser.add_argument(
        "--gpu-id",
        default=0,
        type=int,
        help="Gpu ID to be used",
    )

    args = parser.parse_args()
    caption = args.text_prompt
    caption = caption.lower()
    caption = caption.strip()
    if not caption.endswith("."):
        caption = caption + "."
    cat_list = caption.split(" . ")
    cat_list[-1] = cat_list[-1].replace(" .", "")
    # cudaSetDevice(args.gpu_id)
    if args.single_bbox_sam:
        num_boxes = 1
    else:
        num_boxes = 32

    sam = None

    # mask, filt_box, _, _ = gdino.predict(
    #    np.zeros((720, 1280, 3), dtype=np.uint8), cat_list, draw=True
    # )

    # gsam.send_warmup_request(args.pipeline_width, args.pipeline_height)

    chunkInfo = ChunkInfo
    chunkInfo.file = args.file
    chunkInfo.start_pts = args.input_start_time * 1000000000
    chunkInfo.end_pts = args.input_end_time * 1000000000 if args.input_end_time >= 0 else -1
    chunkInfo.pts_offset_ns = 0
    chunkInfo.chunkIdx = 0

    print(args.input_start_time)

    def on_gsam_result(chunk, pts, predictions):
        print(f"Frame {pts}: {predictions}")

    # memory_pool = cp.cuda.MemoryPool(cp.cuda.malloc_managed)
    # Set the memory pool as the default allocator
    # cp.cuda.set_allocator(memory_pool.malloc)

    gsam_pipeline = GSAMPipeline(
        # gdino,
        chunkInfo,
        on_gsam_result,
        pipeline_width=args.pipeline_width,
        pipeline_height=args.pipeline_height,
        output_file=args.output_file,
        text_prompts=cat_list,
        is_rgb=args.is_rgb,
        tracker_config=args.tracker_config,
        inference_interval=args.inference_interval,
        mask_border_width=3,
        mask_color=[0, 0, 1, 1],
        draw_bbox=False,
        center_text_on_object=True,
        gpu_id=args.gpu_id,
        batch_size=args.batch_size,
        buffer_pool_size=args.buffer_pool_size,
    )
    gsam_pipeline.single_bbox_sam = args.single_bbox_sam
    signal.signal(signal.SIGINT, partial(signal_handler, gsam_pipeline))
    gsam_pipeline.start()
