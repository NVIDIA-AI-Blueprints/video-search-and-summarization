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

#include "liveMedia.hh"
#include "rtspconnectionclient.h"
#include "livevideosource.h"
#include "stream_monitor.h"
#include "stream_event_manager.h"
#include "logger.h"
#include "macros.h"
#include "prometheus_client/prometheus_client.h"
#include <atomic>
#include <algorithm>
#include <vector>
#include <cctype>
#include "rtspserver.h"
#include "mm_utils.h"
#include "network_utils.h"
#include "modules_apis.h"
#include "vstmodule.h"
#include "stats.h"
#include <fstream>
#include <sstream>
#include <sys/statvfs.h>
#include <unistd.h>

//#define DUMP_QOS_IN_NON_CSV_FORMAT
constexpr int MAX_WAIT_MSECS = 1000; /* Wait max. 1 second */
constexpr long CURL_REQUEST_TIMEOUT = 10L;  /* curl request timeout in seconds */
constexpr int DEFAULT_AUDIO_CHANNELS = 1;  /* default audio channels */
constexpr int DEFAULT_FREQUENCY = 8000;  /* default audio frequency */
constexpr int DEFAULT_CODEC_DATA = 1408;  /* default audio codec_data */

/* The DEFAULT_CODEC_DATA is  populated from below table
** 8k  single channel 1588 dual channel 1590
** 16k single channel 1408 dual channel 1410
** 32k single channel 1288 dual channel 1290  
** 24K single channel 1308 dual channel 1310
** 22.05K mono 1388 dual channel 1390
** 44.1k  mono 1208 dual Channel 1210
** The values are as per standards defined in ISO/IEC 14496-3
*/

class QosRtspClient;

StreamMonitor*  StreamMonitor ::m_pInstance = nullptr;


StreamMonitor::StreamMonitor()
    : m_curlMultiHandle(nullptr)
    , m_exit(false)
    , m_enableQoS(false)
    , m_exitQosThread(false)
{
    LOG(info) << "StreamMonitor::StreamMonitor" << endl;
    m_vmsConfig = GET_CONFIG();
    if (m_vmsConfig.enable_qos_monitoring)
    {
        LOG(info) << "QoS monitoring is enabled" << endl;
        m_enableQoS = true;
    }
    startMonitor();
}
void StreamMonitor::startMonitor()
{
    LOG(info) << "StreamMonitor::startMonitor" << endl;
    // Initialize status of each url as unknown.
    std::map<std::string, StreamStatus, std::less<>> uriList = getLivenessMonitorStreamList();
    std::map<std::string, StreamStatus, std::less<>>::iterator it;
    for (it = uriList.begin(); it != uriList.end(); ++it)
    {
        it->second = STREAM_STATUS_UNKNOWN;
    }
    /* Start the liveness monitor & qos monitor threads */
    m_streamMonitorThread = std::thread([this] { this->livenessMonitorTask(); });
    m_qosMeasurementThread = std::thread([this] { this->qosMeasurementTask(); });

    StreamEventManager::getInstance().start();

    if (m_enableQoS)
    {
        nv_logger::Logger *mlogger = nv_logger::Logger::getInstance();
        mlogger->setupQoSLogging();
    }
}

StreamMonitor::~StreamMonitor()
{
    LOG(info) << "~StreamMonitor" << endl;
    m_exit = true;
    m_exitQosThread = true;
    StreamEventManager::getInstance().stop();

    /* Terminate the liveness monitor thread */
    m_cvMonitorThread.notify_all();
    if (m_streamMonitorThread.joinable())
    {
        m_streamMonitorThread.join();
    }
    m_livenessMonitorList.clear();

    /* Terminate the QoS monitor thread */
    m_qosThreadSync.signal();
    if (m_qosMeasurementThread.joinable())
    {
        m_qosMeasurementThread.join();
    }

}

void StreamMonitor::addUriListForLivenessMonitor(const std::vector<std::string>& inList)
{
    std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
    for (auto const& uri : inList)
    {
        std::map<std::string, StreamStatus, std::less<>>::iterator it = m_livenessMonitorList.find(uri);
        if (it != m_livenessMonitorList.end())
        {
            LOG(verbose) << "url already present in the list: " << secureUrlForLogging(uri) << endl;
        }
        else
        {
            m_livenessMonitorList.insert({uri, STREAM_STATUS_UNKNOWN});
        }
    }
}

void StreamMonitor::removeUriListFromLivenessMonitor(const std::vector<std::string>& inList)
{
    std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
    for (auto const& uri : inList)
    {
        std::map<std::string, StreamStatus, std::less<>>::iterator it = m_livenessMonitorList.find(uri);
        if (it != m_livenessMonitorList.end())
        {
            m_livenessMonitorList.erase (it);
        }
        else
        {
            LOG(verbose) << "url not found in the list: " << secureUrlForLogging(uri) << endl;
        }
    }
}

std::map<std::string, StreamStatus, std::less<>> StreamMonitor::getLivenessMonitorStreamList()
{
    std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
    return m_livenessMonitorList;
}

bool StreamMonitor::checkIfStreamAlive(const std::string& inUrl)
{
    bool isUrlInMonitorList = false;
    {
        std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
        std::map<std::string, StreamStatus, std::less<>>::iterator it;
        it = m_livenessMonitorList.find(inUrl);
        if (it != m_livenessMonitorList.end())
        {
            isUrlInMonitorList = true;
            if(it->second == STREAM_STATUS_ONLINE || it->second == STREAM_STATUS_STREAMING)
            {
                return true;
            }
        }
    }

    if (!isUrlInMonitorList)
    {
        CURLcode errCode = CURLE_OK;
        CURL *curl = curl_easy_init();
        if (curl)
        {
            errCode = curl_easy_setopt(curl, CURLOPT_URL, inUrl.c_str());
            CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

            errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, CURL_REQUEST_TIMEOUT);
            CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

            errCode = curl_easy_setopt(curl, CURLOPT_RTSP_REQUEST, CURL_RTSPREQ_OPTIONS);
            CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

            // Execute the rtsp oprion request.
            errCode = curl_easy_perform(curl);
            CURL_CHECK_ERROR(curl_easy_perform, errCode, false)

            curl_easy_cleanup(curl);
            return true;
        }
    }
    return false;
}

std::vector<std::string> StreamMonitor::getListofAliveStreams()
{
    std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
    std::vector<std::string> aliveStreamList;
    std::map<std::string, StreamStatus, std::less<>>::iterator it;
    for (it = m_livenessMonitorList.begin(); it != m_livenessMonitorList.end(); ++it)
    {
        if(it->second == STREAM_STATUS_ONLINE || it->second == STREAM_STATUS_STREAMING)
            aliveStreamList.push_back(it->first);
    }
    return aliveStreamList;
}

void StreamMonitor::addCurlRequest(const std::string& url)
{
    CURLcode errCode = CURLE_OK;
    CURLMcode retM = CURLM_OK;
    bool isResponsePending = true;

    CURL *curl = curl_easy_init();
    if (curl)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        CURL_CHECK_ERROR2(curl_easy_setopt, errCode)

        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, CURL_REQUEST_TIMEOUT);
        CURL_CHECK_ERROR2(curl_easy_setopt, errCode)

        errCode = curl_easy_setopt(curl, CURLOPT_RTSP_REQUEST, CURL_RTSPREQ_OPTIONS);
        CURL_CHECK_ERROR2(curl_easy_setopt, errCode)

        retM = curl_multi_add_handle(m_curlMultiHandle, curl);
        CURL_CHECK_ERROR2(curl_multi_add_handle, retM)

        m_curlList.push_back(std::make_tuple(curl, url, isResponsePending));
    }
}

void StreamMonitor::removeCurlRequest(CURL *curl)
{
    CURLMcode retM = CURLM_OK;
    std::vector<std::tuple<CURL*, std::string, bool>>::iterator it;
    for(it = m_curlList.begin(); it != m_curlList.end(); ++it)
    {
        if(get<0>(*it) == curl)
        {
            m_curlList.erase(it);
            break;
        }
    }
    retM = curl_multi_remove_handle(m_curlMultiHandle, curl);
    if (retM != CURLM_OK)
    {
        LOG(error) << "error: curl_multi_remove_handle() failed:" << retM << endl;
    }
    curl_easy_cleanup(curl);
}

bool StreamMonitor::isCurlResponsePendingForUri(const std::string& url)
{
    for(const auto &i : m_curlList)
    {
        if(get<1>(i) == url && get<2>(i) == true)
        {
            return true;
        }
    }
    return false;
}

void StreamMonitor::setCurlResponsePendingStatus(CURL *curl, bool isResponsePending)
{
    for(auto &i : m_curlList)
    {
        if(get<0>(i) == curl)
        {
            get<2>(i) = isResponsePending;
        }
    }
}

std::string StreamMonitor::getUriByUsingCurlHandle(const CURL *curl)
{
    std::string url;
    for(const auto &i : m_curlList)
    {
        if(get<0>(i) == curl)
        {
            url = get<1>(i);
            break;
        }
    }
    return url;
}

void StreamMonitor::livenessMonitorTask()
{
    CURL *curl = nullptr;
    CURLMsg *msg = nullptr;
    CURLcode return_code = CURLE_OK;
    int still_running = 0, msgs_left = 0, repeats = 0;

    LOG(info) << "Started the thread livenessMonitorTask" << endl;
    m_curlMultiHandle = curl_multi_init();
    if(!m_curlMultiHandle)
    {
        LOG(error) << "curl_multi_init failed" << endl;
        return;
    }

    while (m_exit == false)
    {
        std::map<std::string, StreamStatus, std::less<>> m_MonitorList = getLivenessMonitorStreamList();
        for (auto const& uri : m_MonitorList)
        {
            // Create curl handle & rtsp option request for each url.
            if(m_curlMultiHandle)
            {
                if(!isCurlResponsePendingForUri(uri.first))
                {
                    addCurlRequest(uri.first);
                }
            }
        }

        do
        {
            int numfds = 0;
            CURLMcode errCode = CURLM_OK;
            errCode = curl_multi_perform(m_curlMultiHandle, &still_running);
            if(errCode != CURLM_OK)
            {
                LOG(error) << "error: curl_multi_perform() failed." << errCode << endl;
                break;
            }
            errCode = curl_multi_wait(m_curlMultiHandle, nullptr, 0, MAX_WAIT_MSECS, &numfds);
            if(errCode != CURLM_OK)
            {
                LOG(error) << "error: curl_multi_wait() failed." << errCode << endl;
                break;
            }

            if(!numfds)
            {
                repeats++; /* count number of repeated zero numfds */
                if(repeats > 1)
                {
                    usleep(100*1000); /* sleep 100 milliseconds */
                }

                while ((msg = curl_multi_info_read(m_curlMultiHandle, &msgs_left)))
                {
                        if (msg->msg == CURLMSG_DONE)
                        {
                            curl = msg->easy_handle;
                            return_code = msg->data.result;
                            // Ignore few curl errors
                            if (return_code == CURLE_RTSP_CSEQ_ERROR || return_code == CURLE_RECV_ERROR)
                            {
                                return_code = CURLE_OK;
                            }
                            if(return_code != CURLE_OK)
                            {
                                LOG(error) << "CURL error:" << curl_easy_strerror(return_code) << " [" << return_code << "] for url:" << secureUrlForLogging(getUriByUsingCurlHandle(curl)) << endl;
                                setCurlResponsePendingStatus(curl, false);
                                updateUriStatus(getUriByUsingCurlHandle(curl), STREAM_STATUS_OFFLINE, return_code);

                            }
                            else
                            {
                                //LOG(info) << "Response[" << return_code << "] " << curl_easy_strerror(return_code) << " for url:" << getUriByUsingCurlHandle(curl) << endl;
                                setCurlResponsePendingStatus(curl, false);
                                updateUriStatus(getUriByUsingCurlHandle(curl), STREAM_STATUS_ONLINE, return_code);
                            }
                            removeCurlRequest(curl);
                        }
                        else
                        {
                            LOG(error) << "error: after curl_multi_info_read(), CURLMsg:" << msg->msg << endl;
                        }
                        still_running = 0;
                }
            }
            else
            {
                repeats = 0;
            }
        }while(still_running);
        // Stream monitor Sleep for 5seconds or untill get notified.
        {
            std::unique_lock<std::mutex> lck(m_monitorThreadMutex);
            m_cvMonitorThread.wait_for(lck,std::chrono::seconds(m_vmsConfig.stream_monitor_interval_secs));
        }
    }
    curl_multi_cleanup(m_curlMultiHandle);
    LOG(info) << "Exited the thread livenessMonitorTask" << endl;
}

void StreamMonitor::sendStatusEvent(const string &url, StreamStatus status, StreamEncParam& details)
{
    StreamEventManager::getInstance().sendEvent(url, status, details);
}

void StreamMonitor::notifyStreamStatus(const StreamStatus& status, const std::string& camera_id)
{

}

/* =============Implementation of QoS Measurement ====================== */


constexpr const char* RTP_UDP_TRANSPORT_MODE = "udp";
constexpr const char* RTP_DATA_TIMEOUT_PERIOD_SEC = "10";
constexpr int QOS_COLLECT_INTERVAL_SEC = 1;
constexpr int URL_BLACKLIST_PERIOD_SEC = 60;  // 1min
constexpr int QOS_DATA_DUMP_INTERVAL_SEC = 10;
constexpr int RTSP_CONNECTION_MAX_RETRY_COUNT = 3;
constexpr int RTSP_CONNECTION_RETRY_INTERVAL_SEC = 5;

 // forwad declarations
class QosMeasurementRecord;
class QosRtspClient;

// function declarations
static void qosDataCollector(void* clientData);
static void qosDataCollectorBackend(void* clientData);
void printQoSData();
void printBackendQoSData();

std::map<string, shared_ptr<QosMeasurementRecord>, std::less<>> g_records;
std::map<string, QosRtspClient *, std::less<>> g_rtspSources;
std::map<string, struct timeval, std::less<>> g_blackList;
std::multimap<string, pair<string, string>, std::less<>> g_streamFailureCount;
std::mutex g_streamFailureMapMutex, g_qosDumpMutex, g_rtspSourceMutex;

