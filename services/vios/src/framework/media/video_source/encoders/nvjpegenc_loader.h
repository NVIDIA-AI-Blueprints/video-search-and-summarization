/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <stdio.h>
#include <stdint.h>
#include "libjpeg-8b/jpeglib.h"

typedef struct jpeg_error_mgr* (*jpeg_std_error_t) (struct jpeg_error_mgr*);
typedef void (*jpeg_CreateCompress_t) (j_compress_ptr, int, size_t);
typedef void (*jpeg_suppress_tables_t) (j_compress_ptr, boolean);
typedef void (*jpeg_mem_dest_t) (j_compress_ptr, unsigned char**, unsigned long*);
typedef void (*jpeg_set_defaults_t) (j_compress_ptr);
typedef void (*jpeg_set_quality_t) (j_compress_ptr, int, boolean);
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
typedef void (*jpeg_set_hardware_acceleration_parameters_enc_t) (j_compress_ptr, boolean,
                    unsigned int, unsigned int, unsigned int);
#endif
typedef void (*jpeg_start_compress_t) (j_compress_ptr, boolean);
typedef JDIMENSION (*jpeg_write_raw_data_t) (j_compress_ptr, JSAMPIMAGE, JDIMENSION);
typedef void (*jpeg_finish_compress_t) (j_compress_ptr);
typedef void (*jpeg_destroy_compress_t) (j_compress_ptr);

class NvJpegEncLoader
{
public:
    static NvJpegEncLoader* getInstance();
    static void deleteInstance();

    jpeg_std_error_t jpeg_std_error;
    jpeg_CreateCompress_t jpeg_CreateCompress;
    jpeg_suppress_tables_t jpeg_suppress_tables;
    jpeg_mem_dest_t jpeg_mem_dest;
    jpeg_set_defaults_t jpeg_set_defaults;
    jpeg_set_quality_t jpeg_set_quality;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    jpeg_set_hardware_acceleration_parameters_enc_t jpeg_set_hardware_acceleration_parameters_enc;
#endif
    jpeg_start_compress_t jpeg_start_compress;
    jpeg_write_raw_data_t jpeg_write_raw_data;
    jpeg_finish_compress_t jpeg_finish_compress;
    jpeg_destroy_compress_t jpeg_destroy_compress;

    bool isError()  { return m_error; }
    int nvjpegEncodeFromFd(int fd, unsigned char **out_buf, unsigned long &out_buf_size);
    int nvjpegEncodeFromBuffer(unsigned char* buffer, uint32_t width, uint32_t height, unsigned char **out_buf, unsigned long &out_buf_size);
private:
    static NvJpegEncLoader* m_instance;
    bool m_error;
    void* m_handleNvJpeg;

    NvJpegEncLoader();
    ~NvJpegEncLoader();
};
