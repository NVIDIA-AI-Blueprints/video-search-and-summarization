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

#include "WebrtcStreamStats.h"
#include "PeerConnection.h"
#include <unordered_map>

#define CSV_DELIMITER ", "

WebrtcStreamStats::WebrtcStreamStats(std::string peerId) : m_peerId(peerId)
{}

WebrtcStreamStats::~WebrtcStreamStats()
{
    stopLogging();
}

void WebrtcStreamStats::createLogFile()
{
    string logfile;
    string fname = "webrtc_stats_" + m_peerId + "_" + getCurrentTime() + ".csv";
    logfile = GET_CONFIG().vst_data_path + string("/") + fname;
    m_logStream.open(logfile, std::ofstream::out  | std::ofstream::app);
    if (m_logStream.fail())
    {
        LOG(error) << "Failed to open log file" << endl;
        m_error = true;
        return;
    }
    LOG(info) << "Webrtc Stats logging started at: " << logfile << endl;

    string header = "camera_name, time, peerId, packetsReceived, packetsLost, framesReceived, framesDropped, "
                    "framesDecoded, pliCount, nackCount, lastPacketReceivedTimestamp, keyFramesDecoded, framesPerSecond, bitrate";
    m_logStream << header << "\n";
}

void WebrtcStreamStats::logStatsInFile(const Json::Value stats, uint64_t bitrate)
{
    if (m_error || !m_logStream.is_open())
    {
        return;
    }

    m_logStream << m_cameraName << CSV_DELIMITER
                << getCurrentUtcTime().c_str() << CSV_DELIMITER
                << m_peerId << CSV_DELIMITER
                << stats.get("packetsReceived", "0").asString() << CSV_DELIMITER
                << stats.get("packetsLost", "0").asString() << CSV_DELIMITER
                << stats.get("framesReceived", "0").asString() << CSV_DELIMITER
                << stats.get("framesDropped", "0").asString() << CSV_DELIMITER
                << stats.get("framesDecoded", "0").asString() << CSV_DELIMITER
                << stats.get("pliCount", "0").asString() << CSV_DELIMITER
                << stats.get("nackCount", "0").asString() << CSV_DELIMITER
                << stats.get("lastPacketReceivedTimestamp", "0").asString() << CSV_DELIMITER
                << stats.get("keyFramesDecoded", "0").asString() << CSV_DELIMITER
                << stats.get("framesPerSecond", "0").asString() << CSV_DELIMITER
                << std::to_string(bitrate) << "\n";
    m_logStream.flush();
}

void WebrtcStreamStats::stopLogging()
{
    m_logStream.close();
}

void WebrtcStreamStats::setCameraName(std::string camera_name)
{
    m_cameraName = camera_name;
}
