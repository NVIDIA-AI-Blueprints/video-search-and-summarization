####################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
####################################################################################################

import json

import torch

# triton_python_backend_utils is available in every Triton Python model. You
# need to use this module to create inference requests and responses. It also
# contains some utility functions for extracting information from model_config
# and converting Triton input/output types to numpy types.
import triton_python_backend_utils as pb_utils
from torch.utils.dlpack import from_dlpack


def post_process(pred_logits, pred_boxes, pos_maps, target_sizes, device="cuda", num_select=300):
    """Perform the post-processing. Scale back the boxes to the original size using PyTorch on GPU.

    Args:
        pred_logits (torch.Tensor): (B x NQ x 4) logit values from TRT engine.
        pred_boxes (torch.Tensor): (B x NQ x 4) bbox values from TRT engine.
        pos_maps (torch.Tensor): (C x C) positional maps.
        target_sizes (torch.Tensor): (B x 4) [w, h, w, h] containing original image dimension.
        num_select (int): Top-K proposals to choose from.

    Returns:
        labels (torch.Tensor): (B x NS) class label of top num_select predictions.
        scores (torch.Tensor): (B x NS) class probability of top num_select predictions.
        boxes (torch.Tensor):  (B x NS x 4) scaled back bounding boxes of top num_select predictions.
    """
    # device = pred_logits.device  # Ensure tensors are on the same device (GPU)
    bs = pred_logits.shape[0]

    # Sigmoid
    prob_to_token = torch.sigmoid(pred_logits)

    # Normalize pos_maps
    for label_ind in range(len(pos_maps)):
        if pos_maps[label_ind].sum() != 0:
            pos_maps[label_ind] = pos_maps[label_ind] / pos_maps[label_ind].sum()

    pos_maps = pos_maps.to(device).cuda()  # Move pos_maps to GPU

    # prob_to_label = torch.matmul(prob_to_token, pos_maps.T)
    prob_to_label = prob_to_token.cuda() @ pos_maps.T

    prob = prob_to_label

    # Get topk scores
    topk_indices = torch.topk(prob.view(bs, -1), k=num_select, dim=1, largest=True).indices
    # topk_indices = torch.argsort(prob.view(bs, -1), dim=1, descending=True)[:, :num_select]
    scores = torch.stack(
        [per_batch_prob[ind] for per_batch_prob, ind in zip(prob.view(bs, -1), topk_indices)]
    )

    # Get corresponding boxes
    topk_boxes = topk_indices // prob.shape[2]
    # Get corresponding labels
    labels = topk_indices % prob.shape[2]

    # Convert to x1, y1, x2, y2 format
    x_c, y_c, w, h = pred_boxes[..., 0], pred_boxes[..., 1], pred_boxes[..., 2], pred_boxes[..., 3]
    boxes = torch.stack(
        [(x_c - 0.5 * w), (y_c - 0.5 * h), (x_c + 0.5 * w), (y_c + 0.5 * h)], dim=-1
    )

    # Take corresponding topk boxes
    boxes = torch.gather(boxes.cuda(), 1, topk_boxes.cuda().unsqueeze(-1).expand(-1, -1, 4))

    boxes = boxes * target_sizes[:, None, :].to(device)
    # Clamp bounding box coordinates
    for i, target_size in enumerate(target_sizes):
        w, h = target_size[0], target_size[1]
        boxes[i, :, 0::2] = torch.clamp(boxes[i, :, 0::2], 0.0, w)
        boxes[i, :, 1::2] = torch.clamp(boxes[i, :, 1::2], 0.0, h)

    return labels.cpu().numpy(), scores.cpu().numpy(), boxes.cpu().numpy()


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

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Using device: {self.device}")

        # Get OUTPUT0 configuration
        output0_config = pb_utils.get_output_config_by_name(model_config, "labels")

        # Convert Triton types to numpy types
        self.output0_dtype = pb_utils.triton_string_to_numpy(output0_config["data_type"])

        # Get OUTPUT0 configuration
        output1_config = pb_utils.get_output_config_by_name(model_config, "boxes")
        self.output1_dtype = pb_utils.triton_string_to_numpy(output1_config["data_type"])

        output2_config = pb_utils.get_output_config_by_name(model_config, "scores")
        self.output2_dtype = pb_utils.triton_string_to_numpy(output2_config["data_type"])

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

        responses = []

        # Every Python backend must iterate over everyone of the requests
        # and create a pb_utils.InferenceResponse for each of them.
        for request in requests:
            # Get INPUT0
            in_0 = pb_utils.get_input_tensor_by_name(request, "pred_logits")
            in_1 = pb_utils.get_input_tensor_by_name(request, "pred_boxes")
            in_2 = pb_utils.get_input_tensor_by_name(request, "pos_map")
            in_3 = pb_utils.get_input_tensor_by_name(request, "target_sizes")

            pred_logits = from_dlpack(in_0.to_dlpack())
            pred_boxes = from_dlpack(in_1.to_dlpack())
            pos_map = from_dlpack(in_2.to_dlpack())
            target_sizes = from_dlpack(in_3.to_dlpack())

            class_labels, scores, boxes = post_process(
                pred_logits, pred_boxes, pos_map[0], target_sizes
            )

            out_tensor_0 = pb_utils.Tensor("labels", class_labels.astype(output0_dtype))
            out_tensor_1 = pb_utils.Tensor("boxes", boxes.astype(output1_dtype))
            out_tensor_2 = pb_utils.Tensor("scores", scores.astype(output2_dtype))

            # out_tensor_0 = pb_utils.Tensor.from_dlpack("labels", class_labels)
            # out_tensor_1 = pb_utils.Tensor.from_dlpack("boxes", boxes)
            # out_tensor_2 = pb_utils.Tensor.from_dlpack("scores", scores)

            inference_response = pb_utils.InferenceResponse(
                output_tensors=[out_tensor_0, out_tensor_1, out_tensor_2]
            )
            responses.append(inference_response)

        # You should return a list of pb_utils.InferenceResponse. Length
        # of this list must match the length of `requests` list.
        return responses

    def finalize(self):
        """`finalize` is called only once when the model is being unloaded.
        Implementing `finalize` function is OPTIONAL. This function allows
        the model to perform any necessary clean ups before exit.
        """
        print("Cleaning up...")
