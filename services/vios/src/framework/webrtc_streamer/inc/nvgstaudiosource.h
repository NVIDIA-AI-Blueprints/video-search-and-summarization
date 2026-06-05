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
#include "logger.h"
#include "gstnvaudiodecoder.h"
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <atomic>
#include "libasync++/async++.h"

class NvGstAudioSource : public webrtc::Notifier<webrtc::AudioSourceInterface>
{
public:
    SourceState state() const override { return kLive; }

    bool remote() const override { return true; }

    virtual void AddSink(webrtc::AudioTrackSinkInterface *sink) override
    {
        LOG(info) << "NvGstAudioSource::AddSink " << endl;
        m_gstdecoder->appendWebrtcSink(sink);
        m_sinks.push_back(sink);
    }

    virtual void RemoveSink(webrtc::AudioTrackSinkInterface *sink) override
    {
        LOG(info) << "NvGstAudioSource::RemoveSink " << endl;
        m_gstdecoder->removeWebrtcSink(sink);
        m_sinks.remove(sink);
    }

    NvGstAudioSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    :  m_uri(uri)
    {
        m_gstdecoder.reset(new GstNvAudioDecoder(uri, opts));
        if (m_gstdecoder->create() == -1)
        {
            LOG(error) << "Error in Creating Audio Decoder Pipeline" << endl;
            throw std::invalid_argument( "Error in Creating Audio Decoder Pipeline" );
        }
    }

    virtual ~NvGstAudioSource()
    {
        LOG(info) << __METHOD_NAME__ <<  endl;
        m_gstdecoder->destroy();
    }

private:
    shared_ptr<GstNvAudioDecoder>                  m_gstdecoder = nullptr;
    std::string                                    m_uri;
    std::list<webrtc::AudioTrackSinkInterface *>   m_sinks;
};
