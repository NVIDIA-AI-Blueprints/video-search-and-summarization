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

#include "SingleStreamPipelineBuilder.h"
#include "../decoders/gstnvvideodecoder.h"
#include "../decoders/decoderpool.h"
#include "../encoders/nvvideoencoder.h"
#include "../encoders/image_encoder.h"
#include "../../overlays/ll_overlay.h"
#include "../processors/transforms/ll_transform.h"
#include "../processors/compositors/nvcompositor.h"
#include "../senders/videosenderpool.h"
#include "../senders/webrtc_sink_consumer.h"
#include "../senders/videowebRTCsender.h"
#include "../producers/native_stream_monitor.h"
#include "../producers/nativestreamproducer.h"
#ifdef JETSON_PLATFORM
#include "../producers/gstnvipcproducer.h"
#include "../producers/ipcproducerpool.h"
#endif
#include "logger.h"
#include "config.h"
#include "nvhwdetection.h"
#include "utils.h"
#include "osd/llosd.h"



// Implementation of SingleStreamPipelineBuilder
void SingleStreamPipelineBuilder::buildPipeline(const PipelineConfiguration& config)
{
    m_config = config;
    
    // Create common components first
    createCommonComponents(config);
    
    if (config.isPassThrough()) {
        buildPassThroughPipeline(config);
    } else if (config.isNativeStream()) {
        buildNativeStreamPipeline(config);
    } else if (config.isGodsEyeView()) {
        buildGodsEyeViewPipeline(config);
    } else if (config.getOverlay().enabled && config.getOverlay().bboxEnabled && 
               GET_CONFIG().enable_ipc_path == true)
    {
#ifdef JETSON_PLATFORM
        buildIPCPipeline(config);
#else
        buildStandardPipeline(config);
#endif
    }
    else
    {
        buildStandardPipeline(config);
    }
}

void SingleStreamPipelineBuilder::buildPassThroughPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "Alert: Pass Through Mode Enabled.. !" << endl;
    
    std::string codec = config.getCodec();
    if (!codec.empty() && !iequals(codec, "H264") && !iequals(codec, "H265")) {
        std::string unsupported_error = codec + " codec not supported in pass through mode";
        LOG(error) << unsupported_error << endl;
        throw std::invalid_argument(unsupported_error);
    }
    
    VideoSenderPool* pool = VideoSenderPool::getInstance();
    m_videoSender = pool->getVideoSender(config.getUri());
    if (m_videoSender == nullptr) {
        LOG(info) << "VideoSender not found in pool, adding stream: " << config.getUri() << endl;
        pool->addStream(config.getUri());
        m_videoSender = pool->getVideoSender(config.getUri());
        
        if (m_videoSender == nullptr) {
            LOG(error) << "CRITICAL: Failed to create VideoWebRTCSender for URI: " << config.getUri() << endl;
            throw std::runtime_error("Failed to create VideoWebRTCSender for pass-through mode");
        }
    }
    
    LOG(info) << "VideoSender created successfully for pass-through mode" << endl;
    std::string deviceId = config.getDeviceId();
    m_videoSender->createPassThroughMode(deviceId);
}

void SingleStreamPipelineBuilder::buildNativeStreamPipeline(const PipelineConfiguration& config)
{
    m_nativeStreamProducer = NativeStreamMonitor::getInstance()->getNativeStreamProducer(config.getDeviceId());
    if (m_nativeStreamProducer == nullptr) {
        LOG(error) << "Native stream producer is not found for deviceId:" << config.getDeviceId() << endl;
        throw std::invalid_argument("NativeStreamProducer instance not found error");
    }
    m_nativeStreamProducer->setOptions(config.getOptions());
}

void SingleStreamPipelineBuilder::buildGodsEyeViewPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "Creating decoder for file: " << config.getUri() << endl;
    string consumer_name = "video_decoder_gods_eye_" + config.getPeerId();
    m_decoder = std::make_shared<GstNvVideoDecoder>(consumer_name, config.getUri(), config.getOptions());
    m_decoder->create(true);
    
    // Set up producer for live playback (including live image capture)
    if (config.isLivePlayback()) {
        setupDecoderWithProducer(m_decoder, config.getUri(), config);
    }
}

