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

#pragma once

#include <list>
#include "nvbufwrapper.h"
#include "libnv_encoder.h"

struct _FdIndexInfo
{
    FD_Index_Pair m_fdIndexPair;
    bool          m_isTransformed = false;
} typedef FdIndexInfo;

class NvSurfacePool
{
public:
    NvSurfacePool() {};
    ~NvSurfacePool() {};
    FD_Index_Pair getFreeFd(bool is_drc, unsigned int target_width, unsigned int target_height, bool need_allocations);

    bool allocateSurfaces    (int num_surfaces, unsigned int target_width, unsigned int target_height, bool need_allocations,
                NvBufSurfaceColorFormat format = NVBUF_COLOR_FORMAT_NV12, NvBufSurfaceLayout layout = NVBUF_LAYOUT_PITCH,
                NvBufSurfaceMemType memType = NVBUF_MEM_DEFAULT);
    bool freeSurfacesAndDataStructure        (bool check_transform = true);

    FD_Index_Pair getFreeSurfaceFromQ ();
    void addFreeSurfaceToQ   (FdIndexInfo);

    bool m_surfacesAllocated = false;

private:
    std::queue<FdIndexInfo>  m_fdSurfaceQ;
    std::mutex               m_fdSurfaceQLock;
    int                      m_numSurfaces = 10;
    unsigned int             m_width = WIDTH_1080p;
    unsigned int             m_height = HEIGHT_1080p;
    std::vector<int>         m_compositorFDList;
};

class fdWrapper
{
public:
    fdWrapper (std::shared_ptr<NvSurfacePool> pool, int fd, int index)
    : m_fd (fd)
    , m_index (index)
    , m_surfacePool(pool)
    { 
        m_fd = fd; m_index = index;
    }
    ~fdWrapper ()
    {
        try {
            FdIndexInfo fd_index_info;
            fd_index_info.m_fdIndexPair.first = m_fd;
            fd_index_info.m_fdIndexPair.second = m_index;
            fd_index_info.m_isTransformed = true;
            m_surfacePool->addFreeSurfaceToQ(fd_index_info);
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~fdWrapper: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~fdWrapper" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }
    void setIndex (int index)
    {
        m_index = index;
    }
private:
    int m_fd = 0;
    int m_index = -1;
    std::shared_ptr<NvSurfacePool> m_surfacePool;
};