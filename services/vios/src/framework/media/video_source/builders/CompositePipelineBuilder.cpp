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

#include "CompositePipelineBuilder.h"
#include "../decoders/gstnvvideodecoder.h"
#include "../decoders/decoderpool.h"
#include "../encoders/nvvideoencoder.h"
#include "../encoders/image_encoder.h"
#include "../../overlays/ll_overlay.h"
#include "../processors/transforms/ll_transform.h"
#include "../processors/compositors/nvcompositor.h"
#include "../senders/webrtc_sink_consumer.h"
#include "logger.h"
#include "config.h"
#include "nvhwdetection.h"
#include "utils.h"
#include "osd/llosd.h"
#include <thread>
#include <chrono>



// Implementation of CompositePipelineBuilder
void CompositePipelineBuilder::buildPipeline(const PipelineConfiguration& config)
{
    m_config = config;
    
    // Create common components first
    createCommonComponents(config);
    
    validateCompositorRequirements(config);
    buildDecoderPipelines(config);
    buildCompositorPipeline(config);
    setupCompositorConsumers(config);
}

void CompositePipelineBuilder::validateCompositorRequirements(const PipelineConfiguration& config) const
{
    const auto& compositor = config.getCompositor();
    if (compositor.urls.size() < MIN_COMPOSITOR_LIMIT) {
        throw std::invalid_argument("Error in Creating Compositor, select atleast 2 streams");
    }
    
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == false && 
        NvHwDetection::getInstance()->m_useNvV4l2Dec == false) {
        throw std::invalid_argument("Error in Creating Compositor, HW is required for Video Wall");
    }
    
    if (compositor.urls.size() > MAX_COMPOSITOR_LIMIT) {
        LOG(warning) << "Maximum " << MAX_COMPOSITOR_LIMIT << " streams are supported" << endl;
    }
}

