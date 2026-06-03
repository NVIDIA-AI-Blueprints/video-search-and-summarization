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

#include <iostream>
#include <fstream>
#include <memory>
#include <chrono>
#include <ctime>
#include <cstdlib>
#include <sstream>
#include <iomanip>
#include <sys/stat.h>

#include <grpcpp/grpcpp.h>
#include <google/protobuf/timestamp.pb.h>

#include "ds.grpc.pb.h"

using grpc::Channel;
using grpc::ClientContext;
using grpc::Status;
using grpc::ClientReader;

using protocols::daim::gateway::ds::DirectStreaming;
using protocols::daim::gateway::ds::SnapshotRequest;
using protocols::daim::gateway::ds::Snapshot;
using protocols::daim::gateway::ds::PlaybackRequest;
using protocols::daim::gateway::ds::StreamData;
using google::protobuf::Timestamp;

#include "milestone_data_streaming_client.h"
#include "logger.h"
namespace nv_vms {
namespace grpc_client {

MilestoneClient::MilestoneClient() = default;
MilestoneClient::~MilestoneClient() = default;

bool MilestoneClient::TakeSnapshot(const std::string& stream_id,
                                  uint32_t max_width,
                                  uint32_t max_height,
                                  std::vector<uint8_t>& jpeg_data)
    {
        #if 0
        // Create channel for this request
        auto channel = createChannel();
        if (!channel)
        {
            LOG(error) << "Failed to create gRPC channel" << std::endl;
            return false;
        }

        auto stub = DirectStreaming::NewStub(channel);

        // Prepare the request
        SnapshotRequest request;
        request.set_stream_id(stream_id);
        request.set_max_width(max_width);
        request.set_max_height(max_height);

        // Get token from environment or use empty string
        const char* token_env = std::getenv("MILESTONE_OAUTH_TOKEN");
        request.set_token(token_env ? token_env : "");

        // Create context
        ClientContext context;

        LOG(info) << "Taking snapshot from stream: " << stream_id << std::endl;
        LOG(info) << "Max dimensions: " << max_width << "x" << max_height << std::endl;

        // Make the RPC call
        Snapshot response;
        Status status = stub->TakeSnapshot(&context, request, &response);

        if (!status.ok())
        {
            LOG(error) << "RPC failed: " << status.error_message() << std::endl;
            LOG(error) << "Error code: " << status.error_code() << std::endl;
            return false;
        }

        // Check if we got image data
        if (response.jpeg_image().empty())
        {
            LOG(error) << "No snapshot data received" << std::endl;
            return false;
        }

        // Copy JPEG data to output vector
        jpeg_data.assign(response.jpeg_image().begin(), response.jpeg_image().end());

        LOG(info) << "Snapshot received successfully, size: " << jpeg_data.size() << " bytes" << std::endl;
        #endif
        return true;
    }

bool MilestoneClient::PlaybackStream(const std::string& stream_id,
                                    uint64_t start_epoch_ms,
                                    uint64_t end_epoch_ms,
                                    std::string& output_file_path,
                                    const std::string& grpc_endpoint)
    {
        // Create channel for this request
        auto channel = createChannel(grpc_endpoint);
        if (!channel)
        {
            LOG(error) << "Failed to create gRPC channel" << std::endl;
            return false;
        }

        auto stub = DirectStreaming::NewStub(channel);

        // Convert epoch milliseconds to protobuf Timestamp
        Timestamp start_time, end_time;
        start_time.set_seconds(start_epoch_ms / 1000);
        start_time.set_nanos((start_epoch_ms % 1000) * 1000000);

        end_time.set_seconds(end_epoch_ms / 1000);
        end_time.set_nanos((end_epoch_ms % 1000) * 1000000);

        LOG(info) << "Converted timestamps:" << std::endl;
        LOG(info) << "  Start: " << start_epoch_ms << "ms -> "
                  << start_time.seconds() << "s + " << start_time.nanos() << "ns" << std::endl;
        LOG(info) << "  End: " << end_epoch_ms << "ms -> "
                  << end_time.seconds() << "s + " << end_time.nanos() << "ns" << std::endl;
        LOG(info) << "  Duration: " << (end_epoch_ms - start_epoch_ms) / 1000.0 << " seconds" << std::endl;

        // Prepare the request
        PlaybackRequest request;
        std::string combined_stream_id = stream_id;
        request.set_stream_id(combined_stream_id);
        *request.mutable_from() = start_time;
        *request.mutable_to() = end_time;

        LOG(info) << "Request details:" << std::endl;
        LOG(info) << "  Combined stream ID: " << combined_stream_id << std::endl;
        LOG(info) << "Actual streamId: " << stream_id << std::endl;

        // Generate output filename
        if (output_file_path.empty())
        {
            output_file_path = generateClipFilename(stream_id, start_epoch_ms, end_epoch_ms);
        }

        // Output file will be opened after determining codec
        std::ofstream output_file;

        // Create context
        ClientContext context;

        LOG(info) << "Starting playback download from stream: " << stream_id << std::endl;
        LOG(info) << "Time range: " << start_epoch_ms << " - " << end_epoch_ms << " ms" << std::endl;

        // Make the RPC call
        LOG(info) << "Making PlaybackStream RPC call to server..." << std::endl;
        std::unique_ptr<ClientReader<StreamData>> reader(
            stub->PlaybackStream(&context, request));
        LOG(info) << "RPC call initiated, waiting for response..." << std::endl;

        StreamData stream_data;
        int frame_count = 0;
        size_t total_bytes = 0;
        int stream_messages = 0;
        std::string codec_name;

        // Timing variables
        auto download_start_time = std::chrono::steady_clock::now();
        bool first_frame_received = false;

        LOG(info) << "Waiting for stream data..." << std::endl;

        // Read the stream
        while (reader->Read(&stream_data))
        {
            stream_messages++;

            if (stream_messages == 1)
            {
                LOG(info) << "First message received from server" << std::endl;
            }

            if (stream_data.has_video_data())
            {
                const auto& video_data = stream_data.video_data();

                // Log codec information on first video data
                if (!first_frame_received && video_data.frames().size() > 0)
                {
                    first_frame_received = true;
                    LOG(info) << "First video frame received" << std::endl;

                    switch (video_data.codec())
                    {
                        case protocols::daim::gateway::ds::VideoCodec::H264:
                            codec_name = "H264";
                            break;
                        case protocols::daim::gateway::ds::VideoCodec::H265:
                            codec_name = "H265";
                            break;
                        default:
                            codec_name = "Unknown";
                            break;
                    }
                    LOG(info) << "Video codec: " << codec_name << " (code: " << video_data.codec() << ")" << std::endl;

                    // Update output file path with appropriate extension based on codec
                    std::string correct_extension;
                    if (codec_name == "H264")
                    {
                        correct_extension = ".h264";
                    }
                    else if (codec_name == "H265")
                    {
                        correct_extension = ".h265";
                    }
                    else
                    {
                        correct_extension = ".raw";
                    }

                    // Remove existing extension if present and add correct one
                    size_t dot_pos = output_file_path.rfind('.');
                    if (dot_pos != std::string::npos)
                    {
                        // Check if the dot is after the last directory separator
                        size_t slash_pos = output_file_path.rfind('/');
                        if (slash_pos == std::string::npos || dot_pos > slash_pos)
                        {
                            output_file_path = output_file_path.substr(0, dot_pos);
                        }
                    }
                    output_file_path += correct_extension;

                    LOG(info) << "Output file: " << output_file_path << std::endl;

                    // Now open the output file with the correct extension
                    output_file.open(output_file_path, std::ios::binary);
                    if (!output_file.is_open())
                    {
                        LOG(error) << "Cannot create output file: " << output_file_path << std::endl;
                        return false;
                    }
                }

                // Write each frame's raw video data
                for (const auto& frame : video_data.frames())
                {
                    size_t frame_size = frame.data().size();
                    if (frame_size == 0)
                    {
                        LOG(warning) << "Received empty frame at index " << frame_count << std::endl;
                        continue;
                    }

                    output_file.write(frame.data().data(), frame_size);
                    if (!output_file.good())
                    {
                        LOG(error) << "Failed to write frame " << frame_count << " to file" << std::endl;
                        if (output_file.is_open())
                        {
                            output_file.close();
                        }
                        return false;
                    }

                    frame_count++;
                    total_bytes += frame_size;

                    // Log first few frames for debugging
                    if (frame_count <= 5)
                    {
                        LOG(info) << "Frame " << frame_count << ": size=" << frame_size
                                  << " bytes, timestamp=" << frame.timestamp().seconds()
                                  << "s+" << frame.timestamp().nanos() << "ns" << std::endl;
                    }

                    // Progress indicator every 100 frames
                    if (frame_count % 100 == 0)
                    {
                        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now() - download_start_time);
                        LOG(info) << "Progress: Downloaded " << frame_count << " frames, "
                                  << (total_bytes / 1024.0 / 1024.0) << " MB in "
                                  << elapsed.count() / 1000.0 << "s" << std::endl;
                    }
                }
            }
            else if (stream_data.has_audio_data())
            {
                LOG(warning) << "Received audio data (ignored)" << std::endl;
            }
            else
            {
                LOG(warning) << "Received message with neither video nor audio data" << std::endl;
            }
        }

