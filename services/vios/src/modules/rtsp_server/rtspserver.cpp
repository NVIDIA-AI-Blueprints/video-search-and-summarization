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

#include "rtspserver.h"
#include "database.h"
#include "utils.h"
#include "config.h"
#include "logger.h"
#include <chrono>
#include <sys/prctl.h>
#include "RtspSyncPlayback.h"
#include "Live555Config.hh"

using namespace nv_vms;
using namespace std;

constexpr int MAX_SERVER_START_TIMEOUT = 2000;
constexpr int MAX_OUT_PACKET_BUFFER_SIZE_IN_MB = 1 * 1024 * 1024;
#define ACCESS_CONTROL
constexpr int DEFAULT_RTSP_SERVER_RECLAMATION_TEST_SEC = 65;
constexpr int RTSP_SERVER_NAME_MAX_SIZE = 256;
constexpr int DEFAULT_RTSP_PORT_NUMBER = 554;
constexpr int RTP_INITIAL_PORT_NUMBER = 6970;

char* username = nullptr;
char* password = nullptr;
Boolean streamRTPOverTCP = False;
portNumBits tunnelOverHTTPPortNum = 0;
#ifdef DEBUG
int verbosityLevel = 1;
#else
int verbosityLevel = 0;
#endif
Boolean proxyREGISTERRequests = False;

struct AddProxyTask {
    RtspServer* server;
    std::string id;
    std::string name;
    std::string url;
    std::promise<std::string> promise;

    AddProxyTask(RtspServer* s, std::string i, std::string n, std::string u, std::promise<std::string>&& p)
        : server(s), id(std::move(i)), name(std::move(n)), url(std::move(u)), promise(std::move(p)) {}
};

struct RemoveProxyTask {
    RtspServer* server;
    std::string id;
    std::promise<bool> promise;

    RemoveProxyTask(RtspServer* s, std::string i, std::promise<bool>&& p)
        : server(s), id(std::move(i)), promise(std::move(p)) {}
};

RtspServer::RtspServer(u_int16_t port)
        : m_rtspServer(nullptr)
        , m_rtspServerPortNum(port)
        , m_scheduler(nullptr)
        , m_threadRunning(false)
        , m_eventAddStream(nullptr)
        , m_eventRemoveStream(nullptr)
        , m_sms(nullptr)
        , m_authDB(nullptr)
        , m_isError(false)
{
    LOG(info) << "Creating RtspServer instance on port:" << m_rtspServerPortNum << endl;
    DeviceConfig& config = GET_CONFIG();
    // Increase the maximum size of video frames that we can 'proxy' without truncation.
    // (Such frames are unreasonably large; the back-end servers should really not be sending frames this large!)
    OutPacketBuffer::maxSize = MAX_OUT_PACKET_BUFFER_SIZE_IN_MB; // M bytes

    // Begin by setting up our usage environment:
    //m_scheduler = BasicTaskScheduler::createNew();
    //m_env = BasicUsageEnvironment::createNew(*m_scheduler);
  #ifdef ACCESS_CONTROL
    // To implement client access control to the RTSP server, do the following:
    if(config.use_rtsp_authentication)
    {
        m_authDB = new UserAuthenticationDatabase(AUTHENTICATION_DOMAIN, true);
        updateUser(DEFAULT_USERNAME);
    }
    // Repeat the above with each <username>, <password> that you wish to allow
    // access to the server.
  #endif

    if (config.rtsp_server_use_socket_poll == true)
    {
        m_env.useSocketPoll(true);
    }
    // Create the RTSP server. First check with user defined port and then with default port number (554),
    // and alternative port number (8554).
    m_rtspServer = nullptr;
    int rtsp_server_reclamation_test_sec = DEFAULT_RTSP_SERVER_RECLAMATION_TEST_SEC;
    if (config.rtsp_server_reclamation_client_timeout_sec != -1)
    {
        rtsp_server_reclamation_test_sec = config.rtsp_server_reclamation_client_timeout_sec;
    }

    m_rtspServer = DynamicRTSPServer::createNew(m_env, m_rtspServerPortNum,
                    m_authDB, rtsp_server_reclamation_test_sec);
    if (m_rtspServer == nullptr)
    {
        LOG(error) << "Failed to create RTSP server: " << m_env.getResultMsg() << "\n";
        m_isError = true;
        return;
    }
    LOG(info) << "rtsp server created on port:" << m_rtspServerPortNum << endl;

    /* Set the tx rtp packet size for rtsp-server */
    Live555Config& live555Config = Live555Config::instance();
    live555Config.setInt("rtp_packet_size", config.tx_rtp_packet_size);
    live555Config.setInt("proxyClient_jitterBuf_size_ms", config.proxyclient_jitter_buffer_size_ms);
    if (config.enable_packet_pacing == true)
    {
        live555Config.setBoolean("enable_packet_pacing", True);
        live555Config.setInt("packet_pace_time_us", config.rtp_packet_pace_time_us);
        if (config.rtp_packet_batch_size >= 0)
        {
            live555Config.setInt("packet_batch_size", config.rtp_packet_batch_size);
        }
    }

    m_env.mPreferredIface = nullptr;
    if (!config.rtsp_preferred_network_iface.empty())
    {
        int iface_len = config.rtsp_preferred_network_iface.length();
        m_env.mPreferredIface = new char[iface_len + 1];
        strncpy(m_env.mPreferredIface, config.rtsp_preferred_network_iface.c_str(), iface_len);
        m_env.mPreferredIface[iface_len] = '\0';
    }

    if (config.rtsp_in_base_udp_port_num != -1)
    {
        // Set the starting port number of incoming rtp connections.
        m_rtspServer->setProxyclientPortNumber(config.rtsp_in_base_udp_port_num);
    }

    m_notifier = NotificationFactory::CreatePlatformNotification();
    startAsyncWorker();
    m_thread.reset(new std::thread(&RtspServer::start, this));
    m_sync.wait(MAX_SERVER_START_TIMEOUT);
    LOG(info) << "RTSP server is started" << endl;
}


