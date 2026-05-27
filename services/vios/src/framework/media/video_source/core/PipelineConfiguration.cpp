/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "PipelineConfiguration.h"
#include "logger.h"
#include "config.h"
#include "nvhwdetection.h"
#include "osd/llosd.h"
#include "utils.h"
#include "../../overlays/overlay_internal.h"

// Implementation of PipelineConfiguration
PipelineConfiguration::PipelineConfiguration()
    : m_uri("")
{
    // Default configuration - all members are already default-initialized
}

PipelineConfiguration::PipelineConfiguration(const std::string& uri, const std::map<std::string, std::string, std::less<>>& opts)
    : m_uri(uri)
{
    parseOptions(opts);
    validateConfiguration();
}

void PipelineConfiguration::parseOptions(const std::map<std::string, std::string, std::less<>>& opts)
{
    m_options = opts;
    
    // Parse basic options
    if (opts.find("peerid") != opts.end()) {
        m_peerId = opts.at("peerid");
        m_peerIdStreamId = m_peerId;
    }
    
    if (opts.find("streamId") != opts.end()) {
        m_peerIdStreamId = m_peerIdStreamId + ":" + opts.at("streamId");
    }
    
    // Parse playback type
    m_recordedPlayback = (m_uri.find("file://") == 0);
    m_isCloudStream = (m_uri.find("s3://") != std::string::npos) || ((opts.find("storageLocation") != opts.end() && opts.at("storageLocation") == "cloud"));
    m_isNativeStream = (opts.find("capture_type") != opts.end() && opts.at("capture_type") == "native_stream");
    m_imageCapture = (opts.find("image_capture") != opts.end());
    m_godsEyeView = (opts.find("gods_eye_view") != opts.end() && opts.at("gods_eye_view") == "true");

    // Handle gods eye view URI modification
    if (m_godsEyeView)
    {
        m_uri += GET_CONFIG().floor_map_file_path + "#Camera_Map##";
    }
    // Setup configurations
    setupQualityConfig(opts);
    setupCompositorConfig(opts);
    setupOverlayConfig(opts);
}

void PipelineConfiguration::setupQualityConfig(const std::map<std::string, std::string, std::less<>>& opts)
{
    if (opts.find("quality") != opts.end()) {
        m_quality.quality = opts.at("quality");
    }
    
    if (opts.find("framerate") != opts.end()) {
        m_quality.frameRate = opts.at("framerate");
    }
    
    m_quality.hlsPlayback = (opts.find("hls") != opts.end() && opts.at("hls") == "hls");
    
    // Determine pass-through mode
    if (m_isNativeStream) {
        m_quality.passThrough = false; // Not supported for native streams
    } else if (m_recordedPlayback == false && 
               (GET_CONFIG().webrtc_out_encode_fallback_option == WEBRTC_OUT_FALLBACK_PASS_THROUGH || 
                m_quality.quality == "pass_through")) {
        
        std::string codec = getCodec();
        if (!codec.empty()) {
            if (iequals(codec, "H265") && GET_CONFIG().remote_vst_address.empty()) {
                m_quality.quality = "auto";
                LOG(error) << "Pass Through mode is not supported in H265, falling back to transcode" << endl;
            } else if (iequals(codec, "H264") || iequals(codec, "H265")) {
                m_quality.passThrough = true;
                LOG(info) << "Enabled Pass through by default on this platform" << endl;
            } else {
                m_quality.quality = "high";
                m_quality.passThrough = false;
            }
        }
    }
}

void PipelineConfiguration::setupCompositorConfig(const std::map<std::string, std::string, std::less<>>& opts)
{
    m_compositor.enabled = (opts.find("do_composition") != opts.end());
    
    if (m_compositor.enabled) {
        m_compositor.urls = stringToVector(m_uri);
        
        if (opts.find("overlayShowSensorName") != opts.end()) {
            m_compositor.showSensorName = true;
            if (opts.find("overlaySensorPosX") != opts.end() && opts.find("overlaySensorPosY") != opts.end()) {
                m_compositor.overlaySensorPosX = stoi(opts.at("overlaySensorPosX"));
                m_compositor.overlaySensorPosY = stoi(opts.at("overlaySensorPosY"));
            }
        }

        auto it = opts.find("compositeLayout");
        if (it != opts.end())
        {
            m_compositor.layoutJson = it->second;
        }
    }
}

