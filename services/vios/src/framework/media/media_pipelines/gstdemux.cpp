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

#include <string.h>
#include <iostream>
#include <string.h>
#include <glib/gstdio.h>
#include <errno.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include <Scheduler.h>
#include "gstdemux.h"
#include <limits>
#include <algorithm>
#include <sys/time.h>
#include "rtspserver.h"
#include "liveMedia.hh"
#include "Media.hh"
#include "mm_utils.h"
#include "gst_utils.h"
#include "database.h"
#include "storage_management.h"
#include "RtspSyncPlayback.h"
#include "unified_storage_reader_utils.h"
#include "unified_storage_manager_utils.h"
#include "unified_storage_types.h"

constexpr int EXTN_HEADER_TIME_STAMP_SIZE = 8;

using namespace std;
using namespace nv_vms;

/* playbin flags */
typedef enum {
  GST_PLAY_FLAG_VIDEO         = (1 << 0), /* We want video output */
  GST_PLAY_FLAG_AUDIO         = (1 << 1), /* We want audio output */
  GST_PLAY_FLAG_TEXT          = (1 << 2)  /* We want subtitle output */
} GstPlayFlags;

namespace nv_vms
{
    gboolean demuxBusWatchFunc (GstBus *bus, GstMessage *message, gpointer data)
    {
        GstDeMux* gstDemux = (GstDeMux*)data;
        GError *error = nullptr;
        gchar *name, *debug = nullptr;
        if (gstDemux == nullptr)
        {
            LOG(error) << "DeMuxer object is NULL" << endl;
            goto exit;
        }
        {
            if (message)
            {
                if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ERROR)
                {
                    LOG(error) << "GST_MESSAGE_ERROR" << endl;
                    /* get element name from which error was triggered */
                    name = gst_object_get_path_string (message->src);

                    /* get actual error message and debug info */
                    gst_message_parse_error (message, &error, &debug);
                    if(error != nullptr && name != nullptr)
                    {
                        LOG(error) << "ERROR : " <<  name << error->message << endl;
                        g_error_free (error);
                        g_free (name);
                    }
                    if (debug != nullptr)
                    {
                        LOG (error) << "Additional debug info: " << debug;
                        g_free (debug);
                    }

                    std::lock_guard<std::mutex> consumerLock(gstDemux->m_mediaConsumerLock);
                    if (gstDemux->m_mediaConsumer)
                    {
                        string err_msg = STREAM_MSG_ERROR;
                        FrameParams frame_params;
                        frame_params.m_buffer  = (unsigned char *)(err_msg.data());
                        frame_params.m_size    = err_msg.size();
                        gstDemux->m_mediaConsumer->onFrame(frame_params);
                    }
                }
                else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_EOS)
                {
                    LOG(info) << "GST_MESSAGE_EOS, m_sessionId:" << gstDemux->getSessionId() << endl;
                    if (GET_CONFIG().nv_streamer_sync_file_count > 0 && gstDemux->isLoopPlayback())
                    {
                        RtspSyncPlayback::getInstance()->addToReplayList(gstDemux->getFilename());
                    }
                    else if (!gstDemux->m_isVodStream)
                    {
                        gstDemux->sendEoS();
                    }

                    if (gstDemux->m_isVodStream)
                    {
                        if (gstDemux->getUserEndTime() == 0)
                        {
                            // In case of continuous playback, get next file from here
                            LOG(info) << "This is a continuous playbackstream, Get next file if available" << endl;
                            gstDemux->m_vodTasks.push_back( async::spawn([=] () -> bool
                            {
                                if (gstDemux->setFileSource() == true)
                                {
                                    gstDemux->play_internal();
                                }
                                return true;
                            }));
                        }
                    }
                }
            }
            else
            {
                LOG (info) << "No message on Gstreamer Bus" << endl;
            }
        }
exit:
    return TRUE;
    }
}

namespace
{
    // Range describing one NAL unit inside an Annex B byte-stream Access Unit.
    // 'offset' is at the leading byte of the start code (00 00 01 or 00 00 00 01);
    // 'size' covers the start code and the NAL payload up to (but excluding) the
    // next start code, or to end-of-buffer for the last NAL in the AU.
    struct NalUnitRange
    {
        size_t offset;
        size_t size;
    };

    // Walk an H.264 / H.265 Annex B byte-stream and return every NAL unit it
    // contains. The returned ranges, in order, fully tile [0, size). The
    // start-code grammar is identical for H.264 and H.265, so this is
    // codec-agnostic. Emulation-prevention bytes (00 00 03) cannot be confused
    // with a start code (00 00 01 / 00 00 00 01), so a flat scan is safe.
    std::vector<NalUnitRange>
    splitAUIntoNalUnits(const uint8_t *data, size_t size)
    {
        std::vector<NalUnitRange> result;
        if (data == nullptr || size < 3)
        {
            return result;
        }

        std::vector<size_t> starts;
        starts.reserve(8);

        size_t i = 0;
        while (i + 2 < size)
        {
            if (data[i] == 0x00 && data[i + 1] == 0x00)
            {
                if (data[i + 2] == 0x01)
                {
                    starts.push_back(i);
                    i += 3;
                    continue;
                }
                if ((i + 3) < size && data[i + 2] == 0x00 && data[i + 3] == 0x01)
                {
                    starts.push_back(i);
                    i += 4;
                    continue;
                }
            }
            ++i;
        }

        if (starts.empty())
        {
            return result;
        }

        result.reserve(starts.size());
        for (size_t k = 0; k < starts.size(); ++k)
        {
            NalUnitRange r;
            r.offset = starts[k];
            r.size   = ((k + 1) < starts.size())
                         ? (starts[k + 1] - starts[k])
                         : (size - starts[k]);
            result.push_back(r);
        }
        return result;
    }

    // Returns true iff at least one NAL in the AU is a coded data slice
    // (kIdr/kSlice for H.264; any VCL <= RSV_VCL31 for H.265). The result
    // gates the per-frame throttle and the frame_id increment, both of which
    // must advance once per frame, never per NAL.
    bool auContainsDataNal(const uint8_t *data,
                           const std::vector<NalUnitRange> &ranges,
                           const std::string &codec)
    {
        const bool isH265 = iequals(codec, "h265");
        for (const auto &r : ranges)
        {
            if (isH265)
            {
                H265NaluType t = parseH265NaluType(data + r.offset, (ssize_t)r.size);
                if (isValidDataNAL(t, codec))
                {
                    return true;
                }
            }
            else
            {
                NaluType t = parseH264NaluType(data + r.offset, (ssize_t)r.size);
                if (isValidDataNAL(t, codec))
                {
                    return true;
                }
            }
        }
        return false;
    }

    // Returns true if a NAL of this type should be dropped before being
    // forwarded to the media consumer. Centralises the skip-list shared by
    // every per-NAL emission loop in this file (live + VOD, H.264 + H.265):
    //   - H.264 : Access Unit Delimiter (kAud) and the RTP-only aggregation
    //             types kStapA..kMtap24 (these can't appear in MP4 / Annex-B
    //             streams normally, but we keep parity with the legacy code
    //             that filtered them defensively).
    //   - H.265 : the spec-equivalent Access Unit Delimiter (AUD_NUT).
    // If a new NAL type ever needs to be added to (or removed from) the skip
    // list, this is the single place to update it.
    inline bool shouldSkipNalForEmission(uint8_t nal_type, const std::string &codec)
    {
        if (iequals(codec, "h265"))
        {
            return nal_type == H265NaluType::AUD_NUT;
        }
        return nal_type == NaluType::kAud ||
               (nal_type >= NaluType::kStapA && nal_type <= NaluType::kMtap24);
    }
} // namespace

/* called when the appsink notifies us that there is a new buffer ready for
 * processing.
 *
 * The video pipeline is configured for Access-Unit alignment (h264parse /
 * h265parse alignment="au"), so each appsink callback delivers exactly one
 * complete AU. Multi-slice frames therefore arrive as a single buffer rather
 * than being clocked-out one slice at a time -- the latter (NAL alignment +
 * appsink sync=true) used to spread an 8-slice frame across 8 frame intervals
 * because each per-NAL buffer carried its own DTS slot in the pipeline clock.
 *
 * Downstream consumers still expect one NAL per onFrame() call (parameter
 * sets are tracked separately, IDR boundaries inspect a single NAL header,
 * the multi-slice "prepend SPS/PPS only on the first slice" logic in
 * media_consumer::parseAndCreateFrame depends on per-NAL delivery, etc.), so
 * we split the AU into individual NALs locally and emit them back-to-back,
 * all sharing the same PTS, presentationTime and frame_id.
 */