        LOG(info) << "Stream reading completed" << std::endl;

        // Check the status
        Status status = reader->Finish();
        if (!status.ok())
        {
            LOG(error) << "RPC failed: " << status.error_message() << std::endl;
            LOG(error) << "Error code: " << status.error_code() << std::endl;
            if (output_file.is_open())
            {
                output_file.close();
            }
            return false;
        }

        if (output_file.is_open())
        {
            output_file.close();
        }

        // Calculate total download time
        auto total_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - download_start_time);
        double total_seconds = total_elapsed.count() / 1000.0;

        LOG(info) << "Download completed successfully!" << std::endl;
        LOG(info) << "Summary:" << std::endl;
        LOG(info) << "  Total messages: " << stream_messages << std::endl;
        LOG(info) << "  Total frames: " << frame_count << std::endl;
        LOG(info) << "  Total size: " << (total_bytes / 1024.0 / 1024.0) << " MB" << std::endl;
        LOG(info) << "  Download time: " << std::fixed << std::setprecision(2)
                  << total_seconds << " seconds" << std::endl;

        if (frame_count == 0)
        {
            LOG(warning) << "No frames were received" << std::endl;
            return false;
        }

        return true;
    }

std::shared_ptr<Channel> MilestoneClient::createChannel(const std::string& grpc_endpoint)
    {
        // Check if grpc_endpoint is correct
        const std::string grpc_prefix = "grpc://";
        if (grpc_endpoint.substr(0, grpc_prefix.length()) != grpc_prefix)
        {
            LOG(error) << "Invalid gRPC endpoint: " << grpc_endpoint
                       << " (must start with 'grpc://')" << std::endl;
            return nullptr;
        }

        // Extract server address (everything after "grpc://")
        std::string server_address = grpc_endpoint.substr(grpc_prefix.length());

        if (server_address.empty())
        {
            LOG(error) << "Invalid gRPC endpoint: server address is empty after 'grpc://'" << std::endl;
            return nullptr;
        }

        LOG(info) << "Connecting to gRPC server at " << server_address << std::endl;

        // Create insecure channel
        return grpc::CreateChannel(server_address, grpc::InsecureChannelCredentials());
    }

