// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_IS_INPUT_PENDING_OPTIONS_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_IS_INPUT_PENDING_OPTIONS_H_

#include "third_party/blink/renderer/platform/bindings/script_wrappable.h"

namespace blink {

class IsInputPendingOptionsInit;

class IsInputPendingOptions : public ScriptWrappable {
  DEFINE_WRAPPERTYPEINFO();

 public:
  static IsInputPendingOptions* Create(
      const IsInputPendingOptionsInit* options_init);

  explicit IsInputPendingOptions(bool include_continuous);

  bool includeContinuous() const { return include_continuous_; }
  void setIncludeContinuous(bool include_continuous) {
    include_continuous_ = include_continuous;
  }

 private:
  bool include_continuous_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_IS_INPUT_PENDING_OPTIONS_H_
