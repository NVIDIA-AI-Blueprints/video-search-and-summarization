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

#include <mutex>
#include <vector>
#include <condition_variable>
#include "media_consumer.h"

class ImageEnc : public IMediaDataConsumer
{
public:
    ImageEnc(const std::string& consumer_name);
    ImageEnc(const std::string& consumer_name, const std::map<std::string, std::string, std::less<>> &opts);
    ~ImageEnc();

    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    std::string getImageBuffer();
    GstFlowReturn processJpegImageFromSink(GstElement *appsink);

private:
    int create(string sourceWidth, string sourceHeight, string resizeWidth="", string resizeHeight="");
    void pushBuffer(std::shared_ptr<RawFrameParams> frame_data);
    void hwEncode(uint64_t fd, std::shared_ptr<RawFrameParams> frame_data);

    std::condition_variable m_imgBufferWait;
    std::mutex              m_imgBufferLock;
    std::string             m_imgBuffer;
    std::atomic<bool>       m_stop {false};

    GstElement*             m_pipeline      = nullptr;
    GstElement*             m_source        = nullptr;
    GstElement*             m_parser        = nullptr;
    GstElement*             m_scaler        = nullptr;
    GstElement*             m_filter        = nullptr;
    GstElement*             m_imageEncoder  = nullptr;
    GstElement*             m_sink          = nullptr;
};