GstFlowReturn GstDeMux::processNewSampleFromSink (GstElement * appsink)
{
    GstSample *sample = nullptr;
    GstBuffer *gstBuffer = nullptr;
    GstMapInfo map;
    int64_t current_frame_ts = 0;

    long& frame_index = m_callbackData.m_index;
    /* Get file start time to use in adding timestamp information */
    int64_t file_start_time = m_callbackData.m_fileStartTime;

    /* Pull one Access Unit from the appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            frame_index = 0;
            return GST_FLOW_OK;
        }
        return GST_FLOW_ERROR;
    }

    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (info) << "No more buffers available from app sink element" << endl;
        frame_index = 0;
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    /* Empty buffer -> nothing to emit, but exit cleanly. */
    if (map.size == 0 || map.data == nullptr)
    {
        gst_buffer_unmap (gstBuffer, &map);
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    /* AU PTS / DTS in milliseconds. One value per AU, shared by every NAL we
     * emit below. */
    int64_t gstBuffer_time = 0;
    if (GST_BUFFER_PTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        gstBuffer_time = (GST_BUFFER_PTS (gstBuffer) / 1000000);
    }
    else if (GST_BUFFER_DTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        gstBuffer_time = (GST_BUFFER_DTS (gstBuffer) / 1000000);
    }

    file_start_time += gstBuffer_time;
    current_frame_ts = file_start_time;

    /* User-requested end time reached -> drain pipeline cleanly */
    if (m_callbackData.m_userEndTime != 0 &&
        ((uint64_t)current_frame_ts) >= m_callbackData.m_userEndTime)
    {
        if (m_isEOS == false)
        {
            LOG(info) << "End time is reached, sending EOS, m_sessionId:" << m_sessionId << endl;
            sendEoS();
            m_isEOS = true;
        }
        gst_buffer_unmap (gstBuffer, &map);
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    /* Split the AU into individual NAL units. */
    std::vector<NalUnitRange> nal_ranges = splitAUIntoNalUnits(map.data, map.size);

    /* Defensive fallback: if the buffer somehow has no detectable start codes
     * (e.g., a misconfiguration upstream produced AVC / length-prefixed bytes
     * instead of byte-stream), preserve the legacy behavior of forwarding the
     * whole buffer untouched. The consumer can then decide what to do with it. */
    if (nal_ranges.empty())
    {
        LOG(warning) << "No NAL start codes found in AU of size " << map.size
                     << ", forwarding whole buffer. m_sessionId:" << m_sessionId << endl;
        NalUnitRange whole;
        whole.offset = 0;
        whole.size   = (size_t)map.size;
        nal_ranges.push_back(whole);
    }

    /* Decide once per AU whether this is a "data-bearing" frame. The throttle
     * and frame_id increment must run once per frame, not once per NAL --
     * otherwise the original NAL-aligned per-slice slowdown comes back through
     * a different door. */
    bool isDataNalu = auContainsDataNal(map.data, nal_ranges, m_videoCodec);

    /* Same presentationTime is used for every NAL of this AU. The cache lets
     * us hold the wall-clock the AU was first seen, even across the per-NAL
     * loop below. */
    struct timeval presentationTime;
    if (m_prevGstBufTime != -1 && m_prevGstBufTime == gstBuffer_time)
    {
        presentationTime = m_prevPresentationTime;
    }
    else
    {
        gettimeofday(&presentationTime, nullptr);
        m_prevGstBufTime = gstBuffer_time;
        m_prevPresentationTime = presentationTime;
    }

    /* Per-AU throttle and frame_id assignment */
    int64_t frame_id = -1;
    if (isDataNalu)
    {
        /* Check if Frames are received earlier than expected, synchronize in that case */
        if (GET_CONFIG().nv_streamer_sync_file_count <= 0)
        {
            checkEarlyFramesAndSynchronize();
        }
        if (m_stop == true)
        {
            gst_buffer_unmap (gstBuffer, &map);
            gst_sample_unref (sample);
            return GST_FLOW_OK;
        }

        if (gstBuffer_time != 0)
        {
            frame_id = (gstBuffer_time / (1000.00/m_frameRate)) + 0.5;
        }
        else
        {
            frame_id = m_frameId + 1;
        }
        m_frameId = frame_id;
    }

    /* Common fields for every NAL in this AU. The per-NAL fields (m_buffer
     * and m_size) are set inside the loop. */
    FrameParams frame_params;
    frame_params.m_serverFrameId    = frame_id;
    frame_params.m_serverPts        = current_frame_ts * 1000;
    frame_params.m_presentationTime = presentationTime;
    frame_params.m_codec            = m_videoCodec;

    const bool isH265 = iequals(m_videoCodec, "h265");

    /* Emit each NAL separately to the consumer (preserving the contract of
     * media_consumer::parseAndCreateFrame which inspects exactly one NAL per
     * call to track SPS/PPS/VPS, IDR boundaries, multi-slice prepending, etc.). */
    std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
    for (const auto &r : nal_ranges)
    {
        if (m_stop == true)
        {
            break;
        }
        if (r.size == 0)
        {
            continue;
        }

        unsigned char *nal_ptr = (unsigned char *)map.data + r.offset;
        const size_t   nal_size = r.size;

        /* Parse NAL type (used both for the skip-list check below and for
         * the diagnostic log line). The skip list itself lives in the
         * shared shouldSkipNalForEmission() helper -- single source of
         * truth across live and VOD paths. */
        const uint8_t nal_type_for_log = isH265
            ? static_cast<uint8_t>(parseH265NaluType(nal_ptr, (ssize_t)nal_size))
            : static_cast<uint8_t>(parseH264NaluType(nal_ptr, (ssize_t)nal_size));

        if (shouldSkipNalForEmission(nal_type_for_log, m_videoCodec))
        {
            continue;
        }

        if (frame_index <= 10)
        {
            cout << "[" << getCurrentTimeMS() << "] bufCount:" << frame_index
                 << ", size:" << nal_size
                 << ", pts:" << convertEpocToISO8601_2(current_frame_ts * 1000)
                 << ", nal_type:" << static_cast<int>(nal_type_for_log)
                 << ", m_sessionId:" << m_sessionId << endl;
        }

        frame_params.m_buffer = nal_ptr;
        frame_params.m_size   = nal_size;

        if (m_mediaConsumer)
        {
            m_mediaConsumer->onFrame(frame_params);
        }
        ++frame_index;
    }

#ifdef DEMUXER_OUTPUT_DUMP
    FILE *fp = fopen("./demuxer_out.raw", "ab");
    if (fp)
    {
        fwrite(map.data, 1, map.size, fp);
        fflush(fp);
        fclose(fp);
    }
#endif

    gst_buffer_unmap (gstBuffer, &map);
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

GstFlowReturn GstDeMux::processNewSampleFromSinkForVodStream (GstElement * appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    int64_t current_frame_ts = -1;

    long& frame_index = m_callbackData.m_index;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            frame_index = 0;
            return GST_FLOW_OK;
        }
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (info) << "No more buffers available from app sink element" << endl;
        frame_index = 0;
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    if (m_stop == true)
    {
        gst_sample_unref (sample);
        gst_buffer_unmap (gstBuffer, &map);
        return GST_FLOW_OK;
    }

    if (GST_BUFFER_PTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        current_frame_ts = (GST_BUFFER_PTS (gstBuffer) / 1000);
    }
    else if (GST_BUFFER_DTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        current_frame_ts = (GST_BUFFER_DTS (gstBuffer) / 1000);
    }

    if (frame_index <= 10)
    {
        if (frame_index == 0)
        {
            m_actualStartTime = current_frame_ts/1000;
        }
        LOG(info) << "bufCount:" << frame_index << ", size:" << map.size
            << ", pts:" << current_frame_ts/1000 << ", m_sessionId:" << m_sessionId << endl;
    }

    frame_index ++;

    /* Check if user endTime is reached */
    if (m_callbackData.m_userEndTime != 0 && ((uint64_t)current_frame_ts/1000) >= m_callbackData.m_userEndTime)
    {
        if (m_isEOS == false)
        {
            LOG(info) << "User endTime is reached, sending EOS:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
            sendEoS();
            m_isEOS = true;
            gst_sample_unref (sample);
            gst_buffer_unmap (gstBuffer, &map);
            return GST_FLOW_OK;
        }
    }

    /* Check if end of the file is reached, select next file in that case*/
    if (m_fileEndTime != 0 && ((uint64_t)current_frame_ts/1000) >= m_fileEndTime)
    {
        if (m_currentFileIndex == m_fileNameArray.size() && m_callbackData.m_userEndTime == 0)
        {
            // For continuous playback, Don't rely on the calculated fileEndTime. Wait for EOS from bus
            m_fileEndTime = 0;
        }
        else
        {
            LOG(info) << "VOD: Reached end of the file:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
            gst_sample_unref (sample);
            gst_buffer_unmap (gstBuffer, &map);

            m_vodTasks.push_back( async::spawn([=] () -> bool
            {
                if (setFileSource() == true)
                {
                    play_internal();
                }
                return true;
            }));
            return GST_FLOW_EOS;
        }
    }

    /* Process the frame & send to consumer */
    FrameParams frame_params;

    struct timeval presentationTime;
    presentationTime.tv_sec = current_frame_ts / 1000000;
    presentationTime.tv_usec = current_frame_ts % 1000000;
    frame_params.m_serverPts = current_frame_ts;
    frame_params.m_presentationTime = presentationTime;
    frame_params.m_codec = m_videoCodec;

    /* Split the AU into individual NAL units. Unlike the legacy
     * getListOfNalUnits()/getListOfH265NalUnits() helpers (which stop at
     * the first coded slice and would silently fuse slices 2..N of a
     * multi-slice picture into the last entry's payload), splitAUIntoNalUnits
     * walks the entire byte stream and returns one range per NAL. This
     * makes VOD playback of multi-slice MP4 files byte-correct: every
     * slice is delivered to the consumer as its own onFrame() call. */
    std::vector<NalUnitRange> nal_ranges = splitAUIntoNalUnits(map.data, map.size);

    /* Defensive fallback: if the buffer somehow has no detectable start
     * codes, preserve the legacy behavior of forwarding the whole buffer
     * untouched so the consumer can decide what to do with it. */
    if (nal_ranges.empty())
    {
        LOG(warning) << "VOD: No NAL start codes in AU of size " << map.size
                     << ", forwarding whole buffer. m_sessionId:" << m_sessionId << endl;
        NalUnitRange whole;
        whole.offset = 0;
        whole.size   = (size_t)map.size;
        nal_ranges.push_back(whole);
    }

    const bool isH265 = iequals(m_videoCodec, "h265");

    /* Emit each NAL separately to the consumer. The skip list (AUD / STAP /
     * MTAP) is shared with the live path via shouldSkipNalForEmission(). */
    std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
    for (const auto &r : nal_ranges)
    {
        if (r.size == 0)
        {
            continue;
        }
        unsigned char *nal_ptr  = (unsigned char *)map.data + r.offset;
        const size_t   nal_size = r.size;

        const uint8_t nal_type = isH265
            ? static_cast<uint8_t>(parseH265NaluType(nal_ptr, (ssize_t)nal_size))
            : static_cast<uint8_t>(parseH264NaluType(nal_ptr, (ssize_t)nal_size));

        if (shouldSkipNalForEmission(nal_type, m_videoCodec))
        {
            continue;
        }

        frame_params.m_buffer = nal_ptr;
        frame_params.m_size   = nal_size;

        if (m_mediaConsumer)
        {
            m_mediaConsumer->onFrame(frame_params);
        }
    }

    /* Unref the sample */
    gst_sample_unref (sample);

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    return GST_FLOW_OK;
}

static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstDeMux *gstDemux)
{
    if (gstDemux)
    {
        if (gstDemux->m_isVodStream)
        {
            return gstDemux->processNewSampleFromSinkForVodStream(appsink);
        }
        return gstDemux->processNewSampleFromSink(appsink);
    }
    return GST_FLOW_ERROR;
}

void GstDeMux::checkEarlyFramesAndSynchronize()
{
    int64_t currentFrameTs = getCurrentUnixTimestampInMs();
    if (m_prevFrameTs != 0)
    {
        int64_t frameDiff = currentFrameTs - m_prevFrameTs;
        m_prevFrameTs = currentFrameTs;
        if (frameDiff < m_idealFrameInterval)
        {
            std::unique_lock<std::mutex> earlyFrame_lock(m_demuxFrameMutex);
            int64_t early_time_ms = m_idealFrameInterval - frameDiff;
            auto wait_time = std::chrono::system_clock::now() + chrono::milliseconds(early_time_ms);
            m_demuxFrameCv.wait_until(earlyFrame_lock, wait_time);
            m_prevFrameTs = getCurrentUnixTimestampInMs();
        }
    }
    else
    {
        m_prevFrameTs = currentFrameTs;
    }
}

/* called when the appsink notifies us that there is a new audio buffer ready for
 * processing */
GstFlowReturn GstDeMux::processNewSampleFromSinkAudio (GstElement * appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    int64_t current_frame_ts;

    long& frame_index = m_callbackData.m_index;

    /* Get file start time to use in adding timestamp information */
    int64_t file_start_time = m_callbackData.m_fileStartTime;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            frame_index = 0;
            return GST_FLOW_OK;
        }
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (info) << "No more buffers available from app sink element" << endl;
        frame_index = 0;
        return GST_FLOW_OK;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }
    frame_index++;

    /* Convert gst buffer PTS (ns) to ms, matching the units of m_fileStartTime.
     * The previous version divided by 1e9 (ns -> seconds) and added the result
     * to a millisecond base, which caused current_frame_ts to never increment
     * within the first second and made the [demux-audio] pts log misleading. */
    int64_t gstBuffer_ms = 0;
    if (GST_BUFFER_PTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        gstBuffer_ms = (int64_t)(GST_BUFFER_PTS (gstBuffer) / 1000000);
    }
    else if (GST_BUFFER_DTS (gstBuffer) != GST_CLOCK_TIME_NONE)
    {
        gstBuffer_ms = (int64_t)(GST_BUFFER_DTS (gstBuffer) / 1000000);
    }
    file_start_time += gstBuffer_ms;
    current_frame_ts = file_start_time;

    if (frame_index <= 10)
    {
        LOG(info) << "[demux-audio]: count:" << frame_index
                  << ", size:" << map.size
                  << ", gstBufPts_ms:" << gstBuffer_ms
                  << ", m_sessionId:" << m_sessionId << std::endl;
    }

    if (m_callbackData.m_userEndTime != 0 && (uint64_t)current_frame_ts >= m_callbackData.m_userEndTime)
    {
        if (m_isEOS == false)
        {
            LOG(info) << "End time is reached, sending EOS" << endl;
            sendEoS();
            m_isEOS = true;
        }
    }
    else
    {
        std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
        if (m_mediaConsumer)
        {
            FrameParams frame_params;
            frame_params.m_buffer  = map.data;
            frame_params.m_size    = map.size;

            struct timeval presentationTime;
            gettimeofday(&presentationTime, nullptr);
            frame_params.m_presentationTime = presentationTime;
            m_mediaConsumer->onFrame(frame_params);
        }
    }

