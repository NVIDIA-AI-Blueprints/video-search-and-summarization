/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <set>
#include <atomic>
#include <mutex>

#include "libasync++/async++.h"
#include "logger.h"
#include "utils.h"
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
        ~DecoderPool() = default;

        dec_map getDecoderPool()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            return m_decoderPool;
        }

        /* ------------------------------------------------------------------
         * Ownership API - use these from playback pipelines.
         *
         * Between them these two calls own the entire lifetime of a shared
         * decoder. A caller never creates, starts, destroys or un-pools a
         * decoder itself; it asks for one and later says it is done with it.
         *
         * acquireDecoder() finds-or-creates in a single locked step, so two
         * viewers arriving at the same instant cannot both conclude that the
         * decoder is missing and build one each.
         *
         * releaseDecoder() drops one viewer and tears the decoder down only
         * when that viewer was the last one. Dropping and counting happen
         * together under m_poolLock, so a viewer arriving mid-teardown cannot
         * attach to a decoder that is about to go away.
         *
         * m_attachedViewers tracks viewers from acquire time rather than from
         * the moment they appear in the decoder's own sink list, which closes
         * the window between acquiring a decoder and attaching a consumer to
         * it. Teardown requires both to be empty, so neither list alone can
         * cause a decoder to be destroyed while it is still in use.
         *
         * Lock order is always m_poolLock -> GstNvVideoDecoder::m_videoSinkLock.
         * The decoder never calls back into the pool, so there is no reverse path.
         * ------------------------------------------------------------------ */
        [[nodiscard]] shared_ptr<GstNvVideoDecoder> acquireDecoder(const string& url, const string& peerid,
            const std::map<std::string, std::string, std::less<>>& opts = std::map<string, std::string, std::less<>>())
        {
            std::lock_guard<std::mutex> guard(m_poolLock);

            shared_ptr<GstNvVideoDecoder> dec;
            dec_map::iterator it = m_decoderPool.find(url);
            if (it != m_decoderPool.end() && it->second)
            {
                dec = it->second;
                /* Only hand back a decoder that can actually deliver frames.
                 *
                 * isStopped() matters as much as the other two here. When the last
                 * viewer detaches, removeConsumer() flags the decoder as stopped,
                 * and nothing clears that flag except building a pipeline. A viewer
                 * that arrived between that detach and the pool releasing the
                 * decoder would otherwise be handed one that looks healthy and
                 * never delivers a frame. Rebuilding is safe because a stopped
                 * decoder has no viewers left: anyone who acquired it after it was
                 * stopped would have rebuilt it here first. */
                if (dec->isCreated() == false || dec->getError() || dec->isStopped())
                {
                    LOG(warning) << "Pooled decoder for " << secureUrlForLogging(url) << " is not usable, rebuilding it" << endl;
                    /* Apply this caller's options before rebuilding, so the new
                     * pipeline is built from them. This preserves the previous
                     * ordering, where the caller ran setOptions() before asking
                     * tryDecoderStart() to rebuild a broken decoder. */
                    dec->setOptions(opts);
                    if (reCreateDecoder(dec) == false)
                    {
                        LOG(error) << "Failed to rebuild pooled decoder for " << secureUrlForLogging(url) << endl;
                        m_decoderPool.erase(it);
                        m_attachedViewers.erase(url);
                        return nullptr;
                    }
                }
                LOG(info) << "Reusing pooled decoder for " << secureUrlForLogging(url) << endl;
            }
            else
            {
                LOG(info) << "No pooled decoder for " << secureUrlForLogging(url) << ", creating one" << endl;
                string consumer_name = "video_decoder_pool_" + url;
                dec = std::make_shared<GstNvVideoDecoder>(consumer_name, url, opts);
                if (reCreateDecoder(dec) == false)
                {
                    LOG(error) << "Error in Creating Pipeline for " << secureUrlForLogging(url) << endl;
                    return nullptr;
                }
                m_decoderPool[url] = dec;
            }

            m_attachedViewers[url].insert(peerid);
            LOG(info) << "Acquired decoder for " << secureUrlForLogging(url) << " by " << peerid << ", "
                      << m_attachedViewers[url].size() << " viewer(s) attached" << endl;
            return dec;
        }

        void releaseDecoder(const shared_ptr<GstNvVideoDecoder>& dec, const string& url, const string& peerid)
        {
            if (dec == nullptr)
            {
                LOG(error) << "releaseDecoder called without a decoder for " << secureUrlForLogging(url) << endl;
                return;
            }

            {
                std::lock_guard<std::mutex> guard(m_poolLock);

                dec->removeConsumer(peerid);

                size_t attached = 0;
                std::map<std::string, std::set<std::string>, std::less<>>::iterator vit = m_attachedViewers.find(url);
                if (vit != m_attachedViewers.end())
                {
                    vit->second.erase(peerid);
                    attached = vit->second.size();
                    if (attached == 0)
                    {
                        m_attachedViewers.erase(vit);
                    }
                }

                const size_t sinks = dec->getVideoSinkListSize();
                if (attached > 0 || sinks > 0)
                {
                    LOG(info) << "Released " << peerid << " from " << secureUrlForLogging(url) << ", keeping decoder: "
                              << attached << " viewer(s) attached, " << sinks << " sink(s) connected" << endl;
                    return;
                }

                LOG(info) << "Released " << peerid << " from " << secureUrlForLogging(url)
                          << ", no viewers left, destroying decoder" << endl;

                /* Drop the pool entry only if it still points at this decoder. An
                 * orphaned decoder (one already un-pooled by someone else) must
                 * still be torn down here, but must not take a newer decoder for
                 * the same url down with it. */
                dec_map::iterator it = m_decoderPool.find(url);
                if (it != m_decoderPool.end() && it->second == dec)
                {
                    m_decoderPool.erase(it);
                }
                else
                {
                    LOG(warning) << "Decoder for " << secureUrlForLogging(url) << " was no longer the pooled one" << endl;
                }
            }

            /* Outside m_poolLock on purpose: destroy() blocks until the gstreamer
             * pipeline has stopped, and the decoder is already unreachable through
             * the pool, so nothing can attach to it while this runs. */
            dec->destroy(true);
        }

        /* ------------------------------------------------------------------
         * Low level helpers. These do not track viewers, so a caller that uses
         * them owns the decoder outright and is responsible for its teardown.
         * ------------------------------------------------------------------ */
        void removeStreams()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            for(const auto &it : m_decoderPool)
            {
                shared_ptr<GstNvVideoDecoder> dec = it.second;
                if (dec)
                {
                    LOG(info) << "Deleting dec instance: " << secureUrlForLogging(dec->getUri()) << endl;
                    dec.reset();    // It will destroy the decoder instance.
                }
            }
            m_decoderPool.clear();
            m_attachedViewers.clear();
	}

        void addStream(const string& url, const std::map<std::string, std::string, std::less<>>& opts = std::map<string, std::string, std::less<>>())
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "Adding stream: " << secureUrlForLogging(url) << endl;
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
                LOG(info) << "Found Stream : " << secureUrlForLogging(it->first) << endl;
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
                    LOG(info) << "Deleting dec instance: " << secureUrlForLogging(dec->getUri()) << endl;
                    dec.reset();    // It will destroy the decoder instance.
                }
                m_decoderPool.erase(it);
            }
            m_attachedViewers.erase(url);
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
            LOG(info) << "=== Set Decoder === " << secureUrlForLogging(url) << endl;
            m_decoderPool[url] = dec;
        }

        dec_result tryDecoderStart(shared_ptr<GstNvVideoDecoder>& dec, const string & url)
        {
            LOG(info) << secureUrlForLogging(url) << endl;
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
        /* url -> peer ids that have acquired that decoder and not released it */
        std::map<std::string, std::set<std::string>, std::less<>> m_attachedViewers;
        std::mutex m_poolLock;
};
