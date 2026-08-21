/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "cudaLoader.h"
#include <dlfcn.h>
#include "logger.h"

CudaLoader* CudaLoader::getInstance()
{
    static CudaLoader instance;
    return &instance;
}

CudaLoader::CudaLoader()
        : cuInit(nullptr)
        , cuDeviceGet(nullptr)
        , cuCtxGetCurrent(nullptr)
        , cudaSetDevice(nullptr)
        , m_error(false)
        , m_handleCuda(nullptr)
        , m_handleCudart(nullptr)
{
    // The driver library is injected by the container runtime, and where it
    // lands depends on the host stack: the Jetson CSV mounts used to place it
    // in /usr/lib/<triplet>, while CDI injection (JetPack 7.2 / R39 OpenRM)
    // mounts it under /opt/nvidia/l4t-gpu-libs/openrm and only registers it in
    // the ld cache. Resolve the soname first so any of those layouts works, and
    // keep the absolute paths as a fallback for images with no ld cache entry.
    static const char* const cudaCandidates[] = {
        "libcuda.so.1",
        "libcuda.so",
#ifdef AARCH64_PLATFORM
        "/opt/nvidia/l4t-gpu-libs/openrm/libcuda.so.1",
        "/usr/lib/aarch64-linux-gnu/nvidia/libcuda.so.1",
        "/usr/lib/aarch64-linux-gnu/libcuda.so",
#endif
    };
    // Version-agnostic cudart lookup: try the bare soname first (resolved via
    // ldconfig / LD_LIBRARY_PATH), then the /usr/local/cuda symlink, then a
    // pinned minor version. This lets any CUDA 13.x runtime base work (13.0,
    // 13.2, future 13.x) without hardcoding a minor-version path.
#ifdef AARCH64_PLATFORM
    static const char* const cudartCandidates[] = {
        "libcudart.so.13",
        "/usr/local/cuda/targets/sbsa-linux/lib/libcudart.so.13",
        "/usr/local/cuda-13.2/targets/sbsa-linux/lib/libcudart.so.13",
    };
#else
    static const char* const cudartCandidates[] = {
        "libcudart.so.13",
        "/usr/local/cuda/targets/x86_64-linux/lib/libcudart.so.13",
        "/usr/local/cuda-13.2/targets/x86_64-linux/lib/libcudart.so.13",
    };
#endif
    for (const char* cand : cudaCandidates)
    {
        m_handleCuda = static_cast<SharedLibraryHandle*>(dlopen(cand, RTLD_LAZY));
        if (m_handleCuda)
        {
            break;
        }
        LOG(info) << "libcuda not found at " << cand << ": " << dlerror() << endl;
    }
    for (const char* cand : cudartCandidates)
    {
        m_handleCudart = static_cast<SharedLibraryHandle*>(dlopen(cand, RTLD_LAZY));
        if (m_handleCudart)
        {
            break;
        }
        LOG(info) << "libcudart not found at " << cand << ": " << dlerror() << endl;
    }
    if (!m_handleCuda || !m_handleCudart)
    {
        if (isGpuPresent())
        {
            LOG(error) << "Error loading the CUDA libraries (libcuda: "
                       << (m_handleCuda ? "ok" : "missing") << ", libcudart: "
                       << (m_handleCudart ? "ok" : "missing") << ")" << endl;
            m_error = true;
            throw std::runtime_error("An exception occurred, error loading the CUDA libraries");
        }
    }
    else
    {
        dlerror();
        cuInit = (cuInit_t) dlsym(m_handleCuda, "cuInit");
        cuDeviceGet = (cuDeviceGet_t) dlsym(m_handleCuda, "cuDeviceGet");
        cuCtxGetCurrent = (cuCtxGetCurrent_t) dlsym(m_handleCuda, "cuCtxGetCurrent");
        cudaSetDevice = (cudaSetDevice_t) dlsym(m_handleCudart, "cudaSetDevice");

        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbol " << dlsym_error << endl;
            dlclose(m_handleCuda);
            dlclose(m_handleCudart);
            m_error = true;
            throw std::runtime_error("An exception occurred, error loading the CUDA libraries");
        }
    }
}

CudaLoader::~CudaLoader()
{
    if (m_handleCuda)
    {
        dlclose(m_handleCuda);
    }
    if (m_handleCudart)
    {
        dlclose(m_handleCudart);
    }
}
