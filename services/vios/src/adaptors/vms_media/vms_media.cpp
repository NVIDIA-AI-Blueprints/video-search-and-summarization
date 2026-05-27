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

#include <memory>
#include <string>
#include <sstream>
#include <jsoncpp/json/json.h>

#include "vms_media_interface.h"
#include "logger.h"
#include "network_utils.h"
#include "gst_utils.h"
#include "milestone_data_streaming_client.h"
#include "config.h"

using namespace nv_vms;
struct VideoStreamInfo
{
    std::string video_stream_id;
    std::string grpc_endpoint;
    uint32_t width;
    uint32_t height;
};
namespace {

bool queryGraphQLForStreamInfo(const std::string& camera_id
                            , VideoStreamInfo& stream_info)
{
    nv_vms::DeviceConfig config = GET_CONFIG();
    if (config.ai_bridge_endpoint.empty())
    {
        LOG(error) << "AI Bridge Endpoint is not set" << std::endl;
        return false;
    }
    const std::string graphql_url = config.ai_bridge_endpoint;

    // Form the GraphQL query
    Json::Value postData;
    postData["query"] =
        "query { cameras { id videoStreams { id videoResolution { height width } streamAvailability { ds } } } }";

    LOG(info) << "Querying GraphQL server for camera: " << camera_id << std::endl;

    // Make the curl POST request
    std::string response;
    if (!curlPostRequest(graphql_url, response, postData))
    {
        LOG(error) << "Failed to query GraphQL server" << std::endl;
        return false;
    }

    LOG(verbose) << "Response: " << stringToJson(response).toStyledString() << std::endl;
    // Parse the JSON response
    Json::Value root;
    Json::CharReaderBuilder reader_builder;
    std::string errors;
    std::istringstream response_stream(response);

    if (!Json::parseFromStream(reader_builder, response_stream, &root, &errors))
    {
        LOG(error) << "Failed to parse JSON response: " << errors << std::endl;
        return false;
    }

    // Navigate through the JSON structure
    if (!root.isMember("data") || !root["data"].isMember("cameras"))
    {
        LOG(error) << "Invalid JSON structure" << std::endl;
        return false;
    }

    const Json::Value& cameras = root["data"]["cameras"];
    if (!cameras.isArray())
    {
        LOG(error) << "Cameras is not an array" << std::endl;
        return false;
    }

    // Find the matching camera
    for (const auto& camera : cameras)
    {
        if (!camera.isMember("id") || camera["id"].asString() != camera_id)
        {
            continue;
        }

        LOG(info) << "Found matching camera: " << camera_id << std::endl;

        // Get videoStreams array
        if (!camera.isMember("videoStreams") || !camera["videoStreams"].isArray())
        {
            LOG(error) << "No videoStreams found for camera" << std::endl;
            return false;
        }

        const Json::Value& videoStreams = camera["videoStreams"];
        if (videoStreams.empty())
        {
            LOG(error) << "Empty videoStreams array" << std::endl;
            return false;
        }

        // Always use the first video stream
        LOG(info) << "Using first video stream" << std::endl;
        const Json::Value& selected_stream = videoStreams[0];

        if (!selected_stream.isMember("id"))
        {
            LOG(error) << "Stream missing id field" << std::endl;
            return false;
        }

        stream_info.video_stream_id = selected_stream["id"].asString();

        // Extract resolution
        if (selected_stream.isMember("videoResolution"))
        {
            const auto& resolution = selected_stream["videoResolution"];
            stream_info.width = resolution["width"].asUInt();
            stream_info.height = resolution["height"].asUInt();
        }
        else
        {
            stream_info.width = 0;
            stream_info.height = 0;
        }

        // Extract gRPC endpoint
        if (selected_stream.isMember("streamAvailability") &&
            selected_stream["streamAvailability"].isMember("ds"))
        {
            stream_info.grpc_endpoint = selected_stream["streamAvailability"]["ds"].asString();
        }
        else
        {
            LOG(error) << "Stream missing grpc endpoint" << std::endl;
            return false;
        }

        LOG(info) << "Successfully retrieved stream info:" << std::endl;
        LOG(info) << "  Video Stream ID: " << stream_info.video_stream_id << std::endl;
        LOG(info) << "  gRPC Endpoint: " << stream_info.grpc_endpoint << std::endl;
        LOG(info) << "  Resolution: " << stream_info.width << "x" << stream_info.height << std::endl;

        return true;
    }

    LOG(error) << "Camera ID not found: " << camera_id << std::endl;
    return false;
}
class VmsMedia final : public nv_vms::IMediaInterface
{
private:
    std::unique_ptr<grpc_client::MilestoneClient> m_grpcClient;

public:
    VmsMedia()
        : m_grpcClient(std::make_unique<grpc_client::MilestoneClient>())
    { }

