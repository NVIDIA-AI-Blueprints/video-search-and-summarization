/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "libnv_encoder.h"

#include <assert.h>
#include <string.h>
#include <errno.h>

#include <algorithm>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "absl/memory/memory.h"
#include "api/scoped_refptr.h"
#include "api/video/video_content_type.h"
#include "api/video/video_frame_buffer.h"
#include "api/video/video_timing.h"
#include "api/video/video_frame_type.h"
#include "api/video_codecs/vp8_temporal_layers.h"
#include "common_video/libyuv/include/webrtc_libyuv.h"
#include "modules/video_coding/codecs/interface/common_constants.h"
#include "modules/video_coding/codecs/vp8/include/vp8.h"
#include "modules/video_coding/include/video_error_codes.h"
#include "modules/video_coding/utility/simulcast_rate_allocator.h"
#include "modules/video_coding/utility/simulcast_utility.h"
#include "rtc_base/checks.h"
#include "rtc_base/experiments/field_trial_parser.h"
#include "rtc_base/experiments/field_trial_units.h"
#include "rtc_base/trace_event.h"
#include "system_wrappers/include/field_trial.h"
#include "third_party/libyuv/include/libyuv/scale.h"

#include "modules/video_capture/linux/video_capture_linux.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>
#include <new>
#include <string>
#include <dlfcn.h>

#include "nvlibs.h"
#include "api/scoped_refptr.h"
#include "media/base/video_common.h"
#include "modules/video_capture/video_capture.h"
#include "rtc_base/logging.h"
#include "rtc_base/ref_counted_object.h"
#include "linux/videodev2.h"
#include "cudaLoader.h"
#include "config.h"

// Set to minimum to improve latency
constexpr int MIN_BUFS_OUTPUT_PLANE = 1;
constexpr int DQBUF_WAIT_ON_ERROR = 1000;  //in msecs

std::unordered_map<std::string, v4l2_enc_hw_tuning_info_type> stringToTuningInfoMap =
{
        {"high_quality"        , v4l2_enc_hw_tuning_info_type::V4L2_ENC_TUNING_INFO_HIGH_QUALITY      },
        {"low_latency"         , v4l2_enc_hw_tuning_info_type::V4L2_ENC_TUNING_INFO_LOW_LATENCY       },
        {"ultra_low_latency"   , v4l2_enc_hw_tuning_info_type::V4L2_ENC_TUNING_INFO_ULTRA_LOW_LATENCY }
};

std::unordered_map<std::string, uint32_t> stringToPresetIDMap =
{
        {"ultra_fast" , 1 },
        {"fast"       , 4 },
        {"slow"       , 7 }
};

NvVideoEncoder::NvVideoEncoder()
{
}

NvVideoEncoder::~NvVideoEncoder()
{
    // Ensure cleanup of allocated resources
    if (capturePlane != nullptr)
    {
        free(capturePlane);
        capturePlane = nullptr;
    }
    if (outputPlane != nullptr)
    {
        free(outputPlane);
        outputPlane = nullptr;
    }
}

void NvVideoEncoder::Init()
{
    int ret = 0;
    if (capturePlane == nullptr)
    {
        capturePlane = (v4l2Planes_ *)(malloc(sizeof(v4l2Planes_)));
        capturePlane->plane_name = "Capture Plane";
        InitPlane(capturePlane);
    }
    if (outputPlane == nullptr)
    {
        outputPlane = (v4l2Planes_ *)(malloc(sizeof(v4l2Planes_)));
        outputPlane->plane_name = "Output Plane";
        InitPlane(outputPlane);
    }

    /* open encoder device */
    if (encoder_fd == -1)
    {
        if (g_gpuNodePath.empty())
        {
            g_gpuNodePath = ENCODER_DEV;
#ifdef JETSON_PLATFORM
            gpu_enabled = true;
#endif
        }
        encoder_fd = NvLibs::getInstance()->v4l2_open(g_gpuNodePath.c_str(), flags | O_RDWR);
        LOG(error) << "Opening Nvidia Enc device: " << g_gpuNodePath << endl;
        if (encoder_fd == -1)
        {
#ifdef JETSON_PLATFORM
            g_gpuNodePath = ENCODER_DEV_ALT;
            encoder_fd = NvLibs::getInstance()->v4l2_open(g_gpuNodePath.c_str(), flags | O_RDWR);
            gpu_enabled = false;
            LOG(error) << "Opening Nvidia Enc device: " << g_gpuNodePath << endl;
            if (encoder_fd == -1)
            {

#endif
                /* Exiting the vst process so that it can restart the process */
                LOG(error) << "FATAL ERROR observed - Could not open device ENCODER DEV:" << g_gpuNodePath << endl;
                assert(false);
#ifdef JETSON_PLATFORM
            }
#endif
        }
    }

    unsigned int start_bitrate = DEFAULT_WEBRTC_START_BITRATE;
    Json::Value webrtc_video_quality_tunning = VmsConfigManager::getInstance()->getWebrtcVideoQualityValues(m_height);
    if (webrtc_video_quality_tunning != Json::nullValue)
    {
        start_bitrate = webrtc_video_quality_tunning["bitrate_start"].asInt() * 1000;
    }
    LOG(info) << "Height:"<< m_height <<", Setting encoder start_bitrate:" << start_bitrate << endl;
    SetRates(start_bitrate);

    ret = subscribeEvent(V4L2_EVENT_EOS, 0, 0);
    if (ret == -1)
    {
        LOG(error) << "subscribeEvent failed" << endl;
        return;
    }
    m_encStats.clear();
    m_encStats.setElementName("Video Encode");
}

