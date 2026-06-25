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

#pragma once

#include <memory>
#include <vector>
#include "../core/PipelineConfiguration.h"

// Forward declarations
class GstNvVideoDecoder;
class NvEncoderVideoConsumer;
class WebrtcSinkConsumer;
class NvLLOverlay;
class NvLLTransform;
class ImageEnc;
class NvCompositor;
class VideoWebRTCSender;
class NativeStreamProducer;
class IMediaDataProducer;
#ifdef JETSON_PLATFORM
class NvIPCProducer;
#endif

/**
 * @brief Abstract base class for pipeline builders
 * 
 * This class defines the interface for different types of pipeline builders,
 * allowing for clean separation of pipeline creation logic.
 */
class PipelineBuilder
{
public:
    virtual ~PipelineBuilder() = default;
    
    virtual void buildPipeline(const PipelineConfiguration& config) = 0;
    virtual void destroyPipeline() = 0;
    virtual void startPipeline() = 0;
    virtual void stopPipeline() = 0;
    
    // Common getters for pipeline components
    virtual std::shared_ptr<GstNvVideoDecoder> getDecoder() const = 0;
    virtual std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const = 0;
    virtual std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const = 0;
    virtual std::shared_ptr<NvLLOverlay> getOverlay() const = 0;
    virtual std::shared_ptr<NvLLTransform> getTransform() const = 0;
    virtual std::shared_ptr<NvLLTransform> getTransformSink() const = 0;
    virtual std::shared_ptr<ImageEnc> getImageEncoder() const = 0;
    virtual std::shared_ptr<NvCompositor> getCompositor() const = 0;
    virtual std::shared_ptr<VideoWebRTCSender> getVideoSender() const = 0;
    virtual std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const = 0;
#ifdef JETSON_PLATFORM
    virtual std::shared_ptr<NvIPCProducer> getIPCProducer() const = 0;
#endif

protected:
    void createCommonComponents(const PipelineConfiguration& config);
    void destroyCommonComponents();
    void setupOverlay(const PipelineConfiguration& config);
    void setupTransform(const PipelineConfiguration& config);
    void setupEncoder(const PipelineConfiguration& config);
    void setupWebrtcConsumer(const PipelineConfiguration& config);
    void setupImageEncoder(const PipelineConfiguration& config);
    
    // Source producer utility methods
    std::shared_ptr<IMediaDataProducer> createSourceProducer(const std::string& url, const PipelineConfiguration& config, std::shared_ptr<GstNvVideoDecoder> decoder);
    void setupDecoderWithProducer(std::shared_ptr<GstNvVideoDecoder> decoder, const std::string& url, const PipelineConfiguration& config);
    void clearDecoderProducer(std::shared_ptr<GstNvVideoDecoder> decoder, const PipelineConfiguration& config);

    // Common pipeline components
    std::shared_ptr<NvEncoderVideoConsumer> m_encoder = nullptr;
    std::shared_ptr<NvLLOverlay> m_overlay = nullptr;
    std::shared_ptr<NvLLTransform> m_transform = nullptr;
    std::shared_ptr<NvLLTransform> m_transformSink = nullptr;
    std::shared_ptr<WebrtcSinkConsumer> m_webrtcConsumer = nullptr;
    std::shared_ptr<ImageEnc> m_imageEncoder = nullptr;
};


