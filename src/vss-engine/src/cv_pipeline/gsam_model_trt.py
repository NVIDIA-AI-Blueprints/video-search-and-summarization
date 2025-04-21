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

import argparse
import ctypes
from typing import Tuple

import cupy as cp

# Grounding DINO
# segment anything
import cv2
import numpy as np
import torch
from torchvision.transforms.functional import resize as resize_tv
from transformers import AutoTokenizer

from trt_inference.data_loader import preprocess_input, resize
from trt_inference.gdino_inferencer import GDINOInferencer
from trt_inference.utils import (
    create_positive_map,
    generate_masks_with_special_tokens_and_transfer_map,
    post_process,
)


# from efficientvit.onnx_exporter.export_encoder import SamResize
class SamResize:
    def __init__(self, size: int) -> None:
        self.size = size

    def __call__(self, image: torch.Tensor) -> torch.Tensor:
        h, w, _ = image.shape
        long_side = max(h, w)
        if long_side != self.size:
            return self.apply_image(image)
        else:
            return image

    def apply_image(self, image: torch.Tensor) -> torch.Tensor:
        """
        Expects a torch tensor with shape HxWxC in float format.
        """
        h, w, _ = image.shape
        long_side = max(h, w)
        if long_side != self.size:
            target_size = self.get_preprocess_shape(image.shape[0], image.shape[1], self.size)
            x = resize_tv(image.permute(2, 0, 1), target_size)
            return x
        else:
            return image.permute(2, 0, 1)

    @staticmethod
    def get_preprocess_shape(oldh: int, oldw: int, long_side_length: int) -> Tuple[int, int]:
        """
        Compute the output size given input size and target long side length.
        """
        scale = long_side_length * 1.0 / max(oldh, oldw)
        newh, neww = oldh * scale, oldw * scale
        neww = int(neww + 0.5)
        newh = int(newh + 0.5)
        return (newh, neww)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(size={self.size})"


class CudaAwareArray(np.ndarray):
    def __new__(cls, input_array):
        # Create a new array object instance
        obj = np.asarray(input_array).view(cls)
        return obj

    @property
    def __array_interface__(self):
        # Get the CUDA array interface dictionary
        cai = self.base.__cuda_array_interface__
        # Create the NumPy array interface dictionary
        typestr = np.dtype(cai["typestr"]).str
        ai = {
            "version": 3,
            "typestr": typestr,
            "data": (
                cai["data"][0],
                False,
            ),  # False indicates the data is not read-only
            "shape": cai["shape"],
            "strides": cai["strides"],
            "descr": np.dtype(cai["typestr"]).descr if "descr" in cai else None,
        }
        return ai


# Define a custom NumPy array subclass that can wrap a CuPy array
class CudaMemoryArray(np.ndarray):
    def __new__(cls, cupy_array):
        buffer = (ctypes.c_byte * cupy_array.nbytes).from_address(cupy_array.data.ptr)
        obj = super().__new__(cls, cupy_array.shape, cupy_array.dtype, buffer)
        obj.__cuda_array_interface__ = cupy_array.__cuda_array_interface__
        return obj

    @property
    def __array_interface__(self):
        cai = self.__cuda_array_interface__
        typestr = np.dtype(cai["typestr"]).str
        return {
            "version": 3,
            "typestr": typestr,
            "data": (cai["data"][0], False),
            "shape": cai["shape"],
            "strides": cai["strides"],
            "descr": np.dtype(cai["typestr"]).descr if "descr" in cai else None,
        }