std::string MilestoneClient::generateClipFilename(const std::string& stream_id,
                                                 uint64_t start_ms,
                                                 uint64_t end_ms)
    {
        // Format timestamps for filename
        auto formatTimestamp = [](uint64_t epoch_ms) -> std::string {
            std::time_t seconds = epoch_ms / 1000;
            int millis = epoch_ms % 1000;
            std::tm tm_buf{};
            gmtime_r(&seconds, &tm_buf);

            std::ostringstream oss;
            oss << std::put_time(&tm_buf, "%Y%m%d_%H%M%S");
            oss << "_" << std::setfill('0') << std::setw(3) << millis;
            return oss.str();
        };

        // Replace "/" with "-" in stream_id for valid filename
        std::string safe_stream_id = stream_id;
        std::replace(safe_stream_id.begin(), safe_stream_id.end(), '/', '-');

        std::string base_filename = "clip-" + safe_stream_id + "-" +
                                   formatTimestamp(start_ms) + "-" +
                                   formatTimestamp(end_ms) + ".h264";

        // Check if file exists and add counter if needed
        return getUniqueFilename(base_filename);
    }

std::string MilestoneClient::getUniqueFilename(const std::string& base_filename)
    {
        if (!fileExists(base_filename))
        {
            return base_filename;
        }

        // Extract base and extension
        size_t dot_pos = base_filename.rfind('.');
        std::string base = base_filename.substr(0, dot_pos);
        std::string ext = base_filename.substr(dot_pos);

        // Find unique filename
        int counter = 1;
        std::string new_filename;
        do {
            std::ostringstream oss;
            oss << base << "-" << std::setfill('0') << std::setw(3) << counter << ext;
            new_filename = oss.str();
            counter++;
        } while (fileExists(new_filename));

        return new_filename;
    }

bool MilestoneClient::fileExists(const std::string& filename)
    {
        struct stat buffer;
        return (stat(filename.c_str(), &buffer) == 0);
    }

} // namespace grpc_client
} // namespace nv_vms

