/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "signal_handler.h"
#include "server.h"
#include "user_apis.h"
#include "websocket_apis.h"
#include "Websocket.h"
#include "utils.h"
#include "config.h"
#include "sensor_monitoring.h"
#include "error_code.h"
#include "media/video_source/decoders/decoderpool.h"
#include "profiler.h"
#include "udpclientpool.h"
#include "media/video_source/senders/videosenderpool.h"
#include "vst_common.h"
#include "storage_management.h"
#include "cudaLoader.h"
#include "vstmodule.h"
#include <csignal>
#include "LivePeerConnection.h"
#include "ReplayPeerConnection.h"
#include "StreamBridgeService.h"
#include "utilities/SystemMonitoringTask.h"
#include "garbagecollector.h"
#include <memory>

using namespace std;

struct ShutdownState
{
    static inline std::mutex              lock{};
    static inline std::condition_variable cv{};
    static inline bool                    stopRequested = false;
};

void stopServer()
{
    {
        std::scoped_lock lk(ShutdownState::lock);
        ShutdownState::stopRequested = true;
    }
    ShutdownState::cv.notify_all();
}

void signal_handler( int signal_num )
{
   LOG(info) << "The interrupt signal is (" << signal_num << ")" << endl;
   stopServer();
}

int VmsServer::startGLoop()
{
    m_gmainLoopTask = async::spawn([=]
    {
        m_gmainLoop.run();
        LOG(info) << "Exiting from Main GLoop...." << endl;
    });
    m_gmainLoop.waitForGloopStart();
    LOG(info) << "g_main_loop is running now" << endl;

    gst_init(nullptr, nullptr);
    
    CURLcode errCode = CURLE_OK;
    errCode = curl_global_init(CURL_GLOBAL_ALL);
    CURL_CHECK_ERROR_WITHOUT_CLEANUP(curl_global_init, errCode, -1)

    return errCode;
}

void VmsServer::stopGLoop()
{
    m_gmainLoop.stop();
    gst_deinit ();
    curl_global_cleanup();

    try
    {
        m_gmainLoopTask.get();
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Exception for gmain-loop task: " <<  e.what() << endl;
    }
}

static bool isDeprecatedApi(const string &api)
{
    auto itr = find(g_deprecatedApis.begin(), g_deprecatedApis.end(), api);
    if (itr != g_deprecatedApis.end())
    {
        return true;
    }
    return false;
}

