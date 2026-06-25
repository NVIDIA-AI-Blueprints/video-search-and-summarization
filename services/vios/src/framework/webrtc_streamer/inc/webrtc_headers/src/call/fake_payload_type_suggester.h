/*
 *  Copyright (c) 2015 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef CALL_FAKE_PAYLOAD_TYPE_SUGGESTER_H_
#define CALL_FAKE_PAYLOAD_TYPE_SUGGESTER_H_

#include "absl/strings/string_view.h"
#include "api/payload_type.h"
#include "api/rtc_error.h"
#include "api/rtp_parameters.h"
#include "call/payload_type.h"
#include "call/payload_type_picker.h"
#include "media/base/codec.h"

namespace webrtc {
// Fake payload type suggester, for use in tests.
// It uses a real PayloadTypePicker in order to do consistent PT
// assignment.
class FakePayloadTypeSuggester : public PayloadTypeSuggester {
 public:
  RTCErrorOr<PayloadType> SuggestPayloadType(absl::string_view mid,
                                             const Codec& codec) override {
    // Ignores mid argument.
    return pt_picker_.SuggestMapping(codec, nullptr);
  }
  RTCError AddLocalMapping(absl::string_view,
                           PayloadType payload_type,
                           const Codec& codec) override {
    return RTCError::OK();
  }
  RTCErrorOr<int> SuggestRtpHeaderExtensionId(
      absl::string_view mid,
      const RtpExtension& extension,
      RtpTransceiverIdDomain id_domain) override {
    return rtp_extension_picker_.SuggestMapping(
        extension.uri, extension.encrypt, extension.id, id_domain, nullptr);
  }
  RTCError AddRtpHeaderExtensionMapping(absl::string_view mid,
                                        const RtpExtension& extension,
                                        bool local) override {
    return rtp_extension_picker_.AddMapping(extension.id, extension.uri,
                                            extension.encrypt);
  }

 private:
  PayloadTypePicker pt_picker_;
  RtpHeaderExtensionPicker rtp_extension_picker_;
};

}  // namespace webrtc

#endif  // CALL_FAKE_PAYLOAD_TYPE_SUGGESTER_H_
