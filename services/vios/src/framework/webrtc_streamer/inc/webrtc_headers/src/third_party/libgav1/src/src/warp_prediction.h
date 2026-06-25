/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_WARP_PREDICTION_H_
#define LIBGAV1_SRC_WARP_PREDICTION_H_

#include "src/utils/constants.h"
#include "src/utils/types.h"

namespace libgav1 {

// Sets the alpha, beta, gamma, delta fields in warp_params using the
// warp_params->params array as input (only array entries at indexes 2, 3, 4,
// 5 are used). Returns whether alpha, beta, gamma, delta are valid.
bool SetupShear(GlobalMotion* warp_params);  // 7.11.3.6.

// Computes local warp parameters by performing a least square fit.
// Returns whether the computed parameters are valid.
bool WarpEstimation(int num_samples, int block_width4x4, int block_height4x4,
                    int row4x4, int column4x4, const MotionVector& mv,
                    const int candidates[kMaxLeastSquaresSamples][4],
                    GlobalMotion* warp_params);  // 7.11.3.8.

}  // namespace libgav1

#endif  // LIBGAV1_SRC_WARP_PREDICTION_H_
