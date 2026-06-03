// Copyright 2017 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_CLIPBOARD_NAVIGATOR_CLIPBOARD_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_CLIPBOARD_NAVIGATOR_CLIPBOARD_H_

#include "base/macros.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/frame/navigator.h"
#include "third_party/blink/renderer/platform/heap/handle.h"
#include "third_party/blink/renderer/platform/supplementable.h"

namespace blink {

class Clipboard;
class ScriptState;

class NavigatorClipboard final : public GarbageCollected<NavigatorClipboard>,
                                 public Supplement<Navigator> {
  USING_GARBAGE_COLLECTED_MIXIN(NavigatorClipboard);

 public:
  static const char kSupplementName[];
  static Clipboard* clipboard(ScriptState*, Navigator&);

  explicit NavigatorClipboard(Navigator&);

  void Trace(Visitor*) const override;

 private:
  Member<Clipboard> clipboard_;

  DISALLOW_COPY_AND_ASSIGN(NavigatorClipboard);
};

}  // namespace blink

#endif  // NavigatorClipboard.h