RtspServer::~RtspServer()
{
  try
  {
    LOG(info) << "Deleting Rtspserver port:" << m_rtspServerPortNum << endl;
    stopAsyncWorker();
    if (m_env.mPreferredIface)
    {
        delete[] m_env.mPreferredIface;
        m_env.mPreferredIface = nullptr;
    }
    if (m_eventAddStream)
    {
        m_env.taskScheduler().unscheduleDelayedTask(m_eventAddStream);
        m_eventAddStream = nullptr;
    }
    if (m_eventRemoveStream)
    {
        m_env.taskScheduler().unscheduleDelayedTask(m_eventRemoveStream);
        m_eventRemoveStream = nullptr;
    }
    if(m_authDB)
    {
        delete m_authDB;
    }
    m_env.stop();
    m_thread->join();

    if (GET_CONFIG().nv_streamer_sync_playback == true)
    {
        RtspSyncPlayback::getInstance()->stop();
    }

    if (m_rtspServer != nullptr)
    {
        ((DynamicRTSPServer *)m_rtspServer)->cleanup();
        m_rtspServer = nullptr;
    }
    LOG(info) << "Exited RTSP Server" << endl;
  } catch (const std::exception& e) {
    try { LOG(error) << "Exception in ~RtspServer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
  } catch (...) {
    try { LOG(error) << "Unknown exception in ~RtspServer" << endl; } catch (...) { (void)std::current_exception(); }
  }
}

void RtspServer::updateUser(const char *username)
{
    //update existing user record
    std::string passwordHash = getPasswordHash(username);
    if(passwordHash == EMPTY_STRING)
    {
        LOG(error) << "Invalid password hash" << endl;
        return;
    }
    removeUser(username);
    addUser(username, passwordHash.c_str());
}

void RtspServer::addUser(const char *username, const char *passwordHash)
{
    m_authDB->addUserRecord(username, passwordHash);
}

void RtspServer::removeUser(const char *username)
{
    m_authDB->removeUserRecord(username);
}

vector<StreamDetails> RtspServer::streamList()
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    vector<StreamDetails> list;
    for (auto const& stream : m_streamsList)
    {
        list.push_back(stream.second);
    }
    return list;
}