#ifdef DEMUXER_OUTPUT_DUMP
    static int fcount = 0;
    if (fcount++ <= 100)
    {
        FILE *fp;
        fp = fopen("./demuxer_out.raw","ab");
        LOG(info) << "mkv dumping size:" << content.size() << endl;
        fwrite(content.data(), 1, content.size(), fp);
        fflush(fp);
        fclose(fp);
    }
#endif

    /* Unref the sample */
    gst_sample_unref (sample);

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    return GST_FLOW_OK;
}

static GstFlowReturn
on_new_sample_from_sink_audio (GstElement * appsink, GstDeMux *gstDemux)
{
   if (gstDemux)
   {
       return gstDemux->processNewSampleFromSinkAudio(appsink);
   }
   return GST_FLOW_ERROR;
}

void GstDeMux::setGstClock(GstClock* global_clock, GstClockTime base_time)
{
    LOG(info) << "Setting gstclock for demux pipeline filename:" << m_filename << " m_baseGstTime:" << base_time << endl;
    m_globalGstClock = global_clock;
    m_baseGstTime = base_time;
}

int GstDeMux::create_playbin ()
{
    LOG (info) << "Creating Gstreamer demux-playbin pipeline" << endl;
    if (m_pipeline != nullptr)
    {
        return 0;
    }
    GstCaps *filtercaps = nullptr;
    GstElement *customoutput = nullptr;
    gint flags;
    m_pipeline = gst_pipeline_new ("pipeline");
    m_source   = gst_element_factory_make ("playbin", nullptr);
    m_queueVideo    = gst_element_factory_make ("queue", nullptr);
    m_queueAudio    = gst_element_factory_make ("queue", nullptr);
    m_filterVideo   = gst_element_factory_make ("capsfilter", nullptr);
    m_filterAudio   = gst_element_factory_make ("capsfilter", nullptr);
    m_sinkVideo     = gst_element_factory_make ("appsink", nullptr);
    m_sinkAudio     = gst_element_factory_make ("appsink", nullptr);
    customoutput = gst_bin_new("customoutput");
    m_outBuf.clear();

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_queueVideo || !m_queueAudio || !m_filterVideo || !m_filterAudio
        || !m_sinkVideo || !m_sinkAudio || !customoutput)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        if (filtercaps) gst_caps_unref(filtercaps);
        if (customoutput) gst_object_unref(customoutput);
        if (m_sinkAudio) gst_object_unref(m_sinkAudio);
        if (m_sinkVideo) gst_object_unref(m_sinkVideo);
        if (m_filterAudio) gst_object_unref(m_filterAudio);
        if (m_filterVideo) gst_object_unref(m_filterVideo);
        if (m_queueAudio) gst_object_unref(m_queueAudio);
        if (m_queueVideo) gst_object_unref(m_queueVideo);
        if (m_source) gst_object_unref(m_source);
        if (m_pipeline) gst_object_unref(m_pipeline);
        return -1;
    }

    string bitstream_type;
    if (iequals(m_videoCodec, "h264"))
    {
        bitstream_type = "video/x-h264";
    }
    else if(iequals(m_videoCodec, "h265"))
    {
        bitstream_type = "video/x-h265";
    }
    else
    {
        LOG(error) << "Codec format not supported" << endl;
        if (filtercaps) gst_caps_unref(filtercaps);
        if (customoutput) gst_object_unref(customoutput);
        if (m_sinkAudio) gst_object_unref(m_sinkAudio);
        if (m_sinkVideo) gst_object_unref(m_sinkVideo);
        if (m_filterAudio) gst_object_unref(m_filterAudio);
        if (m_filterVideo) gst_object_unref(m_filterVideo);
        if (m_queueAudio) gst_object_unref(m_queueAudio);
        if (m_queueVideo) gst_object_unref(m_queueVideo);
        if (m_source) gst_object_unref(m_source);
        if (m_pipeline) gst_object_unref(m_pipeline);
        return -1;
    }

    /* Setting properties of elements */
    filtercaps = gst_caps_new_simple (bitstream_type.c_str(),
                    "stream-format", G_TYPE_STRING, "byte-stream",
                    nullptr);
    g_object_set (G_OBJECT (m_filterVideo), "caps", filtercaps, nullptr);

    gst_caps_unref (filtercaps);

    m_bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!m_bus)
    {
        LOG(error) << "Failed to get BUS of De-Muxer playbin pipeline" << endl;
        if (customoutput) gst_object_unref(customoutput);
        if (m_sinkAudio) gst_object_unref(m_sinkAudio);
        if (m_sinkVideo) gst_object_unref(m_sinkVideo);
        if (m_filterAudio) gst_object_unref(m_filterAudio);
        if (m_filterVideo) gst_object_unref(m_filterVideo);
        if (m_queueAudio) gst_object_unref(m_queueAudio);
        if (m_queueVideo) gst_object_unref(m_queueVideo);
        if (m_source) gst_object_unref(m_source);
        if (m_pipeline) gst_object_unref(m_pipeline);
        return -1;
    }
    m_bus_watch_id = gst_bus_add_watch (m_bus, demuxBusWatchFunc, (void*)this);

    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (customoutput), m_queueVideo, m_filterVideo, m_sinkVideo, nullptr);

    /* Link Elements in pipeline */
    if (gst_element_link_many(m_queueVideo, m_filterVideo, m_sinkVideo, nullptr) != TRUE)
    {
        LOG (error) << "Many elements could not be linked." << endl;
        gst_object_unref(m_bus);
        if (customoutput) gst_object_unref(customoutput);
        if (m_sinkAudio) gst_object_unref(m_sinkAudio);
        if (m_sinkVideo) gst_object_unref(m_sinkVideo);
        if (m_filterAudio) gst_object_unref(m_filterAudio);
        if (m_filterVideo) gst_object_unref(m_filterVideo);
        if (m_queueAudio) gst_object_unref(m_queueAudio);
        if (m_queueVideo) gst_object_unref(m_queueVideo);
        if (m_source) gst_object_unref(m_source);
        if (m_pipeline) gst_object_unref(m_pipeline);
        return -1;
    }

    GstPad *sinkpad,*ghost_sinkpad;
    sinkpad = gst_element_get_static_pad (m_queueVideo, "sink");
    ghost_sinkpad = gst_ghost_pad_new ("sink", sinkpad);
    gst_pad_set_active (ghost_sinkpad, TRUE);
    gst_element_add_pad (customoutput, ghost_sinkpad);

    /* set property value */
    g_object_set (m_source, "video-sink", customoutput, nullptr);

    g_object_get (m_source, "flags", &flags, nullptr);
    flags |= GST_PLAY_FLAG_VIDEO;
    flags &= ~GST_PLAY_FLAG_AUDIO;
    flags &= ~GST_PLAY_FLAG_TEXT;
    g_object_set (m_source, "flags", flags, nullptr);

    gst_bin_add_many (GST_BIN(m_pipeline), m_source, nullptr);

    g_object_set (G_OBJECT (m_sinkVideo), "emit-signals", TRUE, "sync", FALSE, nullptr);
