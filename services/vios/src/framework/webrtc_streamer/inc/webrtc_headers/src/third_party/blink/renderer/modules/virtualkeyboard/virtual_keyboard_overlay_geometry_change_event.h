// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_VIRTUAL_KEYBOARD_OVERLAY_GEOMETRY_CHANGE_EVENT_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_VIRTUAL_KEYBOARD_OVERLAY_GEOMETRY_CHANGE_EVENT_H_

#include "third_party/blink/renderer/modules/event_modules.h"
#include "third_party/blink/renderer/platform/heap/handle.h"

namespace blink {

class DOMRect;
class VirtualKeyboardOverlayGeometryChangeEventInit;

class VirtualKeyboardOverlayGeometryChangeEvent final : public Event {
  DEFINE_WRAPPERTYPEINFO();

 public:
  static VirtualKeyboardOverlayGeometryChangeEvent* Create(
      const AtomicString& type,
      const VirtualKeyboardOverlayGeometryChangeEventInit*);

  VirtualKeyboardOverlayGeometryChangeEvent(
      const AtomicString& type,
      const VirtualKeyboardOverlayGeometryChangeEventInit*);
  VirtualKeyboardOverlayGeometryChangeEvent(const AtomicString& type, DOMRect*);

  DOMRect* boundingRect() const { return bounding_rect_; }

  void Trace(Visitor*) const override;

 private:
  Member<DOMRect> bounding_rect_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_VIRTUAL_KEYBOARD_OVERLAY_GEOMETRY_CHANGE_EVENT_H_
