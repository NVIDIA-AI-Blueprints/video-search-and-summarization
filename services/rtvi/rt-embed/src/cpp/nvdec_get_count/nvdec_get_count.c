/*
* SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <stdio.h>
#include <string.h>

#include "cuviddec.h"

static typeof(&cuvidGetDecoderCaps) libcuvidGetDecoderCaps;

#define CHECK_CU_ERR(err) \
    do { \
        CUresult result = err; \
        if (result != CUDA_SUCCESS) { \
            fprintf(stderr, #err " failed with error %d\n", result); \
            if (handle_dec) dlclose(handle_dec); \
            exit(-1); \
        } \
    } while (0)

int main() {
    void *handle_dec = NULL;
    CUcontext g_cuContext = NULL;
    CUVIDDECODECAPS decodecaps;
    memset(&decodecaps, 0, sizeof(decodecaps));
    decodecaps.eCodecType = cudaVideoCodec_H264;
    decodecaps.eChromaFormat = cudaVideoChromaFormat_420;
    decodecaps.nBitDepthMinus8 = 0;

    CHECK_CU_ERR(cuInit(0));
#if CUDA_VERSION >= 13000
    CHECK_CU_ERR(cuCtxCreate_v4(&g_cuContext, NULL, 0, 0));
#else
    CHECK_CU_ERR(cuCtxCreate(&g_cuContext, 0, 0));
#endif

    handle_dec = dlopen("libnvcuvid.so.1", RTLD_LAZY);
    libcuvidGetDecoderCaps = (typeof(&cuvidGetDecoderCaps)) dlsym(handle_dec,"cuvidGetDecoderCaps");

    CHECK_CU_ERR(libcuvidGetDecoderCaps(&decodecaps));
    uint8_t h264Dec = decodecaps.nNumNVDECs;

    decodecaps.eCodecType = cudaVideoCodec_HEVC;
    CHECK_CU_ERR(libcuvidGetDecoderCaps(&decodecaps));
    uint8_t h265Dec = decodecaps.nNumNVDECs;

    printf("%d\n", h264Dec > h265Dec ? h264Dec : h265Dec);
    dlclose(handle_dec);
    cuCtxDestroy(g_cuContext);
}