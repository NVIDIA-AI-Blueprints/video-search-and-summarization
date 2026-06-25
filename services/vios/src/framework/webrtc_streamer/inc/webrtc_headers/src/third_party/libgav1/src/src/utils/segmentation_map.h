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

#ifndef LIBGAV1_SRC_UTILS_SEGMENTATION_MAP_H_
#define LIBGAV1_SRC_UTILS_SEGMENTATION_MAP_H_

#include <cstdint>
#include <memory>

#include "src/utils/array_2d.h"
#include "src/utils/compiler_attributes.h"

namespace libgav1 {

// SegmentationMap stores the segment id associated with each 4x4 block in the
// frame.
class SegmentationMap {
 public:
  SegmentationMap() = default;

  // Not copyable or movable
  SegmentationMap(const SegmentationMap&) = delete;
  SegmentationMap& operator=(const SegmentationMap&) = delete;

  // Allocates an internal buffer of the given dimensions to hold the
  // segmentation map. The memory in the buffer is not initialized. Returns
  // true on success, false on failure (for example, out of memory).
  LIBGAV1_MUST_USE_RESULT bool Allocate(int32_t rows4x4, int32_t columns4x4);

  int8_t segment_id(int row4x4, int column4x4) const {
    return segment_id_[row4x4][column4x4];
  }

  // Sets every element in the segmentation map to 0.
  void Clear();

  // Copies the entire segmentation map. |from| must be of the same dimensions.
  void CopyFrom(const SegmentationMap& from);

  // Sets the region of segmentation map covered by the block to |segment_id|.
  // The block is located at |row4x4|, |column4x4| and has dimensions
  // |block_width4x4| and |block_height4x4|.
  void FillBlock(int row4x4, int column4x4, int block_width4x4,
                 int block_height4x4, int8_t segment_id);

 private:
  int32_t rows4x4_ = 0;
  int32_t columns4x4_ = 0;

  // segment_id_ is a rows4x4_ by columns4x4_ 2D array. The underlying data
  // buffer is dynamically allocated and owned by segment_id_buffer_.
  std::unique_ptr<int8_t[]> segment_id_buffer_;
  Array2DView<int8_t> segment_id_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_SEGMENTATION_MAP_H_
