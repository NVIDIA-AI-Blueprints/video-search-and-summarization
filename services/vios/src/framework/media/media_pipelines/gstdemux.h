/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <gst/gst.h>
#include <gst/gstmessage.h>
#include "config.h"
#include <iomanip>
#include "stream_monitor.h"
#include "event_loop.h"
#include "stream_buffer.h"
#include "media_consumer.h"
#include "sqlite_helper.h"
#include "libasync++/async++.h"
#include "unified_storage_reader.h"
#include "unified_storage_manager.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstClock GstClock;

namespace nv_vms
{
    struct Frame
    {
        uint8_t* m_buf;
        size_t   m_size;
        Frame() : m_buf(nullptr)
                , m_size(0)
        {}
    };
    struct cbData
    {
        cbData() : m_outBuf(nullptr)
                  ,m_source (nullptr)
                  ,m_rate(1.0)
                  ,m_index(0)
                  ,m_fileStartTime(0)
                  ,m_userEndTime(0)
        {

        }
        std::vector<uint8_t>* m_outBuf;
        std::vector<Frame>    m_outFrame;
        GstElement*           m_source = nullptr;
        float                 m_rate;
        long                  m_index;
        uint64_t              m_fileStartTime;
        uint64_t              m_userEndTime;
    };

    class GstDeMux
    {
        public:
            GstDeMux (const std::string& filename, eMediaType mediaType)
                : m_filename(filename)
                , m_mediaType(mediaType)
                , m_sensorId("")
                , m_pipeline(nullptr)
                , m_source(nullptr)
                , m_demux(nullptr)
                , m_queueAudio(nullptr)
                , m_queueVideo(nullptr)
                , m_parserAudio(nullptr)
                , m_parserVideo(nullptr)
                , m_filterAudio(nullptr)
                , m_filterVideo(nullptr)
                , m_sinkAudio(nullptr)
                , m_sinkVideo(nullptr)
                , m_bus(nullptr)
                , m_bus_watch_id(0)
                , m_is_playbin_created(false)
                , m_frameRate(30.0)
                , m_frameId(-1)
                , m_mediaConsumer (nullptr)
                , m_isError(false)
                , m_isEOS(false)
            {
                LOG(info) << "::GstDeMux filename:" << filename << ", m_mediaType:" << mediaTypeAsString(m_mediaType) << endl;
                updateFileMetadata(filename);
                m_loop = GET_CONFIG().nv_streamer_loop_playback == true ? true : false;
            }

            ~GstDeMux ()
            {
                LOG(info) << "~GstDeMux filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
            }

            vector<async::task<bool>> m_vodTasks;
            bool isCreated() { return m_pipeline != nullptr; }
            string getFilename() { return m_filename; }
            void setUrlParams(const string& url_params);
            bool setFileSource();
            void setGstClock(GstClock* global_clock, GstClockTime base_time);
            GstFlowReturn processNewSampleFromSink(GstElement * appsink);
            GstFlowReturn processNewSampleFromSinkAudio(GstElement * appsink);
            GstFlowReturn processNewSampleFromSinkForVodStream(GstElement * appsink);
            int create_internal();
            int create_playbin();
            void play_internal();
            bool pause_internal();
            void resume_internal();
            void sendEoS();
            void destroy_internal();
            friend gboolean busWatchFunc (GstBus *bus, GstMessage *message, gpointer data);
            void seek (int64_t seek_pos , uint64_t end_time, float rate);
            void seekToStart ();
            uint64_t getFileStartTime() { return m_callbackData.m_fileStartTime; }
            uint64_t getActualStartTime() { return m_actualStartTime; }
            void resetActualStartTime() { m_actualStartTime = 0; }
            double getFrameRate() { return m_frameRate; }
            int getFrameCount() { return m_frameCount; }
            string getContainerFormat() { return m_containerFormat; }
            string getVideoCodec() { return m_videoCodec; }
            string getAudioCodec() { return m_audioCodec; }
            int getSampleRate() { return m_sampleRate; }
            int getChannels() { return m_channels; }
            /* Thread-safe snapshot read. Returns a value-copy so the
             * caller can use the result without holding the lock.
             * Writer is on_pad_added_internal() on the GStreamer
             * streaming thread; reader is NvMediaSource::getAacParams()
             * on the live555 task-scheduler thread. */
            string getAudioCodecData()
            {
                std::lock_guard<std::mutex> guard(m_audioCodecDataMutex);
                return m_audioCodecData;
            }
            void on_pad_added_internal (GstElement *demux, GstPad *pad);
            void registerDataCallback(std::string peerid, shared_ptr<IMediaDataConsumer> consumer);
            void deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& peerid);
            void insertFrameId(std::vector<uint8_t>& content);
            void checkEarlyFramesAndSynchronize();
            void updateFileMetadata(const string& filename);
            void setMaxFrameCount(int maxFrameCount) { m_maxFrameCount = maxFrameCount; }
            void setSessionId(std::string session_id) { m_sessionId = session_id; }
            std::string getSessionId() { return m_sessionId; }
            bool isLoopPlayback() { return m_loop; }
            uint64_t getUserEndTime() { return m_callbackData.m_userEndTime; }

