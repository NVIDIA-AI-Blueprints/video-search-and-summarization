/*
 *  Copyright (c) 2018 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

// This file contains codec dependent definitions that are needed in
// order to compile the WebRTC codebase, even if this codec is not used.

#ifndef MODULES_VIDEO_CODING_CODECS_H265_INCLUDE_H265_GLOBALS_H_
#define MODULES_VIDEO_CODING_CODECS_H265_INCLUDE_H265_GLOBALS_H_

#include <array>

#include "modules/video_coding/codecs/h264/include/h264_globals.h"

namespace webrtc {

// RFC 7798 allows many NALUs per aggregation packet; cap stored NALUs per RTP
// packet for fixed-size RTPVideoHeaderH265.
inline constexpr int kMaxNalusPerPacket = 128;
static_assert(kMaxNalusPerPacket == 128);

// The packetization types that we support: single, aggregated, and fragmented.
enum H265PacketizationTypes {
  kH265SingleNalu,  // This packet contains a single NAL unit.
  kH265AP,          // This packet contains aggregation Packet.
                    // If this packet has an associated NAL unit type,
                    // it'll be for the first such aggregated packet.
  kH265FU,          // This packet contains a FU (fragmentation
                    // unit) packet, meaning it is a part of a frame
                    // that was too large to fit into a single packet.
};

struct H265NaluInfo {
  uint8_t type;
  int vps_id;
  int sps_id;
  int pps_id;

  friend bool operator==(const H265NaluInfo& lhs, const H265NaluInfo& rhs) {
    return lhs.type == rhs.type && lhs.sps_id == rhs.sps_id &&
           lhs.pps_id == rhs.pps_id && lhs.vps_id == rhs.vps_id;
  }

  friend bool operator!=(const H265NaluInfo& lhs, const H265NaluInfo& rhs) {
    return !(lhs == rhs);
  }
};

struct RTPVideoHeaderH265 {

  // The NAL unit type. If this is a header for a fragmented packet, it's the
  // NAL unit type of the original data. If this is the header for an aggregated
  // packet, it's the NAL unit type of the first NAL unit in the packet.
  uint8_t nalu_type;
  std::array<H265NaluInfo, 128> nalus{};
  size_t nalus_length;
  // The packetization type of this buffer - single, aggregated or fragmented.
  H265PacketizationTypes packetization_type;

  friend bool operator==(const RTPVideoHeaderH265& lhs,
                         const RTPVideoHeaderH265& rhs) {
    if (lhs.nalu_type != rhs.nalu_type ||
        lhs.packetization_type != rhs.packetization_type ||
        lhs.nalus_length != rhs.nalus_length) {
      return false;
    }
    RTC_DCHECK_LE(lhs.nalus_length, static_cast<size_t>(kMaxNalusPerPacket));
    for (size_t i = 0; i < lhs.nalus_length; ++i) {
      if (lhs.nalus[i] != rhs.nalus[i]) {
        return false;
      }
    }
    return true;
  }

  friend bool operator!=(const RTPVideoHeaderH265& lhs,
                         const RTPVideoHeaderH265& rhs) {
    return !(lhs == rhs);
  }
};

}  // namespace webrtc

#endif  // MODULES_VIDEO_CODING_CODECS_H265_INCLUDE_H265_GLOBALS_H_
