// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_FILTER_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_FILTER_H_

#include "base/macros.h"
#include "third_party/blink/renderer/platform/platform_export.h"
#include "third_party/blink/renderer/platform/widget/input/prediction/input_filter.h"

namespace blink {

// Empty filter is a fake filter. Always returns the same input position as
// the filtered position. Mainly used for testing purpose.
class PLATFORM_EXPORT EmptyFilter : public InputFilter {
 public:
  explicit EmptyFilter();
  ~EmptyFilter() override;

  // Filters the position sent to the filter at a specific timestamp.
  // Returns true if the value is filtered, false otherwise.
  bool Filter(const base::TimeTicks& timestamp,
              gfx::PointF* position) const override;

  // Returns the name of the filter
  const char* GetName() const override;

  // Returns a copy of the filter.
  InputFilter* Clone() override;

  // Reset the filter to its initial state
  void Reset() override;

  DISALLOW_COPY_AND_ASSIGN(EmptyFilter);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_FILTER_H_