void CompositePipelineBuilder::buildDecoderPipelines(const PipelineConfiguration& config)
{
    LOG(info) << "🔧 BUILDING DECODER PIPELINES" << endl;
    LOG(info) << "==========================================" << endl;
    
    const auto& compositor = config.getCompositor();
    auto urls = compositor.urls;
    
    // Limit to MAX_COMPOSITOR_LIMIT
    if (urls.size() > MAX_COMPOSITOR_LIMIT) {
        LOG(warning) << "⚠️  Limiting to " << MAX_COMPOSITOR_LIMIT << " streams (max supported)" << endl;
        urls.erase(urls.begin() + MAX_COMPOSITOR_LIMIT, urls.end());
    }
    
    LOG(info) << "📡 Setting up " << urls.size() << " decoder streams" << endl;
    
    for (size_t i = 0; i < urls.size(); i++) {
        string url = urls.at(i);
        auto url_name_array = splitString(url, "#");
        LOG(info) << "   🔗 Stream " << (i+1) << ": " << urls.at(i) << endl;
        
        std::map<std::string, std::string, std::less<>> opts = config.getOptions();
        opts["sensorID"] = url_name_array[1];
        opts["gods_eye_view"] = "false";
        if ((url.find("rtsp://") != 0)) {
            opts["gods_eye_view"] = "true";
        }
        
        // Create decoder for this URL
        DecoderPool* pool = DecoderPool::getInstance();
        auto decoder = pool->getDecoder(url_name_array[0]);
        if (decoder == nullptr) {
            pool->addStream(url_name_array[0], opts);
            decoder = pool->getDecoder(url_name_array[0]);
        }
        
        if (decoder) {
            decoder->setOptions(opts);
            if (!config.isHlsPlayback()) {
                decoder->setNeedSharedStream();
            }
            
            // Set up source producer for live playback
            if (config.isLivePlayback()) {
                setupDecoderWithProducer(decoder, url_name_array[0], config);
            }
            
            dec_result result = pool->tryDecoderStart(decoder, url_name_array[0]);
            if (!result.first) {
                LOG(error) << "Error in Creating Pipeline for URL: " << url_name_array[0] << endl;
                pool->removeStream(url_name_array[0]);
                throw std::invalid_argument("Error in Creating Pipeline");
            }
            
            m_decoders.push_back(decoder);
            m_decoderUris.push_back(url_name_array[0]);  // Store URI for cleanup
            
            // Create overlay for this decoder if OSD is available and sensor name should be shown
            if (config.getCompositor().showSensorName && !GET_OSD_INSTANCE()->isError()) {
                std::map<std::string, std::string, std::less<>> overlayOpts = opts;
                if (overlayOpts.find("sensorID") != overlayOpts.end() && overlayOpts.find("tag") != overlayOpts.end()) {
                    std::string originalSensorID = overlayOpts.at("sensorID");
                    std::string tag = overlayOpts.at("tag");
                    overlayOpts.erase("sensorID");
                    overlayOpts.insert(std::make_pair("sensorID", originalSensorID + "-" + tag));
                }
                string consumer_name = "overlay_composite_" + std::to_string(i);
                auto overlay = std::make_shared<NvLLOverlay>(consumer_name, url_name_array[0], overlayOpts);
                m_overlays.push_back(overlay);
                LOG(info) << "   🎨 Created overlay for stream " << (i+1) << " (sensor: " << url_name_array[1] << ")" << endl;
            } else {
                m_overlays.push_back(nullptr);
                LOG(info) << "   ⏭️  No overlay for stream " << (i+1) << " (OSD disabled or unavailable)" << endl;
            }
        }
    }

    // Add additional gods eye view stream if enabled
    if (config.isGodsEyeView()) {
        LOG(info) << "  Adding Gods Eye View Stream" << endl;

        LOG(info) << "Creating decoder for file: " << config.getUri() << endl;
        string consumer_name = "video_decoder_gods_eye_composite_" + config.getPeerId();
        auto godsEyeDecoder = std::make_shared<GstNvVideoDecoder>(consumer_name, config.getUri(), config.getOptions());
        godsEyeDecoder->create(true);

        // Set up producer for live playback (including live image capture)
        if (config.isLivePlayback())
        {
            setupDecoderWithProducer(godsEyeDecoder, config.getUri(), config);
        }

        // Add to decoders list
        m_decoders.push_back(godsEyeDecoder);
        m_decoderUris.push_back(config.getUri());  // Store URI for cleanup

        // Create overlay for gods eye view if needed
        if (config.getCompositor().showSensorName && !GET_OSD_INSTANCE()->isError())
        {
            std::map<std::string, std::string, std::less<>> overlayOpts = config.getOptions();
            overlayOpts["sensorID"] = "GodsEyeView";
            string overlay_consumer_name = "overlay_composite_gods_eye_" + config.getPeerId();
            auto overlay = std::make_shared<NvLLOverlay>(overlay_consumer_name, config.getUri(), overlayOpts);
            m_overlays.push_back(overlay);
            LOG(info) << "Created overlay for gods eye view stream" << endl;
        }
        else
        {
            m_overlays.push_back(nullptr);
            LOG(info) << "No overlay for gods eye view stream (OSD disabled or unavailable)" << endl;
        }
        LOG(info) << "Added gods eye view stream to composite pipeline" << endl;
    }
    else
    {
        LOG(info) << "Gods eye view is not enabled for composite pipeline" << endl;
    }

    LOG(info) << "==========================================" << endl;
    LOG(info) << "📊 DECODER PIPELINE SUMMARY" << endl;
    LOG(info) << "   📹 Decoders created: " << m_decoders.size() << endl;
    LOG(info) << "   🎨 Overlays created: " << m_overlays.size() << endl;
    LOG(info) << "==========================================" << endl;
}