bool RtspServer::findStreamId(const string& id)
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    map<string, StreamDetails, std::less<>>::iterator it = m_streamsList.find(id);
    return it != m_streamsList.end();
}

int RtspServer::start()
{
    DeviceConfig& config = GET_CONFIG();
    string k8s_pod_name;
    LOG(info) << "LIVE555 Media Server stating....\n";
    LOG(verbose) << "\tversion " << MEDIA_SERVER_VERSION_STRING
        << " (LIVE555 Streaming Media library version "
        << LIVEMEDIA_LIBRARY_VERSION_STRING << ").\n";

    /* Set name for rtsp serevr thread */
    string threadName = "RtspSvrTh_" + to_string(m_rtspServerPortNum);
    prctl(PR_SET_NAME, threadName.c_str(), 0, 0, 0);

    char *urlPrefix = m_rtspServer->rtspURLPrefix();
    m_urlPrefix = urlPrefix;
    delete[] urlPrefix;

    if (config.server_domain_name.empty())
    {
        char *pod_name_env = getenv("POD_NAME");
        if(pod_name_env != nullptr)
        {
            k8s_pod_name = string(pod_name_env);
            config.server_domain_name = k8s_pod_name.empty() ? "" : k8s_pod_name;
        }
    }

    if (!config.server_domain_name.empty())
    {
        char urlBuffer[RTSP_SERVER_NAME_MAX_SIZE];
        if (m_rtspServerPortNum == DEFAULT_RTSP_PORT_NUMBER)
        {
            snprintf(urlBuffer, RTSP_SERVER_NAME_MAX_SIZE-1, "rtsp://%s/", config.server_domain_name.c_str());
        }
        else
        {
            snprintf(urlBuffer, RTSP_SERVER_NAME_MAX_SIZE-1, "rtsp://%s:%d/", config.server_domain_name.c_str(), m_rtspServerPortNum);
        }

        LOG(info) << "Play streams from this server using the URL\n\t" << secureUrlForLogging(urlBuffer) << ",   OR  "<< secureUrlForLogging(m_urlPrefix) << endl;
        m_rtspServerDomainPrefix = urlBuffer;
    }
    else
    {
        LOG(info) << "Play streams from this server using the URL\n\t" << secureUrlForLogging(m_urlPrefix) << endl;
    }

    // Also, attempt to create a HTTP server for RTSP-over-HTTP tunneling.
    // Try first with the default HTTP port (80), and then with the alternative HTTP
    // port numbers (8000 and 8080).

    if (m_rtspServer->setUpTunnelingOverHTTP(80) || m_rtspServer->setUpTunnelingOverHTTP(8000) || m_rtspServer->setUpTunnelingOverHTTP(8080)) {
      LOG(info) << "(We use port " << m_rtspServer->httpServerPortNum() << " for optional RTSP-over-HTTP tunneling, or for HTTP live streaming (for indexed Transport Stream files only).)\n";
    } else {
      LOG(info) << "(RTSP-over-HTTP tunneling is not available.)\n";
    }

    m_sync.signal();

    m_env.mainloop();
    LOG(info) << "Exiting from RTSP Server thread..." << endl;
    return 0; // only to prevent compiler warning
}

static void addProxyTaskFunc(void* clientData)
{
    auto* task = static_cast<AddProxyTask*>(clientData);
    try
    {
        std::string mutableUrl = task->url;
        task->server->addProxy(task->id, task->name, mutableUrl);
        task->promise.set_value(mutableUrl);
    }
    catch (...)
    {
        task->promise.set_exception(std::current_exception());
    }
    delete task;
}

std::string RtspServer::createProxy(const string& id, const string& name, const string& url)
{
    std::promise<std::string> promise;
    std::future<std::string> future = promise.get_future();

    auto* task = new AddProxyTask(this, id, name, url, std::move(promise));
    m_env.taskScheduler().scheduleDelayedTask(0,
        (TaskFunc*)addProxyTaskFunc,
        task
    );

    // Wait for the result with a 1-second timeout
    std::future_status status = future.wait_for(std::chrono::seconds(1));
    if (status == std::future_status::ready)
    {
        return future.get();
    }
    else
    {
        throw std::runtime_error("addProxy operation timed out");
    }
}

