// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_ISOLATE_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_ISOLATE_H_

#include <memory>

#include "third_party/blink/public/platform/web_common.h"

namespace blink {

// WebIsolate is an interface to expose blink::BlinkIsolate to content/.
class WebIsolate {
 public:
  BLINK_EXPORT static std::unique_ptr<WebIsolate> Create();

  virtual ~WebIsolate() = default;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_ISOLATE_H_
