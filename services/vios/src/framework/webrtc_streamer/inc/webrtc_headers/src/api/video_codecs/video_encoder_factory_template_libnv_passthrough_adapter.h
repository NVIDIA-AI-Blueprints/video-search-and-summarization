/* Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
 *
 * NVIDIA CORPORATION and its licensors retain all intellectual property
 * and proprietary rights in and to this software, related documentation
 * and any modifications thereto.  Any use, reproduction, disclosure or
 * distribution of this software and related documentation without an express
 * license agreement from NVIDIA CORPORATION is strictly prohibited.
 */

#ifndef API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_
#define API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_

#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "absl/container/inlined_vector.h"
#include "api/environment/environment.h"
#include "api/video_codecs/scalability_mode.h"
#include "api/video_codecs/h264_profile_level_id.h"
#include "api/video_codecs/sdp_video_format.h"
#include "media/base/media_constants.h"
#include "rtc_base/checks.h"
#include "modules/video_coding/codecs/nvidia/libnv_passthrough_encoder.h"

namespace webrtc {
struct LibNvPassthroughVideoEncoderTemplateAdapter {
  static SdpVideoFormat CreateH264PassthroughFormat(
      H264Profile profile,
      H264Level level,
      const std::string& packetization_mode) {
    const std::optional<std::string> profile_string =
        H264ProfileLevelIdToString(H264ProfileLevelId(profile, level));
    RTC_CHECK(profile_string);
    return SdpVideoFormat(
        kH264CodecName,
        {{kH264FmtpProfileLevelId, *profile_string},
         {kH264FmtpLevelAsymmetryAllowed, "1"},
         {kH264FmtpPacketizationMode, packetization_mode}});
  }

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
    std::vector<SdpVideoFormat> supported_codecs = {
        CreateH264PassthroughFormat(
            H264Profile::kProfileBaseline, H264Level::kLevel4_1, "1"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileBaseline, H264Level::kLevel4_1, "0"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileBaseline, H264Level::kLevel4_2, "1"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileBaseline, H264Level::kLevel4_2, "0"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileHigh, H264Level::kLevel4_1, "1"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileHigh, H264Level::kLevel4_1, "0"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileHigh, H264Level::kLevel4_2, "1"),
        CreateH264PassthroughFormat(
            H264Profile::kProfileHigh, H264Level::kLevel4_2, "0"),
        SdpVideoFormat(kH264CodecName)};
#ifdef RTC_ENABLE_H265
    supported_codecs.push_back(SdpVideoFormat(webrtc::kH265CodecName));
#endif
    return supported_codecs;
  }

  static std::unique_ptr<VideoEncoder> CreateEncoder(
      const Environment& env,
      const SdpVideoFormat& format) {
    (void)env;
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

#endif  // API_VIDEO_CODECS_VIDEO_ENCODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_