int RtspServer::addProxy(const string& id, const string& name, string& url)
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    int ret = 0;
    char* proxyStreamURL = nullptr;
    m_sms = nullptr;
    string streamName = "";
    string proxiedStreamURL = "";
    SensorDetailsDBColumns row;
    bool transport_tcp = false;
    StreamDetails stream;
    portNumBits initialPortNumber = RTP_INITIAL_PORT_NUMBER;
    Boolean multiplexRTCPwithRTP = false;

#ifndef RELEASE
    LOG(info) << "TaskaddStream live_url: " << secureUrlForLogging(url) << endl;
#endif
    // Create a proxy for each "rtsp://" URL specified on the command line:
    streamName = string("live/") + id;
    proxiedStreamURL = url;

    // Check whether we already have a "ServerMediaSession" for t file, Remove it.
    m_rtspServer->lookupServerMediaSession(streamName.c_str(),
      +[](void* clientData, ServerMediaSession* sessionLookedUp)
    {
        RtspServer *rtspServer = (RtspServer*)clientData;
        if (rtspServer)
        {
            rtspServer->m_sms = sessionLookedUp;
        }
    }, this, false);

    if (m_sms != nullptr)
    {
        string current_url = ((ProxyServerMediaSession *)m_sms)->getBackendUrl();
        LOG(info) << "Current RtspUrl: " << secureUrlForLogging(current_url) << " New RtspUrl:" << secureUrlForLogging(proxiedStreamURL) << endl;
        if(current_url == proxiedStreamURL)
        {
            LOG(info) << "New rtsp url is same, Re-using the sms object" << endl;
            goto set_proxy;
        }
        else
        {
            LOG(info) << "Change in rtsp url is observed, Re-creating the sms object" << endl;
            m_rtspServer->deleteServerMediaSession(m_sms);
            m_sms = nullptr;
        }
    }

    if (GET_CONFIG().rtsp_streaming_over_tcp == true ||
        proxiedStreamURL.find("transport=tcp") != string::npos)
    {
        transport_tcp = true;
        string remove_token = "transport=tcp";
        if (proxiedStreamURL.find("?transport=tcp") != string::npos)
        {
            remove_token = "?transport=tcp";
        }
        else if (proxiedStreamURL.find("&transport=tcp") != string::npos)
        {
            remove_token = "&transport=tcp";
        }
        eraseString(proxiedStreamURL, remove_token);
    }

    if (proxiedStreamURL.find("transport=udp") != string::npos)
    {
        transport_tcp = false;
        string remove_token = "transport=udp";
        if (proxiedStreamURL.find("?transport=udp") != string::npos)
        {
            remove_token = "?transport=udp";
        }
        else if (proxiedStreamURL.find("&transport=udp") != string::npos)
        {
            remove_token = "&transport=udp";
        }
        eraseString(proxiedStreamURL, remove_token);
    }

#ifndef RELEASE
    LOG(info) << "Live_url = " << secureUrlForLogging(proxiedStreamURL) << ", transport_tcp: " << transport_tcp << endl;
