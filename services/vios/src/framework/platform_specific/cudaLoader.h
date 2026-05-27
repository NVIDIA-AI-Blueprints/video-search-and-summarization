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

#ifndef JETSON_PLATFORM
#include <cuda.h>
#include <cuda_runtime_api.h>

typedef CUresult (*cuInit_t) (unsigned int);
typedef CUresult (*cuDeviceGet_t) (CUdevice*, int);
typedef CUresult (*cuCtxGetCurrent_t) (CUcontext*);
typedef cudaError_t (*cudaSetDevice_t) (int);
class CudaLoader
{
public:
    static CudaLoader* getInstance();
    static void deleteInstance();

    cuInit_t cuInit;
    cuDeviceGet_t cuDeviceGet;
    cuCtxGetCurrent_t cuCtxGetCurrent;
    cudaSetDevice_t cudaSetDevice;

    bool isError()  { return m_error; }

private:
    static CudaLoader* m_instance;
    bool m_error;
    void* m_handleCuda;
    void* m_handleCudart;

    CudaLoader();
    ~CudaLoader();
};
#endif
