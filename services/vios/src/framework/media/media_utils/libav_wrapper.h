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

#pragma once

#include <dlfcn.h>
#include <iostream>
#include <stdexcept>
// Include for platform-specific macros (same as vstmodule.cpp)
#include <string>
#include <unistd.h>
#include <climits>
#include <stdlib.h>
#include <cstring>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

// Libav libraries are installed via user_additional_install.sh to standard system directories

// Forward declarations for libav types
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/avutil.h>
#include <libavutil/time.h>
#include <libavutil/mathematics.h>
#include <libavutil/timestamp.h>
#include <libavutil/samplefmt.h>
}
struct AVFrame;

#define DL_ERROR_EXIT  { char *dlsym_error = dlerror(); \
                            if (dlsym_error) { \
                            std::cerr << "[ERROR] Cannot load symbol " <<  dlsym_error << std::endl; \
                            goto close_dl; } }

// Function pointer typedefs for libav functions
typedef void (*av_register_all_t)(void);
typedef int (*avformat_open_input_t)(AVFormatContext **, const char *, const AVInputFormat *, AVDictionary **);
typedef int (*avformat_find_stream_info_t)(AVFormatContext *, AVDictionary **);
typedef int (*avformat_alloc_output_context2_t)(AVFormatContext **, const AVOutputFormat *, const char *, const char *);
typedef int (*avio_open_t)(AVIOContext **, const char *, int);
typedef AVStream* (*avformat_new_stream_t)(AVFormatContext *, const AVCodec *);
typedef int (*avcodec_parameters_copy_t)(AVCodecParameters *, const AVCodecParameters *);
typedef int (*avformat_write_header_t)(AVFormatContext *, AVDictionary **);
typedef int (*av_seek_frame_t)(AVFormatContext *, int, int64_t, int);
typedef AVPacket* (*av_packet_alloc_t)(void);
typedef int (*av_read_frame_t)(AVFormatContext *, AVPacket *);
typedef void (*av_packet_unref_t)(AVPacket *);
typedef void (*av_packet_rescale_ts_t)(AVPacket *, AVRational, AVRational);
typedef int (*av_interleaved_write_frame_t)(AVFormatContext *, AVPacket *);
typedef void (*av_packet_free_t)(AVPacket **);
typedef void (*avformat_close_input_t)(AVFormatContext **);
typedef int (*avio_closep_t)(AVIOContext **);
typedef void (*avformat_free_context_t)(AVFormatContext *);
typedef int (*av_write_trailer_t)(AVFormatContext *);
typedef int (*av_strerror_t)(int, char *, size_t);
typedef int64_t (*av_rescale_q_t)(int64_t, AVRational, AVRational);
typedef AVRational (*av_guess_frame_rate_t)(AVFormatContext *, AVStream *, AVFrame *);
typedef int (*av_find_best_stream_t)(AVFormatContext *, enum AVMediaType, int, int, const AVCodec **, int);
typedef const AVCodecDescriptor* (*avcodec_descriptor_get_t)(enum AVCodecID);
typedef int (*av_get_bits_per_sample_t)(enum AVCodecID);
typedef int (*av_get_bytes_per_sample_t)(enum AVSampleFormat);
typedef int (*av_dict_set_t)(AVDictionary **, const char *, const char *, int);
typedef void (*av_dict_free_t)(AVDictionary **);

enum LibavMode
{
    LibavModeSoftware = 0,
    LibavModeHardware = 1,
    LibavModeInvalid = 0xFFF
};

class LibavWrapper
{
private:
    // Trusted system directories for library loading
    static constexpr const char* TRUSTED_SYSTEM_DIRS[] = {
        "/usr/lib/",
        "/lib/",
        "/usr/local/lib/",
        "/lib/x86_64-linux-gnu/",
        "/usr/lib/x86_64-linux-gnu/",
        "/lib/aarch64-linux-gnu/",
        "/usr/lib/aarch64-linux-gnu/",
        "/lib64/",
        "/usr/lib64/"
    };
    static constexpr size_t TRUSTED_SYSTEM_DIRS_COUNT = sizeof(TRUSTED_SYSTEM_DIRS) / sizeof(TRUSTED_SYSTEM_DIRS[0]);

