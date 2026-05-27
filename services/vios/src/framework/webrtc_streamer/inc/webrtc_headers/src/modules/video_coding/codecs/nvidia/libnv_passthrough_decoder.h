// Copyright (c) 2019-2024, NVIDIA CORPORATION.  All rights reserved.
//
// NVIDIA CORPORATION and its licensors retain all intellectual property
// and proprietary rights in and to this software, related documentation
// and any modifications thereto.  Any use, reproduction, disclosure or
// distribution of this software and related documentation without an express
// license agreement from NVIDIA CORPORATION is strictly prohibited.

// Adapted from WebRTC sources in fake_decoder.cc

#pragma once

#include <stdint.h>

#include <api/task_queue/task_queue_factory.h>
#include <api/video/encoded_image.h>
#include <api/video_codecs/video_codec.h>
#include <api/video_codecs/video_decoder.h>
#include <modules/video_coding/include/video_codec_interface.h>
#include <rtc_base/task_queue.h>
#include "api/video_codecs/webrtcstats.h"

class RTC_EXPORT NvPassthroughVideoDecoder : public webrtc::VideoDecoder
{
 public:
  NvPassthroughVideoDecoder();
  explicit NvPassthroughVideoDecoder(webrtc::TaskQueueFactory* taskQueueFactory);
  virtual ~NvPassthroughVideoDecoder() { receiver_latency_stats.clearQueue(); }

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

  WebrtcCodecStats receiver_latency_stats;
  int64_t lastStatsPrintTime = 0;

 private:
  webrtc::DecodedImageCallback* m_callback;
  int m_width;
  int m_height;
  std::unique_ptr<rtc::TaskQueue> m_taskQueue;
  webrtc::TaskQueueFactory* m_taskQueueFactory;
  webrtc::TimeDelta m_decodeDelayMs;
};
