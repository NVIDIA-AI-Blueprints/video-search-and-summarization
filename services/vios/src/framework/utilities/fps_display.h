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
#include "logger.h"
#include <string.h>

using namespace nv_vms;

inline constexpr int DEFAULT_FPS_CAPTURE_INTERVAL_SEC = 5;  // 5 secs
inline constexpr int DEFAULT_FPS_PUBLISH_INTERVAL_SEC = 60; // 60 secs

class FPSDisplay
{
    public:
        FPSDisplay(int capture_interval_secs, int publish_interval_secs)
        : m_fpsCaptureIntervalSecs (capture_interval_secs * 1000 * 1000)
        , m_fpsPublishIntervalSecs (publish_interval_secs * 1000 * 1000)
        {

        }
        FPSDisplay()
        {
            m_fpsCaptureIntervalSecs = DEFAULT_FPS_CAPTURE_INTERVAL_SEC * 1000 * 1000;
            m_fpsPublishIntervalSecs = DEFAULT_FPS_PUBLISH_INTERVAL_SEC * 1000 * 1000;
        }
        ~FPSDisplay(){ }

        void       displayFPS           (unsigned long pts_in_ms, string peerId_streamId);
        double     getAvgFPS            () { return m_avgFPS; }
        int        getInstFPS           () { return m_instFPS; }

    private:
        /* Below data structure is required only for fps calculation */
        struct timeval                                  m_prevDumpTime {0, 0};
        unsigned long                                   m_sumDiff {0};
        unsigned long                                   m_prevBufferTime {1000};
        unsigned                                        m_frameCount {0};
        struct timeval                                  m_prevCaptureTime {0, 0};
        struct timeval                                  m_prevPublishTime {0, 0};
        std::vector<double>                             m_fpsVector;
        string                                          m_fpsValues;
        int                                             m_fpsCaptureIntervalSecs {0};
        int                                             m_fpsPublishIntervalSecs {0};
        double                                          m_avgFPS {0.0};
        double                                          m_instFPS {0.0};
};