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

/**
 * @brief Builder for single stream pipelines (live/replay)
 */
class SingleStreamPipelineBuilder : public PipelineBuilder
{
public:
    void buildPipeline(const PipelineConfiguration& config) override;
    void destroyPipeline() override;
    void startPipeline() override;
    void stopPipeline() override;
    
    std::shared_ptr<GstNvVideoDecoder> getDecoder() const override { return m_decoder; }
    std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const override { return m_encoder; }
    std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const override { return m_webrtcConsumer; }
    std::shared_ptr<NvLLOverlay> getOverlay() const override { return m_overlay; }
    std::shared_ptr<NvLLTransform> getTransform() const override { return m_transform; }
    std::shared_ptr<NvLLTransform> getTransformSink() const override { return m_transformSink; }
    std::shared_ptr<ImageEnc> getImageEncoder() const override { return m_imageEncoder; }
    std::shared_ptr<NvCompositor> getCompositor() const override { return nullptr; }
    std::shared_ptr<VideoWebRTCSender> getVideoSender() const override { return m_videoSender; }
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const override { return m_nativeStreamProducer; }
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> getIPCProducer() const override { return m_ipcProducer; }
#endif

private:
    void buildDecoderPipeline(const PipelineConfiguration& config);
    void buildPassThroughPipeline(const PipelineConfiguration& config);
    void buildNativeStreamPipeline(const PipelineConfiguration& config);
    void buildGodsEyeViewPipeline(const PipelineConfiguration& config);
    void buildIPCPipeline(const PipelineConfiguration& config);
    void buildStandardPipeline(const PipelineConfiguration& config);
    
    void setupConsumerPipeline(const PipelineConfiguration& config);
    void setupImageCapturePipeline(const PipelineConfiguration& config);
    void setupOverlayPipeline(const PipelineConfiguration& config);
    void setupStandardPipeline(const PipelineConfiguration& config);

    std::shared_ptr<GstNvVideoDecoder> m_decoder = nullptr;
    std::shared_ptr<VideoWebRTCSender> m_videoSender = nullptr;
    std::shared_ptr<NativeStreamProducer> m_nativeStreamProducer = nullptr;
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> m_ipcProducer = nullptr;
#endif
    PipelineConfiguration m_config;
};