    // Secure library loading function with path validation
    void* tryLoadLibrary(const char* lib_path)
    {
        if (!lib_path || lib_path[0] == '\0')
        {
            std::cerr << "[WARNING] Invalid library path provided" << std::endl;
            return nullptr;
        }
        
        // If it's just a library name (no path separators), try system directories
        if (strchr(lib_path, '/') == nullptr)
        {
            for (size_t i = 0; i < TRUSTED_SYSTEM_DIRS_COUNT; i++)
            {
                std::string full_path = std::string(TRUSTED_SYSTEM_DIRS[i]) + lib_path;
                void* handle = tryLoadLibraryFullPath(full_path.c_str());
                if (handle)
                {
                    return handle;
                }
            }
            return nullptr;
        }
        
        // For full paths, use the original logic
        return tryLoadLibraryFullPath(lib_path);
    }
    
    // Helper function for loading with full path validation
    void* tryLoadLibraryFullPath(const char* lib_path)
    {
        // Check if file exists and is readable
        if (access(lib_path, R_OK) != 0)
        {
            // Don't log debug message here to avoid spam
            return nullptr;
        }
        
        // Resolve any symbolic links to get the real path
        char resolved_path[PATH_MAX];
        if (realpath(lib_path, resolved_path) == nullptr)
        {
            return nullptr;
        }
        
        // Validate that the resolved path is in a trusted system directory
        // Use safe string-based prefix checking to avoid dangerous strlen()
        std::string resolved_path_str(resolved_path);
        
        bool is_trusted = false;
        for (size_t i = 0; i < TRUSTED_SYSTEM_DIRS_COUNT; i++)
        {
            if (resolved_path_str.find(TRUSTED_SYSTEM_DIRS[i]) == 0)
            {
                is_trusted = true;
                break;
            }
        }
        
        if (!is_trusted)
        {
            return nullptr;
        }
        
        // Load the library with RTLD_NOW for immediate symbol resolution
        void* handle = dlopen(resolved_path, RTLD_NOW | RTLD_LOCAL);
        if (handle)
        {
            std::cout << "[INFO] LibavWrapper: Successfully loaded library from: " << resolved_path << std::endl;
        }
        
        return handle;
    }

public:
    static LibavWrapper* getInstance()
    {
        static LibavWrapper _instance;
        return &_instance;
    }

