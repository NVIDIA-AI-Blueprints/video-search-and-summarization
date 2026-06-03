/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include<iostream>
#include<map>
#include <tuple>
#include<vector>
#include<thread>
#include<queue>
#include<mutex>
#include <condition_variable>
#include<unistd.h>
#include "device_manager.h"
#include "config.h"
#include <curl/curl.h>

#include "media_consumer.h"
#include "mm_utils.h"
#include "syncobject.h"
#include "media_producer.h"

using namespace std;
using namespace nv_vms;

namespace nv_vms
{
typedef
struct StreamEncParam
{
    string codec;
    int width;
    int height;
    StreamEncParam() : width(0)
                    , height(0)
    {}
} StreamEncParam;
class IStreamStatusEvent
{
public:
    virtual void onStreamStatusChange(const string &url, const StreamStatus newStatus, StreamEncParam& details) = 0;
    virtual void onDecoderPlayingStatus(const string &url) {}
};

class StreamMonitor : public IMediaDataProducer
{
private:
    static StreamMonitor* m_pInstance;
    StreamMonitor();
    void startMonitor();

public:
    static StreamMonitor* getInstance()
    {
        static std::once_flag initFlag;
        std::call_once(initFlag, []() {
            m_pInstance = new StreamMonitor();
        });
        return m_pInstance;
    }

    static void deleteInstance()
    {
        delete m_pInstance;
        m_pInstance = nullptr;
    }
    ~StreamMonitor();

    // Public member function declarations
    // IMediaDataProducer interface implementation
    bool start() override;
    void stop() override;
    bool isRunning() const override;
    eMediaType getProducerMediaType() const override;
    std::string getSourceIdentifier() const override;
    size_t getConsumerCount() const override;
    bool hasConsumers() const override;
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier = "") override;
    // Overloaded registerConsumer for time-range playback
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier, const std::string& startTime, const std::string& endTime) override;
    void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier = "", bool doNotRemoveClient = false) override;
    void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) override;
    void distributeToConsumers(FrameParams& frameParams) override;
    
    // Legacy methods for backward compatibility
    void registerDataCallback(std::string& url, shared_ptr<IMediaDataConsumer> consumer);
    void deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& url, bool doNotRemoveClient = false);
    bool checkIfStreamAlive(const std::string& inUrl);
    std::vector<std::string> getListofAliveStreams();
    void addUriListForLivenessMonitor(const std::vector<std::string>& inList);
    void removeUriListFromLivenessMonitor(const std::vector<std::string>& inList);

    void sendStatusEvent(const string &url, StreamStatus status, StreamEncParam& details);
    void removeStream(std::shared_ptr<StreamInfo> stream);
    void removeStream(const string& stream_id);
    void addStream(std::shared_ptr<StreamInfo> stream);
    void addStream(vector<std::shared_ptr<StreamInfo>> streams);
    void enableTcpStreaming(const string& uri);
    std::vector<shared_ptr<IMediaDataConsumer>> getConsumers(const string& url);
    void getQosInfo(Json::Value &response);
    std::string getUriName(const std::string &url);
    std::shared_ptr<StreamInfo> getStreamInfoForUrl(const std::string &url);
    std::map<string, media_info, std::less<>> getSupportedSubSessions(const std::string& url);
    shared_ptr<StreamInfo> getStreamInfo(const std::string &url, bool isProxyUrl);

    class UrlInfo
    {
        public:
            string m_url;
            string m_devName;
            string m_streamId;
            unsigned m_frameRate;
            bool m_isMainStream;
            string m_livenessUrl;
            bool m_rtspUrlReachable;
    };

    Json::Value m_qosResponse;

private:
    DeviceConfig m_vmsConfig;
    CURLM *m_curlMultiHandle;
    std::thread m_streamMonitorThread;
    std::thread m_qosMeasurementThread;
    std::map<std::string, StreamStatus, std::less<>> m_livenessMonitorList;
    std::vector<std::tuple<CURL*, std::string, bool>> m_curlList;
    std::mutex m_livenessMonitorListMutex;

    bool m_exit;
    std::mutex  m_monitorThreadMutex;
    std::condition_variable m_cvMonitorThread;

    bool m_enableQoS = false;
    SyncObject m_qosThreadSync = {};
    std::map<std::string, std::vector<shared_ptr<IMediaDataConsumer>>, std::less<>> m_streamConsumers;
    mutable std::mutex  m_streamConsumerLock;

    // Private member functions declarations
    std::map<std::string, StreamStatus, std::less<>> getLivenessMonitorStreamList();
    void livenessMonitorTask();
    void updateUriStatus(const std::string& uri, StreamStatus status, CURLcode errorCode);
    void addCurlRequest(const std::string& url);
    void removeCurlRequest(CURL *curl);
    bool isCurlResponsePendingForUri(const std::string& url);
    void setCurlResponsePendingStatus(CURL *curl, bool isResponsePending);
    std::string getUriByUsingCurlHandle(const CURL *curl);
    void notifyStreamStatus(const StreamStatus& status, const std::string& camera_id);
    std::vector<UrlInfo> getQosMonitorStreamList();
    void qosMeasurementTask();
    void cleanupQoSThread();
    void restartQoSMonitoringTask();
    bool isRtspSourceDestroyed(const string& url);
    void waitForCompleteRemoval(const string& url);

    std::vector<StreamMonitor::UrlInfo> m_qosMonitorList;
    std::mutex  m_qosMonitorListMutex;
    bool m_exitQosThread = false;

};

} //nv_vms

