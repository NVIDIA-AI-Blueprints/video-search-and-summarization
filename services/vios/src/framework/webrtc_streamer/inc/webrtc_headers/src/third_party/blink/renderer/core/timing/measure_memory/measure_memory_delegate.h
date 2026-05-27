// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_TIMING_MEASURE_MEMORY_MEASURE_MEMORY_DELEGATE_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_TIMING_MEASURE_MEMORY_MEASURE_MEMORY_DELEGATE_H_

#include "third_party/blink/renderer/platform/bindings/scoped_persistent.h"
#include "third_party/blink/renderer/platform/wtf/allocator/allocator.h"
#include "v8/include/v8.h"

namespace blink {

// Specifies V8 contexts to be measured and resolves the promise once V8
// completes the memory measurement.
class MeasureMemoryDelegate : public v8::MeasureMemoryDelegate {
 public:
  MeasureMemoryDelegate(v8::Isolate* isolate,
                        v8::Local<v8::Context> context,
                        v8::Local<v8::Promise::Resolver> promise_resolver);

  // v8::MeasureMemoryDelegate overrides.
  bool ShouldMeasure(v8::Local<v8::Context> context) override;
  void MeasurementComplete(
      const std::vector<std::pair<v8::Local<v8::Context>, size_t>>&
          context_sizes,
      size_t unattributed_size) override;

 private:
  v8::Isolate* isolate_;
  ScopedPersistent<v8::Context> context_;
  ScopedPersistent<v8::Promise::Resolver> promise_resolver_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_TIMING_MEASURE_MEMORY_MEASURE_MEMORY_DELEGATE_H_
