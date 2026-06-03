// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_OVERSCROLL_BOUNCE_CONTROLLER_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_OVERSCROLL_BOUNCE_CONTROLLER_H_

#include "base/macros.h"
#include "base/memory/weak_ptr.h"
#include "cc/input/input_handler.h"
#include "cc/input/overscroll_behavior.h"
#include "cc/input/scroll_elasticity_helper.h"
#include "third_party/blink/public/common/input/web_gesture_event.h"
#include "third_party/blink/public/platform/input/elastic_overscroll_controller.h"

namespace blink {
// The overbounce version of elastic overscrolling mimics Windows style
// overscroll animations.
class BLINK_PLATFORM_EXPORT OverscrollBounceController
    : public ElasticOverscrollController {
 public:
  explicit OverscrollBounceController(cc::ScrollElasticityHelper* helper);
  ~OverscrollBounceController() override;

  base::WeakPtr<ElasticOverscrollController> GetWeakPtr() override;

  void ObserveGestureEventAndResult(
      const blink::WebGestureEvent& gesture_event,
      const cc::InputHandlerScrollResult& scroll_result) override;
  void Animate(base::TimeTicks time) override;
  void ReconcileStretchAndScroll() override;
  gfx::Vector2d OverscrollBounceDistance(
      const gfx::Vector2dF& distance_overscrolled,
      const gfx::Size& scroller_bounds) const;

 private:
  void ObserveRealScrollBegin(const blink::WebGestureEvent& gesture_event);
  void ObserveRealScrollEnd();
  gfx::Vector2dF OverscrollBoundary(const gfx::Size& scroller_bounds) const;
  void EnterStateActiveScroll();
  void OverscrollIfNecessary(const gfx::Vector2dF& overscroll_delta);

  void ObserveScrollUpdate(const gfx::Vector2dF& unused_scroll_delta);

  enum State {
    // The initial state, during which the overscroll amount is zero.
    kStateInactive,
    // ActiveScroll indicates that this controller is listening to future GSU
    // events, and those events may or may not update the overscroll amount.
    // This occurs when the user is actively panning either via a touchscreen or
    // touchpad, or is an active fling that has not triggered an overscroll.
    kStateActiveScroll,
  };

  State state_;
  cc::ScrollElasticityHelper* helper_;

  // This is the accumulated raw delta in pixels that's been overscrolled. It
  // will be fed into a tanh function (ranging [0, 2]) that decides the stretch
  // bounds.
  gfx::Vector2dF accumulated_scroll_delta_;

  base::WeakPtrFactory<OverscrollBounceController> weak_factory_;
  DISALLOW_COPY_AND_ASSIGN(OverscrollBounceController);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_OVERSCROLL_BOUNCE_CONTROLLER_H_
