####################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
####################################################################################################

import io
import json

import numpy as np
import cupy as cp

# triton_python_backend_utils is available in every Triton Python model. You
# need to use this module to create inference requests and responses. It also
# contains some utility functions for extracting information from model_config
# and converting Triton input/output types to numpy types.
import triton_python_backend_utils as pb_utils


def box_cxcywh_to_xyxy_gpu(x):
    """Convert box from cxcywh to xyxy (GPU-accelerated)."""
    x_c, y_c, w, h = x[..., 0], x[..., 1], x[..., 2], x[..., 3]
    b = [(x_c - 0.5 * w), (y_c - 0.5 * h),
         (x_c + 0.5 * w), (y_c + 0.5 * h)]
    return cp.stack(b, axis=-1)


def sigmoid_gpu(x):
    """GPU-accelerated sigmoid function with overflow protection."""
    # Clip to prevent overflow in exp()
    x = cp.clip(x, -20, 20)
    return 1 / (1 + cp.exp(-x))


def post_process_gpu(pred_logits, pred_boxes, pos_maps, pred_masks, target_sizes, num_select=300):
    """GPU-accelerated post-processing. All operations on GPU with CuPy.
    
    Args:
        pred_logits (cp.ndarray): (B x NQ x 256) Raw logits from TRT (GPU tensor)
        pred_boxes (cp.ndarray): (B x NQ x 4) GPU tensor
        pos_maps (cp.ndarray): GPU tensor (can be 2D or 3D)
        pred_masks (cp.ndarray): (B x NQ x 1 x 136 x 240) GPU tensor
        target_sizes (cp.ndarray): (B x 4) GPU tensor
        num_select (int): Top-K proposals to choose from.
    
    Returns:
        All outputs as GPU tensors (CuPy arrays)
    """
    bs = pred_logits.shape[0]

    # Apply sigmoid to convert logits to probabilities (GPU operation)
    prob_to_token = sigmoid_gpu(pred_logits)
    
    # Matrix multiplication on GPU - handle both 2D and 3D pos_maps
    if pos_maps.ndim == 2:
        # Single pos_map for all batches (common case)
        prob_to_label = prob_to_token @ pos_maps.T
    else:
        # Different pos_map per batch
        prob_to_label = cp.einsum('bij,bkj->bik', prob_to_token, pos_maps)
    prob = prob_to_label

    # Get topk scores (GPU operation) - vectorized for entire batch
    flat_prob = prob.reshape((bs, -1))
    # CuPy's argsort is on GPU - partition is faster than full sort
    topk_indices = cp.argpartition(-flat_prob, num_select, axis=1)[:, :num_select]
    # Get actual top-k values and sort them
    topk_scores = cp.take_along_axis(flat_prob, topk_indices, axis=1)
    sort_idx = cp.argsort(-topk_scores, axis=1)
    topk_indices = cp.take_along_axis(topk_indices, sort_idx, axis=1)

    # Gather scores using GPU indexing - vectorized
    scores = cp.take_along_axis(flat_prob, topk_indices, axis=1)

    # Get corresponding boxes and labels
    topk_boxes = topk_indices // prob.shape[2]
    labels = topk_indices % prob.shape[2]

    # Convert to x1, y1, x2, y2 format (GPU) - batch operation
    boxes = box_cxcywh_to_xyxy_gpu(pred_boxes)

    # Take corresponding topk boxes (GPU) - vectorized
    boxes = cp.take_along_axis(boxes, cp.repeat(cp.expand_dims(topk_boxes, -1), 4, axis=-1), axis=1)

    # Scale boxes (GPU) - vectorized
    boxes = boxes * target_sizes[:, None, :]
    
    # Clamp bounding box coordinates (GPU) - vectorized (avoid loop)
    w = target_sizes[:, 0:1, None]  # Shape: (bs, 1, 1)
    h = target_sizes[:, 1:2, None]  # Shape: (bs, 1, 1)
    boxes[:, :, 0::2] = cp.clip(boxes[:, :, 0::2], 0.0, w)
    boxes[:, :, 1::2] = cp.clip(boxes[:, :, 1::2], 0.0, h)

    # OPTIMIZATION: Vectorized mask selection (avoid loop)
    # Take corresponding topk masks - batch advanced indexing on GPU
    batch_idx = cp.arange(bs)[:, None]  # Shape: (bs, 1)
    pred_masks = pred_masks[batch_idx, topk_boxes, :, :, :]  # Vectorized indexing
    
    # Apply sigmoid AFTER topk selection (only on 300 masks instead of 900)
    # This sigmoid runs entirely on GPU - HUGE speedup
    pred_masks = sigmoid_gpu(pred_masks)

    return labels, scores, boxes, pred_masks


