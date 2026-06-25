// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_NAVIGATOR_VIRTUAL_KEYBOARD_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_NAVIGATOR_VIRTUAL_KEYBOARD_H_

#include "base/macros.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/frame/navigator.h"
#include "third_party/blink/renderer/platform/heap/handle.h"
#include "third_party/blink/renderer/platform/supplementable.h"

namespace blink {

class VirtualKeyboard;

// Navigator supplement which exposes virtual keyboard related functionality.
class NavigatorVirtualKeyboard final
    : public GarbageCollected<NavigatorVirtualKeyboard>,
      public Supplement<Navigator> {
  USING_GARBAGE_COLLECTED_MIXIN(NavigatorVirtualKeyboard);

 public:
  static const char kSupplementName[];
  static VirtualKeyboard* virtualKeyboard(Navigator&);

  explicit NavigatorVirtualKeyboard(Navigator&);

  NavigatorVirtualKeyboard(const NavigatorVirtualKeyboard&) = delete;
  NavigatorVirtualKeyboard operator=(const NavigatorVirtualKeyboard&) = delete;

  void Trace(Visitor*) const override;

 private:
  Member<VirtualKeyboard> virtual_keyboard_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_VIRTUALKEYBOARD_NAVIGATOR_VIRTUAL_KEYBOARD_H_