#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr);
#endif

    /* Add signal to get the buffers from app sink element */
    m_callbackData.m_source = m_source;
    m_callbackData.m_outBuf = &m_outBuf;
    g_signal_connect (m_sinkVideo, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this);

    m_is_playbin_created = true;
    m_callbackData.m_fileStartTime = getFileTimestamp(m_filename);

    LOG (info) << "Created Gstreamer demux-playbin pipeline" << endl;
    return 0;
}

void GstDeMux::on_pad_added_internal (GstElement *demux, GstPad *pad)
{
    GstPad *sink_pad = nullptr;
    LOG(info) << "Received new pad " << GST_PAD_NAME (pad) << " from " << GST_ELEMENT_NAME (demux) << endl;

    GstCaps *caps = gst_pad_get_current_caps (pad);
    gchar *capsString = gst_caps_to_string (caps);
    LOG(verbose) << "Caps = " << capsString << endl;

    bool isAudio = g_str_has_prefix(capsString, "audio");
    bool isVideo = g_str_has_prefix(capsString, "video");

    if (isAudio && m_queueAudio)
    {
        sink_pad = gst_element_get_static_pad (m_queueAudio, "sink");

        /* Capture the authoritative MPEG-4 AudioSpecificConfig directly
         * from the audio caps "codec_data" field, if present. This is the
         * canonical AAC config bytes (LC / HE / HE-v2 etc.) extracted by
         * qtdemux/libav from the file. Used by NvMediaSource::getAacParams
         * to populate the RTSP fmtp 'config=' attribute -- more accurate
         * than always reconstructing an AAC-LC ASC from sample_rate /
         * channels. Failures here are non-fatal; the caller falls back to
         * the manual AOT=2 reconstruction.
         *
         * Refresh m_audioCodecData on EVERY pad-added (even when extraction
         * fails). GstDeMux is reused across VOD continuous-playback file
         * rollover via setFileSource(); a subsequent file may have no
         * codec_data, non-AAC caps, or a different ASC. The single
         * end-of-block assignment under the lock guarantees:
         *   - successful extraction overwrites with the new ASC,
         *   - failed extraction clears the cache (no stale prior-file ASC),
         *   - readers never see a partially-built string.
         *
         * Gate on (audio/mpeg, mpegversion=4) to make sure we don't grab
         * codec_data from other audio codecs (e.g. MP3 is also audio/mpeg
         * but with mpegversion=1, and its codec_data -- if any -- is not
         * an AudioSpecificConfig). */
        std::string newCodecData;  // empty by default; populated only on success
        if (caps != nullptr)
        {
            const GstStructure *s = gst_caps_get_structure(caps, 0);
            const gchar *name = s ? gst_structure_get_name(s) : nullptr;
            gint mpegversion = 0;
            const bool isAac = name &&
                               g_strcmp0(name, "audio/mpeg") == 0 &&
                               gst_structure_get_int(s, "mpegversion", &mpegversion) &&
                               mpegversion == 4;
            const GValue *cdv = isAac ? gst_structure_get_value(s, "codec_data") : nullptr;
            GstBuffer *cdBuf = (cdv && GST_VALUE_HOLDS_BUFFER(cdv))
                                   ? gst_value_get_buffer(cdv) : nullptr;
            if (cdBuf)
            {
                GstMapInfo map;
                if (gst_buffer_map(cdBuf, &map, GST_MAP_READ))
                {
                    static const char hex[] = "0123456789ABCDEF";
                    newCodecData.reserve(map.size * 2);
                    for (gsize i = 0; i < map.size; ++i)
                    {
                        newCodecData.push_back(hex[(map.data[i] >> 4) & 0xF]);
                        newCodecData.push_back(hex[ map.data[i]       & 0xF]);
                    }
                    gst_buffer_unmap(cdBuf, &map);
                }
            }
        }
        if (newCodecData.empty())
        {
            LOG(info) << "Audio pad caps had no AAC codec_data; clearing cached value" << endl;
        }
        else
        {
            LOG(info) << "Captured audio codec_data from caps: " << newCodecData << endl;
        }
        {
            std::lock_guard<std::mutex> guard(m_audioCodecDataMutex);
            m_audioCodecData = std::move(newCodecData);
        }
    }
    else if (isVideo && m_queueVideo)
    {
        sink_pad = gst_element_get_static_pad (m_queueVideo, "sink");
    }
    else
    {
        // Pad is for a stream type this pipeline isn't consuming
        // (e.g., audio pad on a video-only GstDeMux, or a subtitle/data
        //  pad). This is expected for many MP4 files; not an error.
        LOG(info) << "Ignoring pad " << GST_PAD_NAME(pad)
                  << " (caps prefix not handled by this pipeline)" << endl;
        if (caps != nullptr) gst_caps_unref(caps);
        g_free(capsString);
        return;
    }

    if (pad && sink_pad)
    {
        if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "gst_element_link failed" << endl;
            m_isError = true;
        }
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "sink pad or demux pad is NULL" << endl;
        m_isError = true;
    }
    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
    g_free(capsString);
}

static void on_pad_added (GstElement *demux, GstPad *pad, GstDeMux *gstDemux)
{
    if (gstDemux)
    {
        return gstDemux->on_pad_added_internal(demux, pad);
    }
}

void GstDeMux::registerDataCallback(std::string filename, shared_ptr<IMediaDataConsumer> consumer)
{
    std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
    LOG(info) << "Registering consumer for filename: " << filename << endl;
    if (consumer == nullptr)
    {
        LOG(error) << "Consumer is null" << endl;
        return;
    }
    m_mediaConsumer = consumer;
}

void GstDeMux::deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& filename)
{
    std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
    LOG(info) << "De-Registering consumer for filename: " << filename << endl;
    m_mediaConsumer = nullptr;
}

void GstDeMux::updateFileMetadata(const string& filename)
{
    if (m_mediaType == MediaTypeVideo)
    {
        Json::Value metadata = getVideoMetadata(filename);
        m_containerFormat = metadata.get("Container", "").asString();
        m_videoCodec = metadata.get("Codec", "").asString();
        m_frameRate = stringToDouble(metadata.get("Framerate", "30").asString());
        m_frameCount = stringToInt(metadata.get("FrameCount", "0").asString());
        LOG(info) << "updateFileMetadata containerFormat:"<<m_containerFormat<<", videoCodec"<<m_videoCodec<<endl;
    }
    else if (m_mediaType == MediaTypeAudio)
    {
        Json::Value metadata = getAudioMetadata(filename);
        m_containerFormat = metadata.get("Container", "").asString();
        m_audioCodec = metadata.get("Codec", "").asString();
        m_sampleRate = metadata.get("SampleRate", 0).asInt();
        m_channels = metadata.get("Channels", 0).asInt();
        m_bitsPerSample = metadata.get("BitsPerSample", 0).asInt();
    }
}

void GstDeMux::setUrlParams(const string& url_params)
{
    m_urlParams = url_params;
    if (m_urlParams.find("vodStream=true") != string::npos)
    {
        LOG(info) << "This is vod stream" << endl;
        m_isVodStream = true;
        m_sensorId = m_filename;

        // Initialize cloud storage if enabled (before updateFileList)
        if (GET_CONFIG().enable_cloud_storage)
        {
            LOG(info) << "Initializing cloud storage for VOD stream" << endl;
            if (!initUnifiedStorageReader())
            {
                LOG(warning) << "Failed to initialize unified storage reader, continuing without cloud storage" << endl;
                m_cloudStorageEnabled = false;
            }
            if (!initUnifiedStorageManager())
            {
                LOG(warning) << "Failed to initialize unified storage manager, continuing without cloud storage" << endl;
                m_cloudStorageEnabled = false;
            }
        }

        if (updateFileList() == -1)
        {
            /* Failed to get recorded streams */
            m_isError = true;
        }
    }
}