void NvVideoEncoder::Deinit()
{
    int ret = 0;

    if (capturePlane != nullptr)
    {
        free(capturePlane);
        capturePlane = nullptr;
    }

    if (outputPlane != nullptr)
    {
        free(outputPlane);
        outputPlane = nullptr;
    }

    if (encoder_fd < 0)
    {
        LOG(warning) << "Invalid encoder FD instance" << endl;
        return;
    }

    ret = NvLibs::getInstance()->v4l2_close(encoder_fd);
    if (ret)
    {
        LOG(error) << "Unable to close the Encoder" << endl;
    }

    encoder_fd = -1;
    /* Encoder released.*/
    m_released = true;
}

int NvVideoEncoder::Release()
{
    LOG(info) << "Releasing Encoder " << endl;
    int ret_val = 1;
    int ret = 0;

    if(m_released)
    {
        LOG(info) << "Already released" << endl;
        return ret_val;
    }

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_STREAMOFF, &outputPlane->buf_type);
    if (ret)
    {
        LOG(error) << "Unable to STREAMOFF Output Plane" << endl;
    }

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_STREAMOFF, &capturePlane->buf_type);
    if (ret)
    {
        LOG(error) << "Unable to STREAMOFF Capture Plane" << endl;
    }

    for (uint32_t i = 0; i < capturePlane->num_buffers; i++)
    {
        unmapBuffers(capturePlane->buffers[i]);
    }

    LOG(info) << "DQing any pending Output Plane buffers" << endl;
    for (uint32_t i = 0; i < outputPlane->num_buffers; i++)
    {
        struct v4l2_buffer v4l2_buf;
        struct v4l2_plane planes[MAX_PLANES];

        NvBuffer *buffer = nullptr;

        memset(&v4l2_buf, 0, sizeof(v4l2_buf));
        memset(planes, 0, 3 * sizeof(struct v4l2_plane));

        v4l2_buf.m.planes = planes;

        if (dqBuffer(outputPlane, v4l2_buf, &buffer, nullptr, 10) >= 0 && buffer)
        {
            int fd = buffer->planes[0].fd;
            if(fd > 0)
            {
                LOG(info) << "Destroy fd from encoder : " << fd << endl;
                NvBufWrapper::getInstance()->destroyFd(fd);
            }
        }
    }

    if (reqbufs(outputPlane, 0))
    {
        LOG(error) << "Request Buffer failed on Output Plane" << endl;
        return -1;
    }
    if (reqbufs(capturePlane, 0))
    {
        LOG(error) << "Request Buffer failed on Capture Plane" << endl;
        return -1;
    }

    Deinit();
    buffer_number_ = 0;
    flags = 0;
    if (GET_CONFIG().enable_perf_logging)
    {
        m_encStats.printTotalStats();
        m_encStats.clearQueue();
    }
    LOG(info) << "Exit Encoder release" << endl;
    return ret_val;
}

int NvVideoEncoder::fill_buffer_plane_format( uint32_t *num_planes,
        NvBufferPlaneFormat *planefmts,
        uint32_t width, uint32_t height, uint32_t raw_pixfmt)
{
    switch (raw_pixfmt)
    {
        case V4L2_PIX_FMT_YUV444M:
            *num_planes = 3;

            planefmts[0].width = width;
            planefmts[1].width = width;
            planefmts[2].width = width;

            planefmts[0].height = height;
            planefmts[1].height = height;
            planefmts[2].height = height;

            planefmts[0].bytesperpixel = 1;
            planefmts[1].bytesperpixel = 1;
            planefmts[2].bytesperpixel = 1;
            break;
        case V4L2_PIX_FMT_YUV422M:
            *num_planes = 3;

            planefmts[0].width = width;
            planefmts[1].width = width / 2;
            planefmts[2].width = width / 2;

            planefmts[0].height = height;
            planefmts[1].height = height;
            planefmts[2].height = height;

            planefmts[0].bytesperpixel = 1;
            planefmts[1].bytesperpixel = 1;
            planefmts[2].bytesperpixel = 1;
            break;
        case V4L2_PIX_FMT_YUV422RM:
            *num_planes = 3;

            planefmts[0].width = width;
            planefmts[1].width = width;
            planefmts[2].width = width;

            planefmts[0].height = height;
            planefmts[1].height = height / 2;
            planefmts[2].height = height / 2;

            planefmts[0].bytesperpixel = 1;
            planefmts[1].bytesperpixel = 1;
            planefmts[2].bytesperpixel = 1;
            break;
        case V4L2_PIX_FMT_YUV420M:
        case V4L2_PIX_FMT_YVU420M:
            *num_planes = 3;

            planefmts[0].width = width;
            planefmts[1].width = width / 2;
            planefmts[2].width = width / 2;

            planefmts[0].height = height;
            planefmts[1].height = height / 2;
            planefmts[2].height = height / 2;

            planefmts[0].bytesperpixel = 1;
            planefmts[1].bytesperpixel = 1;
            planefmts[2].bytesperpixel = 1;
            break;
        case V4L2_PIX_FMT_NV12M:
            *num_planes = 2;

            planefmts[0].width = width;
            planefmts[1].width = width / 2;

            planefmts[0].height = height;
            planefmts[1].height = height / 2;

            planefmts[0].bytesperpixel = 1;
            planefmts[1].bytesperpixel = 2;
            break;
        case V4L2_PIX_FMT_GREY:
            *num_planes = 1;

            planefmts[0].width = width;

            planefmts[0].height = height;

            planefmts[0].bytesperpixel = 1;
            break;
        case V4L2_PIX_FMT_YUYV:
        case V4L2_PIX_FMT_YVYU:
        case V4L2_PIX_FMT_UYVY:
        case V4L2_PIX_FMT_VYUY:
            *num_planes = 1;

            planefmts[0].width = width;

            planefmts[0].height = height;

            planefmts[0].bytesperpixel = 2;
            break;
        case V4L2_PIX_FMT_ABGR32:
        case V4L2_PIX_FMT_XRGB32:
            *num_planes = 1;

            planefmts[0].width = width;

            planefmts[0].height = height;

            planefmts[0].bytesperpixel = 4;
            break;
        case V4L2_PIX_FMT_P010M:
            *num_planes = 2;

            planefmts[0].width = width;
            planefmts[1].width = width / 2;

            planefmts[0].height = height;
            planefmts[1].height = height / 2;

            planefmts[0].bytesperpixel = 2;
            planefmts[1].bytesperpixel = 4;
            break;
        default:
            LOG(error) << "Unsupported pixel format: " << raw_pixfmt << endl;
            return -1;
    }
    return 0;
}

