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

"""Base TensorRT inferencer."""

from abc import ABC, abstractmethod

import numpy as np
import pycuda.autoinit  # noqa pylint: disable=unused-import
import pycuda.driver as cuda
import tensorrt as trt
from PIL import ImageDraw


class HostDeviceMem(object):
    """Clean data structure to handle host/device memory."""

    def __init__(self, host_mem, device_mem, npshape, name: str = None):
        """Initialize a HostDeviceMem data structure.

        Args:
            host_mem (cuda.pagelocked_empty): A cuda.pagelocked_empty memory buffer.
            device_mem (cuda.mem_alloc): Allocated memory pointer to the buffer in the GPU.
            npshape (tuple): Shape of the input dimensions.

        Returns:
            HostDeviceMem instance.
        """
        self.host = host_mem
        self.device = device_mem
        self.numpy_shape = npshape
        self.name = name

    def __str__(self):
        """String containing pointers to the TRT Memory."""
        return (
            "Name: " + self.name + "\nHost:\n" + str(self.host) + "\nDevice:\n" + str(self.device)
        )

    def __repr__(self):
        """Return the canonical string representation of the object."""
        return self.__str__()


def do_inference(
    context,
    bindings,
    inputs,
    outputs,
    stream,
    engine,
    batch_size=1,
    execute_v2=False,
    return_raw=False,
):
    """Generalization for multiple inputs/outputs.

    inputs and outputs are expected to be lists of HostDeviceMem objects.
    """
    # Transfer input data to the GPU.
    for inp in inputs:
        cuda.memcpy_htod_async(inp.device, inp.host, stream)
    # Run inference.
    # if execute_v2:
    # context.execute_async_v2(bindings=bindings, stream_handle=stream.handle)
    # else:
    # context.execute_async(batch_size=batch_size, bindings=bindings, stream_handle=stream.handle)
    for i in range(engine.num_io_tensors):
        context.set_tensor_address(engine.get_tensor_name(i), bindings[i])
    context.execute_async_v3(stream_handle=stream.handle)
    # Transfer predictions back from the GPU.
    for out in outputs:
        cuda.memcpy_dtoh_async(out.host, out.device, stream)
    # Synchronize the stream
    stream.synchronize()

    if return_raw:
        return outputs

    # Return only the host outputs.
    return [out.host for out in outputs]


def allocate_buffers(engine, context=None, reshape=False):
    """Allocates host and device buffer for TRT engine inference.

    This function is similair to the one in common.py, but
    converts network outputs (which are np.float32) appropriately
    before writing them to Python buffer. This is needed, since
    TensorRT plugins doesn't support output type description, and
    in our particular case, we use NMS plugin as network output.

    Args:
        engine (trt.ICudaEngine): TensorRT engine
        context (trt.IExecutionContext): Context for dynamic shape engine
        reshape (bool): To reshape host memory or not (FRCNN)

    Returns:
        inputs [HostDeviceMem]: engine input memory
        outputs [HostDeviceMem]: engine output memory
        bindings [int]: buffer to device bindings
        stream (cuda.Stream): cuda stream for engine inference synchronization
    """
    inputs = []
    outputs = []
    bindings = []
    stream = cuda.Stream()

    # Current NMS implementation in TRT only supports DataType.FLOAT but
    # it may change in the future, which could brake this sample here
    # when using lower precision [e.g. NMS output would not be np.float32
    # anymore, even though this is assumed in binding_to_type]
    """
    binding_to_type = {
        "Input": np.float32,
        "NMS": np.float32,
        "NMS_1": np.int32,
        "BatchedNMS": np.int32,
        "BatchedNMS_1": np.float32,
        "BatchedNMS_2": np.float32,
        "BatchedNMS_3": np.float32,
        "generate_detections": np.float32,
        "mask_head/mask_fcn_logits/BiasAdd": np.float32,
        "softmax_1": np.float32,
        "input_1": np.float32,
        # D-DETR
        "inputs": np.float32,
        "pred_boxes": np.float32,
        "pred_logits": np.float32,
    }
    """

    # for binding in engine:
    for i in range(engine.num_io_tensors):
        tensor_name = engine.get_tensor_name(i)
        dtype = trt.nptype(engine.get_tensor_dtype(tensor_name))
        # binding_id = i
        binding_name = tensor_name
        # binding_id = engine.get_binding_index(str(binding))
        # binding_name = engine.get_binding_name(binding_id)
        if context:
            # size = trt.volume(context.get_binding_shape(binding_id))
            # dims = context.get_binding_shape(binding_id)
            # BN : TBD : data type 'tensorrt_bindings.tensorrt.Dims' has some issue
            # crashes when given as input to list/get_tensor_shape
            # hence convert it to numpy array and then use
            # size = trt.volume(context.get_tensor_shape(tensor_name))
            # dims = context.get_tensor_shape(tensor_name)
            dims = context.get_tensor_shape(tensor_name)
            numpy_dims = np.zeros(len(dims))
            for j in range(len(dims)):
                numpy_dims[j] = dims[j]
            dims = numpy_dims
            size = trt.volume(dims)
        else:
            # size = trt.volume(engine.get_binding_shape(binding)) * engine.max_batch_size
            # dims = engine.get_binding_shape(binding)
            size = trt.volume(engine.get_tensor_shape(tensor_name))
            dims = engine.get_tensor_shape(tensor_name)
        # avoid error when bind to a number (YOLO BatchedNMS)
        # size = engine.max_batch_size if size == 0 else size
        # if str(binding) in binding_to_type:
        #    dtype = binding_to_type[str(binding)]
        # else:
        #    dtype = trt.nptype(engine.get_binding_dtype(binding))
        # Allocate host and device buffers
        host_mem = cuda.pagelocked_empty(int(size), dtype)

        # FRCNN requires host memory to be reshaped into target shape
        binding_is_input = engine.get_tensor_mode(tensor_name) == trt.TensorIOMode.INPUT
        # if reshape and not engine.binding_is_input(binding):
        if reshape and not binding_is_input:
            # if engine.has_implicit_batch_dimension:
            #    target_shape = (engine.max_batch_size, dims[0], dims[1], dims[2])
            # else:
            target_shape = dims
            host_mem = host_mem.reshape(*target_shape)

        device_mem = cuda.mem_alloc(host_mem.nbytes)
        # Append the device buffer to device bindings.
        bindings.append(int(device_mem))
        # Append to the appropriate list.
        # if engine.binding_is_input(binding):
        if binding_is_input:
            inputs.append(HostDeviceMem(host_mem, device_mem, dims, name=binding_name))
        else:
            outputs.append(HostDeviceMem(host_mem, device_mem, dims, name=binding_name))
    return inputs, outputs, bindings, stream


