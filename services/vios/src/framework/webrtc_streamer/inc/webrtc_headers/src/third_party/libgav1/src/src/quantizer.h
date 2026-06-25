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

#ifndef LIBGAV1_SRC_QUANTIZER_H_
#define LIBGAV1_SRC_QUANTIZER_H_

#include <array>
#include <cstdint>

#include "src/utils/constants.h"
#include "src/utils/dynamic_buffer.h"
#include "src/utils/segmentation.h"
#include "src/utils/types.h"

namespace libgav1 {

using QuantizerMatrix = std::array<
    std::array<std::array<DynamicBuffer<uint8_t>, kNumTransformSizes>,
               kNumPlaneTypes>,
    kNumQuantizerLevelsForQuantizerMatrix>;

// Implements the dequantization functions of Section 7.12.2.
class Quantizer {
 public:
  Quantizer(int bitdepth, const QuantizerParameters* params);

  // Returns the quantizer value for the dc coefficient for the given plane.
  // The caller should call GetQIndex() with Tile::current_quantizer_index_ as
  // the |base_qindex| argument, and pass the return value as the |qindex|
  // argument to this method.
  int GetDcValue(Plane plane, int qindex) const;

  // Returns the quantizer value for the ac coefficient for the given plane.
  // The caller should call GetQIndex() with Tile::current_quantizer_index_ as
  // the |base_qindex| argument, and pass the return value as the |qindex|
  // argument to this method.
  int GetAcValue(Plane plane, int qindex) const;

 private:
  const QuantizerParameters& params_;
  const int16_t* dc_lookup_;
  const int16_t* ac_lookup_;
};

// Initialize the quantizer matrix.
bool InitializeQuantizerMatrix(QuantizerMatrix* quantizer_matrix);

// Get the quantizer index for the |index|th segment.
//
// This function has two use cases. What should be passed as the |base_qindex|
// argument depends on the use case.
// 1. While parsing the uncompressed header or transform type, pass
//    Quantizer::base_index.
//    Note: In this use case, the caller only cares about whether the return
//    value is zero.
// 2. To generate the |qindex| argument to Quantizer::GetDcQuant() or
//    Quantizer::GetAcQuant(), pass Tile::current_quantizer_index_.
int GetQIndex(const Segmentation& segmentation, int index, int base_qindex);

}  // namespace libgav1

#endif  // LIBGAV1_SRC_QUANTIZER_H_