    ~VmsMedia()
    {
        close();
    }

    int connect(const nv_vms::ConnectionParams& conn) override
    {
        (void)conn;
        return nv_vms::MEDIA_OK;
    }

    bool isServerOnline(const std::string& url) override
    {
        (void)url;
        return true;
    }

    nv_vms::Capabilities getCapabilities() const override
    {
        nv_vms::Capabilities caps;
        caps.image_formats = { nv_vms::ImageFormat::JPEG };
        caps.video_codecs = { nv_vms::VideoCodec::H264, nv_vms::VideoCodec::H265 };
        caps.containers = { nv_vms::ContainerFormat::Mp4, nv_vms::ContainerFormat::Mkv };
        return caps;
    }

    int fetchSnapshot(const nv_vms::SnapshotRequest& req, nv_vms::SnapshotResponse& out) override
    {
        (void)req;
        (void)out;
        return nv_vms::MEDIA_ERR_NOT_SUPPORTED;
    }

    int fetchClip(const nv_vms::ClipRequest& req, nv_vms::ClipResponse& out) override
    {
        if (req.camera_id.empty())
        {
            LOG(error) << "Invalid camera_id" << std::endl;
            return MEDIA_ERR_INVALID_ARGUMENT;
        }

        if (req.start_ms >= req.end_ms)
        {
            LOG(error) << "Invalid time range: start="
                    << req.start_ms << ", end=" << req.end_ms << std::endl;
            return MEDIA_ERR_INVALID_ARGUMENT;
        }

        // Query GraphQL server to get video stream information
        VideoStreamInfo stream_info;

        LOG(info) << "Querying GraphQL for camera: " << req.camera_id << std::endl;

        if (!queryGraphQLForStreamInfo(req.camera_id, stream_info))
        {
            LOG(error) << "Failed to query stream info from GraphQL" << std::endl;
            return MEDIA_ERR_IO;
        }

        // Use the full video stream ID from GraphQL response
        std::string stream_id = stream_info.video_stream_id;
        std::string grpc_endpoint = stream_info.grpc_endpoint;
        std::string output_file_path = out.file_path;
        LOG(info) << "Requesting clip for video stream: " << stream_id << std::endl;
        LOG(info) << "  gRPC Endpoint: " << grpc_endpoint << std::endl;
        LOG(info) << "  Resolution: " << stream_info.width << "x" << stream_info.height << std::endl;
        LOG(info) << "  Time range: " << req.start_ms << " - " << req.end_ms << " ms" << std::endl;

        try
        {
            std::string containerFormatStr;
            if (req.container == ContainerFormat::Mp4)
            {
                containerFormatStr = "mp4";
            }
            else if (req.container == ContainerFormat::Mkv)
            {
                containerFormatStr = "mkv";
            }
            else
            {
                LOG(error) << "Unsupported container format" << std::endl;
                return MEDIA_ERR_NOT_SUPPORTED;
            }

            std::string codecStr;
            if (req.codec == VideoCodec::H264)
            {
                codecStr = "h264";
            }
            else if (req.codec == VideoCodec::H265)
            {
                codecStr = "h265";
            }
            else
            {
                LOG(error) << "Unsupported codec" << std::endl;
                return MEDIA_ERR_NOT_SUPPORTED;
            }

            if (!m_grpcClient->PlaybackStream(stream_id, req.start_ms, req.end_ms, output_file_path, grpc_endpoint))
            {
                LOG(error) << "Failed to download playback stream" << std::endl;
                return MEDIA_ERR_IO;
            }

            // Mux the elementary stream into the requested container format
            std::string muxedFilePath;
            if (!muxElementaryStream(output_file_path, codecStr, containerFormatStr, muxedFilePath, req.frame_rate))
            {
                LOG(error) << "Failed to mux elementary stream, returning elementary file" << std::endl;
                // Return elementary file path on muxing failure
                out.file_path = output_file_path;
            }
            else
            {
                // Success - set the muxed file path in response
                out.file_path = muxedFilePath;
            }

            LOG(info) << "Successfully downloaded clip to: " << out.file_path << std::endl;
            return MEDIA_OK;
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Exception: " << e.what() << std::endl;
            return MEDIA_ERR_IO;
        }
    }

    void close() override {}
};

} // anonymous namespace

extern "C" nv_vms::IMediaInterface* createMediaObject()
{
    return new VmsMedia();
}

extern "C" void destroyMediaObject(nv_vms::IMediaInterface* object)
{
    delete object;
}


