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

#ifndef JETSON_PLATFORM
#include "cudaLoader.h"
#include <dlfcn.h>
#include "logger.h"

CudaLoader* CudaLoader::m_instance = nullptr;

CudaLoader* CudaLoader::getInstance()
{
    if (m_instance == nullptr)
    {
        m_instance = new CudaLoader();
    }
    return m_instance;
}

void CudaLoader::deleteInstance()
{
    if (m_instance)
    {
        delete m_instance;
    }
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
#if defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    m_handleCuda = dlopen("/usr/lib/aarch64-linux-gnu/libcuda.so", RTLD_LAZY);
    m_handleCudart = dlopen("/usr/local/cuda-13.0/targets/sbsa-linux/lib/libcudart.so.13", RTLD_LAZY);
#else
    m_handleCuda = dlopen("libcuda.so", RTLD_LAZY);
    m_handleCudart = dlopen("/usr/local/cuda-13.0/targets/x86_64-linux/lib/libcudart.so.13", RTLD_LAZY);
#endif
    if (!m_handleCuda || !m_handleCudart)
    {
        if (g_isGpuPresent)
        {
            LOG(error) << "Error loading the CUDA libraries" << endl;
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
#endif
