// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_NAVIGATOR_SCHEDULING_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_NAVIGATOR_SCHEDULING_H_

#include "third_party/blink/renderer/core/frame/navigator.h"
#include "third_party/blink/renderer/platform/heap/handle.h"
#include "third_party/blink/renderer/platform/supplementable.h"

namespace blink {

class Scheduling;

class CORE_EXPORT NavigatorScheduling final
    : public GarbageCollected<NavigatorScheduling>,
      public Supplement<Navigator> {
  USING_GARBAGE_COLLECTED_MIXIN(NavigatorScheduling);

 public:
  static const char kSupplementName[];

  static Scheduling* scheduling(Navigator& navigator);
  Scheduling* scheduling();

  explicit NavigatorScheduling(Navigator&);

  void Trace(Visitor*) const override;

 private:
  static NavigatorScheduling& From(Navigator&);

  Member<Scheduling> scheduling_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_NAVIGATOR_SCHEDULING_H_
