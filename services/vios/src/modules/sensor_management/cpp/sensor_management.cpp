/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_management.h"
#include "utils.h"
#include "logger.h"
#include "rtspserver.h"
#include "syncInterface.h"
#include "database.h"
#include "streamrecorder.h"
#include "config.h"
#include "error_code.h"
#include "sensor_monitoring.h"
#include "cmdline_parser.h"
#include "profiler.h"
#include "decoderpool.h"
#include "stream_monitor.h"
#include "udpclientpool.h"
#include "vst_common.h"
#include "sensor_management_utils.h"
#ifdef ENABLE_NATIVE_STREAM_MONITOR
#include "native_stream_monitor.h"
#endif

#include <boost/algorithm/clamp.hpp>
#include <algorithm>

using namespace nv_vms;
using namespace std;

extern "C" void* createSensorManagementObject()
{
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    return static_cast<void*>(static_cast<IVstModule*>(new SensorManagement(deviceManager)));
}

extern "C" void deleteSensorManagementObject(IVstModule* object)
{
    SensorManagement* sensorManagement = static_cast<SensorManagement*>(object);
    delete sensorManagement;
}

void SensorManagement::onDecoderPlayingStatus(const string &url)
{
    LOG(info) << "Notifying for " << secureUrlForLogging(url) << endl;
    std::shared_ptr<DeviceManager> deviceManager = getDeviceManagerObject();
    if(deviceManager != nullptr && deviceManager->type == TYPE_VST)
    {
        std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
        for (auto const& stream : streamList)
        {
            if (stream->live_proxy_url == url)
            {
                SensorStatus status;
                status.timeStamp = getCurrentTime();
                status.event = SensorStatusStreaming;
                status.sensorId = stream->sensorId;
                status.sensorName = stream->name;
                string live_url = vst_common::toDomainName(stream->live_proxy_url, stream->id);
                m_sensorMonitoring->notifyEvent(status, live_url, stream->socket_name);
                break;
            }
        }
    }
}

void SensorManagement::onCameraStreaming(const string &streamId, const string &proxy_url, const string &vod_url, const StreamStatus newStatus)
{
    if (newStatus != StreamStatus::STREAM_STATUS_STREAMING)
    {
        /* As of now, Only listening for streaming event */
        return;
    }

    LOG(info) << "onCameraStreaming: streamId:" << streamId << " proxy_url:" << secureUrlForLogging(proxy_url) << " vod_url:" << secureUrlForLogging(vod_url) << endl;
    std::shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if(deviceManager != nullptr && (deviceManager->type == TYPE_VST || deviceManager->type == TYPE_MMS))
    {
        std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
        for (auto const& stream : streamList)
        {
            if (stream->id == streamId && newStatus == StreamStatus::STREAM_STATUS_STREAMING)
            {
                // Set the stream status to streaming
                stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
                        translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));

                stream->live_proxy_url = proxy_url;
                stream->replay_url = vod_url;

                LOG(info) << "Stream status changed to STREAMING for streamId:" << streamId << " live_proxy_url:" << secureUrlForLogging(stream->live_proxy_url)
                << " replay_url:" << secureUrlForLogging(stream->replay_url) << endl;
                break;
            }
        }
        // Remove sub-streams invalid.
        std::vector<shared_ptr<StreamInfo>>::iterator iter;
        for (auto const& stream : streamList)
        {
            SensorVideoEncoderSettingsValues& enc_values = stream->getvideoEncoderValues();
            if (enc_values.encoding.empty() && !stream->isMainStream)
            {
                LOG(info) << "Remove sub stream : "<< secureUrlForLogging(stream->live_url) << endl;
                GET_DB_INSTANCE()->deleteRowStream(stream->id); // remove subStream entry
                deviceManager->removeStream(stream->live_url);
            }
        }
    }
}

void SensorManagement::setConfigValues(std::shared_ptr<DeviceManager> deviceManager)
{
    GET_CONFIG().enable_camera_auto_discovery =  deviceManager->m_sensorDiscoveryObjectPairList.size() > 0;
    LOG(info) << "enable_camera_auto_discovery: " << GET_CONFIG().enable_camera_auto_discovery << endl;
    LOG(info) << "enable_stream_monitoring: " << deviceManager->needStreamMonitoring << endl;
}

