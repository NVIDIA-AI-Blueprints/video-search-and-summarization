/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "gstdemux.h"
#include "network_utils.h"
#include "vstmodule.h"
#include <future>
#include "liveMedia.hh"
#include "NvMediaSource.hh"

inline constexpr int DEFAULT_IDR_INTERVAL = 30;
inline constexpr int COMPENSATION_FRAMES_COUNT = 2;

class RtspSyncPlayback
{
public:
    static RtspSyncPlayback* getInstance()
    {
        static RtspSyncPlayback instance;
        return &instance;
    }

    RtspSyncPlayback()
    {
        m_loop = GET_CONFIG().nv_streamer_loop_playback;
        m_demux = nullptr;
    }
    ~RtspSyncPlayback()
    {
        try {
            stop();
            if (m_globalGstClock)
            {
                gst_object_unref(m_globalGstClock);
                m_globalGstClock = nullptr;
            }
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~RtspSyncPlayback: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~RtspSyncPlayback" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    void start(const string& filename, const int& framecount, int64_t seekoffset = 0)
    {
        updateMaxFrameCount(framecount);

        std::lock_guard<std::mutex> guard(m_fileLock);
        m_syncFilename = filename;
        LOG(info) << "Start synchronous playback with file:" << m_syncFilename << endl;
        m_demux.reset(new GstDeMux(m_syncFilename, MediaTypeVideo));
        if (m_demux)
        {
            m_demux->create_internal();
            m_demux->play_internal();
        }
    }

    void stop()
    {
        LOG(info) << "Stopping rtsp-sync for file: m_syncFilename:" << m_syncFilename << endl;
        std::lock_guard<std::mutex> guard(m_fileLock);
        if (m_demux)
        {
            m_demux->destroy_internal();
            m_demux.reset();
            m_demux = nullptr;
        }
        m_globalFrameId = -1;
        m_syncFilename = "";
    }

    /*
    * This functions called at beginning, Start rtsp-sync playback
    * with one of lowest framecount stream. Assuming all streams are synchronized.
    */
    void enableRtspSyncPlayback(vector<shared_ptr<SensorInfo>>& sensors)
    {
        int least_framecount = INT_MAX;
        string file_path;

        /* Find the stream for which sync-playback can be started */
        for (shared_ptr<SensorInfo> sensor : sensors)
        {
            if (sensor)
            {
                vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                for (shared_ptr<StreamInfo> stream : streams)
                {
                    int framecount = stringToInt(stream->settings.encoderValues.numFrames, 0);
                    if (framecount > 0 && framecount < least_framecount)
                    {
                        least_framecount = framecount;
                        file_path = getFilePathFromUrl(stream->live_url, NV_STREAMER);
                    }
                }
            }
        }

        /* Start the rtsp sync playback */
        if (!file_path.empty())
        {
            start(file_path, least_framecount);
        }
    }

    /*
    * This functions called when new file is uploaded, Check if
    * rtsp-sync playback needs to be updated with this stream
    */
    void updateRtspSyncPlayback(shared_ptr<SensorInfo>& sensor)
    {
        int file_framecount = INT_MAX;
        string file_path;

        if (sensor)
        {
            vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
            if (streams[0])
            {
                file_path = getFilePathFromUrl(streams[0]->live_url, NV_STREAMER);
                file_framecount = stringToInt(streams[0]->settings.encoderValues.numFrames, INT_MAX);
            }
        }

        if (isSynced() == false)
        {
            if (!file_path.empty())
            {
                start(file_path, file_framecount);
                return;
            }
        }
        else
        {
            /* Already sync playback is ON, then update it with new stream */
            if (file_framecount < getMaxFrameCount() && !file_path.empty())
            {
                updateMaxFrameCount(file_framecount);
            }
        }
    }

    /*
    * This functions called when file is deleted, Check if
    * rtsp-sync playback needs to be updated with another stream
    */
    void updateRtspSyncPlayback(const string& filename)
    {
        LOG(info) << "updateRtspSyncPlayback filename:"<<filename<<", syncFileName:"<<m_syncFilename<<endl;
        if (filename.empty())
        {
            return;
        }
        if (m_syncFilename == filename)
        {
            stop();
        }

        /* Start sync playback for other stream */
        int least_framecount = INT_MAX;
        string file_path;
        std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
        if (deviceManager)
        {
            std::vector<shared_ptr<StreamInfo>> streamList;
            streamList = deviceManager->getStreamList();
            for (auto const& stream : streamList)
            {

                int framecount = stringToInt(stream->settings.encoderValues.numFrames, 0);
                if (framecount > 0 && framecount < least_framecount)
                {
                    least_framecount = framecount;
                    file_path = getFilePathFromUrl(stream->live_url, NV_STREAMER);
                }
            }
        }

        if (isSynced() == true)
        {
            if (least_framecount != getMaxFrameCount())
            {
                updateMaxFrameCount(least_framecount);
            }
            return;
        }
        /* Start the rtsp sync playback with new stream */
        if (!file_path.empty())
        {
            start(file_path, least_framecount);
        }
    }

    bool isSynced()
    {
        std::lock_guard<std::mutex> guard(m_fileLock);
        return !m_syncFilename.empty();
    }

    int timeToSync()
    {
        double frame_rate = DEFAULT_FRAMERATE;
        if (m_demux)
        {
            frame_rate = m_demux->getFrameRate();
            frame_rate = frame_rate == 0 ? DEFAULT_FRAMERATE : frame_rate;
        }
        int time_to_wait_ms = (1000 / frame_rate) * (framesToWait() - COMPENSATION_FRAMES_COUNT);
        return time_to_wait_ms;
    }

    int framesToWait()
    {
        int64_t current_frameId = getGlobalFrameId();
        int16_t idr_interval = m_idrInterval == 0 ? DEFAULT_IDR_INTERVAL : m_idrInterval;

        if ((getMaxFrameCount() - current_frameId) <= m_idrInterval)
        {
            /* Last frame or Last GoV case */
            return 0;
        }
        int16_t remainder = current_frameId % idr_interval;
        int16_t frames_to_wait = idr_interval - remainder;
        return frames_to_wait;
    }

    int64_t getGlobalFrameId()
    {
        std::lock_guard<std::mutex> guard(m_frameIdLock);
        return m_globalFrameId;
    }

    void replay()
    {
        /* This wait time is to keep this masterClock in sync with actual mediaSession+demux pipelines */
        int time_to_sync_ms = (1000 / m_demux->getFrameRate()) * COMPENSATION_FRAMES_COUNT;
        usleep(time_to_sync_ms);
        m_demux->play_internal();
    }

    int getMaxFrameCount()
    {
        return m_maxFrameCount;
    }

    void updateMaxFrameCount(const int& new_framecount)
    {
        LOG(info) << "Updating max framecount to:" << new_framecount << endl;
        m_maxFrameCount = new_framecount;

        std::lock_guard<std::mutex> guard(m_demuxerListLock);
        for (auto demux : m_demuxList)
        {
            demux->setMaxFrameCount(m_maxFrameCount);
        }
    }

    void insertDemuxer(shared_ptr<GstDeMux>& demux)
    {
        std::lock_guard<std::mutex> guard(m_demuxerListLock);
        m_demuxList.push_back(demux);
        for (auto demux : m_demuxList)
        {
            demux->setMaxFrameCount(m_maxFrameCount);
        }
    }

    void removeDemuxer(shared_ptr<GstDeMux>& demuxToRemove)
    {
        std::lock_guard<std::mutex> guard(m_demuxerListLock);
        m_demuxList.erase(std::remove_if(m_demuxList.begin(), m_demuxList.end(),
                    [demuxToRemove](const std::shared_ptr<GstDeMux>& ptr) {
                        return ptr == demuxToRemove;
                    }),
                    m_demuxList.end());
    }

    void insertMediaSource(std::shared_ptr<NvMediaSource>& media_source)
    {
        if (media_source)
        {
            std::lock_guard<std::mutex> guard(m_mediaSourceListLock);
            m_mediaSourceList.push_back(media_source);
        }
    }

    void removeMediaSource(std::shared_ptr<NvMediaSource>& media_source)
    {
        if (media_source)
        {
            std::lock_guard<std::mutex> guard(m_mediaSourceListLock);
            m_mediaSourceList.erase(std::remove(m_mediaSourceList.begin(), m_mediaSourceList.end(), media_source), m_mediaSourceList.end());
        }
    }

    size_t getMediaSourceListSize()
    {
        std::lock_guard<std::mutex> guard(m_mediaSourceListLock);
        return m_mediaSourceList.size();
    }

    void startPlayingAllSources()
    {
        std::lock_guard<std::mutex> guard(m_mediaSourceListLock);
        // Start playing from all the sources
        m_globalGstClock = gst_system_clock_obtain();
        m_baseGstTime = gst_clock_get_time(m_globalGstClock);
        LOG(info) << "Starting to play all sources m_baseGstTime:" << m_baseGstTime << endl;
        for (auto source : m_mediaSourceList)
        {
            source->setClock(m_globalGstClock, m_baseGstTime);
        }
        for (auto source : m_mediaSourceList)
        {
            source->play();
        }
        m_isSyncPlaybackStarted = true;
    }


    void addToReplayList(const std::string& filename)
    {
        std::lock_guard<std::mutex> guard(m_replayListLock);
        m_replayList.push_back(filename);
        if (m_replayList.size() == m_mediaSourceList.size())
        {
            m_globalGstClock = gst_system_clock_obtain();
            m_baseGstTime = gst_clock_get_time(m_globalGstClock);
            LOG(info) << "Replaying all sources baseGstTime:" << m_baseGstTime << endl;
            for (auto source : m_mediaSourceList)
            {
                source->setClock(m_globalGstClock, m_baseGstTime);
            }
            for (auto source : m_mediaSourceList)
            {
                source->seekToStart();
            }
            m_replayList.clear();
        }
    }

    bool isSyncPlaybackStarted()
    {
        return m_isSyncPlaybackStarted;
    }

    int getSimulationWaitTime()
    {
        return m_simulationWaitTime;
    }

private:
    std::atomic<int64_t>    m_globalFrameId{-1};
    shared_ptr<GstDeMux>    m_demux;
    std::mutex              m_fileLock;
    std::string             m_syncFilename;
    bool                    m_loop = false;
    std::atomic<int>        m_maxFrameCount = 0;
    std::mutex              m_frameIdLock;
    int16_t                 m_idrInterval = 0;
    vector<shared_ptr<GstDeMux>> m_demuxList;
    std::mutex              m_demuxerListLock;
    std::atomic<int>        m_simulationWaitTime {0};
    GstClock*              m_globalGstClock = nullptr;
    GstClockTime           m_baseGstTime;
    std::mutex              m_mediaSourceListLock;
    std::vector<std::shared_ptr<NvMediaSource>> m_mediaSourceList;
    std::mutex              m_replayListLock;
    std::vector<std::string> m_replayList;
    std::atomic<bool>       m_isSyncPlaybackStarted{false};
};
