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

#include "PipelineManager.h"
#include "../builders/PipelineBuilder.h"
#include "../builders/SingleStreamPipelineBuilder.h"
#include "../builders/CompositePipelineBuilder.h"
#include "logger.h"
#include <thread>
#include <chrono>

// Implementation of PipelineManager
PipelineManager::PipelineManager() = default;

void PipelineManager::createPipeline(const PipelineConfiguration& config)
{
    m_builder = createBuilder(config);
    m_builder->buildPipeline(config);
    m_isComposite = config.getCompositor().enabled;
}

std::unique_ptr<PipelineBuilder> PipelineManager::createBuilder(const PipelineConfiguration& config)
{
    if (config.getCompositor().enabled) {
        return std::make_unique<CompositePipelineBuilder>();
    } else {
        return std::make_unique<SingleStreamPipelineBuilder>();
    }
}

void PipelineManager::destroyPipeline()
{
    LOG(info) << "PipelineManager::destroyPipeline() called" << endl;
    
    if (m_builder) {
        try {
            // First stop the pipeline to ensure all threads are stopped
            LOG(info) << "Stopping pipeline..." << endl;
            m_builder->stopPipeline();
            
            // Give threads time to finish - longer for composite pipelines
            auto delay = m_isComposite ? std::chrono::milliseconds(300) : std::chrono::milliseconds(200);
            std::this_thread::sleep_for(delay);
            
            // Now destroy the pipeline
            LOG(info) << "Destroying pipeline components..." << endl;
            m_builder->destroyPipeline();
            
            // Give destruction time to complete
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            
        } catch (const std::exception& e) {
            LOG(error) << "Exception during pipeline destruction: " << e.what() << endl;
        } catch (...) {
            LOG(error) << "Unknown exception during pipeline destruction" << endl;
        }
        
        // Finally reset the builder - this should always happen
        m_builder.reset();
        m_isComposite = false;
        
        LOG(info) << "PipelineManager::destroyPipeline() completed" << endl;
    }
}

void PipelineManager::startPipeline()
{
    if (m_builder) {
        m_builder->startPipeline();
    }
}

void PipelineManager::stopPipeline()
{
    if (m_builder) {
        m_builder->stopPipeline();
    }
}

void PipelineManager::switchPipeline(const PipelineConfiguration& config)
{
    LOG(info) << "Switching pipeline for peer: " << config.getPeerId() << endl;
    
    // Stop and destroy the current pipeline
    if (m_builder) {
        try {
            m_builder->stopPipeline();
            m_builder->destroyPipeline();
        } catch (const std::exception& e) {
            LOG(error) << "Exception during pipeline switch cleanup: " << e.what() << endl;
        }
        m_builder.reset();
    }
    
    // Create and build the new pipeline
    m_builder = createBuilder(config);
    m_builder->buildPipeline(config);
    m_isComposite = config.getCompositor().enabled;
    
    // Start the new pipeline
    m_builder->startPipeline();
    
    LOG(info) << "Pipeline switched successfully for peer: " << config.getPeerId() << endl;
}

// Pipeline component accessors
std::shared_ptr<GstNvVideoDecoder> PipelineManager::getDecoder() const
{
    return m_builder ? m_builder->getDecoder() : nullptr;
}

std::shared_ptr<NvEncoderVideoConsumer> PipelineManager::getEncoder() const
{
    return m_builder ? m_builder->getEncoder() : nullptr;
}

std::shared_ptr<WebrtcSinkConsumer> PipelineManager::getWebrtcConsumer() const
{
    return m_builder ? m_builder->getWebrtcConsumer() : nullptr;
}

std::shared_ptr<NvLLOverlay> PipelineManager::getOverlay() const
{
    return m_builder ? m_builder->getOverlay() : nullptr;
}

std::shared_ptr<NvLLTransform> PipelineManager::getTransform() const
{
    return m_builder ? m_builder->getTransform() : nullptr;
}

std::shared_ptr<NvLLTransform> PipelineManager::getTransformSink() const
{
    return m_builder ? m_builder->getTransformSink() : nullptr;
}

std::shared_ptr<ImageEnc> PipelineManager::getImageEncoder() const
{
    return m_builder ? m_builder->getImageEncoder() : nullptr;
}

std::shared_ptr<NvCompositor> PipelineManager::getCompositor() const
{
    return m_builder ? m_builder->getCompositor() : nullptr;
}

std::shared_ptr<VideoWebRTCSender> PipelineManager::getVideoSender() const
{
    return m_builder ? m_builder->getVideoSender() : nullptr;
}

std::shared_ptr<NativeStreamProducer> PipelineManager::getNativeStreamProducer() const
{
    return m_builder ? m_builder->getNativeStreamProducer() : nullptr;
}

#ifdef JETSON_PLATFORM
std::shared_ptr<NvIPCProducer> PipelineManager::getIPCProducer() const
{
    return m_builder ? m_builder->getIPCProducer() : nullptr;
}
#endif

const std::vector<std::shared_ptr<GstNvVideoDecoder>>& PipelineManager::getDecoders() const
{
    static const std::vector<std::shared_ptr<GstNvVideoDecoder>> empty;
    if (auto composite = dynamic_cast<CompositePipelineBuilder*>(m_builder.get())) {
        return composite->getDecoders();
    }
    return empty;
}

const std::vector<std::shared_ptr<NvLLOverlay>>& PipelineManager::getOverlays() const
{
    static const std::vector<std::shared_ptr<NvLLOverlay>> empty;
    if (auto composite = dynamic_cast<CompositePipelineBuilder*>(m_builder.get())) {
        return composite->getOverlays();
    }
    return empty;
}
