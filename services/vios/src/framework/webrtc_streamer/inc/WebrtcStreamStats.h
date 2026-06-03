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

#include <string>
#include <jsoncpp/json/json.h>
#include <fstream>
#include "Scheduler.h"

class PeerConnection;
class WebrtcStreamStats
{
private:
    std::ofstream   m_logStream;
    std::string     m_cameraName = "";
    std::string     m_peerId;
    bool            m_error = false;

    void stopLogging();
public:
    WebrtcStreamStats(std::string peerId);
    ~WebrtcStreamStats();

    void createLogFile();
    void setCameraName(std::string camera_name);
    void logStatsInFile(const Json::Value stats, uint64_t bitrate);
};
