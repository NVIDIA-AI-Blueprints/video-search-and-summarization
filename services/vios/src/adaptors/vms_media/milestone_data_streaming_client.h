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

#include <string>
#include <vector>
#include <memory>
#include <cstdint>

namespace grpc {
    class Channel;
}

namespace nv_vms {
namespace grpc_client {

class MilestoneClient
{
public:
    MilestoneClient();
    ~MilestoneClient();

    // Take a snapshot from the specified stream
    bool TakeSnapshot(const std::string& stream_id, 
                     uint32_t max_width, 
                     uint32_t max_height,
                     std::vector<uint8_t>& jpeg_data);

    // Stream playback data and save to file
    bool PlaybackStream(const std::string& stream_id,
                       uint64_t start_epoch_ms,
                       uint64_t end_epoch_ms,
                       std::string& output_file_path,
                       const std::string& grpc_endpoint);

private:
    // Create gRPC channel with configuration
    std::shared_ptr<grpc::Channel> createChannel(const std::string& grpc_endpoint);

    // Generate filename for clip
    std::string generateClipFilename(const std::string& stream_id, 
                                    uint64_t start_ms, 
                                    uint64_t end_ms);

    // Get unique filename with incrementing number if file exists
    std::string getUniqueFilename(const std::string& base_filename);

    // Check if file exists
    bool fileExists(const std::string& filename);
};

} // namespace grpc_client
} // namespace nv_vms