int GstDeMux::updateFileList()
{
    /* Parse the startTime & endTime in epoch format */
    pair<int64_t, int64_t> epochTimeRange = getEpochTimeRangeFromIsoString(m_urlParams);
    m_epochStartTime = epochTimeRange.first;
    m_epochEndTime = epochTimeRange.second;

    /* Get list of files alongwith its associated timestamps */
    auto dbHelper = GET_DB_INSTANCE();
    m_fileNameArray = dbHelper->getFileList(m_filename, m_epochStartTime, m_epochEndTime);
    LOG(info) << "m_fileNameArray size:" << m_fileNameArray.size() << endl;
    if (m_fileNameArray.size() == 0)
    {
        VideoFileInfo receivedFile = dbHelper->getInProgressRecordFile(m_filename, m_epochStartTime);
        if (!receivedFile.m_filePath.empty())
        {
            m_fileNameArray.push_back(receivedFile);
            LOG(info) << "Got in progress file receivedFile:" << receivedFile.m_filePath << ", m_sessionId:" << m_sessionId << endl;
        }
        else
        {
            LOG(error) << "No recorded video files found" << endl;
            return -1;
        }
    }

    if (!m_fileNameArray.empty() && isCloudStorageEnabled())
    {
        LOG(info) << "Starting hybrid download for " << m_fileNameArray.size() << " files" << std::endl;

        // Use existing unified storage reader
        if (!m_unifiedStorageReader)
        {
            LOG(error) << "Unified storage reader not initialized" << std::endl;
            return -1;
        }

        LOG(info) << "Using existing unified storage reader for cloud downloads" << std::endl;

        // Prepare remote-local file pairs for download
        std::vector<std::pair<std::string, std::string>> remoteLocalPairs;
        remoteLocalPairs.reserve(m_fileNameArray.size());

        for (const auto& fileInfo : m_fileNameArray)
        {
            if (fileInfo.m_objectId.empty())
            {
                LOG(info) << "Skipping file with empty objectId: " << fileInfo.m_filePath << std::endl;
                continue;
            }

            remoteLocalPairs.emplace_back(fileInfo.m_objectId, fileInfo.m_filePath);
            LOG(info) << "Prepared cloud download: " << fileInfo.m_objectId << " -> " << fileInfo.m_filePath << std::endl;
        }

        // Only proceed if we have cloud files to download
        if (remoteLocalPairs.empty())
        {
            LOG(info) << "No cloud files to download, using local files directly" << std::endl;
            return 0;
        }

        LOG(info) << "Starting hybrid download for " << remoteLocalPairs.size() << " cloud files" << std::endl;

        // Download files using hybrid approach
        std::string asyncSessionId;

        // Custom per-file completion callback
        auto perFileCompletionCallback = [this](const std::string& filePath, bool success, const std::string& localPath) {
            LOG(info) << "PER-FILE COMPLETION: " << filePath
                      << " -> " << (success ? "SUCCESS" : "FAILED")
                      << " -> " << localPath << std::endl;

            if (success)
            {
                // Find and update the corresponding file path in m_fileNameArray
                for (size_t i = 0; i < m_fileNameArray.size(); ++i)
                {
                    // Check if this file matches the downloaded file
                    if (m_fileNameArray[i].m_filePath.find(filePath) != std::string::npos ||
                        filePath.find(m_fileNameArray[i].m_filePath) != std::string::npos)
                    {
                        LOG(info) << "Updating file path for index " << i
                                  << " from " << m_fileNameArray[i].m_filePath
                                  << " to " << localPath << std::endl;
                        m_fileNameArray[i].m_filePath = localPath;
                        break;
                    }
                }
            }
            else
            {
                // Handle failure - log error and potentially mark file as unavailable
                LOG(warning) << "File download failed for: " << filePath << std::endl;
            }
        };

        bool downloadSuccess = nv_vms::UnifiedStorageReaderUtils::getFiles(
            m_unifiedStorageReader,
            remoteLocalPairs,
            asyncSessionId,
            [this, remoteLocalPairs](const std::string& sessionId, const nv_vms::DownloadResult& result) {
                // Completion callback for async downloads
                LOG(info) << "Async download completed for session: " << sessionId << std::endl;
                LOG(info) << "Success: " << result.successful_downloads << ", Failed: " << result.failed_downloads << std::endl;
                LOG(info) << "Total bytes: " << result.total_bytes_downloaded << ", Avg speed: " << result.average_speed << " MB/s" << std::endl;
            },
            [this, perFileCompletionCallback, remoteLocalPairs](const std::string& filePath, uint64_t bytesDownloaded, uint64_t totalBytes, double speed) {
                // Progress callback for async downloads
                LOG(info) << "Download progress: " << filePath << " - "
                          << bytesDownloaded << "/" << totalBytes << " bytes ("
                          << (totalBytes > 0 ? (bytesDownloaded * 100 / totalBytes) : 0) << "%) at "
                          << speed << " MB/s" << " for " << filePath << std::endl;

                // Find the corresponding local path from remoteLocalPairs
                std::string localPath;
                for (const auto& pair : remoteLocalPairs)
                {
                    if (pair.first == filePath) // filePath is the remote path (object key)
                    {
                        localPath = pair.second; // Use the pre-built local path
                        break;
                    }
                }

                if (localPath.empty())
                {
                    LOG(warning) << "Could not find local path for remote path: " << filePath << std::endl;
                    return;
                }

                // Check if this file download is complete (success)
                if (bytesDownloaded == totalBytes && totalBytes > 0)
                {
                    // Call per-file completion callback for success
                    perFileCompletionCallback(filePath, true, localPath);
                    // Add the downloaded file to our tracking list for cleanup
                    addDownloadedFile(localPath);
                }
                // Check if this file download failed (totalBytes = 0 indicates failure)
                else if (totalBytes == 0 && bytesDownloaded == 0)
                {
                    // Call per-file completion callback for failure
                    // Note: We still pass the intended localPath even for failures
                    // This helps with debugging and potential cleanup operations
                    LOG(warning) << "File download FAILED: " << filePath << " (Object not found) - intended local path: " << localPath << std::endl;
                    perFileCompletionCallback(filePath, false, localPath);
                }
            }
        );

        if (!downloadSuccess)
        {
            LOG(error) << "Failed to start hybrid download" << std::endl;
            return -1;
        }

        // Add the first file to the downloaded files list for cleanup
        addDownloadedFile(remoteLocalPairs[0].second);

        if (!asyncSessionId.empty())
        {
            LOG(info) << "Started async download session: " << asyncSessionId << std::endl;
        }

        LOG(info) << "Hybrid download completed - First file: sync, Remaining: async session " << asyncSessionId << std::endl;
    }

    return 0;
}

string GstDeMux::getNextFile ()
{
    auto dbHelper = GET_DB_INSTANCE();

check_next_file:
    // Check if array is empty
    if (m_fileNameArray.empty() || dbHelper == nullptr)
    {
        LOG(warning) << "Invalid file array or dbHelper is null" << endl;
        return "";
    }

    string file_name;
    if(m_currentFileIndex == m_fileNameArray.size())
    {
        LOG(info) << "Reached at end of file, try to fetch next file(S) if available ..." << endl;
        if (m_currentFileIndex == 0)
        {
            LOG(error) << "Invalid file index" << endl;
            return "";
        }
        VideoFileInfo last_file = m_fileNameArray[m_currentFileIndex - 1 ];
        if (last_file.m_fileFPS == 0) // File FPS is not updated in DB
        {
            last_file = dbHelper->getRecordFileInfo(m_sensorId, last_file.m_startTime);
            if (last_file.m_fileFPS != 0) // File FPS is now updated in DB
            {
                m_fileNameArray[m_currentFileIndex - 1 ] = last_file;
            }
        }
        LOG(info) << "Duration of last file : " << last_file.m_duration << endl;
        if (last_file.m_duration != 1) // check if the last file is being written
        {
            uint64_t last_file_start_time = last_file.m_startTime;
            std::vector <VideoFileInfo> next_files = dbHelper->getNextFileList(m_sensorId, last_file_start_time);
            if (next_files.size() > 0)
            {
                for (auto file : next_files)
                {
                    if (std::find(m_fileNameArray.begin(), m_fileNameArray.end(), file) != m_fileNameArray.end())
                    {
                        continue;
                    }
                    else
                    {
                        // push file which has start time > last file in current list
                        if(last_file < file)
                        {
                            m_fileNameArray.push_back(file);
                        }
                    }
                }
            }
            else
            {
                LOG(warning) << "No next files are available" << endl;
                return "";
            }
        }
    }

    if (m_currentFileIndex < m_fileNameArray.size())
    {
        file_name = m_fileNameArray[m_currentFileIndex].m_filePath;
        std::string object_id = m_fileNameArray[m_currentFileIndex].m_objectId;
        VideoFileInfo currentFile = m_fileNameArray[m_currentFileIndex];

        //If file exists in recorded_video_root, we don't need to download it from cloud
        if (isFileExist(file_name))
        {
            LOG(info) << "File = " << file_name << " is already in the recorded video root path, skipping downloading it from cloud" << endl;
            m_fileEndTime = m_fileNameArray[m_currentFileIndex].m_startTime + m_fileNameArray[m_currentFileIndex].m_duration;
            m_currentFileIndex++;
            return file_name;
        }

        bool file_exists = false;
        if (m_unifiedStorageReader && !object_id.empty())
        {
            nv_vms::FileResult file_result = m_unifiedStorageReader->checkFileExists(object_id);
            file_exists = file_result.success;

            LOG(info) << "File exists check result: " << (file_exists ? "true" : "false") << std::endl;
            if (!file_exists)
            {
                LOG(warning) << "File check error: " << file_result.message << std::endl;
            }
        }
        else
        {
            LOG(warning) << "Unified storage reader is not initialized, assuming file does not exist" << std::endl;
        }

        /* If cloud storage is enabled and object id is not empty, download the file from cloud */
        if (isCloudStorageEnabled() && !object_id.empty())
        {
            LOG(info) << "Attempting to download from cloud: " << object_id << " to local path: " << file_name << std::endl;

            if (m_unifiedStorageReader && nv_vms::UnifiedStorageReaderUtils::getFile(m_unifiedStorageReader, object_id, file_name))
            {
                LOG(verbose) << "Successfully downloaded file from cloud: " << object_id << " to local path: " << file_name << endl;
                // Add the downloaded file to our tracking list for cleanup
                addDownloadedFile(file_name);
                m_fileEndTime = m_fileNameArray[m_currentFileIndex].m_startTime + m_fileNameArray[m_currentFileIndex].m_duration;
                m_currentFileIndex++;
                return file_name;
            }
            else
            {
                LOG(warning) << "Failed to download file from cloud: " << object_id << " to local path: " << file_name << endl;
                LOG(warning) << "Download error: " << nv_vms::UnifiedStorageReaderUtils::getLastError() << std::endl;
            }
        }
        else
        {
            LOG(info) << "Cloud storage is not enabled for this reader" << std::endl;
        }

        /* File doesn't exist locally or in cloud storage, try next file */
        LOG(warning) << "File = " << file_name << " not available, checking next files in list" << endl;
        if (m_currentFileIndex < m_fileNameArray.size() - 1)
        {
            m_currentFileIndex++;
            goto check_next_file;
        }
    }
    else
    {
        LOG(warning) << " Reached at end of playlist, retry after 1sec" << endl;
    }
    return file_name;
}

