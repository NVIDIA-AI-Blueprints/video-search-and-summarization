/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "basler_stream_producer.h"

#include "logger.h"
#include "nvhwdetection.h"
#include "basler_stream_monitor.h"  // report cached SPS/PPS back to the registry

#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>

#include <pylon/PylonIncludes.h>
#include <pylon/ImageFormatConverter.h>  // CImageFormatConverter (libpylonutility)
#include <pylon/PylonImage.h>            // CPylonImage (libpylonutility)

#include <sys/time.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <string>
#include <utility>

namespace nv_vms {

namespace {
constexpr const char* kGigEDeviceClass = "BaslerGigE";  // restrict open() to GigE transport
constexpr unsigned kGrabTimeoutMs = 5000;               // per-frame RetrieveResult timeout
constexpr uint32_t kLogEveryNFrames = 100;              // throttle the per-frame log
constexpr int kNominalFps = 30;                         // appsrc caps framerate hint
constexpr uint32_t kHwBitrateBps = 5000000;             // nvv4l2h264enc bitrate (bps)
constexpr int kSwBitrateKbps = 5000;                    // x264enc bitrate (kbps)

// Annex-B H.264 NAL type from a start-code-prefixed buffer (SPS=7, PPS=8);
// -1 if it is not a recognisable NAL.
int h264NalType(const uint8_t* data, size_t size)
{
    size_t off = 0;
    if (size >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1) {
        off = 4;
    } else if (size >= 3 && data[0] == 0 && data[1] == 0 && data[2] == 1) {
        off = 3;
    } else {
        return -1;
    }
    return off < size ? (data[off] & 0x1F) : -1;
}
}  // namespace

BaslerStreamProducer::BaslerStreamProducer(std::string streamId, std::string serial)
    : m_streamId(std::move(streamId)), m_serial(std::move(serial))
{
}

BaslerStreamProducer::~BaslerStreamProducer()
{
    try {
        stop();
    } catch (...) {
        (void)std::current_exception();
    }
}

void BaslerStreamProducer::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                            const std::string& /*identifier*/)
{
    if (!consumer) {
        return;
    }
    std::lock_guard<std::mutex> lock(m_consumersMutex);
    if (std::find(m_consumers.begin(), m_consumers.end(), consumer) == m_consumers.end()) {
        m_consumers.push_back(std::move(consumer));
    }
}

void BaslerStreamProducer::unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                              const std::string& /*identifier*/,
                                              bool /*doNotRemoveClient*/)
{
    std::lock_guard<std::mutex> lock(m_consumersMutex);
    m_consumers.erase(std::remove(m_consumers.begin(), m_consumers.end(), consumer),
                      m_consumers.end());
}

bool BaslerStreamProducer::start()
{
    if (!m_exit) {
        return true;
    }
    m_exit = false;
    m_thread = std::thread([this] { this->grabLoop(); });
    return true;
}

void BaslerStreamProducer::stop()
{
    if (m_exit) {
        return;
    }
    m_exit = true;
    if (m_thread.joinable()) {
        m_thread.join();
    }
}

bool BaslerStreamProducer::isRunning() const
{
    return !m_exit.load();
}

eMediaType BaslerStreamProducer::getProducerMediaType() const
{
    return MediaTypeVideo;
}

std::string BaslerStreamProducer::getSourceIdentifier() const
{
    return m_serial;
}

size_t BaslerStreamProducer::getConsumerCount() const
{
    std::lock_guard<std::mutex> lock(m_consumersMutex);
    return m_consumers.size();
}

bool BaslerStreamProducer::hasConsumers() const
{
    std::lock_guard<std::mutex> lock(m_consumersMutex);
    return !m_consumers.empty();
}

void BaslerStreamProducer::distributeToConsumers(std::shared_ptr<RawFrameParams> /*frameData*/)
{
}

void BaslerStreamProducer::distributeToConsumers(FrameParams& frameParams)
{
    // Snapshot consumers before delivery to avoid holding the lock during onFrame callbacks.
    std::vector<std::shared_ptr<IMediaDataConsumer>> consumers;
    {
        std::lock_guard<std::mutex> lock(m_consumersMutex);
        consumers = m_consumers;
    }
    for (const auto& consumer : consumers) {
        if (!consumer) {
            continue;
        }
        const eMediaType type = consumer->getConsumerMediaType();
        if (type == MediaTypeVideo || type == MediaTypeAudioVideo) {
            consumer->onFrame(frameParams);
        }
    }
}

