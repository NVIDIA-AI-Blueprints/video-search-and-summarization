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

#pragma once

#include "media_producer.h"  // IMediaDataProducer, IMediaDataConsumer, FrameParams, eMediaType

#include <gst/gst.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace nv_vms {

// Per-camera Basler producer. Implements the standard IMediaDataProducer
// contract -- the same interface StreamMonitor, WebrtcStreamProducer and the
// replay producers use -- so the recorder's GstMux registers on it as an
// ordinary consumer. The pylon SDK is used only in the .cpp, so this producer
// can live in libbasler_producer.so without leaking pylon into any other lib.
//
// A pylon grab thread feeds I420 frames into a GStreamer encode pipeline
// (appsrc -> [nvvideoconvert] -> H.264 encoder -> h264parse -> appsink) with
// HW/SW chosen via NvHwDetection. Encoded access units are fanned out to the
// registered consumers (recorder, live) via distributeToConsumers.
class BaslerStreamProducer : public IMediaDataProducer
{
public:
    BaslerStreamProducer(std::string streamId, std::string serial);
    ~BaslerStreamProducer() override;

    // IMediaDataProducer
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                          const std::string& identifier = "") override;
    void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                            const std::string& identifier = "",
                            bool doNotRemoveClient = false) override;
    bool start() override;
    void stop() override;
    bool isRunning() const override;
    eMediaType getProducerMediaType() const override;
    std::string getSourceIdentifier() const override;
    size_t getConsumerCount() const override;
    bool hasConsumers() const override;

protected:
    void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) override;
    void distributeToConsumers(FrameParams& frameParams) override;

private:
    void grabLoop();
    bool buildPipeline(int width, int height);   // lazily built once geometry is known
    void pushFrame(const void* data, size_t size);
    void teardownPipeline();
    // appsink "new-sample" callback (runs on a GStreamer streaming thread).
    static GstFlowReturn onNewEncodedSample(GstElement* appsink, gpointer userData);

    std::string       m_streamId;
    std::string       m_serial;
    std::thread       m_thread;
    std::atomic<bool> m_exit{true};

    // GStreamer encode pipeline (built lazily on the first grabbed frame).
    GstElement* m_pipeline{nullptr};
    GstElement* m_appsrc{nullptr};
    GstElement* m_converter{nullptr};
    GstElement* m_capsBeforeEnc{nullptr};
    GstElement* m_encoder{nullptr};
    GstElement* m_parser{nullptr};
    GstElement* m_capsAfterEnc{nullptr};
    GstElement* m_appsink{nullptr};
    bool        m_pipelineBuilt{false};

    // SPS/PPS captured once (with start codes) and reported to BaslerStreamMonitor
    // for the RTSP SDP; not refreshed, mirroring the WebRTC encoder.
    std::vector<uint8_t> m_sps;
    std::vector<uint8_t> m_pps;
    bool                 m_headersReported{false};

    mutable std::mutex                               m_consumersMutex;
    std::vector<std::shared_ptr<IMediaDataConsumer>> m_consumers;
};

} // namespace nv_vms

// C ABI factory exported by libbasler_producer.so, resolved via dlsym. Returns
// the producer through the pylon-free IMediaDataProducer interface.
extern "C" IMediaDataProducer* createBaslerProducer(const char* streamId, const char* serial);
extern "C" void destroyBaslerProducer(IMediaDataProducer* producer);