void CompositePipelineBuilder::buildCompositorPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "🏗️  BUILDING COMPOSITOR PIPELINE" << endl;
    LOG(info) << "==========================================" << endl;
    
    // For image capture, we don't need the compositor pipeline
    if (config.isImageCapture()) {
        LOG(info) << "⏭️  Skipping compositor pipeline setup for image capture" << endl;
        LOG(info) << "==========================================" << endl;
        return;
    }
    
    const auto& compositor = config.getCompositor();
    LOG(info) << "🔧 Creating NvCompositor with " << compositor.urls.size() << " streams" << endl;
    
    // Create a grid layout with actual video URLs
    GridLayout gridLayout;
    if (!compositor.layoutJson.empty())
    {
        try
        {
            gridLayout = NvCompositor::parseJsonLayout(compositor.layoutJson);
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Failed to parse custom grid layout, falling back to default. Error: " << e.what() << endl;
            gridLayout = createDefaultGridLayoutWithUrls(compositor.urls, config.isGodsEyeView());
        }
    }
    else
    {
        gridLayout = createDefaultGridLayoutWithUrls(compositor.urls, config.isGodsEyeView());
    }
    
    // Create compositor with grid layout containing video URLs
    m_compositor = std::make_shared<NvCompositor>(gridLayout);
    m_compositor->setFrameRate(config.getQuality().frameRate);
    LOG(info) << "   ⚙️  Frame rate set to: " << config.getQuality().frameRate << endl;
    LOG(info) << "   🎯 Using grid layout: " << gridLayout.columns << "x" << gridLayout.rows 
              << " with " << gridLayout.tiles.size() << " tiles" << endl;
    
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true) {
        m_compositor->setConsumer(m_transform);
        m_transform->setConsumer(m_encoder);
        m_encoder->setConsumer(m_webrtcConsumer);
        LOG(info) << "   🔗 [Compositor] → [Transform] → [HW Encoder] → [WebRTC Consumer]" << endl;
        LOG(info) << "✅ Compositor Pipeline: Using Hardware Encoding" << endl;
    } else {
        m_compositor->setConsumer(m_transformSink);
        m_transformSink->setConsumer(m_webrtcConsumer);
        LOG(info) << "   🔗 [Compositor] → [TransformSink] → [WebRTC Consumer]" << endl;
        LOG(info) << "✅ Compositor Pipeline: Using Software Encoding" << endl;
    }
    
    LOG(info) << "==========================================" << endl;
}