SensorManagement::SensorManagement(std::shared_ptr<DeviceManager> deviceMngr): m_deviceManager(deviceMngr)
{

    map<string, std::shared_ptr<DeviceManager>, std::less<>>::iterator it_server;
    // Supporting one adaptor at a Time.
    std::shared_ptr<DeviceManager> device_manager = GET_DEVICE_MANAGER();
    if(device_manager != nullptr)
    {
        LOG(info) << "Creating SensorManagement object" << endl;
        setConfigValues(device_manager);
#ifdef ENABLE_NATIVE_STREAM_MONITOR
        makeNativeSensorsOffline();
#endif

        m_sensorControl.reset(new SensorControl(device_manager.get()));
        m_sensorMonitoring.reset(new SensorMonitoring(this, device_manager->m_sensorDiscoveryObjectPairList));

#ifdef ENABLE_NATIVE_STREAM_MONITOR
        NativeStreamMonitor::getInstance()->setDeviceManager(device_manager);
#endif
    }
}

void SensorManagement::makeNativeSensorsOffline()
{
    std::shared_ptr<DeviceManager> device_manager = GET_DEVICE_MANAGER();
    if(device_manager != nullptr)
    {
        vector<shared_ptr<SensorInfo>> list = device_manager->getSensorList();
        for (auto sensor: list)
        {
            if (sensor && sensor->type == SENSOR_TYPE_CSI)
            {
                LOG(info) << "Make native sensor offline name:" << sensor->name << " id:" << sensor->id << endl;
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(translateCameraHttpErrorCodeToVmsErrorCode(NoError)));
                sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOffline);
            }
        }
    }
}

void SensorManagement::onMessage(Json::Value payload)
{
    if (payload.isNull() || !payload.isObject())
    {
        LOG(info) << " Invalid JSON message format" << endl;
        return;
    }

    LOG(verbose) << "Received payload on redis:" << payload.toStyledString() << endl;

    std::string alert_type = payload.get("alert_type", "").asString();
    std::string created_at = payload.get("created_at", "").asString();

    // Access the event object
    if (payload.isMember("event") && !payload["event"].isNull())
    {
        Json::Value event = payload["event"];
        std::string stream_id = event.get("camera_id", "").asString();
        std::string camera_name = event.get("camera_name", "").asString();
        std::string proxy_url = event.get("camera_url", "").asString();
        std::string vod_url = event.get("camera_vod_url", "").asString();
        std::string change = event.get("change", "").asString();

        string changeLocal = vst_common::sensorStatusEventToString(nv_vms::SensorStatusStreaming);
        if (change == changeLocal)
        {
            onCameraStreaming(stream_id, proxy_url, vod_url, StreamStatus::STREAM_STATUS_STREAMING);
        }
    }
}