void NvVideoEncoder::unmapBuffers(NvBuffer *currentBuffer)
{
    int ret = 0;
    ret = NvBufWrapper::getInstance()->unmapSurface(currentBuffer->planes[0].fd);
    if (ret < 0)
    {
        LOG(error) << "Error while Unmapping buffer" << endl;
        return;
    }
    currentBuffer->mapped = false;
}

int NvVideoEncoder::mapBuffers(NvBuffer *currentBuffer)
{
    int ret = 0;

    if (currentBuffer->mapped)
    {
        LOG(error) << "Buffer already mapped: " << currentBuffer->index << endl;
        return 0;
    }

    ret = NvBufWrapper::getInstance()->mapSurface(currentBuffer->planes[0].fd, NVBUF_MAP_READ_WRITE);
    if (ret < 0)
    {
        LOG(error) << "Error while Mapping buffer " << endl;
        return -1;
    }

    currentBuffer->planes[0].data = (unsigned char *)NvBufWrapper::getInstance()->getMappedAddr(currentBuffer->planes[0].fd, 0);
    currentBuffer->mapped = true;
    return 0;
}

void NvVideoEncoder::InitNvBuffer(NvBuffer *currentBuffer, enum v4l2_buf_type buf_type, enum v4l2_memory memory_type,
        uint32_t n_planes, NvBufferPlaneFormat * fmt, uint32_t index)
{
    uint32_t i;

    currentBuffer->buf_type = buf_type;
    currentBuffer->memory_type = memory_type;
    currentBuffer->index = index;
    currentBuffer->n_planes = std::min(n_planes, static_cast<uint32_t>(MAX_PLANES));

    currentBuffer->mapped = false;
    currentBuffer->allocated = false;

    memset(currentBuffer->planes, 0, sizeof(currentBuffer->planes));
    for (i = 0; i < currentBuffer->n_planes; i++)
    {
        currentBuffer->planes[i].fd = -1;
        currentBuffer->planes[i].fmt = fmt[i];
    }

    currentBuffer->ref_count = 0;
    pthread_mutex_init(&currentBuffer->ref_lock, nullptr);
}

void NvVideoEncoder::InitPlane(struct v4l2Planes_ * currentPlane)
{
    memset(&currentPlane->planefmts, 0, sizeof(currentPlane->planefmts));

    currentPlane->num_buffers = 0;
    currentPlane->buffers = nullptr;

    currentPlane->n_planes = 0;

    currentPlane->streamon = false;

    if (!strcmp(currentPlane->plane_name, "Capture Plane"))
    {
        currentPlane->buf_type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        currentPlane->memory_type = V4L2_MEMORY_MMAP;
    }
    if (!strcmp(currentPlane->plane_name , "Output Plane"))
    {
        currentPlane->buf_type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
        currentPlane->memory_type = V4L2_MEMORY_DMABUF;
    }
    currentPlane->buffer_count = 0;
}

int NvVideoEncoder::setOutputPlaneFormat(uint32_t pixfmt, uint32_t width,
        uint32_t height)
{
    struct v4l2_format format;
    uint32_t num_bufferplanes;
    int ret = 0;

    if (pixfmt != V4L2_PIX_FMT_YUV420M && pixfmt != V4L2_PIX_FMT_YUV444M  && pixfmt != V4L2_PIX_FMT_NV12M)
    {
        LOG(error) << "Only V4L2_PIX_FMT_YUV420M V4L2_PIX_FMT_YUV444M are supported" << endl;
        return -1;
    }

    outputPlane->pixfmt = pixfmt;
    fill_buffer_plane_format( &num_bufferplanes, outputPlane->planefmts, width,
            height, pixfmt);

    memset(&format, 0, sizeof(struct v4l2_format));
    format.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    format.fmt.pix_mp.width = width;
    format.fmt.pix_mp.height = height;
    format.fmt.pix_mp.pixelformat = pixfmt;
    format.fmt.pix_mp.num_planes = num_bufferplanes;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_FMT, &format);
    if (ret)
    {
        LOG(error) << "Error in VIDIOC_S_FMT" << endl;
    }
    else
    {
        outputPlane->n_planes = std::min<uint8_t>(format.fmt.pix_mp.num_planes, MAX_PLANES);
        for (int j = 0; j < outputPlane->n_planes; j++)
        {
            outputPlane->planefmts[j].stride = format.fmt.pix_mp.plane_fmt[j].bytesperline;
            outputPlane->planefmts[j].sizeimage = format.fmt.pix_mp.plane_fmt[j].sizeimage;
        }
    }
    return ret;
}