void PipelineConfiguration::setupOverlayConfig(const std::map<std::string, std::string, std::less<>>& opts)
{
    if (opts.find("sensorID") != opts.end()) {
        m_overlay.sensorId = opts.at("sensorID");
    }
    
    if (opts.find("tag") != opts.end()) {
        m_overlay.tag = opts.at("tag");
    }
    
    // Check if overlay is explicitly requested
    bool overlayRequested = false;
    if (opts.find("overlay") != opts.end() && opts.at("overlay") == "true") {
        overlayRequested = true;
        LOG(info) << "Overlay explicitly requested via 'overlay' option" << endl;
    }
    
    if (opts.find("overlayBbox") != opts.end() && opts.at("overlayBbox") == "true") {
        overlayRequested = true;
        m_overlay.bboxEnabled = true;
        LOG(info) << "Overlay bounding boxes requested via 'overlayBbox' option" << endl;
    }
    
    if (opts.find("overlayDebug") != opts.end() && opts.at("overlayDebug") == "true") {
        overlayRequested = true;
        LOG(info) << "Overlay debug mode requested via 'overlayDebug' option" << endl;
    }
    
    // Overlay is enabled if OSD is available, not in error state, AND explicitly requested
    m_overlay.enabled = !GET_OSD_INSTANCE()->isError() && overlayRequested;
    
    LOG(info) << "Overlay configuration: enabled=" << (m_overlay.enabled ? "true" : "false") 
              << ", bboxEnabled=" << (m_overlay.bboxEnabled ? "true" : "false") 
              << ", OSD_available=" << (!GET_OSD_INSTANCE()->isError() ? "true" : "false")
              << ", requested=" << (overlayRequested ? "true" : "false") << endl;
}

void PipelineConfiguration::validateConfiguration() const
{
    if (m_compositor.enabled) {
        if (m_compositor.urls.size() < MIN_COMPOSITOR_LIMIT) {
            throw std::invalid_argument("Error in Creating Compositor, select atleast 2 streams");
        }
        
        if (NvHwDetection::getInstance()->m_useNvV4l2Enc == false && 
            NvHwDetection::getInstance()->m_useNvV4l2Dec == false) {
            throw std::invalid_argument("Error in Creating Compositor, HW is required for Video Wall");
        }
        
        if (m_compositor.urls.size() > MAX_COMPOSITOR_LIMIT) {
            LOG(warning) << "Maximum " << MAX_COMPOSITOR_LIMIT << " streams are supported" << endl;
        }
    }
    
    if (m_quality.passThrough && m_isNativeStream) {
        throw std::invalid_argument("pass_through not supported for native stream");
    }
}

std::string PipelineConfiguration::getDeviceId() const
{
    auto it = m_options.find("sensorId");
    return (it != m_options.end()) ? it->second : "";
}

std::string PipelineConfiguration::getCodec() const
{
    auto it = m_options.find("codec");
    return (it != m_options.end()) ? it->second : "";
}

std::string PipelineConfiguration::getSensorType() const
{
    auto it = m_options.find("sensor_type");
    return (it != m_options.end()) ? it->second : "";
}

std::string PipelineConfiguration::getContainer() const
{
    auto it = m_options.find("container");
    return (it != m_options.end()) ? it->second : "";
}

std::string PipelineConfiguration::getStartTime() const
{
    auto it = m_options.find("startTime");
    return (it != m_options.end()) ? it->second : "";
}

std::string PipelineConfiguration::getEndTime() const
{
    auto it = m_options.find("endTime");
    return (it != m_options.end()) ? it->second : "";
}