#endif

    if (transport_tcp)
    {
        // Tell "ProxyServerMediaSession" to stream over TCP, but not using HTTP.
        tunnelOverHTTPPortNum = (portNumBits)(~0);
    }
    if (GET_CONFIG().rtsp_in_base_udp_port_num != -1)
    {
        // Set the starting port number of incoming rtp connections.
        initialPortNumber = GET_CONFIG().rtsp_in_base_udp_port_num;
    }
    if (GET_CONFIG().rtcp_rtp_port_multiplex == true)
    {
        // Single port for RTP & RTCP.
        multiplexRTCPwithRTP = True;
    }

    LOG(info) << "Creating ProxyServerMediaSession for url: " << streamName << endl;
    // Create new "ServerMediaSession" for this stream.
    m_sms = AppProxyServerMediaSession::createNew(this, m_env, m_rtspServer,
                proxiedStreamURL.c_str(), streamName.c_str(),
                username, password, tunnelOverHTTPPortNum, verbosityLevel,
                initialPortNumber, multiplexRTCPwithRTP);
    if (m_sms)
    {
        m_rtspServer->addServerMediaSession(m_sms);
        if (GET_CONFIG().enable_proxy_server_sei_metadata == true)
        {
            ((ProxyServerMediaSession *)m_sms)->setFrameIdSupport(true);
        }
        if (GET_CONFIG().use_sensor_ntp_time == true)
        {
            ((ProxyServerMediaSession *)m_sms)->useCameraNtpTime(true);
        }
        ((ProxyServerMediaSession *)m_sms)->setRxSocketBufSize(GET_CONFIG().rx_socket_buffer_size);
        ((ProxyServerMediaSession *)m_sms)->setTxSocketBufSize(GET_CONFIG().tx_socket_buffer_size);
    }
set_proxy:
    proxyStreamURL = m_rtspServer->rtspURL(m_sms);
    if(proxyStreamURL == nullptr)
    {
         LOG(error) << "Received null proxy url from ServerMediaSession" << endl;
         ret = -1;
         goto notify_exit;
    }

    stream.id = id;
    stream.name = streamName;
    stream.sensorUrl = url;
    stream.sensorName = name;
    stream.proxyUrl = proxyStreamURL;
    m_streamsList.insert ({ id, stream});
    url = proxyStreamURL;

    LOG(info) << "\tPlay this stream using the URL: " << proxyStreamURL << "\n";
    delete[] proxyStreamURL;
notify_exit:
    return ret;
}

void RtspServer::addStream(const string& streamId, const string& url)
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    StreamDetails stream;
    stream.id = streamId;
    stream.name = streamId;
    stream.sensorUrl = url;
    m_streamsList.insert ({ streamId, stream});
    return;
}

int RtspServer::removeProxy(const string& id)
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    map<string, StreamDetails, std::less<>>::iterator it = m_streamsList.find(id);
    if(it == m_streamsList.end())
    {
        LOG(warning) << "Stream object is not found" << endl;
        return 0;
    }
    StreamDetails stream = it->second;
    string streamName = stream.name;
    LOG(info) << "Removing stream: " << streamName << ", id:" << id << endl;

    // Remove ServerMediaSession for this stream.
    m_rtspServer->lookupServerMediaSession(streamName.c_str(),
      +[](void* clientData, ServerMediaSession* sessionLookedUp)
    {
        if(sessionLookedUp) {
            RTSPServer *rtspServer = (RTSPServer*)clientData;
            if (rtspServer)
            {
                rtspServer->deleteServerMediaSession(sessionLookedUp);
            }
        }
    }, m_rtspServer, false);

    m_streamsList.erase(it);

    LOG(info) << "Removed stream: " << streamName << ", id:" << id << endl;
    return 0;
}

static void removeProxyTaskFunc(void* clientData)
{
    auto* task = static_cast<RemoveProxyTask*>(clientData);
    try
    {
        int result = task->server->removeProxy(task->id);
        task->promise.set_value(result == 0);
    }
    catch (...)
    {
        task->promise.set_exception(std::current_exception());
    }
    delete task;
}

bool RtspServer::deleteProxy(const string& id)
{
    std::promise<bool> promise;
    std::future<bool> future = promise.get_future();

    auto* task = new RemoveProxyTask(this, id, std::move(promise));
    m_env.taskScheduler().scheduleDelayedTask(0,
        (TaskFunc*)removeProxyTaskFunc,
        task
    );

    std::future_status status = future.wait_for(std::chrono::seconds(1));
    if (status == std::future_status::ready)
    {
        return future.get();
    }
    else
    {
        throw std::runtime_error("removeProxy operation timed out");
    }
}

unsigned RtspServer::activeClientSessions()
{
    if (m_rtspServer)
    {
        return m_rtspServer->numClientSessions();
    }
    return 0;
}