string GstDeMux::getFirstAvailableFile()
{
    string filename;
    for (size_t i = 0; i < m_fileNameArray.size(); i++)
    {
        string file =  m_fileNameArray[i].m_filePath;
        if (isFileExist(file))
        {
            filename = file;
            break;
        }
    }
    return filename;
}

bool GstDeMux::setFileSource ()
{
    string filename = getNextFile();
    LOG(info) << "Got next filename:" << filename << ", m_sessionId:" << m_sessionId << endl;
    if (!filename.empty())
    {
        m_filename = filename;
        {
            vst_storage::addOrRemoveFileInProtectList(m_filename, true);
            if (!m_prevFileName.empty())
            {
                vst_storage::addOrRemoveFileInProtectList(m_prevFileName, false);
            }
        }

        // Clean up previous downloaded file if it exists and is different from current
        if (!m_prevDownloadedFileName.empty() && m_prevDownloadedFileName != filename)
        {
            if (m_unifiedStorageManager)
            {
                LOG(info) << "Cleaning up previous downloaded file: " << m_prevDownloadedFileName << endl;
                // Use deleteFilesSync with a single-element vector to delete the file synchronously
                std::vector<std::string> filesToDelete = {m_prevDownloadedFileName};
                nv_vms::UnifiedStorageManagerUtils::deleteFilesSync(m_unifiedStorageManager, filesToDelete);
            }
        }

        // Store the downloaded file path for cleanup in next iteration
        m_prevDownloadedFileName = filename;
        m_prevFileName = filename;
        return true;
    }
    else
    {
        LOG(warning) << "Need to stop the pipeline as no files are available for playback" << endl;
        sendEoS();
        return false;
    }
}

int GstDeMux::create_internal ()
{
    if (gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }
    if (m_mediaType == MediaTypeVideo)
    {
        return create_video_pipeline();
    }
    else if (m_mediaType == MediaTypeAudio)
    {
        return create_audio_pipeline();
    }
    return -1;
}

int GstDeMux::create_video_pipeline ()
{
    if (m_pipeline)
    {
        LOG(info) << "Demux pipeline already created" << endl;
        return 0;
    }

    /* Validate file & check if it is list of the files */
    string filename = m_filename;
    if (m_isVodStream)
    {
        m_containerFormat = CONTAINER_FORMAT_MATROSKA;
        setFileSource();
    }
    LOG (info) << "Creating Gstreamer demux-video pipeline, filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
    LOG(info) << "Container: " << m_containerFormat << ", videoCodec:" << m_videoCodec << endl;

    #if 0
    /* TODO: For other container formats, we need to create the pipeline using the common utility API.
    But that is not working as expected. so we kept it commented for now. We need to fix this. */
    if (m_containerFormat != CONTAINER_FORMAT_QUICKTIME && m_containerFormat != CONTAINER_FORMAT_MATROSKA)
    {
        return create_playbin();
    }
    #endif

    GstCaps *filtercaps_video;
    m_pipeline = gst_pipeline_new ("pipeline");
    m_source   = gst_element_factory_make ("filesrc", nullptr);
    // Use the common utility API for container format detection and demuxer creation
    m_demux = createDemuxerForFile(filename, m_containerFormat);
    if (m_demux == nullptr)
    {
        LOG(error) << "File extension not supported or could not create demuxer for file: " << filename << endl;
        m_isError = true;
        return -1;
    }    
    m_queueVideo    = gst_element_factory_make ("queue", nullptr);
    m_filterVideo   = gst_element_factory_make ("capsfilter", nullptr);
    m_sinkVideo     = gst_element_factory_make ("appsink", nullptr);
    m_outBuf.clear();

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_demux || !m_queueVideo || !m_filterVideo || !m_sinkVideo)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        m_isError = true;
        return -1;
    }

    /* Video parse & bitstream caps filters */
    {
        string bitstream_type;
        if (iequals(m_videoCodec, "h264"))
        {
            m_parserVideo = gst_element_factory_make ("h264parse", nullptr);
            bitstream_type = "video/x-h264";
        }
        else if (iequals(m_videoCodec, "h265"))
        {
            m_parserVideo = gst_element_factory_make ("h265parse", nullptr);
            bitstream_type = "video/x-h265";
        }
        else
        {
            LOG(error) << "Codec format not supported" << endl;
            return -1;
        }
        if (!m_parserVideo)
        {
            LOG(error) << "m_parserVideo is null" << endl;
            return -1;
        }

        int keyFrameInt = get_keyframe_interval_from_db(m_filename);
        int config_inteval_value = keyFrameInt == 1 ? 1 : -1;

        LOG(info) << "config_inteval_value = " << config_inteval_value << endl;

        /* Setting properties of elements */
        /* Send SPS-PPS with each IDR Frame */
        g_object_set (G_OBJECT (m_parserVideo), "config-interval", config_inteval_value, nullptr);
        filtercaps_video = gst_caps_new_simple (bitstream_type.c_str(),
                        "stream-format", G_TYPE_STRING, "byte-stream",
                        "alignment", G_TYPE_STRING, "au",
                        nullptr);
        g_object_set (G_OBJECT (m_filterVideo), "caps", filtercaps_video, nullptr);
        gst_caps_unref (filtercaps_video);
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr);
#endif

    m_bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!m_bus)
    {
        LOG(error) << "Failed to get BUS of De-Muxer playbin pipeline" << endl;
        gst_object_unref(m_pipeline);
        return -1;
    }
    m_bus_watch_id = gst_bus_add_watch (m_bus, demuxBusWatchFunc, (void*)this);

    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_demux,
                    m_queueVideo, m_parserVideo, m_filterVideo, m_sinkVideo, nullptr);

    /* Link Elements in pipeline */
    if (gst_element_link(m_source, m_demux) != TRUE)
    {
        LOG (error) << "Element source and demux could not be linked." << endl;
        gst_object_unref(m_pipeline);
        return -1;
    }

    /* Link video Elements in pipeline */
    if (gst_element_link_many(m_queueVideo, m_parserVideo, m_filterVideo, m_sinkVideo, nullptr) != TRUE)
    {
        LOG (error) << "Many video elements could not be linked." << endl;
        gst_object_unref(m_pipeline);
        return -1;
    }

    /* Add signal to link demuxer with audio/video queue */
    g_signal_connect (m_demux, "pad-added", G_CALLBACK (on_pad_added), this);

    g_object_set (G_OBJECT (m_sinkVideo), "emit-signals", TRUE, "sync", TRUE, nullptr);

    /* Add signal to get the buffers from app sink element */
    m_callbackData.m_source = m_source;
    m_callbackData.m_outBuf = &m_outBuf;
    g_signal_connect (m_sinkVideo, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this);

    if (m_isVodStream)
    {
        m_callbackData.m_fileStartTime = m_fileNameArray.size() > 0 ? m_fileNameArray[0].m_startTime : 0;
        m_fileEndTime = m_fileNameArray.size() > 0 ?
            m_fileNameArray[0].m_startTime + m_fileNameArray[0].m_duration : 0;
    }
    else
    {
        m_callbackData.m_fileStartTime = getFileTimestamp(m_filename);
    }
    m_callbackData.m_fileStartTime = GET_CONFIG().enable_mega_simulation ? 0 : m_callbackData.m_fileStartTime;

    /* Ideal FramePlayTime based on frameRate alongwith 10% thresold */
    m_idealFrameInterval = (1000/m_frameRate) / 1.1;

    LOG (info) << "Created Gstreamer demux-video pipeline, m_sessionId:" << m_sessionId << endl;
    return 0;
}

int GstDeMux::get_keyframe_interval_from_db (string file_location)
{
    SensorDetailsDBColumns row =  GET_DB_INSTANCE()->readSensorDetailsByLocation(file_location);
    if (row.sensor_id_value.empty() == false)
    {
        SensorStreamsDBColumns stream_row =  GET_DB_INSTANCE()->readSensorStreams(row.sensor_id_value);
        return stringToInt(stream_row.encodingInterval_value, -1);
    }
    return -1;
}