// class QosMeasurementRecord
class QosMeasurementRecord
{
public:
    QosMeasurementRecord(string name, unsigned framerate)
        : m_rtpSourceProxy(nullptr),
        m_qosPeriodicTask(nullptr),
        m_backendQosPeriodicTask(nullptr),
        m_fName(name),
        m_frameRate(framerate),
        m_kbitsPerSecondMin(1e20), m_kbitsPerSecondMax(0),
        m_kBytesTotal(0.0),
        m_packetLossFractionMin(1.0), m_packetLossFractionMax(0.0),
        m_totNumPacketsReceived(0), m_totNumPacketsExpected(0),
        m_numPacketsLastIteration(0),
        m_backendKbitsPerSecondMin(1e20), m_backendKbitsPerSecondMax(0),
        m_backendKBytesTotal(0.0),
        m_backendPacketLossFractionMin(1.0), m_backendPacketLossFractionMax(0.0),
        m_backendTotNumPacketsReceived(0), m_backendTotNumPacketsExpected(0),
        m_backendNumPacketsLastIteration(0),
        m_prevMeasurementTime(0.0),
        m_isQosStarted(false),
        m_receivedFps(0.0), m_sumDiff(0),
        m_frameCount(0), m_avgFrameCount(0), m_instFps(0.0),
        m_recordedFrameId(0), m_total_frames_lost(0)
    {
        m_measurementStartTime = {};
        m_measurementEndTime = {};
        m_backendMeasurementStartTime = m_backendMeasurementEndTime = {};
        m_prevPts = {};
    }
    virtual ~QosMeasurementRecord()
    {
        LOG(info) << "~QosMeasurementRecord - " << m_fName << endl;
        stopQoS();
    }

    void startQoS(const string& url, RTPSource* rtpSrcProxy)
    {
        LOG(info) << "[streamMonitor] startQoS for " << m_fName << ", m_uri:" << m_uri << endl;
        struct timeval timeNow;
        gettimeofday(&timeNow, nullptr);
        m_measurementEndTime = m_measurementStartTime = m_prevPts = timeNow;
        m_backendMeasurementStartTime = m_backendMeasurementEndTime = timeNow;
        m_prevTime = std::chrono::duration_cast<std::chrono::milliseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();

        if (m_frameRate == 0)
        {
            // Set default frameRate in case if failed to read actual framerate.
            m_frameRate = DEFAULT_STREAM_FRAME_RATE;
        }

        m_uri = url;
        m_rtpSourceProxy = rtpSrcProxy;
        if (m_rtpSourceProxy)
        {
            RTPReceptionStatsDB::Iterator statsIter(m_rtpSourceProxy->receptionStatsDB());

            // Assume that there's only one SSRC source (usually the case):
            RTPReceptionStats* stats = statsIter.next(True);
            if (stats != nullptr)
            {
                m_kBytesTotal = stats->totNumKBytesReceived();
                m_totNumPacketsReceived = stats->totNumPacketsReceived();
                m_totNumPacketsExpected = stats->totNumPacketsExpected();
                m_numPacketsLastIteration = m_totNumPacketsReceived;
            }
            // Do this again later:
            m_qosPeriodicTask = m_rtpSourceProxy->envir().taskScheduler().scheduleDelayedTask(
                GET_CONFIG().qos_data_capture_interval_sec * 1000000, (TaskFunc*)qosDataCollector, (void*)this);

            /* Collect QoS data from backend media session in it's own env thread */
            RTPSource *rtpSourceBackend = getBackendRtpSource();
            if (rtpSourceBackend)
            {
                m_backendQosPeriodicTask = rtpSourceBackend->envir().taskScheduler().scheduleDelayedTask(
                    GET_CONFIG().qos_data_capture_interval_sec * 1000000,
                    (TaskFunc*)qosDataCollectorBackend, (void*)this);
            }
            m_isQosStarted = true;
        }

    }

    void stopQoS()
    {
        if (m_isQosStarted == false)
        {
            return;
        }
        LOG(info) << "[streamMonitor] stopQoS for " << m_fName << endl;
        if (m_rtpSourceProxy && m_qosPeriodicTask)
        {
            m_rtpSourceProxy->envir().taskScheduler().unscheduleDelayedTask(m_qosPeriodicTask);
            m_qosPeriodicTask = nullptr;
        }
        RTPSource *rtpSourceBackend = getBackendRtpSource();
        if (rtpSourceBackend && m_backendQosPeriodicTask)
        {
            rtpSourceBackend->envir().taskScheduler().unscheduleDelayedTask(m_backendQosPeriodicTask);
            m_backendQosPeriodicTask = nullptr;
        }
        m_isQosStarted = false;
        m_rtpSourceProxy = nullptr;
    }

    string getDevName() { return m_fName; }
    void setDevName(string devName) { m_fName = devName; }
    void periodicQosData(struct timeval const& timeNow);
    void periodicQosDataBackend(struct timeval const& timeNow);
    void onFrame(struct timeval& pts, int64_t current_frameId);
    RTPSource* getBackendRtpSource();

public:
    string m_uri;
    RTPSource *m_rtpSourceProxy;
    TaskToken m_qosPeriodicTask;
    TaskToken m_backendQosPeriodicTask;
    string m_fName;
    unsigned m_frameRate;

public:
    struct timeval m_measurementStartTime, m_measurementEndTime;
    double m_kbitsPerSecondMin, m_kbitsPerSecondMax;
    double m_kBytesTotal;
    double m_packetLossFractionMin, m_packetLossFractionMax;
    unsigned m_totNumPacketsReceived, m_totNumPacketsExpected, m_numPacketsLastIteration;
    struct timeval m_backendMeasurementStartTime, m_backendMeasurementEndTime;
    double m_backendKbitsPerSecondMin, m_backendKbitsPerSecondMax;
    double m_backendKBytesTotal;
    double m_backendPacketLossFractionMin, m_backendPacketLossFractionMax;
    unsigned m_backendTotNumPacketsReceived, m_backendTotNumPacketsExpected, m_backendNumPacketsLastIteration;
    double m_prevMeasurementTime;
    bool m_isQosStarted;
    float m_receivedFps;
    std::atomic<unsigned long> m_sumDiff;
    struct timeval m_prevPts;
    unsigned m_frameCount, m_avgFrameCount;
    double m_instFps;
    uint64_t m_prevTime = 0;
    int64_t m_recordedFrameId;
    string m_missedFrames;
    int64_t m_total_frames_lost;
};

// Class QosRtspClient
class QosRtspClient : public VideoSource<RTSPConnection>
{
public:
    QosRtspClient(const std::string &uri, const std::string& name, const std::map<std::string, std::string, std::less<>> &opts);
    virtual ~QosRtspClient();

    struct FrameInfo
    {
        FrameInfo(uint8_t nalType, string media, string codec,
            unsigned char *buffer, ssize_t size, struct timeval presentationTime)
            : m_nalType(nalType)
            , m_media(media)
            , m_codec(codec)
            , m_size(size)
            , m_presentationTime(presentationTime)
            , m_currentFrameId(-1)
            , m_ptsFromServer(0)
        {
            gettimeofday(&m_latencyStartTime, nullptr);
            m_content.insert(m_content.end(), buffer, buffer + size);
        }

        void setFrameIdInfo(int64_t frameId, int64_t ts)
        {
            m_currentFrameId = frameId;
            m_ptsFromServer = ts;
        }
        uint8_t m_nalType;
        string m_media;
        string m_codec;
        std::vector<uint8_t> m_content;
        ssize_t m_size;
        struct timeval m_presentationTime;
        int64_t m_currentFrameId;
        int64_t m_ptsFromServer;
        struct timeval m_latencyStartTime;
    };

    static QosRtspClient *Create(const std::string &url, const std::string& name, const std::map<std::string, std::string, std::less<>> &opts)
    {
        return new QosRtspClient(url, name, opts);
    }

    void restartConnection(bool no_delay = false)
    {
        // Restart RTSP client after some time.
        std::unique_lock<std::mutex> lck(m_RTSPConnLock);
        m_exitFrameMonitorThread = true;
        m_frameQueueCv.notify_all();
        int delay = (no_delay == true) ? 0 : RTSP_CONNECTION_RETRY_INTERVAL_SEC;
        m_cvRTSPConnRestart.wait_for(lck,std::chrono::seconds(delay));
        m_retryCount++;
        m_isRestartRequired = true;
        LOG(info) << "Retrying rtsp connection for:" << m_name << ", retry count:" << m_retryCount << endl;
    }

    virtual void onConnectionTimeout(RTSPConnection &connection) override
    {
        LOG(error) << "[streamMonitor] onConnectionTimeout for " << m_name << ", url:" << secureUrlForLogging(m_uri) << endl;
        updateStreamError(m_uri, "connection timeout error");
        m_streamingStarted = false;
        restartConnection();
    }
    virtual void onDataTimeout(RTSPConnection &connection) override
    {
        LOG(error) << "[streamMonitor] onDataTimeout for " << m_name << ", url:" << secureUrlForLogging(m_uri) << endl;;
        updateStreamError(m_uri, "data timeout error");
        restartConnection(true);
    }
    virtual void onError(RTSPConnection &connection, const char *error_msg) override
    {
        LOG(error) << "[streamMonitor] onError for " << m_name << ", url:" << secureUrlForLogging(m_uri) <<  " error:" << error_msg;
        updateStreamError(m_uri, error_msg);
        m_streamingStarted = false;
        restartConnection();
    }
    virtual void onEOS(RTSPConnection &connection) override
    {
        LOG(info) << "[streamMonitor] onEOS for " << m_name << ", url:" << secureUrlForLogging(m_uri) << endl;
        m_streamingStarted = false;
        StreamMonitor* streamMonitor = StreamMonitor::getInstance();
        if (streamMonitor)
        {
            StreamEncParam details;
            streamMonitor->sendStatusEvent(m_uri, STREAM_STATUS_END_OF_STREAM, details);
        }
    }
    virtual void onPlaying(RTSPConnection &connection, MediaSession *session) override
    {
        LOG(info) << "[streamMonitor] onPlaying for " << m_name << ", url:" << secureUrlForLogging(m_uri) << endl;

        if (session == nullptr)
        {
            LOG(error) << "Media session is null, returning ..." << endl;
            return;
        }
        MediaSubsessionIterator iter(*session);
        MediaSubsession* subsession;
        while ((subsession = iter.next()) != nullptr)
        {
            RTPSource* src = subsession->rtpSource();
            if (src == nullptr) continue;

            string media = subsession->mediumName();
            string codec = subsession->codecName();
            if (media == "video")
            {
                std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
                std::map<std::string, shared_ptr<QosMeasurementRecord>, std::less<>>::iterator it = g_records.find(m_uri);
                if (it != g_records.end())
                {
                    RTPSource *rtpSourceProxy = nullptr;
                    rtpSourceProxy = subsession->rtpSource();

                    /* Start QoS dumping for video stream */
                    m_qosRecord = it->second;
                    if (m_qosRecord && rtpSourceProxy)
                    {
                        m_qosRecord->startQoS(m_uri, rtpSourceProxy);
                    }
                }
                m_sdpVideo = subsession->savedSDPLines();
                m_initFrames = getInitFrames(codec, m_sdpVideo.data());
            }
            if (media == "audio")
            {
                string sdp_audio = subsession->savedSDPLines();
                getAudioInfo(sdp_audio, codec);
            }
        }
        m_session = session;
    }

    bool onDataConsumer(std::shared_ptr<FrameInfo> frameInfo)
    {
        unsigned char *buffer = frameInfo->m_content.data();
        ssize_t size = frameInfo->m_size;
        struct timeval presentationTime = frameInfo->m_presentationTime;
        uint8_t fCurPacketNALUnitType = frameInfo->m_nalType;
        string media = frameInfo->m_media;
        string codec = frameInfo->m_codec;
        int64_t ptsFromServer = frameInfo->m_ptsFromServer;
        FrameParams frame_params;

        // Send the frame to all the registered consumers.
        std::vector<shared_ptr<IMediaDataConsumer>> consumers;
        {
            std::lock_guard<std::mutex> recordLock(m_RTSPConsumerLock);
            consumers = m_consumers;
        }
        for (shared_ptr<IMediaDataConsumer> consumer : consumers)
        {
            frame_params.m_media   = media;
            frame_params.m_codec   = codec;
            frame_params.m_presentationTime = presentationTime;
            memcpy(&frame_params.m_latencyStartTime, &frameInfo->m_latencyStartTime, sizeof(struct timeval));
            eMediaType consumerType = consumer->getConsumerMediaType();
            if (media == "video" && (consumerType == MediaTypeVideo || consumerType == MediaTypeAudioVideo))
            {
                if (ptsFromServer)
                {
                    presentationTime.tv_sec = ptsFromServer / 1000000;
                    presentationTime.tv_usec = (ptsFromServer % 1000000);
                }
                // Attach sps-pps nals with first IDR-frame.
                if (consumer->m_startConsuming == false)
                {
                    if (isIDRFrame(fCurPacketNALUnitType, codec))
                    {
                        if (consumer->isSpsPpsAvailable() == false)
                        {
                            std::vector<std::vector<uint8_t>> initFrames = m_initFrames;
                            for (auto frame : initFrames)
                            {
                                frame_params.m_buffer  = frame.data();
                                frame_params.m_size    = frame.size();
                                consumer->onFrame(frame_params);
                            }
                        }
                    }
                }
                frame_params.m_buffer  = buffer;
                frame_params.m_size    = size;
                consumer->onFrame(frame_params);
            }
            else if (media == "audio" && (consumerType == MediaTypeAudio || consumerType == MediaTypeAudioVideo))
            {
                frame_params.m_buffer  = buffer;
                frame_params.m_size    = size;
                consumer->onFrame(frame_params);
            }
        }
        return true;
    }

    void storeSpsPps(const string& codec, std::shared_ptr<FrameInfo> frameInfoMsg)
    {
        if (iequals(codec, "H264"))
        {
            NaluType nalu_type = parseH264NaluType(frameInfoMsg->m_content.data(), frameInfoMsg->m_content.size());
            if (nalu_type == NaluType::kSps)
            {
                m_sps.clear();
                m_sps.insert(m_sps.end(), frameInfoMsg->m_content.data(), frameInfoMsg->m_content.data() + frameInfoMsg->m_content.size());
            }
            else if (nalu_type == NaluType::kPps)
            {
                m_pps.clear();
                m_pps.insert(m_pps.end(), frameInfoMsg->m_content.data(), frameInfoMsg->m_content.data() + frameInfoMsg->m_content.size());
            }
        }
        if (iequals(codec, "H265"))
        {
            H265NaluType nalu_type = parseH265NaluType(frameInfoMsg->m_content.data(), frameInfoMsg->m_content.size());
            if (nalu_type == H265NaluType::SPS_NUT)
            {
                m_sps.clear ();
                m_sps.insert(m_sps.end(), frameInfoMsg->m_content.data(), frameInfoMsg->m_content.data() + frameInfoMsg->m_content.size());
            }
            else if (nalu_type == H265NaluType::PPS_NUT)
            {
                m_pps.clear();
                m_pps.insert(m_pps.end(), frameInfoMsg->m_content.data(), frameInfoMsg->m_content.data() + frameInfoMsg->m_content.size());
            }
        }
    }