SensorManagement::~SensorManagement()
{
    try {
        LOG(info) << __METHOD_NAME__ << endl;
        stop();
        notifyVmsExitEvent();
        LOG(info) <<"Exiting from ~SensorManagement" << endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~SensorManagement: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~SensorManagement" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void SensorManagement::start()
{
    /* Scan & get all the sensors info */
    if ((m_sensorControl != nullptr) && (m_sensorControl->connect() == 0))
    {
        vector<shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
        /* Send camera_add for all the online sensors */
        for (uint32_t i = 0; i < sensors.size(); i++)
        {
            shared_ptr<SensorInfo> sensor = sensors[i];
            if (sensor->type == SENSOR_TYPE_WEBRTC || sensor->type == SENSOR_TYPE_CSI || sensor->type == SENSOR_TYPE_FILE)
            {
                continue;
            }

            if (sensor->getSensorStatus() == SensorStatusOnline)
            {
                SensorStatus status;
                status.timeStamp = getCurrentTime();
                status.sensorId = sensor->id;
                status.sensorName = sensor->name;
                status.serverId = m_deviceManager->getDeviceId();
                status.event = SensorStatusOnline;

                if (sensor->m_notify == true)
                {
                    LOG(info) << "Sending event:" << status.event  << " for " << sensor->ip << endl;
                    vst_common::notifyEvent(status, "");
                }
            }
        }

        getSensorInfo(true);
    }
}

void SensorManagement::stop()
{
    if (m_deviceManager->needStreamMonitoring)
    {
        StreamMonitor::deleteInstance();
    }
    if (m_sensorMonitoring)
    {
        m_sensorMonitoring.reset();
    }
    if (m_sensorControl)
    {
        m_sensorControl.reset();
    }
#ifdef ENABLE_NATIVE_STREAM_MONITOR
    NativeStreamMonitor::deleteInstance();
#endif
}

void SensorManagement::scanCameras()
{
    MEASURE_FUNCTION_EXECUTION_TIME
    if (m_deviceManager.get() != nullptr)
    {
        if (m_deviceManager->enabled)
        {
            scanCameras(true);
        }
    }
}

void SensorManagement::scanCameras(bool force)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    if (m_deviceManager.get() != nullptr)
    {
        if (force)
        {
            /* Scan all the sensors including user-removed sensors */
            std::lock_guard<std::mutex> removedListMutex(m_userRemovedListMutex);
            m_userRemovedList.clear();
        }
        if (m_deviceManager->getDeviceType() == TYPE_STREAMER || m_deviceManager->isRtspAdaptor)
        {
            /* To scan the filesystem for new files in case of Nvstreamer */
            if ((m_sensorControl != nullptr) && (m_sensorControl->connect() == 0))
            {
                getSensorInfo(force);
            }
        }
    }
}

vector<shared_ptr<SensorInfo>> SensorManagement::getSensorInfo(bool rescan)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    LOG(verbose) << "SensorManagement::getSensorInfo" << endl;
    vector<shared_ptr<SensorInfo>> sensors;
    std::shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if(deviceManager != nullptr)
    {
        if (deviceManager->getSensorsSize() == 0 || rescan)
        {
            if ((deviceManager->type != TYPE_EVENT) && (m_sensorControl != nullptr)) // adaptor of event type is not suppose to deal with camera APIs
            {
                m_sensorControl->getSensorsStreamInfo();
            }
        }
        sensors = deviceManager->getSensorList();
        if(deviceManager->type == TYPE_VST || deviceManager->type == TYPE_MMS)
        {
            for (uint32_t i = 0; i < sensors.size(); i++)
            {
                shared_ptr<SensorInfo> sensor = sensors[i];
                if (sensor->type == SENSOR_TYPE_WEBRTC || sensor->type == SENSOR_TYPE_CSI)
                {
                    continue;
                }
                getAndAddProxyUrl(sensor, deviceManager->type);
                if (sensor->getSensorStatus() == SensorStatusOnline)
                {
                    if (!GET_CONFIG().remote_vst_address.empty())
                    {
                        vst_common::addSensorToRemoteDevice(sensor, deviceManager);
                    }
                }
            }
        }
    }
    return sensors;
}

shared_ptr<SensorInfo> SensorManagement::getSensorInfo(const string& sensorId, bool force)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    LOG(verbose) << "getSensorInfo: sensorId: " << sensorId << endl;
    std::shared_ptr< DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if (deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        return nullptr;
    }

    std::shared_ptr<SensorControl> sensorControl = getSensorControl();
    if(sensorControl == nullptr)
    {
        LOG(error) << "SensorControl object not found for sensorId: " << sensorId << endl;
        return nullptr;
    }

    shared_ptr<SensorInfo> sensor = sensorControl->getSensor(sensorId);
    if(sensor.get() == nullptr)
    {
        LOG(error) << "Failed to find sensor" << endl;
        return nullptr;
    }

    LOG(verbose) << "STREAM size: " << sensor->streams.size() << endl;
    if (force || (sensor->streams.size() == 0 &&
        sensor->getHttpErrorStatus().first != CAMERA_CAMERA_NOT_FOUND_CODE))
    {
        if (deviceManager->type != TYPE_EVENT)
        {
            if ((sensor->type == SENSOR_TYPE_ONVIF) && (m_sensorControl != nullptr))
            {
                m_sensorControl->getSensorStreamInfo(sensor);
            }

            if (sensor->type == SENSOR_TYPE_WEBRTC)
            {
                return sensor;
            }

            if(deviceManager->type != TYPE_EVENT)
            {
                getAndAddProxyUrl(sensor, deviceManager->type);
            }
        }
    }
    return sensor;
}

