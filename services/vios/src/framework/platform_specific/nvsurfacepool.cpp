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

#include "nvsurfacepool.h"

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
#define OUTPUT_PLANE_NUM_BUFFERS 19
#else
#define OUTPUT_PLANE_NUM_BUFFERS 10
#endif

bool NvSurfacePool::allocateSurfaces (int num_surfaces, unsigned int target_width, unsigned int target_height, bool need_allocations,
                                    NvBufSurfaceColorFormat format, NvBufSurfaceLayout layout, NvBufSurfaceMemType memType)
{
    std::lock_guard<std::mutex> queueLock(m_fdSurfaceQLock);
    NvBufSurfaceCreateParams buf_params       = {0};
    NvBufSurface *op_surf                     = NULL;
    if (need_allocations)
    {
        buf_params.width       = target_width;
        buf_params.height      = target_height;
        buf_params.memType     = memType;
#ifdef JETSON_PLATFORM
        /* If Jetson GPU mode is enabled, use CUDA device memory */
        if (memType == NVBUF_MEM_DEFAULT && g_isJetsonGpuMode)
        {
            memType = NVBUF_MEM_CUDA_DEVICE;
            buf_params.memType = memType;
        }
#endif
        buf_params.colorFormat = format;
        buf_params.layout      = layout;
        buf_params.gpuId       = g_gpuIndex;
    }
    for (int i = 0; i < num_surfaces; i++)
    {
        int fd = 0;
        if (need_allocations)
        {
            int status = NvBufWrapper::getInstance()->createSurface(&op_surf, 1, &buf_params);
            if (status < 0)
            {
                LOG(error) << "Failed to create surface" << endl;
                return false;
            }
            fd = op_surf->surfaceList->bufferDesc;
            op_surf->numFilled = 1;
        }
        else
        {
            fd = i * -1;
        }
        LOG(info) << "Buffer Allocated, adding this surface to Q, fd = " << fd << " and index = " << i << endl;
        FdIndexInfo fd_index_info;
        FD_Index_Pair fd_index_pair = std::make_pair(fd, i);
        fd_index_info.m_fdIndexPair = fd_index_pair;
#ifdef JETSON_PLATFORM
        fd_index_info.m_isTransformed = false;
#else
        fd_index_info.m_isTransformed = true; // in case of x86, transform is always true
#endif
        m_fdSurfaceQ.push(fd_index_info);
    }
    m_surfacesAllocated = true;
    m_numSurfaces       = num_surfaces;
    m_width = target_width;
    m_height = target_height;
    return true;
}

bool NvSurfacePool::freeSurfacesAndDataStructure (bool check_transform /* true */)
{
    std::lock_guard<std::mutex> queueLock(m_fdSurfaceQLock);
    while (!m_fdSurfaceQ.empty())
    {
        FD_Index_Pair fd_index_pair;
        /* For compositor case, we need to destroy buffers, so ignore transform flag */
        bool is_transformed = !check_transform;
        FdIndexInfo fd_index_info = m_fdSurfaceQ.front();
        fd_index_pair = fd_index_info.m_fdIndexPair;
        
        if (check_transform)
        {
            is_transformed = fd_index_info.m_isTransformed;
        }
        if (fd_index_pair.first > 0 && is_transformed)
        {
            LOG(info) << "Deleting Fd: " << fd_index_pair.first << " with index = " << fd_index_pair.second << endl;
            NvBufWrapper::getInstance()->destroyFd(fd_index_pair.first);
        }
        m_fdSurfaceQ.pop();
    }
    m_surfacesAllocated = false;
    return true;
}

FD_Index_Pair NvSurfacePool::getFreeSurfaceFromQ ()
{
    std::lock_guard<std::mutex> queueLock(m_fdSurfaceQLock);
    FD_Index_Pair fd_index_pair (-3, -3);

    size_t q_size = m_fdSurfaceQ.size();
    if (q_size)
    {
        FdIndexInfo fd_index_info = m_fdSurfaceQ.front();
        fd_index_pair = fd_index_info.m_fdIndexPair;
        m_fdSurfaceQ.pop();
    }
    return fd_index_pair;
}

void NvSurfacePool::addFreeSurfaceToQ (FdIndexInfo fd_index_info)
{
    std::lock_guard<std::mutex> queueLock(m_fdSurfaceQLock);
    NvBufSurface *nvbuf_surf = nullptr;
    unsigned int surface_width = m_width;
    unsigned int surface_height = m_height;
    FD_Index_Pair fd_index_pair = fd_index_info.m_fdIndexPair;
    bool sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;

    if(fd_index_pair.first > 0)
    {
        // Dummy FDs are created in SW overlay mode, avoid calling getNvSurface for them
        if (!sw_mode)
        {
            nvbuf_surf = (NvBufSurface *)NvBufWrapper::getInstance()->getNvSurface(fd_index_pair.first);
        }

        if (nvbuf_surf != nullptr)
        {
            surface_width = nvbuf_surf->surfaceList[0].width;
            surface_height = nvbuf_surf->surfaceList[0].height;
        }

        /* If the surface width and height are different from the pool width and height, delete the surface */
        if (surface_width != m_width || surface_height != m_height)
        {
            LOG(info) << "Deleting Fd: " << fd_index_pair.first << " with index = " << fd_index_pair.second << endl;
            NvBufWrapper::getInstance()->destroyFd(fd_index_pair.first);
        }
        else
        {
            m_fdSurfaceQ.push(fd_index_info);
        }
    }
}

FD_Index_Pair NvSurfacePool::getFreeFd(bool is_drc, unsigned int target_width, unsigned int target_height, bool need_allocations)
{
    FD_Index_Pair fd_index_pair = make_pair(-3, -3);
    {
        bool reallocate_buffers = false;
        /* Check if surfaces are allocated */
        if (m_surfacesAllocated)
        {
            /* Check if DRC occured, we need to reallocate buffers*/
            if (is_drc)
            {
                reallocate_buffers = true;
            }
            /* Get free surface fd */
            fd_index_pair = getFreeSurfaceFromQ ();
        }
        if (reallocate_buffers || !m_surfacesAllocated)
        {
            /* In case of DRC, we need to free the surfaces */
            if (m_surfacesAllocated)
            {
                freeSurfacesAndDataStructure();
                m_surfacesAllocated = false;
            }
            /* Allocate new surfaces in case of DRC or new instance run */
            LOG(info) << "Allocating Surfaces of resolution = " << target_width << " x " << target_height << endl;
            if (allocateSurfaces (OUTPUT_PLANE_NUM_BUFFERS, target_width, target_height, need_allocations))
            {
                fd_index_pair = getFreeSurfaceFromQ ();
            }
            else
            {
                LOG(error) << "Failed to allocate surfaces" << endl;
            }
        }
    }
    return fd_index_pair;
}