int NvVideoEncoder::setCapturePlaneFormat(uint32_t pixfmt, uint32_t width,
        uint32_t height, uint32_t sizeimage)
{
    int ret = 0;
    struct v4l2_format format;

    memset(&format, 0, sizeof(struct v4l2_format));

    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    format.fmt.pix_mp.pixelformat = pixfmt;
    format.fmt.pix_mp.width = width;
    format.fmt.pix_mp.height = height;
    format.fmt.pix_mp.num_planes = 1;
    format.fmt.pix_mp.plane_fmt[0].sizeimage = sizeimage;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_FMT, &format);
    if (ret)
    {
        LOG(error) << "Error in VIDIOC_S_FMT" << endl;
    }
    else
    {
        capturePlane->n_planes = std::min<uint8_t>(format.fmt.pix_mp.num_planes, MAX_PLANES);
        for (int j = 0; j < capturePlane->n_planes; j++)
        {
            capturePlane->planefmts[j].stride = format.fmt.pix_mp.plane_fmt[j].bytesperline;
            capturePlane->planefmts[j].sizeimage = format.fmt.pix_mp.plane_fmt[j].sizeimage;
        }
    }

    return ret;
}

int NvVideoEncoder::reqbufs(struct v4l2Planes_ * currentPlane, uint32_t num)
{
    struct v4l2_requestbuffers reqbuf;
    int ret = 0 ;

    memset(&reqbuf, 0, sizeof(struct v4l2_requestbuffers));
    reqbuf.count = num;
    reqbuf.type = currentPlane->buf_type;
    reqbuf.memory = currentPlane->memory_type;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_REQBUFS, &reqbuf);
    if (ret)
    {
        LOG(error) << "Error in VIDIOC_REQBUFS at output plane" << endl;
    }
    else
    {
        if (reqbuf.count)
        {
            currentPlane->buffer_count = reqbuf.count;
            currentPlane->buffers  = (NvBuffer ** )malloc(reqbuf.count*sizeof(NvBuffer *));

            for (uint32_t i = 0; i < reqbuf.count; i++)
            {
                currentPlane->buffers[i] = (NvBuffer *)malloc(sizeof(NvBuffer));
                InitNvBuffer(currentPlane->buffers[i], currentPlane->buf_type,
                        currentPlane->memory_type, currentPlane->n_planes,
                        currentPlane->planefmts, i);
            }
        }
        else
        {
            for (uint32_t i = 0; i < currentPlane->num_buffers; i++)
            {
                free(currentPlane->buffers[i]);
            }
            free(currentPlane->buffers);
            currentPlane->buffers = nullptr;
        }
        currentPlane->num_buffers = reqbuf.count;
    }
    return ret;
}

int NvVideoEncoder::queryBuffer(struct v4l2Planes_ * currentPlane, uint32_t i)
{
    struct v4l2_buffer v4l2_buf;
    struct v4l2_plane planes[MAX_PLANES];
    int ret = 0;

    memset(&v4l2_buf, 0, sizeof(struct v4l2_buffer));
    memset(planes, 0, sizeof(planes));
    v4l2_buf.index = i;
    v4l2_buf.type = currentPlane->buf_type;
    v4l2_buf.memory = currentPlane->memory_type;
    v4l2_buf.m.planes = planes;
    v4l2_buf.length = currentPlane->n_planes;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_QUERYBUF, &v4l2_buf);
    if (ret)
    {
        LOG(error) << "Error in VIDIOC_QUERYBUF for " << i << "th Buffer" << endl;
    }
    else
    {
        for (uint32_t j = 0; j < v4l2_buf.length && j < MAX_PLANES; j++)
        {
            currentPlane->buffers[i]->planes[j].length = v4l2_buf.m.planes[j].length;
            currentPlane->buffers[i]->planes[j].mem_offset =
                v4l2_buf.m.planes[j].m.mem_offset;
        }
    }

    return ret;
}

int NvVideoEncoder::exportBuffer(struct v4l2Planes_ * currentPlane, uint32_t i)
{
    struct v4l2_exportbuffer expbuf;
    int ret = 0;

    memset(&expbuf, 0, sizeof(expbuf));
    expbuf.type = currentPlane->buf_type;
    expbuf.index = i;

    for (int j = 0; j < currentPlane->n_planes && j < MAX_PLANES; j++)
    {
        expbuf.plane = j;
        ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_EXPBUF, &expbuf);
        if (ret)
        {
            LOG(error) << "Error in VIDIOC_EXPBUF for " << i << "th Buffer on Plane " << j << endl;
            return -1;
        }
        else
        {
            currentPlane->buffers[i]->planes[j].fd = expbuf.fd;
        }
    }
    return 0;
}

int NvVideoEncoder::setupPlane(struct v4l2Planes_ * currentPlane, enum v4l2_memory mem_type, uint32_t num_buffers,
        bool map, bool allocate)
{
    bool export_buffer = map;
#ifdef JETSON_PLATFORM
    export_buffer = true;
#endif
    uint32_t i;

    currentPlane->memory_type = mem_type;
    if (reqbufs(currentPlane, num_buffers))
    {
        LOG(error) << "Error in reqbufs" << endl;
        return -1;
    }

    for (i = 0; i < currentPlane->num_buffers; i++)
    {
        if (queryBuffer(currentPlane, i))
        {
            LOG(error) << "Error in queryBuffer" << endl;
            return -1;
        }
        if (export_buffer)
        {
            if (currentPlane->memory_type != V4L2_MEMORY_DMABUF)
            {
                if (exportBuffer(currentPlane, i))
                {
                    LOG(error) << "Error in exportBuffer" << endl;
                    return 0;
                }
            }
        }
#ifdef JETSON_PLATFORM
        if (map)
        {
            if (mapBuffers(currentPlane->buffers[i]))
            {
                LOG(error) << "Error in mapBuffers" << endl;
                return -1;
            }
        }
#endif
    }
    return 0;
}

