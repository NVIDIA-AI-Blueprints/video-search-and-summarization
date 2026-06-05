/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "prometheus/exposer.h"
#include "prometheus/registry.h"
#include <memory>
#include <atomic>
#include <string>

#define GET_PROMETHEUS PrometheusClient::getInstance

class PrometheusClient
{
public:
    PrometheusClient();
    static PrometheusClient* getInstance();
    void updateQos(double value, std::string cam_name, std::string metric_name);
    void incrementWebrtcStreams();
    void decrementWebrtcStreams();
    void incrementRecordingStreams();
    void decrementRecordingStreams();
    void updateRtspConnection(unsigned value);
    void updateRecordingStatus(double value, std::string cam_name);
    void updateStorageSize(double value);
    void updateRecorderFps(double value, std::string camera_name);
    void updateWebrtcFps(double value, std::string sensor_name, std::string peer_id);
    void updateCpuUsageByContainer(double value, const std::string& container_name);
    void updateRamUsageByContainer(double value, const std::string& container_name);
    void updateGpuUsageByContainer(double value, const std::string& container_name);
    void updateAvailableStorageSpace(double value);
    void updateAgingBytesReclaimed(uint64_t bytes_reclaimed);

private:
    static PrometheusClient* _instance;
    std::atomic<bool>                                       m_doNothing{false};

    std::shared_ptr<prometheus::Exposer>                    m_exposer = nullptr;
    std::shared_ptr<prometheus::Registry>                   m_registry = nullptr;
    std::shared_ptr<prometheus::Family<prometheus::Gauge> > m_qos_gauge_family = nullptr;
    std::shared_ptr<prometheus::Family<prometheus::Gauge> > m_metric_gauge_family = nullptr;
    std::shared_ptr<prometheus::Gauge>                      m_num_active_webrtc_streams = nullptr;
    std::shared_ptr<prometheus::Gauge>                      m_num_active_recording = nullptr;
    std::shared_ptr<prometheus::Gauge>                      m_num_active_rtsp_connection = nullptr;
    std::shared_ptr<prometheus::Gauge>                      m_record_size = nullptr;
    std::shared_ptr<prometheus::Gauge>                      m_aging_bytes_reclaimed = nullptr;

    void BuildGaugeFamily();

};
