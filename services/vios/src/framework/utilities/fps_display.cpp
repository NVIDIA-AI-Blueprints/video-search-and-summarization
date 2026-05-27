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

#include "fps_display.h"
#include "prometheus_client/prometheus_client.h"
#include <tuple>
#include <string>

static inline std::tuple<std::string, std::string, std::string>parseStreamIdPeerId(const std::string& peerId_streamId) {
    // Case 1: recording_cam_name
    const string recording_prefix = "recording_";
    if (peerId_streamId.compare(0, recording_prefix.size(), recording_prefix) == 0) {
        return {peerId_streamId.substr(recording_prefix.size()), "", "recording"}; //returning sensor_name, peer_id = "", "recording"
    }
    // Case 2: peerid:cam_name_live_out
    size_t colon = peerId_streamId.find(':');
    const string out_suffix = "_live_out";
    if (colon != string::npos) {
        string peer_id = peerId_streamId.substr(0, colon);
        string rest = peerId_streamId.substr(colon + 1);
        if (rest.size() > out_suffix.size() &&
            rest.compare(rest.size() - out_suffix.size(), out_suffix.size(), out_suffix) == 0) {
            string sensor_name = rest.substr(0, rest.size() - out_suffix.size());
            return {sensor_name, peer_id, "live_out"}; //returning sensor_name, peer_id, "live_out"
        }
    }
    else {
        // Case 3: peerid_live_out (For video wall)
        if (peerId_streamId.size() > out_suffix.size() &&
            peerId_streamId.compare(peerId_streamId.size() - out_suffix.size(), out_suffix.size(), out_suffix) == 0) {
            string peer_id = peerId_streamId.substr(0, peerId_streamId.size() - out_suffix.size());
            string sensor_name = "video_wall_" + peer_id;
            return { sensor_name, peer_id, "live_out"}; //returning sensor_name, peer_id, "live_out"
        }
    }
    // Unknown format
    return {"", "", ""};
}


void FPSDisplay::displayFPS(unsigned long pts, string peerId_streamId)
{
    struct timeval timeNow;

    uint64_t current_diff = (pts - m_prevBufferTime);
    m_sumDiff = m_sumDiff + current_diff;
    m_prevBufferTime = pts;
    m_frameCount++;
    if (m_sumDiff)
    {
        m_instFPS = (1000.00) / (static_cast<double>(m_sumDiff)/static_cast<double>(m_frameCount));
    }

    gettimeofday(&timeNow, nullptr);
    long elapsed_time = timevaldiff(m_prevDumpTime, timeNow);
    if (elapsed_time >= 1000000)    // 1sec
    {
        double fps_val = 0;
        if (m_sumDiff && m_frameCount)
        {
            fps_val  = m_instFPS;
        }
        m_prevDumpTime = timeNow;
        m_sumDiff = 0;
        m_frameCount = 0;
        m_fpsVector.push_back(fps_val);

        elapsed_time = timevaldiff(m_prevCaptureTime, timeNow);
        if (elapsed_time >= m_fpsCaptureIntervalSecs)
        {
            double sum = 0.0;
            for (const double &i: m_fpsVector)
            {
                sum += (double)i;
            }
            m_avgFPS = sum / m_fpsVector.size();
            auto [sensor_name, peer_id, type] = parseStreamIdPeerId(peerId_streamId);

            if (type == "recording") {
                GET_PROMETHEUS()->updateRecorderFps(m_avgFPS, sensor_name);
            }
            else if (type == "live_out") {
                GET_PROMETHEUS()->updateWebrtcFps(m_avgFPS,sensor_name , peer_id);
            }
            if (m_fpsValues.empty())
                m_fpsValues = to_string(m_avgFPS);
            else
                m_fpsValues = m_fpsValues + ", " + to_string(m_avgFPS);

            m_fpsVector.clear();
            m_prevCaptureTime = timeNow;
        }
        elapsed_time = timevaldiff(m_prevPublishTime, timeNow);
        if (elapsed_time > m_fpsPublishIntervalSecs)
        {
            LOG(warning) << "Unique ID = " << peerId_streamId << " FPS = { " << m_fpsValues << " }" << endl;
            m_fpsValues.clear();
            m_prevPublishTime = timeNow;
        }
    }
    else
    {
        m_avgFPS = -1;
    }
}