int NvVideoEncoder::subscribeEvent(uint32_t type, uint32_t id, uint32_t flags)
{
    struct v4l2_event_subscription sub;
    int ret = 0;

    memset(&sub, 0, sizeof(struct v4l2_event_subscription));

    sub.type = type;
    sub.id = id;
    sub.flags = flags;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_SUBSCRIBE_EVENT, &sub);
    if (ret < 0)
    {
        LOG(error) << "Error in VIDIOC_SUBSCRIBE_EVENT" << endl;
    }

    return ret;
}

int NvVideoEncoder::setStreamStatus(struct v4l2Planes_ * currentPlane, bool status)
{
    int ret = 0 ;

    if (status)
    {
        ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_STREAMON, &currentPlane->buf_type);
    }
    else
    {
        ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_STREAMOFF, &currentPlane->buf_type);
    }
    if (ret)
    {
        LOG(error) << "Error in " << ((status) ? "STREAMON" : "STREAMOFF") << endl;
    }
    else
    {
        currentPlane->streamon = status;
    }

    return ret;
}

int NvVideoEncoder::dqBuffer(struct v4l2Planes_ * currentPlane,
                             struct v4l2_buffer &v4l2_buf, NvBuffer ** buffer,
                             NvBuffer ** shared_buffer, uint32_t num_retries)
{
    int ret = 0;

    v4l2_buf.type = currentPlane->buf_type;
    v4l2_buf.memory = currentPlane->memory_type;
    do
    {
        ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_DQBUF, &v4l2_buf);
        if (ret == 0)
        {
            if (buffer)
            {
               *buffer = currentPlane->buffers[v4l2_buf.index];
            }

            for (uint32_t i = 0; i < currentPlane->buffers[v4l2_buf.index]->n_planes && i < MAX_PLANES; i++)
            {
                currentPlane->buffers[v4l2_buf.index]->planes[i].bytesused =
                    v4l2_buf.m.planes[i].bytesused;
            }

            if (GET_CONFIG().enable_perf_logging && v4l2_buf.type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE)
            {
                m_encStats.finishProcessing();
            }
        }
        else
        {
            if (errno == EAGAIN)
            {
                if (v4l2_buf.flags & V4L2_BUF_FLAG_LAST)
                {
                    LOG(info) << "Received EAGAIN but last buffer too, so breaking loop" << endl;
                    break;
                }

                if (num_retries-- == 0)
                {
                    LOG(warning) << "Error while DQing buffer: Resource temporarily unavailable" << endl;
                    break;
                }
            }
            else
            {
                LOG(verbose2) << "Could'nt DQ buffer on " << currentPlane->plane_name << ", buffer might be already DQ'ed error code: " << ret << endl;
                ret = -1;
                break;
            }
        }
    }
    while (ret);
    return ret;
}

int NvVideoEncoder::qBuffer(struct v4l2Planes_ * currentPlane, struct v4l2_buffer &v4l2_buf, NvBuffer * shared_buffer)
{
    int ret = 0;
    NvBuffer *buffer;

    buffer = currentPlane->buffers[v4l2_buf.index];

    v4l2_buf.type = currentPlane->buf_type;
    v4l2_buf.memory = currentPlane->memory_type;
    v4l2_buf.length = currentPlane->n_planes;

    for (uint32_t i = 0; i < buffer->n_planes && i < MAX_PLANES; i++)
    {
        v4l2_buf.m.planes[i].bytesused = buffer->planes[i].bytesused;
    }

    if (GET_CONFIG().enable_perf_logging && v4l2_buf.type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE)
    {
        m_encStats.startProcessing();
    }

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_QBUF, &v4l2_buf);
    if (ret)
    {
        LOG(error) << "Error while Qing buffer on " << currentPlane->plane_name
                   << " ret: " << ret << " errno: " << errno <<  endl;
        return -1;
    }

    return ret;
}

