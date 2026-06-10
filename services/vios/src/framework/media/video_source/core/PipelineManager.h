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
#include "PipelineConfiguration.h"

// Include the complete PipelineBuilder definition to avoid incomplete type issues
#include "../builders/PipelineBuilder.h"
class GstNvVideoDecoder;
class NvEncoderVideoConsumer;
class WebrtcSinkConsumer;
class NvLLOverlay;
class NvLLTransform;
class ImageEnc;
class NvCompositor;
class VideoWebRTCSender;
class NativeStreamProducer;
#ifdef JETSON_PLATFORM
class NvIPCProducer;
#endif

/**
 * @brief Manager class for pipeline lifecycle
 * 
 * This class manages the creation, configuration, and lifecycle of video pipelines
 * using the builder pattern for clean separation of concerns.
 */
class PipelineManager
{
public:
    PipelineManager();
    ~PipelineManager() = default;

    void createPipeline(const PipelineConfiguration& config);
    void destroyPipeline();
    void startPipeline();
    void stopPipeline();
    void switchPipeline(const PipelineConfiguration& config);
    
    // Pipeline component accessors
    std::shared_ptr<GstNvVideoDecoder> getDecoder() const;
    std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const;
    std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const;
    std::shared_ptr<NvLLOverlay> getOverlay() const;
    std::shared_ptr<NvLLTransform> getTransform() const;
    std::shared_ptr<NvLLTransform> getTransformSink() const;
    std::shared_ptr<ImageEnc> getImageEncoder() const;
    std::shared_ptr<NvCompositor> getCompositor() const;
    std::shared_ptr<VideoWebRTCSender> getVideoSender() const;
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const;
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> getIPCProducer() const;
#endif

    // For composite pipelines
    const std::vector<std::shared_ptr<GstNvVideoDecoder>>& getDecoders() const;
    const std::vector<std::shared_ptr<NvLLOverlay>>& getOverlays() const;

    bool isCompositePipeline() const { return m_isComposite; }

private:
    std::unique_ptr<PipelineBuilder> createBuilder(const PipelineConfiguration& config);
    
    std::unique_ptr<PipelineBuilder> m_builder;
    bool m_isComposite = false;
};