    LibavWrapper()
        : av_register_all(nullptr)
        , avformat_open_input(nullptr)
        , avformat_find_stream_info(nullptr)
        , avformat_alloc_output_context2(nullptr)
        , avio_open(nullptr)
        , avformat_new_stream(nullptr)
        , avcodec_parameters_copy(nullptr)
        , avformat_write_header(nullptr)
        , av_seek_frame(nullptr)
        , av_packet_alloc(nullptr)
        , av_read_frame(nullptr)
        , av_packet_unref(nullptr)
        , av_packet_rescale_ts(nullptr)
        , av_interleaved_write_frame(nullptr)
        , av_packet_free(nullptr)
        , avformat_close_input(nullptr)
        , avio_closep(nullptr)
        , avformat_free_context(nullptr)
        , av_write_trailer(nullptr)
        , av_strerror(nullptr)
        , av_rescale_q(nullptr)
        , av_dict_set(nullptr)
        , av_dict_free(nullptr)
        , m_libavMode(LibavModeHardware)
        , handle_libavformat(nullptr)
        , handle_libavcodec(nullptr)
        , handle_libavutil(nullptr)
    {
        std::cout << "[INFO] LibavWrapper: Initializing dynamic libav loading" << std::endl;

                 // Try to load libav libraries dynamically using platform-specific paths
         // Follow the same pattern as other modules in vstmodule.cpp
        
         // Load libavformat from system directories (installed via user_additional_install.sh)
         const char* format_versions[] = {"libavformat.so.60", "libavformat.so", nullptr};
         for (int i = 0; format_versions[i] != nullptr && !handle_libavformat; i++)
         {
             handle_libavformat = tryLoadLibrary(format_versions[i]);
             if (handle_libavformat)
             {
                 std::cout << "[INFO] LibavWrapper: Loaded libavformat " << format_versions[i] << " from system directory" << std::endl;
                 break;
             }
         }
        
         // Load libavcodec from system directories (installed via user_additional_install.sh)
         const char* codec_versions[] = {"libavcodec.so.60", "libavcodec.so", nullptr};
         for (int i = 0; codec_versions[i] != nullptr && !handle_libavcodec; i++)
         {
             handle_libavcodec = tryLoadLibrary(codec_versions[i]);
             if (handle_libavcodec)
             {
                 std::cout << "[INFO] LibavWrapper: Loaded libavcodec " << codec_versions[i] << " from system directory" << std::endl;
                 break;
             }
         }
         
         // Load libavutil from system directories (installed via user_additional_install.sh)
         const char* util_versions[] = {"libavutil.so.58", "libavutil.so", nullptr};
         for (int i = 0; util_versions[i] != nullptr && !handle_libavutil; i++)
         {
             handle_libavutil = tryLoadLibrary(util_versions[i]);
             if (handle_libavutil)
             {
                 std::cout << "[INFO] LibavWrapper: Loaded libavutil " << util_versions[i] << " from system directory" << std::endl;
                 break;
             }
         }

        if (handle_libavformat && handle_libavcodec && handle_libavutil)
        {
            /* Load libavformat functions */
            dlerror();
            avformat_open_input = (avformat_open_input_t) dlsym(handle_libavformat, "avformat_open_input");
            DL_ERROR_EXIT
            avformat_find_stream_info = (avformat_find_stream_info_t) dlsym(handle_libavformat, "avformat_find_stream_info");
            DL_ERROR_EXIT
            avformat_alloc_output_context2 = (avformat_alloc_output_context2_t) dlsym(handle_libavformat, "avformat_alloc_output_context2");
            DL_ERROR_EXIT
            avio_open = (avio_open_t) dlsym(handle_libavformat, "avio_open");
            DL_ERROR_EXIT
            avformat_new_stream = (avformat_new_stream_t) dlsym(handle_libavformat, "avformat_new_stream");
            DL_ERROR_EXIT
            avformat_write_header = (avformat_write_header_t) dlsym(handle_libavformat, "avformat_write_header");
            DL_ERROR_EXIT
            av_read_frame = (av_read_frame_t) dlsym(handle_libavformat, "av_read_frame");
            DL_ERROR_EXIT
            av_interleaved_write_frame = (av_interleaved_write_frame_t) dlsym(handle_libavformat, "av_interleaved_write_frame");
            DL_ERROR_EXIT
            avformat_close_input = (avformat_close_input_t) dlsym(handle_libavformat, "avformat_close_input");
            DL_ERROR_EXIT
            avio_closep = (avio_closep_t) dlsym(handle_libavformat, "avio_closep");
            DL_ERROR_EXIT
            avformat_free_context = (avformat_free_context_t) dlsym(handle_libavformat, "avformat_free_context");
            DL_ERROR_EXIT
            av_write_trailer = (av_write_trailer_t) dlsym(handle_libavformat, "av_write_trailer");
            DL_ERROR_EXIT
            av_seek_frame = (av_seek_frame_t) dlsym(handle_libavformat, "av_seek_frame");
            DL_ERROR_EXIT
            av_guess_frame_rate = (av_guess_frame_rate_t) dlsym(handle_libavformat, "av_guess_frame_rate");
            DL_ERROR_EXIT
            av_find_best_stream = (av_find_best_stream_t) dlsym(handle_libavformat, "av_find_best_stream");
            DL_ERROR_EXIT

            /* Load libavcodec functions */
            dlerror();
            avcodec_parameters_copy = (avcodec_parameters_copy_t) dlsym(handle_libavcodec, "avcodec_parameters_copy");
            DL_ERROR_EXIT
            avcodec_descriptor_get = (avcodec_descriptor_get_t) dlsym(handle_libavcodec, "avcodec_descriptor_get");
            DL_ERROR_EXIT
            av_get_bits_per_sample = (av_get_bits_per_sample_t) dlsym(handle_libavcodec, "av_get_bits_per_sample");
            DL_ERROR_EXIT
            
            // In newer libav/FFmpeg versions, packet functions are in libavcodec
            av_packet_alloc = (av_packet_alloc_t) dlsym(handle_libavcodec, "av_packet_alloc");
            DL_ERROR_EXIT
            av_packet_unref = (av_packet_unref_t) dlsym(handle_libavcodec, "av_packet_unref");
            DL_ERROR_EXIT
            av_packet_rescale_ts = (av_packet_rescale_ts_t) dlsym(handle_libavcodec, "av_packet_rescale_ts");
            DL_ERROR_EXIT
            av_packet_free = (av_packet_free_t) dlsym(handle_libavcodec, "av_packet_free");
            DL_ERROR_EXIT

            /* Load libavutil functions */
            dlerror();
            av_strerror = (av_strerror_t) dlsym(handle_libavutil, "av_strerror");
            DL_ERROR_EXIT
            av_rescale_q = (av_rescale_q_t) dlsym(handle_libavutil, "av_rescale_q");
            DL_ERROR_EXIT
            av_get_bytes_per_sample = (av_get_bytes_per_sample_t) dlsym(handle_libavutil, "av_get_bytes_per_sample");
            DL_ERROR_EXIT
            av_dict_set = (av_dict_set_t) dlsym(handle_libavutil, "av_dict_set");
            DL_ERROR_EXIT
            av_dict_free = (av_dict_free_t) dlsym(handle_libavutil, "av_dict_free");
            DL_ERROR_EXIT

            // Try to load deprecated function (may not be available in newer versions)
            av_register_all = (av_register_all_t) dlsym(handle_libavformat, "av_register_all");
            // Don't exit on error for this deprecated function

            m_libavMode = LibavModeHardware;
            std::cout << "[INFO] LibavWrapper: Dynamic libav loading successful" << std::endl;
        }
        else
        {
            goto close_dl;
        }
        
        std::cout << "[INFO] LibavWrapper mode: " << m_libavMode << std::endl;
        return;

    close_dl:
            std::cerr << "[ERROR] LibavWrapper: Error loading libav libraries" << std::endl;
            if (handle_libavformat) dlclose(handle_libavformat);
            if (handle_libavcodec) dlclose(handle_libavcodec);
            if (handle_libavutil) dlclose(handle_libavutil);
            m_libavMode = LibavModeSoftware;
            throw std::runtime_error("LibavWrapper: Failed to load libav libraries dynamically");
    }

