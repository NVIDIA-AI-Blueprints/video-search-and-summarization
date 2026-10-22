/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

// Adapted from WebRTC sources in fake_decoder.cc

#pragma once

#include <stdint.h>

#include "api/task_queue/task_queue_base.h"
#include "api/task_queue/task_queue_factory.h"
#include <api/video/encoded_image.h>
#include <api/video_codecs/video_codec.h>
#include <api/video_codecs/video_decoder.h>
#include <modules/video_coding/include/video_codec_interface.h>
#include "api/video_codecs/webrtcstats.h"

class RTC_EXPORT NvPassthroughVideoDecoder : public webrtc::VideoDecoder
{
 public:
  NvPassthroughVideoDecoder();
  explicit NvPassthroughVideoDecoder(webrtc::TaskQueueFactory* taskQueueFactory);
  virtual ~NvPassthroughVideoDecoder() {}

  int32_t InitDecode(const webrtc::VideoCodec* config,
                     int32_t numberOfCores);// override;

  int32_t Decode(const webrtc::EncodedImage& input,
                 bool missingFrames,
                 int64_t renderTimeMs);// override;

  int32_t RegisterDecodeCompleteCallback(
      webrtc::DecodedImageCallback* callback);// override;

  int32_t Release();// override;

  const char* ImplementationName() const;// override;

  static const char* kImplementationName;

  void SetDelayedDecoding(int decodeDelayMs);

  virtual bool Configure(const Settings& settings) { return true; }

 private:
  webrtc::DecodedImageCallback* m_callback;
  int m_width;
  int m_height;
  std::unique_ptr<webrtc::TaskQueueBase, webrtc::TaskQueueDeleter> m_taskQueue;
  webrtc::TaskQueueFactory* m_taskQueueFactory;
  webrtc::TimeDelta m_decodeDelayMs;
  webrtc::VideoCodecType configured_codec_type_ = webrtc::kVideoCodecGeneric;
};
