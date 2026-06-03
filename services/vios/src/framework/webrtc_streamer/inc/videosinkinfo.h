/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "media/base/video_broadcaster.h"
#include "stats.h"
#include "mm_utils.h"
#include "nvvideoencoder.h"


struct DecoderStats : public LatencyStats
{
    DecoderStats() {}
    ~DecoderStats() {}
    std::queue<int64_t> qTimestamp;
    std::mutex   m_queueLock;
    std::condition_variable  m_qTsCond;
};

struct VideoSinkInfo
{
    VideoSinkInfo(): m_broadcaster(nullptr), m_quality(""), m_isSinkReady{false}
    {}
    rtc::VideoBroadcaster* m_broadcaster;
    DecoderStats m_decoderStats;
    CodecStats m_decoderLatencyStats;
    FrameSize m_frameSize;
    std::string m_quality;
    std::shared_ptr<IMediaDataConsumer>     m_consumer;
    std::atomic<bool>                       m_isSinkReady;
};