int SensorManagement::setSensorInfo(const string sensor_id)
{
    return m_sensorControl->setSensorInfo(sensor_id);
}

int SensorManagement::getAndAddProxyUrl(shared_ptr<SensorInfo>& sensorInfo, const string& type)
{
    int ret = 0;
    if (sensorInfo && (sensorInfo->type == SENSOR_TYPE_UDP || sensorInfo->type == SENSOR_TYPE_FILE))
    {
        return ret;
    }
    if (sensorInfo->getHttpErrorStatus().first == CAMERA_NO_ERROR_CODE)
    {
        vector<shared_ptr<StreamInfo>> streams = sensorInfo->getStreams();
        shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
        if (deviceManager == nullptr)
        {
            LOG(error) << "Invalid deviceMngr object" << endl;
            ret = -1;
            return ret;
        }

        bool updateSensorSts = false;
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            shared_ptr<StreamInfo> stream = streams[j];

            if (stream->isMainStream && sensorInfo->type == SENSOR_TYPE_ONVIF) // set default values for main stream only
            {
                std::shared_ptr<nv_vms::SensorManagement> sensorMgmt = getself();
                setStreamDefaultSettings(sensorMgmt, sensorInfo->id, stream);
            }

            if(sensorInfo->type == SENSOR_TYPE_CSI && (stream->live_proxy_url.empty() ||
            sensorInfo->getSensorStatus() != SensorStatusEvent::SensorStatusOnline)) // For CSI sensor
            {
                stream->live_url = stream->replay_url = stream->live_proxy_url = vst_rtsp::rtspUrlPrefix(sensorInfo->id) + string(NV_CSI_SENSOR) + string("/") + stream->id;
#ifdef ENABLE_NATIVE_STREAM_MONITOR
                if (false == NativeStreamMonitor::getInstance()->addNativeStream(stream, sensorInfo->location))
                {
                    stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_OFFLINE,
                    translateStreamStatusToString(StreamStatus::STREAM_STATUS_OFFLINE)));
                    sensorInfo->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
                    break;
                }
#endif

                updateSensorSts = true;
                vst_common::notifySensorStatusEvent(SensorStatusStreaming, sensorInfo);
                stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
                    translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));

#ifdef ENABLE_NATIVE_STREAM_MONITOR
                NativeStreamMonitor::getInstance()->updateStreamSettings(stream->id, stream->settings);
