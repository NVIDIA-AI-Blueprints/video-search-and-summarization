/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <string>
#include <vector>

namespace nv_vms {

enum class ImageFormat { JPEG };
enum class VideoCodec { H264, H265 };
// Avoid macro collisions with common defines like MP4/MKV by using camel-case names
enum class ContainerFormat { Mp4, Mkv };

struct ConnectionParams
{
    std::string ip;
    std::string user;
    std::string password;
    uint16_t    port{0};
    std::string url; // optional
};

struct Capabilities
{
    std::vector<ImageFormat>    image_formats;
    std::vector<VideoCodec>     video_codecs;
    std::vector<ContainerFormat> containers;
};

struct SnapshotRequest
{
    std::string camera_id;
    ImageFormat format{ImageFormat::JPEG};
    uint32_t    width{0};   // 0 = original
    uint32_t    height{0};  // 0 = original
    uint64_t    ts_ms{0};   // 0 = now
};

struct SnapshotResponse
{
    std::vector<uint8_t> bytes; // encoded JPEG
    ImageFormat          format{ImageFormat::JPEG};
};

struct ClipRequest
{
    std::string     camera_id;
    uint64_t        start_ms{0};
    uint64_t        end_ms{0};
    VideoCodec      codec{VideoCodec::H265};
    ContainerFormat container{ContainerFormat::Mp4};
    int32_t         frame_rate{1};
};

struct ClipResponse
{
    // Return path to a produced file for simplicity
    std::string file_path;
};

constexpr int MEDIA_OK                    = 0;
constexpr int MEDIA_ERR_NOT_SUPPORTED     = -1;
constexpr int MEDIA_ERR_INVALID_ARGUMENT  = -2;
constexpr int MEDIA_ERR_CONNECTION        = -3;
constexpr int MEDIA_ERR_IO                = -4;

} // namespace nv_vms