void CompositePipelineBuilder::setupCompositorConsumers(const PipelineConfiguration& config)
{
    LOG(info) << "==========================================" << endl;
    LOG(info) << "SETTING UP COMPOSITE PIPELINE" << endl;
    LOG(info) << "==========================================" << endl;
    LOG(info) << "Peer ID: " << config.getPeerId() << endl;
    LOG(info) << "Number of Streams: " << config.getCompositor().urls.size() << endl;
    LOG(info) << "Show Sensor Names: " << (config.getCompositor().showSensorName ? "YES" : "NO") << endl;
    LOG(info) << "Image Capture: " << (config.isImageCapture() ? "YES" : "NO") << endl;
    LOG(info) << "Playback Type: " << (config.isLivePlayback() ? "LIVE" : "RECORDED") << endl;
    LOG(info) << "==========================================" << endl;
    
    // Handle image capture pipeline for composite
    if (config.isImageCapture()) {
        LOG(info) << "🎯 COMPOSITE IMAGE CAPTURE PIPELINE SETUP" << endl;
        LOG(info) << "==========================================" << endl;
        
        // Validate that we have the required components for image capture
        if (!m_imageEncoder) {
            LOG(error) << "❌ Image encoder not initialized for composite image capture" << endl;
            return;
        }
        
        if (m_decoders.empty()) {
            LOG(error) << "❌ No decoders available for composite image capture" << endl;
            return;
        }
        
        if (!m_decoders[0]) {
            LOG(error) << "❌ First decoder is null for composite image capture" << endl;
            return;
        }

        // For composite image capture, we'll use the first decoder and connect it to image encoder
        if (m_transform) {
            m_decoders[0]->setConsumer(config.getPeerId(), m_transform);
            m_transform->setConsumer(m_imageEncoder);
            LOG(info) << "✅ Pipeline: [First Decoder] → [Transform] → [ImageEncoder] → [JPEG Output]" << endl;
            LOG(info) << "   📸 Composite image will be captured from first stream and returned as JPEG buffer" << endl;
        } else {
            // Fallback: First Decoder -> ImageEncoder (direct connection)
            m_decoders[0]->setConsumer(config.getPeerId(), m_imageEncoder);
            LOG(info) << "✅ Pipeline: [First Decoder] → [ImageEncoder] → [JPEG Output]" << endl;
            LOG(info) << "   📸 Composite image will be captured from first stream and returned as JPEG buffer" << endl;
        }

        const auto& opts = config.getOptions();
        int resizeWidth = 0, resizeHeight = 0;
        if (opts.find("resize_width") != opts.end() && opts.find("resize_height") != opts.end())
        {
            resizeWidth = stringToInt(opts.at("resize_width"), 0);
            resizeHeight = stringToInt(opts.at("resize_height"), 0);
        }
        // Set target resolution in decoder if transform is present and resize dimensions provided
        if (m_transform && resizeWidth > 0 && resizeHeight > 0)
        {
            m_decoders[0]->setQuality(config.getPeerId(), "custom", resizeWidth, resizeHeight);
            LOG(info) << "   ⚙️  Quality set to: custom, " << resizeWidth << "x" << resizeHeight << endl;
        }
        else
        {
            m_decoders[0]->setQuality(config.getPeerId(), config.getQuality().quality);
            LOG(info) << "   ⚙️  Quality set to: " << config.getQuality().quality << endl;
        }

        LOG(info) << "==========================================" << endl;
        return; // Exit early for image capture
    }
    
    // Regular composite pipeline setup (non-image capture)
    LOG(info) << "🎬 COMPOSITE VIDEO PIPELINE SETUP" << endl;
    LOG(info) << "==========================================" << endl;
    
    const auto& compositor = config.getCompositor();
    LOG(info) << "🔗 Connecting " << m_decoders.size() << " decoders to compositor" << endl;
    
    for (size_t i = 0; i < m_decoders.size(); i++) {
        auto decoder = m_decoders[i];
        if (decoder) {
            if (compositor.showSensorName && i < m_overlays.size() && m_overlays[i]) {
                decoder->setConsumer(config.getPeerId(), m_overlays[i]);
                decoder->setQuality(config.getPeerId(), config.getQuality().quality);
                m_overlays[i]->setConsumer(m_compositor);
                LOG(info) << "   🔗 Stream " << (i+1) << ": [Decoder] → [Overlay] → [Compositor]" << endl;
            } else {
                decoder->setConsumer(config.getPeerId(), m_compositor);
                decoder->setQuality(config.getPeerId(), config.getQuality().quality);
                LOG(info) << "   🔗 Stream " << (i+1) << ": [Decoder] → [Compositor]" << endl;
            }
        } else {
            LOG(warning) << "   ⚠️  Stream " << (i+1) << ": Decoder is null" << endl;
        }
    }
    
    // Show the complete composite pipeline
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true) {
        LOG(info) << "✅ Complete Composite Pipeline: [Multiple Decoders] → [Overlays] → [Compositor] → [Transform] → [HW Encoder] → [WebRTC]" << endl;
    } else {
        LOG(info) << "✅ Complete Composite Pipeline: [Multiple Decoders] → [Overlays] → [Compositor] → [TransformSink] → [WebRTC]" << endl;
    }
    
    LOG(info) << "==========================================" << endl;
    LOG(info) << "🎉 COMPOSITE PIPELINE SETUP COMPLETE" << endl;
    LOG(info) << "==========================================" << endl;
}