#endif

                /* Add stream in recorder */
                std::string socket_name = addStream(stream);
                if (m_sensorMonitoring)
                {
                    if (socket_name.empty() == false)
                    {
                        LOG(info) << "socket_name = " << socket_name << endl;
                        stream->socket_name = socket_name;
                    }
                    else
                    {
                        /* TODO: Notify event */
                        // if (GET_CONFIG().enable_ipc_path == false)
                        // {
                        //     string live_url = vst_common::toDomainName(stream->live_proxy_url, stream->id);
                        //     m_sensorMonitoring->notifyEvent(status, live_url);
                        // }
                    }
                }
            }
            else if (stream->live_proxy_url.empty() || sensorInfo->m_notify)
            {
                if(type == TYPE_VST || type == TYPE_MMS)
                {
                    string url = stream->live_url;

                    SensorDetailsDBColumns row = GET_DB_INSTANCE()->readSensorDetails("", stream->sensorId);
                    if (!row.sensor_id_value.empty() && !url.empty() &&
                        !(row.username_value.empty() || row.password_value.empty()))
                    {
                        // Check if URL already contains credentials
                        size_t at_pos = url.find('@');
                        if (at_pos == string::npos)
                        {
                            string token("//");
                            string substr = row.username_value + ":" + row.password_value + "@";
                            insertString(url, token, substr);
                            /* Securely erase credentials from memory after use */
                            std::fill(substr.begin(), substr.end(), '\0');
                            substr.clear();
                        }
                    }

                    if (GET_DEVICE_MANAGER()->needRtspServer == false)
                    {
                        /* Send camera_proxy event */
                        if (sensorInfo->type != SENSOR_TYPE_FILE && sensorInfo->getSensorStatus() == SensorStatusOnline && stream->isMainStream)
                        {
                            LOG(info) << "Sending camera_proxy for streamId:" << stream->id << " url: " << secureUrlForLogging(url) << endl;
                            SensorStatus status;
                            status.timeStamp = getCurrentTime();
                            status.sensorId = stream->id;
                            status.sensorName = sensorInfo->name;
                            status.serverId = m_deviceManager->getDeviceId();
                            status.event = SensorStatusProxy;
                            status.tags = sensorInfo->tags;
                            if (sensorInfo->type == SENSOR_TYPE_MMS_ONVIF)
                            {
                                status.type = SENSOR_TYPE_MMS_ONVIF;
                            }

                            if (sensorInfo->m_notify == true)
                            {
                                /* Adding sensor details and its streams into the DB before sent camera_proxy to RTSP sevrver microservice.
                                So RTSP server microservice will update the created proxy into the DB */
                                vst_common::updateSensorDetailsToDB(deviceManager->id, sensorInfo);
                                vst_common::notifyEvent(status, url, &stream->getvideoEncoderValues());

                                sensorInfo->m_notify = false;

                                // Set the stream status to camera_proxy
                                stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_PROXY,
                                translateStreamStatusToString(StreamStatus::STREAM_STATUS_PROXY)), false);
                            }
                        }
                    }
                    else
                    {
                        string vodUrl;
                        if (vst_rtsp::addStream(stream->id, stream->name, url, vodUrl) == 0)
                        {
                            stream->live_proxy_url = url;
                            if (sensorInfo->type != SENSOR_TYPE_MMS_ONVIF)
                            {
                                stream->replay_url = vodUrl;
                            }

                            if (stream->isMainStream)
                            {
                                sensorInfo->m_notify = false;
                            }
                        }
                        else
                        {
                            if (stream->isMainStream)
                            {
                                LOG (error) << "Failed to get proxy URL for Main stream Id:" << stream->id << endl;
                                ret = -1;
                                return ret;
                            }
                        }
                    }
                }
            }
        }

        if (sensorInfo->type == SENSOR_TYPE_CSI && updateSensorSts)
        {
            if (sensorInfo->getSensorStatus() != SensorStatusEvent::SensorStatusOnline)
            {
                sensorInfo->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
            }
            sensorInfo->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));

            if (m_sensorControl)
            {
                m_sensorControl->setCacheSensorList();
            }
        }

        /* For standalone VST service, Sensors and its streams details will be updated in DB from here */
        if (GET_DEVICE_MANAGER()->needRtspServer)
        {
            vst_common::updateSensorDetailsToDB(deviceManager->id, sensorInfo);
        }
    }
    else
    {
        LOG(error) << "Device is error : " << sensorInfo->name << endl;
    }
    return ret;
}

std::string SensorManagement::addStream(shared_ptr<StreamInfo> stream)
{
    if (stream->isMainStream)
    {
        if (GET_DEVICE_MANAGER()->needRecording == true)
        {
            int ret_val = vst_recorder::addStream(stream->id, stream->live_proxy_url);
            if (ret_val != 0)
            {
                LOG(error) << "Recorder addstream failed for stream: " << stream->name << endl;
            }
        }

#ifdef JETSON_PLATFORM
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
        if (GET_CONFIG().enable_ipc_path)
        {
            DecoderPool* pool = DecoderPool::getInstance();
            if (pool != nullptr)
            {
                std::string url = stream->live_proxy_url;
                shared_ptr<GstNvVideoDecoder> gst_decoder = pool->getDecoder(url);
                std::map<std::string, std::string, std::less<>> opts;
                opts["peerid"] = url;
                opts["framerate"] ="30";
                if (gst_decoder == nullptr)
                {
                    LOG(warning) << "Decoder not found for " << secureUrlForLogging(url) << " so create new decoder instance..." << endl;
                    pool->addStream(url, opts);
                    gst_decoder = pool->getDecoder(url);
                }
                bool ret = false;
                if(gst_decoder)
                {
                    gst_decoder->setNeedSharedStream();
                    dec_result result = pool->tryDecoderStart(gst_decoder, url);
                    ret = result.first;
                }
                if (ret == false)
                {
                    LOG(error) << "Error in Creating Pipeline" << endl;
                    pool->removeStream(url);
                    throw std::invalid_argument( "Error in Creating Pipeline" );
                }
                gst_decoder->play();
                gst_decoder->registerDecoderPlayingStatusListener(this);
            }
            std::string socket_name = GET_CONFIG().ipc_socket_path + stream->id;
            socket_name = "ipc://" + socket_name;
            return socket_name;
        }
#endif
#endif
    }
    return "";
}