    void notifyToQosRecord(std::shared_ptr<FrameInfo> frameInfo)
    {
        string media = frameInfo->m_media;
        string codec = frameInfo->m_codec;
        uint8_t fCurPacketNALUnitType = frameInfo->m_nalType;
        struct timeval presentationTime = frameInfo->m_presentationTime;
        int64_t ptsFromServer = frameInfo->m_ptsFromServer;
        int64_t currentFrameId = frameInfo->m_currentFrameId;

        /* Pass only IDR & slice frames to QoS record. Generate QoS records. */
        if (media == "video")
        {
            if (isValidDataNAL(fCurPacketNALUnitType, codec))
            {
                /* If server is providing pts then use it */
                if (ptsFromServer)
                {
                    presentationTime.tv_sec = ptsFromServer / 1000000;
                    presentationTime.tv_usec = (ptsFromServer % 1000000);
                }
                /* Generate QoS data only for Main stream, ignore substreams */
                if (m_isMainStream && m_qosRecord)
                {
                    m_qosRecord->onFrame(presentationTime, currentFrameId);
                }
            }
        }
        else
        {
            /* Nothing to do for Audio */
        }
    }

    virtual bool onData(const char *id, unsigned char *buffer, ssize_t size, struct timeval presentationTime)
    {
        uint8_t fCurPacketNALUnitType = (uint8_t)NaluType::kNalUnknown;
        string media = "video", codec = "H264";
        if (m_session)
        {
            MediaSubsessionIterator iter(*m_session);
            MediaSubsession* subsession;
            while ((subsession = iter.next()) != nullptr)
            {
                if (subsession->sink && strcmp(id, subsession->sink->name()) == 0)
                {
                    media = subsession->mediumName();
                    codec = subsession->codecName();

                    // If Onvif Extn Timestamp is enabled, then use the timestamp from the Onvif Extn Header.
                    if (m_useOnvifExtnTimestamp)
                    {
                        if (subsession->readSource() != nullptr)
                        {
                            struct timeval onvifExtnTimestamp;
                            uint64_t rtpExtensionTimestamp = subsession->readSource()->fFrameTimestamp;
                            onvifExtnTimestamp.tv_sec = rtpExtensionTimestamp / 1000000;
                            onvifExtnTimestamp.tv_usec = (rtpExtensionTimestamp % 1000000);
                            presentationTime = onvifExtnTimestamp;
                        }
                    }
                }
            }
        }
        else
        {
            return true;
        }

        if (media == "video")
        {
            if (codec == "H265")
                fCurPacketNALUnitType = (uint8_t) parseH265NaluType(buffer, size);
            else
                fCurPacketNALUnitType = (uint8_t) parseH264NaluType(buffer, size);
        }

        /* Create FrameInfo object */
        std::shared_ptr<FrameInfo> frameInfoMsg(
                new FrameInfo(fCurPacketNALUnitType, media, codec,
                buffer, size, presentationTime));

        /* Parse the frameId & time info from user-defined SEI frame */
        if (media == "video")
        {
            bool isUserDefinedSeiFrame = false;
            if (codec == "H264" && fCurPacketNALUnitType == NaluType::kSei)
            {
                m_currentFrameId = parseSeiFrameId(buffer, size, m_ptsFromServer, codec);
                isUserDefinedSeiFrame = true;
            }
            else if (codec == "H265" && fCurPacketNALUnitType == H265NaluType::PREFIX_SEI_NUT)
            {
                m_currentFrameId = parseSeiFrameId(buffer, size, m_ptsFromServer, codec);
                isUserDefinedSeiFrame = true;
            }
            /* set the frameId & ptsFromServer to next nal units */
            if (m_currentFrameId != -1 || (m_ptsFromServer >= 0 && GET_CONFIG().enable_mega_simulation))
            {
                frameInfoMsg->setFrameIdInfo(m_currentFrameId, m_ptsFromServer);
                if (isUserDefinedSeiFrame == true)
                {
                    /* Skip the user defined sei frame, Since we have already parsed pts/frameid */
                    return true;
                }
            }
        }

        /* Notify frame details to QoS recorder */
        notifyToQosRecord(frameInfoMsg);

        /* Streaming started callback publish, only once */
        if (media == "video")
        {
            if (m_initFrames.empty() && m_isVideoMetadataUpdated == false)
            {
                storeSpsPps (codec, frameInfoMsg);
                if (m_sps.empty() == false && m_pps.empty() == false)
                {
                    m_initFrames.push_back(m_sps);
                    m_initFrames.push_back(m_pps);
                }
            }
            if (isValidDataNAL(fCurPacketNALUnitType, codec))
            {
                if (m_streamingStarted == false)
                {
                    StreamEncParam details;
                    details.codec = codec;
                    StreamMonitor::getInstance()->sendStatusEvent(m_uri, STREAM_STATUS_STREAMING, details);
                    m_streamingStarted = true;
                }
                if ((m_ptsFromServer >= 0 && GET_CONFIG().enable_mega_simulation) || m_currentFrameId != -1)
                {
                    presentationTime.tv_sec = m_ptsFromServer / 1000000;
                    presentationTime.tv_usec = (m_ptsFromServer % 1000000);
                    frameInfoMsg->m_presentationTime = presentationTime;
                
                    LOG(verbose) << "[CLIENT] receievd frame frameId:"<<m_currentFrameId<<", pts:"
                        << presentationTime.tv_sec<<"."<<presentationTime.tv_usec <<", fCurPacketNALUnitType = " << (int)fCurPacketNALUnitType <<
                        " size = " << size << endl;
                }
            }
            if (isIDRFrame(fCurPacketNALUnitType, codec) && m_isVideoMetadataUpdated == false)
            {
                m_videoMetadataFetchTask = async::spawn([=]
                {
                    string frame_rate, width, height;
                    std::vector<std::vector<uint8_t>> sps_pps_idr_frames;
                    string uri =  m_uri;
                    string local_codec = codec;
                    std::vector<std::vector<uint8_t>> initFrames = m_initFrames;
                    for (auto frame : initFrames)
                    {
                        sps_pps_idr_frames.push_back(frame);
                    }
                    sps_pps_idr_frames.push_back(frameInfoMsg->m_content);
                    Json::Value response = getRTSPStreamDetails (uri, local_codec, sps_pps_idr_frames);
                    if (response.isMember("width"))
                    {
                        width = response.get("width", "").asString();
                    }
                    if (response.isMember("height"))
                    {
                        height = response.get("height", "").asString();
                    }
                    if (response.isMember("frame_rate"))
                    {
                        frame_rate = response.get("frame_rate", "").asString();
                    }
                    m_isVideoMetadataUpdated = !( width.empty () || height.empty() );
                    if (m_isVideoMetadataUpdated)
                    {
                        std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
                        std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
                        for (auto const& stream : streamList)
                        {
                            
                            if (stream->live_proxy_url == uri)
                            {
                                SensorVideoEncoderSettingsValues values;
                                values.encoding = codec;
                                values.frameRate = frame_rate;
                                values.resolution.width = width;
                                values.resolution.height = height;
                                stream->updateVideoEncoderValues(values);
                                stream->printInfo();
                                break;
                            }
                        }
                    }
                });
            }
        }

        /* Notify with frameInfo object to registered consumers */
        {
            std::lock_guard<std::mutex> recordLock(m_RTSPConsumerLock);
            if (m_consumers.size() > 0)
            {
                std::unique_lock<std::mutex> lk(m_frameQueueMutex);
                m_frameQueue.push(frameInfoMsg);
                m_frameQueueCv.notify_all();
            }
        }
        return true;
    }

    void frameMonitorTask()
    {
        LOG(info) << "Started the thread frameMonitorTask name: " << m_name << endl;
        while (m_exitFrameMonitorThread == false)
        {
            std::shared_ptr<FrameInfo> msg;
            {
                std::unique_lock<std::mutex> lk(m_frameQueueMutex);
                while (m_frameQueue.empty() && m_exitFrameMonitorThread == false)
                {
                    auto until = std::chrono::system_clock::now() + 100ms;
                    m_frameQueueCv.wait_until(lk, until);
                }
                if (m_frameQueue.empty()) continue;

                msg = m_frameQueue.front();
                m_frameQueue.pop();
            }

            /* Forward the data to consumers & to qos thread */
            if (msg && msg->m_content.empty() == false)
            {
                onDataConsumer(msg);
            }
        }
        LOG(info) << "Exited the thread frameMonitorTask name: " << m_name << endl;
    }

    void addConsumer(shared_ptr<IMediaDataConsumer> consumer)
    {
        std::lock_guard<std::mutex> lock(m_RTSPConsumerLock);
        LOG(info) << "Adding consumer for " << m_name << ", url: " << secureUrlForLogging(m_uri) << endl;
        if (std::find(m_consumers.begin(), m_consumers.end(), consumer) == m_consumers.end())
        {
            m_consumers.push_back(consumer);
        }
    }

    void removeConsumer(shared_ptr<IMediaDataConsumer> consumer)
    {
        std::lock_guard<std::mutex> lock(m_RTSPConsumerLock);
        LOG(info) << "Removing consumer for " << m_name << ", url: " << secureUrlForLogging(m_uri) << endl;
        m_consumers.erase(std::remove(m_consumers.begin(), m_consumers.end(), consumer), m_consumers.end());
    }

    void pause()
    {
        controlStreamLiveVideoSource("pause", "");
    }
    void teardown()
    {
        controlStreamLiveVideoSource("teardown", "");
    }
    void getAudioInfo(const string& sdp, const string& codec)
    {
        LOG(info) << "\nsubsession->savedSDPLines() = " << sdp << endl;

        std::string fmt(sdp);
        std::transform(fmt.begin(), fmt.end(), fmt.begin(), [](unsigned char c) { return std::tolower(c); });
        std::string codecstr(codec);
        std::transform(codecstr.begin(), codecstr.end(), codecstr.begin(), [](unsigned char c) { return std::tolower(c); });
        size_t pos = fmt.find(codecstr);
        if (pos != std::string::npos)
        {
            fmt.erase(0, pos + strlen(codec.c_str()));
            fmt.erase(fmt.find_first_of(" \r\n"));
            std::istringstream is(fmt);
            std::string dummy;
            std::getline(is, dummy, '/');
            std::string freq;
            std::getline(is, freq, '/');
            if (!freq.empty())
            {
                m_freq = stringToInt(freq, DEFAULT_FREQUENCY);
            }
            std::string channel;
            std::getline(is, channel, '/');
            if (!channel.empty())
            {
                m_channel = stringToInt(channel, DEFAULT_AUDIO_CHANNELS);
            }
        }

        fmt = sdp;
        pos = fmt.find("config");
        if (pos != std::string::npos)
        {
            fmt.erase(0, pos + strlen("config"));
            fmt.erase(fmt.find_first_of(" \r\n"));
            std::istringstream is(fmt);
            std::string dummy;
            std::getline(is, dummy, '=');
            std::string config;
            std::getline(is, config, '=');
            if (!config.empty())
            {
                m_codecData = stringToInt(config, DEFAULT_CODEC_DATA);
                LOG(info) << "Stream Monitor::getAudioInfo codecData = " << m_codecData << endl;
            }
        }
        m_audioParams["channel"]    = m_channel;
        m_audioParams["frequency"]  = m_freq;
        m_audioParams["codec_data"] = m_codecData;
        LOG(info) << "Stream Monitor::getAudioInfo codec:" << codecstr << " freq:" << m_freq << " channel:" << m_channel << endl;
    }
    string getUri() { return m_uri; }
    string getDevName() { return m_name; }
    void setDevName(string devName) { m_name = devName; }
    MediaSession *getSession() { return m_session; }
    std::map<std::string, int, std::less<>> getAudioChannelFreq() { return m_audioParams; }
    void updateStreamError(string uri, string error_msg);

private:
    string m_uri;
    string m_name;
    MediaSession *m_session;
    shared_ptr<QosMeasurementRecord> m_qosRecord;
    int m_freq;
    int m_channel;
    int m_codecData;
    std::map<std::string, int, std::less<>> m_audioParams;
    condition_variable m_cvRTSPConnRestart;
    std::mutex  m_RTSPConnLock;
    std::mutex  m_RTSPConsumerLock;
    string m_sdpVideo;
    std::thread m_frameMonitorThread;
    std::queue<std::shared_ptr<FrameInfo>> m_frameQueue;
    std::mutex m_frameQueueMutex;
    std::condition_variable m_frameQueueCv;
    std::atomic<bool> m_exitFrameMonitorThread {false};
    std::vector<std::vector<uint8_t>> m_initFrames;
    async::task<void>           m_videoMetadataFetchTask;
    std::atomic<bool>           m_isVideoMetadataUpdated {true};
    std::vector<uint8_t>        m_sps;
    std::vector<uint8_t>        m_pps;
    std::atomic<bool>           m_useOnvifExtnTimestamp {false};
public:
    unsigned m_retryCount;
    bool m_isRestartRequired;
    std::vector<shared_ptr<IMediaDataConsumer>> m_consumers;
    bool m_isMainStream;
    std::atomic<bool> m_streamingStarted {false};
    int64_t m_currentFrameId = -1;
    int64_t m_ptsFromServer = 0;
    std::atomic<bool> m_tryTcpStreaming {false};
};

