// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_DELEGATED_INK_NAVIGATOR_INK_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_DELEGATED_INK_NAVIGATOR_INK_H_

#include "third_party/blink/renderer/core/frame/navigator.h"
#include "third_party/blink/renderer/platform/bindings/script_state.h"
#include "third_party/blink/renderer/platform/supplementable.h"

namespace blink {

class Ink;

class NavigatorInk : public GarbageCollected<NavigatorInk>,
                     public Supplement<Navigator> {
  USING_GARBAGE_COLLECTED_MIXIN(NavigatorInk);

 public:
  static const char kSupplementName[];
  static Ink* ink(Navigator& navigator);

  explicit NavigatorInk(Navigator& navigator);

  void Trace(blink::Visitor*) const override;

 private:
  Member<Ink> ink_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_DELEGATED_INK_NAVIGATOR_INK_H_
