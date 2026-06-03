/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string>
#include <map>
#include <memory>
#include <atomic>
#include <mutex>
#include "logger.h"
#include "gstnvvideoudpclient.h"
#include "gstnvaudioudpclient.h"
#include "udpclient.h"
#include "network_utils.h"

#define DEFAULT_UDP_PORT_RANGE "31000-31200"

using namespace std;
using namespace nv_vms;

typedef std::map<string, shared_ptr<nv_vms::UdpClient>> udpClientMap;
class UdpClientPool
{
    public:
        static UdpClientPool* getInstance()
        {
            static UdpClientPool instance;
            return &instance;
        }
        UdpClientPool()
        {
            string udp_port_range = GET_CONFIG().rtp_udp_port_range;
            if (udp_port_range.empty())
            {
                udp_port_range = DEFAULT_UDP_PORT_RANGE;
            }
            vector<string> port_range = splitString(udp_port_range, "-");
            int startPortIndex = stringToInt(port_range[0], 31000);
            int endPortIndex = stringToInt(port_range[1], 31200);
            for (int pt = startPortIndex; pt < endPortIndex; pt++)
            {
                m_udpPortList.insert({pt, false});
            }
            LOG(info) << "UDP port range = " << port_range[0] << "-" << port_range[1] << endl;

            if (GET_CONFIG().webrtc_port_range != Json::nullValue)
            {
                int webrtc_minPort = GET_CONFIG().webrtc_port_range.get("min", 0).asUInt();
                int webrtc_maxPort = GET_CONFIG().webrtc_port_range.get("max", 0).asUInt();
                if (webrtc_minPort != 0 && webrtc_maxPort != 0)
                {
                    for (int pt = webrtc_minPort; pt < webrtc_maxPort; pt++)
                    {
                        m_webrtcUdpPortList.insert({pt, false});
                    }
                    LOG(info) << "Webrtc port range min:"<< webrtc_minPort <<", max:"<< webrtc_maxPort << endl;
                }
            }
        }
        ~UdpClientPool()
        {
            clear();
        }

