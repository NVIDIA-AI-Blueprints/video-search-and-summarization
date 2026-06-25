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

#ifndef LIBGAV1_SRC_UTILS_BIT_MASK_SET_H_
#define LIBGAV1_SRC_UTILS_BIT_MASK_SET_H_

#include <cstdint>

namespace libgav1 {

// This class is used to check if a given value is equal to one of the several
// predetermined values using a bit mask instead of a chain of comparisons and
// ||s. This usually results in fewer instructions.
//
// Usage:
//   constexpr BitMaskSet set(value1, value2);
//   set.Contains(value1) => returns true.
//   set.Contains(value3) => returns false.
class BitMaskSet {
 public:
  explicit constexpr BitMaskSet(uint32_t mask) : mask_(mask) {}

  constexpr BitMaskSet(int v1, int v2) : mask_((1U << v1) | (1U << v2)) {}

  constexpr BitMaskSet(int v1, int v2, int v3)
      : mask_((1U << v1) | (1U << v2) | (1U << v3)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4, int v5)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4) | (1U << v5)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4, int v5, int v6)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4) | (1U << v5) |
              (1U << v6)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4, int v5, int v6, int v7)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4) | (1U << v5) |
              (1U << v6) | (1U << v7)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4, int v5, int v6, int v7,
                       int v8, int v9)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4) | (1U << v5) |
              (1U << v6) | (1U << v7) | (1U << v8) | (1U << v9)) {}

  constexpr BitMaskSet(int v1, int v2, int v3, int v4, int v5, int v6, int v7,
                       int v8, int v9, int v10)
      : mask_((1U << v1) | (1U << v2) | (1U << v3) | (1U << v4) | (1U << v5) |
              (1U << v6) | (1U << v7) | (1U << v8) | (1U << v9) | (1U << v10)) {
  }

  constexpr bool Contains(uint8_t value) const {
    return MaskContainsValue(mask_, value);
  }

  static constexpr bool MaskContainsValue(uint32_t mask, uint8_t value) {
    return ((mask >> value) & 1) != 0;
  }

 private:
  const uint32_t mask_;
};

}  // namespace libgav1
#endif  // LIBGAV1_SRC_UTILS_BIT_MASK_SET_H_