QosRtspClient::QosRtspClient(const std::string& uri, const std::string& name, const std::map<std::string, std::string, std::less<>>& opts)
    : VideoSource(uri, opts)
    , m_uri(uri)
    , m_name(name)
    , m_session(nullptr)
    , m_qosRecord(nullptr)
    , m_freq(DEFAULT_FREQUENCY)
    , m_channel(DEFAULT_AUDIO_CHANNELS)
    , m_codecData(DEFAULT_CODEC_DATA)
    , m_retryCount(0)
    , m_isRestartRequired(false)
    , m_isMainStream(false)
    , m_streamingStarted(false)
{
    LOG(info) << "QosRtspClient: name:" << m_name << ", uri:" << m_uri << endl;
    m_frameMonitorThread = std::thread([this] { this->frameMonitorTask(); });
    m_consumers = StreamMonitor::getInstance()->getConsumers(m_uri);
    std::shared_ptr<DeviceManager> m_deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
    vector<shared_ptr<SensorInfo>> sensors = m_deviceMngr->getSensorList();
    m_isVideoMetadataUpdated = true;
    string base_uri = m_uri.find("?") != string::npos ? m_uri.substr(0, m_uri.find("?")) : m_uri;
    for (uint32_t i = 0; i < sensors.size(); i++)
    {
        vector<shared_ptr<StreamInfo>> streams = sensors[i]->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream->live_proxy_url == m_uri)
            {
                if (sensors[i]->type == SENSOR_TYPE_RTSP)
                {
                    m_isVideoMetadataUpdated = false;
                }
                break;
            }
        }

        /* Check if Onvif RTP extension timestamp is supported for this sensor */
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream->replay_url == base_uri)
            {
                if (sensors[i]->type == SENSOR_TYPE_MMS_ONVIF)
                {
                    m_useOnvifExtnTimestamp = true;
                }
                break;
            }
        }
    }
}

QosRtspClient::~QosRtspClient()
{
    LOG(info) << " ~QosRtspClient: name:" << m_name << ", uri:" << m_uri << endl;
    m_exitFrameMonitorThread = true;
    m_frameQueueCv.notify_all();
    if (m_frameMonitorThread.joinable())
    {
        m_frameMonitorThread.join();
    }
    m_frameQueue = {};
    m_cvRTSPConnRestart.notify_all();
    if (m_videoMetadataFetchTask.valid())
    {
        try
        {
            m_videoMetadataFetchTask.get();
        }
        catch(const std::exception& e)
        {
            LOG(error) << "Caught Exception for m_videoMetadataFetch Async task: " <<  e.what() << endl;
        }
    }
}

void QosRtspClient::updateStreamError(string uri, string error_msg)
{
    std::lock_guard<std::mutex> recordLock(g_streamFailureMapMutex);
    string currentTime = getCurrentTimeMS();
    std::replace(error_msg.begin(), error_msg.end(), ',', ' ');
    g_streamFailureCount.insert({uri, make_pair(error_msg, currentTime)});
}

void removeRecord(string uri)
{
    if (GET_CONFIG().enable_qos_monitoring)
    {
        std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
        std::map<std::string, shared_ptr<QosMeasurementRecord>, std::less<>>::iterator it_record = g_records.begin();
        while (it_record != g_records.end())
        {
            if (it_record->first == uri)
            {
                shared_ptr<QosMeasurementRecord> record = it_record->second;
                if (record)
                {
                    record->stopQoS();
                }
                g_records.erase (it_record);
                break;
            }
            ++it_record;
        }
    }
}

void startRecord(const StreamMonitor::UrlInfo &stream)
{
    shared_ptr<QosMeasurementRecord> m_qosRecord(new QosMeasurementRecord(stream.m_devName, stream.m_frameRate));
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    g_records.insert({stream.m_url, m_qosRecord});
}

shared_ptr<QosMeasurementRecord> getRecord(const string& url)
{
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    std::map<std::string, shared_ptr<QosMeasurementRecord>, std::less<>>::iterator it_record = g_records.begin();
    while (it_record != g_records.end())
    {
        if (it_record->first == url)
        {
            return it_record->second;
        }
        ++it_record;
    }
    return nullptr;
}

void StreamMonitor::enableTcpStreaming(const string& uri)
{
    if (GET_CONFIG().rtsp_streaming_over_tcp == true)
    {
        return;
    }
    // TODO: Reduce scope of lock. Take processing and while loop outside lock.
    std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
    std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
    while (it_monitor != m_qosMonitorList.end())
    {
        UrlInfo elem = *it_monitor;
        if (elem.m_url == uri)
        {
            if (ModuleLoader::getInstance()->getRtspServerMgmtInstance())
            {
                LOG(info) << "Recreating Proxy with tcp streaming" << endl;
                vst_rtsp::removeStream(elem.m_streamId);

                /* Read credentials from database */
                string live_url = elem.m_livenessUrl;
                SensorDetailsDBColumns row = GET_DB_INSTANCE()->readSensorDetails("", elem.m_streamId);
                if (!row.sensor_id_value.empty() && !live_url.empty() &&
                    !(row.username_value.empty() || row.password_value.empty()))
                {
                    string token("//");
                    string substr = row.username_value + ":" + row.password_value + "@";
                    insertString(live_url, token, substr);
                    /* Securely erase credentials from memory after use */
                    std::fill(substr.begin(), substr.end(), '\0');
                    substr.clear();
                }
                live_url += live_url.find("?") != string::npos ? "&transport=tcp" : "?transport=tcp";
                string vodUrl;
                vst_rtsp::addStream(elem.m_streamId, elem.m_devName, live_url, vodUrl);
            }
            break;
        }
        it_monitor++;
    }
}

QosRtspClient *startRtspClient(string uri, string devName = "")
{
    LOG(info) << "starting RtspClient for " << devName << ", uri:" << uri << endl;
    unsigned old_retryCount = 0;
    bool tryTcpTransport = false;
    std::map<string, string, std::less<>> opts;
    QosRtspClient *rtspSrc = nullptr;

    opts["rtptransport"] = RTP_UDP_TRANSPORT_MODE;
    opts["qosMode"] = "true";
    if (GET_CONFIG().enable_mega_simulation)
    {
        /* This value is in seconds */
        constexpr const char* MILLION = "10000";
        opts["timeout"] = MILLION;
    }
    else
    {
        opts["timeout"] = RTP_DATA_TIMEOUT_PERIOD_SEC;
    }
    if (tryTcpTransport == true)
    {
        /* Try tcp streaming only once, Since udp-firewall might be an issue */
        StreamMonitor::getInstance()->enableTcpStreaming(uri);
    }

    std::lock_guard<std::mutex> devicesLock(g_rtspSourceMutex);
    // Check if rtsp client for given url is present, remove it.
    std::map<string, QosRtspClient *, std::less<>>::iterator it = g_rtspSources.find(uri);
    if (it != g_rtspSources.end())
    {
        QosRtspClient *oldRtspSrc = it->second;
        if (oldRtspSrc)
        {
            old_retryCount = oldRtspSrc->m_retryCount;
            tryTcpTransport = oldRtspSrc->m_tryTcpStreaming;
            removeRecord(uri);
            delete oldRtspSrc;
            g_rtspSources.erase(uri);
        }
    }

    rtspSrc = QosRtspClient::Create(uri, devName, opts);
    if (rtspSrc)
    {
        rtspSrc->m_retryCount = old_retryCount;
        rtspSrc->m_tryTcpStreaming = tryTcpTransport;
        g_rtspSources[uri] = rtspSrc;
    }
    LOG(verbose) << "[streamMonitor] Created rtspClient for " << devName << ", uri:" << uri << endl;
    return rtspSrc;
}

void removeRtspClient(string uri)
{
    QosRtspClient *rtspSrc = nullptr;
    {
        std::lock_guard<std::mutex> devicesLock(g_rtspSourceMutex);
        std::map<string, QosRtspClient *, std::less<>>::iterator it = g_rtspSources.find(uri);
        if (it != g_rtspSources.end())
        {
            LOG(verbose) << "[streamMonitor] Removing rtspClient for "
                    << it->second->getDevName() << ", uri:" << uri << endl;
            rtspSrc = it->second;
            g_rtspSources.erase(uri);
        }
    }
    if (rtspSrc)
    {
        delete rtspSrc;
    }
}

QosRtspClient *getRtspClient(string uri)
{
    QosRtspClient *rtspSrc = nullptr;
    std::lock_guard<std::mutex> devicesLock(g_rtspSourceMutex);
    std::map<string, QosRtspClient *, std::less<>>::iterator it = g_rtspSources.find(uri);
    if (it != g_rtspSources.end())
    {
        rtspSrc = it->second;
    }
    return rtspSrc;
}

static void qosDataCollector(void* clientData)
{
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    struct timeval timeNow;
    bool recordFound = false;

    // Check if record exists to avoid dangling pointer case.
    QosMeasurementRecord *m_qosRecord = (QosMeasurementRecord *)clientData;
    for (auto& it : g_records)
    {
        if (it.second.get() == m_qosRecord)
        {
            recordFound = true;
            break;
        }
    }
    if (m_qosRecord && recordFound)
    {
        gettimeofday(&timeNow, nullptr);

        /* Collect QoS data from Frontend media session */
        m_qosRecord->periodicQosData(timeNow);

        // Do this again later:
        if (m_qosRecord->m_rtpSourceProxy)
        {
            m_qosRecord->m_qosPeriodicTask = m_qosRecord->m_rtpSourceProxy->envir().taskScheduler().scheduleDelayedTask(
                GET_CONFIG().qos_data_capture_interval_sec * 1000000, (TaskFunc*)qosDataCollector, (void*)m_qosRecord);
        }
    }
}

static void qosDataCollectorBackend(void* clientData)
{
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    struct timeval timeNow;
    bool recordFound = false;

    // Check if record exists to avoid dangling pointer case.
    QosMeasurementRecord *m_qosRecord = (QosMeasurementRecord *)clientData;
    for (auto& it : g_records)
    {
        if (it.second.get() == m_qosRecord)
        {
            recordFound = true;
            break;
        }
    }

    if (m_qosRecord && recordFound)
    {
        gettimeofday(&timeNow, nullptr);
        m_qosRecord->periodicQosDataBackend(timeNow);

        /* Collect QoS data from backend media session in it's own env thread */
        RTPSource *rtpSourceBackend = m_qosRecord->getBackendRtpSource();
        if (rtpSourceBackend)
        {
            m_qosRecord->m_backendQosPeriodicTask = rtpSourceBackend->envir().taskScheduler().scheduleDelayedTask(
                GET_CONFIG().qos_data_capture_interval_sec * 1000000,
                (TaskFunc*)qosDataCollectorBackend, (void*)m_qosRecord);
        }
    }
}

RTPSource* QosMeasurementRecord::getBackendRtpSource()
{
    RTPSource *rtpSourceBackend = nullptr;
#if 0
    MediaSession *backend_session = nullptr;

    shared_ptr<StreamInfo> stream = StreamMonitor::getInstance()->getStreamInfo(m_uri, true);
    if (stream)
    {
        ProxyServerMediaSession *sms =
            (ProxyServerMediaSession *)RtspServer::getInstance()->getServerMediaSessionForStream(stream);
        if (sms != nullptr)
        {
            backend_session = sms->getMediaSession();
            if (backend_session == nullptr) return nullptr;

            MediaSubsessionIterator iter(*backend_session);
            MediaSubsession* subsession;
            while ((subsession = iter.next()) != nullptr)
            {
                string mediumName = subsession->mediumName();
                if (mediumName == "video")
                {
                    rtpSourceBackend = subsession->rtpSource();
                    break;
                }
            }
        }
    }
#endif
    return rtpSourceBackend;
}

void QosMeasurementRecord::periodicQosData(struct timeval const& timeNow)
{
    unsigned secsDiff = timeNow.tv_sec - m_measurementEndTime.tv_sec;
    int usecsDiff = timeNow.tv_usec - m_measurementEndTime.tv_usec;
    double timeDiff = secsDiff + usecsDiff/1000000.0;
    m_measurementEndTime = timeNow;

    if (m_rtpSourceProxy)
    {
        RTPReceptionStatsDB::Iterator statsIter(m_rtpSourceProxy->receptionStatsDB());
        // Assume that there's only one SSRC source (usually the case):
        RTPReceptionStats* stats = statsIter.next(True);
        if (stats != nullptr)
        {
            double kBytesTotalNow = stats->totNumKBytesReceived();
            double kBytesDeltaNow = kBytesTotalNow - m_kBytesTotal;
            m_kBytesTotal = kBytesTotalNow;
            double kbpsNow = timeDiff == 0.0 ? 0.0 : 8*kBytesDeltaNow/timeDiff;
            if (kbpsNow < 0.0) kbpsNow = 0.0; // in case of roundoff error
            if (kbpsNow < m_kbitsPerSecondMin) m_kbitsPerSecondMin = kbpsNow;
            if (kbpsNow > m_kbitsPerSecondMax) m_kbitsPerSecondMax = kbpsNow;

            unsigned totReceivedNow = stats->totNumPacketsReceived();
            unsigned totExpectedNow = stats->totNumPacketsExpected();
            unsigned deltaReceivedNow = totReceivedNow - m_totNumPacketsReceived;
            unsigned deltaExpectedNow = totExpectedNow - m_totNumPacketsExpected;
            if (totReceivedNow == m_totNumPacketsReceived)
            {
                LOG(verbose) << "same packet count during 1sec time duration" << endl;
            }
            m_totNumPacketsReceived = totReceivedNow;
            m_totNumPacketsExpected = totExpectedNow;

            double lossFractionNow = deltaExpectedNow == 0 ? 0.0
            : 1.0 - deltaReceivedNow/(double)deltaExpectedNow;
            //if (lossFractionNow < 0.0) lossFractionNow = 0.0; //reordering can cause
            if (lossFractionNow < m_packetLossFractionMin)
            {
                m_packetLossFractionMin = lossFractionNow;
            }
            if (lossFractionNow > m_packetLossFractionMax)
            {
                m_packetLossFractionMax = lossFractionNow;
            }
        }
    }

    /* Video frame rate calculations */
    double avg_fps = 0;
    if (m_sumDiff && m_frameCount)
    {
        avg_fps  = (1000.00) / (m_sumDiff/m_frameCount);
    }
    if (avg_fps < (0.9*m_frameRate))
    {
        if (GET_CONFIG().enable_highlighting_logs)
        {
            LOG(verbose) << magenta << "--[" << m_fName << " - below 90% alert] fps:" << avg_fps << ", m_frameCount:" << m_frameCount << reset << endl;
        }
        else
        {
            LOG(verbose) << "--[" << m_fName << " - below 90% alert] fps:" << avg_fps << ", m_frameCount:" << m_frameCount << endl;
        }
    }
    else if (avg_fps > (1.25*m_frameRate))
    {
        if (GET_CONFIG().enable_highlighting_logs)
        {
            LOG(verbose) << magenta << "--[" << m_fName << " - Above 25% alert] fps:" << avg_fps << ", m_frameCount:" << m_frameCount << reset << endl;
        }
        else
        {
            LOG(verbose) << "--[" << m_fName << " - Above 25% alert] fps:" << avg_fps << ", m_frameCount:" << m_frameCount << endl;
        }
    }
    m_receivedFps += avg_fps;
    m_avgFrameCount += m_frameCount;
    m_sumDiff = 0;
    m_frameCount = 0;
}

