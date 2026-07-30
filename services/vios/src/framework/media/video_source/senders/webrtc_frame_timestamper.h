/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <atomic>
#include <cstdint>

#include "rtc_base/time_utils.h"

struct WebrtcFrameTimestamp
{
    int64_t  m_timestampUs   = 0;
    uint32_t m_rtpTimestamp  = 0;
};

/* Produces capture timestamps for webrtc::VideoFrame. webRTC's
 * FrameCadenceAdapter rejects frames whose timestamp_us is not strictly
 * increasing, so one instance must be kept per frame source feeding a
 * broadcaster. */
class WebrtcFrameTimestamper
{
    public:
        [[nodiscard]] WebrtcFrameTimestamp next()
        {
            const int64_t timestamp_us = nextTimestampUs();
            return WebrtcFrameTimestamp{timestamp_us, toRtpTimestamp(timestamp_us)};
        }

    private:
        static constexpr int64_t RTP_TICKS_PER_MS = 90; /* 90 kHz video RTP clock */

        [[nodiscard]] int64_t nextTimestampUs()
        {
            int64_t last_timestamp_us = m_lastTimestampUs.load();
            while (true)
            {
                int64_t timestamp_us = webrtc::TimeMicros();
                if (timestamp_us <= last_timestamp_us)
                {
                    timestamp_us = last_timestamp_us + 1;
                }
                if (m_lastTimestampUs.compare_exchange_weak(last_timestamp_us, timestamp_us))
                {
                    return timestamp_us;
                }
            }
        }

        [[nodiscard]] static uint32_t toRtpTimestamp(int64_t timestamp_us)
        {
            return static_cast<uint32_t>((timestamp_us * RTP_TICKS_PER_MS) / 1000);
        }

        std::atomic<int64_t> m_lastTimestampUs{0};
};
