// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_FILTER_UNITTEST_HELPERS_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_FILTER_UNITTEST_HELPERS_H_

#include "third_party/blink/renderer/platform/widget/input/prediction/input_filter.h"

#include "testing/gtest/include/gtest/gtest.h"

namespace blink {
namespace test {

constexpr double kEpsilon = 0.0001;

// Base class for predictor unit tests
class InputFilterTest : public testing::Test {
 public:
  InputFilterTest();
  ~InputFilterTest() override;

  void TestCloneFilter();

  void TestResetFilter();

 protected:
  std::unique_ptr<InputFilter> filter_;

  DISALLOW_COPY_AND_ASSIGN(InputFilterTest);
};

}  // namespace test
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_FILTER_UNITTEST_HELPERS_H_