std::shared_ptr<DeviceManager> SensorManagement::getDeviceManagerObject()
{
    if (m_deviceManager.get() == nullptr)
    {
        return nullptr;
    }
    return m_deviceManager;
}

int SensorManagement::addSensorManually(shared_ptr<SensorInfo>& sensorInfo, string& response)
{
    if(sensorInfo.get())
    {
        if (sensorInfo->type == SENSOR_TYPE_ONVIF)
        {
            string name = sensorInfo->name;
            if (m_sensorMonitoring)
            {
                if(m_sensorMonitoring->searchSensor(*sensorInfo) == -1)
                {
                    LOG(error) << "Searching sensor failed: " << sensorInfo->ip << endl;
                    response = "Device not found";
                    return -1;
                }
            }
            sensorInfo->name = name.empty() ? sensorInfo->name : name; // restore the camera name given by user.
        }
        if (m_sensorMonitoring)
        {
            if (m_sensorMonitoring->onSensorFound(*sensorInfo) != 0)
            {
                LOG(error) << "onSensorFound failed" << endl;
                return -1;
            }
        }
        // return 0 for remote sensor
        if (sensorInfo->type == SENSOR_TYPE_REMOTE)
        {
            return 0;
        }
        // Otherwise check status code
        if(sensorInfo->getHttpErrorStatus().first != 200)
        {
            response = sensorInfo->getHttpErrorStatus().second;
            return -1;
        }
    }
    return 0;
}

int SensorManagement::deleteSensor(const string sensor_id, bool isReqFromCloudDevice, bool isReqFromEdgeDevice)
{
    int ret = 0;
    shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if (deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        ret = -1;
        return ret;
    }

    shared_ptr<SensorInfo> sensor = deviceManager->getSensorInfo(sensor_id);
    if (sensor == nullptr)
    {
        LOG(error) << "Failed to delete sensor" << endl;
        ret = -1;
        return ret;
    }

    // Publish camera_remove for every sensor type, including SENSOR_TYPE_FILE.
    // SDR routes the event to streamprocessing-ms's proxy/delete handler, which
    // cleans up the on-disk recording for file-type uploads.
    vst_common::notifySensorStatusEvent(SensorStatusOffline, sensor);

    if ((!isReqFromEdgeDevice) && (m_sensorControl != nullptr) && (!m_sensorControl->deleteSensor(sensor)))
    {
        LOG(error) << "Failed to delete sensor" << endl;
        ret = -1;
    }
    if (sensor)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            shared_ptr<StreamInfo> stream = streams[j];
            if (stream->isMainStream)
            {
                if (GET_DEVICE_MANAGER()->needRecording == true)
                {
                    vst_recorder::removeStream(stream->id);
                }
#ifdef JETSON_PLATFORM
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
                if (GET_CONFIG().enable_ipc_path)
                {
                    DecoderPool* pool = DecoderPool::getInstance();
                    shared_ptr<GstNvVideoDecoder> gst_decoder = pool->getDecoder(stream->live_proxy_url);
                    if (gst_decoder)
                    {
                        gst_decoder->deregisterDecoderPlayingStatusListener(this);
                        gst_decoder->destroy(true);
                        pool->removeStream(stream->live_proxy_url);
                    }
                }
#endif
#endif
            }
        }
        std::lock_guard<std::mutex> removedListMutex(m_userRemovedListMutex);
        m_userRemovedList.push_back(sensor->ip);
    }

    deleteSensorDetails(sensor_id);
    deviceManager->deleteSensor(sensor_id); // remove entry from cache

    /* Remove sensor from the remote-vst as well */
    if ((isReqFromCloudDevice == false) && (!GET_CONFIG().remote_vst_address.empty()))
    {
        vst_common::removeSensorFromRemoteDevice(sensor_id);
    }
    return ret;
}

