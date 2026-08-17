/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "rtspserver.h"
#include "network_utils.h"
#include "logger.h"
#include <memory>
#include <regex>

inline constexpr int MAX_RTSP_SERVER_COUNT = 512;

namespace nv_vms
{
class RtspLoadBalancer
{
public:
    RtspLoadBalancer() = default;
    ~RtspLoadBalancer() = default;

    int start(uint16_t serversCount, uint16_t startPort)
    {
        m_serversCount = serversCount;
        std::vector<uint16_t> portArray(m_serversCount);

        if (m_serversCount == 0)
        {
            LOG(error) << "Invalid number of servers count" << endl;
            return -1;
        }
        do
        {
            if (serversCount == 1)
            {
                auto rtspserver = std::make_unique<RtspServer>(startPort);
                if (rtspserver && !rtspserver->isError())
                {
                    m_servers.push_back(std::move(rtspserver));
                    portArray[0] = startPort;
                    break;
                }
            }

            /* For multi-instances, check availabality of ports */
            for (int i = 0; i < m_serversCount; i++)
            {
                for (uint16_t port = startPort; port < startPort + MAX_RTSP_SERVER_COUNT; port++) /* Need to check range */
                {
                    int ret_val = checkIfPortAvailable(port, "tcp");
                    if (ret_val == 0 || ret_val == PORT_TIMED_WAIT_STATE)
                    {
                        portArray[i] = port;
                        startPort = port + 1;
                        break;
                    }
                }
            }

            /* Launch the rtsp-server instances */
            for (int i = 0; i < m_serversCount; i++)
            {
                auto rtspserver = std::make_unique<RtspServer>(portArray[i]);
                if (rtspserver && !rtspserver->isError())
                {
                    m_servers.push_back(std::move(rtspserver));
                }
            }
        } while (0);

        m_serversCount = m_servers.size();
        if (m_serversCount > 0)
        {
            LOG(info) << "Created " << m_serversCount << " instances of rtsp-server starting from port:" << portArray[0] << endl;
        }
        else
        {
            LOG(error) << "Failed to create one/all rtsp server instances" << endl;
            return -1;
        }

        auto dbHelper = GET_DB_INSTANCE();
        std::vector<shared_ptr<StreamInfo>> streamList;
        if (0 == dbHelper->getAllStreams(streamList, ModuleLoader::getInstance()->getDeviceId()))
        {
            for (auto const& stream : streamList)
            {
                if (stream && !stream->live_proxy_url.empty() && !stream->id.empty())
                {
                    int port = extractPort(stream->live_proxy_url);
                    if (port != -1)
                    {
                        m_streamListDb[stream->id] = port;
                        LOG(info) << "Rtsp-server port from db:" << m_streamListDb[stream->id] << ", for id:" << stream->id << endl;
                    }
                    else
                    {
                        LOG(error) << "Invalid port for stream id:" << stream->id << " url:" << stream->live_proxy_url << endl;
                    }
                }
            }
        }
        return 0;
    }

    void stop()
    {
        /* Stop all the rtsp-server instances */
        m_servers.clear();
        m_vodServer.reset();
    }

    int startVodServer(uint16_t startPort)
    {
        int server_port = startPort + m_serversCount;
        for (int port = server_port; port < server_port + MAX_RTSP_SERVER_COUNT; port++) /* Need to check range */
        {
            int ret_val = checkIfPortAvailable(port, "tcp");
            if (ret_val == 0 || ret_val == PORT_TIMED_WAIT_STATE)
            {
                server_port = port;
                break;
            }
        }

        auto rtspserver = std::make_unique<RtspServer>(server_port);
        if (rtspserver == nullptr)
        {
            LOG(error) << "Failed to create VoD server on port:" << server_port << endl;
            return -1;
        }
        LOG(info) << "Created Vod rtsp-server on port:" << server_port << endl;
        m_vodServerPort = server_port;
        rtspserver->setVodServer(true);
        m_vodServer = std::move(rtspserver);
        return 0;
    }

    int addProxyStream(const string& streamId, const string& name, string& url)
    {
        int ret = 0;
        int maxRetries = 3; // Total attempts including the first try
        int attempt = 0;
        RtspServer* rtspserver = nullptr;

        if (!url.empty())
        {
            rtspserver = getRtspServerFromDbList(streamId);
            if (rtspserver == nullptr)
            {
                rtspserver = rtspServer();
            }

            while (attempt < maxRetries)
            {
                try
                {
                    std::string proxyUrl = rtspserver->createProxy(streamId, name, url);
                    url = proxyUrl;
                    ret = 0;
                    break;
                }
                catch (const std::runtime_error& e)
                {
                    LOG(error) << "Retry streamId:" << streamId  << ", Attempt " << attempt + 1 << ": Error adding proxy: " << e.what() << endl;
                    usleep(100 * 1000);
                    attempt++;
                }
            }
        }

        if (ret == 0)
        {
            /* Round-robin, but publish the index of the instance the proxy was
             * actually created on (rtspserver) instead of the free-running
             * m_currentServerIndex, which a concurrent addProxyStream() may have
             * advanced between selection and here. Otherwise the client URL
             * derived from m_serverIndexMap can point at a different instance
             * than the one hosting the session -> persistent 404 Stream Not
             * Found and a stream that never starts. */
            std::lock_guard<std::mutex> guard(m_serverMapLock);
            int usedIndex = (rtspserver != nullptr) ? serverIndexOf(rtspserver)
                                                    : static_cast<int>(m_currentServerIndex.load());
            m_serverIndexMap[streamId] = usedIndex;
            m_currentServerIndex = (usedIndex + 1) % m_serversCount;
        }
        return ret;
    }