vector<string> RtspServer::getActiveStreams()
{
    vector<string> active_streams;
    if (m_rtspServer)
    {
        return ((DynamicRTSPServer *)m_rtspServer)->getActiveStreams();
    }
    return active_streams;
}

string RtspServer::originalPrefix()
{
    string rtspServerPrefix;
    char *rtsp_prefix = m_rtspServer->rtspURLPrefix();
    if (rtsp_prefix)
    {
        rtspServerPrefix = rtsp_prefix;
        delete[] rtsp_prefix;
    }
    return rtspServerPrefix;
}

ServerMediaSession* RtspServer::serverMediaSessionForStream(const string& id)
{
    map<string, StreamDetails, std::less<>>::iterator it = m_streamsList.find(id);
    StreamDetails stream = it->second;
    if(it == m_streamsList.end())
    {
        LOG(warning) << "Stream object is not found" << endl;
        return nullptr;
    }
    ServerMediaSession *sms = nullptr;
    if (m_rtspServer)
    {
        string streamName = stream.name;
        sms = ((DynamicRTSPServer *)m_rtspServer)->getServerMediaSessionForStream(streamName.c_str());
    }
    return sms;
}

int RtspServer::removeServerMediaSession(const string& id)
{
    LOG(info) << __METHOD_NAME__ << endl;
    std::lock_guard<std::mutex> lock(m_streamLock);
    map<string, StreamDetails, std::less<>>::iterator it = m_streamsList.find(id);
    if(it == m_streamsList.end())
    {
        LOG(warning) << "Stream object is not found" << endl;
        return -1;
    }
    StreamDetails stream = it->second;
    LOG(info) << "stream.name: " << stream.name << endl;
    if (m_rtspServer)
    {
        ((DynamicRTSPServer *)m_rtspServer)->deleteServerMediaSessionForStream(stream.name.c_str());
    }
    m_streamsList.erase(it);
    return 0;
}

void RtspServer::setVodServer(bool isVodServer)
{
    if (m_rtspServer)
    {
        ((DynamicRTSPServer *)m_rtspServer)->setVodServer(true);
    }
}

void RtspServer::startAsyncWorker()
{
    m_asyncWorkerRunning = true;
    m_asyncWorker = std::thread([this]()
    {
        prctl(PR_SET_NAME, "RtspAsyncWrk", 0, 0, 0);
        while (true)
        {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lock(m_asyncTaskLock);
                m_asyncTaskCv.wait(lock, [this]() {
                    return !m_asyncTasks.empty() || !m_asyncWorkerRunning;
                });
                if (!m_asyncWorkerRunning && m_asyncTasks.empty())
                {
                    return;
                }
                task = std::move(m_asyncTasks.front());
                m_asyncTasks.pop();
            }
            try
            {
                task();
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Async worker task failed: " << e.what() << endl;
            }
        }
    });
}

void RtspServer::stopAsyncWorker()
{
    {
        std::lock_guard<std::mutex> lock(m_asyncTaskLock);
        m_asyncWorkerRunning = false;
    }
    m_asyncTaskCv.notify_one();
    if (m_asyncWorker.joinable())
    {
        m_asyncWorker.join();
    }
    LOG(info) << "Exited stopAsyncWorker" << endl;
}

void RtspServer::postAsyncTask(std::function<void()> task)
{
    {
        std::lock_guard<std::mutex> lock(m_asyncTaskLock);
        m_asyncTasks.push(std::move(task));
    }
    m_asyncTaskCv.notify_one();
}

void RtspServer::updateStreamMetadata(const string& id, const string& vodUrl,
                                      const string& codec, const string& resolution,
                                      const string& framerate, const string& tags)
{
    std::lock_guard<std::mutex> lock(m_streamLock);
    auto it = m_streamsList.find(id);
    if (it != m_streamsList.end())
    {
        it->second.vodUrl = vodUrl;
        it->second.codec = codec;
        it->second.resolution = resolution;
        it->second.framerate = framerate;
        it->second.tags = tags;
    }
}