#ifdef JETSON_PLATFORM
void SingleStreamPipelineBuilder::buildIPCPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "Creating IPC Producer object" << endl;
    string stream_id = getStreamIdFromUrl(config.getUri(), "/live/");
    IPCProducerPool* pool = IPCProducerPool::getInstance();
    m_ipcProducer = pool->getIPCProducer(stream_id);
    if (m_ipcProducer == nullptr) {
        pool->addStream(stream_id);
        m_ipcProducer = pool->getIPCProducer(stream_id);
        m_ipcProducer->setOptions(config.getOptions());
        m_ipcProducer->create();
    }
}
#endif

void SingleStreamPipelineBuilder::buildStandardPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "Building standard pipeline" << endl;
    
    // Check if we need a new decoder instance (recorded playback, image capture, or new_dec option)
    bool needNewDecoder = config.isRecordedPlayback() || config.isImageCapture();
    const auto& opts = config.getOptions();
    if (opts.find("new_dec") != opts.end() && opts.at("new_dec") == "true") {
        needNewDecoder = true;
        LOG(info) << "new_dec option detected - creating dedicated decoder instance" << endl;
    }
    
    if (needNewDecoder) {
        LOG(info) << "Creating new decoder instance for recorded/image capture (not using pool)" << endl;
        string consumer_name = "video_decoder_" + config.getPeerId();
        m_decoder = std::make_shared<GstNvVideoDecoder>(consumer_name, config.getUri(), config.getOptions());
        if (m_decoder->create(true) == -1) {
            LOG(error) << "Error in Creating Pipeline" << endl;
            throw std::invalid_argument("Error in Creating Pipeline");
        }
        LOG(info) << "New decoder instance created successfully" << endl;
        
        // Set up producer for live playback (including live image capture)
        if (config.isLivePlayback() || config.isCloudStream() || config.isImageCapture() || config.getSensorType() == SENSOR_TYPE_MMS_ONVIF) {
            setupDecoderWithProducer(m_decoder, config.getUri(), config);
        }
        
        // For live image capture, we still need shared stream capability
        if (config.isLivePlayback() && config.isImageCapture() && !config.isHlsPlayback()) {
            m_decoder->setNeedSharedStream();
            LOG(info) << "Set shared stream for live image capture" << endl;
        }
        else if (config.getSensorType() == SENSOR_TYPE_MMS_ONVIF) {
            m_decoder->setNeedSharedStream();
        }
    } else {
        LOG(info) << "Using decoder pool for live streaming" << endl;
        DecoderPool* pool = DecoderPool::getInstance();
        m_decoder = pool->getDecoder(config.getUri());
        if (m_decoder == nullptr) {
            LOG(warning) << "Decoder is not found so create new decoder instance..." << endl;
            pool->addStream(config.getUri(), config.getOptions());
            m_decoder = pool->getDecoder(config.getUri());
        }
        
        if (m_decoder) {
            m_decoder->setOptions(config.getOptions());
            
            // IMediaDataProducer integration for single decoder case
            if (config.isLivePlayback()) {
                setupDecoderWithProducer(m_decoder, config.getUri(), config);
            }
            
            if (!config.isHlsPlayback()) {
                m_decoder->setNeedSharedStream();
            }
            
            dec_result result = pool->tryDecoderStart(m_decoder, config.getUri());
            if (!result.first) {
                LOG(error) << "Error in Creating Pipeline" << endl;
                pool->removeStream(config.getUri());
                throw std::invalid_argument("Error in Creating Pipeline");
            }
        }
    }
}

