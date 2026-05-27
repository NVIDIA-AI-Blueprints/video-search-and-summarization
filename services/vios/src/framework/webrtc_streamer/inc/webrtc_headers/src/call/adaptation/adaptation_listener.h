/*
 *  Copyright 2020 The WebRTC Project Authors. All rights reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef CALL_ADAPTATION_ADAPTATION_LISTENER_H_
#define CALL_ADAPTATION_ADAPTATION_LISTENER_H_

#include "api/adaptation/resource.h"
#include "api/scoped_refptr.h"
#include "call/adaptation/video_source_restrictions.h"
#include "call/adaptation/video_stream_input_state.h"

namespace webrtc {

// TODO(hbos): Can this be consolidated with
// ResourceAdaptationProcessorListener::OnVideoSourceRestrictionsUpdated()? Both
// listen to adaptations being applied, but on different layers with different
// arguments.
class AdaptationListener {
 public:
  virtual ~AdaptationListener();

  // TODO(https://crbug.com/webrtc/11172): When we have multi-stream adaptation
  // support, this interface needs to indicate which stream the adaptation
  // applies to.
  virtual void OnAdaptationApplied(
      const VideoStreamInputState& input_state,
      const VideoSourceRestrictions& restrictions_before,
      const VideoSourceRestrictions& restrictions_after,
      rtc::scoped_refptr<Resource> reason_resource) = 0;
};

}  // namespace webrtc

#endif  // CALL_ADAPTATION_ADAPTATION_LISTENER_H_