void RtspServer::registerStreamAsync(const string& id, const string& name,
                                     const string& proxyUrl,
                                     const Json::Value& params)
{
    INotificationInterface* notifier = m_notifier;
    /* Capture by value so the async lambda is independent of the caller. */
    Json::Value paramsCopy = params;
    postAsyncTask([id, name, proxyUrl, paramsCopy, notifier]()
    {
        const string vodUrl           = paramsCopy.get("vodUrl", "").asString();
        const string codec            = paramsCopy.get("codec", "").asString();
        const string resolution       = paramsCopy.get("resolution", "").asString();
        const string framerate        = paramsCopy.get("framerate", "").asString();
        const string tags             = paramsCopy.get("tags", "").asString();
        const string sdpDetectedCodec = paramsCopy.get("sdpDetectedCodec", "").asString();

        const Json::Value audioJson   = paramsCopy.get("audio", Json::Value(Json::nullValue));
        const bool   hasAudio         = audioJson.isObject() && audioJson.get("present", false).asBool();
        const string audioEncoding    = hasAudio ? audioJson.get("encoding", "").asString()    : "";
        const int    audioSampleRate  = hasAudio ? audioJson.get("sample_rate", 0).asInt()      : 0;
        const int    audioChannels    = hasAudio ? audioJson.get("channels",  1).asInt()       : 0;

        std::shared_ptr<DeviceManager> deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
        if (!deviceMngr)
        {
            LOG(error) << "registerStreamAsync: DeviceManager not available for stream: " << id << endl;
            return;
        }

        string sensorId;
        deviceMngr->getSensorIdFromStreamId(id, sensorId);
        shared_ptr<SensorInfo> sensor;
        if (!sensorId.empty())
        {
            sensor = deviceMngr->getSensorInfo(sensorId);
        }

        if (sensor)
        {
            shared_ptr<StreamInfo> stream = sensor->getStream(id);
            if (!stream)
            {
                LOG(warning) << "registerStreamAsync: Sensor " << sensorId
                             << " present, but stream " << id
                             << " missing; adding fresh entry" << endl;
                stream = std::make_shared<StreamInfo>();
                stream->id           = id;
                stream->sensorId     = sensorId;
                stream->isMainStream = true;
                sensor->addStreams(stream);
            }

            stream->name           = name;
            stream->live_proxy_url = proxyUrl;
            stream->replay_url     = vodUrl;

            SensorVideoEncoderSettingsValues vEnc = stream->getvideoEncoderValues();
            if (!codec.empty())      vEnc.encoding   = codec;
            if (!resolution.empty()) vEnc.resolution = resolution;
            if (!framerate.empty())  vEnc.frameRate  = framerate;
            stream->updateVideoEncoderValues(vEnc, /*updateDB=*/false);

            if (hasAudio && !audioEncoding.empty())
            {
                SensorAudioEncoderSettingsValues aEnc = stream->getAudioEncoderValues();
                aEnc.enable      = true;
                aEnc.encoding    = audioEncoding;
                aEnc.sample_rate = std::to_string(audioSampleRate);
                aEnc.channels    = std::to_string(audioChannels);
                if (aEnc.bits_per_sample.empty())
                {
                    aEnc.bits_per_sample = "16";
                }
                stream->updateAudioEncoderValues(aEnc, /*updateDB=*/false);
                LOG(info) << "registerStreamAsync: Stream " << id
                          << " has audio: encoding=" << audioEncoding
                          << " rate=" << audioSampleRate
                          << " channels=" << audioChannels << endl;
            }

            if (!tags.empty())
            {
                sensor->tags = tags;
            }
            LOG(info) << "registerStreamAsync: Enriched existing sensor "
                      << sensorId << " stream " << id
                      << " (in-memory only; DB persist deferred to STREAMING)" << endl;
        }
        else
        {
            /* No sensor in cache or DB */
            shared_ptr<StreamInfo> stream_to_add = std::make_shared<StreamInfo>();
            shared_ptr<SensorInfo> sensor_to_add = std::make_shared<SensorInfo>();

            stream_to_add->id             = id;
            stream_to_add->sensorId       = sensorId;
            stream_to_add->live_proxy_url = proxyUrl;
            stream_to_add->replay_url     = vodUrl;
            stream_to_add->name           = name;
            stream_to_add->isMainStream   = true;

            SensorVideoEncoderSettingsValues& enc_values = stream_to_add->getvideoEncoderValues();
            enc_values.resolution = resolution;
            enc_values.encoding   = codec;
            enc_values.frameRate  = framerate;
            sensor_to_add->tags   = tags;

            if (hasAudio && !audioEncoding.empty())
            {
                SensorAudioEncoderSettingsValues& aenc_values = stream_to_add->getAudioEncoderValues();
                aenc_values.enable          = true;
                aenc_values.encoding        = audioEncoding;
                aenc_values.sample_rate     = std::to_string(audioSampleRate);
                aenc_values.channels        = std::to_string(audioChannels);
                if (aenc_values.bits_per_sample.empty())
                {
                    aenc_values.bits_per_sample = "16";
                }
                LOG(info) << "registerStreamAsync: Stream " << id
                          << " has audio: encoding=" << audioEncoding
                          << " rate=" << audioSampleRate
                          << " channels=" << audioChannels << endl;
            }

            sensor_to_add->addStreams(stream_to_add);
            sensor = deviceMngr->addOrUpdateSensor(*sensor_to_add);
            LOG(info) << "registerStreamAsync: Created new sensor for stream " << id << endl;
        }

        LOG(info) << "registerStreamAsync: Stream registered for: " << id << endl;

        Json::Value payload, event, metadata;
        string vod_url = vodUrl;

        event["camera_id"] = id;
        event["camera_name"] = name;
        event["camera_url"] = proxyUrl;
        event["camera_vod_url"] = vodUrl;
        event["change"] = "camera_streaming";

        /* Reuse the sensor resolved in Step 1; getSensor(id) was unsafe here
         * because it looks up by sensor->id, not by stream id. */
        if(sensor.get() != nullptr && sensor->tags.empty() == false)
        {
            event["tags"] = sensor->tags;
        }

        /* Add stream into stream_monitor */
        if (sensor.get() != nullptr)
        {
            shared_ptr<StreamInfo> stream_info = sensor->getStream(id);
            if (stream_info)
            {
                if (sensor->type == SENSOR_TYPE_MMS_ONVIF)
                {
                    vod_url = stream_info->replay_url;
                    event["camera_vod_url"] = vod_url;
                }
                SensorVideoEncoderSettingsValues& encoder_values = stream_info->getvideoEncoderValues();
                if (!sdpDetectedCodec.empty())
                {
                    encoder_values.encoding = sdpDetectedCodec;
                    metadata["codec"] = encoder_values.encoding;
                    event["metadata"] = metadata;
                }

                /* Add stream event into stream_event_manager */
                StreamEncParam details;
                details.codec = encoder_values.encoding;
                string stream_url = stream_info->live_proxy_url.empty() ? proxyUrl : stream_info->live_proxy_url;
                StreamEventManager::getInstance().sendEvent(stream_url, STREAM_STATUS_STREAMING, details);

                if(deviceMngr && deviceMngr->needStreamMonitoring && deviceMngr->needRtspServer == true)
                {
                    if (stream_info->live_proxy_url.empty())
                    {
                        stream_info->live_proxy_url = proxyUrl;
                        stream_info->replay_url = vod_url;
                    }
                    StreamMonitor* streamMonitor = StreamMonitor::getInstance();
                    if (streamMonitor)
                    {
                        streamMonitor->addStream(stream_info);
                    }
                }
            }
        }

        // Step 3: Send camera_streaming notification
        payload["created_at"] = getCurrentTime();
        payload["source"] = "vst";
        payload["alert_type"] = "camera_status_change";
        payload["event"] = event;
        Json::Value logPayload = payload;
        logPayload["event"]["camera_url"] = secureUrlForLogging(proxyUrl);
        LOG(info) << logPayload.toStyledString() << endl;

        if (notifier)
        {
            notifier->sendMessage(payload);
        }
        else
        {
            LOG(error) << "Notification Manager instance is not created" << endl;
        }
    });
}