void SingleStreamPipelineBuilder::destroyPipeline()
{
    LOG(info) << "Destroying single stream pipeline" << endl;
    
    try {
        // Phase 1: Stop data flow by removing consumers
        if (m_decoder) {
            try {
                m_decoder->removeConsumer(m_config.getPeerId());
                LOG(info) << "Removed consumer from decoder" << endl;
            } catch (const std::exception& e) {
                LOG(error) << "Exception while removing consumer: " << e.what() << endl;
            }
        }

        // Clear producer from decoder
        if (m_decoder && (m_config.isCloudStream() || m_config.isImageCapture()) && m_decoder->getProducer()) {
            try {
                clearDecoderProducer(m_decoder, m_config);
                LOG(info) << "Cleared producer from decoder" << endl;
            } catch (const std::exception& e) {
                LOG(error) << "Exception while clearing producer: " << e.what() << endl;
            }
        }
        
        // Phase 2: Handle decoder cleanup based on how it was created
        bool wasNewDecoder = m_config.isRecordedPlayback() || m_config.isImageCapture();
        const auto& opts = m_config.getOptions();
        if (opts.find("new_dec") != opts.end() && opts.at("new_dec") == "true") {
            wasNewDecoder = true;
        }
        
        // Phase 3: CRITICAL - Must explicitly call destroy() to break circular dependency
        // ALL decoders (pooled or new) that use StreamMonitor need explicit destroy() call.
        // The decoder is registered with StreamMonitor which holds a shared_ptr reference.
        // Decoder's destructor calls destroy() which unregisters from StreamMonitor.
        // BUT destructor won't run while StreamMonitor holds the reference!
        // SOLUTION: Call destroy() explicitly BEFORE final cleanup to break the cycle.
        // IMPORTANT: Do NOT clear producer before destroy() - destroy_internal() needs it to unregister from StreamMonitor!
        if (m_decoder) {
            try {
                m_decoder->destroy(true);
            } catch (const std::exception& e) {
                LOG(error) << "Exception while destroying decoder: " << e.what() << endl;
            }
        }

        // Phase 4: Remove from pool if this was a pooled decoder
        if (!wasNewDecoder) {
            // Remove from pool - this releases the pool's shared_ptr reference
            DecoderPool* pool = DecoderPool::getInstance();
            if (m_decoder) {
                try {
                    pool->removeStream(m_config.getUri());
                    LOG(info) << "Removed decoder from pool for URI: " << m_config.getUri() << endl;
                } catch (const std::exception& e) {
                    LOG(error) << "Exception while removing from pool: " << e.what() << endl;
                }
            }
        }
        
        // Phase 5: Clear component references
        // This releases the pipeline's shared_ptr reference to the decoder
        // Combined with destroy() above (which unregistered from StreamMonitor),
        // pool cleanup (if applicable), and CommonVideoSource cleanup (from RemoveSink),
        // the ref count should reach 0, triggering the destructor
        m_decoder.reset();
        m_videoSender.reset();
        m_nativeStreamProducer.reset();
#ifdef JETSON_PLATFORM
        m_ipcProducer.reset();
#endif
        
        // Phase 6: Destroy common components (encoder, transform, etc.)
        destroyCommonComponents();
        
    } catch (const std::exception& e) {
        LOG(error) << "Exception during single stream pipeline destruction: " << e.what() << endl;
    } catch (...) {
        LOG(error) << "Unknown exception during single stream pipeline destruction" << endl;
    }
    
    LOG(info) << "Single stream pipeline destruction completed" << endl;
}

