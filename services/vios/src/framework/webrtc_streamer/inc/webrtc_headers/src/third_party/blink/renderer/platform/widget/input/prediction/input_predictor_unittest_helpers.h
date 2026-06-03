// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_PREDICTOR_UNITTEST_HELPERS_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_PREDICTOR_UNITTEST_HELPERS_H_

#include "base/time/time.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/blink/public/common/input/web_input_event.h"
#include "third_party/blink/public/platform/input/input_predictor.h"

namespace blink {

constexpr base::TimeDelta kExpectedDefaultTimeInterval =
    base::TimeDelta::FromMilliseconds(8);

// Base class for predictor unit tests
class InputPredictorTest : public testing::Test {
 public:
  InputPredictorTest();
  ~InputPredictorTest() override;

  static base::TimeTicks FromMilliseconds(int64_t ms) {
    return WebInputEvent::GetStaticTimeStampForTests() +
           base::TimeDelta::FromMilliseconds(ms);
  }

  void ValidatePredictor(const std::vector<double>& x,
                         const std::vector<double>& y,
                         const std::vector<double>& timestamp_ms);

  void ValidatePredictor(const std::vector<double>& events_x,
                         const std::vector<double>& events_y,
                         const std::vector<double>& events_ts_ms,
                         const std::vector<double>& prediction_ts_ms,
                         const std::vector<double>& predicted_x,
                         const std::vector<double>& predicted_y);

 protected:
  static constexpr double kEpsilon = 0.1;

  std::unique_ptr<InputPredictor> predictor_;

  DISALLOW_COPY_AND_ASSIGN(InputPredictorTest);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_INPUT_PREDICTOR_UNITTEST_HELPERS_H_
