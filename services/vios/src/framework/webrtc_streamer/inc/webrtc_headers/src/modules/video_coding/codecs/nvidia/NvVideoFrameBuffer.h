/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "api/ref_count.h"
#include "api/scoped_refptr.h"
#include <api/video/video_frame_buffer.h>
#include <functional>
#include <string>

#pragma once

struct encoder_params
{
    int m_qp;
    unsigned int m_targetBps;
    double       m_frameRate;
};

class RTC_EXPORT NvVideoFrameBuffer : public webrtc::VideoFrameBuffer
{
public:
    NvVideoFrameBuffer(int width, int height);
    ~NvVideoFrameBuffer();

    webrtc::scoped_refptr<webrtc::I420BufferInterface> ToI420() override;
    webrtc::VideoFrameBuffer::Type type() const override;

    void AddRef() const override;
    webrtc::RefCountReleaseStatus Release() const override;

    int width () const override;
    int height () const override;

    void setPassThrough(bool mode);
    bool getPassThrough();

    std::function<void(void*)> m_callBackFunction;
    void registerCB (std::function <void (void* )> callBackFunction);

    void*    m_clientBuffer;
    int      m_width = 1920;
    int      m_height = 1080;
    uint8_t* m_encodedData;
    size_t   m_encodedSize;
    /* To store the NvBufSurface of the decoded data */
    uint8_t* m_decodedData;
    size_t   m_decodedSize;
    std::string codecName = "";

    encoder_params* params;
    struct timeval rtspToWebrtcStartTime;
    struct timeval rtspToWebrtcEndTime;
private:
    bool m_passThroughMode;
};