GridLayout CompositePipelineBuilder::createDefaultGridLayoutWithUrls(const std::vector<std::string>& urls, bool isGodsEyeView) const
{
    size_t streamCount = urls.size();
    LOG(info) << "Creating grid layout for " << streamCount << " streams with actual URLs" << endl;

    GridLayout gridLayout;
    gridLayout.isCustom = true;

    // Special handling for gods eye view layout
    if (isGodsEyeView && streamCount > 1)
    {
        gridLayout.tileSpacing.horizontal = 0;
        gridLayout.tileSpacing.vertical = 0;
        LOG(info) << "Creating default gods eye view layout" << endl;

        gridLayout.columns = 100;
        gridLayout.rows = 100;

        // The last URL is the gods eye view stream (added in buildDecoderPipelines)
        size_t godsEyeIndex = streamCount - 1;
        size_t cornerStreamCount = streamCount - 1; // Exclude gods eye view

        // First, add the gods eye view as a full-screen background layer
        GridTile godsEyeTile;
        godsEyeTile.id = "gods_eye_tile";
        godsEyeTile.userId = "gods_eye_user";
        godsEyeTile.row = 0;
        godsEyeTile.column = 0;
        godsEyeTile.width = 100;   // Full width
        godsEyeTile.height = 100;  // Full height
        godsEyeTile.videoUrl = urls[godsEyeIndex];
        gridLayout.tiles.push_back(godsEyeTile);

        // Now add corner and edge streams as smaller overlays (up to 9)
        if (cornerStreamCount > 0 && cornerStreamCount <= 9)
        {
            const int cornerWidth = 22;   // 22% of screen width
            const int cornerHeight = 22;  // 22% of screen height
            const int margin = 0;         // No margin from edges

            // Define positions: 4 corners + 4 edge centers + 1 center
            const int streamPositions[9][2] = {
                {margin, margin},                                        // Stream 1: Top-left corner
                {margin, 100 - cornerWidth - margin},                    // Stream 2: Top-right corner
                {100 - cornerHeight - margin, margin},                   // Stream 3: Bottom-left corner
                {100 - cornerHeight - margin, 100 - cornerWidth - margin},  // Stream 4: Bottom-right corner
                {50 - cornerHeight/2, margin},                           // Stream 5: Left edge center
                {50 - cornerHeight/2, 100 - cornerWidth - margin},       // Stream 6: Right edge center
                {margin, 50 - cornerWidth/2},                            // Stream 7: Top edge center
                {100 - cornerHeight - margin, 50 - cornerWidth/2},       // Stream 8: Bottom edge center
                {50 - cornerHeight/2, 50 - cornerWidth/2}                // Stream 9: Screen center
            };

            for (size_t i = 0; i < cornerStreamCount && i < 9; i++)
            {
                GridTile overlayTile;
                overlayTile.id = "overlay_tile_" + std::to_string(i);
                overlayTile.userId = "overlay_user_" + std::to_string(i);
                overlayTile.row = streamPositions[i][0];
                overlayTile.column = streamPositions[i][1];
                overlayTile.width = cornerWidth;
                overlayTile.height = cornerHeight;
                overlayTile.videoUrl = urls[i];
                gridLayout.tiles.push_back(overlayTile);
            }
        }
    }
    else
    {
        // Standard grid layout with spacing
        gridLayout.tileSpacing.horizontal = 0;
        gridLayout.tileSpacing.vertical = 1;

        // Determine grid dimensions based on stream count
        if (streamCount == 1) {
            gridLayout.columns = 1;
            gridLayout.rows = 1;
        } else if (streamCount == 2) {
            gridLayout.columns = 2;
            gridLayout.rows = 1;
        } else if (streamCount <= 4) {
            gridLayout.columns = 2;
            gridLayout.rows = 2;
        } else if (streamCount <= 6) {
            gridLayout.columns = 3;
            gridLayout.rows = 2;
        } else if (streamCount <= 9) {
            gridLayout.columns = 3;
            gridLayout.rows = 3;
        } else if (streamCount <= 12) {
            gridLayout.columns = 4;
            gridLayout.rows = 3;
        } else {
            gridLayout.columns = 4;
            gridLayout.rows = 4;
        }

        // Create tiles for the grid with actual video URLs
        for (size_t i = 0; i < streamCount && i < static_cast<size_t>(gridLayout.columns * gridLayout.rows); i++) {
            int row = static_cast<int>(i / gridLayout.columns);
            int col = static_cast<int>(i % gridLayout.columns);

            GridTile tile;
            tile.id = "stream_tile_" + std::to_string(i);
            tile.userId = "stream_user_" + std::to_string(i);
            tile.row = row;
            tile.column = col;
            tile.width = 1;
            tile.height = 1;
            tile.videoUrl = urls[i];  // Use actual video URL from the input

            gridLayout.tiles.push_back(tile);
        }
    }

    // Calculate tile positions
    gridLayout.calculateTilePositions();
    
    LOG(info) << "   ✅ Grid layout created: " << gridLayout.columns << "x" << gridLayout.rows 
              << " with " << gridLayout.tiles.size() << " tiles using actual video URLs" << endl;
    
    return gridLayout;
}