    ~LibavWrapper()
    {
        std::cout << "[INFO] LibavWrapper: Destructor called" << std::endl;
        if (handle_libavformat) dlclose(handle_libavformat);
        if (handle_libavcodec) dlclose(handle_libavcodec);
        if (handle_libavutil) dlclose(handle_libavutil);
    }

    LibavMode getLibavMode() const
    {
        return m_libavMode;
    }

    void setLibavMode(const LibavMode& mode)
    {
        m_libavMode = mode;
    }

    bool isLibavAvailable() const
    {
        return (m_libavMode == LibavModeHardware) && 
               (handle_libavformat != nullptr) && 
               (handle_libavcodec != nullptr) && 
               (handle_libavutil != nullptr);
    }

public:
    // Function pointers
    av_register_all_t av_register_all;
    avformat_open_input_t avformat_open_input;
    avformat_find_stream_info_t avformat_find_stream_info;
    avformat_alloc_output_context2_t avformat_alloc_output_context2;
    avio_open_t avio_open;
    avformat_new_stream_t avformat_new_stream;
    avcodec_parameters_copy_t avcodec_parameters_copy;
    avformat_write_header_t avformat_write_header;
    av_seek_frame_t av_seek_frame;
    av_packet_alloc_t av_packet_alloc;
    av_read_frame_t av_read_frame;
    av_packet_unref_t av_packet_unref;
    av_packet_rescale_ts_t av_packet_rescale_ts;
    av_interleaved_write_frame_t av_interleaved_write_frame;
    av_packet_free_t av_packet_free;
    avformat_close_input_t avformat_close_input;
    avio_closep_t avio_closep;
    avformat_free_context_t avformat_free_context;
    av_write_trailer_t av_write_trailer;
    av_strerror_t av_strerror;
    av_rescale_q_t av_rescale_q;
    av_guess_frame_rate_t av_guess_frame_rate;
    av_find_best_stream_t av_find_best_stream;
    avcodec_descriptor_get_t avcodec_descriptor_get;
    av_get_bits_per_sample_t av_get_bits_per_sample;
    av_get_bytes_per_sample_t av_get_bytes_per_sample;
    av_dict_set_t av_dict_set;
    av_dict_free_t av_dict_free;

private:
    LibavMode m_libavMode;
    void* handle_libavformat;
    void* handle_libavcodec;
    void* handle_libavutil;
}; 