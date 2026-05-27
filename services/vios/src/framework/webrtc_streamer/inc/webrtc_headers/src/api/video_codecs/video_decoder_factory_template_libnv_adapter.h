/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_
#define API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_

#include <memory>
#include <vector>

#include "api/video_codecs/sdp_video_format.h"
#include "modules/video_coding/codecs/vp8/include/vp8.h"
#include "modules/video_coding/codecs/h264/include/h264.h"
#include "modules/video_coding/codecs/nvidia/libnv_decoder.h"

namespace webrtc {
struct LibNvVideoDecoderTemplateAdapter {
  static std::vector<SdpVideoFormat> SupportedFormats() {
    std::vector<SdpVideoFormat> formats;
    formats.push_back(SdpVideoFormat(cricket::kVp8CodecName));
    formats.push_back(SdpVideoFormat(cricket::kVp9CodecName));
    formats.push_back(SdpVideoFormat(cricket::kH264CodecName));
    formats.push_back(SdpVideoFormat(cricket::kAv1CodecName));
#ifndef DISABLE_H265
    formats.push_back(SdpVideoFormat(cricket::kH265CodecName));
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
