// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_KALMAN_FILTER_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_KALMAN_FILTER_H_

#include "base/macros.h"
#include "third_party/blink/renderer/platform/platform_export.h"
#include "ui/gfx/geometry/matrix3_f.h"

namespace blink {

// This Kalman filter is used to predict state in one axles.
class PLATFORM_EXPORT KalmanFilter {
 public:
  KalmanFilter();
  ~KalmanFilter();

  // Get the estimation of current state.
  const gfx::Vector3dF& GetStateEstimation() const;

  // Will return true only if the kalman filter has seen enough data and is
  // considered as stable.
  bool Stable() const;

  // Update the observation of the system.
  void Update(double observation, double dt);

  void Reset();

  // Get the predicted values from the kalman filter.
  double GetPosition() const;
  double GetVelocity() const;
  double GetAcceleration() const;

 private:
  void Predict(double dt);

  // Estimate of the latent state
  // Symbol: X
  // Dimension: state_vector_dim_
  gfx::Vector3dF state_estimation_;

  // The covariance of the difference between prior predicted latent
  // state and posterior estimated latent state (the so-called "innovation".
  // Symbol: P
  // Dimension: state_vector_dim_, state_vector_dim_
  gfx::Matrix3F error_covariance_matrix_;

  // For position, state transition matrix is derived from basic physics:
  // new_x = x + v * dt + 1/2 * a * dt^2
  // new_v = v + a * dt
  // ...
  // Matrix that transforms current state to next state
  // Symbol: F
  // Dimension: state_vector_dim_, state_vector_dim_
  gfx::Matrix3F state_transition_matrix_;

  // A time-varying noise parameter that will be estimated as part of the
  // kalman filter process.
  // Symbol: Q
  // Dimension: state_vector_dim_, state_vector_dim_
  gfx::Matrix3F process_noise_covariance_matrix_;

  // Vector to transform estimate to measurement.
  // Symbol: H
  // Dimension: state_vector_dim_
  const gfx::Vector3dF measurement_vector_;

  // A time-varying noise parameter that will be estimated as part of the
  // kalman filter process.
  // Symbol: R
  double measurement_noise_variance_;

  // Tracks number of update iteration happened at this kalman filter. At the
  // 1st iteration, the state estimate will be updated to the measured value.
  // After a few iterations, the KalmanFilter is considered stable.
  uint32_t iteration_count_;

  DISALLOW_COPY_AND_ASSIGN(KalmanFilter);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_WIDGET_INPUT_PREDICTION_KALMAN_FILTER_H_