            // Cloud storage methods
            bool isCloudStorageEnabled() const { return m_cloudStorageEnabled && m_unifiedStorageReader != nullptr; }
            void cleanupDownloadedFiles();
            void addDownloadedFile(const std::string& filePath);

        private:
            int create_video_pipeline ();
            int create_audio_pipeline ();
            int updateFileList();
            string getNextFile();
            string getFirstAvailableFile();
            int get_keyframe_interval_from_db (string file_location);

            // Cloud storage initialization methods
            bool initUnifiedStorageReader();
            bool initUnifiedStorageManager();

        private:
            std::string             m_filename;
            eMediaType              m_mediaType;
            string                  m_sensorId;
            string                  m_urlParams;
            string                  m_sessionId;
            std::vector<VideoFileInfo> m_fileNameArray;
            GstElement*             m_pipeline = nullptr;
            GstElement*             m_source = nullptr;
            GstElement*             m_demux = nullptr;
            GstElement*             m_queueAudio = nullptr;
            GstElement*             m_queueVideo = nullptr;
            GstElement*             m_parserAudio = nullptr;
            GstElement*             m_parserVideo = nullptr;
            GstElement*             m_filterAudio = nullptr;
            GstElement*             m_filterVideo = nullptr;
            GstElement*             m_sinkAudio = nullptr;
            GstElement*             m_sinkVideo = nullptr;
            GstBus*                 m_bus = nullptr;
            std::vector<uint8_t>    m_outBuf;
            cbData                  m_callbackData;
            guint                   m_bus_watch_id;
            bool                    m_is_playbin_created;
            string                  m_containerFormat;
            string                  m_videoCodec;
            string                  m_audioCodec;
            double                  m_frameRate = DEFAULT_FRAMERATE;
            int                     m_frameCount = 0;
            int                     m_sampleRate = 0;
            int                     m_channels = 0;
            int                     m_bitsPerSample = 0;
            string                  m_audioCodecData;  // see getAudioCodecData()
            std::mutex              m_audioCodecDataMutex;  // guards m_audioCodecData (writer in on_pad_added_internal, reader in getAudioCodecData())
            int64_t                 m_frameId;
            int64_t                 m_idealFrameInterval = 0;
            int64_t                 m_prevFrameTs = 0;
            std::mutex              m_demuxFrameMutex;
            std::condition_variable m_demuxFrameCv;
            std::atomic<int>        m_maxFrameCount{INT_MAX};
            int64_t                 m_prevGstBufTime = -1;
            struct timeval          m_prevPresentationTime = {0};
            size_t                  m_currentFileIndex = 0;
            int64_t                 m_epochStartTime{0};
            int64_t                 m_epochEndTime{0};
            string                  m_prevFileName;
            uint64_t                m_actualStartTime = 0;
            uint64_t                m_fileEndTime = 0;
            GstClock*               m_globalGstClock = nullptr;
            GstClockTime            m_baseGstTime = GST_CLOCK_TIME_NONE;
            bool                    m_loop = false;
        public:
            std::shared_ptr<IMediaDataConsumer>     m_mediaConsumer = nullptr;
            std::mutex                              m_mediaConsumerLock;
            std::atomic<bool>                       m_isError;
            bool                                    m_isEOS;
            std::atomic<bool>                       m_stop{false};
            std::atomic<bool>                       m_isVodStream{false};

            // Cloud storage state
            std::atomic<bool> m_cloudStorageEnabled{false};
            
            // Unified storage reader for cloud storage access
            std::shared_ptr<nv_vms::UnifiedStorageReader> m_unifiedStorageReader = nullptr;
            std::mutex m_storageMutex;
            
            // Unified storage manager for file management
            std::shared_ptr<nv_vms::UnifiedStorageManager> m_unifiedStorageManager = nullptr;
            
            // Track downloaded files for cleanup
            std::string m_prevDownloadedFileName{""};
            std::vector<std::string> m_downloadedFiles;
    };
}