def load_engine(trt_runtime, engine_path):
    """Helper funtion to load an exported engine."""
    with open(engine_path, "rb") as f:
        engine_data = f.read()
    engine = trt_runtime.deserialize_cuda_engine(engine_data)
    return engine


class TRTInferencer(ABC):
    """Base TRT Inferencer."""

    def __init__(self, engine_path):
        """Init.

        Args:
            engine_path (str): The path to the serialized engine to load from disk.
        """
        # Load TRT engine
        self.logger = trt.Logger(trt.Logger.WARNING)
        trt.init_libnvinfer_plugins(self.logger, namespace="")
        # Initialize runtime needed for loading TensorRT engine from file
        self.trt_runtime = trt.Runtime(self.logger)
        self.engine = load_engine(self.trt_runtime, engine_path)
        self.context = self.engine.create_execution_context()
        assert self.engine
        assert self.context

    @abstractmethod
    def infer(self, imgs, scales=None):
        """Execute inference on a batch of images.

        The images should already be batched and preprocessed.
        Memory copying to and from the GPU device will be performed here.

        Args:
            imgs (np.ndarray): A numpy array holding the image batch.
            scales: The image resize scales for each image in this batch.
                Default: No scale postprocessing applied.

        Returns:
            A nested list for each image in the batch and each detection in the list.
        """
        detections = {}
        return detections

    @abstractmethod
    def __del__(self):
        """Simple function to destroy tensorrt handlers."""

    def draw_bbox(self, img, prediction, class_mapping, threshold=0.3):
        """Draws bbox on image and dump prediction in KITTI format

        Args:
            img (numpy.ndarray): Preprocessed image
            prediction (numpy.ndarray): (N x 6) predictions
            class_mapping (dict): key is the class index and value is the class string.
                If set to None, no class predictions are displayed
            threshold (float): value to filter predictions
        """
        draw = ImageDraw.Draw(img)
        color_list = ["Black", "Red", "Blue", "Gold", "Purple"]

        label_strings = []
        for i in prediction:
            if class_mapping and int(i[0]) not in class_mapping:
                continue
            if float(i[1]) < threshold:
                continue

            if isinstance(class_mapping, dict):
                cls_name = class_mapping[int(i[0])]
            else:
                cls_name = str(int(i[0]))

            # Default format is xyxy
            x1, y1, x2, y2 = float(i[2]), float(i[3]), float(i[4]), float(i[5])

            draw.rectangle(((x1, y1), (x2, y2)), outline=color_list[int(i[0]) % len(color_list)])
            # txt pad
            draw.rectangle(
                ((x1, y1), (x1 + 75, y1 + 10)), fill=color_list[int(i[0]) % len(color_list)]
            )

            if isinstance(class_mapping, dict):
                draw.text((x1, y1), f"{cls_name}: {i[1]:.2f}")
            else:
                # If label_map is not provided, do not show class prediction
                draw.text((x1, y1), f"{i[1]:.2f}")

            # Dump predictions

            label_head = cls_name + " 0.00 0 0.00 "
            bbox_string = f"{x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f}"
            label_tail = f" 0.00 0.00 0.00 0.00 0.00 0.00 0.00 {float(i[1]):.3f}\n"
            label_string = label_head + bbox_string + label_tail
            label_strings.append(label_string)

        return img, label_strings
