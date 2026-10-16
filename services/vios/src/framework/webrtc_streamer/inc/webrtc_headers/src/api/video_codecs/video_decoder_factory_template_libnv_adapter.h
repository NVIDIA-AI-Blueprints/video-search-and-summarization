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

#ifndef API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_
#define API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_

#include <memory>
#include <vector>

#include "api/video_codecs/sdp_video_format.h"
#include "media/base/media_constants.h"
#include "modules/video_coding/codecs/vp8/include/vp8.h"
#include "modules/video_coding/codecs/h264/include/h264.h"
#include "modules/video_coding/codecs/nvidia/libnv_decoder.h"

namespace webrtc {
struct LibNvVideoDecoderTemplateAdapter {
  static std::vector<SdpVideoFormat> SupportedFormats() {
    std::vector<SdpVideoFormat> formats;
    formats.push_back(SdpVideoFormat(webrtc::kVp8CodecName));
    formats.push_back(SdpVideoFormat(webrtc::kVp9CodecName));
    formats.push_back(SdpVideoFormat(webrtc::kH264CodecName));
    formats.push_back(SdpVideoFormat(webrtc::kAv1CodecName));
#ifdef RTC_ENABLE_H265
    formats.push_back(SdpVideoFormat(webrtc::kH265CodecName));
#endif
    return formats;
  }

  static std::unique_ptr<VideoDecoder> CreateDecoder(
      const SdpVideoFormat& format) {
    std::unique_ptr<VideoDecoder> nvDecoder = nullptr;
    nvDecoder = NvDecoder::Create(format.name);
    return nvDecoder;
  }
};
}  // namespace webrtc

#endif  // API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_