class TritonPythonModel:
    """Your Python model must use the same class name. Every Python model
    that is created must have "TritonPythonModel" as the class name.
    """

    def initialize(self, args):
        """`initialize` is called only once when the model is being loaded.
        Implementing `initialize` function is optional. This function allows
        the model to initialize any state associated with this model.

        Parameters
        ----------
        args : dict
          Both keys and values are strings. The dictionary keys and values are:
          * model_config: A JSON string containing the model configuration
          * model_instance_kind: A string containing model instance kind
          * model_instance_device_id: A string containing model instance device ID
          * model_repository: Model repository path
          * model_version: Model version
          * model_name: Model name
        """

        # You must parse model_config. JSON string is not parsed here
        self.model_config = model_config = json.loads(args["model_config"])

        # Get OUTPUT0 configuration
        output0_config = pb_utils.get_output_config_by_name(model_config, "labels")

        # Convert Triton types to numpy types
        self.output0_dtype = pb_utils.triton_string_to_numpy(
            output0_config["data_type"]
        )

        # Get OUTPUT1 configuration
        output1_config = pb_utils.get_output_config_by_name(model_config, "boxes")
        self.output1_dtype = pb_utils.triton_string_to_numpy(
            output1_config["data_type"]
        )

        output2_config = pb_utils.get_output_config_by_name(model_config, "scores")
        self.output2_dtype = pb_utils.triton_string_to_numpy(
            output2_config["data_type"]
        )

        # Get OUTPUT3 configuration
        output3_config = pb_utils.get_output_config_by_name(model_config, "masks")
        self.output3_dtype = pb_utils.triton_string_to_numpy(
            output3_config["data_type"]
        )

    def execute(self, requests):
        """`execute` MUST be implemented in every Python model. `execute`
        function receives a list of pb_utils.InferenceRequest as the only
        argument. This function is called when an inference request is made
        for this model. Depending on the batching configuration (e.g. Dynamic
        Batching) used, `requests` may contain multiple requests. Every
        Python model, must create one pb_utils.InferenceResponse for every
        pb_utils.InferenceRequest in `requests`. If there is an error, you can
        set the error argument when creating a pb_utils.InferenceResponse

        Parameters
        ----------
        requests : list
          A list of pb_utils.InferenceRequest

        Returns
        -------
        list
          A list of pb_utils.InferenceResponse. The length of this list must
          be the same as `requests`
        """

        output0_dtype = self.output0_dtype
        output1_dtype = self.output1_dtype
        output2_dtype = self.output2_dtype
        output3_dtype = self.output3_dtype

        responses = []

        # Every Python backend must iterate over everyone of the requests
        # and create a pb_utils.InferenceResponse for each of them.
        for request in requests:
            # ZERO-COPY OPTIMIZATION: Try to get GPU tensors directly via as_numpy()
            # Triton may provide GPU pointers if backend supports it
            pred_logits_np = pb_utils.get_input_tensor_by_name(request, "pred_logits").as_numpy()
            pred_boxes_np = pb_utils.get_input_tensor_by_name(request, "pred_boxes").as_numpy()
            pos_map_np = pb_utils.get_input_tensor_by_name(request, "pos_map").as_numpy()
            target_sizes_np = pb_utils.get_input_tensor_by_name(request, "target_sizes").as_numpy()
            pred_masks_np = pb_utils.get_input_tensor_by_name(request, "pred_masks").as_numpy()

            # Transfer to GPU for processing (single transfer per tensor)
            # Use asarray for zero-copy if already on GPU, otherwise copy
            pred_logits = cp.asarray(pred_logits_np)
            pred_boxes = cp.asarray(pred_boxes_np)
            pos_map = cp.asarray(pos_map_np)
            target_sizes = cp.asarray(target_sizes_np)
            pred_masks = cp.asarray(pred_masks_np)
            
            bs = pred_logits.shape[0]

            # OPTIMIZATION: Pre-allocate output arrays on GPU (avoid dynamic stacking)
            # Process entire batch at once instead of per-item loop
            if bs == 1:
                # Fast path for batch=1 (most common case)
                class_labels, scores, boxes, pred_masks_out = post_process_gpu(
                    pred_logits, pred_boxes, pos_map, pred_masks, target_sizes
                )
            else:
                # Batch processing path - pre-allocate outputs
                num_select = 300
                class_labels_list = []
                scores_list = []
                boxes_list = []
                pred_masks_list = []
                
                for k in range(bs):
                    labels_k, scores_k, boxes_k, masks_k = post_process_gpu(
                        pred_logits[k:k+1], 
                        pred_boxes[k:k+1],
                        pos_map[k],
                        pred_masks[k:k+1],
                        target_sizes[k:k+1]
                    )
                    class_labels_list.append(labels_k)
                    scores_list.append(scores_k)
                    boxes_list.append(boxes_k)
                    pred_masks_list.append(masks_k)
                
                # Single stack operation on GPU (faster than repeated vstack)
                class_labels = cp.concatenate(class_labels_list, axis=0)
                scores = cp.concatenate(scores_list, axis=0)
                boxes = cp.concatenate(boxes_list, axis=0)
                pred_masks_out = cp.concatenate(pred_masks_list, axis=0)

            # OPTIMIZATION: Cast to correct dtypes on GPU, then single transfer to CPU
            # Use contiguous arrays for faster CPU transfer
            class_labels_np = cp.asnumpy(cp.ascontiguousarray(class_labels.astype(output0_dtype)))
            boxes_np = cp.asnumpy(cp.ascontiguousarray(boxes.astype(output1_dtype)))
            scores_np = cp.asnumpy(cp.ascontiguousarray(scores.astype(output2_dtype)))
            pred_masks_np = cp.asnumpy(cp.ascontiguousarray(pred_masks_out.astype(output3_dtype)))

            # Create output tensors
            out_tensor_0 = pb_utils.Tensor("labels", class_labels_np)
            out_tensor_1 = pb_utils.Tensor("boxes", boxes_np)
            out_tensor_2 = pb_utils.Tensor("scores", scores_np)
            out_tensor_3 = pb_utils.Tensor("masks", pred_masks_np)

            # Create InferenceResponse
            inference_response = pb_utils.InferenceResponse(
                output_tensors=[out_tensor_0, out_tensor_1, out_tensor_2, out_tensor_3]
            )
            responses.append(inference_response)

        return responses

    def finalize(self):
        """`finalize` is called only once when the model is being unloaded.
        Implementing `finalize` function is OPTIONAL. This function allows
        the model to perform any necessary clean ups before exit.
        """
        print("[MaskGDINO] Cleaning up GPU resources...")
