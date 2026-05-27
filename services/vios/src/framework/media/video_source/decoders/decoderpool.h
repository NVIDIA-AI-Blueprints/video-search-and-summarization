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

#include <string>
#include <map>
#include <atomic>
#include <mutex>

#include "libasync++/async++.h"
#include "logger.h"
#include "gstnvvideodecoder.h"

inline constexpr int MAX_DECODER_START_ATTEMPTS = 100;
inline constexpr int MAX_DECODER_RESTART_ATTEMPTS = 5;
inline constexpr int MAX_DECODER_START_WAIT = 50;

typedef std::map<string, shared_ptr<GstNvVideoDecoder>, std::less<>> dec_map;
typedef std::pair<bool, async::task<bool>> dec_result;

static bool reCreateDecoder(const shared_ptr<GstNvVideoDecoder>& dec)
{
    LOG(info) << "reCreateDecoder" << endl;
    if(dec->isCreated())
    {
        dec->destroy();
    }
    if (dec->create(true) != 0)
    {
        LOG(error) << "Error in Creating Pipeline" << endl;
        dec->setError();
        return false;
    }
    return true;
}

class DecoderPool
{
    public:
        static DecoderPool* getInstance()
        {
            static DecoderPool instance;
            return &instance;
        }
        ~DecoderPool() {}

        dec_map getDecoderPool()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            return m_decoderPool;
        }

        void removeStreams()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            for(const auto &it : m_decoderPool)
            {
                shared_ptr<GstNvVideoDecoder> dec = it.second;
                if (dec)
                {
                    LOG(info) << "Deleting dec instance: " << dec->getUri() << endl;
                    dec.reset();    // It will destroy the decoder instance.
                }
            }
            m_decoderPool.clear();
	}

        void addStream(const string& url, const std::map<std::string, std::string, std::less<>>& opts = std::map<string, std::string, std::less<>>())
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "Adding stream: " << url << endl;
            dec_map::iterator it = m_decoderPool.find(url);
            if (it == m_decoderPool.end())
            {
                string consumer_name = "video_decoder_pool_" + url;
                shared_ptr<GstNvVideoDecoder> dec(new GstNvVideoDecoder(consumer_name, url, opts));
                if (reCreateDecoder(dec))
                {
                    m_decoderPool[url] = dec;
                }
            }
            else
            {
                LOG(info) << "Found Stream : " << it->first << endl;
            }
        }

        void removeStream(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            dec_map::iterator it = m_decoderPool.find(url);
            if (it != m_decoderPool.end())
            {
                shared_ptr<GstNvVideoDecoder> dec = it->second;
                if (dec)
                {
                    LOG(info) << "Deleting dec instance: " << dec->getUri() << endl;
                    dec.reset();    // It will destroy the decoder instance.
                }
                m_decoderPool.erase(it);
            }
        }
        shared_ptr<GstNvVideoDecoder> getDecoder(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            shared_ptr<GstNvVideoDecoder> dec;
            dec_map::iterator it = m_decoderPool.find(url);
            if (it != m_decoderPool.end())
            {
                dec = it->second;
            }
            return dec;
        }

        void setDecoder(shared_ptr<GstNvVideoDecoder>& dec, const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "=== Set Decoder === " << url << endl;
            m_decoderPool[url] = dec;
        }

        dec_result tryDecoderStart(shared_ptr<GstNvVideoDecoder>& dec, const string & url)
        {
            LOG(info) << url << endl;
            bool result = true;
            std::lock_guard<std::mutex> guard(m_poolLock);
            if (dec.get() == nullptr)
            {
                LOG(error) << "Decoder object is not created" << endl;
                result = false;
            }
            if (dec->isCreated() == false || dec->getError())
            {
                if (reCreateDecoder(dec) == false)
                {
                    LOG(error) << "Error in Creating Pipeline" << endl;
                    result = false;
                }
            }
            // FIX: Don't capture 'dec' in lambda - it creates lingering shared_ptr references
            // The lambda doesn't use any local variables anyway, so use [&] or []
            return std::make_pair (result, async::spawn([]
            {
                return true;
            }));
        }
    private:
        dec_map m_decoderPool;
        std::mutex m_poolLock;
};