void QosMeasurementRecord::periodicQosDataBackend(struct timeval const& timeNow)
{
    unsigned secsDiff = timeNow.tv_sec - m_backendMeasurementEndTime.tv_sec;
    int usecsDiff = timeNow.tv_usec - m_backendMeasurementEndTime.tv_usec;
    double timeDiff = secsDiff + usecsDiff/1000000.0;
    m_backendMeasurementEndTime = timeNow;

    RTPSource *rtpSourceBackend = getBackendRtpSource();
    if (rtpSourceBackend)
    {
        RTPReceptionStatsDB::Iterator statsIter(rtpSourceBackend->receptionStatsDB());
        // Assume that there's only one SSRC source (usually the case):
        RTPReceptionStats* stats = statsIter.next(True);
        if (stats != nullptr)
        {
            double kBytesTotalNow = stats->totNumKBytesReceived();
            double kBytesDeltaNow = kBytesTotalNow - m_backendKBytesTotal;
            m_backendKBytesTotal = kBytesTotalNow;

            double kbpsNow = timeDiff == 0.0 ? 0.0 : 8*kBytesDeltaNow/timeDiff;
            if (kbpsNow < 0.0) kbpsNow = 0.0; // in case of roundoff error
            if (kbpsNow < m_backendKbitsPerSecondMin) m_backendKbitsPerSecondMin = kbpsNow;
            if (kbpsNow > m_backendKbitsPerSecondMax) m_backendKbitsPerSecondMax = kbpsNow;

            unsigned totReceivedNow = stats->totNumPacketsReceived();
            unsigned totExpectedNow = stats->totNumPacketsExpected();
            unsigned deltaReceivedNow = totReceivedNow - m_backendTotNumPacketsReceived;
            unsigned deltaExpectedNow = totExpectedNow - m_backendTotNumPacketsExpected;
            if (totReceivedNow == m_backendTotNumPacketsReceived)
            {
                LOG(verbose) << "same packet count during 1sec time duration" << endl;
            }
            m_backendTotNumPacketsReceived = totReceivedNow;
            m_backendTotNumPacketsExpected = totExpectedNow;

            double lossFractionNow = deltaExpectedNow == 0 ? 0.0
            : 1.0 - deltaReceivedNow/(double)deltaExpectedNow;
            //if (lossFractionNow < 0.0) lossFractionNow = 0.0; //reordering can cause
            if (lossFractionNow < m_backendPacketLossFractionMin)
            {
                m_backendPacketLossFractionMin = lossFractionNow;
            }
            if (lossFractionNow > m_backendPacketLossFractionMax)
            {
                m_backendPacketLossFractionMax = lossFractionNow;
            }
        }
    }
}

void QosMeasurementRecord::onFrame(struct timeval& pts, int64_t current_frameId)
{
    uint64_t current_time = std::chrono::duration_cast<std::chrono::milliseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();
    uint64_t current_diff = (current_time - m_prevTime);
    if (current_diff)
    {
        m_instFps = double(1000.00 / current_diff);
    }
    m_sumDiff = m_sumDiff + current_diff;
    m_prevTime = current_time;
    m_frameCount++;

    /* Check if frames are missing between current frame & last recorded frame */
    /*if (current_frameId != 0 && (current_frameId != m_recordedFrameId + 1))
    {
        for (int64_t j = m_recordedFrameId + 1; j < current_frameId; j++)
        {
            m_missedFrames += to_string(j) + " ";
            m_total_frames_lost++;
        }
    }*/
    m_recordedFrameId = current_frameId;
}

bool isRtspReconnectionRequired(string uri)
{
    bool isReconnectionRequired = false;
    QosRtspClient *rtspSrc = getRtspClient(uri);
    if (rtspSrc)
    {
        isReconnectionRequired = rtspSrc->m_isRestartRequired;
    }
    return isReconnectionRequired;
}

void removeUrlFromBlackList(const string& uri)
{
    std::map<string, struct timeval, std::less<>>::iterator it = g_blackList.find(uri);
    if (it != g_blackList.end())
    {
        it = g_blackList.erase(it);
    }
}

bool isUrlBlackListed(string uri)
{
    bool isblackListed = false;
    struct timeval timeNow;
    std::map<string, struct timeval, std::less<>>::iterator it = g_blackList.find(uri);
    if (it != g_blackList.end())
    {
        isblackListed = true;
        gettimeofday(&timeNow, nullptr);
        unsigned int blacklist_period = timevaldiff(it->second, timeNow) / 1000000;
        if (blacklist_period >= URL_BLACKLIST_PERIOD_SEC)
        {
            LOG(info) << "[streamMonitor] removing from qos-blacklist url:" << secureUrlForLogging(uri) << endl;
            isblackListed = false;
            it = g_blackList.erase(it);
        }
    }
    return isblackListed;
}

std::map<string, media_info, std::less<>> StreamMonitor::getSupportedSubSessions(const std::string& url)
{
    std::map<string, media_info, std::less<>> supported_map;
    QosRtspClient *rtspSrc = getRtspClient(url);
    if (rtspSrc == nullptr) return supported_map;
    MediaSession* session = rtspSrc->getSession();
    if (session == nullptr) return supported_map;
    MediaSubsessionIterator iter(*session);
    MediaSubsession* subsession;
    while ((subsession = iter.next()) != nullptr)
    {
        RTPSource* src = subsession->rtpSource();
        if (src == nullptr) continue;

        string media = subsession->mediumName();
        string codec = subsession->codecName();
        if (media == "video")
        {
            media_info video_info;
            video_info.codec     = codec;
            video_info.channel   = 0;
            video_info.frequency = 0;
            video_info.codecData = 0;
            supported_map["video"] = video_info;
        }
        if (media == "audio")
        {
            std::map<std::string, int, std::less<>> audio_params = rtspSrc->getAudioChannelFreq();
            media_info audio_info;
            audio_info.codec     = codec;
            audio_info.channel   = audio_params["channel"];
            audio_info.frequency = audio_params["frequency"];
            audio_info.codecData = audio_params["codec_data"];
            supported_map["audio"] = audio_info;
        }
    }
    return supported_map;
}
void StreamMonitor::registerDataCallback(std::string& url, shared_ptr<IMediaDataConsumer> consumer)
{
    if (consumer == nullptr)
    {
        LOG(error) << "Consumer is null" << endl;
        return;
    }
    {
        std::lock_guard<std::mutex> lock(m_streamConsumerLock);
        std::map<std::string, std::vector<shared_ptr<IMediaDataConsumer>>, std::less<>>::iterator it = m_streamConsumers.find(url);
        if (it != m_streamConsumers.end())
        {
            std::vector<shared_ptr<IMediaDataConsumer>>& list = it->second;
            if (std::find(list.begin(), list.end(), consumer) == list.end())
            {
                list.push_back(consumer);
            }
        }
        else
        {
            m_streamConsumers[url].push_back(consumer);
        }
    }
    QosRtspClient *rtspSrc = getRtspClient(url);
    if (rtspSrc == nullptr)
    {
        LOG(info) << "Rtsp client not found, creating new for url:" << secureUrlForLogging(url) << endl;
        string devName = getUriName(url);
        string streamId = "";

        string base_uri = url.find("?") != string::npos ? url.substr(0, url.find("?")) : url;
        std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
        if (deviceManager)
        {
            vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
            for (uint32_t i = 0; i < streamList.size(); i++)
            {
                std::shared_ptr<StreamInfo> stream = streamList[i];
                if (stream && (stream->replay_url == base_uri || stream->live_proxy_url == base_uri))
                {
                    devName = stream->name;
                    streamId = stream->id;
                    break;
                }
            }
        }

        rtspSrc = startRtspClient(url, devName);

        // Add URL to qos monitor list if not already present
        if (rtspSrc != nullptr)
        {
            std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
            bool urlExists = false;
            for (const auto& stream : m_qosMonitorList)
            {
                if (stream.m_url == url)
                {
                    urlExists = true;
                    break;
                }
            }

            if (!urlExists)
            {
                UrlInfo url_info;
                url_info.m_url = url;
                url_info.m_devName = devName;
                url_info.m_streamId = streamId;
                url_info.m_rtspUrlReachable = true;
                m_qosMonitorList.push_back(url_info);
                LOG(info) << "Added URL to qos monitor list for " << devName << endl;
            }
        }
    }
    rtspSrc->addConsumer(consumer);
}

void StreamMonitor::deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& url, bool doNotRemoveClient)
{
    if (consumer == nullptr)
    {
        return;
    }

    QosRtspClient *rtspSrc = getRtspClient(url);
    if (rtspSrc)
    {
        rtspSrc->removeConsumer(consumer);
    }
    else
    {
        LOG(error) << "Rtsp client not found " << secureUrlForLogging(url) << endl;
    }

    if (doNotRemoveClient)
    {
        return;
    }

    bool needToRemoveRtspClient = false;
    {
        std::lock_guard<std::mutex> lock(m_streamConsumerLock);
        std::map<std::string, std::vector<shared_ptr<IMediaDataConsumer>>, std::less<>>::iterator it = m_streamConsumers.find(url);
        if (it != m_streamConsumers.end())
        {
            std::vector<shared_ptr<IMediaDataConsumer>>& list = it->second;
            list.erase(std::remove(list.begin(), list.end(), consumer), list.end());
            if(list.size() == 0)
            {
                m_streamConsumers.erase (it);
                // delete stream if no more cosumers.
                std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
                if (deviceManager && deviceManager->needRtspServer == false)
                {
                    /* In distributed microservices architecture, Since client is created on demand. Delete the rtsp client if no more consumers */
                    needToRemoveRtspClient = true;
                }
                else if (rtspSrc && !rtspSrc->m_isMainStream)
                {
                    needToRemoveRtspClient = true;
                }
            }
        }
        else
        {
            LOG(verbose) << "Callback not registered for this url:" << secureUrlForLogging(url) << endl;
        }
    }

    if (needToRemoveRtspClient)
    {
        string stream_id;
        if (url.empty() == false)
        {
            std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
            std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
            while (it_monitor != m_qosMonitorList.end())
            {
                UrlInfo elem = *it_monitor;
                if (elem.m_url == url)
                {
                    stream_id = elem.m_streamId;
                    break;
                }
                it_monitor++;
            }
        }
        if (stream_id.empty() == false)
        {
            removeStream(stream_id);
        }
    }
}

std::vector<shared_ptr<IMediaDataConsumer>> StreamMonitor::getConsumers(const string& url)
{
    std::vector<shared_ptr<IMediaDataConsumer>> consumers;
    std::lock_guard<std::mutex> lock(m_streamConsumerLock);
    std::map<std::string, std::vector<shared_ptr<IMediaDataConsumer>>, std::less<>>::iterator it = m_streamConsumers.find(url);
    if (it != m_streamConsumers.end())
    {
        consumers = it->second;
    }
    return consumers;
}

void StreamMonitor::qosMeasurementTask()
{
    LOG(info) << "Started the QoS measurement thread" << endl;
    m_exitQosThread = false;
    struct timeval prevQosDumpTime = {}, timeNow = {};

    try
    {
        while (m_exitQosThread == false)
        {
            std::vector<StreamMonitor::UrlInfo> streamList = getQosMonitorStreamList();
            for (auto stream: streamList)
            {
                /* Create rtsp client only in below cases:
                -> 1. url is newly detected/added.
                -> 2. In case of reconnection retry (3 attempts) if failed previously.
                -> 3. If stream is not blacklisted due to multiple failures.
                */
                bool createRecord = false;
                std::map<std::string, QosRtspClient*, std::less<>>::iterator it = g_rtspSources.find(stream.m_url);
                if (it == g_rtspSources.end() && stream.m_isMainStream)
                {
                    QosRtspClient *rtspSource = startRtspClient(stream.m_url, stream.m_devName);
                    if (rtspSource)
                    {
                        rtspSource->m_isMainStream = true;
                        createRecord = true;
                    }
                }
                else if(isRtspReconnectionRequired(stream.m_url))
                {
                    QosRtspClient *rtspSrc = getRtspClient(stream.m_url);
                    if (isRtspServerReachable(stream.m_livenessUrl) == false)
                    {
                        if (rtspSrc)
                        {
                            /* Since rtsp url itself is not reachanble, put url into blacklist */
                            LOG(error) << "RTSP url is not reachable for:" << stream.m_devName << endl;
                            rtspSrc->m_retryCount = RTSP_CONNECTION_MAX_RETRY_COUNT;
                        }

                        {
                            std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
                            for (auto& streaminfo : m_qosMonitorList)
                            {
                                if (stream.m_url == streaminfo.m_url)
                                {
                                    streaminfo.m_rtspUrlReachable = false;
                                    break;
                                }
                            }
                        }
                    }
                    if (rtspSrc && rtspSrc->m_retryCount >= RTSP_CONNECTION_MAX_RETRY_COUNT)
                    {
                        LOG(error) << "[streamMonitor] Max limit reached for reconnection retry for:" << stream.m_devName << endl;
                        removeRecord(stream.m_url);
                        removeRtspClient(stream.m_url);
                        {
                            // Add stream into blacklist for given time period.
                            struct timeval timeNow;
                            gettimeofday(&timeNow, nullptr);
                            g_blackList.insert({stream.m_url, timeNow});
                        }
                        continue;
                    }

                    QosRtspClient *rtspSource = startRtspClient(stream.m_url, stream.m_devName);
                    if (rtspSource)
                    {
                        if (stream.m_isMainStream)
                        {
                            rtspSource->m_isMainStream = true;
                            createRecord = true;
                        }
                    }
                }

                // Create the qos record for corrensponding rtsp client.
                if ((stream.m_isMainStream && g_records.find(stream.m_url) == g_records.end()) || createRecord == true)
                {
                    if (m_enableQoS)
                    {
                        startRecord(stream);
                    }
                }

                // Check if camera has been renamed, update it in that case.
                QosRtspClient *rtspSrc = getRtspClient(stream.m_url);
                if (rtspSrc && (rtspSrc->getDevName() != stream.m_devName))
                {
                    rtspSrc->setDevName(stream.m_devName);
                    if (m_enableQoS)
                    {
                        shared_ptr<QosMeasurementRecord> qRecord = g_records[stream.m_url];
                        if (qRecord)
                            qRecord->setDevName(stream.m_devName);
                    }
                }
                // Check if any change in camera fps, update it in that case.
                shared_ptr<QosMeasurementRecord> qRecord = getRecord(stream.m_url);
                if (qRecord && (qRecord->m_frameRate != stream.m_frameRate))
                {
                    LOG(info) << "Change in source fps, update the qos-record" << endl;
                    qRecord->m_frameRate = stream.m_frameRate;
                }
            }

            // Check if any url to be removed from monitoring.
            std::map<std::string, QosRtspClient *, std::less<>>::iterator it_record = g_rtspSources.begin();
            while (it_record != g_rtspSources.end())
            {
                bool found = false;
                for (auto stream: streamList)
                {
                    if (stream.m_url == it_record->first)
                    {
                        found = true;
                        break;
                    }
                }
                if (found == false)
                {
                    LOG(info) << "Proxy url not present in streamList, removing " << it_record->second->getDevName() << endl;
                    removeRecord(it_record->first);
                    delete it_record->second;
                    it_record = g_rtspSources.erase(it_record);
                    if (g_blackList.size() > 0)
                    {
                        std::map<string, struct timeval, std::less<>>::iterator it = g_blackList.find(it_record->first);
                        if (it != g_blackList.end())
                        {
                            it = g_blackList.erase(it);
                        }
                    }
                }
                else
                {
                    ++it_record;
                }
            }

            if (m_enableQoS && g_records.size() > 0)
            {
                gettimeofday(&timeNow, nullptr);
                int qosDump_elapsed_time = timevaldiff(prevQosDumpTime, timeNow) / 1000000;
                if (qosDump_elapsed_time >= m_vmsConfig.qos_data_publish_interval_sec)
                {
                    printQoSData();
                    prevQosDumpTime = timeNow;
                }
            }

            // Stream monitor Sleep for 5seconds or untill get notified.
            {
                m_qosThreadSync.wait(m_vmsConfig.qos_data_publish_interval_sec * 1000);
            }
        } // Main while loop (exit == false)
    }
    catch(const std::invalid_argument& e)
    {
        LOG(error) << "exception in qosMeasurementTask, restarting the task. error: " << e.what() << endl;
        restartQoSMonitoringTask();
        return;
    }

    cleanupQoSThread();
    LOG(info) << "Exiting the QoS measurement thread" << endl;
}

