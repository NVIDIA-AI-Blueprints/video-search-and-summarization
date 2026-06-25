// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_CONVERSION_FUNCTION_HELPERS_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_CONVERSION_FUNCTION_HELPERS_H_

#include <string>

namespace crubit_cc_std_internal::conversion_function_helpers {

// std::string helpers start
inline size_t StringGetSize(const void* s) {
  return reinterpret_cast<const std::string*>(s)->size();
}

inline const char* StringGetData(const void* s) {
  return reinterpret_cast<const std::string*>(s)->data();
}

// Move-constructs a `std::string` `dst` from `src`.
//
// `src` and `dst` must point to a `std::string`, and `dst` must be
// uninitialized.
inline void StringCreateInPlace(void* dst, void* src) {
  new (reinterpret_cast<std::string*>(dst))
      std::string(std::move(*reinterpret_cast<std::string*>(src)));
}

inline void* StringCreateFromBuffer(const char* buffer, size_t size) {
  return new std::string(buffer, size);
}

// Moves the string from `s` to a new C++ allocated string.
//
// `s` must point to a `std::string`.
// C++ guarantees that the string is moved and not copied and is O(1):
// https://en.cppreference.com/w/cpp/string/basic_string/basic_string.
inline void* StringMoveOwnedPtr(void* s) {
  return new std::string(std::move(*reinterpret_cast<std::string*>(s)));
}

// Makes a copy of the string from `s` and returns a owned pointer to the copy.
//
// `s` must point to a `std::string`.
inline void* StringCopyOwnedPtr(void* s) {
  return new std::string(*reinterpret_cast<std::string*>(s));
}

inline constexpr size_t SizeOfString() { return sizeof(std::string); }

inline bool StringEqual(const void* s1, const void* s2) {
  return *reinterpret_cast<const std::string*>(s1) ==
         *reinterpret_cast<const std::string*>(s2);
}

inline void StringDelete(void* s) { delete reinterpret_cast<std::string*>(s); }
// std::string helpers end

}  // namespace crubit_cc_std_internal::conversion_function_helpers

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_CONVERSION_FUNCTION_HELPERS_H_
