// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_VIEWPORT_INTERSECTION_STATE_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_VIEWPORT_INTERSECTION_STATE_H_

#include "third_party/blink/public/platform/platform.h"
#include "third_party/blink/public/platform/web_rect.h"
#include "ui/gfx/geometry/point.h"

namespace blink {

// Indicates whether a child frame is occluded or visually altered (e.g., with
// CSS opacity or transform) by content or styles in the parent frame.
enum class FrameOcclusionState {
  // No occlusion determination was made.
  kUnknown = 0,
  // The frame *may* be occluded or visually altered.
  kPossiblyOccluded = 1,
  // The frame is definitely not occluded or visually altered.
  kGuaranteedNotOccluded = 2,
  kMaxValue = kGuaranteedNotOccluded,
};

// These values are used to implement a browser intervention: if a cross-
// origin iframe has moved more than 30 screen pixels (manhattan distance)
// within its embedding page's viewport within the last 500 milliseconds, most
// input events targeting the iframe will be quietly discarded.
static constexpr uint32_t kMaxChildFrameScreenRectMovement = 30;
static constexpr uint32_t kMinScreenRectStableTimeMs = 500;

// Communicates information about the position and visibility of a child frame
// within the viewport of the top-level main frame.
struct BLINK_PLATFORM_EXPORT ViewportIntersectionState {
  bool operator==(const ViewportIntersectionState& other) const {
    return viewport_offset == other.viewport_offset &&
           viewport_intersection == other.viewport_intersection &&
           compositor_visible_rect == other.compositor_visible_rect &&
           occlusion_state == other.occlusion_state &&
           main_frame_viewport_size == other.main_frame_viewport_size &&
           main_frame_scroll_offset == other.main_frame_scroll_offset;
  }
  bool operator!=(const ViewportIntersectionState& other) const {
    return !(*this == other);
  }

  // Child frame's offset from the main frame.
  gfx::Point viewport_offset;
  // Portion of the child frame which is within the main frame's scrolling
  // Child frame's offset from the main frame.
  WebRect viewport_intersection;
  // Same as viewport_intersection, but without applying the main frame's
  // document-level overflow clip.
  WebRect main_frame_document_intersection;
  // Area of the child frame that needs to be rastered, in physical pixels.
  WebRect compositor_visible_rect;
  // Occlusion state, as described above.
  FrameOcclusionState occlusion_state = FrameOcclusionState::kUnknown;
  // Main frame's size.
  WebSize main_frame_viewport_size;
  // Main frame's scrolling offset.
  gfx::Point main_frame_scroll_offset;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_VIEWPORT_INTERSECTION_STATE_H_