void StreamMonitor::cleanupQoSThread()
{
    // Delete all the measurements records.
    g_records.clear();
    g_blackList.clear();
    g_streamFailureCount.clear();

    // Delete the rtsp sources
    for (const auto& source : g_rtspSources)
    {
        delete source.second;
    }
    g_rtspSources.clear();
}

void StreamMonitor::restartQoSMonitoringTask()
{
    cleanupQoSThread();
    m_qosMeasurementThread = std::thread([this] { this->qosMeasurementTask(); });
}

std::string StreamMonitor::getUriName(const std::string &url)
{
    std::string dev_name;
    std::vector<UrlInfo> streamList = m_qosMonitorList;
    for (auto const& stream : streamList)
    {
        if (stream.m_url == url)
        {
            dev_name = stream.m_devName;
        }
    }
    return dev_name;
}

std::shared_ptr<StreamInfo> StreamMonitor::getStreamInfoForUrl(const std::string &url)
{
    std::shared_ptr<StreamInfo> streamInfo;
    std::string base_url = url.find("?") != string::npos ? url.substr(0, url.find("?")) : url;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager)
    {
        vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
        for (auto const& stream : streamList)
        {
            if (stream && (stream->live_proxy_url == url || stream->live_proxy_url == base_url))
            {
                streamInfo = stream;
                break;
            }
        }
    }
    return streamInfo;
}

#if 0
shared_ptr<StreamInfo> StreamMonitor::getStreamInfo(const std::string &url, bool isProxyUrl)
{
    shared_ptr<StreamInfo> streamInfo;
    std::vector<shared_ptr<StreamInfo>> streamList = m_deviceManager->getStreamList();
    for (auto const& stream : streamList)
    {
        if (isProxyUrl && stream->live_proxy_url == url)
        {
            streamInfo = stream;
            break;
        }
        else if (stream->live_url == url)
        {
            streamInfo = stream;
            break;
        }
    }
    return streamInfo;
}
#endif

std::vector<StreamMonitor::UrlInfo> StreamMonitor::getQosMonitorStreamList()
{
    std::vector<StreamMonitor::UrlInfo> monitorList;
    std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
    for (auto& stream : m_qosMonitorList)
    {
        /* Avoiding stream monitoring for webrtc input stream */
        if (stream.m_url.find("webrtc/") != std::string::npos)
        {
            continue;
        }
        if (!stream.m_url.empty())
        {
            if(isUrlBlackListed(stream.m_url) == true)
            {
                if (stream.m_rtspUrlReachable == false && isRtspServerReachable(stream.m_livenessUrl))
                {
                    removeUrlFromBlackList(stream.m_url);
                    stream.m_rtspUrlReachable = true;
                }
                else
                {
                    continue;
                }
            }
            monitorList.push_back(stream);
        }
    }
    return monitorList;
}

void StreamMonitor::updateUriStatus(const std::string& uri, StreamStatus status, CURLcode errorCode)
{
    {
        std::lock_guard<std::mutex> devicesLock(m_livenessMonitorListMutex);
        std::map<std::string, StreamStatus, std::less<>>::iterator it;
        for (it = m_livenessMonitorList.begin(); it != m_livenessMonitorList.end(); it++)
        {
            if(it->first == uri && it->second != status)
            {
                std::string url = it->first;
                StreamEncParam params;
                sendStatusEvent(url, status, params);
                it->second = status;
            }
        }
    }
    std::vector<UrlInfo> streamList = getQosMonitorStreamList();
    for (auto& stream : streamList)
    {
        if (stream.m_livenessUrl == uri)
        {
            if (status == STREAM_STATUS_ONLINE)
            {
                QosRtspClient *rtspSrc = getRtspClient(stream.m_url);
                if (rtspSrc && rtspSrc->getSession() != nullptr)
                {
                    status = STREAM_STATUS_STREAMING;
                }
            }
            else if (errorCode == CURLE_COULDNT_CONNECT || errorCode == CURLE_OPERATION_TIMEDOUT)
            {
                QosRtspClient *rtspSrc = getRtspClient(stream.m_url);
                if (rtspSrc)
                {
                    rtspSrc->m_streamingStarted = false;
                }
            }
            /*string error_message = string("curl_error_code: ") + to_string(errorCode) +
                    string(" error_message: ") + curl_easy_strerror(errorCode);
            stream->updateErrorStatus(std::make_pair(status, error_message));*/
        }
    }
}

void StreamMonitor::addStream(std::shared_ptr<StreamInfo> stream)
{
    UrlInfo url_info;
    LOG(info) << "[streamMonitor] Received addStream callback for " << stream->name << ", url:" << secureUrlForLogging(stream->live_proxy_url) << endl;

    /* As of now avoiding webrtc & udp streams */
    if (stream->live_url.find("udp:") != string::npos ||
        stream->live_proxy_url.find("webrtc/") != std::string::npos)
    {
        return;
    }

    /* Add URL into Liveness monitor list, ignore if already present */
    if (stream->live_url.empty() == false && stream->isMainStream)
    {
        url_info.m_livenessUrl = stream->live_url;
        vector<string> livenessMonitorList;
        livenessMonitorList.push_back(stream->live_url);
        addUriListForLivenessMonitor(livenessMonitorList);
    }

    /* Add URL into qos monitor list, ignore if already present */
    if (stream->live_proxy_url.empty() == false)
    {
        std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
        std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
        while (it_monitor != m_qosMonitorList.end())
        {
            UrlInfo elem = *it_monitor;
            if (elem.m_url == stream->live_proxy_url)
            {
                LOG(info) << "URL already present in Stream Monitor" << endl;
                return;
            }
            it_monitor++;
        }
        url_info.m_url = stream->live_proxy_url;
        url_info.m_devName = stream->name;
        url_info.m_streamId = stream->id;
        url_info.m_frameRate = stringToInt(stream->settings.encoderValues.frameRate, DEFAULT_STREAM_FRAME_RATE);
        url_info.m_isMainStream = stream->isMainStream;
        url_info.m_rtspUrlReachable = true;
        m_qosMonitorList.push_back(url_info);
    }

    m_qosThreadSync.signal();
}

void StreamMonitor::addStream(vector<std::shared_ptr<StreamInfo>> streams)
{
    for (uint32_t j = 0; j < streams.size(); j++)
    {
        UrlInfo url_info;
        shared_ptr<StreamInfo> stream = streams[j];
        /* As of now avoiding webrtc & udp streams */
        if (stream->live_url.find("udp:") != string::npos ||
            stream->live_proxy_url.find("webrtc/") != std::string::npos)
        {
            LOG(error) << "udp/webrtc stream not supported in stream_monitor" << endl;
            continue;
        }

        LOG(info) << "[streamMonitor] Received addStream callback for " << stream->name << ", url:" << secureUrlForLogging(stream->live_proxy_url) << endl;

        /* Add URL into Liveness monitor list, ignore if already present */
        if (stream->live_url.empty() == false && stream->isMainStream)
        {
            url_info.m_livenessUrl = stream->live_url;
            vector<string> livenessMonitorList;
            livenessMonitorList.push_back(stream->live_url);
            addUriListForLivenessMonitor(livenessMonitorList);
        }

        /* Add URL into qos monitor list, ignore if already present */
        if (stream->live_proxy_url.empty() == false)
        {
            std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
            std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
            while (it_monitor != m_qosMonitorList.end())
            {
                UrlInfo elem = *it_monitor;
                if (elem.m_url == stream->live_proxy_url)
                {
                    LOG(info) << "URL already present in Stream Monitor" << endl;
                    return;
                }
                it_monitor++;
            }
            url_info.m_url = stream->live_proxy_url;
            url_info.m_devName = stream->name;
            url_info.m_streamId = stream->id;
            url_info.m_frameRate = stringToInt(stream->settings.encoderValues.frameRate, DEFAULT_STREAM_FRAME_RATE);
            url_info.m_isMainStream = stream->isMainStream;
            url_info.m_rtspUrlReachable = true;
            m_qosMonitorList.push_back(url_info);
        }
    }

    m_qosThreadSync.signal();
}

void StreamMonitor::removeStream(std::shared_ptr<StreamInfo> stream)
{
    LOG(info) << "[streamMonitor] Received removeStream callback for:" << stream->name << ", url:" << secureUrlForLogging(stream->live_proxy_url) << endl;

    /* Remove from qos monitor list */
    if (stream->live_proxy_url.empty() == false)
    {
        {
            std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
            std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
            while (it_monitor != m_qosMonitorList.end())
            {
                UrlInfo elem = *it_monitor;
                if (elem.m_url == stream->live_proxy_url)
                {
                    m_qosMonitorList.erase(it_monitor);
                    break;
                }
                it_monitor++;
            }
        }
        std::map<string, struct timeval, std::less<>>::iterator it = g_blackList.find(stream->live_proxy_url);
        if (it != g_blackList.end())
        {
            it = g_blackList.erase(it);
        }
    }

    /* Remove URL from Liveness monitor list */
    if (stream->live_url.empty() == false)
    {
        vector<string> livenessMonitorList;
        livenessMonitorList.push_back(stream->live_url);
        removeUriListFromLivenessMonitor(livenessMonitorList);
    }
    m_qosThreadSync.signal();

    // Wait until stream removal is complete or a max of 10ms
    waitForCompleteRemoval(stream->live_proxy_url);
}

void StreamMonitor::waitForCompleteRemoval(const string& url)
{
    auto start = std::chrono::steady_clock::now();
    const auto timeout = std::chrono::milliseconds(10);
    while (true)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        auto elapsed = std::chrono::steady_clock::now() - start;
        if (elapsed >= timeout)
        {
            break;
        }
        if (isRtspSourceDestroyed(url) == true)
        {
            break;
        }
    }
}

bool StreamMonitor::isRtspSourceDestroyed(const string& url)
{
    std::lock_guard<std::mutex> devicesLock(g_rtspSourceMutex);
    std::map<string, QosRtspClient *, std::less<>>::iterator it = g_rtspSources.find(url);
    if (it == g_rtspSources.end())
    {
       return true;
    }
    return false;
}

void StreamMonitor::removeStream(const string& stream_id)
{
    LOG(info) << "[streamMonitor] Received removeStream callback for streamId:" << stream_id << endl;

    /* Remove from monitor list */
    string liveness_url;
    if (stream_id.empty() == false)
    {
        std::lock_guard<std::mutex> streamUrlLock(m_qosMonitorListMutex);
        std::vector<UrlInfo>::iterator it_monitor = m_qosMonitorList.begin();
        while (it_monitor != m_qosMonitorList.end())
        {
            UrlInfo elem = *it_monitor;
            if (elem.m_streamId == stream_id)
            {
                liveness_url = elem.m_livenessUrl;
                m_qosMonitorList.erase(it_monitor);
                std::map<string, struct timeval, std::less<>>::iterator it = g_blackList.find(elem.m_url);
                if (it != g_blackList.end())
                {
                    it = g_blackList.erase(it);
                }
                break;
            }
            it_monitor++;
        }
    }

    /* Remove URL from Liveness monitor list */
    if (liveness_url.empty() == false)
    {
        vector<string> livenessMonitorList;
        livenessMonitorList.push_back(liveness_url);
        removeUriListFromLivenessMonitor(livenessMonitorList);
    }
    m_qosThreadSync.signal();
}