class GroundingDino:
    def __init__(self, trt_engine="data/swinb.fp16.engine", max_text_len=256, batch_size=1):
        self._max_text_len = max_text_len
        self._batch_size = batch_size
        self._trt_infer = GDINOInferencer(
            trt_engine, batch_size=batch_size, num_classes=max_text_len
        )

        # Load model
        self._frame_no = 0

        _, h, w = self._trt_infer._input_shape[0]
        self._h = h
        self._w = w

        self.tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
        self.specical_tokens = self.tokenizer.convert_tokens_to_ids(["[CLS]", "[SEP]", ".", "?"])

    def predict(
        self,
        ip_image,
        captions_list=["cat", "dog"],
        threshold=0.3,
        measure_perf=False,
        draw=False,
    ):
        label_list = np.arange(len(captions_list))
        # Get Captions
        captions = [" . ".join(captions_list) + " ."]
        tokenized = self.tokenizer(
            captions,
            padding="max_length",
            return_tensors="np",
            max_length=self._max_text_len,
        )
        pos_map = create_positive_map(
            tokenized,
            label_list,
            captions_list,
            captions[0],
            max_text_len=self._max_text_len,
        )

        (
            text_self_attention_masks,
            position_ids,
        ) = generate_masks_with_special_tokens_and_transfer_map(tokenized, self.specical_tokens)

        if text_self_attention_masks.shape[1] > self._max_text_len:
            text_self_attention_masks = text_self_attention_masks[
                :, : self._max_text_len, : self._max_text_len
            ]

        position_ids = position_ids[:, : self._max_text_len]
        tokenized["input_ids"] = tokenized["input_ids"][:, : self._max_text_len]
        tokenized["attention_mask"] = tokenized["attention_mask"][:, : self._max_text_len]
        tokenized["token_type_ids"] = tokenized["token_type_ids"][:, : self._max_text_len]

        input_ids = tokenized["input_ids"].astype(int)
        attention_mask = tokenized["attention_mask"].astype(bool)
        position_ids = position_ids.astype(int)
        token_type_ids = tokenized["token_type_ids"].astype(int)
        text_self_attention_masks = text_self_attention_masks.astype(bool)

        if isinstance(ip_image, cp.ndarray):
            # image = (cp.asnumpy(ip_image)).astype(np.uint8)
            image = CudaMemoryArray(ip_image)
        else:
            image = ip_image
        # print (image.shape, image.dtype, captions_list)

        # preprocess
        dtype = self._trt_infer.inputs[0].host.dtype
        image = np.asarray(image, dtype=dtype)
        orig_h, orig_w, _ = image.shape
        image, _ = resize(image, None, size=(self._h, self._w))
        img_std = [0.229, 0.224, 0.225]
        image = preprocess_input(image, data_format="channels_last", img_std=img_std, mode="torch")
        new_h, new_w, _ = image.shape
        scale = (orig_h / new_h, orig_w / new_w)
        image = np.transpose(image, (2, 0, 1))

        # Add batch dim
        batches = np.array([image])

        # Prepare
        inputs = (
            batches,
            input_ids,
            attention_mask,
            position_ids,
            token_type_ids,
            text_self_attention_masks,
        )
        # infer
        pred_logits, pred_boxes = self._trt_infer.infer(inputs)
        target_sizes = []

        orig_h, orig_w = int(scale[0] * new_h), int(scale[1] * new_w)
        target_sizes.append([orig_w, orig_h, orig_w, orig_h])

        class_labels, scores, boxes = post_process(pred_logits, pred_boxes, target_sizes, pos_map)
        y_pred_valid = np.concatenate([class_labels[..., None], scores[..., None], boxes], axis=-1)

        # boxes_filt = boxes
        # print (boxes[:4], scores[:4], class_labels[:4])

        inv_classes = {i: c for i, c in enumerate(captions_list)}
        color_map = {c: "green" for c in captions_list}

        bbox_img, bboxes, class_name, class_conf = self.get_bbox(
            ip_image,
            y_pred_valid[0],
            inv_classes,
            threshold,
            color_map=color_map,
            draw=draw,
        )
        # print (y_pred_valid[0])
        # print (class_conf)

        return bbox_img, bboxes, class_name, class_conf

    def get_bbox(
        self, img, prediction, class_mapping, threshold=0.3, color_map=None, draw=False
    ):  # noqa pylint: disable=W0237
        """Draws bbox on image and dump prediction in KITTI format

        Args:
            img (numpy.ndarray): Preprocessed image
            prediction (numpy.ndarray): (N x 6) predictions
            class_mapping (dict): key is the class index and value is the class name
            threshold (float): value to filter predictions
            color_map (dict): key is the class name and value is the color to be used
        """
        frame1 = img

        boxes = []
        class_name = []
        class_conf = []
        for i in prediction:
            if int(i[0]) not in class_mapping:
                print(i[0], class_mapping)
                continue
            cls_name = class_mapping[int(i[0])]
            if float(i[1]) < threshold:
                continue

            if cls_name in color_map and draw:
                frame1 = cv2.rectangle(
                    frame1,
                    (int(i[2]), int(i[3])),
                    (int(i[4]), int(i[5])),
                    (0, 255, 0),
                    2,
                )
                frame1 = cv2.putText(
                    frame1,
                    f"{cls_name}: {i[1]:.2f}",
                    (int(i[2]), int(i[3] - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (255, 255, 255),
                    2,
                )

            x1, y1, x2, y2 = float(i[2]), float(i[3]), float(i[4]), float(i[5])
            boxes.append([x1, y1, x2, y2])
            class_name.append(cls_name)
            class_conf.append(i[1])
        return frame1, np.array(boxes), class_name, class_conf

    def send_warmup_request(self, width, height):
        img = np.random.randint(256, size=(height, width, 3), dtype=np.uint8)
        mask, filt_box = self.predict(img, ["cat", "dog"])


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="gsam Pipeline")

    parser.add_argument("file", type=str, help="File to run the gsam pipeline on")

    parser.add_argument(
        "--gdino_engine",
        default="swinb.fp16.engine",
        type=str,
        help="Gdino engine file",
    )
    parser.add_argument(
        "--sam_enc_engine",
        default="l2.sam.encoder.engine",
        type=str,
        help="Sam encoder engine file",
    )
    parser.add_argument(
        "--sam_dec_engine",
        default="l2.sam.decoder.fp16.engine",
        type=str,
        help="Sam decoder engine",
    )
    parser.add_argument("--outputfile", default="out.jpg", type=str, help="Output file")
    parser.add_argument(
        "--text_prompt",
        default="player . basketball .",
        type=str,
        help="Text prompt for GD SAM",
    )

    args = parser.parse_args()

    # Preprocess text prompt
    caption = args.text_prompt
    caption = caption.lower()
    caption = caption.strip()
    if not caption.endswith("."):
        caption = caption + "."
    cat_list = caption.split(" . ")
    cat_list[-1] = cat_list[-1].replace(" .", "")

    gdino = GroundingDino(trt_engine=args.gdino_engine, max_text_len=256, batch_size=1)
