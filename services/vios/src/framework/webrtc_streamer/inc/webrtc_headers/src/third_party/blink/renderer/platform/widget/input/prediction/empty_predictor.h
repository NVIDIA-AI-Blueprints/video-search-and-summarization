// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_PREDICTOR_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_PREDICTOR_H_

#include "base/optional.h"
#include "third_party/blink/public/platform/input/input_predictor.h"
#include "third_party/blink/renderer/platform/platform_export.h"

namespace blink {

// An empty predictor class. This will not generate any prediction.
class PLATFORM_EXPORT EmptyPredictor : public InputPredictor {
 public:
  EmptyPredictor();
  ~EmptyPredictor() override;

  const char* GetName() const override;

  void Reset() override;

  // store the cur_input in last_input_
  void Update(const InputData& cur_input) override;

  // Always returns false;
  bool HasPrediction() const override;

  // Returns the last_input_ for testing.
  std::unique_ptr<InputData> GeneratePrediction(
      base::TimeTicks predict_time) const override;

  // Returns kTimeInterval for testing.
  base::TimeDelta TimeInterval() const override;

 private:
  // store the last_input_ point for testing
  base::Optional<InputData> last_input_;

  DISALLOW_COPY_AND_ASSIGN(EmptyPredictor);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_EMPTY_PREDICTOR_H_