bool BaslerStreamProducer::buildPipeline(int width, int height)
{
    if (!gst_is_initialized()) {
        gst_init(nullptr, nullptr);
    }

    const bool useHwEnc = NvHwDetection::getInstance()->m_useNvV4l2Enc;

    m_pipeline      = gst_pipeline_new(nullptr);
    m_appsrc        = gst_element_factory_make("appsrc", nullptr);
    m_parser        = gst_element_factory_make("h264parse", nullptr);
    m_capsAfterEnc  = gst_element_factory_make("capsfilter", nullptr);
    m_appsink       = gst_element_factory_make("appsink", nullptr);

    if (useHwEnc) {
        m_encoder = gst_element_factory_make("nvv4l2h264enc", nullptr);
#ifdef JETSON_PLATFORM
        m_converter = gst_element_factory_make("nvvidconv", nullptr);
#else
        m_converter = gst_element_factory_make("nvvideoconvert", nullptr);
        if (!m_converter) {
            m_converter = gst_element_factory_make("nvvidconv", nullptr);
        }
#endif
        m_capsBeforeEnc = gst_element_factory_make("capsfilter", nullptr);
    } else {
        m_encoder = gst_element_factory_make("x264enc", nullptr);
        m_converter = gst_element_factory_make("videoconvert", nullptr);
    }

    if (!m_pipeline || !m_appsrc || !m_converter || !m_encoder || !m_parser ||
        !m_capsAfterEnc || !m_appsink || (useHwEnc && !m_capsBeforeEnc)) {
        LOG(error) << "BaslerStreamProducer[" << m_streamId
                   << "]: failed to create pipeline elements (hwEnc=" << useHwEnc << ")"
                   << std::endl;
        teardownPipeline();
        return false;
    }

    // appsrc: live source of I420 frames; appsrc timestamps on push.
    {
        GstCaps* srcCaps = gst_caps_new_simple(
            "video/x-raw",
            "format", G_TYPE_STRING, "I420",
            "width", G_TYPE_INT, width,
            "height", G_TYPE_INT, height,
            "framerate", GST_TYPE_FRACTION, kNominalFps, 1,
            nullptr);
        g_object_set(G_OBJECT(m_appsrc), "caps", srcCaps, "format", GST_FORMAT_TIME,
                     "is-live", TRUE, "do-timestamp", TRUE, "block", TRUE,
                     "max-bytes", (guint64)(width * height * 3), nullptr);  // ~2 frames of backpressure
        gst_caps_unref(srcCaps);
    }

    // Encoder configuration mirrors transcode_writer_consumer.cpp.
    if (useHwEnc) {
        GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_encoder), "num-B-Frames");
        if (ps) g_object_set(G_OBJECT(m_encoder), "num-B-Frames", 0, nullptr);
        ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_encoder), "idrinterval");
        if (ps) g_object_set(G_OBJECT(m_encoder), "idrinterval", kNominalFps, nullptr);
        g_object_set(G_OBJECT(m_encoder), "bitrate", kHwBitrateBps, nullptr);

        GstCaps* nvmmCaps = gst_caps_from_string("video/x-raw(memory:NVMM), format=(string)NV12");
        g_object_set(G_OBJECT(m_capsBeforeEnc), "caps", nvmmCaps, nullptr);
        gst_caps_unref(nvmmCaps);
    } else {
        GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_encoder), "bframes");
        if (ps) g_object_set(G_OBJECT(m_encoder), "bframes", 0, nullptr);
        ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_encoder), "speed-preset");
        if (ps) g_object_set(G_OBJECT(m_encoder), "speed-preset", 1, nullptr);  // ultrafast
        ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_encoder), "key-int-max");
        if (ps) g_object_set(G_OBJECT(m_encoder), "key-int-max", kNominalFps, nullptr);
        g_object_set(G_OBJECT(m_encoder), "bitrate", kSwBitrateKbps, nullptr);
    }

    // Prepend SPS/PPS before every IDR so mid-stream consumers can decode from the next key frame.
    {
        GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(m_parser), "config-interval");
        if (ps) g_object_set(G_OBJECT(m_parser), "config-interval", -1, nullptr);
    }

    // alignment=nal: consumers expect one NAL per onFrame call (SPS, then PPS, then IDR).
    // An AU-aligned buffer misreads as a lone SPS, preventing m_startConsuming from flipping.
    {
        GstCaps* h264Caps = gst_caps_from_string(
            "video/x-h264, stream-format=(string)byte-stream, alignment=(string)nal");
        g_object_set(G_OBJECT(m_capsAfterEnc), "caps", h264Caps, nullptr);
        gst_caps_unref(h264Caps);
    }

    g_object_set(G_OBJECT(m_appsink), "emit-signals", TRUE, "sync", FALSE, nullptr);
    g_signal_connect(m_appsink, "new-sample", G_CALLBACK(onNewEncodedSample), this);

    // Assemble: appsrc -> converter -> [capsBeforeEnc] -> encoder -> parser -> capsAfterEnc -> appsink
    gst_bin_add_many(GST_BIN(m_pipeline), m_appsrc, m_converter, m_encoder, m_parser,
                     m_capsAfterEnc, m_appsink, nullptr);
    if (m_capsBeforeEnc) {
        gst_bin_add(GST_BIN(m_pipeline), m_capsBeforeEnc);
    }

    gboolean linked = m_capsBeforeEnc
        ? gst_element_link_many(m_appsrc, m_converter, m_capsBeforeEnc, m_encoder, m_parser,
                                m_capsAfterEnc, m_appsink, nullptr)
        : gst_element_link_many(m_appsrc, m_converter, m_encoder, m_parser,
                                m_capsAfterEnc, m_appsink, nullptr);
    if (!linked) {
        LOG(error) << "BaslerStreamProducer[" << m_streamId << "]: failed to link pipeline"
                   << std::endl;
        teardownPipeline();
        return false;
    }

    if (gst_element_set_state(m_pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
        LOG(error) << "BaslerStreamProducer[" << m_streamId << "]: pipeline failed to start"
                   << std::endl;
        teardownPipeline();
        return false;
    }

    m_pipelineBuilt = true;
    LOG(info) << "BaslerStreamProducer[" << m_streamId << "]: encode pipeline started "
              << width << "x" << height << " (" << (useHwEnc ? "HW nvv4l2h264enc" : "SW x264enc")
              << ")" << std::endl;
    return true;
}

