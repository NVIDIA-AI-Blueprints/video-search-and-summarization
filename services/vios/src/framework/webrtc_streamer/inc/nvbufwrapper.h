/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#pragma once

#include <cstdint>
#include <memory>
#include <dlfcn.h>
#include "logger.h"
#include "nvbuf_utils.h"
#include "nvbufsurface.h"
#include "nvbufsurftransform.h"
#include "media_consumer.h"
#include "gst_utils.h"
#include "webrtc_headers/src/common_video/include/video_frame_buffer.h"
#include "webrtc_headers/src/api/video/video_frame_buffer.h"
#include "webrtc_headers/src/api/video/i420_buffer.h"
#include "nvhwdetection.h"

#ifdef USE_NV_ENC
#include "modules/video_coding/codecs/nvidia/NvVideoFrameBuffer.h"
#endif

#define DL_ERROR_EXIT  { char *dlsym_error = dlerror(); \
                            if (dlsym_error) { \
                            LOG(error) << "Cannot load symbol " <<  dlsym_error << endl; \
                            goto close_dl; } }

typedef int (*NvBufSurfaceCopy_t) (NvBufSurface *, NvBufSurface *);
typedef int (*NvBufSurfaceCreate_t) (NvBufSurface **, uint32_t, NvBufSurfaceCreateParams *);
typedef int (*NvBufSurfaceDestroy_t) (NvBufSurface *);
typedef int (*NvBufSurfaceFromFd_t) (int, void**);
typedef int (*NvBufSurfaceAllocate_t) (NvBufSurface **, uint32_t, NvBufSurfaceAllocateParams *);

typedef int (*NvBufSurfaceMap_t) (NvBufSurface *, int, int,  NvBufSurfaceMemMapFlags);
typedef int (*NvBufSurfaceSyncForCpu_t) (NvBufSurface *, int, int);
typedef int (*NvBufSurfaceUnMap_t) (NvBufSurface *, int, int);

typedef NvBufSurfTransform_Error (*NvBufSurfTransform_t) (NvBufSurface *, NvBufSurface *, NvBufSurfTransformParams *);
typedef int (*NvBufSurfTransformSetSessionParams_t) (NvBufSurfTransformConfigParams*);
typedef int (*NvBufSurfTransformSetDefaultSession_t) (void);
typedef NvBufSurfTransform_Error (*NvBufSurfTransformMultiInputBufCompositeBlend_t) (NvBufSurface **src, NvBufSurface *dst, NvBufSurfTransformCompositeBlendParamsEx *composite_blend_params);
#ifdef JETSON_PLATFORM
typedef int (*NvBufSurface2Raw_t) (NvBufSurface*, unsigned int, unsigned int, unsigned int, unsigned int, unsigned char*);
#endif

enum NvBufferMode
{
    NvBufferModeSoftware = 0,
    NvBufferModeHardwareSurface = 1,
    NvBufferModeInvalid = 0xFFF
};

struct InputBufferType
{
    InputBufferType()
    {
        m_inputBuffer = nullptr;
        m_inputFD = -1;
    }
    unsigned char *m_inputBuffer;
    int m_inputFD;
};
class NvBufWrapper 
{
    public:
        static NvBufWrapper* getInstance()
        {
            static NvBufWrapper _instance;
            return &_instance;
        }