int NvVideoEncoder::InitEncode(uint32_t width, uint32_t height, string codecString) 
{
    m_width  = width;
    m_height = height;

    LOG(info) << "Init encode for codec " << codecString << " resolution = " << width << "x" << height << endl;
    int ret = 0;
    DeviceConfig vms_config = GET_CONFIG();

#ifndef JETSON_PLATFORM
    CUresult cu_result = CUDA_SUCCESS;
    cudaError_t CUerr  = cudaSuccess;

    if (CudaLoader::getInstance()->isError())
    {
        LOG(error) << "Cuda library not present" << endl;
        return -1;
    }
    CudaLoader::getInstance()->cuInit(0);
    LOG(info) << "Init CUDA device " << g_gpuIndex << endl;
    cu_result = CudaLoader::getInstance()->cuDeviceGet(&cuDevice, g_gpuIndex);
    if (cu_result != CUDA_SUCCESS)
    {
        LOG(error) << "ENC_CTX Unable to get Cuda device\n" << cu_result << endl;
        return -1;
    }
    CUerr = CudaLoader::getInstance()->cudaSetDevice(cuDevice);
    if (CUerr != cudaSuccess)
    {
        LOG(error) << "Failed cudaSetDevice" << endl;
        return -1;
    }

    cu_result = CudaLoader::getInstance()->cuCtxGetCurrent(&cuContext);
    if (cu_result != CUDA_SUCCESS)
    {
        LOG(error) << "Failed cuCtxGetCurrent" << endl;
        return -1;
    }
    if (cuContext == nullptr)
    {
        LOG(error) << "Failed cuContext is NULL" << endl;
        return -1;
    }
#endif

    Init();

    // SetVBR ();
    setMaxPerfMode();

#ifndef JETSON_PLATFORM
    /* This is required for x86 as QP for I values when unset, shoot to 50 when there are
    *  high Motion Vectors in the frame */
    setQpRange();
#endif

    if (iequals(codecString, "H264"))
    {
        ret = setCapturePlaneFormat(V4L2_PIX_FMT_H264, m_width, m_height, 2 * 1024 * 1024);
        if(ret == -1)
        {
            LOG(error) << "Set Capture Format failed" << endl;
            return -1;
        }
    }

    ret = setOutputPlaneFormat(V4L2_PIX_FMT_NV12M, m_width, m_height);
    if(ret == -1)
    {
        LOG(error) << "Set Output Format failed" << endl;
        return -1;
    }

#ifndef JETSON_PLATFORM
    setGPUIndex(g_gpuIndex);
#endif

    DeviceConfig config =  GET_CONFIG();
    if (config.webrtc_out_enable_insert_sps_pps)
    {
        setInsertSpsPpsAtIdrEnabled(config.webrtc_out_enable_insert_sps_pps);
    }
    if (config.webrtc_out_set_idr_interval)
    {
        setIDRInterval (config.webrtc_out_set_idr_interval);
    }
    if (config.webrtc_out_set_iframe_interval)
    {
        setIFrameInterval (config.webrtc_out_set_iframe_interval);
    }

    setHWPreset();

#ifndef JETSON_PLATFORM
    setTuningInfo ();
    setCudaPresetID ();
#endif

    struct v4l2_streamparm parm;
    memset(&parm, 0, sizeof(struct v4l2_streamparm));
    parm.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    parm.parm.output.timeperframe.numerator = 1;
    parm.parm.output.timeperframe.denominator = vms_config.webrtc_in_video_sender_max_framerate;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_PARM, &parm);
    if (ret)
    {
        LOG(error) << "Error in VIDIOC_S_PARM" << endl;
        return -1;
    }

    ret = setupPlane(outputPlane, V4L2_MEMORY_DMABUF, 32, false, false);
    if(ret == -1)
    {
        LOG(error) << "O/P Setup failed" << endl;
        return -1;
    }

    ret = setupPlane(capturePlane, V4L2_MEMORY_MMAP, 10, true, false);
    if(ret == -1)
    {
        LOG(error) << "C/P Setup failed" << endl;
        return -1;
    }

    {
        ret = setStreamStatus(outputPlane, true);
        if(ret == -1)
        {
            LOG(error) << "setStreamStatus failed" << endl;
            return -1;
        }
    }
    {
        ret = setStreamStatus(capturePlane, true);
        if(ret == -1)
        {
            LOG(error) << "setStreamStatus failed" << endl;
            return -1;
        }
    }
    // Enqueue all the empty capture plane buffers
    for (uint32_t i = 0; i < capturePlane->num_buffers; i++)
    {
        struct v4l2_buffer v4l2_buf;
        struct v4l2_plane planes[MAX_PLANES];

        memset(&v4l2_buf, 0, sizeof(v4l2_buf));
        memset(planes, 0, MAX_PLANES * sizeof(struct v4l2_plane));

        v4l2_buf.index = i;
        v4l2_buf.m.planes = planes;

        ret = qBuffer(capturePlane, v4l2_buf, nullptr);
        if (ret < 0)
        {
            LOG(error) << "qBuffer on C/P Plane failed" << endl;
        }
    }
    m_released = false;
    return 1;
}

int NvVideoEncoder::setQpRange()
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    v4l2_ctrl_video_qp_range qprange;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    /* Default values */
    unsigned int minQpI = 10, maxQpI = 30;
    unsigned int minQpP = 10, maxQpP = 30;
    unsigned int minQpB = 10, maxQpB = 30;

    Json::Value webrtc_video_quality_tunning = VmsConfigManager::getInstance()->getWebrtcVideoQualityValues(m_height);
    if (webrtc_video_quality_tunning != Json::nullValue)
    {
        std::vector<int> qp_range_I = jsonArrayToVector(webrtc_video_quality_tunning["qp_range_I"]);
        if (qp_range_I.size() == 2)
        {
            minQpI = qp_range_I[0];
            maxQpI = qp_range_I[1];
        }

        std::vector<int> qp_range_P = jsonArrayToVector(webrtc_video_quality_tunning["qp_range_P"]);
        if (qp_range_P.size() == 2)
        {
            minQpP = minQpB = qp_range_P[0];
            maxQpP = maxQpB = qp_range_P[1];
        }
    }
    LOG(info) << "Height:"<< m_height <<", I_QP values: {"<<minQpI<<","<<maxQpI<<"}  P_QP_values: {"<<minQpP<<","<<maxQpP<<"}"<<endl;

    qprange.MinQpI = minQpI;
    qprange.MaxQpI = maxQpI;
    qprange.MinQpP = qprange.MinQpB = minQpP;
    qprange.MaxQpP = qprange.MaxQpB = maxQpP;

    control.id = V4L2_CID_MPEG_VIDEOENC_QP_RANGE;
    control.string = (char *)&qprange;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);
    if (ret < 0)
    {
        LOG(error) << "Set setQpRange failed" << endl;
        return -1;
    }
    else
    {
        LOG(info) << "Set setQpRange success" << endl;
    }
    return 0;
}

void NvVideoEncoder::SetVBR()
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));
    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEO_BITRATE_MODE;
    control.value = V4L2_MPEG_VIDEO_BITRATE_MODE_VBR;
    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set VBR failed";
    }
    else
    {
        LOG(info) << "Set VBR success";
    }
}

void NvVideoEncoder::setCudaPresetID()
{
    string preset_string = GET_CONFIG().webrtc_out_enc_preset;
    auto it = stringToPresetIDMap.find(preset_string);

    uint32_t preset_id = (it != stringToPresetIDMap.end()) ? (static_cast<int>(it->second)) : (1);

    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));


    control.id = V4L2_CID_MPEG_VIDEOENC_CUDA_PRESET_ID;
    control.value = preset_id;

    ctrls.count = 1;
    ctrls.controls = &control;
    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set Preset ID failed, value = " << preset_id << endl;
    }
    else
    {
        LOG(info) << "Set Preset ID success, value = " << preset_id << endl;
    }
}