void BaslerStreamProducer::pushFrame(const void* data, size_t size)
{
    if (!m_appsrc || size == 0) {
        return;
    }
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, size, nullptr);
    if (!buffer) {
        return;
    }
    // Copy: the pylon CPylonImage backing buffer is reused for the next frame.
    gst_buffer_fill(buffer, 0, data, size);
    GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(m_appsrc), buffer);  // takes ownership
    if (ret != GST_FLOW_OK) {
        LOG(warning) << "BaslerStreamProducer[" << m_streamId << "]: appsrc push returned "
                     << gst_flow_get_name(ret) << std::endl;
    }
}

GstFlowReturn BaslerStreamProducer::onNewEncodedSample(GstElement* appsink, gpointer userData)
{
    auto* self = static_cast<BaslerStreamProducer*>(userData);
    GstSample* sample = gst_app_sink_pull_sample(GST_APP_SINK(appsink));
    if (!sample) {
        return GST_FLOW_OK;
    }
    GstBuffer* buffer = gst_sample_get_buffer(sample);
    GstMapInfo map;
    if (buffer && gst_buffer_map(buffer, &map, GST_MAP_READ)) {
        // Cache the first SPS/PPS for RTSP SDP sprop-parameter-sets.
        if (!self->m_headersReported) {
            const int nt = h264NalType(map.data, map.size);
            if (nt == 7 && self->m_sps.empty()) {
                self->m_sps.assign(map.data, map.data + map.size);
            } else if (nt == 8 && self->m_pps.empty()) {
                self->m_pps.assign(map.data, map.data + map.size);
            }
            if (!self->m_sps.empty() && !self->m_pps.empty()) {
                BaslerStreamMonitor::getInstance()->setVideoHeaders(
                    self->m_streamId, {self->m_sps, self->m_pps});
                self->m_headersReported = true;
                LOG(info) << "BaslerStreamProducer[" << self->m_streamId
                          << "]: cached SPS/PPS for SDP (sps=" << self->m_sps.size()
                          << "B pps=" << self->m_pps.size() << "B)" << std::endl;
            }
        }
        FrameParams frameParams;
        frameParams.m_media  = "video";
        frameParams.m_codec  = "h264";
        frameParams.m_buffer = map.data;
        frameParams.m_size   = static_cast<ssize_t>(map.size);
        gettimeofday(&frameParams.m_presentationTime, nullptr);
        self->distributeToConsumers(frameParams);

        gst_buffer_unmap(buffer, &map);
    }
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

void BaslerStreamProducer::teardownPipeline()
{
    if (m_pipeline) {
        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
    }
    // Children were owned by the pipeline bin; drop our dangling handles.
    m_appsrc = m_converter = m_capsBeforeEnc = m_encoder = nullptr;
    m_parser = m_capsAfterEnc = m_appsink = nullptr;
    m_pipelineBuilt = false;
}

void BaslerStreamProducer::grabLoop()
{
    // Per-thread pylon init (ref-counted across producers); released on return.
    Pylon::PylonAutoInitTerm autoInit;
    try {
        Pylon::CDeviceInfo deviceInfo;
        deviceInfo.SetDeviceClass(kGigEDeviceClass);
        deviceInfo.SetSerialNumber(Pylon::String_t(m_serial.c_str()));

        Pylon::CInstantCamera camera(Pylon::CTlFactory::GetInstance().CreateDevice(deviceInfo));
        camera.Open();

        // Demosaic + colorspace convert Bayer8 -> planar I420 (the encoder input).
        Pylon::CImageFormatConverter converter;
        converter.OutputPixelFormat = Pylon::PixelType_YUV420planar;  // I420
        Pylon::CPylonImage i420Image;

        camera.StartGrabbing();  // continuous, oldest-first (GrabStrategy_OneByOne)
        LOG(info) << "BaslerStreamProducer[" << m_streamId << "]: grabbing from serial "
                  << m_serial << std::endl;

        uint32_t frames = 0;
        while (camera.IsGrabbing() && !m_exit) {
            Pylon::CGrabResultPtr result;
            camera.RetrieveResult(kGrabTimeoutMs, result, Pylon::TimeoutHandling_ThrowException);
            if (!result->GrabSucceeded()) {
                LOG(warning) << "BaslerStreamProducer[" << m_streamId << "]: grab failed: "
                             << result->GetErrorDescription().c_str() << std::endl;
                continue;
            }

            converter.Convert(i420Image, result);

            // Build the encode pipeline lazily, once the geometry is known.
            if (!m_pipelineBuilt) {
                if (!buildPipeline(static_cast<int>(result->GetWidth()),
                                   static_cast<int>(result->GetHeight()))) {
                    break;  // build failed; stop grabbing
                }
            }

            pushFrame(i420Image.GetBuffer(), i420Image.GetImageSize());

            if (++frames % kLogEveryNFrames == 1) {
                LOG(info) << "BaslerStreamProducer[" << m_streamId << "]: grabbed frame " << frames
                          << " " << result->GetWidth() << "x" << result->GetHeight() << std::endl;
            }
        }

        camera.StopGrabbing();
        camera.Close();
        LOG(info) << "BaslerStreamProducer[" << m_streamId << "]: stopped after " << frames
                  << " grabbed frame(s)" << std::endl;
    } catch (const Pylon::GenericException& e) {
        LOG(error) << "BaslerStreamProducer[" << m_streamId << "]: pylon exception: "
                   << e.GetDescription() << std::endl;
    }

    teardownPipeline();
}

}  // namespace nv_vms

extern "C" IMediaDataProducer* createBaslerProducer(const char* streamId, const char* serial)
{
    return new nv_vms::BaslerStreamProducer(streamId ? streamId : "", serial ? serial : "");
}

extern "C" void destroyBaslerProducer(IMediaDataProducer* producer)
{
    delete producer;
}