void CompositePipelineBuilder::destroyPipeline()
{
    LOG(info) << "Destroying composite pipeline" << endl;
    
    try {
        // Phase 1: Stop data flow by removing consumers
        LOG(info) << "Phase 1: Removing consumers from decoders" << endl;
        for (auto& decoder : m_decoders) {
            if (decoder) {
                try {
                    decoder->removeConsumer(m_config.getPeerId());
                    LOG(info) << "Removed consumer from decoder" << endl;
                } catch (const std::exception& e) {
                    LOG(error) << "Exception while removing consumer: " << e.what() << endl;
                }
            }
        }
        
        // Phase 2: Clear producers from all decoders
        LOG(info) << "Phase 2: Clearing producers from decoders" << endl;
        for (auto& decoder : m_decoders) {
            if (decoder) {
                try {
                    clearDecoderProducer(decoder, m_config);
                } catch (const std::exception& e) {
                    LOG(error) << "Exception while clearing producer: " << e.what() << endl;
                }
            }
        }
        
        // Phase 3: Remove decoders from pool (for live streaming)
        if (!m_config.isRecordedPlayback()) {
            LOG(info) << "Phase 3: Removing decoders from pool" << endl;
            DecoderPool* pool = DecoderPool::getInstance();
            for (size_t i = 0; i < m_decoders.size() && i < m_decoderUris.size(); i++) {
                if (m_decoders[i]) {
                    try {
                        pool->removeStream(m_decoderUris[i]);
                        LOG(info) << "Removed composite decoder from pool for URI: " << m_decoderUris[i] << endl;
                    } catch (const std::exception& e) {
                        LOG(error) << "Exception while removing from pool: " << e.what() << endl;
                    }
                }
            }
        }
        
        // Phase 4: Clear references in reverse dependency order
        LOG(info) << "Phase 4: Clearing component references" << endl;
        
        // First clear compositor to break consumer chain
        m_compositor.reset();
        
        // Then stop and clear overlays
        for (auto& overlay : m_overlays) {
            if (overlay) {
                overlay->stopOverlay();
                LOG(info) << "Stopped overlay before destruction" << endl;
                
                // Give the overlay thread time to finish
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
        }
        m_overlays.clear();
        
        // Finally clear decoders and URIs
        m_decoders.clear();
        m_decoderUris.clear();
        
        // Phase 5: Destroy common components (encoder, transform, etc.)
        LOG(info) << "Phase 5: Destroying common components" << endl;
        destroyCommonComponents();
        
    } catch (const std::exception& e) {
        LOG(error) << "Exception during composite pipeline destruction: " << e.what() << endl;
    } catch (...) {
        LOG(error) << "Unknown exception during composite pipeline destruction" << endl;
    }
    
    LOG(info) << "Composite pipeline destruction completed" << endl;
}

void CompositePipelineBuilder::startPipeline()
{
    // Set up consumer pipeline first
    setupCompositorConsumers(m_config);
}

void CompositePipelineBuilder::stopPipeline()
{
    LOG(info) << "Stopping composite pipeline" << endl;
    
    // Stop all decoders first
    for (auto& decoder : m_decoders) {
        if (decoder) {
            decoder->removeConsumer(m_config.getPeerId());
            LOG(info) << "Removed consumer from decoder in stop" << endl;
        }
    }
    
    // Give some time for data flow to stop
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    
    LOG(info) << "Composite pipeline stopped" << endl;
}