void NvVideoEncoder::setTuningInfo()
{
    string tuning_info_string = GET_CONFIG().webrtc_out_enc_quality_tuning;
    auto it = stringToTuningInfoMap.find(tuning_info_string);

    uint32_t tuning_info = (it != stringToTuningInfoMap.end()) ? (static_cast<int>(it->second)) : (v4l2_enc_hw_tuning_info_type::V4L2_ENC_TUNING_INFO_ULTRA_LOW_LATENCY);

    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));


    control.id = V4L2_CID_MPEG_VIDEOENC_CUDA_TUNING_INFO;
    control.value = tuning_info;

    ctrls.count = 1;
    ctrls.controls = &control;
    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set Tuning info failed, value = " << tuning_info << endl;
    }
    else
    {
        LOG(info) << "Set Tuning info success, value = " << tuning_info << endl;
    }
}

void NvVideoEncoder::SetRates(unsigned int target_bitrate_bps) 
{
    LOG(verbose2) << "Set Rates";
    if(!target_bitrate_bps)
    {
        /* Setting target bitrate 1Mbps */
        target_bitrate_bps = 1000000;
    }
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));
    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEO_BITRATE;
    control.value = target_bitrate_bps;
    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set Rates failed";
    }
    else
    {
        LOG(verbose2) << "Set Rates success";
    }
}

int
NvVideoEncoder::setIFrameInterval(uint32_t interval)
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEO_GOP_SIZE;
    control.value = interval;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set Iframe Interval failed" << endl;
        return -1;
    }
    else
    {
        LOG(info) << "Set Iframe Interval success" << endl;
    }
    return 0;
}

int NvVideoEncoder::setHWPreset()
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEOENC_HW_PRESET_TYPE_PARAM;
    control.value = V4L2_ENC_HW_PRESET_ULTRAFAST;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set HW Preset failed";
        return -1;
    }
    else
    {
        LOG(info) << "Set HW Preset success";
    }
    return 0;
}

int
NvVideoEncoder::setIDRInterval(uint32_t interval)
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEO_IDR_INTERVAL;
    control.value = interval;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set IDRInterval failed" << endl;
        return -1;
    }
    else
    {
        LOG(info) << "Set IDRInterval success" << endl;
    }
    return 0;
}

int
NvVideoEncoder::forceIDR()
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEOENC_FORCE_IDR_FRAME;
    control.value = 1;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Force IDR failed";
        return -1;
    }
    else
    {
        LOG(verbose2) << "Force IDR success" << endl;
    }
    return 0;
}

int
NvVideoEncoder::setInsertSpsPpsAtIdrEnabled(bool enabled)
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEOENC_INSERT_SPS_PPS_AT_IDR;
    control.value = enabled;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set InsertSpsPpsAtIdrEnabled failed" << endl;
        return -1;
    }
    else
    {
        LOG(info) << "Set InsertSpsPpsAtIdrEnabled success" << endl;
    }
    return 0;
}

int NvVideoEncoder::setMaxPerfMode()
{
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;

    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));

    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;

    control.id = V4L2_CID_MPEG_VIDEO_MAX_PERFORMANCE;
    control.value = true;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set Rates failed" << endl;
    }
    else
    {
        LOG(verbose) << "Set Rates success" << endl;
    }

    return 0;
}

void NvVideoEncoder::setGPUIndex(int index)
{
    LOG(info) << "Set GPU Index " << index << endl;
    struct v4l2_ext_control control;
    struct v4l2_ext_controls ctrls;
    memset(&control, 0, sizeof(control));
    memset(&ctrls, 0, sizeof(ctrls));
    ctrls.count = 1;
    ctrls.controls = &control;
    ctrls.ctrl_class = V4L2_CTRL_CLASS_MPEG;
    control.id = V4L2_CID_MPEG_VIDEO_CUDA_GPU_ID;
    control.value = index;

    int ret;
    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_S_EXT_CTRLS, &ctrls);

    if (ret < 0)
    {
        LOG(error) << "Set GPU Index failed" << endl;
    }
    else
    {
        LOG(info) << "Set GPU Index success" << endl;
    }
}

int NvVideoEncoder::GetEncodedPartitions(unsigned char** data, ssize_t *size, bool last_buffers)
{
    int ret = 0;
    int result = -1;

    struct v4l2_buffer v4l2_capture_buf;
    struct v4l2_plane capture_planes[MAX_PLANES];
    NvBuffer *capplane_buffer = nullptr;
    bool capture_dq_continue = true;
    memset(&v4l2_capture_buf, 0, sizeof(v4l2_capture_buf));
    memset(capture_planes, 0, sizeof(capture_planes));
    v4l2_capture_buf.m.planes = capture_planes;
    v4l2_capture_buf.length = 1;
    if (capturePlane == nullptr)
    {
        LOG(warning) << "Capture plane is NULL" << endl;
        return -1;
    }
    result = dqBuffer(capturePlane, v4l2_capture_buf, &capplane_buffer, nullptr, 10);
    if (result < 0)
    {
        LOG(warning) << "Buffers not yet available on C/P plane " << result << endl;
        return -1;
    }
    else
    {
        if (v4l2_capture_buf.flags & V4L2_BUF_FLAG_LAST)
        {
            LOG(info) << "Recieved Last buffer ACK" << endl;
            result = 1;
        }
    }
    if (capplane_buffer == nullptr)
    {
        LOG(error) << "capplane_buffer is NULL" << endl;
        return -1;
    }
    *data = (uint8_t* )malloc(capplane_buffer->planes[0].bytesused);
    *size =  capplane_buffer->planes[0].bytesused;
#ifdef JETSON_PLATFORM
    memcpy(*data,  capplane_buffer->planes[0].data,  capplane_buffer->planes[0].bytesused);
#else
    void* surface_data_ptr = NvBufWrapper::getInstance()->extractSurface(capplane_buffer->planes[0].fd);
    if (surface_data_ptr)
    {
        memcpy(*data,  (unsigned char*)surface_data_ptr,  capplane_buffer->planes[0].bytesused);
    }
#endif

    if (!last_buffers)
    {
        if (!capture_dq_continue)
        {
            LOG(warning) << "Capture plane dequeued 0 size buffer" << endl;
        }
        ret= qBuffer(capturePlane, v4l2_capture_buf, nullptr);
        if (ret < 0)
        {
            LOG(warning) << "qBuffer at capture plane failed" << endl;
        }
    }

    return result;
}

