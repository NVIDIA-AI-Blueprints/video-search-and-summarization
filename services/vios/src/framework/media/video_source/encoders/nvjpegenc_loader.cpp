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

#include "nvjpegenc_loader.h"
#include <dlfcn.h>
#include "logger.h"
#include "nvbufwrapper.h"

constexpr int JPEG_DEFAULT_QUALITY = 75;
#ifndef MAX_CHANNELS
/**
 * Specifies the maximum number of channels to be used with the
 * JPEG encoder.
 */
#define MAX_CHANNELS 3
#endif
#define ROUND_UP_4(num)  (((num) + 3) & ~3)

NvJpegEncLoader* NvJpegEncLoader::m_instance = nullptr;

NvJpegEncLoader* NvJpegEncLoader::getInstance()
{
    if (m_instance == nullptr)
    {
        m_instance = new NvJpegEncLoader();
    }
    return m_instance;
}

void NvJpegEncLoader::deleteInstance()
{
    if (m_instance)
    {
        delete m_instance;
    }
}

NvJpegEncLoader::NvJpegEncLoader()
        : jpeg_std_error(nullptr)
        , jpeg_CreateCompress(nullptr)
        , jpeg_suppress_tables(nullptr)
        , jpeg_mem_dest(nullptr)
        , jpeg_set_defaults(nullptr)
        , jpeg_set_quality(nullptr)
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        , jpeg_set_hardware_acceleration_parameters_enc(nullptr)
#endif
        , jpeg_start_compress(nullptr)
        , jpeg_write_raw_data(nullptr)
        , jpeg_finish_compress(nullptr)
        , jpeg_destroy_compress(nullptr)
        , m_error(false)
        , m_handleNvJpeg(nullptr)
{
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    m_handleNvJpeg = dlopen("/usr/lib/aarch64-linux-gnu/nvidia/libnvmm_jpeg.so", RTLD_LAZY);
#else

    const char* lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "deepstream/libnvds_lljpeg.so");
    m_handleNvJpeg = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleNvJpeg)
    {
        LOG(error) << "Error loading libnvds_lljpeg library" << endl;
        m_error = true;
        throw std::runtime_error("An exception occurred, error loading libnvds_lljpeg library");
    }
    else
    {
        dlerror();
        jpeg_std_error = (jpeg_std_error_t) dlsym (m_handleNvJpeg, "jpeg_std_error");
        jpeg_CreateCompress = (jpeg_CreateCompress_t) dlsym(m_handleNvJpeg, "jpeg_CreateCompress");
        jpeg_suppress_tables = (jpeg_suppress_tables_t) dlsym(m_handleNvJpeg, "jpeg_suppress_tables");
        jpeg_mem_dest = (jpeg_mem_dest_t) dlsym(m_handleNvJpeg, "jpeg_mem_dest");
        jpeg_set_defaults = (jpeg_set_defaults_t) dlsym(m_handleNvJpeg, "jpeg_set_defaults");
        jpeg_set_quality = (jpeg_set_quality_t) dlsym(m_handleNvJpeg, "jpeg_set_quality");
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        jpeg_set_hardware_acceleration_parameters_enc = (jpeg_set_hardware_acceleration_parameters_enc_t) dlsym(m_handleNvJpeg, "jpeg_set_hardware_acceleration_parameters_enc");
#endif
        jpeg_start_compress = (jpeg_start_compress_t) dlsym(m_handleNvJpeg, "jpeg_start_compress");
        jpeg_write_raw_data = (jpeg_write_raw_data_t) dlsym(m_handleNvJpeg, "jpeg_write_raw_data");
        jpeg_finish_compress = (jpeg_finish_compress_t) dlsym(m_handleNvJpeg, "jpeg_finish_compress");
        jpeg_destroy_compress = (jpeg_destroy_compress_t) dlsym(m_handleNvJpeg, "jpeg_destroy_compress");

        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbol " << dlsym_error << endl;
            dlclose(m_handleNvJpeg);
            m_error = true;
            throw std::runtime_error("An exception occurred, error loading libnvds_lljpeg library");
        }
    }
}

NvJpegEncLoader::~NvJpegEncLoader()
{
    if (m_handleNvJpeg)
    {
        dlclose(m_handleNvJpeg);
    }
}

int NvJpegEncLoader::nvjpegEncodeFromFd(int fd, unsigned char **out_buf, unsigned long &out_buf_size)
{
    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    NvBufSurface *buf_surf = nullptr;
#endif

    if (fd < 0)
    {
        LOG(error) << "Not encoding because fd = " << fd << endl;
        return -1;
    }

    /* Initialize */
    memset((void *)&cinfo, 0, sizeof(cinfo));
    memset(&jerr, 0, sizeof(jerr));
    cinfo.err = jpeg_std_error(&jerr);

    jpeg_CreateCompress(&cinfo, JPEG_LIB_VERSION, sizeof(struct jpeg_compress_struct));
    jpeg_suppress_tables(&cinfo, TRUE);

    /* JPEG encode starts */
    jpeg_mem_dest(&cinfo, out_buf, &out_buf_size);

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    cinfo.fd = fd;
#else
    if (!NvBufWrapper::getInstance()->NvBufSurfaceFromFd)
    {
        LOG(error) << "NvBufSurfaceFromFd not loaded" << endl;
        jpeg_destroy_compress(&cinfo);
        return -1;
    }
    NvBufWrapper::getInstance()->NvBufSurfaceFromFd(fd, (void **)&buf_surf);
    cinfo.pVendor_buf = (unsigned char *)buf_surf;
#endif
    cinfo.IsVendorbuf = TRUE;

    cinfo.raw_data_in = TRUE;
    cinfo.in_color_space = JCS_YCbCr;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, JPEG_DEFAULT_QUALITY, TRUE);
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    jpeg_set_hardware_acceleration_parameters_enc(&cinfo, TRUE, out_buf_size, 0, 0);
#else
    cinfo.dest->next_output_byte = *out_buf;
    cinfo.outputBuffSize = cinfo.dest->free_in_buffer = out_buf_size;
