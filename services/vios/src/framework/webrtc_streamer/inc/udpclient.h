/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "media_consumer.h"

typedef std::map<string, std::shared_ptr<IMediaDataConsumer>> mediaConsumerMap;
namespace nv_vms
{
    struct UdpStream
    {
        UdpStream () : m_videoPort(0)
                     , m_audioPort(0)
                     , m_type("unknown")
                     , m_audioFreq(8000)
                     , m_videoCodec("h264")
        {}
        UdpStream (const UdpStream& obj)
        {
            this->m_audioPort = obj.m_audioPort;
            this->m_videoPort = obj.m_videoPort;
            this->m_type = obj.m_type;
            this->m_audioFreq = obj.m_audioFreq;
            this->m_videoCodec = obj.m_videoCodec;
        }
        unsigned int m_videoPort;
        unsigned int m_audioPort;
        string m_type;
        int m_audioFreq;
        string m_videoCodec;
    };
    class UdpClient
    {
        public:
            static const string UDP_VIDEO_TYPE;
            static const string UDP_AUDIO_TYPE;
            static const string UDP_VIDEO_AUDIO_TYPE;
            static const string UDP_UNKNOWN_TYPE;
            UdpClient() : m_id("")
            {}
            UdpClient(const string& id, UdpStream& stream) : m_id(id)
                                                           , m_udpStream(stream)
            {}
            virtual ~UdpClient() {}

            virtual int create() { return -1; };
            virtual int create(int freq) { return -1; };
            virtual int create_audio() { return -1; };
            virtual void destroy (bool expect_result) = 0;
            virtual void start () = 0;
            virtual void pause () = 0;
            void setConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const string& media_type)
            {
                m_consumerMap[media_type] = consumer;
            }

            std::shared_ptr<IMediaDataConsumer> getConsumer(const string& media_type)
            {
                mediaConsumerMap::iterator it = m_consumerMap.find(media_type);
                if(it != m_consumerMap.end())
                {
                    return it->second;
                }
                return nullptr;
            }

            virtual EventLoop *getEventLoop() { return nullptr; }
            string getId() { return m_id; }
            string getType() { return m_udpStream.m_type; }
            unsigned int getVideoPort() { return m_udpStream.m_videoPort; }
            unsigned int getAudioPort() { return m_udpStream.m_audioPort; }
            int getAudioFreq() { return m_udpStream.m_audioFreq; }
            string getVideoCodec() { return m_udpStream.m_videoCodec; }
        protected:
            string m_id;
            UdpStream m_udpStream;
            mediaConsumerMap m_consumerMap;
    };
} //nv_vms
