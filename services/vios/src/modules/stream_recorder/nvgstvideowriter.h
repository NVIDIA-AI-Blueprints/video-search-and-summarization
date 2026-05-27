/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "database_schema.h"
#include "gstmux.h"
#include "config.h"
#include <sys/time.h>
#include <ctime>

#include <stdio.h>
#include <stdlib.h>
#include <glib.h>
#include <sys/time.h>
#include <ctime>
#include <chrono>
#include <iomanip>
#include <assert.h>
#include <fts.h>
#include <string.h>
#include <limits>
#include <Scheduler.h>
#if defined(LIVE_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
#include "webrtcstreamproducer.h"
#endif
#ifdef ENABLE_NATIVE_STREAM_MONITOR
#include "native_stream_monitor.h"
#endif
#include "sqlite_helper.h"
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
#include "postgresql_helper.h"
#endif
#include "user_apis.h"
#include "vst_common.h"
#include "modules_apis.h"

inline constexpr int SCHEDULER_THREAD_COUNT = 1;

using namespace nv_vms;
using namespace std;

namespace nv_vms
{
    class NvGstVideoRecorder
    {
    public:
        NvGstVideoRecorder(shared_ptr<StreamInfo> stream, GAsyncQueue *qErrorDeviceID, RecordState record_state) : m_isRunning(false), m_isInError(false), m_qErrorDeviceID(qErrorDeviceID)
        {
            updateRecordingStatus(stream->id, record_state, stream->sensorId);
            m_mux.reset(new GstMux(record_state));
            LOG(verbose) << __METHOD_NAME__ << endl;
            m_updatePipelineScheduler = make_unique<Bosma::Scheduler>(SCHEDULER_THREAD_COUNT);
            m_uri = stream->live_proxy_url;
            m_deviceId = stream->sensorId;
            m_streamId = stream->id;
            this->Start(stream, m_qErrorDeviceID);
        }

        static std::shared_ptr<NvGstVideoRecorder> Create(shared_ptr<StreamInfo> stream, GAsyncQueue *qErrorDeviceID, RecordState record_state)
        {
            std::shared_ptr<NvGstVideoRecorder> capturer(new NvGstVideoRecorder(stream, qErrorDeviceID, record_state));
            return capturer;
        }

