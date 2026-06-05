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

#pragma once
#include "media/video_source/core/CommonVideoSource.h"

class VodVideoSource
{
public:
    VodVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    :  m_commonVideoSource(uri, opts)
    {}

    void createConsumerPipeline() { m_commonVideoSource.createConsumerPipeline(); }
    void setBitstreamConsumer(std::shared_ptr<IMediaDataConsumer> consumer) { m_commonVideoSource.setBitstreamConsumer(consumer); }
    void setConsumerReady() { m_commonVideoSource.setConsumerReady(); }
    void startStream() { m_commonVideoSource.startStream(); }
    void stopAndRemoveConsumers() { m_commonVideoSource.stopAndRemoveConsumers(); }
    void stopStream() { m_commonVideoSource.resetConsumerAndDestroyDecoderIfRequired(); }
    int64_t getFirstTs() { return m_commonVideoSource.getFirstTs(); }

private:
    CommonVideoSource   m_commonVideoSource;
};