FD_Index_Pair NvVideoEncoder::Encode(std::pair<int, int> fd_index_pair, unsigned char** data,
                           ssize_t *sized) 
{
    FD_Index_Pair return_pair (-1, -1) ;
    if (m_released)
    {
        LOG(warning) << "Encoder already released" << endl;
        return return_pair;
    }
    struct v4l2_buffer v4l2_buf;
    struct v4l2_plane planes[MAX_PLANES];
    int retn = 0;

    NvBuffer *buffer = nullptr;

    memset(&v4l2_buf, 0, sizeof(v4l2_buf));
    memset(planes, 0, 3 * sizeof(struct v4l2_plane));

    v4l2_buf.m.planes = planes;
    if (outputPlane)
    {
        if (buffer_number_ >= MIN_BUFS_OUTPUT_PLANE)
        {
            if (dqBuffer(outputPlane, v4l2_buf, &buffer, nullptr, 10) < 0)
            {
                LOG(warning) << "ERROR while DQing buffer at output plane" << endl;
            }
            else if (buffer)
            {
                return_pair = std::make_pair(buffer->planes[0].fd, buffer->index);
            }
        }
        else    // impossible condition: this will never be called
        {
            if (buffer_number_ >= outputPlane->num_buffers)
            {
                LOG(warning) << "Buffer number  >= outputPlane->num_buffers " << endl;
                buffer = nullptr;
            }
            else
            {
                buffer = outputPlane->buffers[buffer_number_];
            }
        }
        int incoming_index = fd_index_pair.second;
        if (incoming_index >= 0 && incoming_index < outputPlane->buffer_count)
        {
            v4l2_buf.index = incoming_index;
            buffer_number_++;
            buffer = outputPlane->buffers[v4l2_buf.index];
            buffer->index = v4l2_buf.index;
            int fd = fd_index_pair.first;

            if (fd > 0)
            {
                buffer->planes[0].fd = fd;
                buffer->planes[1].fd = fd;
                buffer->planes[2].fd = fd;

                uint32_t frame_size = m_width * m_height;
                v4l2_buf.m.planes[0].bytesused = frame_size;
                v4l2_buf.m.planes[1].bytesused = frame_size / 4;
                v4l2_buf.m.planes[2].bytesused = frame_size / 4;
                buffer->planes[0].bytesused = v4l2_buf.m.planes[0].bytesused;
                buffer->planes[1].bytesused = v4l2_buf.m.planes[1].bytesused;
                buffer->planes[2].bytesused = v4l2_buf.m.planes[2].bytesused;
                v4l2_buf.m.planes[0].m.fd = buffer->planes[0].fd;
                v4l2_buf.m.planes[1].m.fd = buffer->planes[1].fd;
                v4l2_buf.m.planes[2].m.fd = buffer->planes[2].fd;

                retn = qBuffer(outputPlane, v4l2_buf, nullptr);
                if (retn < 0)
                {
                    LOG(warning) << "qbuffer on output plane failed fd = " << fd_index_pair.first 
                            << " v4l2_buf.index : " << v4l2_buf.index << endl;

                }
            }
        }
        else
        {
            LOG(error) << "Invalid buffer index = " << incoming_index << " for fd = " << fd_index_pair.first << endl;
        }
    }
    if (buffer_number_ >= MIN_BUFS_OUTPUT_PLANE)
    {
        GetEncodedPartitions(data, sized);
    }
    return return_pair;
}

int NvVideoEncoder::sendLastBuffer()
{
    int ret = -1;
    struct v4l2_buffer v4l2_buf;
    struct v4l2_plane planes[MAX_PLANES];

    memset(&v4l2_buf, 0, sizeof(v4l2_buf));
    memset(planes, 0, 3 * sizeof(struct v4l2_plane));

    v4l2_buf.m.planes = planes;
    v4l2_buf.m.planes[0].m.userptr = 0;
    v4l2_buf.m.planes[0].bytesused = 0;
    v4l2_buf.m.planes[1].bytesused = 0;
    v4l2_buf.m.planes[2].bytesused = 0;
    v4l2_buf.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    v4l2_buf.memory = V4L2_MEMORY_DMABUF;

    ret = NvLibs::getInstance()->v4l2_ioctl(encoder_fd, VIDIOC_QBUF, &v4l2_buf);
    if (ret < 0)
    {
        LOG(error) << "Error while Qing buffer on output plane"
                   << " ret: " << ret << " errno: " << errno <<  endl;
        return -1;
    }
    else if (ret == 0)
    {
        if(v4l2_buf.m.planes[0].bytesused == 0)
        {
            LOG(info) << "Last buffer queued success" << endl;
            ret = 0;
        }
    }
    return ret;
}

int NvVideoEncoder::dqueueLastBuffers(unsigned char** data, ssize_t *size)
{
    return GetEncodedPartitions(data, size, true);
}
