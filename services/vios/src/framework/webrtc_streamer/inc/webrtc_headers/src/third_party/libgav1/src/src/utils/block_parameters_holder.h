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

#ifndef LIBGAV1_SRC_UTILS_BLOCK_PARAMETERS_HOLDER_H_
#define LIBGAV1_SRC_UTILS_BLOCK_PARAMETERS_HOLDER_H_

#include <atomic>
#include <memory>

#include "src/utils/array_2d.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/dynamic_buffer.h"
#include "src/utils/types.h"

namespace libgav1 {

// Holds the BlockParameters pointers to each 4x4 block in the frame.
class BlockParametersHolder {
 public:
  BlockParametersHolder() = default;

  // Not copyable or movable.
  BlockParametersHolder(const BlockParametersHolder&) = delete;
  BlockParametersHolder& operator=(const BlockParametersHolder&) = delete;

  LIBGAV1_MUST_USE_RESULT bool Reset(int rows4x4, int columns4x4);

  // Returns a pointer to a BlockParameters object that can be used safely until
  // the next call to Reset(). Returns nullptr on memory allocation failure. It
  // also fills the cache matrix for the block starting at |row4x4|, |column4x4|
  // of size |block_size| with the returned pointer.
  BlockParameters* Get(int row4x4, int column4x4, BlockSize block_size);

  // Finds the BlockParameters corresponding to |row4x4| and |column4x4|. This
  // is done as a simple look up of the |block_parameters_cache_| matrix.
  // Returns nullptr if the BlockParameters cannot be found.
  BlockParameters* Find(int row4x4, int column4x4) const {
    return block_parameters_cache_[row4x4][column4x4];
  }

  BlockParameters** Address(int row4x4, int column4x4) {
    return block_parameters_cache_.data() + row4x4 * columns4x4_ + column4x4;
  }

  BlockParameters* const* Address(int row4x4, int column4x4) const {
    return block_parameters_cache_.data() + row4x4 * columns4x4_ + column4x4;
  }

  int columns4x4() const { return columns4x4_; }

 private:
  // Needs access to FillCache for testing Cdef.
  template <int bitdepth, typename Pixel>
  friend class PostFilterApplyCdefTest;

  void FillCache(int row4x4, int column4x4, BlockSize block_size,
                 BlockParameters* bp);

  int rows4x4_ = 0;
  int columns4x4_ = 0;

  // Owns the memory of BlockParameters pointers for the entire frame. It can
  // hold upto |rows4x4_| * |columns4x4_| objects. Each object will be allocated
  // on demand and re-used across frames.
  DynamicBuffer<std::unique_ptr<BlockParameters>> block_parameters_;

  // Points to the next available index of |block_parameters_|.
  std::atomic<int> index_;

  // This is a 2d array of size |rows4x4_| * |columns4x4_|. This is filled in by
  // FillCache() and used by Find() to perform look ups using exactly one look
  // up (instead of traversing the entire tree).
  Array2D<BlockParameters*> block_parameters_cache_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_BLOCK_PARAMETERS_HOLDER_H_