#endif

    jpeg_start_compress (&cinfo, 0);

    if (cinfo.err->msg_code)
    {
        char err_string[256];
        cinfo.err->format_message((j_common_ptr) &cinfo, err_string);
        LOG(info) << "Error in jpeg_start_compress: " << endl;
        return -1;
    }

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    jpeg_write_raw_data (&cinfo, nullptr, 0);
#endif
    jpeg_finish_compress(&cinfo);
    /* JPEG encode completed */
    LOG(info) << "Succesfully encoded buffer fd =" << fd << endl;

    /* Destroy */
    jpeg_destroy_compress(&cinfo);
    return 0;
}

/* Only support I420 SW buffer */
int NvJpegEncLoader::nvjpegEncodeFromBuffer(unsigned char* buffer, uint32_t width, uint32_t height, unsigned char **out_buf, unsigned long &out_buf_size)
{
    unsigned char **line[3];

    uint32_t comp_height[MAX_CHANNELS];
    uint32_t comp_width[MAX_CHANNELS];
    uint32_t h_samp[MAX_CHANNELS];
    uint32_t v_samp[MAX_CHANNELS];
    uint32_t h_max_samp = 0;
    uint32_t v_max_samp = 0;
    uint32_t channels;

    unsigned char *base[MAX_CHANNELS], *end[MAX_CHANNELS];
    unsigned int stride[MAX_CHANNELS];

    uint32_t i, j, k;
    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;

    /* Initialize */
    memset((void *)&cinfo, 0, sizeof(cinfo));
    memset(&jerr, 0, sizeof(jerr));
    cinfo.err = jpeg_std_error(&jerr);

    jpeg_CreateCompress(&cinfo, JPEG_LIB_VERSION, sizeof(struct jpeg_compress_struct));
    jpeg_suppress_tables(&cinfo, TRUE);

    /* JPEG encode starts */
    jpeg_mem_dest(&cinfo, out_buf, &out_buf_size);

    /* Set the width, height for YCbCr */
    channels = 3;
    comp_width[0] = width;
    comp_height[0] = height;
    comp_width[1] = width / 2;
    comp_height[1] = height / 2;
    comp_width[2] = width / 2;
    comp_height[2] = height / 2;
    h_max_samp = 0;
    v_max_samp = 0;

    for (i = 0; i < channels; ++i)
    {
        h_samp[i] = ROUND_UP_4(comp_width[0]) / comp_width[i];
        h_max_samp = MAX(h_max_samp, h_samp[i]);
        v_samp[i] = ROUND_UP_4(comp_height[0]) / comp_height[i];
        v_max_samp = MAX(v_max_samp, v_samp[i]);
    }

    for (i = 0; i < channels; ++i)
    {
        h_samp[i] = h_max_samp / h_samp[i];
        v_samp[i] = v_max_samp / v_samp[i];
    }

    cinfo.image_width = width;
    cinfo.image_height = height;
    cinfo.input_components = channels;
    cinfo.in_color_space = JCS_YCbCr;
    cinfo.raw_data_in = TRUE;

    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, JPEG_DEFAULT_QUALITY, TRUE);
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    jpeg_set_hardware_acceleration_parameters_enc(&cinfo, TRUE, out_buf_size, 0, 0);
#else
    cinfo.dest->next_output_byte = *out_buf;
    cinfo.dest->free_in_buffer = out_buf_size;
#endif

    for (i = 0; i < channels; i++)
    {
        cinfo.comp_info[i].h_samp_factor = h_samp[i];
        cinfo.comp_info[i].v_samp_factor = v_samp[i];
        line[i] = (unsigned char **) malloc(v_max_samp * DCTSIZE *
                sizeof(unsigned char *));
    }

    for (i = 0; i < channels; i++)
    {
        base[i] = (unsigned char *) buffer;
        if (i > 0)
        {
            base[i] = end[i - 1];
        }
        stride[i] = comp_width[i] * ((channels == 2 && i == 1) ? 2 : 1);
        end[i] = base[i] + comp_height[i] * stride[i];
    }

    jpeg_start_compress(&cinfo, TRUE);

    if (cinfo.err->msg_code)
    {
        char err_string[256];
        cinfo.err->format_message((j_common_ptr) &cinfo, err_string);
        LOG(error) << "Error in jpeg_start_compress: " << err_string;
        return -1;
    }

    for (i = 0; i < height; i += v_max_samp * DCTSIZE)
    {
        for (k = 0; k < channels; k++)
        {
            for (j = 0; j < v_samp[k] * DCTSIZE; j++)
            {
                line[k][j] = base[k];
                if (base[k] + stride[k] < end[k])
                    base[k] += stride[k];
            }
        }
        jpeg_write_raw_data(&cinfo, line, v_max_samp * DCTSIZE);
    }

    jpeg_finish_compress(&cinfo);
    for (i = 0; i < channels; i++)
    {
        free(line[i]);
    }
    LOG(info) << "Succesfully encoded Buffer" << endl;

    /* Destroy */
    jpeg_destroy_compress(&cinfo);
    return 0;
}
