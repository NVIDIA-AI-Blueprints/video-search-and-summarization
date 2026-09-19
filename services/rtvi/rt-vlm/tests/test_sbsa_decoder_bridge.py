# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path


def _dockerfile() -> str:
    return (Path(__file__).parents[1] / "docker" / "Dockerfile").read_text()


def test_non_sbsa_build_uses_empty_sbsa_source_stage():
    dockerfile = _dockerfile()

    assert dockerfile.index('ARG ARM_PLATFORM="sbsa"') < dockerfile.index("FROM ")
    assert "FROM scratch AS sbsa-media-igpu" in dockerfile
    assert "FROM ${SBSA_MEDIA_IMAGE} AS sbsa-media-sbsa" in dockerfile
    assert "FROM sbsa-media-${ARM_PLATFORM} AS sbsa-media-runtime" in dockerfile
    assert "FROM ${SBSA_MEDIA_IMAGE} AS sbsa-media-runtime" not in dockerfile

    repository_root = Path(__file__).resolve().parents[4]
    inventory = json.loads(
        (repository_root / "deploy" / "docker" / "container-inventory.json").read_text()
    )
    non_sbsa_image = next(
        image for image in inventory["images"] if image["name"] == "vss-rt-vlm"
    )
    assert non_sbsa_image["build_args"]["SBSA_MEDIA_IMAGE"] == "scratch"


def test_deepstream_install_and_cleanup_share_one_layer():
    dockerfile = _dockerfile()
    start = dockerfile.index("# Install or copy DeepStream")
    end = dockerfile.index("# The SBSA DeepStream image supplies", start)
    deepstream_layer = dockerfile[start:end]

    assert deepstream_layer.count("RUN ") == 1
    assert "deepstream_sdk_v9.1.0_x86_64.tbz2" in deepstream_layer
    assert "deepstream_sdk_v9.1.0_jetson.tbz2" in deepstream_layer
    assert (
        "&& rm -rf /usr/lib/*-linux-gnu/gstreamer-1.0/libgstaudioparsers.so" in deepstream_layer
    )


def test_sbsa_uses_the_deepstream_v4l2_shim():
    dockerfile = _dockerfile()

    assert "source_v4l2_shim=/tmp/sbsa-root/usr/lib/aarch64-linux-gnu/tegra/libnvv4l2.so" in dockerfile
    assert "source_v4l_plugins=/tmp/sbsa-root/usr/lib/aarch64-linux-gnu/libv4l/plugins/nv" in dockerfile
    assert 'cp -a "$source_v4l_plugins" /usr/lib/aarch64-linux-gnu/libv4l/plugins/nv' in dockerfile
    shim_link = "ln -sfn /usr/lib/aarch64-linux-gnu/tegra/libnvv4l2.so /usr/lib/aarch64-linux-gnu/libv4l2.so.0.0.999999"
    dispatch_link = "ln -sfn libv4l2.so.0.0.999999 /usr/lib/aarch64-linux-gnu/libv4l2.so.0"
    unversioned_link = "ln -sfn libv4l2.so.0 /usr/lib/aarch64-linux-gnu/libv4l2.so"
    assert shim_link in dockerfile
    assert dispatch_link in dockerfile
    assert unversioned_link in dockerfile
    assert dockerfile.rindex(dispatch_link) > dockerfile.index("RUN cd /usr && find .")
    assert "/usr/lib/*-linux-gnu/gstreamer-1.0/libgstnvcodec.so" in dockerfile
    assert "/usr/lib/*-linux-gnu/libavcodec*" in dockerfile