        udpClientMap getUdpclientList()
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            return m_clientList;
        }

        shared_ptr<nv_vms::UdpClient> addClient(const string& id, UdpStream& stream)
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            shared_ptr<nv_vms::UdpClient> client = nullptr;
            udpClientMap::iterator it = m_clientList.find(id);
            if (it == m_clientList.end())
            {
                if(stream.m_type == UdpClient::UDP_VIDEO_TYPE ||
                   stream.m_type == UdpClient::UDP_VIDEO_AUDIO_TYPE)
                {
                    if (stream.m_videoPort == 0)
                    {
                        LOG(warning) << "Video port is not provided, using available udp port" << endl;
                        stream.m_videoPort = getUdpPort();
                    }
                    if (stream.m_type == UdpClient::UDP_VIDEO_AUDIO_TYPE && stream.m_audioPort == 0)
                    {
                        LOG(warning) << "Audio port is not provided, using available udp port" << endl;
                        stream.m_audioPort = getUdpPort();
                    }
                    client.reset(new GstUDPVideoClient(id, stream));
                }
                else if(stream.m_type == UdpClient::UDP_AUDIO_TYPE)
                {
                    if (stream.m_audioPort == 0)
                    {
                        LOG(warning) << "Audio port is not provided, using available udp port" << endl;
                        stream.m_audioPort = getUdpPort();
                    }
                    client.reset(new GstUDPAudioClient(id, stream));
                }
                else
                {
                    LOG(error) << "Unsupported media type" << endl;
                    return nullptr;
                }
            }

            if (client)
            {
                m_clientList.insert({id, client});
                LOG(info) << "Added udp Client:" << client << ", id:" << id
                          <<" video port: "<< stream.m_videoPort
                          <<" audio port: "<< stream.m_audioPort << endl;
            }
            return client;
        }

        void removeClient(const string& id)
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            udpClientMap::iterator it = m_clientList.find(id);
            if (it != m_clientList.end())
            {
                /* Ports needs to be freed ? in case of standalone udp device */
                /*shared_ptr<nv_vms::UdpClient> client = it->second;
                if (client)
                {
                    freeUdpPort(client->getVideoPort());
                    freeUdpPort(client->getAudioPort());
                }*/
                m_clientList.erase(it);
            }
        }

        shared_ptr<nv_vms::UdpClient> getClient(const string& id, const string& media)
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            udpClientMap::iterator it = m_clientList.find(id);
            if (it != m_clientList.end())
            {
                shared_ptr<nv_vms::UdpClient> client = it->second;
                if (client->getType() == media || 
                   (client->getType() == UdpClient::UDP_VIDEO_AUDIO_TYPE))
                {
                    return client;
                }
            }
            return nullptr;
        }

        bool isClientExist(const string& id, const string& media)
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            udpClientMap::iterator it = m_clientList.find(id);
            if(it != m_clientList.end())
            {
                shared_ptr<nv_vms::UdpClient> client = it->second;
                if (client->getType() == media || 
                   (client->getType() == UdpClient::UDP_VIDEO_AUDIO_TYPE))
                {
                    return true;
                }
            }
            return false;
        }

        int getUdpPort()
        {
            std::lock_guard<std::mutex> guard(m_udpPortLock);

            int availablePort = -1;
            std::map<int32_t, bool>::iterator it = m_udpPortList.begin();
            while (it != m_udpPortList.end())
            {
                /* Check if port from the list is availabe from system */
                if (it->second == false)
                {
                    int ret = checkIfPortAvailable(it->first);
                    if (ret == 0)
                    {
                        availablePort = it->first;
                        break;
                    }
                    else if (ret < 0)
                    {
                        LOG(error) << "checkIfPortAvailable failed with error:" << ret << endl;
                        return -1;
                    }
                }
                ++it;
            }

            if (availablePort == -1)
            {
                LOG(error) << "Port is not available at this moment" << endl;
                return -1;
            }
            m_udpPortList[availablePort] = true;
            return availablePort;
        }

        int getWebrtcUdpPort()
        {
            std::lock_guard<std::mutex> guard(m_webrtcUdpPortLock);
            int availablePort = -1;
            std::map<int32_t, bool>::iterator it = m_webrtcUdpPortList.begin();
            while (it != m_webrtcUdpPortList.end())
            {
                if (it->second == false)
                {
                    /* Check if port from the list is availabe from system */
                    int ret = checkIfPortAvailable(it->first);
                    if (ret == 0)
                    {
                        availablePort = it->first;
                        break;
                    }
                    else if (ret < 0)
                    {
                        LOG(error) << "checkIfPortAvailable failed with error:" << ret << endl;
                        return -1;
                    }
                }
                ++it;
            }

            if (availablePort == -1)
            {
                LOG(error) << "Webrtc Port is not available at this moment" << endl;
                return -1;
            }
            m_webrtcUdpPortList[availablePort] = true;
            return availablePort;
        }

        void freeWebrtcUdpPort(int port)
        {
            std::lock_guard<std::mutex> guard(m_webrtcUdpPortLock);
            if (port <= 0)
            {
                return;
            }
            std::map<int32_t, bool>::iterator it = m_webrtcUdpPortList.begin();
            while (it != m_webrtcUdpPortList.end())
            {
                /* Check if port from the list is availabe from system */
                if (it->first == port)
                {
                    it->second = false;
                    break;
                }
                ++it;
            }
        }

        void freeUdpPort(int port)
        {
            std::lock_guard<std::mutex> guard(m_udpPortLock);
            if (port <= 0)
            {
                return;
            }
            std::map<int32_t, bool>::iterator it = m_udpPortList.begin();
            while (it != m_udpPortList.end())
            {
                /* Check if port from the list is availabe from system */
                if (it->first == port)
                {
                    it->second = false;
                    break;
                }
                ++it;
            }
        }

        void clear()
        {
            std::lock_guard<std::mutex> guard(m_clientLock);
            for(const auto &it : m_clientList)
            {
                shared_ptr<nv_vms::UdpClient> client = it.second;
                client.reset();
            }
            m_clientList.clear();
	    }

        private:
            udpClientMap m_clientList;
            std::mutex m_clientLock;
            std::map<int32_t, bool> m_udpPortList;
            std::map<int32_t, bool> m_webrtcUdpPortList;
            std::mutex m_udpPortLock;
            std::mutex m_webrtcUdpPortLock;
};