int GstDeMux::create_audio_pipeline ()
{
    LOG (info) << "Creating Gstreamer demux-audio pipeline, filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
    if (m_pipeline)
    {
        LOG(info) << "Demux pipeline already created" << endl;
        return 0;
    }

    LOG(info) << "container: " << m_containerFormat << ", audioCodec:" << m_audioCodec << endl;
    #if 0
    /* TODO: For other container formats, we need to create the pipeline using the common utility API.
    But that is not working as expected. so we kept it commented for now. We need to fix this. */
    if (m_containerFormat != CONTAINER_FORMAT_QUICKTIME && m_containerFormat != CONTAINER_FORMAT_MATROSKA)
    {
        return create_playbin();
    }
    #endif

    GstCaps *filtercaps_audio;
    m_pipeline = gst_pipeline_new ("pipeline");
    m_source   = gst_element_factory_make ("filesrc", nullptr);
    // Use the common utility API for demuxer creation
    m_demux = createDemuxerForContainer(m_containerFormat);
    if (m_demux == nullptr)
    {
        LOG(error) << "Could not create demuxer for container format: " << m_containerFormat << endl;
        m_isError = true;
        return -1;
    }
    m_queueAudio    = gst_element_factory_make ("queue", nullptr);
    m_filterAudio   = gst_element_factory_make ("capsfilter", nullptr);
    m_sinkAudio     = gst_element_factory_make ("appsink", nullptr);
    m_outBuf.clear();

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_demux || !m_queueAudio || !m_filterAudio || !m_sinkAudio)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        m_isError = true;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif

    /* Audio parse & bitstream caps filters */
    {
        string audio_bitstream_type = "audio/mpeg";
        //string audio_bitstream_type = "audio/x-ac3";
        /* Match any AAC variant. Probed codec strings vary by source:
         *   - getMediaInformation()        -> "AAC (Advanced Audio Coding)"
         *   - getAudioMetadata() fallback  -> "MPEG-4 AAC"
         *   - device_manager / DB         -> "AAC"
         * Substring "AAC" covers all of the above. */
        if (m_audioCodec.find("AAC") != string::npos)
        {
            m_parserAudio = gst_element_factory_make ("aacparse", nullptr);
            //m_parserAudio = gst_element_factory_make ("ac3parse", NULL);
        }
        if (!m_parserAudio)
        {
            LOG(error) << "m_parserAudio is null, audioCodec:" << m_audioCodec << endl;
            m_isError = true;
            return -1;
        }
        /* Setting properties of elements */
        filtercaps_audio = gst_caps_new_simple (audio_bitstream_type.c_str(),
                        "framed", G_TYPE_BOOLEAN, 1,
                        "stream-format", G_TYPE_STRING, "adts",
                        nullptr);
        g_object_set (G_OBJECT (m_filterAudio), "caps", filtercaps_audio, nullptr);
        gst_caps_unref (filtercaps_audio);
    }

    m_bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!m_bus)
    {
        LOG(error) << "Failed to get BUS of De-Muxer playbin pipeline" << endl;
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
        m_isError = true;
        return -1;
    }
    m_bus_watch_id = gst_bus_add_watch (m_bus, demuxBusWatchFunc, (void*)this);

    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_demux,
                    m_queueAudio, m_parserAudio,
                    m_filterAudio, m_sinkAudio, nullptr);

    /* Link Elements in pipeline */
    if (gst_element_link(m_source, m_demux) != TRUE)
    {
        LOG (error) << "Element source and demux could not be linked." << endl;
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
        m_isError = true;
        return -1;
    }

    /* Link audio Elements in pipeline */
    if (gst_element_link_many(m_queueAudio, m_parserAudio, m_filterAudio, m_sinkAudio, nullptr) != TRUE)
    {
        LOG (error) << "Many audio elements could not be linked." << endl;
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
        m_isError = true;
        return -1;
    }

    /* Add signal to link demuxer with audio/video queue */
    g_signal_connect (m_demux, "pad-added", G_CALLBACK (on_pad_added), this);

    g_object_set (G_OBJECT (m_sinkAudio), "emit-signals", TRUE, "sync", TRUE, nullptr);

    /* Add signal to get the buffers from app sink element */
    m_callbackData.m_source = m_source;
    m_callbackData.m_outBuf = &m_outBuf;
    g_signal_connect (m_sinkAudio, "new-sample", G_CALLBACK (on_new_sample_from_sink_audio), (void*)this);

    if (m_isVodStream)
    {
        m_callbackData.m_fileStartTime = m_fileNameArray.size() > 0 ? m_fileNameArray[0].m_startTime : 0;
    }
    else
    {
        m_callbackData.m_fileStartTime = getFileTimestamp(m_filename);
    }
    m_callbackData.m_fileStartTime = GET_CONFIG().enable_mega_simulation ? 0 : m_callbackData.m_fileStartTime;
    LOG (info) << "Created Gstreamer demux-audio pipeline, m_sessionId:" << m_sessionId << endl;
    return 0;
}

void GstDeMux::seek (int64_t seek_pos , uint64_t end_time, float rate /*1*/)
{
    LOG (info) << "  pipeline seek_pos:" << seek_pos
            << ", seek_rate:" << rate << ", end_time:" << end_time << ", m_sessionId:" << m_sessionId << endl;

    m_callbackData.m_userEndTime = end_time;
    m_actualStartTime = 0;
    if (m_pipeline)
    {
        if (seek_pos > 0)
        {
            gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
            gst_element_get_state (m_pipeline, nullptr, nullptr, 2 * GST_SECOND);
            m_callbackData.m_index = 0;
            m_isEOS = false;

            if (rate > 0)
            {
                if (!gst_element_seek_simple (m_pipeline, GST_FORMAT_TIME,
                    (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT),
                    seek_pos * GST_MSECOND))
                {
                    LOG(error) << "Seek failed, Playing from start" << endl;
                    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
                }
            }
            else
            {
                if (!gst_element_seek (m_pipeline, 1, GST_FORMAT_TIME,
                    (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE), GST_SEEK_TYPE_SET, 0 * GST_SECOND,
                    GST_SEEK_TYPE_SET, seek_pos * GST_MSECOND))
                {
                    LOG(error) << "Seek failed, Playing from start" << endl;
                    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
                }
            }
            m_frameId = -1;
            gst_element_get_state (m_pipeline, nullptr, nullptr, 2 * GST_SECOND);
            gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
        }
    }
}