int VmsServer::initialize()
{
    g_hostIp = getHostIP();
    LOG(info) << "Host Ip = " << g_hostIp << endl;

    m_moduleLoader = ModuleLoader::getInstance();
    if (m_moduleLoader == nullptr)
    {
        LOG(error) << "Module loder instance creation failed" << endl;
        return -1;
    }
    if (m_moduleLoader->initialize(m_moduleId) != 0)
    {
        LOG(error) << "LoadModule failed for moduleId:" << m_moduleId << endl;
    }

    m_webServer.reset(new WebServer());
    try
    {
        LOG(verbose) << "SIZE: " << m_func.size() << std::endl;
        m_webServer->registerRESTAPIs(m_func);
        if (m_moduleLoader->getRtspServerMgmtInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getRtspServerMgmtInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_moduleLoader->getPeerConnectionLiveInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getPeerConnectionLiveInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_moduleLoader->getPeerConnectionReplayInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getPeerConnectionReplayInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_moduleLoader->getStreamBridgeInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getStreamBridgeInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_moduleLoader->getRecorderInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getRecorderInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_moduleLoader->getStorageMngtInstance() != nullptr)
        {
            std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
            func = m_moduleLoader->getStorageMngtInstance()->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
    }
    catch (const CivetException & ex)
    {
        LOG(error) << "Cannot Initialize start HTTP server exception:" << ex.what() << std::endl;
    }
    return 0;
}

int VmsServer::start()
{
    std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> func;
    std::map<std::string,WebsocketServerRequestHandler::httpFunction, std::less<>> websocketFunc;

    try
    {
        handleRestAPIs();

#ifdef USE_GRPC_SERVER
        if (m_grpcServer)
        {
#ifdef STREAMBRIDGE_MODULE
            if (m_moduleLoader->getStreamBridgeInstance() != nullptr)
            {
                StreamBridgeService* stream_bridge = static_cast<StreamBridgeService*>(m_moduleLoader->getStreamBridgeInstance());
                if (stream_bridge)
                {
                    func = stream_bridge->getHttpApi();
                    m_grpcServer->addRequestHandler(func);
                    func = m_grpcServer->getHttpApi();
                    stream_bridge->addRequestHandler(func);
                }
            }
#endif // STREAMBRIDGE_MODULE
        }
#endif
        if (m_userApis)
        {
            func = m_userApis->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_senorManagementApis)
        {
            func = m_senorManagementApis->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_peerConnectionManagerApis)
        {
            func = m_peerConnectionManagerApis->getHttpApi();
            m_webServer->registerRESTAPIs(func);
        }
        if (m_websocketApis)
        {
            websocketFunc = m_websocketApis->getWebsocketApis();
            m_webServer->registerWSAPIs(websocketFunc);
            if (m_moduleLoader->getStreamBridgeInstance() != nullptr)
            {
                func = m_moduleLoader->getStreamBridgeInstance()->getHttpApi();
                m_websocketApis->addRequestHandler(func);
            }
            if (m_moduleLoader->getPeerConnectionLiveInstance() != nullptr)
            {
                func = m_moduleLoader->getPeerConnectionLiveInstance()->getHttpApi();
                m_websocketApis->addRequestHandler(func);
            }
            if (m_moduleLoader->getPeerConnectionReplayInstance() != nullptr)
            {
                func = m_moduleLoader->getPeerConnectionReplayInstance()->getHttpApi();
                m_websocketApis->addRequestHandler(func);
            }
        }
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
        if (!GET_CONFIG().remote_vst_address.empty() && m_remoteSensorControlApis)
        {
            std::map<std::string,MessageBus::dataChannelFunction, std::less<>> dataChannelFunc;
            m_messageBus.reset(new MessageBus());
            dataChannelFunc = m_remoteSensorControlApis->getRemoteSensorControlApis();
            m_messageBus->addRequestHandler(dataChannelFunc);
        }
#endif
    }
    catch (const CivetException & ex)
    {
        LOG(error) << "Cannot Initialize start HTTP server exception:" << ex.what() << std::endl;
    }

    LOG(error) << "Main Loop..." << endl;
    m_serverReady = true;

#ifdef SENSOR_MODULE
    m_sensorManagement->notifyVmsRedinessEvent();
    m_sensorManagement->start();
#endif

    // Start system monitoring after all services are ready (if enabled)
    static std::unique_ptr<SystemMonitoringTask> systemMonitor;
    if (GET_CONFIG().enable_system_metric) 
    {
        try {
            LOG(info) << "Server ready - starting system monitoring" << endl;
            systemMonitor = std::make_unique<SystemMonitoringTask>();
            systemMonitor->start();
        }
        catch (const std::exception& e) {
            LOG(error) << "Failed to start system monitoring: " << e.what() << endl;
            systemMonitor.reset(); // Ensure it's null if start failed
        }
        catch (...) {
            LOG(error) << "Failed to start system monitoring: Unknown error" << endl;
            systemMonitor.reset(); // Ensure it's null if start failed
        }
    }
    else 
    {
        LOG(info) << "System monitoring disabled by configuration" << endl;
    }

    // mainloop
    
    std::unique_lock lk(ShutdownState::lock);
    ShutdownState::cv.wait(lk, [] { return ShutdownState::stopRequested; });
    m_serverReady = false;
    //sleep(10);

    // Stop system monitoring gracefully
    if (systemMonitor) {
        LOG(info) << "Stopping system monitoring..." << endl;
        systemMonitor->stop();
        systemMonitor.reset();
    }

    stopGLoop();
    LOG(info) << "Exiting Main Loop" << std::endl;
    LOG(info) << "Garbage collection count: " << GarbageCollector::getInstace()->m_garbageCollector.size() << endl;
    return 0;
}

bool VmsServer::remoteDeviceRequireHTTPS()
{
    const string remoteAddressPath = GET_CONFIG().remote_vst_address;
    if (isSubstring(remoteAddressPath, "https"))
    {
        LOG(info) << "remote device is HTTPS enabled" << endl;
        return true;
    }
    return false;
}

Json::Value VmsServer::getAPIInfo()
{
    Json::Value apiList;
    if (m_peerConnectionManagerApis)
    {
        for (auto it : m_peerConnectionManagerApis->getHttpApi())
        {
            apiList.append(it.first);
        }
    }
    if (m_moduleLoader->getPeerConnectionLiveInstance() != nullptr)
    {
        for (auto it : m_moduleLoader->getPeerConnectionLiveInstance()->getHttpApi())
        {
            apiList.append(it.first);
        }
    }
    if (m_moduleLoader->getPeerConnectionReplayInstance() != nullptr)
    {
        for (auto it : m_moduleLoader->getPeerConnectionReplayInstance()->getHttpApi())
        {
            apiList.append(it.first);
        }
    }
    if (m_moduleLoader->getStreamBridgeInstance() != nullptr)
    {
        for (auto it : m_moduleLoader->getStreamBridgeInstance()->getHttpApi())
        {
            apiList.append(it.first);
        }
    }
    for (auto it : m_userApis->getHttpApi())
    {
        if (isDeprecatedApi(it.first))
        {
            continue;
        }
        apiList.append(it.first);
    }
    for (auto it : m_func)
    {
        apiList.append(it.first);
    }
    return apiList;
}

void VmsServer::checkLibsSanity ()
{
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    NvBufWrapper::getInstance();
#ifdef JETSON_PLATFORM
    if (!GET_OSD_INSTANCE()->isError())
    {
        GET_OSD_INSTANCE()->osd_global_init();
    }
#else
    CudaLoader::getInstance();
#endif
#endif
}

VmsServer::VmsServer(ModuleId module_id)
    : m_serverReady(false)
    , m_moduleId(module_id)
{
    LOG(info) << "#### VST version: " << VST_VERSION << " ####" << endl;
    SignalHandler signalHandler(signal_handler);
#ifndef DEBUG
    // This will generate backtrace & prints on console
    signalHandler.initBacktraceGenerator();
#endif
    startGLoop();
    detectGPU();
    initialize();
    checkLibsSanity();

    SensorManagement* sensorMgmt = GET_SENSOR_MNGT();
    m_sensorManagement.reset(sensorMgmt, SensorManagementObjDeleter());
    m_deviceManager = m_moduleLoader->getDeviceManagerObject();
    if(m_deviceManager->getDeviceType() == TYPE_VST || m_deviceManager->getDeviceType() == TYPE_MMS)
    {
        GET_DB_INSTANCE();
    }
    GET_WEBSOCKET_INSTANCE()->getInstance();

    m_userApis.reset(new UserRESTApis());

#ifdef SENSOR_MODULE
    m_senorManagementApis.reset(new SensorManagementApis(m_sensorManagement, m_deviceManager));
#endif

#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    m_peerConnectionManagerApis.reset(new PeerConnectionManagerApis());
#endif

    m_websocketApis.reset(new WebsocketApis(m_deviceManager));

#ifdef SENSOR_MODULE
    m_remoteSensorControlApis.reset(new RemoteSensorControlApis(m_sensorManagement, m_deviceManager));
#endif

#ifdef USE_GRPC_SERVER
    if (GET_CONFIG().enable_grpc)
    {
        m_grpcServer = std::make_unique<GrpcServer>(m_deviceManager, m_sensorManagement);
    }
#endif
}
void VmsServer::handleRestAPIs()
{
    m_func["/api/help"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        out = this->getAPIInfo();
        return VmsErrorCode::NoError;
    };

    m_func["/api/version"] = [=](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        out["type"] =  m_deviceManager->getDeviceType();
        if(m_deviceManager->getDeviceType() == TYPE_VST)
        {
            out["version"] = VST_VERSION;
        }
        else if(m_deviceManager->getDeviceType() == TYPE_MMS)
        {
            out["version"] = MMS_VERSION;
        }
        else if(m_deviceManager->getDeviceType() == TYPE_STREAMER)
        {
            out["version"] = STREAMER_VERSION;
        }
        return VmsErrorCode::NoError;
    };

    m_func["/v1/metadata"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        // Add version information
        std::string fullVersion = VST_VERSION;
        std::string version = fullVersion.substr(0, fullVersion.find('-'));
        out["version"] = version;
        out["sub-version"] = "";

        // Add license information
        Json::Value licenseInfo;
        licenseInfo["name"] = "NVIDIA-Proprietary";
        licenseInfo["path"] = "/opt/mm/LICENSE";
        licenseInfo["url"] = "file:///opt/mm/LICENSE";
        out["licenseInfo"] = licenseInfo;

        // Add model information (empty array for now)
        Json::Value modelInfo(Json::arrayValue);
        Json::Value emptyModel;
        emptyModel["modelUrl"] = "";
        emptyModel["shortName"] = "";
        modelInfo.append(emptyModel);
        out["modelInfo"] = modelInfo;

        return VmsErrorCode::NoError;
    };

    if (m_webServer)
    {
         m_webServer->registerRESTAPIs(m_func);
    }
}

VmsServer::~VmsServer()
{
  try
  {
    LOG(info) << "Exiting VMS Server" << endl;
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    DecoderPool::getInstance()->removeStreams();
    VideoSenderPool::getInstance()->removeStreams();
    UdpClientPool::getInstance()->clear();
#ifdef JETSON_PLATFORM
    if (!GET_OSD_INSTANCE()->isError())
    {
        GET_OSD_INSTANCE()->osd_global_destroy();
    }
#endif
#endif

#ifdef USE_GRPC_SERVER
    if (m_grpcServer)
    {
        m_grpcServer.reset();
    }
#endif

#ifdef USE_GRPC_CLIENT
    GrpcClient::deleteInstance();
#endif

    m_remoteSensorControlApis.reset();
    m_websocketApis.reset();
    m_peerConnectionManagerApis.reset();
    m_senorManagementApis.reset();
    m_userApis.reset();
    m_sensorManagement.reset();
    m_webServer.reset();
    Websocket::deleteInstance();

    m_moduleLoader->deInitialize();

#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
#ifdef JETSON_PLATFORM
    if (!GET_OSD_INSTANCE()->isError())
    {
        GET_OSD_INSTANCE()->osd_global_destroy();
    }
#endif
#endif
    LOG(info) << "Exited VMS Server" << endl;
  } catch (const std::exception& e) {
    try { LOG(error) << "Exception in ~VmsServer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
  } catch (...) {
    try { LOG(error) << "Unknown exception in ~VmsServer" << endl; } catch (...) { (void)std::current_exception(); }
  }
}