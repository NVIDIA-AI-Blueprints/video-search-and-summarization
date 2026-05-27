/*
 *  Copyright (c) 2023 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_
#define API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_

#pragma once

#include <memory>
#include <vector>

#include "modules/video_coding/codecs/nvidia/libnv_passthrough_decoder.h"
//#include "common_types.h"
#include "absl/strings/match.h"
#include "media/base/codec.h"
#include "media/base/h264_profile_level_id.h"
#include "media/base/media_constants.h"
#include "rtc_base/checks.h"
#include "rtc_base/logging.h"

namespace webrtc {
struct LibNvPassthroughVideoDecoderTemplateAdapter {
  static webrtc::SdpVideoFormat CreateH264Format(
    webrtc::H264Profile profile,
    webrtc::H264Level level,
    const std::string& packetization_mode) {
      const absl::optional<std::string> profile_string =
        webrtc::H264ProfileLevelIdToString(webrtc::H264ProfileLevelId(profile, level));
      RTC_CHECK(profile_string);
      return webrtc::SdpVideoFormat(
        cricket::kH264CodecName,
        {{cricket::kH264FmtpProfileLevelId, *profile_string},
        {cricket::kH264FmtpLevelAsymmetryAllowed, "1"},
        {cricket::kH264FmtpPacketizationMode, packetization_mode}
        });
}

  static bool IsFormatSupported(const std::vector<webrtc::SdpVideoFormat>& supported_formats,
                                    const webrtc::SdpVideoFormat& format) {
    for (const webrtc::SdpVideoFormat& supported_format : supported_formats) {
      if (format.IsSameCodec(supported_format)) {
        return true;
      }
    }
    return false;
  }

  static std::vector<webrtc::SdpVideoFormat> SupportedFormats() {
    std::vector<webrtc::SdpVideoFormat> formats {
      CreateH264Format(webrtc::H264Profile::kProfileBaseline, webrtc::H264Level::kLevel4_1, "1"),
      CreateH264Format(webrtc::H264Profile::kProfileBaseline, webrtc::H264Level::kLevel4_1, "0"),
      CreateH264Format(webrtc::H264Profile::kProfileBaseline, webrtc::H264Level::kLevel4_2, "1"),
      CreateH264Format(webrtc::H264Profile::kProfileBaseline, webrtc::H264Level::kLevel4_2, "0"),

      /// these should be what is "really" supported
      CreateH264Format(webrtc::H264Profile::kProfileHigh, webrtc::H264Level::kLevel4_1, "1"),
      CreateH264Format(webrtc::H264Profile::kProfileHigh, webrtc::H264Level::kLevel4_1, "0"),
      CreateH264Format(webrtc::H264Profile::kProfileHigh, webrtc::H264Level::kLevel4_2, "1"),
      CreateH264Format(webrtc::H264Profile::kProfileHigh, webrtc::H264Level::kLevel4_2, "0")
    };

    formats.push_back(webrtc::SdpVideoFormat(cricket::kH264CodecName));
#ifndef DISABLE_H265
    formats.push_back(webrtc::SdpVideoFormat(cricket::kH265CodecName));
#endif
    return formats;
  }

  static std::unique_ptr<VideoDecoder> CreateDecoder(
      const webrtc::SdpVideoFormat& format) {
    if (!IsFormatSupported(SupportedFormats(), format)) {
      auto param = format.parameters.find(cricket::kH264FmtpProfileLevelId);
      if (param != format.parameters.end()) {
        printf("\nTrying to create decoder for unsupported format: %s %s %s\n",
          format.name.c_str(),
          (*param).first.c_str(),
          (*param).second.c_str());
      }
      else {
        printf("\nTrying to create decoder for unsupported format: %s\n", format.name.c_str());
      }
      return nullptr;
    }
    return std::make_unique<NvPassthroughVideoDecoder>();
  }
};
}  // namespace webrtc

#endif // API_VIDEO_CODECS_VIDEO_DECODER_FACTORY_TEMPLATE_LIBNV_PASSTHROUGH_ADAPTER_H_