void SingleStreamPipelineBuilder::setupConsumerPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "==========================================" << endl;
    LOG(info) << "SETTING UP SINGLE STREAM PIPELINE" << endl;
    LOG(info) << "==========================================" << endl;
    LOG(info) << "Peer ID: " << config.getPeerId() << endl;
    LOG(info) << "URI: " << config.getUri() << endl;
    LOG(info) << "Playback Type: " << (config.isLivePlayback() ? "LIVE" : "RECORDED") << endl;
    LOG(info) << "Image Capture: " << (config.isImageCapture() ? "YES" : "NO") << endl;
    LOG(info) << "Pass Through: " << (config.isPassThrough() ? "YES" : "NO") << endl;
    LOG(info) << "Native Stream: " << (config.isNativeStream() ? "YES" : "NO") << endl;
    
    // Log overlay configuration details
    LOG(info) << "OVERLAY CONFIGURATION:" << endl;
    LOG(info) << "  Overlay Enabled: " << (config.getOverlay().enabled ? "YES" : "NO") << endl;
    LOG(info) << "  Bbox Enabled: " << (config.getOverlay().bboxEnabled ? "YES" : "NO") << endl;
    LOG(info) << "  Sensor ID: " << config.getOverlay().sensorId << endl;
    LOG(info) << "  Tag: " << config.getOverlay().tag << endl;
    
    // Log specific overlay options from the raw options map
    const auto& opts = config.getOptions();
    LOG(info) << "RAW OVERLAY OPTIONS:" << endl;
    if (opts.find("overlay") != opts.end()) {
        LOG(info) << "  overlay = " << opts.at("overlay") << endl;
    }
    if (opts.find("overlayBbox") != opts.end()) {
        LOG(info) << "  overlayBbox = " << opts.at("overlayBbox") << endl;
    }
    if (opts.find("overlayDebug") != opts.end()) {
        LOG(info) << "  overlayDebug = " << opts.at("overlayDebug") << endl;
    }
    if (opts.find("tripwire") != opts.end()) {
        LOG(info) << "  tripwire = " << opts.at("tripwire") << endl;
    }
    if (opts.find("roi") != opts.end()) {
        LOG(info) << "  roi = " << opts.at("roi") << endl;
    }
    if (opts.find("bboxDebug") != opts.end()) {
        LOG(info) << "  bboxDebug = " << opts.at("bboxDebug") << endl;
    }
    if (opts.find("overlayShowSensorName") != opts.end()) {
        LOG(info) << "  overlayShowSensorName = " << opts.at("overlayShowSensorName") << endl;
    }
    
    // Log overlay component status
    LOG(info) << "OVERLAY COMPONENT STATUS:" << endl;
    LOG(info) << "  m_overlay created: " << (m_overlay ? "YES" : "NO") << endl;
    if (m_overlay) {
        LOG(info) << "  m_overlay->isOverlayEnabled(): " << (m_overlay->isOverlayEnabled() ? "YES" : "NO") << endl;
        LOG(info) << "  m_overlay->isBboxEnabled(): " << (m_overlay->isBboxEnabled() ? "YES" : "NO") << endl;
    }
    LOG(info) << "  GET_OSD_INSTANCE()->isError(): " << (GET_OSD_INSTANCE()->isError() ? "YES" : "NO") << endl;
    
    // Log transform component status
    LOG(info) << "TRANSFORM COMPONENT STATUS:" << endl;
    LOG(info) << "  m_transform created: " << (m_transform ? "YES" : "NO") << endl;
    LOG(info) << "  m_transformSink created: " << (m_transformSink ? "YES" : "NO") << endl;
    LOG(info) << "  m_imageEncoder created: " << (m_imageEncoder ? "YES" : "NO") << endl;
    LOG(info) << "==========================================" << endl;
    
    // Handle image capture pipeline
    if (config.isImageCapture()) {
        LOG(info) << "🎯 IMAGE CAPTURE PIPELINE SETUP" << endl;
        LOG(info) << "==========================================" << endl;
        
        // Validate that we have the required components for image capture
        if (!m_imageEncoder) {
            LOG(error) << "❌ Image encoder not initialized for image capture" << endl;
            return;
        }

        if (m_decoder) {
            // Check if overlay is enabled for image capture
            if (m_overlay && !GET_OSD_INSTANCE()->isError() && m_overlay->isOverlayEnabled()) {
                LOG(info) << "🎨 IMAGE CAPTURE WITH OVERLAY PIPELINE" << endl;
                
                if (m_transform && m_transformSink) {
                    // CORRECT: Decoder -> Transform -> Overlay -> TransformSink -> ImageEncoder
                    m_decoder->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_overlay);
                    m_overlay->setConsumer(m_transformSink);
                    m_transformSink->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [Decoder] → [Transform] → [Overlay] → [TransformSink] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Image with overlay will be captured and returned as JPEG buffer" << endl;
                } else if (m_transform) {
                    // Fallback without transformSink: Decoder -> Transform -> Overlay -> ImageEncoder
                    m_decoder->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_overlay);
                    m_overlay->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [Decoder] → [Transform] → [Overlay] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   ⚠️  Missing TransformSink - may affect image quality" << endl;
                } else {
                    // Fallback: Decoder -> Overlay -> ImageEncoder (direct connection)
                    m_decoder->setConsumer(config.getPeerId(), m_overlay);
                    m_overlay->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [Decoder] → [Overlay] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   ⚠️  Missing Transform components - may affect image quality" << endl;
                }
            } else {
                LOG(info) << "📸 IMAGE CAPTURE WITHOUT OVERLAY PIPELINE" << endl;
                
                // For image capture without overlay: Decoder -> Transform -> ImageEncoder
                if (m_transform) {
                    m_decoder->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [Decoder] → [Transform] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Image will be captured and returned as JPEG buffer" << endl;
                } else {
                    // Fallback: Decoder -> ImageEncoder (direct connection)
                    m_decoder->setConsumer(config.getPeerId(), m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [Decoder] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Image will be captured and returned as JPEG buffer" << endl;
                }
            }
        } else if (m_nativeStreamProducer) {
            // Check if overlay is enabled for native stream image capture
            if (m_overlay && !GET_OSD_INSTANCE()->isError() && m_overlay->isOverlayEnabled()) {
                LOG(info) << "🎨 NATIVE STREAM IMAGE CAPTURE WITH OVERLAY PIPELINE" << endl;
                
                if (m_transform && m_transformSink) {
                    // CORRECT: NativeStream -> Transform -> Overlay -> TransformSink -> ImageEncoder
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_overlay);
                    m_overlay->setConsumer(m_transformSink);
                    m_transformSink->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [NativeStreamProducer] → [Transform] → [Overlay] → [TransformSink] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Native stream image with overlay will be captured and returned as JPEG buffer" << endl;
                } else if (m_transform) {
                    // Fallback without transformSink: NativeStream -> Transform -> Overlay -> ImageEncoder
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_overlay);
                    m_overlay->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [NativeStreamProducer] → [Transform] → [Overlay] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   ⚠️  Missing TransformSink - may affect image quality" << endl;
                } else {
                    // Fallback: NativeStream -> Overlay -> ImageEncoder (direct connection)
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_overlay);
                    m_overlay->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [NativeStreamProducer] → [Overlay] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   ⚠️  Missing Transform components - may affect image quality" << endl;
                }
            } else {
                LOG(info) << "📸 NATIVE STREAM IMAGE CAPTURE WITHOUT OVERLAY PIPELINE" << endl;
                
                // For native stream image capture without overlay: NativeStream -> Transform -> ImageEncoder
                if (m_transform) {
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [NativeStreamProducer] → [Transform] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Native stream image will be captured and returned as JPEG buffer" << endl;
                } else {
                    // Fallback: NativeStream -> ImageEncoder (direct connection)
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_imageEncoder);
                    LOG(info) << "✅ Pipeline: [NativeStreamProducer] → [ImageEncoder] → [JPEG Output]" << endl;
                    LOG(info) << "   📸 Native stream image will be captured and returned as JPEG buffer" << endl;
                }
            }
        } else {
            LOG(error) << "❌ No decoder or native stream producer available for image capture" << endl;
            return;
        }

        // Set quality for the decoder if available
        if (m_decoder) {
            int resizeWidth = 0, resizeHeight = 0;
            if (opts.find("resize_width") != opts.end() && opts.find("resize_height") != opts.end())
            {
                resizeWidth = stringToInt(opts.at("resize_width"), 0);
                resizeHeight = stringToInt(opts.at("resize_height"), 0);
            }
            // Set target resolution in decoder if transform is present and resize dimensions provided
            if (m_transform && resizeWidth > 0 && resizeHeight > 0)
            {
                m_decoder->setQuality(config.getPeerId(), "custom", resizeWidth, resizeHeight);
                LOG(info) << "   ⚙️  Quality set to: custom, " << resizeWidth << "x" << resizeHeight << endl;
            }
            else
            {
                m_decoder->setQuality(config.getPeerId(), config.getQuality().quality);
                LOG(info) << "   ⚙️  Quality set to: " << config.getQuality().quality << endl;
            }
        }

        LOG(info) << "==========================================" << endl;
        return; // Exit early for image capture
    }
    
    // Regular pipeline setup (non-image capture)
    LOG(info) << "🎬 REGULAR VIDEO PIPELINE SETUP" << endl;
    LOG(info) << "==========================================" << endl;
    
    if (m_decoder) {
        LOG(info) << "📹 Using GstNvVideoDecoder as source" << endl;
        
        if (m_overlay && !GET_OSD_INSTANCE()->isError() && m_overlay->isOverlayEnabled()) {
            LOG(info) << "🎨 OVERLAY PIPELINE (with OSD enabled)" << endl;
            
            // Decoder -> Transform -> Overlay -> Encoder -> WebRTC
            if (m_transform) {
                m_decoder->setConsumer(config.getPeerId(), m_transform);
                m_transform->setConsumer(m_overlay);
                LOG(info) << "   🔗 [Decoder] → [Transform] → [Overlay]" << endl;
            } else {
                m_decoder->setConsumer(config.getPeerId(), m_overlay);
                LOG(info) << "   🔗 [Decoder] → [Overlay]" << endl;
            }
            
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true) {
                if (m_encoder && m_webrtcConsumer) {
                    m_overlay->setConsumer(m_encoder);
                    m_encoder->setConsumer(m_webrtcConsumer);
                    LOG(info) << "   🔗 [Overlay] → [HW Encoder] → [WebRTC Consumer]" << endl;
                    LOG(info) << "✅ Complete Pipeline: [Decoder] → [Transform] → [Overlay] → [HW Encoder] → [WebRTC]" << endl;
                }
            } else {
                if (m_transformSink && m_webrtcConsumer) {
                    m_overlay->setConsumer(m_transformSink);
                    m_transformSink->setConsumer(m_webrtcConsumer);
                    LOG(info) << "   🔗 [Overlay] → [TransformSink] → [WebRTC Consumer]" << endl;
                    LOG(info) << "✅ Complete Pipeline: [Decoder] → [Transform] → [Overlay] → [TransformSink] → [WebRTC]" << endl;
                }
            }
        } else {
            LOG(info) << "🎬 STANDARD PIPELINE (no overlay)" << endl;
            
            // Decoder -> Transform -> Encoder -> WebRTC (no overlay)
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true) {
                if (m_transform && m_encoder && m_webrtcConsumer) {
                    m_decoder->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_encoder);
                    m_encoder->setConsumer(m_webrtcConsumer);
                    LOG(info) << "✅ Complete Pipeline: [Decoder] → [Transform] → [HW Encoder] → [WebRTC]" << endl;
                }
            } else {
                if (m_transformSink && m_webrtcConsumer) {
                    m_decoder->setConsumer(config.getPeerId(), m_transformSink);
                    m_transformSink->setConsumer(m_webrtcConsumer);
                    LOG(info) << "✅ Complete Pipeline: [Decoder] → [TransformSink] → [WebRTC]" << endl;
                }
            }
        }
    } else if (m_nativeStreamProducer) {
        LOG(info) << "🌐 Using NativeStreamProducer as source" << endl;
        
        if (m_overlay && !GET_OSD_INSTANCE()->isError() && m_overlay->isOverlayEnabled()) {
            LOG(info) << "🎨 NATIVE STREAM OVERLAY PIPELINE" << endl;
            if (m_transform) {
                m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transform);
                m_transform->setConsumer(m_overlay);
                LOG(info) << "✅ Complete Pipeline: [NativeStream] → [Transform] → [Overlay]" << endl;
            }
        } else {
            LOG(info) << "🎬 NATIVE STREAM STANDARD PIPELINE" << endl;
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true) {
                if (m_transform && m_encoder) {
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transform);
                    m_transform->setConsumer(m_encoder);
                    LOG(info) << "✅ Complete Pipeline: [NativeStream] → [Transform] → [HW Encoder]" << endl;
                }
            } else {
                if (m_transformSink) {
                    m_nativeStreamProducer->setConsumer(config.getPeerId(), m_transformSink);
                    LOG(info) << "✅ Complete Pipeline: [NativeStream] → [TransformSink]" << endl;
                }
            }
        }
    }
