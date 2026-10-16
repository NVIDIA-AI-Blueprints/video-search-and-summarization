/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