VmsErrorCode SensorManagement::addSensorToEdgeVst(const Json::Value& sensorInfo)
{
    if (m_sensorControl)
    {
        return m_sensorControl->addSensor(sensorInfo);
    }
    return VmsErrorCode::VMSInternalError;
}

int SensorManagement::rebootSensorDiscovery()
{
    int ret = -1;
    if (m_sensorMonitoring)
    {
        ret = m_sensorMonitoring->restartDiscovery();
    }
    return ret;
}

void SensorManagement::notifyVmsRedinessEvent()
{
    Json::Value payload, event;
    event["service_status"] = "init_ready";
    payload["created_at"] = getCurrentTime();
    payload["source"] = "vst";
    payload["alert_type"] = "service_status_change";
    payload["event"] = event;
    INotificationInterface* notifier = NotificationFactory::CreatePlatformNotification();
    if (notifier)
    {
        notifier->sendMessage(payload);
    }
    else
    {
        LOG(error) << "Notification Manager instance is not created" << endl;
    }
    LOG(info) << payload.toStyledString() << endl;
}

void SensorManagement::notifyVmsExitEvent()
{
    Json::Value payload, event;
    event["service_status"] = "exiting";
    payload["created_at"] = getCurrentTime();
    payload["source"] = "vst";
    payload["alert_type"] = "service_status_change";
    payload["event"] = event;
    INotificationInterface* notifier = NotificationFactory::CreatePlatformNotification();
    if (notifier)
    {
        notifier->sendMessage(payload);
    }
    else
    {
        LOG(error) << "Notification Manager instance is not created" << endl;
    }
    LOG(info) << payload.toStyledString() << endl;
    if (notifier)
    {
        NotificationFactory::DeletePlatformNotification();
    }
}

VmsErrorCode SensorManagement::replaceSensor(const string& old_sensor_id, const string& new_sensor_id)
{
    SensorDetailsDBColumns old_details =  GET_DB_INSTANCE()->readSensorDetails("", old_sensor_id);
    SensorDetailsDBColumns details = GET_DB_INSTANCE()->readSensorDetails("", new_sensor_id);
    std::vector<SensorStreamsDBColumns> substream_details = GET_DB_INSTANCE()->readAllStreamsForGivenSensorID(new_sensor_id);
    details.sensor_id_value = old_sensor_id;
    string always_recording =  GET_DB_INSTANCE()->readStreamProperty(old_sensor_id, SensorStreamsDBColumns::isAlwaysRecording);

    shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if (deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    if (GET_DEVICE_MANAGER()->needRecording == true)
    {
        vst_recorder::removeStream(old_sensor_id);   // stop recording, if recording
    }

    shared_ptr<SensorInfo> old_sensor = deviceManager->getSensorInfo(old_sensor_id);
    shared_ptr<SensorInfo> new_sensor = deviceManager->getSensorInfo(new_sensor_id);

    deleteSensorDetails(old_sensor_id);
    deleteSensorDetails(new_sensor_id);
    deviceManager->deleteSensor(old_sensor_id); // remove entry from cache

    new_sensor->id = old_sensor_id;
    deviceManager->replaceSensor(old_sensor_id, new_sensor_id);

    int ret = GET_DB_INSTANCE()->insertRowSensorDetails(details);
    if ( ret == -1)
    {
        LOG(error) << "Error updating Camera details into DB" << endl;
    }

    // Initialise streams
    vector<shared_ptr<StreamInfo>> streams = new_sensor->getStreams();
    for (uint32_t j = 0; j < streams.size(); j++)
    {
        shared_ptr<StreamInfo> stream = streams[j];
        string actual_stream_id = stream->id;
        stream->sensorId = stream->id = old_sensor_id;
        if (!stream->isMainStream)
        {
            stream->id += "-" + stream->settings.token.profileToken;
        }
        stream->live_proxy_url = "";
        for (auto& row: substream_details)
        {
            if (row.stream_id_value == actual_stream_id)
            {
                row.sensor_id_value = old_sensor_id;
                row.stream_id_value = stream->id;
                if (row.isMainStream_value == "true")
                {
                    row.isAlwaysRecording_value = always_recording;
                }
                GET_DB_INSTANCE()->insertRowStream(row);
                break;
            }
        }
    }

    getAndAddProxyUrl(new_sensor, deviceManager->type);

    shared_ptr<StreamInfo> stream = new_sensor->streams[0];
    if (stream && GET_DEVICE_MANAGER()->needRecording == true)
    {
        vst_recorder::addStream(stream->id, stream->live_proxy_url);
    }
    return VmsErrorCode::NoError;
}

void SensorManagement::deleteSensorDetails(const string& sensor_id)
{
    shared_ptr<DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if (deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        return;
    }

    shared_ptr<SensorInfo> sensor = deviceManager->getSensorInfo(sensor_id);

    // Remove streams
    if(sensor != nullptr)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            shared_ptr<StreamInfo> stream = streams[j];
            DecoderPool::getInstance()->removeStream(stream->live_proxy_url);

            if (stream->stream_type == StreamType::Native)
            {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
                NativeStreamMonitor* streamMonitor = NativeStreamMonitor::getInstance();
                streamMonitor->removeNativeStream(stream);
#endif
            }
            else
            {
                if (m_deviceManager && m_deviceManager->needStreamMonitoring)
                {
                    StreamMonitor* streamMonitor = StreamMonitor::getInstance();
                    streamMonitor->removeStream(stream);
                }
            }

            // delete proxy url, this should happen at the end.
            if (sensor->type != SENSOR_TYPE_WEBRTC && sensor->type != SENSOR_TYPE_UDP && sensor->type != SENSOR_TYPE_CSI) // WAR to be removed.
            {
                if (GET_DEVICE_MANAGER()->needRtspServer == true)
                {
                    if (vst_rtsp::removeStream(stream->id) != 0)
                    {
                        LOG(error) << "Failed to remove proxy url from RTSP Proxy server" << endl;
                    }
                }
            }

            /* Free ports in case they are allocated */
            if (stream->stream_type == StreamType::Udp)
            {
                vector<string> url_info = splitString(stream->live_url, ":");
                for (size_t k = 1; k < url_info.size(); k++)
                {
                    int port = stringToInt(url_info[k], 0);
                    UdpClientPool::getInstance()->freeUdpPort(port);
                }
            }
        }
    }

    GET_DB_INSTANCE()->deleteSensorDetails(sensor_id); // remove DB entry
}