#ifndef DUMP_QOS_IN_NON_CSV_FORMAT
void printQoSData()
{
    // TODO: Remove possible double lockings, as the scope of this lock is very wide
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    std::map<std::string, shared_ptr<QosMeasurementRecord>, std::less<>>::iterator it;
    static bool printHeader = false;
    StreamMonitor::getInstance()->m_qosResponse.clear();

    if (printHeader == false)
    {
        string qos_header = "camera_name,  time,  transport,  total_packets_received,  total_packets_lost,  elapsed_measurement_time,  "
            "kbps_min,  kbps_avg,  kbps_max,  packet_loss_min(%),  packet_loss_avg(%),  packet_loss_max(%),  inter_packet_gap_ms_min,  "
            "inter_packet_gap_ms_avg,  inter_packet_gap_ms_max,  avg_fps,  avg_framecount,  total_frames_lost,  missing_frames,  errors";
        LOG_QOS("%s\n", qos_header.c_str());
        printHeader = true;
    }

    int num_rtsp_conn = 0;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager && deviceManager->needRtspServer == true && g_records.size() > 0)
    {
        Json::Value jout = vst_rtsp::activeClientSessions();
        num_rtsp_conn = stringToInt(jout.get("activeClientSessions", "0").asString(), 0);
    }

    for (it = g_records.begin(); it != g_records.end(); it++)
    {
        Json::Value info;
        QosRtspClient *rtspSrc = getRtspClient(it->first);
        if (rtspSrc == nullptr)
        {
            continue;
        }

        // Print out stats for each active subsession:
        shared_ptr<QosMeasurementRecord> curQOSRecord = it->second;
        if (curQOSRecord != nullptr)
        {
            RTPSource* src = curQOSRecord->m_rtpSourceProxy;
            if (src == nullptr || !curQOSRecord->m_isQosStarted)
            {
                // Errorneous case, print dummy values with actual error.
                string errorLog = ",  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  [ ],  ";
                if (g_streamFailureCount.count(rtspSrc->getUri()) > 0)
                {
                    LOG_QOS("\n %s ,\t",curQOSRecord->m_fName.c_str());
                    LOG_QOS("%s,\t", getCurrentUtcTime().c_str());
                    LOG_QOS("%s", errorLog.c_str());
                    // New API keys for QOS API are not backward compatible.
                    info["name"] = curQOSRecord->m_fName;
                    info["rtspUrl"] = it->first;
                    info["timestamp"] = getCurrentUtcTime();
                    info["totalPacketsReceived"] = 0;
                    info["totalPacketsLost"] = 0;
                    info["elapsedMeasurementTime"] = 0;
                    info["bitrateKbpsAvg"] = 0;
                    info["bitrateKbpsMin"] = 0;
                    info["bitrateKbpsMax"] = 0;
                    info["packetLossPercentageMin"] = 0;
                    info["packetLossPercentageAvg"] = 0;
                    info["packetLossPercentageMax"] = 0;
                    info["interPacketGapMsMin"] = 0;
                    info["interPacketGapMsAvg"] = 0;
                    info["interPacketGapMsMax"] = 0;
                    info["avgFps"] = 0;
                    info["avgFramecount"] = 0;
                }
                for (const auto &err: g_streamFailureCount)
                {
                    if (rtspSrc->getUri() == err.first)
                    {
                        LOG_QOS("%s", err.second.first.c_str());/* << "at: " << err.second.second;*/
                        break;
                    }
                }
                continue;
            }

            LOG_QOS("\n %s,  ",curQOSRecord->m_fName.c_str());
            LOG_QOS("%s,  ", getCurrentUtcTime().c_str());
            LOG_QOS("%s,  ", rtspSrc->m_tryTcpStreaming == true ? "tcp" : "udp");
            info["name"] = curQOSRecord->m_fName;
            info["rtspUrl"] = it->first;
            info["timestamp"] = getCurrentUtcTime();
            unsigned numPacketsReceived = 0, numPacketsExpected = 0;
            numPacketsReceived = curQOSRecord->m_totNumPacketsReceived;
            numPacketsExpected = curQOSRecord->m_totNumPacketsExpected;

            if (GET_CONFIG().enable_mega_simulation == false && numPacketsReceived == curQOSRecord->m_numPacketsLastIteration)
            {
                LOG(info) << "zero packet during this iteration for device:" << curQOSRecord->m_fName << endl;
                if (rtspSrc)
                {
                    rtspSrc->updateStreamError(rtspSrc->getUri(), "zero packet error");
                }
            }
            curQOSRecord->m_numPacketsLastIteration = numPacketsReceived;

            LOG_QOS("%u,  %d,  ", numPacketsReceived, int(numPacketsExpected - numPacketsReceived));
            info["totalPacketsReceived"] = numPacketsReceived;
            info["totalPacketsLost"] = int(numPacketsExpected - numPacketsReceived);
            {
                unsigned secsDiff = curQOSRecord->m_measurementEndTime.tv_sec
                    - curQOSRecord->m_measurementStartTime.tv_sec;
                int usecsDiff = curQOSRecord->m_measurementEndTime.tv_usec
                    - curQOSRecord->m_measurementStartTime.tv_usec;
                double measurementTime = secsDiff + usecsDiff/1000000.0;

                LOG_QOS("%.2f,  ", measurementTime);
                info["elapsedMeasurementTime"] = measurementTime;
                if (curQOSRecord->m_kbitsPerSecondMax == 0)
                {
                    // special case: we didn't receive any data:
                    LOG_QOS("unavailable,  unavailable,  unavailable,  ");
                    info["bitrateKbpsMin"] = 0;
                    info["bitrateKbpsAvg"] = 0;
                    info["bitrateKbpsMax"] = 0;
                }
                else
                {
                    LOG_QOS("%.2f,  ", curQOSRecord->m_kbitsPerSecondMin);
                    LOG_QOS("%.2f,  ", (measurementTime == 0.0 ? 0.0 : 8*curQOSRecord->m_kBytesTotal/measurementTime));
                    LOG_QOS("%.2f,  ", curQOSRecord->m_kbitsPerSecondMax);
                    info["bitrateKbpsMin"] = curQOSRecord->m_kbitsPerSecondMin;
                    info["bitrateKbpsAvg"] = (measurementTime == 0.0 ? 0.0 : 8*curQOSRecord->m_kBytesTotal/measurementTime);
                    info["bitrateKbpsMax"] = curQOSRecord->m_kbitsPerSecondMax;
                }
                LOG_QOS("%.2f,  ", 100*curQOSRecord->m_packetLossFractionMin);
                info["packetLossPercentageMin"] = 100*curQOSRecord->m_packetLossFractionMin;
                double packetLossFraction = numPacketsExpected == 0 ? 1.0
                    : 1.0 - numPacketsReceived/(double)numPacketsExpected;
                if (packetLossFraction < 0.0) packetLossFraction = 0.0;
                LOG_QOS("%.2f,  ", 100*packetLossFraction);
                LOG_QOS("%.2f,  ", (packetLossFraction == 1.0 ? 100.0 : 100*curQOSRecord->m_packetLossFractionMax));
                info["packetLossPercentageAvg"] = 100*packetLossFraction;
                info["packetLossPercentageMax"] = (packetLossFraction == 1.0 ? 100.0 : 100*curQOSRecord->m_packetLossFractionMax);

                if (src != nullptr)
                {
                    RTPReceptionStatsDB::Iterator statsIter(src->receptionStatsDB());
                    // Assume that there's only one SSRC source (usually the case):
                    RTPReceptionStats* stats = statsIter.next(True);
                    if (stats != nullptr)
                    {
                        info["interPacketGapMsMin"] = stats->minInterPacketGapUS()/1000.0;
                        LOG_QOS("%.2f,  ", stats->minInterPacketGapUS()/1000.0);
                        struct timeval totalGaps = stats->totalInterPacketGaps();
                        double totalGapsMS = totalGaps.tv_sec*1000.0 + totalGaps.tv_usec/1000.0;
                        unsigned m_totNumPacketsReceived = stats->totNumPacketsReceived();
                        LOG_QOS("%.2f,  ", (m_totNumPacketsReceived == 0 ? 0.0 : totalGapsMS/m_totNumPacketsReceived));
                        LOG_QOS("%.2f,  ", stats->maxInterPacketGapUS()/1000.0);
                        info["interPacketGapMsAvg"] = (m_totNumPacketsReceived == 0 ? 0.0 : totalGapsMS/m_totNumPacketsReceived);
                        info["interPacketGapMsMax"] = stats->maxInterPacketGapUS()/1000.0;
                    }
                }
                double avg_fps = (curQOSRecord->m_receivedFps / (measurementTime - curQOSRecord->m_prevMeasurementTime));
                double avg_fc = (curQOSRecord->m_avgFrameCount / (measurementTime - curQOSRecord->m_prevMeasurementTime));
                LOG_QOS("%.2f,  ", avg_fps);
                LOG_QOS("%.2f,  ", avg_fc);
                info["avgFps"] = avg_fps;
                info["avgFramecount"] = avg_fc;
                curQOSRecord->m_receivedFps = 0;
                curQOSRecord->m_avgFrameCount = 0;
                curQOSRecord->m_prevMeasurementTime = measurementTime;
                // Send to prometheus
                double bitrate_min = info.get("bitrateKbpsMin", 0).asDouble();
                double bitrate_avg = info.get("bitrateKbpsAvg", 0).asDouble();
                double bitrate_max = info.get("bitrateKbpsMax", 0).asDouble();
                double inter_packet_gap_min = info.get("interPacketGapMsMin", 0).asDouble();
                double inter_packet_gap_avg = info.get("interPacketGapMsAvg", 0).asDouble();
                double inter_packet_gap_max = info.get("interPacketGapMsMax", 0).asDouble();
                double packet_loss_percent = info.get("packetLossPercentageMax", 0).asDouble();
                double total_packets_lost = info.get("totalPacketsLost", 0).asDouble();
                // Using old metric names for PROMETHEUS to avoid other changes
                GET_PROMETHEUS()->updateQos(avg_fps, curQOSRecord->m_fName, "avg_fps");
                GET_PROMETHEUS()->updateQos(avg_fc, curQOSRecord->m_fName, "avg_frame_count");
                GET_PROMETHEUS()->updateQos(bitrate_min, curQOSRecord->m_fName, "bitrate_kbps_min");
                GET_PROMETHEUS()->updateQos(bitrate_avg, curQOSRecord->m_fName, "bitrate_kbps_avg");
                GET_PROMETHEUS()->updateQos(bitrate_max, curQOSRecord->m_fName, "bitrate_kbps_max");
                GET_PROMETHEUS()->updateQos(inter_packet_gap_min, curQOSRecord->m_fName, "inter_packet_gap_ms_min");
                GET_PROMETHEUS()->updateQos(inter_packet_gap_avg, curQOSRecord->m_fName, "inter_packet_gap_ms_avg");
                GET_PROMETHEUS()->updateQos(inter_packet_gap_max, curQOSRecord->m_fName, "inter_packet_gap_ms_max");
                GET_PROMETHEUS()->updateQos(packet_loss_percent, curQOSRecord->m_fName, "packet_loss_percentage_max");
                GET_PROMETHEUS()->updateQos(numPacketsReceived, curQOSRecord->m_fName, "total_packets_received");
                GET_PROMETHEUS()->updateQos(total_packets_lost, curQOSRecord->m_fName, "total_packets_lost");
                
                GET_PROMETHEUS()->updateRtspConnection(num_rtsp_conn);
            }

            // Print frames lost & missing frameId.
            LOG_QOS("%d,  ", curQOSRecord->m_total_frames_lost);
            curQOSRecord->m_total_frames_lost = 0;
            if (curQOSRecord->m_missedFrames.empty() == false)
            {
                if (curQOSRecord->m_missedFrames.size() > VA_ARG_MAX_BUFFER_LENGTH)
                {
                    curQOSRecord->m_missedFrames.resize(VA_ARG_MAX_BUFFER_LENGTH);
                }
                LOG_QOS("[%s],  ", curQOSRecord->m_missedFrames.c_str());
                curQOSRecord->m_missedFrames.clear();
            }
            else
            {
                LOG_QOS("[ ],  ");
            }

            // Print all the errors if any
            string error;
            for (const auto &err: g_streamFailureCount)
            {
                if (rtspSrc->getUri() == err.first)
                {
                    LOG_QOS("%s", err.second.first.c_str());/* << "at: " << err.second.second;*/
                    error = err.second.first;
                    break;
                }
            }
            info["errors"] = error;
        }
        StreamMonitor::getInstance()->m_qosResponse.append(info);
    }

    std::map<string, struct timeval, std::less<>>::iterator itr;
    for (itr = g_blackList.begin(); itr != g_blackList.end(); itr++)
    {
        Json::Value info;
        const std::string url_name = StreamMonitor::getInstance()->getUriName(itr->first);
        if(!url_name.empty())
        {
            info["name"] = url_name;
            info["rtsp_url"] = itr->first;
            int64_t ts = itr->second.tv_sec;
            ts = ts * 1000000 + itr->second.tv_usec;
            info["timestamp"] = convertEpocToISO8601_2(ts);
            info["errors"] = "Stream Error";
            StreamMonitor::getInstance()->m_qosResponse.append(info);
        }
    }

    /* As of now removing backend qos dumping since rtsp server might be running in separate process/ms */
    //printBackendQoSData();
    g_streamFailureCount.clear();
    return;
}

