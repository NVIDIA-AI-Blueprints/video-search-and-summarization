/* Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
 *
 * NVIDIA CORPORATION and its licensors retain all intellectual property
 * and proprietary rights in and to this software, related documentation
 * and any modifications thereto.  Any use, reproduction, disclosure or
 * distribution of this software and related documentation without an express
 * license agreement from NVIDIA CORPORATION is strictly prohibited.
 */

#ifndef API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_
#define API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_

#include <memory>
#include <vector>

#include "absl/container/inlined_vector.h"
#include "api/video_codecs/sdp_video_format.h"
#include "modules/video_coding/codecs/nvidia/libnv_passthrough_encoder.h"

namespace webrtc {
struct LibNvPassthroughVideoEncoderTemplateAdapter {
  static bool IsFormatSupported(const std::vector<SdpVideoFormat>& supportedFormats,
                       const SdpVideoFormat& format) {
    for (const SdpVideoFormat& supported_format : supportedFormats) {
      if (format.IsSameCodec(supported_format)) {
        return true;
      }
    }
    return false;
  }

  static std::vector<SdpVideoFormat> SupportedFormats() {
    std::vector<SdpVideoFormat> supported_codecs;
    for (const SdpVideoFormat& format : SupportedH264Codecs())
    {
        supported_codecs.push_back(format);
    }
#ifndef DISABLE_H265
    supported_codecs.push_back(SdpVideoFormat(cricket::kH265CodecName));
#endif
    return supported_codecs;
  }

  static std::unique_ptr<VideoEncoder> CreateEncoder(
      const SdpVideoFormat& format) {
    std::unique_ptr<VideoEncoder> nvEncoder = nullptr;
    if (IsFormatSupported (SupportedFormats(), format))
    {
        nvEncoder = NvPassthroughEncoder::Create(format.name);
    }
    return nvEncoder;
  }

  static bool IsScalabilityModeSupported(ScalabilityMode scalability_mode) {
    return false;
  }
};
}  // namespace webrtc

#endif  // API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_ADAPTER_H_

