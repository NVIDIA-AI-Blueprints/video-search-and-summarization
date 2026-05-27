// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_SCROLL_TYPES_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_SCROLL_TYPES_H_

namespace blink {

// Platform overlay scrollbars are controlled and painted by the operating
// system (e.g., OSX and Android).  CSS overlay scrollbars are created by
// setting overflow:overlay, and they are painted by chromium.
enum OverlayScrollbarClipBehavior {
  kIgnorePlatformOverlayScrollbarSize,
  kIgnorePlatformAndCSSOverlayScrollbarSize,
  kExcludeOverlayScrollbarSizeForHitTesting
};

}  // namespace blink

#endif
