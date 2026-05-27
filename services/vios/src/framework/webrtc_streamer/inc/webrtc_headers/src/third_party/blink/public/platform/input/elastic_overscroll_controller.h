// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_ELASTIC_OVERSCROLL_CONTROLLER_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_ELASTIC_OVERSCROLL_CONTROLLER_H_

#include <memory>

#include "base/macros.h"
#include "base/memory/weak_ptr.h"
#include "cc/input/overscroll_behavior.h"
#include "third_party/blink/public/platform/web_common.h"

namespace cc {
struct InputHandlerScrollResult;
class ScrollElasticityHelper;
}  // namespace cc

namespace blink {
class WebGestureEvent;

// ElasticOverscrollControllers are objects that live on the compositor thread
// which are responsible for maintaining the elastic scroll amount of the root
// scroller. They will passively observe scroll results, and if there were any
// unconsumed deltas for the root scroller (meaning that the user scrolled past
// the bounds), then they will start the overscroll and filter future events
// from reaching the intended scroller.
class BLINK_PLATFORM_EXPORT ElasticOverscrollController {
 public:
  static std::unique_ptr<ElasticOverscrollController> Create(
      cc::ScrollElasticityHelper* helper);
  virtual ~ElasticOverscrollController() = default;

  virtual base::WeakPtr<ElasticOverscrollController> GetWeakPtr() = 0;

  virtual void ObserveGestureEventAndResult(
      const WebGestureEvent& gesture_event,
      const cc::InputHandlerScrollResult& scroll_result) = 0;

  virtual void Animate(base::TimeTicks time) = 0;

  virtual void ReconcileStretchAndScroll() = 0;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_ELASTIC_OVERSCROLL_CONTROLLER_H_
