// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_XMLHTTPREQUEST_MAIN_THREAD_DISALLOW_SYNCHRONOUS_XHR_SCOPE_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_XMLHTTPREQUEST_MAIN_THREAD_DISALLOW_SYNCHRONOUS_XHR_SCOPE_H_

#include "base/macros.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/platform/wtf/allocator/allocator.h"

namespace blink {

class CORE_EXPORT MainThreadDisallowSynchronousXHRScope final {
  STACK_ALLOCATED();

 public:
  MainThreadDisallowSynchronousXHRScope();
  ~MainThreadDisallowSynchronousXHRScope();

  static bool ShouldDisallowSynchronousXHR();

  DISALLOW_COPY_AND_ASSIGN(MainThreadDisallowSynchronousXHRScope);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_XMLHTTPREQUEST_MAIN_THREAD_DISALLOW_SYNCHRONOUS_XHR_SCOPE_H_