#ifdef JETSON_PLATFORM
    else if (m_ipcProducer) {
        LOG(info) << "🔗 Using IPC Producer as source" << endl;
        LOG(info) << "🎨 IPC OVERLAY PIPELINE (with bbox enabled)" << endl;
        
        if (m_overlay && !GET_OSD_INSTANCE()->isError() && m_overlay->isBboxEnabled()) {
            if (m_transform) {
                m_ipcProducer->setConsumer(config.getPeerId(), m_transform);
                m_transform->setConsumer(m_overlay);
                LOG(info) << "✅ Complete Pipeline: [IPC Producer] → [Transform] → [Overlay]" << endl;
            }
        }
    }
#endif
    
    // Set original frame sizes for all components
    LOG(info) << "⚙️  CONFIGURING COMPONENT FRAME SIZES" << endl;
    LOG(info) << "==========================================" << endl;
    
    if (m_overlay) {
        m_overlay->setOriginalFrameSize();
        LOG(info) << "   📐 Overlay frame size configured" << endl;
    }
    if (m_transform) {
        m_transform->setOriginalFrameSize();
        LOG(info) << "   📐 Transform frame size configured" << endl;
    }
    if (m_transformSink) {
        m_transformSink->setOriginalFrameSize();
        LOG(info) << "   📐 TransformSink frame size configured" << endl;
    }
    
    LOG(info) << "==========================================" << endl;
    LOG(info) << "🎉 SINGLE STREAM PIPELINE SETUP COMPLETE" << endl;
    LOG(info) << "==========================================" << endl;
}

void SingleStreamPipelineBuilder::startPipeline()
{
    // Set up consumer pipeline first
    setupConsumerPipeline(m_config);
}

void SingleStreamPipelineBuilder::stopPipeline()
{
    if (m_decoder) {
        m_decoder->removeConsumer(m_config.getPeerId());
    } else if (m_nativeStreamProducer) {
        m_nativeStreamProducer->removeConsumer(m_config.getPeerId());
    }
#ifdef JETSON_PLATFORM
    else if (m_ipcProducer) {
        m_ipcProducer->removeConsumer(m_config.getPeerId());
    }
#endif
}
