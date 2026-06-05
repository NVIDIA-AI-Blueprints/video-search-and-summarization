/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <dlfcn.h>
#include <fcntl.h>
#include "logger.h"
#include "nvlibs.h"
#include <linux/videodev2.h>
#include "v4l2_nv_extensions.h"

#ifdef JETSON_PLATFORM
// dev-nodes for encoder and decoder
#define  V4L2_DEVICE_PATH_NVDEC       "/dev/nvhost-nvdec"
#define  V4L2_DEVICE_PATH_NVDEC_ALT   "/dev/v4l2-nvdec"
#define  V4L2_DEVICE_PATH_NVENC       "/dev/nvhost-msenc"
#define  V4L2_DEVICE_PATH_NVENC_ALT   "/dev/v4l2-nvenc"
#endif

#define ABSOLUTE_LIBRARY_PATH_X86_64 "/usr/lib/x86_64-linux-gnu/libv4l2.so.0"
#define ABSOLUTE_LIBRARY_PATH_ARCH64 "/usr/lib/aarch64-linux-gnu/libv4l2.so.0"

NvLibs* NvLibs::_instance = NULL;

NvLibs* NvLibs::getInstance()
{
    if(_instance == NULL)
    {
        _instance = new NvLibs();
    }
    return _instance;
}

NvLibs::NvLibs()
    : v4l2_ioctl(nullptr)
    , v4l2_open(nullptr)
    , v4l2_close(nullptr)
    , error(false)
    , handle_v4l2(nullptr)
{
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = ABSOLUTE_LIBRARY_PATH_ARCH64;
#else
    lib_path = ABSOLUTE_LIBRARY_PATH_X86_64;
#endif
    handle_v4l2 = dlopen(lib_path, RTLD_LAZY); 
    if (!handle_v4l2)
    {
        LOG(error) << "Cannot open v4l2 library: " << dlerror() << endl;
        error = true;
    }
    else
    {
        dlerror();
        v4l2_ioctl = (v4l2_ioctl_t) dlsym(handle_v4l2, "v4l2_ioctl");
        v4l2_open = (v4l2_open_t) dlsym(handle_v4l2, "v4l2_open");
        v4l2_close = (v4l2_close_t) dlsym(handle_v4l2, "v4l2_close");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbol 'v4l2_ioctl_': " << dlsym_error << endl;
            dlclose(handle_v4l2);
        }
    }
}

NvLibs::~NvLibs()
{
    dlclose(handle_v4l2);
}

#ifdef JETSON_PLATFORM
bool NvLibs::isV4l2EncPresent()
{
    if (access (V4L2_DEVICE_PATH_NVENC, F_OK) == 0)
    {
        return true;
    }
    else if (access (V4L2_DEVICE_PATH_NVENC_ALT, F_OK) == 0)
    {
        return true;
    }
    return false;
}

bool NvLibs::isV4l2DecPresent()
{
    if (access (V4L2_DEVICE_PATH_NVDEC, F_OK) == 0)
    {
        return true;
    }
    else if (access (V4L2_DEVICE_PATH_NVDEC_ALT, F_OK) == 0)
    {
        return true;
    }
    return false;
}
#else
bool NvLibs::isV4l2EncPresent()
{
    int ret = 0;
    struct v4l2_capability encoder_caps;
    int fd = -1;

    if (g_gpuNodePath.empty())
    {
        return false;
    }
    fd = v4l2_open(g_gpuNodePath.c_str(), O_RDWR);
    if (fd == -1)
    {
        LOG(error) << "Could not open device ENCODER DEV" << endl;
        return false;
    }

    ret = v4l2_ioctl(fd, VIDIOC_QUERYCAP, &encoder_caps);
    if (ret != 0 || !(encoder_caps.capabilities & V4L2_CAP_VIDEO_M2M_MPLANE))
    {
        LOG(error) << "Query caps failed" << endl;
        v4l2_close(fd);
        return false;
    }
    struct v4l2_format format;
    memset(&format, 0, sizeof (struct v4l2_format));
    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    format.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_H264;
    format.fmt.pix_mp.width = 1920;
    format.fmt.pix_mp.height = 1080;
    format.fmt.pix_mp.num_planes = 1;
    format.fmt.pix_mp.plane_fmt[0].sizeimage = (2 * 1024 * 1024);

    ret = v4l2_ioctl(fd, VIDIOC_S_FMT, &format);
    if (ret != 0)
    {
        LOG(error) << "Set fmt ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }

    memset(&format, 0, sizeof (struct v4l2_format));
    format.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    format.fmt.pix_mp.width = 1920;
    format.fmt.pix_mp.height = 1080;
    format.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12M;
    format.fmt.pix_mp.num_planes = 2;

    ret = v4l2_ioctl(fd, VIDIOC_S_FMT, &format);
    if (ret != 0)
    {
        LOG(error) << "Set fmt ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }

    struct v4l2_streamparm parms;

    memset(&parms, 0, sizeof(parms));
    parms.parm.output.timeperframe.numerator = 1;
    parms.parm.output.timeperframe.denominator = 30;
    parms.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;

    ret = v4l2_ioctl(fd, VIDIOC_S_PARM, &parms);
    if (ret != 0)
    {
        LOG(error) << "Set param ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }
    v4l2_close(fd);
    return true;
}

bool NvLibs::isV4l2DecPresent()
{
    int ret = 0;
    struct v4l2_capability decoder_caps;
    int fd = -1;
    struct v4l2_format format;
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    if (g_gpuNodePath.empty())
    {
        return false;
    }
    fd = v4l2_open(g_gpuNodePath.c_str(), O_RDWR);
    if (fd == -1)
    {
        LOG(error) << "Could not open device DECODER DEV" << endl;
        return false;
    }

    ret = v4l2_ioctl(fd, VIDIOC_QUERYCAP, &decoder_caps);
    if (ret != 0)
    {
        LOG(error) << "Query caps failed" << endl;
        v4l2_close(fd);
        return false;
    }

    memset(&format, 0, sizeof (struct v4l2_format));
    format.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    format.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_H264;
    format.fmt.pix_mp.num_planes = 1;
    format.fmt.pix_mp.plane_fmt[0].sizeimage = (4 * 1024 * 1024);

    ret = v4l2_ioctl(fd, VIDIOC_S_FMT, &format);
    if (ret != 0)
    {
        LOG(error) << "Set fmt ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }

    memset(&format, 0, sizeof (struct v4l2_format));
    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    format.fmt.pix_mp.width = 1920;
    format.fmt.pix_mp.height = 1080;
    format.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12M;
    format.fmt.pix_mp.num_planes = 2;

    ret = v4l2_ioctl(fd, VIDIOC_S_FMT, &format);
    if (ret != 0)
    {
        LOG(error) << "Set fmt ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    control.id = V4L2_CID_MPEG_VIDEO_CUDA_GPU_ID;
    control.value = 0;
    ctrls.count = 1;
    ctrls.controls = &control ;
    ret = v4l2_ioctl (fd, VIDIOC_S_EXT_CTRLS, &ctrls);
    if (ret != 0)
    {
        LOG(error) << "Set ext ctrl gpu ID ioctl failed" << endl;
        v4l2_close(fd);
        return false;
    }
    v4l2_close(fd);
    return true;
}
#endif