        virtual ~NvGstVideoRecorder()
        {
            try {
                LOG(verbose) << __METHOD_NAME__ << endl;
                m_updatePipelineScheduler.reset();
                this->Stop();
            } catch (const std::exception& e) {
                try { LOG(error) << "Exception in ~NvGstVideoRecorder: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
            } catch (...) {
                try { LOG(error) << "Unknown exception in ~NvGstVideoRecorder" << endl; } catch (...) { (void)std::current_exception(); }
            }
        }

        void Start(shared_ptr<StreamInfo> stream, GAsyncQueue *qErrorDeviceID)
        {
            LOG(verbose) << __METHOD_NAME__ << endl;
            if (m_mux->create(stream, qErrorDeviceID) == -1)
            {
                m_isInError = true;
                LOG(error) << "Mux pipeline creation failed." << endl;
                g_async_queue_push(m_qErrorDeviceID, &(stream->sensorId));
                return;
            }
            if (m_isInError == false)
            {
#if defined(LIVE_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
                if (m_uri.find("webrtc") != std::string::npos)
                {
                    m_webrtcDevice = true;
                    WebrtcStreamProducer::getInstance()->registerDataCallback(m_deviceId, m_mux);
                }
                else
#endif
                if (m_uri.find(NV_CSI_SENSOR) != std::string::npos)
                {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
                    m_nativeStream = true;
                    m_mux->setConsumerMediaType(MediaTypeVideo);
                    NativeStreamMonitor::getInstance()->registerDataCallback(m_deviceId, m_mux, BITSTREAM_H265);
#endif
                }
                else
                {
                    StreamMonitor::getInstance()->registerDataCallback(stream->live_proxy_url, m_mux);
                }
                m_isInError = m_mux->play();
                m_isRunning = true;
            }
        }

        int changeRecordStateTo(RecordState new_state)
        {
            int ret = 0;
            if (m_mux)
            {
                if (m_mux->changeRecordStateTo(new_state) == -1)
                {
                    LOG(error) << "Failed to change state to" << new_state << endl;
                    ret = -1;
                }
            }
            return ret;
        }

        int onEvent()
        {
            int ret = 0;
            if (m_mux)
            {
                if (m_mux->onEvent() == -1)
                {
                    ret = -1;
                }
            }
            return ret;
        }

        void updateRecordingStatus(const string &streamId, RecordState new_status, const std::optional<string> &sensorId = std::nullopt)
        {
            if (streamId.empty())
            {
                LOG(error) << "Failed to update Recording status: streamId is Empty" << endl;
                return;
            }
            int ret = GET_DB_INSTANCE()->setRecordingStatus(streamId, new_status, sensorId);
            if (ret == -1)
            {
                LOG(error) << "Failed to update Recording status to database" << endl;
            }
        }

        RecordState getRecordStatus(const string &streamId)
        {
            std::map<std::string, RecordingStatusDBColumns, std::less<>> currStatus;
            VmsErrorCode ret = GET_DB_INSTANCE()->getRecordingStatus(currStatus, streamId);
            if (ret == VmsErrorCode::VMSInternalError)
            {
                return Error;
            }

            RecordState result;
            auto status_it = currStatus.find(streamId);
            if (status_it != currStatus.end())
            {
                result = static_cast<RecordState>(status_it->second.recordingStatus_value);
            }
            else
            {
                LOG(warning) << "Status not found for stream " << streamId;
                result = RecordState::UnknownState;
            }
            if (result == Schedule || result == User ||
                result == AlwaysOn)
            {
                if (isPlaying() == false)
                {
                    return Error;
                }
            }
            if (result == Error)
            {
                return Error;
            }
            return result;
        }

        void Stop()
        {
            LOG(info) << "Enter Stop" << endl;
            m_isRunning = false;

            if (m_mux->isCreated())
            {
#if defined(LIVE_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
                if (m_webrtcDevice)
                {
                    WebrtcStreamProducer::getInstance()->deregisterDataCallback(m_mux, m_deviceId);
                }
                else
#endif
                if (m_nativeStream)
                {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
                    NativeStreamMonitor::getInstance()->deregisterDataCallback(m_mux, m_deviceId, BITSTREAM_H265);
#endif
                }
                else
                {
                    StreamMonitor::getInstance()->deregisterDataCallback(m_mux, m_uri);
                }
                m_mux->destroy();
            }
            LOG(info) << "Exit Stop" << endl;
        }

#ifdef UNIT_TEST
        bool getError()
        {
            return m_mux->getError();
        }
        void disableEOS()
        {
            m_mux->disableEOS();
        }
        bool isRecordGap()
        {
            return m_mux->isRecordGap();
        }
        bool isAudioSupported()
        {
            return m_mux->isAudioSupported();
        }
#endif
        bool isPlaying()
        {
            return m_mux->isPlaying();
        }

        VideoFileInfo getActiveLocalRecording()
        {
            if (!m_mux || !m_mux->isCreated())
            {
                return VideoFileInfo();
            }
            return m_mux->getActiveLocalRecordingSnapshot();
        }

        const std::string& getBoundStreamId() const
        {
            return m_streamId;
        }

        const std::string& getBoundSensorId() const
        {
            return m_deviceId;
        }

        bool IsRunning()
        {
            return (m_isRunning);
        }

    private:
        shared_ptr<GstMux> m_mux = nullptr;
        bool m_isRunning;
        bool m_isInError;
        std::string m_outputFileName;
        std::string m_prevOutputFileName;
        GAsyncQueue *m_qErrorDeviceID;
        std::unique_ptr<Bosma::Scheduler> m_updatePipelineScheduler;
        std::string m_uri;
        std::string m_deviceId;
        std::string m_streamId;
        bool m_webrtcDevice{false};
        bool m_nativeStream{false};
    };
}