void printBackendQoSData()
{
    std::map<std::string, shared_ptr<QosMeasurementRecord>, std::less<>>::iterator it;
    for (it = g_records.begin(); it != g_records.end(); it++)
    {
        Json::Value info;
        QosRtspClient *rtspSrc = getRtspClient(it->first);
        if (rtspSrc == nullptr)
        {
            continue;
        }

        // Print out stats for each active subsession:
        shared_ptr<QosMeasurementRecord> curQOSRecord = it->second;
        if (curQOSRecord != nullptr)
        {
            string device_name = curQOSRecord->m_fName + "_backend";
            RTPSource* src = curQOSRecord->getBackendRtpSource();
            if (src == nullptr || !curQOSRecord->m_isQosStarted ||
                g_streamFailureCount.count(rtspSrc->getUri()) > 0)
            {
                // Errorneous case, print dummy values with actual error.
                string errorLog = " ,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0, ";
                LOG_QOS("\n %s ,\t",device_name.c_str());
                LOG_QOS("%s,\t", getCurrentUtcTime().c_str());
                LOG_QOS("%s", errorLog.c_str());

                info["name"] = curQOSRecord->m_fName;
                info["rtsp_url"] = it->first;
                info["timestamp"] = getCurrentUtcTime();
                info["total_packets_received"] = 0;
                info["total_packets_lost"] = 0;
                info["elapsed_measurement_time"] = 0;
                info["bitrate_kbps_avg"] = 0;
                info["bitrate_kbps_min"] = 0;
                info["bitrate_kbps_max"] = 0;
                info["packet_loss_percentage_min"] = 0;
                info["packet_loss_percentage_avg"] = 0;
                info["packet_loss_percentage_max"] = 0;
                info["inter_packet_gap_ms_min"] = 0;
                info["inter_packet_gap_ms_avg"] = 0;
                info["inter_packet_gap_ms_max"] = 0;
                continue;
            }

            LOG_QOS("\n %s,  ",device_name.c_str());
            LOG_QOS("%s,  ", getCurrentUtcTime().c_str());
            LOG_QOS("%s,  ", rtspSrc->m_tryTcpStreaming == true ? "tcp" : "udp");
            info["name"] = device_name;
            info["rtsp_url"] = it->first;
            info["timestamp"] = getCurrentUtcTime();
            unsigned numPacketsReceived = 0, numPacketsExpected = 0;
            numPacketsReceived = curQOSRecord->m_backendTotNumPacketsReceived;
            numPacketsExpected = curQOSRecord->m_backendTotNumPacketsExpected;

            LOG_QOS("%u,  %d,  ", numPacketsReceived, int(numPacketsExpected - numPacketsReceived));
            info["total_packets_received"] = numPacketsReceived;
            info["total_packets_lost"] = int(numPacketsExpected - numPacketsReceived);

            {
                unsigned secsDiff = curQOSRecord->m_backendMeasurementEndTime.tv_sec
                    - curQOSRecord->m_backendMeasurementStartTime.tv_sec;
                int usecsDiff = curQOSRecord->m_backendMeasurementEndTime.tv_usec
                    - curQOSRecord->m_backendMeasurementStartTime.tv_usec;
                double measurementTime = secsDiff + usecsDiff/1000000.0;

                LOG_QOS("%.2f,  ", measurementTime);
                info["elapsed_measurement_time"] = measurementTime;
                if (curQOSRecord->m_backendKbitsPerSecondMax == 0)
                {
                    // special case: we didn't receive any data:
                    LOG_QOS("unavailable,  unavailable,  unavailable,  ");
                    info["bitrate_kbps_min"] = 0;
                    info["bitrate_kbps_avg"] = 0;
                    info["bitrate_kbps_max"] = 0;
                }
                else
                {
                    LOG_QOS("%.2f,  ", curQOSRecord->m_backendKbitsPerSecondMin);
                    LOG_QOS("%.2f,  ", (measurementTime == 0.0 ? 0.0 : 8*curQOSRecord->m_backendKBytesTotal/measurementTime));
                    LOG_QOS("%.2f,  ", curQOSRecord->m_backendKbitsPerSecondMax);
                    info["bitrate_kbps_min"] = curQOSRecord->m_backendKbitsPerSecondMin;
                    info["bitrate_kbps_avg"] = (measurementTime == 0.0 ? 0.0 : 8*curQOSRecord->m_backendKBytesTotal/measurementTime);
                    info["bitrate_kbps_max"] = curQOSRecord->m_backendKbitsPerSecondMax;
                }

                LOG_QOS("%.2f,  ", 100*curQOSRecord->m_backendPacketLossFractionMin);
                info["packet_loss_percentage_min"] = 100*curQOSRecord->m_backendPacketLossFractionMin;
                double packetLossFraction = numPacketsExpected == 0 ? 1.0
                    : 1.0 - numPacketsReceived/(double)numPacketsExpected;
                if (packetLossFraction < 0.0) packetLossFraction = 0.0;
                LOG_QOS("%.2f,  ", 100*packetLossFraction);
                LOG_QOS("%.2f,  ", (packetLossFraction == 1.0 ? 100.0 : 100*curQOSRecord->m_backendPacketLossFractionMax));
                info["packet_loss_percentage_avg"] = 100*packetLossFraction;
                info["packet_loss_percentage_max"] = (packetLossFraction == 1.0 ? 100.0 : 100*curQOSRecord->m_backendPacketLossFractionMax);

                src = curQOSRecord->getBackendRtpSource();
                if (src == nullptr) return;

                RTPReceptionStatsDB::Iterator statsIter(src->receptionStatsDB());
                // Assume that there's only one SSRC source (usually the case):
                RTPReceptionStats* stats = statsIter.next(True);
                if (stats != nullptr)
                {
                    info["inter_packet_gap_ms_min"] = stats->minInterPacketGapUS()/1000.0;
                    LOG_QOS("%.2f,  ", stats->minInterPacketGapUS()/1000.0);
                    struct timeval totalGaps = stats->totalInterPacketGaps();
                    double totalGapsMS = totalGaps.tv_sec*1000.0 + totalGaps.tv_usec/1000.0;
                    unsigned m_backendTotNumPacketsReceived = stats->totNumPacketsReceived();
                    LOG_QOS("%.2f,  ", (m_backendTotNumPacketsReceived == 0 ? 0.0 : totalGapsMS/m_backendTotNumPacketsReceived));
                    LOG_QOS("%.2f,  ", stats->maxInterPacketGapUS()/1000.0);
                    info["inter_packet_gap_ms_avg"] = (m_backendTotNumPacketsReceived == 0 ? 0.0 : totalGapsMS/m_backendTotNumPacketsReceived);
                    info["inter_packet_gap_ms_max"] = stats->maxInterPacketGapUS()/1000.0;
                }
            }
        }
    }
    return;
}
#endif

void StreamMonitor::getQosInfo(Json::Value &response)
{
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    response = m_qosResponse;
    return;
}

#ifdef DUMP_QOS_IN_NON_CSV_FORMAT
// This function will print QoS data on console in readable format instead of csv format.
void printQoSData()
{
    std::lock_guard<std::mutex> qosdumplock(g_qosDumpMutex);
    std::map<std::string, shared_ptr<QosMeasurementRecord>>::iterator it;
    int rcount = 0;

    LOG(info) << endl;
    LOG(info) << yellow << "====================== QoS stats for " << g_records.size() << " streams " << "===========================" << endl;
    for (it = g_records.begin(); it != g_records.end(); it++)
    {
        QosRtspClient *rtspSrc = getRtspClient(it->first);

        // Print out stats for each active subsession:
        shared_ptr<QosMeasurementRecord> curQOSRecord = it->second;
        if (curQOSRecord != nullptr)
        {
            RTPSource* src = curQOSRecord->m_rtpSourceProxy;
            LOG(info) << "-------- " << ++rcount << ". " << curQOSRecord->m_fName << " -----------" << endl;
            if (src == nullptr || !curQOSRecord->m_isQosStarted)
            {
                LOG(info) << "QoS not started for this stream" << endl;
                LOG(info) << "stream failure count:" << g_streamFailureCount.count(rtspSrc->getUri()) << endl;
                for (const auto &err: g_streamFailureCount)
                {
                    if (rtspSrc->getUri() == err.first)
                    {
                        LOG(info) << "   -> \"" << err.second.first << "\" at: " << err.second.second << endl;
                    }
                }
                continue;
            }

            unsigned numPacketsReceived = 0, numPacketsExpected = 0;
            numPacketsReceived = curQOSRecord->m_totNumPacketsReceived;
            numPacketsExpected = curQOSRecord->m_totNumPacketsExpected;

            if (GET_CONFIG().enable_mega_simulation == false && numPacketsReceived == curQOSRecord->m_numPacketsLastIteration)
            {
                LOG(error) << "zero packet during this iteration" << endl;
                if (rtspSrc) {
                    rtspSrc->updateStreamError(rtspSrc->getUri(), "zero packet error");
                }
            }
            curQOSRecord->m_numPacketsLastIteration = numPacketsReceived;

            LOG(info) << "num_packets_received\t" << numPacketsReceived << endl;
            LOG(info) << "num_packets_lost\t" << int(numPacketsExpected - numPacketsReceived) << endl;

            {
                unsigned secsDiff = curQOSRecord->m_measurementEndTime.tv_sec
                    - curQOSRecord->m_measurementStartTime.tv_sec;
                int usecsDiff = curQOSRecord->m_measurementEndTime.tv_usec
                    - curQOSRecord->m_measurementStartTime.tv_usec;
                double measurementTime = secsDiff + usecsDiff/1000000.0;

                LOG(info) << "elapsed_measurement_time\t" << measurementTime << endl;
                LOG(info) << "kBytes_received_total\t" << curQOSRecord->m_kBytesTotal << endl;

                if (curQOSRecord->m_kbitsPerSecondMax == 0)
                {
                    // special case: we didn't receive any data:
                    LOG(info) <<
                        "m_kbitsPerSecondMin\tunavailable" << endl <<
                        "kbits_per_second_ave\tunavailable" << endl <<
                        "m_kbitsPerSecondMax\tunavailable" << endl;
                }
                else
                {
                    LOG(info) << "m_kbitsPerSecondMin\t" << curQOSRecord->m_kbitsPerSecondMin << endl;
                    LOG(info) << "kbits_per_second_ave\t"
                        << (measurementTime == 0.0 ? 0.0 : 8*curQOSRecord->m_kBytesTotal/measurementTime) << endl;
                    LOG(info) << "m_kbitsPerSecondMax\t" << curQOSRecord->m_kbitsPerSecondMax << endl;
                }

                LOG(info) << "packet_loss_percentage_min\t" << 100*curQOSRecord->m_packetLossFractionMin << endl;
                double packetLossFraction = numPacketsExpected == 0 ? 1.0
                    : 1.0 - numPacketsReceived/(double)numPacketsExpected;
                if (packetLossFraction < 0.0) packetLossFraction = 0.0;
                LOG(info) << "packet_loss_percentage_ave\t" << 100*packetLossFraction << endl;
                LOG(info) << "packet_loss_percentage_max\t"
                    << (packetLossFraction == 1.0 ? 100.0 : 100*curQOSRecord->m_packetLossFractionMax) << endl;

                RTPReceptionStatsDB::Iterator statsIter(src->receptionStatsDB());
                // Assume that there's only one SSRC source (usually the case):
                RTPReceptionStats* stats = statsIter.next(True);
                if (stats != nullptr)
                {
                    LOG(info) << "inter_packet_gap_ms_min\t" << stats->minInterPacketGapUS()/1000.0 << endl;
                    struct timeval totalGaps = stats->totalInterPacketGaps();
                    double totalGapsMS = totalGaps.tv_sec*1000.0 + totalGaps.tv_usec/1000.0;
                    unsigned m_totNumPacketsReceived = stats->totNumPacketsReceived();
                    LOG(info) << "inter_packet_gap_ms_ave\t"
                        << (m_totNumPacketsReceived == 0 ? 0.0 : totalGapsMS/m_totNumPacketsReceived) << endl;
                    LOG(info) << "inter_packet_gap_ms_max\t" << stats->maxInterPacketGapUS()/1000.0 << endl;
                }

                double avg_fps = (curQOSRecord->m_receivedFps / (measurementTime - curQOSRecord->m_prevMeasurementTime));
                double avg_fc = (curQOSRecord->m_avgFrameCount / (measurementTime - curQOSRecord->m_prevMeasurementTime));
                LOG(info) << "avg_fps:" << avg_fps
                << ",  avg_framecount:" << avg_fc << endl;
                curQOSRecord->m_receivedFps = 0;
                curQOSRecord->m_avgFrameCount = 0;
                curQOSRecord->m_prevMeasurementTime = measurementTime;
                GET_PROMETHEUS()->updateQos(avg_fc, curQOSRecord->m_fName, "avg_frame_count");
                GET_PROMETHEUS()->updateQos(avg_fps, curQOSRecord->m_fName, "avg_fps");
            }

            // Print all the errors if any
            LOG(info) << "stream failure count:" << g_streamFailureCount.count(rtspSrc->getUri()) << endl;
            for (const auto &err: g_streamFailureCount)
            {
                if (rtspSrc->getUri() == err.first)
                {
                    LOG(info) << "   -> \"" << err.second.first << "\" at: " << err.second.second;
                    break;
                }
            }
        }
    }
    g_streamFailureCount.clear();
    LOG(info) << "=============================================================================" << endl << reset << endl;
    return;
}
#endif



bool StreamMonitor::start()
{
    LOG(info) << "StreamMonitor::start() - Starting StreamMonitor as IMediaDataProducer" << endl;
    StreamEventManager::getInstance().start();
    // StreamMonitor is already started in constructor, so just return true
    return true;
}

void StreamMonitor::stop()
{
    LOG(info) << "StreamMonitor::stop() - Stopping StreamMonitor as IMediaDataProducer" << endl;
    // Note: StreamMonitor is a singleton and should not be stopped
    // This method is provided for interface compliance
    LOG(warning) << "StreamMonitor::stop() called - StreamMonitor is a singleton and should not be stopped" << endl;
}

bool StreamMonitor::isRunning() const
{
    // StreamMonitor singleton is always running
    return true;
}

eMediaType StreamMonitor::getProducerMediaType() const
{
    // StreamMonitor can handle both video and audio streams
    return MediaTypeVideo; // Default to video, can be enhanced to return actual type
}

std::string StreamMonitor::getSourceIdentifier() const
{
    return "StreamMonitor";
}

size_t StreamMonitor::getConsumerCount() const
{
    std::lock_guard<std::mutex> lock(m_streamConsumerLock);
    size_t totalConsumers = 0;
    for (const auto& pair : m_streamConsumers)
    {
        totalConsumers += pair.second.size();
    }
    return totalConsumers;
}

bool StreamMonitor::hasConsumers() const
{
    return getConsumerCount() > 0;
}

void StreamMonitor::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier)
{
    // Use the existing comprehensive registerDataCallback implementation
    std::string url = identifier;
    registerDataCallback(url, consumer);
}

void StreamMonitor::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier, const std::string& startTime, const std::string& endTime)
{
    std::string url = identifier;
    // Only append time parameters if they're provided AND not already in the URL
    if (!startTime.empty() || !endTime.empty())
    {
        bool hasStartTime = (url.find("startTime=") != std::string::npos);
        bool hasEndTime = (url.find("endTime=") != std::string::npos);
        if ((!startTime.empty() && !hasStartTime) || (!endTime.empty() && !hasEndTime))
        {
            url += (url.find("?") == std::string::npos) ? "?" : "&";
            if (!startTime.empty() && !hasStartTime)
            {
                url += "startTime=" + startTime;
                // Add separator if endTime also needs to be added
                if (!endTime.empty() && !hasEndTime)
                {
                    url += "&";
                }
            }
            if (!endTime.empty() && !hasEndTime)
            {
                url += "endTime=" + endTime;
            }
        }
    }
    registerDataCallback(url, consumer);
}

void StreamMonitor::unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier, bool doNotRemoveClient)
{
    // Use the existing comprehensive deregisterDataCallback implementation
    std::string url = identifier;
    deregisterDataCallback(consumer, url, doNotRemoveClient);
}

void StreamMonitor::distributeToConsumers(std::shared_ptr<RawFrameParams> frameData)
{
    std::lock_guard<std::mutex> lock(m_streamConsumerLock);
    for (const auto& pair : m_streamConsumers)
    {
        for (const auto& consumer : pair.second)
        {
            if (consumer)
            {
                consumer->onFrame(frameData);
            }
        }
    }
}

void StreamMonitor::distributeToConsumers(FrameParams& frameParams)
{
    std::lock_guard<std::mutex> lock(m_streamConsumerLock);
    for (const auto& pair : m_streamConsumers)
    {
        for (const auto& consumer : pair.second)
        {
            if (consumer)
            {
                consumer->onFrame(frameParams);
            }
        }
    }
}


