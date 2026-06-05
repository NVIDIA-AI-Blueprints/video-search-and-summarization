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

#include "PipelineBuilder.h"
#include "../processors/compositors/nvcompositor.h"

/**
 * @brief Builder for composite/multi-stream pipelines
 */
class CompositePipelineBuilder : public PipelineBuilder
{
public:
    void buildPipeline(const PipelineConfiguration& config) override;
    void destroyPipeline() override;
    void startPipeline() override;
    void stopPipeline() override;
    
    std::shared_ptr<GstNvVideoDecoder> getDecoder() const override { return nullptr; }
    std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const override { return m_encoder; }
    std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const override { return m_webrtcConsumer; }
    std::shared_ptr<NvLLOverlay> getOverlay() const override { return nullptr; }
    std::shared_ptr<NvLLTransform> getTransform() const override { return m_transform; }
    std::shared_ptr<NvLLTransform> getTransformSink() const override { return m_transformSink; }
    std::shared_ptr<ImageEnc> getImageEncoder() const override { return m_imageEncoder; }
    std::shared_ptr<NvCompositor> getCompositor() const override { return m_compositor; }
    std::shared_ptr<VideoWebRTCSender> getVideoSender() const override { return nullptr; }
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const override { return nullptr; }
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> getIPCProducer() const override { return nullptr; }
#endif

    const std::vector<std::shared_ptr<GstNvVideoDecoder>>& getDecoders() const { return m_decoders; }
    const std::vector<std::shared_ptr<NvLLOverlay>>& getOverlays() const { return m_overlays; }

private:
    void validateCompositorRequirements(const PipelineConfiguration& config) const;
    void buildDecoderPipelines(const PipelineConfiguration& config);
    void buildCompositorPipeline(const PipelineConfiguration& config);
    void setupCompositorConsumers(const PipelineConfiguration& config);
    GridLayout createDefaultGridLayoutWithUrls(const std::vector<std::string>& urls, bool isGodsEyeView = false) const;

    std::vector<std::shared_ptr<GstNvVideoDecoder>> m_decoders;
    std::vector<std::shared_ptr<NvLLOverlay>> m_overlays;
    std::shared_ptr<NvCompositor> m_compositor = nullptr;
    std::vector<std::string> m_decoderUris;
    PipelineConfiguration m_config;
};
