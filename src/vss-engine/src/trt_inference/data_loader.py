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

import cv2
import numpy as np


def resize(image, target, size, max_size=None):
    """resize."""

    # size can be min_size (scalar) or (w, h) tuple
    def get_size_with_aspect_ratio(image_size, size, max_size=None):
        """get_size_with_aspect_ratio."""
        w, h = image_size
        if max_size is not None:
            min_original_size = float(min((w, h)))
            max_original_size = float(max((w, h)))
            if max_original_size / min_original_size * size > max_size:
                size = int(round(max_size * min_original_size / max_original_size))

        if (w <= h and w == size) or (h <= w and h == size):
            return (h, w)

        if w < h:
            ow = size
            oh = int(size * h / w)
        else:
            oh = size
            ow = int(size * w / h)

        return (oh, ow)

    def get_size(image_size, size, max_size=None):
        """get_size."""
        # Size needs to be (width, height)
        if isinstance(size, (list, tuple)):
            return_size = size[::-1]
        else:
            return_size = get_size_with_aspect_ratio(image_size, size, max_size)
        return return_size

    size = get_size(image.size, size, max_size)

    # PILLOW bilinear is not same as F.resize from torchvision
    # PyTorch mimics OpenCV's behavior.
    # Ref: https://tcapelle.github.io/pytorch/fastai/2021/02/26/image_resizing.html
    rescaled_image = cv2.resize(image, size, interpolation=cv2.INTER_LINEAR)

    if target is None:
        return rescaled_image, None

    ratios = tuple(float(s) / float(s_orig) for s, s_orig in zip(rescaled_image.size, image.size))
    ratio_width, ratio_height = ratios

    target = target.copy()
    if "boxes" in target:
        boxes = target["boxes"]
        scaled_boxes = boxes * [ratio_width, ratio_height, ratio_width, ratio_height]
        target["boxes"] = scaled_boxes

    h, w = size
    target["size"] = np.array([h, w])

    return rescaled_image, target


def _preprocess_numpy_input(
    x, data_format, mode, color_mode, img_mean, img_std, img_depth, **kwargs
):
    """Preprocesses a Numpy array encoding a batch of images.

    # Arguments
        x: Input array, 3D or 4D.
        data_format: Data format of the image array.
        mode: One of "caffe", "tf" or "torch".
            - caffe: will convert the images from RGB to BGR,
                then will zero-center each color channel with
                respect to the ImageNet dataset,
                without scaling.
            - tf: will scale pixels between -1 and 1,
                sample-wise.
            - torch: will scale pixels between 0 and 1 and then
                will normalize each channel with respect to the
                ImageNet dataset.

    # Returns
        Preprocessed Numpy array.
    """
    assert img_depth in [8, 16], f"Unsupported image depth: {img_depth}, should be 8 or 16."

    if not issubclass(x.dtype.type, np.floating):
        x = x.astype(np.float32, copy=False)

    if mode == "tf":
        if img_depth == 8:
            x /= 127.5
        else:
            x /= 32767.5
        x -= 1.0
        return x

    if mode == "torch":
        override_mean = False
        if (isinstance(img_mean, list) and (np.array(img_mean) > 1).any()) or (img_mean is None):
            override_mean = True
        if img_depth == 8:
            x /= 255.0
        else:
            x /= 65535.0

        if color_mode == "rgb":
            assert img_depth == 8, f"RGB images only support 8-bit depth, got {img_depth}, "

            if override_mean:
                mean = [0.485, 0.456, 0.406]
                std = [0.224, 0.224, 0.224]
            else:
                mean = img_mean
                std = img_std
        elif color_mode == "grayscale":
            if not img_mean:
                mean = [0.449]
                std = [0.224]
            else:
                assert (
                    len(img_mean) == 1
                ), "image_mean must be a list of a single value \
                    for gray image input."
                mean = img_mean
                if img_std is not None:
                    assert (
                        len(img_std) == 1
                    ), "img_std must be a list of a single value \
                        for gray image input."
                    std = img_std
                else:
                    std = None
        else:
            raise NotImplementedError(f"Invalid color mode: {color_mode}")
    else:
        if color_mode == "rgb":
            assert img_depth == 8, f"RGB images only support 8-bit depth, got {img_depth}, "
            if data_format == "channels_first":
                # 'RGB'->'BGR'
                if x.ndim == 3:
                    x = x[::-1, ...]
                else:
                    x = x[:, ::-1, ...]
            else:
                # 'RGB'->'BGR'
                x = x[..., ::-1]
            if not img_mean:
                mean = [103.939, 116.779, 123.68]
            else:
                assert (
                    len(img_mean) == 3
                ), "image_mean must be a list of 3 values \
                    for RGB input."
                mean = img_mean
            std = None
        else:
            if not img_mean:
                if img_depth == 8:
                    mean = [117.3786]
                else:
                    # 117.3786 * 256
                    mean = [30048.9216]
            else:
                assert (
                    len(img_mean) == 1
                ), "image_mean must be a list of a single value \
                    for gray image input."
                mean = img_mean
            std = None

    # Zero-center by mean pixel
    if data_format == "channels_first":
        for idx in range(len(mean)):
            if x.ndim == 3:
                x[idx, :, :] -= mean[idx]
                if std is not None:
                    x[idx, :, :] /= std[idx]
            else:
                x[:, idx, :, :] -= mean[idx]
                if std is not None:
                    x[:, idx, :, :] /= std[idx]
    else:
        for idx in range(len(mean)):
            x[..., idx] -= mean[idx]
            if std is not None:
                x[..., idx] /= std[idx]
    return x


def preprocess_input(
    x,
    data_format="channels_first",
    mode="caffe",
    color_mode="rgb",
    img_mean=None,
    img_std=None,
    img_depth=8,
    **kwargs,
):
    """Preprocesses a tensor or Numpy array encoding a batch of images.

    # Arguments
        x: Input Numpy or symbolic tensor, 3D or 4D.
            The preprocessed data is written over the input data
            if the data types are compatible. To avoid this
            behaviour, `numpy.copy(x)` can be used.
        data_format: Data format of the image tensor/array.
        mode: One of "caffe", "tf" or "torch".
            - caffe: will convert the images from RGB to BGR,
                then will zero-center each color channel with
                respect to the ImageNet dataset,
                without scaling.
            - tf: will scale pixels between -1 and 1,
                sample-wise.
            - torch: will scale pixels between 0 and 1 and then
                will normalize each channel with respect to the
                ImageNet dataset.

    # Returns
        Preprocessed tensor or Numpy array.

    # Raises
        ValueError: In case of unknown `data_format` argument.
    """
    return _preprocess_numpy_input(
        x,
        data_format=data_format,
        mode=mode,
        color_mode=color_mode,
        img_mean=img_mean,
        img_std=img_std,
        img_depth=img_depth,
        **kwargs,
    )