bool SensorManagement::isRemovedByUser(const SensorInfo& sensorInfo)
{
    std::lock_guard<std::mutex> removedListMutex(m_userRemovedListMutex);
    std::vector<std::string>::iterator it;
    it = std::find (m_userRemovedList.begin(), m_userRemovedList.end(), sensorInfo.ip);
    if (it != m_userRemovedList.end())
    {
        return true;
    }
    return false;
}

vector<shared_ptr<SensorInfo>> SensorManagement:: getSensors()
{
    vector<shared_ptr<SensorInfo>> sensor_list;
    std::shared_ptr< DeviceManager> deviceManager = GET_DEVICE_MANAGER();
    if (deviceManager == nullptr)
    {
        LOG(error) << "Invalid deviceMngr object" << endl;
        return sensor_list;
    }
    vector<shared_ptr<SensorInfo>> list = deviceManager->getSensorList();
    for (auto sensor: list)
    {
        if (sensor && (sensor->type == SENSOR_TYPE_ONVIF || sensor->type == SENSOR_TYPE_CSI))
        {
            sensor_list.push_back(sensor);
        }
    }
    return sensor_list;
}

std::shared_ptr<SensorControl> SensorManagement::getSensorControl()
{
    if (m_sensorControl == nullptr)
    {
        return nullptr;
    }

    return m_sensorControl;
}

SensorMonitoring* SensorManagement::startSensorDiscovery()
{
    if (m_sensorMonitoring == nullptr)
    {
        std::shared_ptr<DeviceManager> deviceManager = getDeviceManagerObject();
        if (deviceManager->m_sensorDiscoveryObjectPairList.size() > 0)
        {
            m_sensorMonitoring.reset(new SensorMonitoring(this, m_deviceManager->m_sensorDiscoveryObjectPairList));
            return m_sensorMonitoring.get();
        }
        return nullptr;
    }
    else
    {
        return m_sensorMonitoring.get();
    }
}
