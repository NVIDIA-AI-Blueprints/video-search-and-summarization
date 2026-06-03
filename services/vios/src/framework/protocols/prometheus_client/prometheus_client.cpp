/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "prometheus_client.h"
#include "prometheus/gauge.h"
#include "config.h"
#include "logger.h"

#include <memory>
#include <string>
#include <iostream>

using namespace prometheus;

#define CHECK_DO_NOTHING if (m_doNothing) { return; }

PrometheusClient* PrometheusClient::_instance = nullptr;

PrometheusClient::PrometheusClient()
{
    LOG(info) << __FUNCTION__ << " Creation" << std::endl;
    nv_vms::DeviceConfig config =  GET_CONFIG();
    if (config.enable_prometheus == false)
    {
        m_doNothing = true;
        return;
    }

    try
    {
        std::string prometheus_port = config.prometheus_port;
        LOG(info) << "Attempting to bind Prometheus exposer to port: " << prometheus_port << std::endl;

        m_exposer = std::make_shared<Exposer> (prometheus_port);
        m_registry = std::make_shared<Registry>();

        BuildGaugeFamily();
        m_exposer->RegisterCollectable(m_registry);

        LOG(info) << "Prometheus client initialized successfully on port: " << prometheus_port << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to initialize Prometheus client: " << e.what() << std::endl;
        LOG(error) << "Prometheus functionality will be disabled" << std::endl;
        m_doNothing = true;
        // Reset any partially initialized shared_ptr objects
        m_exposer.reset();
        m_registry.reset();
        return;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception occurred while initializing Prometheus client" << std::endl;
        LOG(error) << "Prometheus functionality will be disabled" << std::endl;
        m_doNothing = true;
        // Reset any partially initialized shared_ptr objects
        m_exposer.reset();
        m_registry.reset();
        return;
    }
}

PrometheusClient* PrometheusClient::getInstance()
{
    if (_instance == nullptr)
    {
        _instance = new PrometheusClient();
    }
    return _instance;
}

void PrometheusClient::BuildGaugeFamily()
{
    auto& qos_gauge_family = BuildGauge()
                        .Name("vms_qos")
                        .Help("QOS data from vms")
                        .Labels({{"app_name", "vms"}})
                        .Register(*m_registry);
    m_qos_gauge_family.reset(&qos_gauge_family);

    auto& metric_gauge_family = BuildGauge()
                            .Name("vms_metrics")
                            .Help("Metrics from vms")
                            .Labels({{"app_name", "vms"}})
                            .Register(*m_registry);
    auto& num_active_webrtc_streams =
        metric_gauge_family.Add({{"metric_name", "num_active_webrtc_streams"}});
    auto& num_active_recording =
        metric_gauge_family.Add({{"metric_name", "num_active_recording_streams"}});
    auto& num_active_rtsp_connection =
        metric_gauge_family.Add({{"metric_name", "num_active_rtsp_connection"}});
    auto& record_size =
        metric_gauge_family.Add({{"metric_name", "record_size"}});
    auto& aging_bytes_reclaimed =
        metric_gauge_family.Add({{"metric_name", "aging_bytes_reclaimed"}});
    
    m_num_active_webrtc_streams.reset(&num_active_webrtc_streams);
    m_num_active_recording.reset(&num_active_recording);
    m_num_active_rtsp_connection.reset(&num_active_rtsp_connection);
    m_record_size.reset(&record_size);
    m_aging_bytes_reclaimed.reset(&aging_bytes_reclaimed);
    m_metric_gauge_family.reset(&metric_gauge_family);
}

void PrometheusClient::updateQos(double value, std::string cam_name, std::string metric_name)
{
    CHECK_DO_NOTHING
    m_qos_gauge_family->Add({{"metric_name", metric_name}, {"camera", cam_name}}).Set(value);
}

void PrometheusClient::incrementWebrtcStreams()
{
    CHECK_DO_NOTHING
    m_num_active_webrtc_streams->Increment();
}

void PrometheusClient::decrementWebrtcStreams()
{
    CHECK_DO_NOTHING
    m_num_active_webrtc_streams->Decrement();
}

void PrometheusClient::incrementRecordingStreams()
{
    CHECK_DO_NOTHING
    m_num_active_recording->Increment();
}

void PrometheusClient::decrementRecordingStreams()
{
    CHECK_DO_NOTHING
    m_num_active_recording->Decrement();
}

void PrometheusClient::updateRtspConnection(unsigned value)
{
    CHECK_DO_NOTHING
    m_num_active_rtsp_connection->Set(value);
}

void PrometheusClient::updateRecordingStatus(double value, std::string cam_name)
{
    CHECK_DO_NOTHING
    m_metric_gauge_family->Add({{"metric_name", "camera_recording_status"},
                                {"camera_name", cam_name}}).Set(value);
}

void PrometheusClient::updateStorageSize(double value)
{
    CHECK_DO_NOTHING
    m_record_size->Set(value);
}

void PrometheusClient::updateRecorderFps(double value, std::string camera_name)
{
    CHECK_DO_NOTHING
    m_qos_gauge_family->Add({{"metric_name", "recorder_stream_fps"}, {"camera", camera_name}}).Set(value);
}

void PrometheusClient::updateWebrtcFps(double value, std::string sensor_name, std::string peer_id)
{
    CHECK_DO_NOTHING
    m_qos_gauge_family->Add({{"metric_name", "webrtc_fps"}, {"sensor_name", sensor_name}, {"peer_id", peer_id}}).Set(value);
}

// Container-based methods (using single container_name label)
void PrometheusClient::updateCpuUsageByContainer(double value, const std::string& container_name)
{
    CHECK_DO_NOTHING
    m_metric_gauge_family->Add({{"metric_name", "cpu_usage"}, {"container_name", container_name}}).Set(value);
}

void PrometheusClient::updateRamUsageByContainer(double value, const std::string& container_name)
{
    CHECK_DO_NOTHING
    m_metric_gauge_family->Add({{"metric_name", "ram_usage"}, {"container_name", container_name}}).Set(value);
}

void PrometheusClient::updateGpuUsageByContainer(double value, const std::string& container_name)
{
    CHECK_DO_NOTHING
    m_metric_gauge_family->Add({{"metric_name", "gpu_usage"}, {"container_name", container_name}}).Set(value);
}

void PrometheusClient::updateAvailableStorageSpace(double value)
{
    CHECK_DO_NOTHING
    m_metric_gauge_family->Add({{"metric_name", "available_storage_mb"}}).Set(value);
}

void PrometheusClient::updateAgingBytesReclaimed(uint64_t bytes_reclaimed)
{
    CHECK_DO_NOTHING
    m_aging_bytes_reclaimed->Increment(static_cast<double>(bytes_reclaimed));
}
