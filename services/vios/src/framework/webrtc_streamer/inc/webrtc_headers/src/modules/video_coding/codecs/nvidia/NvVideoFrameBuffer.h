/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 * 
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include <api/video/video_frame_buffer.h>
#include <functional>

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

    rtc::scoped_refptr<webrtc::I420BufferInterface> ToI420() override;
    webrtc::VideoFrameBuffer::Type type() const override;

    void AddRef() const override;
    rtc::RefCountReleaseStatus Release() const override;

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
#ifndef DISABLE_H265
    std::string codecName = "";
#endif

    encoder_params* params;
    struct timeval rtspToWebrtcStartTime;
    struct timeval rtspToWebrtcEndTime;
private:
    bool m_passThroughMode;
};