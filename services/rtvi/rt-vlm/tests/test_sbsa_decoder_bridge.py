# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path


def test_sbsa_uses_the_deepstream_v4l2_shim():
    dockerfile = (Path(__file__).parents[1] / "docker" / "Dockerfile").read_text()

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