        NvBufWrapper()
            : NvBufSurfaceCopy (nullptr)
            , NvBufSurfaceFromFd (nullptr)
            , NvBufSurfTransform (nullptr)
            , NvBufSurfTransformSetSessionParams (nullptr)
            , NvBufSurfTransformSetDefaultSession(nullptr)
#ifdef JETSON_PLATFORM
            , NvBufSurface2Raw (nullptr)
#endif
            , m_nvBufferMode (NvBufferModeHardwareSurface)
            , handle_nvbufsurface_utils (nullptr)
            , handle_nvbufsurfacetransform_utils (nullptr)
            , NvBufSurfaceCreate (nullptr)
            , NvBufSurfaceDestroy (nullptr)
            , NvBufSurfaceAllocate(nullptr)
            , NvBufSurfaceMap(nullptr)
            , NvBufSurfaceSyncForCpu(nullptr)
            , NvBufSurfaceUnMap(nullptr)
        {
            LOG(info) << __func__ << endl;
            // Calling getInstance first, for correct prints
            NvHwDetection::getInstance();
            LOG (info) << "m_useNvV4l2Enc:" << NvHwDetection::getInstance()->m_useNvV4l2Enc <<
                        ", m_useNvV4l2Dec:" << NvHwDetection::getInstance()->m_useNvV4l2Dec << endl;

            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == false &&
                NvHwDetection::getInstance()->m_useNvV4l2Dec == false)
            {
                m_nvBufferMode = NvBufferModeSoftware;
                LOG(info) << "Nv Buffer format: " << m_nvBufferMode << endl;
                return;
            }

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
            handle_nvbufsurface_utils = dlopen("/usr/lib/aarch64-linux-gnu/nvidia/libnvbufsurface.so", RTLD_LAZY);
            handle_nvbufsurfacetransform_utils = dlopen("/usr/lib/aarch64-linux-gnu/nvidia/libnvbufsurftransform.so", RTLD_LAZY);
#else
            handle_nvbufsurface_utils = dlopen("/usr/lib/x86_64-linux-gnu/libnvbufsurface.so", RTLD_LAZY);
            handle_nvbufsurfacetransform_utils = dlopen("/usr/lib/x86_64-linux-gnu/libnvbufsurftransform.so", RTLD_LAZY);
#endif
            if (handle_nvbufsurface_utils && handle_nvbufsurfacetransform_utils)
            {
                /* Surface core library functions */
                dlerror();
                NvBufSurfaceCopy = (NvBufSurfaceCopy_t) dlsym(handle_nvbufsurface_utils, "NvBufSurfaceCopy");
                DL_ERROR_EXIT
                NvBufSurfaceCreate = (NvBufSurfaceCreate_t) dlsym(handle_nvbufsurface_utils, "NvBufSurfaceCreate");
                DL_ERROR_EXIT
                NvBufSurfaceDestroy = (NvBufSurfaceDestroy_t) dlsym(handle_nvbufsurface_utils, "NvBufSurfaceDestroy");
                DL_ERROR_EXIT
                NvBufSurfaceFromFd = (NvBufSurfaceFromFd_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurfaceFromFd");
                DL_ERROR_EXIT
                NvBufSurfaceAllocate = (NvBufSurfaceAllocate_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurfaceAllocate");
                DL_ERROR_EXIT

                NvBufSurfaceMap = (NvBufSurfaceMap_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurfaceMap");
                DL_ERROR_EXIT
                NvBufSurfaceSyncForCpu = (NvBufSurfaceSyncForCpu_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurfaceSyncForCpu");
                DL_ERROR_EXIT
                NvBufSurfaceUnMap = (NvBufSurfaceUnMap_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurfaceUnMap");
                DL_ERROR_EXIT
#ifdef JETSON_PLATFORM
                NvBufSurface2Raw = (NvBufSurface2Raw_t)  dlsym(handle_nvbufsurface_utils, "NvBufSurface2Raw");
                DL_ERROR_EXIT
#endif

                /* Surface transform library functions */
                dlerror();
                NvBufSurfTransform = (NvBufSurfTransform_t) dlsym(handle_nvbufsurfacetransform_utils, "NvBufSurfTransform");
                DL_ERROR_EXIT
                NvBufSurfTransformSetSessionParams = (NvBufSurfTransformSetSessionParams_t) dlsym(handle_nvbufsurfacetransform_utils, "NvBufSurfTransformSetSessionParams");
                DL_ERROR_EXIT
                NvBufSurfTransformMultiInputBufCompositeBlend = (NvBufSurfTransformMultiInputBufCompositeBlend_t) dlsym(handle_nvbufsurfacetransform_utils, "NvBufSurfTransformMultiInputBufCompositeBlend");
                DL_ERROR_EXIT
                NvBufSurfTransformSetDefaultSession = (NvBufSurfTransformSetDefaultSession_t) dlsym(handle_nvbufsurfacetransform_utils, "NvBufSurfTransformSetDefaultSession");
                DL_ERROR_EXIT

                /* WAR to initialize pthread key nvbufsurftransform_key, in libnvbufsurftransform.so to avoid crash */
                NvBufSurfTransformSetDefaultSession();
                m_nvBufferMode = NvBufferModeHardwareSurface;
            }
            else
            {
                goto close_dl;
            }
            LOG(info) << "Nv Buffer format: " << m_nvBufferMode << endl;
            return;

        close_dl:
                LOG(error) << "Error loading the plugins, default buffer format: " << m_nvBufferMode << endl;
                if (handle_nvbufsurface_utils)  dlclose(handle_nvbufsurface_utils);
                if (handle_nvbufsurfacetransform_utils)  dlclose(handle_nvbufsurfacetransform_utils);
                throw std::runtime_error("An exception occurred, error loading the NvBuf libraries");
        }

        ~NvBufWrapper()
        {
            LOG(info) << "Destructor NvBufWrapper::~NvBufWrapper" << endl;
            if (handle_nvbufsurface_utils)  dlclose(handle_nvbufsurface_utils);
            if (handle_nvbufsurfacetransform_utils)  dlclose(handle_nvbufsurfacetransform_utils);
        }

        int getFDAndDoTransformIfNeeded(InputBufferType &buffer_type, uint32_t sourceWidth, uint32_t sourceHeight,
                                uint32_t targetWidth, uint32_t targetHeight,
                                bool software_mode = false, int* fd = nullptr, bool *ret_transform = nullptr)
        {
            int ret = 0;
            uint32_t width = 0, height = 0;
            bool is_transformed_needed = false;
            is_transformed_needed = (targetWidth * targetHeight) < (sourceWidth * sourceHeight);
            if (is_transformed_needed)
            {
                width = targetWidth;
                height = targetHeight;
            }
            else
            {
                width = sourceWidth;
                height = sourceHeight;
            }

            if (m_nvBufferMode == NvBufferModeSoftware || software_mode)
            {
                is_transformed_needed = true;
                NvBufSurface *sw_surf   = (NvBufSurface *) calloc (1, sizeof(NvBufSurface));
                sw_surf->surfaceList    = (NvBufSurfaceParams *) calloc (1, sizeof(NvBufSurfaceParams));

                sw_surf->gpuId                                   = g_gpuIndex;
                sw_surf->batchSize                               = 1;
                sw_surf->numFilled                               = 1;
                sw_surf->memType                                 = NVBUF_MEM_SYSTEM;

                // number of planes is 3 for I420
                sw_surf->surfaceList->planeParams.num_planes     = 3;
                sw_surf->surfaceList->pitch                      = sourceWidth;
                sw_surf->surfaceList->colorFormat                = NVBUF_COLOR_FORMAT_YUV420;
                sw_surf->surfaceList->layout                     = NVBUF_LAYOUT_PITCH;
                sw_surf->surfaceList->width                      = sourceWidth;
                sw_surf->surfaceList->height                     = sourceHeight;
                sw_surf->surfaceList->dataPtr                    = buffer_type.m_inputBuffer;
                // data size calculation for I420 frame
                sw_surf->surfaceList->dataSize                   = (sourceWidth * sourceHeight) + (sourceWidth * sourceHeight / 2);

                sw_surf->surfaceList->planeParams.offset[0]      = 0;
                sw_surf->surfaceList->planeParams.width[0]       = sourceWidth;
                sw_surf->surfaceList->planeParams.height[0]      = sourceHeight;
                sw_surf->surfaceList->planeParams.psize[0]       = sourceWidth * sourceHeight;
                sw_surf->surfaceList->planeParams.pitch[0]       = sourceWidth;
                sw_surf->surfaceList->planeParams.bytesPerPix[0] = 1;

                sw_surf->surfaceList->planeParams.offset[1]      = sw_surf->surfaceList->planeParams.psize[0];
                sw_surf->surfaceList->planeParams.width[1]       = sourceWidth / 2;
                sw_surf->surfaceList->planeParams.height[1]      = sourceHeight / 2;
                sw_surf->surfaceList->planeParams.psize[1]       = sourceWidth / 2 * sourceHeight / 2;
                sw_surf->surfaceList->planeParams.pitch[1]       = sourceWidth / 2;
                sw_surf->surfaceList->planeParams.bytesPerPix[1] = 1;

                sw_surf->surfaceList->planeParams.offset[2]      = sw_surf->surfaceList->planeParams.psize[0] + sw_surf->surfaceList->planeParams.psize[1];
                sw_surf->surfaceList->planeParams.width[2]       = sourceWidth / 2;
                sw_surf->surfaceList->planeParams.height[2]      = sourceHeight / 2;
                sw_surf->surfaceList->planeParams.psize[2]       = sourceWidth / 2 * sourceHeight / 2;
                sw_surf->surfaceList->planeParams.pitch[2]       = sourceWidth / 2;
                sw_surf->surfaceList->planeParams.bytesPerPix[2] = 1;

                // convert from I420 sw memory to I420 hw memory
                NvBufSurface *hw_surf = 0;
                NvBufSurfaceAllocateParams input_params = {0};
                input_params.params.width       = sourceWidth;
                input_params.params.height      = sourceHeight;
                input_params.params.layout      = NVBUF_LAYOUT_PITCH;
                input_params.params.colorFormat = NVBUF_COLOR_FORMAT_YUV420;
#ifdef JETSON_PLATFORM
                if (g_isJetsonGpuMode)
                {
                    LOG(info) << "Using CUDA Device memory" << endl;
                    input_params.params.memType     = NVBUF_MEM_CUDA_DEVICE;
                }
                else
                {
                    LOG(info) << "Using Default memory" << endl;
                    input_params.params.memType     = NVBUF_MEM_DEFAULT;
                }
#else
                input_params.params.memType     = NVBUF_MEM_DEFAULT;
#endif
                input_params.memtag             = NvBufSurfaceTag_VIDEO_CONVERT;
                int ret = NvBufSurfaceAllocate(&hw_surf, 1, &input_params);
                if (ret != 0)
                {
                    free(sw_surf->surfaceList);
                    free (sw_surf);
                    LOG(error) << "NvBufSurfaceAllocate failed" << endl;
                    ret = -1;
                    return ret;
                }
                hw_surf->numFilled = 1;
                ret = NvBufSurfaceCopy (sw_surf, hw_surf);
                if (ret != 0)
                {
                    free(sw_surf->surfaceList);
                    free (sw_surf);
                    NvBufSurfaceDestroy(hw_surf);
                    LOG(error) << "NvBufSurfaceCopy failed" << endl;
                    ret = -1;
                    return ret;
                }

                // Color conversion from I420 to NV12 and Resolution change
                NvBufSurface *op_surf = 0;
#ifndef JETSON_PLATFORM
                if (fd && *fd == -1)
#endif
                {
                    input_params = {0};
                    input_params.params.width       = width;
                    input_params.params.height      = height;
                    input_params.params.layout      = NVBUF_LAYOUT_PITCH;
                    input_params.params.colorFormat = NVBUF_COLOR_FORMAT_NV12;
#ifdef JETSON_PLATFORM
                    if (g_isJetsonGpuMode)
                    {
                        LOG(info) << "Using CUDA Device memory" << endl;
                        input_params.params.memType     = NVBUF_MEM_CUDA_DEVICE;
                    }
                    else
                    {
                        LOG(info) << "Using Default memory" << endl;
                        input_params.params.memType     = NVBUF_MEM_DEFAULT;
                    }
#else
                    input_params.params.memType     = NVBUF_MEM_DEFAULT;
#endif
                    input_params.memtag             = NvBufSurfaceTag_VIDEO_CONVERT;
                    ret = NvBufSurfaceAllocate(&op_surf, 1, &input_params);
                    if (ret != 0)
                    {
                        free(sw_surf->surfaceList);
                        free (sw_surf);
                        NvBufSurfaceDestroy(hw_surf);
                        LOG(error) << "NvBufSurfaceAllocate1 failed" << endl;
                        ret = -1;
                        return ret;
                    }
                    op_surf->numFilled = 1;
                }
#ifndef JETSON_PLATFORM
                else
                {
                    int status = -1;
                    if (fd)
                    {
                        status = NvBufSurfaceFromFd (*fd, (void**)(&op_surf));
                        if (status < 0)
                        {
                            LOG(error) << "Failed to get surface from fd =" << *fd << endl;
                            free(sw_surf->surfaceList);
                            free (sw_surf);
                            NvBufSurfaceDestroy(hw_surf);
                            NvBufSurfaceDestroy(op_surf);
                            ret = -1;
                            return ret;
                        }
                    }
                }
#endif
                NvBufSurfTransformConfigParams config_params;
                config_params.gpu_id       = g_gpuIndex;
                config_params.compute_mode = NvBufSurfTransformCompute_Default;
                config_params.cuda_stream  = nullptr;
                NvBufSurfTransformSetSessionParams (&config_params);

                NvBufSurfTransformRect *src_rect = nullptr, *dst_rect = nullptr;
                NvBufSurfTransformParams transform_params = {0};
                src_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));
                dst_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));
                src_rect->top                       = 0;
                src_rect->left                      = 0;
                src_rect->width                     = sourceWidth;
                src_rect->height                    = sourceHeight;
                dst_rect->top                       = 0;
                dst_rect->left                      = 0;
                dst_rect->width                     = width;
                dst_rect->height                    = height;
                transform_params.src_rect           = src_rect;
                transform_params.dst_rect           = dst_rect;
                transform_params.transform_flag     = NVBUFSURF_TRANSFORM_FILTER;
                transform_params.transform_flip     = NvBufSurfTransform_None;
                transform_params.transform_filter   = NvBufSurfTransformInter_Default;

                NvBufSurfTransform_Error transform_error = NvBufSurfTransform (hw_surf, op_surf, &transform_params);
                if (transform_error != NvBufSurfTransformError_Success)
                {
                    LOG(error) << "Failed to Transform" << endl;
                    free(sw_surf->surfaceList);
                    free (sw_surf);
                    free (dst_rect);
                    free (src_rect);
                    NvBufSurfaceDestroy(hw_surf);
                    NvBufSurfaceDestroy(op_surf);
                    ret = -1;
                    return ret;
                }
                if (fd && ret_transform)
                {
                    *fd = op_surf->surfaceList->bufferDesc;
                    *ret_transform = is_transformed_needed;
                }
                free (src_rect);
                free (dst_rect);
                free (sw_surf->surfaceList);
                free (sw_surf);
                NvBufSurfaceDestroy(hw_surf);
#ifdef DUMP_YUV
                {
                    // LOG(info) << "Writing to file.yuv" << endl;
                    std::ofstream outFile("/tmp/file.yuv", std::ios::binary|std::ios::app);
                    std::ofstream *stream = &outFile;
                    // outFile.write ((const char*)map.data, map.size);
                    // outFile.close();

                    int ret = -1;
                    int dmabuf_fd = fd != nullptr ? *fd : -1;
                    for (int plane = 0; plane < 2; ++plane)
                    {
                        NvBufSurface *nvbuf_surf = 0;
                        ret = NvBufSurfaceFromFd(dmabuf_fd, (void**)(&nvbuf_surf));
                        if (ret != 0)
                        {
                            // return -1;
                        }
                        ret = NvBufSurfaceMap(nvbuf_surf, 0, plane, NVBUF_MAP_READ_WRITE);
                        if (ret < 0)
                        {
                            printf("NvBufSurfaceMap failed\n");
                            // return ret;
                        }
                        NvBufSurfaceSyncForCpu (nvbuf_surf, 0, plane);
                        for (uint i = 0; i < nvbuf_surf->surfaceList->planeParams.height[plane]; ++i)
                        {
                            stream->write((char *)nvbuf_surf->surfaceList->mappedAddr.addr[plane] + i * nvbuf_surf->surfaceList->planeParams.pitch[plane],
                                            nvbuf_surf->surfaceList->planeParams.width[plane] * nvbuf_surf->surfaceList->planeParams.bytesPerPix[plane]);
                        }
                        ret = NvBufSurfaceUnMap(nvbuf_surf, 0, plane);
                        if (ret < 0)
                        {
                            printf("NvBufSurfaceUnMap failed\n");
                            // return ret;
                        }
                    }
                    outFile.close();
                }
#endif
            }
            else if(m_nvBufferMode == NvBufferModeHardwareSurface)
            {
                NvBufSurface *ip_surf = nullptr;
                NvBufSurfTransformConfigParams config_params;
                config_params.gpu_id       = g_gpuIndex;
                config_params.compute_mode = NvBufSurfTransformCompute_Default;
                config_params.cuda_stream  = nullptr;
                /* TODO add this API after CUDA libs and headers are added */
                // cudaStreamCreateWithFlags(&(config_params.cuda_stream), 0x01);

                NvBufSurfTransformSetSessionParams (&config_params);
                if (ret_transform && *ret_transform == true)
                {
                    is_transformed_needed = true;
                }
                if (is_transformed_needed)
                {
                    NvBufSurfTransformRect *src_rect          = nullptr;
                    NvBufSurfTransformRect *dst_rect          = nullptr;
                    NvBufSurfaceCreateParams buf_params       = {0};
                    NvBufSurfTransformParams transform_params = {0};
                    NvBufSurface *op_surf                     = nullptr;

                    if (fd && *fd == -1)
                    {
                        buf_params.width       = targetWidth;
                        buf_params.height      = targetHeight;
#ifdef JETSON_PLATFORM
                        if (g_isJetsonGpuMode)
                        {
                            LOG(info) << "Using CUDA Device memory" << endl;
                            buf_params.memType     = NVBUF_MEM_CUDA_DEVICE;
                        }
                        else
                        {
                            LOG(info) << "Using Default memory" << endl;
                            buf_params.memType     = NVBUF_MEM_DEFAULT;
                        }
#else
                        buf_params.memType     = NVBUF_MEM_DEFAULT;
#endif
                        buf_params.colorFormat = NVBUF_COLOR_FORMAT_NV12;
                        buf_params.gpuId       = g_gpuIndex;
                        int status = NvBufSurfaceCreate(&op_surf, 1, &buf_params);
                        if (status < 0)
                        {
                            LOG(error) << "Failed to create surface" << endl;
                            ret = -1;
                            return ret;
                        }
                        ip_surf = (NvBufSurface*)buffer_type.m_inputBuffer;
                    }
                    /* Compositor case with transform */
                    else if (buffer_type.m_inputFD != -1)
                    {
                        int status = NvBufSurfaceFromFd (buffer_type.m_inputFD, (void**)(&(ip_surf)));
                        if (status < 0)
                        {
                            LOG(error) << "Failed to get surface from fd =" << *fd << endl;
                            ret = -1;
                            return ret;
                        }
                        if (fd)
                        {
                            status = NvBufSurfaceFromFd (*fd, (void**)(&op_surf));
                            if (status < 0)
                            {
                                LOG(error) << "Failed to get surface from fd =" << *fd << endl;
                                ret = -1;
                                return ret;
                            }
                        }
                    }
                    else
                    {
                        int status = -1;
                        if (fd)
                        {
                            status = NvBufSurfaceFromFd (*fd, (void**)(&op_surf));
                            if (status < 0)
                            {
                                LOG(error) << "Failed to get surface from fd =" << *fd << endl;
                                ret = -1;
                                return ret;
                            }
                        }
                        ip_surf = (NvBufSurface*)buffer_type.m_inputBuffer;
                    }
                    src_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));
                    dst_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));

                    src_rect->top    = 0;
                    src_rect->left   = 0;
                    src_rect->width  = sourceWidth;
                    src_rect->height = sourceHeight;
                    dst_rect->top    = 0;
                    dst_rect->left   = 0;
                    dst_rect->width  = targetWidth;
                    dst_rect->height = targetHeight;

                    transform_params.src_rect = src_rect;
                    transform_params.dst_rect = dst_rect;

                    transform_params.transform_flag   = NVBUFSURF_TRANSFORM_FILTER;
                    transform_params.transform_flip   = NvBufSurfTransform_None;
                    transform_params.transform_filter = NvBufSurfTransformInter_Default;

                    NvBufSurfTransform_Error transform_error = NvBufSurfTransform ((NvBufSurface*)ip_surf, op_surf, &transform_params);
                    if (transform_error != NvBufSurfTransformError_Success)
                    {
                        LOG(error) << "Failed to Transform" << endl;
                        int status = NvBufSurfaceDestroy(op_surf);
                        if (status < 0)
                        {
                            LOG(error) << "Failed to destroy surface" << endl;
                        }
                        free (src_rect);
                        free (dst_rect);
                        ret = -1;
                        return ret;
                    }
                    if (fd && *fd)
                    {
                        *fd = op_surf->surfaceList->bufferDesc;
                    }
                    if (ret_transform)
                    {
                        *ret_transform = is_transformed_needed;
                    }
                    free (src_rect);
                    free (dst_rect);
                }
                else
                {
                    if (fd)
                    {
                        *fd = ((NvBufSurface*)buffer_type.m_inputBuffer)->surfaceList->bufferDesc;
                    }
                    if (ret_transform)
                    {
                        *ret_transform = is_transformed_needed;
                    }
                }
            }
            else
            {
                LOG(error) << "Wrong buffer format" << endl;
                ret = -1;
            }
            return ret;
        }

        NvBufferMode getNvBufferMode()
        {
            return m_nvBufferMode;
        }

        void setNvBufferMode(const NvBufferMode& bufferMode)
        {
            m_nvBufferMode = bufferMode;
        }

        void destroyFd(int fd)
        {
            if (!NvBufSurfaceFromFd || !NvBufSurfaceDestroy)
            {
                LOG(error) << "NvBufSurface functions not loaded" << endl;
                return;
            }
            NvBufSurface *nvbuf_surf = 0;
            int status = -1;
            status = NvBufSurfaceFromFd (fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to get surface from fd =" << fd << endl;
            }
            else
            {
                status = NvBufSurfaceDestroy(nvbuf_surf);
                if (status < 0)
                {
                    LOG(error) << "Failed to destroy surface" << endl;
                }
            }
        }

        int unmapSurface(int fd)
        {
            if (!NvBufSurfaceFromFd || !NvBufSurfaceUnMap)
            {
                LOG(error) << "NvBufSurface functions not loaded" << endl;
                return -1;
            }
            NvBufSurface *nvbuf_surf = nullptr;
            int status = 0;
            uint32_t i = 0;

            status = NvBufSurfaceFromFd(fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to fetch surface from fd =" << fd << endl;
                return -1;
            }
            if (nvbuf_surf->memType != NVBUF_MEM_CUDA_DEVICE)
            {
                for (i = 0; i < nvbuf_surf->surfaceList[0].planeParams.num_planes; i++)
                {
                    status = NvBufSurfaceUnMap(nvbuf_surf, 0, i);
                    if (status < 0)
                    {
                        LOG(error) << "Failed to unmap surface" << endl;
                        return -1;
                    }
                }
            }
            return 0;
        }
    
        int mapSurface(int fd, NvBufSurfaceMemMapFlags flags)
        {
            if (!NvBufSurfaceFromFd || !NvBufSurfaceMap)
            {
                LOG(error) << "NvBufSurface functions not loaded" << endl;
                return -1;
            }
            NvBufSurface *nvbuf_surf = nullptr;
            int status = 0;
            uint32_t i = 0;

            status = NvBufSurfaceFromFd(fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to fetch surface from fd =" << fd << endl;
                return -1;
            }
            if (nvbuf_surf->memType != NVBUF_MEM_CUDA_DEVICE)
            {
                for (i = 0; i <  nvbuf_surf->surfaceList[0].planeParams.num_planes; i++)
                {
                    status = NvBufSurfaceMap(nvbuf_surf, 0, i, flags);
                    if (status < 0)
                    {
                        LOG(error) << "Failed to map surface" << endl;
                        return -1;
                    }
                }
            }
            return 0;
        }

        void *getMappedAddr(int fd, uint32_t plane)
        {
            if (!NvBufSurfaceFromFd)
            {
                LOG(error) << "NvBufSurfaceFromFd not loaded" << endl;
                return nullptr;
            }
            NvBufSurface *nvbuf_surf = nullptr;
            int status = 0;
            status = NvBufSurfaceFromFd(fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to get surface from fd =" << fd << endl;
                return nullptr;
            }
            return nvbuf_surf->surfaceList[0].mappedAddr.addr[plane];
        }

        void* extractSurface (int fd)
        {
            if (!NvBufSurfaceFromFd)
            {
                LOG(error) << "NvBufSurfaceFromFd not loaded" << endl;
                return nullptr;
            }
            NvBufSurface *nvbuf_surf = 0;
            int status = -1;
            status = NvBufSurfaceFromFd (fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to get surface from fd =" << fd << endl;
                return nullptr;
            }
            else
            {
                return nvbuf_surf->surfaceList->dataPtr;
            }
        }

        void* getNvSurface (int fd)
        {
            if (!NvBufSurfaceFromFd)
            {
                LOG(error) << "NvBufSurfaceFromFd not loaded" << endl;
                return nullptr;
            }
            NvBufSurface *nvbuf_surf = nullptr;
            int status = -1;
            status = NvBufSurfaceFromFd (fd, (void**)(&nvbuf_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to get surface from fd =" << fd << endl;
                return nullptr;
            }
            else
            {
                return nvbuf_surf;
            }
        }

        int createSurface (NvBufSurface **surf, uint32_t batchSize, NvBufSurfaceCreateParams *params)
        {
            if (!NvBufSurfaceCreate)
            {
                LOG(error) << "NvBufSurfaceCreate not loaded" << endl;
                return -1;
            }
            int status = NvBufSurfaceCreate(surf, 1, params);
            if (status < 0)
            {
                LOG(error) << "Failed to create surface" << endl;
                return -1;
            }
            return 0;
        }

        void doComposition(int *op_fd, std::map<std::string, std::shared_ptr<RawFrameParams>, std::less<>> nv_buffer_list, uint32_t& target_w, uint32_t& target_h)
        {
            // Call the overloaded version with nullptr to use default layout
            doComposition(op_fd, nv_buffer_list, target_w, target_h, nullptr, 0);
        }

        void doComposition(int *op_fd, std::map<std::string, std::shared_ptr<RawFrameParams>, std::less<>> nv_buffer_list, uint32_t& target_w, uint32_t& target_h, NvBufSurfTransformRect* customLayout, size_t layoutSize)
        {
            size_t list_size = nv_buffer_list.size();
            auto batch_surf = std::make_unique<NvBufSurface*[]>(list_size);
            int i = 0;
            for (auto it : nv_buffer_list)
            {
                batch_surf[i] = (NvBufSurface *)it.second->m_map.data;
                if (!batch_surf[i])
                {
                    int fd = it.second->m_fd;
                    NvBufSurfaceFromFd(fd, (void **)&batch_surf[i]);
                }
                i++;
            }

            NvBufSurface *op_surf                     = nullptr;
            NvBufSurface *blk_surface                 = nullptr;
            NvBufSurfTransformRect dstCompRect[list_size];

            NvBufSurfaceCreateParams buf_params       = {0};
            buf_params.width       = target_w;
            buf_params.height      = target_h;
            buf_params.memType     = NVBUF_MEM_SYSTEM;
            buf_params.colorFormat = NVBUF_COLOR_FORMAT_NV12;
            buf_params.gpuId       = g_gpuIndex;
            int blk_surface_fd = -1;
            int status = NvBufWrapper::getInstance()->createSurface(&blk_surface, 1, &buf_params);
            if (status < 0)
            {
                LOG(error) << "Failed to create surface" << endl;
            }
            blk_surface_fd = blk_surface->surfaceList->bufferDesc;                
            blk_surface->numFilled = 1;
            unsigned char* src_ptr = (unsigned char*)blk_surface->surfaceList->dataPtr;
            int lumaSize           = blk_surface->surfaceList->planeParams.psize[0];;
            int chromaSize         = blk_surface->surfaceList->planeParams.psize[1];
            memset(src_ptr, 0, lumaSize);
            memset(src_ptr+lumaSize, 128, chromaSize);

            status = NvBufSurfaceFromFd (*op_fd, (void**)(&op_surf));
            if (status < 0)
            {
                LOG(error) << "Failed to get surface from fd =" << *op_fd << endl;
                *op_fd = -1;
            }
            NvBufSurfaceCopy ( blk_surface, op_surf);

            int32_t spacing = 1;
            int32_t scaling_factor = 1; // init to 1 to avoid divide by zero crash/error
            int32_t top_offset = 0;
        {

            /* This layout upto 2 frames
            +-------------------------------+-----------------------------------+
            |                         blank   space                             |
            |                                                                   |
            +-------------------------------+-----------------------------------+
            |                               |                                   |
            |           Frame 1             |            Frame 2                |
            |                               |                                   |
            |                               |                                   |
            +-------------------------------+-----------------------------------+
            |                         blank   space                             |
            |                                                                   |
            +-------------------------------+-----------------------------------+

            This layout upto 3 frames
            +-------------------------------+-----------------------------------+
            |                               |                                   |
            |           Frame 1             |            Frame 2                |
            |                               |                                   |
            |                               |                                   |
            +-------------------------------+-----------------------------------+
            |               |                               |                   |
            |  blank space  |            Frame 3            |   blank space     |
            |               |                               |                   |
            |               |                               |                   |
            +-------------------------------+-----------------------------------+


            This layout upto 4 frames
            +-------------------------------+-----------------------------------+
            |                               |                                   |
            |           Frame 1             |            Frame 2                |
            |                               |                                   |
            |                               |                                   |
            +-------------------------------+-----------------------------------+
            |                               |                                   |
            |           Frame 3             |            Frame 4                |
            |                               |                                   |
            |                               |                                   |
            +-------------------------------+-----------------------------------+

            This layout for 5 frames
            +-------------+-----------------------------------+
            |                  blank   space                  |
            |                                                 |
            +-------------------------------------------------+
            |              |                |                 |
            |   Frame 1    |    Frame 2     |   Frame 3       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------------------------+
            |       |              |                |         |
            |       |   Frame 4    |    Frame 5     |         |
            |       |              |                |         |
            |       |              |                |         |
            +-------------------------------------------------+
            |                  blank   space                  |
            |                                                 |
            +-------------------------------------------------+

            This layout for 6 frames
            +-------------+-----------------------------------+
            |                  blank   space                  |
            |                                                 |
            +-------------------------------------------------+
            |              |                |                 |
            |   Frame 1    |    Frame 2     |   Frame 3       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------------------------+
            |              |                |                 |
            |   Frame 4    |    Frame 5     |   Frame 6       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------------------------+
            |                  blank   space                  |
            |                                                 |
            +-------------------------------------------------+

            This layout for 7 frames
            +-------------------------------+-----------------+
            |              |                |                 |
            |   Frame 1    |    Frame 2     |   Frame 3       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+
            |              |                |                 |
            |   Frame 4    |    Frame 5     |   Frame 6       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+
            |              |                |                 |
            |              |     Frame 7    |                 |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+

            This layout for 8 frames
            +-------------------------------+-----------------+
            |              |                |                 |
            |   Frame 1    |    Frame 2     |   Frame 3       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+
            |              |                |                 |
            |   Frame 4    |    Frame 5     |   Frame 6       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+
            |      |                |                |        |
            |      |     Frame 7    |    Frame 8     |        |
            |      |                |                |        |
            |      |                |                |        |
            +-------------------------------+-----------------+


            This layout for 9 frames
            (0,0)--------(0,cw)----------(0,cw*2)-------------+
            |              |                |                 |
            |   Frame 1    |    Frame 2     |   Frame 3       |
            |              |                |                 |
            |              |                |                 |
            (ch,0)------(ch,cw)---------(ch,cw*2)-------------+
            |              |                |                 |
            |   Frame 4    |    Frame 5     |   Frame 6       |
            |              |                |                 |
            |              |                |                 |
            (ch*2,0)------(ch*2,cw)-------(ch*2,cw*2)---------+
            |              |                |                 |
            |   Frame 7    |    Frame 8     |   Frame 9       |
            |              |                |                 |
            |              |                |                 |
            +-------------------------------+-----------------+
            (t, l) : t = top, l = left
            cw = cellWidth, ch = cellHeight
            */
        }
            if (list_size <= 4)
            {
                scaling_factor = 2;
            }
            if (list_size > 4)
            {
                scaling_factor = 3;
            }
            if (list_size > 9)
            {
                scaling_factor = 4;
            }
            
            // Use custom layout if provided, otherwise calculate default layout
            if (customLayout && layoutSize > 0)
            {
                // Use the provided custom layout
                for (size_t i = 0; i < list_size && i < layoutSize; i++)
                {
                    dstCompRect[i] = customLayout[i];
                }
            }
            else
            {
                // Default layout calculation
                int32_t cellWidth = (target_w / scaling_factor) - spacing;
                int32_t cellHeight = (target_h / scaling_factor) - spacing;

                /* Setting common cell width and height for all */
                for (size_t i = 0; i < list_size; i++)
                {
                    dstCompRect[i].width = cellWidth;
                    dstCompRect[i].height = cellHeight;
                }
                
                // Default positioning logic
                if (list_size == 1)
            {
                dstCompRect[0].top  = 0;
                dstCompRect[0].left = 0;
            }
            else if (list_size <= 2)
            {
                dstCompRect[0].top  = cellHeight / 2;
                dstCompRect[0].left = 0;
                dstCompRect[1].top  = cellHeight / 2;
                dstCompRect[1].left = cellWidth;
            }
            else if (list_size > 2 && list_size <= 4)
            {
                dstCompRect[0].top  = 0;
                dstCompRect[0].left = 0;
                dstCompRect[1].top  = 0;
                dstCompRect[1].left = cellWidth;
                dstCompRect[2].top  = cellHeight;
                dstCompRect[2].left = cellWidth / 2;
                if (list_size > 3)
                {
                    dstCompRect[2].left = 0;
                    dstCompRect[3].top  = cellHeight + top_offset;
                    dstCompRect[3].left = cellWidth;
                }
            }
            else if (list_size > 4 && list_size < 6)
            {
                dstCompRect[0].width = target_w;
                dstCompRect[0].height = target_h;
                /* Setting common cell width and height for all */
                for (size_t i = 1; i < list_size; i++)
                {
                    dstCompRect[i].width = 420;
                    dstCompRect[i].height = 236;
                }
                dstCompRect[0].top  = 0;
                dstCompRect[0].left = 0;

                dstCompRect[1].top  = 93;
                dstCompRect[1].left = 0;

                dstCompRect[2].top  = 236 + 236 + 93 + 93 + 93;
                dstCompRect[2].left = 0;

                dstCompRect[3].top  = 93;
                dstCompRect[3].left = 1500;

                dstCompRect[4].top  = 236 + 236 + 93 + 93 + 93;
                dstCompRect[4].left = 1500;
                if (list_size > 5)
                {
                    dstCompRect[3].left = 0;
                    dstCompRect[4].left = cellWidth;
                    dstCompRect[5].top  = dstCompRect[0].top + cellHeight;
                    dstCompRect[5].left = cellWidth * 2;                    
                }
            }

            else if (list_size == 6)
            {
                dstCompRect[0].top  = cellHeight / 2;
                dstCompRect[0].left = 0;
                dstCompRect[1].top  = cellHeight / 2;
                dstCompRect[1].left = cellWidth;
                dstCompRect[2].top  = cellHeight / 2;
                dstCompRect[2].left = cellWidth * 2;
                dstCompRect[3].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[3].left = cellWidth / 2;
                dstCompRect[4].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[4].left = cellWidth / 2 + cellWidth;
                if (list_size > 5)
                {
                    dstCompRect[3].left = 0;
                    dstCompRect[4].left = cellWidth;
                    dstCompRect[5].top  = dstCompRect[0].top + cellHeight;
                    dstCompRect[5].left = cellWidth * 2;                    
                }
            }
            else if (list_size > 6 && list_size <= 9)
            {
                dstCompRect[0].width = target_w;
                dstCompRect[0].height = target_h;

                /* Setting common cell width and height for all */
                for (size_t i = 1; i < list_size; i++)
                {
                    dstCompRect[i].width = 420;
                    dstCompRect[i].height = 236;
                }
                dstCompRect[0].top  = 0;
                dstCompRect[0].left = 0;

                dstCompRect[1].top  = 93;
                dstCompRect[1].left = 0;
                dstCompRect[2].top  = 236 + 93 + 93;
                dstCompRect[2].left = 0;
                dstCompRect[3].top  = 236 + 236 + 93 + 93 + 93;
                dstCompRect[3].left = 0;

                dstCompRect[4].top  = 93;
                dstCompRect[4].left = 1500;
                dstCompRect[5].top  = 236 + 93 + 93;
                dstCompRect[5].left = 1500;
                dstCompRect[6].top  = 236 + 236 + 93 + 93 + 93;
                dstCompRect[6].left = 1500;
                if (list_size > 7)
                {
                    dstCompRect[6].left = cellWidth / 2;
                    dstCompRect[7].top  = cellHeight * 2;
                    dstCompRect[7].left = cellWidth / 2 + cellWidth;
                }
                if (list_size > 8)
                {
                    dstCompRect[6].left = 0;
                    dstCompRect[7].left = cellWidth;
                    dstCompRect[8].top  = cellHeight * 2;
                    dstCompRect[8].left = cellWidth * 2;
                }
            }

            else if (list_size > 9 && list_size <= 12)
            {
                dstCompRect[0].top  = cellHeight / 2;
                dstCompRect[0].left = 0;
                dstCompRect[1].top  = cellHeight / 2;
                dstCompRect[1].left = cellWidth;
                dstCompRect[2].top  = cellHeight / 2;
                dstCompRect[2].left = cellWidth * 2;
                dstCompRect[3].top  = cellHeight / 2;
                dstCompRect[3].left = cellWidth * 3;

                dstCompRect[4].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[4].left = 0;
                dstCompRect[5].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[5].left = cellWidth;
                dstCompRect[6].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[6].left = cellWidth * 2;
                dstCompRect[7].top  = dstCompRect[0].top + cellHeight;
                dstCompRect[7].left = cellWidth * 3;

                dstCompRect[8].top  = dstCompRect[4].top + cellHeight;
                dstCompRect[8].left = cellWidth;
                dstCompRect[9].top  = dstCompRect[4].top + cellHeight;
                dstCompRect[9].left = cellWidth * 2;

                if (list_size > 10)
                {
                    dstCompRect[8].left = cellWidth / 2;
                    dstCompRect[9].top  = dstCompRect[4].top + cellHeight;
                    dstCompRect[9].left = dstCompRect[8].left + cellWidth;
                    dstCompRect[10].top  = dstCompRect[4].top + cellHeight;
                    dstCompRect[10].left = dstCompRect[9].left + cellWidth;
                }
                if (list_size > 11)
                {
                    dstCompRect[8].left = 0;
                    dstCompRect[9].top  = dstCompRect[4].top + cellHeight;
                    dstCompRect[9].left = cellWidth;
                    dstCompRect[10].top  = dstCompRect[4].top + cellHeight;
                    dstCompRect[10].left = cellWidth * 2;
                    dstCompRect[11].top  = dstCompRect[4].top + cellHeight;
                    dstCompRect[11].left = cellWidth * 3;
                }
            }
            } // End of else block for default layout

            /* Initialize composite parameters */
            NvBufSurfTransformCompositeBlendParamsEx compositeParam;
            memset(&compositeParam, 0, sizeof(compositeParam));

            compositeParam.params.composite_blend_flag   = NVBUFSURF_TRANSFORM_COMPOSITE;
            compositeParam.params.input_buf_count        = list_size;
            compositeParam.params.composite_blend_filter = NvBufSurfTransformInter_Algo3;

            size_t alloc_size = (sizeof(NvBufSurfTransformRect) * list_size);
            compositeParam.dst_comp_rect = static_cast<NvBufSurfTransformRect*> (malloc(alloc_size));
            compositeParam.src_comp_rect = static_cast<NvBufSurfTransformRect*> (malloc(alloc_size));

            // Use safe memory copy with bounds validation
            size_t src_size = sizeof(dstCompRect);
            if (alloc_size > src_size)
            {
                LOG(error) << "Buffer overflow prevented: alloc_size(" << alloc_size 
                          << ") > src_size(" << src_size << ")" << endl;
                free(compositeParam.dst_comp_rect);
                free(compositeParam.src_comp_rect);
                return;  // Early return from void function
            }
            memmove(compositeParam.dst_comp_rect, &dstCompRect[0], alloc_size);

            /* Setting source width and height in src rectange, this source width and height
            ** is populated by decoder class */
            int j = 0;
            for (auto it : nv_buffer_list)
            {
                compositeParam.src_comp_rect[j].top    = 0;
                compositeParam.src_comp_rect[j].left   = 0;
                compositeParam.src_comp_rect[j].width  = it.second->m_sourceWidth;
                compositeParam.src_comp_rect[j].height = it.second->m_sourceHeight;
                j++;
            }

            /* Preserve aspect ratio within each destination rectangle */
            for (size_t k = 0; k < list_size; k++)
            {
                NvBufSurfTransformRect &dst = compositeParam.dst_comp_rect[k];
                NvBufSurfTransformRect &src = compositeParam.src_comp_rect[k];

                if (dst.width == 0 || dst.height == 0 || src.width == 0 || src.height == 0)
                {
                    continue;
                }

                double src_aspect = static_cast<double>(src.width) / static_cast<double>(src.height);
                double dst_aspect = static_cast<double>(dst.width) / static_cast<double>(dst.height);

                if (dst_aspect > src_aspect)
                {
                    uint32_t new_width = static_cast<uint32_t>(dst.height * src_aspect);
                    if (new_width < 1)
                    {
                        new_width = 1;
                    }
                    uint32_t x_offset = (dst.width > new_width) ? (dst.width - new_width) / 2 : 0;
                    dst.left += x_offset;
                    dst.width = new_width;
                }
                else if (dst_aspect < src_aspect)
                {
                    uint32_t new_height = static_cast<uint32_t>(dst.width / src_aspect);
                    if (new_height < 1)
                    {
                        new_height = 1;
                    }
                    uint32_t y_offset = (dst.height > new_height) ? (dst.height - new_height) / 2 : 0;
                    dst.top += y_offset;
                    dst.height = new_height;
                }
            }
            NvBufSurfTransformMultiInputBufCompositeBlend(batch_surf.get(), op_surf, &compositeParam);
#ifdef DUMP_YUV
                {
                    std::ofstream outFile("/tmp/file.yuv", std::ios::binary|std::ios::app);
                    std::ofstream *stream = &outFile;

                    int ret = -1;
                    int dmabuf_fd = op_surf->surfaceList->bufferDesc;
                    for (int plane = 0; plane < 2; ++plane)
                    {
                        NvBufSurface *nvbuf_surf = 0;
                        ret = NvBufSurfaceFromFd(dmabuf_fd, (void**)(&nvbuf_surf));
                        ret = NvBufSurfaceMap(nvbuf_surf, 0, plane, NVBUF_MAP_READ_WRITE);
                        if (ret < 0)
                        {
                            printf("NvBufSurfaceMap failed\n");
                        }
                        NvBufSurfaceSyncForCpu (nvbuf_surf, 0, plane);
                        for (uint i = 0; i < nvbuf_surf->surfaceList->planeParams.height[plane]; ++i)
                        {
                            stream->write((char *)nvbuf_surf->surfaceList->mappedAddr.addr[plane] + i * nvbuf_surf->surfaceList->planeParams.pitch[plane],
                                            nvbuf_surf->surfaceList->planeParams.width[plane] * nvbuf_surf->surfaceList->planeParams.bytesPerPix[plane]);
                        }
                        ret = NvBufSurfaceUnMap(nvbuf_surf, 0, plane);
                        if (ret < 0)
                        {
                            printf("NvBufSurfaceUnMap failed\n");
                        }
                    }
                    outFile.close();
                    exit (0);
                }
#endif
            NvBufWrapper::getInstance()->destroyFd(blk_surface_fd);
            if (compositeParam.src_comp_rect)
            {
                free(compositeParam.src_comp_rect);
            }
            if (compositeParam.dst_comp_rect)
            {
                free(compositeParam.dst_comp_rect);
            }
        }

        int getSwNvBufSurface(rtc::scoped_refptr<webrtc::I420Buffer> buffer, NvBufSurface* sw_surf)
        {
            if (!sw_surf || !sw_surf->surfaceList)
            {
                LOG(error) << "SW surface not allocated" << endl;
                return -1;
            }

            if (!buffer)
            {
                LOG(error) << "Webrtc I420 buffer not allocated" << endl;
                return -1;
            }

            int width = buffer->width();
            int height = buffer->height();
            int pitch = buffer->StrideY();

            sw_surf->gpuId                                   = g_gpuIndex;
            sw_surf->batchSize                               = 1;
            sw_surf->numFilled                               = 1;
            sw_surf->memType                                 = NVBUF_MEM_SYSTEM;

            // number of planes is 3 for I420
            sw_surf->surfaceList->planeParams.num_planes     = 3;
            sw_surf->surfaceList->pitch                      = pitch;
            sw_surf->surfaceList->colorFormat                = NVBUF_COLOR_FORMAT_YUV420;
            sw_surf->surfaceList->layout                     = NVBUF_LAYOUT_PITCH;
            sw_surf->surfaceList->width                      = width;
            sw_surf->surfaceList->height                     = height;
            sw_surf->surfaceList->dataPtr                    = buffer->MutableDataY();
            // data size calculation for I420 frame
            sw_surf->surfaceList->dataSize                   = (width * height) + (width * height / 2);

            sw_surf->surfaceList->planeParams.offset[0]      = 0;
            sw_surf->surfaceList->planeParams.width[0]       = width;
            sw_surf->surfaceList->planeParams.height[0]      = height;
            sw_surf->surfaceList->planeParams.psize[0]       = width * height;
            sw_surf->surfaceList->planeParams.pitch[0]       = width;
            sw_surf->surfaceList->planeParams.bytesPerPix[0] = 1;

            sw_surf->surfaceList->planeParams.offset[1]      = sw_surf->surfaceList->planeParams.psize[0];
            sw_surf->surfaceList->planeParams.width[1]       = width / 2;
            sw_surf->surfaceList->planeParams.height[1]      = height / 2;
            sw_surf->surfaceList->planeParams.psize[1]       = width / 2 * height / 2;
            sw_surf->surfaceList->planeParams.pitch[1]       = width / 2;
            sw_surf->surfaceList->planeParams.bytesPerPix[1] = 1;

            sw_surf->surfaceList->planeParams.offset[2]      = sw_surf->surfaceList->planeParams.psize[0] + sw_surf->surfaceList->planeParams.psize[1];
            sw_surf->surfaceList->planeParams.width[2]       = width / 2;
            sw_surf->surfaceList->planeParams.height[2]      = height / 2;
            sw_surf->surfaceList->planeParams.psize[2]       = width / 2 * height / 2;
            sw_surf->surfaceList->planeParams.pitch[2]       = width / 2;
            sw_surf->surfaceList->planeParams.bytesPerPix[2] = 1;

            return 0;
        }

    public:
        NvBufSurfaceCopy_t NvBufSurfaceCopy;
        NvBufSurfaceFromFd_t NvBufSurfaceFromFd;
        NvBufSurfTransform_t NvBufSurfTransform;
        NvBufSurfTransformSetSessionParams_t NvBufSurfTransformSetSessionParams;
        NvBufSurfTransformSetDefaultSession_t NvBufSurfTransformSetDefaultSession;
#ifdef JETSON_PLATFORM
        NvBufSurface2Raw_t NvBufSurface2Raw;
#endif

    private:
        NvBufferMode m_nvBufferMode;
        void* handle_nvbufsurface_utils;
        void* handle_nvbufsurfacetransform_utils;
        NvBufSurfaceCreate_t NvBufSurfaceCreate;
        NvBufSurfaceDestroy_t NvBufSurfaceDestroy; 
        NvBufSurfaceAllocate_t NvBufSurfaceAllocate;

        NvBufSurfaceMap_t NvBufSurfaceMap;
        NvBufSurfaceSyncForCpu_t NvBufSurfaceSyncForCpu;
        NvBufSurfaceUnMap_t NvBufSurfaceUnMap;

        NvBufSurfTransformMultiInputBufCompositeBlend_t NvBufSurfTransformMultiInputBufCompositeBlend;
};