    int addStream(const string& streamId, string& url)
    {
        int ret = 0;
        RtspServer* rtspserver = getRtspServerFromDbList(streamId);
        if (rtspserver == nullptr)
        {
            rtspserver = rtspServer();
        }
        url = rtspserver->urlPrefix();
        if (!url.empty())
        {
            rtspserver->addStream(streamId, url);

            /* Publish the index of the instance actually used (see
             * addProxyStream()), so the map never diverges from where the
             * session lives under concurrent registration. */
            std::lock_guard<std::mutex> guard(m_serverMapLock);
            int usedIndex = serverIndexOf(rtspserver);
            m_serverIndexMap[streamId] = usedIndex;
            m_currentServerIndex = (usedIndex + 1) % m_serversCount;
        }
        return ret;
    }

    int removeStream(const string& streamId)
    {
        int ret = 0;
        std::lock_guard<std::mutex> guard(m_serverMapLock);
        auto it = m_serverIndexMap.find(streamId);
        if (it != m_serverIndexMap.end())
        {
            RtspServer *rtspserver = m_servers[it->second].get();
            if (rtspserver)
            {
                try
                {
                    bool removed = rtspserver->deleteProxy(streamId);
                    if (!removed)
                    {
                        LOG(error) << "Failed to remove stream id:" << streamId << endl;
                    }
                }
                catch (const std::exception& e)
                {
                    LOG(error) << "Error removing proxy: " << e.what() << endl;
                    ret = -1;
                }
                m_serverIndexMap.erase(it);
            }
        }
        return ret;
    }

    RtspServer* rtspServer()
    {
        std::lock_guard<std::mutex> guard(m_serverMapLock);
        if (m_servers[m_currentServerIndex])
            return m_servers[m_currentServerIndex].get();
        else
            return m_servers[0].get();
    }
    RtspServer* rtspServer(uint16_t index)
    {
        return m_servers[index].get();
    }

    RtspServer* rtspServer(const string& id)
    {
        int serverIndex = 0;
        std::lock_guard<std::mutex> guard(m_serverMapLock);
        auto it = m_serverIndexMap.find(id);
        if (it != m_serverIndexMap.end())
        {
            serverIndex = it->second;
        }
        return m_servers[serverIndex].get();
    }

    RtspServer* getRtspServerFromDbList(const std::string& streamId)
    {
        std::lock_guard<std::mutex> guard(m_streamListDbLock);
        auto it = m_streamListDb.find(streamId);
        if (it != m_streamListDb.end())
        {
            int port = it->second;
            int portIndex = 0;
            for (auto& server : m_servers)
            {
                if (server->getPort() == port)
                {
                    m_currentServerIndex = portIndex;
                    return server.get();
                }
                portIndex++;
            }
        }
        return nullptr;
    }

    RtspServer* getVodServer()
    {
        return m_vodServer.get();
    }

    string getVodUrl(const string& url)
    {
        std::regex rgx(R"(rtsp://([a-zA-Z0-9.-]+):(\d{5})/live/([^\s/]+))");
        return std::regex_replace(url, rgx, "rtsp://$1:" + std::to_string(m_vodServerPort) + "/vod/$3");
    }

    string getVodServerDomainPrefix(const string& serverPrefix)
    {
        std::regex rgx(R"(rtsp://([a-zA-Z0-9.-]+):(\d{5})/)");
        return std::regex_replace(serverPrefix, rgx, "rtsp://$1:" + std::to_string(m_vodServerPort) + "/");
    }

    int getServersCount()
    {
        return m_serversCount;
    }

private:
    /* Index of `server` within m_servers, or the current round-robin index as a
     * fallback. Lets addProxyStream()/addStream() publish the index of the
     * instance the session was actually created on, so a concurrent restore
     * cannot leave m_serverIndexMap pointing at a different instance than the
     * one hosting the session (which surfaced as a persistent "404 Stream Not
     * Found" on the client and a stream that never started). m_servers is
     * immutable between start() and stop(); callers hold m_serverMapLock. */
    int serverIndexOf(const RtspServer* server) const
    {
        for (int i = 0; i < static_cast<int>(m_servers.size()); ++i)
        {
            if (m_servers[i].get() == server)
            {
                return i;
            }
        }
        return static_cast<int>(m_currentServerIndex.load());
    }

    std::vector<std::unique_ptr<RtspServer>> m_servers;
    uint16_t m_serversCount = 0;
    std::atomic<unsigned int> m_currentServerIndex{0};
    std::map<std::string, int, std::less<>> m_serverIndexMap;
    std::mutex m_serverMapLock;
    std::unique_ptr<RtspServer> m_vodServer;
    int32_t m_vodServerPort = 0;
    std::map<std::string, int, std::less<>> m_streamListDb;
    std::mutex m_streamListDbLock;
};
}