void GstDeMux::seekToStart ()
{
    m_callbackData.m_index = 0;
    m_isEOS = false;
    m_stop = false;
    m_frameId = -1;
    m_actualStartTime = 0;

    if (!m_pipeline)
    {
        // Pipeline not created yet, nothing to seek to.
        return;
    }

    if (m_globalGstClock)
    {
        gst_pipeline_use_clock(GST_PIPELINE(m_pipeline), m_globalGstClock);
        gst_element_set_base_time(m_pipeline, m_baseGstTime);
        gst_element_set_start_time(m_pipeline, GST_CLOCK_TIME_NONE);
    }

    /* Seek on the appropriate capsfilter for the configured media type.
     * For an audio-only demux instance m_filterVideo is null, and vice versa. */
    GstElement* seekTarget = (m_mediaType == MediaTypeAudio) ? m_filterAudio : m_filterVideo;
    LOG(info) << "Seeking to start for demux pipeline filename:" << m_filename << " m_baseGstTime:" << m_baseGstTime << endl;
    if (seekTarget == nullptr)
    {
        LOG(error) << "seekToStart: seek target is null for mediaType:"
                   << mediaTypeAsString(m_mediaType) << ", filename:" << m_filename << endl;
        return;
    }
    if (!gst_element_seek_simple (seekTarget, GST_FORMAT_TIME,
        (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT), 0))
    {
        LOG(error) << "Seek to start failed, Playing from start" << endl;
        gst_element_set_state(m_pipeline, GST_STATE_READY);
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
}

void GstDeMux::play_internal ()
{
    LOG (info) << "Play Gstreamer demux pipeline m_filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
    /* Clear all the data structure */
    m_callbackData.m_index = 0;
    m_isEOS = false;
    m_stop = false;
    m_frameId = -1;
    m_actualStartTime = 0;

    /* Clear the output buffer vector */
    m_outBuf.clear();

    if (m_pipeline)
    {
        /* Setting Pipeline to NULL state */
        gst_element_set_state (m_pipeline, GST_STATE_NULL);

        /* Update the file source location with new file name
        ** and set the pipeline to playing state again
        */
        if (m_is_playbin_created)
        {
            string uri = string("file:///") + m_filename;
            g_object_set (m_source, "uri", uri.c_str() , nullptr);
        }
        else
        {
            g_object_set (m_source, "location", m_filename.c_str() , nullptr);
        }
        LOG(info) << "File Name = " <<  m_filename << endl;

        if (m_globalGstClock)
        {
            gst_pipeline_use_clock(GST_PIPELINE(m_pipeline), m_globalGstClock);
            gst_element_set_base_time(m_pipeline, m_baseGstTime);
            gst_element_set_start_time(m_pipeline, GST_CLOCK_TIME_NONE);
        }
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
    LOG (info) << "Exit - play Gstreamer demux pipeline m_filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
}

bool GstDeMux::pause_internal()
{
    bool ret = true;
    if (m_pipeline)
    {
        LOG (info) << "Pausing the pipeline, filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
        if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
        {
            LOG (error) << "gst_element_set_state failed. " << endl;
            ret = false;
        }
        else
        {
            gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
            LOG (info) << "State change success, m_sessionId:" << m_sessionId << endl;
        }
    }
    return ret;
}

void GstDeMux::resume_internal ()
{
    LOG (info) << "Resume Gstreamer demux pipeline, m_sessionId:" << m_sessionId << endl;
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
}

void GstDeMux::sendEoS()
{
    std::lock_guard<std::mutex> consumerLock(m_mediaConsumerLock);
    if (m_mediaConsumer)
    {
        string eos_msg = STREAM_MSG_EOS;
        FrameParams frame_params;
        frame_params.m_buffer  = (unsigned char *)(eos_msg.data());
        frame_params.m_size    = 0;
        m_mediaConsumer->onFrame(frame_params);
    }
}

void GstDeMux::destroy_internal ()
{
    LOG(info) << "Terminating gstreamer demux pipeline filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
    m_stop = true;
    m_demuxFrameCv.notify_all();
    if (m_pipeline == nullptr)
    {
        return;
    }

    if (m_isVodStream)
    {
        if (!m_prevFileName.empty())
        {
            vst_storage::addOrRemoveFileInProtectList(m_prevFileName, false);
        }
        for(uint32_t i = 0; i < m_vodTasks.size(); i++ )
        {
            auto t = move(m_vodTasks[i]);
            t.get();
        }
        LOG (info) << "Vod async tasks wait done filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
    }

    // Clean up downloaded files if cloud storage was used
    if (m_cloudStorageEnabled)
    {
        LOG(info) << "Cleaning up downloaded cloud files for filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
        cleanupDownloadedFiles();
    }

    if (m_pipeline)
    {
        if (m_isVodStream)
        {
            GstState current, pending;
            GstStateChangeReturn ret = gst_element_get_state(m_pipeline, &current, &pending, 0);
            if (ret != GST_STATE_CHANGE_FAILURE && current == GST_STATE_PLAYING)
            {
                // Send EOS event
                GstEvent *eos = gst_event_new_eos();
                if (!gst_element_send_event(m_pipeline, eos))
                {
                    LOG(warning) << "Failed to send EOS event" << endl;
                    gst_event_unref(eos);
                }

                // Send flush events
                GstEvent *flushStart = gst_event_new_flush_start();
                if (!gst_element_send_event(m_pipeline, flushStart))
                {
                    LOG(warning) << "Failed to send flush start event" << endl;
                    gst_event_unref(flushStart);
                }

                GstEvent *flushStop = gst_event_new_flush_stop(TRUE);
                if (!gst_element_send_event(m_pipeline, flushStop))
                {
                    LOG(warning) << "Failed to send flush stop event" << endl;
                    gst_event_unref(flushStop);
                }
                // Brief wait to allow events to propagate
                g_usleep(100000); // 100ms wait
            }
        }

        // First try to pause the pipeline before setting NULL state
        GstStateChangeReturn ret = gst_element_set_state(m_pipeline, GST_STATE_PAUSED);
        if (ret != GST_STATE_CHANGE_FAILURE)
        {
            // Wait for max 1 second for PAUSED state
            gst_element_get_state(m_pipeline, nullptr, nullptr, GST_SECOND);
        }

        // Now set NULL state with timeout
        ret = gst_element_set_state(m_pipeline, GST_STATE_NULL);
        if (ret != GST_STATE_CHANGE_FAILURE)
        {
            // Wait for max 2 seconds for NULL state
            ret = gst_element_get_state(m_pipeline, nullptr, nullptr, 2 * GST_SECOND);
            if (ret == GST_STATE_CHANGE_SUCCESS)
            {
                LOG(info) << "NULL State change success" << endl;
            }
            else
            {
                LOG(warning) << "NULL State change timeout/failed" << endl;
            }
        }
    }

    // Clean up bus watch and bus after pipeline state changes
    if (m_bus_watch_id != G_MAXUINT)
    {
        g_source_remove(m_bus_watch_id);
        m_bus_watch_id = G_MAXUINT;
    }
    if (m_bus)
    {
        gst_object_unref(m_bus);
        m_bus = nullptr;
    }

    // Clean up all elements
    if (m_sinkAudio)
    {
        gst_object_unref(m_sinkAudio);
        m_sinkAudio = nullptr;
    }
    if (m_sinkVideo)
    {
        gst_object_unref(m_sinkVideo);
        m_sinkVideo = nullptr;
    }
    if (m_filterAudio)
    {
        gst_object_unref(m_filterAudio);
        m_filterAudio = nullptr;
    }
    if (m_filterVideo)
    {
        gst_object_unref(m_filterVideo);
        m_filterVideo = nullptr;
    }
    if (m_queueAudio)
    {
        gst_object_unref(m_queueAudio);
        m_queueAudio = nullptr;
    }
    if (m_queueVideo)
    {
        gst_object_unref(m_queueVideo);
        m_queueVideo = nullptr;
    }
    if (m_source)
    {
        gst_object_unref(m_source);
        m_source = nullptr;
    }
    if (m_pipeline)
    {
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
    }

    LOG(info) << "Terminated gstreamer demux pipeline filename:" << m_filename << ", m_sessionId:" << m_sessionId << endl;
}

// Cloud storage initialization methods
bool GstDeMux::initUnifiedStorageReader()
{
    std::lock_guard<std::mutex> lock(m_storageMutex);

    try
    {
        if (m_cloudStorageEnabled || m_unifiedStorageReader)
        {
            LOG(info) << "Unified storage reader already initialized" << std::endl;
            return true;
        }

        // Get configuration from DeviceConfig
        const nv_vms::DeviceConfig& config = GET_CONFIG();

        // Use UnifiedStorageReaderUtils to create storage reader
        m_unifiedStorageReader = nv_vms::UnifiedStorageReaderUtils::createStorageReader(config);

        if (!m_unifiedStorageReader)
        {
            LOG(error) << "Failed to create unified storage reader: " << nv_vms::UnifiedStorageReaderUtils::getLastError() << std::endl;
            return false;
        }

        // Set cloud storage enabled flag based on reader type
        m_cloudStorageEnabled = nv_vms::UnifiedStorageReaderUtils::isCloudStorageEnabled(m_unifiedStorageReader);

        LOG(info) << "Unified storage reader initialized successfully" << std::endl;
        LOG(info) << "Cloud storage enabled: " << (m_cloudStorageEnabled ? "true" : "false") << std::endl;

        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage initialization: " << e.what() << std::endl;
        return false;
    }
}

bool GstDeMux::initUnifiedStorageManager()
{
    std::lock_guard<std::mutex> lock(m_storageMutex);

    try
    {
        // If already initialized, return success
        if (m_unifiedStorageManager)
        {
            LOG(info) << "Unified storage manager already initialized" << std::endl;
            return true;
        }

        // Get configuration from DeviceConfig
        const nv_vms::DeviceConfig& config = GET_CONFIG();

        // Initialize unified storage manager for file management
        m_unifiedStorageManager = nv_vms::UnifiedStorageManagerUtils::initializeStorageManager(config);

        if (!m_unifiedStorageManager)
        {
            LOG(error) << "Failed to create unified storage manager: " << nv_vms::UnifiedStorageManagerUtils::getLastError() << std::endl;
            return false;
        }

        LOG(info) << "Unified storage manager initialized successfully" << std::endl;
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage manager initialization: " << e.what() << std::endl;
        return false;
    }
}

void GstDeMux::cleanupDownloadedFiles()
{
    // First, acquire lock to check state and copy data
    std::vector<std::string> filesToCleanup;

    {
        std::lock_guard<std::mutex> lock(m_storageMutex);

        if (m_downloadedFiles.empty())
        {
            LOG(info) << "No downloaded files to clean up" << std::endl;
            return;
        }

        if (!m_unifiedStorageManager)
        {
            LOG(warning) << "Unified storage manager not available, skipping file cleanup" << std::endl;
            return;
        }

        // Copy the downloaded files list
        filesToCleanup = m_downloadedFiles;
    }

    // Release lock before lengthy I/O operations
    LOG(info) << "Cleaning up " << filesToCleanup.size() << " downloaded files" << std::endl;

    // Define callbacks for progress and completion
    auto progressCallback = [](const std::string& filePath, size_t currentIndex, size_t totalFiles) {
        LOG(info) << "Deleting file " << (currentIndex + 1) << "/" << totalFiles << ": " << filePath << std::endl;
    };

    auto completionCallback = [](const std::string& filePath, bool success, const std::string& errorMessage) {
        if (success)
        {
            LOG(verbose) << "Successfully deleted file: " << filePath << std::endl;
        }
        else
        {
            LOG(warning) << "Failed to delete file: " << filePath << " - " << errorMessage << std::endl;
        }
    };

    // Delete all files synchronously (lock is released during this operation)
    bool success = nv_vms::UnifiedStorageManagerUtils::deleteFilesSync(
        m_unifiedStorageManager,
        filesToCleanup,
        completionCallback,
        progressCallback
    );

    // Re-acquire lock to update state after deletion
    {
        std::lock_guard<std::mutex> lock(m_storageMutex);

        if (success)
        {
            LOG(info) << "Successfully cleaned up " << filesToCleanup.size() << " files" << std::endl;
            // Clear the downloaded files list after successful cleanup
            m_downloadedFiles.clear();
        }
        else
        {
            LOG(warning) << "Some files failed to clean up - this is normal if files were already deleted" << std::endl;
            // Even if some files failed, clear the list to avoid re-attempting cleanup
            m_downloadedFiles.clear();
        }
    }
}

void GstDeMux::addDownloadedFile(const std::string& filePath)
{
    std::lock_guard<std::mutex> lock(m_storageMutex);

    // Check if the file is already in the list to avoid duplicates
    auto it = std::find(m_downloadedFiles.begin(), m_downloadedFiles.end(), filePath);
    if (it == m_downloadedFiles.end())
    {
        m_downloadedFiles.push_back(filePath);
        LOG(verbose) << "Added downloaded file to cleanup list: " << filePath << std::endl;
    }
    else
    {
        LOG(verbose) << "File already in cleanup list: " << filePath << std::endl;
    }
}
