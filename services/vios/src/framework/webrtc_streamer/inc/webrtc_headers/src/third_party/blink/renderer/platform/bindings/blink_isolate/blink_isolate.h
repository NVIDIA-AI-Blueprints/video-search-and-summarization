// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_BINDINGS_BLINK_ISOLATE_BLINK_ISOLATE_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_BINDINGS_BLINK_ISOLATE_BLINK_ISOLATE_H_

#include "third_party/blink/public/platform/web_isolate.h"
#include "third_party/blink/renderer/platform/platform_export.h"

namespace blink {

// An BlinkIsolate is a memory and scheduling isolation boundary in Blink,
// which is currently being introduced as a part of Multiple Blink Isolates
// project.
// TODO(crbug.com/1051790): is the tracking bug for the project.
//
// An Isolate will hold a distinct memory region (V8 and Oilpan heaps),
// and a dedicated scheduler.
class PLATFORM_EXPORT BlinkIsolate : public WebIsolate {
 public:
  // Get the currently active BlinkIsolate. GetCurrent() may return nullptr if
  // there is no active BlinkIsolate.
  // TODO(crbug.com/1051395): GetCurrent() returns the singleton BlinkIsolate
  // instance for now, but will change once we start instantiating multiple
  // BlinkIsolates.
  static BlinkIsolate* GetCurrent();

  BlinkIsolate();
  ~BlinkIsolate() override;

 private:
  BlinkIsolate(const BlinkIsolate&) = delete;
  BlinkIsolate& operator=(const BlinkIsolate&) = delete;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_BINDINGS_BLINK_ISOLATE_BLINK_ISOLATE_H_
