// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_HELPER_FUNCTIONS_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_HELPER_FUNCTIONS_H_

#include <stddef.h>

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <numeric>
#include <vector>

#include "absl/base/no_destructor.h"
#include "support/cc_std_impl/test/vector/cc_movable_types.h"

namespace crubit_test {

struct TwoWords {
  void* ptr1;
  void* ptr2;
};

inline int32_t vector_int32_sum(std::vector<int32_t>* vec) {
  return std::accumulate(vec->begin(), vec->end(), int32_t{0});
}

inline size_t vector_int32_capacity(const std::vector<int32_t>* vec) {
  return vec->capacity();
}

inline void vector_int32_push_back(std::vector<int32_t>* vec, int32_t value) {
  vec->push_back(value);
}

inline void vector_int32_construct(std::vector<int32_t>* v) {
  new (v) std::vector<int32_t>();
}

inline void vector_int32_call_destructor(std::vector<int32_t>* vec) {
  vec->~vector<int32_t>();
}

inline void vector_int32_delete(std::vector<int32_t>* vec) { delete vec; }

inline void vector_int32_clear(std::vector<int32_t>* vec) { vec->clear(); }

inline std::vector<int32_t> vector_int32_get() { return {1, 1, 2, 5, 14, 42}; }

inline const std::vector<int32_t>* vector_int32_get_ptr() {
  static const absl::NoDestructor<std::vector<int32_t>> v({1, 1, 2, 3, 5, 8});
  return v.get();
}

inline void vector_int32_sort(std::vector<int32_t>* vec) {
  std::sort(vec->begin(), vec->end());
}

inline std::vector<SimpleRustMovableType>
vector_get_simple_rust_movable_type() {
  return {SimpleRustMovableType(1), SimpleRustMovableType(2),
          SimpleRustMovableType(3)};
}
inline std::vector<char> vector_char_get() { return {'a', 'b', 'c'}; }

inline std::vector<short> vector_short_get() { return {1, 2, 3}; }

inline std::vector<void*> vector_void_ptr_get() {
  static int i = 42;
  return {&i};
}

inline std::vector<TwoWords> vector_two_words_get() {
  return {TwoWords(), TwoWords()};
}

inline void vector_char_destroy(std::vector<char>) {}
inline void vector_short_destroy(std::vector<short>) {}
inline void vector_void_ptr_destroy(std::vector<void*>) {}
inline void vector_two_words_destroy(std::vector<TwoWords>) {}

inline size_t vector_size_by_value(std::vector<SimpleRustMovableType> vec) {
  // Note: the vector is destroyed after this function returns.
  return vec.size();
}

}  // namespace crubit_test

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_HELPER_FUNCTIONS_H_
