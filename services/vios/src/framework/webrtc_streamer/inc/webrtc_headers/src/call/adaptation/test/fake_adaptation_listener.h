/*
 *  Copyright 2020 The WebRTC Project Authors. All rights reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef CALL_ADAPTATION_TEST_FAKE_ADAPTATION_LISTENER_H_
#define CALL_ADAPTATION_TEST_FAKE_ADAPTATION_LISTENER_H_

#include "call/adaptation/adaptation_listener.h"

namespace webrtc {

class FakeAdaptationListener : public AdaptationListener {
 public:
  FakeAdaptationListener();
  ~FakeAdaptationListener() override;

  size_t num_adaptations_applied() const;

  // AdaptationListener implementation.
  void OnAdaptationApplied(
      const VideoStreamInputState& input_state,
      const VideoSourceRestrictions& restrictions_before,
      const VideoSourceRestrictions& restrictions_after,
      rtc::scoped_refptr<Resource> reason_resource) override;

 private:
  size_t num_adaptations_applied_;
};

}  // namespace webrtc

#endif  // CALL_ADAPTATION_TEST_FAKE_ADAPTATION_LISTENER_H_
